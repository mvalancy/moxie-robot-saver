# 🧭 `sim/tools/journey/` — the visitor-journey instrument

What one visitor meets on the hosted Sim, measured where it happens: in a real Chrome on a
phone profile, with the network, the console, every status line, every sound started or cut,
and a **cue tracker** that samples at 20 Hz whether anything of Moxie working can be seen or
heard. Hand-run; not in CI. Every probe that can spend money goes through one spend guard.

## The pieces

- [`lib.mjs`](lib.mjs) — the visitor: an incognito context with the page-side timeline (bubble,
  transcript rows, `#chat-status` / `#mic-status`, mode, API calls, clip fetches), `instrumentWebAudio`
  from [`browser_harness.mjs`](../../browser_harness.mjs), and `installCueTracker` (50 ms samples of
  the status text, `moxieAlive.__state()`'s thinking stage and pose, `body[data-mic]`, Web Audio
  playing, the browser voice). `cueGaps` / `turnCues` turn the samples into the longest cue-free
  interval of a turn; `turnSummary` reads one turn's timings; `waitTurnDone` knows when her reply
  is over. The **spend guard** (`__guard`) ledgers every `/api/chat|speech|transcribe` fetch before
  it leaves and refuses past the per-run caps, or past 5 chat turns against the site's own
  origin across every run sharing the ledger.
- [`mockgw.mjs`](mockgw.mjs) — a zero-spend OpenAI-compatible gateway: scripted replies, a
  syllable-like WAV per sentence, a transcript, and `GET /__ctl?chat=ok|500|hang|429|html|empty&speech=…&stt=…&chatDelay=ms…`
  to drive every failure mode.
- [`up.sh`](up.sh) — in a **copy** of the checkout: `up.sh mock PORT MOCKPORT` writes a `.dev.vars`
  for the mock and starts `wrangler pages dev` on PORT; `up.sh real PORT` keeps the `.dev.vars` you
  wrote (a real gateway) and adds the allowed origin. Either way the page is reached as
  `http://moxie.hosted.test:PORT` (the probes map that name to 127.0.0.1).
- [`states.mjs`](states.mjs) — the limit and failure states, on local copies: `minute`, `hour`,
  `kill`, `budget`, `outage`, `voice`, `rapid`, `back`, and `gap`: the cue tracker over N typed
  turns and N mic turns with the mock at chat ~2.0 s / speech ~2.5 s.
- [`turns.mjs`](turns.mjs) — a child's conversation end to end: a typed turn, a mic turn, goodbye
  and the 50 s after it, "I'm back", a reload, "do you remember me?". Spends; capped.
- [`load.mjs`](load.mjs) — the free first visit: the hub, hub → `/sim`, 30 s idle, the first tap,
  30 s after it, on `phone-slow`, `phone` and `desktop` profiles.
- [`a11y.mjs`](a11y.mjs) — keyboard-only use, the accessibility tree, reduced motion.

## Running one

```sh
# a zero-spend copy on 19180 with the mock on 19181 (from the copy's repo root)
sim/tools/journey/up.sh mock 19180 19181
JOURNEY_OUT=/tmp/moxie-journey/gap node sim/tools/journey/states.mjs \
  --base=http://moxie.hosted.test:19180 --scenario=gap --mock=19181 --turns=5 --mic=/path/to/child.wav --micturns=3
# a real gateway: write .dev.vars from .dev.vars.example first, then
sim/tools/journey/up.sh real 19184
node sim/tools/journey/turns.mjs --base=http://moxie.hosted.test:19184 --mic=/path/to/child.wav --tag=local
```

`--mic=WAV` is a 16-bit PCM WAV Chrome plays as the microphone (`--use-file-for-fake-audio-capture`);
render one from a shipped child clip, e.g. `ffmpeg -i sim/web/audio/child/<file>.mp3 -ac 1 -ar 16000 child.wav`.

Environment: `JOURNEY_OUT` (dumps, screenshots and results; default `<tmpdir>/moxie-journey/out`),
`JOURNEY_LEDGER` (the spend ledger; default `<tmpdir>/moxie-journey/ledger.jsonl` — point it at a
durable file when the production cap must hold across reboots), `JOURNEY_TAG` (names `load.mjs`'s runs).

## Reading the cue report

Each turn's `cue` is `{longest_ms, total_ms, runs, until_is, first_cue_ms}`: the samples between the
send (a typed turn: the page's own click on Ask, `sendTime`, since the probe's clock reading precedes
its tap's round trips) or the recorder's auto-stop (a mic turn) and her first word. A sample is **cued**
when `#chat-status` has text, `moxieAlive.__state().stage > 0` or `.pose` is set, `body[data-mic]` is
on, a Web Audio buffer is playing, or the browser voice is speaking. `longest_ms` is the widest gap
between two cued samples (50 ms resolution). The gate W4-S1 set: at most 300 ms on every turn.

Each turn's `cuts` (`audioCuts`) lists every Web Audio play that was stopped before it ran out, with
`filler: true` for one of her thinking fillers (named by the clip's byte length) and `by_voice: true`
when her gateway voice started within 100 ms of the stop. A filler cut by her own voice within 1 s of
its start is the one-voice defect the filler's timing exists to prevent (`fillersCutByVoice`).

Numbers from these probes name the copy they ran on, the N and the raw dump; a run against the
site's own origin is a spend and is ledgered as one.

---
📖 [Tools](../README.md) · [Back to top](../../../README.md)
