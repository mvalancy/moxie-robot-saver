"""Break each brain-registry guard (positive list, pin, layer precedence, server-only key
filter, once-per-turn resolution); `test_brains.py` + `test_brain_runtime.py` must go red.
Runner and verdicts: `mutation_runner.py`.

    python3 sim/tools/brain_mutation_check.py [ROW ...]
"""
from mutation_runner import WT, node_verdict, pytest, run_table  # noqa: F401

B = "mqtt/moxie_sdk/brains.py"
C = "mqtt/config.py"
CC = "mqtt/moxie_sdk/cloud_config.py"
R_BRAIN = "mqtt/supervisor/moxie_runtime/brain.py"
R_CONTENT = "mqtt/supervisor/moxie_runtime/content.py"
R_LIFECYCLE = "mqtt/supervisor/moxie_runtime/lifecycle.py"
R_TURNS = "mqtt/supervisor/moxie_runtime/turns.py"
TESTS = ["sim/tests/test_brains.py", "sim/tests/test_brain_runtime.py"]

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
    ("M14 app_for always answers with the appliance's own brain", R_BRAIN,
     "        name = self.brain_for(device_id)[\"brain\"]",
     "        name = getattr(self.app, 'name', '')"),
    ("M15 a failed build kills the turn instead of keeping the brain", R_BRAIN,
     "                return self.app\n            self._wire_memory_policy(app)",
     "                raise RuntimeError(note)\n            self._wire_memory_policy(app)"),
    ("M16 the failed-build note is repeated every turn", R_BRAIN,
     "                if self._brain_failed.get(name) != note:",
     "                if True:"),
    ("M17 the turn re-resolves the brain instead of carrying it", R_TURNS,
     "            reply = self._safe_respond(turn, app=app)",
     "            reply = self._safe_respond(turn, app=self.app_for(device_id))"),
    ("M18 lifecycle hooks go to the appliance's own brain", R_TURNS,
     "            self.app_for(device_id).on_event(robot, name, data)",
     "            self.app.on_event(robot, name, data)"),
    ("M19 a built brain misses the memory privacy gate", R_BRAIN,
     "            self._wire_memory_policy(app)",
     "            pass"),
    ("M20 reload_content only swaps the appliance's own brain", R_CONTENT,
     "        for app in self._content_apps():\n            if getattr(app, \"module\", None) is not None:",
     "        for app in [self.app]:\n            if getattr(app, \"module\", None) is not None:"),
    ("M21 the snapshot reports the appliance brain for every robot", R_LIFECYCLE,
     '                "brain": self.brain_for(r.device_id)["brain"],',
     '                "brain": getattr(self.app, "name", ""),'),
    ("M22 brain_update stores a refused pick anyway", R_BRAIN,
     "        except ValueError as e:\n            return {\"ok\": False, \"error\": str(e), \"reason\": str(e)}",
     "        except ValueError as e:\n            name = None"),
]


if __name__ == "__main__":
    raise SystemExit(run_table(MUTATIONS, lambda r: pytest(TESTS, None, "-x"), baseline=[pytest(TESTS)]))
