/* QR parity tests — the browser encoder (sim/web/qr.js) must emit BYTE-IDENTICAL payloads
 * to the Python generators it shadows: `moxie_toolkit.qr_codec` (revival codes the robot's
 * setup parser reads) and `moxie_sdk.launch_cards` (launch cards our cloud reads back).
 * A drift fails silently as a QR that just doesn't scan.
 * Grammar: docs/reverse-engineering/protocol/qr-commands.md (firmware v24.10.803).
 *
 * Run: node sim/test_qr.mjs
 */
import { readFileSync } from "node:fs";
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

// ---- 1. the firmware's IOTEndpoint enum (OPEN_MOXIE / EMBODIED_LOCAL home a stock robot) ----
ok(Q.ENDPOINTS.OPEN_MOXIE === 11 && Q.ENDPOINTS.EMBODIED_LOCAL === 8 && Q.ENDPOINTS.IOT_DEFAULT === 0 &&
   Object.keys(Q.ENDPOINTS).length === 12, `IOTEndpoint values as in firmware v24.10.803: ${JSON.stringify(Q.ENDPOINTS)}`);
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

// ---- 3. setup.html offers exactly the firmware's WifiBandSelect names ----------
const setup = readFileSync(join(here, "web", "setup.html"), "utf8");
for (const band of ["ANY", "ONLY_24G", "ONLY_50G"])
  ok(setup.includes(`"${band}"`), `setup.html missing band value ${band}`);

// ---- 6. launch cards: browser↔Python byte parity (T12) -----------------------
// A card feeds the robot's RUNTIME reader and is answered by our cloud, so it gets its own
// parity leg against the SDK and — being unauthenticated stranger input — a refusal leg
// ACROSS the boundary: built in the browser, refused by the real Python decoder.
// Ceiling: no physical Moxie has ever sent us an `eb-qr-event`.

// `PYTHONDONTWRITEBYTECODE` is load-bearing: a same-length one-character mutation of
// launch_cards.py can otherwise be served the stale .pyc (mtime is whole seconds).
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
// `encodeCard` throws by design: a refusal is reported as its named assertion, never as an
// uncaught exception that stops the file.
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

// ---- 7. the printed sheet: modules on paper, read back and decoded (P0-c) ----
// qrcode.js builds each card's module matrix at the pinned version/EC level, and
// `sim/tests/helpers_qr_matrix.py` decodes it as a scanner would before `launch_cards.decode`.
// Semantic, not byte-for-byte: segno and qrcode.js legitimately differ in pad placement.
// The one legitimate skip (no `segno`) is reported BY NAME; every other failure is red.
const SHEET_SCRIPT = [
  "import sys, json",
  "sys.path[:0] = ['mqtt', 'sim/tests']",
  "from moxie_sdk import launch_sheet as ls",
  "try:",
  "    deck = ls.cards_for()",
  "    v = ls.deck_version([p for _, _, p in deck])",
  "except RuntimeError as e:",
  "    print(json.dumps({'skip': str(e)})); raise SystemExit(0)",
  "print(json.dumps({'level': ls.ERROR_LEVEL.upper(), 'version': v,",
  "                  'geometry': ls.geometry(v), 'min_module_mm': ls.MIN_MODULE_MM,",
  "                  'cards': [{'id': i, 'label': l, 'payload': p} for i, l, p in deck]}))",
].join("\n");

const READ_SCRIPT = [
  "import sys, json",
  "sys.path[:0] = ['mqtt', 'sim/tests']",
  "import helpers_qr_matrix as qrm",
  "from moxie_sdk import launch_cards as lc",
  "req = json.load(sys.stdin)",
  "out = []",
  "for rows in req['matrices']:",
  "    grid = [[1 if ch == '1' else 0 for ch in row] for row in rows]",
  "    try:",
  "        text = qrm.read_payload(grid)",
  "    except Exception as e:",
  "        out.append({'error': type(e).__name__ + ': ' + str(e)}); continue",
  "    a = lc.decode(text)",
  "    out.append({'payload': text,",
  "                'module_id': None if a is None else a.module_id})",
  "print(json.dumps({'read': out}))",
].join("\n");

let sheetInfo = null;
let sheetSkip = cards ? "" : "moxie_sdk not importable";
if (cards) {
  // The SDK imported above, so anything failing from here is a defect: a crashing generator
  // must not report itself as a skip.
  try {
    const got = pyJSON(repo, SHEET_SCRIPT, {});
    if (got.skip) sheetSkip = got.skip;
    else sheetInfo = got;
  } catch (e) {
    fails.push("launch_sheet could not render the deck: " +
               String(e.message).split("\n").filter(Boolean).pop());
  }
}
if (sheetSkip) console.log("ℹ️  printed-sheet checks skipped —", sheetSkip);

if (sheetInfo) {
  // (a) the sheet prints exactly the payloads the browser would encode. Three generators
  //     now agree on one string: the derived Python catalog, the transcribed JS one, and
  //     the paper. The sheet is not a third transcription — it asks `launch_cards.encode`.
  const sheetIds = sheetInfo.cards.map((c) => c.id);
  ok(sheetIds.join(",") === CAT.slice().sort().join(","),
     "the printed sheet's ids differ from qr.js's catalog\n       sheet: " +
     sheetIds.join(",") + "\n       js:    " + CAT.slice().sort().join(","));
  for (const c of sheetInfo.cards)
    ok(c.payload === card(c.id),
       `sheet card ${c.id}: payload differs from encodeCard\n       sheet: ${c.payload}` +
       `\n       js:    ${card(c.id)}`);

  // (b) the geometry a parent's printer will produce. Millimetres, not pixels — the whole
  //     reason the sheet is inline SVG. The floor is practical print guidance, NOT a
  //     measurement of Moxie's camera, which our corpus does not describe.
  const g = sheetInfo.geometry;
  ok(g.module_mm >= sheetInfo.min_module_mm,
     `a printed module is ${g.module_mm.toFixed(2)} mm, under the ${sheetInfo.min_module_mm} mm floor`);
  ok(g.units === g.modules + 8, "the symbol must carry a 4-module quiet zone on every side");
  ok(g.grid_w_mm <= g.page_w_mm && g.used_h_mm <= g.page_h_mm,
     `the card grid (${g.grid_w_mm} x ${g.used_h_mm} mm) does not fit the printable box ` +
     `(${g.page_w_mm} x ${g.page_h_mm} mm) shared by A4 and US Letter`);

  // (c) the browser builds each card's symbol, and Python reads the payload back OUT of
  //     the modules. `qrcode(version, level)` is the vendored encoder the SIM already
  //     renders with, driven at the sheet's pinned geometry.
  const rawQr = new Function(
    readFileSync(join(here, "web", "vendor", "qrcode.js"), "utf8") + "; return qrcode;")();
  const matrixOf = (text) => {
    const q = rawQr(sheetInfo.version, sheetInfo.level);
    q.addData(text);
    q.make();
    const n = q.getModuleCount(), rows = [];
    for (let r = 0; r < n; r++) {
      let line = "";
      for (let c = 0; c < n; c++) line += q.isDark(r, c) ? "1" : "0";
      rows.push(line);
    }
    return rows;
  };
  const drawn = sheetInfo.cards.map((c) => matrixOf(c.payload));
  ok(new Set(drawn.map((m) => m.length)).size === 1 &&
     drawn[0].length === sheetInfo.geometry.modules,
     "every card in the deck must be drawn at one symbol size");

  // (d) …and a refusal drawn as real modules is still refused after being read back off
  //     them. The string is built by qr.js's ungated formatter, drawn by qr.js's encoder,
  //     read by our matrix reader and handed to the real decoder — the full paper path.
  const PAPER_REFUSALS = [
    ["out-of-catalog id on paper", Q.cardPayload("launch", "NOPE")],
    ["sleep card on paper",        Q.cardPayload("sleep")],
    ["launch_if_confirmed paper",  Q.cardPayload("launch_if_confirmed", "DM")],
  ];
  let back = null;
  try {
    back = pyJSON(repo, READ_SCRIPT,
                  { matrices: drawn.concat(PAPER_REFUSALS.map((r) => matrixOf(r[1]))) });
  } catch (e) {
    fails.push("the matrix reader crashed on browser-built symbols: " +
               String(e.message).split("\n").filter(Boolean).pop());
  }
  if (back) {
    for (let i = 0; i < sheetInfo.cards.length; i++) {
      const c = sheetInfo.cards[i], got = back.read[i];
      ok(got && got.payload === c.payload,
         `card ${c.id}: the modules read back as ${JSON.stringify(got)}, not ${c.payload}`);
      ok(got && got.module_id === c.id,
         `card ${c.id}: python decoded what the modules carry as ${JSON.stringify(got)}`);
    }
    for (let i = 0; i < PAPER_REFUSALS.length; i++) {
      const [name, str] = PAPER_REFUSALS[i];
      const got = back.read[sheetInfo.cards.length + i];
      ok(got && got.payload === str,
         `${name}: the modules did not carry ${JSON.stringify(str)} — ${JSON.stringify(got)}`);
      ok(got && got.module_id === null,
         `${name}: decode ACCEPTED ${JSON.stringify(str)} read off real modules as ` +
         JSON.stringify(got) + " — paper must not widen what a card may do");
    }
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
            (sheetInfo
              ? `, ${sheetInfo.cards.length} printed cards read back out of their own ` +
                `modules at ${sheetInfo.geometry.module_mm.toFixed(2)} mm/module`
              : " (sheet checks skipped)") +
            "");
