/* Harness for `sim/test_mode.mjs`: the ledger, the fake deployment and the probe call. */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { repo, api, ledger } from "../common.mjs";

export { readFileSync, join, repo };
export const here = join(repo, "sim");
export const { fails, C, ok, eq, deep } = ledger();

export const lib = await api("_lib", "env.js");
export const env2 = await api("_lib", "envelope.js");
export const health = await api("health.js");
/** The REAL counters the probe reads; `__reset`/`__state`/`__exhaustBudget` drive them. */
export const limits = await api("_lib", "limits.js");

/** A fully-configured deployment (`.invalid.test`, a key the secret grep cannot mistake). */
export const FULL = {
  DEMO_GATEWAY_BASE_URL: "https://gw.invalid.test/v1",
  DEMO_GATEWAY_API_KEY: "sk-testonly-abcdefghijklmnop",
  DEMO_CHAT_MODEL: "test-brain-model",
  DEMO_TTS_MODEL: "test-voice-model",
  DEMO_STT_MODEL: "test-ears-model",
};

/** Call `/api/health` with a plain `context.env`. */
export async function probe(env) {
  const res = await health.onRequestGet({ env });
  const text = await res.clone().text();
  return { res, text, body: JSON.parse(text) };
}
