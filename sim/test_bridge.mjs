/* sim/web/bridge/ under bare node: firmware markup -> window.moxie, telehealth, response
 * actions, robot->cloud envelopes (byte-compared with the SIL robot's golden), the
 * presence events, and the face following the sentence (with life.js's idle beats).
 * No browser, no network. Run: node sim/test_bridge.mjs
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { loadBridge, audioSpy, readGolden, checks, here } from "./bridge_harness.mjs";

/* Both voice doors are spied: a child line must use `speakClipOnly`, never `speak()`
 * (which would read a visitor's own words back at them). */
const { voice, audio } = audioSpy();
const { calls, published, subscribed, client: mqttClient } = loadBridge({ audio });

// ---- drive real firmware markup through the message handler ----
const birthdayMarkup =
  '<mark name="cmd:playback-mood,data:{+mood+:1,+intensity+:1}"/>' +
  '<mark name="cmd:icons-v2,data:{+command+:0,+icon0+:{+iconType+:1,+value+:+Birthday+,+background+:+Null+},+highlight+:0}"/>' +
  '<mark name="cmd:behaviour-tree,data:{+eventName+:+Gesture_Celebrate+,+behaviour+:+Bht_Gesture_Celebrate+}"/>' +
  'Happy birthday!' +
  '<mark name="cmd:icons-v2,data:{+command+:2,+icon0+:{+iconType+:1,+value+:+Birthday+,+background+:+Null+},+highlight+:0}"/>';
mqttClient._emit("message", "/devices/d_test/commands/remote_chat",
  Buffer.from(JSON.stringify({ command: "remote_chat", output: { text: "Happy birthday!", markup: birthdayMarkup } })));

// a second reply exercising the authoritative ePlaybackMood map (8 = Confused → thinking)
mqttClient._emit("message", "/devices/d_test/commands/remote_chat",
  Buffer.from(JSON.stringify({ command: "remote_chat", output: { text: "Hmm?", markup:
    '<mark name="cmd:playback-mood,data:{+mood+:8,+intensity+:1}"/>Hmm?' } })));

// an authoritative Bht_* behaviour tree — Bht_Spin_360 drives the body-yaw motor (5)
mqttClient._emit("message", "/devices/d_test/commands/remote_chat",
  Buffer.from(JSON.stringify({ command: "remote_chat", output: { text: "Wheee!", markup:
    '<mark name="cmd:behaviour-tree,data:{+behaviour+:+Bht_Spin_360+,+eventName+:+Gesture_None+}"/>Wheee!' } })));

mqttClient._emit("message", "/devices/d_test/events/remote-chat",
  Buffer.from(JSON.stringify({ command: "prompt", speech: "I feel happy today" })));
mqttClient._emit("message", "/devices/d_test/events/remote-chat",
  Buffer.from(JSON.stringify({ command: "notify", speech: "echo of Moxie" })));  // must be skipped

// a CloudTTSResponse on commands/tts is handed to voice/ to decode and play
mqttClient._emit("message", "/devices/d_test/commands/tts", Buffer.from(JSON.stringify(
  { request_source: "ROBOT_TTS_REQUEST", audio: { buffer: "AAA=", channels: 1, sample_rate: 22050 },
    marks: [], event_id: "tts-1", chunk_num: 0 })));

mqttClient._emit("message", "/devices/d_test/commands/motor",
  Buffer.from(JSON.stringify({ motors: { "0": 30000, "4": 24000 } })));  // SIL motor channel

// ---- telehealth: the operator's line drives the avatar like a brain reply ----
const puppetMarkup =
  '<mark name="cmd:playback-mood,data:{+mood+:2,+intensity+:2}"/>' +
  '<mark name="cmd:behaviour-tree,data:{+behaviour+:+Bht_Spin_360+,+eventName+:+Gesture_None+}"/>' +
  'I missed you.';
mqttClient._emit("message", "/devices/d_test/commands/telehealth",
  Buffer.from(JSON.stringify({ command: "telehealth", message: {
    action: "START_SESSION", session_id: "ths-1" } })));
mqttClient._emit("message", "/devices/d_test/commands/telehealth",
  Buffer.from(JSON.stringify({ command: "telehealth", message: {
    action: "PLAY_OUTPUT", session_id: "ths-1",
    output: { text: "I missed you.", markup: puppetMarkup } } })));
mqttClient._emit("message", "/devices/d_test/commands/telehealth",
  Buffer.from(JSON.stringify({ command: "telehealth", message: {
    action: "INTERRUPT", session_id: "ths-1" } })));
const th = window.moxieBridge.telehealthStats();
const speechAtInterrupt = calls.setSpeech.slice();

// ---- response_actions (the golden script `test_sim_client_parity.py` pins by event_id) ----
mqttClient._emit("message", "/devices/d_test/commands/remote_chat",
  Buffer.from(JSON.stringify({ command: "remote_chat", result: 0, event_id: "act-1",
    output: { text: "Yes! Let's draw.", markup: "Yes! Let's draw." },
    response_actions: [{ output_type: "GLOBAL_RESPONSE", action: "launch",
                         module_id: "DRAW", content_id: "default" }] })));
const afterLaunch = window.moxieBridge.actionStats();

// An action-less entry carrying ONLY an event subscription — legal, and not an error.
mqttClient._emit("message", "/devices/d_test/commands/remote_chat",
  Buffer.from(JSON.stringify({ command: "remote_chat", result: 6, event_id: "act-2",
    output: { text: "", markup: "" },
    response_action: { output_type: "GLOBAL_RESPONSE",
                       event_subscription: { active: ["eb-found-face", "eb-lost-target"],
                                             clear: false } },
    response_actions: [{ output_type: "GLOBAL_RESPONSE",
                         event_subscription: { active: ["eb-found-face", "eb-lost-target"],
                                               clear: false } }] })));

// An unknown verb and a junk entry are COUNTED and skipped, never thrown.
mqttClient._emit("message", "/devices/d_test/commands/remote_chat",
  Buffer.from(JSON.stringify({ command: "remote_chat", event_id: "act-3",
    output: { text: "…", markup: "…" },
    response_actions: [{ output_type: "GLOBAL_RESPONSE", action: "teleport_to_mars" }, "nonsense"] })));

// …then goodbye: exit the module and go to sleep.
mqttClient._emit("message", "/devices/d_test/commands/remote_chat",
  Buffer.from(JSON.stringify({ command: "remote_chat", result: 0, event_id: "act-4",
    output: { text: "Bye Sam!", markup: "Bye Sam!" },
    response_actions: [{ output_type: "GLOBAL_RESPONSE", action: "exit_module" },
                       { output_type: "GLOBAL_RESPONSE", action: "sleep" }] })));
const act = window.moxieBridge.actionStats();

// ---- robot -> cloud: the activity log, byte-compared with the SIL robot's ----
window.moxieBridge.reportMentorBehavior({ module_id: "DRAW", content_id: "default",
                                          action: "completed", timestamp: 1788360800925 });
// the cloud's answer to the `schedule` query the bridge sent on connect
mqttClient._emit("message", "/devices/d_test/commands/query_result",
  Buffer.from(JSON.stringify({ command: "query_result", query: "schedule",
    request_id: window.moxieBridge.activityStats().published[0].request_id,
    schedule: [{ module_id: "DRAW", at: "07:38" }] })));
const log = window.moxieBridge.activityStats();

const golden = readGolden("robot_to_cloud_activity.json");
const identity = golden.identity_keys;

/* Same keys in the same order at every level, same values — except `identity_keys` (which
 * robot, and when), compared by JSON type only. */
function cmp(path, want, got, out) {
  if (identity.indexOf(path) >= 0) {
    if (typeof want !== typeof got)
      out.push(`${path}: identity field is ${typeof got}, the SIL robot sends ${typeof want}`);
    return;
  }
  if (want && typeof want === "object" && !Array.isArray(want)) {
    if (!got || typeof got !== "object" || Array.isArray(got))
      return out.push(`${path || "<root>"}: expected an object, got ${JSON.stringify(got)}`);
    const wk = Object.keys(want), gk = Object.keys(got);
    if (JSON.stringify(wk) !== JSON.stringify(gk))
      out.push(`${path || "<root>"}: keys ${JSON.stringify(gk)} != ${JSON.stringify(wk)}`);
    for (const k of wk) cmp(path ? `${path}.${k}` : k, want[k], got[k], out);
    return;
  }
  if (JSON.stringify(want) !== JSON.stringify(got))
    out.push(`${path}: ${JSON.stringify(got)} != ${JSON.stringify(want)}`);
}

const parity = [];
const byKind = {
  query: log.published.find((e) => e.subtopic === "query"),
  mentor_behavior: log.published.find((e) => e.mentor_behavior),
  telehealth_state: log.published.find((e) => e.subtopic === "telehealth"),
};
for (const [kind, spec] of Object.entries(golden.envelopes)) {
  const got = byKind[kind];
  if (!got) { parity.push(`${kind}: the browser SIM published no such envelope`); continue; }
  const out = [];
  cmp("", spec.payload, got, out);
  for (const f of out) parity.push(`${kind} ${f}`);
  // the golden's documented key order must be the golden's own key order
  if (JSON.stringify(spec.key_order) !== JSON.stringify(Object.keys(spec.payload)))
    parity.push(`${kind}: golden key_order is stale`);
}

// ---- assertions ----
const { ok, report } = checks();
ok(calls.setSpeech.includes("Happy birthday!"), "setSpeech('Happy birthday!')");
ok(calls.setFace.includes("happy"), `mood 1 → setFace('happy'); got ${JSON.stringify(calls.setFace)}`);
ok(calls.setFace.includes("confused"), `mood 8 (Confused) → setFace('confused'); got ${JSON.stringify(calls.setFace)}`);
ok(calls.setMotor.some(([i]) => i === 5), `Bht_Spin_360 → body-yaw motor (5) driven; got ${JSON.stringify(calls.setMotor)}`);
ok(JSON.stringify(calls.showIcons).includes("Birthday"), `icons-v2 → showIcons(['Birthday']); got ${JSON.stringify(calls.showIcons)}`);
ok(calls.transcript.includes("I feel happy today"), `child turn → transcript; got ${JSON.stringify(calls.transcript)}`);
ok(!calls.transcript.includes("echo of Moxie"), "notify turn must NOT appear in transcript");

ok(voice.speakClipOnly.some(([t, w]) => t === "I feel happy today" && w === "child"),
   `child turn → speakClipOnly(text, "child"); got ${JSON.stringify(voice.speakClipOnly)}`);
ok(!voice.speak.includes("I feel happy today"),
   `a child line must NEVER reach speak(); got ${JSON.stringify(voice.speak)}`);
ok(voice.sfx.includes("listen"), `child turn still fires sfx("listen"); got ${JSON.stringify(voice.sfx)}`);
ok(!voice.speakClipOnly.some(([t]) => t === "echo of Moxie") && !voice.speak.includes("echo of Moxie"),
   `a 'notify' echo must not be spoken as the child; got ${JSON.stringify(voice.speakClipOnly)}`);
ok(calls.transcript.includes("Happy birthday!"), "Moxie reply → transcript");
ok(subscribed.includes("/devices/+/commands/tts") &&
   voice.cloudTTS.some((p) => (typeof p === "string" ? JSON.parse(p) : p).event_id === "tts-1"),
   `commands/tts is subscribed and routed to moxieAudio.playCloudTTS; got ${JSON.stringify(voice.cloudTTS)}`);
ok(calls.setMotor.some(([i, v]) => i === 0 && v === 30000) && calls.setMotor.some(([i, v]) => i === 4 && v === 24000),
   `commands/motor → setMotor(0,30000)+setMotor(4,24000); got ${JSON.stringify(calls.setMotor)}`);

ok(calls.setSpeech.includes("I missed you."), "telehealth PLAY_OUTPUT → setSpeech('I missed you.')");
ok(calls.setFace.includes("sad"), `telehealth mood 2 → setFace('sad'); got ${JSON.stringify(calls.setFace)}`);
ok(calls.transcript.includes("I missed you."), "telehealth line → transcript, like any Moxie reply");
ok(th.lines.length === 1 && th.lines[0].text === "I missed you." && !!th.lines[0].markup,
   `telehealth recorded one line with markup; got ${JSON.stringify(th.lines)}`);
ok(th.session_id === "ths-1", `telehealth session_id recorded; got ${th.session_id}`);
ok(th.interrupts === 1 && th.last_action === "INTERRUPT",
   `INTERRUPT recorded; got ${th.interrupts}/${th.last_action}`);
ok(speechAtInterrupt[speechAtInterrupt.length - 1] === "",
   `INTERRUPT clears the speech bubble; got ${JSON.stringify(speechAtInterrupt.slice(-2))}`);

ok(afterLaunch.module_id === "DRAW" && afterLaunch.content_id === "default",
   `launch → the SIM is in the module; got ${afterLaunch.module_id}/${afterLaunch.content_id}`);
ok(JSON.stringify(calls.showIcons).includes("DRAW"),
   `launch DRAW → the module badge is shown; got ${JSON.stringify(calls.showIcons)}`);
ok(act.launches === 1 && act.exits === 1, `one launch + one exit recorded; got ${act.launches}/${act.exits}`);
ok(act.module_id === "" && act.content_id === "", `exit → out of the module; got ${JSON.stringify(act)}`);
ok(act.asleep === true && act.last === "sleep", `sleep → asleep; got ${act.asleep}/${act.last}`);
ok(calls.setFace.includes("sleep"), `sleep action → setFace('sleep'); got ${JSON.stringify(calls.setFace)}`);
ok(act.unknown === 2, `an unknown action type and a junk entry are counted, not thrown; got ${act.unknown}`);
ok(JSON.stringify(act.subscribed) === JSON.stringify(["eb-found-face", "eb-lost-target"]),
   `event_subscription recorded; got ${JSON.stringify(act.subscribed)}`);
ok(act.applied.every((a) => a.action !== "teleport_to_mars"),
   `an unknown action never reaches the avatar; got ${JSON.stringify(act.applied)}`);

ok(log.topic === "/devices/d_sim/events/client-service-activity-log",
   `activity log rides the recovered topic; got ${log.topic}`);
ok(published.some((p) => p.topic === log.topic),
   `the activity log actually reached the bus; published ${JSON.stringify(published.map((p) => p.topic))}`);
ok(subscribed.includes("/devices/+/commands/query_result"),
   `the SIM subscribes to the answers it asks for; got ${JSON.stringify(subscribed)}`);
ok(log.last_query === "schedule" && log.published.length >= 3,
   `the SIM pulls its day on connect and logs upstream; got ${log.last_query}/${log.published.length}`);
ok(log.results.schedule && Array.isArray(log.results.schedule.value) &&
   log.results.schedule.field === "schedule",
   `the CloudQueryResponse is decoded into its own proto field; got ${JSON.stringify(log.results)}`);
ok(log.telehealth_state === "IN_SESSION",
   `START_SESSION → the robot reports IN_SESSION upstream; got ${log.telehealth_state}`);
ok(parity.length === 0,
   `robot→cloud envelopes must match ${golden.reference_client}:\n     ${parity.join("\n     ")}`);


// ---- presence: a face event rides remote-chat as `speech`, never enters the comms log ----
{
  const P = loadBridge();
  const B = P.bridge;
  const badge = () => P.attrs["presence-badge/data-presence"];
  const chat = (event_id, result, text) => P.emit("/devices/d_sim/commands/remote_chat",
    { command: "remote_chat", result, event_id, output: { text, markup: text } });
  ok(B.presenceStats().present === null && badge() === "unknown" && P.els["presence-badge"].hidden === true,
     "presence starts UNKNOWN with the badge hidden (rendered check: test_liveliness.mjs)");

  const foundId = B.faceEvent("found");
  const { topic, payload } = P.published[P.published.length - 1] || {};
  const msg = JSON.parse(payload || "{}");
  ok(topic === "/devices/d_sim/events/remote-chat" && msg.speech === "eb-found-face" &&
     msg.command === "prompt" && msg.backend === "router" && msg.event_id === foundId,
     `found -> an ordinary remote-chat request carrying the event; got ${topic} ${payload}`);
  ok(badge() === "here" && P.els["presence-state"].textContent === "HERE" &&
     P.els["presence-badge"].hidden === false && P.els["presence-toggle"].textContent === "Walk away",
     `found -> badge HERE, revealed, toggle 'Walk away'; got ${badge()}`);
  ok(!P.calls.transcript.includes("eb-found-face"), "a vision event never enters the comms log");

  chat(foundId, "NOREPLY_ACK", "");
  ok(B.presenceStats().greetings.length === 0, "NOREPLY_ACK carries no words -> no greeting");

  B.faceEvent("lost");
  ok(B.presenceStats().present === false && badge() === "away", "lost -> away");
  const backId = B.faceEvent("found");
  chat(backId, "SUCCESS", "Hey Sam, there you are!");
  const st = B.presenceStats();
  ok(st.greetings.length === 1 && st.greetings[0].startsWith("Hey Sam") &&
     st.arrivals === 2 && st.departures === 1 &&
     st.events.join(",") === "eb-found-face,eb-lost-target,eb-found-face",
     `the answer to a face event is recorded as a greeting; got ${JSON.stringify(st)}`);

  P.emit("/devices/d_sim/events/remote-chat", { command: "prompt", speech: "eb-lost-target", event_id: "bus-1" });
  ok(B.presenceStats().present === false && !P.calls.transcript.includes("eb-lost-target"),
     "a bus-sourced event updates presence and still stays out of the log");
  P.clickHandlers["presence-toggle"]();
  ok(B.presenceStats().present === true, "the toggle walks the child back in");
}


// ---- the face follows the sentence: the mood mark owns the face, gestures move the arms ----
/* The hosted brain's markup (the REAL functions/api/_lib/wire.js floor over the model's own
 * {mood, gesture}) through the real bridge. think/question/celebrate used to re-set the face
 * after the mood mark, so a concerned line ended 'thinking' and a sad one 'happy'. */
const wire = await import(join(here, "..", "functions", "api", "_lib", "wire.js"));
/** One reply as /api/chat answers it, through a fresh bridge; its recorded calls. */
function hostedLine(text, chosen, extraMarks = "") {
  const L = loadBridge({ connect: false });
  L.bridge.route("/devices/d_sim/commands/remote_chat", JSON.stringify(wire.buildChatResponse(
    { eventId: "sim-face", text, markup: wire.markupFloor(text, chosen) + extraMarks })));
  return L.calls;
}
for (const [text, chosen, want, motor] of [
  ["Oh no, I'm sorry to hear that, Sam. School can be tricky sometimes.",
   { mood: "concerned", gesture: "think" }, "concerned", [3, 8000]],
  ["What is your favorite animal?", { mood: "curious", gesture: "question" }, "curious", [3, 8000]],
  ["That makes me a little sad too.", { mood: "sad", gesture: "celebrate" }, "sad", [0, 30000]],
  ["What happened at school today?", null, "curious", [3, 8000]],     // the floor alone ('?')
]) {
  const c = hostedLine(text, chosen);
  const label = `${JSON.stringify(chosen)} "${text.slice(0, 30)}…"`;
  ok(JSON.stringify(c.setFace) === JSON.stringify([want]),
     `${label}: the line wears one face, its mood '${want}'; got ${JSON.stringify(c.setFace)}`);
  ok(c.setMotor.some(([i, v]) => i === motor[0] && v === motor[1]),
     `${label}: its gesture still moves the arm (${motor}); got ${JSON.stringify(c.setMotor)}`);
}
{
  // No mood mark (a brain that sends only a gesture), or a mood the bridge cannot map: the
  // gesture still supplies the face, as it does for alive.js's thinking cue.
  const L = loadBridge({ connect: false });
  const think = wire.MK.gesture("Gesture_Think");
  const say = (text, markup) => L.bridge.route("/devices/d_sim/commands/remote_chat",
    JSON.stringify({ command: "remote_chat", output: { text, markup } }));
  say("Hmm.", think + "Hmm.");
  say("Hmm?", wire.MK.mood(42) + think + "Hmm?");
  ok(JSON.stringify(L.calls.setFace) === '["thinking","thinking"]',
     `no usable mood mark: think still shows 'thinking'; got ${JSON.stringify(L.calls.setFace)}`);
}
{
  // A goodbye ends on a Bht_Sign_off tree after the floor's marks (the planner's `closing` act
  // does): the mood rule must not swallow the wave (arm up, then the hand swings out).
  const signOff = '<mark name="cmd:behaviour-tree,data:{+transition+:0.5,+duration+:1.0,+repeat+:1,' +
    '+blocking+:false,+action+:0,+eventName+:+Gesture_None+,+category+:+BehaviourTree+,' +
    '+behaviour+:+Bht_Sign_off+,+Track+:++}"/>';
  const c = hostedLine("Bye, Sam! See you later!", { mood: "happy", gesture: "none" }, signOff);
  ok(c.setMotor.some(([i, v]) => i === 0 && v === 30000) && c.setMotor.some(([i, v]) => i === 1 && v === 26000),
     `goodbye + Bht_Sign_off: she still waves; got ${JSON.stringify(c.setMotor)}`);
}

// ---- …and life.js's idle beats leave the line's face alone while she says it ----
/* The REAL sim/web/life.js beat loop on a virtual clock: a frame every 16 ms, a beat forced
 * every 256 ms (the page beats every 1.4-4 s; forcing them fills every window with face
 * beats), a seeded Math.random, and a moxieAudio whose isMoxieBusy (voice/'s broad
 * predicate) is the only sign she is speaking. */
{
  const saved = { perf: Object.getOwnPropertyDescriptor(globalThis, "performance"),
                  raf: globalThis.requestAnimationFrame, random: Math.random };
  let now = 0, frames = [], seed = 7;
  Object.defineProperty(globalThis, "performance",
    { value: { now: () => now }, configurable: true, writable: true });
  globalThis.requestAnimationFrame = (cb) => frames.push(cb);
  Math.random = () => {                                     // mulberry32: same beats every run
    seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  let line = [Infinity, Infinity];                          // [start, end) of her voice
  const audio = { speak() {}, stop() {}, sfx() {}, speakClipOnly() {},
    // voice/core.js's meaning: speaking now, or stopped less than `grace` ms ago
    isMoxieBusy: (grace) => (now >= line[0] && now < line[1]) ||
                            (+grace > 0 && now >= line[1] && now - line[1] < +grace) };
  const L = loadBridge({ connect: false, audio,
    moxie: { isAlive: () => true, isUserHeld: () => false, MOTOR_CENTER: 16384 } });
  (0, eval)(readFileSync(join(here, "web", "life.js"), "utf8"));
  /** Run the beat loop for `ms`; the faces it set meanwhile. */
  const live = (ms) => {
    const n0 = L.calls.setFace.length;
    for (const end = now + ms; now < end;) {
      now += 16;
      if (now % 256 === 0) window.moxieLife.beatNow();
      const due = frames; frames = [];
      due.forEach((f) => f(now));
    }
    return L.calls.setFace.slice(n0);
  };

  const idle = live(4000);
  ok(idle.length > 0, `CONTROL: idle, life.js does shift her face (else the next checks prove nothing); got ${JSON.stringify(idle)}`);
  line = [now, now + 4000];
  const speaking = live(4000);
  ok(speaking.length === 0, `while a 4 s line plays, life.js sets no face; got ${JSON.stringify(speaking)}`);
  const finished = live(6000);
  ok(finished.length > 0, `…and after the line the idle mood shifts come back; got ${JSON.stringify(finished)}`);

  // A reply's face lands before its audio (the voice is still on its way): it holds anyway.
  const text = "That sounds really lonely, and I am sorry it happened to you.";
  L.bridge.route("/devices/d_sim/commands/remote_chat", JSON.stringify(wire.buildChatResponse(
    { eventId: "sim-hold", text, markup: wire.markupFloor(text, { mood: "sad", gesture: "self" }) })));
  ok(L.calls.setFace[L.calls.setFace.length - 1] === "sad",
     `the reply set its face; got ${JSON.stringify(L.calls.setFace.slice(-1))}`);
  const beforeVoice = live(3900);
  ok(beforeVoice.length === 0, `for 4 s after a reply lands, voice or not, life.js keeps its face; got ${JSON.stringify(beforeVoice)}`);
  const released = live(6000);
  ok(released.length > 0, `…then the hold ends and the mood shifts come back; got ${JSON.stringify(released)}`);

  if (saved.perf) Object.defineProperty(globalThis, "performance", saved.perf);
  globalThis.requestAnimationFrame = saved.raf;
  Math.random = saved.random;
}

report("✅ bridge unit test OK — markup->avatar, telehealth, response_actions, activity-log " +
       `parity with ${golden.reference_client}, presence events, the face following the sentence`);
process.exit(0);   // the local-voice grace timer would otherwise hold the loop open
