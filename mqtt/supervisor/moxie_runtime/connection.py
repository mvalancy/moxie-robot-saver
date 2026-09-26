"""Broker connection: paho client, (re)connect, subscriptions, publish, connect detection, roster."""
from __future__ import annotations
import json, os, threading, time

from moxie_sdk.types import RobotContext
from moxie_sdk import conn_telemetry as conn_seam
from moxie_sdk import roster as roster_seam
from .constants import CONNECT_RE, DISCONNECT_RE, RECONNECT_MIN_DELAY_S, RECONNECT_MAX_DELAY_S


class ConnectionMixin:
    def _build_client(self):
        import paho.mqtt.client as mqtt
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="supervisor")
        # The supervisor's broker credential (security-broker-auth.md §2.2): the one
        # fleet-wide identity allowed `$SYS/broker/log`. Unset = anonymous (dev/SIL/CI).
        try:
            from config import broker_credentials
            username, password = broker_credentials()
        except Exception:                      # config not importable → anonymous
            username, password = "", ""
        if username and password:
            self.client.username_pw_set(username, password)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        # The SUBACK: the only evidence we are actually subscribed (see `_on_subscribe`).
        self.client.on_subscribe = self._on_subscribe
        # paho reconnects on its own; these make drops and retries visible (§4.1 C4).
        self.client.on_disconnect = self._on_disconnect
        self.client.on_connect_fail = self._on_connect_fail
        self.client.reconnect_delay_set(min_delay=RECONNECT_MIN_DELAY_S,
                                        max_delay=RECONNECT_MAX_DELAY_S)
        return self.client

    #: Re-installed on every successful CONNACK (the broker drops them with the session).
    SUBSCRIPTIONS = ("/devices/+/events/#", "/devices/+/state",
                     "$SYS/broker/log/#", "$SYS/broker/clients/#")

    @staticmethod
    def _connack_failed(rc) -> bool:
        """True when a CONNACK refused us (`rc` is a ReasonCode under paho VERSION2,
        an int under VERSION1)."""
        failed = getattr(rc, "is_failure", None)
        if failed is not None:
            return bool(failed)
        try:
            return int(rc) != 0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _suback_failed(rc) -> bool:
        """True when one SUBACK entry refused a filter (MQTT 3.1.1 `0x80`, MQTT 5 a
        failure ReasonCode) — e.g. an ACL not granting `/devices/+/state`."""
        failed = getattr(rc, "is_failure", None)
        if failed is not None:
            return bool(failed)
        try:
            return int(rc) >= 128
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _connack_reason(rc) -> str:
        """`connack_string(rc)` when paho is importable, else the code as written."""
        try:
            import paho.mqtt.client as mqtt
            return str(mqtt.connack_string(rc))
        except Exception:
            return str(rc)

    def _on_connect(self, c, u, flags, rc, props=None):
        """A CONNACK arrived — not necessarily a yes. A refusal (e.g. rc=5, not
        authorised) is logged as such and nothing is subscribed."""
        if self._connack_failed(rc):
            self.broker_connected = False
            self.last_connect_error = self._connack_reason(rc)
            print(f"[runtime] ⛔ broker REFUSED the connection: "
                  f"{self.last_connect_error}", flush=True)
            self._note("error", f"⛔ broker refused the connection: "
                                f"{self.last_connect_error}")
            self._record_conn(conn_seam.REFUSED, reason=self.last_connect_error)
            return                            # and subscribe to nothing
        now = time.time()
        # Outage length from the recorded disconnect; None on the first connect.
        gap = conn_seam.gap_since(self.last_broker_disconnect, now)
        self.broker_connected = True
        self.last_broker_connect = now
        self.last_connect_error = ""
        self._connect_generation += 1
        # Subscribe first, then announce. `subscribe()` only queues the packet, so the
        # "broker connected" line means "CONNACK said yes and we asked"; readiness for
        # robot traffic is the SUBACK line in `_on_subscribe` (the config reply to a
        # `/state` is QoS 0, so a robot announcing itself earlier is lost, not delayed).
        # One list call = one SUBACK to wait for; `test_connect_readiness.py` pins that
        # this is the only `subscribe()` call site.
        self.subscriptions_acked.clear()   # a reconnect re-subscribes: re-arm, not latch
        c.subscribe([(t, 0) for t in self.SUBSCRIPTIONS])
        # Flushed: harnesses block on this line from a block-buffered redirected stdout.
        print(f"[runtime] broker connected rc={rc}", flush=True)
        self._note("conn", "broker connected" if gap is None
                   else f"broker reconnected after {gap:.1f}s")
        self._record_conn(conn_seam.CONNECT, at=now, gap_s=gap)
        # Off the network thread, after subscribing: never block the MQTT loop on a burst.
        self._schedule_roster_resume()

    def _on_subscribe(self, c, u, mid, reason_codes=None, properties=None):
        """The SUBACK: the moment the supervisor can actually hear a robot.

        Harnesses wait for the line printed here, not for "broker connected". Defaults
        cover both paho callback APIs (VERSION2 passes reason codes, VERSION1 granted QoS).
        Idempotent. A refused filter is reported and readiness withheld — deaf, and saying so.
        """
        refused = [str(rc) for rc in (reason_codes or []) if self._suback_failed(rc)]
        if refused:
            print(f"[runtime] ⛔ the broker REFUSED {len(refused)} of "
                  f"{len(self.SUBSCRIPTIONS)} subscriptions ({', '.join(refused)}) — "
                  f"this appliance cannot hear its robots", flush=True)
            self._note("error", f"⛔ broker refused {len(refused)} subscription(s): "
                                f"{', '.join(refused)}")
            return                       # readiness NOT armed: we really are deaf
        if self.subscriptions_acked.is_set():
            return
        self.subscriptions_acked.set()
        # Flushed: waiters read this from a redirected, block-buffered stdout.
        print(f"[runtime] subscriptions acknowledged by the broker "
              f"({len(self.SUBSCRIPTIONS)} topics)", flush=True)

    def _on_disconnect(self, c, u, flags=None, rc=None, props=None):
        """The socket went away: record it and stale every in-flight turn.

        Bumping `_turn_seq` reuses `_is_stale`: the robot re-prompts with a new event_id
        after ~20 s, so an answer delivered after the gap would answer the wrong question.
        Runs on the network loop thread, the only `_turn_seq` writer (A19).
        """
        was = self.broker_connected
        self.broker_connected = False
        # Our subscriptions died with the socket; the next CONNACK/SUBACK re-arms this.
        self.subscriptions_acked.clear()
        self.last_broker_disconnect = time.time()
        reason = self._connack_reason(rc) if rc is not None else "connection lost"
        for device_id in set(self._turn_seq) | set(self.robots):
            self._turn_seq[device_id] = self._turn_seq.get(device_id, 0) + 1
        # We lost OUR socket: every robot is now unconfirmed (not removed — see
        # `_device_connect`), and cached state beliefs are dropped.
        self._forget_robot_state()
        if self._stopping:
            # A disconnect we asked for. `request_stop()` already wrote the `shutdown` row.
            print("[runtime] 👋 broker connection closed cleanly (shutting down)", flush=True)
            self._note("conn", "👋 broker connection closed cleanly (shutting down)")
            return
        if was:
            print(f"[runtime] ⚠️  broker disconnected: {reason}", flush=True)
            self._note("conn", f"⚠️ broker disconnected: {reason} — "
                               f"{len(self.robots)} robot(s) in flight abandoned")
            self._record_conn(conn_seam.DISCONNECT, reason=reason)

    def _on_connect_fail(self, c, u=None):
        """The socket never opened (broker down, DNS gone) — makes the retry loop visible."""
        self.broker_connected = False
        self.last_connect_error = f"could not reach the broker at {self.host}:{self.port}"
        # Printed too, so `docker logs` shows the retries (backoff caps it at ~1/min).
        print(f"[runtime] ⛔ {self.last_connect_error} — retrying", flush=True)
        self._note("error", f"⛔ {self.last_connect_error} — retrying")
        self._record_conn(conn_seam.CONNECT_FAIL, reason=self.last_connect_error)

    def _on_store_lock_timeout(self, lock_path, waited):
        """A store write another process would not release: recorded, never retried
        forever (production-hardening.md §3.3). `waited_s` is the evidence for tuning
        MOXIE_STORE_LOCK_TIMEOUT_S."""
        if getattr(self, "recent", None) is None:
            return
        self._note("error", f"⏳ a store write was refused after {waited:.1f}s — another "
                            f"process holds {os.path.basename(lock_path)}")
        self._record_conn(conn_seam.LOCK_TIMEOUT, waited_s=waited,
                          reason=os.path.basename(lock_path))

    # ---- the connection's own durable history (§8 P1) ----
    def _record_conn(self, kind: str, **fields) -> bool:
        """Append one row to the `fleet/conn_events` ring. Never raises; best effort.

        Re-entrant calls are dropped (the lock-timeout recorder itself writes to the
        store), and every failure is swallowed: this runs on the paho thread and inside
        `_publish`, where telemetry must never cost a turn. Returns whether it recorded.
        """
        if self._recording_conn:
            return False
        self._recording_conn = True
        try:
            row = conn_seam.build_event(kind, **fields)
            return self.store.append_shared(conn_seam.COLLECTION, row,
                                            cap=conn_seam.max_events()) is not None
        except Exception:
            return False
        finally:
            self._recording_conn = False

    def conn_events(self) -> list:
        """The stored connection ring, oldest first. `[]` when nothing has happened."""
        rows = self.store.read_shared(conn_seam.COLLECTION, [])
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    def conn_view(self, limit: int = 40) -> dict:
        """The connection view: the durable ring rolled up, beside the live scalars
        ("down now" vs "dropped nine times this hour, up now")."""
        summary = conn_seam.summarize(self.conn_events(), limit=limit)
        return {"ok": True,
                "connected": self.broker_connected,
                "last_connect": self.last_broker_connect,
                "last_disconnect": self.last_broker_disconnect,
                "last_error": self.last_connect_error,
                "publish_drops": self.publish_drops,
                "store_lock_timeouts": getattr(self.store, "lock_timeouts", 0),
                "uptime_s": int(time.time() - self.started_at),
                "retention": {"events": conn_seam.max_events()},
                "health": conn_seam.health(summary, connected=self.broker_connected),
                "summary": summary,
                "events": summary["latest"],
                "roster": roster_seam.summarize(self.roster())}

    # ---- the durable robot roster (§8 P1) ----
    def roster(self) -> dict:
        """Every robot this appliance has served; re-read each time (another process
        may have edited it)."""
        return self.store.read_shared(roster_seam.COLLECTION, roster_seam.new_roster())

    def _is_known(self, device_id: str) -> bool:
        """Connected now, or in the durable roster (served before, maybe offline)."""
        return device_id in self.robots or device_id in roster_seam.device_ids(self.roster())

    def _roster_seen(self, device_id: str) -> bool:
        """Record that we serve `device_id` (read-modify-write under the record's lock,
        so two supervisors on one data dir cannot lose each other's robots)."""
        try:
            with self.store.transaction_shared(roster_seam.COLLECTION):
                current = self.roster()
                self.store.write_shared(roster_seam.COLLECTION,
                                        roster_seam.record_seen(current, device_id))
            return True
        except Exception:
            return False

    def _roster_forget(self, device_id: str) -> bool:
        """Drop `device_id` from the roster (un-paired: stop pushing config to it)."""
        try:
            with self.store.transaction_shared(roster_seam.COLLECTION):
                self.store.write_shared(roster_seam.COLLECTION,
                                        roster_seam.forget(self.roster(), device_id))
            return True
        except Exception:
            return False

    def resume_roster(self) -> list:
        """Re-push config to every rostered robot with no live evidence on this
        connection; returns the ids pushed to.

        After a supervisor restart nothing would otherwise prompt a push (`$SYS` log is
        live-only; a robot sends `/state` on ITS connect, not ours). Marks nobody
        connected: a QoS 0 push to an absent robot is simply discarded.
        """
        if not roster_seam.resume_enabled():
            return []
        # `_seen_since_connect`, not `self.robots`: after a broker restart all are unconfirmed.
        targets = roster_seam.resume_targets(
            self.roster(), connected=sorted(self._seen_since_connect),
            permitted=self.is_permitted)
        pushed = []
        for device_id in targets:
            try:
                self._push_config(device_id)
                pushed.append(device_id)
            except Exception as e:
                print(f"[runtime] roster re-push failed for {device_id}: {e}", flush=True)
        if pushed:
            self._note("robot", f"🤖 re-pushed config to {len(pushed)} robot(s) from the "
                                f"roster (no event needed)")
        return pushed

    #: CONNACK -> roster re-push delay (same settle as `_device_connect`).
    ROSTER_RESUME_DELAY_S = 1.0

    def _schedule_roster_resume(self):
        """Run `resume_roster()` off the network thread, once per connect generation, so
        a flapping link's CONNACK storm queues only the latest burst."""
        generation = self._connect_generation

        def _resume():
            if generation != self._connect_generation or self._stopping:
                return                        # superseded by a newer connect, or stopping
            try:
                self.resume_roster()
            except Exception as e:
                print(f"[runtime] roster resume failed: {e}", flush=True)

        timer = threading.Timer(self.ROSTER_RESUME_DELAY_S, _resume)
        timer.daemon = True                   # never hold a shutdown open for a re-push
        timer.start()

    # ---- publishing, with the return code read (§4.1 C5) ----
    def _broker_connected(self) -> bool:
        """Is there really a socket? paho's `is_connected()` when available; a transport
        double without one (the SIL loopback) is trusted."""
        client = self.client
        if client is None:
            return False
        checker = getattr(client, "is_connected", None)
        if not callable(checker):
            return True
        try:
            return bool(checker())
        except Exception:
            return False

    #: What a route tells a parent when there is no broker.
    NO_BROKER_REASON = "The supervisor is not connected to the broker."

    def _publish(self, topic: str, payload, *, device_id: str = "", what: str = ""):
        """Publish one message and read the return code. Returns `(ok, reason)`.

        At QoS 0 paho drops (does not queue) a publish with no socket, so every drop is
        counted and recorded. QoS 0 stays on purpose: a QoS 1 queue would deliver stale
        answers after a gap (production-hardening.md §4.2-4.3).
        """
        body = payload if isinstance(payload, str) else json.dumps(payload)
        label = what or topic.rsplit("/", 1)[-1]
        if not self._broker_connected():
            self._record_drop(topic, device_id, label, self.NO_BROKER_REASON)
            return False, self.NO_BROKER_REASON
        try:
            info = self.client.publish(topic, body)
        except Exception as e:                # a transport that raises is still a drop
            reason = f"the transport refused the message ({type(e).__name__})"
            self._record_drop(topic, device_id, label, reason)
            return False, reason
        rc = getattr(info, "rc", 0)           # a double that returns None means success
        if rc:
            reason = f"the broker connection dropped the message (rc={rc})"
            self._record_drop(topic, device_id, label, reason)
            return False, reason
        return True, ""

    def _record_drop(self, topic, device_id, label, reason):
        self.publish_drops += 1
        who = f"{device_id} " if device_id else ""
        print(f"[runtime] ⚠️  dropped {label} for {who}— {reason}", flush=True)
        self._note("drop", f"⚠️ dropped {label} for {who or 'the fleet'}— {reason}")
        # In the appliance-wide ring so disconnect -> drops -> reconnect reads in order.
        self._record_conn(conn_seam.PUBLISH_DROP, device_id=device_id, topic=topic,
                          reason=reason)

    # ---- message router ----
    def _on_message(self, c, u, msg):
        topic = msg.topic
        try:
            if topic.startswith("$SYS/broker/log/"):
                return self._on_log(msg.payload.decode("utf-8", "replace"))
            parts = topic.split("/")            # ['', 'devices', d_id, 'events', name...]
            if len(parts) >= 4 and parts[1] == "devices":
                device_id = parts[2]
                kind = parts[3]
                if kind == "state":
                    return self._on_state(device_id, msg.payload)
                if kind == "events" and len(parts) >= 5:
                    # The pairing gate, at the transport boundary so no handler can
                    # skip it. `/state` still flows: it makes an unknown robot visible.
                    if not self.is_permitted(device_id):
                        return self._serve_unpermitted(device_id, parts[4], msg.payload)
                    return self._on_event(device_id, parts[4], msg.payload)
        except Exception as e:
            print(f"[runtime] error handling {topic}: {e}")

    # ---- connect detection via broker log ----
    def _note(self, kind: str, text: str):
        """Record a line for the UI's connection monitor."""
        self.recent.append({"t": time.time(), "kind": kind, "text": text})

    def _on_log(self, line: str):
        # surface interesting broker activity to the UI (any sign of life)
        low = line.lower()
        if any(k in low for k in ("new connection", "new client", "disconnect",
                                  "closed its connection", "error", "socket", "denied")):
            kind = "error" if ("error" in low or "socket" in low) else "conn"
            self._note(kind, line.split(": ", 1)[-1] if ": " in line else line)
        m = CONNECT_RE.search(line)
        if m:
            return self._device_connect(m.group(2))
        m = DISCONNECT_RE.search(line)
        if m:
            return self._device_disconnect(m.group(1))

    def _device_connect(self, device_id: str):
        """Onboard a robot: register it, push its config, and let the app greet it.

        Idempotent per broker *connection*, not per process: after a broker restart the
        `$SYS` disconnect line never arrives, so a returning robot is re-onboarded on its
        first evidence (a `/state`, an event). Robots are not dropped on our own
        disconnect — that would claim knowledge we lack, stampede on a blip and end the
        child's conversation. The `RobotContext` is reused, so history and per-robot state
        survive the outage.
        """
        robot = self.robots.get(device_id)
        if robot is not None and device_id in self._seen_since_connect:
            return                            # already onboarded on this connection
        returning = robot is not None
        if robot is None:
            robot = RobotContext(device_id=device_id, child=self.child)
            self.robots[device_id] = robot
        self._seen_since_connect.add(device_id)
        if returning:
            print(f"[runtime] 🤖 robot back after the outage: {device_id}", flush=True)
            self._note("robot", f"🤖 robot back after the outage: {device_id} — "
                                f"re-pushing config")
        else:
            print(f"[runtime] 🤖 robot connected: {device_id}", flush=True)
            self._note("robot", f"🤖 robot connected: {device_id}")
        self.history.setdefault(device_id, [])
        # Every ingress path converges here, so this is where the roster is written.
        self._roster_seen(device_id)
        # Push config after a short settle delay, WITHOUT blocking the MQTT loop.
        def _settle():
            self._push_config(device_id)
            # A pending robot never reaches the app; `set_permit` runs `on_connect` later.
            if not self.is_permitted(device_id):
                return
            try:
                self.app_for(device_id).on_connect(robot)
            except Exception as e:
                print(f"[runtime] app.on_connect error: {e}", flush=True)
        threading.Timer(1.0, _settle).start()

    # ---- one place that forgets what we believe about a robot's state ----
    # Cached beliefs (onboarded on this connection; vision subscribed) are pure
    # optimisation: forgetting costs one redundant message, remembering wrongly leaves a
    # robot silently half-connected. When in doubt, forget. The vision latch has one extra
    # invalidator (module exit), hence `vision_only`. Robot *data* (history, memory,
    # telemetry, presence, the RobotContext) is never forgotten here.
    def _forget_robot_state(self, device_id: str | None = None, *, vision_only=False):
        """Drop our cached beliefs about one robot (or all of them, `device_id=None`)."""
        with self._presence_lock:
            if device_id is None:
                self._vision_subscribed.clear()
                self._pack_subscribed.clear()
            else:
                self._vision_subscribed.pop(device_id, None)
                self._pack_subscribed.pop(device_id, None)
        if vision_only:
            return
        if device_id is None:
            self._seen_since_connect.clear()
        else:
            self._seen_since_connect.discard(device_id)

    def _device_disconnect(self, device_id: str):
        # Real evidence the robot left: drop every belief about its state.
        self._forget_robot_state(device_id)
        robot = self.robots.pop(device_id, None)
        if robot:
            print(f"[runtime] robot disconnected: {device_id}")
            self._end_conversation(device_id, "disconnect", robot=robot)
            try:
                self.app_for(device_id).on_disconnect(robot)
            except Exception:
                pass

    def _on_state(self, device_id, payload):
        # A real Moxie sends /state on its connect: this re-onboards after a broker restart.
        self._device_connect(device_id)          # fallback if we missed the log line
        try:
            from moxie_sdk.cloud_config import parse_robot_status
            status = parse_robot_status(payload)
            robot = self.robots.get(device_id)
            if robot:
                if status.get("robot_firmware_version"):
                    robot.firmware = status["robot_firmware_version"]
                robot.extra["status"] = status      # battery/volume/wifi/mode for the UI
        except Exception:
            pass
