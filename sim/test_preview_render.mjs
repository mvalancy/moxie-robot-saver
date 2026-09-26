/* The 🎬 rehearsal, replayed through the only renderer we can execute.
 *
 * `sim/test_performance_render.mjs` plays the planner's 22 dialog-act **goldens** through
 * `sim/web/bridge.js`. This plays something different and, for an integration pass, more
 * load-bearing: the bytes a REAL robot was handed by a REAL supervisor over a REAL
 * broker, captured by `sim/tests/test_sil_performance_e2e.py` at the moment
 * `MoxieRuntime.preview` published them.
 *
 * The distinction matters because everything between `render()` and the robot — the
 * `Staged` tuple, `_publish_chat`, `build_chat_response`, `json.dumps`, mosquitto, the
 * client's own JSON parse — is invisible to a golden file. A markup string that survives
 * `json.dumps` but not the SIM's `<mark …>` parser, or a payload whose `output.markup`
 * arrives under a different key, is a robot standing perfectly still while every Python
 * test in the tree stays green.
 *
 * Usage (the Python test writes the file and calls this):
 *
 *     node sim/test_preview_render.mjs <capture.json>
 *
 * where the capture is `{"messages": [ <remote_chat payload>, … ]}` — payloads exactly as
 * received off `/devices/<id>/commands/remote_chat`.
 *
 * Run standalone with no argument and it self-checks against the committed goldens
 * instead, so this file is never un-runnable by hand.
 *
 * No browser, no network.
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

  /* Peak displacement per motor — the recorded state, never a live sample (the SIM
   * test rule: sampling a live value on a loaded runner is how three fast-tier flakes
   * were born). Not asserted per message: `other` is the act with nothing to perform,
   * and its tree legitimately drives every motor straight back to rest. The batch is
   * asserted instead, below. */
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
