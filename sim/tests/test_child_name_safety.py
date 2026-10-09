"""The child's name is safe to speak and stays private (K7.1).

After K7 the child's name was the one piece of text any device on the home network can set
(`POST /config`, or the console) that Moxie then SAYS, and the walk-back-in hello said it
without the output safety check every other spoken line passes; the lines that said it
also wrote it to the supervisor's log and activity feed. What each test pins:

* safety at write time: the one name rule (`cloud_config.check_name`, for the child's name
  and the Try it card alike) runs the name through Moxie's safety table on the child's
  side, so every word the table lists is refused (a single profanity too), with a 400 that
  changes nothing and says why in plain words, never the name;
* NFC: a decomposed name is the same name and is kept composed; a combining mark is allowed
  after a letter (a Devanagari name works) and nowhere else;
* defence in depth at speak time: a hello naming a child the safety rules block (a name that
  never met the write check: the appliance's own, or one an older build saved) is the
  generic hello, and only the parent hears of it; a name an old file kept is never said;
* privacy: a turn, a hello, a queued hello, a content pack's answer, a rehearsal and a stale
  answer leave no name in the supervisor's log or feed ('[child]' stands in), while the
  robot hears the real one; a line cut for the feed never keeps part of a name;
* a revoke takes the name off the robot's settings (so an unpair or reset whose clear never
  arrived can be retried with Revoke in Robot access), and says when it could not save that.

The safety table's words are read from `safety_rules.json`, never written out here. A child's
name is personal data: the only names are 'Sam', 'José', 'Zoë', 'Mary-Kate' and 'सैम' (Sam
written in Devanagari). No broker: the transport is `helpers_runtime.FakeClient`.
"""
import json
import os
import re
import unicodedata

import pytest

from moxie_sdk import cloud_config                               # noqa: E402
from moxie_sdk.cloud_config import sanitize_config_overrides     # noqa: E402
from moxie_sdk.store import JsonStore                            # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RULES = os.path.join(REPO, "mqtt", "moxie_sdk", "safety_rules.json")
CONFIG = "/devices/{}/config"
ACCEPTED = ("Sam", "José", "Zoë", "Mary-Kate", "सैम")


def _table() -> dict:
    with open(RULES, encoding="utf-8") as fh:
        return json.load(fh)


def _words(category=None) -> list:
    """`[(category id, word)]`: every word the shipped table lists (in `category`)."""
    return [(c["id"], w) for c in _table()["categories"] for w in c.get("words") or []
            if category in (None, c["id"])]


def _word(category) -> str:
    return _words(category)[0][1]


def _label(category) -> str:
    return next(c["label"] for c in _table()["categories"] if c["id"] == category)


def _named(text, name) -> bool:
    """Does `text` carry `name` as a word, in any case?"""
    return re.search(r"(?<!\w)%s(?!\w)" % re.escape(name), text, re.IGNORECASE) is not None


def _app(reply="ok", perceived=None, on_respond=None):
    from moxie_sdk.app import MoxieApp
    from moxie_sdk.types import Reply

    class _App(MoxieApp):
        name = "content"

        def respond(self, turn):
            if on_respond is not None:
                on_respond()
            return Reply(text=reply)

        def perceive(self, turn):
            return Reply(text=perceived) if perceived else None

    return _App()


def _runtime(tmp_path, devices=("d_one",), *, nickname="friend", app=None, **kw):
    """A runtime on `tmp_path` whose appliance name is `nickname`, with `devices`
    connected. The store goes to the constructor, which reads saved settings."""
    pytest.importorskip("paho.mqtt.client", reason="the runtime imports paho")
    from helpers_runtime import make_runtime
    from moxie_sdk.types import RobotContext
    rt, _ = make_runtime(app or _app(), device_id=devices[0], nickname=nickname,
                         store=JsonStore(root=str(tmp_path)), **kw)
    for d in devices[1:]:
        rt.robots[d] = RobotContext(device_id=d, child=rt.child)
    return rt


def _name(rt, device_id, nickname):
    """What the console's `POST /config?device_id=…` does with `{"child": …}`."""
    return rt.update_config(device_id, **sanitize_config_overrides(
        {"child": None if nickname is None else {"nickname": nickname}}))


def _record(tmp_path, device_id="d_one"):
    return tmp_path / "robots" / device_id / "config.json"


def _pushed(rt, device_id):
    msgs = rt.client.on(CONFIG.format(device_id))
    assert msgs, f"no config pushed to {device_id}"
    return msgs[-1]


def _hello(rt, device_id="d_one", event_id="evt-eye"):
    """The robot's eye says someone walked back in after 15 minutes: the reply."""
    from helpers_runtime import drive_turn, fresh_pool, seed_absent
    from moxie_sdk import presence
    rt.greet_after_s = 300.0
    seed_absent(rt, device_id, away_s=900.0)
    fresh_pool(rt)
    return drive_turn(rt, device_id, presence.FOUND_FACE, event_id=event_id)


def _generic_hellos() -> set:
    """The hellos that name no one: `pick_greeting`'s own stand-in, 'friend'."""
    from moxie_sdk import presence
    return {g.format(name="friend") for g in presence.GREETINGS}


# --------------------------------------------------------------------------- #
# safety at write time: the one name rule
# --------------------------------------------------------------------------- #

def test_every_word_moxies_safety_rules_list_is_refused_as_a_name():
    """Every word the table lists, in every category (a profanity, a slur, the sexual and
    violence terms, ...), alone, capitalised or after a real name: refused, and the
    refusal names the category, never the word. Those the table only FLAGS on the child's
    side (a profanity) are refused too: Moxie would say the name at every hello."""
    words = _words()
    assert {c for c, _ in words} >= {"profanity", "hate", "sexual", "violence"}, words[:3]
    for category, word in words:
        for name in (word, word.title(), f"Sam {word}"):
            with pytest.raises(ValueError) as refused:
                sanitize_config_overrides({"child": {"nickname": name}})
            why = str(refused.value)
            assert "safety rules" in why and not _named(why, word), (category, why)
    for word in (w for _, w in _words("profanity")):        # a single profanity, alone
        with pytest.raises(ValueError, match=re.escape(_label("profanity"))):
            cloud_config.clean_child_name(word)
    for name in ACCEPTED:
        assert sanitize_config_overrides({"child": {"nickname": name}}) == {
            "child": {"nickname": name}}


def test_a_name_the_safety_rules_refuse_is_a_400_that_changes_nothing(tmp_path):
    """Through the console's own route: a 400 whose reason is the category in plain words,
    and the robot's layer, its saved settings, the wire and the name it says are as they
    were. `POST /child-name` (what the console asks before it saves a record) judges the
    same way and saves nothing."""
    from helpers_runtime import http_call, status_server
    rt = _runtime(tmp_path)
    _name(rt, "d_one", "José")
    base = status_server(rt)

    def state():
        return (dict(rt._config_overrides["d_one"]), _record(tmp_path).read_text(),
                len(rt.client.published), rt.robots["d_one"].child.nickname)

    before = state()
    for category in ("profanity", "hate", "sexual", "violence"):
        word = _word(category)
        code, out = http_call(f"{base}/config?device_id=d_one", method="POST",
                              body={"child": {"nickname": word}, "audio_volume": 30})
        assert code == 400 and out["ok"] is False, (category, out)
        assert _label(category) in out["error"] and not _named(out["error"], word)
        assert state() == before, category
        code, out = http_call(f"{base}/child-name", method="POST", body={"nickname": word})
        assert code == 400 and _label(category) in out["reason"], (category, out)
    for name in ACCEPTED + (unicodedata.normalize("NFD", "José"),):
        assert http_call(f"{base}/child-name", method="POST",
                         body={"nickname": name}) == (200, {"ok": True}), name
    assert state() == before                       # asking saved nothing
    code, out = http_call(f"{base}/child-name", method="POST", body={"nickname": "<exit>"})
    assert code == 400 and "40 letters" in out["reason"]


def test_a_decomposed_name_is_kept_composed_and_marks_follow_letters(tmp_path):
    """NFC first: an NFD 'José' or 'Zoë' (a keyboard or a paste can send one) is the same
    name and is saved and sent composed. A combining mark is allowed after a letter, so a
    name written with vowel signs (Devanagari) works, and nowhere else."""
    for name in ("José", "Zoë"):
        nfd = unicodedata.normalize("NFD", name)
        assert nfd != name
        assert sanitize_config_overrides({"child": {"nickname": nfd}}) == {
            "child": {"nickname": name}}
    assert sanitize_config_overrides({"child": {"nickname": "सैम"}}) == {
        "child": {"nickname": "सैम"}}
    for bad in ("\u0301Sam", "Sam \u0301", "Sam-\u0301", "5\u0301", "\u0948", "Sam.\u0948"):
        with pytest.raises(ValueError):
            sanitize_config_overrides({"child": {"nickname": bad}})

    rt = _runtime(tmp_path)
    _name(rt, "d_one", unicodedata.normalize("NFD", "José"))
    saved = _record(tmp_path).read_text(encoding="utf-8")
    assert json.loads(saved) == {"child": {"nickname": "José"}}
    assert unicodedata.is_normalized("NFC", json.loads(saved)["child"]["nickname"])
    assert _pushed(rt, "d_one")["child_pii"]["nickname"] == "José"
    _name(rt, "d_one", "सैम")
    assert _pushed(rt, "d_one")["child_pii"]["nickname"] == "सैम"


def test_a_name_is_refused_while_the_safety_rules_cannot_be_read(tmp_path, monkeypatch):
    """A rules file that cannot be read checks nothing, so no name is taken (the reason says
    which file to fix) and a saved one is not said: Moxie says its default meanwhile."""
    from moxie_sdk import safety
    from moxie_sdk.types import ChildProfile
    broken = tmp_path / "broken_rules.json"
    broken.write_text("{ not json")
    monkeypatch.setenv("MOXIE_SAFETY_RULES", str(broken))
    monkeypatch.setattr(safety, "_DEFAULT", None)            # read the rules afresh
    with pytest.raises(ValueError, match="MOXIE_SAFETY_RULES"):
        cloud_config.clean_child_name("Sam")
    friend = ChildProfile(nickname="friend")
    assert cloud_config.child_profile_for({"child": {"nickname": "Sam"}}, friend) is friend


def test_the_try_it_card_refuses_the_same_names(tmp_path):
    """A name typed on the Try it card is read into the brain's prompt: the same rule, so a
    word the safety rules list is a refusal before any brain is asked, and an NFD name is
    the composed one."""
    calls = []
    rt = _runtime(tmp_path, app=_app("Hi there!", on_respond=lambda: calls.append(1)))
    out = rt.tryit_turn({"speech": "hello", "nickname": _word("hate")})
    assert (out["ok"], out["kind"]) == (False, "bad_request"), out
    assert _label("hate") in out["reason"] and calls == []
    out = rt.tryit_turn({"speech": "hello", "nickname": unicodedata.normalize("NFD", "Zoë")})
    assert out["ok"] is True and out["child"]["nickname"] == "Zoë", out


# --------------------------------------------------------------------------- #
# defence in depth at speak time: the walk-back-in hello
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("source", ["appliance", "old file"])
def test_a_hello_naming_a_child_the_safety_rules_block_is_the_generic_hello(
        tmp_path, monkeypatch, capsys, source):
    """A name that never met the write check reaches the hello: the appliance's own
    (`MOXIE_CHILD_NICKNAME` is the owner's and is not checked), or one written straight
    into `robots/<id>/config.json` by a build without the check (the check is switched off
    here, the file written, the supervisor restarted). The hello passes the output check
    like every other line: blocked, so Moxie says the generic hello, and only the parent
    hears why (the review queue, with the name masked; one feed line)."""
    from moxie_sdk import safety
    from moxie_sdk.types import RobotContext
    word = _word("profanity")
    if source == "appliance":
        rt = _runtime(tmp_path, nickname=word)
    else:
        monkeypatch.setattr(cloud_config, "_name_safety_refusal", lambda name: "",
                            raising=False)
        _record(tmp_path).parent.mkdir(parents=True)
        _record(tmp_path).write_text(json.dumps({"child": {"nickname": word}}))
        rt = _runtime(tmp_path, devices=("d_seed",))         # a restart on that data dir
        # The context `_device_connect` builds for a robot that connects (connection.py).
        rt.robots["d_one"] = RobotContext(device_id="d_one", child=rt.child_for("d_one"))
    assert rt.robots["d_one"].child.nickname == word          # past the write check

    resp = _hello(rt)
    said = resp["output"]["text"]
    assert said in _generic_hellos() and not _named(said, word), said
    rows = rt.store.read("d_one", safety.EVENTS_COLLECTION, [])
    assert [(r["side"], r["action"]) for r in rows] == [("moxie", "block")], rows
    assert "profanity" in rows[0]["categories"]
    assert "[child]" in rows[0]["excerpt"] and not _named(rows[0]["excerpt"], word)
    notes = [n["text"] for n in rt.recent if n["kind"] == "safety"]
    assert any("safety rules block that name" in n for n in notes), notes
    out = capsys.readouterr()
    for text in (out.out, out.err, json.dumps(list(rt.recent), ensure_ascii=False)):
        assert not _named(text, word), text[-400:]


def test_a_name_an_old_file_kept_is_never_said_after_a_restart(tmp_path, capsys):
    """With the check in force, a file an older build wrote with a name the safety rules
    now refuse loads without it: the robot's config, the brain and the hello say the
    appliance's name, and the load says which key it dropped, never its value."""
    from moxie_sdk.apps.llm_app import LLMApp
    from moxie_sdk.types import RobotContext
    word = _word("sexual")
    _record(tmp_path).parent.mkdir(parents=True)
    _record(tmp_path).write_text(json.dumps({"child": {"nickname": word}}))
    rt = _runtime(tmp_path, devices=("d_seed",))
    rt.robots["d_one"] = RobotContext(device_id="d_one", child=rt.child_for("d_one"))
    assert rt.child_for("d_one").nickname == "friend"
    assert rt._push_config("d_one")["child_pii"]["nickname"] == "friend"
    llm = LLMApp("http://127.0.0.1:1/v1", "unused", client=object())
    assert "You are talking to friend." in llm._system(rt.robots["d_one"])
    assert _hello(rt)["output"]["text"] in _generic_hellos()
    out = capsys.readouterr().out
    assert "dropped at load" in out and "child" in out and not _named(out, word)


def test_a_flagged_hello_is_said_and_recorded_without_the_name(tmp_path):
    """On Moxie's side the table only FLAGS violent talk: a hello that carries such a word
    (the appliance's own name; a saved one is refused at write time) is said, like a
    flagged answer, and recorded for the parent without the name."""
    from moxie_sdk import safety
    word = _word("violence_talk")
    rt = _runtime(tmp_path, nickname=word)
    said = _hello(rt)["output"]["text"]
    assert _named(said, word), said
    rows = rt.store.read("d_one", safety.EVENTS_COLLECTION, [])
    assert [(r["side"], r["action"]) for r in rows] == [("moxie", "flag")], rows
    assert "[child]" in rows[0]["excerpt"] and not _named(rows[0]["excerpt"], word)


def test_a_hello_queued_with_an_old_name_is_dropped_by_a_rename(tmp_path):
    """A hello queued behind a turn in flight was built with the name of that moment: a
    rename before it is spoken drops it, so Moxie never says the old name, and the log,
    which masks the names in force, never prints it."""
    from moxie_sdk.types import ResultCode
    rt = _runtime(tmp_path)
    _name(rt, "d_one", "Zoë")
    rt._busy.add("d_one")                                # a turn is in flight
    assert _hello(rt)["result"] == ResultCode.NOREPLY_ACK      # queued, not said
    assert "Zoë" in rt._pending_opener["d_one"]
    _name(rt, "d_one", "José")
    assert "d_one" not in rt._pending_opener


# --------------------------------------------------------------------------- #
# privacy: the log and the activity feed
# --------------------------------------------------------------------------- #

def test_a_turn_and_a_hello_leave_no_name_in_the_log_or_the_feed(tmp_path, capsys):
    """Child 'Sam' (the parent's record): a turn where the child says it and Moxie answers
    with it, a hello, a hello queued behind a turn, a content pack's answer to the eye, a
    rehearsal and an answer that went stale. The robot hears 'Sam' in every one; the
    supervisor's log and the activity feed say '[child]', in any case the name was said."""
    from helpers_runtime import drive_turn, fresh_pool
    from moxie_sdk import presence
    from moxie_sdk.types import Turn
    holder = {}

    def supersede():                     # a newer turn starts while the brain is thinking
        if holder.get("stale"):
            rt._turn_seq["d_one"] = rt._turn_seq.get("d_one", 0) + 1

    rt = _runtime(tmp_path, app=_app("Great fort, Sam! SAM, you build like a pro.",
                                     perceived="Peekaboo, Sam!", on_respond=supersede))
    _name(rt, "d_one", "Sam")
    capsys.readouterr()                                  # only what follows is judged

    drive_turn(rt, "d_one", "my name is sam", event_id="evt-turn")
    assert "Sam" in _hello(rt, event_id="evt-hello")["output"]["text"]
    rt._busy.add("d_one")                                # a turn in flight: queued
    _hello(rt, event_id="evt-queued")
    rt._busy.discard("d_one")
    fresh_pool(rt)
    drive_turn(rt, "d_one", "what now", event_id="evt-next")    # the queued hello rides out
    rt.vision = True                                     # a content pack asked for the eye
    rt._pack_subscribed["d_one"] = {presence.FOUND_FACE: rt.robots["d_one"].module_id}
    fresh_pool(rt)
    drive_turn(rt, "d_one", presence.FOUND_FACE, event_id="evt-pack")
    rt.preview("d_one", "Good night, Sam. Sleep tight.")
    holder["stale"] = True
    seq = rt._turn_seq["d_one"] = rt._turn_seq.get("d_one", 0) + 1
    rt._handle_turn("d_one", "evt-stale", "and then?",
                    Turn(robot=rt.robots["d_one"], speech="and then?"), seq)

    heard = [p["output"]["text"] for p in rt.client.chat_replies("d_one") if p.get("output")]
    # the turn, the hello, the queued hello, the next answer, the pack, the rehearsal
    assert sum("Sam" in t for t in heard) == 6, heard
    out = capsys.readouterr()
    log, feed = out.out + out.err, json.dumps(list(rt.recent), ensure_ascii=False)
    for text in (log, feed):
        assert not _named(text, "Sam"), [ln for ln in text.splitlines() if _named(ln, "Sam")]
    for line in ("walked back in", "delivering queued opener", "woke a content pack",
                 "preview →", "superseded on d_one; dropping"):
        assert any(line in ln and "[child]" in ln for ln in log.splitlines()), line
    assert "💬 'my name is [child]' → 'Great fort, [child]! [child]," in feed


def test_every_feed_line_masks_every_name_moxie_calls_a_child(tmp_path):
    """The feed's one funnel (`_note`) masks any line, whoever wrote it: the names of the
    connected robots, of a robot that is away (its saved record) and the appliance's own,
    in any case, with or without their accents, whole or by part. The generic 'friend' is
    a word, not a name, and stays."""
    rt = _runtime(tmp_path, devices=("d_one", "d_two"), nickname="Sam",
                  allow_unverified_bots=False)
    rt.set_permit("d_away", True)
    _name(rt, "d_one", "Zoë")
    _name(rt, "d_two", "José")
    _name(rt, "d_away", "Mary-Kate")
    rt._note("chat", "ZOE met sam and jose, Mary and kate, then mary-kate; Samantha is the "
                     "same; hi friend")
    assert rt.recent[-1]["text"] == ("[child] met [child] and [child], [child] and [child], "
                                     "then [child]; Samantha is the same; hi friend")
    # The appliance's own name, with no robot connected and no record naming anyone.
    bare = _runtime(tmp_path / "bare", nickname="Sam")
    bare.robots.clear()
    bare._note("chat", "hi sam")
    assert bare.recent[-1]["text"] == "hi [child]"


def test_a_renamed_or_cleared_name_stays_masked_for_the_run(tmp_path, capsys):
    """A rename (or an unpair's clear) takes a name out of force, but the conversation's
    history can still make the brain say it: the log and the feed keep masking it for the
    rest of the run."""
    from helpers_runtime import drive_turn, fresh_pool
    rt = _runtime(tmp_path, app=_app("Zoë, José: both great names!"))
    _name(rt, "d_one", "Zoë")
    _name(rt, "d_one", "José")                           # renamed
    _name(rt, "d_one", None)                             # and cleared
    assert rt.robots["d_one"].child.nickname == "friend"
    capsys.readouterr()
    fresh_pool(rt)
    drive_turn(rt, "d_one", "do you remember zoe?", event_id="evt-old")
    out = capsys.readouterr()
    for text in (out.out + out.err, json.dumps(list(rt.recent), ensure_ascii=False)):
        assert not _named(text, "Zoë") and not _named(text, "Zoe") and not _named(text, "José")
    assert "Zoë, José" in rt.client.chat_replies("d_one")[-1]["output"]["text"]


def test_a_feed_line_written_before_the_name_was_saved_is_masked_once_it_is(tmp_path):
    """The child may say their name before the parent saves it: that feed line could not be
    masked then. Saving the name masks the feed's lines in RAM again."""
    from helpers_runtime import drive_turn
    rt = _runtime(tmp_path, app=_app("Nice to meet you!"))
    drive_turn(rt, "d_one", "my name is sam", event_id="evt-early")
    assert any(_named(n["text"], "sam") for n in rt.recent)
    _name(rt, "d_one", "Sam")
    assert not any(_named(n["text"], "sam") for n in rt.recent), list(rt.recent)
    assert any("my name is [child]" in n["text"] for n in rt.recent)


def test_a_line_cut_for_the_feed_never_keeps_part_of_a_name(tmp_path, monkeypatch):
    """A feed line keeps the first 30, 40 or 60 characters of what was said. The name is
    masked BEFORE the cut, so a name across the cut never leaves its first letters behind
    ('Jo' of 'José'): the hello, the exchange (both sides), a rehearsal, the ears' 'heard',
    a voice test and telehealth's 'said'."""
    from helpers_audio import pb_zmq_stt_frame, tone_pcm
    from helpers_runtime import CountingSynth, deliver, drive_turn, fresh_pool
    from moxie_sdk import presence
    from moxie_sdk.stt import Transcriber

    def across(cut, tail=" José ok"):
        """`x…x José ok` with 'José' straddling character `cut`."""
        return "x" * (cut - 3) + tail

    class _Ears(Transcriber):
        name = "fixed"

        def transcribe(self, pcm, sample_rate=16000):
            return across(40)

    monkeypatch.setattr(presence, "GREETINGS", ("w" * 37 + " {name}!",))
    reply = across(40, " José " + "y" * 14 + " José!")      # 'José' across 40 and 60
    rt = _runtime(tmp_path, app=_app(reply))
    _name(rt, "d_one", "José")
    before = len(rt.recent)
    _hello(rt)
    fresh_pool(rt)
    drive_turn(rt, "d_one", across(30), event_id="evt-cut")
    rt.preview("d_one", reply)
    rt.set_transcriber(_Ears())
    for i, chunk in enumerate((tone_pcm(200, amplitude=0.3), tone_pcm(200, amplitude=0.3),
                               b"")):
        deliver(rt, "/devices/d_one/events/zmq", pb_zmq_stt_frame(min(i + 1, 3), chunk,
                                                                   "utt-cut"))
    rt.set_synthesizer(CountingSynth())
    assert rt.voice_test("d_one", across(40))["ok"] is True
    rt.telehealth_enable("d_one", True)
    rt.telehealth_session("d_one", "START_SESSION")
    assert rt.telehealth_speak("d_one", across(40))["ok"] is True

    lines = [n["text"] for n in list(rt.recent)[before:]]
    for marker in ("hello (unprompted)", "💬", "🎬 rehearsed", "👂 heard", "🎚️ test",
                   "🎭 said"):
        hits = [ln for ln in lines if marker in ln]
        assert hits, (marker, lines)
        for ln in hits:
            assert not re.search(r"(?<!\w)(?:Jos|Jo)(?!\w)", ln) and not _named(ln, "José"), ln


# --------------------------------------------------------------------------- #
# a revoke takes the name off (the retry for an unpair whose clear never arrived)
# --------------------------------------------------------------------------- #

def test_a_revoke_takes_the_name_off_the_robots_settings(tmp_path):
    """A robot this appliance no longer lets in keeps no child's name: Revoke drops it from
    RAM and from `robots/<id>/config.json`, and says so (`child_cleared`). Let in again
    without an account naming it (Permit with no record), it says the appliance's name,
    never the earlier family's. A revoke for a robot with no name writes nothing."""
    rt = _runtime(tmp_path, allow_unverified_bots=False)
    rt.set_permit("d_one", True)
    _name(rt, "d_one", "Zoë")
    out = rt.set_permit("d_one", False)
    assert "child" not in json.loads(_record(tmp_path).read_text())
    assert out["ok"] is True and out.get("child_cleared") is True, out
    assert rt.robots["d_one"].child.nickname == "friend"
    rt.set_permit("d_one", True)                         # Permit, no account record
    assert _pushed(rt, "d_one")["child_pii"]["nickname"] == "friend"
    assert rt.set_permit("d_never", False).get("child_cleared") is True
    assert not _record(tmp_path, "d_never").exists()
    assert "child_cleared" not in rt.set_permit("d_one", True)       # a permit says nothing


def test_a_revoke_that_cannot_save_says_so_and_can_be_repeated(tmp_path, monkeypatch):
    """When the store refuses the write, the robot stops using the name but its saved
    settings still hold it: the revoke answers `child_cleared: false`. Revoking again once
    the store takes writes takes it off for good."""
    rt = _runtime(tmp_path, allow_unverified_bots=False)
    rt.set_permit("d_one", True)
    _name(rt, "d_one", "Zoë")
    write = rt.store.write

    def refused(device_id, collection, value):
        if collection == rt.ROBOT_CONFIG_COLLECTION:
            return False
        return write(device_id, collection, value)

    monkeypatch.setattr(rt.store, "write", refused)
    assert rt.set_permit("d_one", False).get("child_cleared") is False
    assert json.loads(_record(tmp_path).read_text())["child"] == {"nickname": "Zoë"}
    assert rt.robots["d_one"].child.nickname == "friend"
    monkeypatch.setattr(rt.store, "write", write)
    assert rt.set_permit("d_one", False).get("child_cleared") is True
    assert "child" not in json.loads(_record(tmp_path).read_text())


# --------------------------------------------------------------------------- #
# the mask itself
# --------------------------------------------------------------------------- #

def test_the_mask_takes_every_spelling_of_a_name_and_nothing_else():
    mask = cloud_config.mask_child_names
    assert mask("Hi José! jose, JOSÉ and José's hat", ["José"]) == (
        "Hi [child]! [child], [child] and [child]'s hat")
    assert mask(unicodedata.normalize("NFD", "Hi José"), ["José"]) == "Hi [child]"
    assert mask("Mary-Kate, Mary and Kate", ["Mary-Kate"]) == "[child], [child] and [child]"
    assert mask("Sam, Samantha, same, sam.", ["Sam"]) == "[child], Samantha, same, [child]."
    assert mask("सैम आया", ["सैम"]) == "[child] आया"
    assert mask("hi friend", ["friend", "Friend"]) == "hi friend"
    assert mask("hi Sam", []) == "hi Sam" and mask(None, ["Sam"]) == ""
