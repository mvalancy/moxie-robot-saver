/* 🎬 Does the BROWSER SIM decode an `execute`'s payload the way the SIL robot does?
 *
 * DoD criterion 4 says the SIM and a re-homed robot are drop-in replacements for each
 * other. PR #116 taught `sim/virtual_moxie.py` to act on `response_actions` and PR #119
 * put `function_id` (RemoteChat.proto field 7), `function_args` (8, `repeated string`)
 * and `action_args` (10, `repeated ActionArgsEntry{key, value}`) on the wire — but
 * `sim/web/bridge.js::applyAction` still read `entry.function` alone and no args at all.
 * So our own server's named `execute` rendered in the browser as `(unnamed)` while the
 * SIL robot named it: the two clients disagreed about the one verb that had just gained
 * a payload. This suite is that disagreement being made impossible.
 *
 * It loads the REAL `sim/web/bridge.js` with stubbed window/document/mqtt — no browser,
 * no network — drives it over `sim/tests/goldens/cloud_to_robot_actions.json`'s
 * `execute_script`, and asserts the applied actions equal `execute_expected` **entry by
 * entry, key by key**. That golden is the SAME document
 * `sim/tests/test_sim_client_parity.py` asserts `VirtualMoxie` reaches, so the equality
 * here is a claim about both clients and not two separate stories.
 *
 * ── The negative control ──────────────────────────────────────────────────────────────
 * An assertion that cannot fail proves nothing, and this repo has been bitten by exactly
 * that (nine browser suites that skipped for months and stayed green). So the fix is
 * REVERTED textually — `function_id ||` deleted from the name lookup, the args lookup
 * replaced by `null` — and the mutated bridge is run through the identical comparison,
 * which MUST fail, with the two symptoms named: the armed execute goes back to `""`
 * ("(unnamed)") and every `args` collapses to `[]`. Both mutations are asserted to have
 * actually changed the source, because a `replace()` that silently matched nothing would
 * make this control vacuous — the failure mode it exists to catch.
 *
 * Run: node sim/test_action_payload.mjs
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const SRC = readFileSync(join(here, "web", "bridge.js"), "utf8");
const GOLDEN = JSON.parse(
  readFileSync(join(here, "tests", "goldens", "cloud_to_robot_actions.json"), "utf8"));

const fails = [];
const ok = (cond, msg) => { if (!cond) fails.push(msg); };

// --------------------------------------------------------------------------------------
// A throwaway sandbox per bridge build. `(0, eval)` runs the IIFE in global scope, so the
// stubs are reset first and each build gets its own `actionState` closure.
// --------------------------------------------------------------------------------------
function drive(src) {
  const clickHandlers = {}, els = {}, clientRef = { c: null };
  const fakeEl = (id) => ({
    id, value: "", textContent: "", innerHTML: "", className: "", scrollTop: 0, scrollHeight: 0,
    addEventListener: (e, cb) => { if (e === "click" && id) clickHandlers[id] = cb; },
    appendChild: () => {},
    querySelector: () => ({ set textContent(v) {}, get textContent() { return ""; } }),
  });
  const noop = () => {};
  globalThis.window = {
    moxie: { setFace: noop, setSpeech: noop, setMotor: noop, getMotor: () => 16384,
             showIcons: noop, clearIcons: noop, setHeartLED: noop },
    addEventListener: noop,
    moxieAudio: { speak: noop, speakClipOnly: noop, stop: noop, sfx: noop, playCloudTTS: noop },
  };
  globalThis.location = { hostname: "127.0.0.1" };
  globalThis.document = {
    getElementById: (id) => (els[id] ||= fakeEl(id)),
    createElement: () => {
      const el = fakeEl();
      Object.defineProperty(el, "querySelector",
        { value: () => ({ set textContent(v) {} }) });
      return el;
    },
  };
  globalThis.mqtt = {
    connect: () => {
      const h = {};
      clientRef.c = { connected: true, on: (e, cb) => { h[e] = cb; }, subscribe: noop,
                      end: noop, publish: noop, _emit: (e, ...a) => h[e] && h[e](...a) };
      return clientRef.c;
    },
  };

  (0, eval)(src);
  if (!clickHandlers["bus-connect"]) throw new Error("bridge did not wire the connect button");
  clickHandlers["bus-connect"]();
  const client = clientRef.c;
  if (!client) throw new Error("bridge did not connect over mqtt");

  for (const response of GOLDEN.execute_script) {
    const msg = {};
    for (const [k, v] of Object.entries(response)) if (k !== "_why") msg[k] = v;
    client._emit("message", "/devices/d_test/commands/remote_chat",
                 Buffer.from(JSON.stringify(msg)));
  }
  // Projected onto the keys BOTH clients are held to — `t` is the browser's documented
  // delta (`client_only_keys`) and is not part of the comparison.
  return window.moxieBridge.actionStats().applied
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
ok(SRC.includes(NAME_FIX), `negative control cannot run: ${NAME_FIX} is not in bridge.js`);
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
   `NEGATIVE CONTROL: bridge.js with the payload fix reverted still matched the golden` +
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
