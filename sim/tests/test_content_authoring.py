"""
✍️ Content authoring P0 — a parent writes a conversation, and it is exactly as untrusted
as a stranger's.

The build document is `docs/architecture/backlog/content-authoring.md`; this file is its
§7, minus the rungs P0 does not build (`/content/try`, its budget, and the brain call —
T6-T9 and T18 belong to P1, and there is deliberately nothing here that calls a model).

**What this suite is actually for.** Authoring adds two write-shaped routes to an
appliance that already has five, and the one property worth proving is negative: an
authored item goes through the *same* functions an imported one does, so the editor
contributes no second validation path. §6.3 names the single `if` that makes that true —
`POST /content/item` must call `packs.validate_item` itself, because `packs.mark_edited`
normalizes and does **not** validate — and `sim/tools/authoring_mutation_check.py` deletes
that call (and four more guards) and requires a named test below to go red. A green run of
this file says the guards are present; the checker says they are load-bearing.

Everything runs against a genuine `MoxieRuntime` on a scratch data dir with a fake MQTT
transport and its own status HTTP server on a free port (`helpers_runtime`), so what is
proved is the handler the parent console really talks to. No broker, no gateway, no robot,
no sleeps, and — by design — **no brain**: `build()`'s chat function raises if anything
calls it, which is how T10 proves the render panel is free rather than merely cheap.

The pure half (`packs.shadow_check`, `render.render_prompt`'s counts out-parameter, the
console's closed chip list) needs neither paho nor a runtime and runs in every tier.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))
sys.path.insert(0, os.path.dirname(__file__))

from moxie_sdk.content import packs as P                            # noqa: E402
from moxie_sdk.content import render as R                           # noqa: E402

try:                                            # the runtime's transport
    import paho.mqtt.client                     # noqa: F401
    HAVE_PAHO = True
except Exception:                               # pragma: no cover - tier-dependent
    HAVE_PAHO = False

needs_runtime = pytest.mark.skipif(not HAVE_PAHO,
                                   reason="the runtime's transport (paho) is not installed")


# --------------------------------------------------------------------------- #
# Fixtures — a real supervisor over a shipped module, and a brain that must not run
# --------------------------------------------------------------------------- #

SHIPPED_PROMPT = "You are Moxie, the shipped starter chat."
SHIPPED_CODE = "def post_process(volley, session):\n    raise RuntimeError('never run')\n"
SHIPPED_EXT = {"ext_format": 1, "capabilities": ["say"], "on": "turn.before",
               "rules": [{"do": [{"say": "Nice to see you."}]}]}


def shipped_module():
    """The `MOXIE_CONTENT_MODULE` file an appliance has on disk today.

    `FREE_CHAT/default` deliberately carries **both** a `code` block and an `extension`,
    because T4 and T16 are about what a save does to fields the editor may not author.
    """
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
    """A real runtime whose app is a real `ContentApp` over `shipped_module()`.

    Boots the way `config.build_content_app()` does — shipped defaults first, then the
    overlay already in this data dir — so calling it twice against one `tmp_path` is a
    faithful restart.
    """
    from helpers_runtime import make_runtime
    from moxie_sdk.content import ContentApp
    from moxie_sdk.store import JsonStore

    store = JsonStore(str(tmp_path))
    shipped = P.shipped_items(shipped_module())
    stored = store.read_shared("content_items", {}) or {}
    overlay = stored.get("items") if isinstance(stored, dict) else None
    app = ContentApp(P.build_module(shipped, overlay if isinstance(overlay, dict) else {}),
                     chat, memory=False, content_defaults=shipped)
    rt, _device_id = make_runtime(app, store=store)
    return rt


@pytest.fixture
def rt(tmp_path):
    return build(tmp_path)


@pytest.fixture
def base(rt):
    """The runtime's REAL status HTTP server on a free port."""
    from helpers_runtime import status_server
    return status_server(rt)


def post(base, path, body, *, expect=200):
    """One POST against the status server; returns `(status, payload)`.

    A refusal is a *value* here, not an exception, because most of this file is about
    what a refusal says — `expect` is asserted so a test can never mistake a 200 for the
    400 it was written to prove.
    """
    from helpers_runtime import http_json
    try:
        payload = http_json(base + path, method="POST", body=body)
        status = 200
    except urllib.error.HTTPError as e:
        status = e.code
        payload = json.loads(e.read().decode() or "{}")
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


# --------------------------------------------------------------------------- #
# T1 — the round trip
# --------------------------------------------------------------------------- #

@needs_runtime
def test_authored_item_round_trips(rt, base):
    """Save a new conversation → it is in the inventory as a local edit, and the module
    the next turn renders from carries the author's prompt byte for byte.

    The last clause is the one that matters: an item that is merely *stored* has not been
    authored. `build_module(defaults, overlay)` is the same call `reload_content()` makes,
    so reading the prompt back out of a `Conversation` proves the write reached the thing
    Moxie actually talks from."""
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

    # The live app was swapped, not just the file — `reload_content()` is a guard the
    # mutation checker deletes, and this is the assertion that must notice.
    live = next((c for c in rt.app.module.conversations if c.module_id == "BEDTIME"), None)
    assert live is not None and live.prompt == draft["prompt"], \
        "the save wrote the overlay but never reloaded the live module"


# --------------------------------------------------------------------------- #
# T2 — §6.3's one `if`
# --------------------------------------------------------------------------- #

@needs_runtime
def test_a_bad_pattern_is_refused_with_validate_items_own_sentence(base):
    """`mark_edited` normalizes and does not validate, so the route must call
    `validate_item` itself (§6.3). A global whose `pattern` does not compile is the case
    that proves it: `Global.from_dict` compiles at **load**, so an unvalidated save takes
    down the next `reload_content()` rather than failing here.

    The refusal must be `validate_item`'s own string, not a paraphrase — a second sentence
    is a second validator wearing a coat."""
    bad = {"name": "Broken", "pattern": "what time is it("}
    _, out = post(base, "/content/item", {"kind": "global", "data": bad}, expect=400)
    assert out["ok"] is False, out

    expected = P.validate_item({"kind": "global", "key": "Broken", "data": bad,
                                "source_version": 1})
    assert expected, "validate_item did not refuse the pattern this test is built on"
    assert expected[0] in (out.get("error") or ""), (expected, out)
    assert "pattern does not compile" in expected[0], expected

    # And nothing landed: a refusal that half-wrote is worse than no refusal.
    view = get(base, "/content")
    assert not [r for r in view["items"] if r["id"] == "global:Broken"], view["items"]


# --------------------------------------------------------------------------- #
# T3 — the allowlist
# --------------------------------------------------------------------------- #

@needs_runtime
def test_a_field_outside_the_allowlist_never_lands(rt, base):
    """`normalize_data` is G1 and the editor gets it for free — the stored `data` has
    exactly `FIELDS[kind]`, so a key the form never offered cannot be smuggled in by a
    hand-rolled POST."""
    draft = dict(conversation(), secret="not-a-field-this-appliance-has", code_exec=True)
    _, out = post(base, "/content/item", {"kind": "conversation", "data": draft})
    assert out["ok"], out

    stored = rt._content_overlay()["conversation:BEDTIME/default"]["data"]
    assert set(stored) == set(P.FIELDS["conversation"]), sorted(stored)
    assert "secret" not in stored and "code_exec" not in stored


# --------------------------------------------------------------------------- #
# T4 — what a save must not lose
# --------------------------------------------------------------------------- #

@needs_runtime
def test_saving_a_name_change_preserves_code_and_extension(rt, base):
    """§4.2's hard requirement. The editor round-trips the **whole** normalized `data`, so
    renaming a shipped item that carries a `code` block and an extension must not quietly
    drop either. Byte-identical, not merely present."""
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


# --------------------------------------------------------------------------- #
# T5 — provenance, for free
# --------------------------------------------------------------------------- #

@needs_runtime
def test_authored_then_imported_reports_conflict(base):
    """An authored item is `local_edited` because it has no `imported_rev`
    (`is_local_edited`:577, deliberately). So a stranger's pack carrying the same key at a
    higher `source_version` reports CONFLICT and defaults **un-ticked** — with no change to
    `review_pack` at all. That is the whole of A3, and it is why authoring needed no new
    review state."""
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


# --------------------------------------------------------------------------- #
# T10 / T11 — rung 1, the free feedback
# --------------------------------------------------------------------------- #

@needs_runtime
def test_render_route_calls_no_brain(base):
    """Rung 1 costs zero gateway calls. `build()`'s chat function raises on any call, so
    this is a real absence rather than a comment — and the route still returns the resolved
    system prompt, with the sample nickname substituted."""
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
    """§4.3's promise is *portability*: a guided prompt renders the same on an appliance
    with jinja2 and on a bare `pip install moxie-cloud-sdk` without the `content` extra.

    So the panel reports both renders — and a `{% for %}`, which only the real renderer can
    evaluate, must come back with a non-zero `stripped` and `portable_identical: false`.
    Written this way on purpose: reading `render.STRIPPED` around a single `render_prompt`
    call would report **zero** on any machine that has jinja2, i.e. on every appliance we
    ship, which is a counter that can never fire where it matters.

    The negative control is inline: the same route, the same probe, a portable prompt →
    zero. Without it, a route that hard-coded `stripped: 1` would pass the first half."""
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
    """§9 item 2's one line. The out-parameter exists so a route does not have to take a
    before/after delta of two process-global integers by hand.

    Both directions, because a `counts` dict that is merely *written* proves nothing: a
    construct the fallback drops must move `stripped`, and one it renders must not."""
    counts = {}
    R._minimal_render("{{ volley.config.child_pii.nickname }}", {"volley": None})
    text = R.render_prompt("{{ x.y }}", {"x": {"y": "ok"}}, counts=counts)
    assert text == "ok"
    assert counts == {"blocked": 0, "stripped": 0}, counts

    counts2 = {}
    R._minimal_render("{% for a in b %}{{ a }}{% endfor %}", {}, counts=counts2)
    assert counts2["stripped"] >= 1, counts2


# --------------------------------------------------------------------------- #
# T12 / T13 — the shadow rule (§4.4)
# --------------------------------------------------------------------------- #

@needs_runtime
def test_shadow_warning_names_the_earlier_command(base):
    """`match_global` returns the FIRST pattern that fires and `module_data` sorts by
    `kind:key`, so a global's precedence is alphabetical by its `name` — and nothing on
    screen would say so. Authoring *When is it* behind the installed *Time* must come back
    naming Time and saying commands are tried in name order."""
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
    """T12's vacuity guard, with its own positive control first: the same route, the same
    item, one phrase that IS shadowed → a warning; then a disjoint phrase → none. If the
    route simply never warned, the first half fails and this test cannot pass by silence."""
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
    """The pure half (A5). The check runs the author's own phrases against installed
    patterns in `sorted` order and reports only an installed global that sorts EARLIER —
    a later-sorting one loses the race and is not a shadow."""
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


# --------------------------------------------------------------------------- #
# T14 — undo, unchanged
# --------------------------------------------------------------------------- #

@needs_runtime
def test_undo_restores_an_authored_save(rt, base):
    """A save snapshots the overlay exactly as an import does, so `POST /content/undo`
    puts an authored item back into non-existence with no new mechanism — and leaves the
    pack ledger alone, because a save is not an import."""
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
    """§3.3, made checkable: there is no history. Saving twice and undoing once returns the
    PREVIOUS save, not the original — and the response says the slot is single."""
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


# --------------------------------------------------------------------------- #
# T15 / T16 — what the editor refuses
# --------------------------------------------------------------------------- #

@needs_runtime
def test_schedule_is_refused_by_the_editor_route(rt, base):
    """§0's ceiling, enforced at the route rather than in the form. A schedule is the one
    item kind that reaches the robot as `ContentSchedule`, and no physical Moxie has ever
    been served a pack-authored one — so a parent-facing button must not put an unobserved
    wire behaviour behind it. The refusal names the reason, not just the rule."""
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
    """§4.5: the AST stays read-only in every phase, and `code` is not editable, not
    creatable and not runnable. A save that CHANGES either is refused with a sentence that
    points somewhere — the extensions brief owns the text→AST surface, and building a
    second compiler in this card would be the mistake that brief already refused."""
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

    # The control: the SAME payload with the extension and code left alone saves fine, so
    # the refusal is about the change and not about the fields being present at all.
    _, ok = post(base, "/content/item",
                 {"kind": "conversation", "data": dict(data, name="Renamed")})
    assert ok["ok"], ok


@needs_runtime
def test_a_second_tab_cannot_silently_discard_the_first(rt, base):
    """R7. The save carries the `local_rev` it opened with; a mismatch is a **409** with
    the same wording the import conflict uses. One slot of undo is not a fix for this and
    the response must not pretend it is."""
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


# --------------------------------------------------------------------------- #
# T17 — the routes are declared where the console says they are
# --------------------------------------------------------------------------- #

def _asset(name):
    with open(os.path.join(REPO, "server", "static", name)) as fh:
        return fh.read()


def test_the_authoring_routes_are_declared():
    """The console's route decorators pinned as literal source strings — the idiom
    `test_brain_console.py`:179 already uses. A route that quietly moves fails a test
    rather than a parent.

    Read as text rather than imported: the hermetic tier has no fastapi."""
    with open(os.path.join(REPO, "server", "moxie_server", "main.py")) as fh:
        main = fh.read()
    assert '@app.post("/local/content/item")' in main
    assert '@app.post("/local/content/render")' in main
    assert "normalize_content_item_result" in main, \
        "the item route does not normalize its answer, so a card could 500 on a refusal"
    # P0 does not build the paid rung, and must not accidentally ship its route. Matched
    # as a route LITERAL rather than as a substring, so prose about P1 does not trip it.
    assert '@app.post("/local/content/try")' not in main, "`/content/try` is P1 (§9), not P0"

    with open(os.path.join(REPO, "mqtt", "supervisor", "moxie_runtime.py")) as fh:
        runtime = fh.read()
    assert '"/content/item"' in runtime and '"/content/render"' in runtime
    assert '"/content/try"' not in runtime, "`/content/try` is P1 (§9), not P0"


def test_the_supervisor_route_owns_the_validation_not_the_proxy():
    """R6. `validate_item` belongs to the route that WRITES; a check in the console proxy
    would be bypassed by a direct `curl` at the supervisor. So the supervisor's source
    names it and the console's does not."""
    with open(os.path.join(REPO, "mqtt", "supervisor", "moxie_runtime.py")) as fh:
        runtime = fh.read()
    with open(os.path.join(REPO, "server", "moxie_server", "main.py")) as fh:
        main = fh.read()
    assert "content_packs.validate_item(" in runtime, \
        "the supervisor's writing route does not call validate_item at all (§6.3)"
    assert "validate_item(" not in main, \
        "the console proxy validates; a direct curl at the supervisor would skip it"


def test_the_chip_list_is_closed_to_the_two_portable_forms():
    """AC10. The guided surface may only emit `{{ dotted.path }}` and
    `{% if dotted.path %}` — exactly the intersection the dependency-free fallback renders
    identically to the sandbox — so a guided prompt renders the same with and without
    jinja2 by construction rather than by discipline.

    Asserted over the chip table's own source: every fragment it can insert is run through
    `_minimal_render` and must come back with `STRIPPED` unmoved."""
    import re
    js = _asset("app.js")
    m = re.search(r"const ED_CHIPS\s*=\s*\[(.*?)\n\];", js, re.S)
    assert m, "app.js has no ED_CHIPS table"
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
    """The card must not grow a button for something the route will refuse. Deletion has
    no `merge_items` operation at all (§3.3), schedules are §0, and `code`/`extension` are
    read-only windows."""
    html = _asset("index.html")
    js = _asset("app.js")
    assert "ed-panel" in html, "the editor panel is not on the page"
    assert "readonly" in html.lower() or "readOnly" in js, \
        "the raw surface must be read-only in P0 (R1)"
    assert "'/local/content/try'" not in js, "`/content/try` is P1 (§9), not P0"


def test_the_card_grew_the_four_functions_the_brief_names():
    """§9 item 9's file list, pinned by name. Not decoration: `openEditor` / `saveItem` /
    `renderDraftPrompt` / `renderChips` are the four seams the brief hands a later agent,
    and a rename that silently split one of them would leave that agent reading a plan
    that no longer describes the file."""
    js = _asset("app.js")
    for fn in ("function openEditor(", "async function saveItem(",
               "async function renderDraftPrompt(", "function renderChips("):
        assert fn in js, f"app.js has no {fn}…)"
    assert "'/local/content/item'" in js and "'/local/content/render'" in js


def test_no_timer_in_the_editor_can_reach_a_model():
    """P0's shape of T9. The paid rung does not exist yet, so the property to hold now is
    the one that makes adding it safe: **the only thing on a timer is the free route.**

    `renderDraftPrompt` is debounced (400 ms) and calls `/content/render`, which makes no
    brain call by construction; `saveItem` is bound to a click and nothing else. If a
    later pass adds a *Try it*, this assertion is what stops it from being wired to the
    same debounce — the mistake upstream's harness makes, where every keypress-to-answer
    is a real model call with no budget and no counter."""
    js = _asset("app.js")
    editor = js[js.index("const ED_CHIPS"):]
    timers = [ln for ln in editor.splitlines()
              if "setTimeout(" in ln or "setInterval(" in ln]
    assert timers, "the debounce is gone; this guard now proves nothing"
    for line in timers:
        assert "renderDraftPrompt" in line, \
            f"a timer in the editor calls something other than the free render: {line!r}"
    assert "saveItem" in editor and "sv.onclick=saveItem" in editor.replace(" ", ""), \
        "Save is not bound to a click"
