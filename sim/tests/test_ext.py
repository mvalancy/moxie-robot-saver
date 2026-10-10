"""T1–T18 — behaviour, determinism and integration for sandboxed content extensions.

`test_ext_escapes.py` asks "can a stranger's pack hurt this appliance?". This file asks
whether the language expresses what authors wrote (T1–T7, the hand-ported upstream hooks)
and whether a broken extension still leaves the child a working robot (T8–T18).

Design: `docs/architecture/backlog/sandboxed-extensions.md`. Prior art: OpenMoxie (MIT,
© Justin Beghtol) — cited, hand-ported, never copied; see `ATTRIBUTION.md`.
"""
import json
import os
import re

import pytest

from helpers_ext import CHAT_MODULE, app_with, robot
from moxie_sdk.content import ext as E
from moxie_sdk.content import packs as P
from moxie_sdk.content import content_app as CA
from moxie_sdk.content.content_app import ContentApp
from moxie_sdk.content.module import load_modules
from moxie_sdk.store import JsonStore
from moxie_sdk.memory_store import MemoryStore
from moxie_sdk.types import Turn

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

CONFORMANCE = os.path.join(os.path.dirname(__file__), "data", "ext_conformance.json")
STARTER = os.path.join(REPO, "mqtt", "content_modules", "starter.json")


def rows():
    with open(CONFORMANCE, encoding="utf-8") as fh:
        return {r["name"]: r for r in json.load(fh)["rows"]}


ROWS = rows()


def run_row(row, *, allow_p1=False):
    """Evaluate one conformance row exactly as the file records it."""
    return E.evaluate(row["ast"], row["facts"], grants=set(row["grants"]),
                      now_ms=row["now_ms"], clock_local=row["clock_local"],
                      seed=row["seed"])


# --------------------------------------------------------------------------- #
# T1–T6 — the six §8 hooks reproduce their goldens byte for byte
# --------------------------------------------------------------------------- #

#: What each remaining xfail row still lacks (a stale reason is a lie the suite repeats).
P1_REASON = {
    "G5": "needs the `brain` capability and its one-call-per-turn budget (brief §5.1)",
}


@pytest.mark.parametrize("name", ["G1", "G2", "G3", "G4", "G6"])
def test_t1_t6_conformance(name):
    """T1–T6 — each hand-ported upstream hook reproduces its golden byte for byte, under the
    real grant gate (no `allow_p1` door). G6's subscribing rules live in `test_ext_subscribe`."""
    row = ROWS[name]
    assert E.validate(row["ast"], grants=set(row["grants"])) == []
    r = run_row(row)
    assert r.ok, r.reason
    assert r.effects == row["expected_effects"], r.effects
    assert r.handled == row["expected_handled"]


@pytest.mark.parametrize("name", ["G5"])
def test_t1_t6_conformance_still_p1(name, request):
    """T5 — strict xfail: the day `brain` becomes grantable, the XPASS fails the suite."""
    request.node.add_marker(pytest.mark.xfail(strict=True, reason=P1_REASON[name]))
    row = ROWS[name]
    r = run_row(row)
    assert r.ok, r.reason
    assert r.effects == row["expected_effects"]


# --------------------------------------------------------------------------- #
# T7 — determinism
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", ["G1", "G3"])
def test_t7_the_same_inputs_give_byte_identical_effects(name):
    """T7 — 100 runs at a fixed injected clock and seed produce one golden (§6.1): no
    ambient clock/entropy, sorted `keys`, total `sort`, explicit `format` specs."""
    row = ROWS[name]
    first = json.dumps(run_row(row).effects, sort_keys=True)
    for _ in range(100):
        assert json.dumps(run_row(row).effects, sort_keys=True) == first
    assert json.loads(first) == row["expected_effects"]


def test_t7_a_different_clock_changes_the_answer():
    """T7's other direction — a constant evaluator would pass the test above."""
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

MODULE = dict(CHAT_MODULE, globals=[{"name": "Broken", "pattern": "tell me a story",
                                      "extension": POISON}])


def test_t8_a_breach_does_not_end_the_turn():
    """T8 — Moxie keeps talking (§6.4): a poisoned global falls through, a poisoned
    turn.before is skipped, and the child hears no mention of a failure."""
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
    """T9 — after `MOXIE_EXT_MAX_BREACHES` a broken extension is not evaluated at all, and
    the parent gets ONE `ext_events` entry."""
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
    """T9 — a breach code without parent-facing words would show as a bare identifier."""
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
    """T10 — export → parse → review → apply → live next turn (§7.1, §7.5); the exported
    bytes re-import to the same digest."""
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
    """T10 — `SPEC`'s `_d` coercer round-trips JSON, so a stored extension is provably
    JSON-only before the validator sees it (A11, §4.4)."""
    data = P.normalize_data("global", ext_item(caps=("say", "clock"))["data"])
    assert data["extension"]["capabilities"] == ["say", "clock"]
    assert json.loads(json.dumps(data["extension"])) == data["extension"]
    # Unknown keys inside `data` are dropped, and `extension` is not one of them.
    assert "extension" not in P.dropped_fields("global", ext_item()["data"])


def test_t11_a_capability_escalation_defaults_unticked():
    """T11 — §7.3's matrix, over the capability SET: a version bump cannot escalate
    privileges and a shrinking set is never a conflict."""
    base = ext_item(caps=("say",))
    installed_data = P.normalize_data("global", base["data"])
    rev = P.local_rev({"kind": "global", "data": installed_data})

    def review(incoming, *, edited=False, version=2):
        # "Edited" (P4) = `imported_rev` no longer matching the stored data's digest.
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
    """T12 — flip one operator and the review ticks nothing (P2, P3)."""
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
    """T13 — one sentence per rule, and nothing that reads like a program."""
    for name, row in ROWS.items():
        lines = E.explain(row["ast"])
        assert len(lines) == len(row["ast"]["rules"]), name
        for line in lines:
            assert line and line[0].isupper() and line.endswith("."), (name, line)
            for banned in ("{", "}", '"var"', "ext_format", "capabilities", "[", "]"):
                assert banned not in line, (name, banned, line)
            for cap in list(E.CAPABILITY_WORDS) + ["act.eb_timer_request"]:
                # Word boundaries: "that card says" is prose, not the `say` identifier.
                assert not re.search(rf"\b{re.escape(cap)}\b", line), (name, cap, line)


def test_t13_every_capability_has_parent_facing_words():
    """T13 — a new capability cannot ship without words a parent can read (brake on R1)."""
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
    """T13 + X10: declared == used is a load condition, so the list is the program's reach."""
    row = ROWS["G1"]
    assert E.grant_list(row["ast"]) == ["Can check the time",
                                        "Can answer on its own, without asking the AI",
                                        "Can speak to your child"]
    assert E.validate(row["ast"]) == []


# --------------------------------------------------------------------------- #
# T14 — the privacy policy
# --------------------------------------------------------------------------- #

def test_t14_no_data_policy_drops_the_write_and_the_note(tmp_path, capsys):
    """T14 — under `NO_DATA` a `remember` is dropped at the store (M6) and the extension
    still speaks."""
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

    # The same program does write when the policy allows it.
    allowed = MemoryStore(JsonStore(str(tmp_path / "ok")))
    app2 = ContentApp(module, lambda m: "x", default_module_id="CHAT", memory=allowed,
                      safety_classifier=False,
                      ext_grants=E.DEFAULT_GRANTS | {"memory.write"})
    app2.respond(Turn(robot=robot(), speech="i won"))
    assert allowed.load("robot-1")["ext:global_scorer"]["score"] == 7


def test_t14_a_note_never_reaches_the_child():
    """`note` replaces `print()` (§4.3): capped, logged, never spoken or persisted."""
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
    """T15 — `extension` is in the field allowlist, pinned against the dataclasses (P1)."""
    assert "extension" in P.FIELDS["conversation"]
    assert "extension" in P.FIELDS["global"]
    assert "extension" not in P.FIELDS["schedule"], \
        "a schedule has no trigger, so it has nothing to run a program from"
    from dataclasses import fields as dc_fields
    for kind, cls in P.DATACLASS.items():
        names = {f.name for f in dc_fields(cls) if not f.name.startswith("_")}
        assert set(P.FIELDS[kind]) <= names, kind


def test_t16_the_extension_budget_is_inside_the_turn_budget(monkeypatch):
    """T16 — an inverted budget fails startup with an actionable sentence."""
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


def test_t16_every_limit_is_an_env_var(monkeypatch):
    """A7: every limit is chosen, not measured, so each must be tunable without a code change."""
    import importlib
    import config as cfg
    limits = {"MOXIE_EXT_MAX_STEPS": ("EXT_MAX_STEPS", 123),
              "MOXIE_EXT_BUDGET_S": ("EXT_BUDGET_S", 0.125),
              "MOXIE_EXT_MAX_VALUE_BYTES": ("EXT_MAX_VALUE_BYTES", 1234),
              "MOXIE_EXT_MAX_TOTAL_BYTES": ("EXT_MAX_TOTAL_BYTES", 12345),
              "MOXIE_EXT_MAX_BREACHES": ("EXT_MAX_BREACHES", 7)}
    for env, (_attr, value) in limits.items():
        monkeypatch.setenv(env, str(value))
    try:
        importlib.reload(cfg)
        assert {a: getattr(cfg, a) for a, _v in limits.values()} == dict(limits.values())
    finally:
        for env in limits:
            monkeypatch.delenv(env)
        importlib.reload(cfg)


# --------------------------------------------------------------------------- #
# T17 — validation runs on load, not only on import
# --------------------------------------------------------------------------- #

def test_t17_validation_runs_on_load_not_only_on_import():
    """T17 — an extension written straight into the store is refused where it would run."""
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
    """T17 — the grant is checked every turn, so revoking one needs no restart (P8)."""
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
    """`ContentApp` as `config.build_content_app()` builds it, including `content_defaults`
    (which anchors the wider grant set to a shipped program's digest)."""
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
    """T18 — the shipped clock extension answers with no model call."""
    app, calls = shipped_app()
    reply = app.respond(Turn(robot=robot(), speech="hey Moxie, what time is it?"))
    assert calls == [], "a clock question must not cost a model call"
    assert re.fullmatch(r"The time is (1[0-2]|[1-9]):[0-5]\d (AY M|P M)", reply.text), reply


def test_t18_the_shipped_clock_tells_the_time_in_the_zone_the_robot_was_told(monkeypatch):
    """K10 — `clock.local` is the house's wall clock: the zone the robot's config push named
    (`robot.extra["timezone_id"]`), never this process's. The process runs on Asia/Kolkata
    here (+05:30, so not even the minutes of its clock can pass for the house's): at 02:30Z
    on 8 October 2026 a robot told Los Angeles says 7:30 P M, one told Berlin 4:30 AY M, one
    told nothing the default zone's time, and a zone this server cannot read is UTC (the
    runtime labels it). The program is the G1 golden, byte for byte (the fence above)."""
    import datetime
    import time
    from moxie_sdk.content.ext_host import _clock_local
    monkeypatch.delenv("MOXIE_TIMEZONE", raising=False)
    asked = datetime.datetime(2026, 10, 8, 2, 30, tzinfo=datetime.timezone.utc).timestamp()
    doc = json.load(open(STARTER))
    defaults = P.shipped_items(doc)
    app = ContentApp(P.build_module(defaults, {}), lambda m: "the model answered",
                     default_module_id="FREE_CHAT", memory=False, safety_classifier=False,
                     content_defaults=defaults, clock=lambda: asked)

    def said(zone):
        told = robot()
        if zone is not None:
            told.extra["timezone_id"] = zone
        return app.respond(Turn(robot=told, speech="what time is it")).text

    before = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Kolkata"
    time.tzset()
    try:
        assert datetime.datetime.fromtimestamp(asked).strftime("%H:%M") == "08:00", \
            "this process's own clock must disagree with every house here"
        assert said("America/Los_Angeles") == "The time is 7:30 P M"
        assert said("Europe/Berlin") == "The time is 4:30 AY M"
        assert said(None) == "The time is 7:30 P M"          # the default: Los Angeles
        assert said("Mars/Olympus") == "The time is 2:30 AY M"
        assert _clock_local(asked, "Asia/Tokyo") == {
            "hour": 11, "minute": 30, "weekday": 4, "iso": "2026-10-08T11:30:00"}
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        time.tzset()


def test_t18_an_imported_lookalike_does_not_inherit_the_shipped_grants():
    """T18 — the shipped grant is anchored to the program's BYTES: a different program under
    the same key gets default grants; a byte-identical copy is ours."""
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

    same = P.build_module(defaults, {"global:What Time Is It":
                                     json.loads(json.dumps(defaults["global:What Time Is It"]))})
    app2 = ContentApp(same, lambda m: "the model answered", default_module_id="FREE_CHAT",
                      memory=False, safety_classifier=False, content_defaults=defaults)
    assert app2.respond(Turn(robot=robot(),
                             speech="what time is it")).text.startswith("The time is ")


def test_t18_the_shipped_extension_is_the_conformance_golden():
    """The G1 golden fences what actually ships, not a copy of it."""
    doc = json.load(open(STARTER))
    shipped = [g for g in doc["globals"] if g["name"] == "What Time Is It"][0]
    assert shipped["extension"] == ROWS["G1"]["ast"]
    assert E.validate(shipped["extension"]) == []


def test_t18_the_shipped_activity_reviews_in_english():
    """What a parent sees if this activity arrives in a pack: plain sentences only."""
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
    """A pack needing `brain` (G5) installs, and the review says it will not run here (P5)."""
    data = P.normalize_data("conversation", {"module_id": "M", "content_id": "c",
                                             "prompt": "hi",
                                             "extension": ROWS["G5"]["ast"]})
    warnings = P.extension_warnings(data)
    assert any(w.startswith("…but not yet on this appliance") for w in warnings), warnings
    assert any("ask the AI a question of its own" in w for w in warnings), warnings
    assert P.validate_item({"kind": "conversation", "key": "M/c", "data": data}) == []


def test_a_pack_that_acts_now_reviews_as_something_this_appliance_can_run():
    """`act` is honoured, so G2 reviews as runnable — but `act.<name>` is still not granted
    to imports, so one declaring it is refused at load."""
    data = P.normalize_data("global", {"name": "Timer", "pattern": "set a timer",
                                       "extension": ROWS["G2"]["ast"]})
    warnings = P.extension_warnings(data)
    assert not any(w.startswith("…but not yet on this appliance") for w in warnings), warnings
    assert "this activity can ask Moxie to set or cancel a timer" in warnings, warnings
    assert P.validate_item({"kind": "global", "key": "Timer", "data": data}) == []
    # The grant is a real gate: `eb_wake` is declared but ungranted.
    waker = dict(ROWS["G2"]["ast"])
    # No `handled`: an unused capability's reason would mask the grant reason.
    waker["capabilities"] = ["say", "act.eb_wake"]
    waker["rules"] = [{"do": [{"act": {"name": "eb_wake", "args": []}}, {"say": "ok"}]}]
    refused = E.validate(waker, grants=E.DEFAULT_GRANTS | CA.SHIPPED_EXTRA_GRANTS)
    assert refused and "has not been granted: act.eb_wake" in refused[0]
