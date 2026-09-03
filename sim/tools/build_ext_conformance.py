"""Regenerate `sim/tests/data/ext_conformance.json` — the six hand-ported OpenMoxie
hooks of `docs/architecture/backlog/sandboxed-extensions.md` section 8.

Run: `python3 sim/tools/build_ext_conformance.py` (from the repo root, with `mqtt/` on
the path). The file it writes is committed; the test reads the file, never this script,
so a bug here cannot quietly rewrite the goldens it is supposed to be checked against.
"""
import json, sys, collections
import os
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(REPO)
sys.path.insert(0, os.path.join(REPO, "mqtt"))
from moxie_sdk.content import ext as E
from moxie_sdk import vocab

CHIME = vocab.audio_mark(vocab.SFX_STINGER)
BREAK = vocab.break_mark("1s")

G1 = {
  "ext_format": 1,
  "capabilities": ["clock", "handled", "say"],
  "on": "global",
  "rules": [
    {"let": {
       "h24": {"get": [{"clock.local": []}, "hour", 0]},
       "minute": {"get": [{"clock.local": []}, "minute", 0]},
       "h12": {"if": [{"==": [{"%": [{"var": "h24"}, 12]}, 0]},
                      12, {"%": [{"var": "h24"}, 12]}]},
       "half": {"if": [{"<": [{"var": "h24"}, 12]}, "AY M", "P M"]}},
     "do": [
       {"say": {"concat": ["The time is ", {"str": [{"var": "h12"}]}, ":",
                           {"format": ["02d", {"var": "minute"}]}, " ",
                           {"var": "half"}]}},
       {"handled": True}]}]}

G2 = {
  "ext_format": 1,
  "capabilities": ["act.eb_timer_request", "clock", "handled", "memory.write", "say"],
  "on": "global",
  "rules": [
    {"let": {
       "count": {"int": [{"var": "entities.0"}]},
       "unit_ms": {"get": [{"lit": {"second": 1000, "minute": 60000, "hour": 3600000}},
                           {"var": "entities.1"}, 1000]},
       "expiry": {"+": [{"clock.ms": []},
                        {"*": [{"var": "count"}, {"var": "unit_ms"}]}]}},
     "do": [
       {"remember": {"key": "timers.1", "value": {"var": "expiry"}}},
       {"act": {"name": "eb_timer_request",
                "args": ["1", {"str": [{"var": "expiry"}]}]}},
       {"say": {"concat": ["Starting timer for ", {"str": [{"var": "count"}]}, " ",
                           {"plural": [{"var": "entities.1"}, {"var": "count"}]}]}},
       {"handled": True}]}]}

_REMAIN = {"-": [{"var": "memory.timers.1"}, {"clock.ms": []}]}
G3 = {
  "ext_format": 1,
  "capabilities": ["act.eb_timer_request", "clock", "handled", "memory.read",
                   "memory.write", "say"],
  "on": "global",
  "rules": [
    {"when": {"==": [{"lower": [{"var": "entities.0"}]}, "status"]},
     "let": {
       "left": {"max": [0, {"floor": [{"/": [_REMAIN, 1000]}]}]},
       "h": {"floor": [{"/": [{"var": "left"}, 3600]}]},
       "m": {"floor": [{"/": [{"%": [{"var": "left"}, 3600]}, 60]}]},
       "s": {"%": [{"var": "left"}, 60]},
       "parts": {"compact": [{"list": [
           {"if": [{">": [{"var": "h"}, 0]},
                   {"concat": [{"str": [{"var": "h"}]}, " ",
                               {"plural": ["hour", {"var": "h"}]}]}, ""]},
           {"if": [{">": [{"var": "m"}, 0]},
                   {"concat": [{"str": [{"var": "m"}]}, " ",
                               {"plural": ["minute", {"var": "m"}]}]}, ""]},
           {"if": [{">": [{"var": "s"}, 0]},
                   {"concat": [{"str": [{"var": "s"}]}, " ",
                               {"plural": ["second", {"var": "s"}]}]}, ""]}]}]}},
     "do": [
       {"say": {"if": [{">": [{"len": [{"var": "parts"}]}, 0]},
                       {"concat": ["You have ",
                                   {"join": [{"var": "parts"}, ", "]}, " left."]},
                       "That timer is already done."]}},
       {"handled": True}]},
    {"when": {"==": [{"lower": [{"var": "entities.0"}]}, "cancel"]},
     "do": [
       {"act": {"name": "eb_timer_request", "args": ["1", "0"]}},
       {"forget": {"key": "timers.1"}},
       {"say": "Okay, I cancelled that timer."},
       {"handled": True}]}]}

G4 = {
  "ext_format": 1,
  "capabilities": ["handled", "markup", "memory.write", "say", "session"],
  "on": "turn.before",
  "rules": [
    {"when": {"session.is_empty": []},
     "let": {"line": "Ding ding ding! Your timer is done."},
     "do": [
       {"scratch": {"key": "fired_timer", "value": {"var": "input_vars.eb_timer_id"}}},
       {"forget": {"key": "timers.1"}},
       {"say": {"var": "line"},
        "markup": {"concat": [{"repeat": [CHIME + BREAK, 3]}, {"var": "line"}]}},
       {"handled": True}]}]}

G5 = {
  "ext_format": 1,
  "capabilities": ["brain", "memory.read", "random", "say"],
  "on": "turn.before",
  "rules": [
    {"let": {"pick": {"random.pick": [{"var": "memory.summaries"}]}},
     "do": [
       {"brain": {"prompt": {"concat": [
           "Greet the child warmly in one sentence, referring to this: ",
           {"str": [{"var": "pick"}]}]}}},
       {"say": {"concat": ["Last time we talked about ",
                           {"str": [{"var": "pick"}]}, "."]}}]}]}

G6 = {
  "ext_format": 1,
  "capabilities": ["act.eb_enable_qr", "handled", "say", "subscribe"],
  "on": "turn.before",
  "rules": [
    {"when": {"==": [{"trim": [{"var": "speech"}]}, ""]},
     "do": [
       {"act": {"name": "eb_enable_qr", "args": ["true"]}},
       {"subscribe": ["eb-qr-event"]},
       {"say": "Show me a card and I will read it!"},
       {"handled": True}]},
    {"when": {"and": [{"==": [{"var": "speech"}, "eb-qr-event"]},
                      {"starts_with": [{"var": "input_vars.eb_qr_value"}, "GO"]}]},
     "do": [
       {"say": {"concat": ["That card says ",
                           {"slice": [{"var": "input_vars.eb_qr_value"}, 2]}, "!"]}},
       {"handled": True}]},
    {"when": {"==": [{"var": "speech"}, "eb-qr-event"]},
     "do": [
       {"act": {"name": "eb_enable_qr", "args": ["true"]}},
       {"subscribe": ["eb-qr-event"]},
       {"say": "Hmm, I did not know that one. Try another card!"},
       {"handled": True}]}]}

ROWS = collections.OrderedDict()

def facts(**kw):
    base = {"speech": "", "entities": [], "input_vars": {}, "scratch": {},
            "child": {"nickname": "Sam"},
            "session": {"total_volleys": 0, "is_empty": True, "overflow": False},
            "presence": {"face_present": False, "line": ""}, "memory": {}}
    base.update(kw)
    return base

NOW = 1_756_900_000_000          # a fixed injected epoch-ms; nothing reads a real clock
LOCAL = {"hour": 15, "minute": 5, "weekday": 3, "iso": "2026-09-03T15:05:00"}

ROWS["G1"] = dict(
    upstream="MoxieTime.get_response", ast=G1, p1=False,
    grants=sorted(E.DEFAULT_GRANTS | {"clock"}),
    facts=facts(speech="what time is it"), now_ms=NOW, clock_local=LOCAL, seed=1)
ROWS["G2"] = dict(
    upstream="MoxieTimers handle_volley (set)", ast=G2, p1=True,
    grants=sorted(E.DEFAULT_GRANTS | {"clock", "memory.write", "act.eb_timer_request"}),
    facts=facts(speech="set a timer for 5 minutes", entities=["5", "minute"]),
    now_ms=NOW, clock_local=LOCAL, seed=1)
ROWS["G3"] = dict(
    upstream="MoxieTimers handle_volley (status/cancel)", ast=G3, p1=True,
    grants=sorted(E.DEFAULT_GRANTS | {"clock", "memory.read", "memory.write",
                                      "act.eb_timer_request"}),
    facts=facts(speech="timer status", entities=["status"],
                memory={"timers": {"1": NOW + 3_725_000}}),
    now_ms=NOW, clock_local=LOCAL, seed=1)
ROWS["G4"] = dict(
    upstream="MoxieTimers pre_process + notify_handler", ast=G4, p1=False,
    grants=sorted(E.DEFAULT_GRANTS | {"markup", "memory.write"}),
    facts=facts(input_vars={"eb_timer_id": "1"}), now_ms=NOW, clock_local=LOCAL, seed=1)
ROWS["G5"] = dict(
    upstream="MemoryChat post_process + complete_handler", ast=G5, p1=True,
    grants=sorted(E.DEFAULT_GRANTS | {"memory.read", "random", "brain"}),
    facts=facts(memory={"summaries": ["the dinosaur museum", "a rainy walk",
                                      "your new bike"]}),
    now_ms=NOW, clock_local=LOCAL, seed=7)
ROWS["G6"] = dict(
    upstream="MoxieGo pre_process + complete_handler", ast=G6, p1=True,
    grants=sorted(E.DEFAULT_GRANTS | {"act.eb_enable_qr", "subscribe"}),
    facts=facts(speech="eb-qr-event",
                input_vars={"eb_qr_value": "GOdinosaur_quiz"}),
    now_ms=NOW, clock_local=LOCAL, seed=1)

# The goldens for the four rows P0 declines to GRANT are still computed here, because
# their grammar is valid today (brief section 8). One predicate is lifted, in this
# generator only — `evaluate()` itself has no such door.
E._is_p1 = lambda cap: False

out = []
for name, row in ROWS.items():
    reasons = E.validate(row["ast"], allow_p1=True)
    assert not reasons, (name, reasons)
    r = E.evaluate(row["ast"], row["facts"], grants=set(row["grants"]),
                   now_ms=row["now_ms"], clock_local=row["clock_local"],
                   seed=row["seed"])
    assert r.ok, (name, r.reason)
    row = dict(row, name=name, expected_effects=r.effects, expected_handled=r.handled,
               explain=E.explain(row["ast"]), grant_list=E.grant_list(row["ast"]))
    out.append(row)
    print(name, "->", json.dumps(r.effects)[:180])
    for line in row["explain"]:
        print("   ", line)

# NOT sort_keys: `let` is an ORDERED map (§4.3) — each binding is visible to the ones
# after it, so sorting the keys would silently reorder every program in this file. The
# escape suite caught exactly that.
doc = {
  "_comment": [
    "Conformance golden set for sandboxed content extensions (BEYOND #6 P0).",
    "docs/architecture/backlog/sandboxed-extensions.md section 8: all six upstream",
    "OpenMoxie hooks (MIT, (c) Justin Beghtol) HAND-PORTED into our AST. No upstream",
    "code is in this file: these are re-authored programs, and the table in the brief",
    "cites which hook each one ports. It is simultaneously the proof the grammar is",
    "expressive enough, the regression suite, and (P1) the Python<->JavaScript contract.",
    "Rows with p1=true declare a capability P0 cannot grant yet (act/subscribe/brain);",
    "their GRAMMAR is asserted valid today so the day the wire lands they turn green."],
  "ext_format": E.EXT_FORMAT,
  "rows": out,
}
path = os.path.join(REPO, "sim", "tests", "data", "ext_conformance.json")
open(path, "w").write(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
print("wrote", path)
