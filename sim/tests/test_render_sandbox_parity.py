"""Did the sandbox break a legitimate prompt? — the parity half of the render sandbox.

`test_render_sandbox.py` proves escapes come back inert. But `SandboxedEnvironment`
substitutes an undefined (silently) when `is_safe_attribute` refuses, so an over-tight
sandbox would quietly empty working prompts while every escape test passes. This fence is
differential:

* The oracle is the pre-sandbox renderer (`_plain_render`: `jinja2.Environment(undefined=
  ChainableUndefined, autoescape=False, keep_trailing_newline=True)` with the same
  minimal-render fallback). Legitimate templates must render byte-identically through both.
* The corpus is real: every Jinja-bearing string in `mqtt/content_modules/*.json`, collected
  at test time so new modules join automatically.
* `BLOCKED` must not move — a refusal of an already-empty value is invisible in output.
* The whole path: shipped `memory_chat.json` through the real `ContentApp` and
  `MoxieRuntime`; the brain's system message carries the nickname and fact, no `{{`.

A broken legitimate construct is fixed in `render.py`; the sandbox stays.
"""
from __future__ import annotations

import glob
import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

from moxie_sdk.content import render as R          # noqa: E402

jinja2 = pytest.importorskip("jinja2", reason="the sandbox only exists when jinja2 does")

MODULE_DIR = os.path.join(REPO, "mqtt", "content_modules")


# --------------------------------------------------------------- the oracle --
def _plain_render(template: str, context: dict) -> str:
    """`render_prompt` as it behaved BEFORE the sandbox, transcribed literally (including
    the `except Exception -> _minimal_render` tail) so edits to `render.py` cannot move the
    oracle."""
    if not template:
        return ""
    try:
        env = jinja2.Environment(undefined=jinja2.ChainableUndefined,
                                 autoescape=False, keep_trailing_newline=True)
        return env.from_string(template).render(**context)
    except Exception:
        return R._minimal_render(template, context)


# ------------------------------------------------------------- the contexts --
def _child_context():
    """The context `ContentApp` really builds, with memory — through `wrap_facts`, so facts
    render as bullet lines as they do at runtime."""
    from moxie_sdk.content.memory import wrap_facts
    from moxie_sdk.content.volley import Session, Volley

    persist = wrap_facts({
        "memory_chat": {
            "facts": [{"id": "f1", "text": "has a dog named Rufus"},
                      "is six years old"],
            "preferences": ["likes dinosaurs"],
            "open_threads": ["we were going to name the dinosaur"],
            "summaries": ["we talked about space"],
        },
        "free_chat": {"facts": ["loves the colour green"]},
    })
    v = Volley(speech="hi", config={"child_pii": {"nickname": "Sam",
                                                  "pronouns": "she/her",
                                                  "birthday": "2020-04-01",
                                                  "notes": ""}},
               request={"input_vars": {"topic": "space"}},
               persist_data=persist)
    session = Session(history=[{"role": "user", "content": "hi"}], persist_data=persist,
                      max_volleys=40, module_id="OPENMOXIE_CHAT", content_id="memory")
    presence = {"face_present": True, "line": "Sam just walked in.", "seen_s_ago": 3}
    return {"volley": v, "session": session, "presence": presence}


def _empty_context():
    """The same shape with *nothing* remembered — the FTUE turn, and the case where a
    refusal would be invisible because the honest answer is empty too."""
    from moxie_sdk.content.memory import wrap_facts
    from moxie_sdk.content.volley import Session, Volley
    persist = wrap_facts({"memory_chat": {"facts": [], "preferences": [],
                                          "open_threads": [], "summaries": []},
                          "free_chat": {"facts": []}})
    v = Volley(speech="", config={"child_pii": {"nickname": "Ada"}},
               persist_data=persist)
    return {"volley": v, "session": Session(), "presence": {"face_present": False,
                                                            "line": ""}}


CONTEXTS = {"remembering": _child_context, "first-run": _empty_context}


# --------------------------------------------------- the shipped-module corpus --
def _walk(o, path=""):
    if isinstance(o, dict):
        for k, val in o.items():
            yield from _walk(val, f"{path}.{k}")
    elif isinstance(o, list):
        for i, val in enumerate(o):
            yield from _walk(val, f"{path}[{i}]")
    elif isinstance(o, str):
        yield path, o


def _shipped_templates():
    """[(label, template)] for every Jinja-bearing string in the modules we ship."""
    out = []
    for p in sorted(glob.glob(os.path.join(MODULE_DIR, "*.json"))):
        with open(p) as fh:
            data = json.load(fh)
        for path, s in _walk(data):
            if "{{" in s or "{%" in s:
                out.append((os.path.basename(p) + path, s))
    return out


SHIPPED = _shipped_templates()

#: The corpus has to be non-empty or every parametrized test below silently passes.
#: 4 is a floor under what `content_modules/` carries (starter: opener + prompt;
#: memory_chat: opener + two prompts).
MIN_SHIPPED_TEMPLATES = 4


def test_the_shipped_corpus_is_not_empty():
    """A globbed corpus that finds nothing is a green test that proves nothing."""
    assert len(SHIPPED) >= MIN_SHIPPED_TEMPLATES, \
        f"only {len(SHIPPED)} Jinja-bearing strings found under {MODULE_DIR}"


@pytest.mark.parametrize("ctx_name", sorted(CONTEXTS))
@pytest.mark.parametrize("label,template", SHIPPED, ids=[l for l, _ in SHIPPED])
def test_a_shipped_prompt_renders_identically_through_the_sandbox(label, template,
                                                                  ctx_name):
    """The differential assertion: sandbox output == pre-sandbox output, byte for byte."""
    ctx = CONTEXTS[ctx_name]()
    expected = _plain_render(template, ctx)
    got = R.render_prompt(template, ctx)
    assert got == expected, (
        f"{label} under {ctx_name} changed when the sandbox landed:\n"
        f"  pre-sandbox: {expected!r}\n  sandboxed:   {got!r}")


@pytest.mark.parametrize("label,template", SHIPPED, ids=[l for l, _ in SHIPPED])
def test_a_shipped_prompt_still_renders_non_empty_and_personalised(label, template):
    """Not just *equal* — actually working. Every shipped template must come back with
    content, with no un-rendered `{{`, and with the child's name in it (the whole
    reason the templating exists)."""
    ctx = _child_context()
    out = R.render_prompt(template, ctx)
    assert out.strip(), f"{label} rendered empty"
    assert "{{" not in out and "{%" not in out, f"{label} left raw Jinja: {out[:120]!r}"
    assert "Sam" in out, f"{label} never substituted child_pii.nickname: {out[:200]!r}"


@pytest.mark.parametrize("label,template", SHIPPED, ids=[l for l, _ in SHIPPED])
def test_a_shipped_prompt_never_trips_the_refusal_counter(label, template):
    """`BLOCKED` moving on our own content would mean the sandbox is refusing something
    legitimate — invisible in the output whenever the refused value was empty anyway."""
    before = R.BLOCKED
    for make in CONTEXTS.values():
        R.render_prompt(template, make())
    assert R.BLOCKED == before, \
        f"{label} tripped the sandbox {R.BLOCKED - before}x — a legitimate refusal is a bug"


def test_the_remembered_facts_reach_the_prompt_as_bullets():
    """The one substantive render the corpus tests can't state generically: memory
    actually lands in the prompt in the shape `memory.py::FactList` promises."""
    with open(os.path.join(MODULE_DIR, "memory_chat.json")) as fh:
        module = json.load(fh)
    out = R.render_prompt(module["conversations"][0]["prompt"], _child_context())
    assert "- has a dog named Rufus" in out
    assert "- is six years old" in out
    assert "- likes dinosaurs" in out


# ------------------------------------------- the documented construct corpus --
#: Everything `content-module-contract.md`:42 promises (`{{…}}`, `{% if %}`) plus ordinary
#: Jinja an author would reach for next, chosen for the sandbox's surface on LEGITIMATE code:
#: dict-key fallthrough (`persist_data.memory_chat`), bound methods on known mutables
#: (`.items()`, `.get()`, `.keys()`), string methods, getattr-using filters (`|attr` is an
#: escape probe and lives elsewhere), iteration over `FactList`, and a dataclass
#: `@property` (`session.overflow`). Each must agree across renderers and produce output.
LEGIT = {
    # the contract's two documented forms
    "dotted_path": "Hi {{ volley.config.child_pii.nickname }}!",
    "if_block": "{% if presence.face_present %}They are here.{% else %}Nobody.{% endif %}",
    # dict access that only resolves via __getitem__
    "persist_namespace": "Facts:\n{{ volley.persist_data.memory_chat.facts }}",
    "subscript": "{{ volley.persist_data['memory_chat']['preferences'] }}",
    "input_vars": "Topic: {{ volley.request.input_vars.topic }}",
    # iteration over the FactList subclass
    "for_loop": "{% for f in volley.persist_data.memory_chat.facts %}"
                "{{ loop.index }}. {{ f }}\n{% endfor %}",
    "for_else": "{% for f in volley.persist_data.free_chat.facts %}{{ f }}"
                "{% else %}nothing yet{% endfor %}",
    # dict methods on a known mutable (SandboxedEnvironment allows these; the
    # *Immutable* variant would not — pin which one we chose)
    "dict_items": "{% for k, v in presence.items() %}{{ k }}={{ v }} {% endfor %}",
    "dict_get": "{{ volley.config.child_pii.get('pronouns', 'they/them') }}",
    "dict_keys": "{{ volley.persist_data.memory_chat.keys() | list | sort | join(',') }}",
    # string + list methods and filters
    "string_methods": "{{ volley.config.child_pii.nickname.upper().strip() }}",
    "join_filter": "{{ volley.persist_data.memory_chat.preferences | join('; ') }}",
    "default_filter": "{{ volley.config.child_pii.missing | default('friend') }}",
    "length_filter": "{{ volley.persist_data.memory_chat.facts | length }} things",
    "trim_lower": "{{ presence.line | trim | lower }}",
    "list_slice": "{{ volley.persist_data.memory_chat.facts[:1] | join(', ') }}",
    # a @property on a dataclass — a real attribute lookup through is_safe_attribute
    "property_access": "{% if session.overflow %}wrap up{% else %}keep going{% endif %}",
    "session_history": "{{ session.history | length }} messages",
    # control structures an author would plausibly use
    "set_stmt": "{% set n = volley.config.child_pii.nickname %}Hi {{ n }}, hi {{ n }}!",
    "nested_if": "{% if session.history %}{% if presence.face_present %}both"
                 "{% endif %}{% endif %}",
    "whitespace_control": "A{%- if presence.face_present %} B{% endif -%} C",
    "filter_block": "{% filter upper %}shout {{ volley.config.child_pii.nickname }}"
                    "{% endfilter %}",
    "concat_test": "{{ 'yes' if volley.speech is defined else 'no' }}",
    "comment": "before{# a note #}after",
    "raw_block": "{% raw %}{{ literal }}{% endraw %}",
    "global_range": "{{ range(3) | list | join('-') }}",
    "global_dict": "{{ dict(a=1)['a'] }}",
    "trailing_newline": "line one\n",
}


@pytest.mark.parametrize("ctx_name", sorted(CONTEXTS))
@pytest.mark.parametrize("name", sorted(LEGIT))
def test_a_documented_construct_renders_identically_through_the_sandbox(name, ctx_name):
    ctx = CONTEXTS[ctx_name]()
    template = LEGIT[name]
    expected = _plain_render(template, ctx)
    got = R.render_prompt(template, ctx)
    assert got == expected, (f"{name} under {ctx_name}: pre-sandbox {expected!r} != "
                             f"sandboxed {got!r}")


@pytest.mark.parametrize("name", sorted(LEGIT))
def test_a_documented_construct_does_not_trip_the_counter(name):
    before = R.BLOCKED
    for make in CONTEXTS.values():
        R.render_prompt(LEGIT[name], make())
    assert R.BLOCKED == before, f"{name} tripped the sandbox — legitimate Jinja refused"


@pytest.mark.parametrize("name", sorted(LEGIT))
def test_a_documented_construct_produces_something(name):
    """Parity with a renderer that also returned "" would be worthless. Every construct
    above must actually put characters on the page under the remembering context."""
    out = R.render_prompt(LEGIT[name], _child_context())
    assert out.strip(), f"{name} rendered empty"


def test_the_fence_would_notice_an_over_tight_sandbox():
    """The parity assertion has teeth: a sandbox whose `is_safe_attribute` refuses
    everything must be caught on the shipped corpus. (`ImmutableSandboxedEnvironment` is
    useless as a control — it only refuses mutating methods, which no prompt uses.)
    """
    from jinja2.sandbox import SandboxedEnvironment

    class _RefuseEverything(SandboxedEnvironment):
        def is_safe_attribute(self, obj, attr, value):
            return False

    env = _RefuseEverything(undefined=jinja2.ChainableUndefined,
                            autoescape=False, keep_trailing_newline=True)
    ctx = _child_context()
    caught = []
    for label, template in SHIPPED:
        try:
            got = env.from_string(template).render(**ctx)
        except Exception:
            caught.append(label)
            continue
        if got != _plain_render(template, ctx):
            caught.append(label)
    assert len(caught) == len(SHIPPED), (
        "a sandbox that refuses every attribute did NOT change every shipped prompt, "
        f"so the parity assertion is not load-bearing (missed {len(SHIPPED) - len(caught)})")


# ------------------------------------ the renderer a BARE-METAL install still uses --
#
# The container ships jinja2 (`test_render_container_deps.py`), but `pyproject.toml` keeps it
# behind the `content` extra, so a bare `pip install moxie-cloud-sdk` renders with
# `_minimal_render`. Every shipped module must render there too.

def _no_jinja2_render(template: str, context: dict) -> str:
    """`render_prompt` with jinja2 made unimportable (import blocked, nothing uninstalled —
    as in `test_package_contents.py`)."""
    import builtins
    real_import = builtins.__import__

    def _blocked(name, *a, **kw):
        if name == "jinja2" or name.startswith("jinja2."):
            raise ImportError("blocked: simulating the shipped container")
        return real_import(name, *a, **kw)

    saved = {k: v for k, v in sys.modules.items() if k.split(".")[0] == "jinja2"}
    for k in saved:
        del sys.modules[k]
    builtins.__import__ = _blocked
    try:
        return R.render_prompt(template, context)
    finally:
        builtins.__import__ = real_import
        sys.modules.update(saved)


@pytest.mark.parametrize("label,template", SHIPPED, ids=[l for l, _ in SHIPPED])
def test_a_shipped_prompt_also_renders_without_jinja2_at_all(label, template):
    """Every module we ship uses only `{{ dotted.path }}`, which is exactly the subset
    the dependency-free fallback covers — so the container renders them personalised
    too. If a future shipped module reaches for a filter or a block, this fails here
    rather than in a parent's living room."""
    out = _no_jinja2_render(template, _child_context())
    assert out.strip() and "Sam" in out, f"{label} did not render without jinja2: {out[:160]!r}"
    assert "{{" not in out and "{%" not in out, \
        f"{label} left template syntax in the prompt the brain receives: {out[:200]!r}"


def test_the_fallback_reaches_memory_the_same_way_jinja_does():
    """`FactList.__str__` is why `persist_data` renders as bullets in *both* renderers
    (`memory.py`:88-91 says so). Asserted, because it is the one place the two paths
    could silently disagree about the child's own remembered facts."""
    with open(os.path.join(MODULE_DIR, "memory_chat.json")) as fh:
        prompt = json.load(fh)["conversations"][0]["prompt"]
    ctx = _child_context()
    assert _no_jinja2_render(prompt, ctx) == R.render_prompt(prompt, ctx)


def test_a_block_construct_is_no_longer_a_hole_in_the_fallback():
    """Without jinja2 the fallback EVALUATES a simple `{% if dotted.path %}` (the documented
    form) byte-identically to jinja2 and strips what it cannot evaluate, counting it in
    `render.STRIPPED`. The two asserts are the two branches; a guessing fallback would get
    one wrong."""
    tail = "Hi {{ volley.config.child_pii.nickname }}"
    template = "{% if presence.face_present %}They are here.{% endif %}" + tail
    ctx = _child_context()
    assert ctx["presence"]["face_present"] is True
    assert _no_jinja2_render(template, ctx) == "They are here.Hi Sam"

    ctx["presence"]["face_present"] = False
    assert _no_jinja2_render(template, ctx) == "Hi Sam"

    # And the promise generalises: identical to the real renderer, both ways.
    for face in (True, False):
        ctx["presence"]["face_present"] = face
        assert _no_jinja2_render(template, ctx) == _plain_render(template, ctx)


# ------------------------------------------------------- the whole-path proof --
def test_the_shipped_module_renders_through_the_real_runtime(tmp_path):
    """End to end, creds-free: shipped `memory_chat.json` → real `ContentApp` → real
    `MoxieRuntime` → the wire, with a fake brain only to read its system message. This is
    what would fail if the sandbox emptied the prompt."""
    from helpers_runtime import assert_spec_response, drive_once
    from moxie_sdk.content import ContentApp, load_modules
    from moxie_sdk.store import JsonStore, MemoryStore

    with open(os.path.join(MODULE_DIR, "memory_chat.json")) as fh:
        module = load_modules(json.load(fh))

    memory = MemoryStore(JsonStore(str(tmp_path / "data")))
    memory.save("d_sandbox", {"memory_chat": {
        "facts": [{"id": "f1", "text": "has a dog named Rufus"}],
        "preferences": ["likes dinosaurs"], "open_threads": [], "summaries": []}})

    seen = {}

    def _brain(messages):
        seen["messages"] = messages
        return "Rufus sounds wonderful! What is he like?"

    conv = module.conversations[0]
    app = ContentApp(module, _brain, memory=memory)
    resp = drive_once(app, "guess what happened today",
                      device_id="d_sandbox", nickname="Sam",
                      module_id=conv.module_id, content_id=conv.content_id,
                      event_id="evt-sandbox-1")
    assert_spec_response(resp, device_id="d_sandbox", event_id="evt-sandbox-1")

    system = seen["messages"][0]
    assert system["role"] == "system"
    body = system["content"]
    assert body.strip(), "the sandbox emptied the shipped prompt on the real path"
    assert "{{" not in body and "{%" not in body, f"raw Jinja reached the brain: {body[:160]!r}"
    assert "Sam" in body, "child_pii.nickname never rendered"
    assert "- has a dog named Rufus" in body, "durable memory never rendered"
    assert resp["output"]["text"] and resp["output"]["markup"]


def test_the_opener_renders_through_the_real_content_app():
    """`greeting()` renders the module's `opener` through the same sandbox, and it is
    the one render whose output a child hears *verbatim* — an emptied opener is a
    silent robot, not a degraded prompt."""
    from moxie_sdk.content import ContentApp, load_modules
    from moxie_sdk.types import ChildProfile, RobotContext

    with open(os.path.join(MODULE_DIR, "memory_chat.json")) as fh:
        module = load_modules(json.load(fh))
    conv = module.conversations[0]
    app = ContentApp(module, lambda m: "hi", memory=False)
    robot = RobotContext(device_id="d_open", child=ChildProfile(nickname="Sam"),
                         module_id=conv.module_id, content_id=conv.content_id)
    reply = app.greeting(robot)
    assert reply is not None and reply.text.strip()
    assert "Sam" in reply.text, f"opener lost the nickname: {reply.text!r}"
    assert "{{" not in reply.text
