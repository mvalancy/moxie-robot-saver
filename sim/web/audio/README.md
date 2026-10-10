# 🔊 `sim/web/audio/` — pre-rendered voice clips

Pre-rendered MP3s (mono, 22050 Hz, 64 kbps) so a static deploy has sound with no TTS server. Built by
[`../../tools/prerender_audio.py`](../../tools/prerender_audio.py); played by [`../voice/local.js`](../voice/local.js),
which falls back to a live TTS service, then silent text, when a line has no clip.

- [`index.json`](index.json) — manifest `{ "moxie": {text: file}, "child": {…}, "ambient": {…} }`. Keys are the
  EXACT utterance string, so punctuation must match `stub.js` / `ambient.json` / `filler.py` character for
  character (`sim/test_fallback_coverage.mjs` guards it).
- [`moxie-phrases.txt`](moxie-phrases.txt) — fixed Moxie phrases to pre-render, one per line.
- [`moxie/`](moxie/README.md), [`child/`](child/README.md), [`ambient/`](ambient/README.md) — the clips, named by
  the first 16 hex chars of the SHA-1 of the line's text.

## One voice

Every Moxie clip (the `moxie` and `ambient` groups, 138 files) is **`tts-piper-kristin`**, rendered through the
voice gateway: the voice the live demo answers in, so a pre-rendered line and a live reply sound like the
same robot. The two `child` clips are a different Piper voice on purpose: the child is another speaker, and
a demo conversation in one voice is what `sim/test_fallback_coverage.mjs` §2 refuses.

Each MP3 records its voice in an ID3 `TXXX:moxie_voice` frame (`gateway:tts-piper-kristin`; a local render says
`piper:en_US-amy-medium`). The manifest keeps its plain `{group: {text: file}}` shape. The test
[`../../tests/test_prerender_gateway.py`](../../tests/test_prerender_gateway.py) fails if any Moxie clip is in
another voice, and names the clips.

## Regenerate

New lines go in `moxie-phrases.txt`, `ambient.json` or a session file; then render what is missing, in the
shipped voice. The tool does not read `stub.js` or `filler.py`, so pass a new line from one of those in a
phrases file (`--phrases`), exactly as written there. You need an OpenAI-compatible `/audio/speech` endpoint
and its key in the git-ignored `mqtt/.env`:

```sh
python3 sim/tools/prerender_audio.py --engine gateway --env-file mqtt/.env --model tts-piper-kristin \
  --phrases sim/web/audio/moxie-phrases.txt --ambient sim/web/ambient.json sim/web/sessions/demo.json \
  --out sim/web/audio
```

To change the voice, re-render every Moxie clip. The `--rerender` flag skips clips already in the target
voice, so a stopped run resumes when you run it again:

```sh
python3 sim/tools/prerender_audio.py --engine gateway --env-file mqtt/.env --model tts-piper-kristin \
  --rerender moxie --rerender ambient --max-calls 150 --out sim/web/audio
```

Offline, local Piper is still a first-class engine (`--engine piper`, the default; voices in
[`../../tts/voices/`](../../tts/voices/README.md)). Use it for every Moxie clip, not just new ones
(`--rerender moxie --rerender ambient`). Otherwise the page speaks in two voices. Child lines are rendered
only by `--engine piper`.

Size: 5.13 MB for 140 clips (the first 96 were 3.13 MB in this voice, 3.21 MB in Piper amy). Served with `max-age=86400` (`/audio/*` in
[`../_headers`](../_headers)). Not to be confused with [`../voice/`](../voice/README.md), the player code.

📖 [sim/web](../README.md) · [Back to top](../../../README.md)
