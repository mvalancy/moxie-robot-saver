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

/** A fake DOM element: just enough for `sim/web/env.js` to paint its badge, pill and banner. */
export function fakeEl(id) {
  const el = {
    id: id || "", tagName: "SPAN", textContent: "", innerHTML: "", title: "", hidden: false,
    className: "", children: [], attrs: {},
    classList: {
      add: (c) => { if (!el.className.split(/\s+/).includes(c)) el.className = (el.className + " " + c).trim(); },
      remove: (c) => { el.className = el.className.split(/\s+/).filter((x) => x && x !== c).join(" "); },
      toggle: (c, on) => { on ? el.classList.add(c) : el.classList.remove(c); },
      contains: (c) => el.className.split(/\s+/).includes(c),
    },
    setAttribute: (k, v) => { el.attrs[k] = String(v); },
    getAttribute: (k) => (k in el.attrs ? el.attrs[k] : null),
    addEventListener: () => {},
    appendChild: (c) => { el.children.push(c); c.parentNode = el; return c; },
    insertBefore: (c) => { el.children.push(c); c.parentNode = el; return c; },
    remove: () => {},
    querySelector: (sel) => {
      const cls = sel.replace(/^\./, "");
      for (const c of el.children) if (c.classList.contains(cls)) return c;
      // env.js reads `.eb-text` out of innerHTML it just wrote: a lazily-created stand-in.
      el._sub = el._sub || {};
      return (el._sub[cls] = el._sub[cls] || fakeEl(cls));
    },
  };
  return el;
}
