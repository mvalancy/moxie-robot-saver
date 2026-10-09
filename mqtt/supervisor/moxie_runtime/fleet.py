"""Fleet/per-robot config, the device permit list, config push, wake and rehearsal preview."""
from __future__ import annotations
import contextlib, json, os, time

from moxie_sdk.types import ResultCode
from moxie_sdk.wire import (build_activity_response, build_remote_modules, is_data_query,
                            is_module_query)
from moxie_sdk import safety as safety_seam
from moxie_sdk import performance as performance_seam
from moxie_sdk.cloud_config import LoggingPolicy
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
        return face_child_id(labels, child_key=self.child_for(device_id).nickname)

    def child_for(self, device_id):
        """The child this robot's config, brains, hello, `/status` and day plan name: the
        parent's record saved on this robot's own layer (`child`, which a house rule never
        carries), else the appliance's profile (`MOXIE_CHILD_NICKNAME`)."""
        from moxie_sdk.cloud_config import child_profile_for
        return child_profile_for(self._config_overrides.get(device_id), self.child)

    def child_names(self) -> set:
        """Every name Moxie may call a child on this appliance, for the log and feed mask
        (`_masked`): each connected robot's (`RobotContext.child`), each robot's saved
        record (`child` on its layer, connected or away) and the appliance's own
        (`MOXIE_CHILD_NICKNAME`). Raw strings, unchecked: a name the rule refuses is
        masked too. Safe while the constructor is still loading the layers."""
        names = {getattr(self.child, "nickname", None)}
        names.update(getattr(r.child, "nickname", None)
                     for r in list((getattr(self, "robots", None) or {}).values()))
        for layer in list((getattr(self, "_config_overrides", None) or {}).values()):
            record = layer.get("child") if isinstance(layer, dict) else None
            if isinstance(record, dict):
                names.add(record.get("nickname"))
        return {n for n in names if isinstance(n, str) and n.strip()}

    def _child_changed(self, device_id, before) -> None:
        """After this robot's `child` record changed (a parent's save, a revoke): the next
        turn, hello, brain card and `/status` say the name in force at once. When it is
        another name (or none), the day plan's stored "why" lines, which name the child,
        are dropped (`GET /schedule` plans on the spot until the robot asks for its next
        plan), and so is a hello queued for the next turn: it was built with the old name."""
        child = self.child_for(device_id)
        if device_id in self.robots:
            self.robots[device_id].child = child
        if (child.nickname, child.birthday_iso) != (before.nickname, before.birthday_iso):
            self.store.delete(device_id, self.SCHEDULE_EXPLAIN_COLLECTION)
            with self._presence_lock:
                self._pending_opener.pop(device_id, None)

    def _forget_child(self, device_id) -> bool:
        """A robot this appliance no longer lets in keeps no child's name: the `child`
        record on its layer is dropped, in RAM and in its saved settings. An unpair or a
        reset whose clear (`child: null`) never arrived is retried this way: Revoke in
        Robot access. True when its saved settings hold no name now; False when they
        could not be saved (the store refused, or the record failed closed), so a restart
        could bring the name back."""
        layer = self._config_overrides.get(device_id)
        had = isinstance(layer, dict) and "child" in layer
        if not had and self.settings_saved(device_id):
            return True                  # RAM and the saved record agree: no name
        before = self.child_for(device_id)
        with self._settings_record(device_id) as held:
            if isinstance(layer, dict):
                layer.pop("child", None)
            saved = self._save_config_overrides(device_id, held)
        if had:
            self._child_changed(device_id, before)
            line = f"🧹 {device_id} is not let in, so its settings keep no child's name"
            self._note("permit", line if saved else
                       f"{line} (NOT saved: a restart brings the name back)")
        return saved

    def configurable(self, device_id) -> bool:
        """May a parent's setting be saved for this robot now? A connected robot, or one
        this appliance knows while it is away: on the permit list or in the roster. Its
        saved settings reach it on its next connect (`_device_connect`'s settle)."""
        device_id = str(device_id or "")
        return bool(device_id) and (self._is_known(device_id)
                                    or device_id in self.permits()["devices"])

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
        on permit, the minimal un-paired document on revoke). A revoke also takes the
        child's name off the robot's settings (`_forget_child`), and its answer says
        whether they hold none now (`child_cleared`)."""
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
        cleared = None if permitted else self._forget_child(device_id)
        if device_id in self.robots:
            if permitted:
                self._admit(device_id)
            else:
                self._push_config(device_id)             # the minimal un-paired document
                self._forget_stt_ask(device_id)          # pending: never "mic asked"
        out = self.permits_view()
        if cleared is not None:
            out["child_cleared"] = cleared
        return out

    def _admit(self, device_id):
        """What a connected robot gets the moment it is let in, by a Permit or by the
        fleet-wide toggle: its full config, then the mic ask, then the app's greeting —
        the settle's order (`_device_connect`). Asks regardless of the latch: the
        parent's click is a "make it work" button."""
        self._push_config(device_id)
        self._subscribe_stt(device_id, again=True)       # config, then the mic ask
        try:
            self.app_for(device_id).on_connect(self.robots[device_id])
        except Exception as e:
            print(f"[runtime] app.on_connect error: {e}", flush=True)

    def set_allow_unverified_bots(self, allowed: bool) -> dict:
        """The fleet-wide "serve any robot" toggle; re-pushes every connected robot. A
        robot it lets in is onboarded like a Permit (`_admit`), one it shuts out is
        pending again (the minimal config, no `mic asked`). Judged on the enforced
        value: under a constructor or env pin the stored flag changes and nothing else."""
        was = {device_id: self.is_permitted(device_id) for device_id in list(self.robots)}
        rec = self.permits()
        rec["allow_unverified_bots"] = bool(allowed)
        self.store.write_shared(self.FLEET_PERMITS_COLLECTION, rec)
        self._permits_cache = None      # our own write invalidates outright,
                                        # never trusting mtime granularity
        self._note("permit", f"🔓 allow_unverified_bots={bool(allowed)}"
                             if allowed else "🔒 allow_unverified_bots=False")
        for device_id, before in was.items():
            now = self.is_permitted(device_id)
            if now and not before:
                self._admit(device_id)
            else:
                self._push_config(device_id)
                if not now:
                    self._forget_stt_ask(device_id)
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
          is dropped. A module query -> an empty list; any other data query -> dropped.
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
            if is_data_query(rcr):          # no other data query is answered (turns.py)
                self._note("permit", f"ignored a data query from pending {device_id}")
                return None
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
            # `robot_config_kwargs` drops the keys that are no builder kwargs (`brain`,
            # `child`); the child rides in as `child_pii`, from the parent's record.
            cfg = build_robot_cloud_config(
                self.child_for(device_id),
                **robot_config_kwargs(self.effective_config(device_id)))
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
        self._subscribe_stt(device_id, again=True)   # a woken robot has no mic subscription
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
        self._note("preview", f"🎬 rehearsed '{self._masked(line, 40)}' on {device_id}")
        print(f"[runtime] 🎬 preview → {device_id}: '{self._masked(line, 60)}'", flush=True)
        out = {"ok": True, "device_id": device_id, "published": True, "spoke": speak,
               "event_id": event_id, "text": line, "markup": staged.markup,
               "mode": staged.mode, "scored": scored,
               "performance": performance_seam.to_json(staged.performance),
               "dropped": list(getattr(staged.performance, "dropped", ()) or ())}
        if verdict:
            out["flagged"] = list(verdict.categories)
        return out

    def update_config(self, device_id, **overrides):
        """Per-robot config edit: merge overrides into this device's RobotCloudConfig,
        save the parent's settings so they survive a restart (`_save_config_overrides`)
        and re-publish it. Overrides persist across re-pushes; a robot that is away hears
        nothing now (QoS 0) and gets them from the settle on its next connect. Logs and
        the feed name the keys, never a value: `child` is the child's name."""
        self._note("config", f"⚙️  config updated: {', '.join(overrides)}")
        before = self.child_for(device_id) if "child" in overrides else None
        # One transaction on the robot's record across the merge, the snapshot and the
        # write: two edits at once reach the disk in the order they changed RAM, so the
        # file never ends up holding the older one (`_settings_record`).
        with self._settings_record(device_id) as held:
            layer = self._config_overrides.setdefault(device_id, {})
            ends_fail_closed = (self.failed_closed(device_id)
                                and bool(self._storable_settings(overrides)[0]))
            if ends_fail_closed and "logging_policy" not in overrides:
                # A parent's save ends `_fail_closed`: data sharing is this save's own
                # choice again, or the layer underneath (house rule, default).
                layer.pop("logging_policy", None)
            layer.update(overrides)
            if "child" in overrides and overrides["child"] is None:
                layer.pop("child", None)     # a cleared name leaves no trace in the record
            if ends_fail_closed:
                # Only once the layer holds the parent's choice: a transcript save landing
                # between the two would otherwise read the fail-closed NO_DATA as a
                # parent's, and erase the transcript failing closed exists to keep.
                self._settings_unreadable.discard(device_id)
            # Saved before the purge and the push: a crash after this line still boots
            # with the parent's choice, so a NO_DATA that was set is swept at the next start.
            self._save_config_overrides(device_id, held)
        if before is not None:
            # The new name at once; the day plan's "why" lines and a queued hello, which
            # name the child too, never linger with the old one (`_child_changed`).
            self._child_changed(device_id, before)
        if "logging_policy" in overrides or ends_fail_closed:
            # The privacy switch moved (a parent's choice, or a parent's choice back in force
            # after failing closed): under NO_DATA erase transcript + activity record now.
            self.purge_transcripts()
            self.purge_telemetry()
        if "face" in overrides:
            # Own feed line: the one edit a child sees on the robot's face.
            from moxie_sdk.faces import describe_face
            look = describe_face(overrides["face"] or {}) or "the default look"
            self._note("config", f"🎨 look updated: {look}")
        return self._push_config(device_id)

    # ---- the per-robot layer on disk: settings survive a restart ----
    # `robots/<id>/config.json` holds one robot's layer of `effective_config` (volume,
    # bedtime, look, brain pick, data sharing, ...): only what the console's whitelist,
    # `sanitize_config_overrides`, accepts. Every `update_config` rewrites it (a record
    # that failed closed waits for a parent's save, `_fail_closed`); it is read
    # back ONCE, at construction, before the transcript sweep, because brain, safety,
    # lifecycle and the status server read `_config_overrides` directly. Telehealth's
    # `moxie_mode` is not in the whitelist, so it is not kept: its session lives in RAM,
    # and a restart hands the robot back to its own brain, as before.
    ROBOT_CONFIG_COLLECTION = "config"          # → $MOXIE_DATA_DIR/robots/<id>/config.json
    #: What a robot whose saved data-sharing choice cannot be read runs under: the most
    #: restrictive LoggingPolicy (enums.proto: NO_DATA keeps nothing, NO_MEDIA all but
    #: audio and video, FULL everything). "A policy it cannot read fails closed rather
    #: than open" (config-and-telemetry-contract.md).
    UNREADABLE_SETTINGS_POLICY = LoggingPolicy.NO_DATA

    @staticmethod
    def _storable_settings(overrides) -> tuple:
        """`(kept, dropped)`: each key re-checked ON ITS OWN through the console's
        whitelist, so one bad value costs only itself. `kept` holds canonical values."""
        from moxie_sdk.cloud_config import sanitize_config_overrides
        kept, dropped = {}, []
        for key, value in overrides.items():
            try:
                clean = sanitize_config_overrides({key: value})
            except Exception:                    # ValueError, TypeError, KeyError (enum name)
                clean = {}
            if key in clean:
                kept[key] = clean[key]
            else:
                dropped.append(str(key))
        return kept, dropped

    @contextlib.contextmanager
    def _settings_record(self, device_id):
        """Hold this robot's record (`store.transaction`) for one edit. Yields False when
        it cannot be held (another process kept the lock past the store's timeout, which
        the store records): the edit still applies, and `_save_config_overrides` says it
        was not saved instead of waiting on the same lock a second time."""
        from moxie_sdk.store import StoreLockTimeout
        held = contextlib.ExitStack()
        try:
            held.enter_context(self.store.transaction(device_id, self.ROBOT_CONFIG_COLLECTION))
        except Exception as e:                   # persistence must never cost the edit
            if not isinstance(e, StoreLockTimeout):
                print(f"[runtime] settings write failed for {device_id}: {e}", flush=True)
            yield False
            return
        with held:
            yield True

    def _save_config_overrides(self, device_id, held: bool = True) -> bool:
        """Write this robot's saved settings (the store's locked, atomic write), inside
        `_settings_record`. A write that fails, or a record that could not be held, is said
        aloud: the edit still applies now, but not after a restart."""
        if device_id in self._settings_unreadable:
            return False                         # kept as found until a parent saves
        saved = False
        if held:
            kept, _ = self._storable_settings(
                dict(self._config_overrides.get(device_id) or {}))
            try:
                saved = self.store.write(device_id, self.ROBOT_CONFIG_COLLECTION, kept)
            except Exception as e:               # persistence must never cost the edit
                print(f"[runtime] settings write failed for {device_id}: {e}", flush=True)
        if not saved:
            line = (f"⚠️  settings for {device_id} applied but NOT saved — they will not "
                    f"survive a restart")
            self._note("error", line)
            print(f"[runtime] {line}", flush=True)
            self._settings_unsaved.add(device_id)
        else:
            self._settings_unsaved.discard(device_id)  # the record holds the whole layer
        return saved

    def settings_saved(self, device_id) -> bool:
        """False while this robot's record does not hold the settings it runs with: the
        store refused the last save, or the record could not be read at start and no
        parent has saved since (`_fail_closed`). The `POST /config`, `/brain` and
        `/telehealth` answers carry it as `saved`, and the console then says the change
        will be lost on a restart instead of "Saved"."""
        return device_id not in self._settings_unsaved and not self.failed_closed(device_id)

    def failed_closed(self, device_id) -> bool:
        """True while this robot runs under `UNREADABLE_SETTINGS_POLICY` because its saved
        data-sharing choice could not be read (`_fail_closed`), until a parent's save. That
        NO_DATA stops every new write, but it is not a parent's choice, so nothing already
        stored is erased for it: `purge_transcripts`, `purge_telemetry` and the
        transcript's write path (`_save_memory`) pass this robot over."""
        return device_id in self._settings_unreadable

    def _load_config_overrides(self) -> dict:
        """Every robot's saved settings, keyed by device id (the store's directory name).

        Runs in the constructor and never raises. A damaged or non-object record loads no
        settings (one line). A key the whitelist now refuses, or a brain the current
        `MOXIE_APP` pin refuses, is dropped (one line per robot) and never pushed. Either
        way a data-sharing choice that cannot be read fails closed (`_fail_closed`).
        Loading writes nothing: a record that failed closed stays as found until a parent
        saves this robot's settings, and any other record is rewritten by its next edit."""
        from moxie_sdk import brains as brain_seam
        pin = brain_seam.pin_for_env(os.environ.get(brain_seam.ENV_VAR, ""))
        loaded = {}
        try:
            devices = self.store.devices()
        except Exception as e:
            print(f"[runtime] ⚠️  saved settings not loaded: {e}", flush=True)
            return loaded
        missing = object()
        restored = 0
        for device_id in devices:
            path = self.store.path(device_id, self.ROBOT_CONFIG_COLLECTION)
            try:
                raw = self.store.read(device_id, self.ROBOT_CONFIG_COLLECTION, missing)
                if raw is missing and not os.path.exists(path):
                    continue                     # nothing saved for this robot
            except Exception:                    # e.g. RecursionError from a hostile file
                raw = missing
            if not isinstance(raw, dict):
                what = "unreadable" if raw is missing else "not a settings object"
                print(f"[runtime] ⚠️  {path} is {what}: no saved settings for {device_id} "
                      f"(left as found until a parent saves this robot's settings)",
                      flush=True)
                loaded[device_id] = self._fail_closed(device_id, {}, "its saved settings")
                continue
            kept, dropped = self._storable_settings(raw)
            brain = kept.get(brain_seam.CONFIG_KEY)
            if brain and not brain_seam.honours_pin(brain, pin):
                del kept[brain_seam.CONFIG_KEY]
                dropped.append(f"{brain_seam.CONFIG_KEY} ({brain_seam.ENV_VAR} pins {pin})")
            if dropped:
                print(f"[runtime] ⚠️  {device_id}: saved settings dropped at load (not "
                      f"accepted here now): {', '.join(sorted(dropped))}", flush=True)
            restored += bool(kept)
            if "logging_policy" in raw and "logging_policy" not in kept:
                kept = self._fail_closed(device_id, kept, "its saved data-sharing choice")
            if kept:
                loaded[device_id] = kept
        if restored:
            print(f"[runtime] restored saved settings for {restored} robot(s)", flush=True)
        return loaded

    def _fail_closed(self, device_id, kept: dict, what: str) -> dict:
        """`kept` plus `UNREADABLE_SETTINGS_POLICY`, for a robot whose saved data-sharing
        choice cannot be read, until a parent's next save (`update_config`). Every writer
        sees NO_DATA, so nothing new is kept (no transcript, memory, activity record or
        safety excerpt). Nothing already stored is erased (`failed_closed`): a damaged
        file, or a value from a newer build, is not a parent's choice to erase. Until that
        save the record stays as found, so a restart fails closed again. The activity
        feed says so in one line."""
        self._settings_unreadable.add(device_id)
        policy = self.UNREADABLE_SETTINGS_POLICY
        line = (f"🔒 {device_id}: {what} could not be read, so it runs under "
                f"{policy.name} (the strictest data sharing) until a parent saves its "
                f"settings again; what is already stored is kept")
        self._note("error", line)
        print(f"[runtime] {line}", flush=True)
        return {**kept, "logging_policy": int(policy)}
