"""The dependency-free fallback must never put template syntax into a system prompt.

`content-module-contract.md`:42 promises a module author that `prompt` is Jinja2-templated
and names the block form. Half 1 of the fix (`test_render_container_deps.py`) makes that
true in the container. This file is half 2: what happens when jinja2 is *genuinely* absent
— a bare-metal install, or `pip install moxie-cloud-sdk` without the `content` extra.

The old fallback substituted `{{ dotted.path }}` and passed everything else through
**verbatim**, measured:

    input : You are Moxie.{% if presence.face_present %} Sam is here.{% endif %} Say hi to {{ nickname }}.
    output: You are Moxie.{% if presence.face_present %} Sam is here.{% endif %} Say hi to Sam.

That string is a *system prompt*. Literal `{% if presence.face_present %}` in the place a
model takes its instructions from is not a cosmetic glitch — it is instructions-shaped
noise, and the model's response to it is anybody's guess.

So the fence here has three parts, and the first one is deliberately **general** rather
than a list of expected strings, because "some construct nobody thought of leaks syntax"
is exactly the bug class:

1. **No template syntax, ever** — a regex over the output, applied to every construct the
   contract names, to every construct jinja2 offers that an author could plausibly type,
   and to every one of those nested inside every other one (§1).
2. **Faithful where it claims to be** — the constructs the fallback evaluates must be
   *byte-identical* to real jinja2, so the fallback stays a subset of the real renderer
   rather than a divergent dialect (§2, jinja2 required).
3. **Counted where it is not** — `render.STRIPPED` is `BLOCKED`'s sibling: the degradation
   is invisible in the output by design, so without a counter a deployment could serve
   thinned prompts forever (§3).

Everything here drives `_minimal_render` directly, so the file is meaningful in **both**
venv shapes — with jinja2 installed and without. §2 is the only jinja2-gated section.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.dirname(__file__))

from moxie_sdk.content import render as R  # noqa: E402

MODULE_DIR = os.path.join(REPO, "mqtt", "content_modules")

#: **The general assertion.** Any of these six fragments in a rendered prompt means
#: template source reached the brain. Written as a pattern rather than a set of expected
#: outputs on purpose: a hand-picked list only catches the constructs somebody remembered.
TEMPLATE_SYNTAX = re.compile(r"\{\{|\}\}|\{%|%\}|\{#|#\}")

#: The context the constructs below are rendered over — the shape `ContentApp` really
#: builds, trimmed to what a template can reach.
def _ctx(face: bool = True) -> dict:
    return {
        "nickname": "Sam",
        "volley": {"config": {"child_pii": {"nickname": "Sam", "pronouns": "she/her"}},
                   "persist_data": {"memory_chat": {"facts": ["has a dog named Rufus"],
                                                    "preferences": ["likes dinosaurs"]}},
                   "speech": "hi"},
        "session": {"history": [{"role": "user", "content": "hi"}], "overflow": False},
        "presence": {"face_present": face, "line": "Sam just walked in."},
    }


def _child_context() -> dict:
    """The context `ContentApp` really builds, with memory populated — real `Volley`,
    `Session` and `FactList` objects rather than the plain dicts `_ctx` uses, because
    `{{ volley.persist_data.ns.facts }}` renders as bullet lines only through
    `FactList.__str__` (`memory.py`). Built locally rather than imported from
    `test_render_sandbox_parity`, which `importorskip`s jinja2 at module scope and would
    take this whole file down in the no-jinja2 venv — the shape that matters most here."""
    from moxie_sdk.content.memory import wrap_facts
    from moxie_sdk.content.volley import Session, Volley

    persist = wrap_facts({
        "memory_chat": {"facts": [{"id": "f1", "text": "has a dog named Rufus"},
                                  "is six years old"],
                        "preferences": ["likes dinosaurs"],
                        "open_threads": ["we were going to name the dinosaur"],
                        "summaries": ["we talked about space"]},
        "free_chat": {"facts": ["loves the colour green"]},
    })
    v = Volley(speech="hi", config={"child_pii": {"nickname": "Sam",
                                                  "pronouns": "she/her"}},
               request={"input_vars": {"topic": "space"}}, persist_data=persist)
    session = Session(history=[{"role": "user", "content": "hi"}], persist_data=persist,
                      max_volleys=40, module_id="OPENMOXIE_CHAT", content_id="memory")
    return {"volley": v, "session": session,
            "presence": {"face_present": True, "line": "Sam just walked in.",
                         "seen_s_ago": 3}}


# ---------------------------------------------------------------- the corpus --
#: Every construct the contract names, plus every jinja2 form an author would reach for
#: next. `faithful` says whether the fallback claims to reproduce jinja2 exactly (and so
#: must not move `STRIPPED`); `keeps` is a substring the output must still contain, for
#: the constructs that are supposed to survive.
CONSTRUCTS = {
    # --- what content-module-contract.md:42 explicitly promises -----------------
    "dotted_path": dict(t="Hi {{ nickname }}!", faithful=True, keeps="Sam"),
    "nested_dotted_path": dict(t="Hi {{ volley.config.child_pii.nickname }}!",
                               faithful=True, keeps="Sam"),
    "no_spaces": dict(t="Hi {{volley.config.child_pii.nickname}}!",
                      faithful=True, keeps="Sam"),
    "if_block": dict(t="You are Moxie.{% if presence.face_present %} Sam is here."
                       "{% endif %} Say hi to {{ nickname }}.",
                     faithful=True, keeps="Sam is here"),
    "if_block_false": dict(t="You are Moxie.{% if session.overflow %} Wrap up.{% endif %}"
                             " Say hi to {{ nickname }}.",
                           faithful=True, keeps="Say hi to Sam"),
    "if_else": dict(t="{% if presence.face_present %}They are here.{% else %}Nobody."
                      "{% endif %}", faithful=True, keeps="They are here"),
    "if_elif_else": dict(t="{% if session.overflow %}wrap{% elif presence.face_present %}"
                           "greet{% else %}wait{% endif %}", faithful=True, keeps="greet"),
    "if_not": dict(t="{% if not session.overflow %}keep going{% endif %}",
                   faithful=True, keeps="keep going"),
    "nested_if": dict(t="{% if session.history %}{% if presence.face_present %}both"
                        "{% endif %}{% endif %}", faithful=True, keeps="both"),
    "if_missing_path": dict(t="{% if nobody.here.at.all %}no{% else %}yes{% endif %}",
                            faithful=True, keeps="yes"),
    "comment": dict(t="before{# a note #}after", faithful=True, keeps="beforeafter"),
    "whitespace_control": dict(t="A{%- if presence.face_present %} B{% endif -%} C",
                               faithful=True, keeps="B"),
    "var_whitespace_control": dict(t="{{- nickname -}} ok", faithful=True, keeps="Sam"),
    # --- richer forms the fallback cannot evaluate: must degrade, never leak ----
    "if_comparison": dict(t="{% if session.history | length > 2 %}long{% else %}short"
                            "{% endif %}", faithful=False, keeps="short"),
    "if_and": dict(t="{% if presence.face_present and session.history %}both{% else %}"
                     "neither{% endif %}", faithful=False, keeps="neither"),
    # `true`/`false`/`none` LOOK like bare names but are jinja2 literals; the fallback
    # got this wrong until §2 caught it (`_LITERALS` in render.py).
    "if_literal_true": dict(t="{% if true %}always{% else %}never{% endif %}",
                            faithful=True, keeps="always"),
    "if_literal_false": dict(t="{% if false %}never{% else %}always{% endif %}",
                             faithful=True, keeps="always"),
    "if_literal_none": dict(t="{% if none %}never{% else %}always{% endif %}",
                            faithful=True, keeps="always"),
    "if_not_literal": dict(t="{% if not false %}always{% endif %}",
                           faithful=True, keeps="always"),
    "for_loop": dict(t="{% for f in volley.persist_data.memory_chat.facts %}"
                       "{{ loop.index }}. {{ f }}\n{% endfor %}", faithful=False),
    "for_else": dict(t="{% for f in volley.persist_data.memory_chat.facts %}{{ f }}"
                       "{% else %}nothing yet{% endfor %}",
                     faithful=False, keeps="nothing yet"),
    "for_nested_if": dict(t="{% for f in volley.persist_data.memory_chat.facts %}"
                            "{% if f %}{{ f }}{% endif %}{% endfor %}", faithful=False),
    "join_filter": dict(t="{{ volley.persist_data.memory_chat.preferences | join('; ') }}",
                        faithful=False),
    "length_filter": dict(t="{{ session.history | length }} messages", faithful=False,
                          keeps="messages"),
    "default_filter": dict(t="{{ missing | default('friend') }}", faithful=False),
    "trim_lower": dict(t="{{ presence.line | trim | lower }}", faithful=False),
    "subscript": dict(t="{{ volley.persist_data['memory_chat']['facts'] }}",
                      faithful=False),
    "method_call": dict(t="{{ volley.config.child_pii.get('pronouns') }}", faithful=False),
    "string_method": dict(t="{{ nickname.upper() }}", faithful=False),
    "inline_if": dict(t="{{ 'yes' if presence.face_present else 'no' }}", faithful=False),
    "arithmetic": dict(t="{{ 1 + 2 }}", faithful=False),
    "global_range": dict(t="{{ range(3) | list | join('-') }}", faithful=False),
    "set_stmt": dict(t="{% set n = nickname %}Hi {{ n }}!", faithful=False, keeps="Hi"),
    "filter_block": dict(t="{% filter upper %}shout {{ nickname }}{% endfilter %}",
                         faithful=False),
    "with_block": dict(t="{% with a = 1 %}inner {{ a }}{% endwith %}", faithful=False),
    "raw_block": dict(t="{% raw %}{{ literal }}{% endraw %}", faithful=False),
    "macro_block": dict(t="{% macro m(x) %}{{ x }}{% endmacro %}done", faithful=False,
                        keeps="done"),
    "autoescape_block": dict(t="{% autoescape true %}x{% endautoescape %}",
                             faithful=False),
    "include_stmt": dict(t="{% include 'other.txt' %}tail", faithful=False, keeps="tail"),
    "unknown_future_tag": dict(t="{% teleport %}gone", faithful=False, keeps="gone"),
    # --- malformed input: the debris case ---------------------------------------
    "unterminated_var": dict(t="oops {{ unterminated", faithful=False),
    "unterminated_tag": dict(t="oops {% if x", faithful=False),
    "stray_closer": dict(t="stray {% endif %} tail", faithful=False, keeps="tail"),
    "stray_endfor": dict(t="{% endfor %}tail", faithful=False, keeps="tail"),
    "lone_braces": dict(t="}} weird {{", faithful=False),
}

#: Text that merely *looks* templatey and must survive untouched — the false-positive
#: side of the fence. A prompt asking the brain for JSON is the realistic case.
INNOCENT = {
    "json_example": 'Reply as JSON like {"mood": "happy"} please.',
    "single_braces": "Use {brackets} sparingly.",
    "plain": "You are Moxie. Be kind.",
    "trailing_newline": "line one\n",
    "css_ish": "a { color: red }",
    "empty": "",
}


def _shipped_templates():
    """Every Jinja-bearing string in the modules we actually ship, collected at test time
    so a new module joins this fence without anyone remembering the file exists."""
    out = []
    for path in sorted(glob.glob(os.path.join(MODULE_DIR, "*.json"))):
        data = json.load(open(path))

        def walk(o, p=""):
            if isinstance(o, dict):
                for k, v in o.items():
                    yield from walk(v, f"{p}.{k}")
            elif isinstance(o, list):
                for i, v in enumerate(o):
                    yield from walk(v, f"{p}[{i}]")
            elif isinstance(o, str) and ("{{" in o or "{%" in o):
                yield p, o

        out += [(os.path.basename(path) + p, s) for p, s in walk(data)]
    return out


SHIPPED = _shipped_templates()


def test_the_corpora_are_not_empty():
    """A corpus that finds nothing makes every parametrized test below a green no-op."""
    assert len(CONSTRUCTS) >= 40, len(CONSTRUCTS)
    assert len(SHIPPED) >= 4, f"only {len(SHIPPED)} Jinja-bearing strings in {MODULE_DIR}"
    assert sum(1 for c in CONSTRUCTS.values() if c["faithful"]) >= 12
    assert sum(1 for c in CONSTRUCTS.values() if not c["faithful"]) >= 20


# ============================================================================
# §1 — no template syntax, ever. The general assertion.
# ============================================================================
@pytest.mark.parametrize("face", [True, False], ids=["present", "absent"])
@pytest.mark.parametrize("name", sorted(CONSTRUCTS))
def test_no_construct_leaks_template_syntax(name, face):
    """The bug, as a regex. Not "the output equals this string" — any of `{{ }} {% %} {# #}`
    surviving into a system prompt is the failure, whatever produced it."""
    out = R._minimal_render(CONSTRUCTS[name]["t"], _ctx(face))
    leak = TEMPLATE_SYNTAX.search(out)
    assert not leak, (f"{name} left {leak.group(0)!r} in the prompt the brain receives: "
                      f"{out[:200]!r}")


@pytest.mark.parametrize("outer", sorted(CONSTRUCTS))
@pytest.mark.parametrize("inner", sorted(CONSTRUCTS))
def test_no_construct_leaks_when_nested_inside_another(inner, outer):
    """Generality, taken seriously: ~1.8k combinations of one construct concatenated with
    and wrapped by another. The scanner keeps a frame stack rather than parsing, and a
    stack is exactly where an unbalanced or unfamiliar tag would desynchronise and start
    emitting the source it was meant to remove."""
    a, b = CONSTRUCTS[outer]["t"], CONSTRUCTS[inner]["t"]
    for combo in (a + b,
                  "{% if presence.face_present %}" + a + b + "{% endif %}",
                  "{% for x in nothing %}" + a + "{% endfor %}" + b):
        out = R._minimal_render(combo, _ctx())
        leak = TEMPLATE_SYNTAX.search(out)
        assert not leak, f"{outer}+{inner} leaked {leak.group(0)!r}: {out[:160]!r}"


@pytest.mark.parametrize("label,template", SHIPPED, ids=[l for l, _ in SHIPPED])
def test_no_shipped_template_leaks_template_syntax(label, template):
    """The modules we ship, through the renderer a bare-metal install runs."""
    out = R._minimal_render(template, _child_context())
    assert not TEMPLATE_SYNTAX.search(out), f"{label}: {out[:200]!r}"
    assert "Sam" in out, f"{label} lost the nickname: {out[:200]!r}"


@pytest.mark.parametrize("name", sorted(n for n, c in CONSTRUCTS.items() if c.get("keeps")))
def test_a_construct_that_should_survive_keeps_its_authored_text(name):
    """Removing syntax is only half the job — "" passes §1 trivially. Every construct with
    a `keeps` must still put the author's own words on the page, so a degradation cannot
    quietly become a deletion. (The `keeps`-less entries are the ones whose *entire*
    content is a construct the fallback cannot evaluate; those correctly render empty.)"""
    case = CONSTRUCTS[name]
    out = R._minimal_render(case["t"], _ctx())
    assert case["keeps"] in out, f"{name} lost {case['keeps']!r}: {out!r}"


@pytest.mark.parametrize("name", sorted(INNOCENT))
def test_text_that_only_looks_templatey_is_untouched(name):
    """The false-positive fence. Single braces are not Jinja, and a prompt that asks the
    brain for JSON is the realistic way this would have been over-eager."""
    text = INNOCENT[name]
    assert R._minimal_render(text, _ctx()) == text


# ============================================================================
# §2 — faithful where it claims to be. The differential half (jinja2 required).
# ============================================================================
try:                                   # NOT importorskip: that would skip this whole file,
    import jinja2                      # and §1/§3 are the sections that matter *without*
except ImportError:                    # jinja2. Only §2 is gated.
    jinja2 = None

needs_jinja2 = pytest.mark.skipif(jinja2 is None,
                                  reason="§2 compares the fallback against the real "
                                         "renderer, which needs jinja2 installed")


def _real_render(template: str, context: dict) -> str:
    """Real Jinja2, configured exactly as `render_prompt` configures its sandbox."""
    env = jinja2.Environment(undefined=jinja2.ChainableUndefined, autoescape=False,
                             keep_trailing_newline=True)
    return env.from_string(template).render(**context)


FAITHFUL = sorted(n for n, c in CONSTRUCTS.items() if c["faithful"])


@needs_jinja2
@pytest.mark.parametrize("face", [True, False], ids=["present", "absent"])
@pytest.mark.parametrize("name", FAITHFUL)
def test_a_faithful_construct_matches_real_jinja2_byte_for_byte(name, face):
    """The fallback must be a *subset* of the real renderer, not a dialect. Every
    construct it claims to evaluate — the two forms the contract documents, plus
    `elif`/`else`/`not`/nesting/comments/whitespace-control — renders identically with
    jinja2 and without it, so upgrading a deployment changes nothing.

    This is the assertion that would catch a hand-rolled `{% if %}` getting the
    truthiness of `0`, `[]`, `""` or a missing path subtly wrong."""
    template = CONSTRUCTS[name]["t"]
    ctx = _ctx(face)
    assert R._minimal_render(template, ctx) == _real_render(template, ctx)


@needs_jinja2
@pytest.mark.parametrize("label,template", SHIPPED, ids=[l for l, _ in SHIPPED])
def test_a_shipped_template_matches_real_jinja2_byte_for_byte(label, template):
    """The corpus that actually ships, held to the same standard."""
    ctx = _child_context()
    assert R._minimal_render(template, ctx) == _real_render(template, ctx)


@needs_jinja2
def test_the_documented_block_form_renders_correctly_with_jinja2():
    """Half 1's promise, at the `render_prompt` seam: the form
    `content-module-contract.md`:42 advertises now works, both branches, sandbox and all."""
    t = ("You are Moxie.{% if presence.face_present %} Sam is here.{% endif %}"
         " Say hi to {{ nickname }}.")
    assert R.render_prompt(t, _ctx(True)) == "You are Moxie. Sam is here. Say hi to Sam."
    assert R.render_prompt(t, _ctx(False)) == "You are Moxie. Say hi to Sam."


# ============================================================================
# §3 — counted where it is not faithful.
# ============================================================================
@pytest.mark.parametrize("name", sorted(n for n, c in CONSTRUCTS.items()
                                        if not c["faithful"]))
def test_a_degraded_construct_is_counted(name):
    """`STRIPPED` is `BLOCKED`'s sibling. A fallback deployment serving block-using
    content degrades *invisibly* — that is the whole design goal — so the counter is the
    only thing that separates "working fine" from "quietly serving thinner prompts"."""
    before = R.STRIPPED
    R._minimal_render(CONSTRUCTS[name]["t"], _ctx())
    assert R.STRIPPED > before, f"{name} degraded silently"


@pytest.mark.parametrize("name", sorted(n for n, c in CONSTRUCTS.items() if c["faithful"]))
def test_a_faithful_construct_is_not_counted(name):
    """The other direction, and the reason the counter is worth reading: a number that
    ticks for `{# comments #}` and for `{% if %}`s that rendered perfectly well is noise,
    not a signal. Only real divergence from jinja2 counts."""
    before = R.STRIPPED
    R._minimal_render(CONSTRUCTS[name]["t"], _ctx())
    assert R.STRIPPED == before, f"{name} is faithful but moved STRIPPED"


@pytest.mark.parametrize("label,template", SHIPPED, ids=[l for l, _ in SHIPPED])
def test_no_shipped_template_is_counted(label, template):
    """Our own content uses only `{{ dotted.path }}`, so a bare-metal install of *today's*
    modules should read `STRIPPED == 0`. The day someone adds a filter to a shipped
    module, this fails here rather than in a parent's living room."""
    before = R.STRIPPED
    R._minimal_render(template, _child_context())
    assert R.STRIPPED == before, f"{label} needs jinja2 — it must not ship as a default"


def test_a_construct_inside_an_untaken_branch_is_not_counted():
    """Precision. jinja2 would never have rendered it either, so removing it is no
    divergence — counting it would make the number fire on healthy content."""
    before = R.STRIPPED
    out = R._minimal_render("{% if session.overflow %}{{ a | join(',') }}{% endif %}ok",
                            _ctx())
    assert out == "ok" and R.STRIPPED == before


def test_the_counter_counts_per_construct_not_per_render():
    """Like `BLOCKED`: three unevaluable things in one template is three."""
    before = R.STRIPPED
    R._minimal_render("{{ a|join(',') }}{{ b|join(',') }}{{ c|join(',') }}", _ctx())
    assert R.STRIPPED == before + 3


# ============================================================================
# §4 — the whole path: what the brain actually receives.
# ============================================================================
def _without_jinja2(fn, *a, **kw):
    """Run `fn` with jinja2 unimportable — the bare-metal install's code path, simulated
    by blocking the import rather than uninstalling anything, so this holds in a full-fat
    venv too (the technique `test_package_contents.py` uses)."""
    import builtins
    real = builtins.__import__

    def blocked(name, *args, **kwargs):
        if name == "jinja2" or name.startswith("jinja2."):
            raise ImportError("blocked: simulating an install without the content extra")
        return real(name, *args, **kwargs)

    saved = {k: v for k, v in sys.modules.items() if k.split(".")[0] == "jinja2"}
    for k in saved:
        del sys.modules[k]
    builtins.__import__ = blocked
    try:
        return fn(*a, **kw)
    finally:
        builtins.__import__ = real
        sys.modules.update(saved)


def test_render_prompt_takes_the_fallback_and_emits_no_syntax_without_jinja2():
    """`render_prompt`, not just the private helper: the `ImportError` branch is wired."""
    t = ("You are Moxie.{% if presence.face_present %} Sam is here.{% endif %}"
         " Say hi to {{ nickname }}.")
    out = _without_jinja2(R.render_prompt, t, _ctx(True))
    assert out == "You are Moxie. Sam is here. Say hi to Sam."
    assert not TEMPLATE_SYNTAX.search(out)


def test_a_block_using_module_reaches_the_brain_as_english(tmp_path):
    """The end the bug was reported at: a module a parent authored or imported, driven
    through the real `ContentApp` and the real `MoxieRuntime` with jinja2 unimportable,
    and the assertion is on **the system message the brain received**. Creds-free.

    Before this fix that message contained `{% if presence.face_present %}` verbatim."""
    from helpers_runtime import assert_spec_response, drive_once
    from moxie_sdk.content import ContentApp, load_modules

    authored = {"conversations": [{
        "name": "Parent's Chat", "module_id": "PARENT_CHAT", "content_id": "default",
        "opener": "Hello!<opener>",
        "prompt": ("You are Moxie talking to {{ volley.config.child_pii.nickname }}."
                   "{% if volley.config.child_pii.notes %} Notes: "
                   "{{ volley.config.child_pii.notes }}.{% endif %}"
                   "{% for t in volley.persist_data.parent_chat.open_threads %}"
                   " Follow up on {{ t }}.{% endfor %}"
                   " Reply as JSON like {\"mood\":\"happy\"} if asked."),
        "max_tokens": 60}]}

    seen = {}

    def brain(messages):
        seen["messages"] = messages
        return "Hi! What shall we do today?"

    def run():
        app = ContentApp(load_modules(authored), brain, memory=False)
        return drive_once(app, "hello", device_id="d_fb", nickname="Sam",
                          module_id="PARENT_CHAT", content_id="default",
                          event_id="evt-fb-1")

    resp = _without_jinja2(run)
    assert_spec_response(resp, device_id="d_fb", event_id="evt-fb-1")

    system = seen["messages"][0]
    assert system["role"] == "system"
    body = system["content"]
    leak = TEMPLATE_SYNTAX.search(body)
    assert not leak, f"template source reached the brain: {leak.group(0)!r} in {body!r}"
    assert "Sam" in body, f"the fallback lost the nickname: {body!r}"
    assert '{"mood":"happy"}' in body, "the fallback ate the prompt's JSON example"
    assert body.strip().endswith("if asked."), f"trailing text lost: {body!r}"


def test_the_fallback_is_what_a_security_error_falls_back_to():
    """A `SecurityError` (an unsafe *operation*, not an attribute) escapes the sandbox as
    an exception and lands in `_minimal_render`. That path must obey the same rule — this
    is the one place a hostile pack could have aimed syntax at the brain *with* jinja2
    installed."""
    out = R.render_prompt("{% for i in ''.__class__.__mro__ %}{{ i }}{% endfor %}ok",
                          _ctx())
    assert not TEMPLATE_SYNTAX.search(out), out[:200]
    assert "class" not in out.lower(), out[:200]


def test_the_counter_is_documented_next_to_blocked():
    """`STRIPPED` only helps if an operator can find out what it means. Both counters are
    module-level ints with an explanatory comment; pin that they stay a pair."""
    src = open(os.path.join(REPO, "mqtt", "moxie_sdk", "content", "render.py")).read()
    assert re.search(r"^BLOCKED = 0$", src, re.M)
    assert re.search(r"^STRIPPED = 0$", src, re.M)
    assert "STRIPPED" in src.split("STRIPPED = 0")[0][-1200:], \
        "STRIPPED needs a `#:` comment above it, like BLOCKED"
    assert isinstance(R.STRIPPED, int) and isinstance(R.BLOCKED, int)
