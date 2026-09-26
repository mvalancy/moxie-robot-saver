"""
✍️ Content authoring P0 (`backlog/content-authoring.md` §7, minus P1's `/content/try`):
a parent's conversation is exactly as untrusted as a stranger's.

An authored item goes through the SAME functions an imported one does. `POST /content/item`
must call `packs.validate_item` itself (§6.3) because `mark_edited` only normalizes;
`sim/tools/authoring_mutation_check.py` deletes that and other guards and needs a test here
to go red. Real `MoxieRuntime` + status HTTP server (`helpers_runtime`); `build()`'s brain
raises if called, which is how T10 proves the render panel is free.
"""
from __future__ import annotations

import os

import pytest

from helpers_console import console_js
from helpers_content import boot_runtime, post_status

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

from moxie_sdk.content import packs as P                            # noqa: E402
from moxie_sdk.content import render as R                           # noqa: E402

try:                                            # the runtime's transport
    import paho.mqtt.client                     # noqa: F401
    HAVE_PAHO = True
except Exception:                               # pragma: no cover - tier-dependent
    HAVE_PAHO = False

needs_runtime = pytest.mark.skipif(not HAVE_PAHO,
                                   reason="the runtime's transport (paho) is not installed")


# --- Fixtures — a real supervisor over a shipped module, and a brain that must not run ---

SHIPPED_PROMPT = "You are Moxie, the shipped starter chat."
SHIPPED_CODE = "def post_process(volley, session):\n    raise RuntimeError('never run')\n"
SHIPPED_EXT = {"ext_format": 1, "capabilities": ["say"], "on": "turn.before",
               "rules": [{"do": [{"say": "Nice to see you."}]}]}


def shipped_module():
    """`FREE_CHAT/default` carries `code` and an `extension`: T4/T16 test what a save does
    to fields the editor may not author."""
    return {
        "conversations": [
            {"name": "Free Chat", "module_id": "FREE_CHAT", "content_id": "default",
             "prompt": SHIPPED_PROMPT, "opener": "Hi!", "source_version": 1,
             "code": SHIPPED_CODE, "extension": SHIPPED_EXT},
        ],
        "globals": [
            {"name": "Time", "pattern": r"(what time is it|what's the time)",
             "entity_groups": "1"},
        ],
        "schedules": [],
    }


def no_brain(messages):
    """The brain this suite refuses to have. P0 makes no model call anywhere."""
    raise AssertionError("a P0 authoring route called the brain; that is P1's rung 3")


def build(tmp_path, chat=no_brain):
    return boot_runtime(tmp_path, shipped_module(), chat)[0]


@pytest.fixture
def rt(tmp_path):
    return build(tmp_path)


@pytest.fixture
def base(rt):
    """The runtime's REAL status HTTP server on a free port."""
    from helpers_runtime import status_server
    return status_server(rt)


def post(base, path, body, *, expect=200):
    """`(status, payload)`, with `expect` asserted so a 200 is never mistaken for the 400
    under test."""
    status, payload = post_status(base, path, body)
    assert status == expect, f"{path} → {status} {payload!r}"
    return status, payload


def get(base, path):
    from helpers_runtime import http_json
    return http_json(base + path)


def conversation(**over):
    """A draft a parent could plausibly have typed in the guided surface."""
    data = {"name": "Bedtime wind-down", "module_id": "BEDTIME", "content_id": "default",
            "prompt": "You are Moxie at bedtime. Talk to {{ volley.config.child_pii.nickname }}"
                      " about their day.",
            "opener": "Ready to wind down?"}
    data.update(over)
    return data


# --- T1 — the round trip ---

@needs_runtime
def test_authored_item_round_trips(rt, base):
    """A save shows up as a local edit, and the module the next turn renders from carries
    the author's prompt byte for byte."""
    draft = conversation()
    _, out = post(base, "/content/item", {"kind": "conversation", "data": draft})
    assert out["ok"] and out["created"] is True, out
    assert out["id"] == "conversation:BEDTIME/default", out

    view = get(base, "/content")
    row = next((r for r in view["items"] if r["id"] == "conversation:BEDTIME/default"), None)
    assert row is not None, view["items"]
    assert row["origin"] == "local", row
    assert row["local_edited"] is True, row
    assert row["name"] == "Bedtime wind-down", row

    module = P.build_module(rt._content_defaults(), rt._content_overlay())
    conv = next((c for c in module.conversations if c.module_id == "BEDTIME"), None)
    assert conv is not None, [c.module_id for c in module.conversations]
    assert conv.prompt == draft["prompt"], (conv.prompt, draft["prompt"])
    assert conv.opener == draft["opener"]

    # The live app was swapped, not just the file (the checker deletes `reload_content()`).
    live = next((c for c in rt.app.module.conversations if c.module_id == "BEDTIME"), None)
    assert live is not None and live.prompt == draft["prompt"], \
        "the save wrote the overlay but never reloaded the live module"


# --- T2 — §6.3's one `if` ---

@needs_runtime
def test_a_bad_pattern_is_refused_with_validate_items_own_sentence(base):
    """§6.3: the route calls `validate_item` itself, else an uncompilable `pattern` breaks
    the next `reload_content()`. The refusal is its own string, not a second validator."""
    bad = {"name": "Broken", "pattern": "what time is it("}
    _, out = post(base, "/content/item", {"kind": "global", "data": bad}, expect=400)
    assert out["ok"] is False, out

    expected = P.validate_item({"kind": "global", "key": "Broken", "data": bad,
                                "source_version": 1})
    assert expected, "validate_item did not refuse the pattern this test is built on"
    assert expected[0] in (out.get("error") or ""), (expected, out)
    assert "pattern does not compile" in expected[0], expected

    # And nothing landed.
    view = get(base, "/content")
    assert not [r for r in view["items"] if r["id"] == "global:Broken"], view["items"]


# --- T3 — the allowlist ---

@needs_runtime
def test_a_field_outside_the_allowlist_never_lands(rt, base):
    """G1: stored `data` has exactly `FIELDS[kind]`; a hand-rolled POST smuggles nothing."""
    draft = dict(conversation(), secret="not-a-field-this-appliance-has", code_exec=True)
    _, out = post(base, "/content/item", {"kind": "conversation", "data": draft})
    assert out["ok"], out

    stored = rt._content_overlay()["conversation:BEDTIME/default"]["data"]
    assert set(stored) == set(P.FIELDS["conversation"]), sorted(stored)
    assert "secret" not in stored and "code_exec" not in stored


# --- T4 — what a save must not lose ---

@needs_runtime
def test_saving_a_name_change_preserves_code_and_extension(rt, base):
    """§4.2: renaming a shipped item keeps its `code` and extension byte-identical."""
    view = get(base, "/content")
    assert any(r["id"] == "conversation:FREE_CHAT/default" and r["has_code"]
               for r in view["items"]), view["items"]

    module = P.build_module(rt._content_defaults(), {})
    shipped = next(c for c in module.conversations if c.module_id == "FREE_CHAT")
    data = {f: getattr(shipped, f) for f in P.FIELDS["conversation"]}
    assert data["code"] == SHIPPED_CODE and data["extension"] == SHIPPED_EXT, data

    data["name"] = "Free Chat (ours)"
    _, out = post(base, "/content/item", {"kind": "conversation", "data": data})
    assert out["ok"] and out["created"] is False, out

    stored = rt._content_overlay()["conversation:FREE_CHAT/default"]["data"]
    assert stored["name"] == "Free Chat (ours)"
    assert stored["code"] == SHIPPED_CODE, "the save dropped the code block"
    assert P.canonical(stored["extension"]) == P.canonical(SHIPPED_EXT), \
        "the save altered the extension"


# --- T5 — provenance, for free ---

@needs_runtime
def test_authored_then_imported_reports_conflict(base):
    """An authored item is `local_edited`, so a stranger's newer pack with the same key is
    CONFLICT, un-ticked (A3) with no change to `review_pack`."""
    mine = {"name": "Time", "pattern": "(what o'?clock|what time is it)",
            "entity_groups": "1"}
    _, saved = post(base, "/content/item", {"kind": "global", "data": mine})
    assert saved["ok"], saved

    pack = P.export_pack(
        [{"kind": "global", "key": "Time", "source_version": 7,
          "data": {"name": "Time", "pattern": "(the time|what time)", "entity_groups": "1"}}],
        name="Stranger's commands", pack_id="stranger", now=1788400000)
    _, review = post(base, "/content/review", pack)
    row = next(r for r in review["items"] if r["id"] == "global:Time")
    assert row["state"] == P.CONFLICT, row
    assert row["local_edited"] is True, row
    assert row["default"] is False, row
    assert "global:Time" not in review["accept"], review["accept"]


# --- T10 / T11 — rung 1, the free feedback ---

@needs_runtime
def test_render_route_calls_no_brain(base):
    """Rung 1 costs zero brain calls (`build()`'s brain raises) yet returns the resolved
    prompt with the sample nickname."""
    draft = conversation(prompt="Hello {{ volley.config.child_pii.nickname }}, "
                                "{% if presence.face_present %}you are here.{% endif %}")
    _, out = post(base, "/content/render",
                  {"kind": "conversation", "data": draft,
                   "context": {"nickname": "Ada", "face_present": True}})
    assert out["ok"], out
    assert "Ada" in out["prompt"], out["prompt"]
    assert "you are here." in out["prompt"], out["prompt"]
    assert "{{" not in out["prompt"] and "{%" not in out["prompt"], out["prompt"]
    assert out["context"]["nickname"] == "Ada"


@needs_runtime
def test_render_reports_stripped_for_a_construct_the_fallback_drops(base):
    """§4.3: a `{% for %}` (jinja2-only) shows non-zero `stripped` and
    `portable_identical: false`; a portable prompt (the control) shows zero."""
    portable = conversation(prompt="Hi {{ volley.config.child_pii.nickname }}.")
    _, clean = post(base, "/content/render", {"kind": "conversation", "data": portable})
    assert clean["ok"] and clean["counts"]["stripped"] == 0, clean
    assert clean["portable_identical"] is True, clean

    richer = conversation(memory={"namespace": "bedtime"},
                          prompt="Facts:{% for f in volley.persist_data.bedtime.facts %}"
                                 " {{ f }}{% endfor %}")
    _, out = post(base, "/content/render", {"kind": "conversation", "data": richer})
    assert out["ok"], out
    assert out["counts"]["stripped"] > clean["counts"]["stripped"], (out, clean)
    assert out["portable_identical"] is False, out
    assert out["counts_advisory"] is True, "the process-global counters are advisory (§5.1)"


def test_render_prompt_hands_a_caller_its_own_counts():
    """`counts`, both directions: a dropped construct moves `stripped`, a rendered one not."""
    counts = {}
    R._minimal_render("{{ volley.config.child_pii.nickname }}", {"volley": None})
    text = R.render_prompt("{{ x.y }}", {"x": {"y": "ok"}}, counts=counts)
    assert text == "ok"
    assert counts == {"blocked": 0, "stripped": 0}, counts

    counts2 = {}
    R._minimal_render("{% for a in b %}{{ a }}{% endfor %}", {}, counts=counts2)
    assert counts2["stripped"] >= 1, counts2


# --- T12 / T13 — the shadow rule (§4.4) ---

@needs_runtime
def test_shadow_warning_names_the_earlier_command(base):
    """Globals fire first-match in name order, invisibly; authoring *When is it* behind
    *Time* must name Time and say commands are tried in name order."""
    draft = {"name": "When is it", "pattern": "(what time is it)", "entity_groups": ""}
    _, out = post(base, "/content/item",
                  {"kind": "global", "data": draft,
                   "phrases": ["what time is it", "moxie what time is it"]})
    assert out["ok"], out
    shadow = out["shadow"]
    assert shadow, "no shadow warning for a phrase an earlier command answers"
    assert any(s["name"] == "Time" for s in shadow), shadow
    sentence = " ".join(s["sentence"] for s in shadow)
    assert "Time" in sentence and "name order" in sentence, sentence
    assert "what time is it" in sentence, sentence


@needs_runtime
def test_no_shadow_warning_when_nothing_shadows(base):
    """T12's vacuity guard: a shadowed phrase warns (positive control), a disjoint one
    does not — so the test cannot pass by silence."""
    draft = {"name": "When is it", "pattern": "(tell me a joke)", "entity_groups": ""}
    _, control = post(base, "/content/item",
                      {"kind": "global", "data": draft,
                       "phrases": ["what time is it"]})
    assert control["shadow"], "the probe cannot see a shadow at all"

    _, out = post(base, "/content/item",
                  {"kind": "global", "data": draft,
                   "phrases": ["tell me a joke", "say something funny"]})
    assert out["ok"], out
    assert out["shadow"] == [], out["shadow"]


def test_shadow_check_is_exact_for_the_phrases_and_claims_nothing_more():
    """A5: only an installed global that sorts EARLIER and matches a typed phrase shadows."""
    installed = {
        "global:Time": {"kind": "global", "key": "Time",
                        "data": {"name": "Time", "pattern": "(what time is it)"}},
        "global:Zebra": {"kind": "global", "key": "Zebra",
                         "data": {"name": "Zebra", "pattern": "(what time is it)"}},
    }
    draft = {"name": "When is it", "pattern": "(what time is it)"}
    rows = P.shadow_check(draft, installed, ["what time is it"])
    assert [r["name"] for r in rows] == ["Time"], rows

    # An earlier name that does NOT match the phrase is not a shadow.
    assert P.shadow_check(draft, installed, ["sing me a song"]) == []
    # And a draft that sorts first is shadowed by nobody.
    assert P.shadow_check({"name": "Aardvark", "pattern": "(what time is it)"},
                          installed, ["what time is it"]) == []


def test_shadow_check_never_reports_the_item_against_itself():
    """Re-saving an installed command must not warn that it shadows itself."""
    installed = {"global:Time": {"kind": "global", "key": "Time",
                                 "data": {"name": "Time", "pattern": "(what time is it)"}}}
    assert P.shadow_check({"name": "Time", "pattern": "(what time is it|clock)"},
                          installed, ["what time is it"]) == []


# --- T14 — undo, unchanged ---

@needs_runtime
def test_undo_restores_an_authored_save(rt, base):
    """A save snapshots like an import, so undo removes an authored item and leaves the
    pack ledger alone (a save is not an import)."""
    ledger_before = rt._content_packs()
    _, out = post(base, "/content/item", {"kind": "conversation", "data": conversation()})
    assert out["ok"] and out["undo_available"] is True, out
    assert "conversation:BEDTIME/default" in rt._content_overlay()

    _, undone = post(base, "/content/undo", {})
    assert undone["ok"], undone
    assert "conversation:BEDTIME/default" not in rt._content_overlay()
    assert rt._content_packs() == ledger_before, rt._content_packs()
    assert not [c for c in rt.app.module.conversations if c.module_id == "BEDTIME"], \
        "undo restored the file but not the live module"


@needs_runtime
def test_the_undo_slot_holds_one_save_and_the_route_says_so(rt, base):
    """§3.3: no history — two saves and one undo return the PREVIOUS save."""
    post(base, "/content/item", {"kind": "conversation", "data": conversation()})
    post(base, "/content/item",
         {"kind": "conversation", "data": conversation(prompt="Second version.")})
    stored = rt._content_overlay()["conversation:BEDTIME/default"]["data"]
    assert stored["prompt"] == "Second version."

    _, undone = post(base, "/content/undo", {})
    assert undone["ok"], undone
    back = rt._content_overlay()["conversation:BEDTIME/default"]["data"]
    assert back["prompt"] == conversation()["prompt"], \
        "undo did not return the previous save"
    assert "conversation:BEDTIME/default" in rt._content_overlay(), \
        "one slot means the FIRST save survives a single undo"


# --- T15 / T16 — what the editor refuses ---

@needs_runtime
def test_schedule_is_refused_by_the_editor_route(rt, base):
    """§0, at the route: no real Moxie has been served an authored `ContentSchedule`, so
    the editor refuses one and says why."""
    _, out = post(base, "/content/item",
                  {"kind": "schedule", "data": {"name": "Morning", "schedule": {}}},
                  expect=400)
    assert out["ok"] is False, out
    text = (out.get("reason") or "") + " " + (out.get("error") or "")
    assert "schedule" in text.lower(), text
    assert "robot" in text.lower(), f"the refusal must say WHY, not just no: {text}"
    assert "schedule:Morning" not in rt._content_overlay()


@needs_runtime
def test_extension_and_code_are_not_writable(rt, base):
    """§4.5: a save that CHANGES `extension` or `code` is refused, pointing at the
    extensions brief (no second compiler in this card)."""
    module = P.build_module(rt._content_defaults(), {})
    shipped = next(c for c in module.conversations if c.module_id == "FREE_CHAT")
    data = {f: getattr(shipped, f) for f in P.FIELDS["conversation"]}

    changed = dict(data, extension={"ext_format": 1, "capabilities": ["say"],
                                    "on": "turn.before",
                                    "rules": [{"do": [{"say": "mine now"}]}]})
    _, out = post(base, "/content/item", {"kind": "conversation", "data": changed},
                  expect=400)
    assert "extension" in (out.get("error") or "").lower(), out
    assert "sandboxed-extensions" in (out.get("reason") or "") + (out.get("error") or ""), out

    changed_code = dict(data, code="def post_process(v, s):\n    return 1\n")
    _, out2 = post(base, "/content/item", {"kind": "conversation", "data": changed_code},
                   expect=400)
    assert "code" in (out2.get("error") or "").lower(), out2

    # Neither refusal wrote anything, and the shipped item is untouched.
    assert "conversation:FREE_CHAT/default" not in rt._content_overlay()

    # Control: the same payload with both untouched saves — the refusal is about change.
    _, ok = post(base, "/content/item",
                 {"kind": "conversation", "data": dict(data, name="Renamed")})
    assert ok["ok"], ok


@needs_runtime
def test_a_second_tab_cannot_silently_discard_the_first(rt, base):
    """R7: a save carrying a stale `local_rev` is a 409 and writes nothing."""
    post(base, "/content/item", {"kind": "conversation", "data": conversation()})
    entry = rt._content_overlay()["conversation:BEDTIME/default"]
    stale = P.local_rev({"kind": "conversation", "data": conversation(prompt="older")})
    assert stale != P.local_rev(entry), "the probe's stale revision is not actually stale"

    _, out = post(base, "/content/item",
                  {"kind": "conversation", "data": conversation(prompt="tab two"),
                   "local_rev": stale}, expect=409)
    assert out["ok"] is False and out.get("conflict") is True, out
    kept = rt._content_overlay()["conversation:BEDTIME/default"]["data"]
    assert kept["prompt"] == conversation()["prompt"], "the 409 wrote anyway"


# --- T17 — the routes are declared where the console says they are ---

def _asset(name):
    with open(os.path.join(REPO, "server", "static", name)) as fh:
        return fh.read()


def test_the_authoring_routes_are_declared():
    """Route decorators pinned as source strings (the hermetic tier has no fastapi)."""
    from helpers_console import server_source
    main = server_source()
    assert '.post("/local/content/item")' in main
    assert '.post("/local/content/render")' in main
    assert "normalize_content_item_result" in main, \
        "the item route does not normalize its answer, so a card could 500 on a refusal"
    # P0 must not ship the paid rung; matched as a route literal so prose does not trip it.
    assert '.post("/local/content/try")' not in main, "`/content/try` is P1 (§9), not P0"

    from helpers_runtime import runtime_source
    runtime = runtime_source()
    assert '"/content/item"' in runtime and '"/content/render"' in runtime
    assert '"/content/try"' not in runtime, "`/content/try` is P1 (§9), not P0"


def test_the_supervisor_route_owns_the_validation_not_the_proxy():
    """R6: `validate_item` lives in the supervisor route that writes, not the console proxy
    a direct `curl` would bypass."""
    from helpers_runtime import runtime_source
    runtime = runtime_source()
    from helpers_console import server_source
    main = server_source()
    assert "content_packs.validate_item(" in runtime, \
        "the supervisor's writing route does not call validate_item at all (§6.3)"
    assert "validate_item(" not in main, \
        "the console proxy validates; a direct curl at the supervisor would skip it"


def test_the_chip_list_is_closed_to_the_two_portable_forms():
    """AC10: every chip fragment renders under the dependency-free fallback with
    `STRIPPED` unmoved, so a guided prompt is portable by construction."""
    import re
    js = console_js()
    m = re.search(r"const ED_CHIPS\s*=\s*\[(.*?)\n\];", js, re.S)
    assert m, "the console JS has no ED_CHIPS table"
    fragments = re.findall(r"insert:\s*'((?:[^'\\]|\\.)*)'", m.group(1))
    assert len(fragments) >= 4, fragments
    ctx = {"volley": {"config": {"child_pii": {"nickname": "Ada"}},
                      "persist_data": {"ns": {"facts": []}}},
           "session": {"overflow": False}, "presence": {"face_present": True}}
    for fragment in fragments:
        text = fragment.replace("\\'", "'").replace("<ns>", "ns")
        before = R.STRIPPED
        R._minimal_render(text, ctx)
        assert R.STRIPPED == before, \
            f"chip fragment is not renderable by the dependency-free fallback: {text!r}"


def test_the_editor_never_offers_a_verb_p0_refuses():
    """No button for something the route refuses; `code`/`extension` are read-only."""
    html = _asset("index.html")
    js = console_js()
    assert "ed-panel" in html, "the editor panel is not on the page"
    assert "readonly" in html.lower() or "readOnly" in js, \
        "the raw surface must be read-only in P0 (R1)"
    assert "'/local/content/try'" not in js, "`/content/try` is P1 (§9), not P0"


def test_the_card_grew_the_four_functions_the_brief_names():
    """§9 item 9: the four seams the brief hands a later agent, pinned by name."""
    js = console_js()
    for fn in ("function openEditor(", "async function saveItem(",
               "async function renderDraftPrompt(", "function renderChips("):
        assert fn in js, f"the console JS has no {fn}…)"
    assert "'/local/content/item'" in js and "'/local/content/render'" in js


def test_no_timer_in_the_editor_can_reach_a_model():
    """T9 for P0: the only thing on a timer is the free render route; Save is click-only.
    This is what stops a later *Try it* being wired to the keypress debounce."""
    js = console_js()
    editor = js[js.index("const ED_CHIPS"):]
    timers = [ln for ln in editor.splitlines()
              if "setTimeout(" in ln or "setInterval(" in ln]
    assert timers, "the debounce is gone; this guard now proves nothing"
    for line in timers:
        assert "renderDraftPrompt" in line, \
            f"a timer in the editor calls something other than the free render: {line!r}"
    assert "saveItem" in editor and "sv.onclick=saveItem" in editor.replace(" ", ""), \
        "Save is not bound to a click"


# --- The console proxy, end to end — the URL, the body and the status code ---
# Above drives the supervisor route (where validation lives, R6); a proxy path typo would
# still 503 on a real appliance, so the two hops are joined here once.

@pytest.fixture
def console(rt, base, tmp_path, monkeypatch):
    """The real console app in-process, pointed at the real supervisor above."""
    from helpers_console import console_app, set_status_url
    TestClient, main = console_app(tmp_path / "console.db", base)
    set_status_url(base, monkeypatch)
    with TestClient(main.app) as c:
        yield c


@needs_runtime
def test_the_console_proxies_a_save_and_a_render(console, rt):
    """Both proxies, both hops, and the shape the card reads."""
    r = console.post("/local/content/item",
                     json={"kind": "conversation", "data": conversation()})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is True and out["created"] is True
    assert out["id"] == "conversation:BEDTIME/default"
    assert out["item"]["name"] == "Bedtime wind-down"
    assert out["item"]["local_edited"] is True
    assert out["error"] is None
    assert out["undo_slots"] == 1, "the card must be told there is exactly one step back"
    assert "conversation:BEDTIME/default" in rt._content_overlay()

    r2 = console.post("/local/content/render",
                      json={"kind": "conversation", "data": conversation(),
                            "context": {"nickname": "Ada"}})
    assert r2.status_code == 200, r2.text
    v = r2.json()
    assert v["ok"] is True and "Ada" in v["prompt"]
    assert v["counts_advisory"] is True and v["portable_identical"] is True


@needs_runtime
def test_the_console_forwards_a_refusal_as_a_sentence_not_a_500(console):
    """A refusal reaches the card as its status code AND sentence, never a 500."""
    r = console.post("/local/content/item",
                     json={"kind": "schedule", "data": {"name": "Morning", "schedule": {}}})
    assert r.status_code == 400, r.text
    out = r.json()
    assert out["ok"] is False
    assert "robot" in (out["error"] or "").lower(), out
    assert out["item"] == {} or not out["item"].get("id"), out
