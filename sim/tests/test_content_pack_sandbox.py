"""A hostile pack, driven through the REAL import path, executes and reads nothing.

`test_render_sandbox.py` fences the renderer; this fences the path a pack travels:

    pack JSON → parse_pack → review_pack/diff_item → apply_pack → JsonStore
              → reload_content → build_module → ContentApp → render_prompt → the brain

* Review is a read: `render.BLOCKED`/`STRIPPED` must not move across parse, review, diff,
  inventory, scan and export.
* What the brain receives is inert: import as `POST /content/import` does, take a real
  turn, read the system message. Probes walk what a pack can reach (`volley`, `session`,
  `presence`), not jinja2's globals.
* Parity: an ordinary imported pack still personalises its prompt.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

from helpers_content import boot_runtime, free_chat_pack, recording_brain  # noqa: E402
from moxie_sdk.content import packs as P              # noqa: E402
from moxie_sdk.content import render as R             # noqa: E402

jinja2 = pytest.importorskip("jinja2", reason="the sandbox only exists when jinja2 does")

#: Substrings that prove a template reached off its leash (`test_render_sandbox.py`'s list
#: plus two planted sentinels; not `sk-`-shaped, so the secret scan stays quiet).
SENTINEL_ENV = "MOXIE-PACK-SENTINEL-never-render"
SENTINEL_MEMORY = "packsentinel-remembered-fact"
LEAKS = ("posix", "nt=", "/home/", "C:\\", "<class ", "Environment", "subprocess",
         "builtins", "environ", SENTINEL_ENV, SENTINEL_MEMORY)

#: Escapes over a pack's render context: the author cannot choose the context, only every
#: attribute walked over it.
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
    # Was live once: the jinja2-less fallback `getattr`-walked this into `os.environ`.
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


# --- Fixtures — a pack built exactly the way an exporter builds one ---

NOW = 1788400000
IDENT = "conversation:FREE_CHAT/default"


def hostile_pack(prompt, *, opener="Hi!", version=2, **kw) -> dict:
    return free_chat_pack(prompt, opener=opener, version=version, now=NOW,
                          name="Totally normal bedtime pack", pack_id="totally-normal",
                          author="a stranger", **kw)


def shipped_module(prompt="You are Moxie, the shipped starter chat.") -> dict:
    return {"conversations": [{"name": "Free Chat", "module_id": "FREE_CHAT",
                               "content_id": "default", "prompt": prompt,
                               "opener": "Hi!", "source_version": 1}]}


def runtime_with(tmp_path, chat, *, defaults=None):
    return boot_runtime(tmp_path / "data", defaults or shipped_module(), chat)


def import_through_the_runtime(rt, pack):
    """Exactly what `POST /content/import` does: review, then apply what it ticked."""
    reviewed = rt.content_review(json.dumps(pack))
    return rt.content_import(json.dumps(pack), reviewed["accept"],
                             reviewed["expect_digest"])


# --- 1 · The review is a read. Looking at a pack must never evaluate it. ---

@pytest.mark.parametrize("name", sorted(ESCAPES))
def test_reviewing_a_hostile_pack_never_renders_a_single_construct(name):
    """"Look before you install" must not run anything. Both counters, because the sandbox
    refuses (`BLOCKED`) and the fallback removes (`STRIPPED`)."""
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
    """R4: a pack can still *say* something, so the review shows the whole prompt."""
    probe = ESCAPES["volley_class_globals"]
    pack = hostile_pack(probe)
    rows = P.review_pack(pack, {})
    diff = json.dumps(rows[0]["diff"])
    assert probe in diff, "the review must show the prompt exactly as it will be stored"


# --- 2 · Apply stores it as data — the same treatment `code` gets ---

def test_a_hostile_prompt_is_stored_byte_for_byte_as_inert_data(tmp_path):
    """Not scrubbed or rewritten: a mangled copy would make the review a lie."""
    probe = ESCAPES["session_mro"]
    rt, _device_id = runtime_with(tmp_path, lambda m: "ok")
    import_through_the_runtime(rt, hostile_pack(probe))

    on_disk = json.load(open(tmp_path / "data" / "fleet" / "content_items.json"))
    assert on_disk["items"][IDENT]["data"]["prompt"] == probe
    assert rt.content_items()[IDENT]["data"]["prompt"] == probe


# --- 3 · The whole path: import → turn → what the brain was actually handed ---

@pytest.mark.parametrize("name", sorted(ESCAPES))
def test_a_hostile_pack_reaches_the_brain_inert(tmp_path, name):
    """The assertion this file exists for: everything is production code except the brain,
    faked only to read the system message."""
    from helpers_runtime import drive_turn

    seen, brain = recording_brain()
    rt, device_id = runtime_with(tmp_path, brain)
    before = (R.BLOCKED, R.STRIPPED)
    applied = import_through_the_runtime(rt, hostile_pack(ESCAPES[name]))
    assert applied["applied"] == [IDENT], "the hostile pack must really have installed"

    drive_turn(rt, device_id, "hello")
    assert_inert(seen["system"], name)
    assert (R.BLOCKED, R.STRIPPED) > before, \
        f"{name} rendered without tripping either counter — a refusal nobody can see"


def test_a_hostile_opener_is_inert_in_the_line_the_child_hears(tmp_path):
    """`greeting()` speaks the rendered `opener` verbatim — no model between it and a child."""
    from moxie_sdk.types import ChildProfile, RobotContext

    probe = ESCAPES["volley_init_globals_os"]
    rt, _device_id = runtime_with(tmp_path, lambda m: "ok")
    import_through_the_runtime(rt, hostile_pack("You are Moxie.", opener=probe))

    robot = RobotContext(device_id="d_open", child=ChildProfile(nickname="Sam"),
                         module_id="FREE_CHAT", content_id="default")
    reply = rt.app.greeting(robot)
    # An inert opener renders empty (no line at all); neither shape may carry the host.
    assert_inert("" if reply is None else reply.text, "opener")


def test_a_hostile_pack_cannot_read_a_secret_this_process_holds(tmp_path, monkeypatch):
    """The process really holds an API key and a remembered fact while the turn runs."""
    from helpers_runtime import drive_turn

    monkeypatch.setenv("MOXIE_LLM_API_KEY", SENTINEL_ENV)
    seen, brain = recording_brain()
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
    """A stranger-chosen memory namespace never becomes a path (asserted, not reasoned:
    "it is only a dict key" precedes every traversal)."""
    from helpers_runtime import drive_turn

    root = tmp_path / "data"
    outside = tmp_path / "outside"
    outside.mkdir()
    traversal = "../../../../" + str(outside / "pwned")
    pack = free_chat_pack("You are Moxie.", name="traversal", pack_id="traversal", now=NOW,
                          memory={"namespace": traversal, "summarize": True})

    rt, device_id = runtime_with(tmp_path, lambda m: "ok")
    import_through_the_runtime(rt, pack)
    drive_turn(rt, device_id, "hello")

    assert list(outside.iterdir()) == [], "a pack escaped the data dir"
    written = [os.path.join(dirpath, f)
               for dirpath, _dirs, files in os.walk(str(root)) for f in files]
    assert written, "the import wrote nothing at all — the test proves nothing"
    for path in written:
        assert os.path.realpath(path).startswith(os.path.realpath(str(root)))


# --- 4 · The renderer a bare-metal install still uses ---

@pytest.fixture
def no_jinja2(monkeypatch):
    """`render_prompt` with jinja2 unimportable — a bare install with no `content` extra."""
    monkeypatch.setitem(sys.modules, "jinja2", None)
    monkeypatch.setitem(sys.modules, "jinja2.sandbox", None)
    return R.render_prompt


def test_a_private_attribute_is_refused_but_an_ordinary_one_is_not(no_jinja2):
    """Only `_`-LEADING segments are refused: `child_pii` must keep resolving."""
    from moxie_sdk.content.volley import Volley

    v = Volley(speech="hi", config={"child_pii": {"nickname": "Sam"}})
    ctx = {"volley": v, "session": None, "presence": {}}
    assert no_jinja2("Hi {{ volley.config.child_pii.nickname }}!", ctx) == "Hi Sam!"
    assert no_jinja2("{{ volley._nothing }}", ctx) == ""
    assert no_jinja2("{{ volley.config._x }}", ctx) == ""


@pytest.mark.parametrize("name", sorted(ESCAPES))
def test_a_hostile_pack_is_inert_without_jinja2_too(name, no_jinja2):
    """The fallback evaluates only bare dotted paths and removes the rest; both renderers
    must hold, since which runs depends on the install, not the pack."""
    from moxie_sdk.content.volley import Session, Volley

    pack = hostile_pack(ESCAPES[name])
    stored, _ = P.apply_pack(pack, {}, [IDENT], now=NOW + 10)
    prompt = P.module_data(stored)["conversations"][0]["prompt"]
    out = no_jinja2(prompt, {"volley": Volley(speech="hi"), "session": Session(),
                                     "presence": {"face_present": True}})
    assert_inert(out, f"{name} (no jinja2)")


# --- 5 · Parity — the sandbox must not have cost packs their reason to exist ---

def test_an_ordinary_imported_pack_still_personalises_the_prompt(tmp_path):
    """Everything above would pass if importing did nothing; this proves it did."""
    from helpers_runtime import drive_turn

    seen, brain = recording_brain("Hello!")
    rt, device_id = runtime_with(tmp_path, brain)
    import_through_the_runtime(rt, hostile_pack(
        "You are Moxie, talking to {{ volley.config.child_pii.nickname }}."
        "{% if presence.face_present %} They are right here.{% endif %}"))

    drive_turn(rt, device_id, "hello")
    assert "talking to Sam." in seen["system"], \
        f"the sandbox emptied a legitimate pack prompt: {seen['system']!r}"
    assert "{{" not in seen["system"] and "{%" not in seen["system"]


def test_a_legitimate_pack_never_trips_the_refusal_counter(tmp_path):
    """`BLOCKED` means "somebody tried"; ordinary content must never set it off."""
    from helpers_runtime import drive_turn

    rt, device_id = runtime_with(tmp_path, lambda m: "Hello!")
    import_through_the_runtime(rt, hostile_pack(
        "You are Moxie, talking to {{ volley.config.child_pii.nickname }}."))
    before = (R.BLOCKED, R.STRIPPED)
    drive_turn(rt, device_id, "hello")
    assert (R.BLOCKED, R.STRIPPED) == before
