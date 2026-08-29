#!/usr/bin/env python3
"""
🔊 Moxie TTS service — Piper speech for the SIL (and a real revival server).

Mirrors the robot's real architecture: the SERVER renders speech to audio
(CloudTTSResponse -> PCM, see docs/reverse-engineering/perception-pipeline.md).
Here we expose it as plain HTTP so the web sim can play it:

    GET /tts?text=Hello%20there            -> audio/wav (Piper synthesis)
    GET /health                            -> {"ok":true,"voice":...}

Usage:  python3 sim/tts/server.py [port]
Env:    MOXIE_PIPER_PY    python with `piper` installed (default: auto-detect)
        MOXIE_PIPER_VOICE path to a .onnx voice   (default: sim/tts/voices/*.onnx)

Voices: https://github.com/rhasspy/piper (download a .onnx + .onnx.json into
sim/tts/voices/). Offline, no cloud, no API key.
"""
import http.server, socketserver, os, sys, subprocess, tempfile, json, glob
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8081

def find_python():
    cand = os.environ.get("MOXIE_PIPER_PY")
    if cand and os.path.exists(cand):
        return cand
    for p in ("/tmp/piper-venv/bin/python", sys.executable):
        try:
            subprocess.run([p, "-c", "import piper"], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return p
        except Exception:
            continue
    return None

def find_voice():
    v = os.environ.get("MOXIE_PIPER_VOICE")
    if v and os.path.exists(v):
        return v
    hits = sorted(glob.glob(os.path.join(HERE, "voices", "*.onnx")))
    return hits[0] if hits else None

PY_BIN, VOICE = find_python(), find_voice()

def synth(text: str) -> bytes:
    """Render text -> WAV bytes with Piper."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
        out = fh.name
    try:
        subprocess.run([PY_BIN, "-m", "piper", "-m", VOICE, "-f", out],
                       input=text.encode("utf-8"), check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=60)
        with open(out, "rb") as fh:
            return fh.read()
    finally:
        try: os.unlink(out)
        except OSError: pass

class H(http.server.SimpleHTTPRequestHandler):
    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")   # the web sim is on another port
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/health":
            ok = bool(PY_BIN and VOICE)
            return self._send(200 if ok else 503, json.dumps(
                {"ok": ok, "voice": os.path.basename(VOICE) if VOICE else None,
                 "python": PY_BIN}).encode(), "application/json")
        if u.path == "/tts":
            text = (parse_qs(u.query).get("text") or [""])[0].strip()
            if not text:
                return self._send(400, b'{"error":"missing text"}', "application/json")
            if not (PY_BIN and VOICE):
                return self._send(503, b'{"error":"piper or voice not available"}', "application/json")
            try:
                return self._send(200, synth(text[:1000]), "audio/wav")
            except subprocess.CalledProcessError as e:
                return self._send(500, json.dumps(
                    {"error": "synthesis failed", "detail": e.stderr.decode()[-300:]}).encode(),
                    "application/json")
        return self._send(404, b'{"error":"not found"}', "application/json")

    def log_message(self, *a): pass

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True; daemon_threads = True

if __name__ == "__main__":
    print(f"[tts] piper={PY_BIN} voice={os.path.basename(VOICE) if VOICE else None}", flush=True)
    print(f"[tts] http://127.0.0.1:{PORT}/tts?text=hello", flush=True)
    Server(("127.0.0.1", PORT), H).serve_forever()
