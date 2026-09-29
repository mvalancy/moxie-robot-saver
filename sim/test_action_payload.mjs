/* Does the BROWSER SIM decode an `execute`'s payload the way the SIL robot does?
 *
 * `function_id` (RemoteChat.proto field 7), `function_args` (8) and `action_args` (10) must be
 * read by bridge/actions.js::applyAction exactly as sim/virtual_moxie.py reads them. Drives the
 * REAL bridge over goldens/cloud_to_robot_actions.json's `execute_script` and compares the
 * applied actions with `execute_expected`, entry by entry — the golden
 * sim/tests/test_sim_client_parity.py holds VirtualMoxie to. Run: node sim/test_action_payload.mjs
 */
import { loadBridge, readGolden, checks } from "./bridge_harness.mjs";

const GOLDEN = readGolden("cloud_to_robot_actions.json");
const { ok, report } = checks();

const noop = () => {};
const { emit, bridge } = loadBridge({
  audio: { speak: noop, speakClipOnly: noop, stop: noop, sfx: noop, playCloudTTS: noop } });
for (const response of GOLDEN.execute_script) {
  const { _why, ...msg } = response;
  emit("/devices/d_test/commands/remote_chat", msg);
}
// Projected onto the keys BOTH clients are held to (`t` is the browser's documented delta).
const got = bridge.actionStats().applied
  .map((a) => Object.fromEntries(GOLDEN.applied_keys.map((k) => [k, a[k]])));
const want = GOLDEN.execute_expected;

ok(got.length === want.length,
   `the browser applied ${got.length} actions, the SIL robot ${want.length}: ${JSON.stringify(got)}`);
for (let i = 0; i < Math.max(got.length, want.length); i++)
  ok(JSON.stringify(got[i]) === JSON.stringify(want[i]),
     `applied[${i}] disagrees with ${GOLDEN.reference_client}:\n       browser ${JSON.stringify(got[i])}` +
     `\n       robot   ${JSON.stringify(want[i])}`);

// The bug's two symptoms, named so a regression says WHICH half broke.
const armed = got.find((a) => a.function === "eb_enable_qr");
ok(armed && JSON.stringify(armed.args) === '["true"]',
   `function_id names the execute and function_args ride with it; got ${JSON.stringify(armed)}`);
const mapped = got.find((a) => a.function === "eb_set_volume");
ok(mapped && mapped.args && mapped.args.level === "3" && mapped.args.fade === "true",
   `action_args decode to the {key: value} mapping they encode; got ${JSON.stringify(mapped)}`);
ok(got.some((a) => a.function === "eb_wins") && !got.some((a) => a.function === "eb_loses"),
   `function_id wins over the older \`function\`; got ${JSON.stringify(got.map((a) => a.function))}`);

report(`✅ action-payload parity OK — ${want.length} applied actions decoded identically to ` +
       `${GOLDEN.reference_client}`);
process.exit(0);   // the bridge's local-voice grace timer would otherwise hold the loop open
