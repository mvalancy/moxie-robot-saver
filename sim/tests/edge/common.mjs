/* Shared harness for the Cloudflare-edge node suites (`sim/test_demo_proxy.mjs`,
 * `sim/test_turnstile.mjs`, `sim/test_demo_ears.mjs`, `sim/test_demo_tickets.mjs`).
 *
 * The Pages Functions are plain ES modules, so every suite imports the real handlers and
 * calls them with a synthetic `Request` and a plain object as `context.env`, with `fetch`
 * stubbed. Nothing here needs a Cloudflare account, a gateway key or the network.
 */
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

export const repo = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "..");

/** Import a module under `functions/api/`, e.g. `api("_lib", "limits.js")`. */
export const api = (...parts) => import(join(repo, "functions", "api", ...parts));

/** One assertion ledger per suite. `C.asserts` counts every check made. */
export function ledger() {
  const fails = [];
  const C = { asserts: 0, sweeps: 0 };
  const ok = (c, m) => { C.asserts++; if (!c) fails.push(m); };
  const eq = (a, b, m) => ok(a === b, `${m} — got ${JSON.stringify(a)}, want ${JSON.stringify(b)}`);
  const deep = (a, b, m) => eq(JSON.stringify(a), JSON.stringify(b), m);
  return { fails, C, ok, eq, deep };
}

/* The fake deployment. The host is RFC 6761 `.invalid` (unresolvable, so a stub that
 * leaked a real request could reach nothing) and the key is shaped so the repo's
 * pre-commit secret grep cannot mistake it for a real one. */
export const BASE = "https://gw.invalid.test/v1";
export const KEY = "sk-testonly-abcdefghijklmnopqrstuv";
export const ORIGIN = "https://demo.invalid.test";
export const GATEWAY = Object.freeze({
  DEMO_GATEWAY_BASE_URL: BASE,
  DEMO_GATEWAY_API_KEY: KEY,
  DEMO_CHAT_MODEL: "test-brain-model",
});

/** The headers a real same-origin browser POST carries. */
export const BROWSER_HEADERS = Object.freeze({
  Origin: ORIGIN,
  "Sec-Fetch-Site": "same-origin",
  "CF-Connecting-IP": "203.0.113.9",
});

/** A same-origin POST to `path`; a non-string body is JSON-encoded. */
export function post(path, body, headers, contentType = "application/json") {
  return new Request(ORIGIN + path, {
    method: "POST",
    headers: Object.assign({ "Content-Type": contentType }, BROWSER_HEADERS, headers || {}),
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
}

/** `{text, headerText}` of a response, for the no-leak sweeps. */
export async function responseText(res) {
  const text = await res.clone().text();
  let headerText = "";
  for (const [k, v] of res.headers.entries()) headerText += k + ": " + v + "\n";
  return { text, headerText };
}

/** The response body as JSON, or `null`. */
export async function jsonOf(res) {
  try { return JSON.parse(await res.clone().text()); } catch { return null; }
}

/** A 16 kHz mono 16-bit RIFF/WAVE of `ms` milliseconds of silence — the shape
 *  `sim/web/mic.js::encodeWav` produces, which the route sniffs rather than trusting
 *  the Content-Type. */
export function wavBytes(ms, rate = 16000) {
  const samples = Math.round((rate * ms) / 1000);
  const bytes = new Uint8Array(44 + samples * 2);
  const view = new DataView(bytes.buffer);
  const wr = (off, str) => { for (let i = 0; i < str.length; i++) view.setUint8(off + i, str.charCodeAt(i)); };
  wr(0, "RIFF"); view.setUint32(4, 36 + samples * 2, true); wr(8, "WAVE");
  wr(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  wr(36, "data"); view.setUint32(40, samples * 2, true);
  return bytes;
}

/** Import each section module in order; each runs its assertions at top level. */
export async function runSections(dirUrl, names) {
  for (const n of names) await import(new URL(n, dirUrl));
}
