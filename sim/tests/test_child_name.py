"""Moxie says the child's name: the parent's record reaches the robot (K7).

The parent types the child's first name in the web app, and the console sends it to the
supervisor as `child: {nickname}` on that robot's own layer (`POST /config?device_id=…`,
`server/moxie_server/child_profile.py`). Until now the supervisor had one fleet-wide
`ChildProfile` from `MOXIE_CHILD_NICKNAME` (default "friend"), and the whitelist dropped a
`child` key, so every robot heard "Hi friend!" whatever the parent typed. What each test
pins:

* the whitelist: one name rule, shared with the Try it card (its safety, NFC and marks are
  `test_child_name_safety.py`'s); a refused name changes nothing; a house rule names no
  child; `child` is never a builder kwarg;
* the runtime: the next `/config`, both brains, the walk-back-in hello, the opener, the day
  plan and `/status` name the parent's child, per robot, at once and after a restart;
* a setting saved for a robot that is away is kept, and lands when it connects;
* K4's fail-closed race: a parent's save that ends failing closed keeps the transcript even
  when a transcript save lands inside it;
* the name never reaches the supervisor's log, the activity feed or telemetry on any path
  this slice adds, and a cleared name leaves no copy in the robot's files.

A child's name is personal data: the only names here are 'Sam', 'José', 'Zoë' and the
pairing placeholder 'Moxie Kid'. No broker: the transport is `helpers_runtime.FakeClient`.
"""
import datetime
import json
import os

import pytest

from moxie_sdk.cloud_config import sanitize_config_overrides     # noqa: E402
from moxie_sdk.store import JsonStore                           # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG = "/devices/{}/config"


def _app(name="content"):
    from moxie_sdk.app import MoxieApp
    from moxie_sdk.types import Reply

    class _App(MoxieApp):
        def respond(self, turn):
            return Reply(text="ok")

    _App.name = name
    return _App()


def _runtime(tmp_path, devices=("d_one", "d_two"), *, app=None, **kw):
    """A runtime on `tmp_path` whose appliance profile is the default "friend", with
    `devices` connected. The store goes to the constructor, which reads saved settings."""
    pytest.importorskip("paho.mqtt.client", reason="the runtime imports paho")
    from helpers_runtime import make_runtime
    from moxie_sdk.types import RobotContext
    rt, first = make_runtime(app or _app(), device_id=devices[0], nickname="friend",
                             store=JsonStore(root=str(tmp_path)), **kw)
    for d in devices[1:]:
        rt.robots[d] = RobotContext(device_id=d, child=rt.child)
    return rt


def _name(rt, device_id, nickname):
    """What the console's `POST /config?device_id=…` does with `{"child": …}`."""
    return rt.update_config(device_id, **sanitize_config_overrides(
        {"child": None if nickname is None else {"nickname": nickname}}))


def _pushed(rt, device_id):
    msgs = rt.client.on(CONFIG.format(device_id))
    assert msgs, f"no config pushed to {device_id}"
    return msgs[-1]


def _record(tmp_path, device_id="d_one"):
    return tmp_path / "robots" / device_id / "config.json"


# --------------------------------------------------------------------------- #
# the whitelist: one name rule
# --------------------------------------------------------------------------- #

def test_the_whitelist_keeps_a_childs_name_and_refuses_what_is_not_one():
    s = sanitize_config_overrides
    assert s({"child": {"nickname": "  Sam  "}}) == {"child": {"nickname": "Sam"}}
    assert s({"child": {"nickname": "José"}}) == {"child": {"nickname": "José"}}
    assert s({"child": {"nickname": "Zoë"}}) == {"child": {"nickname": "Zoë"}}
    assert s({"child": {"nickname": "Moxie  Kid"}}) == {"child": {"nickname": "Moxie Kid"}}
    assert s({"child": {"nickname": "x" * 40}}) == {"child": {"nickname": "x" * 40}}
    assert s({"child": None}) == {"child": None}                     # clears
    # nothing in the parent app collects pronouns or notes: they are dropped, not stored
    assert s({"child": {"nickname": "Sam", "pronouns": "they", "notes": "…"}}) == {
        "child": {"nickname": "Sam"}}
    assert s({"child": {"nickname": "Sam", "birthday": "2019-05-01"}}) == {
        "child": {"nickname": "Sam", "birthday": "2019-05-01"}}
    for bad in ("<exit>", "{{ x }}", "Sam\nexit", "Sam\r\n", "Sam\u2028", "", "   ",
                "x" * 41, 5, None):
        with pytest.raises(ValueError):
            s({"child": {"nickname": bad}})
    for bad in ("Sam", ["Sam"], {}):
        with pytest.raises(ValueError):
            s({"child": bad})
    for bad in ("2019-13-01", "05/01/2019", "2019-5-1", 20190501):
        with pytest.raises(ValueError):
            s({"child": {"nickname": "Sam", "birthday": bad}})


def test_the_try_it_card_and_the_childs_name_share_one_rule():
    """One rule, `cloud_config.check_name` (the shape, NFC, Moxie's safety table, K7.1),
    which the Try it card imports: the card's behaviour is otherwise unchanged (a blank
    try names no one; a line break typed for a try folds into a space), and a child's
    name is stricter in exactly those two places. `is` alone cannot tell the import from a
    copy, so the source is checked for one: no name pattern, cap or rule of its own."""
    import inspect
    import json as _json
    import unicodedata
    from moxie_sdk import cloud_config
    from moxie_runtime import tryit
    assert tryit._check_name is cloud_config.check_name           # a drifted copy fails
    source = inspect.getsource(tryit)
    assert "NAME_RE" not in source and "TRY_MAX_NAME_CHARS = " not in source \
        and "def check_name" not in source, \
        "tryit keeps its own copy of the name rule: import it from moxie_sdk.cloud_config"
    assert cloud_config.NAME_RE.pattern == r"^[\w .'\-]+$"
    assert tryit.TRY_MAX_NAME_CHARS == cloud_config.NAME_MAX_CHARS == 40
    assert tryit._try_name("") == "" and tryit._try_name("Sam\nB") == "Sam B"
    # The allowed names punctuated: a period, an apostrophe and a hyphen, each of which the
    # two sides must treat alike (a fixture with none lets six such divergences pass); a
    # decomposed name and one written with vowel signs, which both now take, composed.
    for raw in ("Sam", " José ", "Zoë", "Moxie Kid", "x" * 40, "Sam. Zoë-José O'Sam",
                unicodedata.normalize("NFD", "Zoë"), "सैम"):
        assert tryit._try_name(raw) == cloud_config.clean_child_name(raw)
    with open(os.path.join(REPO, "mqtt", "moxie_sdk", "safety_rules.json")) as fh:
        listed = [w for c in _json.load(fh)["categories"] for w in (c.get("words") or [])[:1]]
    for raw in ("<exit>", "{{ x }}", "x" * 41, "Sam!", "\u0301Sam", *listed):
        with pytest.raises(ValueError):
            tryit._try_name(raw)
        with pytest.raises(ValueError):
            cloud_config.clean_child_name(raw)


def test_the_child_is_never_a_builder_kwarg_and_reaches_child_pii():
    from moxie_sdk.cloud_config import (SERVER_ONLY_KEYS, build_robot_cloud_config,
                                        child_profile_for, robot_config_kwargs)
    from moxie_sdk.types import ChildProfile
    layer = {"child": {"nickname": "Sam", "birthday": "2019-05-01"}, "audio_volume": 0.5}
    assert "child" in SERVER_ONLY_KEYS
    assert robot_config_kwargs(layer) == {"audio_volume": 0.5}
    friend = ChildProfile(nickname="friend")
    cfg = build_robot_cloud_config(child_profile_for(layer, friend),
                                   **robot_config_kwargs(layer))
    assert cfg["child_pii"] == {"nickname": "Sam", "birthday": "2019-05-01"}
    # no record, a cleared one, or one the whitelist would refuse: the appliance's own
    for no in ({}, None, {"child": None}, {"child": {"nickname": "<exit>"}},
               {"child": "Sam"}):
        assert child_profile_for(no, friend) is friend


# --------------------------------------------------------------------------- #
# the runtime: every place Moxie names the child
# --------------------------------------------------------------------------- #

def test_the_name_a_parent_typed_is_the_name_moxie_says(tmp_path):
    """Through the console's own route: the next `/config`, the robot's context, `/status`,
    the llm brain's prompt and the face cache id say the parent's name for that robot;
    another robot with none keeps the appliance's."""
    from helpers_runtime import http_call, http_json, status_server
    from moxie_sdk.apps.llm_app import LLMApp
    from moxie_sdk.faces import face_child_id, face_options_list, validate_face
    rt = _runtime(tmp_path)
    face = {"eye_color": "teal"}
    rt.update_config("d_one", face=face)
    base = status_server(rt)
    code, out = http_call(f"{base}/config?device_id=d_one", method="POST",
                          body={"child": {"nickname": "Sam"}})
    assert code == 200 and out["applied"] == {"child": {"nickname": "Sam"}}, out
    assert (out["saved"], out["online"], out["pushed"]) == (True, True, True)
    assert _pushed(rt, "d_one")["child_pii"]["nickname"] == "Sam"
    assert rt.robots["d_one"].child.nickname == "Sam"
    status = {r["device_id"]: r for r in http_json(f"{base}/status")["robots"]}
    assert (status["d_one"]["child"], status["d_two"]["child"]) == ("Sam", "friend")
    llm = LLMApp("http://127.0.0.1:1/v1", "unused", client=object())
    assert "You are talking to Sam." in llm._system(rt.robots["d_one"])
    assert "You are talking to friend." in llm._system(rt.robots["d_two"])
    labels = face_options_list(validate_face(face))
    assert status["d_one"]["face_cache_id"] == face_child_id(labels, child_key="Sam")
    assert rt.face_cache_id("d_one") == _pushed(rt, "d_one")["child_pii"]["id"]
    rt._push_config("d_two")
    assert _pushed(rt, "d_two")["child_pii"]["nickname"] == "friend"


def test_the_content_brain_names_the_child_over_the_shipped_modules(tmp_path):
    """The content brain renders `volley.config.child_pii.nickname` from the robot's
    child: the shipped Memory Chat's system message, and the shipped Free Chat opener."""
    from helpers_runtime import drive_turn
    from moxie_sdk.content import ContentApp, load_module
    seen = []

    def chat(messages):
        seen.append(messages)
        return "That sounds fun!"

    def module(name):
        with open(os.path.join(REPO, "mqtt", "content_modules", name)) as fh:
            return load_module(json.load(fh))

    app = ContentApp(module("memory_chat.json"), chat, memory=False)
    rt = _runtime(tmp_path, devices=("d_one",), app=app, module_id="MEMORY_CHAT")
    _name(rt, "d_one", "Sam")
    drive_turn(rt, "d_one", "I built a fort")
    system = seen[0][0]["content"]
    assert "talking with your friend Sam." in system, system[:200]
    assert "your friend friend" not in system
    starter = ContentApp(module("starter.json"), chat, memory=False)
    rt.robots["d_one"].module_id = "FREE_CHAT"
    assert starter.greeting(rt.robots["d_one"]).text == (
        "Hi Sam! What do you want to talk about?")


def test_the_walk_back_in_hello_names_the_parents_child(tmp_path):
    from helpers_runtime import drive_turn, seed_absent
    from moxie_sdk import presence
    from moxie_sdk.types import ResultCode
    rt = _runtime(tmp_path, devices=("d_one",))
    rt.greet_after_s = 300.0
    _name(rt, "d_one", "Sam")
    seed_absent(rt, "d_one", away_s=900.0)
    resp = drive_turn(rt, "d_one", presence.FOUND_FACE, event_id="evt-eye")
    assert resp["result"] == ResultCode.SUCCESS, resp
    assert "Sam" in resp["output"]["text"], resp["output"]["text"]


def test_the_day_plan_explains_itself_with_the_parents_child(tmp_path):
    rt = _runtime(tmp_path, devices=("d_one",))
    _name(rt, "d_one", "Sam")
    rt.build_schedule_for("d_one")
    stored = rt.store.read("d_one", rt.SCHEDULE_EXPLAIN_COLLECTION, None)
    assert stored["inputs"]["child_name"] == "Sam"
    # A pinned morning, so the day has slots whatever the time of the run.
    _, explanations, _ = rt.plan_schedule_for("d_one",
                                              now=datetime.datetime(2026, 9, 2, 9, 0))
    lines = [e["line"] for e in explanations]
    assert any("Sam" in line for line in lines), lines
    assert not any(" friend " in f" {line} " for line in lines), lines


# --------------------------------------------------------------------------- #
# the robot's own record: a restart, a clear, a robot that is away
# --------------------------------------------------------------------------- #

def test_the_name_survives_a_restart_and_a_clear_goes_back_to_the_default(tmp_path):
    from helpers_runtime import LatchClient
    rt = _runtime(tmp_path, devices=("d_one",))
    _name(rt, "d_one", "Sam")
    assert json.loads(_record(tmp_path).read_text()) == {"child": {"nickname": "Sam"}}

    fresh = _runtime(tmp_path, devices=("d_other",))       # same data dir, new runtime
    assert fresh._push_config("d_one")["child_pii"]["nickname"] == "Sam"
    # the robot connects after the restart: its context carries the name before any edit,
    # and the settle's first /config says it
    fresh.client = LatchClient(fresh)
    fresh._device_connect("d_one")
    assert fresh.robots["d_one"].child.nickname == "Sam"
    assert fresh.client.wait_for(lambda pubs: any(t == CONFIG.format("d_one")
                                                  for t, _ in pubs))
    assert _pushed(fresh, "d_one")["child_pii"]["nickname"] == "Sam"

    _name(fresh, "d_one", None)
    assert _pushed(fresh, "d_one")["child_pii"]["nickname"] == "friend"
    assert fresh.robots["d_one"].child.nickname == "friend"
    assert "child" not in json.loads(_record(tmp_path).read_text())   # no trace, not null
    again = _runtime(tmp_path, devices=("d_one",))
    assert again.child_for("d_one").nickname == "friend"
    assert again._push_config("d_one")["child_pii"]["nickname"] == "friend"


def test_a_write_for_a_sleeping_robot_lands_when_it_connects(tmp_path):
    """A robot that is switched off is not in `robots`, and the console's write for it
    used to be refused ('unknown device_id'). One this appliance knows (permitted, or in
    the roster) now has the setting saved (its config is published as for any edit, at
    QoS 0 and never retained, so no one hears it) and the settle's first `/config`
    carries it when it connects. An id it has never known is still a 400."""
    from helpers_runtime import LatchClient, deliver, http_call, status_server
    rt = _runtime(tmp_path, devices=("d_awake",), allow_unverified_bots=False)
    rt.client = LatchClient(rt)
    rt.set_permit("d_asleep", True)                 # permitted earlier, switched off now
    rt._roster_seen("d_gone")                       # served once, gone, not permitted
    base = status_server(rt)
    for device_id in ("d_asleep", "d_gone"):
        code, out = http_call(f"{base}/config?device_id={device_id}", method="POST",
                              body={"child": {"nickname": "Sam"}})
        assert code == 200, out
        assert (out["ok"], out["saved"], out["online"], out["pushed"]) == (
            True, True, False, False)
        assert json.loads(_record(tmp_path, device_id).read_text()) == {
            "child": {"nickname": "Sam"}}

    before = len(rt.client.on(CONFIG.format("d_asleep")))     # QoS 0: nobody heard those
    deliver(rt, "/devices/d_asleep/state", json.dumps({"robot_firmware_version": "x"}))
    assert rt.client.wait_for(lambda pubs: sum(t == CONFIG.format("d_asleep")
                                               for t, _ in pubs) > before)
    first = rt.client.on(CONFIG.format("d_asleep"))[before]   # the settle's, on connect
    assert first["pairing_status"] == "paired" and first["child_pii"]["nickname"] == "Sam"

    code, out = http_call(f"{base}/config?device_id=d_stranger", method="POST",
                          body={"child": {"nickname": "Sam"}})
    assert code == 400 and "unknown device_id" in out["error"], out
    assert not _record(tmp_path, "d_stranger").exists()


def test_a_house_rule_names_no_child(tmp_path):
    from helpers_runtime import http_call, status_server
    rt = _runtime(tmp_path)
    base = status_server(rt)
    for child in ({"nickname": "Sam"}, None):
        code, out = http_call(f"{base}/config?scope=fleet", method="POST",
                              body={"child": child, "audio_volume": 30})
        assert code == 400 and "house rule" in out["error"], out
    assert rt.fleet_config() == {}


@pytest.mark.parametrize("nickname", ["<exit>", "{{ x }}", "Sam\nexit", "", "x" * 41])
def test_a_name_the_rule_refuses_is_a_400_and_changes_nothing(tmp_path, nickname):
    from helpers_runtime import http_call, status_server
    rt = _runtime(tmp_path, devices=("d_one",))
    _name(rt, "d_one", "José")
    before = (dict(rt._config_overrides["d_one"]), _record(tmp_path).read_text(),
              len(rt.client.published))
    code, out = http_call(f"{status_server(rt)}/config?device_id=d_one", method="POST",
                          body={"child": {"nickname": nickname}, "audio_volume": 30})
    assert code == 400 and out["ok"] is False, out
    assert (dict(rt._config_overrides["d_one"]), _record(tmp_path).read_text(),
            len(rt.client.published)) == before
    assert rt.robots["d_one"].child.nickname == "José"
    assert nickname.strip() == "" or nickname not in out["error"]   # never echoed back


# --------------------------------------------------------------------------- #
# K4's fail-closed race
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("save", [{"audio_volume": 0.4}, {"child": {"nickname": "Sam"}}])
def test_a_save_that_ends_failing_closed_keeps_the_transcript_under_a_concurrent_write(
        tmp_path, monkeypatch, save):
    """A robot whose saved settings could not be read runs under NO_DATA and keeps what
    is stored. A parent's save ends that, and data sharing is the layer underneath again
    (the default keeps transcripts). The save used to end it BEFORE the fail-closed NO_DATA
    left the layer: a transcript save from a turn landing in between read a NO_DATA that
    was no longer failing closed, a parent's as far as it could tell, and erased the
    transcript. The hook below lands that save exactly there."""
    memdir = tmp_path / "transcripts"
    monkeypatch.setenv("MOXIE_MEMORY_DIR", str(memdir))       # read at construction
    rt = _runtime(tmp_path, devices=("d_one",))
    rt._remember("d_one", "a-marker", "ok")                    # a transcript on disk
    transcript = memdir / "d_one.json"
    assert transcript.exists()
    _record(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    _record(tmp_path).write_text("{not json")                  # the record is damaged ...
    fresh = _runtime(tmp_path, devices=("d_one",))             # ... so the restart fails closed
    assert fresh.failed_closed("d_one") and transcript.exists()

    class _SaveLandsInside(set):
        def discard(self, device_id):
            super().discard(device_id)
            fresh._save_memory(device_id)                      # a turn's save, right then

    fresh._settings_unreadable = _SaveLandsInside(fresh._settings_unreadable)
    fresh.update_config("d_one", **save)                       # the parent saves
    assert not fresh.failed_closed("d_one")
    assert transcript.exists(), "the save that ended failing closed erased the transcript"
    assert "a-marker" in transcript.read_text()


# --------------------------------------------------------------------------- #
# where the name goes, and where it never goes
# --------------------------------------------------------------------------- #

def test_the_name_never_reaches_the_log_the_feed_or_telemetry(tmp_path, capsys):
    """Set, rename and clear the name of a connected robot (its day plan stored in
    between), save one for a robot that is away and let it connect, and have a name
    refused: the supervisor's output, the activity feed and the connection record never
    carry a name, and afterwards the only file holding one is the away robot's own
    settings. (A line Moxie SAYS that carries the name is masked in the feed and the log:
    `test_child_name_safety.py`.)"""
    from helpers_runtime import LatchClient, deliver, http_call, status_server
    names = ("Zoë", "José")
    rt = _runtime(tmp_path, devices=("d_one",), allow_unverified_bots=False)
    rt.client = LatchClient(rt)
    rt.set_permit("d_one", True)
    rt.set_permit("d_away", True)
    base = status_server(rt)

    def post(device_id, child):
        return http_call(f"{base}/config?device_id={device_id}", method="POST",
                         body={"child": child})

    assert post("d_one", {"nickname": "Zoë"})[0] == 200
    rt.build_schedule_for("d_one")                 # its "why" lines name the child
    assert "Zoë" in json.dumps(rt.store.read("d_one", rt.SCHEDULE_EXPLAIN_COLLECTION),
                               ensure_ascii=False)
    assert post("d_one", {"nickname": "José"})[0] == 200
    assert post("d_one", {"nickname": "José\n<exit>"})[0] == 400
    assert post("d_one", None)[0] == 200
    assert post("d_away", {"nickname": "Zoë"})[0] == 200
    deliver(rt, "/devices/d_away/state", json.dumps({"robot_firmware_version": "x"}))
    assert rt.client.wait_for(lambda pubs: any(t == CONFIG.format("d_away")
                                               for t, _ in pubs))
    assert rt.client.on(CONFIG.format("d_away"))[0]["child_pii"]["nickname"] == "Zoë"

    said = capsys.readouterr()
    feed = json.dumps(list(rt.recent), ensure_ascii=False)
    conn = json.dumps(rt.conn_events(), ensure_ascii=False)
    spellings = [form for n in names for form in (n, json.dumps(n)[1:-1])]  # Zoë, Zo\u00eb
    for text in (said.out, said.err, feed, conn):
        assert not [f for f in spellings if f in text], text[-500:]

    def holds_a_name(path):
        text = path.read_text(encoding="utf-8")
        try:
            text = json.dumps(json.loads(text), ensure_ascii=False)
        except ValueError:
            pass
        return any(f in text for f in spellings)

    holders = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")
                     if p.is_file() and holds_a_name(p))
    assert holders == ["robots/d_away/config.json"], holders
