"""Break each guard on the child's name that K7.1 left uncovered (W9, S7) or K14b added; the
name suites must go red.

W9 and S7 survived the whole hermetic suite after K7.1: the cap counted on what was typed
instead of the name as it is kept, and the no-hello branch (both hellos blocked) forced
off. The rest guard K14b: the spelling a name is kept in (a phone's apostrophe, the code
points a screen shows as nothing), the mask's edges, a clear that is owed when the
robot's record cannot take it, and the console's child writes off its event loop.

Bytecode is purged before every run (the baseline, each mutant, and a clean-tree control
after the last row): a mutant restored within the same second at the same size is
otherwise read back from a stale `__pycache__` by a later run, and a row passes or fails for
the wrong tree. Runner and verdicts: `mutation_runner.py`. The browser half (the Robot
access card saying a clear is owed) is a tooth in `sim/test_console_child_name.mjs`.

    python3 sim/tools/name_safety_mutation_check.py [ROW ...]
"""
import shutil
import subprocess

from mutation_runner import ENV, WT, pytest, run_table  # noqa: F401

CLOUD = "mqtt/moxie_sdk/cloud_config.py"
FLEET = "mqtt/supervisor/moxie_runtime/fleet.py"
PRESENCE = "mqtt/supervisor/moxie_runtime/presence.py"
ACCOUNT = "server/moxie_server/routes/account.py"
ROBOTS = "server/moxie_server/routes/robots.py"
PROFILE = "server/moxie_server/child_profile.py"
TESTS = ["sim/tests/test_child_name_safety.py", "sim/tests/test_console_child_name.py",
         "sim/tests/test_child_name.py"]

MUTATIONS = [
    # ---- K7.1's two survivors ----
    ("W9  the cap counted on what was typed, before NFC", CLOUD,
     "    name = _spelled(name)\n    if len(name) > NAME_MAX_CHARS or not _name_shape_ok(name):",
     "    raw, name = name, _spelled(name)\n"
     "    if len(raw) > NAME_MAX_CHARS or not _name_shape_ok(name):"),
    ("S7  a hello the rules block is said even when the generic one is blocked too", PRESENCE,
     "        if again and again.action == safety_seam.BLOCK:",
     "        if False:"),

    # ---- the name as it is kept ----
    ("N1  a phone's apostrophe kept as typed", CLOUD,
     '    text = _DEFAULT_IGNORABLE.sub("", str(name).translate(_APOSTROPHES))\n'
     '    return unicodedata.normalize("NFC", " ".join(text.split()))',
     '    text = _DEFAULT_IGNORABLE.sub("", str(name))\n'
     '    return unicodedata.normalize("NFC", " ".join(text.split()))'),
    ("N2  code points a screen shows as nothing kept in the name", CLOUD,
     '    text = _DEFAULT_IGNORABLE.sub("", str(name).translate(_APOSTROPHES))\n'
     '    return unicodedata.normalize("NFC", " ".join(text.split()))',
     '    text = str(name).translate(_APOSTROPHES)\n'
     '    return unicodedata.normalize("NFC", " ".join(text.split()))'),
    ("N3  whitespace left uncollapsed once they are dropped", CLOUD,
     '    text = _DEFAULT_IGNORABLE.sub("", str(name).translate(_APOSTROPHES))\n'
     '    return unicodedata.normalize("NFC", " ".join(text.split()))',
     '    text = _DEFAULT_IGNORABLE.sub("", str(name).translate(_APOSTROPHES))\n'
     '    return unicodedata.normalize("NFC", text)'),
    ("N4  an invisible name judged as typed, not as empty", CLOUD,
     "    name = _spelled(raw)\n    if not name:",
     '    name = " ".join(raw.split())\n    if not name:'),
    ("N5  an enclosing mark (a keycap) taken after a letter", CLOUD,
     '            if not after_letter or kind == "Me":',
     "            if not after_letter:"),

    # ---- the mask ----
    ("M1  the mask masks its own mark again", CLOUD,
     '    return _re.compile(r"(?-i:%s)|',
     '    return _re.compile(r"(?-i:%s)(?!)|'),
    ("M2  a line's phone apostrophe not taken for the name's", CLOUD,
     "    forms |= {f.replace(\"'\", mark) for f in forms if \"'\" in f",
     "    forms |= {f.replace(\"'\", mark) for f in forms if False"),
    ("M3  the mask built from the name as saved, not as kept", CLOUD,
     "    whole = _spelled(name)\n",
     '    whole = unicodedata.normalize("NFC", " ".join(str(name).split()))\n'),
    ("M4  a name the load drops leaves the masked names", FLEET,
     '        names.update(getattr(self, "_child_names_dropped", None) or ())',
     "        names.update(())"),
    ("M5  the load does not keep the name it drops", FLEET,
     '                self._child_names_dropped.add(record["nickname"])',
     '                self._child_names_dropped.discard(record["nickname"])'),

    # ---- a clear that is owed ----
    ("O1  a revoke's clear that cannot be saved is not owed", FLEET,
     "        kept_owed = saved or self._owe_child_clear(device_id)",
     "        kept_owed = saved"),
    ("O2  an owed clear kept in RAM only (no note survives a restart)", FLEET,
     "            return bool(self.store.write(device_id, self.CHILD_CLEAR_COLLECTION,",
     "            return False and bool((self.CHILD_CLEAR_COLLECTION,"),
    ("O3  the load ignores an owed clear", FLEET,
     '            if device_id in self._child_clears_owed and "child" in kept:',
     '            if False and "child" in kept:'),
    ("O4  the next connect does not make an owed clear", FLEET,
     "        if self.child_clear_owed(device_id):\n            self._settle_child_clear(device_id)",
     "        if False:\n            self._settle_child_clear(device_id)"),
    ("O5  a save that lands leaves the clear owed", FLEET,
     "            self._child_clear_paid(device_id)\n        return saved",
     "            pass\n        return saved"),
    ("O6  a revoke takes 'no name in RAM' for 'nothing owed'", FLEET,
     "        if not had and self.settings_saved(device_id) and not self.child_clear_owed(device_id):",
     "        if not had and self.settings_saved(device_id):"),
    ("O7  a record that failed closed never takes the clear", FLEET,
     "        if not self.failed_closed(device_id):\n"
     "            return self._save_config_overrides(device_id, held, quiet=quiet)",
     "        if True:\n"
     "            return self._save_config_overrides(device_id, held, quiet=quiet)"),
    ("O8  a record that failed closed is rewritten from RAM, not kept as found", FLEET,
     '            raw.pop("child")',
     "            raw = dict(self._config_overrides.get(device_id) or {})"),
    ("O9  a parent's clear that cannot be saved is not owed", FLEET,
     '        if "child" in overrides and overrides["child"] is None and not saved:',
     "        if False:"),
    ("O10 a revoke's answer ignores the clear its own push made", FLEET,
     '            out["child_cleared"] = cleared or not self.child_clear_owed(device_id)',
     '            out["child_cleared"] = cleared'),
    ("O11 the push's retry repeats the NOT saved line", FLEET,
     "            if not quiet:",
     "            if True:"),
    ("O12 the unpair names a Revoke button a revoked robot does not have", ROBOTS,
     '    elif access["revoked"]:',
     "    elif False:"),

    # ---- the console ----
    ("C1  the name check blocks the console's event loop", ACCOUNT,
     "    why = await run_in_threadpool(child_profile.refusal_for, name)",
     "    why = child_profile.refusal_for(name)"),
    ("C2  the rename's push blocks the console's event loop", ACCOUNT,
     '    pushed = await run_in_threadpool(child_profile.push_to_robots_of_child, u["id"], cid)',
     '    pushed = child_profile.push_to_robots_of_child(u["id"], cid)'),
    ("C3  a new child's record kept as typed", ACCOUNT,
     '    attrs = child_profile.spelled_record(body.get("child", body))',
     '    attrs = body.get("child", body)'),
    ("C4  a renamed child's record kept as typed", ACCOUNT,
     '    attrs = child_profile.spelled_record({**before, **body.get("child", body)})',
     '    attrs = {**before, **body.get("child", body)}'),
    ("C5  the console's copy of the spelling drifts", PROFILE,
     '    text = _DEFAULT_IGNORABLE.sub("", str(name).translate(_APOSTROPHES))',
     "    text = str(name).translate(_APOSTROPHES)"),
]


def purge_bytecode():
    """Every `__pycache__` the suites could read a stale module from: the mutated trees
    (`mqtt/`, `server/`) and the tests' own (not `sim/web`, which holds no Python)."""
    for tree in ("mqtt", "server", "sim/tests", "sim/tools"):
        for cache in (WT / tree).rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)


def command(_row):
    purge_bytecode()                       # after the mutant is written, before it runs
    return pytest(TESTS)


if __name__ == "__main__":
    purge_bytecode()                       # the baseline reads no earlier run's bytecode
    rc = run_table(MUTATIONS, command, baseline=[pytest(TESTS)])
    purge_bytecode()
    control = subprocess.run(pytest(TESTS), cwd=WT, capture_output=True, text=True, env=ENV)
    tail = (control.stdout.strip().splitlines() or [""])[-1]
    print(f"CONTROL (clean tree after the last row): "
          f"{'green' if control.returncode == 0 else 'RED'}  ({tail})")
    raise SystemExit(rc or control.returncode)
