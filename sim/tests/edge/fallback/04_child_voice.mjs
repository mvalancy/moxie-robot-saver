/* §8b–9: the CHILD's voice is clip-or-nothing (driven through the real `voice/`), end to end
 * on the real assets through the real `bridge/`, and the renderer cannot drop a manifest group.
 */
import { BRIDGE_SRC, VOICE_SRC } from "../../../bridge_harness.mjs";
import {
  FakeCustomEvent, audioSrc, eq, existsSync, fakeWebAudio, here, join, manifest, notes, ok,
  readFileSync, sessionsDir, web, withGlobals,
} from "./harness.mjs";

/* --------------------------------------------------------------------------- *
 * 8b. The CHILD's voice — clip, or nothing, driven for real
 *
 * The same handler carries a visitor's own typed or spoken words, and `speak()` guarantees
 * sound (clip -> Piper -> browser voice), which would read them back in a stranger's voice.
 * `speakClipOnly` has no route to a synthesizer. Proven on the REAL `voice/`: which URLs
 * were fetched, which buffers STARTED/STOPPED, whether the mouth moved or speechSynthesis ran.
 * --------------------------------------------------------------------------- */

/** Boot the real voice/ against a fake Web Audio stack and report what it did. */
const RIG_GLOBALS = ["window", "document", "localStorage", "location", "fetch", "CustomEvent",
                     "requestAnimationFrame", "cancelAnimationFrame", "AudioContext", "SpeechSynthesisUtterance"];
async function voiceRig(run, manifestOverride) {
  const log = { urls: [], started: [], stopped: [], mouth: [], synthesized: [] };
  await withGlobals(RIG_GLOBALS, (g) => voiceRigIn(g, log, run, manifestOverride));
  return log;
}
async function voiceRigIn(g, log, run, manifestOverride) {
  // Each URL gets its own byteLength, so a decoded buffer traces back to its file.
  const byLen = new Map();
  let nextLen = 64;
  const bufFor = (url) => {
    if (![...byLen.entries()].some(([, u]) => u === url)) { byLen.set((nextLen += 8), url); }
    const len = [...byLen.entries()].find(([, u]) => u === url)[0];
    return new ArrayBuffer(len);
  };

  const Ctx = fakeWebAudio(log, byLen);

  // `pump()`'s next frame is dropped, so a clip that drives the mouth records exactly one
  // sample and one that does not records none — the whole assertion for `opts.mouth:false`.
  g.requestAnimationFrame = () => 0;
  g.cancelAnimationFrame = () => {};
  g.CustomEvent = FakeCustomEvent;
  g.AudioContext = Ctx;
  g.SpeechSynthesisUtterance = class { constructor(t) { this.text = t; } };
  g.window = {
    addEventListener() {}, removeEventListener() {}, dispatchEvent: () => true,
    AudioContext: Ctx,
    moxie: { setMouthOpen: (v) => log.mouth.push(v) },
    /* Records the utterance only: firing `u.onstart()` would start a mouth-wobble interval
     * that outlives this rig's globals and crashes a later test. */
    speechSynthesis: { cancel() {}, getVoices: () => [], speak: (u) => log.synthesized.push(u.text) },
  };
  g.document = { getElementById: () => null, body: { classList: { toggle() {} } } };
  g.localStorage = { getItem: () => null, setItem() {} };
  g.location = { protocol: "https:", hostname: "moxie.example" };
  g.fetch = (url) => {
    url = String(url);
    log.urls.push(url);
    if (url.endsWith("audio/index.json"))
      return Promise.resolve({ ok: true, json: () => Promise.resolve(manifestOverride || RIG_MANIFEST) });
    if (url.startsWith("audio/"))
      return Promise.resolve({ ok: true, arrayBuffer: () => Promise.resolve(bufFor(url)) });
    return Promise.reject(new Error("nothing is listening"));   // :8081 and anything else
  };

  new Function(audioSrc)();
  await run(g.window.moxieAudio, log);
}

// A miniature manifest with one line per interesting case.
const RIG_MANIFEST = {
  moxie: { "Happy birthday!": "moxie/m1.mp3", "Only Moxie has this one.": "moxie/m2.mp3" },
  child: { "Guess what, it's my birthday today!": "child/c1.mp3", "Thank you Moxie!": "child/c2.mp3" },
  ambient: {},
};
const C1 = "audio/child/c1.mp3", C2 = "audio/child/c2.mp3", M1 = "audio/moxie/m1.mp3";
const probed = (log) => log.urls.some((u) => u.includes(":8081"));

{
  // (a) a scripted child line PLAYS — from the child group, and from nothing else.
  await voiceRig(async (audio, log) => {
    const ok1 = await audio.speakClipOnly("Guess what, it's my birthday today!", "child");
    eq(ok1, true, "a scripted child line with a clip must actually play — this is the whole feature");
    ok(log.started.includes(C1),
       `…from the child clip; started ${JSON.stringify(log.started)}`);
    eq(log.mouth.length, 0,
       "the child's clip must NOT drive Moxie's mouth — a robot lip-syncing the child's words " +
       "is a visibly broken toy (playUrl opts.mouth:false)");
    eq(log.synthesized.length, 0, "a child line must never reach speechSynthesis");
    eq(probed(log), false, "a child line must never probe the Piper sidecar");
  });

  // (b) NO clip -> silence. Not Piper, not the browser voice, not a tone. This is the trap
  //     the whole design exists for: a visitor's own words come through this same call.
  await voiceRig(async (audio, log) => {
    const said = await audio.speakClipOnly("is my mum going to be ok", "child");
    eq(said, false, "a child line with no clip must report that it made no sound");
    eq(log.started.length, 0, "…and must start no audio at all");
    eq(log.synthesized.length, 0,
       "a child line with no clip must NEVER be synthesized — that reads the visitor's own " +
       "sentence back at them in a stranger's voice, which is worse than the silence we started with");
    eq(probed(log), false,
       "…and must not even ask Piper: there is no fallback chain out of speakClipOnly, by construction");
  });

  // (c) the SAME text through `speak()` DOES make sound. Without this, (b) could pass on a
  //     rig where nothing can make sound at all, and would prove nothing.
  await voiceRig(async (audio, log) => {
    await audio.speak("is my mum going to be ok");
    ok(log.synthesized.length > 0 || probed(log),
       "control: speak() must still fall through to a synthesizer for an uncached line — " +
       "otherwise the no-fallback assertions above are vacuous");
  });

  // (d) strict group. A text cached only in the `moxie` group is NOT the child's voice.
  //     `playClip` deliberately falls through moxie -> child; `speakClipOnly` must not.
  await voiceRig(async (audio, log) => {
    const said = await audio.speakClipOnly("Only Moxie has this one.", "child");
    eq(said, false,
       "speakClipOnly must look in the named group and NOWHERE else — falling through to " +
       "`moxie` would answer a child line with a clip of Moxie's voice saying the child's words");
    eq(log.started.length, 0, "…and start nothing");
  });

  // (e) ORDERING, half one: the child never cuts Moxie off. A visitor typing while Moxie
  //     answers must not be able to silence her.
  await voiceRig(async (audio, log) => {
    await audio.speak("Happy birthday!");
    eq(log.started.length, 1, "precondition: Moxie's clip is playing");
    const said = await audio.speakClipOnly("Thank you Moxie!", "child");
    eq(said, false, "a child line must not start while Moxie is speaking");
    eq(log.stopped.length, 0,
       "…and must not stop her: the robot is the subject of the page and is never talked over by a prop");
    eq(log.started.length, 1, "…so no second source was started");
  });

  // (f) ORDERING, half two: Moxie DOES cut the child. `speak()` calls `stop()` first, and
  //     that stays true — it is why §2b has to time the shipped session.
  await voiceRig(async (audio, log) => {
    await audio.speakClipOnly("Guess what, it's my birthday today!", "child");
    eq(log.started.length, 1, "precondition: the child's clip is playing");
    await audio.speak("Happy birthday!");
    ok(log.stopped.includes(C1),
       `Moxie starting to speak must stop the child's clip; stopped ${JSON.stringify(log.stopped)}`);
    ok(log.started.includes(M1), "…and Moxie's own clip must play");
  });

  // (g) a newer child line replaces an older one rather than layering over it.
  await voiceRig(async (audio, log) => {
    await audio.speakClipOnly("Guess what, it's my birthday today!", "child");
    await audio.speakClipOnly("Thank you Moxie!", "child");
    ok(log.stopped.includes(C1), `the older child clip must be stopped; got ${JSON.stringify(log.stopped)}`);
    ok(log.started.includes(C2), `…and the newer one started; got ${JSON.stringify(log.started)}`);
  });

  // (h) structural: no synthesizer is reachable from the function at all (the behavioural
  //     cases above only cover the lines the rig happens to try).
  const body = audioSrc.slice(audioSrc.indexOf("function speakClipOnly"));
  const fnEnd = body.indexOf("\n  }\n");
  const clipOnlyBody = fnEnd === -1 ? body : body.slice(0, fnEnd);
  ok(clipOnlyBody.length > 0, "speakClipOnly must exist in voice/");
  for (const forbidden of ["speakBrowser", "speakLive", "speechSynthesis", "sfx(", "speak("])
    ok(!clipOnlyBody.includes(forbidden),
       `speakClipOnly's body must not mention ${forbidden} — the no-fallback guarantee is meant to be ` +
       `a property of WHICH FUNCTION you called, not a condition someone can loosen`);
  ok(/window\.moxieAudio\s*=\s*\{[\s\S]{0,400}speakClipOnly/.test(audioSrc),
     "speakClipOnly must be exported on window.moxieAudio — bridge/ calls it by name");

  // (i) …and the caller really is `handleUserTurn`, with the child group named.
  const bridgeSrc = BRIDGE_SRC;
  const turn = bridgeSrc.slice(bridgeSrc.indexOf("function handleUserTurn"));
  const turnBody = turn.slice(0, turn.indexOf("\n  }\n"));
  ok(/speakClipOnly\(\s*speech\s*,\s*"child"\s*\)/.test(turnBody),
     "bridge/handleUserTurn must speak the child's line through speakClipOnly(speech, \"child\")");
  ok(!/moxieAudio\.speak\(/.test(turnBody),
     "handleUserTurn must NEVER call speak() — that is the path that synthesizes a visitor's own words");

  notes.push("child voice: clip-only — scripted lines play from the `child` group, uncached lines " +
             "(a visitor's own words) make no sound at all; child yields, Moxie interrupts");
}

/* --------------------------------------------------------------------------- *
 * 8c. End to end, on the REAL assets
 *
 * The REAL `bridge/` + `voice/` against the REAL manifest, clips and `sessions/demo.json`:
 * the demo's child events are routed as `replay()` routes them, and the MP3 the site ships
 * must be the URL that goes out.
 * --------------------------------------------------------------------------- */
await withGlobals([...RIG_GLOBALS, "mqtt"], async (g) => {
  const urls = [], started = [], mouth = [], synthesized = [];
  const byLen = new Map();
  const Ctx = fakeWebAudio({ started }, byLen);

  g.requestAnimationFrame = () => 0;
  g.cancelAnimationFrame = () => {};
  g.CustomEvent = FakeCustomEvent;
  g.AudioContext = Ctx;
  g.SpeechSynthesisUtterance = class { constructor(t) { this.text = t; } };
  const el = () => ({ value: "", textContent: "", innerHTML: "", className: "", scrollTop: 0,
                      scrollHeight: 0, addEventListener() {}, appendChild() {},
                      querySelector: () => ({ set textContent(v) {} }), classList: { toggle() {} } });
  g.document = { getElementById: () => el(), createElement: () => el(), body: el() };
  g.localStorage = { getItem: () => null, setItem() {} };
  g.location = { protocol: "https:", hostname: "moxie.example" };
  g.window = {
    addEventListener() {}, removeEventListener() {}, dispatchEvent: () => true,
    AudioContext: Ctx,
    // A minimal avatar: enough for bridge/ to render, and it RECORDS every mouth call.
    moxie: { setFace() {}, setSpeech() {}, setMotor() {}, getMotor: () => 16384, showIcons() {},
             clearIcons() {}, setHeartLED() {}, setMouthOpen: (v) => mouth.push(v) },
    speechSynthesis: { cancel() {}, getVoices: () => [], speak: (u) => synthesized.push(u.text) },
  };
  g.mqtt = { connect: () => { throw new Error("this test never goes on the bus"); } };
  // Serves the site's real files, so the URLs asserted below are the URLs that ship.
  g.fetch = (url) => {
    url = String(url);
    urls.push(url);
    const onDisk = join(web, url);
    if (!url.startsWith("audio/") || !existsSync(onDisk))
      return Promise.reject(new Error("not served: " + url));
    if (url.endsWith(".json"))
      return Promise.resolve({ ok: true, json: () => Promise.resolve(JSON.parse(readFileSync(onDisk, "utf8"))) });
    const bytes = readFileSync(onDisk);
    const ab = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
    byLen.set(ab.byteLength, url);
    return Promise.resolve({ ok: true, arrayBuffer: () => Promise.resolve(ab) });
  };

  new Function(audioSrc)();                                    // the real voice/
  new Function(BRIDGE_SRC)();                                  // the real bridge/
  ok(!!g.window.moxieBridge, "bridge/ must expose window.moxieBridge");

  // The demo's own child events, routed exactly as replay() routes them.
  const demo = JSON.parse(readFileSync(join(sessionsDir, "demo.json"), "utf8"));
  const childEvents = demo.filter((e) => String(e.topic).endsWith("/events/remote-chat"));
  eq(childEvents.length, 2, "sessions/demo.json must still carry the two child turns");

  for (const ev of childEvents) {
    started.length = 0;
    g.window.moxieBridge.route(ev.topic, ev.payload);
    // speakClipOnly is async (manifest fetch -> clip fetch -> decode); let it settle.
    for (let i = 0; i < 20 && started.length === 0; i++) await new Promise((r) => setImmediate(r));

    const text = JSON.parse(ev.payload).speech;
    const want = "audio/" + manifest.child[text];
    ok(urls.includes(want),
       `replaying ${JSON.stringify(text.slice(0, 30))} must fetch the shipped clip ${want}; ` +
       `fetched ${JSON.stringify(urls.filter((u) => u.endsWith(".mp3")))}`);
    ok(started.includes(want), `…and actually start it; started ${JSON.stringify(started)}`);
  }
  eq(mouth.length, 0, "replaying the child's turns must never move Moxie's mouth");
  eq(synthesized.length, 0, "…and must never reach speechSynthesis");
  eq(urls.some((u) => u.includes(":8081")), false, "…and must never probe the Piper sidecar");

  // The same route, with something a VISITOR could have typed: silent, end to end.
  const before = urls.length;
  g.window.moxieBridge.route("/devices/d_demo/events/remote-chat",
    JSON.stringify({ command: "prompt", speech: "my hamster died last night" }));
  for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r));
  eq(urls.slice(before).filter((u) => u.endsWith(".mp3")).length, 0,
     "a visitor's own words must fetch no audio at all — this is the trap the design exists for");
  eq(synthesized.length, 0, "…and must not be synthesized");

  notes.push(`end to end: both demo.json child turns play their shipped MP3 through the real ` +
             `bridge/ + voice/; an unscripted line fetches nothing`);
});

/* --------------------------------------------------------------------------- *
 * 9. The renderer cannot silently drop a manifest group again
 *
 * §1 guards the artefact; this guards the tool that writes it. The merge used to name
 * `moxie` and `child` explicitly, so any group it did not know about was erased on the
 * next write. It must be group-agnostic.
 * --------------------------------------------------------------------------- */
{
  const toolPath = join(here, "tools", "prerender_audio.py");
  ok(existsSync(toolPath), "sim/tools/prerender_audio.py must exist");
  const tool = readFileSync(toolPath, "utf8");
  const i = tool.indexOf("idx_path = os.path.join");
  const merge = i === -1 ? "" : tool.slice(i, tool.indexOf("total = 0", i));
  ok(merge.length > 0, "prerender_audio.py must still merge an existing manifest before writing it");
  ok(/\.items\(\)/.test(merge),
     "prerender_audio.py's manifest merge must iterate the groups it FINDS — naming them one by one " +
     "silently erased the whole `ambient` group (56 clips) on any run that did not pass --ambient");
  ok(!/cur\.get\("ambient"/.test(merge),
     "…and it must not need to know a group's name to keep it");
}
