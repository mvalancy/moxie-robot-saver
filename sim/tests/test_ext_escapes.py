"""X1–X12 — escape tests for sandboxed content extensions.

Kept apart from `test_ext.py`'s behaviour tests so "can a stranger's content pack hurt this
appliance?" is answered by one file. The design (`backlog/sandboxed-extensions.md` §3.2) is a
declarative rule list over a total JSON-AST expression language — no `exec`, parser, loop or
reachable host object — so these are provable properties of a closed table.

Each test names the guard it fences; its "Mutation checked" note records the hand-removed
guard that made it fail.
"""
import ast as pyast
import json
import os
import sys

import pytest

from moxie_sdk.content import content_app as CA
from moxie_sdk.content import ext as E
from moxie_sdk.content import packs as P
from moxie_sdk.content import render as R
from moxie_sdk.content.volley import Volley, Session
from moxie_sdk.store import JsonStore
from moxie_sdk.memory_store import MemoryStore

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

CONTENT = os.path.join(REPO, "mqtt", "moxie_sdk", "content")
EXT_DIR = os.path.join(CONTENT, "ext")
#: Every module of the evaluator package. X7 bounds the package, not one file, so a new
#: sibling is inside the boundary the moment it exists.
EXT_FILES = sorted(os.path.join(EXT_DIR, f) for f in os.listdir(EXT_DIR) if f.endswith(".py"))


def facts(**kw):
    """The §4.4 fact base, as the host builds it. Plain JSON, and nothing else."""
    base = {"speech": "", "entities": [], "input_vars": {}, "scratch": {},
            "child": {"nickname": "Sam"}, "memory": {},
            "session": {"total_volleys": 0, "is_empty": True, "overflow": False},
            "presence": {"face_present": False, "line": ""}}
    base.update(kw)
    return base


def ext(rules, caps=("say",), on="global"):
    return {"ext_format": 1, "capabilities": list(caps), "on": on, "rules": rules}


def say(expr, caps=("say",)):
    return ext([{"do": [{"say": expr}]}], caps=caps)


# --------------------------------------------------------------------------- #
# X1 — no op and no path can name an import or a dunder
# --------------------------------------------------------------------------- #

#: The operator table, frozen as a literal — the audit surface (§4.2) and risk R1's brake:
#: "just one more op" cannot land without a test edit and a reviewer.
FROZEN_OPS = {
    "+", "-", "*", "/", "%", "floor", "ceil", "round", "abs", "min", "max",
    "==", "!=", "<", "<=", ">", ">=",
    "and", "or", "not", "if",
    "concat", "lower", "upper", "trim", "len", "slice", "starts_with", "ends_with",
    "contains", "replace", "split", "join", "repeat", "format", "str", "plural",
    "int", "num",
    "list", "get", "compact", "reverse", "sort",
    "has", "keys",
    "clock.ms", "clock.local", "random.int", "random.pick",
    "presence.face_present", "session.total_volleys", "session.is_empty",
}

#: The statement table, frozen for the same reason.
FROZEN_STATEMENTS = {"say", "markup", "remember", "forget", "scratch", "act",
                     "subscribe", "brain", "handled", "note"}

#: The fact roots. With `FROZEN_OPS`, §5.2's invariant: everything that resolves to anything
#: is finite and enumerated in our own source.
FROZEN_FACT_ROOTS = {"speech", "entities", "input_vars", "scratch", "child", "memory",
                     "session", "presence"}

#: `{name: (expression, capabilities a legal version would need)}` — each probe declares
#: exactly those, so the ONLY thing left to refuse it is the guard under test.
ESCAPE_ASTS = {
    "dunder_class_on_a_host_object": ({"var": "volley.__class__"}, ()),
    "builtins": ({"var": "__builtins__"}, ()),
    "private_memory_meta": ({"var": "memory._meta"}, ("memory.read",)),
    "private_provenance": ({"var": "memory.timers._provenance"}, ("memory.read",)),
    "dunder_init_globals": ({"var": "session.__init__.__globals__"}, ("session",)),
    "private_child_field": ({"var": "child._secret"}, ("child.profile",)),
    "private_scratch": ({"var": "scratch._x"}, ()),
    "private_input_var": ({"var": "input_vars._token"}, ()),
    "import_op": ({"import": ["os"]}, ()),
    "getattr_op": ({"getattr": [{"var": "speech"}, "__class__"]}, ()),
    "eval_op": ({"eval": ["1+1"]}, ()),
    "exec_op": ({"exec": ["import os"]}, ()),
    "open_op": ({"open": ["/etc/passwd"]}, ()),
    "fetch_op": ({"fetch": ["http://example.invalid"]}, ()),
    "subprocess_op": ({"subprocess": ["ls"]}, ()),
    "env_root": ({"var": "environ.MOXIE_LLM_API_KEY"}, ()),
    "os_root": ({"var": "os.environ"}, ()),
    "config_root": ({"var": "config.api_key"}, ()),
}


@pytest.mark.parametrize("name", sorted(ESCAPE_ASTS))
def test_x1_no_op_or_path_can_name_import_or_a_dunder(name):
    """X1 — an unknown op, a non-fact root or a `_`-leading segment is not a valid program,
    so it is never evaluated.

    Mutation checked: `_Validator._var`'s `_`-segment and `FACT_ROOTS` refusals.
    """
    expr, needed = ESCAPE_ASTS[name]
    e = say(expr, caps=("say",) + needed)
    reasons = E.validate(e)
    assert reasons, f"{name} was not refused at load"
    assert not any("declare" in x or "never uses" in x for x in reasons), \
        f"{name} was refused for the wrong reason: {reasons}"
    # `evaluate` re-validates, so a caller that skipped `validate` is protected too.
    grants = E.DEFAULT_GRANTS | set(needed)
    r = E.evaluate(e, facts(), grants=grants)
    assert not r.ok and r.breach == "invalid"
    assert r.effects == []


def test_x1_the_op_table_is_frozen():
    """X1 — the op, statement and fact-root sets EQUAL frozen literals (R1's brake).

    Mutation checked: adding `"eval"` to `ext.OPS`.
    """
    assert set(E.OPS) == FROZEN_OPS
    assert set(E.STATEMENTS) == FROZEN_STATEMENTS
    assert set(E.FACT_ROOTS) == FROZEN_FACT_ROOTS
    resolvable = set(E.OPS) | set(E.FACT_ROOTS) | {"lit", "var"}
    for forbidden in ("import", "getattr", "eval", "exec", "open", "fetch", "require",
                      "subprocess", "environ", "os", "sys", "globals", "config",
                      "volley", "store", "loop", "for", "while", "def",
                      "call", "func", "lambda", "regex", "sleep"):
        assert forbidden not in resolvable, f"{forbidden!r} resolves to something"


def test_x1_no_grammar_construct_defines_or_calls_anything():
    """X1 — `let` binds values, never references, so cost is statically computable (§4.3)."""
    e = ext([{"let": {"f": {"lit": {"do": [{"say": "hi"}]}}},
              "do": [{"say": {"str": [{"var": "f"}]}}]}])
    r = E.evaluate(e, facts(), grants=E.DEFAULT_GRANTS)
    assert r.ok
    # The literal came back as *text*, not as a program that ran.
    assert r.effects == [{"kind": "say", "text": "", "markup": None}]


# --------------------------------------------------------------------------- #
# X2 — the fact base contains no host object
# --------------------------------------------------------------------------- #

def _walk_types(v, path="facts", seen=None):
    """Every value in the structure, with the path that reached it."""
    out = [(path, v)]
    if isinstance(v, dict):
        for k, sub in v.items():
            out += _walk_types(sub, f"{path}.{k}")
    elif isinstance(v, list):
        for i, sub in enumerate(v):
            out += _walk_types(sub, f"{path}[{i}]")
    return out


def test_x2_the_fact_base_contains_no_host_object():
    """X2 — the fact base is plain JSON all the way down: nothing to attribute-walk (§4.4).

    Mutation checked: the live `Volley` in `ext_facts`.
    """
    v = Volley("what time is it", config={"child_pii": {"nickname": "Sam",
                                                        "pronouns": "she/her",
                                                        "birthday": "2018-04-01",
                                                        "notes": "loves dinosaurs"}},
               request={"input_vars": {"eb_timer_id": "1"}}, entities=["5", "minute"],
               persist_data={"ext:timer": {"timers": {"1": 12345}}})
    built = CA.ext_facts(v, Session(history=[{"role": "user", "content": "hi"}]),
                         namespace="ext:timer",
                         grants=E.DEFAULT_GRANTS | {"memory.read", "presence",
                                                    "child.profile"},
                         presence={"face_present": True, "line": "Sam is here"})
    assert isinstance(built, dict)
    for path, value in _walk_types(built):
        assert isinstance(value, (str, int, float, bool, dict, list)) or value is None, \
            f"{path} is a {type(value).__name__}"
        if isinstance(value, dict):
            for k in value:
                assert isinstance(k, str), f"{path} has a non-string key {k!r}"
    # The evaluator is handed *that*, and a program still cannot reach off it.
    r = E.evaluate(say({"str": [{"var": "child.nickname"}]}, caps=("say",
                                                                  "child.nickname")),
                   built, grants=E.DEFAULT_GRANTS)
    assert r.ok and r.effects[0]["text"] == "Sam"


def test_x2_a_hostile_fact_base_still_cannot_produce_an_object():
    """X2 — even a leaked object is inert (no op does attribute access), so one host bug is
    not an escape."""
    class Hostile:
        secret = "sk-should-never-appear"

        def __init__(self):
            self.environ = {"MOXIE_LLM_API_KEY": "sk-should-never-appear"}

    poisoned = facts()
    poisoned["memory"] = {"leaked": Hostile()}
    e = say({"concat": ["<", {"str": [{"var": "memory.leaked"}]}, ">",
                        {"str": [{"var": "memory.leaked.environ"}]}]},
            caps=("say", "memory.read"))
    r = E.evaluate(e, poisoned, grants=E.DEFAULT_GRANTS | {"memory.read"})
    assert r.ok
    assert r.effects[0]["text"] == "<>", r.effects
    assert "sk-" not in r.effects[0]["text"]


# --------------------------------------------------------------------------- #
# X3 — a prompt cannot execute Python through Jinja
# --------------------------------------------------------------------------- #

#: X3 fences the template renderer (owned by `test_render_sandbox.py`), repeated here so an
#: audit of this sandbox sees the appliance's other execution surface in the same file.
JINJA_ESCAPES = {
    "volley_init_globals_builtins": "{{ volley.__init__.__globals__['__builtins__'] }}",
    "volley_init_globals_import_getcwd":
        "{{ volley.__init__.__globals__['__builtins__']['__import__']('os').getcwd() }}",
    "volley_class": "{{ volley.__class__ }}",
    "session_class_mro": "{{ session.__class__.__mro__ }}",
    "volley_config_walk": "{{ volley.config.__class__.__init__.__globals__ }}",
    "session_repr_globals_environ":
        "{{ session.__class__.__repr__.__globals__.inspect.os.environ }}",
}

#: Anything here in a rendered prompt means the template reached off its leash.
JINJA_LEAKS = ("posix", "/home/", "C:\\", "<class ", "builtins", "MOXIE_", "sk-",
               "subclasses", "Environment")


@pytest.mark.parametrize("name", sorted(JINJA_ESCAPES))
def test_x3_a_prompt_cannot_execute_python_through_jinja(name, monkeypatch):
    """X3 — a pack's `prompt` is not a code-execution channel, with jinja2 installed or
    absent (both shapes run; a skip cannot read as coverage).

    Mutation checked: plain `jinja2.Environment` in `render._sandbox()`; the `_`-refusal
    in `render._resolve` (fallback shape).
    """
    monkeypatch.setenv("MOXIE_LLM_API_KEY", "sk-x3-canary-value")
    v = Volley("hi", config={"child_pii": {"nickname": "Sam"}})
    ctx = {"volley": v, "session": Session(), "presence": {"face_present": False}}

    for shape in ("as shipped", "without jinja2"):
        if shape == "without jinja2":
            monkeypatch.setitem(sys.modules, "jinja2", None)      # ImportError on import
            monkeypatch.setitem(sys.modules, "jinja2.sandbox", None)
        before = R.BLOCKED
        out = R.render_prompt(JINJA_ESCAPES[name], ctx)
        assert isinstance(out, str), shape
        low = out.lower()
        for leak in JINJA_LEAKS:
            assert leak.lower() not in low, f"{name} leaked {leak!r} ({shape}): {out[:200]!r}"
        assert len(out) < 400, f"{name} returned {len(out)} chars ({shape})"
        assert R.BLOCKED >= before, shape          # refusals are counted, not swallowed


def test_x3_ordinary_templating_still_works_in_both_shapes(monkeypatch):
    """X3's other direction — a sandbox that broke templating would just get reverted."""
    v = Volley("hi", config={"child_pii": {"nickname": "Sam"}})
    ctx = {"volley": v, "session": Session()}
    tpl = "Hi {{ volley.config.child_pii.nickname }}!"
    assert R.render_prompt(tpl, ctx) == "Hi Sam!"
    monkeypatch.setitem(sys.modules, "jinja2", None)
    monkeypatch.setitem(sys.modules, "jinja2.sandbox", None)
    assert R.render_prompt(tpl, ctx) == "Hi Sam!"


def test_x3_an_extension_is_the_only_other_execution_surface():
    """X3 — no file under `content/` calls exec/eval/compile/__import__ (§7.4)."""
    names = [os.path.relpath(os.path.join(d, f), CONTENT)
             for d, _s, files in os.walk(CONTENT) for f in files if f.endswith(".py")]
    assert {"content_app.py", "render.py", "ext/machine.py", "packs/review.py"} <= set(names)
    for name in names:
        tree = pyast.parse(open(os.path.join(CONTENT, name)).read())
        for node in pyast.walk(tree):
            if isinstance(node, pyast.Call) and isinstance(node.func, pyast.Name):
                assert node.func.id not in ("exec", "eval", "compile", "__import__"), \
                    f"{name} calls {node.func.id}()"


# --------------------------------------------------------------------------- #
# X4 — an infinite loop is unrepresentable, and the budget still holds
# --------------------------------------------------------------------------- #

def test_x4_repeat_is_a_bounded_string_builder_not_a_loop():
    """X4(i) — no loop op exists (the frozen table in X1 pins that); `repeat` is capped."""
    r = E.evaluate(say({"repeat": ["ab", 1000]}), facts(), grants=E.DEFAULT_GRANTS)
    assert r.ok and r.effects[0]["text"] == "ab" * E.MAX_REPEAT


def test_x4_a_costly_ast_hits_the_step_budget_and_returns():
    """X4(ii) — a costly AST hits `MOXIE_EXT_MAX_STEPS` and discards its effects.

    Mutation checked: the `steps > max_steps` raise in `_Machine.step`.
    """
    costly = {"concat": [{"str": [{"+": list(range(16))}]}] * 32}   # ~577 nodes
    e = say(costly)
    assert E.validate(e) == [], "the AST itself must be legal — the budget is the point"
    r = E.evaluate(e, facts(), grants=E.DEFAULT_GRANTS,
                   limits=E.Limits(max_steps=50, budget_s=1e9))
    assert not r.ok and r.breach == "steps", r
    assert r.effects == []
    assert r.steps <= 51
    # The same AST within budget works: the test is about the budget.
    ok = E.evaluate(e, facts(), grants=E.DEFAULT_GRANTS)
    assert ok.ok and ok.effects[0]["text"] == "120" * 32


def test_x4_the_wall_clock_budget_holds_without_threads_or_signals():
    """X4(ii) — the wall-clock budget is an injected monotonic reading (no thread/signal).

    Mutation checked: the `monotonic() > deadline` raise.
    """
    node = {"concat": [{"str": [{"+": list(range(16))}]}] * 32}    # ~577 nodes
    clock = {"t": 0.0}

    def monotonic():
        clock["t"] += 0.2                     # each reading is 0.2 s later
        return clock["t"]

    r = E.evaluate(say(node), facts(), grants=E.DEFAULT_GRANTS,
                   limits=E.Limits(max_steps=10 ** 9, budget_s=0.25),
                   monotonic=monotonic)
    assert not r.ok and r.breach == "budget", r
    assert r.effects == []


# --------------------------------------------------------------------------- #
# X5 — a huge allocation fails the op, not the process
# --------------------------------------------------------------------------- #

HUGE = {
    "repeat_nested_to_depth_8": None,          # built below (needs recursion in Python)
    "concat_of_16_x_2KiB": {"concat": ["A" * 2000] * 16},
    "join_over_a_big_list": {"join": [{"split": [{"repeat": ["a,", 16]}, ","]},
                                      "x" * 1200]},
    "format_with_a_huge_width": {"format": ["99999d", 7]},
}
_node = {"repeat": ["AAAAAAAAAAAAAAAA", 16]}
for _ in range(7):
    _node = {"repeat": [_node, 16]}
HUGE["repeat_nested_to_depth_8"] = _node


@pytest.mark.parametrize("name", sorted(HUGE))
def test_x5_a_huge_allocation_fails_the_op_not_the_process(name):
    """X5 — every allocation path hits the value/total byte cap; never a MemoryError.

    Mutation checked: the `n > max_value_bytes` raise in `_Machine.charge`.
    """
    e = say(HUGE[name])
    assert E.validate(e) == [], "the AST is legal; the *value* is what must fail"
    r = E.evaluate(e, facts(), grants=E.DEFAULT_GRANTS)
    assert not r.ok, f"{name} produced {len(r.effects and r.effects[0].get('text', ''))}"
    assert r.breach in ("value", "total"), r
    assert r.effects == []


def test_x5_a_width_beyond_the_spec_is_refused_at_load():
    """X5 — `format` caps width at five digits: a wider spec is the error value, and an
    error reaching `say` fails the extension (§4.6)."""
    assert E.is_error(E._format("1000000000d", 1))
    r = E.evaluate(say({"format": ["1000000000d", 1]}), facts(), grants=E.DEFAULT_GRANTS)
    assert not r.ok and r.breach == "error", r
    assert r.effects == []


def test_x5_the_total_allocation_counter_stops_death_by_a_thousand_strings():
    """X5 — no single value breaches but the running total does.

    Mutation checked: the `total > max_total_bytes` raise.
    """
    e = ext([{"let": {f"b{i}": {"repeat": ["A", 16]} for i in range(24)},
              "do": [{"say": {"concat": [{"var": f"b{i}"} for i in range(24)]}}]}])
    # Every value is under the value cap, so only the running total can stop it.
    r = E.evaluate(e, facts(), grants=E.DEFAULT_GRANTS,
                   limits=E.Limits(max_value_bytes=4096, max_total_bytes=200))
    assert not r.ok and r.breach == "total", r
    assert r.effects == []


# --------------------------------------------------------------------------- #
# X6 — deep recursion cannot reach the Python stack
# --------------------------------------------------------------------------- #

def _nest(depth):
    node = 1
    for _ in range(depth):
        node = {"+": [node, 1]}
    return node


def test_x6_deep_recursion_cannot_reach_the_python_stack():
    """X6 — past `MAX_DEPTH` is a load refusal; no `RecursionError` escapes.

    Mutation checked: `depth > MAX_DEPTH` in `_Validator.expr`.
    """
    ok = say(_nest(28))
    assert E.validate(ok) == []
    r = E.evaluate(ok, facts(), grants=E.DEFAULT_GRANTS)
    assert r.ok and r.effects[0]["text"] == "29"

    for depth in (E.MAX_DEPTH + 4, 200, 10_000):
        deep = say(_nest(depth))
        try:
            reasons = E.validate(deep)
        except RecursionError:                                     # pragma: no cover
            pytest.fail(f"depth {depth} reached the Python stack")
        assert reasons, f"depth {depth} was not refused"
        assert any("deeper than" in x or "nodes" in x for x in reasons), reasons
        try:
            r = E.evaluate(deep, facts(), grants=E.DEFAULT_GRANTS)
        except RecursionError:                                     # pragma: no cover
            pytest.fail(f"depth {depth} reached the Python stack in evaluate()")
        assert not r.ok and r.effects == []


def test_x6_the_evaluator_is_depth_counted_even_without_validation():
    """X6 — belt and braces: without `validate()` the evaluator is still depth-counted."""
    m = E._Machine(facts(), E.Limits(max_steps=10 ** 7), 0, {}, 0, None)
    with pytest.raises(E._Breach) as caught:
        m.eval(_nest(500))
    assert caught.value.kind == "invalid"


# --------------------------------------------------------------------------- #
# X7 — clock and entropy are injected only
# --------------------------------------------------------------------------- #

FORBIDDEN_IMPORTS = {"time", "random", "os", "datetime", "secrets", "subprocess",
                     "socket", "pathlib", "shutil", "importlib", "ctypes", "threading"}
STDLIB_ALLOWED = {"__future__", "math", "re", "unicodedata", "dataclasses"}


def _import_breaches(src: str, siblings) -> list:
    """What in `src` reaches outside the `ext` package: anything but `STDLIB_ALLOWED` and a
    level-1 import of an existing sibling. `from ..` leaves the package, so it is a breach
    even with no forbidden name — a neighbour could hand the evaluator a clock or a store."""
    bad = []
    for node in pyast.walk(pyast.parse(src)):
        if isinstance(node, pyast.Import):
            bad += [a.name for a in node.names if a.name.split(".")[0] not in STDLIB_ALLOWED]
        elif isinstance(node, pyast.ImportFrom):
            if node.level == 0:
                if (node.module or "").split(".")[0] not in STDLIB_ALLOWED:
                    bad.append(node.module)
            elif node.level != 1:
                bad.append("." * node.level + (node.module or ""))
            else:
                names = [node.module.split(".")[0]] if node.module else [a.name for a in node.names]
                bad += ["." + n for n in names if n not in siblings]
        elif (isinstance(node, pyast.Call) and isinstance(node.func, pyast.Name)
              and node.func.id == "__import__"):
            bad.append("__import__()")
    return bad


def test_x7_the_import_audit_itself_refuses_each_way_out():
    """The audit is the whole X7 guarantee, so prove it in both directions on built escapes."""
    sib = {"grammar", "values"}
    assert _import_breaches("import math\nfrom .grammar import OPS\nfrom . import values", sib) == []
    for escape in ("import time", "import os.path", "from datetime import datetime",
                   "from .. import store", "from ..render import render_prompt",
                   "from ...types import Turn", "from .nope import x", "from . import nope",
                   "from moxie_sdk import store", "__import__('os')"):
        assert _import_breaches(escape, sib), escape
    for mod in sorted(FORBIDDEN_IMPORTS):
        assert _import_breaches(f"import {mod}", sib), mod
    assert not (FORBIDDEN_IMPORTS & STDLIB_ALLOWED)


def test_x7_the_evaluator_imports_no_clock_and_no_entropy():
    """X7(i) — no `ext` module imports a clock, entropy, I/O or anything outside the package
    (the mechanism behind §6.1's determinism).

    Mutation checked: `import time` in `ext/machine.py`.
    """
    siblings = {os.path.basename(f)[:-3] for f in EXT_FILES}
    assert {"__init__", "grammar", "machine"} <= siblings, siblings
    for path in EXT_FILES:
        src = open(path, encoding="utf-8").read()
        assert not _import_breaches(src, siblings), \
            f"ext/{os.path.basename(path)} imports {_import_breaches(src, siblings)}"


def test_x7_two_clock_reads_in_one_program_agree():
    """X7(ii) — `clock.ms` is injected once per turn: no timing side-channel, replayable."""
    e = ext([{"do": [{"say": {"concat": [{"str": [{"clock.ms": []}]}, "|",
                                         {"str": [{"clock.ms": []}]}]}}]}],
            caps=("say", "clock"))
    r = E.evaluate(e, facts(), grants=E.DEFAULT_GRANTS | {"clock"}, now_ms=1_700_000_000)
    assert r.ok
    a, b = r.effects[0]["text"].split("|")
    assert a == b == "1700000000"


def test_x7_the_same_seed_gives_the_same_stream():
    """X7(ii) — `random.*` is a seeded PRNG: replayable (§5.1), and seeds differ per turn."""
    e = ext([{"do": [{"say": {"join": [{"list": [{"random.int": [1, 1000]},
                                                 {"random.int": [1, 1000]},
                                                 {"random.pick": [{"lit": ["a", "b", "c",
                                                                           "d"]}]}]},
                                       "-"]}}]}],
            caps=("say", "random"))
    grants = E.DEFAULT_GRANTS | {"random"}
    first = E.evaluate(e, facts(), grants=grants, seed=42).effects[0]["text"]
    again = E.evaluate(e, facts(), grants=grants, seed=42).effects[0]["text"]
    other = E.evaluate(e, facts(), grants=grants, seed=43).effects[0]["text"]
    assert first == again, "the same seed must replay byte for byte"
    assert first != other, "a different seed must actually differ"


def test_x7_a_fact_op_without_its_capability_is_refused_at_load():
    """X7(iii) — a fact op without its declared and granted capability fails at load (§4.2)."""
    undeclared = ext([{"do": [{"say": {"str": [{"clock.ms": []}]}}]}], caps=("say",))
    reasons = E.validate(undeclared)
    assert reasons and "clock" in reasons[0]
    declared = ext([{"do": [{"say": {"str": [{"clock.ms": []}]}}]}], caps=("say", "clock"))
    assert E.validate(declared) == []
    assert E.validate(declared, grants=E.DEFAULT_GRANTS) != []
    assert E.validate(declared, grants=E.DEFAULT_GRANTS | {"clock"}) == []


# --------------------------------------------------------------------------- #
# X8 — Unicode tricks cannot change a capability or an op
# --------------------------------------------------------------------------- #

UNICODE_TRICKS = {
    "dotless_i": "memory.wr\u0131te",
    "zero_width_space": "memory\u200b.write",
    "fullwidth_m": "\uff4demory.write",
    "rtl_override": "\u202ememory.write\u202c",
    "uppercase": "MEMORY.WRITE",
    "mixed_case": "Memory.Write",
    "nbsp": "memory.write\u00a0",
    "combining": "memory.wri\u0307te",
    "cyrillic_e": "m\u0435mory.write",           # U+0435 CYRILLIC SMALL LETTER IE
    "math_bold": "\U0001d426emory.write",        # NFKC-folds to "memory.write"
}


@pytest.mark.parametrize("name", sorted(UNICODE_TRICKS))
def test_x8_unicode_tricks_cannot_change_a_capability(name):
    """X8 — normalize-and-COMPARE, not normalize-and-use: a homoglyph capability is refused,
    never folded into a grant the review did not show.

    Mutation checked: `normal_name` returning the NFKC fold.
    """
    trick = UNICODE_TRICKS[name]
    assert E.normal_name(trick) == "", f"{name} normalized to a usable name"
    e = ext([{"do": [{"remember": {"key": "x", "value": 1}}, {"say": "hi"}]}],
            caps=("say", trick))
    reasons = E.validate(e)
    assert reasons, f"{name} was accepted as a capability"
    assert E.capabilities_of(e) == ["say"], E.capabilities_of(e)
    assert not set(E.grant_list(e)) - {E.CAPABILITY_WORDS["say"]}, E.grant_list(e)


UNICODE_OP_TRICKS = ["ｃoncat", "CONCAT", "conc\u0430t", "＋", "\uff1d\uff1d", "sta\u0155t"]


@pytest.mark.parametrize("trick", UNICODE_OP_TRICKS)
def test_x8_unicode_tricks_cannot_change_an_op(trick):
    """X8 for operator names (`normal_op`), symbolic ones included: `＋` is refused, not `+`."""
    assert E.normal_op(trick) == "" or E.normal_op(trick) not in E.OPS
    r = E.evaluate(say({trick: [1, 2]}), facts(), grants=E.DEFAULT_GRANTS)
    assert not r.ok and r.breach == "invalid"


# --------------------------------------------------------------------------- #
# X9 — no other namespace, no other child
# --------------------------------------------------------------------------- #

def test_x9_an_extension_cannot_read_another_modules_namespace():
    """X9(i) — the fact base holds only this extension's host-supplied namespace."""
    v = Volley("hi", persist_data={"ext:mine": {"score": 7},
                                   "other_module": {"secret": "not yours"},
                                   "memory_chat": {"summaries": ["a private thing"]}})
    built = CA.ext_facts(v, Session(), namespace="ext:mine",
                         grants=E.DEFAULT_GRANTS | {"memory.read"})
    assert built["memory"] == {"score": 7}, built["memory"]
    e = say({"concat": ["<", {"str": [{"var": "memory.other_module.secret"}]},
                        {"str": [{"var": "memory.memory_chat.summaries"}]}, ">"]},
            caps=("say", "memory.read"))
    r = E.evaluate(e, built, grants=E.DEFAULT_GRANTS | {"memory.read"})
    assert r.ok and r.effects[0]["text"] == "<>"


BAD_KEYS = ["../other/x", "/etc/passwd", "a/../../b", "..", "a..b", "", " ",
            "_meta", "timers._provenance", "a\x00b", "x/y", "\\windows\\system32",
            "a b", "ext:other.x", "https://example.invalid"]


@pytest.mark.parametrize("key", BAD_KEYS)
def test_x9_a_traversal_key_is_refused_at_load(key):
    """X9(ii) — memory keys are dot-segmented `[A-Za-z0-9][A-Za-z0-9_-]*` (no `/`, no `_`
    segment). `"other_ns.x"` is allowed: it lands under the host's namespace (next test).

    Mutation checked: widening `_KEY`.
    """
    e = ext([{"do": [{"remember": {"key": key, "value": 1}}, {"say": "hi"}]}],
            caps=("say", "memory.write"))
    reasons = E.validate(e)
    assert reasons, f"{key!r} was accepted as a memory key"


def test_x9_the_store_call_names_a_host_supplied_namespace(tmp_path):
    """X9(iii) — the extension picks a key, never a namespace or device; a second robot's
    file is byte-unchanged.

    Mutation checked: taking the namespace from the effect in `apply_ext_effects`.
    """
    store = MemoryStore(JsonStore(str(tmp_path)))
    store.merge("robot-b", "other_ns", {"score": 1})
    before = (tmp_path / "robots" / "robot-b" / "memory.json").read_bytes()

    calls = []
    real_merge = store.merge

    def spy(device_id, namespace, values, **kw):
        calls.append((device_id, namespace, values))
        return real_merge(device_id, namespace, values, **kw)

    store.merge = spy
    # A hostile `namespace`/`device_id` on the effect (no grammar produces one) is ignored:
    # the namespace is the host's to choose (§4.4 rule 3).
    effects = [{"kind": "remember", "key": "other_ns.x", "value": 99,
                "namespace": "memory_chat", "device_id": "robot-b"}]
    CA.apply_ext_effects(effects, volley=Volley("hi"), memory=store,
                         device_id="robot-a", namespace="ext:mine")
    assert calls and calls[0][0] == "robot-a" and calls[0][1] == "ext:mine", calls
    a = store.load("robot-a")
    assert list(a) == ["ext:mine"], a
    assert a["ext:mine"]["other_ns"] == {"x": 99}
    assert "score" not in a["ext:mine"]
    assert (tmp_path / "robots" / "robot-b" / "memory.json").read_bytes() == before


# --------------------------------------------------------------------------- #
# X10 — a capability mismatch is a load refusal in BOTH directions
# --------------------------------------------------------------------------- #

def test_x10_a_capability_mismatch_is_a_load_refusal_in_both_directions():
    """X10 — declared == used, or it does not install (acceptance criterion 4).

    Mutation checked: the `missing` and `spare` branches of `validate`.
    """
    uses_undeclared = ext([{"do": [{"remember": {"key": "x", "value": 1}},
                                   {"say": "ok"}]}], caps=("say",))
    reasons = E.validate(uses_undeclared)
    assert reasons and "did not declare" in reasons[0] and "memory.write" in reasons[0]

    declares_unused = ext([{"do": [{"say": "ok"}]}], caps=("say", "memory.write"))
    reasons = E.validate(declares_unused)
    assert reasons and "never uses" in reasons[0] and "memory.write" in reasons[0]

    exact = ext([{"do": [{"remember": {"key": "x", "value": 1}}, {"say": "ok"}]}],
                caps=("say", "memory.write"))
    assert E.validate(exact) == []


@pytest.mark.parametrize("cap,ast", [
    ("clock", {"clock.ms": []}),
    ("random", {"random.int": [1, 2]}),
    ("presence", {"str": [{"var": "presence.face_present"}]}),
    ("session", {"str": [{"var": "session.total_volleys"}]}),
    ("memory.read", {"str": [{"var": "memory.x"}]}),
    ("child.nickname", {"str": [{"var": "child.nickname"}]}),
    ("child.profile", {"str": [{"var": "child.birthday"}]}),
])
def test_x10_every_gated_read_costs_its_capability(cap, ast):
    """X10 — each gated read, op or path, costs its capability (`child.birthday` costs
    `child.profile`, the highest-value PII, §5.1)."""
    without = say(ast, caps=("say",))
    assert E.validate(without), f"{cap} was free"
    with_it = say(ast, caps=("say", cap))
    assert E.validate(with_it) == [], E.validate(with_it)


def test_x10_the_default_granted_set_is_exactly_four():
    """Acceptance criterion 5 — widening the default grants is a reviewer's decision."""
    assert set(E.DEFAULT_GRANTS) == {"say", "handled", "session", "child.nickname"}


def test_x10_p1_capabilities_are_declared_rendered_and_refused():
    """P1 boundary: `brain` parses but is refused at load; `evaluate()` has no `allow_p1` door."""
    e = ext([{"do": [{"brain": {"prompt": "hi"}}, {"say": "ok"}]}],
            caps=("say", "brain"))
    assert E.validate(e, allow_p1=True) == [], "the grammar must accept it today"
    reasons = E.validate(e)
    assert reasons and "cannot grant yet" in reasons[0]
    r = E.evaluate(e, facts(), grants=E.DEFAULT_GRANTS | {"brain"})
    assert not r.ok and r.effects == [], "evaluate() has no allow_p1 door"
    assert "brain" in E.P1_CAPABILITIES
    assert "subscribe" not in E.P1_CAPABILITIES, \
        "subscribe has a host; a capability with a host is not P1"


def test_x10_an_act_is_bounded_declared_and_granted_or_it_does_not_load():
    """An `act` loads only if (1) its name is in the closed `ACTION_WORDS` table, (2) the
    pack declared it, and (3) the host granted it."""
    good = ext([{"do": [{"act": {"name": "eb_timer_request", "args": ["1", "0"]}},
                        {"say": "ok"}]}], caps=("say", "act.eb_timer_request"))

    # 1 — a name outside the closed table is not a program, at load.
    for bogus in ("eb_shell", "eb_timer_request2", "os.system", "EB_WAKE", ""):
        bad = ext([{"do": [{"act": {"name": bogus, "args": []}}, {"say": "ok"}]}],
                  caps=("say", f"act.{bogus}"))
        assert E.validate(bad, allow_p1=True), bogus
        assert E.validate(bad), bogus
    assert set(E.ACTION_WORDS) == {"eb_timer_request", "eb_enable_qr", "eb_wake"}, (
        "widening the robot-function allowlist is a reviewer's decision, not a diff's")

    # 2 — used but not declared: a load refusal, not a runtime one.
    undeclared = ext([{"do": [{"act": {"name": "eb_wake", "args": []}},
                              {"say": "ok"}]}], caps=("say",))
    reasons = E.validate(undeclared, allow_p1=True)
    assert reasons and "did not declare" in reasons[0], reasons
    r = E.evaluate(undeclared, facts(), grants=E.DEFAULT_GRANTS | {"act.eb_wake"})
    assert not r.ok and r.effects == [], "an undeclared act must never reach an effect"

    # 3 — declared and known, but not granted: nothing runs. The shipped set is pinned.
    shipped_acts = {c for c in set(E.DEFAULT_GRANTS) | set(CA.SHIPPED_EXTRA_GRANTS)
                    if c.startswith("act.")}
    assert shipped_acts == {"act.eb_timer_request"}, (
        f"exactly one act is granted to shipped extensions, got {sorted(shipped_acts)} — "
        "widening this is a reviewer's decision, not a diff's")

    waker = ext([{"do": [{"act": {"name": "eb_wake", "args": []}}, {"say": "ok"}]}],
                caps=("say", "act.eb_wake"))
    ungranted = E.validate(waker, grants=E.DEFAULT_GRANTS | CA.SHIPPED_EXTRA_GRANTS)
    assert ungranted and "has not been granted" in ungranted[0], ungranted
    r = E.evaluate(waker, facts(), grants=E.DEFAULT_GRANTS)
    assert not r.ok and r.effects == []

    # …and with all three satisfied it runs, and says so in words a parent reads.
    assert E.validate(good, grants=E.DEFAULT_GRANTS | {"act.eb_timer_request"}) == []
    r = E.evaluate(good, facts(), grants=E.DEFAULT_GRANTS | {"act.eb_timer_request"})
    assert r.ok and r.effects[0] == {"kind": "act", "name": "eb_timer_request",
                                     "args": ["1", "0"]}
    assert "Can ask Moxie to set or cancel a timer" in E.grant_list(good)


def test_x10_the_host_will_not_name_a_function_the_table_does_not():
    """Second gate at the host boundary: an effect list and a Python global's
    `add_execution_action` both drop an unknown function name."""
    v = Volley("hi")
    CA.apply_ext_effects([{"kind": "act", "name": "eb_shell", "args": ["rm"]},
                          {"kind": "act", "name": "__import__", "args": []},
                          {"kind": "act", "name": "eb_wake", "args": []}], volley=v)
    assert [a["name"] for a in v.execution_actions] == ["eb_wake"]

    v2 = Volley("hi")
    v2.add_execution_action("eb_shell", ["rm", "-rf"])
    v2.add_execution_action("eb_enable_qr", ["true"])
    out = CA.execution_actions_of(v2)
    assert [a.function for a in out] == ["eb_enable_qr"]
    assert CA.robot_functions() == frozenset(E.ACTION_WORDS)


def test_x10_a_subscribe_is_bounded_declared_and_granted_or_it_does_not_load():
    """A `subscribe` loads only if (1) the event is in the closed `SUBSCRIBE_EVENTS` catalog
    (an input is as much a surface as an output), (2) declared, (3) granted."""
    good = ext([{"do": [{"subscribe": ["eb-qr-event"]}, {"say": "ok"}]}],
               caps=("say", "subscribe"))

    # 1 — an event outside the closed catalog (incl. homoglyph/whitespace shapes) is refused.
    for bogus in ("eb-shell", "eb_qr_event", "eb-qr-event ", "EB-QR-EVENT", "",
                  "eb\u2011qr\u2011event", "*"):
        bad = ext([{"do": [{"subscribe": [bogus]}, {"say": "ok"}]}],
                  caps=("say", "subscribe"))
        assert E.validate(bad, allow_p1=True), bogus
        assert E.validate(bad), bogus
    assert set(E.SUBSCRIBE_EVENTS) == {"eb-found-face", "eb-lost-target", "eb-lost-face",
                                       "eb-qr-event", "eb-dr-event", "eb-br-event"}, (
        "widening the event vocabulary is a reviewer's decision, not a diff's")

    # 2 — used but not declared: a load refusal, not a runtime one.
    undeclared = ext([{"do": [{"subscribe": ["eb-found-face"]}, {"say": "ok"}]}],
                     caps=("say",))
    reasons = E.validate(undeclared, allow_p1=True)
    assert reasons and "did not declare" in reasons[0], reasons
    r = E.evaluate(undeclared, facts(), grants=E.DEFAULT_GRANTS | {"subscribe"})
    assert not r.ok and r.effects == [], "an undeclared subscribe must never reach an effect"

    # 3 — declared and known, but not granted: still nothing runs.
    ungranted = E.validate(good, grants=E.DEFAULT_GRANTS | CA.SHIPPED_EXTRA_GRANTS)
    assert ungranted and "has not been granted" in ungranted[0], ungranted
    assert "subscribe" not in set(E.DEFAULT_GRANTS) | set(CA.SHIPPED_EXTRA_GRANTS)
    r = E.evaluate(good, facts(), grants=E.DEFAULT_GRANTS)
    assert not r.ok and r.effects == []

    # …and with all three satisfied it runs, and says so in words a parent reads.
    assert E.validate(good, grants=E.DEFAULT_GRANTS | {"subscribe"}) == []
    r = E.evaluate(good, facts(), grants=E.DEFAULT_GRANTS | {"subscribe"})
    assert r.ok and r.effects[0] == {"kind": "subscribe", "events": ["eb-qr-event"]}
    assert "Can listen for things the robot notices" in E.grant_list(good)


def test_x10_the_host_will_not_name_an_event_the_table_does_not():
    """Second gate at the host boundary (`subscriptions_of`): an effect list and a Python
    global's `update_subscriptions` are both filtered."""
    v = Volley("hi")
    CA.apply_ext_effects([{"kind": "subscribe", "events": ["eb-shell", "eb-found-face"]},
                          {"kind": "subscribe", "events": ["../eb-qr-event"]}], volley=v)
    assert CA.subscriptions_of(v) == ["eb-found-face"]

    v2 = Volley("hi")
    v2.update_subscriptions(["eb-timer-event", "eb-qr-event"])
    assert CA.subscriptions_of(v2) == ["eb-qr-event"]
    assert CA.robot_events() == frozenset(E.SUBSCRIBE_EVENTS)


# --------------------------------------------------------------------------- #
# X11 — effects are all or nothing
# --------------------------------------------------------------------------- #

def test_x11_effects_are_all_or_nothing():
    """X11 — a mid-program breach leaves no write, output or note: effects are discarded
    whole (§4.5).

    Mutation checked: returning the effect prefix on `_Breach`.
    """
    e = ext([{"do": [
        {"remember": {"key": "score", "value": 10}},
        {"note": "about to break"},
        {"say": {"repeat": ["A", 16]}},          # 16 chars, fine…
        {"say": {"concat": [{"repeat": ["B", 16]}] * 32}},   # …this one breaches
        {"remember": {"key": "after", "value": 1}},
    ]}], caps=("say", "memory.write"))
    r = E.evaluate(e, facts(), grants=E.DEFAULT_GRANTS | {"memory.write"},
                   limits=E.Limits(max_value_bytes=64, max_total_bytes=4096))
    assert not r.ok, r
    assert r.effects == [], r.effects
    assert r.notes == [], r.notes


def test_x11_an_error_value_reaching_an_effect_fails_the_extension():
    """X11 — an error value reaching `say` fails the extension, never becomes speech (§4.6)."""
    e = say({"concat": ["I counted ", {"str": [{"int": ["banana"]}]}, " sheep"]})
    r = E.evaluate(e, facts(), grants=E.DEFAULT_GRANTS)
    assert not r.ok and r.breach == "error", r
    assert r.effects == []
    assert "error" not in r.sentence.lower() or "error" not in r.reason.lower()

    # An author can handle it with `has`/`if`.
    handled = say({"if": [{"has": [{"int": [{"var": "entities.0"}]}]},
                          "I can count that", "Say a number for me!"]})
    r = E.evaluate(handled, facts(entities=["banana"]), grants=E.DEFAULT_GRANTS)
    assert r.ok and r.effects[0]["text"] == "Say a number for me!"


@pytest.mark.parametrize("expr,expected", [
    ({"/": [1, 0]}, "error"),
    ({"%": [1, 0]}, "error"),
    ({"int": ["banana"]}, "error"),
    ({"sort": [{"lit": [1, "a", True]}]}, "error"),
    ({"get": [{"lit": {"a": 1}}, "missing"]}, None),
    ({"get": [{"lit": [1, 2]}, 99]}, None),
    ({"var": "memory.nothing.at.all"}, None),
    ({"len": [None]}, 0),
    ({"<": ["a", 1]}, False),
    ({"==": [True, 1]}, False),
    ({"format": ["2d", 12345]}, "12345"),        # a width too small never truncates
])
def test_x11_every_bad_input_returns_a_value_rather_than_raising(expr, expected):
    """§4.6 sweep — the evaluator always returns: ÷0 is an error value, missing key/index
    is null, cross-type comparison is false."""
    m = E._Machine(facts(), E.Limits(), 0, {}, 0, None)
    got = m.eval(expr)
    if expected == "error":
        assert E.is_error(got), got
    else:
        assert got == expected and type(got) is type(expected), got


# --------------------------------------------------------------------------- #
# X12 — a pathological regex is still capped by the item
# --------------------------------------------------------------------------- #

def test_x12_a_pathological_regex_is_still_capped_by_the_item():
    """X12 — extensions cannot build regexes; the item's own `pattern` is capped at
    `MAX_PATTERN_CHARS` (accepted risk P7: stdlib regex has no timeout)."""
    over = {"kind": "global", "key": "x",
            "data": {"name": "x", "pattern": "(a+)+$" * P.MAX_PATTERN_CHARS}}
    reasons = P.validate_item(over)
    assert reasons and "pattern is" in reasons[0], reasons
    fine = {"kind": "global", "key": "x",
            "data": {"name": "x", "pattern": "(set|start) a timer",
                     "extension": say({"str": [1]})}}
    assert P.validate_item(fine) == [], P.validate_item(fine)


# --------------------------------------------------------------------------- #
# The invariant the whole file exists to state (§5.2, acceptance criterion 6)
# --------------------------------------------------------------------------- #

NEVER_REACHABLE = ("network", "filesystem", "subprocess", "environment variable",
                   "credential", "another device's store", "another module's namespace",
                   "the safety rule table", "LoggingPolicy")


def test_nothing_an_extension_can_express_reaches_any_of_these():
    """Acceptance criterion 6 — forbidden surfaces are absent from the grammar altogether."""
    # A program naming any of them is a refusal, not a runtime block.
    for bad in ("network.get", "fs.read", "process.env", "secrets.api_key",
                "store.other", "safety.rules", "policy.set", "gateway.key"):
        r = E.evaluate(say({"str": [{"var": bad}]}), facts(), grants=E.DEFAULT_GRANTS)
        assert not r.ok and r.breach == "invalid", bad


def test_the_conformance_file_is_real_and_covers_all_six_hooks():
    """§8's migration table is a deliverable: it parses and carries all six rows."""
    path = os.path.join(os.path.dirname(__file__), "data", "ext_conformance.json")
    doc = json.load(open(path, encoding="utf-8"))
    rows = {r["name"]: r for r in doc["rows"]}
    assert sorted(rows) == ["G1", "G2", "G3", "G4", "G5", "G6"]
    for name, row in rows.items():
        assert E.validate(row["ast"], allow_p1=True) == [], (name, row["ast"])
        assert row["expected_effects"], name
        assert row["explain"], name
