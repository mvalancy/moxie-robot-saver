/* Unit test for sim/web/bridge.js — loads the REAL bridge with stubbed
 * window/document/mqtt and asserts it drives window.moxie correctly from firmware
 * markup. No browser, no network. Run: node sim/test_bridge.mjs
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "web", "bridge.js"), "utf8");

// ---- spies / stubs ----
const calls = { setFace: [], setSpeech: [], setMotor: [], showIcons: [], clearIcons: [], transcript: [] };
const moxie = {
  setFace: (f) => calls.setFace.push(f),
  setSpeech: (t) => calls.setSpeech.push(t),
  setMotor: (i, v) => calls.setMotor.push([i, v]),
  getMotor: () => 16384,
  showIcons: (n) => calls.showIcons.push(n),
  clearIcons: () => calls.clearIcons.push(true),
  setHeartLED: () => {},
};
const clickHandlers = {}, mqttClientRef = { c: null };
const els = {};                                   // id-keyed elements (real DOM is stable per id)
const fakeEl = (id) => ({
  id, value: "", textContent: "", innerHTML: "", className: "", scrollTop: 0, scrollHeight: 0,
  addEventListener: (e, cb) => { if (e === "click" && id) clickHandlers[id] = cb; },
  appendChild: (child) => calls.transcript.push(child && child._text),
  querySelector: () => ({ set textContent(v) {}, get textContent() { return ""; } }),
});
globalThis.window = { moxie, addEventListener: () => {} };
// A no-op audio stub: bridge.js only *calls* these (local TTS on connect, and
// stop() on a telehealth INTERRUPT); nothing here is asserted, so it stays silent.
globalThis.window.moxieAudio = { speak: () => {}, stop: () => {}, sfx: () => {},
                                 playCloudTTS: () => {} };
globalThis.location = { hostname: "127.0.0.1" };
globalThis.document = {
  getElementById: (id) => (els[id] ||= fakeEl(id)),
  createElement: () => {
    const el = fakeEl();
    Object.defineProperty(el, "querySelector", { value: () => ({ set textContent(v) { el._text = v; } }) });
    return el;
  },
};
// The stub client is CONNECTED and records every publish, because the bridge is a robot
// in both directions now: what it puts on `events/...` is as much under test as what it
// does with what it receives.
const published = [];
globalThis.mqtt = {
  connect: () => {
    const h = {};
    mqttClientRef.c = { connected: true, on: (e, cb) => { h[e] = cb; },
      subscribe: (t) => subscribed.push(t), end: () => {},
      publish: (topic, payload) => published.push({ topic, payload }),
      _emit: (e, ...a) => h[e] && h[e](...a) };
    return mqttClientRef.c;
  },
};
const subscribed = [];

// ---- load bridge.js (IIFE runs; window.moxie exists → initUI wires the clicks) ----
(0, eval)(src);
if (!clickHandlers["bus-connect"]) throw new Error("bridge did not wire the connect button");
clickHandlers["bus-connect"]();       // → connect() → mqtt.connect → mqttClientRef.c
const mqttClient = mqttClientRef.c;
if (!mqttClient) throw new Error("bridge did not connect over mqtt");
mqttClient._emit("connect");

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

mqttClient._emit("message", "/devices/d_test/commands/motor",
  Buffer.from(JSON.stringify({ motors: { "0": 30000, "4": 24000 } })));  // SIL motor channel

// ---- 🎭 telehealth: the operator's line must drive the avatar exactly like a brain
// reply, because `Output.markup` IS the same behavior language (telehealth.md:16-17).
// Same markup as the "Hmm?" reply above, delivered on the puppet channel instead.
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
// snapshot: the action turns below drive setSpeech again, and the INTERRUPT
// assertion is about the state the bubble was left in *then*.
const speechAtInterrupt = calls.setSpeech.slice();

// ---- 🎬 response_actions: the cloud drives navigation, and the avatar must obey ----
// The shape is the one `sim/tests/test_e2e_actions_to_robot.py` asserts arrives at the
// robot: `{output_type:"GLOBAL", action, module_id, content_id}` off
// `mqtt/moxie_sdk/wire.py::build_chat_response`. That test's docstring says outright that
// no SIM client acts on them; these assertions are that gap closing.
mqttClient._emit("message", "/devices/d_test/commands/remote_chat",
  Buffer.from(JSON.stringify({ command: "remote_chat", result: "SUCCESS", event_id: "act-1",
    output: { text: "Yes! Let's draw.", markup: "Yes! Let's draw." },
    response_actions: [{ output_type: "GLOBAL", action: "launch",
                         module_id: "DRAW", content_id: "default" }] })));
const afterLaunch = window.moxieBridge.actionStats();

// An action-less entry carrying ONLY an event subscription — legal, and not an error.
mqttClient._emit("message", "/devices/d_test/commands/remote_chat",
  Buffer.from(JSON.stringify({ command: "remote_chat", result: "NOREPLY_ACK", event_id: "act-2",
    output: { text: "", markup: "" },
    response_action: { output_type: "GLOBAL",
                       event_subscription: { active: ["eb-found-face", "eb-lost-target"],
                                             clear: false } },
    response_actions: [{ output_type: "GLOBAL",
                         event_subscription: { active: ["eb-found-face", "eb-lost-target"],
                                               clear: false } }] })));

// An action type this client does not implement, and a junk entry: both must be COUNTED
// and skipped. A future server verb may not break an old client's turn.
mqttClient._emit("message", "/devices/d_test/commands/remote_chat",
  Buffer.from(JSON.stringify({ command: "remote_chat", event_id: "act-3",
    output: { text: "…", markup: "…" },
    response_actions: [{ output_type: "GLOBAL", action: "teleport_to_mars" }, "nonsense"] })));

// …then goodbye: exit the module and go to sleep.
mqttClient._emit("message", "/devices/d_test/commands/remote_chat",
  Buffer.from(JSON.stringify({ command: "remote_chat", result: "SUCCESS", event_id: "act-4",
    output: { text: "Bye Sam!", markup: "Bye Sam!" },
    response_actions: [{ output_type: "GLOBAL", action: "exit" },
                       { output_type: "GLOBAL", action: "sleep" }] })));
const act = window.moxieBridge.actionStats();

// ---- 📒 robot → cloud: the activity log, byte-compared with the SIL robot's ----
window.moxieBridge.reportMentorBehavior({ module_id: "DRAW", content_id: "default",
                                          action: "completed", timestamp: 1788360800925 });
// the cloud's answer to the `schedule` query the bridge sent on connect
mqttClient._emit("message", "/devices/d_test/commands/query_result",
  Buffer.from(JSON.stringify({ command: "query_result", query: "schedule",
    request_id: window.moxieBridge.activityStats().published[0].request_id,
    schedule: [{ module_id: "DRAW", at: "07:38" }] })));
const log = window.moxieBridge.activityStats();

const golden = JSON.parse(readFileSync(
  join(here, "tests", "goldens", "robot_to_cloud_activity.json"), "utf8"));
const identity = golden.identity_keys;

/* Compare one published envelope with the golden the SIL robot produced: same keys in the
 * same order at every level, same values — except the `identity_keys`, which say WHICH
 * robot is speaking and WHEN and are therefore compared by JSON type only. */
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
const fails = [];
const ok = (cond, msg) => { if (!cond) fails.push(msg); };
ok(calls.setSpeech.includes("Happy birthday!"), "setSpeech('Happy birthday!')");
ok(calls.setFace.includes("happy"), `mood 1 → setFace('happy'); got ${JSON.stringify(calls.setFace)}`);
ok(calls.setFace.includes("confused"), `mood 8 (Confused) → setFace('confused'); got ${JSON.stringify(calls.setFace)}`);
ok(calls.setMotor.some(([i]) => i === 5), `Bht_Spin_360 → body-yaw motor (5) driven; got ${JSON.stringify(calls.setMotor)}`);
ok(calls.setMotor.length > 0, "Gesture_Celebrate → setMotor(...) called");
ok(JSON.stringify(calls.showIcons).includes("Birthday"), `icons-v2 → showIcons(['Birthday']); got ${JSON.stringify(calls.showIcons)}`);
ok(calls.transcript.includes("I feel happy today"), `child turn → transcript; got ${JSON.stringify(calls.transcript)}`);
ok(!calls.transcript.includes("echo of Moxie"), "notify turn must NOT appear in transcript");
ok(calls.transcript.includes("Happy birthday!"), "Moxie reply → transcript");
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

// 🎬 response_actions → the avatar
ok(afterLaunch.module_id === "DRAW" && afterLaunch.content_id === "default",
   `launch → the SIM is in the module; got ${afterLaunch.module_id}/${afterLaunch.content_id}`);
ok(JSON.stringify(calls.showIcons).includes("DRAW"),
   `launch DRAW → the module badge is shown; got ${JSON.stringify(calls.showIcons)}`);
ok(calls.setMotor.length > 0, "launch → the greet gesture drove the motors");
ok(act.launches === 1 && act.exits === 1, `one launch + one exit recorded; got ${act.launches}/${act.exits}`);
ok(act.module_id === "" && act.content_id === "", `exit → out of the module; got ${JSON.stringify(act)}`);
ok(act.asleep === true && act.last === "sleep", `sleep → asleep; got ${act.asleep}/${act.last}`);
ok(calls.setFace.includes("sleep"), `sleep action → setFace('sleep'); got ${JSON.stringify(calls.setFace)}`);
ok(act.unknown === 2, `an unknown action type and a junk entry are counted, not thrown; got ${act.unknown}`);
ok(JSON.stringify(act.subscribed) === JSON.stringify(["eb-found-face", "eb-lost-target"]),
   `event_subscription recorded; got ${JSON.stringify(act.subscribed)}`);
ok(act.applied.every((a) => a.action !== "teleport_to_mars"),
   `an unknown action never reaches the avatar; got ${JSON.stringify(act.applied)}`);

// 📒 robot → cloud: the activity log
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

if (fails.length) {
  console.log("❌ bridge unit test FAILED:");
  for (const f of fails) console.log("   -", f);
  process.exit(1);
}
console.log(`✅ bridge unit test OK — ${Object.values(calls).reduce((a, c) => a + c.length, 0)} avatar calls asserted ` +
  `(mood→face, gesture→motor, icons-v2→badges, transcript, notify-skip, 🎭 telehealth, ` +
  `🎬 response_actions, 📒 activity-log parity with ${golden.reference_client})`);
process.exit(0);   // the local-voice grace timer would otherwise hold the loop open
