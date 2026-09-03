#!/usr/bin/env python3
"""🧠 Delete one brain-registry guard at a time and require a test to go red.

The house rule is that a feature's tests are proven in BOTH directions: green with the
guard, red without it. A test that only ever runs against correct code cannot tell you
whether it is asserting the property or merely restating it — `sim/tools/ext_mutation_check.py`
is the same tool for the sandboxed-extension grammar, and this is its sibling for
"any brain, hot-swappable, per child".

Each entry deletes exactly one guard (the positive list's refusal, the pin, a layer's
precedence, the server-only key filter, the once-per-turn resolution, …), runs
`test_brains.py` + `test_brain_runtime.py`, and restores the file. A mutation that leaves
the suite GREEN is a hole in the tests, not a pass.

    python3 sim/tools/brain_mutation_check.py     # from the repo root

Uses the repo's own virtualenv if it has one, else the interpreter running this script.
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = ROOT / ".venv/bin/python"
if not PY.exists():
    PY = pathlib.Path(sys.executable)
TESTS = ["sim/tests/test_brains.py", "sim/tests/test_brain_runtime.py"]

B = "mqtt/moxie_sdk/brains.py"
C = "mqtt/config.py"
CC = "mqtt/moxie_sdk/cloud_config.py"
R = "mqtt/supervisor/moxie_runtime.py"

MUTATIONS = [
    ("M1  unknown name resolves to the default again", B,
     "    return name if name in BRAINS else \"\"",
     "    return name if name in BRAINS else DEFAULT_BRAIN"),
    ("M2  the pin no longer overrules a stored pick", B,
     "    if pinned and chosen != pinned:",
     "    if False and pinned and chosen != pinned:"),
    ("M3  the layers stack robot-then-fleet (fleet wins)", B,
     '    for layer, value in (("fleet", fleet), ("robot", robot)):',
     '    for layer, value in (("robot", robot), ("fleet", fleet)):'),
    ("M4  an unset layer counts as a choice of nothing", B,
     "        if value is None or value == \"\":\n            continue",
     "        if False:\n            continue"),
    ("M5  a layer naming a non-brain is taken verbatim", B,
     "        name = sanitize_brain(value)\n        if not name:",
     "        name = sanitize_brain(value) or str(value)\n        if not name:"),
    ("M6  normalize_brain_patch skips the pin check", B,
     "    if not honours_pin(name, pin):",
     "    if False and not honours_pin(name, pin):"),
    ("M7  normalize_brain_patch accepts an unknown name", B,
     "    name = sanitize_brain(value)\n    if not name:\n        raise ValueError",
     "    name = sanitize_brain(value)\n    if False:\n        raise ValueError"),
    ("M8  filter_options ignores the pin", B,
     "    return [dict(e) for e in entries or () if e.get(\"id\") == pinned]",
     "    return [dict(e) for e in entries or ()]"),
    ("M9  the pin reads the RESOLVED MOXIE_APP, not the raw one", C,
     "    return brains.pin_for_env(BRAIN_ENV)",
     "    return brains.pin_for_env(MOXIE_APP)"),
    ("M10 build_brain guesses instead of refusing", C,
     "    if not key or key not in BRAIN_BUILDERS:\n        raise _unknown_brain(name)",
     "    if not key or key not in BRAIN_BUILDERS:\n        key = brains.DEFAULT_BRAIN"),
    ("M11 default_brain falls back for a typo", C,
     "    raise _unknown_brain(MOXIE_APP)",
     "    return brains.DEFAULT_BRAIN"),
    ("M12 the config whitelist stops validating the brain", CC,
     "            name = brains.sanitize_brain(value)\n            if not name:",
     "            name = brains.sanitize_brain(value) or str(value)\n            if not name:"),
    ("M13 the server-only key travels to the robot", CC,
     "    return {k: v for k, v in cfg.items() if k not in SERVER_ONLY_KEYS}",
     "    return dict(cfg)"),
    ("M14 app_for always answers with the appliance's own brain", R,
     "        name = self.brain_for(device_id)[\"brain\"]",
     "        name = getattr(self.app, 'name', '')"),
    ("M15 a failed build kills the turn instead of keeping the brain", R,
     "                return self.app\n            self._wire_memory_policy(app)",
     "                raise RuntimeError(note)\n            self._wire_memory_policy(app)"),
    ("M16 the failed-build note is repeated every turn", R,
     "                if self._brain_failed.get(name) != note:",
     "                if True:"),
    ("M17 the turn re-resolves the brain instead of carrying it", R,
     "            reply = app.respond(turn)",
     "            reply = self.app_for(device_id).respond(turn)"),
    ("M18 lifecycle hooks go to the appliance's own brain", R,
     "            self.app_for(device_id).on_event(robot, name, data)",
     "            self.app.on_event(robot, name, data)"),
    ("M19 a built brain misses the memory privacy gate", R,
     "            self._wire_memory_policy(app)",
     "            pass"),
    ("M20 reload_content only swaps the appliance's own brain", R,
     "        for app in self._content_apps():\n            if getattr(app, \"module\", None) is not None:",
     "        for app in [self.app]:\n            if getattr(app, \"module\", None) is not None:"),
    ("M21 the snapshot reports the appliance brain for every robot", R,
     '                "brain": self.brain_for(r.device_id)["brain"],',
     '                "brain": getattr(self.app, "name", ""),'),
    ("M22 brain_update stores a refused pick anyway", R,
     "        except ValueError as e:\n            return {\"ok\": False, \"error\": str(e), \"reason\": str(e)}",
     "        except ValueError as e:\n            name = None"),
]


def run():
    proc = subprocess.run([str(PY), "-m", "pytest", *TESTS, "-q", "-x", "--no-header"],
                          cwd=ROOT, capture_output=True, text=True,
                          env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                               "HOME": os.environ.get("HOME", "/tmp"),
                               # Blanked explicitly: a bare run finds the main worktree's
                               # `mqtt/.env` and would spend real gateway calls.
                               "MOXIE_LLM_API_KEY": "", "MOXIE_LLM_BASE_URL": "",
                               "MOXIE_VOICE_BASE_URL": "", "MOXIE_STT_BASE_URL": "",
                               "MOXIE_SKIP_DOTENV": "1"})
    return proc.returncode, proc.stdout.strip().splitlines()[-1] if proc.stdout else ""


def main():
    caught, missed = 0, []
    for label, rel, old, new in MUTATIONS:
        path = ROOT / rel
        backup = path.read_text()
        if backup.count(old) != 1:
            missed.append(f"{label}: anchor not unique ({backup.count(old)})")
            continue
        path.write_text(backup.replace(old, new, 1))
        try:
            code, tail = run()
        finally:
            path.write_text(backup)
        if code == 0:
            missed.append(f"{label}: STILL GREEN — {tail}")
        else:
            caught += 1
            print(f"✅ {label} → {tail}")
    print(f"\n{caught}/{len(MUTATIONS)} mutations caught")
    for m in missed:
        print("❌ " + m)
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
