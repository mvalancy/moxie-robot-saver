#!/usr/bin/env python3
"""
🔊 Pre-render session audio for a STATIC deploy (e.g. Cloudflare Pages).

A scripted session's lines are known in advance, so we can render them at build time
and ship plain audio files — no TTS service, no STT, no LLM at runtime. Renders BOTH
sides: Moxie's replies and the child's turns.

Two engines, both first-class (an offline install has no gateway; a hosted one has no
local models):

    --engine piper     local Piper and a voice from sim/tts/voices (the default)
    --engine gateway   any OpenAI-compatible `POST {base}/audio/speech` — a LiteLLM
                       gateway, OpenAI, a local server. Base URL, key and model come from
                       flags or the environment (MOXIE_VOICE_BASE_URL → MOXIE_LLM_BASE_URL,
                       MOXIE_VOICE_API_KEY → MOXIE_LLM_API_KEY, MOXIE_VOICE_MODEL), or
                       from a dotenv via --env-file. No host is written down here.

    python3 sim/tools/prerender_audio.py sim/web/sessions/demo.json --out sim/web/audio

The shipped clips are ONE voice — the one the live demo answers in — so changing it
means re-rendering every Moxie clip the manifest already lists:

    python3 sim/tools/prerender_audio.py --engine gateway --env-file mqtt/.env \
        --model tts-piper-kristin --rerender moxie --rerender ambient --max-calls 150

Produces:
    <out>/moxie/<sha1>.mp3      Moxie's lines (mono 64k MP3 — the engine renders WAV,
    <out>/child/<sha1>.mp3      ffmpeg transcodes; the WAV is a temp file, never kept)
    <out>/ambient/<sha1>.mp3    the ambient self-talk lines (--ambient)
    <out>/index.json            { "moxie": {text: file}, "child": …, "ambient": … }

Each MP3 names the voice that rendered it in an ID3 `TXXX:moxie_voice` frame
(`gateway:tts-piper-kristin`, `piper:en_US-amy-medium`). That is what lets --rerender
skip a clip already in the target voice, so a run that stops half way resumes when run
again, and what lets `sim/tests/test_prerender_gateway.py` prove the page speaks in one
voice. The manifest keeps its `{group: {text: file}}` shape: it has no natural slot for
a marker, and every reader of it expects nothing but groups.

The key in index.json is the EXACT utterance string, so the punctuation here and the
punctuation in stub.js / ambient.json / filler.py must match character for character —
`sim/test_fallback_coverage.mjs` is the guard that says so out loud.

The web app looks a line up in index.json and plays the file; if it's missing it
falls back to the live TTS service, then to silent text.
"""
import argparse, array, hashlib, json, os, re, shutil, subprocess, sys, glob
import tempfile, time, urllib.error, urllib.request, wave

HERE = os.path.dirname(os.path.abspath(__file__))
VOICES = os.path.join(HERE, "..", "tts", "voices")
sys.path.insert(0, os.path.join(HERE, "..", "..", "mqtt"))
# One rule with the SDK (and its port in functions/api): the `voice` field derived from
# the model name, and audio sniffed from its bytes — LiteLLM labels WAV `audio/mpeg`.
from moxie_sdk.tts import VoiceServerError, pcm_from_audio, voice_for_model  # noqa: E402

#: The voice the shipped clips are in: the one the owner picked out of the gateway's
#: voices, and the one the live demo answers in. A public model alias, not a host.
SHIPPED_MODEL = "tts-piper-kristin"
#: The ID3 TXXX description a clip's voice is recorded under.
VOICE_TAG = "moxie_voice"
#: Statuses worth another try: the gateway is busy or briefly down, not refusing.
RETRYABLE = (408, 429, 500, 502, 503, 504)
#: All that --env-file reads; nothing else in the file can steer this tool.
GATEWAY_ENV = ("MOXIE_VOICE_BASE_URL", "MOXIE_LLM_BASE_URL", "MOXIE_VOICE_API_KEY",
               "MOXIE_LLM_API_KEY", "MOXIE_VOICE_MODEL", "MOXIE_TTS_VOICE",
               "MOXIE_VOICE_FORMAT", "MOXIE_VOICE_SAMPLE_RATE")


def find_python():
    for p in ("/tmp/piper-venv/bin/python", sys.executable):
        try:
            subprocess.run([p, "-c", "import piper"], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return p
        except Exception:
            continue
    return None


def find_voice(prefer):
    hits = sorted(glob.glob(os.path.join(VOICES, "*.onnx")))
    for p in prefer:
        for h in hits:
            if p in os.path.basename(h).lower():
                return h
    return hits[0] if hits else None


class PiperEngine:
    """Local Piper: offline, one voice file per speaker from sim/tts/voices."""

    def __init__(self, py, voices):
        self.py, self.voices = py, voices

    def _voice(self, who):
        return self.voices.get(who) or self.voices["moxie"]

    def marker(self, who):
        return "piper:" + os.path.splitext(os.path.basename(self._voice(who)))[0]

    def describe(self):
        return "piper " + os.path.basename(self.voices["moxie"])

    def render_wav(self, text, who, wav):
        subprocess.run([self.py, "-m", "piper", "-m", self._voice(who), "-f", wav],
                       input=text.encode("utf-8"), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class GatewayError(RuntimeError):
    """The voice server did not give us speech. The message has been through redact()."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A 3xx is an error, never followed: urllib would re-send the Authorization header to
    wherever it points — a login page in front of the gateway, say."""

    def redirect_request(self, *args, **kwargs):
        return None


class GatewayEngine:
    """Any OpenAI-compatible `POST {base}/audio/speech`, standard library only.

    On our gateway the MODEL is the voice; `voice` is required but ignored, so it
    defaults to the SDK's derivation (`tts-piper-kristin` -> `kristin`). A `wav` reply
    carries its own rate; `pcm` is raw 16-bit mono at `sample_rate`. The key travels in
    the Authorization header only, and every message this engine writes goes through
    redact(). It speaks for Moxie only: see marker().
    """

    def __init__(self, base_url, api_key, model, voice="", fmt="wav", sample_rate=22050,
                 timeout=60.0, retries=2, max_calls=None):
        self.url = base_url.rstrip("/") + "/audio/speech"
        self.api_key, self.model, self.fmt = api_key, model, fmt
        self.voice = voice or voice_for_model(model)
        self.sample_rate, self.timeout = int(sample_rate), timeout
        self.retries, self.max_calls, self.calls = max(0, retries), max_calls, 0
        self._open = urllib.request.build_opener(_NoRedirect).open

    def marker(self, who):
        # The child is another person: the demo's two speakers in one voice is exactly
        # what test_fallback_coverage.mjs §2 refuses. Child clips come from --engine piper.
        return None if who == "child" else "gateway:" + self.model

    def describe(self):
        return f"gateway model {self.model!r} (voice field {self.voice!r}, {self.fmt})"

    def redact(self, text):
        return text.replace(self.api_key, "<key>") if self.api_key else text

    def render_wav(self, text, who, wav):
        body = {"model": self.model, "voice": self.voice, "input": text,
                "response_format": self.fmt}
        pcm, rate, channels = self._decode(self._post(json.dumps(body).encode("utf-8")))
        with wave.open(wav, "wb") as w:
            w.setnchannels(channels)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm)

    def _post(self, data):
        headers = {"Content-Type": "application/json", "User-Agent": "moxie-prerender-audio"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        for attempt in range(self.retries + 1):
            if self.max_calls is not None and self.calls >= self.max_calls:
                raise GatewayError(f"stopped at --max-calls {self.max_calls}")
            self.calls += 1
            req = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
            try:
                with self._open(req, timeout=self.timeout) as resp:
                    return resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code not in RETRYABLE or attempt == self.retries:
                    raise GatewayError(self.redact(
                        f"/audio/speech answered HTTP {exc.code}: {_error_summary(exc)}")) from None
                wait = _retry_after(exc, attempt)
            except OSError as exc:                 # refused, reset, DNS, timed out
                if attempt == self.retries:
                    raise GatewayError(self.redact(f"/audio/speech unreachable: {exc}")) from None
                wait = 2.0 ** attempt
            print(f"    retrying in {wait:g}s (try {attempt + 2} of {self.retries + 1})")
            time.sleep(wait)

    def _decode(self, raw):
        # Markers, not a leading "<": raw pcm may start with that byte.
        if raw[:512].lstrip().lower().startswith((b"<!doctype", b"<html")):
            raise GatewayError("the voice server sent HTML, not audio — a login page in front of it?")
        try:
            pcm, rate, channels = pcm_from_audio(raw, sample_rate=self.sample_rate)
        except VoiceServerError as exc:            # empty, a JSON error, a broken WAV
            raise GatewayError(self.redact(str(exc))) from None
        if self.fmt == "wav" and raw[:4] != b"RIFF":
            raise GatewayError(f"asked for wav, got {len(raw)} bytes with no RIFF header")
        if not _audible(pcm):
            raise GatewayError("the voice server sent silence, not speech")
        return pcm, rate, channels


def _error_summary(exc):
    """One line out of an HTTP error body: a JSON error's message, else the text."""
    try:
        raw = exc.read(2000)
    except Exception:
        return str(exc.reason)
    try:
        body = json.loads(raw)
        err = body.get("error", body) if isinstance(body, dict) else body
        detail = err.get("message", err) if isinstance(err, dict) else err
    except ValueError:
        detail = raw.decode("utf-8", "replace")
    return " ".join(str(detail).split())[:300]


def _retry_after(exc, attempt):
    """The server's Retry-After in seconds (capped at 30), else 1, 2, 4…"""
    try:
        return max(0.0, min(float(exc.headers.get("Retry-After")), 30.0))
    except (TypeError, ValueError):
        return 2.0 ** attempt


def _audible(pcm, floor=500):
    """A 16-bit peak above `floor` (about -36 dBFS): speech, not an empty or silent reply
    that would pass every size check downstream."""
    samples = array.array("h", pcm[:len(pcm) - len(pcm) % 2])
    if sys.byteorder == "big":
        samples.byteswap()
    return bool(samples) and max(max(samples), -min(samples)) >= floor


def read_env_file(path, keys):
    """`KEY=VALUE` lines from a dotenv, for `keys` only. As in mqtt/config.py, a quoted value
    is verbatim and otherwise a whitespace-led `#` starts a comment. Never printed."""
    out = {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("export "):
                line = line[len("export "):].lstrip()
            key, eq, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not eq or key not in keys:
                continue
            if value[:1] in ("'", '"'):
                end = value.find(value[0], 1)
                value = value[1:end] if end != -1 else value[1:]
            elif value.startswith("#"):
                value = ""
            else:
                value = re.split(r"\s#", value, maxsplit=1)[0].strip()
            out[key] = value
    return out


def gateway_engine(args):
    """--engine gateway, configured by flags, then the environment, then --env-file."""
    keys = GATEWAY_ENV + ((args.api_key_env,) if args.api_key_env else ())
    env = read_env_file(args.env_file, keys) if args.env_file else {}
    env.update({k: os.environ[k] for k in keys if os.environ.get(k)})

    def pick(*names):
        return next((env[n] for n in names if env.get(n)), "")

    base = args.base_url or pick("MOXIE_VOICE_BASE_URL", "MOXIE_LLM_BASE_URL")
    if not base:
        sys.exit("--engine gateway needs a base URL: --base-url, or MOXIE_VOICE_BASE_URL / "
                 "MOXIE_LLM_BASE_URL in the environment or --env-file")
    names = (args.api_key_env,) if args.api_key_env else ("MOXIE_VOICE_API_KEY", "MOXIE_LLM_API_KEY")
    key = pick(*names)
    if not key:
        print(f"  (no key in {' / '.join(names)}: sending no Authorization header)")
    fmt = (args.format or pick("MOXIE_VOICE_FORMAT") or "wav").lower()
    if fmt not in ("wav", "pcm"):
        sys.exit(f"--format must be wav or pcm, not {fmt!r}")
    return GatewayEngine(base, key, args.model or pick("MOXIE_VOICE_MODEL") or SHIPPED_MODEL,
                         voice=args.voice or pick("MOXIE_TTS_VOICE"), fmt=fmt,
                         sample_rate=args.sample_rate or pick("MOXIE_VOICE_SAMPLE_RATE") or 22050,
                         timeout=args.timeout, retries=args.retries, max_calls=args.max_calls)


def transcode(wav, dest, tags):
    """WAV -> web-friendly MP3 (mono 64k; keeps the repo small). `tags` become ID3 frames."""
    meta = [arg for k, v in tags.items() for arg in ("-metadata", f"{k}={v}")]
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav, "-ac", "1",
                    "-c:a", "libmp3lame", "-b:a", "64k", *meta, "-f", "mp3", dest],
                   check=True)


def render(engine, who, text, dest):
    """One clip: engine -> temp WAV -> tagged MP3, written under a temporary name and then
    renamed, so a run that dies half way never leaves half a clip behind a manifest key."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    fd, wav = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    part = dest + ".part"
    try:
        engine.render_wav(text, who, wav)
        transcode(wav, part, {VOICE_TAG: engine.marker(who)})
        os.replace(part, dest)
    finally:
        for p in (wav, part):
            try: os.unlink(p)
            except OSError: pass


def _syncsafe(b):
    return (b[0] & 0x7F) << 21 | (b[1] & 0x7F) << 14 | (b[2] & 0x7F) << 7 | (b[3] & 0x7F)


def voice_tag(path):
    """The voice a clip was rendered in (its ID3 `TXXX:moxie_voice`, as ffmpeg writes it
    from -metadata), or "" for an untagged clip — anything rendered before tags existed."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(10)
            if len(head) < 10 or head[:3] != b"ID3":
                return ""
            body = fh.read(_syncsafe(head[6:10]))
    except OSError:
        return ""
    i, v4 = 0, head[3] >= 4
    while i + 10 <= len(body) and body[i:i + 4].isalnum():    # padding ends the frames
        size = _syncsafe(body[i + 4:i + 8]) if v4 else int.from_bytes(body[i + 4:i + 8], "big")
        frame, data = body[i:i + 4], body[i + 10:i + 10 + size]
        i += 10 + size
        if frame == b"TXXX" and data:
            codec = {1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(data[0], "latin-1")
            text = data[1:].decode(codec, "replace").replace("\ufeff", "")
            desc, _, value = text.partition("\x00")
            if desc == VOICE_TAG:
                return value.rstrip("\x00")
    return ""


def lines_from_session(path):
    """Pull (speaker, text) pairs out of a recorded session or a scenario file."""
    with open(path) as fh:
        data = json.load(fh)
    out = []
    events = data if isinstance(data, list) else data.get("turns", [])
    for ev in events:
        # recorded session event
        payload = ev.get("payload")
        if payload:
            try:
                msg = json.loads(payload)
            except Exception:
                continue
            topic = ev.get("topic", "")
            if topic.endswith("/commands/remote_chat"):
                t = ((msg.get("output") or {}).get("text") or "").strip()
                if t:
                    out.append(("moxie", t))
            elif topic.endswith("/events/remote-chat") and msg.get("command") != "notify":
                t = (msg.get("speech") or "").strip()
                if t:
                    out.append(("child", t))
        # scenario turn
        elif ev.get("say"):
            out.append(("child", ev["say"].strip()))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Pre-render session audio with local Piper or an OpenAI-compatible voice gateway.")
    ap.add_argument("session", nargs="*", help="session/scenario JSON file(s)")
    ap.add_argument("--phrases", help="text file of fixed Moxie phrases (one per line) to pre-render")
    ap.add_argument("--ambient", help="ambient.json ({lines:[{text,...}]}) — self-talk, pre-rendered under the 'ambient' group")
    ap.add_argument("--out", default="sim/web/audio")
    ap.add_argument("--rerender", action="append", default=[], metavar="GROUP",
                    help="render every line already in this manifest group again, skipping clips "
                         "already in this voice — so running a stopped run again resumes it "
                         "(repeatable: --rerender moxie --rerender ambient)")
    ap.add_argument("--engine", default="piper", choices=("piper", "local", "gateway", "openai"),
                    help="piper|local: offline Piper (default). gateway|openai: an "
                         "OpenAI-compatible /audio/speech endpoint")
    pp = ap.add_argument_group("--engine piper")
    pp.add_argument("--moxie-voice", default="amy", help="voice substring for Moxie")
    pp.add_argument("--child-voice", default="libritts", help="voice substring for the child")
    gw = ap.add_argument_group("--engine gateway (a flag beats the environment, which beats --env-file)")
    gw.add_argument("--base-url", help="the …/v1 base; else MOXIE_VOICE_BASE_URL, then MOXIE_LLM_BASE_URL")
    gw.add_argument("--model", help=f"the voice; else MOXIE_VOICE_MODEL, then {SHIPPED_MODEL} (the shipped clips')")
    gw.add_argument("--voice", help="the `voice` field; else MOXIE_TTS_VOICE, then derived from the model")
    gw.add_argument("--format", help="wav (default: carries its own rate) or pcm; else MOXIE_VOICE_FORMAT")
    gw.add_argument("--sample-rate", type=int, help="pcm only; else MOXIE_VOICE_SAMPLE_RATE, then 22050")
    gw.add_argument("--env-file", help="a dotenv (e.g. mqtt/.env) to read the MOXIE_* settings above from")
    gw.add_argument("--api-key-env", metavar="NAME",
                    help="read the key from this variable, not MOXIE_VOICE_API_KEY / MOXIE_LLM_API_KEY "
                         "(a key is never a flag: flags end up in shell history and ps)")
    gw.add_argument("--max-calls", type=int, help="spend at most N calls, retries included")
    gw.add_argument("--retries", type=int, default=2, help="extra tries on 429/5xx/timeouts (default 2)")
    gw.add_argument("--timeout", type=float, default=60.0, help="seconds per call (default 60)")
    args = ap.parse_args(argv)

    if args.engine in ("gateway", "openai"):
        engine = gateway_engine(args)
    else:
        py = find_python()
        if not py:
            sys.exit("no python with piper installed (pip install piper-tts)")
        voices = {"moxie": find_voice([args.moxie_voice]),
                  "child": find_voice([args.child_voice, args.moxie_voice])}
        if not voices["moxie"]:
            sys.exit(f"no piper voice found in {VOICES}")
        engine = PiperEngine(py, voices)
    print(f"voice: {engine.describe()}")

    # Merge into any existing manifest so fixed phrases + scenario clips coexist.
    #
    # EVERY group is carried over, not just the two a given run might write. This used to
    # copy `moxie` and `child` by name only, so a run with `--phrases` alone REWROTE the
    # manifest without an `ambient` key at all: 56 committed MP3s orphaned on disk, the
    # whole ambient self-talk layer silently muted, and not one error printed. Nothing
    # caught it because the clips were still there — only the strings that find them were
    # gone. `sim/test_fallback_coverage.mjs` now fails on exactly that shape.
    idx_path = os.path.join(args.out, "index.json")
    index = {"moxie": {}, "child": {}}
    if os.path.exists(idx_path):
        try:
            cur = json.load(open(idx_path))
            for group, entries in (cur or {}).items():
                if isinstance(entries, dict):
                    index.setdefault(group, {}).update(entries)
        except Exception:
            pass
    total = 0

    # Every line this run answers for, as (group, text, clip path, label), in input order.
    jobs = []

    def want(group, text, label, rel=None):
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
        jobs.append((group, text, rel or f"{group}/{digest}.mp3", label))

    # fixed Moxie phrases (the UI's guaranteed-working, tap-to-play lines)
    if args.phrases:
        with open(args.phrases) as fh:
            for ln in fh:
                if ln.strip() and not ln.startswith("#"):
                    want("moxie", ln.strip(), "moxie (fixed)")
    # ambient self-talk lines -> index["ambient"], Moxie's voice
    if args.ambient:
        index.setdefault("ambient", {})
        for entry in json.load(open(args.ambient)).get("lines", []):
            text = (entry.get("text") or "").strip()
            if text:
                want("ambient", text, "ambient")
    for path in args.session:
        for who, text in lines_from_session(path):
            want(who, text, who)
    # --rerender: every line the manifest already keys in the group, under its own file
    for group in args.rerender:
        if group not in index:
            sys.exit(f"--rerender {group}: {idx_path} has no such group ({', '.join(index)})")
        for text, rel in index[group].items():
            want(group, text, f"{group} (re-render)", rel)

    # What needs a render: a missing clip, or (--rerender) a clip in some other voice.
    # `done` maps (voice, text) to a clip already in that voice, so a line two groups share
    # (moxie + ambient share 8) costs one render, also when a stopped run is resumed.
    todo, seen, done = [], set(), {}
    for group, text, rel, label in jobs:
        if (group, text) in seen:
            continue
        seen.add((group, text))
        index.setdefault(group, {})[text] = rel
        dest = os.path.join(args.out, rel)
        if os.path.exists(dest):
            if group not in args.rerender:
                continue
            if voice_tag(dest) == engine.marker(group):
                done[(engine.marker(group), text)] = dest
                continue
        todo.append((group, text, dest, label))

    refused = sorted({g for g, _, _, _ in todo if engine.marker(g) is None})
    if refused:
        sys.exit(f"--engine gateway speaks for Moxie only; {'/'.join(refused)} line(s) are "
                 f"another speaker — render them with --engine piper. Nothing was spent.")
    renders = len({(engine.marker(g), t) for g, t, _, _ in todo} - done.keys())
    if args.max_calls is not None and renders > args.max_calls:
        sys.exit(f"{renders} clip(s) to render but --max-calls is {args.max_calls}. Nothing was spent.")

    try:
        for group, text, dest, label in todo:
            key = (engine.marker(group), text)
            if key in done:
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.copyfile(done[key], dest + ".part")
                os.replace(dest + ".part", dest)
            else:
                render(engine, group, text, dest)
                done[key] = dest
            total += 1
            print(f"  rendered {label}: {text[:48]!r}")
    except GatewayError as exc:
        sys.exit(f"✗ {exc}\n  {total} clip(s) were rendered first; the manifest was not rewritten. "
                 f"Run the same command again to resume: clips already in this voice are skipped.")

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "index.json"), "w") as fh:
        json.dump(index, fh, indent=1)
    spent = f" in {engine.calls} gateway call(s)" if isinstance(engine, GatewayEngine) else ""
    print(f"✅ {total} new clip(s){spent}; manifest: {os.path.join(args.out, 'index.json')} "
          f"({len(index['moxie'])} moxie / {len(index['child'])} child lines)")


if __name__ == "__main__":
    main()
