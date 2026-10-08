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
                # Pending = on the broker but not permitted: served the child-free config.
                "permitted": permitted, "pending": not permitted,
                "permit_label": (permits["devices"].get(r.device_id) or {}).get("label", ""),
                # False = served before a broker outage and silent since (a ghost, kept
                # because our socket dying says nothing about the robot).
                "seen_since_connect": r.device_id in self._seen_since_connect,
                # When we last asked this robot to stream its microphone (the STT
                # `ProtoSubscribe`) with live evidence of its session; None = not asked
                # since it was last confirmed on this connection (an ask to a ghost may
                # have reached nobody, so it is not recorded). An ask, not an ack.
                "stt_subscribed_at": r.extra.get("stt_subscribed_at"),
                # Utterances the honest ears answered with no speech (digital silence, a
                # sound label, a phantom "Bye."; voice.py). In memory, on the robot's
                # record: the broker saying it left, or a supervisor restart, starts it at 0.
                "stt_dropped": int(r.extra.get("stt_dropped") or 0),
                "firmware": r.firmware or st.get("robot_firmware_version"),
                "battery_level": st.get("battery_level"),
                "audio_volume": st.get("audio_volume"),
                "wifi_ssid": st.get("wifi_ssid"), "mode": st.get("mode"),
                "ota_reboot_required": st.get("ota_reboot_required"),
                "config_overrides": self._config_overrides.get(r.device_id, {}),
                "config_effective": self.effective_config(r.device_id),
                # The face cache-buster the next /config push carries ("" = no face).
                "face_cache_id": self.face_cache_id(r.device_id),
                # Which brain answers this child, and which layer decided.
                "brain": self.brain_for(r.device_id)["brain"],
                "brain_source": self.brain_for(r.device_id)["source"],
                # Includes history hydrated from telemetry_packets.json.
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
                # The appliance's own brain (MOXIE_APP + fleet layer) and the env pin.
                "brain": brain_seam.sanitize_brain(self.app.name),
                "brain_pin": self.brain_pin(),
                "uptime_s": int(time.time() - self.started_at),
                "fleet_config": self.fleet_config(),
                "allow_unverified_bots": open_fleet,
                "pending_count": sum(1 for r in robots if r["pending"]),
                "schedule_modules": list(schedulable_module_ids()),
                # The appearance catalog the console renders (single source of truth).
                "face_catalog": face_catalog(),
                # Recorded broker state (§4.1): what the last CONNACK said, not a live probe.
                "broker_connected": self.broker_connected,
                # SUBACK seen — what a harness should gate robot traffic on.
                "broker_subscribed": self.subscriptions_acked.is_set(),
                "last_broker_connect": self.last_broker_connect,
                "last_broker_disconnect": self.last_broker_disconnect,
                "last_connect_error": self.last_connect_error,
                "publish_drops": self.publish_drops,
                "store_lock_timeouts": getattr(self.store, "lock_timeouts", 0),
                # Robots ever served: a count + timestamps (`GET /conn` has the ids).
                "roster": roster_seam.summarize(self.roster()),
                # Durable connection history headline (e.g. "up, but dropped 9 times").
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
        # connect_async + retry_first_connection=True, or a broker that is not up yet kills
        # the process: loop_forever() otherwise re-raises the first OSError (§4.1 A2).
        self._install_signal_handlers()
        self.client.connect_async(self.host, self.port, KEEPALIVE_S)
        self.client.loop_forever(retry_first_connection=True)
        # loop_forever returns only after disconnect(); a crash still raises.
        print("[runtime] 👋 supervisor stopped", flush=True)

    # ---- clean shutdown (§8 P1) ----
    #: SIGKILL cannot be caught; the store's atomic `os.replace` covers that case.
    STOP_SIGNALS = ("SIGTERM", "SIGINT")

    def _install_signal_handlers(self) -> list:
        """Catch SIGTERM/SIGINT and close the broker connection cleanly.

        Without it the broker notices only at keepalive expiry (45 s) and logs no
        disconnect, so a quickly restarted supervisor talks past its own ghost session.
        Returns the signal names installed — none off the main thread (embedded/tests),
        which is fine; the outcome is printed either way.
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
                # Not the main thread / unsupported platform: run without it.
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

        Callable without a signal (tests, embedders). The `shutdown` row is written before
        `disconnect()` so it is not racing interpreter teardown.
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
