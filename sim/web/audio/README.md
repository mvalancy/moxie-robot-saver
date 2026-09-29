# 🔊 `sim/web/audio/` — pre-rendered voice clips

Piper-rendered MP3s (mono, 64 kbps) so a static deploy has sound with no TTS server. Built by
[`../../tools/prerender_audio.py`](../../tools/prerender_audio.py); played by [`../voice/local.js`](../voice/local.js),
which falls back to a live TTS service, then silent text, when a line has no clip.

- [`index.json`](index.json) — manifest `{ "moxie": {text: file}, "child": {…}, "ambient": {…} }`. Keys are the
  EXACT utterance string, so punctuation must match `stub.js` / `ambient.json` / `filler.py` character for
  character (`sim/test_fallback_coverage.mjs` guards it).
- [`moxie-phrases.txt`](moxie-phrases.txt) — fixed Moxie phrases to pre-render, one per line.
- [`moxie/`](moxie/README.md), [`child/`](child/README.md), [`ambient/`](ambient/README.md) — the clips, named by
  the first 16 hex chars of the SHA-1 of the line's text.

Regenerate:

```sh
python3 sim/tools/prerender_audio.py sim/web/sessions/demo.json \
  --phrases sim/web/audio/moxie-phrases.txt --ambient sim/web/ambient.json --out sim/web/audio
```

Served with `max-age=86400` (`/audio/*` in [`../_headers`](../_headers)). Not to be confused with
[`../voice/`](../voice/README.md), the player code.

📖 [sim/web](../README.md) · [Back to top](../../../README.md)
