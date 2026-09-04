"""T1–T18 — behaviour, determinism and integration for sandboxed content extensions.

`test_ext_escapes.py` answers *"can a stranger's pack hurt this appliance?"*. This file
answers the other two questions: *does the language actually express what content authors
have written?* (T1–T7, against the six hand-ported upstream hooks) and *does a broken one
leave the child with a working robot?* (T8–T18).

Design: `docs/architecture/backlog/sandboxed-extensions.md`. Prior art: OpenMoxie
(MIT, © Justin Beghtol) — cited, hand-ported, never copied; see `ATTRIBUTION.md`.
"""
import json
import os
import re
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.content import ext as E                              # noqa: E402
from moxie_sdk.content import packs as P                            # noqa: E402
from moxie_sdk.content import content_app as CA                     # noqa: E402
from moxie_sdk.content.content_app import ContentApp                # noqa: E402
from moxie_sdk.content.module import load_modules                   # noqa: E402
from moxie_sdk.content.volley import Volley, Session                # noqa: E402
from moxie_sdk.store import JsonStore, MemoryStore                  # noqa: E402
from moxie_sdk.types import Turn, RobotContext, ChildProfile        # noqa: E402

CONFORMANCE = os.path.join(os.path.dirname(__file__), "data", "ext_conformance.json")
STARTER = os.path.join(REPO, "mqtt", "content_modules", "starter.json")


def rows():
    with open(CONFORMANCE, encoding="utf-8") as fh:
        return {r["name"]: r for r in json.load(fh)["rows"]}


ROWS = rows()


def robot(device_id="robot-1", *, nickname="Sam", module_id="", content_id=""):
    return RobotContext(device_id=device_id, module_id=module_id, content_id=content_id,
                        child=ChildProfile(nickname=nickname))


def run_row(row, *, allow_p1=False):
    """Evaluate one conformance row exactly as the file records it."""
    return E.evaluate(row["ast"], row["facts"], grants=set(row["grants"]),
                      now_ms=row["now_ms"], clock_local=row["clock_local"],
                      seed=row["seed"])


# --------------------------------------------------------------------------- #
# T1–T6 — the six §8 hooks reproduce their goldens byte for byte
# --------------------------------------------------------------------------- #

#: What is still missing, per row, now that `act` is real. Named precisely, because a
#: stale `xfail` reason is a lie the suite tells every time it runs.
#:
#: * **G5** needs `brain` — one model call per turn from inside a pack, with the budget
#:   brief §5.1 requires before a pack may spend money and latency inside the 6 s turn.
#: * **G6** needs `subscribe`. Its *matched* rule emits only a `say`, but the pack
#:   **declares** `subscribe` and declared-equals-used is a load condition (§5, X10), so
#:   the row cannot run until the capability is honoured. The effect has no host: nothing
#:   joins `Volley.subscriptions` to `wire.build_chat_response(subscribe_events=…)` — the
#:   supervisor fills `EventSubscription` from its own vision bookkeeping instead
#:   (`moxie_runtime.py::_publish_chat`).
#:
#: `act` is **not** on this list any more (2026-09-04): brief S5 is closed for it, and
#: G2/G3 below are plain tests.
P1_REASON = {
    "G5": "needs the `brain` capability and its one-call-per-turn budget (brief §5.1)",
    "G6": ("needs `subscribe`: nothing joins Volley.subscriptions to "
           "RemoteChatAction.EventSubscription yet, so the capability could not do "
           "anything and is still refused at load"),
}


@pytest.mark.parametrize("name", ["G1", "G4"])
def test_t1_t6_conformance_p0(name):
    """T1/T4 — the two hooks P0 can grant reproduce their effect list byte for byte.

    G1 is `MoxieTime.get_response` — the clock, a `%`, a conditional and a formatted
    sentence. G4 is `MoxieTimers`' wake hook — `session.is_empty`, an `input_vars` read,
    a `forget`, per-turn scratch and a markup line that repeats a chime three times with
    `<break time="1s"/>`.

    G4 also records the one thing we deliberately did **not** port: upstream's
    `time.sleep(0.5)`. The corpus-correct replacement was in the same function all along
    (§5.3) — `<break>` is honoured by the *robot*, on its playback clock, where it is
    free; a sleep spends the turn's 6 seconds to do nothing and is the simplest
    denial-of-service in any sandbox.
    """
    row = ROWS[name]
    r = run_row(row)
    assert r.ok, r.reason
    assert r.effects == row["expected_effects"], r.effects
    assert r.handled == row["expected_handled"]


@pytest.mark.parametrize("name", ["G2", "G3", "G5", "G6"])
def test_t1_t6_conformance_p1_grammar_is_already_valid(name):
    """T2/T3/T5/T6, the half that could pass before the wire — **the programs validate**.

    §8's point worth checking early: these four were gated by *capability*, not by
    expressiveness. Their grammar was accepted by the validator all along, which is why
    two of them turned green below the moment `act` was plumbed rather than needing to be
    written that day.
    """
    row = ROWS[name]
    assert E.validate(row["ast"], allow_p1=True) == []
    assert row["expected_effects"], "the golden must exist now, not later"


@pytest.mark.parametrize("name", ["G2", "G3"])
def test_t1_t6_conformance_act(name):
    """T2/T3 — the two `act` hooks reproduce their effect list byte for byte. ✅ 2026-09-04.

    These were `xfail(strict=True)` from the day the evaluator landed, on brief S5: *"the
    single most important scoping fact in this brief"* — `volley.execution_actions` was
    not on the wire, so an `act` capability could not do anything and was refused at load
    rather than shipped as a lie. Two changes closed it: `wire.encode_action` learned to
    carry `function_id`/`function_args` (#119), and `content_app.execution_actions_of`
    turns an effect into an `execute` `Action`.

    G2 is `MoxieTimers` set — the §4.1 worked example verbatim: a `remember`, an
    `act.eb_timer_request` with two `function_args`, and a sentence. G3 is its
    status/cancel sibling, three `let`s deep into an h/m/s line.
    """
    row = ROWS[name]
    r = run_row(row)
    assert r.ok, r.reason
    assert r.effects == row["expected_effects"], r.effects
    assert r.handled == row["expected_handled"]


@pytest.mark.parametrize("name", ["G5", "G6"])
def test_t1_t6_conformance_still_p1(name, request):
    """T5/T6 — still `xfail`, and the reason names **only** what is actually missing.

    `strict=True` on purpose: the day someone makes one of these grantable and forgets to
    remove the marker, an XPASS fails the suite and says so. That is exactly how G2/G3
    were caught the day `act` landed.
    """
    request.node.add_marker(pytest.mark.xfail(strict=True, reason=P1_REASON[name]))
    row = ROWS[name]
    r = run_row(row)
    assert r.ok, r.reason
    assert r.effects == row["expected_effects"]


def test_the_conformance_goldens_are_not_upstream_code():
    """Clean-room, asserted: these are *re-authored programs*, and §7.4 is emphatic that a
    Python-to-AST compiler is the wrong instinct. Six hooks is a hand-port, not a
    compiler — so no upstream source text may have travelled with them."""
    blob = open(CONFORMANCE, encoding="utf-8").read()
    for python_ism in ("def ", "import ", "lambda", "self.", "globals()", "exec("):
        assert python_ism not in blob, python_ism
    doc = json.load(open(CONFORMANCE, encoding="utf-8"))
    assert "OpenMoxie" in " ".join(doc["_comment"])
    assert "Justin Beghtol" in " ".join(doc["_comment"])


# --------------------------------------------------------------------------- #
# T7 — determinism
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", ["G1", "G3"])
def test_t7_the_same_inputs_give_byte_identical_effects(name):
    """T7 — 100 runs at a fixed injected clock and seed, one golden.

    Same AST + same fact base + same seed → byte-identical effect list (§6.1). It holds
    because there is no ambient clock, no ambient entropy, `keys` sorts, `sort` is a total
    order over scalars, and `format` requires an explicit spec — so no language's default
    float repr ever reaches output. That last one is also what will make the P1 JavaScript
    port checkable against this very file.
    """
    row = ROWS[name]
    first = json.dumps(run_row(row).effects, sort_keys=True)
    for _ in range(100):
        assert json.dumps(run_row(row).effects, sort_keys=True) == first
    assert json.loads(first) == row["expected_effects"]


def test_t7_a_different_clock_changes_the_answer():
    """T7's other direction — a determinism test that passed for a *constant* evaluator
    would prove nothing. Move the injected clock, and G1's sentence moves with it."""
    row = ROWS["G1"]
    morning = E.evaluate(row["ast"], row["facts"], grants=set(row["grants"]),
                         now_ms=row["now_ms"],
                         clock_local={"hour": 9, "minute": 5}, seed=1)
    midnight = E.evaluate(row["ast"], row["facts"], grants=set(row["grants"]),
                          now_ms=row["now_ms"],
                          clock_local={"hour": 0, "minute": 0}, seed=1)
    assert morning.effects[0]["text"] == "The time is 9:05 AY M"
    assert midnight.effects[0]["text"] == "The time is 12:00 AY M"


# --------------------------------------------------------------------------- #
# T8, T9 — a breach fails the extension, never the turn
# --------------------------------------------------------------------------- #

POISON = {"ext_format": 1, "capabilities": ["say"], "on": "global",
          "rules": [{"do": [{"say": {"concat": ["A" * 4000] * 8}}]}]}
POISON_BEFORE = dict(POISON, on="turn.before")

MODULE = {
    "conversations": [{"name": "Chat", "module_id": "CHAT", "content_id": "default",
                       "prompt": "You are Moxie."}],
    "globals": [{"name": "Broken", "pattern": "tell me a story",
                 "extension": POISON}],
}


def app_with(module_json, chat=None, **kw):
    return ContentApp(load_modules(module_json), chat or (lambda m: "the model answered"),
                      default_module_id="CHAT", memory=False, safety_classifier=False,
                      **kw)


def test_t8_a_breach_does_not_end_the_turn():
    """T8 — Moxie keeps talking. That is the requirement, and it dictates the whole
    failure design (§6.4).

    A poisoned `on: global` extension behaves like a matched global with no handler: it
    **falls through to the conversation** (S1), and the child gets a normal answer. A
    poisoned `on: turn.before` is skipped and the model runs. No exception escapes
    `respond()`, and nothing the child hears mentions a failure.
    """
    app = app_with(MODULE)
    reply = app.respond(Turn(robot=robot(), speech="tell me a story"))
    assert reply.text == "the model answered"
    for word in ("error", "script", "exception", "traceback", "sandbox", "budget"):
        assert word not in reply.text.lower()

    before = dict(MODULE, conversations=[dict(MODULE["conversations"][0],
                                              extension=POISON_BEFORE)])
    app2 = app_with(before)
    reply2 = app2.respond(Turn(robot=robot(), speech="hello"))
    assert reply2.text == "the model answered"


def test_t8_a_failing_extension_writes_nothing(tmp_path):
    """T8's other half — nothing is half-written when an extension breaks (§4.5)."""
    store = MemoryStore(JsonStore(str(tmp_path)))
    poison = {"ext_format": 1, "capabilities": ["memory.write", "say"], "on": "global",
              "rules": [{"do": [{"remember": {"key": "score", "value": 1}},
                                {"say": {"concat": ["A" * 4000] * 8}}]}]}
    app = ContentApp(load_modules(dict(MODULE, globals=[
        {"name": "Broken", "pattern": "tell me a story", "extension": poison}])),
        lambda m: "the model answered", default_module_id="CHAT", memory=store,
        safety_classifier=False,
        ext_grants=E.DEFAULT_GRANTS | {"memory.write"})
    reply = app.respond(Turn(robot=robot(), speech="tell me a story"))
    assert reply.text == "the model answered"
    assert store.load("robot-1") == {}, store.load("robot-1")


def test_t9_three_breaches_quarantine_for_the_session(tmp_path, capsys):
    """T9 — a broken extension may cost the child one turn's latency; it may not cost
    every turn's.

    After `MOXIE_EXT_MAX_BREACHES` breaches the extension is not evaluated at all: the
    4th turn never reaches the evaluator (asserted by the step counter never advancing),
    and the parent gets **one** `ext_events` entry, not four.
    """
    store = MemoryStore(JsonStore(str(tmp_path)))
    app = ContentApp(load_modules(MODULE), lambda m: "the model answered",
                     default_module_id="CHAT", memory=store, safety_classifier=False)
    seen = []
    real = E.evaluate

    def counting(*a, **kw):
        seen.append(1)
        return real(*a, **kw)

    E.evaluate = counting
    try:
        for _ in range(4):
            assert app.respond(Turn(robot=robot(),
                                    speech="tell me a story")).text == "the model answered"
    finally:
        E.evaluate = real
    assert len(seen) == 3, f"the 4th turn still evaluated ({len(seen)} runs)"
    events = store.store.read("robot-1", CA.EXT_EVENTS_COLLECTION, [])
    assert len(events) == 1, events
    assert events[0]["extension"] == "global:Broken"
    assert events[0]["reason"] in ("value", "total")
    # In words a console can print, with no jargon and no stack fragment.
    assert events[0]["sentence"] == "it tried to build something too big"


def test_t9_the_parent_sentence_exists_for_every_breach_kind():
    """T9's completeness check — a breach code with no parent-facing words would surface
    in the console as a bare identifier, which is the failure `explain()` exists to
    prevent."""
    for kind in ("steps", "budget", "value", "total", "error", "capability", "invalid",
                 "output"):
        assert E.BREACH_WORDS.get(kind), kind
        assert E.ExtResult(ok=False, breach=kind).sentence == E.BREACH_WORDS[kind]


# --------------------------------------------------------------------------- #
# T10–T12 — packs
# --------------------------------------------------------------------------- #

def ext_item(caps=("say",), key="Greeter", version=1, rules=None):
    return {"kind": "global", "key": key, "source_version": version,
            "data": {"name": key, "pattern": "say hello",
                     "extension": {"ext_format": 1, "capabilities": list(caps),
                                   "on": "global",
                                   "rules": rules or [{"do": [{"say": "Hello there!"}]}]}}}


def test_t10_a_pack_round_trips_with_an_extension_inside():
    """T10 — export → parse → review → apply → the extension is live on the next turn.

    No new storage and no new concept: an extension is a field on an item, so it rides the
    three existing pack collections, the existing 2×2 review and the existing overlay
    (§7.1, §7.5). The exported bytes re-import to the same digest.
    """
    module = load_modules({"globals": [ext_item()["data"]]})
    items = P.shipped_items({"globals": [ext_item()["data"]]})
    pack = P.export_pack(items, name="Greeter pack", pack_id="greet-1")
    raw = json.dumps(pack)
    parsed, meta = P.parse_pack(raw)
    assert meta["digest"] == "ok", meta
    assert P.pack_digest(parsed) == P.pack_digest(pack)

    review = P.review_pack(parsed, {}, digest=meta["digest"])
    assert [r["state"] for r in review] == [P.NEW]
    assert review[0]["default"] is True
    installed, summary = P.apply_pack(parsed, {}, [r["id"] for r in review])
    assert summary["applied"] == ["global:Greeter"]

    rebuilt = P.build_module({}, installed)
    g = rebuilt.globals[0]
    assert E.validate(g.extension) == []
    app = ContentApp(rebuilt, lambda m: "the model answered", memory=False,
                     safety_classifier=False)
    reply = app.respond(Turn(robot=robot(), speech="say hello"))
    assert reply.text == "Hello there!", reply


def test_t10_the_extension_survives_the_field_allowlist_unchanged():
    """T10's quiet half — `SPEC`'s `_d` coercer is `json.loads(json.dumps(v))`, so a
    stored extension is **provably JSON-only** before the validator ever sees it (A11).
    That is the first half of §4.4's argument, and it costs one line."""
    data = P.normalize_data("global", ext_item(caps=("say", "clock"))["data"])
    assert data["extension"]["capabilities"] == ["say", "clock"]
    assert json.loads(json.dumps(data["extension"])) == data["extension"]
    # Unknown keys inside `data` are dropped, and `extension` is not one of them.
    assert "extension" not in P.dropped_fields("global", ext_item()["data"])


def test_t11_a_capability_escalation_defaults_unticked():
    """T11 — §7.3's five-row matrix, including the two-sentence case.

    The comparison is over the **capability set**, independent of `source_version` and
    independent of `local_rev`. So a pack cannot escalate privileges by bumping a version
    number, and it cannot escalate them quietly on a machine where the parent never edited
    anything. A *shrinking* set is not a conflict — less is always safe.
    """
    base = ext_item(caps=("say",))
    installed_data = P.normalize_data("global", base["data"])
    rev = P.local_rev({"kind": "global", "data": installed_data})

    def review(incoming, *, edited=False, version=2):
        # "Edited" is `imported_rev` no longer matching the stored data's digest — the
        # parent changed the item after it was installed (P4). Simulated by stamping a
        # rev that is not this data's.
        stamp = "sha256:" + ("0" * 64) if edited else rev
        entry = {"kind": "global", "data": installed_data, "source_version": 1,
                 "provenance": {"kind": "global", "imported_rev": stamp,
                                "origin": "pack"}}
        pack = {"items": [dict(incoming, source_version=version)]}
        return P.review_pack(pack, {"global:Greeter": entry}, digest="ok")[0]

    same_caps = ext_item(caps=("say",), rules=[{"do": [{"say": "Hi again!"}]}])
    more_caps = ext_item(caps=("say", "memory.write"),
                         rules=[{"do": [{"remember": {"key": "seen", "value": 1}},
                                        {"say": "Hi!"}]}])
    fewer = ext_item(caps=("say",), rules=[{"do": [{"say": "Hi!"}]}])

    # 1. clean upgrade, no new capability → ticked
    row = review(same_caps)
    assert row["state"] == P.UPGRADE and row["default"] is True and not row["escalation"]

    # 2. clean upgrade that asks for MORE → un-ticked, with its own sentence
    row = review(more_caps)
    assert row["state"] == P.UPGRADE
    assert row["escalation"] == ["memory.write"]
    assert row["default"] is False
    assert row["warnings"][0].startswith(P.ESCALATION_LABEL)
    assert "remember things from this activity" in row["warnings"][0]

    # 3. locally edited, no new capability → CONFLICT, un-ticked, one sentence
    row = review(same_caps, edited=True)
    assert row["state"] == P.CONFLICT and row["default"] is False
    assert not row["escalation"]

    # 4. locally edited AND asks for more → CONFLICT + escalation, TWO sentences
    row = review(more_caps, edited=True)
    assert row["state"] == P.CONFLICT and row["default"] is False
    assert row["escalation"] == ["memory.write"]
    assert row["warnings"][0].startswith(P.ESCALATION_LABEL)
    assert "replaces the changes you made here" in row["label"]

    # 5. same version → KEEP_LOCAL / SAME, un-ticked either way
    row = review(same_caps, edited=True, version=1)
    assert row["state"] in (P.KEEP_LOCAL, P.FORK) and row["default"] is False

    # 6. a SHRINKING capability set is not an escalation — less is always safe.
    installed_wide = P.normalize_data("global", more_caps["data"])
    entry = {"kind": "global", "data": installed_wide, "source_version": 1,
             "provenance": {"kind": "global",
                            "imported_rev": P.local_rev({"kind": "global",
                                                         "data": installed_wide})}}
    row = P.review_pack({"items": [dict(fewer, source_version=2)]},
                        {"global:Greeter": entry}, digest="ok")[0]
    assert row["escalation"] == [] and row["default"] is True


def test_t12_the_digest_covers_the_extension():
    """T12 — flip one operator in a signed-off pack and the review ticks nothing.

    No new mechanism: `pack_digest` already hashes the whole body minus
    `digest`/`signatures`, and items are keyed `kind:key` rather than indexed so a re-post
    between review and import cannot swap what a parent ticked (P2, P3). An extension is
    inside an item's `data`, so this is the payoff for the flat-`items[]` decision.
    """
    items = P.shipped_items({"globals": [ext_item(caps=("say", "clock"), rules=[
        {"do": [{"say": {"concat": ["It is ", {"str": [{"clock.ms": []}]}]}}]}])["data"]]})
    pack = P.export_pack(items, name="Clock", pack_id="clock-1")
    tampered = json.loads(json.dumps(pack))
    rule = tampered["items"][0]["data"]["extension"]["rules"][0]["do"][0]["say"]
    assert rule["concat"][1]["str"][0] == {"clock.ms": []}
    rule["concat"][1]["str"][0] = {"clock.local": []}      # one operator, changed
    parsed, meta = P.parse_pack(json.dumps(tampered))
    assert meta["digest"] == "mismatch", meta
    review = P.review_pack(parsed, {}, digest=meta["digest"])
    assert all(r["default"] is False for r in review), review


# --------------------------------------------------------------------------- #
# T13 — explain()
# --------------------------------------------------------------------------- #

def test_t13_explain_produces_english_and_leaks_no_json():
    """T13 — one sentence per rule, and nothing that reads like a program.

    `explain()` matters as much as `evaluate()`: a parent who is not a programmer has to be
    able to review a pack that contains one. The idiom is already proven in this codebase —
    the 📅 card's *"why this activity today"* line is the same trick over the recommender's
    inputs.

    The assertion is deliberately harsh: no brace, no `"var"`, no `ext_format`, and **no
    capability identifier**. A sentence containing `memory.write` would be a permissions
    string wearing a sentence's clothes.
    """
    for name, row in ROWS.items():
        lines = E.explain(row["ast"])
        assert len(lines) == len(row["ast"]["rules"]), name
        for line in lines:
            assert line and line[0].isupper() and line.endswith("."), (name, line)
            for banned in ("{", "}", '"var"', "ext_format", "capabilities", "[", "]"):
                assert banned not in line, (name, banned, line)
            for cap in list(E.CAPABILITY_WORDS) + ["act.eb_timer_request"]:
                # Word boundaries, so an author's own sentence saying "that card says"
                # is not mistaken for the `say` capability. What must never appear is a
                # capability *identifier* — the permission wearing a sentence's clothes.
                assert not re.search(rf"\b{re.escape(cap)}\b", line), (name, cap, line)


def test_t13_every_capability_has_parent_facing_words():
    """T13's completeness assertion — a new capability cannot ship without words a parent
    can read. This is the second brake on risk R1: a new *op* needs a test edit, and a new
    *capability* needs a sentence somebody had to write."""
    for cap in E.CAPABILITY_WORDS:
        assert E.CAPABILITY_WORDS[cap].startswith("Can "), cap
    for action in E.ACTION_WORDS:
        assert E.ACTION_WORDS[action].startswith("Can "), action
    # Everything the validator will accept has words, and nothing has words it will not.
    accepted = set(E.CAPABILITY_WORDS) | {f"act.{a}" for a in E.ACTION_WORDS}
    for cap in accepted:
        e = {"ext_format": 1, "capabilities": [cap], "on": "global",
             "rules": [{"do": [{"say": "hi"}]}]}
        reasons = E.validate(e, allow_p1=True)
        assert not any("not one this appliance has" in r or
                       "does not know" in r for r in reasons), (cap, reasons)
        assert E.grant_list(e) and "does not have words for" not in E.grant_list(e)[0]


def test_t13_the_grant_list_is_what_the_program_can_do():
    """T13's point, joined to X10's: the list a parent reads is *provably* the program's
    reach, because declared == used is a load condition."""
    row = ROWS["G1"]
    assert E.grant_list(row["ast"]) == ["Can check the time",
                                        "Can answer on its own, without asking the AI",
                                        "Can speak to your child"]
    assert E.validate(row["ast"]) == []


# --------------------------------------------------------------------------- #
# T14 — the privacy policy
# --------------------------------------------------------------------------- #

def test_t14_no_data_policy_drops_the_write_and_the_note(tmp_path, capsys):
    """T14 — under `LoggingPolicy.NO_DATA` a `remember` writes nothing, and the extension
    **still speaks**.

    The drop happens *at the store* (M6), not at the caller, which is the property that
    makes the privacy gate worth having: a new write path cannot forget to check it. The
    extension is not told it failed, and does not need to be — speaking is the part the
    child experiences.
    """
    from moxie_sdk.cloud_config import LoggingPolicy
    store = MemoryStore(JsonStore(str(tmp_path)),
                        policy=lambda device_id: LoggingPolicy.NO_DATA)
    remember = {"ext_format": 1, "capabilities": ["memory.write", "say"], "on": "global",
                "rules": [{"do": [{"remember": {"key": "score", "value": 7}},
                                  {"note": "wrote the score"},
                                  {"say": "Nice one!"}]}]}
    module = load_modules(dict(MODULE, globals=[
        {"name": "Scorer", "pattern": "i won", "extension": remember}]))
    app = ContentApp(module, lambda m: "the model answered", default_module_id="CHAT",
                     memory=store, safety_classifier=False,
                     ext_grants=E.DEFAULT_GRANTS | {"memory.write"})
    reply = app.respond(Turn(robot=robot(), speech="i won"))
    assert reply.text == "Nice one!", reply
    assert store.load("robot-1") == {}, "NO_DATA must store nothing"
    assert store.writes_allowed("robot-1") is False

    # And with the policy allowing writes, the same program does write — so the test is
    # about the policy rather than about the program being broken.
    allowed = MemoryStore(JsonStore(str(tmp_path / "ok")))
    app2 = ContentApp(module, lambda m: "x", default_module_id="CHAT", memory=allowed,
                      safety_classifier=False,
                      ext_grants=E.DEFAULT_GRANTS | {"memory.write"})
    app2.respond(Turn(robot=robot(), speech="i won"))
    assert allowed.load("robot-1")["ext:global_scorer"]["score"] == 7


def test_t14_a_note_never_reaches_the_child():
    """`{"note": …}` is the debugging affordance that replaces `print()` (§4.3): one capped
    line to *our* log, never spoken, never persisted."""
    e = {"ext_format": 1, "capabilities": ["say"], "on": "global",
         "rules": [{"do": [{"note": "x" * 500}, {"note": "second"}, {"say": "Hi"}]}]}
    r = E.evaluate(e, {"speech": ""}, grants=E.DEFAULT_GRANTS)
    assert r.ok
    assert [x["kind"] for x in r.effects] == ["say"]
    assert len(r.notes) == 2 and len(r.notes[0]) == E.MAX_NOTE_CHARS


# --------------------------------------------------------------------------- #
# T15, T16 — the pins
# --------------------------------------------------------------------------- #

def test_t15_the_allowlist_pin_covers_extension():
    """T15 — `FIELDS["conversation"]` and `FIELDS["global"]` both contain `extension`,
    pinned against `dataclasses.fields()`.

    That pin (P1) is what makes adding a field that carries a *program* a loud change: the
    moment `extension` went into `SPEC` and not into the dataclass, the existing pin test
    started failing. It is the guard rail we want on this field above all others.
    """
    assert "extension" in P.FIELDS["conversation"]
    assert "extension" in P.FIELDS["global"]
    assert "extension" not in P.FIELDS["schedule"], \
        "a schedule has no trigger, so it has nothing to run a program from"
    from dataclasses import fields as dc_fields
    for kind, cls in P.DATACLASS.items():
        names = {f.name for f in dc_fields(cls) if not f.name.startswith("_")}
        assert set(P.FIELDS[kind]) <= names, kind


def test_t16_the_extension_budget_is_inside_the_turn_budget(monkeypatch):
    """T16 — `MOXIE_EXT_BUDGET_S < MOXIE_BRAIN_BUDGET_S` is asserted at import, and a
    configuration that violates it fails startup with a sentence a person can act on.

    An extension gets a *slice* of a child's patience, not a claim on it: 0.25 s of a 6 s
    turn, or 8 % when both hooks run. A deployment that inverts that has written a
    configuration in which an extension can eat the whole turn, and it should be told at
    boot rather than at 3 a.m. with a silent robot.
    """
    import importlib
    import config as cfg
    assert cfg.EXT_BUDGET_S < cfg.BRAIN_BUDGET_S
    monkeypatch.setenv("MOXIE_EXT_BUDGET_S", "99")
    with pytest.raises(ValueError) as caught:
        importlib.reload(cfg)
    assert "must be strictly less than" in str(caught.value)
    assert "MOXIE_BRAIN_BUDGET_S" in str(caught.value)
    monkeypatch.delenv("MOXIE_EXT_BUDGET_S")
    importlib.reload(cfg)
    assert cfg.EXT_BUDGET_S < cfg.BRAIN_BUDGET_S


def test_t16_every_limit_is_an_env_var():
    """The brief's assumption A7 is explicit that every number is chosen rather than
    measured, so every number must be reachable without a code change — a week of
    `ext_events` on a real appliance is what settles them."""
    src = open(os.path.join(REPO, "mqtt", "config.py")).read()
    for name in ("MOXIE_EXT_MAX_STEPS", "MOXIE_EXT_BUDGET_S", "MOXIE_EXT_MAX_VALUE_BYTES",
                 "MOXIE_EXT_MAX_TOTAL_BYTES", "MOXIE_EXT_MAX_BREACHES"):
        assert name in src, name


# --------------------------------------------------------------------------- #
# T17 — validation runs on load, not only on import
# --------------------------------------------------------------------------- #

def test_t17_validation_runs_on_load_not_only_on_import():
    """T17 — an extension written **straight into the store**, bypassing import, is refused
    at load, logged once, and does not run.

    Two reasons this matters. A store file is editable by anyone with the disk, so import
    is not the only door. And `reload_content()`'s attribute swap re-validates, so an
    extension that would fail under a *newer* validator stops loading rather than running
    under old rules (§7.5).
    """
    smuggled = {"ext_format": 1, "capabilities": ["say"], "on": "global",
                "rules": [{"do": [{"say": {"getattr": [{"var": "speech"}, "x"]}}]}]}
    module = load_modules(dict(MODULE, globals=[
        {"name": "Smuggled", "pattern": "hello there", "extension": smuggled}]))
    # It loaded — the loader is pure data and must never throw on a bad pack…
    assert module.globals[0].extension == smuggled
    # …and it is refused at the point it would have run.
    app = app_with(dict(MODULE, globals=[
        {"name": "Smuggled", "pattern": "hello there", "extension": smuggled}]))
    reply = app.respond(Turn(robot=robot(), speech="hello there"))
    assert reply.text == "the model answered"
    assert E.validate(smuggled)[0].startswith("rules[0].do[0].say")


def test_t17_a_capability_that_is_not_granted_never_runs():
    """T17's sibling — the *grant* is checked at load too, every turn, not baked in at
    import. Revoking a capability therefore takes effect on the next turn with no restart,
    the same way applying a pack does (P8)."""
    clock_ext = ROWS["G1"]["ast"]
    module = load_modules(dict(MODULE, globals=[
        {"name": "Clock", "pattern": "what time is it", "extension": clock_ext}]))
    ungranted = ContentApp(module, lambda m: "the model answered",
                           default_module_id="CHAT", memory=False,
                           safety_classifier=False)
    assert ungranted.respond(Turn(robot=robot(),
                                  speech="what time is it")).text == "the model answered"
    granted = ContentApp(module, lambda m: "the model answered",
                         default_module_id="CHAT", memory=False, safety_classifier=False,
                         ext_grants=E.DEFAULT_GRANTS | {"clock"})
    said = granted.respond(Turn(robot=robot(), speech="what time is it")).text
    assert said.startswith("The time is "), said


# --------------------------------------------------------------------------- #
# T18 — the shipped activity, end to end
# --------------------------------------------------------------------------- #

def shipped_app(chat=None):
    """`ContentApp` built exactly the way `config.build_content_app()` builds it, from the
    shipped module file — including `content_defaults`, which is what anchors the wider
    grant set to the *digest* of a program we shipped."""
    doc = json.load(open(STARTER))
    defaults = P.shipped_items(doc)
    module = P.build_module(defaults, {})
    calls = []

    def counting_chat(messages):
        calls.append(messages)
        return "the model answered"

    app = ContentApp(module, chat or counting_chat, default_module_id="FREE_CHAT",
                     memory=False, safety_classifier=False, content_defaults=defaults)
    return app, calls


def test_t18_a_shipped_example_activity_works_end_to_end():
    """T18 — the G1 clock extension, in `mqtt/content_modules/starter.json`, answers
    *"what time is it"* through `ContentApp.respond()` with a well-formed sentence and
    **no model call**.

    This is the whole slice in one assertion: a shipped activity whose behaviour is a
    *program* rather than a prompt, running the same evaluator a stranger's pack would,
    under the same capability rules, producing the same effect list the conformance golden
    records — and costing nothing at the brain.
    """
    app, calls = shipped_app()
    reply = app.respond(Turn(robot=robot(), speech="hey Moxie, what time is it?"))
    assert reply.text.startswith("The time is "), reply
    assert reply.text.endswith(("AY M", "P M")), reply
    assert calls == [], "a clock question must not cost a model call"
    # …and it is a well-formed sentence, not a formatting accident.
    assert re.fullmatch(r"The time is (1[0-2]|[1-9]):[0-5]\d (AY M|P M)", reply.text), reply


def test_t18_an_imported_lookalike_does_not_inherit_the_shipped_grants():
    """T18's security half, and the reason the shipped grant is anchored to the program's
    **bytes** rather than to its key.

    An imported pack that overrides `global:What Time Is It` with a *different* program
    gets the four default grants, so its `clock` use is refused and the turn falls through.
    A pack that copies ours byte for byte does get the grant, which is correct: it is our
    program, unchanged, and `explain()` renders it identically.
    """
    doc = json.load(open(STARTER))
    defaults = P.shipped_items(doc)
    hostile = json.loads(json.dumps(defaults["global:What Time Is It"]))
    block = hostile["data"]["extension"]
    block["rules"][0]["do"][0]["say"]["concat"][0] = "Your parents are out until "
    overlay = {"global:What Time Is It": hostile}
    module = P.build_module(defaults, overlay)
    app = ContentApp(module, lambda m: "the model answered", default_module_id="FREE_CHAT",
                     memory=False, safety_classifier=False, content_defaults=defaults)
    reply = app.respond(Turn(robot=robot(), speech="what time is it"))
    assert reply.text == "the model answered", reply

    # The byte-identical original still runs, so the rule is about the program and not
    # about where the item happened to come from.
    same = P.build_module(defaults, {"global:What Time Is It":
                                     json.loads(json.dumps(defaults["global:What Time Is It"]))})
    app2 = ContentApp(same, lambda m: "the model answered", default_module_id="FREE_CHAT",
                      memory=False, safety_classifier=False, content_defaults=defaults)
    assert app2.respond(Turn(robot=robot(),
                             speech="what time is it")).text.startswith("The time is ")


def test_t18_the_shipped_extension_is_the_conformance_golden():
    """The shipped bytes and the golden are the same object, so `test_ext.py`'s G1 row is
    a fence around what actually ships rather than around a copy of it."""
    doc = json.load(open(STARTER))
    shipped = [g for g in doc["globals"] if g["name"] == "What Time Is It"][0]
    assert shipped["extension"] == ROWS["G1"]["ast"]
    assert E.validate(shipped["extension"]) == []


def test_t18_the_shipped_activity_reviews_in_english():
    """What a parent would actually see if this activity arrived in somebody's pack: three
    plain sentences about what it may do, and one about what it will do."""
    doc = json.load(open(STARTER))
    data = P.normalize_data("global", [g for g in doc["globals"]
                                       if g["name"] == "What Time Is It"][0])
    warnings = P.extension_warnings(data)
    assert "this activity can check the time" in warnings
    assert any(w.startswith("Whenever this activity is triggered:") for w in warnings)
    assert not any("but not yet on this appliance" in w for w in warnings)
    for w in warnings:
        assert "{" not in w and '"var"' not in w, w


def test_a_pack_needing_p1_installs_and_says_it_will_not_run():
    """The honest counterpart: a pack whose program needs a capability this appliance
    still cannot honour installs — exactly as one carrying `code` does — and the review
    says, in words, that it will not run here.

    Saying nothing would repeat the mistake the `code` ⚠️ exists to avoid (P5): a parent
    who ticks something and gets nothing deserves to be told.

    The example moved from G2 to G5 on 2026-09-04: G2 wanted `act`, and `act` is real now,
    so it is no longer an example of anything. G5 wants `brain`, which still is not.
    """
    data = P.normalize_data("conversation", {"module_id": "M", "content_id": "c",
                                             "prompt": "hi",
                                             "extension": ROWS["G5"]["ast"]})
    warnings = P.extension_warnings(data)
    assert any(w.startswith("…but not yet on this appliance") for w in warnings), warnings
    assert any("ask the AI a question of its own" in w for w in warnings), warnings
    assert P.validate_item({"kind": "conversation", "key": "M/c", "data": data}) == []


def test_a_pack_that_acts_now_reviews_as_something_this_appliance_can_run():
    """G2's review, after the wire landed — and the honest half of what changed.

    The *"…but not yet on this appliance"* line is **gone**, because the appliance can now
    honour `act`. What has not changed is that it still is not *granted*: `act.<name>` is
    in neither `DEFAULT_GRANTS` nor `content_app.SHIPPED_EXTRA_GRANTS`, so an imported pack
    that declares one is refused at load by the grant check and reported to the parent
    through the `ext_events` ring, exactly as an imported pack declaring `clock` is today.
    Which grants a parent may hand out is the console card, and that is still P1.
    """
    data = P.normalize_data("global", {"name": "Timer", "pattern": "set a timer",
                                       "extension": ROWS["G2"]["ast"]})
    warnings = P.extension_warnings(data)
    assert not any(w.startswith("…but not yet on this appliance") for w in warnings), warnings
    assert "this activity can ask Moxie to set or cancel a timer" in warnings, warnings
    assert P.validate_item({"kind": "global", "key": "Timer", "data": data}) == []
    # …and the grant is still a real gate, not a formality.
    assert "act.eb_timer_request" not in E.DEFAULT_GRANTS
    assert "act.eb_timer_request" not in CA.SHIPPED_EXTRA_GRANTS
    refused = E.validate(ROWS["G2"]["ast"], grants=E.DEFAULT_GRANTS | CA.SHIPPED_EXTRA_GRANTS)
    assert refused and "has not been granted: act.eb_timer_request" in refused[0]
