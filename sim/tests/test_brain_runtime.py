"""
🧠 Any brain, hot-swappable, per child — the live half.

`test_brains.py` covers the pure registry: the table, the layering, the pin. This file
covers what a parent's click does to a RUNNING supervisor — which brain answers which
child, on the next turn, with no restart:

  * two robots on one appliance, answered by two different brains in the same process;
  * a swap that lands on the NEXT turn while a turn already in flight finishes with the
    brain it started with (the `voice_update` / `reload_content` rule);
  * an explicit `MOXIE_APP` beating a stored per-child pick, and refusing a stale page's
    with the variable named;
  * a brain that cannot be built keeping the appliance talking, and saying so once;
  * `brain` riding the ordinary config layers and never reaching the robot's document.

Hermetic: the builders arrive through `set_brain_engines()` — the seam the runtime was
given so no test needs `openai`, an endpoint or a key — and the HTTP tier goes through
`MoxieRuntime._start_status_server` itself, so the real handlers are what is exercised.
"""
import json
import os
import sys
import threading
import urllib.error

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MQTT = os.path.join(REPO, "mqtt")
for _p in (MQTT, os.path.join(MQTT, "supervisor")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from helpers_runtime import (drive_turn, http_json, make_runtime,     # noqa: E402
                             status_server)
from moxie_sdk import brains                                          # noqa: E402
from moxie_sdk.app import MoxieApp                                    # noqa: E402
from moxie_sdk.store import JsonStore                                 # noqa: E402
from moxie_sdk.types import Reply, RobotContext                       # noqa: E402

CONFIG_TOPIC = "/devices/{device_id}/config"


def refused(url, body):
    """A POST the status server rejects → `(status code, decoded body)`.

    `http_json` raises `HTTPError` on 4xx by design; the refusal's BODY is the part that
    matters here, because it is the sentence the console shows a parent."""
    with pytest.raises(urllib.error.HTTPError) as exc:
        http_json(url, method="POST", body=body)
    return exc.value.code, json.loads(exc.value.read().decode() or "{}")


class _Brain(MoxieApp):
    """A brain that signs its answers, so a turn's reply says which one produced it."""

    def __init__(self, name):
        self.name = name
        self.heard = []
        self.connected = []
        self.events = []
        self.ended = []

    def respond(self, turn):
        self.heard.append(turn.speech)
        return Reply(text=f"{self.name} heard {turn.speech}")

    def on_connect(self, robot):
        self.connected.append(robot.device_id)

    def on_event(self, robot, name, payload):
        self.events.append((robot.device_id, name))

    def on_session_end(self, robot, history, reason=""):
        self.ended.append((robot.device_id, reason))


class _Slow(_Brain):
    """A brain that holds a turn open until a test lets it finish."""

    def __init__(self, name, gate, entered):
        super().__init__(name)
        self.gate, self.entered = gate, entered

    def respond(self, turn):
        self.entered.set()
        self.gate.wait(5.0)
        return super().respond(turn)


class _Engines:
    """Stands in for `config.BrainEngines`: scripted pin, recording builders.

    `fail` names brains whose build must raise `SystemExit` — the real shape of "this
    brain's environment is missing" (`config.build_brain` → `require_llm_base_url`), which
    must never cost the appliance the brain it already had.
    """

    def __init__(self, *, pin="", default="echo", fail=(), boom=()):
        self.pin, self.default = pin, default
        self.fail, self.boom = set(fail), set(boom)
        self.built = []
        self.made = {}

    def available(self):
        return {"available": brains.filter_options(
                    brains.options(default=self.default), self.pin),
                "pin": self.pin, "pin_note": brains.pin_note(self.pin or ""),
                "default": self.default}

    def build(self, name):
        self.built.append(name)
        if name in self.fail:
            raise SystemExit(f"MOXIE_APP={name} needs MOXIE_LLM_BASE_URL")
        if name in self.boom:
            raise RuntimeError("the builder itself exploded")
        self.made[name] = self.made.get(name) or _Brain(name)
        return self.made[name]


def _runtime(tmp_path, *, app=None, pin="", default="echo", fail=(), boom=(),
             robots=("d_one",)):
    """A runtime on a scratch data dir with the brain seam installed and N robots."""
    app = app if app is not None else _Brain(default)
    rt, first = make_runtime(app, device_id=robots[0], store=JsonStore(str(tmp_path)))
    engines = _Engines(pin=pin, default=default, fail=fail, boom=boom)
    rt.set_brain_engines(engines)
    for device_id in robots[1:]:
        rt.robots[device_id] = RobotContext(device_id=device_id, child=rt.child,
                                            module_id="FREE_CHAT", content_id="default")
    return rt, engines


def _fresh_pool(rt):
    """`drive_turn` drains and shuts the runtime's pool; a test that drives a SECOND turn
    through the same supervisor needs a live one. Re-arming it is the only way to assert
    "the same running process answered the next turn differently", which is the whole
    claim of a swap with no restart."""
    from concurrent.futures import ThreadPoolExecutor
    rt._pool = ThreadPoolExecutor(max_workers=4)


# --------------------------------------------------- one appliance, two brains --

def test_two_children_on_one_appliance_get_two_different_brains(tmp_path):
    """The headline. Same process, same broker, same turn loop — two robots answered by
    two brains, because the per-robot config layer says so."""
    rt, engines = _runtime(tmp_path, default="echo", robots=("d_one", "d_two"))
    rt.update_config("d_two", brain="webhook")

    assert drive_turn(rt, "d_one", "hi")["output"]["text"].startswith("echo heard")
    _fresh_pool(rt)
    assert drive_turn(rt, "d_two", "hi")["output"]["text"].startswith("webhook heard")
    assert engines.built == ["webhook"], \
        "only the brain nobody had was built; the appliance's own was reused"


def test_the_house_rule_applies_to_every_robot_that_has_no_pick_of_its_own(tmp_path):
    rt, _ = _runtime(tmp_path, default="echo", robots=("d_one", "d_two"))
    rt.update_fleet_config(brain="content")
    rt.update_config("d_two", brain="webhook")
    assert rt.brain_for("d_one") == {"brain": "content", "source": "fleet",
                                     "requested": "", "pinned": "", "note": ""}
    assert rt.brain_for("d_two")["brain"] == "webhook"


def test_clearing_a_robot_s_pick_falls_back_to_the_house_rule_not_the_appliance(tmp_path):
    rt, _ = _runtime(tmp_path, default="echo")
    rt.update_fleet_config(brain="content")
    rt.update_config("d_one", brain="webhook")
    assert rt.brain_for("d_one")["brain"] == "webhook"
    rt.update_config("d_one", brain=None)
    assert rt.brain_for("d_one") == {"brain": "content", "source": "fleet",
                                     "requested": "", "pinned": "", "note": ""}


def test_a_brain_is_built_once_and_shared_by_the_children_on_it(tmp_path):
    """Keyed by name, not by device — exactly today's semantics, where one app object
    serves every robot. Building one per child would double a content module's memory
    and quadruple a gateway client's connection pool for no gain."""
    rt, engines = _runtime(tmp_path, default="echo", robots=("d_one", "d_two"))
    rt.update_fleet_config(brain="webhook")
    assert rt.app_for("d_one") is rt.app_for("d_two")
    assert engines.built == ["webhook"]


# ------------------------------------------------------- the swap, on the wire --

def test_a_swap_lands_on_the_next_turn_of_the_same_running_supervisor(tmp_path):
    """No restart, no reconnect, no dropped turn — `reload_content`'s attribute-swap
    rule, applied to the brain itself."""
    rt, _ = _runtime(tmp_path, default="echo")
    assert drive_turn(rt, "d_one", "one")["output"]["text"].startswith("echo heard")
    _fresh_pool(rt)
    rt.brain_update({"brain": "content"}, device_id="d_one")
    assert drive_turn(rt, "d_one", "two")["output"]["text"].startswith("content heard")


def test_a_turn_already_in_flight_finishes_with_the_brain_it_started_with(tmp_path):
    """The other half of the rule, and the one that would be invisible until a child hit
    it: a parent pressing Save while Moxie is thinking must not make the answer arrive
    from a different brain than the one that heard the question."""
    gate, entered = threading.Event(), threading.Event()
    slow = _Slow("echo", gate, entered)
    rt, engines = _runtime(tmp_path, app=slow, default="echo")
    robot = rt.robots["d_one"]
    rt._on_remote_chat("d_one", robot, json.dumps(
        {"command": "prompt", "backend": "router", "event_id": "evt-1",
         "speech": "mid-flight"}))
    assert entered.wait(5.0), "the slow brain never started"
    rt.brain_update({"brain": "webhook"}, device_id="d_one")   # ← the swap, mid-answer
    gate.set()
    rt._pool.shutdown(wait=True)
    answer = rt.client.chat_replies("d_one")[-1]["output"]["text"]
    assert answer.startswith("echo heard"), "the turn changed brains mid-answer"
    _fresh_pool(rt)
    assert drive_turn(rt, "d_one", "after")["output"]["text"].startswith("webhook heard")


def test_the_brain_is_resolved_once_per_turn_not_once_per_lookup(tmp_path):
    """A turn that consulted the layers twice could straddle a swap, and the window is
    real rather than theoretical: `respond_stream` is asked first, and a parent's Save can
    land between that question and `respond`. So the swap is performed from INSIDE that
    window, and the answer must still come from the brain the turn started with."""
    rt, _ = _runtime(tmp_path, default="echo")

    class _Switching(_Brain):
        def respond_stream(self, turn):
            rt.update_config("d_one", brain="webhook")   # ← the swap, mid-turn
            return None                                  # …and this brain does not stream

    rt.app = rt._brains["echo"] = _Switching("echo")
    assert drive_turn(rt, "d_one", "hi")["output"]["text"].startswith("echo heard")
    assert rt.brain_for("d_one")["brain"] == "webhook", \
        "the layers really did change during the turn"


# --------------------------------------------------------------------- the pin --

def test_an_explicit_moxie_app_beats_a_stored_per_child_pick(tmp_path):
    """The owner rule (PR #77, for `MOXIE_TTS`/`MOXIE_STT`): the operator's environment
    is a statement about the box and a dropdown does not talk it out of it."""
    rt, _ = _runtime(tmp_path, default="echo", pin="echo")
    rt._config_overrides["d_one"] = {"brain": "webhook"}    # written before the pin existed
    resolved = rt.brain_for("d_one")
    assert (resolved["brain"], resolved["source"], resolved["requested"]) == \
        ("echo", "pin", "webhook")
    assert drive_turn(rt, "d_one", "hi")["output"]["text"].startswith("echo heard")


def test_a_stale_page_posting_a_pinned_away_brain_is_refused_naming_the_variable(tmp_path):
    rt, _ = _runtime(tmp_path, default="echo", pin="echo")
    out = rt.brain_update({"brain": "content"}, device_id="d_one")
    assert out["ok"] is False
    assert brains.ENV_VAR in out["error"]
    assert rt._config_overrides.get("d_one", {}).get("brain") is None, \
        "a refused pick must not be stored"


def test_the_card_offers_only_the_pinned_brain_and_says_why(tmp_path):
    rt, _ = _runtime(tmp_path, default="echo", pin="echo")
    view = rt.brain_view()
    assert brains.option_ids(view["available"]) == ["echo"]
    assert brains.ENV_VAR in view["pin_note"]


def test_with_no_pin_every_brain_is_offered(tmp_path):
    rt, _ = _runtime(tmp_path, default="echo")
    assert brains.option_ids(rt.brain_view()["available"]) == list(brains.BRAIN_IDS)
    assert rt.brain_view()["pin_note"] == ""


# ------------------------------------------------------- refusals and failures --

def test_a_brain_nobody_can_build_keeps_the_appliance_talking(tmp_path):
    """A downgrade caused by an attempt to improve things is the worst shape a failure
    can take (`_install_voice`'s rule). The child still gets an answer."""
    rt, _ = _runtime(tmp_path, default="echo", fail=("llm",))
    rt.update_config("d_one", brain="llm")
    assert drive_turn(rt, "d_one", "hi")["output"]["text"].startswith("echo heard")
    assert any("could not be built" in n.get("text", "") for n in rt.recent), \
        "…and the operator is told, in the console's own activity feed"


def test_a_brain_that_will_not_build_is_reported_once_not_once_per_turn(tmp_path):
    rt, _ = _runtime(tmp_path, default="echo", fail=("llm",))
    rt.update_config("d_one", brain="llm")
    for _ in range(4):
        rt.app_for("d_one")
    assert sum(1 for n in rt.recent if "could not be built" in n.get("text", "")) == 1


def test_a_builder_that_raises_anything_at_all_is_survived(tmp_path):
    rt, _ = _runtime(tmp_path, default="echo", boom=("content",))
    rt.update_config("d_one", brain="content")
    assert rt.app_for("d_one") is rt.app, "kept the brain we already had"


def test_an_unknown_brain_is_refused_with_the_four_real_names(tmp_path):
    rt, _ = _runtime(tmp_path, default="echo")
    out = rt.brain_update({"brain": "gpt5"}, device_id="d_one")
    assert out["ok"] is False
    for name in brains.BRAIN_IDS:
        assert name in out["error"]
    assert "brain" not in rt._config_overrides.get("d_one", {})


def test_a_pick_for_a_robot_this_appliance_does_not_serve_is_refused(tmp_path):
    rt, _ = _runtime(tmp_path, default="echo")
    assert rt.brain_update({"brain": "echo"}, device_id="d_nope")["ok"] is False


def test_with_no_builders_installed_the_card_still_renders_and_the_box_still_talks(tmp_path):
    """`set_brain_engines` is optional — a runtime built by a test, or an embedder using
    the SDK directly, has none. The honest floor is the real table plus the brain that is
    actually running."""
    rt, _device = make_runtime(_Brain("echo"), device_id="d_one",
                               store=JsonStore(str(tmp_path)))
    rt.update_config("d_one", brain="webhook")
    view = rt.brain_view()
    assert brains.option_ids(view["available"]) == list(brains.BRAIN_IDS)
    assert rt.app_for("d_one") is rt.app
    assert drive_turn(rt, "d_one", "hi")["output"]["text"].startswith("echo heard")


# ------------------------------------------- `brain` is an ordinary config key --

def test_the_pushed_document_never_carries_the_brain(tmp_path):
    """`brain` rides the config layers because they are the one layering this codebase
    has. The robot has no field for it — and `build_robot_cloud_config` raises on an
    unexpected keyword, so without `robot_config_kwargs` this is a crash on every push."""
    rt, _ = _runtime(tmp_path, default="echo")
    rt.update_fleet_config(brain="content")
    rt.update_config("d_one", brain="webhook", audio_volume=0.4)
    docs = rt.client.on(CONFIG_TOPIC.format(device_id="d_one"))
    assert docs, "the config was pushed"
    assert "brain" not in docs[-1]
    assert docs[-1]["audio_volume"] == 0.4, "…and the ordinary overrides still went out"


def test_the_brain_is_stored_in_the_same_place_as_every_other_parent_setting(tmp_path):
    """One store, one layering. `fleet/config.json` already holds the house rules; a
    second file for the brain would be a second thing to back up, migrate and disagree."""
    rt, _ = _runtime(tmp_path, default="echo")
    rt.brain_update({"brain": "content"}, scope="fleet")
    assert rt.fleet_config()["brain"] == "content"
    assert json.loads((tmp_path / "fleet" / "config.json").read_text())["brain"] \
        == "content"


def test_the_snapshot_says_which_brain_answers_each_robot(tmp_path):
    rt, _ = _runtime(tmp_path, default="echo", robots=("d_one", "d_two"))
    rt.update_config("d_two", brain="content")
    snap = rt.status_snapshot()
    by_id = {r["device_id"]: r for r in snap["robots"]}
    assert by_id["d_one"]["brain"] == "echo"
    assert by_id["d_two"]["brain"] == "content"
    assert by_id["d_two"]["brain_source"] == "robot"
    assert snap["app"] == "echo", "the appliance's own brain still reads as it always did"


# --------------------------------------------------- everything a brain hears --

def test_the_brain_that_answers_a_robot_is_the_one_that_hears_its_lifecycle(tmp_path):
    """A per-child brain that never heard an event or the end of a session would be a
    different kind of brain than the appliance's own — the seam has to be whole. Long-term
    memory hangs off `on_session_end` in particular (`content-module-contract.md`), so a
    brain that is not told would lose the child's whole conversation."""
    rt, engines = _runtime(tmp_path, default="echo")
    rt.update_config("d_one", brain="webhook")
    drive_turn(rt, "d_one", "hello")
    rt._on_event("d_one", "eb-module-start", {})
    rt._end_conversation("d_one", "exit", inline=True)
    hook = engines.made["webhook"]
    assert hook.heard == ["hello"]
    assert ("d_one", "eb-module-start") in hook.events
    assert hook.ended == [("d_one", "exit")]
    assert rt.app.events == [] and rt.app.ended == [], \
        "and the appliance's own brain was not told about a child it does not serve"


def test_a_built_brain_gets_the_same_memory_privacy_gate_as_the_default_one(tmp_path):
    """A privacy switch that applied to one brain and not another would be worse than
    none, because nobody would know which child it covered."""
    class _WithMemory(_Brain):
        def __init__(self, name):
            super().__init__(name)
            self.memory = type("M", (), {"policy": None})()

    engines = _Engines(default="echo")
    engines.made["content"] = _WithMemory("content")
    rt, _device = make_runtime(_Brain("echo"), device_id="d_one",
                               store=JsonStore(str(tmp_path)))
    rt.set_brain_engines(engines)
    rt.update_config("d_one", brain="content")
    assert rt.app_for("d_one").memory.policy == rt.memory_policy


def test_a_content_pack_reaches_a_child_who_is_the_only_one_running_content(tmp_path):
    """`reload_content` used to swap `self.app.module`. On an appliance whose default is
    `llm`, the child actually running the content engine would never have seen the pack
    a parent just installed."""
    from moxie_sdk.content import packs as content_packs

    class _Content(_Brain):
        def __init__(self, name):
            super().__init__(name)
            self.content_defaults = {}
            self.module = content_packs.build_module({}, {})

    engines = _Engines(default="echo")
    engines.made["content"] = _Content("content")
    rt, _device = make_runtime(_Brain("echo"), device_id="d_one",
                               store=JsonStore(str(tmp_path)))
    rt.set_brain_engines(engines)
    rt.update_config("d_one", brain="content")
    before = rt.app_for("d_one").module
    out = rt.reload_content()
    assert out["live"] is True, "the swap found a live content brain"
    assert rt.app_for("d_one").module is not before


# ------------------------------------------------------------------- the HTTP --

def test_the_card_reads_the_whole_picture_over_http(tmp_path):
    rt, _ = _runtime(tmp_path, default="echo", robots=("d_one", "d_two"))
    rt.update_fleet_config(brain="content")
    rt.update_config("d_two", brain="webhook")
    base = status_server(rt)
    view = http_json(f"{base}/brain")
    assert view["ok"] is True
    assert view["fleet"] == "content"
    assert brains.option_ids(view["available"]) == list(brains.BRAIN_IDS)
    rows = {r["device_id"]: r for r in view["robots"]}
    assert rows["d_one"]["brain"] == "content" and rows["d_one"]["source"] == "fleet"
    assert rows["d_two"]["brain"] == "webhook" and rows["d_two"]["source"] == "robot"
    assert "webhook" in rows["d_two"]["line"]


def test_a_post_picks_a_brain_for_one_child(tmp_path):
    rt, _ = _runtime(tmp_path, default="echo")
    base = status_server(rt)
    out = http_json(f"{base}/brain?device_id=d_one", method="POST",
                    body={"brain": "content"})
    assert out["ok"] is True
    assert out["applied"] == {"scope": "robot", "device_id": "d_one", "brain": "content"}
    assert rt.brain_for("d_one")["brain"] == "content"


def test_a_post_at_fleet_scope_sets_the_house_rule(tmp_path):
    rt, _ = _runtime(tmp_path, default="echo")
    base = status_server(rt)
    out = http_json(f"{base}/brain?scope=fleet", method="POST", body={"brain": "webhook"})
    assert out["applied"]["scope"] == "fleet"
    assert rt.fleet_config()["brain"] == "webhook"


def test_a_post_naming_a_brain_that_does_not_exist_is_a_400(tmp_path):
    rt, _ = _runtime(tmp_path, default="echo")
    base = status_server(rt)
    code, out = refused(f"{base}/brain?device_id=d_one", {"brain": "gpt5"})
    assert code == 400 and out["ok"] is False
    for name in brains.BRAIN_IDS:
        assert name in out["error"]


def test_a_post_for_an_unknown_robot_is_a_404(tmp_path):
    rt, _ = _runtime(tmp_path, default="echo")
    base = status_server(rt)
    code, out = refused(f"{base}/brain?device_id=d_nope", {"brain": "echo"})
    assert code == 404 and out["ok"] is False


def test_the_ordinary_config_route_can_set_a_brain_too(tmp_path):
    """The card is a convenience, not the only door: `brain` is a whitelisted config
    override, so `POST /config` — which every existing console already speaks — sets it."""
    rt, _ = _runtime(tmp_path, default="echo")
    base = status_server(rt)
    out = http_json(f"{base}/config?device_id=d_one", method="POST",
                    body={"brain": "content", "audio_volume": 0.5})
    assert out["ok"] is True
    assert out["config_effective"]["brain"] == "content"
    assert rt.brain_for("d_one")["brain"] == "content"


def test_the_ordinary_config_route_refuses_a_brain_that_does_not_exist(tmp_path):
    rt, _ = _runtime(tmp_path, default="echo")
    base = status_server(rt)
    code, out = refused(f"{base}/config?device_id=d_one", {"brain": "gpt5"})
    assert code == 400 and out["ok"] is False


def test_an_appliance_nobody_has_configured_behaves_exactly_as_it_did(tmp_path):
    """The floor: with no fleet record, no per-robot override and no pin, every robot is
    answered by the appliance's own brain — byte-for-byte the pre-registry behaviour."""
    rt, engines = _runtime(tmp_path, default="echo", robots=("d_one", "d_two"))
    for device_id in ("d_one", "d_two"):
        assert rt.app_for(device_id) is rt.app
        assert rt.brain_for(device_id)["source"] == "default"
    assert engines.built == []
