/* test_mode §5–6: `sim/web/env.js` driven by the MODE on a fake DOM, and the public-repo lint. */
import {
  readFileSync, join, here, repo, ok, eq, fakeEl,
} from "./harness.mjs";

const ENV_SRC = readFileSync(join(here, "web", "env.js"), "utf8");

// 5. sim/web/env.js — badge, pill, banner and needs-backend marks, driven by the MODE and not
//    the hostname. Hermetic: a fake DOM (`harness.mjs::fakeEl`), no browser.
function mountEnv(snapshot) {
  const els = {};
  const get = (id) => (els[id] = els[id] || fakeEl(id));
  const linkstate = fakeEl("linkstate");
  const bar = fakeEl("topbar");
  bar.appendChild(linkstate);
  const body = fakeEl("body");
  let cb = null;
  globalThis.location = { protocol: "https:", hostname: "sim.example", origin: "https://sim.example" };
  globalThis.document = {
    body,
    getElementById: (id) => (["tts-test", "speech-btn", "mic-btn", "bus-connect", "mic-status",
                              "bus-status", "tts-status", "memory-hint"].includes(id) ? get(id) : null),
    querySelector: (sel) => (sel === "#topbar .linkstate" ? linkstate : null),
    createElement: (tag) => { const e = fakeEl(); e.tagName = String(tag).toUpperCase(); return e; },
  };
  get("bus-status").textContent = "not connected";
  get("memory-hint").hidden = true;              // as sim.html ships it
  const hints = [];
  globalThis.window = {
    moxieAudio: { setTtsHint: (h) => hints.push(h), hasCloudVoice: () => false, isSpeaking: () => false },
    moxieMode: snapshot === null ? undefined : {
      snapshot: () => snapshot,
      onChange: (fn) => { cb = fn; fn(snapshot); return () => {}; },
    },
  };
  globalThis.localStorage = { getItem: () => null, setItem: () => {} };
  globalThis.fetch = () => Promise.resolve({ ok: false });
  (0, eval)(ENV_SRC);
  const badge = bar.children.find((c) => c.className.includes("env-badge"));
  const pill = bar.children.find((c) => c.className.startsWith("mode-pill"));
  const banner = body.children.find((c) => c.id === "env-banner");
  return {
    badge, pill, banner, hints, body,
    el: get,
    push(next) { snapshot = next; if (cb) cb(next); },
    get bannerText() { return banner ? banner.querySelector(".eb-text").innerHTML : ""; },
  };
}

const snap = (over) => Object.assign({
  state: "degraded", reason: "gateway_not_configured", badge: "HOSTED DEMO", message: "",
  level: "ok", load: { level: "ok", inflight: 0, capacity: 4 }, limits: {},
  voice: false, ears: false, liveTurns: false, retryAfterS: 0,
}, over);

{
  // The fail-safe rendering: nothing configured => the page is exactly today's.
  const v = mountEnv(snap({}));
  eq(v.badge.textContent, "HOSTED DEMO", "not configured: today's badge");
  eq(v.pill.hidden, true, "not configured: no pill");
  eq(v.body.getAttribute("data-env"), "hosted", "the hostname still decides data-env");
  eq(v.body.getAttribute("data-mode"), "degraded", "...and the MODE is published too");
  ok(v.el("mic-btn").classList.contains("needs-backend"), "no ears => the mic is marked");
  ok(v.el("bus-connect").classList.contains("needs-backend"), "the live-robot link is always marked");
  ok(v.el("tts-test").classList.contains("needs-backend"), "no local Piper => TTS test is marked");
  ok(/only pre&#8209;scripted lines have audio/.test(v.hints.map((h) => h.html).join(" ")),
     "not configured: today's exact TTS wording");
  ok(/scripted child line/.test(v.el("mic-status").innerHTML), "not configured: today's mic wording");
  ok(/need a locally/.test(v.bannerText), "not configured: today's banner");
  eq(v.el("memory-hint").hidden, true, "not configured: no memory line (a scripted page remembers nothing)");

  // ...then the deployment turns out to be live. Same page object, honest new words.
  v.push(snap({ state: "live", reason: null, badge: "MOXIE ONLINE", message: "",
                voice: true, ears: true, liveTurns: true }));
  eq(v.badge.textContent, "MOXIE ONLINE", "live: the visitor-facing badge says so plainly");
  eq(v.body.getAttribute("data-mode"), "live", "live: data-mode follows");
  eq(v.pill.hidden, true, "live and idle: nothing to apologise for");
  ok(!v.el("mic-btn").classList.contains("needs-backend"),
     "live ears => the mic mark is REMOVED (env.js:100 used to assert it unconditionally)");
  ok(!/needs the STT server/i.test(v.el("mic-btn").getAttribute("title") || ""),
     `...and its tooltip stops claiming a local server (got "${v.el("mic-btn").getAttribute("title")}")`);
  ok(v.el("bus-connect").classList.contains("needs-backend"),
     "...but a REAL robot's broker is still not available here, in every mode");
  ok(/own voice is live/.test(v.hints[v.hints.length - 1].html), "live: the voice line is honest");
  ok(/live brain answers on this page/.test(v.bannerText), "live: the banner stops claiming otherwise");
  // What she keeps used to end this banner, which a live hosted page hides (style.css): it is
  // said under the composer now, where it can be read (W4-S6).
  eq(v.el("memory-hint").hidden, false, "live: the page says what she keeps (#memory-hint shown)");
  ok(!/forgets|remembers/.test(v.bannerText), "live: …and the hidden banner no longer carries a second copy");

  // ...then she gets busy.
  v.push(snap({ state: "live", reason: "at_capacity", badge: "HOSTED DEMO · BUSY",
                message: "Moxie has her hands full right now.", level: "full",
                voice: true, ears: true, liveTurns: false }));
  eq(v.badge.textContent, "HOSTED DEMO · BUSY", "busy: the badge changes");
  eq(v.pill.hidden, false, "busy: the pill appears");
  eq(v.pill.textContent, "Moxie has her hands full right now.", "busy: the pill carries §7's copy");
  eq(v.pill.title, "Moxie has her hands full right now.",
     "...and the title too, because the pill's text is dropped at phone widths");
  ok(v.pill.className.includes("level-full"), "busy: the pill is styled by level");
  ok(v.pill.getAttribute("aria-live") === "polite", "the pill is announced, not just shown");

  // ...then the budget runs out.
  v.push(snap({ state: "degraded", reason: "budget_exhausted", badge: "HOSTED DEMO · SCRIPTED",
                message: "Moxie’s live brain is out of demo budget — back in about 17 minutes.",
                level: "ok" }));
  eq(v.badge.textContent, "HOSTED DEMO · SCRIPTED", "budget spent: the badge says scripted");
  eq(v.pill.hidden, false, "budget spent: the pill explains");
  ok(v.el("mic-btn").classList.contains("needs-backend"), "budget spent: the mic is marked again");
  // NOT "need a locally-run backend": this deployment HAS a brain, out until the budget resets.
  // ONE SOURCE (W4-S6): the banner says the pill's own sentence — they read "today's demo
  // budget" and "try again later" for the same hour.
  eq(v.bannerText, v.pill.textContent, "budget spent: the banner says exactly what the pill says");
  ok(!/locally/.test(v.bannerText), `budget spent: …and never that the site needs a local backend (${v.bannerText})`);
  eq(v.el("memory-hint").hidden, false, "budget spent: the memory line stays (still true of this page's chat)");
}
{
  // mode.js absent entirely (a fork that did not copy it): the page must be today's.
  const v = mountEnv(null);
  eq(v.badge.textContent, "HOSTED DEMO", "no mode.js: today's badge");
  eq(v.pill.hidden, true, "no mode.js: no pill");
  eq(v.body.getAttribute("data-mode"), "boot", "no mode.js: data-mode says boot");
  ok(v.el("mic-btn").classList.contains("needs-backend"), "no mode.js: today's marks");
  ok(/only pre&#8209;scripted lines have audio/.test(v.hints.map((h) => h.html).join(" ")),
     "no mode.js: today's exact wording");
}

// 6. C1, as a repo lint: the repo is PUBLIC, so no key, account id or deployment hostname in
//    these files — comments included (a real key in a comment is still leaked).
{
  const FORBIDDEN = [
    [/mattvalancy/i, "a deployment hostname"],
    [/graphlings/i, "the gateway hostname"],
    [/\b(?:sk|pk|rk)-[A-Za-z0-9_-]{12,}/, "a key-shaped token"],
    [/\b[0-9a-f]{32}\b/, "a Cloudflare account id"],
  ];
  const files = [
    "functions/api/health.js",
    "functions/api/_lib/env.js",
    "functions/api/_lib/envelope.js",
    "sim/web/mode.js",
    "sim/web/env.js",
  ];
  for (const rel of files) {
    const text = readFileSync(join(repo, rel), "utf8");
    for (const [rx, what] of FORBIDDEN)
      ok(!rx.test(text), `${rel} must not contain ${what} (${rx})`);
  }
  // wrangler.toml is committed and world-readable, so it may never carry variables.
  const wrangler = readFileSync(join(repo, "wrangler.toml"), "utf8");
  ok(!/^\s*\[vars\]/m.test(wrangler), "wrangler.toml must have NO [vars] block — it is public");
  ok(!/account_id/.test(wrangler), "wrangler.toml must carry no account id");
}
