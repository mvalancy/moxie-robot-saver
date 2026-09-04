"""Remove each guard the extension sandbox rests on, and check its test goes red.

"A test for every feature, proven in BOTH directions": a green suite proves the guards
are *present*, and this proves they are *load-bearing*. Run it by hand after touching
`ext.py`, `render.py`, `content_app.py`'s host half or `packs.py`'s pattern cap:

    python3 sim/tools/ext_mutation_check.py

Every row must say "caught". A row that says NOT CAUGHT means the assertion passes with
the guard deleted, which means it is not testing what its name claims.

Nothing here writes to the tree permanently — each mutation is reverted in a `finally`.
`PYTHONDONTWRITEBYTECODE` is not a nicety: without it a `__pycache__` entry from an
earlier mutation can shadow a later one, and a guard reads as un-caught when it is fine.
"""
import pathlib, subprocess
# Resolved from this file, like every other checker here. It used to be the literal path
# of the worktree it was written in (`.../wt-ext`), which stopped existing when that slice
# was merged — so this tool could not run **anywhere**, silently, until
# `sim/tests/test_mutation_tables.py` asked whether its anchors still resolved.
WT = pathlib.Path(__file__).resolve().parents[2]
EXT = WT / "mqtt/moxie_sdk/content/ext.py"
REN = WT / "mqtt/moxie_sdk/content/render.py"
CA  = WT / "mqtt/moxie_sdk/content/content_app.py"
PK  = WT / "mqtt/moxie_sdk/content/packs.py"

MUTATIONS = [
 ("X1  drop the `_`-segment path refusal", EXT,
  '            if seg.startswith("_"):', '            if False:', "x1_no_op_or_path"),
 ("X1  drop the fact-root refusal", EXT,
  "        if root not in FACT_ROOTS:", "        if False:", "x1_no_op_or_path"),
 ("X1  add an `eval` operator", EXT,
  '    "has": (1, 2, None), "keys": (1, 1, None),',
  '    "has": (1, 2, None), "keys": (1, 1, None), "eval": (1, 1, None),', "frozen"),
 ("X2  leak the live Volley into the fact base", CA,
  '        "presence": {},\n    }', '        "presence": {}, "volley": volley,\n    }',
  "x2_the_fact_base"),
 ("X3  swap the sandbox back to a plain jinja2 Environment", REN,
  "    from jinja2.sandbox import SandboxedEnvironment",
  "    from jinja2 import Environment as SandboxedEnvironment", "x3_a_prompt"),
 ("X3  drop the `_`-refusal in the dependency-free fallback", REN,
  '        if part.startswith("_"):', "        if False:", "x3_a_prompt"),
 ("X4  drop the step budget", EXT,
  "        if self.steps > self.limits.max_steps:", "        if False:", "x4_a_costly"),
 ("X4  drop the wall-clock budget", EXT,
  "            if self.monotonic() > self.deadline:", "            if False:",
  "x4_the_wall_clock"),
 ("X5  drop the per-value byte cap", EXT,
  "        if n > self.limits.max_value_bytes:", "        if False:", "x5_a_huge"),
 ("X5  drop the total-allocation cap", EXT,
  "        if self.total > self.limits.max_total_bytes:", "        if False:",
  "x5_the_total"),
 ("X6  drop the validator's depth cap", EXT,
  '        if depth > MAX_DEPTH:\n            return self.fail(f"{where}: nested deeper than {MAX_DEPTH}")',
  '        if False:\n            return self.fail("")', "x6_deep"),
 ("X6  drop the evaluator's depth cap", EXT,
  "        if depth > MAX_DEPTH:\n            # Unreachable",
  "        if False:\n            # Unreachable", "x6_the_evaluator"),
 ("X7  add `import time` to the evaluator", EXT,
  "import math\nimport re", "import math\nimport re\nimport time",
  "x7_the_evaluator_imports"),
 ("X7  read a fresh clock on every `clock.ms`", EXT,
  "            return self.now_ms                      # injected once per turn",
  "            self.now_ms += 1\n            return self.now_ms", "x7_two_clock"),
 ("X7  seed the PRNG from something other than the seed", EXT,
  "        self._s = int(seed) & 0xFFFFFFFF", "        self._s = 12345", "x7_the_same_seed"),
 ("X8  normalize-and-USE instead of normalize-and-check", EXT,
  '    if unicodedata.normalize("NFKC", raw) != raw:\n        return ""\n    return raw if _IDENT.match(raw) else ""',
  '    raw = unicodedata.normalize("NFKC", raw)\n    return raw if _IDENT.match(raw) else ""',
  "x8_unicode_tricks_cannot_change_a_capability"),
 ("X8  drop the NFKC check on operator names", EXT,
  '    if unicodedata.normalize("NFKC", raw) != raw:\n        return ""\n    if raw in SYMBOLIC_OPS:',
  '    raw = unicodedata.normalize("NFKC", raw)\n    if raw in SYMBOLIC_OPS:',
  "x8_unicode_tricks_cannot_change_an_op"),
 ("X9  widen the memory-key grammar to anything", EXT,
  '_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*(\\.[A-Za-z0-9][A-Za-z0-9_-]*)*$")',
  '_KEY = re.compile(r"^[^\\x00]+$")', "x9_a_traversal"),
 ("X9  let the effect choose its own namespace", CA,
  "                    got = memory.merge(device_id, namespace, {top: block[top]},",
  '                    got = memory.merge(eff.get("device_id", device_id), eff.get("namespace", namespace), {top: block[top]},',
  "x9_the_store_call"),
 ("X9  hand the whole persist_data to the evaluator", CA,
  '        memory = _ext_json(block) if isinstance(block, dict) else {}',
  '        memory = _ext_json(getattr(volley, "persist_data", None) or {})',
  "x9_an_extension_cannot_read"),
 ("X10 drop the uses-but-did-not-declare check", EXT,
  "    missing = sorted(v.used - declared)", "    missing = []", "x10_a_capability"),
 ("X10 drop the declares-but-never-uses check", EXT,
  "    spare = sorted(declared - v.used)", "    spare = []", "x10_a_capability"),
 ("X10 grant the still-P1 capabilities anyway", EXT,
  "    return cap in P1_CAPABILITIES", "    return cap in ()", "x10_p1"),
 ("X10 let a pack name a robot function the table does not", CA,
  "        if name not in known:", "        if False:",
  "x10_the_host_will_not_name"),
 ("X10 grant `act` to every pack by default", EXT,
  'DEFAULT_GRANTS = frozenset({"say", "handled", "session", "child.nickname"})',
  'DEFAULT_GRANTS = frozenset({"say", "handled", "session", "child.nickname",'
  ' "act.eb_timer_request", "act.eb_wake"})',
  "x10_an_act_is_bounded or x10_the_default_granted"),
 ("X10 drop the host grant check", EXT,
  "    if grants is not None:\n        ungranted = sorted(declared - set(grants))",
  "    if False:\n        ungranted = sorted(declared - set(grants))", "x10_every_gated or x7_a_fact_op"),
 ("X11 hand back the effect prefix on a breach", EXT,
  "        return ExtResult(ok=False, reason=b.reason, breach=b.kind, steps=m.steps)",
  "        return ExtResult(ok=False, effects=effects, notes=notes, reason=b.reason, breach=b.kind, steps=m.steps)",
  "x11_effects_are_all"),
 ("X11 let an error value be spoken", EXT,
  '        if is_error(text) or is_error(markup):\n            raise _Breach("error", "a value it worked out did not come out right")',
  "        if False:\n            pass", "x11_an_error_value"),
 ("§4.6 make division by zero raise instead of returning ERROR", EXT,
  "            if is_error(x) or is_error(y) or y == 0:\n                return ERROR                        # never an exception (§4.6)",
  "            if is_error(x) or is_error(y):\n                return ERROR", "x11_every_bad_input"),
 ("X12 drop the pattern length cap", PK,
  "        if len(pattern) > MAX_PATTERN_CHARS:", "        if False:", "x12_a_pathological"),
]

# The runner lives in `main()` behind a `__main__` guard, like the other four
# checkers. It used to be bare module-level code, so **importing** this file ran
# twenty-eight mutations against the working tree — which is exactly what
# `sim/tests/test_mutation_tables.py` did on its first draft, from inside pytest.
def main() -> int:
    caught = missed = noop = 0
    for name, path, old, new, sel in MUTATIONS:
        src = path.read_text()
        if old not in src:
            print(f"  NO-OP       {name}  (anchor not found)"); noop += 1; continue
        backup = src
        path.write_text(src.replace(old, new, 1))
        try:
            r = subprocess.run([str(WT / ".venv/bin/python"), "-m", "pytest",
                                "sim/tests/test_ext_escapes.py", "-q", "-k", sel,
                                "-p", "no:cacheprovider"],
                               cwd=WT, capture_output=True, text=True,
                               env={"PATH": "/usr/bin:/bin", "MOXIE_LLM_API_KEY": "",
                                    "HOME": "/home/scubasonar", "PYTHONDONTWRITEBYTECODE": "1"})
            if r.returncode == 0:
                print(f"  NOT CAUGHT  {name}"); missed += 1
            else:
                print(f"  caught      {name}"); caught += 1
        finally:
            path.write_text(backup)
    print(f"\nMUTATIONS: {caught} caught, {missed} missed, {noop} no-op")
    return 1 if (missed or noop) else 0


if __name__ == "__main__":
    raise SystemExit(main())
