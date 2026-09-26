"""Run loop, status snapshot, and clean shutdown on SIGTERM/SIGINT."""
from __future__ import annotations
import time

from moxie_sdk import safety as safety_seam
from moxie_sdk import conn_telemetry as conn_seam
from moxie_sdk import roster as roster_seam
from moxie_sdk import brains as brain_seam
from .constants import KEEPALIVE_S


class LifecycleMixin:
    def status_snapshot(self) -> dict:
        """The supervisor + robots snapshot the parent console reads (JSON). Each robot
        carries its live state (battery/volume/wifi/mode/firmware) from the last /state."""
        robots = []
        permits = self.permits()
        open_fleet = self.allow_unverified_bots()
        for r in self.robots.values():
            st = r.extra.get("status", {})
            permitted = open_fleet or r.device_id in permits["devices"]
            robots.append({
                "device_id": r.device_id, "child": r.child.nickname,
                # The pairing gate, per robot: `pending` is a robot that reached the
                # broker but is not on the permit list — it is being served the minimal
                # child-free config and nothing else until a parent lets it in.
                "permitted": permitted, "pending": not permitted,
                "permit_label": (permits["devices"].get(r.device_id) or {}).get("label", ""),
                # Have we heard from this robot on the CURRENT broker connection? False
                # means "we served it before the outage and it has not spoken since" —
                # a ghost, labelled as one rather than removed, because our socket dying
                # is evidence about us and not about the robot.
                "seen_since_connect": r.device_id in self._seen_since_connect,
                "firmware": r.firmware or st.get("robot_firmware_version"),
                "battery_level": st.get("battery_level"),
                "audio_volume": st.get("audio_volume"),
                "wifi_ssid": st.get("wifi_ssid"), "mode": st.get("mode"),
                "ota_reboot_required": st.get("ota_reboot_required"),
                "config_overrides": self._config_overrides.get(r.device_id, {}),
                "config_effective": self.effective_config(r.device_id),
                # The face cache-buster as this robot's next /config push will carry it
                # (`child_pii.id`) — "" when no face is chosen and the field is omitted.
                # Surfaced so a parent (and a test) can see that changing the look really
                # did re-key the texture, without reading the MQTT wire.
                "face_cache_id": self.face_cache_id(r.device_id),
                # 🧠 Which brain answers this child, and which layer decided — the console
                # renders it beside the robot, and the SIL smoke asserts a per-robot swap
                # without reading the runtime's internals.
                "brain": self.brain_for(r.device_id)["brain"],
                "brain_source": self.brain_for(r.device_id)["source"],
                # Hydrated from `telemetry_packets.json` on first touch, so this is how
                # many events we *hold* for this robot — history included, not just what
                # arrived since the supervisor started.
                "telemetry_count": len(self._telemetry_buffer(r.device_id, r)),
                "safety_total": int((self.store.read(
                    r.device_id, safety_seam.COUNTS_COLLECTION, {}) or {}).get("total", 0)),
                "safety_unreviewed": sum(
                    1 for e in (self.store.read(
                        r.device_id, safety_seam.EVENTS_COLLECTION, []) or [])
                    if isinstance(e, dict) and not e.get("reviewed")),
            })
        from moxie_sdk.cloud_config import schedulable_module_ids
        from moxie_sdk.faces import face_catalog
        return {"ok": True, "app": self.app.name,
                # The appliance's own brain (`MOXIE_APP` ⊕ the fleet layer, resolved at
                # boot) and what the environment pins. `app` stays what it always was —
                # the object that is running — so nothing that read it has to change.
                "brain": brain_seam.sanitize_brain(self.app.name),
                "brain_pin": self.brain_pin(),
                "uptime_s": int(time.time() - self.started_at),
                "fleet_config": self.fleet_config(),
                "allow_unverified_bots": open_fleet,
                "pending_count": sum(1 for r in robots if r["pending"]),
                "schedule_modules": list(schedulable_module_ids()),
                # The appearance catalog the 🎨 card renders (audit ADOPT #9). Published
                # rather than hard-coded in the console so the two can never disagree
                # about which slots exist or which options are actually cited.
                "face_catalog": face_catalog(),
                # What we actually know about the broker (§4.1 C4 / §8 file 8). The
                # console's existing connection monitor renders these with **no console
                # change**, and `broker_connected` is the honest answer to the question
                # every other status field silently assumed: is there a socket at all?
                # The **recorded** state (what a CONNACK last told us), not a live probe
                # of the transport: a status page reports what happened, and the two only
                # ever differ for a test double with no socket to have an opinion about.
                "broker_connected": self.broker_connected,
                # …and whether the broker has ACKNOWLEDGED our subscriptions, which is the
                # field a harness should gate robot traffic on. `broker_connected` alone
                # was `sim/tools/soak.py`'s readiness wait — and its `resubscribed_after_s`
                # metric, a name that already claimed the thing the signal did not prove.
                "broker_subscribed": self.subscriptions_acked.is_set(),
                "last_broker_connect": self.last_broker_connect,
                "last_broker_disconnect": self.last_broker_disconnect,
                "last_connect_error": self.last_connect_error,
                "publish_drops": self.publish_drops,
                "store_lock_timeouts": getattr(self.store, "lock_timeouts", 0),
                # 🤖 How many robots this appliance has ever served (§8 P1). A count and
                # two timestamps rather than the ids: `/status` is polled every few seconds
                # and no card renders a fleet-sized id list. `GET /conn` carries the rest.
                "roster": roster_seam.summarize(self.roster()),
                # 🔌 The durable connection history's headline, so an operator can see
                # "up, but it dropped nine times this hour" without a second request.
                "connection_health": conn_seam.health(
                    conn_seam.summarize(self.conn_events(), limit=0),
                    connected=self.broker_connected),
                "robots": robots, "recent": list(self.recent)[-60:]}

    # ---- lifecycle ----
    def run(self, status_port: int = 8930):
        if self.client is None:
            self._build_client()
        self._start_status_server(status_port)
        print(f"[runtime] connecting to broker {self.host}:{self.port} · app={self.app.name}")
        self._note("info", f"supervisor started (app={self.app.name})")
        # **All three of these, or none of them.** A plain blocking `connect()` raises
        # `ConnectionRefusedError` / `socket.gaierror` straight out of `run()` when the
        # broker is not listening yet, and the supervisor process dies — survivable under
        # `docker compose up` only because `depends_on: condition: service_healthy` holds
        # the container back, and not survivable at all on bare metal, in the SIL harness,
        # or any time the broker restarts before our first connect.
        #
        # And `connect_async` **alone changes nothing**: `loop_forever()` defaults to
        # `retry_first_connection=False` and re-raises the first `OSError` from
        # `reconnect()` (A2, read out of the installed paho). `loop_start()` gets it right
        # only because its thread body passes the flag — which is why porting "add
        # connect_async" from a `loop_start()` codebase onto this one is a no-op. S6 in
        # `sim/tests/test_connection_resilience.py` exists to fail on exactly that
        # half-done fix rather than only on no fix at all.
        self._install_signal_handlers()
        self.client.connect_async(self.host, self.port, KEEPALIVE_S)
        self.client.loop_forever(retry_first_connection=True)
        # `loop_forever` returns when `disconnect()` was called — i.e. only on the clean
        # path below. A crash still raises out of it, which is what we want.
        print("[runtime] 👋 supervisor stopped", flush=True)

    # ---- clean shutdown (§8 P1) ----
    #: The signals a container runtime and a terminal actually send. SIGKILL is absent
    #: because it cannot be caught — which is the case the store's atomic `os.replace`
    #: covers instead, and the reason §5.3's A6 kills the writer 20 times.
    STOP_SIGNALS = ("SIGTERM", "SIGINT")

    def _install_signal_handlers(self) -> list:
        """Catch SIGTERM/SIGINT and close the broker connection **cleanly**.

        Without this, `docker stop` / `compose restart` / Ctrl-C kills the process with
        its TCP session still open, and the broker only notices when the keepalive expires:
        30 s keepalive → **45 s** before mosquitto declares us gone (§4.1 C2). For that
        three-quarters of a minute the broker holds a session for a `client_id="supervisor"`
        that no longer exists, and — the part that actually bites — `$SYS/broker/log` emits
        nothing, so `DISCONNECT_RE` never fires and a supervisor that comes back inside the
        window is talking past its own ghost.

        A `disconnect()` sends a DISCONNECT packet, the broker logs the close immediately,
        and `loop_forever()` returns instead of being torn down mid-callback.

        Returns the signal names actually installed. It is **not** an error to install
        none: `signal.signal` only works on the main thread of the main interpreter, and
        the runtime is legitimately embedded (the SIL harness, a test, a future
        supervisor-in-a-thread). Silently doing nothing there is right; silently doing
        nothing in the *container* is the bug, so what happened is printed either way.
        """
        import signal as _signal
        installed = []
        for name in self.STOP_SIGNALS:
            sig = getattr(_signal, name, None)
            if sig is None:
                continue
            try:
                _signal.signal(sig, self._on_stop_signal)
                installed.append(name)
            except (ValueError, OSError, RuntimeError):
                # Not the main thread, or a platform without it. Not fatal: the appliance
                # still runs, it just stops the old rude way.
                pass
        if installed:
            print(f"[runtime] clean shutdown armed ({', '.join(installed)})", flush=True)
        else:
            print("[runtime] ⚠️  clean shutdown NOT armed (signals unavailable here) — a "
                  "stop will look like a 45s keepalive timeout to the broker", flush=True)
        return installed

    def _on_stop_signal(self, signum, frame=None):
        try:
            import signal as _signal
            name = _signal.Signals(signum).name
        except (ValueError, ImportError):
            name = str(signum)
        print(f"[runtime] {name} — closing the broker connection cleanly", flush=True)
        self.request_stop(reason=name)

    def request_stop(self, reason: str = "stop") -> bool:
        """Begin a clean shutdown. Idempotent; True when this call started it.

        Separate from the signal handler so a test (and the SIL harness) can exercise the
        real path without sending a real signal to a real process — and so an embedder
        that installed no handlers still has a way to stop politely.

        The `shutdown` row is written **here**, before `disconnect()`, deliberately: once
        the socket is closing the store write is racing the interpreter's teardown, and a
        history whose last row is missing exactly when the appliance was stopped on purpose
        would be missing it in every case an operator cares about.
        """
        if self._stopping:
            return False
        self._stopping = True
        self._note("conn", f"👋 clean shutdown requested ({reason})")
        self._record_conn(conn_seam.SHUTDOWN, reason=reason)
        client = self.client
        if client is not None:
            try:
                client.disconnect()
            except Exception as e:
                print(f"[runtime] disconnect during shutdown failed: {e}", flush=True)
        return True
