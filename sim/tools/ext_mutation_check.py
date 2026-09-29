"""Break each guard the extension sandbox rests on; its `test_ext_escapes.py` test (the
row's `-k` selector) must go red. Run after touching `ext/`, `render.py`, `ext_host.py` or
`packs/`'s pattern cap. Runner and verdicts: `mutation_runner.py`.

    python3 sim/tools/ext_mutation_check.py [ROW ...]
"""
from mutation_runner import WT, node_verdict, pytest, run_table  # noqa: F401

EXT_G = WT / "mqtt/moxie_sdk/content/ext/grammar.py"
EXT_V = WT / "mqtt/moxie_sdk/content/ext/values.py"
EXT_L = WT / "mqtt/moxie_sdk/content/ext/validate.py"
EXT_M = WT / "mqtt/moxie_sdk/content/ext/machine.py"
REN = WT / "mqtt/moxie_sdk/content/render.py"
HOST = WT / "mqtt/moxie_sdk/content/ext_host.py"
PK  = WT / "mqtt/moxie_sdk/content/packs/items.py"
TESTS = "sim/tests/test_ext_escapes.py"

MUTATIONS = [
 # The anchor carries the `for` line above it because `if seg.startswith("_"):` occurs
 # TWICE in ext.py: here in `_var` (the LOAD-TIME refusal this row is about) and again in
 # `lookup`, the belt-and-braces that answers null for the same path at RUNTIME. The bare
 # one-liner matched both, and `replace(old, new, 1)` silently took whichever came first —
 # right by luck of line order, and proving nothing that a reordering could not undo.
 ("X1  drop the `_`-segment path refusal", EXT_L,
  '        for seg in arg.split("."):\n            if seg.startswith("_"):',
  '        for seg in arg.split("."):\n            if False:', "x1_no_op_or_path"),
 ("X1  drop the fact-root refusal", EXT_L,
  "        if root not in FACT_ROOTS:", "        if False:", "x1_no_op_or_path"),
 ("X1  add an `eval` operator", EXT_G,
  '    "has": (1, 2, None), "keys": (1, 1, None),',
  '    "has": (1, 2, None), "keys": (1, 1, None), "eval": (1, 1, None),', "frozen"),
 ("X2  leak the live Volley into the fact base", HOST,
  '        "presence": {},\n    }', '        "presence": {}, "volley": volley,\n    }',
  "x2_the_fact_base"),
 ("X3  swap the sandbox back to a plain jinja2 Environment", REN,
  "    from jinja2.sandbox import SandboxedEnvironment",
  "    from jinja2 import Environment as SandboxedEnvironment", "x3_a_prompt"),
 ("X3  drop the `_`-refusal in the dependency-free fallback", REN,
  '        if part.startswith("_"):', "        if False:", "x3_a_prompt"),
 ("X4  drop the step budget", EXT_M,
  "        if self.steps > self.limits.max_steps:", "        if False:", "x4_a_costly"),
 ("X4  drop the wall-clock budget", EXT_M,
  "            if self.monotonic() > self.deadline:", "            if False:",
  "x4_the_wall_clock"),
 ("X5  drop the per-value byte cap", EXT_M,
  "        if n > self.limits.max_value_bytes:", "        if False:", "x5_a_huge"),
 ("X5  drop the total-allocation cap", EXT_M,
  "        if self.total > self.limits.max_total_bytes:", "        if False:",
  "x5_the_total"),
 ("X6  drop the validator's depth cap", EXT_L,
  '        if depth > MAX_DEPTH:\n            return self.fail(f"{where}: nested deeper than {MAX_DEPTH}")',
  '        if False:\n            return self.fail("")', "x6_deep"),
 ("X6  drop the evaluator's depth cap", EXT_M,
  "        if depth > MAX_DEPTH:\n            # Unreachable",
  "        if False:\n            # Unreachable", "x6_the_evaluator"),
 ("X7  add `import time` to the evaluator", EXT_M,
  "import math\nimport re", "import math\nimport re\nimport time",
  "x7_the_evaluator_imports"),
 ("X7  read a fresh clock on every `clock.ms`", EXT_M,
  "            return self.now_ms                      # injected once per turn",
  "            self.now_ms += 1\n            return self.now_ms", "x7_two_clock"),
 ("X7  seed the PRNG from something other than the seed", EXT_V,
  "        self._s = int(seed) & 0xFFFFFFFF", "        self._s = 12345", "x7_the_same_seed"),
 ("X8  normalize-and-USE instead of normalize-and-check", EXT_G,
  '    if unicodedata.normalize("NFKC", raw) != raw:\n        return ""\n    return raw if _IDENT.match(raw) else ""',
  '    raw = unicodedata.normalize("NFKC", raw)\n    return raw if _IDENT.match(raw) else ""',
  "x8_unicode_tricks_cannot_change_a_capability"),
 ("X8  drop the NFKC check on operator names", EXT_G,
  '    if unicodedata.normalize("NFKC", raw) != raw:\n        return ""\n    if raw in SYMBOLIC_OPS:',
  '    raw = unicodedata.normalize("NFKC", raw)\n    if raw in SYMBOLIC_OPS:',
  "x8_unicode_tricks_cannot_change_an_op"),
 ("X9  widen the memory-key grammar to anything", EXT_G,
  '_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*(\\.[A-Za-z0-9][A-Za-z0-9_-]*)*$")',
  '_KEY = re.compile(r"^[^\\x00]+$")', "x9_a_traversal"),
 ("X9  let the effect choose its own namespace", HOST,
  "                    got = memory.merge(device_id, namespace, {top: block[top]},",
  '                    got = memory.merge(eff.get("device_id", device_id), eff.get("namespace", namespace), {top: block[top]},',
  "x9_the_store_call"),
 ("X9  hand the whole persist_data to the evaluator", HOST,
  '        memory = _ext_json(block) if isinstance(block, dict) else {}',
  '        memory = _ext_json(getattr(volley, "persist_data", None) or {})',
  "x9_an_extension_cannot_read"),
 ("X10 drop the uses-but-did-not-declare check", EXT_L,
  "    missing = sorted(v.used - declared)", "    missing = []", "x10_a_capability"),
 ("X10 drop the declares-but-never-uses check", EXT_L,
  "    spare = sorted(declared - v.used)", "    spare = []", "x10_a_capability"),
 ("X10 grant the still-P1 capabilities anyway", EXT_G,
  "    return cap in P1_CAPABILITIES", "    return cap in ()", "x10_p1"),
 # `if name not in known:` occurs twice in content_app.py — once in `execution_actions_of`
 # (robot FUNCTIONS, which is this row) and once in `subscriptions_of` (robot EVENTS,
 # which is `subscribe_mutation_check.py`'s S4). Two different guards, one anchor. The
 # `name = str(...)` line above is unique to the functions gate, so the row now names the
 # block it is about. Note that S4 was written disambiguated from the start: its table
 # enforces a unique anchor, so its author was FORCED to. This one was not.
 ("X10 let a pack name a robot function the table does not", HOST,
  '        name = str((entry or {}).get("name") or "")\n        if name not in known:',
  '        name = str((entry or {}).get("name") or "")\n        if False:',
  "x10_the_host_will_not_name"),
 ("X10 grant `act` to every pack by default", EXT_G,
  'DEFAULT_GRANTS = frozenset({"say", "handled", "session", "child.nickname"})',
  'DEFAULT_GRANTS = frozenset({"say", "handled", "session", "child.nickname",'
  ' "act.eb_timer_request", "act.eb_wake"})',
  "x10_an_act_is_bounded or x10_the_default_granted"),
 ("X10 drop the host grant check", EXT_L,
  "    if grants is not None:\n        ungranted = sorted(declared - set(grants))",
  "    if False:\n        ungranted = sorted(declared - set(grants))", "x10_every_gated or x7_a_fact_op"),
 ("X11 hand back the effect prefix on a breach", EXT_M,
  "        return ExtResult(ok=False, reason=b.reason, breach=b.kind, steps=m.steps)",
  "        return ExtResult(ok=False, effects=effects, notes=notes, reason=b.reason, breach=b.kind, steps=m.steps)",
  "x11_effects_are_all"),
 ("X11 let an error value be spoken", EXT_M,
  '        if is_error(text) or is_error(markup):\n            raise _Breach("error", "a value it worked out did not come out right")',
  "        if False:\n            pass", "x11_an_error_value"),
 ("§4.6 make division by zero raise instead of returning ERROR", EXT_M,
  "            if is_error(x) or is_error(y) or y == 0:\n                return ERROR                        # never an exception (§4.6)",
  "            if is_error(x) or is_error(y):\n                return ERROR", "x11_every_bad_input"),
 ("X12 drop the pattern length cap", PK,
  "        if len(pattern) > MAX_PATTERN_CHARS:", "        if False:", "x12_a_pathological"),
]


if __name__ == "__main__":
    raise SystemExit(run_table(MUTATIONS, lambda r: pytest(TESTS, r[4]), baseline=[pytest(TESTS)]))
