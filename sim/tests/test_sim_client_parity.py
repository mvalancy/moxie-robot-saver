"""Are the two SIM clients interchangeable? The headless SIL robot (`sim/virtual_moxie.py`)
and the browser SIM (`sim/web/bridge/`) are held to the same goldens
(`goldens/robot_to_cloud_activity.json`, `goldens/cloud_to_robot_actions.json`).

Split of duties: this file RUNS the SIL robot against the goldens (so they cannot go stale)
and compares the cross-language TABLES a JS runtime cannot see from Python (action verbs vs
`ActionType`, stat/applied keys, the query-field table). The browser's runtime behaviour
against the same goldens is `sim/test_bridge.mjs` and `sim/test_action_payload.mjs`.
"""
import json
import os
import re
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "sim"))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from helpers_web import script_group  # noqa: E402

GOLDENS = os.path.join(os.path.dirname(__file__), "goldens")
with open(os.path.join(GOLDENS, "robot_to_cloud_activity.json")) as _fh:
    GOLDEN = json.load(_fh)
with open(os.path.join(GOLDENS, "cloud_to_robot_actions.json")) as _fh:
    ACTIONS_GOLDEN = json.load(_fh)
BRIDGE = script_group("bridge")


# ---- reading tables out of the JS, without a JS engine ----------------------------
def _balanced(src: str, start: int) -> str:
    """`src[start]` is `{` — the substring through its matching `}`."""
    depth = 0
    for i in range(start, len(src)):
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        if depth == 0:
            return src[start:i + 1]
    raise AssertionError("unbalanced object literal in sim/web/bridge/")


_KEY_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*:")


def _keys(literal: str) -> list:
    """The key names of a JS object literal, in source order, top level only."""
    keys, depth, i = [], 0, 0
    while i < len(literal):
        c = literal[i]
        depth += {"{": 1, "}": -1}.get(c, 0)
        if depth == 1 and c not in "{}" and (i == 0 or literal[i - 1] in "{,\n\t "):
            m = _KEY_RE.match(literal, i)
            if m:
                keys.append(m.group(1))
                i = m.end() - 1
        i += 1
    return keys


def _object_after(anchor: str, opener: str = "{") -> str:
    return _balanced(BRIDGE, BRIDGE.index(opener, BRIDGE.index(anchor)))


def _vm(device_id="d_parity"):
    pytest.importorskip("paho.mqtt.client", reason="the SIL robot needs paho")
    from virtual_moxie import VirtualMoxie
    return VirtualMoxie(host="127.0.0.1", port=1, device_id=device_id, verbose=False)


# ---- robot → cloud: the golden IS what the SIL robot publishes ----------------------
class _Recorder:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload):
        self.published.append((topic, json.loads(payload)))


@pytest.fixture(scope="module")
def sil_envelopes():
    vm = _vm("d_golden")
    vm.client = _Recorder()
    vm.send_query("schedule")
    vm.report_mentor_behavior({"module_id": "DRAW", "content_id": "default",
                               "action": "completed", "timestamp": 1788360800925})
    vm.report_telehealth_state("IN_SESSION", "ths-1")
    return vm.client.published


def _compare(path, want, got, out):
    """Same keys in the same order, same values — identity keys by JSON type only."""
    if path in GOLDEN["identity_keys"]:
        if type(want) is not type(got):
            out.append(f"{path}: identity field is {type(got).__name__}, "
                       f"golden has {type(want).__name__}")
        return
    if isinstance(want, dict):
        if not isinstance(got, dict):
            return out.append(f"{path or '<root>'}: expected an object, got {got!r}")
        if list(want) != list(got):
            out.append(f"{path or '<root>'}: keys {list(got)} != {list(want)}")
        for k in want:
            _compare(f"{path}.{k}" if path else k, want[k], got.get(k), out)
        return
    if want != got:
        out.append(f"{path}: {got!r} != {want!r}")


def test_the_sil_robot_publishes_all_three_envelopes_on_one_topic(sil_envelopes):
    assert {t for (t, _) in sil_envelopes} == {f"/devices/d_golden/{GOLDEN['topic_suffix']}"}
    assert len(sil_envelopes) == 3, sil_envelopes


@pytest.mark.parametrize("kind", ["query", "mentor_behavior", "telehealth_state"])
def test_the_golden_still_matches_the_sil_robot(kind, sil_envelopes):
    picks = {"query": lambda p: p.get("subtopic") == "query",
             "mentor_behavior": lambda p: "mentor_behavior" in p,
             "telehealth_state": lambda p: p.get("subtopic") == "telehealth"}
    got = next(p for (_, p) in sil_envelopes if picks[kind](p))
    out = []
    _compare("", GOLDEN["envelopes"][kind]["payload"], got, out)
    assert not out, f"{kind} drifted from the golden:\n  " + "\n  ".join(out)


# ---- cloud → robot: the tables both clients must share ------------------------------
def test_both_clients_decode_query_result_with_the_same_proto_field_table():
    body = _object_after("const QUERY_FIELD = {")
    browser = dict(re.findall(r'([A-Za-z_][A-Za-z0-9_]*)\s*:\s*"([^"]*)"', body))
    assert browser == dict(type(_vm()).QUERY_FIELD)


def test_all_three_action_vocabularies_are_the_same_list():
    """What the server sends (the recovered `ActionID` names, `types.ACTION_IDS`) plus the
    two older spellings both clients keep accepting (`LEGACY_ACTION_NAMES`), what the
    browser implements and what the SIL robot implements. Two clients that implement
    different verbs are not peers; a server verb neither client knows is a dropped turn."""
    from moxie_sdk.types import (ACTION_IDS, LEGACY_ACTION_NAMES, Action, ActionType)
    from moxie_sdk.wire import encode_action
    at = BRIDGE.index("[", BRIDGE.index("const ACTION_KINDS = ["))
    browser = set(re.findall(r'"([a-z_]+)"', BRIDGE[at:BRIDGE.index("]", at)]))
    import virtual_moxie
    accepted = set(ACTION_IDS) | set(LEGACY_ACTION_NAMES)
    assert browser == accepted == set(virtual_moxie.ACTION_KINDS) \
        == set(ACTIONS_GOLDEN["action_kinds"])
    # …and every `ActionType` really does go out under one of the recovered names.
    sent = {encode_action(Action(type=t))["action"] for t in ActionType}
    assert sent == set(ACTION_IDS), sent


def test_both_clients_report_what_an_action_did_under_the_same_names():
    """`bridge/actionStats()` and `VirtualMoxie.action_stats()` are what every test reads."""
    at = BRIDGE.index("actionStats = function ()")
    browser = set(_keys(_balanced(BRIDGE, BRIDGE.index("{", BRIDGE.index("return", at)))))
    assert browser == set(_vm().action_stats()) == set(ACTIONS_GOLDEN["stat_keys"])


def test_both_clients_record_an_applied_action_under_the_same_keys_in_the_same_order():
    """Shared keys in the golden's order, plus exactly the golden's documented
    per-client extras (so `client_only_keys` cannot lie either)."""
    shared, extras = ACTIONS_GOLDEN["applied_keys"], ACTIONS_GOLDEN["client_only_keys"]
    extra = {c: [k.split("[].", 1)[1] for k in keys] for c, keys in extras.items()}
    browser = _keys(_object_after("actionState.applied.push({"))
    assert browser == shared + extra["sim/web/bridge/actions.js"], browser
    vm = _vm()
    vm._on_chat_reply({"command": "remote_chat", "event_id": "e", "output": {"text": ""},
                       "response_actions": [{"output_type": "GLOBAL_RESPONSE", "action": "execute",
                                             "function_id": "f", "function_args": ["a"]}]})
    assert list(vm.action_stats()["applied"][0]) == shared + extra["sim/virtual_moxie.py"]


def test_the_sil_robot_decodes_the_execute_payload_exactly_as_the_golden_says():
    """The reference client, run — `sim/test_action_payload.mjs` holds the browser to the
    same `execute_expected`, entry by entry."""
    vm = _vm()
    for response in ACTIONS_GOLDEN["execute_script"]:
        vm._on_chat_reply({k: v for k, v in response.items() if k != "_why"})
    keys = ACTIONS_GOLDEN["applied_keys"]
    got = [{k: a[k] for k in keys} for a in vm.action_stats()["applied"]]
    assert got == ACTIONS_GOLDEN["execute_expected"], got


def test_the_two_clients_are_driven_over_the_same_action_script():
    """`sim/test_bridge.mjs` hard-codes its responses; each golden one must be among them
    or `expected_state` is two separate stories rather than one claim about both clients."""
    peer = open(os.path.join(REPO, ACTIONS_GOLDEN["peer_test"]), encoding="utf-8").read()
    missing = [r["event_id"] for r in ACTIONS_GOLDEN["script"] if f'"{r["event_id"]}"' not in peer]
    assert not missing, f"golden responses not driven by {ACTIONS_GOLDEN['peer_test']}: {missing}"
