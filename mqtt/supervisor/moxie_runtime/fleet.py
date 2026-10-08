"""Fleet/per-robot config, the device permit list, config push, wake and rehearsal preview."""
from __future__ import annotations
import json, os, time

from moxie_sdk.types import ResultCode
from moxie_sdk.wire import build_activity_response, build_remote_modules, is_module_query
from moxie_sdk import safety as safety_seam
from moxie_sdk import performance as performance_seam
from markup import perform


class FleetMixin:
    # ---- config push / edit (parent console) ----
    FLEET_CONFIG_COLLECTION = "config"          # → $MOXIE_DATA_DIR/fleet/config.json

    def fleet_config(self) -> dict:
        """Appliance-wide default overrides (house rules, audit ADOPT #6). Re-read each
        time so another process's edit lands on the next push. `{}` when never set."""
        cfg = self.store.read_shared(self.FLEET_CONFIG_COLLECTION, {})
        return dict(cfg) if isinstance(cfg, dict) else {}

    def effective_config(self, device_id) -> dict:
        """`fleet + per-robot` override layers (builder defaults sit underneath)."""
        from moxie_sdk.cloud_config import merge_config_layers
        return merge_config_layers(self.fleet_config(),
                                   self._config_overrides.get(device_id, {}))

    def face_cache_id(self, device_id) -> str:
        """The `child_pii.id` face cache-buster the next `/config` push will carry
        (`moxie_sdk/faces.py`), from the same layers `_push_config` uses. `""` = no face."""
        face = (self.effective_config(device_id) or {}).get("face")
        if not face:
            return ""
        from moxie_sdk.faces import face_child_id, face_options_list, validate_face
        try:
            labels = face_options_list(validate_face(face))
        except ValueError:
            return ""
        if not labels:
            return ""
        child = (self.robots[device_id].child if device_id in self.robots else self.child)
        return face_child_id(labels, child_key=child.nickname)

    def update_fleet_config(self, **overrides):
        """Fleet config edit: merge into the appliance-wide defaults, persist, and re-push
        every connected robot. Per-robot overrides still win."""
        cfg = self.fleet_config()
        cfg.update(overrides)
        self.store.write_shared(self.FLEET_CONFIG_COLLECTION, cfg)
        self._note("config", f"⚙️  fleet config updated: {', '.join(overrides) or '—'}")
        # A NO_DATA house rule erases what is already on disk, for every robot on the box.
        self.purge_transcripts()
        self.purge_telemetry()
        for device_id in list(self.robots):
            self._push_config(device_id)
        return cfg

    # ---- the pairing gate: which devices this appliance serves ----
    # The broker accepts anonymous connections (the robot's JWT is never verified,
    # mqtt-and-conversation.md §3b), so reaching the port must not earn the child's
    # `child_pii`. A durable permit list, closed by default — the idea of OpenMoxie's
    # `permit` / `allow_unverified_bots` (MIT, see ATTRIBUTION.md), enforced here.
    FLEET_PERMITS_COLLECTION = "permits"        # → $MOXIE_DATA_DIR/fleet/permits.json

    def permits(self) -> dict:
        """The durable permit record, normalized:
        `{"allow_unverified_bots": bool, "devices": {device_id: {permitted_at, label}}}`.

        Hot path (every inbound message), so the parse is memoized on the file's
        `(mtime, size)`; another process's edit is still picked up. A missing or corrupt
        file reads as nothing permitted — fails closed."""
        path = self.store.shared_path(self.FLEET_PERMITS_COLLECTION)
        try:
            st = os.stat(path)
            key = (path, st.st_mtime_ns, st.st_size)
        except OSError:
            key = (path, None, None)
        cached = self._permits_cache
        if cached is None or cached[0] != key:
            rec = self.store.read_shared(self.FLEET_PERMITS_COLLECTION, {})
            if not isinstance(rec, dict):
                rec = {}
            devices = rec.get("devices")
            if not isinstance(devices, dict):
                devices = {}
            cached = (key, bool(rec.get("allow_unverified_bots")),
                      {str(k): (v if isinstance(v, dict) else {})
                       for k, v in devices.items()})
            self._permits_cache = cached
        # A fresh mapping: callers mutate it, the cache must not be edited through them.
        return {"allow_unverified_bots": cached[1], "devices": dict(cached[2])}

    def allow_unverified_bots(self) -> bool:
        """True when this appliance serves any robot that connects. Precedence: the
        constructor arg; `MOXIE_ALLOW_UNVERIFIED_BOTS` (migration switch); the durable fleet
        flag from the console; else False."""
        if self._allow_unverified_bots is not None:
            return bool(self._allow_unverified_bots)
        env = (os.environ.get("MOXIE_ALLOW_UNVERIFIED_BOTS") or "").strip().lower()
        if env:
            return env not in ("0", "off", "false", "no")
        return self.permits()["allow_unverified_bots"]

    def is_permitted(self, device_id) -> bool:
        """May this device be served the child's config and the brain?"""
        return self.allow_unverified_bots() or str(device_id) in self.permits()["devices"]

    def pending_robots(self) -> list:
        """Connected-but-unpermitted device ids (the console's "Pending robots")."""
        return [d for d in self.robots if not self.is_permitted(d)]

    def set_permit(self, device_id, permitted: bool = True, label: str = "") -> dict:
        """Permit or revoke one device durably, and re-push its config now (full config
        on permit, the minimal un-paired document on revoke)."""
        device_id = str(device_id or "").strip()
        if not device_id:
            raise ValueError("device_id is required")
        rec = self.permits()
        if permitted:
            rec["devices"][device_id] = {"permitted_at": int(time.time()),
                                         "label": str(label or "")}
        else:
            rec["devices"].pop(device_id, None)
        self.store.write_shared(self.FLEET_PERMITS_COLLECTION, rec)
        self._permits_cache = None      # our own write invalidates outright,
                                        # never trusting mtime granularity
        self._note("permit", f"{'✅ permitted' if permitted else '⛔ revoked'} {device_id}")
        if device_id in self.robots:
            self._push_config(device_id)
            if permitted:
                try:
                    self.app_for(device_id).on_connect(self.robots[device_id])
                except Exception as e:
                    print(f"[runtime] app.on_connect error: {e}", flush=True)
        return self.permits_view()

    def set_allow_unverified_bots(self, allowed: bool) -> dict:
        """The fleet-wide "serve any robot" toggle; re-pushes every connected robot."""
        rec = self.permits()
        rec["allow_unverified_bots"] = bool(allowed)
        self.store.write_shared(self.FLEET_PERMITS_COLLECTION, rec)
        self._permits_cache = None      # our own write invalidates outright,
                                        # never trusting mtime granularity
        self._note("permit", f"🔓 allow_unverified_bots={bool(allowed)}"
                             if allowed else "🔒 allow_unverified_bots=False")
        for device_id in list(self.robots):
            self._push_config(device_id)
        return self.permits_view()

    def permits_view(self) -> dict:
        """Console permit view: the enforced flag (env included), the stored flag, the
        permit list and the pending robots."""
        rec = self.permits()
        return {"ok": True,
                "allow_unverified_bots": self.allow_unverified_bots(),
                "allow_unverified_bots_stored": rec["allow_unverified_bots"],
                "permits": [{"device_id": d,
                             "permitted_at": v.get("permitted_at"),
                             "label": v.get("label") or ""}
                            for d, v in sorted(rec["devices"].items())],
                "pending": sorted(self.pending_robots()),
                "connected": sorted(self.robots)}

    # All a pending device may hear: one fixed line that names no one.
    NOT_PAIRED_LINE = ("I'm not connected to a family yet. "
                       "Ask a grown-up to add me in the Moxie console.")

    def _serve_unpermitted(self, device_id, name, payload):
        """Everything a not-permitted device gets on `/events/…`:
        * remote-chat prompt -> one fixed child-free line (no brain, no history); `notify`
          is dropped.
        * activity-log queries (`schedule`, `mentor_behaviors`, `license`) -> an empty
          CloudQueryResponse so the robot's pull resolves; reports are dropped.
        * everything else (zmq audio, telemetry, vision, lifecycle) -> dropped.
        """
        if name.startswith("remote-chat"):
            try:
                rcr = json.loads(payload)
            except Exception:
                return
            if rcr.get("command") == "notify":
                return
            backend = rcr.get("backend", "router")
            if is_module_query(rcr):
                return self._publish_chat(device_id, rcr.get("event_id"), backend, "",
                                          markup="", result=ResultCode.SUCCESS,
                                          query_data=build_remote_modules([]))
            self._note("permit", f"⛔ turn refused — {device_id} is pending")
            line, scored = self._stage(self.NOT_PAIRED_LINE)
            return self._publish_chat(device_id, rcr.get("event_id"), backend,
                                      self.NOT_PAIRED_LINE, markup=line,
                                      end_turn=True, scored=scored)
        if name == "client-service-activity-log":
            try:
                data = json.loads(payload)
            except Exception:
                return
            query = data.get("query")
            if data.get("subtopic") in (None, "", "query") and query in (
                    "schedule", "mentor_behaviors", "license"):
                resp = build_activity_response(query, None,
                                               request_id=data.get("request_id"))
                self._publish(f"/devices/{device_id}/commands/query_result", resp,
                              device_id=device_id, what="query_result")
                return resp
            return None
        return None

    def _push_config(self, device_id):
        """Publish this robot's `/config`: the full RobotCloudConfig (paired + child_pii +
        parent settings) when permitted, else `build_unpaired_cloud_config()`."""
        from moxie_sdk.cloud_config import (build_robot_cloud_config,
                                            build_unpaired_cloud_config,
                                            robot_config_kwargs)
        if self.is_permitted(device_id):
            # `robot_config_kwargs` drops server-only keys (today: `brain`).
            cfg = build_robot_cloud_config(
                self.child, **robot_config_kwargs(self.effective_config(device_id)))
        else:
            cfg = build_unpaired_cloud_config()
            self._note("permit", f"⛔ {device_id} is not permitted — pending "
                                 f"(minimal config, no child data)")
        self._publish(f"/devices/{device_id}/config", cfg,
                      device_id=device_id, what="config")
        print(f"[runtime] → pushed config to {device_id} "
              f"(pairing_status={cfg.get('pairing_status')})")
        return cfg

    # ---- device commands: wake ----
    # `/devices/{id}/commands/wakeup` `{"command": "wakeup"}` (mqtt-and-conversation.md
    # §3.5). The robot sends no acknowledgement, so we report only that it was published.
    # Not yet run against a physical robot.
    WAKEUP_COMMAND = "wakeup"

    def _command_refusal(self, device_id) -> dict | None:
        """The refusal for a device command aimed at an unknown or pending robot."""
        if device_id not in self.robots:
            return {"ok": False, "device_id": device_id, "published": False,
                    "error": f"unknown device_id {device_id!r}",
                    "reason": "No robot with that id has connected to this appliance."}
        if not self.is_permitted(device_id):
            return {"ok": False, "device_id": device_id, "published": False,
                    "error": "robot is pending",
                    "reason": "Let this robot in first (Permit it in the fleet panel)."}
        return None

    def wake_robot(self, device_id) -> dict:
        """Publish the recovered `wakeup` command at one robot.

        `{ok, published: true, acknowledged: false}` when it went out; `ok: false` with a
        parent-actionable reason for an unknown/pending robot or no broker."""
        refused = self._command_refusal(device_id)
        if refused:
            return refused
        # A sleeping robot drops its subscriptions; forget our latch so the next reply
        # re-sends them (the "crossed ears" variant, upstream openmoxie PR #59).
        self._forget_robot_state(device_id, vision_only=True)
        # Check the socket, not the client object: `published` must never be false.
        if not self._broker_connected():
            return {"ok": False, "device_id": device_id, "published": False,
                    "acknowledged": False, "error": "no broker connection",
                    "reason": self.NO_BROKER_REASON}
        topic = f"/devices/{device_id}/commands/{self.WAKEUP_COMMAND}"
        payload = {"command": self.WAKEUP_COMMAND}
        ok, why = self._publish(topic, payload, device_id=device_id, what="wakeup")
        if not ok:
            # The socket died between the check and the write. Still not a success.
            return {"ok": False, "device_id": device_id, "published": False,
                    "acknowledged": False, "error": "publish failed", "reason": why}
        cfg = self.effective_config(device_id) or {}
        # `wake_button_enabled` defaults True: only an explicit False is worth a warning.
        wake_button = cfg.get("wake_button_enabled", True)
        note = ("Sent. The robot sends no acknowledgement for this command, so this "
                "confirms the message left the appliance, not that Moxie woke up.")
        if wake_button is False:
            note += (" Heads up: this robot's wake button is switched off in Settings, "
                     "which is the setting the recovered command depends on.")
        self._note("robot", f"⏰ wakeup published to {device_id}")
        print(f"[runtime] ⏰ → {topic} {payload}", flush=True)
        return {"ok": True, "device_id": device_id, "published": True,
                "acknowledged": False, "topic": topic, "payload": payload,
                "wake_button_enabled": bool(wake_button), "note": note}

    # ---- rehearsal: watch a line perform before a child does ----
    def preview(self, device_id, text, *, speak=False, **opts) -> dict:
        """Stage one line and publish it as an ordinary `remote_chat` turn (the rehearsal
        hook, C7). No SIM-specific API: whatever renders that device shows it.

        No brain call, history, memory or turn record — only the ordinary output-side
        safety check. `speak=True` also synthesizes (off by default: no voice spend).
        Returns the staged performance as JSON plus `dropped` (ids `validate` refused).
        """
        refused = self._command_refusal(device_id)
        if refused:
            return refused
        line = str(text or "").strip()
        if not line:
            return {"ok": False, "device_id": device_id, "published": False,
                    "error": "empty line", "reason": "Type a line to rehearse."}
        # Same output-side classifier as a brain's line; a BLOCK goes back to the author
        # with its reason (a human is at the keyboard) instead of a redirect.
        verdict = self._assess(line, safety_seam.MOXIE)
        if verdict and verdict.action == safety_seam.BLOCK:
            self._record_safety(device_id, verdict)
            return {"ok": False, "device_id": device_id, "published": False,
                    "error": "blocked", "blocked": True,
                    "categories": list(verdict.categories),
                    "reason": "Moxie will not say that. Nothing was published — "
                              "please rephrase."}
        event_id = f"preview-{int(time.time() * 1000)}"
        staged = perform(line, turn_key=event_id, chunk_index=0, **opts)
        scored = dict(staged.scored)
        self._publish_chat(device_id, event_id, "router", line, staged.markup,
                           result=ResultCode.SUCCESS, scored=scored)
        if speak:
            self._maybe_synthesize(device_id, staged.markup, event_id, chunk_num=0)
        self._note("preview", f"🎬 rehearsed '{line[:40]}' on {device_id}")
        print(f"[runtime] 🎬 preview → {device_id}: '{line[:60]}'", flush=True)
        out = {"ok": True, "device_id": device_id, "published": True, "spoke": speak,
               "event_id": event_id, "text": line, "markup": staged.markup,
               "mode": staged.mode, "scored": scored,
               "performance": performance_seam.to_json(staged.performance),
               "dropped": list(getattr(staged.performance, "dropped", ()) or ())}
        if verdict:
            out["flagged"] = list(verdict.categories)
        return out

    def update_config(self, device_id, **overrides):
        """Per-robot config edit: merge overrides into this device's RobotCloudConfig
        and re-publish it. Overrides persist across re-pushes."""
        self._config_overrides.setdefault(device_id, {}).update(overrides)
        self._note("config", f"⚙️  config updated: {', '.join(overrides)}")
        if "logging_policy" in overrides:
            # The privacy switch moved: under NO_DATA erase transcript + activity record now.
            self.purge_transcripts()
            self.purge_telemetry()
        if "face" in overrides:
            # Own feed line: the one edit a child sees on the robot's face.
            from moxie_sdk.faces import describe_face
            look = describe_face(overrides["face"] or {}) or "the default look"
            self._note("config", f"🎨 look updated: {look}")
        return self._push_config(device_id)
