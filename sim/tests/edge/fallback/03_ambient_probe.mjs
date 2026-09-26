/* §7–8: the degraded line driven for real through `ambient.js`, and when `audio.js`'s
 * 1.4 s Piper probe may fire — watched on the wire, not grepped.
 */
import {
  FakeCustomEvent, ambient, ambientSrc, audioSrc, degradedText, eq, manifest, notes, ok,
  withGlobals,
} from "./harness.mjs";

/* --------------------------------------------------------------------------- *
 * 7. §6.2 row 3 — the degraded line, driven for real
 *
 * The real `ambient.js` is loaded under a stubbed window and a stub mode machine is walked
 * through boot -> degraded -> live -> degraded. What is asserted is what came OUT of
 * `moxieAudio.speak`, not that the file contains a function with a promising name.
 * --------------------------------------------------------------------------- */
{
  ok(!!ambient.degraded, "ambient.json must carry a `degraded` entry (§6.2 row 3)");
  ok(degradedText.length > 0, "the degraded line must have text");
  // Not in the random bag, or Moxie announces a dead cloud as a quip at a healthy moment.
  ok(!ambient.lines.some((l) => (l.text || "").trim() === degradedText),
     "the degraded line must NOT also be in ambient.json's lines[] — it would become a random quip");
  // Its clip is in `moxie` on purpose: `playClip` never falls back to `ambient`.
  ok(!!(manifest.moxie || {})[degradedText],
     "the degraded line's clip must be in the manifest's `moxie` group");
  ok(!(manifest.ambient || {})[degradedText],
     "the degraded line must not also sit in the `ambient` group — one line, one clip");
  // The presentation it asks for must be one ambient.js can play.
  const FACES = new Set(["sleep", "neutral", "happy", "sad", "surprised", "thinking", "blink"]);
  const GESTURES = new Set(["wave", "raiseBoth", "shrug", "leanIn", "tilt", "point", "peek", "slump"]);
  const d = ambient.degraded || {};
  ok(!d.face || FACES.has(d.face), `the degraded line has an unknown face: ${d.face}`);
  ok(!d.gesture || GESTURES.has(d.gesture), `the degraded line has an unknown gesture: ${d.gesture}`);
  ok(!d.heart || /^#[0-9a-fA-F]{6}$/.test(d.heart), `the degraded line has a bad heart colour: ${d.heart}`);
}

/** A stub browser just big enough to run the real `ambient.js`. Returns what it heard. */
function runAmbient(script) {
  return withGlobals(["window", "document", "fetch", "CustomEvent"], (g) => ambientRig(g, script));
}
async function ambientRig(g, script) {
  const said = [];             // [text, group] handed to moxieAudio.speak
  const bubbles = [];          // setSpeech
  const faces = [];
  const winListeners = {};
  const docListeners = {};
  const modeListeners = [];
  let snap = { state: "boot" };
  const idle = { checked: script.liveness !== false, addEventListener() {} };

  const fire = (reg, ev) => (reg[ev] || []).slice().forEach((fn) => { try { fn({ type: ev }); } catch {} });

  g.CustomEvent = FakeCustomEvent;
  g.window = {
    addEventListener: (ev, cb) => { (winListeners[ev] ||= []).push(cb); },
    removeEventListener: () => {},
    moxie: {
      setFace: (f) => faces.push(f), setHeartLED() {}, showIcons() {},
      setMotor() {}, setSpeech: (t) => bubbles.push(t), centerAll() {},
    },
    moxieAudio: {
      speak: (t, group) => { said.push([t, group]); return Promise.resolve(true); },
      isUnlocked: () => script.unlocked !== false,
    },
    moxieMode: {
      state: () => snap.state,
      onChange(fn) {
        modeListeners.push(fn);
        try { fn(snap); } catch {}
        return () => { const i = modeListeners.indexOf(fn); if (i !== -1) modeListeners.splice(i, 1); };
      },
    },
  };
  g.document = {
    hidden: !!script.hidden,
    getElementById: (id) => (id === "idle-on" ? idle : null),
    addEventListener: (ev, cb) => { (docListeners[ev] ||= []).push(cb); },
  };
  g.fetch = (url) => (String(url) === "ambient.json"
    ? Promise.resolve({ ok: true, json: () => Promise.resolve(ambient) })
    : Promise.resolve({ ok: false, json: () => Promise.resolve(null) }));

  new Function(ambientSrc)();
  const api = g.window.moxieAmbient;
  const settle = () => new Promise((r) => setTimeout(r, 0));
  const t = {
    said, bubbles, faces, api,
    heard: () => said.filter((s) => s[0] === degradedText),
    setMode: async (state) => {
      snap = { state };
      modeListeners.slice().forEach((fn) => { try { fn(snap); } catch {} });
      await settle(); await settle();
    },
    unlock: async () => {
      g.window.moxieAudio.isUnlocked = () => true;
      fire(winListeners, "moxie-audio-unlocked");
      await settle(); await settle();
    },
    show: async () => {
      g.document.hidden = false;
      fire(docListeners, "visibilitychange");
      await settle(); await settle();
    },
    state: () => api.degradedState(),
  };
  await settle();
  await script.run(t);
  try { api.stop(); } catch {}                       // release ambient.js's own timers
  return t;
}

{
  // (a) boot -> degraded says it, once, in the `moxie` group, with the right face — and
  //     (b) never again: not on a recovery, not on a second failure, not ever.
  await runAmbient({ run: async (t) => {
    await t.setMode("degraded");
    eq(t.heard().length, 1, "entering `degraded` must say the degraded line exactly once");
    eq(t.heard().length ? t.heard()[0][1] : null, "moxie",
       "the degraded line must be spoken from the `moxie` clip group (playClip never falls back to `ambient`)");
    ok(t.bubbles.includes(degradedText), "…and it must reach the speech bubble, not only the speakers");
    ok(t.faces.includes((ambient.degraded || {}).face), "…with the face ambient.json asked for");
    eq(t.state().said, true, "the once-only latch must be set after it is spoken");

    await t.setMode("live");
    await t.setMode("degraded");
    await t.setMode("degraded");
    eq(t.heard().length, 1,
       "the degraded line must NEVER be said a second time (§6.2: 'spoken once … never repeated')");
  }});

  // (c) `offline` must stay byte-identical to today's page: no new line, ever. This is
  //     §6.3's promise that a fork with no Functions cannot be regressed by any of this.
  await runAmbient({ run: async (t) => {
    await t.setMode("offline");
    await t.setMode("offline");
    eq(t.heard().length, 0,
       "`offline` must NOT say the degraded line — §6.3 promises that page is unchanged");
  }});

  // (d) autoplay still locked: armed, not lost, and it lands on the unlock.
  await runAmbient({ unlocked: false, run: async (t) => {
    await t.setMode("degraded");
    eq(t.heard().length, 0, "nothing may be spoken while the browser's autoplay lock is still on");
    eq(t.state().pending, true, "…but the line must be ARMED rather than dropped");
    await t.unlock();
    eq(t.heard().length, 1, "the armed degraded line must land the moment audio is unlocked");
  }});

  // (e) a hidden tab is not talked at, and the line survives until it is looked at.
  await runAmbient({ hidden: true, run: async (t) => {
    await t.setMode("degraded");
    eq(t.heard().length, 0, "a hidden tab must not be spoken to");
    eq(t.state().pending, true, "…and the line must still be armed");
    await t.show();
    eq(t.heard().length, 1, "the armed line must land when the tab becomes visible");
  }});

  // (f) the liveness toggle is respected — unticking it means "stop talking to yourself".
  await runAmbient({ liveness: false, run: async (t) => {
    await t.setMode("degraded");
    eq(t.heard().length, 0, "a visitor who unticked liveness has asked for quiet, degraded or not");
  }});
}

/* --------------------------------------------------------------------------- *
 * 8. When the 1.4 s Piper probe fires, and when it must not
 *
 * The real `audio.js` is loaded under a stubbed window and asked to speak a line with no
 * clip. What is asserted is whether a request to the sidecar port actually left the page.
 *   1. §6.2 row 4 — skip in `degraded`: that deployment has Functions and no sidecar, so the
 *      1.4 s wait is dead air.
 *   2. Skip on any host from which a localhost port cannot be reached: from a public origin
 *      the request violates the site's own CSP (`connect-src 'self'`) and Chrome logs it.
 *      The hostname decides only whether a sidecar could be reachable, never WHICH voice.
 * --------------------------------------------------------------------------- */
function probeFired(modeState, opts = {}) {
  return withGlobals(["window", "document", "localStorage", "location", "fetch",
                      "CustomEvent", "requestAnimationFrame", "cancelAnimationFrame"],
                     (g) => probeRig(g, modeState, opts));
}
async function probeRig(g, modeState, opts) {
  const urls = [];

  g.CustomEvent = FakeCustomEvent;
  g.requestAnimationFrame = () => 0;
  g.cancelAnimationFrame = () => {};
  g.window = {
    addEventListener() {}, removeEventListener() {}, dispatchEvent: () => true,
    moxieMode: modeState ? { state: () => modeState } : undefined,
  };
  g.document = { getElementById: () => null, body: { classList: { toggle() {} } } };
  g.localStorage = {
    getItem: (k) => (k === "moxie.ttsBase" ? (opts.ttsBase || null) : null),
    setItem() {},
  };
  g.location = { protocol: "https:", hostname: opts.hostname || "127.0.0.1" };
  g.fetch = (url) => {
    urls.push(String(url));
    if (String(url).endsWith("audio/index.json"))
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ moxie: {}, child: {}, ambient: {} }) });
    return Promise.reject(new Error("nothing is listening"));
  };

  new Function(audioSrc)();
  await g.window.moxieAudio.speak("a line that no manifest anywhere has a clip for");
  return urls.some((u) => u.includes(":8081"));
}

{
  // --- rule 1: on a host where a sidecar COULD be reachable, the mode decides. ---
  eq(await probeFired("degraded"), false,
     "`degraded` must go clip -> browser voice DIRECTLY: the 1.4 s probe is dead air on a deployment " +
     "that answered /api/health and therefore has no Piper sidecar (§6.2 row 4)");
  eq(await probeFired("offline"), true,
     "`offline` must KEEP the probe — that is exactly what a self-hoster running sim/serve.py gets, " +
     "and their local Piper on :8081 is the whole reason it exists");
  eq(await probeFired("live"), true,
     "a LOCAL `live` page keeps the probe — that path is only reached when the gateway voice " +
     "did not arrive, and a sidecar on this machine really could answer");
  eq(await probeFired(null), true,
     "with no mode machine at all (audio.js loaded standalone) the probe must still run");
  eq(await probeFired("degraded", { ttsBase: "http://127.0.0.1:8081" }), true,
     "an explicit moxie.ttsBase beats the mode — somebody who typed a TTS address asked for the probe");
  for (const h of ["localhost", "192.168.1.40", "10.0.0.9", "moxie.local"])
    eq(await probeFired("offline", { hostname: h }), true,
       `a self-hoster on ${h} still probes — a LAN address is a host a sidecar can live on`);

  // --- rule 2: on a public origin it can never work, whatever the mode says. ---
  for (const state of ["live", "offline", "degraded", null])
    eq(await probeFired(state, { hostname: "moxie.example" }), false,
       `a page served from a public origin must NOT probe :8081 in \`${state}\` — the request ` +
       "cannot succeed and this site's own connect-src 'self' logs it as a violation");
  eq(await probeFired("offline", { hostname: "moxie.example", ttsBase: "https://moxie.example:8081" }), false,
     "…and not even an explicit moxie.ttsBase can arm it there: the address is unreachable either way, " +
     "so honouring it would only trade a wasted request for a console error");

  ok(/skipProbe/.test(audioSrc) && /moxieMode/.test(audioSrc),
     "the mode half of the skip must still be gated on window.moxieMode, not on a hostname regex");
  ok(/pageCouldReachSidecar/.test(audioSrc),
     "…and the host half must be a named, documented predicate, not an inline test");
}


notes.push(`degraded line: ${JSON.stringify(degradedText)} — once on entering degraded, never repeated`);
notes.push("piper probe: local/LAN host — skipped in `degraded`, kept in `offline`, `live`, standalone\n               and with an explicit ttsBase; public origin — never, in any mode (connect-src 'self')");
