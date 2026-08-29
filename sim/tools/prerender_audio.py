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
    ap.add_argument("session", nargs="+", help="session/scenario JSON file(s)")
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

    index = {"moxie": {}, "child": {}}
    total = 0
    for path in args.session:
        for who, text in lines_from_session(path):
            digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
            rel = f"{who}/{digest}.wav"
            dest = os.path.join(args.out, rel)
            index[who][text] = rel
            if os.path.exists(dest):
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            subprocess.run([py, "-m", "piper", "-m", voices[who] or voices["moxie"], "-f", dest],
                           input=text.encode("utf-8"), check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            total += 1
            print(f"  rendered {who}: {text[:48]!r}")

    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "index.json"), "w") as fh:
        json.dump(index, fh, indent=1)
    print(f"✅ {total} new clip(s); manifest: {os.path.join(args.out, 'index.json')} "
          f"({len(index['moxie'])} moxie / {len(index['child'])} child lines)")


if __name__ == "__main__":
    main()
