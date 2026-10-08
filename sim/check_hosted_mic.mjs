/* check_hosted_mic.mjs — put a REAL VOICE through the microphone on a REAL DEPLOYMENT, and
 * read back what the site heard, what it answered, and what it said out loud.
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
 * The complement of `check_deployed.mjs`, which never spends: this is the one question no
 * free check answers — `getUserMedia`, `mic.js::wavCapture`/`encodeWav` and the real ears,
 * through the button a visitor presses. Chrome plays a WAV into a fake capture device.
 * A healthy run costs 3 gateway calls (STT + chat + TTS); `MOXIE_MIC_BUDGET` is a request
 * interceptor, not a promise. Not a `test_*.mjs` (it spends and needs a deployment): the fast
 * tier runs `--selftest`, `deployed.yml` runs the paid run only on `workflow_dispatch`.
 *
 * `--selftest` proves the teeth without a gateway: the baseline clip must pass every clause
 * and digital silence must never leave the page (`mic.js` drops a clip with no speech in it,
 * so the upload clause reddens); the overlap scorer, a negative control for
 * clause 4, the scorer proof and the degradation gauntlet run over committed bytes. It does
 * NOT prove capture fidelity (a CI microphone saturates) or anything about the gateway.
 * The maths lives in `sim/tests/hosted_mic/score.mjs`, the browser half in `probe.mjs`.
 */
import { requireBrowser, serveWeb, makeChecks, finish, pcmToneBase64, liveFixture,
         deployedTarget } from "./browser_harness.mjs";
import { wordOverlap, assertWords, scorerProof, gauntlet, loopTo } from "./tests/hosted_mic/score.mjs";
import { SPOKEN_TEXT, DECOY_TEXT, fixtures, launchWithMic, probeTurn, assertHeard, report }
  from "./tests/hosted_mic/probe.mjs";

const LABEL = "hosted-mic check";
const argv = process.argv.slice(2);
const SELFTEST = argv.includes("--selftest");
/** `--dry-run`: the spending routes are ABORTED, yet clauses 1-3 hold because the upload is
 *  read off `fetch` before it may leave. Run it before every paid run. */
const DRY = argv.includes("--dry-run");
const cliUrl = argv.find((a) => !a.startsWith("-"));

/** The hard ceiling on gateway-backed requests. Enforced by the interceptor, not by hope. */
const BUDGET = Math.max(1, Number(process.env.MOXIE_MIC_BUDGET || 5) || 5);

/** The recording runs for more than twice the clip, so one full pass is inside the window
 *  whatever phase the looping fake device starts on. */
const RECORD_MS = 8600;

/* ════════════════════════ selftest: the teeth ═════════════════════════════════ */
async function selftest(puppeteer, chrome, fx) {
  const c = makeChecks();

  /* ---- the paper mutations first: no browser, no clock ---- */
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

  // NEGATIVE CONTROL: a correct transcript scored against words nobody said must FAIL.
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

  /* ---- the browser cases, `/api/*` answered at the browser ----
   * A MAPPED `.test` host, not 127.0.0.1: on loopback env.js/voice/ probe the :8081/:8082
   * sidecars, the CSP refuses them, and clause 6 would redden a healthy tree. `/api/transcribe`
   * always answers the SPOKEN sentence — the mutations move the SOUND, not the words. */
  const site = await serveWeb({ headers: true });
  const HOST = "moxie.hosted.test";
  const url = `http://${HOST}:${site.port}/sim.html`;
  const FX = await liveFixture({ eid: "sim-hostedmic", reply: "What a lovely thing to say!",
                                 tone: pcmToneBase64({ seconds: 0.6 }), ticket: "v1.SELFTEST.MAC" });
  const json = (body) => ({ status: 200, contentType: "application/json", body });
  const ANSWERS = [
    [/\/api\/health\b/, FX.health],
    [/\/api\/transcribe\b/, FX.env({ transcript: SPOKEN_TEXT })],
    [/\/api\/chat\b/, FX.chat],
    [/\/api\/speech\b/, FX.speech],
  ];
  const stub = (r) => {
    const u = r.url();
    const hit = ANSWERS.find(([re]) => re.test(u));
    if (hit) { r.respond(json(hit[1])); return true; }
    if (/:808[12]\//.test(u)) { r.abort("connectionrefused"); return true; }
    return false;
  };

  /* Identity over a browser capture no longer gates a push (a runner's capture once voted
   * 71 % for the wrong clip); `scorerProof` does that on committed bytes. What needs a
   * browser: the baseline must pass, and silence — a binary tooth — must redden. Since
   * `mic.js` drops a clip with no speech in it unsent, silence reddens at the upload itself
   * (it used to be uploaded and redden AUDIBLE), and `also` pins WHY nothing was posted. */
  const CASES = [
    ["baseline · the sentence clip", fx.spoken.path, null, null],
    ["mutation A · digital silence", fx.silence.path, /POSTed the clip to \/api\/transcribe exactly once/,
     (p) => !!p.stats && p.stats.noSpeech > 0 && p.stats.posts === 0],
  ];

  try {
    for (const [name, wav, wanted, also] of CASES) {
      const browser = await launchWithMic(puppeteer, chrome, wav,
        [`--unsafely-treat-insecure-origin-as-secure=http://${HOST}:${site.port}`],
        { [HOST]: site.port });
      const mm = makeChecks();
      let p = null;
      try {
        p = await probeTurn(browser, url, { recordMs: RECORD_MS, budget: BUDGET, stub });
        /* `words: true` with no ASR: the transcript is held constant, so clause 4 proves the
         * plumbing (route answer scored, reaches the log) and clause 5 runs in full.
         * `fidelity: false`: a CI microphone saturates. */
        assertHeard(mm, p, name.split(" ·")[0], {
          source: fx.spoken.pcm, sourceRate: fx.spoken.rate,
          decoyPcm: fx.decoy.pcm, decoyRate: fx.decoy.rate,
          spoken: SPOKEN_TEXT, decoy: DECOY_TEXT, words: true, fidelity: false,
        });
        report(p, name);
      } finally {
        try { await browser.close(); } catch {}
      }
      console.log(`    → fired: ${mm.fails.length ? mm.fails.map((f) => "· " + f.split("\n")[0]).join("\n              ") : "NOTHING"}`);

      if (!wanted) {
        // The control: a baseline that does not pass proves nothing about the mutations.
        c.ok(mm.fails.length === 0,
             `${name} must pass every clause — ${mm.fails.length} failure(s): ` +
             JSON.stringify(mm.fails.map((f) => f.split("\n")[0])));
      } else {
        c.ok(mm.fails.length > 0, `${name} must make the check FAIL — it passed`);
        c.ok(mm.fails.some((f) => wanted.test(f)),
             `${name} must fire the ${wanted} clause specifically — ` +
             JSON.stringify(mm.fails.map((f) => f.split("\n")[0])));
      }
      if (also) {
        c.ok(!!p && also(p), `${name}: mic.js must DROP the silent clip unsent (noSpeech), ` +
             `not fail to capture it — stats ${JSON.stringify(p && p.stats)}`);
      }
    }
  } finally {
    site.close();
  }

  // The deterministic halves, over the FIXTURE: identical on every machine.
  scorerProof(c, fx);
  gauntlet(c, loopTo(fx.spoken.pcm, fx.spoken.rate, 10), fx.spoken.rate,
           { source: fx.spoken.pcm, sourceRate: fx.spoken.rate,
             decoyPcm: fx.decoy.pcm, decoyRate: fx.decoy.rate });
  return c;
}

/* ═════════════════════════════════ main ═══════════════════════════════════════ */
const { puppeteer, chrome } = await requireBrowser(LABEL);
const fx = await fixtures(puppeteer, chrome);
console.log(`\n${LABEL}: the microphone will play` +
            `\n  spoken  ${JSON.stringify(fx.spoken.text)}` +
            `\n          ${fx.spoken.path}  ${(fx.spoken.pcm.length / fx.spoken.rate).toFixed(2)}s @ ${fx.spoken.rate} Hz` +
            `\n  decoy   ${JSON.stringify(fx.decoy.text)} (scored against the same transcript)`);

if (SELFTEST) finish(LABEL + " (selftest)", await selftest(puppeteer, chrome, fx));

const target = deployedTarget(cliUrl, LABEL);
console.log(DRY
  ? `\n  --dry-run: /api/chat, /api/speech and /api/transcribe are ABORTED at the browser, ` +
    `so this run costs ${target} NOTHING. Clauses 1-3 and 6 still hold; the words do not.`
  : `\n  ⚠ THIS RUN SPENDS REAL MONEY on ${target} — ceiling ${BUDGET} gateway ` +
    `requests, enforced at the browser.`);
const browser = await launchWithMic(puppeteer, chrome, fx.spoken.path);
try {
  // Belt and braces with the fake-UI flag: the CDP grant means the prompt is never asked.
  try {
    await browser.defaultBrowserContext()
                 .overridePermissions(new URL(target).origin, ["microphone"]);
  } catch (e) { console.log(`    (CDP permission grant skipped: ${(e && e.message) || e})`); }

  const c = makeChecks();
  const p = await probeTurn(browser, target, { recordMs: RECORD_MS, budget: BUDGET, dry: DRY });
  // Asserted BEFORE reported: `assertHeard` computes the scores `report` prints.
  assertHeard(c, p, DRY ? "dry run" : "deployed", {
    source: fx.spoken.pcm, sourceRate: fx.spoken.rate,
    decoyPcm: fx.decoy.pcm, decoyRate: fx.decoy.rate,
    spoken: fx.spoken.text, decoy: fx.decoy.text, words: !DRY,
    // A real deployment or a developer's box: a degraded capture is a finding here.
    identity: true, fidelity: true,
  });
  report(p, DRY ? "deployed (dry run)" : "deployed");
  console.log(`\n  SPENT: ${p.spend.transcribe} STT + ${p.spend.chat} chat + ` +
              `${p.spend.speech} TTS = ${p.spend.total} gateway request(s) of ${BUDGET}` +
              `${DRY ? `  (dry run — ${p.refused.length} aborted before they could cost anything)` : ""}.`);
  await browser.close();
  finish(LABEL + (DRY ? " (dry run)" : ""), c);
} catch (err) {
  try { await browser.close(); } catch {}
  // A network failure against a real deployment is a RESULT, not a skip.
  console.error(`❌ ${LABEL}: ${err && err.stack ? err.stack : err}`);
  process.exit(1);
}
