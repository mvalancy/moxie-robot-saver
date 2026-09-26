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
import re
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

from moxie_sdk.content import ext as E                      # noqa: E402
from moxie_sdk.content import render as R                   # noqa: E402
from moxie_sdk.content.volley import Volley, Session        # noqa: E402

CONTENT = os.path.join(REPO, "mqtt", "moxie_sdk", "content")
EXT_DIR = os.path.join(CONTENT, "ext")
#: Every module of the evaluator package. X7 bounds the package, not one file, so a new
#: sibling is inside the boundary the moment it exists.
EXT_FILES = sorted(os.path.join(EXT_DIR, f) for f in os.listdir(EXT_DIR) if f.endswith(".py"))


def ext_source() -> str:
    return "\n".join(open(f, encoding="utf-8").read() for f in EXT_FILES)


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

#: The operator table, frozen as a literal. **This is the audit surface** (§4.2), and
#: freezing it here is risk R1's brake: "just one more op" cannot land without a test edit
#: and a reviewer. 53 entries.
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

#: The fact roots. Together with `FROZEN_OPS` this is §5.2's invariant made checkable:
#: *the set of strings that resolve to anything at all is the op table plus the fact base,
#: and both are finite and enumerated in our own source.*
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
    """X1 — every classic escape is a load-time refusal: an unknown op, a non-fact root, or
    a `_`-leading segment is not a valid program, so it is never evaluated.

    Mutation checked: deleting the `seg.startswith("_")` refusal in `_Validator._var` fails
    the private/dunder rows; deleting `root not in FACT_ROOTS` fails `env_root`, `os_root`,
    `config_root` and `builtins`.
    """
    expr, needed = ESCAPE_ASTS[name]
    e = say(expr, caps=("say",) + needed)
    reasons = E.validate(e)
    assert reasons, f"{name} was not refused at load"
    assert not any("declare" in x or "never uses" in x for x in reasons), \
        f"{name} was refused for the wrong reason: {reasons}"
    # And it must never be *evaluated*: `evaluate` re-validates, so a caller that skipped
    # `validate` is protected too.
    grants = E.DEFAULT_GRANTS | set(needed)
    r = E.evaluate(e, facts(), grants=grants)
    assert not r.ok and r.breach == "invalid"
    assert r.effects == []


def test_x1_the_op_table_is_frozen():
    """X1 — the op, statement and fact-root key sets EQUAL frozen literals, so adding (or
    removing) an op is a test edit a reviewer sees — the brake on risk R1.

    Mutation checked: adding `"eval": (1, 1, None)` to `ext.OPS` fails this test.
    """
    assert set(E.OPS) == FROZEN_OPS
    assert set(E.STATEMENTS) == FROZEN_STATEMENTS
    assert set(E.FACT_ROOTS) == FROZEN_FACT_ROOTS
    # §5.2's invariant, stated as an assertion: the complete set of resolvable strings.
    resolvable = set(E.OPS) | set(E.FACT_ROOTS) | {"lit", "var"}
    for forbidden in ("import", "getattr", "eval", "exec", "open", "fetch", "require",
                      "subprocess", "environ", "os", "sys", "globals", "config",
                      "volley", "store", "loop", "for", "while", "def",
                      "call", "func", "lambda", "regex", "sleep"):
        assert forbidden not in resolvable, f"{forbidden!r} resolves to something"


def test_x1_no_grammar_construct_defines_or_calls_anything():
    """X1 — no statement or op takes a rule, index, function or name; `let` binds values,
    never references, so an extension's maximum cost is statically computable (§4.3)."""
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
    """X2 — the fact dict `ContentApp` builds for a real turn is plain JSON all the way
    down, so attribute-walking has nothing to walk to (§4.4).

    Mutation checked: putting the live `Volley` into `content_app.ext_facts` fails this.
    """
    from moxie_sdk.content import content_app as CA
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
    """X2's corollary — even a leaked object is inert: no op does attribute access and
    every op coerces, so one host bug is not an escape."""
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

#: X3 fences the template renderer (`render_prompt`: jinja2 SandboxedEnvironment, plus the
#: `_`-refusing fallback). `test_render_sandbox.py` / `test_content_pack_sandbox.py` own
#: that work; it is repeated here so an audit of THIS sandbox sees the appliance's other
#: execution surface fenced in the same file.
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
    """X3 — a pack-importable `prompt` is not a code-execution channel, with jinja2
    installed or absent (both shapes run; neither skips, so a skip cannot read as coverage).

    Under a plain `jinja2.Environment` these walks reach `__builtins__` and `os.getcwd()`.

    Mutation checked: a plain `jinja2.Environment` in `render._sandbox()` fails every probe
    on the jinja2 shape; deleting `part.startswith("_")` in `render._resolve` fails
    `session_repr_globals_environ` on the fallback shape.
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
    """X3's other direction — a sandbox that broke `Hi {{ … }}` would just get reverted."""
    v = Volley("hi", config={"child_pii": {"nickname": "Sam"}})
    ctx = {"volley": v, "session": Session()}
    tpl = "Hi {{ volley.config.child_pii.nickname }}!"
    assert R.render_prompt(tpl, ctx) == "Hi Sam!"
    monkeypatch.setitem(sys.modules, "jinja2", None)
    monkeypatch.setitem(sys.modules, "jinja2.sandbox", None)
    assert R.render_prompt(tpl, ctx) == "Hi Sam!"


def test_x3_an_extension_is_the_only_other_execution_surface():
    """X3 — with the renderer sandboxed, the §5 capability model is the only execution
    surface; `code` round-trips as opaque data and is never exec/eval/compiled (§7.4)."""
    names = ["content_app.py", "module.py", "packs.py", "render.py"]
    for name in names + [os.path.relpath(f, CONTENT) for f in EXT_FILES]:
        tree = pyast.parse(open(os.path.join(CONTENT, name)).read())
        for node in pyast.walk(tree):
            if isinstance(node, pyast.Call) and isinstance(node.func, pyast.Name):
                assert node.func.id not in ("exec", "eval", "compile", "__import__"), \
                    f"{name} calls {node.func.id}()"


# --------------------------------------------------------------------------- #
# X4 — an infinite loop is unrepresentable, and the budget still holds
# --------------------------------------------------------------------------- #

def test_x4_the_grammar_has_no_loop_or_recursion_construct():
    """X4(i) — there is no loop to write: the frozen op/statement sets have no iteration,
    jump, user function or rule reference."""
    for word in ("while", "for", "loop", "each", "map", "filter", "reduce", "recurse",
                 "goto", "call", "def", "fn", "lambda", "apply", "yield"):
        assert word not in E.OPS, f"{word!r} is an operator"
        assert word not in E.STATEMENTS, f"{word!r} is a statement"
    # `repeat` is the one thing that looks like iteration, and it is a *bounded string*
    # builder, not a control construct: it takes text and a count, never a program.
    lo, hi, cap = E.OPS["repeat"]
    assert (lo, hi, cap) == (2, 2, None)
    r = E.evaluate(say({"repeat": ["ab", 1000]}), facts(), grants=E.DEFAULT_GRANTS)
    assert r.ok and r.effects[0]["text"] == "ab" * E.MAX_REPEAT


def test_x4_a_costly_ast_hits_the_step_budget_and_returns():
    """X4(ii) — a costly `if` chain hits `MOXIE_EXT_MAX_STEPS`, returns `ok=False,
    breach="steps"` and discards its effects. Timed on the injected clock, not the runner.

    Mutation checked: removing the `steps > max_steps` raise in `_Machine.step` → `ok=True`.
    """
    costly = {"concat": [{"str": [{"+": list(range(16))}]}] * 32}   # ~577 nodes
    e = say(costly)
    assert E.validate(e) == [], "the AST itself must be legal — the budget is the point"
    r = E.evaluate(e, facts(), grants=E.DEFAULT_GRANTS,
                   limits=E.Limits(max_steps=50, budget_s=1e9))
    assert not r.ok and r.breach == "steps", r
    assert r.effects == []
    assert r.steps <= 51
    # …and the same AST inside a budget that fits still works, so the test is about the
    # budget rather than about the program being impossible.
    ok = E.evaluate(e, facts(), grants=E.DEFAULT_GRANTS)
    assert ok.ok and ok.effects[0]["text"] == "120" * 32


def test_x4_the_wall_clock_budget_holds_without_threads_or_signals():
    """X4(ii) — the wall-clock budget is an injected monotonic reading checked every 256
    steps (no thread, no signal), so it behaves the same in the supervisor and a Worker.

    Mutation checked: removing the `monotonic() > deadline` raise → `ok=True`.
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
    """X5 — every allocation path (nested `repeat`, big `concat`, `join`, wide `format`)
    hits the value/total byte cap, returns `ok=False`, leaves no effect, never MemoryErrors.

    Mutation checked: removing the `n > max_value_bytes` raise in `_Machine.charge` makes
    `repeat_nested_to_depth_8` return a 4-billion-character string.
    """
    e = say(HUGE[name])
    assert E.validate(e) == [], "the AST is legal; the *value* is what must fail"
    r = E.evaluate(e, facts(), grants=E.DEFAULT_GRANTS)
    assert not r.ok, f"{name} produced {len(r.effects and r.effects[0].get('text', ''))}"
    assert r.breach in ("value", "total"), r
    assert r.effects == []


def test_x5_a_width_beyond_the_spec_is_refused_at_load():
    """X5 — `format`'s spec grammar caps width at five digits, so a billion-wide field is a
    malformed program refused at import."""
    # The spec is *data*, so the op returns the error value rather than raising…
    assert E.is_error(E._format("1000000000d", 1))
    # …and an error reaching a `say` fails the extension rather than speaking "error"
    # (§4.6), so the child hears the conversation's answer instead of a billion spaces.
    r = E.evaluate(say({"format": ["1000000000d", 1]}), facts(), grants=E.DEFAULT_GRANTS)
    assert not r.ok and r.breach == "error", r
    assert r.effects == []


def test_x5_the_total_allocation_counter_stops_death_by_a_thousand_strings():
    """X5 — no single value breaches but the running total does.

    Mutation checked: removing the `total > max_total_bytes` raise → `ok=True`.
    """
    e = ext([{"let": {f"b{i}": {"repeat": ["A", 16]} for i in range(24)},
              "do": [{"say": {"concat": [{"var": f"b{i}"} for i in range(24)]}}]}])
    # Every individual value here is 16-384 bytes, comfortably under the value cap — so
    # only the running total can stop it, which is what makes this an independent proof
    # rather than the value cap firing again under another name.
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
    """X6 — depth 32 evaluates, 33 and 10 000 are load refusals; no `RecursionError`
    escapes (validator and evaluator are both depth-counted).

    Mutation checked: removing `depth > MAX_DEPTH` from `_Validator.expr` makes the
    10 000-deep case raise `RecursionError` out of `validate()`.
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
    """X6's second half — the belt to the brace. A caller that skipped `validate()`
    entirely still cannot drive the evaluator into the interpreter's stack."""
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
    """What in `src` reaches outside the evaluator package's boundary.

    The only legal imports are the four pure stdlib modules and `from .<sibling>`: a
    level-1 relative import naming a module of THIS package. `from .. import store` or
    `from ..render import …` is level 2 — it leaves the package — and is a breach even
    though no forbidden name appears in it, because a sibling of `ext/` could hand the
    evaluator a clock, a store or a network client.
    """
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
    """The audit below is the whole X7 guarantee, so it is proven in both directions here
    against sources built to escape — not only against a tree that happens to be clean."""
    sib = {"grammar", "values"}
    assert _import_breaches("import math\nfrom .grammar import OPS\nfrom . import values", sib) == []
    for escape in ("import time", "import os.path", "from datetime import datetime",
                   "from .. import store", "from ..render import render_prompt",
                   "from ...types import Turn", "from .nope import x", "from . import nope",
                   "from moxie_sdk import store", "__import__('os')"):
        assert _import_breaches(escape, sib), escape


def test_x7_the_evaluator_imports_no_clock_and_no_entropy():
    """X7(i) — no module of the `ext` package imports a clock, entropy, I/O or anything
    outside the package (the mechanism behind §6.1's determinism claim).

    Mutation checked: adding `import time` to `ext/machine.py` fails this test.
    """
    siblings = {os.path.basename(f)[:-3] for f in EXT_FILES}
    assert {"__init__", "grammar", "machine"} <= siblings, siblings
    for path in EXT_FILES:
        src = open(path, encoding="utf-8").read()
        assert not _import_breaches(src, siblings), \
            f"ext/{os.path.basename(path)} imports {_import_breaches(src, siblings)}"


def test_x7_two_clock_reads_in_one_program_agree():
    """X7(ii) — `clock.ms` is an injected value captured once per turn, so a program cannot
    observe its own execution time. That closes the timing side-channel *and* makes the
    conformance goldens replayable."""
    e = ext([{"do": [{"say": {"concat": [{"str": [{"clock.ms": []}]}, "|",
                                         {"str": [{"clock.ms": []}]}]}}]}],
            caps=("say", "clock"))
    r = E.evaluate(e, facts(), grants=E.DEFAULT_GRANTS | {"clock"}, now_ms=1_700_000_000)
    assert r.ok
    a, b = r.effects[0]["text"].split("|")
    assert a == b == "1700000000"


def test_x7_the_same_seed_gives_the_same_stream():
    """X7(ii) — `random.*` is a PRNG seeded by `sha256(turn_key ‖ extension_id)`: for
    replayable determinism (§5.1), while the changing turn key still gives variety."""
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
    """X7(iii) — `clock.ms` without declaring `clock` fails validation, and `ContentApp`
    proceeds as if there were no extension (§4.2: the turn is never at risk)."""
    undeclared = ext([{"do": [{"say": {"str": [{"clock.ms": []}]}}]}], caps=("say",))
    reasons = E.validate(undeclared)
    assert reasons and "clock" in reasons[0]
    # Declared but not granted is also a load refusal, not a runtime surprise.
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
    """X8 — a homoglyph capability is refused, never granted or rendered as the real one.

    Normalize-and-COMPARE, not normalize-and-use: the name must already be NFKC-normal and
    match `^[a-z0-9_.]+$`, else `"ｍemory.write"` would fold to a grant the review never showed.

    Mutation checked: `normal_name` returning the NFKC fold makes `fullwidth_m` and
    `math_bold` validate and appear as "Can remember things from this activity".
    """
    trick = UNICODE_TRICKS[name]
    assert E.normal_name(trick) == "", f"{name} normalized to a usable name"
    e = ext([{"do": [{"remember": {"key": "x", "value": 1}}, {"say": "hi"}]}],
            caps=("say", trick))
    reasons = E.validate(e)
    assert reasons, f"{name} was accepted as a capability"
    assert E.capabilities_of(e) == ["say"], E.capabilities_of(e)
    words = E.grant_list(e)
    assert "Can remember things from this activity" not in words, words


@pytest.mark.parametrize("name", sorted(UNICODE_TRICKS))
def test_x8_the_grant_sentence_is_generated_from_the_normalized_name(name):
    """X8's second half — the parent-facing text comes from the fixed table keyed by the
    normalized name, never from anything an author wrote. A homoglyph cannot make a scary
    grant read as a harmless one, because the only path to a sentence is a table lookup on
    a name that already passed `normal_name`."""
    e = {"ext_format": 1, "capabilities": [UNICODE_TRICKS[name]], "on": "global",
         "rules": [{"do": [{"say": "hi"}]}]}
    for sentence in E.grant_list(e):
        assert sentence not in E.CAPABILITY_WORDS.values(), sentence


UNICODE_OP_TRICKS = ["ｃoncat", "CONCAT", "conc\u0430t", "＋", "\uff1d\uff1d", "sta\u0155t"]


@pytest.mark.parametrize("trick", UNICODE_OP_TRICKS)
def test_x8_unicode_tricks_cannot_change_an_op(trick):
    """X8 on the other table — the same rule guards operator names, including the eleven
    symbolic ones (`normal_op`). A fullwidth `＋` folds to `+` and is refused for it."""
    assert E.normal_op(trick) == "" or E.normal_op(trick) not in E.OPS
    r = E.evaluate(say({trick: [1, 2]}), facts(), grants=E.DEFAULT_GRANTS)
    assert not r.ok and r.breach == "invalid"


# --------------------------------------------------------------------------- #
# X9 — no other namespace, no other child
# --------------------------------------------------------------------------- #

def test_x9_an_extension_cannot_read_another_modules_namespace():
    """X9(i) — `memory.other_module.x` is null: the fact base holds only this extension's
    host-supplied namespace, and no grammar word names a namespace, device or file."""
    from moxie_sdk.content import content_app as CA
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
    """X9(ii) — memory keys are dot-segmented `[A-Za-z0-9][A-Za-z0-9_-]*`: no empty
    segment, no `/` or `\\`, no `_`-leading segment (`_meta`/`_provenance` belong to MemoryStore).

    Deliberate deviation from the brief: `"other_ns.x"` is NOT refused — it is shaped like
    `"timers.1"` — and is safe because the write lands under the host-supplied namespace
    (next test).

    Mutation checked: widening `_KEY` to `^[^\\x00]+$` makes every row validate.
    """
    e = ext([{"do": [{"remember": {"key": key, "value": 1}}, {"say": "hi"}]}],
            caps=("say", "memory.write"))
    reasons = E.validate(e)
    assert reasons, f"{key!r} was accepted as a memory key"


def test_x9_the_store_call_names_a_host_supplied_namespace(tmp_path):
    """X9(iii) — writes go to `merge(device_id, own_namespace, …)`, both host-supplied; a
    second robot's file is byte-unchanged. The extension picks a key, never a namespace.

    Mutation checked: taking the namespace from the effect in `apply_ext_effects` fails
    the cross-namespace assertion.
    """
    from moxie_sdk.content import content_app as CA
    from moxie_sdk.store import JsonStore, MemoryStore
    store = MemoryStore(JsonStore(str(tmp_path)))
    store.merge("robot-b", "other_ns", {"score": 1})
    before = (tmp_path / "robots" / "robot-b" / "memory.json").read_bytes()

    calls = []
    real_merge = store.merge

    def spy(device_id, namespace, values, **kw):
        calls.append((device_id, namespace, values))
        return real_merge(device_id, namespace, values, **kw)

    store.merge = spy
    # The effect carries a hostile `namespace` of its own. Nothing in the grammar can
    # produce one — but if a future evaluator bug did, the applier must still ignore it,
    # because the namespace is the host's to choose (§4.4 rule 3).
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
    """X10 — declared == used, or it does not install. Over-declaring matters to a parent:
    the grant list shown must be exactly what the program can do (acceptance criterion 4).

    Mutation checked: deleting the `missing` branch lets `uses_undeclared` install;
    deleting the `spare` branch lets `declares_unused` install.
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
    """X10's coverage sweep — each capability-gated read is refused without its grant and
    accepted with it. A path is as much a capability as an op: `{"var": "child.birthday"}`
    costs `child.profile`, which is refused by default, because a birthday and free-text
    notes are the highest-value PII on the appliance (§5.1)."""
    without = say(ast, caps=("say",))
    assert E.validate(without), f"{cap} was free"
    with_it = say(ast, caps=("say", cap))
    assert E.validate(with_it) == [], E.validate(with_it)


def test_x10_the_default_granted_set_is_exactly_four():
    """Acceptance criterion 5 — and nothing else can be granted at P0 without a code
    change: there is deliberately no env var and no console control, because the
    parent-facing grant flow is P1."""
    assert set(E.DEFAULT_GRANTS) == {"say", "handled", "session", "child.nickname"}
    src = open(os.path.join(REPO, "mqtt", "config.py")).read()
    assert "MOXIE_EXT_GRANTS" not in src, "grants must not become an env var at P0"


def test_x10_p1_capabilities_are_declared_rendered_and_refused():
    """P1 boundary: a capability that cannot yet do anything (`brain`) parses as grammar
    but is refused at load, and `evaluate()` has no `allow_p1` door. `act` and `subscribe`
    have left `P1_CAPABILITIES`; their gates are asserted in the two tests below.
    """
    e = ext([{"do": [{"brain": {"prompt": "hi"}}, {"say": "ok"}]}],
            caps=("say", "brain"))
    assert E.validate(e, allow_p1=True) == [], "the grammar must accept it today"
    reasons = E.validate(e)
    assert reasons and "cannot grant yet" in reasons[0]
    r = E.evaluate(e, facts(), grants=E.DEFAULT_GRANTS | {"brain"})
    assert not r.ok and r.effects == [], "evaluate() has no allow_p1 door"
    assert "brain" in E.P1_CAPABILITIES
    assert "subscribe" not in E.P1_CAPABILITIES, \
        "subscribe has a host since 2026-09-05; a capability with a host is not P1"


def test_x10_an_act_is_bounded_declared_and_granted_or_it_does_not_load():
    """An `act` loads only if all three gates hold:

    1. the name is in the closed `ext.ACTION_WORDS` table (the same one the parent-facing
       sentence comes from — a safety property, not tidiness);
    2. the pack declared it (declared == used is a load condition);
    3. the host granted it (`act.<name>` is not in `DEFAULT_GRANTS`; only the set pinned in
       `SHIPPED_EXTRA_GRANTS` is) — ungranted means the program never runs.
    """
    from moxie_sdk.content import content_app as CA
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

    # 3 — declared and known, but not granted: still nothing runs. The shipped grant SET
    # is pinned (only `act.eb_timer_request`, for the `Timer` global) so widening it
    # reddens here; `eb_wake` is in the catalog and not shipped, so it exercises the gate.
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
    """Second gate on the action table at the host boundary (`execution_actions_of` is the
    last step before a `function_id` reaches a robot). Both an effect list passed straight
    to `apply_ext_effects` and a Python global calling `add_execution_action` drop the
    unknown name and keep the known one.
    """
    from moxie_sdk.content import content_app as CA
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
    """A `subscribe` loads only if all three gates hold:

    1. the event is in the closed `ext.SUBSCRIBE_EVENTS` vision catalog — an input a
       stranger's pack can arrange to receive is as much a surface as an output;
    2. the pack declared it;
    3. the host granted it (`subscribe` is in neither `DEFAULT_GRANTS` nor
       `SHIPPED_EXTRA_GRANTS`).
    """
    from moxie_sdk.content import content_app as CA
    good = ext([{"do": [{"subscribe": ["eb-qr-event"]}, {"say": "ok"}]}],
               caps=("say", "subscribe"))

    # 1 — an event outside the closed catalog is not a program, at load. The last two are
    #     the homoglyph and whitespace shapes X8 worries about for capability names.
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
    """Second gate on the event table at the host boundary (`subscriptions_of`), twin of
    the action test: an effect list and a Python global calling `update_subscriptions`
    (which never meets the validator) are both filtered.
    """
    from moxie_sdk.content import content_app as CA
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
    """X11 — a breach in statement three leaves no memory write, output or note: effects
    are applied by the host only after the program returns, and discarded whole (§4.5).

    Mutation checked: returning the successful effect prefix on `_Breach` fails every
    assertion below.
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
    """X11 — an error never becomes speech: `int("banana")` propagates to `say`, and the
    extension fails rather than the child hearing "error" (§4.6)."""
    e = say({"concat": ["I counted ", {"str": [{"int": ["banana"]}]}, " sheep"]})
    r = E.evaluate(e, facts(), grants=E.DEFAULT_GRANTS)
    assert not r.ok and r.breach == "error", r
    assert r.effects == []
    assert "error" not in r.sentence.lower() or "error" not in r.reason.lower()

    # …and an author who *wants* to handle it can, because `has` and `if` can test for it.
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
    """X12 — extensions cannot construct regexes; the only regex is the item's own
    `pattern`, capped by `packs.validate_item` at `MAX_PATTERN_CHARS` (accepted risk P7:
    stdlib regex has no timeout; filed against packs, R6).
    """
    from moxie_sdk.content import packs as P
    for word in ("regex", "match", "search", "pattern", "compile", "re"):
        assert word not in E.OPS, f"{word!r} is an operator"
        assert word not in E.STATEMENTS
    assert P.MAX_PATTERN_CHARS > 0
    over = {"kind": "global", "key": "x",
            "data": {"name": "x", "pattern": "(a+)+$" * P.MAX_PATTERN_CHARS}}
    reasons = P.validate_item(over)
    assert reasons and "pattern is" in reasons[0], reasons
    # An extension riding on that item never gets the chance to make it worse.
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
    """Acceptance criterion 6 — the forbidden surfaces are absent from the op table and
    fact base altogether, so no config flag could re-enable them."""
    surface = set(E.OPS) | set(E.STATEMENTS) | set(E.FACT_ROOTS) | {"lit", "var"}
    # Every name in the surface is in our own source, and the surface is small enough to
    # read in one screen — which is the property that justified choosing this design.
    assert len(surface) <= 80, len(surface)
    src = ext_source()
    for name in surface:
        assert name in src

    # A program naming any of them is a refusal, not a runtime block.
    for bad in ("network.get", "fs.read", "process.env", "secrets.api_key",
                "store.other", "safety.rules", "policy.set", "gateway.key"):
        r = E.evaluate(say({"str": [{"var": bad}]}), facts(), grants=E.DEFAULT_GRANTS)
        assert not r.ok and r.breach == "invalid", bad


def test_the_conformance_file_is_real_and_covers_all_six_hooks():
    """§8's migration table is a **deliverable**, not an illustration — so assert the file
    exists, parses, and carries all six rows before `test_ext.py` leans on it."""
    path = os.path.join(os.path.dirname(__file__), "data", "ext_conformance.json")
    doc = json.load(open(path, encoding="utf-8"))
    rows = {r["name"]: r for r in doc["rows"]}
    assert sorted(rows) == ["G1", "G2", "G3", "G4", "G5", "G6"]
    for name, row in rows.items():
        assert E.validate(row["ast"], allow_p1=True) == [], (name, row["ast"])
        assert row["expected_effects"], name
        assert row["explain"], name
    # No upstream source text travelled with the port (clean-room + attribution).
    blob = json.dumps(doc)
    for python_ism in ("def ", "import ", "lambda", "self.", "volley.", "time.sleep"):
        assert python_ism not in blob, python_ism
    assert not re.search(r"\bexec\s*\(", blob)
