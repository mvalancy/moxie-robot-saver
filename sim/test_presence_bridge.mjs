/* Unit test for the presence half of sim/web/bridge.js — loads the REAL bridge with a
 * stubbed window/document/mqtt and asserts that "someone walked in" behaves like the
 * recovered vision contract: the event goes out as the `speech` of a RemoteChatRequest
 * on the ordinary remote-chat topic (docs/architecture/vision.md §1.1), it never lands
 * in the comms log, the badge records it, and the server's answer to that event_id is
 * recorded as a greeting. No browser, no network.
 *
 * Run: node sim/test_presence_bridge.mjs
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "web", "bridge.js"), "utf8");

// ---- stubs ----
const calls = { transcript: [], speech: [] };
const moxie = {
  setFace: () => {}, setSpeech: (t) => calls.speech.push(t), setMotor: () => {},
  getMotor: () => 16384, showIcons: () => {}, clearIcons: () => {}, setHeartLED: () => {},
};
const clickHandlers = {}, mqttClientRef = { c: null }, els = {}, attrs = {};
const fakeEl = (id) => ({
  id, value: "", textContent: "", innerHTML: "", className: "", scrollTop: 0, scrollHeight: 0,
  setAttribute: (k, v) => { attrs[id + "/" + k] = v; },
  addEventListener: (e, cb) => { if (e === "click" && id) clickHandlers[id] = cb; },
  appendChild: (child) => calls.transcript.push(child && child._text),
  querySelector: () => ({ set textContent(v) {}, get textContent() { return ""; } }),
});
globalThis.window = { moxie, addEventListener: () => {} };
globalThis.location = { hostname: "127.0.0.1" };
globalThis.document = {
  getElementById: (id) => (els[id] ||= fakeEl(id)),
  createElement: () => {
    const el = fakeEl();
    Object.defineProperty(el, "querySelector", { value: () => ({ set textContent(v) { el._text = v; } }) });
    return el;
  },
};
const published = [];
globalThis.mqtt = {
  connect: () => {
    const h = {};
    mqttClientRef.c = {
      connected: true,
      on: (e, cb) => { h[e] = cb; }, subscribe: () => {}, end: () => {},
      publish: (topic, payload) => published.push([topic, payload]),
      _emit: (e, ...a) => h[e] && h[e](...a),
    };
    return mqttClientRef.c;
  },
};

// ---- load bridge.js and connect ----
(0, eval)(src);
clickHandlers["bus-connect"]();
const client = mqttClientRef.c;
client._emit("connect");

const fails = [];
const ok = (cond, msg) => { if (!cond) fails.push(msg); };
const B = window.moxieBridge;

// ---- 1. nothing is known until the robot says something ----
ok(B.presenceStats().present === null, "presence starts UNKNOWN, not false");
ok(attrs["presence-badge/data-presence"] === "unknown",
   `badge starts 'unknown'; got ${attrs["presence-badge/data-presence"]}`);

// ---- 2. "walk in" publishes the recovered event as an ordinary chat request ----
const foundId = B.faceEvent("found");
const [topic, payload] = published[published.length - 1] || [];
ok(topic === "/devices/d_sim/events/remote-chat",
   `face event rides the remote-chat topic; got ${topic}`);
const msg = JSON.parse(payload || "{}");
ok(msg.speech === "eb-found-face", `speech is the event string; got ${msg.speech}`);
ok(msg.command === "prompt" && msg.backend === "router", "same envelope a child's turn uses");
ok(msg.event_id === foundId, "the caller gets the event_id back so the reply can be matched");

let stats = B.presenceStats();
ok(stats.present === true, "found → present");
ok(stats.arrivals === 1 && stats.departures === 0, `counters; got ${JSON.stringify(stats)}`);
ok(attrs["presence-badge/data-presence"] === "here",
   `badge → 'here'; got ${attrs["presence-badge/data-presence"]}`);
ok(els["presence-state"].textContent === "HERE", "badge label");
ok(els["presence-toggle"].textContent === "Walk away", "the button becomes 'walk away'");

// ---- 3. a perception event is NOT something a child said ----
ok(!calls.transcript.includes("eb-found-face"),
   `a vision event must never enter the comms log; got ${JSON.stringify(calls.transcript)}`);

// ---- 4. the server's silent acknowledgement is not a greeting ----
client._emit("message", "/devices/d_sim/commands/remote_chat", Buffer.from(JSON.stringify(
  { command: "remote_chat", result: "NOREPLY_ACK", event_id: foundId, output: { text: "", markup: "" } })));
ok(B.presenceStats().greetings.length === 0, "NOREPLY_ACK carries no words → no greeting");

// ---- 5. walk away, then back in, and the server says hello ----
B.faceEvent("lost");
stats = B.presenceStats();
ok(stats.present === false && stats.departures === 1, "lost → away");
ok(attrs["presence-badge/data-presence"] === "away", "badge → 'away'");

const backId = B.faceEvent("found");
client._emit("message", "/devices/d_sim/commands/remote_chat", Buffer.from(JSON.stringify(
  { command: "remote_chat", result: "SUCCESS", event_id: backId,
    output: { text: "Hey Sam, there you are! I missed you.",
              markup: '<mark name="cmd:playback-mood,data:{+mood+:1,+intensity+:1}"/>Hey Sam!' } })));
stats = B.presenceStats();
ok(stats.greetings.length === 1 && stats.greetings[0].startsWith("Hey Sam"),
   `the hello is recorded; got ${JSON.stringify(stats.greetings)}`);
ok(stats.arrivals === 2 && stats.departures === 1, `counters; got ${JSON.stringify(stats)}`);
ok(stats.events.join(",") === "eb-found-face,eb-lost-target,eb-found-face",
   `the recorded event log; got ${stats.events.join(",")}`);

// ---- 6. an event arriving FROM the bus (a real robot / a replay) counts too ----
client._emit("message", "/devices/d_sim/events/remote-chat",
  Buffer.from(JSON.stringify({ command: "prompt", speech: "eb-lost-target", event_id: "bus-1" })));
ok(B.presenceStats().present === false, "a bus-sourced lost updates the badge");
ok(!calls.transcript.includes("eb-lost-target"), "and still never enters the comms log");

// ---- 7. the toggle button walks in and out ----
clickHandlers["presence-toggle"]();
ok(B.presenceStats().present === true, "the toggle walks the child back in");
clickHandlers["presence-toggle"]();
ok(B.presenceStats().present === false, "and back out");

if (fails.length) {
  console.log("❌ presence bridge unit test FAILED:");
  for (const f of fails) console.log("   -", f);
  process.exit(1);
}
console.log(`✅ presence bridge unit test OK — ${B.presenceStats().events.length} vision events, ` +
            `${B.presenceStats().greetings.length} greeting recorded, badge + toggle asserted`);
