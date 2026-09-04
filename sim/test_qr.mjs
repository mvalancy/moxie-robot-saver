/* Revival-QR parity tests — the browser encoder (sim/web/qr.js) must emit
 * BYTE-IDENTICAL payloads to the Python toolkit (tools/robot-toolkit
 * moxie_toolkit.qr_codec), because both feed the same firmware parser.
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
                    { cwd: join(repo, "tools", "robot-toolkit"), encoding: "utf8" })
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

// ---- report ------------------------------------------------------------------
if (fails.length) {
  console.log("❌ qr tests FAILED:");
  for (const f of fails) console.log("   -", f);
  process.exit(1);
}
console.log(`✅ qr tests OK — ${CASES.length} payloads` +
            (py ? " byte-identical to the python toolkit" : " (parity skipped)") +
            ", enum + HUD + setup.html wiring verified`".slice(0, -1));
