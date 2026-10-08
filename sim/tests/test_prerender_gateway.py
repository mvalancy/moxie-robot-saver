"""`sim/tools/prerender_audio.py --engine gateway`: the shipped clips' voice, rendered
through any OpenAI-compatible `/audio/speech`, proven against a stub on loopback.

Why each assertion exists:
  · the gateway REQUIRES `voice` (a 500 without it) and the MODEL is the voice, so a wrong
    model ships a second voice on one robot;
  · the key belongs in the Authorization header and nowhere else: this tool's output lands
    in terminals, CI logs and PRs, and urllib would carry the header across a redirect;
  · a `wav` reply's own header must beat the configured rate, and a `pcm` reply has only
    the configured rate to go on;
  · a JSON error, a login page, an empty body or silence is not a clip, and must not
    become one that passes every size check downstream.

Every test reads what the stub RECEIVED and what the tool handed its transcoder. ffmpeg is
never spawned (it is not a declared binary, see test_speech_guard.py): the fake writes the
ID3 frame ffmpeg 6.1 writes for `-metadata moxie_voice=...` (measured, see fake_mp3).

The last test reads the SHIPPED clips: every Moxie clip (the moxie and ambient groups) is in
the one voice the tool names as shipped, and the child's clips are not: another speaker.

Red on the tool before `--engine` existed: `main()` took no argv and `voice_tag` did not exist.
"""
import hashlib
import importlib.util
import io
import json
import math
import os
import struct
import sys
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TOOL = os.path.join(REPO, "sim", "tools", "prerender_audio.py")
AUDIO = os.path.join(REPO, "sim", "web", "audio")

#: A fake key, and the one string no output may ever contain.
KEY = "sk-test-0123456789abcdef"
LINE = "Hi! I am Moxie. It is nice to meet you."
KRISTIN = "gateway:tts-piper-kristin"
#: Every variable the tool reads, and the proxies urllib would route loopback through.
ENV = ("MOXIE_VOICE_BASE_URL", "MOXIE_LLM_BASE_URL", "MOXIE_VOICE_API_KEY",
       "MOXIE_LLM_API_KEY", "MOXIE_VOICE_MODEL", "MOXIE_TTS_VOICE", "MOXIE_VOICE_FORMAT",
       "MOXIE_VOICE_SAMPLE_RATE", "MOXIE_TEST_TTS_KEY", "http_proxy", "https_proxy",
       "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")


def tone(seconds=0.3, rate=22050, amp=8000):
    """16-bit mono PCM: an audible stand-in for speech."""
    n = int(seconds * rate)
    return struct.pack(f"<{n}h", *(int(amp * math.sin(2 * math.pi * 220 * i / rate))
                                   for i in range(n)))


def wav_bytes(pcm, rate=22050):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def syncsafe(n):
    return bytes(((n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F))


def fake_mp3(tags):
    """What ffmpeg 6.1 writes in front of the audio for `-metadata k=v`: an ID3v2.4 header
    and one UTF-8 (encoding byte 3) TXXX frame `k\\0v\\0` per tag. Then filler past the 2 KB
    floor test_fallback_coverage.mjs applies to a clip."""
    frames = b""
    for k, v in tags.items():
        data = b"\x03" + k.encode() + b"\x00" + v.encode() + b"\x00"
        frames += b"TXXX" + syncsafe(len(data)) + b"\x00\x00" + data
    return b"ID3\x04\x00\x00" + syncsafe(len(frames)) + frames + b"\xff\xf3" + bytes(4096)


class Stub:
    """A loopback `/audio/speech` that records every request. `replies` queues
    (status, headers, body); when it is empty the stub answers as the gateway does: a
    22050 Hz WAV for `wav`, raw PCM for `pcm`, both labelled `audio/mpeg` like LiteLLM."""

    def __init__(self):
        self.requests, self.replies, self.pcm = [], [], tone()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                req = {"path": self.path,
                       "headers": {k.lower(): v for k, v in self.headers.items()},
                       "json": json.loads(raw or b"{}")}
                stub.requests.append(req)
                status, headers, body = (stub.replies.pop(0) if stub.replies
                                         else stub.speech(req["json"]))
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def speech(self, body):
        audio = self.pcm if body.get("response_format") == "pcm" else wav_bytes(self.pcm)
        return 200, {"Content-Type": "audio/mpeg"}, audio

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stub():
    s = Stub()
    yield s
    s.close()


@pytest.fixture
def tool(monkeypatch):
    """The tool, imported fresh, with no stray settings and ffmpeg replaced by a recorder
    (`tool.transcoded`) that writes the tag ffmpeg would."""
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    spec = importlib.util.spec_from_file_location("prerender_audio_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.transcoded = []

    def transcode(wav, dest, tags):
        with wave.open(wav, "rb") as w:
            mod.transcoded.append({"rate": w.getframerate(), "channels": w.getnchannels(),
                                   "width": w.getsampwidth(),
                                   "pcm": w.readframes(w.getnframes()), "tags": dict(tags)})
        with open(dest, "wb") as fh:
            fh.write(fake_mp3(tags))

    # raising=False: a tool without the seam still loads, so it fails on behaviour.
    monkeypatch.setattr(mod, "transcode", transcode, raising=False)
    return mod


@pytest.fixture
def gateway(tool, monkeypatch, stub):
    """The plain configuration: the LLM base URL and key, as mqtt/.env carries them. After
    `tool`, which clears every setting first."""
    monkeypatch.setenv("MOXIE_LLM_BASE_URL", stub.base)
    monkeypatch.setenv("MOXIE_LLM_API_KEY", KEY)
    return stub


@pytest.fixture
def run(tool, capsys, monkeypatch):
    """Run the tool's CLI as a shell would: (exit code, everything it said). No run may
    say the key."""
    def _run(*argv):
        monkeypatch.setattr(sys, "argv", ["prerender_audio.py"] + [str(a) for a in argv])
        code, said = 0, ""
        try:
            tool.main()
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
            said = "" if exc.code is None else str(exc.code)
        out = capsys.readouterr()
        said = out.out + out.err + said
        assert KEY not in said, "the tool printed the API key"
        return code, said
    return _run


def phrases(tmp_path, *lines):
    path = tmp_path / "phrases.txt"
    path.write_text("# a comment line is skipped\n" + "\n".join(lines or (LINE,)) + "\n")
    return path


def clip_rel(group, text):
    return f"{group}/{hashlib.sha1(text.encode('utf-8')).hexdigest()[:16]}.mp3"


# --------------------------------------------------------------------------- #
# The request, and the audio that comes back
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("fmt, configured_rate, framed_at", [
    ("wav", "16000", 22050),     # the WAV header's own rate wins over configuration
    ("pcm", "24000", 24000),     # raw PCM has only the configured rate
])
def test_the_request_the_gateway_needs_and_the_audio_it_returns(
        tool, gateway, run, tmp_path, monkeypatch, fmt, configured_rate, framed_at):
    monkeypatch.setenv("MOXIE_VOICE_FORMAT", fmt)
    monkeypatch.setenv("MOXIE_VOICE_SAMPLE_RATE", configured_rate)
    out = tmp_path / "audio"
    code, said = run("--engine", "gateway", "--model", "tts-piper-kristin",
                     "--phrases", phrases(tmp_path), "--out", out)
    assert code == 0, said

    [req] = gateway.requests
    assert req["path"] == "/v1/audio/speech"
    assert req["headers"]["authorization"] == "Bearer " + KEY
    assert req["headers"]["content-type"] == "application/json"
    # `voice` is derived from the model like the SDK and functions/api derive it: present,
    # because LiteLLM 500s without it, and ignored, because the model IS the voice.
    assert req["json"] == {"model": "tts-piper-kristin", "voice": "kristin", "input": LINE,
                           "response_format": fmt}

    [clip] = tool.transcoded
    assert (clip["rate"], clip["channels"], clip["width"]) == (framed_at, 1, 2)
    assert clip["pcm"] == gateway.pcm, "the samples must reach the transcoder untouched"
    assert clip["tags"] == {"moxie_voice": KRISTIN}

    rel = clip_rel("moxie", LINE)
    assert json.loads((out / "index.json").read_text()) == {"moxie": {LINE: rel}, "child": {}}
    assert tool.voice_tag(str(out / rel)) == KRISTIN
    assert "tts-piper-kristin" in said and gateway.base not in said, \
        "the log names the voice, never the host"


def test_voice_settings_beat_the_llm_settings_they_fall_back_to(
        tool, stub, run, tmp_path, monkeypatch):
    monkeypatch.setenv("MOXIE_VOICE_BASE_URL", stub.base)
    monkeypatch.setenv("MOXIE_LLM_BASE_URL", "http://127.0.0.1:9/v1")   # discard: never dialled
    monkeypatch.setenv("MOXIE_VOICE_API_KEY", KEY)
    monkeypatch.setenv("MOXIE_LLM_API_KEY", "sk-the-llm-key")
    monkeypatch.setenv("MOXIE_VOICE_MODEL", "tts-piper-ryan")             # no --model flag
    monkeypatch.setenv("MOXIE_TTS_VOICE", "alloy")                        # beats the derived one
    code, said = run("--engine", "gateway", "--retries", "0",
                     "--phrases", phrases(tmp_path), "--out", tmp_path / "audio")
    assert code == 0, said
    [req] = stub.requests
    assert req["headers"]["authorization"] == "Bearer " + KEY
    assert (req["json"]["model"], req["json"]["voice"]) == ("tts-piper-ryan", "alloy")


def test_a_flag_beats_the_environment_which_beats_the_env_file(
        tool, stub, run, tmp_path, monkeypatch):
    env_file = tmp_path / "dot.env"
    env_file.write_text(
        "MOXIE_LLM_BASE_URL=http://127.0.0.1:9/v1   # beaten by --base-url\n"
        f"export MOXIE_LLM_API_KEY='{KEY}'\n"
        "MOXIE_VOICE_MODEL=tts-piper-ryan # beaten by the environment\n"
        "MOXIE_VOICE_FORMAT=pcm\n")
    monkeypatch.setenv("MOXIE_VOICE_MODEL", "tts-piper-amy")
    code, said = run("--engine", "gateway", "--env-file", env_file,
                     "--base-url", stub.base, "--retries", "0",
                     "--phrases", phrases(tmp_path), "--out", tmp_path / "audio")
    assert code == 0, said
    [req] = stub.requests
    assert req["headers"]["authorization"] == "Bearer " + KEY, "a quoted value is verbatim"
    assert req["json"] == {"model": "tts-piper-amy", "voice": "amy", "input": LINE,
                           "response_format": "pcm"}


def test_the_key_comes_from_the_named_variable_or_is_not_sent_at_all(
        tool, stub, run, tmp_path, monkeypatch):
    monkeypatch.setenv("MOXIE_LLM_BASE_URL", stub.base)
    monkeypatch.setenv("MOXIE_LLM_API_KEY", "sk-the-llm-key")
    monkeypatch.setenv("MOXIE_TEST_TTS_KEY", KEY)
    argv = ("--engine", "gateway", "--phrases", phrases(tmp_path), "--out", tmp_path / "a")
    assert run(*argv, "--api-key-env", "MOXIE_TEST_TTS_KEY")[0] == 0
    assert stub.requests[-1]["headers"]["authorization"] == "Bearer " + KEY

    monkeypatch.delenv("MOXIE_LLM_API_KEY")
    code, said = run("--engine", "gateway", "--phrases", phrases(tmp_path),
                     "--out", tmp_path / "b")
    assert code == 0, said
    assert "authorization" not in stub.requests[-1]["headers"]
    assert "no Authorization header" in said


def test_no_base_url_is_refused_before_anything_is_sent(
        tool, run, tmp_path, monkeypatch):
    monkeypatch.setenv("MOXIE_LLM_API_KEY", KEY)
    code, said = run("--engine", "gateway", "--phrases", phrases(tmp_path),
                     "--out", tmp_path / "audio")
    assert code != 0 and "needs a base URL" in said
    assert not (tmp_path / "audio").exists()


# --------------------------------------------------------------------------- #
# What is not speech never becomes a clip, and the key never leaves the header
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("fmt, body, why", [
    ("wav", json.dumps({"error": {"message": f"no such model (key {KEY})"}}).encode(), "JSON"),
    ("wav", b"\n<!DOCTYPE html><html><title>Sign in</title></html>", "HTML"),
    ("wav", b"", "empty"),
    ("wav", wav_bytes(bytes(8000)), "silence"),
    ("wav", tone(), "no RIFF header"),           # raw samples where a WAV was asked for
    ("pcm", bytes(8000), "silence"),
], ids=["json-error", "login-page", "empty", "silent-wav", "not-a-wav", "silent-pcm"])
def test_a_reply_that_is_not_speech_is_refused(
        tool, gateway, run, tmp_path, monkeypatch, fmt, body, why):
    monkeypatch.setenv("MOXIE_VOICE_FORMAT", fmt)
    gateway.replies.append((200, {"Content-Type": "audio/mpeg"}, body))
    out = tmp_path / "audio"
    code, said = run("--engine", "gateway", "--phrases", phrases(tmp_path),
                     "--out", out)
    assert code != 0 and why in said, said
    assert tool.transcoded == [] and not (out / "index.json").exists()
    assert not (out / clip_rel("moxie", LINE)).exists()


def test_an_http_error_is_reported_without_the_key_and_not_retried(
        tool, gateway, run, tmp_path):
    gateway.replies.append((401, {"Content-Type": "application/json"}, json.dumps(
        {"error": {"message": f"Authentication Error, invalid key {KEY}"}}).encode()))
    code, said = run("--engine", "gateway", "--phrases", phrases(tmp_path),
                     "--out", tmp_path / "audio")
    assert code != 0 and "HTTP 401" in said and "Authentication Error" in said
    assert len(gateway.requests) == 1, "a refusal is not worth a second call"


def test_a_redirect_is_never_followed_with_the_key(
        tool, gateway, run, tmp_path):
    elsewhere = Stub()
    try:
        gateway.replies.append((302, {"Location": elsewhere.base + "/audio/speech"}, b""))
        code, said = run("--engine", "gateway", "--phrases", phrases(tmp_path),
                         "--out", tmp_path / "audio")
        assert code != 0 and "HTTP 302" in said
        assert elsewhere.requests == [], "the Authorization header followed a redirect"
    finally:
        elsewhere.close()


# --------------------------------------------------------------------------- #
# The budget: retries count, and a run that cannot fit is refused up front
# --------------------------------------------------------------------------- #

def test_a_busy_gateway_is_retried_and_every_try_is_counted(
        tool, gateway, run, tmp_path):
    gateway.replies.append((503, {"Retry-After": "0"}, b"busy"))
    code, said = run("--engine", "gateway", "--max-calls", "2",
                     "--phrases", phrases(tmp_path), "--out", tmp_path / "audio")
    assert code == 0, said
    assert len(gateway.requests) == 2 and len(tool.transcoded) == 1
    assert "1 new clip(s) in 2 gateway call(s)" in said


def test_the_budget_stops_a_retry_and_refuses_a_run_that_cannot_fit(
        tool, gateway, run, tmp_path):
    gateway.replies.append((503, {"Retry-After": "0"}, b"busy"))
    code, said = run("--engine", "gateway", "--max-calls", "1",
                     "--phrases", phrases(tmp_path), "--out", tmp_path / "a")
    assert code != 0 and "--max-calls 1" in said and len(gateway.requests) == 1

    code, said = run("--engine", "gateway", "--max-calls", "2",
                     "--phrases", phrases(tmp_path, "One.", "Two.", "Three."),
                     "--out", tmp_path / "b")
    assert code != 0 and "Nothing was spent" in said
    assert len(gateway.requests) == 1, "the oversized run must not make a single call"


# --------------------------------------------------------------------------- #
# --rerender: one call per line, resumable, and never the child
# --------------------------------------------------------------------------- #

SHARED = "Hmm, let me think about that one."     # in both moxie and ambient, like filler.py's 8
ONLY_MOXIE = "Great job! High five!"
ONLY_AMBIENT = "I am not plotting anything. Probably."
CHILD = "Thank you Moxie!"


def amy_set(out):
    """A manifest in the old voice: four Moxie clips tagged amy, one untagged child clip."""
    manifest = {"moxie": {SHARED: clip_rel("moxie", SHARED),
                          ONLY_MOXIE: clip_rel("moxie", ONLY_MOXIE)},
                "child": {CHILD: clip_rel("child", CHILD)},
                "ambient": {SHARED: clip_rel("ambient", SHARED),
                            ONLY_AMBIENT: clip_rel("ambient", ONLY_AMBIENT)}}
    for group, entries in manifest.items():
        for rel in entries.values():
            (out / rel).parent.mkdir(parents=True, exist_ok=True)
            tags = {} if group == "child" else {"moxie_voice": "piper:en_US-amy-medium"}
            (out / rel).write_bytes(fake_mp3(tags))
    (out / "index.json").write_text(json.dumps(manifest, indent=1))
    return manifest


def test_rerender_moves_every_moxie_clip_to_the_new_voice_once(
        tool, gateway, run, tmp_path):
    out = tmp_path / "audio"
    manifest = amy_set(out)
    child_before = (out / manifest["child"][CHILD]).read_bytes()
    argv = ("--engine", "gateway", "--model", "tts-piper-kristin",
            "--rerender", "moxie", "--rerender", "ambient", "--out", out)

    code, said = run(*argv)
    assert code == 0, said
    assert sorted(r["json"]["input"] for r in gateway.requests) == \
        sorted([SHARED, ONLY_MOXIE, ONLY_AMBIENT]), "a line two groups share costs one call"
    for group in ("moxie", "ambient"):
        for rel in manifest[group].values():
            assert tool.voice_tag(str(out / rel)) == KRISTIN, rel
    assert (out / manifest["child"][CHILD]).read_bytes() == child_before
    assert json.loads((out / "index.json").read_text()) == manifest, \
        "re-rendering changes voices, never the manifest"

    code, said = run(*argv)
    assert code == 0 and "0 new clip(s) in 0 gateway call(s)" in said
    assert len(gateway.requests) == 3, "clips already in this voice are skipped"


def test_a_stopped_rerender_resumes_where_it_stopped(
        tool, gateway, run, tmp_path):
    out = tmp_path / "audio"
    manifest = amy_set(out)
    argv = ("--engine", "gateway", "--rerender", "moxie", "--rerender", "ambient",
            "--retries", "0", "--out", out)
    gateway.replies += [(200, {}, wav_bytes(gateway.pcm)),
                        (400, {}, b'{"error": {"message": "nope"}}')]
    code, said = run(*argv)
    assert code != 0 and "resume" in said and len(gateway.requests) == 2
    assert json.loads((out / "index.json").read_text()) == manifest

    code, said = run(*argv)
    assert code == 0, said
    asked = [r["json"]["input"] for r in gateway.requests]
    assert asked[0] == SHARED and SHARED not in asked[2:], \
        "the clip finished before the stop is reused, in both of its groups"
    assert len(asked) == 4
    for group in ("moxie", "ambient"):
        for rel in manifest[group].values():
            assert tool.voice_tag(str(out / rel)) == KRISTIN, rel


def test_the_child_is_never_rendered_in_moxies_voice(
        tool, gateway, run, tmp_path):
    out = tmp_path / "audio"
    amy_set(out)
    code, said = run("--engine", "gateway", "--rerender", "child", "--out", out)
    assert code != 0 and "--engine piper" in said and "Nothing was spent" in said

    # The demo session brings new child lines too: refused before Moxie's lines are spent.
    code, said = run("--engine", "gateway",
                     os.path.join(REPO, "sim", "web", "sessions", "demo.json"),
                     "--out", tmp_path / "fresh")
    assert code != 0 and "child" in said
    assert gateway.requests == []


# --------------------------------------------------------------------------- #
# The shipped set
# --------------------------------------------------------------------------- #

def test_every_shipped_moxie_clip_is_in_one_voice_and_the_child_is_not(tool):
    manifest = json.load(open(os.path.join(AUDIO, "index.json"), encoding="utf-8"))
    want = "gateway:" + tool.SHIPPED_MODEL
    wrong = []
    for group in ("moxie", "ambient"):
        for text, rel in manifest[group].items():
            got = tool.voice_tag(os.path.join(AUDIO, rel))
            if got != want:
                wrong.append(f"{rel} ({text[:40]!r}): {got or 'untagged'}")
    assert len(manifest["moxie"]) + len(manifest["ambient"]) >= 94
    assert not wrong, (
        f"{len(wrong)} Moxie clip(s) are not in the shipped voice {want}, so the page speaks "
        f"in two voices. Render them with: python3 sim/tools/prerender_audio.py --engine "
        f"gateway --env-file mqtt/.env --model {tool.SHIPPED_MODEL} --rerender moxie "
        f"--rerender ambient\n  " + "\n  ".join(wrong))
    for text, rel in manifest["child"].items():
        assert tool.voice_tag(os.path.join(AUDIO, rel)) != want, \
            f"the child's {text!r} is in Moxie's voice: one voice talking to itself"
