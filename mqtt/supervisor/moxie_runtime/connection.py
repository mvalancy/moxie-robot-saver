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
        # The supervisor's broker credential (security-broker-auth.md §2.2). It is the
        # ONE fleet-wide identity: `$SYS/broker/log` — the connect watch below — and any
        # write outside a client's own device subtree are supervisor-only once the ACL
        # is loaded. Unset (a bare-metal dev broker, the SIL harness, CI) leaves this an
        # anonymous client, byte-for-byte what it was before.
        try:
            from config import broker_credentials
            username, password = broker_credentials()
        except Exception:                      # config not importable → anonymous
            username, password = "", ""
        if username and password:
            self.client.username_pw_set(username, password)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        # The SUBACK. Without this callback the appliance could only ever *assume* it was
        # subscribed, which is what made `[runtime] broker connected` a promise the
        # runtime had no way to keep (see `_on_subscribe`).
        self.client.on_subscribe = self._on_subscribe
        # After a successful first connect paho already reconnects on its own; what it
        # does NOT do is tell anyone. These two callbacks are the difference between "the
        # appliance recovered" and "nobody knows why the robot went quiet" (§4.1 C4).
        self.client.on_disconnect = self._on_disconnect
        self.client.on_connect_fail = self._on_connect_fail
        self.client.reconnect_delay_set(min_delay=RECONNECT_MIN_DELAY_S,
                                        max_delay=RECONNECT_MAX_DELAY_S)
        return self.client

    #: Everything the supervisor listens to. Re-installed on **every** successful CONNACK,
    #: because the broker drops subscriptions with the session.
    SUBSCRIPTIONS = ("/devices/+/events/#", "/devices/+/state",
                     "$SYS/broker/log/#", "$SYS/broker/clients/#")

    @staticmethod
    def _connack_failed(rc) -> bool:
        """True when a CONNACK refused us.

        `rc` is a paho `ReasonCode` under `CallbackAPIVersion.VERSION2` and a plain int
        under VERSION1, so ask the object first and fall back to the integer comparison.
        """
        failed = getattr(rc, "is_failure", None)
        if failed is not None:
            return bool(failed)
        try:
            return int(rc) != 0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _suback_failed(rc) -> bool:
        """True when one entry of a SUBACK **refused** a filter.

        The twin of `_connack_failed`, and it exists for the twin reason: an
        acknowledgement is not a yes. MQTT 3.1.1 returns `0x80` where a granted QoS would
        be; MQTT 5 returns a `ReasonCode` whose `is_failure` says so. A broker ACL that
        does not grant the supervisor `/devices/+/state` (security-broker-auth.md §2.2)
        answers exactly like this, and an appliance that armed readiness on it would be
        deaf and confident — `broker connected rc=5` again, one callback later.
        """
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
        """A CONNACK arrived — and it is not necessarily a *yes*.

        This used to print `broker connected rc={rc}` and subscribe unconditionally, so a
        refusal (`rc=5`, *not authorised*, which the supervisor's broker credential made
        reachable for the first time) logged the words **"broker connected"** and then
        subscribed into a socket the broker was closing. Same class of bug as a route that
        reports success for a publish that never happened: a comfortable lie in the one
        place an operator looks. Behaviour ported from Fork A's `moxie_server.py`:206-215
        (MIT, © Justin Beghtol — read as prior art, no code copied).
        """
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
        # The gap is measured BEFORE `last_broker_connect` moves, and from the recorded
        # disconnect rather than from a fresh clock read, so it is the outage the appliance
        # actually experienced. `None` on the very first connect: an appliance that has
        # never dropped must not report a zero-second outage as its first history row.
        gap = conn_seam.gap_since(self.last_broker_disconnect, now)
        self.broker_connected = True
        self.last_broker_connect = now
        self.last_connect_error = ""
        self._connect_generation += 1
        # SUBSCRIBE FIRST, THEN SAY SO. `helpers_stack.Supervisor.start()` waits for this
        # exact line before letting a robot announce itself, so printing it first meant the
        # supervisor advertised readiness it did not yet have: the SIL robot's single
        # `/state` could land in the gap and go unheard, and `test_live_gateway_turn_e2e`
        # failed as "no config pushed within timeout" — twice, and only on a quiet box,
        # because a busy one is slow enough to lose the race.
        #
        # That is this function's own lesson applied to itself. The docstring above
        # explains why `rc=5` must not print "broker connected"; announcing before
        # subscribing is the same comfortable lie one step later, and playbook rule 23's
        # shape — a readiness signal that was true of an earlier moment.
        #
        # …AND IT WAS STILL A LIE, ONE STEP FURTHER ALONG (2026-09-05). `subscribe()` does
        # not subscribe: it generates a mid, queues a SUBSCRIBE packet and returns, and
        # under `loop_forever()` the bytes leave on the network thread *after* this
        # callback returns. So the line below has always meant **"we asked"**, never "the
        # broker agreed" — and a robot that announces itself in that gap publishes
        # `/state` into a broker with no matching subscription. The answer to a `/state`
        # is a config push at **QoS 0, not retained** (`_publish`; QoS 1 refused on
        # purpose, production-hardening.md §4.3), so losing that race does not delay the
        # message, it DELETES it. That is why no timeout is ever long enough, and it is
        # the same defect PR #143 fixed on the robot side of the same wire.
        #
        # ONE call, not four: a list subscribe is one SUBSCRIBE packet answered by one
        # SUBACK, so `_on_subscribe` has a single unambiguous event to wait for and
        # nothing has to count acknowledgements. (`_on_connect` is the only `subscribe()`
        # call site in the runtime — `test_connect_readiness.py` pins that, because a
        # second one would make an unmatched SUBACK arm this event early.)
        self.subscriptions_acked.clear()   # a reconnect re-subscribes: re-arm, not latch
        c.subscribe([(t, 0) for t in self.SUBSCRIPTIONS])
        # …and FLUSHED, for the same reason it is printed last. This line is not log
        # decoration: `helpers_stack.Supervisor.start()`, `sim/run_smoke.sh`,
        # `sim/run_scenarios.sh`, `sim/run_broker_outage.sh` and `sim/tools/soak.py` all
        # BLOCK on it. Every one of them redirects this process's stdout to a file, where
        # Python is block-buffered, so without this the line sits in an 8 KB buffer and a
        # waiter times out on a supervisor that connected long ago — a boot failure
        # disguised as a slow boot. Four callers each carried `PYTHONUNBUFFERED=1` to
        # compensate; that is four guards covering for one missing keyword, and the fifth
        # caller (a straightforward rewrite of run_smoke.sh, 2026-09-03) forgot it and
        # waited the full 40 s. The refusal branch above has always flushed; the success
        # branch not flushing was the asymmetry.
        #
        # ITS MEANING IS UNCHANGED and deliberately so: *"a CONNACK said yes and we have
        # asked for our topics"*. Everything that reads it for that — `/status`'s
        # `broker_connected`, the console's connection card, `test_connection_resilience`'s
        # rc=5 guard — is still right. What must NOT key on it is anything that then puts
        # traffic on the bus expecting us to hear it; that waits for the SUBACK line in
        # `_on_subscribe` below.
        print(f"[runtime] broker connected rc={rc}", flush=True)
        self._note("conn", "broker connected" if gap is None
                   else f"broker reconnected after {gap:.1f}s")
        self._record_conn(conn_seam.CONNECT, at=now, gap_s=gap)
        # Off the network thread, and after the subscriptions are in: a config push is a
        # publish, and `_device_connect` has always used the same one-second settle timer
        # for the same reason. Blocking the MQTT loop on a roster-sized burst of publishes
        # would stall every robot the reconnect just recovered.
        self._schedule_roster_resume()

    def _on_subscribe(self, c, u, mid, reason_codes=None, properties=None):
        """The SUBACK. **This** is the moment the supervisor can hear a robot.

        Everything that boots a robot against this appliance must wait for the line
        printed here rather than for `broker connected` one callback earlier. The
        difference is not milliseconds of politeness: between the two, a `/state` is
        delivered to nobody, its config answer is never generated, and the robot waits out
        a timeout for a message that does not exist. Observed as
        `❌ scenario 'basic-conversation': 0/4 turns OK — no config pushed within timeout`
        with the *second* scenario green in the same job — first-fails-second-passes is a
        startup race, never a scenario bug.

        Signature carries defaults for both paho callback API versions: VERSION2 passes
        `(client, userdata, mid, reason_code_list, properties)`, VERSION1 `(client,
        userdata, mid, granted_qos)`.

        Idempotent, because one SUBSCRIBE gets one SUBACK but nothing about MQTT forbids a
        broker from being generous; the *first* ack is the one that made us audible.

        And an ack can say **no**. A SUBACK carries one code per filter, and `0x80` (MQTT
        5: a failure `ReasonCode`) is a refusal — which is what a broker ACL that does not
        grant this credential `/devices/+/state` returns. Arming readiness on that would
        be the original bug with an extra callback in front of it, so a refusal is said out
        loud and readiness is withheld: the harnesses then time out with the reason
        printed above them, instead of a robot silently failing to be heard.
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
        # Flushed for exactly the reason the line above it is: every waiter reads this
        # from a redirected, block-buffered stdout.
        print(f"[runtime] subscriptions acknowledged by the broker "
              f"({len(self.SUBSCRIPTIONS)} topics)", flush=True)

    def _on_disconnect(self, c, u, flags=None, rc=None, props=None):
        """The socket went away. Two jobs, and the second is the subtle one.

        1. Record it, so a gap is a thing an operator can see rather than a silence.
        2. **Stale every in-flight turn.** `_turn_seq` already numbers turns per robot and
           `_is_stale` already suppresses an answer whose child has moved on, at seven call
           sites; bumping the sequence here reuses that machinery whole. The alternative —
           letting the answer out after the gap — is actively harmful under the recovered
           contract: the robot re-prompts (~20 s) with a **new** `event_id`, so the child
           would hear the answer to the question they gave up on arriving after the answer
           to the one they asked instead. That is precisely what `_is_stale` was written to
           prevent, and a reconnect is not a reason to re-open it.

        The `_turn_seq` invariant (*"the MQTT loop is the only writer here"*) survives:
        `on_disconnect` is dispatched from the network loop, which under `loop_forever()`
        is that same thread (A19).
        """
        was = self.broker_connected
        self.broker_connected = False
        # Our subscriptions died with the socket (the SIL/CI brokers run `clean_session`),
        # so the SUBACK we hold is a belief about a connection that no longer exists —
        # the same class as `_forget_robot_state()` below. Re-armed, not latched: the next
        # `_on_connect` re-subscribes and the next SUBACK re-earns it.
        self.subscriptions_acked.clear()
        self.last_broker_disconnect = time.time()
        reason = self._connack_reason(rc) if rc is not None else "connection lost"
        for device_id in set(self._turn_seq) | set(self.robots):
            self._turn_seq[device_id] = self._turn_seq.get(device_id, 0) + 1
        # Every robot is now **unconfirmed**, and every vision subscription we believed we
        # held is now a belief rather than a fact: we lost *our* socket, and that is
        # evidence about us, not about them. See `_device_connect` for why the robots
        # themselves are not removed, and `_forget_robot_state` for why these two caches
        # are cleared together.
        self._forget_robot_state()
        if self._stopping:
            # A disconnect we asked for. Recorded as a `shutdown`, not a fault: an operator
            # reading a history where every planned stop looks like an outage learns
            # nothing from the outages.
            # The `shutdown` row was already written by `request_stop()`, before the
            # socket started closing — writing a second one here would double every clean
            # stop in the history and make `by_kind` count intentions instead of events.
            print("[runtime] 👋 broker connection closed cleanly (shutting down)", flush=True)
            self._note("conn", "👋 broker connection closed cleanly (shutting down)")
            return
        if was:
            print(f"[runtime] ⚠️  broker disconnected: {reason}", flush=True)
            self._note("conn", f"⚠️ broker disconnected: {reason} — "
                               f"{len(self.robots)} robot(s) in flight abandoned")
            self._record_conn(conn_seam.DISCONNECT, reason=reason)

    def _on_connect_fail(self, c, u=None):
        """The socket never opened (broker down, DNS gone). Distinct from a CONNACK
        refusal and from a disconnect, and without it the retry loop is invisible — which
        makes *"it is just sitting there"* the bug report."""
        self.broker_connected = False
        self.last_connect_error = f"could not reach the broker at {self.host}:{self.port}"
        # Printed as well as `_note`d. Found by starting a real supervisor before a real
        # broker: `recent` had the four retries and **stdout had nothing**, so anyone
        # tailing `docker logs` saw a process that had said "connecting to broker" and
        # then gone silent — which reads exactly like the hang this change removes. The
        # backoff throttles it for us: at 1, 2, 4 … 60 s this is at worst a line a minute.
        print(f"[runtime] ⛔ {self.last_connect_error} — retrying", flush=True)
        self._note("error", f"⛔ {self.last_connect_error} — retrying")
        self._record_conn(conn_seam.CONNECT_FAIL, reason=self.last_connect_error)

    def _on_store_lock_timeout(self, lock_path, waited):
        """A store write another **process** would not let go of. Recorded, never retried
        forever and never swallowed (production-hardening.md §3.3 #3).

        Also the row that measures A13: `MOXIE_STORE_LOCK_TIMEOUT_S = 2.0` is the brief's
        one openly *chosen* number, and a `lock_timeout` row carrying `waited_s` is the
        only evidence that could ever retune it.
        """
        if getattr(self, "recent", None) is None:
            return
        self._note("error", f"⏳ a store write was refused after {waited:.1f}s — another "
                            f"process holds {os.path.basename(lock_path)}")
        self._record_conn(conn_seam.LOCK_TIMEOUT, waited_s=waited,
                          reason=os.path.basename(lock_path))

    # ---- the connection's own durable history (§8 P1) ----
    def _record_conn(self, kind: str, **fields) -> bool:
        """Append one row to the appliance's `fleet/conn_events` ring. Never raises.

        Three properties, each of which is a bug somewhere else in this file's history:

        1. **Re-entrant calls are dropped, not recursed.** The `lock_timeout` recorder
           writes to the store, and a store under contention is exactly when it fires.
        2. **Every failure is swallowed here and nowhere else.** This runs on the paho
           network thread (`_on_connect`, `_on_disconnect`) and inside `_publish`; a
           telemetry write must never cost a child their turn, still less take the MQTT
           loop down. The bounded `flock` wait (§3.3 #3) is what makes that safe to say.
        3. **It is best-effort by construction and the return value says so**, so a caller
           that cares (the tests, the soak) can tell "recorded" from "we were busy".
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
        """The 🔌 connection view: the durable ring rolled up, beside P0's live scalars.

        Both halves on purpose. The scalars are what is true **now** and the rows are what
        has **happened**, and an operator staring at a robot that has gone quiet needs to
        tell "it is down right now" from "it dropped nine times this hour and is up at the
        moment", which neither half answers alone.
        """
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
        """Every robot this appliance has served, read from the store each time so a
        second process's edit (or a hand-edited `fleet/roster.json`) is picked up — the
        same promise `fleet_config()` already makes."""
        return self.store.read_shared(roster_seam.COLLECTION, roster_seam.new_roster())

    def _roster_seen(self, device_id: str) -> bool:
        """Record that we are serving `device_id`. Read-modify-write under the record's
        own lock, so two supervisors on one data directory cannot lose each other's
        robots — which is precisely the `append()` bug §3 exists to fix, in a new place."""
        try:
            with self.store.transaction_shared(roster_seam.COLLECTION):
                current = self.roster()
                self.store.write_shared(roster_seam.COLLECTION,
                                        roster_seam.record_seen(current, device_id))
            return True
        except Exception:
            return False

    def _roster_forget(self, device_id: str) -> bool:
        """Drop `device_id` from the roster — a parent un-pairing a robot has said this
        appliance no longer serves it, and a roster that kept publishing config at a robot
        the family gave away would be the permit list quietly not applying."""
        try:
            with self.store.transaction_shared(roster_seam.COLLECTION):
                self.store.write_shared(roster_seam.COLLECTION,
                                        roster_seam.forget(self.roster(), device_id))
            return True
        except Exception:
            return False

    def resume_roster(self) -> list:
        """Re-push config to every rostered robot we have no live evidence of. Returns the
        ids pushed to.

        This is what makes P0's C6 *prompt* instead of merely *possible*. C6 registers an
        unknown device when it next speaks; the roster does not wait for it to speak.
        After a supervisor restart with the robot still connected there is no event to
        wait for at all — `$SYS/broker/log` is live-only (A15) and a real Moxie publishes
        `/state` on **its** connect, not on ours — so without this the appliance is silent
        until the child is, which at bedtime is tomorrow.

        **It does not mark anybody connected.** A rostered robot stays out of
        `self.robots`; a QoS 0 push to a robot that is not there is discarded by the broker
        and claims nothing. Inventing presence to make a card look populated is the exact
        disease this brief was written about.
        """
        if not roster_seam.resume_enabled():
            return []
        # `_seen_since_connect`, **not** `self.robots`: after a broker restart every robot
        # is still in `self.robots` and none of them is confirmed, so subtracting the
        # remembered set would make this resume push to nobody in exactly the case it was
        # built for. It is the same conflation `_device_connect` used to make.
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

    #: Seconds between a successful CONNACK and the roster re-push. The same 1.0 s
    #: `_device_connect` has always used to let a robot settle before its config lands.
    ROSTER_RESUME_DELAY_S = 1.0

    def _schedule_roster_resume(self):
        """Run `resume_roster()` off the network thread, once per connect generation.

        The generation check is what makes a reconnect **storm** safe: paho's ladder can
        fire several CONNACKs inside the settle window on a flapping link, and without it
        each one would queue its own full-roster burst of publishes at a broker that is
        already struggling. The last generation to be scheduled is the only one that runs.
        """
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
        """Is there really a socket?

        `if self.client is None` — the guard this replaces — asks whether an *object*
        exists, which stays true for the whole life of the process. paho's
        `is_connected()` is the transport's own answer. A transport double with no opinion
        (the SIL loopback) is trusted, because it has no socket to be wrong about.
        """
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

    #: The sentence a route hands a parent when the appliance has no broker. One place,
    #: because it is the same fact whichever button they pressed.
    NO_BROKER_REASON = "The supervisor is not connected to the broker."

    def _publish(self, topic: str, payload, *, device_id: str = "", what: str = ""):
        """Publish one message and **read the return code**. Returns `(ok, reason)`.

        All eight publish sites used to be `self.client.publish(...)` with the result
        thrown away. At QoS 0 paho calls `_send_publish` directly and returns
        `MQTT_ERR_NO_CONN` on `info.rc` when there is no socket — the message is *not*
        queued (A3) — so a reply published during a gap was discarded and nothing in this
        process knew. QoS 0 stays (§4.3: a QoS 1 queue would deliver exactly the stale
        answers §4.2 just decided are harmful); what changes is that a drop is now a fact
        the appliance holds rather than one it never learns.
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
        # Filed in the appliance-wide ring even though it carries a device id, so the
        # sequence `disconnect → three drops → connect after 4.2 s` reads in order. That
        # ordering is the whole reason a stream beats six scalars.
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
                    # The pairing gate lives on the transport boundary, so there is ONE
                    # place a device that is not permitted can be refused service — no
                    # handler can forget it. `/state` is deliberately still processed:
                    # it is how an unknown robot becomes visible as *pending* at all.
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

        **Idempotent per broker connection, not per process** — and that distinction is a
        bug fix, not a refinement. This used to early-return on `device_id in self.robots`,
        and the only thing that ever *removed* a robot was `_device_disconnect`, which is
        driven by a `$SYS/broker/log` line and therefore **dies with the broker**. So a
        robot that came back after a broker restart with the same device id was already
        "known", was never re-onboarded, and got **no config push and no `app.on_connect`**
        — silently half-connected for the rest of the session. Reproduced 4/4 by
        `sim/run_broker_outage.sh` phase 5c.

        **Why not simply drop the robots on `on_disconnect`?** It is the obvious fix and it
        is the wrong one, for three reasons:

        1. *It claims knowledge we do not have.* Our socket died; the robot's did not
           necessarily. "The supervisor dropped, therefore the robot is gone" is a belief,
           and a `/status` that reports beliefs as observations is the disease this whole
           brief exists to cure.
        2. *It stampedes on a blip.* A 200 ms flap would drop every robot and re-onboard
           the lot on their next packet — N config pushes and N `on_connect`s at a broker
           that has just come back, for an outage nobody noticed.
        3. *It would have to lie about the conversation.* Removal runs through
           `_device_disconnect`, which fires `app.on_disconnect` and `_end_conversation`.
           Ending a child's session because *we* lost the broker is a worse error than the
           one being fixed; skipping those and removing anyway leaves the app's model of
           the session and ours disagreeing.

        So the roster is not cleared — **confirmation** is. Nothing happens until the robot
        gives us real evidence it is there (a `/state`, an event), and then exactly one
        robot is re-onboarded: the one that actually came back. The `RobotContext` is
        **reused**, so `history`, the telemetry buffer, presence and every other per-robot
        thing survive the outage — a returning child continues their conversation rather
        than meeting a stranger.
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
        # The one place every ingress path converges — the broker log line, `_on_state`'s
        # fallback and P0's C6 all land here — so it is the one place the roster has to be
        # written for "every robot we have ever served" to be true.
        self._roster_seen(device_id)
        # Push config after a short settle delay, WITHOUT blocking the MQTT loop.
        def _settle():
            self._push_config(device_id)
            # A pending (unpermitted) robot never reaches the app: the brain is a service
            # this appliance provides to the family's robot, not to whatever connected.
            # `set_permit` runs `on_connect` at the moment a parent lets it in.
            if not self.is_permitted(device_id):
                return
            try:
                self.app_for(device_id).on_connect(robot)
            except Exception as e:
                print(f"[runtime] app.on_connect error: {e}", flush=True)
        threading.Timer(1.0, _settle).start()

    # ---- one place that forgets what we believe about a robot's state ----
    #
    # Two defects found within a day of each other turned out to be the same defect:
    #
    #   * `_device_connect` early-returned on `device_id in self.robots` — *"already
    #     onboarded"* — so a robot returning after a broker restart was never re-onboarded;
    #   * `_vision_subscribed[device_id]` — *"already subscribed"* — was never cleared, so
    #     after a module exit, a sleep/wake or an outage the robot had silently dropped the
    #     subscription while our latch still said we held it, and the vision and QR events
    #     went nowhere.
    #
    # Both are **a cached belief about the robot's state outliving the robot's actual
    # state**, and both caches are pure optimisation: the thing they save is one config
    # push and one `EventSubscription.active[]` list on a reply. So the asymmetry that
    # settles the design is the cost of being wrong in each direction — being wrong by
    # *forgetting* costs a redundant message; being wrong by *remembering* costs a robot
    # that is silently half-connected, or eyes that never report, with nothing logged
    # either way. When in doubt, forget.
    #
    # `_seen_since_connect` and `_vision_subscribed` have deliberately different
    # lifetimes, and the shorter one is a **strict subset** of the longer: everything that
    # breaks connection continuity invalidates both, and the vision latch has one extra
    # invalidator (a module exit) that says nothing about whether the robot is connected.
    # That is why there is one shared method plus one extra call site, rather than two
    # independent patches that can drift apart.
    #
    # What is deliberately NOT forgotten here: `history`, memory, the telemetry buffer,
    # presence and the `RobotContext` itself. Those are the robot's **data**, not our
    # belief about its state, and a child mid-conversation when the broker blinked must
    # continue it rather than meet a stranger.
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
        # The broker told us this client went away: that is real evidence about the robot,
        # so everything we believed about its state goes with it.
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
        # Unconditional: `_device_connect` is idempotent per broker connection now, and
        # this is the packet a real Moxie sends on **its** connect — so it is the evidence
        # that re-onboards a robot the broker restart made us forget we could see.
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
