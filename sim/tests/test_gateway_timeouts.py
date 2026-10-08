"""
Bounded engine calls — a gateway that accepts connections and never answers costs one
bound, not a turn worker for 5 x 600 s; a standby lets go of a recovered primary.

Measured before the fix (core wave 2, K8): every `OpenAI(...)` this repo built had no
timeout and the installed SDK's default is `Timeout(connect=5, read=600, write=600,
pool=600)`; `call_with_backoff` classed the resulting `APITimeoutError` as transient and
retried it four times; and `FallbackTranscriber` / `FallbackSynthesizer` latched their
standby on the first failure for the rest of the run — in the default image the ears'
standby is `NullTranscriber`, so one outage left Moxie deaf until a restart.

The server here is the real shape of that failure: a loopback socket that accepts every
connection, reads nothing and answers nothing. Every client is the real openai one
(skipped without the package) against it. The deadline, the shared streaming bound and
the latch are driven with injected clocks, so nothing here sleeps for its assertion.
"""
import socketserver
import threading

import pytest

from helpers_runtime import drive_turn, make_runtime, reload_config          # noqa: E402
from moxie_sdk import chat as chat_seam                                      # noqa: E402
from moxie_sdk.apps.llm_app import LLMApp                                    # noqa: E402
from moxie_sdk.chat import (Pacer, call_with_backoff, client_timeout,        # noqa: E402
                            is_offline_error, is_timeout_error, last_call_error,
                            make_openai_chat, make_openai_stream, model_calls)
from moxie_sdk.stt import (FallbackTranscriber, OpenAITranscriber,            # noqa: E402
                           Transcriber)
from moxie_sdk.tts import (FallbackSynthesizer, OpenAIVoiceSynthesizer,       # noqa: E402
                           Synthesizer)
from moxie_sdk.types import ChildProfile, ResultCode, RobotContext, Turn     # noqa: E402

#: The knobs this slice added, cleared before every `reload_config` so a developer's
#: environment cannot decide what "the default" is.
KNOBS = ("MOXIE_BRAIN_TIMEOUT_S", "MOXIE_STT_TIMEOUT_S", "MOXIE_TTS_TIMEOUT_S",
         "MOXIE_ENGINE_RETRY_S", "MOXIE_VOICE_BASE_URL", "MOXIE_VOICE_API_KEY",
         "MOXIE_LLM_BASE_URL", "MOXIE_LLM_API_KEY", "MOXIE_STT_BASE_URL",
         "MOXIE_STT_API_KEY", "MOXIE_STT", "MOXIE_TTS", "MOXIE_APP")

#: A real hang fails the test instead of hanging the suite: on origin/dev the call is
#: still blocked when this fires (the SDK's 600 s read timeout).
GUARD_S = 5.0
#: The knob under test, and the ceiling the whole call (connect, request, backoff) must
#: respect with it. 0.3 s against 3 s leaves room for a loaded CI box.
BOUND_S = 0.3
CEILING_S = 3.0

#: 0.5 s of 16 kHz mono PCM16 — over the transcriber's 120 ms gate.
PCM_16K = b"\x11\x22" * 8000
MESSAGES = [{"role": "user", "content": "hello"}]
URL = "http://127.0.0.1:1/v1"


# ------------------------------------------------------------ a wedged gateway --
class WedgedGateway:
    """Accepts every connection and never answers — a gateway whose process is alive
    and wedged. Connects succeed at once, so only a READ bound can end the call."""

    def __init__(self):
        self._stop = threading.Event()
        stop = self._stop

        class _Hold(socketserver.BaseRequestHandler):
            def handle(self):
                stop.wait()                      # read nothing, answer nothing

        class _Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            allow_reuse_address = True

        self._server = _Server(("127.0.0.1", 0), _Hold)   # an ephemeral port from the OS
        self.port = self._server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}/v1"
        threading.Thread(target=self._server.serve_forever,
                         kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self._stop.set()
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def wedged():
    gateway = WedgedGateway()
    try:
        yield gateway
    finally:
        gateway.close()


def _bounded(fn):
    """Run `fn` on a daemon thread: `(within_ceiling, finished_by_guard, err)`, measured
    by two bounded waits on an Event — never by reading a clock (test_clock_dependence)."""
    done, box = threading.Event(), {}

    def _go():
        try:
            box["out"] = fn()
        except Exception as e:                   # noqa: BLE001 — the outcome under test
            box["err"] = e
        finally:
            done.set()

    threading.Thread(target=_go, daemon=True).start()
    within = done.wait(CEILING_S)
    finished = within or done.wait(GUARD_S - CEILING_S)
    return within, finished, box.get("err")


def _assert_bounded(fn, what):
    within, finished, err = _bounded(fn)
    assert finished, f"{what} was still blocked when the {GUARD_S:g} s guard fired"
    assert within, f"{what} took over {CEILING_S:g} s under a {BOUND_S} s bound"
    assert err is not None, f"{what} returned instead of failing offline: {err!r}"
    assert is_offline_error(err), f"{what} raised a non-offline {type(err).__name__}"
    assert is_timeout_error(err), f"{what} should have ended on a timeout: {err!r}"
    return err


# ----------------------------------------------- every client, against the hang --
def test_a_gateway_that_never_answers_frees_the_turn_worker(wedged, monkeypatch):
    """The brain: under MOXIE_BRAIN_TIMEOUT_S=0.3 the chat call ends offline-class
    within 3 s, backoff included, and spends exactly ONE request (a timeout is not
    retried). origin/dev: still blocked when the 5 s guard fires."""
    pytest.importorskip("openai")
    c = reload_config(monkeypatch, KNOBS, MOXIE_BRAIN_TIMEOUT_S=str(BOUND_S))
    assert c.BRAIN_TIMEOUT_S == BOUND_S
    chat = make_openai_chat(wedged.base_url, "sk-test", "m", timeout_s=c.BRAIN_TIMEOUT_S,
                            on_backoff=None)
    before = model_calls("chat")
    _assert_bounded(lambda: chat(MESSAGES), "the chat call")
    assert model_calls("chat") - before == 1, "a hung request was retried"


def test_the_stream_open_is_bounded_the_same_way(wedged):
    pytest.importorskip("openai")
    stream = make_openai_stream(wedged.base_url, "sk-test", "m", timeout_s=BOUND_S,
                                on_backoff=None)
    before = model_calls("stream")
    _assert_bounded(lambda: next(iter(stream(MESSAGES))), "the stream open")
    assert model_calls("stream") - before == 1


def test_the_gateway_ears_are_bounded(wedged, monkeypatch):
    pytest.importorskip("openai")
    c = reload_config(monkeypatch, KNOBS, MOXIE_STT_TIMEOUT_S=str(BOUND_S))
    ears = OpenAITranscriber(wedged.base_url, "sk-test", timeout_s=c.STT_TIMEOUT_S)
    _assert_bounded(lambda: ears.transcribe(PCM_16K, 16000), "the transcription")


def test_the_gateway_voice_is_bounded(wedged, monkeypatch):
    pytest.importorskip("openai")
    c = reload_config(monkeypatch, KNOBS, MOXIE_TTS_TIMEOUT_S=str(BOUND_S))
    voice = OpenAIVoiceSynthesizer(wedged.base_url, "sk-test", model="piper-amy",
                                   timeout_s=c.TTS_TIMEOUT_S)
    _assert_bounded(lambda: voice.synthesize("Hi Sam"), "the speech request")


def test_the_voice_pickers_model_listing_is_bounded(wedged, monkeypatch):
    """`config.gateway_model_ids` feeds the console picker from a background thread; a
    wedged gateway must not hold that thread for 600 s either (the voice knob)."""
    pytest.importorskip("openai")
    c = reload_config(monkeypatch, KNOBS, MOXIE_VOICE_BASE_URL=wedged.base_url,
                      MOXIE_TTS_TIMEOUT_S=str(BOUND_S))
    _assert_bounded(c.gateway_model_ids, "the model listing")


def test_every_built_client_carries_its_knob(monkeypatch):
    """Each construction site hands its knob to the SDK client as the per-request
    timeout (read/write/pool = the knob, connect = the SDK's 5 s) with retries still
    the backoff's, not openai's."""
    openai = pytest.importorskip("openai")
    built = []

    class _Recorder:
        def __init__(self, **kw):
            built.append(kw)
            self.models = self

        def list(self):
            return type("L", (), {"data": []})()

    monkeypatch.setattr(openai, "OpenAI", _Recorder)
    make_openai_chat(URL, "k", timeout_s=7)
    make_openai_stream(URL, "k", timeout_s=8)
    LLMApp(URL, "k", timeout_s=9)
    OpenAITranscriber(URL, "k", timeout_s=10)
    OpenAIVoiceSynthesizer(URL, "k", timeout_s=11)
    c = reload_config(monkeypatch, KNOBS, MOXIE_VOICE_BASE_URL=URL,
                      MOXIE_TTS_TIMEOUT_S="12")
    c.gateway_model_ids()
    assert [kw["timeout"].read for kw in built] == [7, 8, 9, 10, 11, 12]
    assert [kw["timeout"].write for kw in built] == [7, 8, 9, 10, 11, 12]
    assert all(kw["timeout"].connect == 5.0 for kw in built)
    assert all(kw["max_retries"] == 0 for kw in built)
    # a knob shorter than the connect bound shortens the connect bound too; 0 is refused
    assert client_timeout(0.3).connect == 0.3
    with pytest.raises(ValueError):
        client_timeout(0)


def test_the_knobs_default_to_the_documented_bounds_and_reach_every_engine(monkeypatch):
    """60 / 12 / 15 / 60 s — a hang bound, chosen not measured — and the same numbers
    the SDK classes carry, so a brain built outside config is bounded the same way.
    Then each builder is shown passing the knob on."""
    c = reload_config(monkeypatch, KNOBS)
    assert (c.BRAIN_TIMEOUT_S, c.STT_TIMEOUT_S, c.TTS_TIMEOUT_S, c.ENGINE_RETRY_S) == \
        (60.0, 12.0, 15.0, 60.0)
    assert c.BRAIN_TIMEOUT_S == chat_seam.DEFAULT_TIMEOUT_S
    assert c.STT_TIMEOUT_S == OpenAITranscriber.TIMEOUT_S
    assert c.TTS_TIMEOUT_S == OpenAIVoiceSynthesizer.TIMEOUT_S
    assert c.ENGINE_RETRY_S == FallbackTranscriber.RETRY_S == FallbackSynthesizer.RETRY_S
    assert c.BRAIN_TIMEOUT_S > c.BRAIN_BUDGET_S, "the hang bound sits above the filler budget"

    import moxie_sdk.apps as apps
    import moxie_sdk.stt as stt
    import moxie_sdk.tts as tts
    seen = {}
    c = reload_config(monkeypatch, KNOBS, MOXIE_APP="llm", MOXIE_LLM_BASE_URL=URL,
                      MOXIE_VOICE_BASE_URL=URL, MOXIE_STT="gateway",
                      MOXIE_BRAIN_TIMEOUT_S="45", MOXIE_STT_TIMEOUT_S="9",
                      MOXIE_TTS_TIMEOUT_S="8", MOXIE_ENGINE_RETRY_S="120")
    monkeypatch.setattr(apps, "LLMApp", lambda **kw: seen.setdefault("llm", kw))
    c._build_llm()
    assert seen["llm"]["timeout_s"] == 45.0
    monkeypatch.setattr(chat_seam, "make_openai_chat",
                        lambda *a, **kw: seen.setdefault("content", kw) and (lambda m: ""))
    c.build_content_app()
    assert seen["content"]["timeout_s"] == 45.0

    class _Stub(Synthesizer):
        name = "openai-voice"
        sample_rate = 22050

        def synthesize(self, text, voice=None):
            return b"\x00\x01"

    class _Ears(Transcriber):
        name = "openai-stt"

        def transcribe(self, pcm, sample_rate=16000):
            return "cloud"

    class _NoWhisper(Transcriber):
        @classmethod
        def available(cls):
            return False

    monkeypatch.setattr(tts, "make_voice_synthesizer",
                        lambda *a, **kw: seen.setdefault("voice", kw) and _Stub())
    monkeypatch.setattr(stt, "make_openai_transcriber",
                        lambda *a, **kw: seen.setdefault("ears", kw) and _Ears())
    monkeypatch.setattr(stt, "WhisperTranscriber", _NoWhisper)
    voice = c.build_synthesizer()
    ears = c.build_transcriber()
    assert seen["voice"]["timeout_s"] == 8.0 and seen["ears"]["timeout_s"] == 9.0
    assert isinstance(voice, FallbackSynthesizer) and voice._retry_s == 120.0
    assert isinstance(ears, FallbackTranscriber) and ears._retry_s == 120.0


# ------------------------------------------------------------- the deadline --
class _Clock:
    """A clock a test moves by hand."""

    def __init__(self, t=0.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += float(s)


class _ServerErr(Exception):
    status_code = 503


class APITimeoutError(Exception):
    """Named like the SDK's: the classifiers read the type name, not the package."""


def test_a_deadline_stops_the_backoff_and_reraises_the_last_error():
    """A transient error on every attempt, sleep and clock injected: no retry starts
    past `deadline_s`, the last error comes back, and the clock never passes the
    deadline (the wait that would have crossed it is not taken). origin/dev: eleven
    attempts, ~27 s of sleeps."""
    clock = _Clock()
    attempts = []

    def flaky():
        attempts.append(clock())
        raise _ServerErr("busy")

    with pytest.raises(_ServerErr):
        call_with_backoff(flaky, max_retries=10, base=0.4, deadline_s=1.0,
                          sleep=clock.advance, clock=clock)
    # delays grow 0.4-0.8, then 0.8-1.2 (jitter is random, the bound is not): the
    # second retry would always start past 1.0 s
    assert len(attempts) == 2, attempts
    assert clock() < 1.0
    assert isinstance(last_call_error(), _ServerErr)


def test_a_timeout_is_never_retried_within_one_call():
    """The time a retry would cost was already spent once. origin/dev: five attempts."""
    attempts = []

    def hung():
        attempts.append(1)
        raise APITimeoutError("Request timed out.")

    with pytest.raises(APITimeoutError):
        call_with_backoff(hung, sleep=lambda s: None)
    assert len(attempts) == 1
    assert is_offline_error(APITimeoutError()) and is_timeout_error(APITimeoutError())
    assert not is_timeout_error(_ServerErr()) and not is_timeout_error(ConnectionError())


def test_a_healthy_call_is_still_a_single_attempt_with_the_deadline_set():
    clock = _Clock()
    slept = []
    out = call_with_backoff(lambda: "ok", deadline_s=60.0, sleep=slept.append,
                            clock=clock)
    assert out == "ok" and slept == [] and clock() == 0.0
    assert last_call_error() is None


# --------------------------------------- the streaming brain: one bound per turn --
def _turn(speech="why does the moon change shape?"):
    return Turn(robot=RobotContext(device_id="d_x", child=ChildProfile(nickname="Sam")),
                speech=speech)


class _Boom(Exception):
    """A 400: the gateway does not stream. Never transient, never retried."""
    status_code = 400


class _StreamFails:
    """`chat.completions` seam: opening a stream spends `spend` seconds of the injected
    clock and raises `exc`; a non-streamed completion answers and records its kwargs."""

    def __init__(self, clock, spend, exc, whole="I thought about it all at once."):
        self.clock, self.spend, self.exc, self.whole = clock, spend, exc, whole
        self.stream_calls = self.whole_calls = 0
        self.whole_kw = []
        self.chat = self.completions = self

    def create(self, **kw):
        if kw.get("stream"):
            self.stream_calls += 1
            self.clock.advance(self.spend)
            raise self.exc
        self.whole_calls += 1
        self.whole_kw.append(kw)
        msg = type("M", (), {"content": self.whole})()
        return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()


def test_a_hung_stream_open_and_its_fallback_end_within_one_bound():
    """The open ran the whole 9 s bound out: the `respond()` fallback is skipped (it
    would hang just as long) and the turn answers offline, once. origin/dev: a second
    600 s hang, then ERROR_OFFLINE."""
    clock = _Clock()
    fake = _StreamFails(clock, 9.0, APITimeoutError("Request timed out."))
    app = LLMApp(URL, "k", model="m", client=fake, timeout_s=9.0, clock=clock)
    chunks = list(app.respond_stream(_turn()))
    assert [c.result_code for c in chunks] == [ResultCode.ERROR_OFFLINE]
    assert chunks[0].final and chunks[0].text == ""
    assert (fake.stream_calls, fake.whole_calls) == (1, 0), "the fallback hung a second time"
    assert clock() == 9.0, "something ran after the bound"
    assert isinstance(last_call_error(), APITimeoutError)


def test_the_robot_hears_exactly_one_offline_reply_for_a_hung_open():
    clock = _Clock()
    fake = _StreamFails(clock, 9.0, APITimeoutError("Request timed out."))
    app = LLMApp(URL, "k", model="m", client=fake, timeout_s=9.0, clock=clock)
    rt, dev = make_runtime(app)
    rt.brain_budget_s = 0
    drive_turn(rt, dev, "hello", event_id="evt-hung")
    replies = rt.client.chat_replies(dev)
    assert [r["result"] for r in replies] == [ResultCode.ERROR_OFFLINE], replies
    assert "chunk_num" not in replies[0] and replies[0]["event_id"] == "evt-hung"


def test_a_fast_failing_open_hands_the_fallback_the_rest_of_the_bound():
    """An open refused outright 2 s in leaves 7 s: the fallback runs, bounded to what
    is left (its own request timeout), and answers."""
    clock = _Clock()
    fake = _StreamFails(clock, 2.0, _Boom("streaming not supported"))
    app = LLMApp(URL, "k", model="m", client=fake, timeout_s=9.0, clock=clock)
    chunks = list(app.respond_stream(_turn()))
    assert [c.text for c in chunks] == [fake.whole] and chunks[0].final
    assert (fake.stream_calls, fake.whole_calls) == (1, 1)
    assert fake.whole_kw[0]["timeout"] == pytest.approx(7.0)
    # ...and a plain `respond()` with no budget sends no per-request override at all
    app.respond(_turn())
    assert "timeout" not in fake.whole_kw[1]


# ------------------------------------------------- the standby lets go again --
class _FlakyEars(Transcriber):
    """Fails the first `failures` calls, then hears."""
    name = "cloud"

    def __init__(self, failures=1):
        self.failures, self.calls = failures, 0

    def transcribe(self, pcm, sample_rate=16000):
        self.calls += 1
        if self.calls <= self.failures:
            raise APITimeoutError("Request timed out.")
        return "heard in the cloud"


class _LocalEars(Transcriber):
    name = "local-fake"

    def __init__(self):
        self.calls = 0

    def transcribe(self, pcm, sample_rate=16000):
        self.calls += 1
        return "heard locally"


class _FlakyVoice(Synthesizer):
    name = "cloud-voice"
    sample_rate = 22050

    def __init__(self, failures=1):
        self.failures, self.calls = failures, 0

    def synthesize(self, text, voice=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise APITimeoutError("Request timed out.")
        return b"\x01\x02" * 8


class _LocalVoice(Synthesizer):
    name = "piper-fake"
    sample_rate = 16000

    def __init__(self):
        self.calls = 0

    def synthesize(self, text, voice=None):
        self.calls += 1
        return b"\x03\x04" * 8


#: The product's own `HH:MM` formatter: `describe()` is asserted to name `failed_at` and
#: `retry_at()` through it (the instants themselves are asserted as numbers below), and
#: this test tree reads no clock of its own (test_clock_dependence).
from moxie_sdk.stt import _hhmm                                              # noqa: E402

T0 = 1_760_000_000.0            # a fixed wall-clock instant for the HH:MM strings


def test_a_standby_lets_go_of_a_recovered_primary():
    """Ears: the primary times out once and the standby hears; inside the retry window
    every call stays on the standby (the primary is not asked); the first call after
    the window tries the primary and, as it answers, clears the latch with one recovery
    line. origin/dev: every later call goes to the standby, for the rest of the run."""
    clock, logged = _Clock(T0), []
    primary, standby = _FlakyEars(), _LocalEars()
    fb = FallbackTranscriber(primary, standby, log=logged.append, retry_s=60, clock=clock)
    assert fb.describe() == "cloud (standby: local-fake)"

    assert fb.transcribe(PCM_16K) == "heard locally"       # the timeout latches
    assert fb.failed and fb.failed_at == T0 and fb.retry_at() == T0 + 60
    assert len(logged) == 1 and "cloud failed" in logged[0] and "60s" in logged[0]
    clock.advance(30)
    assert fb.transcribe(PCM_16K) == "heard locally"       # inside the window: standby
    assert primary.calls == 1, "the primary was asked inside the cool-down"
    desc = fb.describe()
    assert desc == (f"local-fake (standby since {_hhmm(T0)} — cloud failed; "
                    f"retrying the primary at {_hhmm(T0 + 60)})")

    clock.advance(31)                                      # past the window
    assert fb.transcribe(PCM_16K) == "heard in the cloud"  # the retry, and it answers
    assert not fb.failed and fb.failed_at is None and fb.retry_at() is None
    assert primary.calls == 2 and standby.calls == 2
    assert len(logged) == 2 and "cloud is back" in logged[1]
    assert fb.describe() == "cloud (standby: local-fake)"
    assert fb.transcribe(PCM_16K) == "heard in the cloud"  # and stays on the primary


def test_a_retry_that_fails_again_keeps_the_standby_and_moves_the_window():
    clock, logged = _Clock(T0), []
    primary, standby = _FlakyEars(failures=2), _LocalEars()
    fb = FallbackTranscriber(primary, standby, log=logged.append, retry_s=60, clock=clock)
    fb.transcribe(PCM_16K)
    clock.advance(60)
    assert fb.transcribe(PCM_16K) == "heard locally"       # the retry fails: standby
    assert fb.failed and fb.failed_at == T0 + 60 and primary.calls == 2
    assert len(logged) == 2 and "still failing" in logged[1]
    clock.advance(59)
    fb.transcribe(PCM_16K)
    assert primary.calls == 2, "a second retry inside the new window"
    clock.advance(1)
    assert fb.transcribe(PCM_16K) == "heard in the cloud" and not fb.failed


def test_the_voices_standby_lets_go_the_same_way():
    """`FallbackSynthesizer`: the same latch, the same window, and the sample rate
    follows whichever engine actually spoke."""
    clock, logged = _Clock(T0), []
    primary, standby = _FlakyVoice(), _LocalVoice()
    fb = FallbackSynthesizer(primary, standby, log=logged.append, retry_s=60, clock=clock)
    assert fb.describe() == "cloud-voice (standby: piper-fake)"

    assert fb.synthesize("hi") == b"\x03\x04" * 8 and fb.sample_rate == 16000
    assert fb.failed and fb.voice_name == "piper-fake" and len(logged) == 1
    clock.advance(59)
    fb.synthesize("hi")
    assert primary.calls == 1
    assert fb.describe() == (f"piper-fake (standby since {_hhmm(T0)} — cloud-voice failed; "
                             f"retrying the primary at {_hhmm(T0 + 60)})")
    clock.advance(1)
    assert fb.synthesize("hi") == b"\x01\x02" * 8 and fb.sample_rate == 22050
    assert not fb.failed and fb.voice_name == "cloud-voice"
    assert len(logged) == 2 and "cloud-voice is back" in logged[1]


def test_a_zero_retry_window_tries_the_primary_on_every_call():
    clock = _Clock(T0)
    primary, standby = _FlakyEars(failures=3), _LocalEars()
    fb = FallbackTranscriber(primary, standby, log=lambda m: None, retry_s=0, clock=clock)
    for _ in range(3):
        assert fb.transcribe(PCM_16K) == "heard locally"
    assert primary.calls == 3 and fb.failed
    assert fb.transcribe(PCM_16K) == "heard in the cloud" and not fb.failed


# ------------------------------------------- a knob of 0: refused, never "no bound" --
class _Busy(Exception):
    """A 503 with `Retry-After: 0.3`: transient, so retried — unless the deadline says
    the retry would start past the bound."""
    status_code = 503
    response = type("R", (), {"headers": {"retry-after": "0.3"}})()


class _BusyGateway:
    """Every endpoint (chat, stream, ears, voice) answers 503 forever, and counts."""

    def __init__(self):
        self.calls = 0
        self.chat = self.completions = self.audio = self.transcriptions = self.speech = self

    def create(self, **kw):
        self.calls += 1
        raise _Busy("busy")


@pytest.mark.parametrize("bad", ["0", "-1", "nan", "inf"])
@pytest.mark.parametrize("knob", ["MOXIE_BRAIN_TIMEOUT_S", "MOXIE_STT_TIMEOUT_S",
                                  "MOXIE_TTS_TIMEOUT_S"])
def test_a_knob_of_zero_is_refused_at_startup_not_read_as_no_bound(monkeypatch, knob, bad):
    """Measured on the first K8 head: `MOXIE_BRAIN_TIMEOUT_S=0` loaded silently and
    meant three things at once — the client got `timeout=None` (no bound at all; the
    SDK's own default is 600 s, and the chat call was still blocked at 8 s), the
    deadline forbade every retry (a 429 then OK raised the 429 after one attempt) and
    the streaming fallback had no budget (a stream-open 400 answered ERROR_OFFLINE).
    Now 0 or less has one meaning: refused at startup with a sentence, like the
    store-lock guard."""
    with pytest.raises(ValueError) as caught:
        reload_config(monkeypatch, KNOBS, **{knob: bad})
    assert knob in str(caught.value) and "positive number of seconds" in str(caught.value)
    c = reload_config(monkeypatch, KNOBS)                  # whole again, on the defaults
    assert (c.BRAIN_TIMEOUT_S, c.STT_TIMEOUT_S, c.TTS_TIMEOUT_S) == (60.0, 12.0, 15.0)


def test_no_site_builds_a_client_without_a_bound(monkeypatch):
    """`timeout_s` of 0 or less (or NaN, or inf) is refused at every construction site
    before any SDK client exists — with or without an injected client, so no deadline
    is ever 0 either. None stays each engine's documented default."""
    openai = pytest.importorskip("openai")
    built = []
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: built.append(kw))
    fake = _BusyGateway()
    for bad in (0, -1, 0.0, float("nan"), float("inf"), "0"):
        for build in (lambda: make_openai_chat(URL, "k", timeout_s=bad),
                      lambda: make_openai_stream(URL, "k", timeout_s=bad),
                      lambda: LLMApp(URL, "k", timeout_s=bad),
                      lambda: OpenAITranscriber(URL, "k", timeout_s=bad),
                      lambda: OpenAIVoiceSynthesizer(URL, "k", timeout_s=bad),
                      lambda: client_timeout(bad),
                      lambda: make_openai_chat(URL, "k", client=fake, timeout_s=bad),
                      lambda: LLMApp(URL, "k", client=fake, timeout_s=bad),
                      lambda: OpenAITranscriber(URL, "k", client=fake, timeout_s=bad),
                      lambda: OpenAIVoiceSynthesizer(URL, "k", client=fake, timeout_s=bad)):
            with pytest.raises(ValueError) as caught:
                build()
            assert "positive number of seconds" in str(caught.value)
    assert built == [], "a client was built before its knob was refused"
    assert fake.calls == 0
    # None is the documented default, per engine
    from moxie_sdk.chat import timeout_seconds    # the validator every site shares
    assert client_timeout(None).read == chat_seam.DEFAULT_TIMEOUT_S == 60.0
    assert timeout_seconds(None) == 60.0 and timeout_seconds(None, default=12) == 12.0
    assert timeout_seconds("7.5") == 7.5
    assert OpenAITranscriber(URL, "k", client=fake)._timeout_s == 12.0
    assert OpenAIVoiceSynthesizer(URL, "k", client=fake)._timeout_s == 15.0


def test_the_shipped_knob_keeps_a_retry_and_the_streaming_fallback(monkeypatch):
    """What knob 0 silently took away, pinned under the knob config ships, through
    `config._build_llm`: the SDK client ends up with the 60 s bound; a gateway that
    refuses to stream (400) still falls back to one whole reply, bounded to what is
    left; and a 429 then OK is still retried to its answer."""
    openai = pytest.importorskip("openai")
    made = []

    class _Gateway(_StreamFails):
        def __init__(self, **kw):
            super().__init__(_Clock(), 0.0, _Boom("streaming not supported"))
            self.client_kw = kw
            made.append(self)

    monkeypatch.setattr(openai, "OpenAI", _Gateway)
    c = reload_config(monkeypatch, KNOBS, MOXIE_APP="llm", MOXIE_LLM_BASE_URL=URL)
    app = c._build_llm()
    (gateway,) = made
    assert gateway.client_kw["timeout"].read == c.BRAIN_TIMEOUT_S == 60.0
    chunks = list(app.respond_stream(_turn()))
    assert [ch.text for ch in chunks] == [gateway.whole] and chunks[0].final
    assert chunks[0].result_code is ResultCode.SUCCESS
    assert (gateway.stream_calls, gateway.whole_calls) == (1, 1)
    assert 0 < gateway.whole_kw[0]["timeout"] <= 60.0, "the fallback gets what is left"

    class _Limited(Exception):
        status_code = 429
        response = type("R", (), {"headers": {"retry-after": "0"}})()

    attempts = []

    class _OnceBusy:
        def __init__(self):
            self.chat = self.completions = self

        def create(self, **kw):
            attempts.append(1)
            if len(attempts) == 1:
                raise _Limited("slow down")
            msg = type("M", (), {"content": "ok"})()
            return type("R", (), {"choices": [type("C", (), {"message": msg})()]})()

    chat = make_openai_chat(URL, "k", "m", client=_OnceBusy(), timeout_s=c.BRAIN_TIMEOUT_S,
                            on_backoff=None, pacer=Pacer(sleep=lambda s: None))
    assert chat(MESSAGES) == "ok" and len(attempts) == 2


def test_every_entry_point_stops_retrying_inside_its_bound():
    """A 503 forever with `Retry-After: 0.3` under a 0.2 s knob: the retry would start
    past the bound, so each entry point makes exactly one attempt and raises. Catches a
    site that stops passing its knob as the deadline (then: five attempts and 1.2 s of
    sleeps per site — the mutations the first review found surviving)."""
    fake = _BusyGateway()
    chat = make_openai_chat(URL, "k", "m", client=fake, timeout_s=0.2, on_backoff=None)
    with pytest.raises(_Busy):
        chat(MESSAGES)
    assert fake.calls == 1

    fake = _BusyGateway()
    stream = make_openai_stream(URL, "k", "m", client=fake, timeout_s=0.2, on_backoff=None)
    with pytest.raises(_Busy):
        next(iter(stream(MESSAGES)))
    assert fake.calls == 1

    fake, slept = _BusyGateway(), []
    ears = OpenAITranscriber(URL, "k", client=fake, timeout_s=0.2, sleep=slept.append)
    with pytest.raises(_Busy):
        ears.transcribe(PCM_16K)
    assert fake.calls == 1 and slept == []

    fake, slept = _BusyGateway(), []
    voice = OpenAIVoiceSynthesizer(URL, "k", client=fake, timeout_s=0.2, sleep=slept.append)
    with pytest.raises(_Busy):
        voice.synthesize("hi")
    assert fake.calls == 1 and slept == []


def test_the_streaming_brains_open_and_its_fallback_each_stop_inside_the_bound():
    """The same 503 forever through `LLMApp`: one open attempt, then the fallback gets
    one attempt inside what is left and answers with the fuzzy line (a 503 is not
    offline) — two requests for the turn, never ten."""
    fake = _BusyGateway()
    app = LLMApp(URL, "k", model="m", client=fake, timeout_s=0.2, clock=_Clock())
    chunks = list(app.respond_stream(_turn()))
    assert fake.calls == 2, "the open or its fallback kept retrying past the bound"
    assert len(chunks) == 1 and chunks[0].final and "fuzzy" in chunks[0].text
    assert chunks[0].result_code is not ResultCode.ERROR_OFFLINE
