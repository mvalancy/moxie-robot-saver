"""
Are the two SIM clients really interchangeable? — asserted, not claimed.

`docs/architecture/sim-as-a-client.md` promises that the headless SIL robot
(`sim/virtual_moxie.py`) and the browser SIM (`sim/web/bridge.js`) are drop-in
replacements for each other, and DoD criterion 4 rests on it. Downstream that was true
and tested (both decode the same `/config`, `commands/remote_chat` and `CloudTTSResponse`).
**Upstream it was not true at all**: the SIL robot has always published
`events/client-service-activity-log` — the schedule pull, the `mentor_behavior` report,
the telehealth state — and the browser SIM published nothing on that topic, so it could
not ask the cloud anything and reported no robot state.

This file is the parity guard for the robot→cloud direction, in three parts:

1. **The reference.** `sim/tests/goldens/robot_to_cloud_activity.json` is exactly what the
   SIL robot puts on that topic. Asserted here against the live `VirtualMoxie`, so the
   golden cannot go stale.
2. **The other client.** `sim/web/bridge.js` builds the same envelopes with the same keys
   in the same order — read structurally out of the JS source, so a Python-only CI run
   (no node, no browser) still catches drift. The *runtime* comparison of the envelopes
   the browser actually publishes lives in `sim/test_bridge.mjs`, which loads this same
   golden; between them the two clients cannot diverge without a test going red.
3. **The delta.** The fields that legitimately differ — which robot is speaking and when —
   are the golden's `identity_keys`, and nothing else is allowed to differ.

Plus the cloud→robot half of the same interchangeability claim: the browser SIM must know
every `ActionType` the server can send, and decode `query_result` with the same
`CloudQueryResponse` field table the SIL robot uses.

Hermetic and instant: no broker, no network, no node, no browser.
"""
import json
import os
import re
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "sim"))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

GOLDEN_PATH = os.path.join(os.path.dirname(__file__), "goldens",
                           "robot_to_cloud_activity.json")
BRIDGE_PATH = os.path.join(REPO, "sim", "web", "bridge.js")

with open(GOLDEN_PATH) as _fh:
    GOLDEN = json.load(_fh)
BRIDGE = open(BRIDGE_PATH, encoding="utf-8").read()


# --------------------------------------------------------------------------- #
# Reading object literals out of the JS, without a JS engine
# --------------------------------------------------------------------------- #
def _balanced(src: str, start: int) -> str:
    """`src[start]` is `{` — the substring through its matching `}`."""
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError("unbalanced object literal in bridge.js")


def _literal(anchor: str, opener: str = "publishActivity({") -> str:
    """The object literal `opener` opens, in the first place `anchor` appears."""
    at = BRIDGE.index(anchor)
    return _balanced(BRIDGE, BRIDGE.index(opener, at) + len(opener) - 1)


_KEY_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*:")


def _keys(literal: str) -> list:
    """The key names of a JS object literal, in source order, top level only."""
    keys, depth, i = [], 0, 0
    while i < len(literal):
        c = literal[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif depth == 1 and (i == 0 or literal[i - 1] in "{,\n\t "):
            m = _KEY_RE.match(literal, i)
            if m:
                keys.append(m.group(1))
                i = m.end() - 1
        i += 1
    return keys


def _js_string_map(name: str) -> dict:
    """A `const NAME = { a: "x", … };` table in bridge.js, as a dict."""
    at = BRIDGE.index(f"const {name} = {{")
    body = _balanced(BRIDGE, BRIDGE.index("{", at))
    return dict(re.findall(r'([A-Za-z_][A-Za-z0-9_]*)\s*:\s*"([^"]*)"', body))


# --------------------------------------------------------------------------- #
# 1. The golden is what the SIL robot actually publishes
# --------------------------------------------------------------------------- #
class _Recorder:
    """Stands in for paho: keeps `(topic, decoded)` for every publish."""

    def __init__(self):
        self.published = []

    def publish(self, topic, payload):
        self.published.append((topic, json.loads(payload)))


@pytest.fixture(scope="module")
def sil_envelopes():
    pytest.importorskip("paho.mqtt.client", reason="the SIL robot needs paho")
    from virtual_moxie import VirtualMoxie
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_golden", verbose=False)
    vm.client = _Recorder()
    vm.send_query("schedule")
    vm.report_mentor_behavior({"module_id": "DRAW", "content_id": "default",
                               "action": "completed", "timestamp": 1788360800925})
    vm.report_telehealth_state("IN_SESSION", "ths-1")
    return vm.client.published


def _compare(path, want, got, out):
    """Same keys in the same order, same values — except the golden's identity keys,
    which are compared by JSON type only."""
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
    topics = {t for (t, _) in sil_envelopes}
    assert topics == {f"/devices/d_golden/{GOLDEN['topic_suffix']}"}, topics
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


def test_the_goldens_documented_key_order_is_its_own_key_order():
    """`key_order` is what both clients are held to; it must not be able to lie."""
    for kind, spec in GOLDEN["envelopes"].items():
        assert spec["key_order"] == list(spec["payload"]), kind
        if "message_key_order" in spec:
            assert spec["message_key_order"] == list(spec["payload"]["message"]), kind


# --------------------------------------------------------------------------- #
# 2. The browser SIM builds the same envelopes
# --------------------------------------------------------------------------- #
def test_the_browser_sim_publishes_on_the_recovered_topic():
    assert f'dev("{GOLDEN["topic_suffix"]}")' in BRIDGE, (
        "bridge.js must publish the activity log on the topic the SIL robot uses")


@pytest.mark.parametrize("kind,anchor", [
    ("query", "function sendQuery(query) {"),
    ("mentor_behavior", "function reportMentorBehavior(mbh) {"),
    ("telehealth_state", "function reportTelehealthState(state, sessionId) {"),
])
def test_the_browser_sim_uses_the_same_envelope_keys_in_the_same_order(kind, anchor):
    literal = _literal(anchor)
    assert _keys(literal) == GOLDEN["envelopes"][kind]["key_order"], (
        f"{kind}: bridge.js key order {_keys(literal)} != the SIL robot's "
        f"{GOLDEN['envelopes'][kind]['key_order']}")


def test_the_browser_sims_telehealth_event_has_the_same_inner_message():
    literal = _literal("function reportTelehealthState(state, sessionId) {")
    inner = _balanced(literal, literal.index("{", literal.index("message:")))
    assert _keys(inner) == GOLDEN["envelopes"]["telehealth_state"]["message_key_order"]


@pytest.mark.parametrize("subtopic", ["query", "telehealth"])
def test_the_browser_sim_uses_the_same_subtopic_values(subtopic):
    assert f'subtopic: "{subtopic}"' in BRIDGE, subtopic


def test_the_browser_sim_reports_its_firmware_the_way_the_sil_robot_does():
    """`software_version` is NOT an identity key — a client lying about the build it
    speaks would make every telemetry comparison meaningless."""
    from virtual_moxie import FIRMWARE                       # noqa: E402
    assert f'const FIRMWARE = "{FIRMWARE}";' in BRIDGE
    assert GOLDEN["envelopes"]["query"]["payload"]["software_version"] == FIRMWARE


def test_the_two_clients_name_themselves_differently_and_that_is_the_whole_point():
    """`module_name` and the device id in `auid` are the delta the doc records: they say
    WHICH client is speaking. If they were equal the log could not tell them apart."""
    assert 'const MODULE_NAME = "sim-web";' in BRIDGE
    assert "module_name" in GOLDEN["identity_keys"]
    assert GOLDEN["envelopes"]["query"]["payload"]["module_name"] == "virtual-moxie"


# --------------------------------------------------------------------------- #
# 3. …and the cloud→robot half of the same claim
# --------------------------------------------------------------------------- #
def test_the_browser_sim_acts_on_response_actions_at_all():
    """The gap this slice closed. `grep -rn response_actions sim/web/*.js` used to return
    nothing: the server sent actions and no client consumed them."""
    assert "response_actions" in BRIDGE
    assert "response_action" in BRIDGE, "the legacy singular must be read too"


def test_the_browser_sim_knows_every_action_type_the_server_can_send():
    from moxie_sdk.types import ActionType
    at = BRIDGE.index("const ACTION_KINDS = [")
    kinds = set(re.findall(r'"([a-z_]+)"', _balanced_list(BRIDGE, at)))
    assert kinds == {a.value for a in ActionType}, (
        f"bridge.js implements {sorted(kinds)}; ActionType defines "
        f"{sorted(a.value for a in ActionType)}")


def _balanced_list(src: str, at: int) -> str:
    start = src.index("[", at)
    return src[start:src.index("]", start) + 1]


def test_an_unknown_action_is_counted_and_skipped_rather_than_thrown():
    """The behaviour is asserted for real in `sim/test_bridge.mjs`; this pins the two
    source properties that make it possible, so neither can be deleted quietly."""
    assert "actionState.unknown += 1" in BRIDGE
    assert "catch (e) { actionState.unknown += 1;" in BRIDGE


def test_both_clients_decode_query_result_with_the_same_proto_field_table():
    pytest.importorskip("paho.mqtt.client", reason="the SIL robot needs paho")
    from virtual_moxie import VirtualMoxie
    assert _js_string_map("QUERY_FIELD") == dict(VirtualMoxie.QUERY_FIELD)


def test_the_browser_sim_subscribes_to_the_answers_it_asks_for():
    assert 'client.subscribe("/devices/+/commands/query_result")' in BRIDGE
