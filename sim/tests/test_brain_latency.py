"""
Brain latency — a slow brain must not leave a child listening to silence.

A live gateway turn can take 45 s against the robot's ~20 s reprompt window, so inference
runs in the background and, once `brain_budget_s` is up, a short filler goes out as chunk 0
with `result=REPLY_PENDING` (RemoteChat.proto ResultCode 9), then the real line as chunk 1
with `SUCCESS` + `consistency_control.is_completed` (fields 22 / 18).

No sleeps: the fake brain blocks on an `Event` and the fake transport is a `Condition`. The
one timing assertion (no filler before the budget) uses a monotonic clock, loosely.

"""
import json
import os
import threading
import time

from helpers_runtime import (CHAT_TOPIC, CountingSynth, LatchClient,  # noqa: E402
                             dotenv_values, find_repo_dotenv, load_repo_dotenv,
                             main_worktree, make_runtime)
from moxie_sdk.app import MoxieApp                                # noqa: E402
from moxie_sdk.filler import FILLERS, pick_filler                 # noqa: E402
from moxie_sdk.tts import strip_markup               # noqa: E402
from moxie_sdk.types import Reply, ResultCode                     # noqa: E402

TTS_TOPIC = "/devices/{device_id}/commands/tts"
FILLER_TEXTS = [text for (text, _markup) in FILLERS]

# Long enough that a loaded CI box never trips it, short enough that a real hang fails
# the test instead of hanging the suite.
PATIENCE = 10.0


# --------------------------------------------------------------------------- fakes
class _InstantApp(MoxieApp):
    """A brain that answers immediately — the normal, fast case."""
    name = "instant"

    def respond(self, turn):
        return Reply(text=f"You said: {turn.speech}")


class _SlowApp(MoxieApp):
    """A brain that answers only when the test lets it. No sleeps, so no flakes."""
    name = "slow"

    def __init__(self, text="The Moon is about 384,400 kilometres away."):
        self.text = text
        self.entered = threading.Event()     # set once respond() is running
        self.release = threading.Event()     # the test opens the gate
        self.calls = 0

    def respond(self, turn):
        self.calls += 1
        self.entered.set()
        assert self.release.wait(PATIENCE), "the test never released the slow brain"
        return Reply(text=self.text)


class _SlowThenFastApp(MoxieApp):
    """First turn blocks (the question the child abandons); later turns answer at once."""
    name = "slow-then-fast"

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()
        self.calls = 0

    def respond(self, turn):
        with self._lock:
            self.calls += 1
            first = self.calls == 1
        if first:
            self.entered.set()
            assert self.release.wait(PATIENCE), "the test never released the first turn"
            return Reply(text="the OLD answer")
        return Reply(text=f"the NEW answer about {turn.speech}")


def _slow_runtime(app, *, budget=0.2, device_id="d_slow", synth=None):
    rt, dev = make_runtime(app, device_id=device_id)
    rt.client = LatchClient()
    rt.brain_budget_s = budget
    if synth is not None:
        rt.set_synthesizer(synth)
    return rt, dev


def _push(rt, device_id, speech, event_id):
    """Hand the runtime one `events/remote-chat` prompt without waiting for the pool."""
    rt._on_remote_chat(device_id, rt.robots[device_id],
                       json.dumps({"command": "prompt", "backend": "router",
                                   "event_id": event_id, "speech": speech}))


def _chats(rt, device_id):
    return rt.client.on(CHAT_TOPIC.format(device_id=device_id))


# ------------------------------------------------------------------ the fast path
# (an instant brain's single SUCCESS is test_streaming's non-streaming-app test)
def test_a_zero_budget_disables_the_filler_entirely():
    app = _SlowApp()
    rt, dev = _slow_runtime(app, budget=0)
    _push(rt, dev, "why is the sky blue?", "evt-nofiller")
    assert app.entered.wait(PATIENCE)
    app.release.set()
    rt._pool.shutdown(wait=True)
    replies = _chats(rt, dev)
    assert [r["result"] for r in replies] == [ResultCode.SUCCESS], replies


# ------------------------------------------------------------------ the slow path
def test_slow_brain_speaks_a_filler_then_the_real_answer():
    app = _SlowApp()
    rt, dev = _slow_runtime(app, budget=0.2)
    t0 = time.monotonic()
    _push(rt, dev, "why is the sky blue?", "evt-slow")

    # 1) the child hears something while the brain is still thinking
    assert rt.client.wait_for(lambda pub: len(pub) >= 1), "no filler was published"
    heard_at = time.monotonic() - t0
    assert app.calls == 1 and not app.release.is_set(), "the brain answered early"
    filler = _chats(rt, dev)[0]
    assert filler["result"] == ResultCode.REPLY_PENDING, filler
    assert filler["chunk_num"] == 0
    assert filler["event_id"] == "evt-slow"
    assert filler["consistency_control"] == {"is_completed": False}
    assert filler["output"]["text"] in FILLER_TEXTS
    assert filler["output"]["markup"] != filler["output"]["text"], "filler carries markup"
    assert filler["end_turn"] is False, "the turn is not over — the answer is coming"
    # Not published before the budget, and not minutes after it. The ceiling is loose on
    # purpose: this asserts "inside the window", not a benchmark.
    assert 0.2 <= heard_at < 5.0, heard_at

    # 2) the real answer follows as the closing chunk of the same event
    app.release.set()
    rt._pool.shutdown(wait=True)
    replies = _chats(rt, dev)
    assert len(replies) == 2, replies
    real = replies[1]
    assert real["result"] == ResultCode.SUCCESS, real
    assert real["chunk_num"] == 1
    assert real["event_id"] == "evt-slow"
    assert real["consistency_control"] == {"is_completed": True}
    assert real["output"]["text"] == app.text
    # ...and only the real line is remembered as what Moxie said.
    assert rt.history[dev][-1] == {"role": "assistant", "content": app.text}
    assert all(h["content"] not in FILLER_TEXTS for h in rt.history[dev])


def test_the_filler_never_repeats_itself_on_the_same_robot():
    """Two slow turns in a row must not hear the same line twice — a stuck filler reads
    as a broken robot rather than a thinking one."""
    rt, dev = _slow_runtime(_InstantApp(), budget=0.2)
    said = []
    for i in range(6):
        state = {"lock": threading.Lock(), "done": False, "filler": None}
        said.append(rt._speak_filler(dev, f"evt-{i}", None, state))
        assert state["filler"] == said[-1]
        assert rt._last_filler[dev] == said[-1]
    assert all(a != b for a, b in zip(said, said[1:])), said
    assert all(s in FILLER_TEXTS for s in said)
    assert [r["result"] for r in _chats(rt, dev)] == [ResultCode.REPLY_PENDING] * 6


def test_pick_filler_rotates_without_ever_repeating_the_last_line():
    last, seen = "", set()
    for _ in range(200):
        text, markup = pick_filler(last)
        assert text != last
        assert text in markup, "the spoken line must be inside its markup"
        assert markup.startswith('<mark name="cmd:playback-mood')
        assert 'cmd:behaviour-tree' in markup, "fillers perform a thinking gesture"
        assert strip_markup(markup) == text, "TTS must speak the words, not the marks"
        seen.add(text)
        last = text
    assert seen == set(FILLER_TEXTS), "every filler should be reachable"


# --------------------------------------------------------------- the stale guard
def test_a_newer_turn_drops_the_older_brains_answer():
    """The child gave up and asked something else: the slow answer must never be
    spoken, because it answers a question that is no longer on the table."""
    app = _SlowThenFastApp()
    rt, dev = _slow_runtime(app, budget=0)      # no filler noise in this test
    _push(rt, dev, "what is a quasar?", "evt-old")
    assert app.entered.wait(PATIENCE), "the first turn never reached the brain"

    _push(rt, dev, "can we play a game?", "evt-new")
    assert rt.client.wait_for(
        lambda pub: any(p.get("event_id") == "evt-new" for _t, p in pub)), "no new answer"

    app.release.set()                            # the old brain finally returns
    rt._pool.shutdown(wait=True)

    replies = _chats(rt, dev)
    assert len(replies) == 1, replies
    assert replies[0]["event_id"] == "evt-new"
    assert "NEW" in replies[0]["output"]["text"]
    assert app.calls == 2, "both turns did reach the brain"
    # The abandoned answer is not spoken and not remembered as something Moxie said.
    assert all("OLD" not in h["content"] for h in rt.history[dev]), rt.history[dev]


def test_a_stale_turn_never_even_gets_a_filler():
    """The budget can expire after the child has moved on — say nothing then."""
    rt, dev = _slow_runtime(_InstantApp(), budget=0.2)
    rt._turn_seq[dev] = 7                        # a newer turn already started
    state = {"lock": threading.Lock(), "done": False, "filler": None}
    assert rt._speak_filler(dev, "evt-old", 6, state) is None
    assert state["filler"] is None
    assert _chats(rt, dev) == []


# ------------------------------------------------------------------------- voice
def test_both_chunks_are_synthesized_when_a_voice_is_set():
    """The SIM (and a robot without on-device TTS) must HEAR the filler, not just read
    it — so each chunk gets its own CloudTTSResponse, tagged with its chunk_num."""
    app = _SlowApp(text="Because sunlight scatters in the air.")
    synth = CountingSynth()
    rt, dev = _slow_runtime(app, budget=0.2, synth=synth)
    _push(rt, dev, "why is the sky blue?", "evt-tts")

    assert rt.client.wait_for(
        lambda pub: any(t == TTS_TOPIC.format(device_id=dev) for t, _p in pub))
    app.release.set()
    rt._pool.shutdown(wait=True)

    audio = rt.client.on(TTS_TOPIC.format(device_id=dev))
    assert len(audio) == 2, audio
    assert [a["chunk_num"] for a in audio] == [0, 1]
    assert {a["event_id"] for a in audio} == {"evt-tts"}
    assert all(a["audio"]["buffer"] for a in audio), "both chunks carry real audio"
    # The filler was spoken as WORDS: the behavior marks never reach the voice.
    assert synth.spoken[0] in FILLER_TEXTS, synth.spoken
    assert synth.spoken[1] == app.text
    assert not any("<mark" in s for s in synth.spoken)


# ------------------------------------------------------------------- the knob
def test_the_budget_comes_from_the_env_and_the_constructor_wins(monkeypatch):
    import moxie_runtime
    monkeypatch.setenv("MOXIE_BRAIN_BUDGET_S", "1.5")
    assert moxie_runtime.MoxieRuntime(_InstantApp()).brain_budget_s == 1.5
    assert moxie_runtime.MoxieRuntime(_InstantApp(), brain_budget_s=0.25
                                      ).brain_budget_s == 0.25
    monkeypatch.setenv("MOXIE_BRAIN_BUDGET_S", "not-a-number")
    assert (moxie_runtime.MoxieRuntime(_InstantApp()).brain_budget_s
            == moxie_runtime.DEFAULT_BRAIN_BUDGET_S)
    monkeypatch.delenv("MOXIE_BRAIN_BUDGET_S")
    assert (moxie_runtime.MoxieRuntime(_InstantApp()).brain_budget_s
            == moxie_runtime.DEFAULT_BRAIN_BUDGET_S)


# ------------------------------------------- mqtt/.env from inside a git worktree
def _fake_worktree(tmp_path, env_text="MOXIE_TEST_KNOB=from-main\n", *, in_tree=None):
    """A main checkout (with mqtt/.env) plus a linked worktree pointing at it."""
    main = tmp_path / "repo"
    (main / "mqtt").mkdir(parents=True)
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    (main / "mqtt" / ".env").write_text(env_text)
    wt = tmp_path / "wt"
    (wt / "mqtt").mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'wt'}\n")
    if in_tree is not None:
        (wt / "mqtt" / ".env").write_text(in_tree)
    return str(main), str(wt)


def test_dotenv_is_found_in_the_main_checkout_from_a_worktree(tmp_path):
    main, wt = _fake_worktree(tmp_path)
    assert main_worktree(wt) == main
    assert main_worktree(main) == main, "the main checkout resolves to itself"
    assert find_repo_dotenv(wt) == os.path.join(main, "mqtt", ".env")
    assert dotenv_values(find_repo_dotenv(wt)) == {"MOXIE_TEST_KNOB": "from-main"}


def test_a_dotenv_in_this_tree_wins(tmp_path):
    main, wt = _fake_worktree(tmp_path, in_tree="MOXIE_TEST_KNOB=local\n")
    assert find_repo_dotenv(wt) == os.path.join(wt, "mqtt", ".env")
    assert dotenv_values(find_repo_dotenv(wt))["MOXIE_TEST_KNOB"] == "local"


def test_no_dotenv_anywhere_is_not_an_error(tmp_path):
    (tmp_path / "mqtt").mkdir()
    assert find_repo_dotenv(str(tmp_path)) is None
    assert main_worktree(str(tmp_path)) == str(tmp_path)
    assert dotenv_values(str(tmp_path / "nope.env")) == {}


def test_load_repo_dotenv_never_overrides_the_real_environment(tmp_path, monkeypatch):
    # `allow` names the two inert probe keys explicitly (the loader exports only
    # `LIVE_KEYS`); the `setdefault` semantics under test don't depend on which keys pass.
    path = tmp_path / "sample.env"
    path.write_text("# a comment\n\nMOXIE_TEST_A=fromfile\nMOXIE_TEST_B=fromfile\n")
    monkeypatch.setenv("MOXIE_TEST_A", "already-set")
    monkeypatch.delenv("MOXIE_TEST_B", raising=False)
    probes = ("MOXIE_TEST_A", "MOXIE_TEST_B")
    assert load_repo_dotenv(str(path), allow=probes) == str(path)
    assert os.environ["MOXIE_TEST_A"] == "already-set"
    assert os.environ["MOXIE_TEST_B"] == "fromfile"
    monkeypatch.delenv("MOXIE_TEST_B")
