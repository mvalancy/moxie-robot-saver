"""
Audio helpers for the LIVE speech tests (`test_live_talk_e2e.py`).

Everything here is dependency-light on purpose: numpy (already needed by the Whisper
backend) plus the standard library. Three groups:

NUMPY IS OPTIONAL HERE, AND THAT IS LOAD-BEARING — read `_np()` below before changing it.
Two of the callers (`test_live_gateway_stt.py`, `test_live_hosted_ears.py`) exist to prove
the CLOUD ears and voice work on a box that installed nothing but `openai`, because that is
what a hosted deployment is. So every measurement in here that a numpy-free caller needs
has a stdlib twin — `resample_pcm16` / `resample_pcm16_stdlib`, and (since 2026-09-05)
`is_real_speech` / `is_real_speech_stdlib` — and the numpy import is named in exactly one
place so a missing wheel says which twin to call instead of dying with a bare
`ModuleNotFoundError` from the middle of a live turn. It did exactly that, once: measured
2026-09-05, `test_live_gateway_stt.py` ran a complete healthy live turn (word overlap 1.00,
203 612 B of real audio) and then failed at `helpers_audio.py:157` on the last assertion in
the file, because `is_real_speech` had no numpy-free form and that suite deliberately
`importorskip`s no numpy.

* **PCM maths** — `resample_pcm16` (the robot's mic is 16 kHz; Piper's medium voices
  render at 22050 Hz, so the round-trip has to resample), plus `spectral_flatness` and
  `zcr_series` so a test can prove the audio it just made is *speech* and not the
  built-in `ToneSynthesizer` placeholder.
* **Transcript scoring** — `word_overlap`, a recall ratio over normalized words. ASR is
  lossy and a sampling LLM is not reproducible, so live assertions are ratios, never
  string equality (same reasoning as the rate assertions in `test_live_action_tags.py`).
* **Wire framing** — `pb_zmq_stt_frame` / `stt_frames` hand-encode the
  `zmqSTTRequest` protobuf a real robot puts on `events/zmq`, using the same
  dependency-free varint approach `moxie_sdk.stt.decode_zmq_stt_frame` uses to read it
  (field numbers from embodied/perception/audio/zmqSTT.proto: vad=2, audio_content=3,
  uuid=4). Mirroring the decoder keeps the SDK free of a protobuf dependency.

No pytest imports here — this is a library, so the skip decisions stay in the test file.
"""
from __future__ import annotations

import cmath
import math
import os
import re
import time
from array import array
from collections import Counter
from typing import Iterable, List, Sequence, Tuple

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


# ------------------------------------------------------------- the one numpy --
def _np():
    """numpy, imported at the point of use — and the ONLY place this module names it.

    THREE choices are baked in here, and all three were made the wrong way first.

    *Why not a module-scope `import numpy`.* This module must import on a box that has
    only `openai` installed, because `test_live_gateway_stt.py` and
    `test_live_hosted_ears.py` exist to prove the cloud ears/voice work on exactly such a
    box (see `resample_pcm16_stdlib`). A module-scope import would fail their collection.

    *Why not `pytest.importorskip("numpy")`.* This is a library — the file's own docstring
    has said "no pytest imports here" since it was written — and, more importantly, a skip
    decided down here is a skip nobody reads. This repo has now been bitten four times by
    a missing package making the tests that need it importorskip themselves away, which is
    a skip that reads as a pass. The right place for that decision is the test file, and
    the right *answer* for a numpy-free suite is the stdlib twin, not a skip.

    *Why not five separate in-function imports* — which is what this was until 2026-09-05.
    Five copies of a dependency are five chances to be inconsistent, and the failure they
    produced was a bare `ModuleNotFoundError: No module named 'numpy'` from
    `helpers_audio.py:157`, six frames below a live test, AFTER four gateway calls had been
    spent. One accessor, one message, and the message names the way out.
    """
    try:
        import numpy as np                                  # noqa: PLC0415 (deliberate)
    except ImportError as exc:                              # a broken install, too, not
        raise ModuleNotFoundError(                          # only an absent one
            "numpy is not importable, and this helper needs it. It is declared in "
            "sim/tests/requirements-hermetic.txt (the ONE test-dependency list) — "
            "`pip install -r sim/tests/requirements.txt` provisions it. If this is a "
            "deliberately numpy-free suite (the hosted-ears / gateway-STT tests are), "
            "call the stdlib twin instead: resample_pcm16_stdlib, "
            "spectral_flatness_stdlib, is_real_speech_stdlib."
        ) from exc
    return np


#: Moxie's own voice (the one the supervisor ships with) and a deliberately *different*
#: voice to play the child, so a transcript can never be an echo of Moxie's own audio.
MOXIE_VOICE_FILE = "en_US-amy-medium.onnx"
CHILD_VOICE_FILE = "en_US-lessac-medium.onnx"


def _voice_dirs():
    """Where a Piper voice might live, best first.

    `sim/tts/voices/*.onnx` is git-ignored (63 MB per voice), so a fresh clone — or a
    git *worktree*, which starts empty of ignored files — will not have them even though
    the developer's main checkout does. Look in this tree, then in sibling checkouts /
    worktrees of the same project, then wherever `MOXIE_VOICES_DIR` points. Nothing here
    downloads: a missing voice makes the live tests skip, it never fails them.
    """
    dirs = []
    env = os.environ.get("MOXIE_VOICES_DIR", "").strip()
    if env:
        dirs.append(env)
    dirs.append(os.path.join(REPO, "sim", "tts", "voices"))
    parent = os.path.dirname(REPO)
    try:
        for name in sorted(os.listdir(parent)):
            cand = os.path.join(parent, name, "sim", "tts", "voices")
            if cand not in dirs and os.path.isdir(cand):
                dirs.append(cand)
    except OSError:
        pass
    return dirs


def find_voice(filename: str) -> str:
    """Absolute path of a Piper `.onnx` voice, or "" when it is not installed."""
    for d in _voice_dirs():
        p = os.path.join(d, filename)
        if os.path.isfile(p):
            return p
    return ""


MOXIE_VOICE = find_voice(MOXIE_VOICE_FILE)
CHILD_VOICE = find_voice(CHILD_VOICE_FILE)

#: The robot streams 16 kHz mono PCM16 on the perception bus; Whisper wants the same.
ROBOT_SAMPLE_RATE = 16000


# ---------------------------------------------------------------- PCM maths --
def pcm16_to_float(pcm: bytes):
    """Raw 16-bit little-endian PCM → float32 numpy array in [-1, 1]."""
    np = _np()
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear-interpolation resample of mono PCM16. numpy only — no scipy/soxr.

    Good enough for ASR (Whisper's own front end low-passes at 8 kHz anyway) and it
    keeps the live tests installable from the same short dependency list as CI.
    """
    if src_rate == dst_rate or not pcm:
        return pcm
    np = _np()
    src = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    n_out = int(round(len(src) * dst_rate / float(src_rate)))
    if n_out <= 0:
        return b""
    # sample positions of the output grid, in input-sample units
    pos = np.arange(n_out, dtype=np.float64) * (float(src_rate) / dst_rate)
    out = np.interp(pos, np.arange(len(src), dtype=np.float64), src)
    return np.clip(np.rint(out), -32768, 32767).astype(np.int16).tobytes()


def resample_pcm16_stdlib(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """`resample_pcm16` with **no numpy** — standard library only.

    The gateway-STT live tier proves the robot-rate path (22050 Hz TTS audio → the
    16 kHz the perception bus actually carries), and it must be able to do that on a box
    that installed nothing but `openai`: the whole point of the cloud ears is that a
    hosted deployment has no local model wheels, and numpy arrives with faster-whisper.

    Prefers `audioop.ratecv` (a proper filtered converter, stdlib through 3.12) and falls
    back to plain linear interpolation where `audioop` was removed (3.13+). Both are
    ample for ASR — Whisper's own front end low-passes at 8 kHz.
    """
    if src_rate == dst_rate or not pcm:
        return pcm
    try:
        import warnings
        with warnings.catch_warnings():                 # 3.12 deprecates it; 3.13 drops it
            warnings.simplefilter("ignore", DeprecationWarning)
            import audioop                              # stdlib <= 3.12
        return audioop.ratecv(pcm, 2, 1, int(src_rate), int(dst_rate), None)[0]
    except ImportError:
        pass
    from array import array
    src = array("h")
    src.frombytes(pcm[:len(pcm) - (len(pcm) % 2)])
    n_out = int(round(len(src) * dst_rate / float(src_rate)))
    if n_out <= 0 or not len(src):
        return b""
    step = (len(src) - 1) / float(max(1, n_out - 1)) if n_out > 1 else 0.0
    out = array("h", bytes(2 * n_out))
    for i in range(n_out):
        pos = i * step
        j = int(pos)
        frac = pos - j
        a = src[j]
        b = src[j + 1] if j + 1 < len(src) else a
        out[i] = max(-32768, min(32767, int(round(a + (b - a) * frac))))
    return out.tobytes()


def duration_s(pcm: bytes, sample_rate: int) -> float:
    """Seconds of mono PCM16."""
    return (len(pcm) / 2.0) / float(sample_rate or 1)


def spectral_flatness(pcm: bytes) -> float:
    """Wiener entropy: geometric mean / arithmetic mean of the power spectrum.

    ~0 for a pure sine (all energy in one bin), rising toward 1 for noise. Speech sits
    well above a tone because it has formants, fricatives and silences. This is how the
    round-trip test proves it did not accidentally pass on `ToneSynthesizer` output.
    """
    np = _np()
    x = pcm16_to_float(pcm)
    if x.size < 256:
        return 0.0
    spec = np.abs(np.fft.rfft(x * np.hanning(x.size))) ** 2
    spec = spec[1:]                      # drop DC
    spec = spec[spec > 0]
    if spec.size == 0:
        return 0.0
    return float(np.exp(np.mean(np.log(spec))) / np.mean(spec))


#: The line between REAL SPEECH and the built-in placeholder, in spectral flatness.
#:
#: `ToneSynthesizer` emits 22050 Hz mono PCM16 exactly like the gateway voice does, so a
#: sample rate proves nothing about who spoke — every live audio assertion has to clear
#: this instead. Observed on this gateway: tone ~3.1e-12, piper-amy ~5.2e-02, i.e. ten
#: orders of magnitude apart; 1e-6 sits between them and is tuned to neither.
#:
#: Lives here rather than in one test file because more than one live suite needs it
#: (the turn e2e and the telehealth voice), and a floor that drifts per-file is a floor
#: that stops meaning anything.
SPEECH_FLATNESS_FLOOR = 1e-6


def is_real_speech(pcm: bytes) -> bool:
    """True when this audio is broadband enough to be a voice rather than the tone."""
    return spectral_flatness(pcm) > SPEECH_FLATNESS_FLOOR


def _hann(n: int) -> List[float]:
    """The same window `np.hanning` applies, in the standard library."""
    if n < 2:
        return [0.0] * n
    return [0.5 - 0.5 * math.cos(2.0 * math.pi * i / (n - 1)) for i in range(n)]


def _fft_power(frame: Sequence[float]) -> List[float]:
    """Power spectrum of one power-of-two frame, bins 1..N/2 (DC dropped).

    An iterative in-place radix-2 Cooley-Tukey FFT over `complex`. Twelve lines of
    stdlib rather than a dependency, because the whole point of this path is to need no
    dependency — see `spectral_flatness_stdlib`.
    """
    n = len(frame)
    buf = [complex(v, 0.0) for v in frame]
    # bit-reversal permutation
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            buf[i], buf[j] = buf[j], buf[i]
    # butterflies, stage by stage
    size = 2
    while size <= n:
        step = cmath.exp(-2j * cmath.pi / size)
        half = size // 2
        for start in range(0, n, size):
            w = 1 + 0j
            for k in range(start, start + half):
                a, b = buf[k], buf[k + half] * w
                buf[k], buf[k + half] = a + b, a - b
                w *= step
        size <<= 1
    return [abs(buf[i]) ** 2 for i in range(1, n // 2 + 1)]


#: Frame geometry of `spectral_flatness_stdlib`. 2048 samples is ~93 ms at 22050 Hz —
#: long enough to resolve a formant, short enough that a pure-Python FFT of it costs
#: about a millisecond. Eight frames spread across the buffer cover a whole utterance
#: without the O(N log N) of a 200 000-sample transform in interpreted code.
_STDLIB_FLATNESS_FRAME = 2048
_STDLIB_FLATNESS_FRAMES = 8


def spectral_flatness_stdlib(pcm: bytes) -> float:
    """`spectral_flatness` with **no numpy** — standard library only.

    WHY THIS EXISTS, and it is the same reason `resample_pcm16_stdlib` does: the gateway
    ears and the hosted-ears route must be provable on a box that installed nothing but
    `openai`, because that is precisely what a hosted deployment is, and numpy arrives
    only with faster-whisper. `test_live_gateway_stt.py` was written that way on purpose —
    and then closed with `assert A.is_real_speech(...)`, the one measurement in this file
    that had no numpy-free form. Measured 2026-09-05: that suite ran a complete, healthy
    live turn (overlap 1.00, a real reply, 203 612 B @ 22050 Hz) and then died with
    `ModuleNotFoundError` on its last line, four gateway calls in. The alternative fix —
    `importorskip("numpy")` at the top of that file — would have deleted the entire
    gateway-ears proof on exactly the deployment shape it exists to cover, and turned a
    loud red into a silent pass. So the measurement grew a twin instead.

    NOT bit-identical to the numpy version, and it does not claim to be: that one windows
    the whole buffer in one transform, this one averages the flatness of up to eight
    2048-sample frames (skipping any that are pure digital silence, which has no spectrum
    to be flat or peaky). What matters is the VERDICT, and the verdict has ten orders of
    magnitude of headroom on both sides of `SPEECH_FLATNESS_FLOOR` — measured 2026-09-05,
    a tone lands at 9e-10 and a real recorded voice at 1e-2 on this implementation (5e-16
    and 7e-03 on the numpy one). `sim/tests/test_speech_guard.py` asserts the two agree in
    both directions, on a tone, on synthetic broadband audio and on a real clip, rather
    than leaving that as a claim.
    """
    samples = array("h")
    samples.frombytes(pcm[:len(pcm) - (len(pcm) % 2)])
    n = _STDLIB_FLATNESS_FRAME
    if len(samples) < n:
        return 0.0
    window = _hann(n)
    # Evenly spaced frame starts across the whole buffer, so a leading silence cannot
    # decide the answer for an utterance that speaks later.
    span = len(samples) - n
    count = min(_STDLIB_FLATNESS_FRAMES, max(1, span // n + 1))
    starts = [int(round(span * i / float(count - 1))) if count > 1 else 0
              for i in range(count)]
    flatnesses = []
    for start in starts:
        frame = [samples[start + i] * window[i] / 32768.0 for i in range(n)]
        power = [p for p in _fft_power(frame) if p > 0.0]
        if len(power) < n // 4:
            continue                     # digital silence (or near it): no spectrum
        # Geometric mean in the log domain — the direct product underflows float64 long
        # before bin 1024, which is how a naive version reports every signal as a tone.
        log_mean = math.fsum(math.log(p) for p in power) / len(power)
        arith_mean = math.fsum(power) / len(power)
        if arith_mean <= 0.0:
            continue
        flatnesses.append(math.exp(log_mean) / arith_mean)
    if not flatnesses:
        return 0.0
    return math.fsum(flatnesses) / len(flatnesses)


def is_real_speech_stdlib(pcm: bytes) -> bool:
    """`is_real_speech` with no numpy — same floor, same verdict, stdlib only."""
    return spectral_flatness_stdlib(pcm) > SPEECH_FLATNESS_FLOOR


def zcr_series(pcm: bytes, sample_rate: int = ROBOT_SAMPLE_RATE,
               frame_ms: int = 25) -> List[float]:
    """Per-frame zero-crossing rate. A steady tone gives a flat series; speech does
    not (voiced/unvoiced alternation), so the standard deviation separates them."""
    np = _np()
    x = pcm16_to_float(pcm)
    n = max(1, int(sample_rate * frame_ms / 1000))
    out = []
    for i in range(0, len(x) - n, n):
        f = x[i:i + n]
        out.append(float(np.mean(np.abs(np.diff(np.signbit(f).astype(np.int8))))))
    return out


def zcr_std(pcm: bytes, sample_rate: int = ROBOT_SAMPLE_RATE) -> float:
    np = _np()
    s = zcr_series(pcm, sample_rate)
    return float(np.std(s)) if s else 0.0


# --------------------------------------------------------- transcript maths --
_WORD_RE = re.compile(r"[a-z0-9']+")


def normalize_words(text: str) -> List[str]:
    """Lowercase, punctuation-free word list — ASR punctuation/casing is not content."""
    return _WORD_RE.findall((text or "").lower().replace("’", "'"))


def word_overlap(reference: str, hypothesis: str) -> float:
    """Fraction of `reference` words present in `hypothesis` (multiset recall, 0..1).

    Recall rather than F1 on purpose: a transcript that gets every word right plus a
    stray "um" is a success, while a transcript that drops half the sentence is not.
    """
    ref = normalize_words(reference)
    if not ref:
        return 0.0
    pool = Counter(normalize_words(hypothesis))
    hits = 0
    for w in ref:
        if pool[w] > 0:
            pool[w] -= 1
            hits += 1
    return hits / float(len(ref))


# ------------------------------------------------------------- zmqSTT wire ---
def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


ZMQ_STT_FULL_NAME = b"embodied.perception.audio.zmqSTTRequest"


def pb_zmq_stt_frame(vad: int, audio: bytes = b"", uuid: str = "") -> bytes:
    """Encode one `events/zmq` frame exactly as the robot sends it:
    `b'<proto.full_name>:' + zmqSTTRequest_bytes`.

    The encoder mirrors `moxie_sdk.stt.decode_zmq_stt_frame` (varints by hand) so the
    tests need no protobuf runtime — the same trade-off the SDK made on the read side.
    """
    body = b"\x10" + _varint(int(vad))                       # field 2 (vad), varint
    if audio:
        body += b"\x1a" + _varint(len(audio)) + audio        # field 3, len-delimited
    if uuid:
        u = uuid.encode()
        body += b"\x22" + _varint(len(u)) + u                # field 4, len-delimited
    return ZMQ_STT_FULL_NAME + b":" + body


def stt_frames(pcm: bytes, uuid: str = "utt-1", *,
               sample_rate: int = ROBOT_SAMPLE_RATE,
               frame_ms: int = 200) -> List[bytes]:
    """One utterance of 16 kHz PCM16 → the list of `events/zmq` frames a robot streams:
    START_OF_SPEECH, then SPEECH per chunk, then a trailing END_OF_SPEECH (empty audio),
    every frame carrying the same utterance uuid.
    """
    from moxie_sdk.stt import VADState
    step = max(2, int(sample_rate * frame_ms / 1000) * 2)     # bytes per chunk
    chunks = [pcm[i:i + step] for i in range(0, len(pcm), step)] or [b""]
    frames = [pb_zmq_stt_frame(VADState.START_OF_SPEECH, chunks[0], uuid)]
    for c in chunks[1:]:
        frames.append(pb_zmq_stt_frame(VADState.SPEECH, c, uuid))
    frames.append(pb_zmq_stt_frame(VADState.END_OF_SPEECH, b"", uuid))
    return frames


# ------------------------------------------------------------------ timing ---
class Stage:
    """`with Stage('stt') as s: ...` → `s.seconds`. Used to report the real wall clock
    of each leg of the talk loop (the '~20 s reprompt window' reality check)."""

    def __init__(self, label: str):
        self.label = label
        self.seconds = 0.0

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.seconds = time.perf_counter() - self._t0
        return False

    def __str__(self):
        return f"{self.label}={self.seconds:.2f}s"


def timing_line(*stages: "Stage") -> str:
    total = sum(s.seconds for s in stages)
    return "  ".join(str(s) for s in stages) + f"  total={total:.2f}s"
