#!/usr/bin/env python3
"""
🔊 Pre-render session audio for a STATIC deploy (e.g. Cloudflare Pages).

A scripted session's lines are known in advance, so we can render them with Piper
at build time and ship plain audio files — no TTS service, no STT, no LLM at
runtime. Renders BOTH sides: Moxie's replies and the child's turns.

    python3 sim/tools/prerender_audio.py sim/web/sessions/demo.json --out sim/web/audio

Produces:
    <out>/moxie/<sha1>.wav      Moxie's lines
    <out>/child/<sha1>.wav      the child's lines
    <out>/index.json            { "moxie": {text: file}, "child": {text: file} }

The web app looks a line up in index.json and plays the file; if it's missing it
falls back to the live TTS service, then to silent text.
"""
import argparse, hashlib, json, os, subprocess, sys, glob

HERE = os.path.dirname(os.path.abspath(__file__))
VOICES = os.path.join(HERE, "..", "tts", "voices")


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


def render_mp3(py, voice, text, dest):
    """Piper -> WAV -> web-friendly MP3 (mono 64k). Keeps the repo small."""
    import tempfile
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        wav = tf.name
    try:
        subprocess.run([py, "-m", "piper", "-m", voice, "-f", wav],
                       input=text.encode("utf-8"), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav,
                        "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k", dest],
                       check=True)
    finally:
        try: os.unlink(wav)
        except OSError: pass


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


def main():
    ap = argparse.ArgumentParser(description="Pre-render session audio with Piper.")
    ap.add_argument("session", nargs="*", help="session/scenario JSON file(s)")
    ap.add_argument("--phrases", help="text file of fixed Moxie phrases (one per line) to pre-render")
    ap.add_argument("--ambient", help="ambient.json ({lines:[{text,...}]}) — self-talk, pre-rendered under the 'ambient' group")
    ap.add_argument("--out", default="sim/web/audio")
    ap.add_argument("--moxie-voice", default="amy", help="voice substring for Moxie")
    ap.add_argument("--child-voice", default="libritts", help="voice substring for the child")
    args = ap.parse_args()

    py = find_python()
    if not py:
        sys.exit("no python with piper installed (pip install piper-tts)")
    voices = {"moxie": find_voice([args.moxie_voice]),
              "child": find_voice([args.child_voice, args.moxie_voice])}
    if not voices["moxie"]:
        sys.exit(f"no piper voice found in {VOICES}")

    # merge into any existing manifest so fixed phrases + scenario clips coexist
    idx_path = os.path.join(args.out, "index.json")
    index = {"moxie": {}, "child": {}}
    if os.path.exists(idx_path):
        try:
            cur = json.load(open(idx_path))
            index["moxie"].update(cur.get("moxie", {}))
            index["child"].update(cur.get("child", {}))
        except Exception:
            pass
    total = 0

    # fixed Moxie phrases (the UI's guaranteed-working, tap-to-play lines)
    fixed = []
    if args.phrases:
        with open(args.phrases) as fh:
            fixed = [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    for text in fixed:
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
        rel = f"moxie/{digest}.mp3"
        dest = os.path.join(args.out, rel)
        index["moxie"][text] = rel
        if os.path.exists(dest):
            continue
        render_mp3(py, voices["moxie"], text, dest)
        total += 1
        print(f"  rendered moxie (fixed): {text[:48]!r}")

    # ambient self-talk lines -> index["ambient"], Moxie voice, audio/ambient/*.wav
    if args.ambient:
        index.setdefault("ambient", {})
        adata = json.load(open(args.ambient))
        for entry in adata.get("lines", []):
            text = (entry.get("text") or "").strip()
            if not text:
                continue
            digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
            rel = f"ambient/{digest}.mp3"
            dest = os.path.join(args.out, rel)
            index["ambient"][text] = rel
            if os.path.exists(dest):
                continue
            render_mp3(py, voices["moxie"], text, dest)
            total += 1
            print(f"  rendered ambient: {text[:48]!r}")
    for path in args.session:
        for who, text in lines_from_session(path):
            digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
            rel = f"{who}/{digest}.mp3"
            dest = os.path.join(args.out, rel)
            index[who][text] = rel
            if os.path.exists(dest):
                continue
            render_mp3(py, voices[who] or voices["moxie"], text, dest)
            total += 1
            print(f"  rendered {who}: {text[:48]!r}")

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "index.json"), "w") as fh:
        json.dump(index, fh, indent=1)
    print(f"✅ {total} new clip(s); manifest: {os.path.join(args.out, 'index.json')} "
          f"({len(index['moxie'])} moxie / {len(index['child'])} child lines)")


if __name__ == "__main__":
    main()
