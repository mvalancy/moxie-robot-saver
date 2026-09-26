"""Fleet/per-robot config, the device permit list, config push, wake and rehearsal preview."""
from __future__ import annotations
import json, os, time

from moxie_sdk.types import ResultCode
from moxie_sdk.wire import build_activity_response
from moxie_sdk import safety as safety_seam
from moxie_sdk import performance as performance_seam
from markup import perform


class FleetMixin:
    # ---- config push / edit (parent console) ----
    FLEET_CONFIG_COLLECTION = "config"          # → $MOXIE_DATA_DIR/fleet/config.json

    def fleet_config(self) -> dict:
        """The appliance-wide default overrides — one place to set house rules for every
        robot on this box (audit ADOPT #6). Read from the store each time so an edit from
        another process (or a hand-edited `fleet/config.json`) is picked up on the next
        push. `{}` when none was ever set, which is the pre-fleet behavior exactly."""
        cfg = self.store.read_shared(self.FLEET_CONFIG_COLLECTION, {})
        return dict(cfg) if isinstance(cfg, dict) else {}

    def effective_config(self, device_id) -> dict:
        """`fleet ⊕ per-robot` — the override layer stack this robot's config is built
        from (the builder's own kwarg defaults are the layer underneath)."""
        from moxie_sdk.cloud_config import merge_config_layers
        return merge_config_layers(self.fleet_config(),
                                   self._config_overrides.get(device_id, {}))

    def face_cache_id(self, device_id) -> str:
        """The `child_pii.id` this robot's next `/config` push will carry — the face
        cache-buster (`moxie_sdk/faces.py`, "the cache-buster"). `""` when no face is
        chosen, which is exactly when the field is omitted from the document.

        Read off the same `fleet ⊕ per-robot` layers `_push_config` builds from, so it is
        the value that will actually go out, not a second opinion about it."""
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
        """Parent-console *fleet* config edit: merge overrides into the appliance-wide
        defaults, persist them, and re-push every connected robot's config so the change
        lands everywhere at once. Per-robot overrides still win."""
        cfg = self.fleet_config()
        cfg.update(overrides)
        self.store.write_shared(self.FLEET_CONFIG_COLLECTION, cfg)
        self._note("config", f"⚙️  fleet config updated: {', '.join(overrides) or '—'}")
        # A house rule of `logging_policy=NO_DATA` must take away what is ALREADY on disk,
        # for every robot on the box — not just the connected ones the loop below
        # re-pushes: the rolling transcripts, and the activity record (packet ring, daily
        # roll-up, mentor behaviors). Both sweeps are no-ops under any other policy.
        self.purge_transcripts()
        self.purge_telemetry()
        for device_id in list(self.robots):
            self._push_config(device_id)
        return cfg

    # ---- the pairing gate: which devices this appliance serves --------------------
    #
    # The broker accepts anonymous connections (mqtt-and-conversation.md §3b — the robot's
    # RS256 JWT is never verified, exactly as in the original LAN model), so "reached the
    # port" must not mean "is my child's robot". Without a gate the supervisor pushes
    # `pairing_status:"paired"` **plus the child's `child_pii`** to whatever announces
    # itself on `/devices/{id}/state`. On a home network that is a real exposure.
    #
    # So: a durable permit list, closed by default. The idea is OpenMoxie's
    # `MoxieDevice.permit` + `HiveConfiguration.allow_unverified_bots` (MIT — credited in
    # ATTRIBUTION.md; no code copied, and note that in OpenMoxie the flag is stored but
    # never enforced on the MQTT path, so this is the idea taken further, not a port).
    FLEET_PERMITS_COLLECTION = "permits"        # → $MOXIE_DATA_DIR/fleet/permits.json

    def permits(self) -> dict:
        """The durable permit record, normalized:
        `{"allow_unverified_bots": bool, "devices": {device_id: {permitted_at, label}}}`.

        Like `fleet_config`, this reflects the file rather than a load-time snapshot, so a
        permit granted in another process — or hand-edited into `fleet/permits.json` — is
        picked up without a restart. Unlike `fleet_config` it is on a **hot** path (the
        gate runs on every inbound message, including each audio frame), so the parse is
        memoized against the file's `(mtime, size)`: a changed file re-reads, an unchanged
        one costs a `stat`. A missing/corrupt file reads as "nothing permitted", which
        fails **closed**."""
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
        # A fresh mapping every call: `set_permit` mutates what it gets back, and the
        # cache must never be edited through a caller's reference.
        return {"allow_unverified_bots": cached[1], "devices": dict(cached[2])}

    def allow_unverified_bots(self) -> bool:
        """True when this appliance serves **any** robot that connects (the pre-gate
        behavior). Precedence, most explicit first:

          1. the constructor argument (`MoxieRuntime(..., allow_unverified_bots=True)`);
          2. `MOXIE_ALLOW_UNVERIFIED_BOTS` — the migration switch for a deployment that
             was running before the gate existed (`1/true/on/yes` opens, `0/off/...`
             pins it shut);
          3. the durable fleet flag a parent toggles in the console;
          4. **False** — the safe default."""
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
        """Connected-but-unpermitted device ids — what the console's "Pending robots"
        list shows, and the only place a parent needs to look to let a new robot in."""
        return [d for d in self.robots if not self.is_permitted(d)]

    def set_permit(self, device_id, permitted: bool = True, label: str = "") -> dict:
        """Permit or revoke one device, durably, and make it true on the wire *now*:
        permitting a pending robot re-pushes its full config immediately (no reconnect,
        no restart), revoking one re-pushes the minimal un-paired document so the child's
        data stops being served to it on the same tick."""
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
        """The fleet-wide "serve any robot that connects" toggle. Flipping it re-pushes
        every connected robot's config, so a robot that was pending starts (or stops)
        being served without waiting for a reconnect."""
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
        """The console-facing permit view: the flag (as *enforced*, env included), the
        stored flag, the permit list, and which connected robots are still pending."""
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

    # What a *pending* device is allowed to receive. Nothing about the child, nothing
    # from the brain, nothing from the store — one fixed line so an owner watching their
    # own robot hears why it is quiet instead of nothing at all. It names no one.
    NOT_PAIRED_LINE = ("I'm not connected to a family yet. "
                       "Ask a grown-up to add me in the Moxie console.")

    def _serve_unpermitted(self, device_id, name, payload):
        """Everything a not-permitted device gets on `/events/…`, in one place.

        * `remote-chat` prompt → one fixed, child-free line; the brain is never called,
          no history is kept, nothing is stored. `notify` (the robot telling us what it
          said) is dropped — a pending device does not get a conversation record.
        * `client-service-activity-log` **queries** (`schedule`, `mentor_behaviors`,
          `license`) → the CloudQueryResponse envelope with its *empty* value, so the
          robot's pull resolves instead of hanging; the reports on the same topic (what
          the child finished) are dropped rather than written to the store.
        * everything else — the microphone stream (`zmq`), telemetry, vision, module
          lifecycle — is dropped on the floor.
        """
        if name.startswith("remote-chat"):
            try:
                rcr = json.loads(payload)
            except Exception:
                return
            if rcr.get("command") == "notify":
                return
            backend = rcr.get("backend", "router")
            if backend == "data" and rcr.get("query") == "modules":
                return self._publish_chat(device_id, rcr.get("event_id"), backend, "",
                                          markup="", result=ResultCode.SUCCESS, modules=[])
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
        """Publish this robot's `/config`.

        **Permitted** (or the fleet allows unverified bots) → the full RobotCloudConfig,
        `pairing_status:"paired"` + `child_pii` + the parent's settings, exactly as
        before the gate existed. **Not permitted** → `build_unpaired_cloud_config()`: the
        not-paired status, no `child_pii`, no household settings, privacy gate shut."""
        from moxie_sdk.cloud_config import (build_robot_cloud_config,
                                            build_unpaired_cloud_config,
                                            robot_config_kwargs)
        if self.is_permitted(device_id):
            # `robot_config_kwargs` drops the keys that are the SERVER's business — today
            # exactly `brain`, which rides these layers because they are the one layering
            # this codebase has, and which the robot has no field for.
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

    # ---- device commands: wake (the one the console used to fake) ------------------
    #
    # `POST /api/robots/{id}/wakeup` in the parent console returned `{"error": null}` and
    # published nothing — a button that reported success for an action that never
    # happened. The command itself is real and recovered:
    #
    #   topic   /devices/{device_id}/commands/wakeup
    #   payload {"command": "wakeup"}
    #
    # `docs/architecture/mqtt-and-conversation.md` §3.5 (the cloud→robot command table,
    # "wake a `wake_button_enabled` robot from screen-off") on the topic shape
    # `cloud-protocol.md`:147 establishes for every command. What the corpus does NOT
    # establish is an acknowledgement: no `commands/wakeup` reply, no state field that
    # flips. So this method reports what it truly knows — that the command was published —
    # and says plainly that the robot never confirms. Nothing here has run against a
    # physical robot.
    WAKEUP_COMMAND = "wakeup"

    def wake_robot(self, device_id) -> dict:
        """Publish the recovered `wakeup` command at one robot.

        `{ok:true, published:true, acknowledged:false}` when it went out — never a claim
        that the robot woke. `ok:false` (with a reason a parent can act on) for an unknown
        device, a robot still pending a permit, or no broker connection."""
        robot = self.robots.get(device_id)
        if robot is None:
            return {"ok": False, "device_id": device_id, "published": False,
                    "error": f"unknown device_id {device_id!r}",
                    "reason": "No robot with that id has connected to this appliance."}
        if not self.is_permitted(device_id):
            return {"ok": False, "device_id": device_id, "published": False,
                    "error": "robot is pending",
                    "reason": "Let this robot in first (Permit it in the fleet panel)."}
        # A robot that has been asleep has dropped its subscriptions, so waking it is one
        # of the moments our latch stops being true. Upstream openmoxie PR #59 diagnoses
        # exactly this variant — the STT subscribe must be re-sent on wake — and four
        # independent owner reports of "crossed ears" are the evidence behind it. We clear
        # rather than re-send: the next reply carries the subscription anyway, so the fix
        # is to stop *suppressing* it rather than to add a second publish.
        self._forget_robot_state(device_id, vision_only=True)
        # `if self.client is None` asked whether an OBJECT existed, not whether there was
        # a connection — so a live client over a dead socket answered `published: true`,
        # which is the exact failure PR #55 shipped to kill, surviving in the one place
        # that fix did not look. `published` is the only true thing this route can ever
        # say (the command has no acknowledgement in the recovered corpus), so saying it
        # falsely is the whole bug.
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
        # `wake_button_enabled` defaults True in the config we build (cloud_config.py),
        # so "absent" means "on", and only an explicit False is a warning worth showing.
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
        """Stage one line and publish it as an ordinary turn — the preview hook (C7).

        `sim-as-a-client.md`'s guarantee is that the SIM is **not a special case**, so
        this adds no SIM-specific API and no SIM-specific message. It plans the line,
        validates it, renders it and publishes a perfectly ordinary
        `/devices/<id>/commands/remote_chat` with `result=SUCCESS` — byte-identical in
        shape to what a real turn produces. Whatever is subscribed as that device renders
        it: the browser SIM, `virtual_moxie.py`, or a robot paired as a rehearsal device.

        **Nothing else happens.** No brain is called, no history is written, no turn is
        recorded, no memory is folded, no safety journal row is added beyond the ordinary
        assessment below. That is what makes it a rehearsal: an author can iterate on a
        line and *see* the performance before a child does.

        `speak=True` also synthesizes, so a rehearsal can be heard as well as seen. It is
        off by default because a preview must not spend a voice call unless it was asked
        to — an author is usually watching the body, not listening.

        The returned `performance` is the staged structure as JSON (mood/act/gesture/
        gaze/icon/sfx per beat) plus `dropped`, the ids `validate` refused, so a console
        can flag them in red instead of leaving an author wondering why nothing played.
        """
        robot = self.robots.get(device_id)
        if robot is None:
            return {"ok": False, "device_id": device_id, "published": False,
                    "error": f"unknown device_id {device_id!r}",
                    "reason": "No robot with that id has connected to this appliance."}
        if not self.is_permitted(device_id):
            return {"ok": False, "device_id": device_id, "published": False,
                    "error": "robot is pending",
                    "reason": "Let this robot in first (Permit it in the fleet panel)."}
        line = str(text or "").strip()
        if not line:
            return {"ok": False, "device_id": device_id, "published": False,
                    "error": "empty line", "reason": "Type a line to rehearse."}
        # A rehearsal line is still a line a child could hear, so it passes the same
        # output-side classifier a brain's own line does — and, like telehealth, a BLOCK
        # comes back to the author with its reason instead of being replaced by a
        # redirect. There is a human at the keyboard; substituting for them helps nobody.
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
        """Parent-console config edit: merge overrides (audio_volume, screen_brightness,
        timezone_id, logging_policy, weekday_bedtime, wake toggles, …) into this device's
        RobotCloudConfig and re-publish it. Overrides persist across re-pushes."""
        self._config_overrides.setdefault(device_id, {}).update(overrides)
        self._note("config", f"⚙️  config updated: {', '.join(overrides)}")
        if "logging_policy" in overrides:
            # The parent just moved the privacy switch. If it landed on NO_DATA, what is
            # already on disk goes now — waiting for the next turn would leave it there
            # for a robot that is never spoken to again. Transcript first, then the
            # activity record (packets, day rows, mentor behaviors).
            self.purge_transcripts()
            self.purge_telemetry()
        if "face" in overrides:
            # Worth its own line in the console's activity feed: this is the one config
            # edit whose result a child sees on the robot's face.
            from moxie_sdk.faces import describe_face
            look = describe_face(overrides["face"] or {}) or "the default look"
            self._note("config", f"🎨 look updated: {look}")
        return self._push_config(device_id)
