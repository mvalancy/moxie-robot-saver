"""The brain picker: which MoxieApp answers each robot, hot-swappable per child."""
from __future__ import annotations

from moxie_sdk import brains as brain_seam


class BrainMixin:
    # Brain picker (backlog/brain-picker.md): the registry is `moxie_sdk/brains.py` (a
    # closed list); the selection is the ordinary `brain` config key (defaults + fleet +
    # per-robot); the swap happens once at the top of each turn, so a turn in flight keeps
    # its brain; an explicit MOXIE_APP pins it (enforced in `brains.resolve_brain`). A brain
    # that cannot be built keeps the current one talking and says so once.
    BRAIN_KEY = brain_seam.CONFIG_KEY

    def set_brain_engines(self, engines):
        """Install the appliance's brain builders (`config.brain_engines()`). Without
        them the appliance keeps its boot brain."""
        self._brain_engines = engines

    def _brain_availability(self) -> dict:
        """`{available, pin, pin_note, default}` — never raises; no network (the brain
        set is a table in this repo)."""
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
            except (Exception, SystemExit) as e:   # a bad MOXIE_APP raises SystemExit;
                # show the real table rather than 500 the card.
                self._note("brain", f"🧠 brain options unavailable: {type(e).__name__}")
        return {"available": brain_seam.options(default=boot), "pin": "",
                "pin_note": "", "default": boot}

    def brain_pin(self) -> str:
        """The brain `MOXIE_APP` pins right now — `""` when it pins nothing."""
        return self._brain_availability()["pin"]

    def brain_for(self, device_id) -> dict:
        """Which brain answers THIS robot, and which layer said so:
        `{brain, source, requested, pinned, note}`. Read from the store each time so an
        edit from another process lands on the next turn."""
        avail = self._brain_availability()
        return brain_seam.resolve_brain(
            default=avail["default"],
            fleet=self.fleet_config().get(self.BRAIN_KEY),
            robot=(self._config_overrides.get(device_id) or {}).get(self.BRAIN_KEY),
            pin=avail["pin"])

    def app_for(self, device_id):
        """The `MoxieApp` in force for one robot — built on first use, then cached.

        Called once at the top of a turn. The lock covers the build only, never `respond()`.
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
                # Keep the current brain; report the failure once per name, not per turn.
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
        """The brain card: every brain this appliance can run, the house rule, and which
        one answers each robot (with the deciding layer)."""
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

        Validated against the registry and the MOXIE_APP pin, then stored through the
        ordinary config write. The next turn resolves and builds it; no restart.
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
