/* The rehearsal, replayed through the only renderer we can execute: the bytes a REAL robot
 * received from a REAL supervisor over a REAL broker (captured by
 * sim/tests/test_sil_performance_e2e.py), so a markup string mangled between `render()` and the
 * client's JSON parse is caught instead of leaving the robot standing still.
 *
 *     node sim/test_preview_render.mjs [capture.json]
 *
 * capture = `{"messages": [<remote_chat payload>, …]}`. With no argument it replays the
 * committed performance goldens in the payload shape the supervisor publishes.
 */
import { existsSync, readFileSync } from "node:fs";
import { loadBridge, readGolden, checks } from "./bridge_harness.mjs";

const capturePath = process.argv[2] || "";
if (capturePath && !existsSync(capturePath)) { console.error(`❌ no capture at ${capturePath}`); process.exit(1); }
const messages = capturePath
  ? JSON.parse(readFileSync(capturePath, "utf8")).messages || []
  : readGolden("performance.json").cases.map((c, i) => ({
      command: "remote_chat", result: "SUCCESS", backend: "router", event_id: `preview-golden-${i}`,
      output: { text: c.line, markup: c.markup, dialog_act: c.act } }));
if (!messages.length) { console.error("❌ the capture carried no messages — nothing was asserted"); process.exit(1); }

const { calls, reset, client } = loadBridge();
const { ok, report } = checks();
const REST = 16384;
const facesSeen = new Set();
let moved = 0;
for (const msg of messages) {
  const out = msg.output || {};
  const label = out.dialog_act || msg.event_id || "(unlabelled)";
  reset();
  // Verbatim: what the robot received is what the SIM must be able to play.
  client._emit("message", "/devices/d_test/commands/remote_chat", Buffer.from(JSON.stringify(msg)));
  ok(calls.setSpeech.includes(out.text), `${label}: the spoken line reached the avatar`);
  ok(calls.setFace.length > 0, `${label}: no face was set for ${JSON.stringify(out.markup)}`);
  ok(calls.setMotor.length > 0, `${label}: the body never moved for ${JSON.stringify(out.markup)}`);
  calls.setFace.forEach((f) => facesSeen.add(f));
  // Over the batch, not per message: `other` legitimately drives every motor back to rest.
  if (calls.setMotor.some(([, v]) => v !== REST)) moved++;
}
if (messages.length > 1)
  ok(facesSeen.size > 1, `every line reached the same face (${[...facesSeen]}) — the performances do not differ`);
ok(moved > 0, "no motor ever left rest across the whole batch — the body performed nothing");

report(`✅ preview render: ${messages.length} message(s) from ${capturePath || "the performance goldens"} ` +
       `played through sim/web/bridge/; ${facesSeen.size} distinct face(s), ${moved} moved the body`);
