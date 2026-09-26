/* Does the BROWSER SIM decode an `execute`'s payload the way the SIL robot does?
 *
 * `function_id` (RemoteChat.proto field 7), `function_args` (8) and `action_args` (10)
 * must be read by `sim/web/bridge/actions.js::applyAction` exactly as `sim/virtual_moxie.py` reads
 * them. This drives the REAL bridge over `sim/tests/goldens/cloud_to_robot_actions.json`'s
 * `execute_script` and asserts the applied actions equal `execute_expected` entry by entry,
 * key by key — the same golden `sim/tests/test_sim_client_parity.py` holds VirtualMoxie to.
 *
 * NEGATIVE CONTROL: the fix is reverted textually (name lookup without `function_id`, args
 * lookup replaced by `null`) and the identical comparison MUST fail with both named
 * symptoms. Each mutation is asserted to have changed the source, so the control cannot
 * pass vacuously.
 *
 * Run: node sim/test_action_payload.mjs
 */
import { BRIDGE_SRC as SRC, loadBridge, readGolden } from "./bridge_harness.mjs";

const GOLDEN = readGolden("cloud_to_robot_actions.json");

const fails = [];
const ok = (cond, msg) => { if (!cond) fails.push(msg); };

// One fresh bridge per source; returns the applied actions projected onto the keys BOTH
// clients are held to (`t` is the browser's documented delta, `client_only_keys`).
function drive(src) {
  const noop = () => {};
  const { emit, bridge } = loadBridge({ src,
    audio: { speak: noop, speakClipOnly: noop, stop: noop, sfx: noop, playCloudTTS: noop } });
  for (const response of GOLDEN.execute_script) {
    const msg = {};
    for (const [k, v] of Object.entries(response)) if (k !== "_why") msg[k] = v;
    emit("/devices/d_test/commands/remote_chat", msg);
  }
  return bridge.actionStats().applied
    .map((a) => Object.fromEntries(GOLDEN.applied_keys.map((k) => [k, a[k]])));
}

// --------------------------------------------------------------------------------------
// 1. The real bridge reaches the decode the SIL robot reaches — named, not counted
// --------------------------------------------------------------------------------------
const got = drive(SRC);
const want = GOLDEN.execute_expected;

ok(got.length === want.length,
   `the browser applied ${got.length} actions over execute_script, the SIL robot ${want.length}: ` +
   JSON.stringify(got));
for (let i = 0; i < Math.max(got.length, want.length); i++) {
  const g = got[i], w = want[i];
  ok(JSON.stringify(g) === JSON.stringify(w),
     `applied[${i}] disagrees with ${GOLDEN.reference_client}:\n       browser ${JSON.stringify(g)}` +
     `\n       robot   ${JSON.stringify(w)}`);
}

// The two symptoms of the bug, named individually so a regression says WHICH half broke.
const armed = got.find((a) => a.function === "eb_enable_qr");
ok(armed && JSON.stringify(armed.args) === '["true"]',
   `the armed execute is named and carries its function_args; got ${JSON.stringify(armed)}`);
const mapped = got.find((a) => a.function === "eb_set_volume");
ok(mapped && mapped.args && mapped.args.level === "3" && mapped.args.fade === "true",
   `action_args decode to the {key: value} mapping they encode; got ${JSON.stringify(mapped)}`);
ok(got.some((a) => a.function === "eb_wins") && !got.some((a) => a.function === "eb_loses"),
   `function_id wins over the SIM's older \`function\`; got ${JSON.stringify(got.map((a) => a.function))}`);

// --------------------------------------------------------------------------------------
// 2. The negative control: revert the fix, and the SAME comparison must fail
// --------------------------------------------------------------------------------------
const NAME_FIX = 'entry.function_id || entry.function || ""';
const ARGS_FIX = /let args = entry\.function_args;\n(?:.*\n){2}\s*const recordedArgs =/;
ok(SRC.includes(NAME_FIX), `negative control cannot run: ${NAME_FIX} is not in bridge/`);
ok(ARGS_FIX.test(SRC), "negative control cannot run: the args lookup is not where it was");

let broken = SRC.replace(NAME_FIX, 'entry.function || ""');
broken = broken.replace(ARGS_FIX, "let args = null;\n    const recordedArgs =");
ok(broken !== SRC, "negative control mutated nothing — it would pass vacuously");

let controlFailed = false, controlErr = "";
try {
  const bad = drive(broken);
  controlFailed = JSON.stringify(bad) !== JSON.stringify(want);
  // …and it must fail for the REASON this slice exists, not by falling over.
  ok(bad.every((a) => JSON.stringify(a.args) === "[]"),
     `with the fix reverted every args must collapse to []; got ${JSON.stringify(bad.map((a) => a.args))}`);
  ok(!bad.some((a) => a.function === "eb_enable_qr"),
     `with the fix reverted the armed execute must go back to "(unnamed)"; got ${JSON.stringify(bad.map((a) => a.function))}`);
} catch (e) {
  controlErr = e && e.message;
}
ok(controlFailed,
   `NEGATIVE CONTROL: bridge/ with the payload fix reverted still matched the golden` +
   (controlErr ? ` (it threw instead: ${controlErr})` : "") +
   " — this suite would pass with the bug present and proves nothing");

if (fails.length) {
  console.log("❌ action-payload parity FAILED:");
  for (const f of fails) console.log("   -", f);
  process.exit(1);
}
console.log(`✅ action-payload parity OK — ${want.length} applied actions decoded identically to ` +
  `${GOLDEN.reference_client} (function_id/function_args/action_args + legacy \`function\`), ` +
  `negative control reverted the fix and went red`);
process.exit(0);   // the bridge's local-voice grace timer would otherwise hold the loop open
