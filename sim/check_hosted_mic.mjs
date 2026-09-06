/* check_hosted_mic.mjs — put a REAL VOICE through the microphone on a REAL DEPLOYMENT,
 * and read back what the site heard, what it answered, and what it said out loud.
 *
 *   node sim/check_hosted_mic.mjs                    # the site's own canonical origin — SPENDS
 *   node sim/check_hosted_mic.mjs https://host/sim   # any deployment — SPENDS
 *   MOXIE_DEPLOYED_URL=https://host/sim node sim/check_hosted_mic.mjs
 *   node sim/check_hosted_mic.mjs --dry-run          # the real site, FREE: every spending
 *                                                    #   route aborted, clauses 1-3 + 6 still hold
 *   node sim/check_hosted_mic.mjs --selftest         # hermetic; SPENDS NOTHING, no network
 *
 *   MOXIE_MIC_BUDGET=5   the ceiling on gateway-backed requests, enforced at the browser
 *   MOXIE_MIC_WAV=…      a WAV to speak instead of the shipped clip (needs MOXIE_MIC_TEXT)
 *   MOXIE_MIC_TEXT=…     the words in that WAV, which is what the transcript is scored against
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THIS FILE IS THE DELIBERATE COMPLEMENT OF `sim/check_deployed.mjs`. That one reaches the
 * real deployment and **aborts `/api/chat`, `/api/speech` and `/api/transcribe` at the
 * browser** so it can promise it costs nothing. This one exists to spend — a handful of
 * real gateway calls, on purpose, on the owner's account — because there is exactly one
 * question left on this page that no free check can answer.
 *
 * `docs/architecture/implementation-plan.md` ranks it first and says why:
 *
 *     "A human voice through the hosted mic. The STT path is built, wired and tested, and
 *      no human has ever spoken into it on the hosted site — the single largest untested
 *      surface on the page a visitor actually uses."
 *
 * Three files already circle that gap and `sim/tests/test_live_hosted_ears.py`'s docstring
 * enumerates precisely what each one leaves out:
 *
 *   · `sim/test_demo_ears.mjs`   — the REAL route with `fetch` stubbed and no key. Every
 *                                  assertion would still pass if the gateway transcribed
 *                                  everything as "banana".
 *   · `sim/test_mic_spend.mjs`   — the REAL page in Chrome, but `/api/*` answered at the
 *                                  browser and the audio a 440 Hz tone. `moxieMic.setCapture`
 *                                  replaces the recorder, so `getUserMedia`, the permission
 *                                  prompt and `wavCapture`'s whole graph never run.
 *   · `test_live_hosted_ears.py` — real speech, real gateway, real route, but POSTed by
 *                                  `urllib`. No browser, no microphone, no page.
 *
 * What none of them touches, in that file's own words, is items 1-3 of its closing list:
 * *"no human has spoken into the hosted page — not once; `getUserMedia` and the browser
 * permission prompt on the deployed origin; `mic.js::encodeWav` on a REAL device's sample
 * rate rather than on the 22050 Hz this file hands it."* Those three are what this file
 * runs, and it runs them through the button a visitor actually presses.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHY IT IS NOT `sim/test_*.mjs`, WHICH IS THE FIRST THING TO SETTLE.
 *
 * In this repo that prefix is a promise. `sim/tests/test_ci_test_coverage.py` enumerates
 * `sim/test_*.mjs` and requires some tier to run each one, and
 * `test_ci_browser_suites_actually_run.py` requires that tier to be THE FAST ONE — every
 * push, every PR. A check that spends real money against an external deployment must never
 * be that, for three separate reasons and not one:
 *
 *   · it costs money, and a merge gate that costs money per push is a gate that gets
 *     switched off rather than read;
 *   · it depends on a finished Cloudflare Pages build and the public internet, so its red
 *     would frequently be about neither the code nor the site (`check_deployed.mjs`'s
 *     header carries the measurements: no GitHub Deployment is created for this repo, and
 *     a branch alias 404s before its first build and serves the PREVIOUS one after);
 *   · a fork PR gets no preview at all, so the gate would be red for every outside
 *     contributor.
 *
 * The alternative — a `KNOWN_UNRUN` exemption — is how a guard quietly stops guarding. So
 * this is a TOOL beside the suites, exactly like `check_deployed.mjs` and
 * `browser_harness.mjs`, and `--selftest` is the half that can honestly gate a PR.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE BUDGET, ENFORCED RATHER THAN PROMISED.
 *
 * One healthy run is **three** gateway-backed requests: one `POST /api/transcribe`, one
 * `POST /api/chat`, one `POST /api/speech`. The ceiling is `MOXIE_MIC_BUDGET` (default 5)
 * and it is a REQUEST INTERCEPTOR, not a comment: request number six on a spending route is
 * aborted at the browser and recorded, so a page that decided to loop cannot empty the
 * demo's budget while this file watches. `/api/health` is allowed through unmetered — its
 * own header promises it "makes no gateway call. EVER", and it is what tells `mic.js`
 * there are ears at all.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE MICROPHONE, AND HOW A HEADLESS BROWSER GETS ONE.
 *
 * Chromium can play a WAV file into `getUserMedia` as though it were a capture device:
 *
 *     --use-fake-device-for-media-stream
 *     --use-file-for-fake-audio-capture=<absolute path>.wav
 *     --use-fake-ui-for-media-stream            (auto-accept the permission prompt)
 *
 * plus `BrowserContext.overridePermissions(origin, ["microphone"])` over CDP, which is
 * belt and braces: the fake UI flag answers the prompt, the CDP grant means the prompt is
 * never asked. Both are cheap and they fail in different ways, so both are used.
 *
 * MEASURED ON THIS BOX, 2026-09-05, because two of the three were genuinely open questions:
 *
 *   · IT WORKS HEADLESS. `headless: "new"` + the flags above, `getUserMedia({audio:{…}})`
 *     resolved and a `ScriptProcessor` graph — `mic.js::wavCapture`'s own graph — received
 *     2.90 s of audio at peak 0.9626, RMS 0.1634. Not silence, not a stub.
 *   · CHROME RESAMPLES THE FILE, so the clip does NOT have to be at the capture rate. The
 *     22050 Hz golden was captured at `ctx.sampleRate` 48000 and the LOOP PERIOD of the
 *     captured envelope came back at 0.760 s against the file's true 0.750 s (100 Hz
 *     envelope, autocorrelation peak). A device that had ignored the header would have
 *     looped every 0.344 s and pitch-shifted the speech into nonsense. So the clip is
 *     written at 22050 Hz and the browser is left to do the conversion — which is also the
 *     honest test, because a real laptop hands `mic.js` 48 kHz and `encodeWav` decimates.
 *   · THE FILE LOOPS FOREVER while the stream is open, and nothing here can observe its
 *     PHASE at the moment capture starts. So the recording runs for **more than twice** the
 *     clip's length: whatever phase it starts on, one complete pass through the sentence is
 *     inside the window. That is why the upload is ~8.6 s rather than ~4 s, and it is worth
 *     the extra second of billable transcription.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE VOICE, AND AN HONEST LABEL ON IT.
 *
 * `sim/tests/goldens/real_voice_22050_mono.wav` is this repo's one committed voice fixture.
 * It is used here — `--selftest` captures it and proves the plumbing on the committed
 * artefact — but it CANNOT carry the word-overlap assertion, and that is a measurement
 * rather than an opinion. Byte-matched on 2026-09-05 against every clip in
 * `sim/web/audio/index.json`: the golden is samples **2205 … 18742** of
 * `moxie/3667ba11ce7655ed.mp3`, i.e. **0.100 s … 0.850 s of a 3.25 s clip** whose text is
 * *"Happy birthday! I hope your day is amazing."* Three quarters of a second from the front
 * of that sentence is one word and part of another, looped — an ASR fed it returns a
 * fragment, and "does the transcript resemble the clip" would be a coin toss dressed as an
 * assertion.
 *
 * So the spoken clip is the golden's own PARENT: the whole 3.25 s, eight words, text known
 * exactly because `sim/web/audio/index.json` is the manifest the site itself speaks from.
 * It is decoded **by the same Chrome this file already requires** (`decodeAudioData` in a
 * page served from `sim/web` on loopback) rather than by `ffmpeg` — `sim/tests/goldens/README.md`
 * records what shelling out to ffmpeg cost the last time (CI run 33985062379, a runner with
 * no ffmpeg, a red that a `shutil.which` skip would have turned into a silent pass). No new
 * dependency, and the fixture is a throwaway in `os.tmpdir()`; the golden is never touched.
 *
 * AND THE LABEL: this is **prerendered Piper speech, not a human being**. It is real
 * broadband voiced audio with formants and silences — which is what an ASR and this route
 * care about, and it is the same idiom `test_live_gateway_stt.py` and
 * `test_live_hosted_ears.py` already stand on — but nobody has held a microphone. What this
 * file closes is *a real voice through the real browser microphone path on the real
 * deployment*. What it does not close is *a child, in a room, with a laptop*. Point it at a
 * recording of one with `MOXIE_MIC_WAV` + `MOXIE_MIC_TEXT` and it closes that too.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * WHAT IT ASSERTS, AND WHY EACH CLAUSE IS SEPARATE.
 *
 *   1. THE MIC IS REACHABLE WITHOUT THE ENGINEERING RAIL. Phone viewport, real iOS UA, no
 *      scroll and no tap on CONTROLS: `#mic-btn` must be sized, inside the first viewport,
 *      and receive its own centre point under `elementFromPoint`, with `#rail-toggle` still
 *      `aria-expanded=false`. Same three-part shape as `check_deployed.mjs`, for the same
 *      recorded reasons (a 0×0 box, a box 2 000 px below the fold, and a box with something
 *      laid over it each pass the other two's test).
 *   2. AUDIO REALLY LEFT THE PAGE. `POST /api/transcribe` happened, its body is a 16 kHz
 *      mono RIFF/WAVE of a plausible duration — parsed with the SERVER's own
 *      `functions/api/_lib/wav.js`, so the client encoder and the server decoder are pinned
 *      by one assertion — and its peak amplitude is audible, not a silent buffer.
 *   3. IT IS THE AUDIO WE PLAYED. The capture is scored against the clip that was played
 *      AND against an unrelated one, and the played clip must WIN. Separate from 2 because
 *      "loud" and "the right recording" fail separately: a fake device that fed white
 *      noise, or a page that uploaded the wrong buffer, is loud. It is an ORDERING and not
 *      a magnitude, and that is the load-bearing detail — see the long note at `envelope`,
 *      and CI run 34013443378, where a saturating runner halved every score while getting
 *      the ordering right in all four cases. The magnitude (`FIDELITY_FLOOR`) is asserted
 *      only against a real deployment or a developer's box, and reported everywhere.
 *   4. THE TRANSCRIPT RESEMBLES THE WORDS. `wordOverlap(spoken, heard) >= STT_FLOOR`, and
 *      `wordOverlap(decoy, heard) < DECOY_CEIL`. The second half is what makes the first
 *      non-vacuous: an ASR that returns confident nonsense fails clause one; an ASR that
 *      returns a fixed plausible English sentence fails clause two.
 *   5. SHE ANSWERED, OUT LOUD. `/api/chat` returned a reply, `/api/speech` returned audio,
 *      and a Web Audio buffer with a real peak was actually SCHEDULED — the instrument
 *      `sim/test_typed_turn.mjs` established after PR #82's 770 assertions all passed while
 *      Web Audio was stubbed and the page was silent.
 *   6. ZERO `securitypolicyviolation` EVENTS AND ZERO CONSOLE ERRORS throughout.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE FIRST PAID RUN, 2026-09-05, AGAINST `https://moxie.mattvalancy.com/sim`.
 *
 * A voice went into the microphone on the live site for the first time. Recorded here in
 * full, because a number in a report nobody can re-read is a number nobody can check:
 *
 *     played     "Happy birthday! I hope your day is amazing."  (3.25 s, looped)
 *     device     secureContext=true  mediaDevices=true  opened=true
 *     #mic-btn   89×44 at y=763…807, hit-tested to itself, rail aria-expanded=false
 *     uploaded   311 340 B · 9 728 ms · 16 kHz mono PCM16 · peak 0.9581
 *     envelope   0.985 against the clip played, 0.329 against an unrelated one
 *     TRANSCRIPT "Happy birthday, I hope your day is amazing. Happy birthday, I hope your
 *                 day is amazing. Happy birthday, I hope your day is amazing."
 *                → word overlap 1.00, decoy 0.00
 *     reply      "Happy birthday! I hope you have lots of fun today."
 *     spend      1 STT + 1 chat + 1 TTS = 3 of a ceiling of 5
 *     CSP        0 violations, 0 console errors
 *
 * The commas for exclamation marks are the ASR's punctuation, which `normalizeWords` throws
 * away on purpose, and the sentence appears three times because the fake device loops — both
 * are exactly why the score is multiset RECALL rather than an equality test.
 *
 * THAT RUN ALSO REDDENED TWO CLAUSES, AND BOTH WERE THIS FILE'S FAULT RATHER THAN THE
 * SITE'S — they are written up in `probeTurn`'s wait block and in clause 5 above, because
 * they are the interesting part: the instrument waited on `plays.length > 0`, an AMBIENT
 * QUIP satisfied it while her answer was still being synthesised, and the `/api/speech`
 * body was recorded as 0 B because the response had not arrived. Both are fixed at the
 * root — the wait is now on every watched request having ANSWERED and on a buffer built
 * from gateway PCM (`bytes == null`, which no pre-rendered clip can be) — and both fixes
 * are exercised by `--selftest`, which reproduces the same turn hermetically.
 *
 * WHAT IS THEREFORE STILL UNMEASURED ON A REAL DEPLOYMENT, stated plainly rather than
 * quietly re-run: that her `/api/speech` audio reaches the speakers on the live site. The
 * chat half is measured (a real reply came back), the STT half is measured (overlap 1.00),
 * and the TTS request was made and allowed — but the run snapshotted before its answer
 * landed, and re-running would have cost three more gateway calls against a budget of five
 * that was already three spent. `sim/test_mic_spend.mjs` scenario 4 covers the same
 * assertion hermetically, and one dispatch of `deployed.yml` with `mic: spend` settles it.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * `--selftest`: THE TEETH, HERMETIC, AND WHAT EACH MUTATION IS FOR.
 *
 * A check nobody has watched fail is not a check. `--selftest` serves `sim/web` from
 * loopback under the real `_headers` policy with `/api/*` answered at the browser (the
 * `test_mic_spend.mjs` fixture shape), and runs the SAME `probeTurn()` three times against
 * three different fake microphones:
 *
 *     baseline   the sentence clip     → every clause must PASS
 *     mutation A digital silence       → clause 2 (audible) must go red
 *     mutation B a DIFFERENT clip      → clause 3 (it is the audio we played) must go red
 *                                        while clause 2 still passes
 *     control C  the committed golden  → every clause must PASS on the shipped fixture
 *
 * B is the one that matters: silence reddens almost anything, so a harness could pass A
 * while its correlation was vacuous. B plays real, loud, perfectly good speech that is the
 * WRONG speech, and only the identity clause can tell.
 *
 * Plus THE DEGRADATION GAUNTLET, which is what the fast tier gained after CI reddened. The
 * browser cases prove the instrument works on the machine it is running on; the gauntlet
 * takes the audio the BASELINE CASE REALLY UPLOADED and re-scores it under seven modelled
 * capture defects — saturation, hard saturation, 5/15/30 % of ScriptProcessor blocks
 * dropped, a very quiet input — requiring the identity clause to survive each one AND
 * requiring the same audio to FAIL with the two templates swapped. It is arithmetic over
 * bytes already captured, so it costs nothing and cannot flake, and it puts the exact
 * environment that broke this file permanently under test.
 *
 * Plus two paper mutations that need no browser at all, printed with their real numbers:
 * the overlap scorer against the sentence itself (1.00), against the decoy (measured), and
 * — the negative control the brief asks for — `assertWords()` invoked with a reference
 * nobody said, whose failure messages are PRINTED so a reader can see the assertion fire.
 *
 * It spends nothing, touches no network beyond loopback, and needs no gateway, so the fast
 * tier runs it on every push.
 *
 * WHAT THE FAST TIER THEREFORE CLAIMS, AND WHAT IT DOES NOT — say this plainly, because a
 * green `--selftest` must not be read as "the audio round trip is verified":
 *
 *   IT PROVES  the composer is reachable with the rail shut; `getUserMedia` opens a device
 *              on a real page; `wavCapture`+`encodeWav` produce a 16 kHz mono RIFF/WAVE the
 *              SERVER's own reader accepts; the capture is audible rather than silence; the
 *              audio that was uploaded is the clip that was played rather than a different
 *              one; the transcript reaches the log and is scored; her gateway voice is
 *              scheduled; and the page fires no CSP violation and logs no error.
 *
 *   IT DOES NOT PROVE  that the recording is a FAITHFUL copy of what was played. That is
 *              `FIDELITY_FLOOR`, and it is deliberately not asserted here: a CI runner's
 *              microphone saturates (peak 1.0000, measured) and halves the score with
 *              nothing wrong with this site. The number is printed on every run, and it is
 *              asserted by `--dry-run` and by the paid run, where the audio path is
 *              somebody's to look at.
 *
 *   IT ALSO DOES NOT PROVE anything about the gateway: no ASR runs here, the transcript is
 *              a fixture, and clause 4 is therefore about the plumbing that carries a
 *              transcript, never about whether the ears work. Only the paid run tests that.
 *
 * ════════════════════════════════════════════════════════════════════════════
 * THE CI JUDGEMENT (2026-09-05), WHICH IS PART OF THE DELIVERABLE.
 *
 *   · `sim/ci/ci.yml`  — `--selftest` only. Hermetic, free, gates PRs, proves the teeth.
 *   · `sim/ci/deployed.yml` — the spending run is wired as a **`workflow_dispatch`-only,
 *     opt-in** job: `spend: yes` and an explicit `budget`. It is NOT added to that file's
 *     4×/day schedule and it is not a merge gate.
 *
 * Why not scheduled, when `check_deployed.mjs` is: that one is free, so running it 1 460
 * times a year costs nothing and catches a regression between promotions. This one is not.
 * Four runs a day is ~4 400 transcriptions, chats and speech syntheses a year out of a
 * budget the whole public demo shares — and the failure it would catch (the gateway's ears
 * stopped working) is already caught free, at every push, by `test_live_hosted_ears.py`
 * tier B where a gateway is configured. A monitor that eats the thing it monitors is not a
 * monitor. So: on demand, with the operator naming the budget, and never automatically.
 */
import { readFileSync, writeFileSync, mkdtempSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { requireBrowser, serveWeb, serveStatic, makeChecks, finish, web, repo }
  from "./browser_harness.mjs";
import { wavDurationMs } from "../functions/api/_lib/wav.js";

const LABEL = "hosted-mic check";

/* ---- argv / environment ---------------------------------------------------- */
const argv = process.argv.slice(2);
const SELFTEST = argv.includes("--selftest");
/**
 * `--dry-run`: everything except the money.
 *
 * The spending routes are ABORTED at the browser exactly as `check_deployed.mjs` aborts
 * them, and yet clauses 1-3 still hold — because the upload body is read off `fetch` at the
 * moment the page hands it over, BEFORE the request is allowed to leave. So a free run
 * still proves, against the real deployment: the button is reachable, `getUserMedia` opened
 * a device on that origin, `mic.js::wavCapture` encoded a 16 kHz mono WAV, and the audio in
 * it is the audio that was played. What it cannot prove is anything about the gateway.
 *
 * It exists because the paid run is a ONE-SHOT with a budget the owner pays for, and every
 * way this could fail for a reason that is not about the ears — an unreachable button, a
 * refused microphone, an empty capture graph — is findable for nothing first. Use it before
 * every real run; it is also the honest thing to point at a fork or a preview.
 */
const DRY = argv.includes("--dry-run");
const cliUrl = argv.find((a) => !a.startsWith("-"));

/** The hard ceiling on gateway-backed requests. Enforced by the interceptor, not by hope. */
const BUDGET = Math.max(1, Number(process.env.MOXIE_MIC_BUDGET || 5) || 5);

/** Routes that cost money on a live deployment. `/api/health` is deliberately absent. */
const SPENDING = /\/api\/(chat|speech|transcriptions|transcribe)\b/;

/* ---- the sentence, the decoy, and the floors -------------------------------- *
 * Both numbers are `sim/tests/test_live_hosted_ears.py`'s, adopted rather than reinvented:
 * two different floors for one claim would be two floors that mean nothing. That file
 * argues them at length — 0.7 is what `test_live_gateway_stt.py::STT_FLOOR` already
 * requires of the same round trip, and it is deliberately not 1.00 because an ASR that
 * hears "Moxie" as "Moxy" is not the failure this exists to catch. */
const STT_FLOOR = 0.7;
const DECOY_CEIL = 0.35;

/**
 * The clip the fake microphone plays, and the words in it.
 *
 * Read out of `sim/web/audio/index.json` — the manifest the SITE speaks from — rather than
 * typed here, so re-rendering the clips (`sim/tools/prerender_audio.py`) moves this fixture
 * with them instead of quietly rotting it. The default is the golden's own parent clip; see
 * the header for the byte-match that established the lineage.
 *
 * `MOXIE_MIC_WAV` + `MOXIE_MIC_TEXT` override both, which is how a recording of an actual
 * child gets pointed at this harness without editing it.
 */
const MANIFEST = JSON.parse(readFileSync(join(web, "audio", "index.json"), "utf8"));
const SPOKEN_TEXT = "Happy birthday! I hope your day is amazing.";
/** No content word in common with `SPOKEN_TEXT`; scored against the SAME transcript so the
 *  floor above measures a distance instead of assuming one. */
const DECOY_TEXT = "Can you take a deep breath with me?";

function manifestClip(text) {
  const rel = (MANIFEST.moxie || {})[text];
  if (!rel) throw new Error(`no shipped clip for ${JSON.stringify(text)} — ` +
                            `sim/web/audio/index.json can no longer speak this line`);
  return rel;
}

/* ---- the target ------------------------------------------------------------- *
 * `sim/tests/test_no_deployment_defaults.py` forbids a deployment's hostname as a default
 * in shipped code. The default is read at runtime from the site's OWN canonical link, the
 * trick `check_deployed.mjs` established: the artifact names its own home, and a fork that
 * re-points that line re-points this tool with it. */
function canonicalOrigin() {
  const html = readFileSync(join(web, "index.html"), "utf8");
  const m = html.match(/<link\s+rel=["']canonical["']\s+href=["']([^"']+)["']/i);
  if (!m) return null;
  try { return new URL(m[1]).origin; } catch { return null; }
}

/* The device the composer defect was measured on, and the harder case for clause 1. */
const PHONE = { width: 390, height: 844, isMobile: true, hasTouch: true, deviceScaleFactor: 3 };
const IOS_UA =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 " +
  "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1";

/* ════════════════════════ transcript maths ════════════════════════════════════ *
 * A PORT of `sim/tests/helpers_audio.py::word_overlap`, deliberately and not by accident:
 * that file is the repo's one definition of "did the ASR hear the sentence", and every
 * live suite scores against it. It cannot be imported here — it is Python, and this runs in
 * node with no interpreter guaranteed — so it is restated with the same regex, the same
 * lowercase/curly-apostrophe normalisation and the same MULTISET RECALL semantics.
 *
 * Recall rather than F1, for that file's reason: a transcript that gets every word right
 * plus a stray "um" is a success; one that drops half the sentence is not. The looping fake
 * microphone makes that choice load-bearing here — the transcript legitimately contains the
 * sentence more than once, which an F1 would punish.
 *
 * Verified equal to the Python original on 2026-09-05 over the table in `--selftest`
 * (identity, decoy, empty, repetition, punctuation and curly-apostrophe cases).
 */
const WORD_RE = /[a-z0-9']+/g;
export function normalizeWords(text) {
  return String(text || "").toLowerCase().replace(/’/g, "'").match(WORD_RE) || [];
}
export function wordOverlap(reference, hypothesis) {
  const ref = normalizeWords(reference);
  if (!ref.length) return 0;
  const pool = new Map();
  for (const w of normalizeWords(hypothesis)) pool.set(w, (pool.get(w) || 0) + 1);
  let hits = 0;
  for (const w of ref) {
    const n = pool.get(w) || 0;
    if (n > 0) { pool.set(w, n - 1); hits++; }
  }
  return hits / ref.length;
}

/**
 * Clause 4, as its own function so `--selftest` can watch it FAIL on a reference nobody
 * said. An assertion never seen to fail is not an assertion.
 */
function assertWords(c, heard, { spoken, decoy, where }) {
  const right = wordOverlap(spoken, heard);
  const wrong = wordOverlap(decoy, heard);
  c.ok(String(heard || "").trim().length > 0,
       `${where}: the route returned an EMPTY transcript for real speech`);
  c.ok(right >= STT_FLOOR,
       `${where}: recovered only ${right.toFixed(2)} of the words (floor ${STT_FLOOR})\n` +
       `        said : ${JSON.stringify(spoken)}\n        heard: ${JSON.stringify(heard)}`);
  c.ok(wrong < DECOY_CEIL,
       `${where}: scored ${wrong.toFixed(2)} against a sentence that was NEVER SPOKEN ` +
       `(ceiling ${DECOY_CEIL}) — the overlap measure is not discriminating, so the floor ` +
       `above proves nothing\n        decoy: ${JSON.stringify(decoy)}\n` +
       `        heard: ${JSON.stringify(heard)}`);
  return { right, wrong };
}

/* ════════════════════════ waveform maths ══════════════════════════════════════ *
 * Clause 3 asks "is this the recording we played", through a path that legitimately
 * changes the samples: Chrome resamples the file to the capture rate, `getUserMedia` runs
 * echo cancellation and noise suppression (`mic.js::wavCapture` asks for both), and
 * `encodeWav` decimates to 16 kHz by nearest neighbour. Sample-wise comparison is
 * meaningless after that, so the comparison is over the ENERGY ENVELOPE.
 *
 * ─────────────────────────────────────────────────────────────────────────────
 * THIS BLOCK WAS REWRITTEN AFTER IT WENT RED IN CI (run 34013443378), AND THE REWRITE IS
 * THE INTERESTING PART, SO IT IS WRITTEN DOWN RATHER THAN TUNED AWAY.
 *
 * The first version took the PEAK amplitude per 10 ms frame and slid the source's single
 * period across the capture, asserting an absolute floor and a fixed margin. It passed on
 * this developer's box at 0.955-0.991 and FAILED on the GitHub runner:
 *
 *     baseline    peak 1.0000   src 0.430   decoy 0.294   margin +0.136   ✗ both clauses
 *     mutation A  peak 0.0000   src -1.000  decoy -1.000                  (silence)
 *     mutation B  peak 1.0000   src 0.339   decoy 0.471   margin -0.131
 *     control C   peak 0.9997   src 0.592   decoy 0.277   margin +0.315   ✗ the floor
 *
 * `peak 1.0000` in three of four cases is the whole story: THE RUNNER'S CAPTURE SATURATES.
 * `getUserMedia`'s audio processing applies gain until the loud parts hit full scale, and a
 * PEAK envelope of a clipped signal is a flat top — the feature the measure was built on is
 * the one the environment destroys. Lowering the floor again would have been the third
 * per-box tune of the same number, and a threshold tuned per machine reddens on the next
 * one.
 *
 * So the failure was reproduced OFFLINE instead — the capture chain modelled as
 * loop → resample → compressor with attack/release → clip → decimate — and candidate
 * measures scored across nine conditions (clean/saturated/hard-saturated × 48 kHz/44.1 kHz,
 * 5/15/30 % of ScriptProcessor blocks dropped, and a very quiet input). The model
 * reproduces the defect: peak-envelope Pearson falls 0.981 → 0.730 → 0.665 and its margin
 * +0.671 → +0.238 → +0.168, which is the shape CI reported. Three findings came out of it:
 *
 *   1. **RMS beats peak under clipping.** Clipping caps the top of a waveform but the
 *      frame ENERGY still tracks the speech. Plain RMS held 0.985 → 0.848 where peak fell
 *      to 0.665.
 *   2. **The LOG of the RMS envelope is very nearly invariant to saturation** — 0.982,
 *      0.975, 0.981 across clean, saturated and hard-saturated, a spread of 0.007 against
 *      the peak envelope's 0.316. A compressor is approximately a gain, and a gain is an
 *      offset in log space, which Pearson already removes.
 *   3. **No rigid template survives DROPPED BLOCKS.** A starved main thread makes
 *      `ScriptProcessor` skip 4096-frame blocks, which time-warps the recording; at 15 %
 *      dropped the whole-clip margin fell to +0.105. The fix is to stop requiring one
 *      global alignment: score each ~1 s chunk of the capture against its best match
 *      ANYWHERE in the template. That is what `score()` does, and it holds 0.751 at 30 %
 *      dropped where the rigid version has collapsed.
 *   4. **THE TEMPLATE MUST BE THE LOOPED FILE, NOT ONE PERIOD** — found by this file's own
 *      teeth, one run after the rewrite, when control C (the 0.75 s committed golden) came
 *      back at 52 % while the 3.95 s sentence passed at 89 %. Chrome's fake device loops the
 *      file, so a chunk of the capture routinely STRADDLES A LOOP SEAM, and a seam-crossing
 *      chunk has no matching position in a single copy of the clip — it matches nothing and
 *      votes at random. A 0.45 s chunk of a 0.75 s clip straddles most of the time, which is
 *      why the short fixture failed and the long one did not. Tiling each template with a
 *      copy of itself gives every window a home: the golden goes 57 % → 91 % clean and
 *      40 % → 89 % with a tenth of the blocks dropped, while the mutation that must fail
 *      moves to 22 %. Both templates are tiled, so the position count stays equal — a longer
 *      template would otherwise get more chances at a coincidental match, which was the
 *      other half of why the short golden lost to the longer decoy.
 *
 * AND THE THRESHOLD ITSELF CHANGED SHAPE, which matters more than the statistic did. Read
 * the CI table again: in EVERY case the ordering was right — the clip that was actually
 * played out-scored the other one (0.430 > 0.294; 0.339 < 0.471, correctly, because
 * mutation B played the decoy; 0.592 > 0.277). Saturation moved the MAGNITUDES by half and
 * left the COMPARISON intact. So clause 3 no longer asserts a magnitude at all: it asserts
 * that the played clip out-scores the other. Both scores are produced in the same run, on
 * the same machine, from the same recording — which is the only way a number here can be
 * environment-invariant.
 *
 * And the comparison is taken as a VOTE over chunks rather than as a difference of scores,
 * because a difference was measured to be too noisy to gate on even after all of the above:
 * at load 29 on this box a healthy run scored +0.058 against a mutation's +0.038. The vote
 * separates the same two populations by 0.32. `score()` carries that table.
 */
/** Frames per second of the envelope. 10 ms is finer than anything that matters here. */
const ENV_HZ = 100;

/** RMS energy per frame, 0..1. RMS rather than peak: see finding 1 above. */
function envelope(pcm16, rate, hz = ENV_HZ) {
  const hop = Math.max(1, Math.round(rate / hz));
  const out = [];
  for (let i = 0; i + hop <= pcm16.length; i += hop) {
    let s = 0;
    for (let j = i; j < i + hop; j++) { const v = pcm16[j] / 32768; s += v * v; }
    out.push(Math.sqrt(s / hop));
  }
  return out;
}

/** Log of the envelope, which is what makes it saturation-proof (finding 2). The epsilon
 *  puts digital silence at -4 rather than at -Infinity. */
const logEnv = (e) => e.map((v) => Math.log10(v + 1e-4));

/** Pearson correlation of `a` against `b[at … at+a.length]`, or -1 where either is flat. */
function corrAt(a, b, at) {
  const n = a.length;
  let ma = 0, mb = 0;
  for (let i = 0; i < n; i++) { ma += a[i]; mb += b[at + i]; }
  ma /= n; mb /= n;
  let num = 0, da = 0, db = 0;
  for (let i = 0; i < n; i++) {
    const x = a[i] - ma, y = b[at + i] - mb;
    num += x * y; da += x * x; db += y * y;
  }
  if (da <= 0 || db <= 0) return -1;
  return num / Math.sqrt(da * db);
}

/** The best score for one chunk anywhere in `template`. The inner loop of everything below. */
function bestIn(chunk, template) {
  let best = -1;
  for (let j = 0; j + chunk.length <= template.length; j++) {
    const v = corrAt(chunk, template, j);
    if (v > best) best = v;
  }
  return best;
}

/**
 * Score the capture against BOTH templates, chunk by chunk, WITHOUT requiring one global
 * alignment — a starved main thread makes `ScriptProcessor` skip whole 4096-frame blocks,
 * which time-warps the recording, and no rigid template survives that (finding 3 above).
 * Each ~1 s chunk is matched against its best position anywhere in each template.
 *
 * Returns the two median scores and — the number clause 3 actually asserts — the FRACTION
 * of chunks that matched the played clip better than the unrelated one.
 *
 * WHY A VOTE AND NOT A DIFFERENCE OF SCORES. The first rewrite asserted
 * `median(played) - median(unrelated) >= 0.05`, which is still a comparison made on one
 * machine from one recording, and it was STILL too noisy to gate on. Measured on this box
 * at load 29 with every core busy: a HEALTHY run scored +0.058 and the mutation that must
 * fail scored +0.038. Twenty thousandths between "green" and "the teeth work" is not a
 * threshold, it is a coin toss with a decimal point.
 *
 * A vote concentrates where a difference of medians does not: each chunk is an independent
 * head-to-head and there are ~24 of them, so the noise that moves any single score averages
 * out. Measured across nine modelled capture conditions (clean, saturated, hard-saturated,
 * 5/15/30/50 % of blocks dropped, a very quiet input, 44.1 kHz):
 *
 *     played = the sentence   vote 0.750 … 0.875
 *     played = the decoy      vote 0.208 … 0.429
 *
 * a gap of 0.32 where the difference of medians had 0.02.
 *
 * @returns {{good:number, bad:number, margin:number, vote:number, chunks:number}}
 */
function score(capturePcm, captureRate, ctx) {
  const sig = logEnv(envelope(capturePcm, captureRate));
  const a0 = logEnv(envelope(ctx.source, ctx.sourceRate));
  const b0 = logEnv(envelope(ctx.decoyPcm, ctx.decoyRate));
  const shortest = Math.min(a0.length, b0.length, sig.length);
  const L = Math.min(ENV_HZ, Math.max(20, Math.floor(shortest * 0.6)));
  const stride = Math.max(1, Math.round(L / 2));
  const none = { good: -1, bad: -1, margin: 0, vote: -1, chunks: 0 };
  if (sig.length < L || a0.length < L || b0.length < L) return none;
  /* THE TEMPLATES ARE TILED, and this is a correctness fix rather than a tuning knob.
   * Chrome's fake device LOOPS the file for as long as the stream is open, so a chunk of
   * the capture routinely straddles a loop seam — and a seam-crossing chunk has no matching
   * position in a single copy of the clip, so it matches nothing and votes at random. The
   * shorter the clip, the worse it is: a 0.45 s chunk of the 0.75 s committed golden
   * straddles most of the time, which is exactly how control C failed at 52 % while the
   * 3.95 s sentence passed at 89 %. Concatenating each template with itself gives every
   * seam-crossing window a home. Measured on the golden: 57 % → 91 % clean, 40 % → 89 %
   * with a tenth of the blocks dropped, while the mutation that must fail moved 22 %.
   * Both templates are tiled, so the position count stays equal and the comparison stays
   * unbiased — a longer template would otherwise get more chances at a coincidental match,
   * which is the OTHER half of why the short golden was losing to the longer decoy. */
  const A = a0.concat(a0), B = b0.concat(b0);
  const ga = [], gb = [];
  let win = 0, n = 0;
  for (let i = 0; i + L <= sig.length; i += stride) {
    const chunk = sig.slice(i, i + L);
    const a = bestIn(chunk, A), b = bestIn(chunk, B);
    ga.push(a); gb.push(b);
    if (a > b) win++;
    n++;
  }
  if (!n) return none;
  const med = (x) => { const y = x.slice().sort((p, q) => p - q); return y[Math.floor(y.length / 2)]; };
  const good = med(ga), bad = med(gb);
  return { good, bad, margin: good - bad, vote: win / n, chunks: n };
}

/**
 * The fraction of chunks that must pick the clip that was actually played. Asserted
 * EVERYWHERE, the fast tier included, because it is a COMPARISON and not a magnitude: both
 * halves come out of the same recording, on the same machine, through the same code, so an
 * environment that degrades the audio degrades both halves together.
 *
 * 0.60 sits between a worst measured true positive of 0.750 and a worst measured inversion
 * of 0.429 (nine modelled conditions; the table is at `score`). Deliberately NOT 0.5 + eps:
 * a coin-flip boundary would make a degraded run a coin toss.
 */
const IDENTITY_VOTE = 0.60;

/**
 * The absolute fidelity magnitude, asserted ONLY where the audio path is a known quantity —
 * a developer's box or a real deployment (`--dry-run` and the paid run), never the fast
 * tier. The fast tier REPORTS it instead, because a runner whose own microphone saturates
 * (`peak 1.0000`, measured in CI run 34013443378) is not a defect in this site.
 */
const FIDELITY_FLOOR = 0.60;

/* ════════════════ the degradation gauntlet (the CI failure, under test) ═══════ *
 * `--selftest` browser cases prove the instrument works on THIS machine. This proves it
 * survives a machine it has not got — specifically the one that reddened CI run
 * 34013443378, whose capture saturated at peak 1.0000 and halved every score.
 *
 * It takes the REAL audio the baseline browser case uploaded and degrades it the way a
 * runner does, then re-runs the identity clause over each degraded copy. No browser, no
 * clock, no network: it is arithmetic over bytes that were already captured, so it costs
 * nothing and cannot flake. Two things must hold for every degradation:
 *
 *   · the clip that WAS played still wins the chunk vote by `IDENTITY_VOTE`;
 *   · with the two templates SWAPPED, the same audio must FAIL — otherwise the margin is
 *     being cleared by something other than the audio, which is how this measure would go
 *     quietly vacuous.
 *
 * A degradation that stops reddening the swapped case is itself reported, because a
 * gauntlet whose mutations have gone toothless is worse than no gauntlet.
 */
const DEGRADATIONS = [
  ["as captured", (f) => f],
  /* THE CI DEFECT: gain until the loud parts clip. Modelled as a compressor with a fast
   * attack and slow release, which is what produces `peak 1.0000` on a runner. */
  ["saturated (the runner's own microphone)", (f) => compress(f, 0.30)],
  ["hard saturated", (f) => compress(f, 0.60)],
  /* A STARVED MAIN THREAD: `mic.js::wavCapture` records through a `ScriptProcessor`, which
   * simply does not deliver a 4096-frame block when the page is busy. That time-warps the
   * recording, which is why the score is chunked rather than one global alignment. */
  ["5% of ScriptProcessor blocks dropped", (f) => dropBlocks(f, 0.05)],
  ["15% dropped", (f) => dropBlocks(f, 0.15)],
  /* A THIRD OF THE RECORDING GONE, and this one carries a lower expectation ON PURPOSE —
   * 0.5 rather than `IDENTITY_VOTE`, i.e. it must still CHOOSE the right clip but need not
   * clear the full confidence bar. Dropping 30 % of the blocks also drops 30 % of the
   * recording, so the vote is taken over ~12 chunks instead of ~24, and demanding the same
   * confidence from half the evidence is a statement about sample size rather than about
   * the measure. Measured at 58 % on a box at load 26 — right clip, thinner majority. The
   * swapped control below is still held to the FULL bar, so this cannot go vacuous. */
  ["30% dropped + saturated", (f) => compress(dropBlocks(f, 0.30), 0.30), 0.5],
  ["very quiet input", (f) => scale(f, 0.05)],
];

/** A compressor with attack/release, clipping at full scale. */
function compress(pcm, target) {
  const out = new Int16Array(pcm.length);
  const win = Math.round(0.02 * 16000);
  let g = 1;
  for (let i = 0; i < pcm.length; i += win) {
    const end = Math.min(pcm.length, i + win);
    let r = 0;
    for (let j = i; j < end; j++) { const v = pcm[j] / 32768; r += v * v; }
    r = Math.sqrt(r / Math.max(1, end - i));
    const want = r > 1e-4 ? Math.min(30, target / r) : g;
    g = g + (want - g) * (want > g ? 0.5 : 0.15);
    for (let j = i; j < end; j++)
      out[j] = Math.max(-32768, Math.min(32767, Math.round(pcm[j] * g)));
  }
  return out;
}

/** Whole 4096-frame blocks never delivered, so what remains is spliced together. */
function dropBlocks(pcm, rate) {
  const keep = [];
  for (let i = 0; i < pcm.length; i += 4096)
    if (((i / 4096) % Math.max(2, Math.round(1 / rate))) !== 0)
      keep.push(pcm.subarray(i, Math.min(pcm.length, i + 4096)));
  const total = keep.reduce((a, b) => a + b.length, 0);
  const out = new Int16Array(total);
  let at = 0;
  for (const k of keep) { out.set(k, at); at += k.length; }
  return out;
}

function scale(pcm, g) {
  const out = new Int16Array(pcm.length);
  for (let i = 0; i < pcm.length; i++) out[i] = Math.round(pcm[i] * g);
  return out;
}

/**
 * @param {*} c    the selftest's `makeChecks()` bundle
 * @param {Int16Array} pcm  what the baseline case really uploaded
 * @param {number} rate
 * @param {*} ctx  `{source, sourceRate, decoyPcm, decoyRate}`
 */
function gauntlet(c, pcm, rate, ctx) {
  console.log(`\n  the degradation gauntlet — the CI failure mode, over the audio the ` +
              `baseline case really uploaded`);
  console.log(`    degradation                              peak    vote          played  unrelated  swapped`);
  const swapped = { source: ctx.decoyPcm, sourceRate: ctx.decoyRate,
                    decoyPcm: ctx.source, decoyRate: ctx.sourceRate };
  for (const [name, fn, floor] of DEGRADATIONS) {
    const want = floor === undefined ? IDENTITY_VOTE : floor;
    const d = fn(pcm);
    let pk = 0;
    for (let i = 0; i < d.length; i++) { const v = Math.abs(d[i]); if (v > pk) pk = v; }
    pk /= 32768;
    const got = score(d, rate, ctx);
    const inv = score(d, rate, swapped);
    /* The swapped control is always held to the FULL bar, whatever this row expects of the
     * real one — a mutation judged by a lowered standard is not a mutation. */
    const swapFails = inv.vote < IDENTITY_VOTE;
    console.log(`    ${name.padEnd(40)} ${pk.toFixed(3)}   ${(got.vote * 100).toFixed(0).padStart(3)}%` +
                ` (need ${(want * 100).toFixed(0)}%)  ${got.good.toFixed(3)}    ${got.bad.toFixed(3)}` +
                `   ${swapFails ? "reddens ✓" : "PASSES ✗"}`);
    c.ok(got.vote >= want,
         `gauntlet "${name}": the identity clause must survive it — only ` +
         `${(got.vote * 100).toFixed(0)}% of ${got.chunks} chunks chose the clip played ` +
         `(need ${(want * 100).toFixed(0)}%)`);
    c.ok(swapFails,
         `gauntlet "${name}": with the templates SWAPPED the same audio must FAIL — ` +
         `${(inv.vote * 100).toFixed(0)}% of chunks still chose the "played" clip, so the ` +
         `vote above is not coming from the audio`)
  }
}

/* ════════════════════════ the fake microphone's WAV ═══════════════════════════ */
/** Mono PCM16 → a complete RIFF/WAVE file. The one place this file writes a header. */
function riff(pcm16, rate) {
  const n = pcm16.length;
  const b = Buffer.alloc(44 + n * 2);
  b.write("RIFF", 0); b.writeUInt32LE(36 + n * 2, 4); b.write("WAVE", 8);
  b.write("fmt ", 12); b.writeUInt32LE(16, 16); b.writeUInt16LE(1, 20); b.writeUInt16LE(1, 22);
  b.writeUInt32LE(rate, 24); b.writeUInt32LE(rate * 2, 28);
  b.writeUInt16LE(2, 32); b.writeUInt16LE(16, 34);
  b.write("data", 36); b.writeUInt32LE(n * 2, 40);
  for (let i = 0; i < n; i++) b.writeInt16LE(Math.max(-32768, Math.min(32767, pcm16[i])), 44 + i * 2);
  return b;
}

/** Mono PCM16 out of a RIFF/WAVE file, chunk-walked rather than assuming a 44-byte header. */
function readWav(buf) {
  if (buf.slice(0, 4).toString() !== "RIFF" || buf.slice(8, 12).toString() !== "WAVE")
    throw new Error("not a RIFF/WAVE file");
  let at = 12, rate = 0, channels = 1, bits = 16, data = null;
  while (at + 8 <= buf.length) {
    const id = buf.slice(at, at + 4).toString();
    const size = buf.readUInt32LE(at + 4);
    if (id === "fmt ") {
      channels = buf.readUInt16LE(at + 10); rate = buf.readUInt32LE(at + 12);
      bits = buf.readUInt16LE(at + 22);
    } else if (id === "data") {
      data = buf.slice(at + 8, Math.min(buf.length, at + 8 + size));
    }
    at += 8 + size + (size % 2);
  }
  if (!data || bits !== 16) throw new Error(`unsupported WAV (bits=${bits})`);
  const n = Math.floor(data.length / 2 / channels);
  const pcm = new Int16Array(n);
  for (let i = 0; i < n; i++) pcm[i] = data.readInt16LE(i * 2 * channels);   // channel 0
  return { pcm, rate };
}

/**
 * Decode shipped clips with the browser we already require, and write the throwaway WAVs
 * the fake microphone will play.
 *
 * WHY CHROME AND NOT `ffmpeg`. `sim/tests/goldens/README.md` records the last attempt: a
 * test that shelled out to ffmpeg to decode one mp3 fixture, in a PR whose whole subject
 * was declaring every dependency exactly once, and CI run 33985062379 died with
 * `FileNotFoundError: 'ffmpeg'` because a runner has none. A `shutil.which` skip would have
 * turned that red into a silent pass. The dependency is removed here the same way it was
 * removed there — by using something already declared. `requireBrowser` has already found a
 * Chrome; `decodeAudioData` is a first-class mp3 decoder; a page served from `sim/web` on
 * loopback can fetch the clip same-origin.
 *
 * @param {*} puppeteer @param {string} chrome
 * @param {string[]} rels manifest-relative clip paths, e.g. `moxie/3667…mp3`
 * @param {number} rate  the WAV rate to write (Chrome resamples on capture; see the header)
 * @returns {Promise<Record<string, Int16Array>>} keyed by `rels`
 */
async function decodeClips(puppeteer, chrome, rels, rate) {
  const site = await serveStatic(web);
  const browser = await puppeteer.launch({
    executablePath: chrome, headless: "new",
    args: ["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader"],
  });
  try {
    const page = await browser.newPage();
    /* The HUB, not `sim.html`: this page exists only to be a same-origin `fetch` + an
     * `AudioContext`, and the simulator would drag in three.js, a GLB and a WebGL context
     * to decode an mp3. Any page under `sim/web` would do; the cheapest one is right. */
    await page.goto(site.url + "/index.html", { waitUntil: "domcontentloaded", timeout: 20000 });
    const got = await page.evaluate(async (list, r) => {
      const ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: r });
      const out = {};
      for (const rel of list) {
        const raw = await (await fetch("audio/" + rel, { cache: "no-store" })).arrayBuffer();
        const buf = await ctx.decodeAudioData(raw);
        const ch = buf.getChannelData(0);
        const ints = new Array(ch.length);
        for (let i = 0; i < ch.length; i++) {
          let v = ch[i]; if (v > 1) v = 1; if (v < -1) v = -1;
          ints[i] = Math.round(v * 32767);
        }
        out[rel] = { rate: buf.sampleRate, pcm: ints };
      }
      try { ctx.close(); } catch (e) {}
      return out;
    }, rels, rate);
    const decoded = {};
    for (const rel of rels) {
      if (!got[rel]) throw new Error(`Chrome could not decode audio/${rel}`);
      if (got[rel].rate !== rate)
        throw new Error(`audio/${rel} decoded at ${got[rel].rate} Hz, wanted ${rate}`);
      decoded[rel] = Int16Array.from(got[rel].pcm);
    }
    return decoded;
  } finally {
    try { await browser.close(); } catch {}
    site.close();
  }
}

/** `pcm` with `padS` seconds of digital silence on each end, so a capture that starts a
 *  moment late still gets the first word. */
function padded(pcm, rate, padS = 0.35) {
  const pad = Math.round(padS * rate);
  const out = new Int16Array(pad * 2 + pcm.length);
  out.set(pcm, pad);
  return out;
}

/* ════════════════════════ the run ═════════════════════════════════════════════ */
/**
 * Load `url` on a phone with a fake microphone, press Listen, speak, press it again, and
 * record everything that happened.
 *
 * Returns a plain record; the caller decides what is a failure. That split is what lets
 * `--selftest` demand a RED out of the same code path that demands a GREEN out of
 * production, rather than a second implementation that could disagree with this one.
 *
 * @param {*} browser a browser ALREADY LAUNCHED with the fake-capture flags for this clip
 * @param {string} url
 * @param {{recordMs:number, settleMs?:number, budget:number, dry?:boolean,
 *          stub?:(r:any)=>boolean}} opts
 *   `stub` answers a request locally and returns true when it handled it (`--selftest`);
 *   `dry` aborts every spending route instead of paying for it (`--dry-run`).
 */
async function probeTurn(browser, url, opts) {
  const page = await browser.newPage();
  await page.setViewport(PHONE);
  await page.setUserAgent(IOS_UA);

  /* The violation EVENT, not its console rendering, installed before a single page script
   * runs — `test_csp.mjs` argues this at length: a console line is a sentence to regex,
   * `securitypolicyviolation` carries the directive and the blocked URI, and it fires for
   * refusals that log nothing at all. The `fetch` and Web Audio instruments ride along in
   * the same document-start hook, so `mic.js` cannot beat them to the punch. */
  await page.evaluateOnNewDocument(() => {
    window.__csp = [];
    document.addEventListener("securitypolicyviolation", (e) => {
      window.__csp.push({ directive: e.effectiveDirective || e.violatedDirective,
                          blocked: e.blockedURI, sample: (e.sample || "").slice(0, 80) });
    });

    window.__mic = { calls: [], plays: [], upload: null, uploadBytes: 0, pending: [] };

    /* THE UPLOAD AND THE ANSWER, read off `fetch` rather than off puppeteer's request
     * interception. Two reasons, both practical: `request.postData()` is a STRING and this
     * body is raw 16-bit audio, which a string round trip mangles; and a `clone()` here
     * reads the response the PAGE got, after redirects and after the edge, which is the
     * thing a visitor experienced. */
    const b64 = (ab) => {
      const u = new Uint8Array(ab); let s = "";
      for (let i = 0; i < u.length; i += 0x8000)
        s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000));
      return btoa(s);
    };
    const orig = window.fetch;
    window.fetch = function (input, init) {
      const u = String((input && input.url) || input || "");
      const watched = /\/api\/(chat|speech|transcriptions|transcribe)\b/.test(u);
      let rec = null;
      if (watched) {
        rec = { url: u, status: 0, bodyLen: 0, body: "" };
        window.__mic.calls.push(rec);
        const body = init && init.body;
        if (body && typeof body.arrayBuffer === "function") {
          window.__mic.uploadBytes = body.size || 0;
          window.__mic.pending.push(
            body.arrayBuffer().then((ab) => { window.__mic.upload = b64(ab); }).catch(() => {}));
        }
      }
      const p = orig.apply(this, arguments);
      if (!watched) return p;
      return p.then((r) => {
        let c = null;
        try { c = r.clone(); } catch (e) { c = null; }
        if (c) window.__mic.pending.push(c.text().then((t) => {
          rec.status = r.status; rec.bodyLen = t.length; rec.body = t.slice(0, 3000);
        }).catch(() => {}));
        else rec.status = r.status;
        return r;
      }, (e) => { rec.status = -1; rec.body = String((e && e.message) || e); throw e; });
    };

    /* Web Audio, instrumented where sound is actually MADE — the shape
     * `sim/test_typed_turn.mjs` established and `test_mic_spend.mjs` refined. The gateway
     * voice arrives through `createBuffer` (audio.js builds it by hand from int16 PCM); a
     * pre-rendered clip arrives through `decodeAudioData`. Every buffer that is SCHEDULED
     * is recorded with its peak, because a silent clip passes every structural check while
     * making no sound. */
    const C = window.AudioContext || window.webkitAudioContext;
    if (!C) return;
    const from = new WeakMap();
    const da = C.prototype.decodeAudioData;
    C.prototype.decodeAudioData = function (...a) {
      const bytes = a[0] && a[0].byteLength;            // read BEFORE decode detaches it
      const p = da.apply(this, a);
      return p && p.then ? p.then((b) => { try { from.set(b, bytes); } catch (e) {} return b; }) : p;
    };
    const cbs = C.prototype.createBufferSource;
    C.prototype.createBufferSource = function () {
      const node = cbs.call(this);
      const start = node.start.bind(node);
      node.start = function (...a) {
        const b = node.buffer;
        if (b) {
          const d = b.getChannelData(0);
          let p = 0;
          for (let i = 0; i < d.length; i++) { const v = Math.abs(d[i]); if (v > p) p = v; }
          let bytes = null;
          try { bytes = from.has(b) ? from.get(b) : null; } catch (e) {}
          window.__mic.plays.push({ frames: b.length, rate: b.sampleRate, peak: p, bytes });
        }
        return start(...a);
      };
      return node;
    };
  });

  /* THE BUDGET, as an interceptor. Everything past the ceiling is aborted at the browser
   * and recorded, so "at most N gateway calls" is a property of this run rather than a
   * sentence in a comment. */
  const spent = [];       // spending URLs that were allowed through
  const refused = [];     // spending URLs aborted because the ceiling was reached
  const failed = [];      // requests the network layer never completed
  await page.setRequestInterception(true);
  page.on("request", (r) => {
    if (r.isInterceptResolutionHandled()) return;
    const u = r.url();
    if (SPENDING.test(u)) {
      // `--dry-run` refuses ALL of them; a paid run refuses only what is over the ceiling.
      // Either way the request was MADE, and `fetch`'s wrapper above already has its body,
      // so clauses 1-3 hold in both modes.
      if (opts.dry || spent.length >= opts.budget) { refused.push(u); return r.abort("blockedbyclient"); }
      spent.push(u);
    }
    if (opts.stub && opts.stub(r)) return;
    return r.continue();
  });
  page.on("requestfailed", (r) => {
    if (refused.includes(r.url())) return;                     // ours, on purpose
    failed.push({ url: r.url(), why: r.failure()?.errorText || "?" });
  });
  const consoleErrs = [];
  page.on("console", (m) => { if (m.type() === "error") consoleErrs.push(m.text()); });
  page.on("pageerror", (e) => consoleErrs.push("PAGEERR " + e.message));

  const res = await page.goto(url, { waitUntil: "load", timeout: 45000 });
  await page.waitForFunction("!!window.moxieMic && !!window.moxieMode", { timeout: 20000 })
            .catch(() => {});
  /* `mode.js`'s first `/api/health` decides whether `mic.js` has cloud ears at all, and
   * `env.js` paints the composer off it. Wait for the ANSWER rather than for a clock. */
  await page.waitForFunction("!!window.moxieMode.ears && window.moxieMode.ears() === true",
                             { timeout: 20000 }).catch(() => {});
  await new Promise((r) => setTimeout(r, opts.settleMs ?? 1500));

  /* Clause 1, measured BEFORE anything is pressed: a first-time visitor's page, unscrolled,
   * with the engineering drawer shut. Written inline rather than passed to `eval()` — the
   * shipped CSP has no `'unsafe-eval'`, so an `eval()` here would be REFUSED, and refused
   * by firing the very `securitypolicyviolation` clause 6 is watching for. */
  const before = await page.evaluate(() => {
    const measure = (sel) => {
      const el = document.querySelector(sel);
      if (!el) return { found: false };
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      const out = { found: true, w: Math.round(r.width), h: Math.round(r.height),
                    top: Math.round(r.top), bottom: Math.round(r.bottom),
                    left: Math.round(r.left), right: Math.round(r.right),
                    display: cs.display, visibility: cs.visibility, disabled: !!el.disabled };
      if (r.width <= 0 || r.height <= 0) return { ...out, sized: false };
      const hit = document.elementFromPoint(Math.round(r.left + r.width / 2),
                                            Math.round(r.top + r.height / 2));
      return { ...out, sized: true,
               self: !!hit && (hit === el || el.contains(hit)),
               hit: hit ? (hit.id ? "#" + hit.id : hit.tagName.toLowerCase()) : "null" };
    };
    return {
      scrollY: Math.round(window.scrollY), innerW: innerWidth, innerH: innerHeight,
      /* A microphone needs a SECURE CONTEXT, and this is recorded rather than assumed
       * because getting it wrong looks exactly like a broken page: `navigator.mediaDevices`
       * is simply `undefined` on an insecure origin, `mic.js::wavCapture` rejects with
       * "unsupported", and the button writes an honest status line about a browser that is
       * in fact perfectly capable. Cost an entire hermetic run to learn — `--selftest`
       * serves over http on a MAPPED hostname (`moxie.hosted.test`, so `env.js` takes the
       * hosted branch), and Chrome grants secure-context by HOSTNAME, not by the address it
       * resolves to, so `127.0.0.1` is trusted and a name pointing at it is not. Hence the
       * `--unsafely-treat-insecure-origin-as-secure` flag on that path only; a real
       * deployment is https and needs nothing. */
      secure: !!window.isSecureContext,
      hasMediaDevices: !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia),
      title: document.title, hasHud: !!document.getElementById("hud"),
      railOpen: document.getElementById("rail-toggle")?.getAttribute("aria-expanded") ?? null,
      mic: measure("#mic-btn"), say: measure("#speech-btn"),
      ears: !!(window.moxieMode && window.moxieMode.ears && window.moxieMode.ears()),
      target: window.moxieMic && window.moxieMic.sttTarget ? window.moxieMic.sttTarget() : null,
      maxRecordMs: window.moxieMic && window.moxieMic.maxRecordMs
        ? window.moxieMic.maxRecordMs() : null,
    };
  });

  /* SPEAK. A real click on the real button — not `moxieMic.start()` — because the wiring
   * from the button to the recorder is part of what is under test, and a `page.click` is a
   * genuine user gesture, which is what unblocks audio and the permission grant. */
  let clicked = false;
  try { await page.click("#mic-btn"); clicked = true; } catch (e) {
    consoleErrs.push("CLICK #mic-btn failed: " + ((e && e.message) || e));
  }
  /* DID THE DEVICE ACTUALLY OPEN. `getUserMedia` on the deployed origin is item 2 of
   * `test_live_hosted_ears.py`'s unproven list, so it is a measured fact here and not an
   * assumption — and it is measured EARLY because the alternative is expensive: a
   * microphone that never opens reaches no upload, so both waits below would burn their
   * full timeout and the run would report "nothing happened" instead of "the device was
   * refused". Recorded state, never a live sample (playbook rule 11). */
  const opened = await page.waitForFunction(
    () => !!(window.moxieMic && window.moxieMic.isRecording && window.moxieMic.isRecording()),
    /* 25 s and not 8 s, and the difference is a measurement rather than padding. At 8 s this
     * reported "the microphone never opened" on a 24-core box at load 48 — `getUserMedia`
     * had simply not been serviced yet. It is a WAIT, so it costs nothing when the device
     * opens promptly and everything it buys is on the failure path; the only reason it is
     * bounded at all is to avoid burning the two 45 s waits below on a run that is already
     * dead. A generous bound still does that. */
    { timeout: 25000 }).then(() => true).catch(() => false);

  if (opened) {
    await new Promise((r) => setTimeout(r, opts.recordMs));
    if (clicked) await page.click("#mic-btn").catch(() => {});

    /* Wait for the OUTCOME, not for a clock (playbook rule 11): the upload's answer, then
     * the page's own record of it. `mic.js` writes `stats.transcripts` on a real transcript
     * and `stats.fallbacks` on every way the ears can fail, so either one ends the wait — a
     * failure should be reported as a failure, not as a timeout. */
    await page.waitForFunction(() => {
      const s = window.moxieMic && window.moxieMic.stats ? window.moxieMic.stats() : null;
      return !!s && (s.transcripts > 0 || s.fallbacks > 0 || s.tooShort > 0);
    }, { timeout: 45000 }).catch(() => {});
    /* Then the ANSWER, and THIS BLOCK IS WRITTEN THE WAY IT IS BECAUSE THE FIRST PAID RUN
     * AGAINST PRODUCTION (2026-09-05) REPORTED TWO RED CLAUSES THAT WERE MINE, NOT THE
     * SITE'S. It used to be `plays.length > 0` plus a 2.5 s sleep. Against a real gateway
     * that is wrong twice over, and the run's own output says so:
     *
     *   · `plays.length > 0` was satisfied within a second by an AMBIENT QUIP — she speaks
     *     unprompted every 11-24 s, from a pre-rendered clip. Her actual answer was still
     *     being synthesised. That is `test_mic_spend.mjs`'s recorded defect verbatim ("an
     *     ambient quip standing in for Moxie's answer satisfied it"), and it satisfied a
     *     COUNT here for the same reason.
     *   · the recorded `/api/speech` body was 0 B, which looked like a route returning
     *     nothing and was in fact a response that had not arrived: the `fetch` wrapper only
     *     pushes its `text()` promise once the response RESOLVES, so a request still in
     *     flight contributes no promise for `Promise.all(pending)` to wait on. An empty
     *     record read as an empty body.
     *
     * So the wait is now on the two facts that actually end a turn: every watched request
     * has an answer, and a buffer built BY HAND from gateway PCM has been scheduled.
     * `audio.js::decodeCloudTTS` constructs her gateway voice with `createBuffer`, while
     * every pre-rendered clip arrives through `decodeAudioData` — so `bytes == null` names
     * HER VOICE and nothing else, which is the same discrimination `test_mic_spend.mjs`
     * makes from the other side. A wait only ever EXTENDS the window, so the "nothing else
     * was spent" counters get more time to catch a stray request, never less. */
    await page.waitForFunction(
      () => window.__mic.calls.length > 0 && window.__mic.calls.every((c) => c.status !== 0),
      { timeout: 45000 }).catch(() => {});
    if (!opts.dry) {
      await page.waitForFunction(
        () => (window.__mic.plays || []).some((p) => p.bytes == null && p.frames > 1000),
        { timeout: 45000 }).catch(() => {});
    }
    await new Promise((r) => setTimeout(r, 2500));
  }

  const after = await page.evaluate(async () => {
    try { await Promise.all(window.__mic.pending); } catch (e) {}
    return {
      calls: window.__mic.calls, plays: window.__mic.plays,
      upload: window.__mic.upload, uploadBytes: window.__mic.uploadBytes,
      csp: window.__csp || [],
      micStatus: (document.getElementById("mic-status") || {}).textContent || "",
      transcriptText: (document.getElementById("transcript") || {}).textContent || "",
      stats: window.moxieMic && window.moxieMic.stats ? window.moxieMic.stats() : null,
    };
  });

  await page.close();

  /* The transcript, taken from the ROUTE'S OWN ANSWER rather than from `#mic-status` — that
   * element truncates at 40 characters, and a scored overlap over a truncated sentence is a
   * measurement of the truncation. `mic.js::pickTranscript` reads the same field. */
  let transcript = "", chatReply = "", speechBytes = 0;
  for (const c of after.calls) {
    let body = null;
    try { body = JSON.parse(c.body); } catch { body = null; }
    if (/transcribe|transcriptions/.test(c.url) && body && typeof body.transcript === "string")
      transcript = body.transcript.trim();
    if (/\/api\/chat\b/.test(c.url) && body) {
      for (const m of body.messages || []) {
        try {
          const p = JSON.parse(m.payload);
          if (p && p.output && p.output.text) chatReply = String(p.output.text);
        } catch {}
      }
    }
    if (/\/api\/speech\b/.test(c.url)) speechBytes = Math.max(speechBytes, c.bodyLen || 0);
  }

  const spend = {
    transcribe: spent.filter((u) => /transcribe|transcriptions/.test(u)).length,
    chat: spent.filter((u) => /\/api\/chat\b/.test(u)).length,
    speech: spent.filter((u) => /\/api\/speech\b/.test(u)).length,
    refused: refused.length,
    total: spent.length,
  };
  return { url, status: res ? res.status() : 0, before, opened, ...after,
           transcript, chatReply, speechBytes, spend, failed, consoleErrs, refused };
}

/* ---- the assertions, over one probe record ---------------------------------- */
/**
 * @param {*} c        a `makeChecks()` bundle
 * @param {*} p        a `probeTurn()` record
 * @param {string} tag prefix for every message
 * @param {{source: Int16Array, sourceRate: number, decoyPcm: Int16Array, decoyRate: number,
 *          words: boolean}} ctx
 */
function assertHeard(c, p, tag, ctx) {
  const { ok, eq } = c;

  /* ---- clause 1: reachable, without the engineering rail ---- */
  eq(p.status, 200, `${tag}: HTTP status of ${p.url}`);
  ok(p.before.hasHud, `${tag}: #hud exists — is ${p.url} really the SIM page? ` +
     `(title ${JSON.stringify(p.before.title)})`);
  eq(p.before.scrollY, 0, `${tag}: the page must not have scrolled on its own`);
  ok(p.before.railOpen === null || p.before.railOpen === "false",
     `${tag}: the CONTROLS rail must still be closed (aria-expanded=${p.before.railOpen})`);
  for (const [sel, m] of [["#mic-btn", p.before.mic], ["#speech-btn", p.before.say]]) {
    ok(m.found, `${tag}: ${sel} exists`);
    if (!m.found) continue;
    ok(m.sized, `${tag}: ${sel} has a non-zero box — got ${m.w}×${m.h} ` +
                `(display:${m.display} visibility:${m.visibility})`);
    if (!m.sized) continue;
    ok(m.top >= 0 && m.bottom <= p.before.innerH,
       `${tag}: ${sel} is inside the first viewport — y ${m.top}…${m.bottom} of ${p.before.innerH}`);
    ok(m.self, `${tag}: a tap at the centre of ${sel} reaches it — elementFromPoint gave ${m.hit}`);
  }
  ok(!p.before.mic.disabled, `${tag}: #mic-btn is not disabled — this deployment has ears`);
  ok(p.before.target && p.before.target.kind === "cloud",
     `${tag}: the mic is aimed at the same-origin route, not a local sidecar — ` +
     JSON.stringify(p.before.target));

  /* ---- clause 2: the device opened, and audio really left the page ---- */
  ok(p.before.secure, `${tag}: the page is a SECURE CONTEXT — a microphone is unavailable ` +
     `anywhere else, whatever the button says`);
  ok(p.before.hasMediaDevices, `${tag}: navigator.mediaDevices.getUserMedia exists`);
  ok(p.opened, `${tag}: pressing Listen actually OPENED the microphone — mic.js reports ` +
     `isRecording() (status ${JSON.stringify(p.micStatus)})`);
  /* POSTED, not PAID FOR: under `--dry-run` the same request is made and then aborted, so
   * the count that matters here is "the page tried to upload exactly once". The paid clause
   * — that it was allowed through and cost exactly one call — is clause 5's business. */
  const posted = p.spend.transcribe +
                 p.refused.filter((u) => /transcribe|transcriptions/.test(u)).length;
  eq(posted, 1, `${tag}: the page POSTed the clip to /api/transcribe exactly once`);
  ok(!!p.upload, `${tag}: the upload body was captured at all`);
  if (!p.upload) return;
  const wav = Buffer.from(p.upload, "base64");
  eq(wav.slice(0, 4).toString(), "RIFF", `${tag}: the upload is a RIFF file`);
  eq(wav.slice(8, 12).toString(), "WAVE", `${tag}: …a WAVE file`);
  /* Parsed with the SERVER's own reader, so `mic.js::encodeWav` and
   * `functions/api/_lib/wav.js` are pinned by one assertion rather than by two beliefs —
   * the trick `sim/test_wav_decode.mjs` established for the voice. */
  let dur = null;
  try { dur = wavDurationMs(wav); } catch (e) {
    ok(false, `${tag}: the server's own RIFF walker refused the upload — ${(e && e.message) || e}`);
  }
  if (dur) {
    eq(dur.sampleRate, 16000, `${tag}: uploaded at the rate the ears want ` +
       `(mic.js: "the rate that matters is 16000")`);
    eq(dur.channels, 1, `${tag}: mono`);
    eq(dur.bitsPerSample, 16, `${tag}: 16-bit`);
    ok(dur.ms > 1000, `${tag}: the clip is longer than a second — got ${dur.ms} ms`);
    ok(dur.ms < 15500, `${tag}: the clip is inside mic.js's 15 s hard stop — got ${dur.ms} ms`);
  }
  ok(wav.length >= 2000, `${tag}: over min_audio_bytes — ${wav.length} B`);
  ok(wav.length <= 500000, `${tag}: under max_audio_bytes — ${wav.length} B`);

  const got = readWav(wav);
  let peak = 0;
  for (let i = 0; i < got.pcm.length; i++) { const v = Math.abs(got.pcm[i]); if (v > peak) peak = v; }
  peak /= 32768;
  ok(peak > 0.05, `${tag}: the captured audio is AUDIBLE, not a silent buffer — ` +
     `peak ${peak.toFixed(4)}`);

  /* ---- clause 3: it is the audio we played ----
   * TWO CLAUSES WITH DIFFERENT SCOPES, and the split is the lesson from CI run 34013443378
   * (the long note at `envelope` has the table). The ORDERING is environment-invariant —
   * both numbers come out of the same recording on the same machine — so it is asserted
   * everywhere. The MAGNITUDE is not: the runner's own microphone saturates, which halves
   * every score without anything being wrong with this site, so the fidelity floor is
   * asserted only where the audio path is a known quantity and is REPORTED everywhere. */
  const id = score(got.pcm, got.rate, ctx);
  ok(id.vote >= IDENTITY_VOTE,
     `${tag}: the uploaded audio must be the clip the fake microphone played, not the ` +
     `other one — only ${(id.vote * 100).toFixed(0)}% of its ${id.chunks} chunks matched ` +
     `the clip played better than an unrelated one (need ${(IDENTITY_VOTE * 100).toFixed(0)}%). ` +
     `Median scores: ${id.good.toFixed(3)} played vs ${id.bad.toFixed(3)} unrelated.`);
  if (ctx.fidelity) {
    ok(id.good >= FIDELITY_FLOOR,
       `${tag}: …and it must be a FAITHFUL recording of it — chunked log-RMS envelope ` +
       `score ${id.good.toFixed(3)} (floor ${FIDELITY_FLOOR}). A low score here with the ` +
       `vote above still healthy means the audio arrived but degraded: check the capture ` +
       `peak (${peak.toFixed(4)} — 1.0000 means the device saturated).`);
  }
  p.corr = { good: id.good, bad: id.bad, vote: id.vote, chunks: id.chunks, peak,
             wavBytes: wav.length, ms: dur ? dur.ms : 0, fidelity: !!ctx.fidelity };

  /* ---- clause 6, asserted in EVERY mode: a mutation of the sound must not change what the
   * page refuses or logs, and if it does, that is its own finding. Requests this run aborted
   * on purpose are forgiven ONE FOR ONE and no further — the trick `sim/test_typed_turn.mjs`
   * established: forgive exactly as many as we provoked, so a real error still reddens. ---- */
  c.eq(p.csp.length, 0, `${tag}: ZERO securitypolicyviolation events — ` +
       JSON.stringify(p.csp.slice(0, 4)));
  const noise = notable(p.consoleErrs, p.refused.length);
  c.eq(noise.length, 0,
       `${tag}: ZERO console errors (${p.refused.length} forgiven for the request(s) this ` +
       `run aborted on purpose) — ` + JSON.stringify(noise.slice(0, 4)));

  if (!ctx.words) return;                       // no gateway, no words: `--selftest`/`--dry-run`

  /* ---- clause 4: the transcript resembles the words ---- */
  const scored = assertWords(c, p.transcript, { spoken: ctx.spoken, decoy: ctx.decoy, where: tag });
  p.scored = scored;
  eq(p.stats && p.stats.transcripts, 1, `${tag}: mic.js recorded exactly one real transcript`);
  eq(p.stats && p.stats.fallbacks, 0,
     `${tag}: …and burnt NO scripted consolation line (status ${JSON.stringify(p.micStatus)})`);
  const childOnPage = wordOverlap(p.transcript, p.transcriptText);
  ok(childOnPage >= 0.8, `${tag}: the words are on the page, in the comms log — only ` +
     `${childOnPage.toFixed(2)} of the transcript's words are there`);

  /* ---- clause 5: she answered, out loud ---- */
  eq(p.spend.chat, 1, `${tag}: exactly one POST /api/chat — the words a visitor said`);
  eq(p.spend.speech, 1, `${tag}: exactly one POST /api/speech — her own voice`);
  ok(p.chatReply.trim().length > 0, `${tag}: the brain answered with text — ` +
     JSON.stringify(p.chatReply.slice(0, 90)));
  /* Scored rather than matched as a substring. `bridge.js` renders the MARKUP field, which a
   * real brain decorates with behaviour tags the page then strips, so `includes()` would be
   * asserting on the tag stripper rather than on whether her answer reached the log. Half
   * the words is far more than a coincidence and far less than a demand that two different
   * renderings agree character for character. */
  const onPage = wordOverlap(p.chatReply, p.transcriptText);
  ok(onPage >= 0.5, `${tag}: …and her answer is on the page too — only ${onPage.toFixed(2)} ` +
     `of its words are in the comms log`);
  /* HER OWN VOICE, by identity and not by count. `audio.js::decodeCloudTTS` builds the
   * gateway buffer by hand with `createBuffer`; every pre-rendered clip on this page arrives
   * through `decodeAudioData`, which the instrument tags with the byte length it decoded. So
   * `bytes == null` is her, and `bytes != null` is a clip. Counting instead would let an
   * ambient quip stand in for the answer — measured doing exactly that on the first paid run
   * (a 4.10 s 48 kHz decoded clip satisfied a `plays.length > 0` wait while her voice was
   * still being synthesised), which is `test_mic_spend.mjs`'s recorded defect. */
  const heard = p.plays.map((x) => `${(x.frames / (x.rate || 1)).toFixed(2)}s@` +
                `${x.peak.toFixed(2)}/${x.rate}Hz${x.bytes == null ? " (gateway)" : " (clip)"}`)
                .join(" + ") || "SILENCE";
  const voice = p.plays.filter((x) => x.bytes == null && x.peak > 0.05 && x.frames > 1000);
  ok(voice.length > 0, `${tag}: the page SPOKE HER ANSWER — a buffer built from gateway PCM ` +
     `was scheduled, audibly. Heard: ${heard}`);
  const longest = voice.reduce((a, b) => (b.frames / b.rate > a.frames / a.rate ? b : a),
                               voice[0] || { frames: 0, rate: 1, peak: 0 });
  ok(longest.frames / (longest.rate || 1) > 0.4,
     `${tag}: …for a plausible length, not a click — ` +
     `${(longest.frames / (longest.rate || 1)).toFixed(2)}s at ${longest.rate} Hz`);
  ok(p.speechBytes > 5000, `${tag}: /api/speech carried real audio bytes — ` +
     `${p.speechBytes} B of envelope`);

  c.eq(p.spend.refused, 0, `${tag}: nothing was refused by the budget ceiling ` +
       `(${p.spend.total} of ${BUDGET} spent)`);
}

/** A request WE aborted logs a console error the page did nothing wrong to earn. Forgive
 *  exactly `budget` of them and no more, so a real error still reddens clause 6. */
const ABORTED = /Failed to load resource: net::ERR_(BLOCKED_BY_CLIENT|CONNECTION_REFUSED|FAILED)/;
function notable(errs, budget) {
  let left = budget || 0;
  return errs.filter((e) => {
    if (left > 0 && ABORTED.test(e)) { left--; return false; }
    return true;
  });
}

/** One line per measurement, so a run leaves numbers behind rather than a verdict. */
function report(p, tag) {
  const box = (m) => m.found
    ? (m.sized ? `${m.w}×${m.h} at y=${m.top}…${m.bottom} hit=${m.hit}${m.self ? " (self)" : " ⚠ NOT SELF"}`
               : `${m.w}×${m.h} display:${m.display}`)
    : "ABSENT";
  console.log(`\n  ${tag}  ${p.url}`);
  console.log(`    HTTP ${p.status}  viewport ${p.before.innerW}×${p.before.innerH}  scrollY ${p.before.scrollY}` +
              `  rail aria-expanded=${p.before.railOpen}  ears=${p.before.ears}` +
              `  target=${p.before.target ? p.before.target.kind : "?"}  cap=${p.before.maxRecordMs}ms`);
  console.log(`    microphone      secureContext=${p.before.secure}  mediaDevices=${p.before.hasMediaDevices}` +
              `  opened=${p.opened}`);
  console.log(`    #mic-btn        ${box(p.before.mic)}`);
  console.log(`    #speech-btn     ${box(p.before.say)}`);
  if (p.corr)
    console.log(`    uploaded        ${p.corr.wavBytes} B  ${p.corr.ms} ms  peak ${p.corr.peak.toFixed(4)}` +
                `${p.corr.peak > 0.999 ? " (SATURATED)" : ""}` +
                `\n    identity        ${(p.corr.vote * 100).toFixed(0)}% of ${p.corr.chunks} chunks chose the clip played` +
                ` (need ${(IDENTITY_VOTE * 100).toFixed(0)}%)   medians ${p.corr.good.toFixed(3)} played` +
                ` / ${p.corr.bad.toFixed(3)} unrelated   fidelity floor ` +
                `${p.corr.fidelity ? FIDELITY_FLOOR + " ASSERTED" : "reported only"}`);
  else
    console.log(`    uploaded        ${p.uploadBytes} B (not parsed)`);
  console.log(`    transcript      ${JSON.stringify(p.transcript)}` +
              (p.scored ? `   overlap ${p.scored.right.toFixed(2)} decoy ${p.scored.wrong.toFixed(2)}` : ""));
  console.log(`    reply           ${JSON.stringify(p.chatReply.slice(0, 100))}`);
  console.log(`    spoken back     ${p.plays.map((x) => `${(x.frames / (x.rate || 1)).toFixed(2)}s@${x.peak.toFixed(2)}` +
              `/${x.rate}Hz${x.bytes == null ? "(gateway)" : "(clip)"}`).join(" + ") || "SILENCE"}` +
              `   /api/speech ${p.speechBytes} B`);
  console.log(`    mic-status      ${JSON.stringify(p.micStatus)}`);
  console.log(`    mic stats       ${JSON.stringify(p.stats)}`);
  console.log(`    SPEND           transcribe ${p.spend.transcribe}  chat ${p.spend.chat}` +
              `  speech ${p.spend.speech}   (ceiling ${BUDGET}, refused ${p.spend.refused})`);
  console.log(`    CSP violations  ${p.csp.length}${p.csp.length ? "  " + JSON.stringify(p.csp.slice(0, 3)) : ""}` +
              `   console errors ${p.consoleErrs.length}   failed requests ${p.failed.length}`);
  for (const e of p.consoleErrs.slice(0, 5)) console.log(`      · console ${e.slice(0, 150)}`);
  for (const f of p.failed.slice(0, 5)) console.log(`      · failed ${f.url} — ${f.why}`);
}

/* ════════════════════════ the fixtures ════════════════════════════════════════ */
/**
 * The WAVs the fake microphone plays, written to a throwaway directory.
 *
 * @returns {Promise<{dir, spoken:{path,pcm,rate,text}, decoy:{path,pcm,rate,text},
 *                    silence:{path}, golden:{path,pcm,rate}}>}
 */
async function fixtures(puppeteer, chrome) {
  const RATE = 22050;                       // the golden's rate; Chrome resamples on capture
  const dir = mkdtempSync(join(tmpdir(), "moxie-hostedmic-"));

  const overrideWav = (process.env.MOXIE_MIC_WAV || "").trim();
  const overrideText = (process.env.MOXIE_MIC_TEXT || "").trim();
  const decoyRel = manifestClip(DECOY_TEXT);

  let spokenPcm, spokenRate = RATE, spokenText = SPOKEN_TEXT, spokenPath;
  let decoyPcm;

  if (overrideWav) {
    /* Somebody pointed this at a real recording. It still has to clear every structural
     * check and still has to transcribe back at the floor — an override cannot make this
     * pass dishonestly, it can only change WHOSE voice is being proven. */
    if (!overrideText)
      throw new Error("MOXIE_MIC_WAV needs MOXIE_MIC_TEXT — the words in the clip, or " +
                      "there is nothing to score the transcript against");
    const got = readWav(readFileSync(overrideWav));
    spokenPcm = got.pcm; spokenRate = got.rate; spokenText = overrideText;
    spokenPath = join(dir, "spoken.wav");
    writeFileSync(spokenPath, riff(padded(spokenPcm, spokenRate), spokenRate));
    ({ [decoyRel]: decoyPcm } = await decodeClips(puppeteer, chrome, [decoyRel], RATE));
  } else {
    const spokenRel = manifestClip(SPOKEN_TEXT);
    const got = await decodeClips(puppeteer, chrome, [spokenRel, decoyRel], RATE);
    spokenPcm = got[spokenRel]; decoyPcm = got[decoyRel];
    spokenPath = join(dir, "spoken.wav");
    writeFileSync(spokenPath, riff(padded(spokenPcm, RATE), RATE));
  }

  const decoyPath = join(dir, "decoy.wav");
  writeFileSync(decoyPath, riff(padded(decoyPcm, RATE), RATE));
  const silencePath = join(dir, "silence.wav");
  writeFileSync(silencePath, riff(new Int16Array(RATE * 4), RATE));

  const goldenPath = join(repo, "sim", "tests", "goldens", "real_voice_22050_mono.wav");
  const golden = readWav(readFileSync(goldenPath));

  return {
    dir,
    spoken: { path: spokenPath, pcm: spokenPcm, rate: spokenRate, text: spokenText },
    decoy: { path: decoyPath, pcm: decoyPcm, rate: RATE, text: DECOY_TEXT },
    silence: { path: silencePath },
    golden: { path: goldenPath, pcm: golden.pcm, rate: golden.rate },
  };
}

/** A browser whose microphone is `wavPath`. The flags are a LAUNCH property, so one clip
 *  means one browser — which is why `--selftest` starts three. */
function launchWithMic(puppeteer, chrome, wavPath, extraArgs = []) {
  return puppeteer.launch({
    executablePath: chrome, headless: "new",
    args: ["--no-sandbox", "--use-gl=swiftshader", "--enable-unsafe-swiftshader",
           "--autoplay-policy=no-user-gesture-required",
           "--use-fake-device-for-media-stream",
           "--use-fake-ui-for-media-stream",
           `--use-file-for-fake-audio-capture=${wavPath}`,
           ...extraArgs],
  });
}

/* ════════════════════════ selftest: the teeth ═════════════════════════════════ */
/**
 * The same `probeTurn()`, against `sim/web` on loopback with `/api/*` answered at the
 * browser — the fixture shape `sim/test_mic_spend.mjs` established, down to the reason the
 * host is MAPPED rather than `127.0.0.1`: on a loopback origin `env.js` and `audio.js` probe
 * the optional sidecars at :8081/:8082, which `connect-src 'self'` refuses, so a perfectly
 * healthy tree would fire two `securitypolicyviolation` events and clause 6 would go red for
 * a reason that has nothing to do with anything.
 */
async function selftest(puppeteer, chrome, fx) {
  const c = makeChecks();
  let captured = null;                     // what the baseline browser case really uploaded

  /* ---- the paper mutations first: no browser, no clock, no excuses ---- */
  console.log(`\n  the overlap scorer (a port of helpers_audio.py::word_overlap)`);
  const TABLE = [
    [SPOKEN_TEXT, SPOKEN_TEXT, 1],
    [SPOKEN_TEXT, "Happy birthday I hope your day is amazing Happy birthday I hope", 1],
    [SPOKEN_TEXT, "happy birthday! i hope your day is amazing.", 1],
    [SPOKEN_TEXT, "", 0],
    [SPOKEN_TEXT, DECOY_TEXT, 0],
    [DECOY_TEXT, SPOKEN_TEXT, 0],
    ["I'm so happy", "i’m so happy", 1],
  ];
  for (const [ref, hyp, want] of TABLE) {
    const got = wordOverlap(ref, hyp);
    console.log(`    ${got.toFixed(2)}  ${JSON.stringify(ref.slice(0, 30))} vs ` +
                `${JSON.stringify(hyp.slice(0, 46))}`);
    c.ok(Math.abs(got - want) < 1e-9,
         `overlap(${JSON.stringify(ref)}, ${JSON.stringify(hyp)}) = ${got}, want ${want}`);
  }

  /* THE NEGATIVE CONTROL THE BRIEF ASKS FOR: `assertWords` fed a CORRECT transcript of the
   * golden's own sentence, scored against words nobody said. It must FAIL, and its failure
   * messages are printed, because an assertion nobody has watched fail is not an assertion. */
  const m = makeChecks();
  assertWords(m, SPOKEN_TEXT, { spoken: DECOY_TEXT, decoy: SPOKEN_TEXT, where: "negative control" });
  console.log(`\n  negative control — the real sentence scored against words nobody said:`);
  for (const f of m.fails) console.log(`    · ${f.split("\n")[0]}`);
  c.ok(m.fails.length >= 2,
       `the overlap assertion must FAIL on a mismatched reference — it produced ` +
       `${m.fails.length} failure(s), so clause 4 could pass on anything`);
  c.ok(m.fails.some((f) => /recovered only/.test(f)),
       `…specifically the FLOOR clause — got ${JSON.stringify(m.fails)}`);
  c.ok(m.fails.some((f) => /NEVER SPOKEN/.test(f)),
       `…and the DECOY clause — got ${JSON.stringify(m.fails)}`);

  /* ---- the browser mutations ---- */
  const site = await serveWeb({ headers: true });
  const HOST = "moxie.hosted.test";
  const url = `http://${HOST}:${site.port}/sim.html`;

  /* The real `/api/health` envelope, built by the real Function, so this fixture can never
   * drift from what the route answers (the trick `sim/test_env_hosted.mjs` established). */
  const health = await import(join(repo, "functions", "api", "health.js"));
  const envelope = await import(join(repo, "functions", "api", "_lib", "envelope.js"));
  const HEALTH_LIVE = await (await health.onRequestGet({
    env: { DEMO_GATEWAY_BASE_URL: "https://gw.invalid.test/v1",
           DEMO_GATEWAY_API_KEY: "sk-testonly-abcdefghijklmnop",
           DEMO_CHAT_MODEL: "test-brain-model", DEMO_TTS_MODEL: "test-voice-model",
           DEMO_STT_MODEL: "test-ears-model" },
  })).text();

  /* `/api/transcribe` answers the SPOKEN sentence whatever the microphone played. That is
   * deliberate: the selftest is about the AUDIO path and the instruments, not about an ASR
   * it does not have, so the transcript is held constant and the mutations move the sound. */
  const chatBody = JSON.stringify(envelope.envelope({
    ok: true, mode: "live", voice: true, ears: true,
    messages: [{ topic: "/devices/d_sim/commands/remote_chat",
                 payload: JSON.stringify({ command: "remote_chat", result: "SUCCESS",
                   backend: "router", event_id: "sim-hostedmic",
                   output: { text: "What a lovely thing to say!", markup: "What a lovely thing to say!" },
                   end_turn: false }) }],
    speech: [{ ticket: "v1.SELFTEST.MAC", event_id: "sim-hostedmic", chunk_num: 0 }],
    context: "v1.CTX.MAC",
  }));
  const tone = (() => {
    const n = Math.floor(0.6 * 22050), b = Buffer.alloc(n * 2);
    for (let i = 0; i < n; i++) b.writeInt16LE(Math.round(Math.sin(2 * Math.PI * 440 * i / 22050) * 0.8 * 32767), i * 2);
    return b.toString("base64");
  })();
  const speechBody = JSON.stringify(envelope.envelope({
    ok: true, mode: "live", voice: true, ears: true,
    messages: [{ topic: "/devices/d_sim/commands/tts",
                 payload: JSON.stringify({ request_source: "ROBOT_TTS_REQUEST",
                   audio: { buffer: tone, channels: 1, sample_rate: 22050 },
                   marks: [], event_id: "sim-hostedmic", chunk_num: 0 }) }],
  }));
  const stub = (r) => {
    const u = r.url();
    if (/\/api\/health\b/.test(u)) {
      r.respond({ status: 200, contentType: "application/json", body: HEALTH_LIVE }); return true;
    }
    if (/\/api\/transcribe\b/.test(u)) {
      r.respond({ status: 200, contentType: "application/json",
                  body: JSON.stringify(envelope.envelope({ ok: true, mode: "live", voice: true,
                    ears: true, transcript: SPOKEN_TEXT })) });
      return true;
    }
    if (/\/api\/chat\b/.test(u)) {
      r.respond({ status: 200, contentType: "application/json", body: chatBody }); return true;
    }
    if (/\/api\/speech\b/.test(u)) {
      r.respond({ status: 200, contentType: "application/json", body: speechBody }); return true;
    }
    if (/:808[12]\//.test(u)) { r.abort("connectionrefused"); return true; }
    return false;
  };

  const CASES = [
    ["baseline · the sentence clip", fx.spoken.path, null],
    ["mutation A · digital silence", fx.silence.path, /AUDIBLE, not a silent buffer/],
    ["mutation B · a DIFFERENT clip through the microphone", fx.decoy.path,
     /must be the clip the fake microphone played/],
    ["control C · the committed golden (0.75 s of the same sentence)", fx.golden.path, null],
  ];

  try {
    for (const [name, wav, wanted] of CASES) {
      const browser = await launchWithMic(puppeteer, chrome, wav,
        [`--host-resolver-rules=MAP ${HOST} 127.0.0.1:${site.port}`,
         /* See `probeTurn`'s `secure` note: Chrome grants secure-context by HOSTNAME, and a
          * `.test` name pointing at loopback does not qualify — so `navigator.mediaDevices`
          * would be `undefined` and every case here would fail for a reason that has
          * nothing to do with the site. A real deployment is https and gets neither flag.
          * `--use-fake-device-for-media-stream` is already a "this browser is a test rig"
          * declaration; this is the same declaration for the origin. */
         `--unsafely-treat-insecure-origin-as-secure=http://${HOST}:${site.port}`]);
      const mm = makeChecks();
      try {
        /* The golden is 0.75 s, so its capture window can be short; the others loop a ~4 s
         * file and need more than two periods (see the header on phase). */
        const recordMs = wav === fx.golden.path ? 4000 : 8600;
        const p = await probeTurn(browser, url, { recordMs, budget: BUDGET, stub });
        /* `words: true` even though there is no ASR here, and it is not a pretence. The stub
         * holds the TRANSCRIPT constant while the mutations move the SOUND, so clause 4
         * proves the plumbing it can prove — the route's own answer is what gets scored (not
         * the 40-character `#mic-status` line), it reaches the comms log, and the floor and
         * the decoy ceiling are both computed — and clause 5 is exercised in full, which is
         * the half the first paid run got wrong. Clauses 2 and 3 are the ones the mutations
         * are aimed at, and they are the ones that move. */
        const ctx = {
          source: wav === fx.golden.path ? fx.golden.pcm : fx.spoken.pcm,
          sourceRate: wav === fx.golden.path ? fx.golden.rate : fx.spoken.rate,
          decoyPcm: fx.decoy.pcm, decoyRate: fx.decoy.rate,
          spoken: SPOKEN_TEXT, decoy: DECOY_TEXT, words: true,
          /* NOT asserted here, and the reason is the whole point of the rewrite: a CI
           * runner's microphone saturates, which halves every fidelity score while the site
           * is perfectly healthy. The ORDERING clause still runs, and mutation B is still
           * its teeth. See `assertHeard` clause 3 and the table at `envelope`. */
          fidelity: false,
        };
        assertHeard(mm, p, name.split(" ·")[0], ctx);
        report(p, name);
        // The baseline's real upload is the gauntlet's input: degrading audio that a
        // browser actually captured is worth more than degrading the fixture, because it
        // already carries whatever this machine's capture path did to it.
        if (!wanted && wav !== fx.golden.path && p.upload) captured = readWav(Buffer.from(p.upload, "base64"));
      } finally {
        try { await browser.close(); } catch {}
      }
      console.log(`    → fired: ${mm.fails.length ? mm.fails.map((f) => "· " + f.split("\n")[0]).join("\n              ") : "NOTHING"}`);

      if (!wanted) {
        // The control, and it is not ceremony: a mutation test whose baseline does not pass
        // proves nothing whatsoever about the mutations underneath it.
        c.ok(mm.fails.length === 0,
             `${name} must pass every clause — ${mm.fails.length} failure(s): ` +
             JSON.stringify(mm.fails.map((f) => f.split("\n")[0])));
      } else {
        c.ok(mm.fails.length > 0, `${name} must make the check FAIL — it passed`);
        c.ok(mm.fails.some((f) => wanted.test(f)),
             `${name} must fire the ${wanted} clause specifically — ` +
             JSON.stringify(mm.fails.map((f) => f.split("\n")[0])));
      }
    }
  } finally {
    site.close();
  }

  /* The gauntlet last, over the audio the baseline case really uploaded. It is what keeps
   * the correlation clause honest on a machine this suite has never run on — see its
   * header, and CI run 34013443378 for why it exists. */
  if (captured) {
    gauntlet(c, captured.pcm, captured.rate,
             { source: fx.spoken.pcm, sourceRate: fx.spoken.rate,
               decoyPcm: fx.decoy.pcm, decoyRate: fx.decoy.rate });
  } else {
    c.ok(false, "the baseline case produced no upload, so the degradation gauntlet ran on " +
                "nothing — that is a failure, not a skip");
  }
  return c;
}

/* ═════════════════════════════════ main ═══════════════════════════════════════ */
const { puppeteer, chrome } = await requireBrowser(LABEL);
const fx = await fixtures(puppeteer, chrome);
console.log(`\n${LABEL}: the microphone will play` +
            `\n  spoken  ${JSON.stringify(fx.spoken.text)}` +
            `\n          ${fx.spoken.path}  ${(fx.spoken.pcm.length / fx.spoken.rate).toFixed(2)}s @ ${fx.spoken.rate} Hz` +
            `\n  decoy   ${JSON.stringify(fx.decoy.text)} (scored against the same transcript)`);

if (SELFTEST) {
  const c = await selftest(puppeteer, chrome, fx);
  finish(LABEL + " (selftest)", c);
}

const target = cliUrl || process.env.MOXIE_DEPLOYED_URL ||
               (canonicalOrigin() ? canonicalOrigin() + "/sim" : null);
if (!target) {
  // Not a skip: being unable to work out WHAT to check is a defect in the invocation, and a
  // silent exit(0) here would be the "green while asserting nothing" shape this repo has
  // been bitten by twice (browser_harness.mjs::skipper records both).
  console.error(`❌ ${LABEL}: no target. Pass a URL, set MOXIE_DEPLOYED_URL, or restore the ` +
                `<link rel="canonical"> in sim/web/index.html.`);
  process.exit(1);
}

console.log(DRY
  ? `\n  --dry-run: /api/chat, /api/speech and /api/transcribe are ABORTED at the browser, ` +
    `so this run costs ${target} NOTHING. Clauses 1-3 and 6 still hold; the words do not.`
  : `\n  ⚠ THIS RUN SPENDS REAL MONEY on ${target} — ceiling ${BUDGET} gateway ` +
    `requests, enforced at the browser.`);
const browser = await launchWithMic(puppeteer, chrome, fx.spoken.path);
try {
  /* The prompt is auto-accepted by `--use-fake-ui-for-media-stream`; this grant means it is
   * never asked in the first place. Two mechanisms because they fail differently, and a
   * refused microphone here would look exactly like a broken page. */
  try {
    await browser.defaultBrowserContext()
                 .overridePermissions(new URL(target).origin, ["microphone"]);
  } catch (e) { console.log(`    (CDP permission grant skipped: ${(e && e.message) || e})`); }

  const c = makeChecks();
  const p = await probeTurn(browser, target, { recordMs: 8600, budget: BUDGET, dry: DRY });
  /* Asserted BEFORE it is reported, and the order is load-bearing: `assertHeard` is what
   * computes the correlation and the overlap, and it hangs them on the record so `report`
   * can print them. Reporting first prints "(not parsed)" over a perfectly good run —
   * measured, on the first dry run against production. */
  assertHeard(c, p, DRY ? "dry run" : "deployed", {
    source: fx.spoken.pcm, sourceRate: fx.spoken.rate,
    decoyPcm: fx.decoy.pcm, decoyRate: fx.decoy.rate,
    spoken: fx.spoken.text, decoy: fx.decoy.text, words: !DRY,
    /* ASSERTED here. A developer's box and a real deployment are audio paths somebody can
     * look at, so a degraded recording is a finding rather than a runner quirk. */
    fidelity: true,
  });
  report(p, DRY ? "deployed (dry run)" : "deployed");
  console.log(`\n  SPENT: ${p.spend.transcribe} STT + ${p.spend.chat} chat + ` +
              `${p.spend.speech} TTS = ${p.spend.total} gateway request(s) of ${BUDGET}` +
              `${DRY ? `  (dry run — ${p.refused.length} aborted before they could cost anything)` : ""}.`);
  await browser.close();
  finish(LABEL + (DRY ? " (dry run)" : ""), c);
} catch (err) {
  try { await browser.close(); } catch {}
  // A network failure against a real deployment is a RESULT, not an excuse: the clean skip
  // in browser_harness.mjs is reserved for "this machine has no browser".
  console.error(`❌ ${LABEL}: ${err && err.stack ? err.stack : err}`);
  process.exit(1);
}
