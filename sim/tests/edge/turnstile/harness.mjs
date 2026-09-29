/* Harness for `sim/test_turnstile.mjs`: the stubbed gateway and siteverify, the request
 * helpers and the no-leak sweep. See the entry file for what the suite proves.
 */
import {
  repo, api, ledger, BASE, KEY, ORIGIN, GATEWAY, BROWSER_HEADERS, post as apiPost, leakSweep, jsonOf,
  wavBytes,
} from "../common.mjs";

export { readdirSync, readFileSync } from "node:fs";
export { join } from "node:path";
export { repo, BASE, KEY, ORIGIN, GATEWAY };

export const { fails, C, ok, eq, deep } = ledger();

export const chat = await api("chat.js");
export const transcribe = await api("transcribe.js");
export const health = await api("health.js");
export const limits = await api("_lib", "limits.js");
export const envlib = await api("_lib", "env.js");
export const envelope = await api("_lib", "envelope.js");
export const ts = await api("_lib", "turnstile.js");

export const HOSTNAME = "demo.invalid.test";

/** The two widget actions, by route name; each is refused in the other's place. */
export const ACT = ts.TURNSTILE_ACTIONS;

/* Cloudflare's documented dummy keys (developers.cloudflare.com/turnstile/troubleshooting/testing/). */
export const SITEKEY = "1x00000000000000000000BB";          // always passes, invisible
export const SECRET_PASS = "1x0000000000000000000000000000000AA";
export const TOKEN = "XXXX.DUMMY.TOKEN.XXXX";

/** The gateway configured AND the bot control enforced: the production shape. */
export const ARMED = Object.assign({}, GATEWAY, {
  DEMO_TURNSTILE_SECRET: SECRET_PASS,
  DEMO_TURNSTILE_SITEKEY: SITEKEY,
});

/** Never in a response. The SITEKEY is absent on purpose: it is public and IS published. */
export const FORBIDDEN = [KEY, BASE, "gw.invalid.test", "test-brain-model", SECRET_PASS];

/** Every `error-codes` string Cloudflare can return; none may reach a response body. */
export const ERROR_CODES = ["missing-input-secret", "invalid-input-secret", "missing-input-response",
                            "invalid-input-response", "bad-request", "timeout-or-duplicate",
                            "internal-error"];

/* --------------------------------------------------------------------------- *
 * The stubbed world: one gateway, one siteverify
 * --------------------------------------------------------------------------- */
/** Every outbound request, in order. Cleared in place, never reassigned. */
export const sent = [];
/** `P.plan = { chat, turnstile }` — what the stubs answer next. */
export const P = { plan: {} };

/** Siteverify: the pass verdict for SECRET_PASS, else a failed one; `plan.turnstile` overrides. */
function siteverifyAnswer(form, opt) {
  const p = P.plan.turnstile || {};
  // Never answers, but honours `opt.signal` as a real fetch does (so an unwired deadline hangs).
  if (p.hang) {
    return new Promise((resolve, reject) => {
      const sig = opt && opt.signal;
      if (!sig) return;                       // no deadline wired: hang for ever, on purpose
      const bail = () => {
        const e = new Error("aborted");
        e.name = "AbortError";
        reject(e);
      };
      if (sig.aborted) return bail();
      sig.addEventListener("abort", bail, { once: true });
    });
  }
  if (p.throw) {
    const e = new Error("stub");
    e.name = p.throw;
    throw e;
  }
  if (p.status && p.status !== 200) {
    return new Response(p.text || "", {
      status: p.status, headers: { "Content-Type": "application/json" },
    });
  }
  if (p.text !== undefined) {
    return new Response(p.text, { status: 200, headers: { "Content-Type": "application/json" } });
  }
  const body = p.body || (() => {
    const secret = form.get("secret");
    if (secret === SECRET_PASS) {
      return {
        success: true,
        // siteverify's request carries no route, so a test names the action it is about.
        action: p.action || ACT.chat,
        hostname: HOSTNAME,
        challenge_ts: "2026-09-05T00:00:00.000Z",
      };
    }
    return { success: false, "error-codes": ["invalid-input-response"] };
  })();
  return new Response(JSON.stringify(body), {
    status: 200, headers: { "Content-Type": "application/json" },
  });
}

globalThis.fetch = async (url, opt) => {
  const u = String(url);
  sent.push({ url: u, opt });
  if (u === ts.SITEVERIFY_URL) {
    return siteverifyAnswer(new URLSearchParams(String((opt && opt.body) || "")), opt);
  }
  const p = P.plan.chat || {};
  if (p.throw) {
    const e = new Error("stub");
    e.name = p.throw;
    throw e;
  }
  if (u.includes("/audio/transcriptions")) {
    return new Response(JSON.stringify({ text: "i am a bot" }), {
      status: p.status || 200, headers: { "Content-Type": "application/json" },
    });
  }
  const content = p.content === undefined ? "Hi there! Want to hear a joke?" : p.content;
  return new Response(JSON.stringify({ choices: [{ message: { content } }] }), {
    status: p.status || 200, headers: { "Content-Type": "application/json" },
  });
};

/* --------------------------------------------------------------------------- *
 * Harness
 * --------------------------------------------------------------------------- */
export const req = (body, headers, path) => apiPost(path || "/api/chat", JSON.stringify(body), headers);

export function fresh() {
  limits.__reset();
  ts.__reset();
  sent.length = 0;
  P.plan = {};
}

/** The §4.2 sweep (`common.mjs::leakSweep`) plus the widget secret and every Cloudflare
 *  error code, on EVERY response this suite produces: one assertion per response. */
export async function assertClean(res, label) {
  C.sweeps += 1;
  await leakSweep(ok, res, [...FORBIDDEN, ...ERROR_CODES], label, { stripTopic: true });
}

/** POST to `/api/chat`, sweep the reply, and hand back everything a test asserts on. */
export async function post(body, env, headers) {
  const res = await chat.onRequestPost({ request: req(body, headers), env: env || ARMED });
  await assertClean(res, "chat " + JSON.stringify(body).slice(0, 48));
  return { res, body: await jsonOf(res), status: res.status };
}

/** A turn with the dummy token attached — the ordinary case. */
export const turn = (text, env) => post({ text: text || "hello moxie", [ts.TOKEN_FIELD]: TOKEN }, env);

/** A real 1 s RIFF (the route sniffs bytes), between the byte floor and the duration cap. */
export { wavBytes };
export const CLIP = wavBytes(1000);

/** The ears' env: ARMED plus an STT model; queue OFF so a leaked slot (rows D2/D2b) reddens
 *  a named check instead of hanging the suite. */
export const EARS = Object.assign({}, ARMED, {
  DEMO_STT_MODEL: "test-ears-model",
  DEMO_QUEUE_MAX_WAIT_MS: "0",
});

/** POST to `/api/transcribe`, sweep the reply, and hand back what a test asserts on. */
export async function postAudio(headers, env) {
  const request = new Request(ORIGIN + "/api/transcribe", {
    method: "POST",
    headers: Object.assign({ "Content-Type": "audio/wav" }, BROWSER_HEADERS, headers || {}),
    body: CLIP,
  });
  const res = await transcribe.onRequestPost({ request, env: env || EARS });
  await assertClean(res, "transcribe " + JSON.stringify(headers || {}).slice(0, 40));
  return { res, body: await jsonOf(res), status: res.status };
}

/** A microphone turn with the dummy token on the header the route reads. */
export const clip = (env) => postAudio({ [ts.TOKEN_HEADER]: TOKEN }, env);

export const gatewayCalls = () => limits.__state().stats.upstreamCalls;
export const refundedUnits = () => limits.__state().stats.refundedUnits;
/** Units the budget thinks are spent (both windows are charged alike). */
export const unitsSpent = () => Math.max(0, ...Object.values(limits.__state().budget), 0);
export const verifyCalls = () => ts.__stats().calls;
export const outcomes = () => ts.__stats().outcomes;
