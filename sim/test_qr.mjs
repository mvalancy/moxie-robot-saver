/* QR parity tests — the browser encoder (sim/web/qr.js) must emit BYTE-IDENTICAL
 * payloads to the two Python generators it shadows: `moxie_toolkit.qr_codec`
 * (tools/robot-toolkit) for the revival codes the robot's own setup parser reads,
 * and `moxie_sdk.launch_cards` (mqtt/) for the launch cards OUR cloud reads back.
 *
 * This matters: the browser encoder is what lets someone revive a robot from a
 * phone with nothing installed (static deploy). If it drifts from the toolkit,
 * one of the two is producing codes the robot won't accept — and the failure is
 * silent (a QR that just doesn't scan). Grammar:
 * docs/reverse-engineering/qr-commands.md (firmware v24.10.803).
 *
 * Run: node sim/test_qr.mjs
 */
import { readFileSync } from "node:fs";
import { pageSource } from "./browser_harness.mjs";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..");
const fails = [];
const ok = (cond, msg) => { if (!cond) fails.push(msg); };

// ---- load the browser encoder in a bare global ------------------------------
globalThis.window = globalThis.window || {};
const src = readFileSync(join(here, "web", "qr.js"), "utf8");
new Function(src)();                 // qr.js is an IIFE assigning window.moxieQR
const Q = globalThis.window.moxieQR;
ok(!!Q, "qr.js must expose window.moxieQR");
if (!Q) { console.log("❌ qr tests FAILED:\n   - " + fails.join("\n   - ")); process.exit(1); }

// ---- 1. the firmware's IOTEndpoint enum values ------------------------------
// Baked into the shipped image; OPEN_MOXIE=11 and EMBODIED_LOCAL=8 are the two
// that let a stock robot home to a self-hosted server.
ok(Q.ENDPOINTS.OPEN_MOXIE === 11, "OPEN_MOXIE must be 11");
ok(Q.ENDPOINTS.EMBODIED_LOCAL === 8, "EMBODIED_LOCAL must be 8");
ok(Q.ENDPOINTS.IOT_DEFAULT === 0, "IOT_DEFAULT must be 0");
ok(Object.keys(Q.ENDPOINTS).length === 12, "IOTEndpoint has 12 values");
let threw = false;
try { Q.encodeEndpoint("NOT_A_REAL_ENDPOINT"); } catch { threw = true; }
ok(threw, "encodeEndpoint must reject an unknown endpoint name");

// ---- 2. byte-parity with the Python toolkit ---------------------------------
const CASES = [
  ["endpoint OPEN_MOXIE",     () => Q.encodeEndpoint("OPEN_MOXIE"),
   "q.encode_endpoint_update('OPEN_MOXIE')"],
  ["endpoint EMBODIED_LOCAL", () => Q.encodeEndpoint("EMBODIED_LOCAL"),
   "q.encode_endpoint_update('EMBODIED_LOCAL')"],
  ["debug reset_network",     () => Q.encodeDebug("reset_network"),
   "q.encode_debug('reset_network')"],
  ["debug with param",        () => Q.encodeDebug("bluetooth_pair", "AA:BB:CC"),
   "q.encode_debug('bluetooth_pair', 'AA:BB:CC')"],
  ["wifi plain",              () => Q.encodeWifi("MyNet", "pw"),
   "q.encode_wifi('MyNet', 'pw')"],
  ["wifi hidden + 5GHz",      () => Q.encodeWifi("Hid", "s3cret", { hidden: true, band: "BAND_5G" }),
   "q.encode_wifi('Hid', 's3cret', is_hidden=True, band_select='BAND_5G')"],
  ["wifi open network",       () => Q.encodeWifi("Guest", ""),
   "q.encode_wifi('Guest', '')"],
];

let py = null;
try {
  // qr_codec imports generated protos that live beside it, so both dirs go on the path
  const script = "import sys;sys.path[:0]=['.','moxie_toolkit']\n" +
    "from moxie_toolkit import qr_codec as q\n" +
    "print('\\n'.join([" + CASES.map((c) => c[2]).join(",") + "]))";
  py = execFileSync("python3", ["-c", script],
                    { cwd: join(repo, "tools", "robot-toolkit"), encoding: "utf8",
                      env: Object.assign({}, process.env, { PYTHONDONTWRITEBYTECODE: "1" }) })
        .trim().split("\n");
} catch (e) {
  console.log("ℹ️  python toolkit not importable (skipped parity) —",
              String(e.message).split("\n").pop());
}

for (let i = 0; i < CASES.length; i++) {
  const [name, jsFn] = CASES[i];
  const js = jsFn();
  ok(JSON.parse(js) !== null, `${name}: JS output is not valid JSON`);
  if (py) ok(js === py[i], `${name}: JS/py mismatch\n       js: ${js}\n       py: ${py[i]}`);
}

// ---- 3. the shape the firmware's parser expects ------------------------------
const ep = JSON.parse(Q.encodeEndpoint("OPEN_MOXIE"));
ok(ep.debug && ep.debug.command === "endpoint_update" && ep.debug.param === "OPEN_MOXIE",
   "endpoint QR must be {debug:{command:'endpoint_update',param:<enum name>}}");
const wf = JSON.parse(Q.encodeWifi("N", "p"));
ok(wf.wifi && "ssid" in wf.wifi && "password" in wf.wifi &&
   "is_hidden" in wf.wifi && "band_select" in wf.wifi,
   "wifi QR needs ssid/password/is_hidden/band_select");
ok(typeof wf.wifi.is_hidden === "boolean", "is_hidden must be a bool, not a string");
for (const cmd of ["serial_number_display", "restore_factory", "reset_network",
                   "bluetooth_pair", "endpoint_update"])
  ok(cmd in Q.KNOWN_DEBUG, `KNOWN_DEBUG missing documented command ${cmd}`);

// ---- 4. the HUD panel is actually wired --------------------------------------
const html = pageSource("sim.html");
for (const id of ["qr-kind", "qr-make", "qr-canvas", "qr-status", "qr-ssid", "qr-pass"])
  ok(html.includes(`id="${id}"`), `sim.html missing #${id}`);
ok(/vendor\/qrcode\.js/.test(html), "sim.html must load the vendored qrcode.js");
ok(/src="qr\.js(\?[^"]*)?"/.test(html), "sim.html must load qr.js");
ok(html.includes("setup.html"), "sim HUD should link to the full static setup page");

// ---- 5. the standalone parent-app "basics" page is wired ---------------------
// setup.html is the phone-first, server-free revival page — the parent-app basics
// hosted statically. It must reuse qr.js (not reimplement the encoders) so it can
// never drift from the byte-parity guarantee above.
/* Page + its own scripts: the form glue moved from an inline block to `setup.js`
 * on 2026-09-04 (see `sim/web/_headers`). */
const setup = pageSource("setup.html");
ok(setup.includes("vendor/qrcode.js") && setup.includes('src="qr.js"'),
   "setup.html must load the vendored qrcode.js + qr.js");
ok(setup.includes("moxieQR.encodeWifi") || setup.includes("Q.encodeWifi"),
   "setup.html must build the Wi-Fi code via qr.js (no reimplemented encoder)");
ok(setup.includes("moxieQR.encodeEndpoint") || setup.includes("Q.encodeEndpoint"),
   "setup.html must build the server code via qr.js");
for (const id of ["ssid", "pass", "band", "endpoint", "cv-wifi", "cv-ep"])
  ok(setup.includes(`id="${id}"`), `setup.html missing #${id}`);
// the band values must be the firmware's WifiBandSelect enum names
for (const band of ["ANY", "ONLY_24G", "ONLY_50G"])
  ok(setup.includes(`"${band}"`), `setup.html missing band value ${band}`);

// ---- 6. launch cards: browser↔Python byte parity (T12) -----------------------
// A launch card is NOT one of the seven payloads above. Those feed the robot's setup
// scanner; a card feeds its runtime reader and is answered by OUR cloud
// (`mqtt/moxie_sdk/launch_cards.py`). So it gets its own parity leg, its own python
// process (the SDK, not the toolkit) and — because a card is an unauthenticated input
// a stranger can print — its own refusal leg. The refusals are asserted ACROSS the
// boundary: the string is built in the browser and refused by the real Python decoder,
// not merely refused inside Python by a string Python also wrote.
//
// Ceiling, unchanged by any of this: **no physical Moxie has ever sent us an
// `eb-qr-event`**. This proves two generators agree, and nothing about a camera.

// `PYTHONDONTWRITEBYTECODE` is load-bearing, not hygiene. A `.pyc` is revalidated
// against its source's (mtime-in-whole-seconds, size), so a run that edits
// `launch_cards.py` without changing its length — which is exactly what a one-character
// mutation does — can be served the PREVIOUS module out of `mqtt/moxie_sdk/__pycache__`.
// That silently under-reports a mutation run by ~49 assertions; it did, here, before this
// line existed. Writing no bytecode means the collision can never be set up.
function pyJSON(cwd, script, payload) {
  return JSON.parse(execFileSync("python3", ["-c", script],
    { cwd: cwd, input: JSON.stringify(payload), encoding: "utf8",
      env: Object.assign({}, process.env, { PYTHONDONTWRITEBYTECODE: "1" }) }));
}

const CARD_SCRIPT = [
  "import sys, json",
  "sys.path[:0] = ['.']",
  "from moxie_sdk import launch_cards as lc",
  "req = json.load(sys.stdin)",
  "ids = sorted(lc._catalog())",
  "out = {'catalog': ids, 'encoded': [lc.encode(i) for i in ids],",
  "       'encoded_cid': lc.encode('DRAW', 'x'), 'decoded': []}",
  "for s in req['strings']:",
  "    a = lc.decode(s)",
  "    out['decoded'].append(None if a is None else",
  "        {'module_id': a.module_id, 'content_id': a.content_id or ''})",
  "print(json.dumps(out))",
].join("\n");

// --- the browser half, provable with no python at all -------------------------
// `encodeCard` throws by design, so every call goes through this: a refusal must be
// reported as the named assertion it breaks, never as an uncaught exception that stops
// the file before the parity leg below has run.
const card = (id, cid) => {
  try { return Q.encodeCard(id, cid); } catch (e) { return "<refused: " + e.message + ">"; }
};
const CAT = Q.LAUNCHABLE_MODULE_IDS;
ok(Array.isArray(CAT) && CAT.length === 24, `qr.js must carry the 24-id card catalog (has ${(CAT || []).length})`);
ok(new Set(CAT).size === CAT.length, "the card catalog must not repeat an id");
ok(Q.CARD_PREFIX === "GO", "the card marker is the literal GO");
ok(card("DM") === "GO<launch:DM>", `encodeCard('DM') must be GO<launch:DM>, got ${card("DM")}`);
ok(card("DRAW", "x") === "GO<launch:DRAW:x>", `a content id rides as a third field, got ${card("DRAW", "x")}`);
ok(card("DRAW", "") === "GO<launch:DRAW>", `an empty content id adds no separator, got ${card("DRAW", "")}`);
// The print-side gate: the browser must not be ABLE to make paper the server refuses.
const GATE_REFUSALS = ["NOPE", "dm", "", "DM ", "DM:DRAW", null, undefined, 7];
for (const bad of GATE_REFUSALS) {
  let threwCard = false;
  try { Q.encodeCard(bad); } catch { threwCard = true; }
  ok(threwCard, `encodeCard must refuse ${JSON.stringify(bad)} — it is not a catalog id`);
}

// --- strings the BROWSER builds, for python to accept or refuse ---------------
const cardStrings = CAT.map((id) => card(id));
const cidString = card("DRAW", "x");
// Every one of these is composed with qr.js's own formatter, so each is a string a
// browser can really emit — that is the point. `cardPayload` is the browser's
// `virtual_moxie.py --face-value`.
const REFUSALS = [
  ["launch_if_confirmed card", Q.cardPayload("launch_if_confirmed", "DM")],
  ["sleep card",               Q.cardPayload("sleep")],
  ["exit card",                Q.cardPayload("exit")],
  ["out-of-catalog id",        Q.cardPayload("launch", "NOPE")],
  ["two launches in one card", Q.cardPayload("launch", "DM") + Q.cardPayload("launch", "DRAW")],
];
ok(REFUSALS[0][1] === "GO<launch_if_confirmed:DM>", "the launch_if_confirmed string is built, not assumed");
ok(REFUSALS[1][1] === "GO<sleep>", "the sleep string is built, not assumed");
ok(REFUSALS[2][1] === "GO<exit>", "the exit string is built, not assumed");
ok(REFUSALS[3][1] === "GO<launch:NOPE>", "the out-of-catalog string is built, not assumed");
ok(REFUSALS[4][1] === "GO<launch:DM>GO<launch:DRAW>", "the two-tag string is built, not assumed");

let cards = null;
try {
  cards = pyJSON(join(repo, "mqtt"), CARD_SCRIPT,
                 { strings: cardStrings.concat([cidString], REFUSALS.map((r) => r[1])) });
} catch (e) {
  console.log("ℹ️  moxie_sdk.launch_cards not importable (skipped card parity) —",
              String(e.message).split("\n").pop());
}

if (cards) {
  // (a) the catalogs are the same set of ids. qr.js transcribes; python derives from
  //     schedule.ONBOARD_MODULES. This assertion is the only thing keeping the two equal.
  ok(CAT.slice().sort().join(",") === cards.catalog.join(","),
     "qr.js catalog != launch_cards._catalog()\n       js: " + CAT.slice().sort().join(",") +
     "\n       py: " + cards.catalog.join(","));
  // (b) byte identity, id by id — the EXACT string, not a parsed or normalised form.
  for (let i = 0; i < cards.catalog.length; i++) {
    const id = cards.catalog[i], pyStr = cards.encoded[i];
    let jsStr = null;
    try { jsStr = Q.encodeCard(id); } catch (e) { jsStr = "<threw: " + e.message + ">"; }
    ok(jsStr === pyStr,
       `card ${id}: JS/py byte mismatch\n       js: ${jsStr}\n       py: ${pyStr}`);
  }
  ok(cidString === cards.encoded_cid,
     `card DRAW:x: JS/py byte mismatch\n       js: ${cidString}\n       py: ${cards.encoded_cid}`);
  // (c) the round trip: encoded in the browser, decoded by the real python decoder,
  //     back to the same module id.
  for (let i = 0; i < CAT.length; i++) {
    const got = cards.decoded[i];
    ok(got && got.module_id === CAT[i] && got.content_id === "",
       `card ${CAT[i]}: python decoded the browser payload as ${JSON.stringify(got)}`);
  }
  const cidBack = cards.decoded[CAT.length];
  ok(cidBack && cidBack.module_id === "DRAW" && cidBack.content_id === "x",
     `card DRAW:x round trip lost the content id: ${JSON.stringify(cidBack)}`);
  // (d) the refusals travel the boundary too.
  for (let i = 0; i < REFUSALS.length; i++) {
    const [name, str] = REFUSALS[i];
    const got = cards.decoded[CAT.length + 1 + i];
    ok(got === null,
       `${name}: python ACCEPTED a browser-built ${JSON.stringify(str)} as ` +
       JSON.stringify(got) + " — it must decode to None");
  }
}

// ---- report ------------------------------------------------------------------
if (fails.length) {
  console.log("❌ qr tests FAILED:");
  for (const f of fails) console.log("   -", f);
  process.exit(1);
}
console.log(`✅ qr tests OK — ${CASES.length} revival payloads` +
            (py ? " byte-identical to the python toolkit" : " (parity skipped)") +
            `, ${CAT.length + 1} launch-card payloads` +
            (cards ? " byte-identical to moxie_sdk.launch_cards (+ " + REFUSALS.length +
                     " refusals refused across the boundary)" : " (parity skipped)") +
            ", enum + HUD + setup.html wiring verified`".slice(0, -1));
