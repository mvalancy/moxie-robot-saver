#!/usr/bin/env python3
"""
🎙️ Moxie STT service — speech-to-text for the SIL (and a real revival server).

Returns the **Deepgram-compatible shape the robot already parses**
(`DeepgramResponse`, see docs/reverse-engineering/perception-pipeline.md
#stt-response-wire-format-deepgramresponse), so the same service can serve the web
sim's mic *and* a real robot's audio path:

    POST /stt   (body = audio bytes: wav/webm/ogg)   -> DeepgramResponse JSON
    GET  /health                                     -> {"ok":true,"model":...}

Response:
    {"duration":..,"start":0,"is_final":true,"speech_final":true,
     "channel":{"alternatives":[{"transcript":"...","confidence":0.9,
                                 "words":[{"word":"hi","start":0.0,"end":0.2,
                                           "confidence":0.9}]}]}}

Usage:  python3 sim/stt/server.py [port]
Env:    MOXIE_STT_PY     python with faster-whisper (default: auto-detect)
        MOXIE_STT_MODEL  whisper size: tiny|base|small|medium (default: base.en)

Offline after the first model download; no cloud, no API key. Alternatives that
speak the same shape: Vosk, whisper.cpp, or a real Deepgram endpoint.
"""
import http.server, socketserver, os, sys, json, subprocess, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8082
MODEL_NAME = os.environ.get("MOXIE_STT_MODEL", "base.en")

_model = None


def get_model():
    """Lazy-load the whisper model (first call downloads it, then it's cached)."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8")
    return _model


def transcribe(audio_bytes: bytes) -> dict:
    """Audio bytes -> DeepgramResponse-shaped dict."""
    suffix = ".webm" if audio_bytes[:4] == b"\x1a\x45\xdf\xa3" else ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
        fh.write(audio_bytes)
        path = fh.name
    try:
        segments, info = get_model().transcribe(path, language="en", word_timestamps=True,
                                                vad_filter=True)
        words, texts, confs = [], [], []
        for seg in segments:
            texts.append(seg.text)
            confs.append(getattr(seg, "avg_logprob", -0.5))
            for w in (seg.words or []):
                words.append({"word": w.word.strip(), "start": round(w.start, 3),
                              "end": round(w.end, 3),
                              "confidence": round(min(1.0, max(0.0, w.probability)), 3)})
        transcript = "".join(texts).strip()
        # avg_logprob (~-1..0) -> a rough 0..1 confidence
        conf = round(min(1.0, max(0.0, 1.0 + (sum(confs) / len(confs) if confs else -0.3))), 3)
        return {
            "duration": round(getattr(info, "duration", 0.0), 3),
            "start": 0.0,
            "is_final": True,        # this service returns finals only
            "speech_final": True,    # …and each POST is one complete utterance
            "channel": {"alternatives": [
                {"transcript": transcript, "confidence": conf, "words": words}]},
        }
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


class H(http.server.SimpleHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        if self.path.split("?")[0] == "/health":
            return self._send(200, json.dumps({"ok": True, "model": MODEL_NAME}).encode())
        return self._send(404, b'{"error":"not found"}')

    def do_POST(self):
        if self.path.split("?")[0] != "/stt":
            return self._send(404, b'{"error":"not found"}')
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return self._send(400, b'{"error":"empty body"}')
        audio = self.rfile.read(n)
        try:
            return self._send(200, json.dumps(transcribe(audio)).encode())
        except Exception as e:
            return self._send(500, json.dumps({"error": "transcription failed",
                                               "detail": str(e)[-300:]}).encode())

    def log_message(self, *a):
        pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    print(f"[stt] model={MODEL_NAME} (loads on first request)", flush=True)
    print(f"[stt] POST http://127.0.0.1:{PORT}/stt  ·  GET /health", flush=True)
    Server(("127.0.0.1", PORT), H).serve_forever()
