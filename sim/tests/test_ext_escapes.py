"""X1–X12 — the escape tests for sandboxed content extensions (BEYOND #6 P0).

**A sandbox is worth exactly what its escape tests are worth.** These live in their own
file, apart from `test_ext.py`'s behaviour tests, because they are read by different eyes:
a reviewer asking "can a stranger's content pack hurt this appliance?" should be able to
read one file and get an answer.

The design under test is `docs/architecture/backlog/sandboxed-extensions.md` §3.2 — a
declarative rule list over a **total, JSON-AST expression language**, interpreted by pure
stdlib Python with no `exec`, no parser, no loop and no reachable host object. That choice
is what makes these assertions *provable properties of a closed table* rather than a
standing bet against the next CVE in an interpreter we do not maintain (§3.2 reason 2).

Each test below names the guard it fences. Every one was also checked in the **other**
direction — the guard was removed by hand and the test was watched to fail — and the
mutation is recorded in the docstring so the next reader knows the assertion has teeth.
"""
import ast as pyast
import json
import os
import re
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.content import ext as E                      # noqa: E402
from moxie_sdk.content import render as R                   # noqa: E402
from moxie_sdk.content.volley import Volley, Session        # noqa: E402

EXT_PY = os.path.join(REPO, "mqtt", "moxie_sdk", "content", "ext.py")


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

#: `{name: (expression, the capabilities it would need if it were legal)}`. The
#: capabilities matter: an AST that is refused because it forgot to declare `memory.read`
#: proves nothing about the dunder guard, so each probe declares exactly what a *legal*
#: version of itself would need. That way the **only** thing left to refuse it is the
#: guard under test — which is what made the mutation run below meaningful.
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
    """X1 — every classic escape is a **load-time refusal**, not a runtime block.

    An unknown op, or a path whose root is not a fact, or a path segment beginning `_`:
    each one means the program is never evaluated at all, so there is no evaluation to get
    wrong. `__class__`, `__init__` and `_meta` are therefore not "blocked"; they are not
    valid programs.

    Mutation checked: deleting the `seg.startswith("_")` refusal in `ext._Validator._var`
    makes `private_memory_meta`, `private_provenance` and `dunder_init_globals` fail
    (the dunder paths then validate). Deleting the `root not in FACT_ROOTS` refusal makes
    `env_root`, `os_root`, `config_root` and `builtins` fail.
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
    """X1's second half — the op, statement and fact-root key sets equal frozen literals.

    Adding an operator is therefore a test edit, which is a reviewer. This is the brake on
    risk R1 (*"the op table grows until it is a language"*), and it is deliberately an
    equality rather than a subset: removing an op is a breaking change for installed packs
    and should be noticed too.

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
    """X1's third half — there is no way to *name* a program from inside a program.

    No statement and no op takes another rule, a rule index, a function, or a name that
    could be bound to one. `let` binds **values**, never references (§4.3), which is what
    makes the maximum cost of an extension statically computable at load.
    """
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
    """X2 — the dict `ContentApp` hands the evaluator is plain JSON, all the way down.

    Recursively walk what the host actually builds for a real turn and assert every value
    is `str/int/float/bool/None/list/dict`. **There is no object to walk to**, so
    attribute-walking has no target — which is the whole security argument of §4.4, and it
    is why X1's dunder paths are pointless as well as invalid.

    Mutation checked: making `content_app.ext_facts` put the live `Volley` into the dict
    (`base["volley"] = volley`) fails this immediately.
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
    """X2's corollary — even if a host bug *did* leak an object into the fact base, no op
    can do anything with it: there is no attribute access, and every op coerces.

    This is the belt to §4.4's brace. It documents that the guarantee has two independent
    halves, so one bug is not an escape.
    """
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

#: The §2.6 finding, which was **already closed** before this slice began — PR #56/#62 put
#: `jinja2.sandbox.SandboxedEnvironment` behind `render_prompt`, and PR #78 closed the
#: matching hole in the dependency-free fallback (`_resolve` refuses `_`-leading path
#: segments, counted in `BLOCKED`). `test_render_sandbox.py` and
#: `test_content_pack_sandbox.py` are that work's fences and carry eight escape probes
#: between them. X3 is kept here anyway, in the extensions' own escape file, because the
#: brief's acceptance criterion 1 names it and because a reader auditing *this* sandbox
#: needs the appliance's **other** execution surface fenced in front of them, not in a
#: file they have to know to go and find.
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
    installed **or** absent.

    `prompt` and `opener` are pack-importable fields (`packs.SPEC["conversation"]`), so
    anyone who can hand a parent a pack chooses this string. Under a plain
    `jinja2.Environment` that walk reaches `__builtins__`, `__import__('os')` and
    `getcwd()` — proven by execution when the brief was written. It is fenced now, and
    this test is the fence for **both** renderers: the parametrisation runs once as
    shipped, and once with jinja2 forced absent so the dependency-free fallback is
    exercised on the same probes. A skip that reads as coverage would be worse than a
    failure, so neither shape is skipped.

    Mutation checked (twice): swapping `render._sandbox()` back to a plain
    `jinja2.Environment` fails every probe on the jinja2 shape; deleting the
    `part.startswith("_")` refusal in `render._resolve` fails
    `session_repr_globals_environ` on the jinja2-less shape.
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
    """X3's point, stated as an assertion: with the renderer sandboxed, the capability
    model in §5 is the appliance's **only** execution surface rather than its second one.

    `code` is still never executed — the field round-trips as opaque data, and nothing in
    `moxie_sdk` calls `exec`, `eval` or `compile` on it (§7.4, forever).
    """
    for name in ("content_app.py", "ext.py", "module.py", "packs.py", "render.py"):
        src = open(os.path.join(REPO, "mqtt", "moxie_sdk", "content", name)).read()
        tree = pyast.parse(src)
        for node in pyast.walk(tree):
            if isinstance(node, pyast.Call) and isinstance(node.func, pyast.Name):
                assert node.func.id not in ("exec", "eval", "compile", "__import__"), \
                    f"{name} calls {node.func.id}()"


# --------------------------------------------------------------------------- #
# X4 — an infinite loop is unrepresentable, and the budget still holds
# --------------------------------------------------------------------------- #

def test_x4_the_grammar_has_no_loop_or_recursion_construct():
    """X4(i) — you cannot write a loop, because there is no loop to write.

    Not "loops are rejected": the op and statement key sets (frozen in X1) contain no
    iteration, no jump, no user-defined function and no way to name a rule from inside a
    rule. §2.5's finding — not one of upstream's nine hooks iterates — is what makes that
    affordable rather than crippling.
    """
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
    """X4(ii) — the backstop works, and it returns rather than hanging.

    A deep chain of `if`s that re-evaluates a costly subtree burns steps. It hits
    `MOXIE_EXT_MAX_STEPS`, returns `ok=False` with `breach="steps"`, and discards its
    effects whole. Measured against the *injected* monotonic clock, so the assertion is
    about the evaluator's own accounting rather than about how loaded the CI runner is
    (integration playbook rule 11).

    Mutation checked: removing the `self.steps > self.limits.max_steps` raise in
    `_Machine.step` makes this test run to completion and return `ok=True`.
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
    """X4(ii) again, on the other budget — the wall clock is an **injected** monotonic
    reading checked every 256 steps, with no thread and no signal, so it behaves
    identically in the supervisor's handler thread and in a Worker isolate (§6.2).

    Mutation checked: removing the `self.monotonic() > self.deadline` raise makes this
    return `ok=True`.
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
    """X5 — every allocation path fails at its cap, and the process survives.

    `repeat` nested eight deep, `concat` of 32 × 16 KiB, `join` over a list with a 900-char
    separator, and `format` with a five-digit width: each one hits
    `MOXIE_EXT_MAX_VALUE_BYTES` or `MAX_TOTAL_BYTES`, returns `ok=False`, and leaves no
    effect behind. Nothing here is allowed to raise `MemoryError` — the cap is checked as
    each value is produced, and `repeat`'s own bound (16) means the largest single
    intermediate is 16 × the value cap even in the worst case.

    Mutation checked: removing the `n > self.limits.max_value_bytes` raise in
    `_Machine.charge` makes `repeat_nested_to_depth_8` return `ok=True` with a
    4-billion-character string, which is exactly the failure mode this cap exists for.
    """
    e = say(HUGE[name])
    assert E.validate(e) == [], "the AST is legal; the *value* is what must fail"
    r = E.evaluate(e, facts(), grants=E.DEFAULT_GRANTS)
    assert not r.ok, f"{name} produced {len(r.effects and r.effects[0].get('text', ''))}"
    assert r.breach in ("value", "total"), r
    assert r.effects == []


def test_x5_a_width_beyond_the_spec_is_refused_at_load():
    """X5's other half — `{"format": ["1000000000d", 1]}` never reaches an op at all.

    `format` takes an **explicit spec** (§6.1), and the spec grammar caps the width at five
    digits. A billion-wide field is therefore a malformed program, refused at import, which
    is the only moment at which refusing it costs nobody anything.
    """
    # The spec is *data*, so the op returns the error value rather than raising…
    assert E.is_error(E._format("1000000000d", 1))
    # …and an error reaching a `say` fails the extension rather than speaking "error"
    # (§4.6), so the child hears the conversation's answer instead of a billion spaces.
    r = E.evaluate(say({"format": ["1000000000d", 1]}), facts(), grants=E.DEFAULT_GRANTS)
    assert not r.ok and r.breach == "error", r
    assert r.effects == []


def test_x5_the_total_allocation_counter_stops_death_by_a_thousand_strings():
    """X5's third path — no single value breaches, but the running total does.

    Mutation checked: removing the `self.total > self.limits.max_total_bytes` raise makes
    this return `ok=True`.
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
    """X6 — a 10 000-deep expression is a **load refusal**, never a stack probe.

    Depth 32 evaluates. Depth 33 is refused. Depth 10 000 is refused. In no case does a
    `RecursionError` escape, because the validator is depth-counted before the evaluator is
    ever reached and the evaluator is depth-counted again (`_Machine.eval` raises
    `_Breach("invalid")` at `MAX_DEPTH`) for a caller that skipped validation.

    Mutation checked: removing the `depth > MAX_DEPTH` refusal from `_Validator.expr` makes
    the 10 000-deep case raise `RecursionError` out of `validate()` — which is what turns a
    hostile pack into a 500 instead of a shrug.
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


def test_x7_the_evaluator_imports_no_clock_and_no_entropy():
    """X7(i) — parse `ext.py` with `ast` and assert the forbidden imports are absent.

    This is cheap and it does not rot: an agent adding `import time` for "just a quick
    timeout" fails here before the review ever sees it. It is also the *mechanism* behind
    §6.1's determinism claim, rather than a restatement of it.

    Mutation checked: adding `import time` to `ext.py` fails this test.
    """
    tree = pyast.parse(open(EXT_PY).read())
    imported = set()
    for node in pyast.walk(tree):
        if isinstance(node, pyast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, pyast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & FORBIDDEN_IMPORTS), \
        f"ext.py imports {sorted(imported & FORBIDDEN_IMPORTS)}"
    assert imported <= {"__future__", "math", "re", "unicodedata", "dataclasses"}, \
        f"ext.py grew an import: {sorted(imported)}"


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
    """X7(ii) again — `random.*` draws from a PRNG seeded by the host, not from entropy.

    Not for secrecy, for **determinism** (§5.1): an extension with real entropy cannot be
    replayed, and replay is how the goldens work. The child still perceives variety because
    the seed is `sha256(turn_key ‖ extension_id)` and the turn key changes.
    """
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
    """X7(iii) — a program using `clock.ms` without declaring `clock` never runs.

    "Absent, not refused, when not granted" (§4.2) means the *turn* is never at risk: the
    extension fails validation, and `ContentApp` proceeds exactly as it does with no
    extension at all.
    """
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
    """X8 — a homoglyph capability is **refused**, never silently granted, and never
    rendered into the parent's grant list as the real thing.

    The check is deliberately *normalize and compare*, not *normalize and use*: `"ｍemory
    .write"` NFKC-folds **to** `"memory.write"`, so folding-then-matching would grant a
    capability whose written form is not the one the review rendered. The name must
    already be in NFKC normal form **and** match `^[a-z0-9_.]+$`.

    Mutation checked: changing `normal_name` to `return unicodedata.normalize("NFKC", raw)`
    (fold-and-use) makes `fullwidth_m` and `math_bold` pass validation and appear in the
    grant list as "Can remember things from this activity" — a scary grant reading as an
    accepted one, which is precisely the attack.
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
    """X9(i) — `{"var": "memory.other_module.x"}` is null, because the fact base contains
    **only** this extension's namespace.

    The namespace is supplied by the host, never by the extension: there is no operator, no
    statement and no path segment that names a namespace, a device, a collection or a
    file. The words for those do not exist in the grammar (§4.4 rule 3).
    """
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
    """X9(ii) — a memory key is `^[A-Za-z0-9][A-Za-z0-9_-]*(\\.[…])*$`: dot-segmented, no
    empty segment (so no `..`), no `/` or `\\` (so no traversal), and **no `_`-leading
    segment** (so a program cannot write `_meta` or `_provenance`, which belong to
    `MemoryStore` and not to a pack).

    Honest deviation from the brief's X9 list, recorded here rather than buried: the brief
    also names `"other_ns.x"` as something to refuse. We do **not** refuse it, because it
    is structurally identical to `"timers.1"` — the key the brief's own §4.1 example
    writes — and a rule that refused one would refuse the other. It is safe for a
    different and stronger reason, asserted in the next test: whatever the key, the write
    lands under the **host-supplied namespace**, so `other_ns.x` is a key *inside* this
    extension's own block and reaches nobody else.

    Mutation checked: widening `_KEY` to `^[^\\x00]+$` makes every row here pass
    validation.
    """
    e = ext([{"do": [{"remember": {"key": key, "value": 1}}, {"say": "hi"}]}],
            caps=("say", "memory.write"))
    reasons = E.validate(e)
    assert reasons, f"{key!r} was accepted as a memory key"


def test_x9_the_store_call_names_a_host_supplied_namespace(tmp_path):
    """X9(iii) — the write goes to `merge(device_id, own_namespace, …)` with **both**
    arguments supplied by the host, and a second robot's file is byte-unchanged.

    This is the assertion that makes the previous test's deviation safe: the extension
    chooses a key, never a namespace and never a device.

    Mutation checked: making `content_app.apply_ext_effects` take the namespace from the
    effect (`eff.get("namespace", ns)`) and adding one to the effect makes the
    cross-namespace assertion fail.
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
    """X10 — declared == used, or it does not install.

    Using more than you declared is the obvious half. Declaring more than you use is the
    half that matters to a parent: without it a pack could ask for `memory.write` and never
    write, leaving a door open for a later "upgrade" that does — or make the review look
    scarier than the program is, which trains parents to tick without reading.

    The consequence is acceptance criterion 4: the list a parent was shown is exactly,
    provably, what the program can do.

    Mutation checked (twice): deleting the `missing` branch lets `uses_undeclared` install;
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
    """The P1 boundary, asserted rather than assumed: a capability that cannot yet *do*
    anything parses as grammar (so §8's goldens are checked today) and is **refused at
    load**. Shipping one that does nothing would be worse than refusing it aloud.

    **The example changed on 2026-09-04, and only the example.** This test used to make
    its point with `act`, because `volley.execution_actions` was not on the wire (brief
    S5). It is now, so `act` is no longer an example of a capability that does nothing —
    `test_x10_an_act_is_bounded_declared_and_granted_or_it_does_not_load` below is what
    guards it instead, and it guards *more*, not less. `brain` is still an example, so it
    is the one used here. Nothing about the invariant moved: the last two lines — *a
    still-P1 capability is refused, and `evaluate()` has no `allow_p1` door* — are the
    escape property, and they are unchanged and still assert on `E.P1_CAPABILITIES`.

    **`subscribe` left the set on 2026-09-05**, the same way and for the same reason, so
    the assertion below no longer names it: `Volley.subscriptions` had no consumer, and
    now it has one (`content_app.subscriptions_of` → `Reply.subscribe` →
    `moxie_runtime._publish_chat`'s merge). Its own three gates are asserted in
    `test_x10_a_subscribe_is_bounded_declared_and_granted_or_it_does_not_load` below —
    again, *more* than this test was claiming, not less.
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
    """What replaced the `act` half of the test above — the three gates, each asserted.

    An `act` is not free just because the wire exists. It is refused at **load**, never at
    runtime, unless all three hold:

    1. **The name is in the closed table.** `ext.ACTION_WORDS` is the whole set of robot
       functions this appliance will ever name, and it is the same table the parent-facing
       sentence comes from — so a function nobody wrote English for cannot be declared,
       granted or emitted. `qr-launch-cards.md` §P0-b: *"the catalog is a closed allowlist,
       and this is a safety property, not tidiness."*
    2. **The pack declared it.** `act.foo` used but not declared fails at load, because
       declared-equals-used is a load condition (§5) — so the grant list a parent reads is
       provably the program's reach.
    3. **The host granted it.** `act.<name>` is in neither `DEFAULT_GRANTS` nor
       `content_app.SHIPPED_EXTRA_GRANTS`; an ungranted one does not run at all, so "absent,
       not refused, when not granted" (§4.2) still holds — the turn is never at risk.
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

    # 3 — declared and known, but not granted: still nothing runs.
    ungranted = E.validate(good, grants=E.DEFAULT_GRANTS | CA.SHIPPED_EXTRA_GRANTS)
    assert ungranted and "has not been granted" in ungranted[0], ungranted
    assert not any(c.startswith("act.")
                   for c in set(E.DEFAULT_GRANTS) | set(CA.SHIPPED_EXTRA_GRANTS))
    r = E.evaluate(good, facts(), grants=E.DEFAULT_GRANTS)
    assert not r.ok and r.effects == []

    # …and with all three satisfied it runs, and says so in words a parent reads.
    assert E.validate(good, grants=E.DEFAULT_GRANTS | {"act.eb_timer_request"}) == []
    r = E.evaluate(good, facts(), grants=E.DEFAULT_GRANTS | {"act.eb_timer_request"})
    assert r.ok and r.effects[0] == {"kind": "act", "name": "eb_timer_request",
                                     "args": ["1", "0"]}
    assert "Can ask Moxie to set or cancel a timer" in E.grant_list(good)


def test_x10_the_host_will_not_name_a_function_the_table_does_not():
    """The second gate on the same table, at the host boundary — belt to the validator's
    brace, because `execution_actions_of` is the last function before a string becomes a
    `function_id` addressed to a robot in a child's room.

    Two ways in are checked: an effect list handed straight to `apply_ext_effects` (what a
    future evaluator bug would produce) and a Python global handler calling
    `volley.add_execution_action` directly (which never passed a validator at all). Both
    drop the unknown name and keep the known one.
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
    """`subscribe`'s three gates, the twin of the `act` test above.

    A `subscribe` is not free just because the wire exists. It is refused at **load**,
    never at runtime, unless all three hold:

    1. **The event is in the closed vocabulary.** `ext.SUBSCRIBE_EVENTS` is the recovered
       vision catalog (vision.md §1.1-1.2) and nothing else, so a pack cannot ask to be
       woken by a string somebody invented. The same argument `qr-launch-cards.md` §P0-b
       makes for the launch-card catalogue, pointed the other way down the wire: an
       *input* a stranger's pack can arrange to receive is as much a surface as an output
       it can send.
    2. **The pack declared it.** Declared-equals-used is a load condition (§5), so the
       grant list a parent reads is provably the program's reach.
    3. **The host granted it.** `subscribe` is in neither `DEFAULT_GRANTS` nor
       `content_app.SHIPPED_EXTRA_GRANTS`; grantable is not granted, and an ungranted
       capability means the program never runs at all (§4.2's *"absent, not refused"*).
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
    """The second gate on the event table, at the host boundary — the exact twin of
    `test_x10_the_host_will_not_name_a_function_the_table_does_not` above, and load-bearing
    for the same reason: `subscriptions_of` is the last function before a string becomes an
    `EventSubscription.active[]` entry addressed to a robot in a child's room.

    Two ways in, both checked. An effect list handed straight to `apply_ext_effects` is
    what a future evaluator bug would produce. `volley.update_subscriptions` is the
    *contract's* API for a registered Python global handler
    (`content-module-contract.md` §"What module code may do"), and that caller never met
    the validator at all — which is why the gate cannot live only in `ext.py`.
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
    """X11 — a program whose third statement breaches leaves **no** memory write, **no**
    output and **no** note.

    Statements do not touch the world; they append to an effect list the host applies
    *after* the program returns (§4.5). So a breach mid-program discards the list whole,
    and a partially-executed extension cannot write half a memory record.

    Mutation checked: making `evaluate()` return `ExtResult(ok=False, effects=effects, …)`
    on a `_Breach` — i.e. handing back the prefix that happened to succeed — fails every
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
    """X11's sibling, and §4.6's whole point — an error never becomes speech.

    `int("banana")` is the error value. It propagates through every op, and when it reaches
    a `say` the **extension** fails rather than the child hearing the word "error". That is
    the one instinct of upstream's `f"Script error: {e}"` we deliberately do not port (U6).
    """
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
    """§4.6 as a sweep — *there is no state in which the evaluator does not return.*

    Division by zero yields an error value, a missing key yields null, an out-of-range
    index yields null, a cross-type comparison is false. A total language has no exceptions
    to leak, which is why `test_no_escape` can be a provable property here and is only a
    bet in an embedded-VM design (§3.2 reason 2 and reason 4).
    """
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
    """X12 — extensions do not construct regexes, and they do not fix the one that exists.

    There is no regex operator, no `match`, no `search` and no way to build a pattern; the
    only regex on this path is the **item's own** `pattern`, which `packs.validate_item`
    caps at `MAX_PATTERN_CHARS` and compiles once. That cap is a *named, accepted* risk
    (brief P7: a compiled Python regex has no timeout in the stdlib), and this test asserts
    the boundary rather than claiming the risk away — the risk stays filed against packs,
    where it belongs (R6).
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
    """Acceptance criterion 6 — stated as the enumerated union of the op table and the
    fact base, which is the only form of this claim that a test can actually check.

    These are not "refused by default". **There is no operator, statement or path that
    names them**, so refusing them is not a policy decision a config flag could reverse.
    """
    surface = set(E.OPS) | set(E.STATEMENTS) | set(E.FACT_ROOTS) | {"lit", "var"}
    # Every name in the surface is in our own source, and the surface is small enough to
    # read in one screen — which is the property that justified choosing this design.
    assert len(surface) <= 80, len(surface)
    src = open(EXT_PY).read()
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
