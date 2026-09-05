/* helpers_route.mjs — run ONE real Pages Function against ONE real request, and print
 * what came back. A bridge, not a test.
 *
 * WHY IT EXISTS. `functions/api/transcribe.js` is the code the hosted site runs when a
 * visitor presses the microphone, and it is JavaScript. The audio that would prove it
 * hears real speech is made by `mqtt/moxie_sdk/tts.py`, and that is Python. Everything
 * hermetic about the route is already proven by `sim/test_demo_ears.mjs` with a stubbed
 * `fetch` and no key; everything live about the SPEECH side is proven by
 * `sim/tests/test_live_gateway_stt.py` without ever touching the route. The one claim
 * neither can make — **the shipped route carries real spoken words to a real gateway and
 * gets those words back** — needs the two languages in one process tree, so this file is
 * the seam: stdin/argv in, one JSON object out.
 *
 * It is deliberately dumb. It builds no audio, decides no thresholds, and knows nothing
 * about what a good transcript looks like; `test_live_hosted_ears.py` owns all of that.
 * The only thing this file adds is the ONE number a Python caller cannot see for itself:
 * `_lib/limits.js::__state().stats.upstreamCalls`, so the caller can assert its gateway
 * spend was exactly one rather than trusting that it was.
 *
 * IT NEVER PRINTS A CREDENTIAL. The key and the base URL arrive in the environment and go
 * only into the route's own `context.env`. What is printed is the route's response body
 * and headers, which `sim/test_demo_ears.mjs::assertClean` already proves carry neither —
 * and the Python caller re-checks that against the real strings anyway, because a proof
 * against `sk-testonly-…` is not a proof against the key that is actually in the room.
 *
 *   DEMO_GATEWAY_BASE_URL=… DEMO_GATEWAY_API_KEY=… DEMO_STT_MODEL=… \
 *     node sim/tests/helpers_route.mjs transcribe /path/to/utterance.wav
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..");

const [, , route, audioPath] = process.argv;
if (route !== "transcribe" || !audioPath) {
  console.error("usage: node sim/tests/helpers_route.mjs transcribe <audio-file>");
  process.exit(2);
}

const transcribe = await import(join(repo, "functions", "api", "transcribe.js"));
const limits = await import(join(repo, "functions", "api", "_lib", "limits.js"));

/* The origin the request claims to come from. `.invalid.test` is RFC 6761 reserved and
 * unresolvable, so nothing here can accidentally talk to a real deployment — the request
 * is answered in this process by the route module itself. It has to be a SINGLE origin
 * used for both the URL and the `Origin` header, because §4.3's pin defaults to "the
 * request's own origin" and a mismatch is `forbidden_origin` with no call made. */
const ORIGIN = "https://ears.invalid.test";

/* Everything the route reads is a plain object — the same shape Cloudflare hands a
 * Function, and the same one `sim/test_demo_ears.mjs` uses. The difference is that these
 * values are REAL, so the one `fetch()` inside the route leaves the machine. */
const env = {
  DEMO_GATEWAY_BASE_URL: process.env.DEMO_GATEWAY_BASE_URL || "",
  DEMO_GATEWAY_API_KEY: process.env.DEMO_GATEWAY_API_KEY || "",
  DEMO_STT_MODEL: process.env.DEMO_STT_MODEL || "",
};
/* `DEMO_CHAT_MODEL` is NOT optional even here: `_lib/env.js::readConfig` lists it in
 * `missing`, so without it `configured` is false and the ears answer
 * `gateway_not_configured` before reading a byte. The two Access halves are
 * `DEMO_GATEWAY_ACCESS_*`, not `DEMO_ACCESS_*` — the wrong name would look configured
 * and quietly not be. */
for (const k of ["DEMO_CHAT_MODEL", "DEMO_TTS_MODEL", "DEMO_STT_FORMATS", "DEMO_MAX_RECORD_MS",
                 "DEMO_STT_TIMEOUT_MS", "DEMO_GATEWAY_ACCESS_CLIENT_ID",
                 "DEMO_GATEWAY_ACCESS_CLIENT_SECRET"]) {
  if (process.env[k]) env[k] = process.env[k];
}

const bytes = new Uint8Array(readFileSync(audioPath));
const request = new Request(ORIGIN + "/api/transcribe", {
  method: "POST",
  // What `sim/web/mic.js`:275 actually sends: the encoded WAV as the RAW body, typed by
  // the blob. Not multipart — the multipart is built server-side, by the route.
  headers: { "Content-Type": "audio/wav", Origin: ORIGIN },
  body: bytes,
});

limits.__reset();
const started = Date.now();
const res = await transcribe.onRequestPost({ request, env });
const text = await res.text();

const headers = {};
res.headers.forEach((v, k) => { headers[k] = v; });

process.stdout.write(JSON.stringify({
  status: res.status,
  headers,
  body: text,
  upstream_calls: limits.__state().stats.upstreamCalls,
  elapsed_s: (Date.now() - started) / 1000,
  request_bytes: bytes.length,
}) + "\n");
