/* The 🎬 rehearsal, replayed through the only renderer we can execute.
 *
 * Unlike test_performance_render.mjs (goldens), this plays the bytes a REAL robot received
 * from a REAL supervisor over a REAL broker, captured by sim/tests/test_sil_performance_e2e.py
 * — so a markup string mangled anywhere between `render()` and the client's JSON parse is
 * caught instead of leaving the robot standing still while every Python test is green.
 *
 *     node sim/test_preview_render.mjs <capture.json>
 *
 * capture = `{"messages": [ <remote_chat payload>, … ]}` as received off
 * `/devices/<id>/commands/remote_chat`. With no argument it self-checks against the
 * committed goldens. No browser, no network.
 */
import { existsSync, readFileSync } from "node:fs";
import { loadBridge, readGolden } from "./bridge_harness.mjs";

const capturePath = process.argv[2] || "";
let messages;
let source;
if (capturePath) {
  if (!existsSync(capturePath)) {
    console.error(`❌ no capture at ${capturePath}`);
    process.exit(1);
  }
  const cap = JSON.parse(readFileSync(capturePath, "utf8"));
  messages = cap.messages || [];
  source = capturePath;
} else {
  /* Standalone fallback: the committed goldens, wrapped in the payload shape the
   * supervisor publishes. Same assertions, so a hand run is a real run. */
  const goldens = readGolden("performance.json");
  messages = goldens.cases.map((c, i) => ({
    command: "remote_chat", result: "SUCCESS", backend: "router",
    event_id: `preview-golden-${i}`,
    output: { text: c.line, markup: c.markup, dialog_act: c.act },
  }));
  source = "sim/tests/goldens/performance.json (no capture given)";
}

if (!messages.length) {
  console.error("❌ the capture carried no messages — nothing was asserted");
  process.exit(1);
}

const { calls, reset, client } = loadBridge();

const REST = 16384;
const fails = [];
const ok = (cond, msg) => { if (!cond) fails.push(msg); };
const facesSeen = new Set();
const peakOverall = new Map();
let asserted = 0;

for (const msg of messages) {
  const out = msg.output || {};
  const label = out.dialog_act || msg.event_id || "(unlabelled)";
  reset();
  /* Delivered on the device topic verbatim — no re-serialisation of our own, because
   * the point is that what the robot received is what the SIM can play. */
  client._emit("message", "/devices/d_test/commands/remote_chat",
    Buffer.from(JSON.stringify(msg)));

  ok(calls.setSpeech.some((t) => t === out.text),
     `${label}: the spoken line reached the avatar; got ${JSON.stringify(calls.setSpeech)}`);
  ok(calls.setFace.length > 0,
     `${label}: the avatar never set a face for ${JSON.stringify(out.markup)}`);
  ok(calls.setMotor.length > 0,
     `${label}: the body never moved for ${JSON.stringify(out.markup)}`);

  /* Peak displacement per motor — recorded state, never a live sample. Asserted over the
   * batch, not per message: `other` legitimately drives every motor back to rest. */
  for (const [i, v] of calls.setMotor) {
    const d = Math.abs(v - REST);
    if (d > (peakOverall.get(i) || 0)) peakOverall.set(i, d);
  }
  calls.setFace.forEach((f) => facesSeen.add(f));
  asserted += 1;
}

/* A rehearsal card whose lines all looked identical would pass every assertion above and
 * still be useless to an author, so require the batch to have performed differently.
 * One message cannot vary, so this only bites when there are several. */
if (messages.length > 1) {
  ok(facesSeen.size > 1,
     `every line reached the same face (${[...facesSeen]}) — the performances do not differ`);
}
ok([...peakOverall.values()].some((d) => d > 0),
   "no motor ever left rest across the whole batch — the body performed nothing");

if (fails.length) {
  console.error(`❌ preview render: ${fails.length} failure(s) over ${asserted} message(s)`);
  for (const f of fails) console.error(`   · ${f}`);
  process.exit(1);
}
const moved = [...peakOverall.values()].filter((d) => d > 0).length;
console.log(`✅ preview render: ${asserted} published message(s) from ${source} played `
  + `through sim/web/bridge.js; ${facesSeen.size} distinct face(s) `
  + `(${[...facesSeen].join(", ")}); ${moved} motor(s) left rest`);
