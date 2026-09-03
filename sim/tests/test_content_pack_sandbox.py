"""A hostile pack, driven through the REAL import path, executes and reads nothing.

`test_render_sandbox.py` fences the *renderer*: eight escape probes handed straight to
`render_prompt` come back inert. That is the mechanism. This file fences the *path a pack
actually travels* — the reason the mechanism exists:

    pack JSON → parse_pack → review_pack/diff_item → apply_pack → JsonStore
              → reload_content → build_module → ContentApp → render_prompt → the brain

Every one of those stages handles a string somebody else wrote, and a fence that only
covers the last one is a fence with a gate in it. Three claims are new here and provable
nowhere else:

* **The review is a read, not an evaluation.** A parent looks at a pack *before*
  installing it, so if reviewing a file were enough to evaluate it, the whole
  import-with-review design would be inverted — the safe-looking step would be the
  dangerous one. `render.BLOCKED` and `render.STRIPPED` must not move by a single count
  across parse, review, diff, inventory, scan and export.
* **What the brain finally receives is inert.** Not "`render_prompt` is inert when called
  by a test", but: import the pack the way `POST /content/import` does, take one real turn
  through `MoxieRuntime`, and read the system message the brain was handed.
* **The probes are the ones a pack can actually reach.** `test_render_sandbox.py` uses
  jinja2's own globals (`cycler`, `joiner`, `namespace`). A content pack is rendered over
  `{"volley": …, "session": …, "presence": …}` (`content_app.py`:200), so the objects it
  can walk are *ours* — a `Volley` holding the child's profile, a `Session`, a presence
  dict. Those attribute chains are the reachable surface and they are probed here.

And the parity half, in the same spirit as `test_render_sandbox_parity.py`: an ordinary
imported pack must still personalise its prompt. A "fix" that neutered pack templating
would pass every hostile assertion in this file and be an immediate revert.

Corpus note: the hostile strings below are inert data. Nothing in this file writes them
to a shipped module, and `packs.py` is pinned as pure data handling (no renderer import,
no `eval`/`exec`/`compile`) so that stays true.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))
sys.path.insert(0, os.path.dirname(__file__))

from moxie_sdk.content import packs as P              # noqa: E402
from moxie_sdk.content import render as R             # noqa: E402

jinja2 = pytest.importorskip("jinja2", reason="the sandbox only exists when jinja2 does")

#: Substrings that prove a template reached off its own leash — the list
#: `test_render_sandbox.py` uses, plus the two sentinels this file plants itself.
#: Deliberately not `sk-`-shaped, so the repo's pre-commit secret scan stays quiet.
SENTINEL_ENV = "MOXIE-PACK-SENTINEL-never-render"
SENTINEL_MEMORY = "packsentinel-remembered-fact"
LEAKS = ("posix", "nt=", "/home/", "C:\\", "<class ", "Environment", "subprocess",
         "builtins", "environ", SENTINEL_ENV, SENTINEL_MEMORY)

#: Escapes reachable from a pack's own render context — `volley`, `session`, `presence`
#: (`content_app.py`:163,200). A pack author cannot choose the context; they can choose
#: every attribute walked over it, which is exactly what these do.
ESCAPES = {
    "volley_class_globals": "{{ volley.__class__.__init__.__globals__ }}",
    "volley_init_globals_os": "{{ volley.__init__.__globals__['os'].environ }}",
    "volley_attr_filter": "{{ volley|attr('__class__')|attr('__module__') }}",
    "session_mro": "{{ session.__class__.__mro__ }}",
    "session_dict": "{{ session.__dict__ }}",
    "presence_class_globals": "{{ presence.__class__.__init__.__globals__ }}",
    "int_subclasses": "{{ (1).__class__.__base__.__subclasses__() }}",
    "format_leak": "{{ '{0.__class__}'.format(volley) }}",
    "builtins_open": "{{ open('/etc/passwd').read() }}",
    "import_os_environ": "{{ __import__('os').environ }}",
    "lipsum_globals": "{{ lipsum.__globals__['os'].environ }}",
    # The one that was live. See `test_the_dotted_path_walk_cannot_reach_the_environment`.
    "globals_walk_to_environ":
        "{{ session.__class__.__repr__.__globals__.inspect.os.environ }}",
}


def assert_inert(out, label=""):
    """Nothing that names the host, and short — a subclass walk is enormous."""
    assert isinstance(out, str), f"{label}: render returned {type(out)!r}"
    low = out.lower()
    for leak in LEAKS:
        assert leak.lower() not in low, f"{label} leaked {leak!r}: {out[:200]!r}"
    assert "{{" not in out and "{%" not in out, \
        f"{label} left template syntax in the prompt the brain receives: {out[:200]!r}"
    assert len(out) < 400, f"{label} returned {len(out)} chars: {out[:200]!r}"


# --------------------------------------------------------------------------- #
# Fixtures — a pack built exactly the way an exporter builds one
# --------------------------------------------------------------------------- #

NOW = 1788400000
IDENT = "conversation:FREE_CHAT/default"


def hostile_pack(prompt, *, opener="Hi!", version=2, **kw) -> dict:
    item = {"kind": "conversation", "key": "FREE_CHAT/default",
            "source_version": version,
            "data": dict({"name": "Free Chat", "module_id": "FREE_CHAT",
                          "content_id": "default", "prompt": prompt,
                          "opener": opener}, **kw)}
    return P.export_pack([item], name="Totally normal bedtime pack",
                         pack_id="totally-normal", author="a stranger", now=NOW)


def shipped_module(prompt="You are Moxie, the shipped starter chat.") -> dict:
    return {"conversations": [{"name": "Free Chat", "module_id": "FREE_CHAT",
                               "content_id": "default", "prompt": prompt,
                               "opener": "Hi!", "source_version": 1}]}


def runtime_with(tmp_path, chat, *, defaults=None):
    """A real `MoxieRuntime` over a real `ContentApp`, booted the way
    `config.build_content_app()` boots: shipped defaults, then the overlay on disk."""
    from helpers_runtime import make_runtime
    from moxie_sdk.content import ContentApp
    from moxie_sdk.store import JsonStore

    store = JsonStore(str(tmp_path / "data"))
    shipped = P.shipped_items(defaults or shipped_module())
    stored = store.read_shared("content_items", {}) or {}
    overlay = stored.get("items") if isinstance(stored, dict) else {}
    app = ContentApp(P.build_module(shipped, overlay if isinstance(overlay, dict) else {}),
                     chat, memory=False, content_defaults=shipped)
    return make_runtime(app, store=store)


def import_through_the_runtime(rt, pack):
    """Exactly what `POST /content/import` does: review, then apply what it ticked."""
    reviewed = rt.content_review(json.dumps(pack))
    return rt.content_import(json.dumps(pack), reviewed["accept"],
                             reviewed["expect_digest"])


# --------------------------------------------------------------------------- #
# 1 · The review is a read. Looking at a pack must never evaluate it.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", sorted(ESCAPES))
def test_reviewing_a_hostile_pack_never_renders_a_single_construct(name):
    """The step whose whole purpose is "look before you install" must not be the step
    that runs it. Both counters are checked because the two renderers fail differently:
    the sandbox refuses an attribute (`BLOCKED`), the fallback removes a construct
    (`STRIPPED`), and either moving here would mean something rendered."""
    pack = hostile_pack(ESCAPES[name], opener=ESCAPES[name])
    before = (R.BLOCKED, R.STRIPPED)

    parsed, meta = P.parse_pack(P.dumps_pack(pack))
    assert meta["digest"] == "ok"
    rows = P.review_pack(parsed, {}, digest=meta["digest"])
    P.diff_item(None, parsed["items"][0]["data"])
    installed, _ = P.apply_pack(parsed, {}, [IDENT], now=NOW + 10)
    P.inventory(installed, known_names=("Sam",))
    P.scan_outgoing(installed, ("Sam",))
    P.dumps_pack(P.export_pack(installed, name="re-export", pack_id="re-export",
                               now=NOW + 20))

    assert (R.BLOCKED, R.STRIPPED) == before, \
        f"{name}: the pack pipeline rendered something on its own"
    assert rows[0]["state"] == P.NEW


def test_the_review_shows_the_hostile_prompt_verbatim():
    """R4's answer: a pack that cannot execute can still *say* something, so the review
    shows the whole prompt and never a summary. Truncating or sanitising it here would
    hide the one thing a parent has to read."""
    probe = ESCAPES["volley_class_globals"]
    pack = hostile_pack(probe)
    rows = P.review_pack(pack, {})
    diff = json.dumps(rows[0]["diff"])
    assert probe in diff, "the review must show the prompt exactly as it will be stored"


def test_packs_py_handles_data_and_never_renders_it():
    """Pin the mechanism, not just the symptom (the pattern
    `test_render_sandbox.py::test_the_renderer_uses_the_sandboxed_environment` sets).
    A future refactor that reached for the renderer — to preview a prompt in the review,
    say — would move the evaluation into the step this design promises is inert."""
    src = open(os.path.join(REPO, "mqtt", "moxie_sdk", "content", "packs.py")).read()
    code = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith(("#", '"', "'", "*", ":")))
    # `re.compile` is the one legitimate compile here — a pack's `pattern` is validated
    # by compiling it (`validate_item`), which is why a bad regex is refused at review.
    code = code.replace("re.compile(", "")
    for forbidden in ("render_prompt", "jinja2", "eval(", "exec(", "compile(",
                      "__import__", "importlib", "subprocess", "os.system"):
        assert forbidden not in code, f"packs.py must not reach for {forbidden!r}"


# --------------------------------------------------------------------------- #
# 2 · Apply stores it as data — the same treatment `code` gets
# --------------------------------------------------------------------------- #

def test_a_hostile_prompt_is_stored_byte_for_byte_as_inert_data(tmp_path):
    """It is not scrubbed, escaped or rewritten on the way in. Storing a mangled version
    would make the review a lie (what you approved is not what runs) and would be a much
    worse guarantee than "it cannot do anything anyway"."""
    probe = ESCAPES["session_mro"]
    rt, _device_id = runtime_with(tmp_path, lambda m: "ok")
    import_through_the_runtime(rt, hostile_pack(probe))

    on_disk = json.load(open(tmp_path / "data" / "fleet" / "content_items.json"))
    assert on_disk["items"][IDENT]["data"]["prompt"] == probe
    assert rt.content_items()[IDENT]["data"]["prompt"] == probe


# --------------------------------------------------------------------------- #
# 3 · The whole path: import → turn → what the brain was actually handed
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", sorted(ESCAPES))
def test_a_hostile_pack_reaches_the_brain_inert(tmp_path, name):
    """The assertion this file exists for. Real runtime, real `ContentApp`, real store,
    real import verb; the brain is fake **only** so the test can read the system message
    it was given. Everything upstream of it is production code."""
    from helpers_runtime import drive_turn

    seen = {}

    def brain(messages):
        seen["system"] = messages[0]["content"]
        return "Sure!"

    rt, device_id = runtime_with(tmp_path, brain)
    before = (R.BLOCKED, R.STRIPPED)
    applied = import_through_the_runtime(rt, hostile_pack(ESCAPES[name]))
    assert applied["applied"] == [IDENT], "the hostile pack must really have installed"

    drive_turn(rt, device_id, "hello")
    assert_inert(seen["system"], name)
    assert (R.BLOCKED, R.STRIPPED) > before, \
        f"{name} rendered without tripping either counter — a refusal nobody can see"


def test_a_hostile_opener_is_inert_in_the_line_the_child_hears(tmp_path):
    """`greeting()` renders `opener` through the same sandbox, and that output is spoken
    verbatim (`content_app.py`:163) — the one render with no model between it and a
    child."""
    from moxie_sdk.types import ChildProfile, RobotContext

    probe = ESCAPES["volley_init_globals_os"]
    rt, _device_id = runtime_with(tmp_path, lambda m: "ok")
    import_through_the_runtime(rt, hostile_pack("You are Moxie.", opener=probe))

    robot = RobotContext(device_id="d_open", child=ChildProfile(nickname="Sam"),
                         module_id="FREE_CHAT", content_id="default")
    reply = rt.app.greeting(robot)
    # An inert opener renders empty, and an empty opener is *no line at all* rather than
    # a blank utterance — either shape is safe, neither may carry the host.
    assert_inert("" if reply is None else reply.text, "opener")


def test_a_hostile_pack_cannot_read_a_secret_this_process_holds(tmp_path, monkeypatch):
    """The concrete version of "reads nothing it shouldn't": the process really is
    holding an API key and a remembered fact about a child while the turn runs."""
    from helpers_runtime import drive_turn

    monkeypatch.setenv("MOXIE_LLM_API_KEY", SENTINEL_ENV)
    seen = {}

    def brain(messages):
        seen["system"] = messages[0]["content"]
        return "Sure!"

    rt, device_id = runtime_with(tmp_path, brain)
    rt.store.write("d_test", "memory", {"free_chat": {
        "facts": [{"id": "f1", "text": SENTINEL_MEMORY}]}})
    probe = ("{{ volley.__init__.__globals__['os'].environ }}"
             "{{ volley.__class__.__init__.__globals__ }}"
             "{{ session.__dict__ }}")
    import_through_the_runtime(rt, hostile_pack(probe))

    drive_turn(rt, device_id, "hello")
    assert_inert(seen["system"], "secrets")
    assert os.environ["MOXIE_LLM_API_KEY"] == SENTINEL_ENV, "the key really was set"


def test_a_hostile_pack_writes_no_file_outside_the_data_dir(tmp_path):
    """Item keys and a memory namespace are strings a stranger chose. They index dicts
    and (via `store.safe_name`) never a path — asserted rather than reasoned about,
    because "it is only a dict key" is exactly the sentence that precedes a traversal."""
    from helpers_runtime import drive_turn

    root = tmp_path / "data"
    outside = tmp_path / "outside"
    outside.mkdir()
    traversal = "../../../../" + str(outside / "pwned")
    item = {"kind": "conversation", "key": "FREE_CHAT/default", "source_version": 2,
            "data": {"name": "Free Chat", "module_id": "FREE_CHAT",
                     "content_id": "default", "prompt": "You are Moxie.",
                     "opener": "Hi!",
                     "memory": {"namespace": traversal, "summarize": True}}}
    pack = P.export_pack([item], name="traversal", pack_id="traversal", now=NOW)

    rt, device_id = runtime_with(tmp_path, lambda m: "ok")
    import_through_the_runtime(rt, pack)
    drive_turn(rt, device_id, "hello")

    assert list(outside.iterdir()) == [], "a pack escaped the data dir"
    written = [os.path.join(dirpath, f)
               for dirpath, _dirs, files in os.walk(str(root)) for f in files]
    assert written, "the import wrote nothing at all — the test proves nothing"
    for path in written:
        assert os.path.realpath(path).startswith(os.path.realpath(str(root)))


# --------------------------------------------------------------------------- #
# 4 · The renderer a bare-metal install still uses
# --------------------------------------------------------------------------- #

def _no_jinja2_render(template: str, context: dict) -> str:
    """`render_prompt` with jinja2 made unimportable — the code path a bare
    `pip install moxie-cloud-sdk` (no `content` extra) takes. Blocks the import rather
    than uninstalling anything, so this holds in a full-fat venv too; the technique is
    `test_render_sandbox_parity.py`'s."""
    import builtins
    real_import = builtins.__import__

    def _blocked(name, *a, **kw):
        if name == "jinja2" or name.startswith("jinja2."):
            raise ImportError("blocked: simulating an install with no content extra")
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


def test_the_dotted_path_walk_cannot_reach_the_environment(monkeypatch):
    """**The hole this file found, and the fence on it.**

    `_minimal_render` evaluates a *bare dotted path* — that is its whole grammar — and it
    evaluated it with `getattr` over the live context objects. A pack's `prompt` chooses
    every segment of that path, so the grammar was an attribute-chain escape:

        {{ session.__class__.__repr__.__globals__.inspect.os.environ }}

    rendered `environ({…})` — 4.9 KB of this process's environment, `MOXIE_LLM_API_KEY`
    included — into the system prompt handed to the brain. Measured on this tree before
    the fix, not theorised.

    Reach, stated honestly: the *container* was never exposed, because
    `mqtt/requirements.txt` ships jinja2 and `SandboxedEnvironment` already refuses
    underscore-leading attributes. The exposed shape is an install without the `content`
    extra — the fallback `pyproject.toml` deliberately keeps supported — where a pack a
    parent imported could exfiltrate the appliance's own API key by asking the model to
    repeat its instructions. `render.py::_resolve` now refuses any `_`-leading segment and
    counts it in `BLOCKED`.
    """
    from moxie_sdk.content.volley import Session, Volley

    monkeypatch.setenv("MOXIE_LLM_API_KEY", SENTINEL_ENV)
    ctx = {"volley": Volley(speech="hi"), "session": Session(), "presence": {}}
    probe = ESCAPES["globals_walk_to_environ"]
    assert os.environ["MOXIE_LLM_API_KEY"] == SENTINEL_ENV, "the key really was set"

    before = R.BLOCKED
    out = _no_jinja2_render("Instructions: " + probe, ctx)
    assert SENTINEL_ENV not in out, f"the environment leaked into the prompt: {out[:200]!r}"
    assert out == "Instructions: "
    assert R.BLOCKED > before, "a refusal nobody can count is a refusal nobody will notice"

    # And the sandbox path, which was never exposed, still is not.
    assert SENTINEL_ENV not in R.render_prompt(probe, ctx)


def test_a_private_attribute_is_refused_but_an_ordinary_one_is_not():
    """The guard is `_`-leading segments, and nothing wider. `content-module-contract.md`
    documents `{{ volley.config.child_pii.nickname }}`; `child_pii` merely *contains* an
    underscore and must keep resolving, or the fix would have broken every shipped
    prompt."""
    from moxie_sdk.content.volley import Volley

    v = Volley(speech="hi", config={"child_pii": {"nickname": "Sam"}})
    ctx = {"volley": v, "session": None, "presence": {}}
    assert _no_jinja2_render("Hi {{ volley.config.child_pii.nickname }}!", ctx) == "Hi Sam!"
    assert _no_jinja2_render("{{ volley._nothing }}", ctx) == ""
    assert _no_jinja2_render("{{ volley.config._x }}", ctx) == ""


@pytest.mark.parametrize("name", sorted(ESCAPES))
def test_a_hostile_pack_is_inert_without_jinja2_too(name):
    """No sandbox there at all — the fallback's guarantee is different in kind: it
    *evaluates* only a bare dotted path and removes everything else, so an escape has
    nothing to walk. Both renderers must hold, because which one runs depends on how the
    appliance was installed, not on what the pack says."""
    from moxie_sdk.content.volley import Session, Volley

    pack = hostile_pack(ESCAPES[name])
    stored, _ = P.apply_pack(pack, {}, [IDENT], now=NOW + 10)
    prompt = P.module_data(stored)["conversations"][0]["prompt"]
    out = _no_jinja2_render(prompt, {"volley": Volley(speech="hi"), "session": Session(),
                                     "presence": {"face_present": True}})
    assert_inert(out, f"{name} (no jinja2)")


# --------------------------------------------------------------------------- #
# 5 · Parity — the sandbox must not have cost packs their reason to exist
# --------------------------------------------------------------------------- #

def test_an_ordinary_imported_pack_still_personalises_the_prompt(tmp_path):
    """Every assertion above would also pass if importing a pack simply did nothing.
    This is the test that says it did something: a stranger's pack, imported, greets the
    child by name on the very next turn."""
    from helpers_runtime import drive_turn

    seen = {}

    def brain(messages):
        seen["system"] = messages[0]["content"]
        return "Hello!"

    rt, device_id = runtime_with(tmp_path, brain)
    import_through_the_runtime(rt, hostile_pack(
        "You are Moxie, talking to {{ volley.config.child_pii.nickname }}."
        "{% if presence.face_present %} They are right here.{% endif %}"))

    drive_turn(rt, device_id, "hello")
    assert "talking to Sam." in seen["system"], \
        f"the sandbox emptied a legitimate pack prompt: {seen['system']!r}"
    assert "{{" not in seen["system"] and "{%" not in seen["system"]


def test_a_legitimate_pack_never_trips_the_refusal_counter(tmp_path):
    """`BLOCKED` is the alarm that says "somebody tried". An alarm that ordinary content
    sets off is an alarm nobody reads."""
    from helpers_runtime import drive_turn

    rt, device_id = runtime_with(tmp_path, lambda m: "Hello!")
    import_through_the_runtime(rt, hostile_pack(
        "You are Moxie, talking to {{ volley.config.child_pii.nickname }}."))
    before = (R.BLOCKED, R.STRIPPED)
    drive_turn(rt, device_id, "hello")
    assert (R.BLOCKED, R.STRIPPED) == before
