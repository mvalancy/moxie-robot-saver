"""The brain picker: which MoxieApp answers each robot, hot-swappable per child."""
from __future__ import annotations

from moxie_sdk import brains as brain_seam


class BrainMixin:
    # ---- 🧠 the brain picker: any brain, hot-swappable, per child -----------------
    #
    # `ai-seam.md` §2 calls the brain a seam and says any AI can wear the shell. It was
    # true of the drawing and false of the box: `MOXIE_APP` chose one brain, once, at
    # import, for every child on the appliance. These few methods are the whole feature,
    # and each half is something this codebase already does:
    #
    #   * **the registry** — `moxie_sdk/brains.py`, a closed positive list (the idiom of
    #     `content/packs.py::SPEC` and `content/ext.py::OPS`). A name resolves to a
    #     builder; an unknown name is refused, never guessed;
    #   * **the selection** — `brain` is an ordinary key in the ordinary config layers
    #     (`fleet/config.json` ⊕ the per-robot overrides, audit ADOPT #6). There is no
    #     second store and no second layering: `POST /config?scope=fleet` already writes
    #     the house rule and `POST /config?device_id=` already writes one robot's;
    #   * **the swap** — the 🎚️ voice picker's rule, exactly: the choice is resolved ONCE
    #     at the top of a turn (`_handle_turn`), so the next turn uses the new brain and a
    #     turn already in flight finishes with the one it started with. Same shape as
    #     `reload_content()`'s attribute swap: no restart, no reconnect, no dropped turn;
    #   * **the pin** — an explicit `MOXIE_APP` wins over any per-child pick (PR #77's
    #     owner rule). It is enforced in `brains.resolve_brain`, which every read goes
    #     through, so a pick stored before the pin appeared cannot install anything.
    #
    # A brain that cannot be built on this box (a `webhook` with no endpoint, an `llm`
    # with no `MOXIE_LLM_BASE_URL`) keeps the appliance TALKING with the brain it already
    # had, and says so once — the same trade `_install_voice` makes, for the same reason:
    # a downgrade caused by an attempt to improve things is the worst shape a failure can
    # take.
    BRAIN_KEY = brain_seam.CONFIG_KEY

    def set_brain_engines(self, engines):
        """Install the appliance's brain builders (`config.brain_engines()`).

        Without one the card still renders and the appliance keeps its boot brain — an
        honest floor rather than a picker that offers what this box cannot build."""
        self._brain_engines = engines

    def _brain_availability(self) -> dict:
        """`{available, pin, pin_note, default}` — never raises.

        No discovery and no network: unlike the gateway's voice catalog, the set of
        brains is a table in this repo. With no engines installed the answer is still the
        real table, marked with the brain this runtime actually booted with.
        """
        boot = brain_seam.sanitize_brain(getattr(self.app, "name", "")) \
            or brain_seam.DEFAULT_BRAIN
        engines = self._brain_engines
        if engines is not None:
            try:
                out = engines.available() or {}
                return {"available": list(out.get("available")
                                          or brain_seam.options(default=boot)),
                        "pin": brain_seam.sanitize_brain(out.get("pin")),
                        "pin_note": str(out.get("pin_note") or ""),
                        "default": brain_seam.sanitize_brain(out.get("default")) or boot}
            except (Exception, SystemExit) as e:   # a broken seam is local-only, and a
                # misconfigured `MOXIE_APP` raises SystemExit rather than Exception — a
                # card that 500s is a worse answer than a card that shows the real table.
                self._note("brain", f"🧠 brain options unavailable: {type(e).__name__}")
        return {"available": brain_seam.options(default=boot), "pin": "",
                "pin_note": "", "default": boot}

    def brain_pin(self) -> str:
        """The brain `MOXIE_APP` pins right now — `""` when it pins nothing."""
        return self._brain_availability()["pin"]

    def brain_for(self, device_id) -> dict:
        """Which brain answers THIS robot, and which layer said so.

        `{brain, source, requested, pinned, note}` — `brains.resolve_brain` over
        `defaults ⊕ fleet ⊕ per-robot`, read from the store each time so an edit made in
        another process (or by hand in `fleet/config.json`) is picked up on the next turn.
        """
        avail = self._brain_availability()
        return brain_seam.resolve_brain(
            default=avail["default"],
            fleet=self.fleet_config().get(self.BRAIN_KEY),
            robot=(self._config_overrides.get(device_id) or {}).get(self.BRAIN_KEY),
            pin=avail["pin"])

    def app_for(self, device_id):
        """The `MoxieApp` in force for one robot — built on first use, then cached.

        Called ONCE per turn, at the top, and the result is carried through the turn: a
        parent who swaps a brain mid-answer gets the new one on the child's *next*
        sentence, never halfway through this one.

        The lock covers the BUILD, never the turn: constructing a brain is a client
        object, not a network round trip, and `respond()` runs outside it.
        """
        name = self.brain_for(device_id)["brain"]
        app = self._brains.get(name)
        if app is not None:
            return app
        with self._brain_lock:
            app = self._brains.get(name)
            if app is not None:
                return app
            engines = self._brain_engines
            note = ""
            if engines is None:
                note = "no brain builders installed (set_brain_engines)"
            else:
                try:
                    app = engines.build(name)
                except SystemExit as e:          # a brain whose environment is missing
                    note = str(e)
                except Exception as e:           # noqa: BLE001 — a bad pick must not kill us
                    note = f"{type(e).__name__}: {e}"
            if app is None:
                # Keep talking with the brain we already have, and say it ONCE per name
                # rather than once per turn — a child must not pay for a parent's typo,
                # and an operator must not have to read the same line every ten seconds.
                if self._brain_failed.get(name) != note:
                    self._brain_failed[name] = note
                    self._note("brain", f"🧠 {name} could not be built — keeping "
                                        f"{getattr(self.app, 'name', '?')}: {note}")
                    print(f"[runtime] 🧠 {name} could not be built — keeping "
                          f"{getattr(self.app, 'name', '?')}: {note}", flush=True)
                return self.app
            self._wire_memory_policy(app)
            self._brains[name] = app
            self._brain_failed.pop(name, None)
            self._note("brain", f"🧠 built {brain_seam.describe_brain(name)}")
            print(f"[runtime] 🧠 built {brain_seam.describe_brain(name)}", flush=True)
            return app

    def brain_view(self) -> dict:
        """What the 🧠 card renders: every brain this appliance can run, the house rule,
        and which one answers each robot — with the layer that decided it.

        Fleet-level *and* per-robot in one document, because the whole point of the
        feature is the difference between the two: a card that showed only the appliance
        value could not show that one child is on a different brain.
        """
        avail = self._brain_availability()
        fleet = brain_seam.sanitize_brain(self.fleet_config().get(self.BRAIN_KEY))
        robots = []
        for device_id in self.robots:
            if not self.is_permitted(device_id):
                continue
            r = self.brain_for(device_id)
            override = brain_seam.sanitize_brain(
                (self._config_overrides.get(device_id) or {}).get(self.BRAIN_KEY))
            robots.append({
                "device_id": device_id,
                "child": self.robots[device_id].child.nickname,
                "brain": r["brain"], "source": r["source"],
                "requested": r["requested"], "note": r["note"],
                "label": brain_seam.describe_brain(r["brain"]),
                "override": override,
                "line": brain_seam.boot_line(r, device_id=device_id)})
        return {"ok": True, "available": avail["available"],
                "pin": avail["pin"], "pin_note": avail["pin_note"],
                "default": avail["default"], "fleet": fleet,
                "appliance": getattr(self.app, "name", ""),
                "installed": sorted(k for k in self._brains if k),
                "env_var": brain_seam.ENV_VAR, "robots": robots}

    def brain_update(self, patch, device_id: str = "", scope: str = "robot") -> dict:
        """Persist a brain pick — the house rule (`scope="fleet"`) or one robot's.

        The pick is checked against the registry AND against the environment's pin, and a
        refusal carries the sentence the card shows, naming `MOXIE_APP`. It then goes
        through the ordinary config write (`update_fleet_config` / `update_config`), so
        there is one code path that stores a parent's setting and one that pushes a
        robot's document — this method adds a validation and a log line, not a store.

        Nothing is "installed" here: the next turn resolves the layers and builds what it
        finds. That is what makes the swap free of a restart and safe mid-conversation.
        """
        try:
            name = brain_seam.normalize_brain_patch(patch, pin=self.brain_pin())
        except ValueError as e:
            return {"ok": False, "error": str(e), "reason": str(e)}
        if scope == "fleet":
            self.update_fleet_config(**{self.BRAIN_KEY: name})
            target = "fleet"
        else:
            if not device_id or device_id not in self.robots:
                err = f"unknown device_id {device_id!r}"
                return {"ok": False, "error": err, "reason": err}
            self.update_config(device_id, **{self.BRAIN_KEY: name})
            target = device_id
        line = (f"🧠 {target}: brain → {brain_seam.describe_brain(name)}" if name
                else f"🧠 {target}: brain cleared — the layer underneath decides")
        self._note("brain", line)
        print(f"[runtime] {line}", flush=True)
        out = self.brain_view()
        out["applied"] = {"scope": "fleet" if scope == "fleet" else "robot",
                          "device_id": "" if scope == "fleet" else device_id,
                          self.BRAIN_KEY: name}
        return out
