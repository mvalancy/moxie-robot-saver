"""
Audio helpers for the LIVE speech tests (`test_live_talk_e2e.py`).

Everything here is dependency-light on purpose: numpy (already needed by the Whisper
backend) plus the standard library. Three groups:

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

import os
import re
import time
from collections import Counter
from typing import Iterable, List, Sequence, Tuple

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

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
    import numpy as np
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def resample_pcm16(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear-interpolation resample of mono PCM16. numpy only — no scipy/soxr.

    Good enough for ASR (Whisper's own front end low-passes at 8 kHz anyway) and it
    keeps the live tests installable from the same short dependency list as CI.
    """
    if src_rate == dst_rate or not pcm:
        return pcm
    import numpy as np
    src = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    n_out = int(round(len(src) * dst_rate / float(src_rate)))
    if n_out <= 0:
        return b""
    # sample positions of the output grid, in input-sample units
    pos = np.arange(n_out, dtype=np.float64) * (float(src_rate) / dst_rate)
    out = np.interp(pos, np.arange(len(src), dtype=np.float64), src)
    return np.clip(np.rint(out), -32768, 32767).astype(np.int16).tobytes()


def duration_s(pcm: bytes, sample_rate: int) -> float:
    """Seconds of mono PCM16."""
    return (len(pcm) / 2.0) / float(sample_rate or 1)


def spectral_flatness(pcm: bytes) -> float:
    """Wiener entropy: geometric mean / arithmetic mean of the power spectrum.

    ~0 for a pure sine (all energy in one bin), rising toward 1 for noise. Speech sits
    well above a tone because it has formants, fricatives and silences. This is how the
    round-trip test proves it did not accidentally pass on `ToneSynthesizer` output.
    """
    import numpy as np
    x = pcm16_to_float(pcm)
    if x.size < 256:
        return 0.0
    spec = np.abs(np.fft.rfft(x * np.hanning(x.size))) ** 2
    spec = spec[1:]                      # drop DC
    spec = spec[spec > 0]
    if spec.size == 0:
        return 0.0
    return float(np.exp(np.mean(np.log(spec))) / np.mean(spec))


def zcr_series(pcm: bytes, sample_rate: int = ROBOT_SAMPLE_RATE,
               frame_ms: int = 25) -> List[float]:
    """Per-frame zero-crossing rate. A steady tone gives a flat series; speech does
    not (voiced/unvoiced alternation), so the standard deviation separates them."""
    import numpy as np
    x = pcm16_to_float(pcm)
    n = max(1, int(sample_rate * frame_ms / 1000))
    out = []
    for i in range(0, len(x) - n, n):
        f = x[i:i + n]
        out.append(float(np.mean(np.abs(np.diff(np.signbit(f).astype(np.int8))))))
    return out


def zcr_std(pcm: bytes, sample_rate: int = ROBOT_SAMPLE_RATE) -> float:
    import numpy as np
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
