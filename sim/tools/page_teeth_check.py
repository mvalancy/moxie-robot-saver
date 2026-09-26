"""Serve every browser suite a DELIBERATELY BROKEN site, and report which checks stay green.

The mutation checkers beside this file break the PRODUCT and require a test to redden.
This turns the same proof on the PAGE the browser suites load: delete a script, serve one
200 OK and inert, 404 a fetch, empty a document, stall one resource past every wait — and
require the suites that load it to notice.

Why: several checks here were found passing against a genuinely broken system, each by
luck. Most shared one shape — *the check samples a page that has not finished being a
page, or matches markup present whether or not the page worked* (e.g. `article p`
matching the static "Loading docs…" spinner; `naturalWidth` read before decode;
`check_deployed` printing failed requests and asserting none). This is the sweep for them.

    python3 sim/tools/page_teeth_check.py --baseline-dir /tmp/teeth   # the full sweep
    python3 sim/tools/page_teeth_check.py --selftest      # prove the tool works, ~1 min
    python3 sim/tools/page_teeth_check.py --suite test_csp --breakage qr-inert
    python3 sim/tools/page_teeth_check.py --check-tree    # nothing was left mutated
    python3 sim/tools/page_teeth_check.py --slow 6 --baseline-dir /tmp/teeth

The full sweep takes hours (a suite whose waits all expire runs far longer broken than
healthy); `--baseline-dir` caches the healthy run so one row can be re-read in a minute.

`--slow N` finds the OTHER member of the family, which no breakage can: an assertion that
compares a live sample against recorded state (or two samples at different instants) —
green because the page is faster than the suite guessed. `Emulation.setCPUThrottlingRate`
slows everything inside the renderer while node's own timers keep full speed, which is
what a busy CI runner does. A finding is a check GREEN at full speed and RED throttled,
with the site untouched; checks whose own message is about time are set aside, not mixed
in. Reproduce a race by CREATING it, never by waiting for it.

How a finding is decided:
  · **Exposure is measured.** `teeth_ledger.mjs` records every URL each suite requested
    in its HEALTHY run; a suite is in scope for a breakage only if it fetched the target.
    A green under a breakage the suite never touched is noise that sends someone to "fix"
    a working test.
  · TIER A — an exposed suite still exited 0. No judgement call.
    TIER B — the suite reddened, but a check whose message CLAIMS to cover the broken thing
    (`claims` regex) stayed green. Hand-verify every row before believing it.
  · **Vanished ≠ green.** A check that stopped running is reported separately (absent,
    not toothless).

Never touched: the RESERVED files below (owned by other sessions), even transiently. Every
mutation is reverted in a `finally`, journaled for SIGKILL recovery, and `--check-tree`
verifies the worktree is byte-clean.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import time

WT = pathlib.Path(__file__).resolve().parents[2]
LEDGER_HOOK = WT / "sim" / "tools" / "teeth_ledger.mjs"

# The in-flight mutation journal: a SIGKILL skips the `finally`, so the target is written
# here BEFORE it is touched and removed after restore; `--check-tree [--restore]` reads it
# (every target is tracked, so `git checkout -- <path>` is the whole repair). It lives in
# the real git dir — asked for, not composed, because in a linked worktree `.git` is a FILE.
def _git_dir() -> pathlib.Path:
    r = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], cwd=WT,
                       capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return pathlib.Path(r.stdout.strip())
    return WT                                   # not a checkout: keep it beside the tree


JOURNAL = _git_dir() / "page-teeth-active"

# Reserved by other sessions; refused at mutation time so a future row cannot add one.
RESERVED = {
    "sim/web/sim.html",
    "sim/web/ambient.js",
    "sim/web/style.css",
    "sim/check_deployed.mjs",
}

# ---------------------------------------------------------------------------
# The suites: every browser-launching suite, as (module stem, uses makeChecks, argv).
# The three without `makeChecks` roll their own counters, so only their EXIT CODE can be
# audited — printed in the report rather than hidden. `check_deployed --selftest` is
# hermetic and the most on-point target (it printed failures and asserted none); auditing
# a reserved file is not editing it.
# ---------------------------------------------------------------------------
SUITES = [
    ("check_deployed", True, ["--selftest"]),
    ("test_a11y", True, []),
    ("test_ambient_guard", True, []),
    ("test_api_headers", True, []),
    ("test_bg_perf", True, []),
    ("test_console_insights", True, []),
    ("test_csp", True, []),
    ("test_docs_explorer", True, []),
    ("test_env_hosted", False, []),
    ("test_liveliness", True, []),
    ("test_mermaid", False, []),
    ("test_mic_spend", True, []),
    ("test_mobile_layout", True, []),
    ("test_responsive", False, []),
    ("test_typed_turn", True, []),
]

# ---------------------------------------------------------------------------
# The breakages: (id, kind, target, tier_a, why, claims).
#
#   kind    "delete"  the file is gone; every request for it 404s.
#           "gut"     a .js file served 200 OK and INERT — the tag resolves and the
#                     network log is clean; only a check on what the script DID can tell.
#           "hollow"  an .html page keeps its <head> and its body becomes the same
#                     "Loading…" placeholder that once hid a toothless check.
#           "stall"   ONE resource arrives long after every wait: the file is padded to
#                     ~24 MB of valid filler and the browser throttled to 1 MB/s by the
#                     ledger hook. No request interception (most suites intercept
#                     themselves), a normal 200 — a suite can only notice by WAITING.
#   claims  regex over a check's own message; a green match is a TIER B finding.
#   tier_a  whether a whole suite exiting 0 is itself a finding. False for `stall`: one
#           late asset leaves most of a suite legitimately green, so stall rows are read
#           one named check at a time.
# ---------------------------------------------------------------------------
BREAKAGES = [
    ("qr-gone", "delete", "sim/web/qr.js", True,
     "the QR renderer never loads (the mutation that caught check_deployed)",
     r"\bQR\b|qr\.js|pairing|encode"),
    ("qr-inert", "gut", "sim/web/qr.js", True,
     "qr.js is served 200 OK and does nothing — no 404, no network error, no code",
     r"\bQR\b|qr\.js|pairing|encode"),
    ("docsjs-inert", "gut", "sim/web/docs.js", True,
     "docs.js is served 200 OK and does nothing — the explorer never populates",
     # not bare `docs`: every test_csp check on the page is prefixed "docs.html:"
     r"tree|markdown|search|highlight|renders?\b|populate|explorer|diagram"),
    ("readme-404", "delete", "sim/web/docs-bundle/_root/README.md", True,
     "the docs explorer's home document 404s (defect 4's breakage)",
     r"markdown|README|renders?\b|prose|home document|Loading|hero"),
    ("docs-index-404", "delete", "sim/web/docs-index.json", True,
     "the docs explorer's index 404s — there is no tree to build",
     r"tree|search|index|explorer|list"),
    ("hero-404", "delete", "sim/web/img/sim-hero.png", True,
     "the README hero image 404s (defect 2's subject)",
     r"hero|image|img|decode"),
    ("ambient-404", "delete", "sim/web/ambient.json", True,
     "the ambient self-talk corpus 404s — she has nothing to mutter",
     r"ambient|self-talk|quip|mutter|idle"),
    ("cloud-fixture-404", "delete", "sim/web/fixtures/cloud.json", True,
     "cloud.html's fixture 404s — the panel has nothing to draw",
     r"cloud|panel|fixture"),
    ("homejs-inert", "gut", "sim/web/home.js", True,
     "home.js is served 200 OK and does nothing — the landing page never animates",
     r"home\.js|sparkle|index\.html"),
    ("hudjs-inert", "gut", "sim/web/hud.js", True,
     "hud.js is served 200 OK and does nothing — no simulator control ever wires up",
     r"control|button|toggle|slider|rail|drawer|click|tap"),
    ("moxiejs-inert", "gut", "sim/web/moxie.js", True,
     "moxie.js is served 200 OK and does nothing — the WebGL Moxie never boots",
     r"canvas|webgl|moxie|stage|render|head|camera|bubble"),
    ("modejs-inert", "gut", "sim/web/mode.js", True,
     "mode.js is served 200 OK and does nothing — hosted/offline mode is never decided",
     r"mode|hosted|banner|offline|demo|capabilit"),
    # env.js paints every mark the mode rows are read through; this row keeps them honest.
    ("envjs-inert", "gut", "sim/web/env.js", True,
     "env.js is served 200 OK and does nothing — no badge, no banner, no needs-backend marks",
     r"env\.js|badge|banner|needs-backend|hosted|local\b"),
    # turnstile.js alone builds `#turnstile-holder` and places the challenge; a layout
    # check cannot tell it is gutted, one that asks "where is the challenge" must.
    ("turnstilejs-inert", "gut", "sim/web/turnstile.js", True,
     "turnstile.js is served 200 OK and does nothing — no holder, no widget, no token",
     r"turnstile|challenge|holder|sitekey|widget|bot control"),
    ("docs-hollow", "hollow", "sim/web/docs.html", True,
     "docs.html ships an empty body behind the same 'Loading…' placeholder",
     r"tree|markdown|search|article|explorer|renders?\b"),
    # ---- the "not loaded YET" family: a real 200 that arrives ~24 s late ----------
    ("readme-stalled", "stall", "sim/web/docs-bundle/_root/README.md", False,
     "the docs home document is still on the wire when the suite looks at the article",
     r"markdown|renders?\b|prose|home document|Loading|article|hero"),
    ("hero-stalled", "stall", "sim/web/img/sim-hero.png", False,
     "the README hero is still on the wire when the suite reads naturalWidth (defect 2)",
     r"hero|decode|image|img"),
    ("docsindex-stalled", "stall", "sim/web/docs-index.json", False,
     "the docs index is still on the wire when the suite counts the tree",
     r"tree|index|list|search|explorer"),
]


# ---------------------------------------------------------------------------
# mutation mechanics
# ---------------------------------------------------------------------------
GUT = "/* page_teeth_check: gutted — this file was served 200 OK and did nothing. */\n"
HOLLOW_BODY = (
    "<body>\n"
    "  <div id=\"content\"><article><p class=\"muted\">Loading…</p></article></div>\n"
    "</body>"
)
# ~24 s at the 1 MB/s the browser is throttled to. Everything else on the page is a few
# hundred KB at most and stays effectively instant, so the stall is aimed, not ambient.
STALL_BYTES = 24 * 1024 * 1024
STALL_THROUGHPUT = 1024 * 1024


def _pad(path: pathlib.Path, raw: bytes) -> bytes:
    """`raw`, grown to STALL_BYTES and still valid for its type (comment, JSON field, or
    trailing bytes a decoder ignores) — a stall must look like a slow network, not a
    corrupt payload that error handling would catch."""
    need = max(0, STALL_BYTES - len(raw))
    ext = path.suffix.lower()
    if ext in (".md", ".markdown"):
        return raw + b"\n<!-- " + b"." * need + b" -->\n"
    if ext in (".js", ".mjs", ".css"):
        return raw + b"\n/* " + b"." * need + b" */\n"
    if ext == ".json":
        obj = json.loads(raw)
        if isinstance(obj, dict):
            obj["_page_teeth_padding"] = "." * need
            return json.dumps(obj).encode()
    return raw + b"\0" * need


class Mutation:
    """Apply one breakage to the worktree; restore it whatever happens."""

    def __init__(self, kind: str, target: str):
        self.kind, self.target = kind, target
        self.path = WT / target
        self._backup = None

    @property
    def env(self) -> dict:
        return {"MOXIE_TEETH_THROUGHPUT": str(STALL_THROUGHPUT)} if self.kind == "stall" else {}

    def __enter__(self):
        if self.target in RESERVED:
            raise SystemExit(f"page_teeth_check: refusing to mutate RESERVED {self.target}")
        if not self.path.exists():
            raise SystemExit(f"page_teeth_check: anchor missing — {self.target}")
        self._backup = self.path.read_bytes()
        # NOT in a try/except: no journal, no mutation.
        JOURNAL.write_text(f"{self.kind} {self.target}\n")
        if self.kind == "delete":
            self.path.unlink()
        elif self.kind == "gut":
            self.path.write_text(GUT)
        elif self.kind == "stall":
            self.path.write_bytes(_pad(self.path, self._backup))
        elif self.kind == "hollow":
            src = self._backup.decode()
            out, n = re.subn(r"<body[^>]*>.*</body>", HOLLOW_BODY, src, flags=re.S)
            if n != 1:
                raise SystemExit(f"page_teeth_check: no unique <body> in {self.target}")
            self.path.write_text(out)
        else:
            raise SystemExit(f"page_teeth_check: unknown kind {self.kind}")
        return self

    def __exit__(self, *exc):
        if self._backup is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_bytes(self._backup)
        JOURNAL.unlink(missing_ok=True)
        return False

    def paths_hit(self) -> list[str]:
        """URL fragments whose presence in a healthy run's request log means EXPOSURE."""
        return ["/" + self.target.split("sim/web/", 1)[1]]


# ---------------------------------------------------------------------------
# running one suite
# ---------------------------------------------------------------------------
def run_suite(suite: str, extra_env: dict, argv=(), timeout: int = 900) -> dict:
    """Run `sim/<suite>.mjs` under the ledger hook. Returns the ledger + exit code."""
    fd, tmp = tempfile.mkstemp(suffix=".json", prefix="teeth-")
    os.close(fd)
    env = dict(os.environ)
    env.update({
        "MOXIE_TEETH_LEDGER": tmp,
        "MOXIE_LLM_API_KEY": "",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    env.pop("CI", None)          # a missing browser must SKIP here, not fail the audit
    env.update(extra_env)
    t0 = time.time()
    # SIGTERM before SIGKILL: the ledger flushes on SIGTERM, so a timed-out suite still
    # reports the checks it reached (a SIGKILL would read as "nothing stayed green").
    p = subprocess.Popen(
        ["node", "--import", str(LEDGER_HOOK), f"sim/{suite}.mjs", *argv],
        cwd=WT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    try:
        tail = p.communicate(timeout=timeout)[0]
        rc = p.returncode
    except subprocess.TimeoutExpired:
        p.terminate()
        try:
            tail = p.communicate(timeout=30)[0]
        except subprocess.TimeoutExpired:
            p.kill()
            tail = p.communicate()[0]
        rc, tail = 124, "TIMEOUT\n" + (tail or "")
    led = {"checks": [], "requests": [], "failed": []}
    try:
        led = json.loads(pathlib.Path(tmp).read_text())
    except Exception:
        pass
    finally:
        pathlib.Path(tmp).unlink(missing_ok=True)
    led["rc"] = rc
    led["secs"] = round(time.time() - t0, 1)
    led["tail"] = "\n".join(tail.strip().splitlines()[-14:])
    return led


def by_key(led: dict) -> dict:
    return {c["key"]: c for c in led.get("checks", [])}


def exposed(base: dict, frags: list[str]) -> bool:
    """Did this suite's HEALTHY run actually fetch the file the breakage attacks?
    Measured from the ledger's request log, never inferred from file names."""
    blob = "\n".join(base.get("requests", []))
    return any(f in blob for f in frags)


def _baseline(suites, baseline_dir, header: str, show_tail: bool):
    """Run (or load from `baseline_dir`) every suite on the healthy page. Returns
    `(ledgers, void)`, `void` being the suites already red. The cache is opt-in: a
    baseline from a different tree compares a suite to a page it never saw."""
    print("=" * 78)
    print(header)
    print("=" * 78)
    cache = pathlib.Path(baseline_dir) if baseline_dir else None
    if cache:
        cache.mkdir(parents=True, exist_ok=True)
    base, void = {}, []
    for suite, granular, argv in suites:
        cached = cache / f"{suite}.json" if cache else None
        was_cached = bool(cached and cached.exists())
        if was_cached:
            led = json.loads(cached.read_text())
        else:
            led = run_suite(suite, {}, argv)
            if cached:
                cached.write_text(json.dumps(led))
        base[suite] = led
        flag = "ok " if led["rc"] == 0 else "RED"
        note = " (cached)" if was_cached else ""
        print(f"  {flag} {suite:<22} rc={led['rc']}  {len(led['checks']):>3} checks  "
              f"{led['secs']:>6}s{note}")
        if led["rc"] != 0:
            void.append(suite)
            if show_tail:
                print("      " + led["tail"].replace("\n", "\n      "))
    return base, void


def _section(title: str, rows, fmt) -> None:
    if title:
        print(title)
    for row in rows:
        print(fmt(*row))
    if not rows:
        print("  (none)")


# ---------------------------------------------------------------------------
# the sweep
# ---------------------------------------------------------------------------
def sweep(only_suite=None, only_breakage=None, baseline_only=False,
          baseline_dir=None) -> int:
    suites = [s for s in SUITES if not only_suite or s[0] == only_suite]
    breaks = [b for b in BREAKAGES if not only_breakage or b[0] == only_breakage]

    base, void = _baseline(suites, baseline_dir, "BASELINE — the healthy page. Every "
                           "suite must be green here or the audit is void.", True)
    if void:
        print(f"\n!! {len(void)} suite(s) already red on the healthy tree: {', '.join(void)}")
        print("   Findings against those are meaningless. Fix or exclude them first.")
    if baseline_only:
        return 1 if void else 0

    tier_a, tier_b, vanished, skipped = [], [], [], []
    for bid, kind, target, tier_a_counts, why, claims in breaks:
        m = Mutation(kind, target)
        frags = m.paths_hit()
        todo = [(s, g, av) for s, g, av in suites
                if s not in void and exposed(base[s], frags)]
        print()
        print("=" * 78)
        print(f"BREAKAGE {bid} — {why}")
        print(f"  ({kind} {target})   exposed suites: "
              f"{', '.join(s for s, _, _ in todo) or 'NONE'}")
        print("=" * 78)
        done = {s for s, _, _ in todo}
        for s, _, _ in suites:
            if s not in void and s not in done:
                skipped.append((bid, s))
        if not todo:
            continue
        caught, missed = [], []
        with m:
            for suite, granular, argv in todo:
                led = run_suite(suite, m.env, argv)
                # An instrument that failed is not evidence: an unthrottled `stall` would
                # manufacture a whole family of false "stayed green" findings.
                if m.kind == "stall" and not led.get("throttled"):
                    print(f"  SKIPPED      {suite:<22} the throttle never applied — "
                          f"{'; '.join(led.get('notes') or ['no pages were instrumented'])}")
                    continue
                b, a = by_key(base[suite]), by_key(led)
                green = [k for k, c in a.items() if c["pass"] and b.get(k, {}).get("pass")]
                red = [k for k, c in a.items() if not c["pass"]]
                gone = [k for k in b if k not in a]
                if led["rc"] == 0:
                    verdict = "NO TEETH" if tier_a_counts else "green (ok)"
                else:
                    verdict = f"red ({len(red)})"
                print(f"  {verdict:<12} {suite:<22} rc={led['rc']}  "
                      f"{len(a):>3} checks  {len(green):>3} still green  "
                      f"{len(gone):>3} vanished  {led['secs']:>6}s")
                if led["rc"] == 0 and tier_a_counts:
                    tier_a.append((bid, suite, why, len(green)))
                    if not granular:
                        print("      (no per-check ledger — this suite does not use makeChecks)")
                # TIER B is read on EVERY run: on a red suite it is the detail view, on a
                # green one (always, for `stall`) it is the whole readout.
                for k in green:
                    if re.search(claims, a[k]["msg"], re.I):
                        tier_b.append((bid, suite, a[k]["msg"], why))
                        print(f"      TIER B green: {a[k]['msg'][:96]}")
                if gone:
                    vanished.append((bid, suite, len(gone)))
                    print(f"      vanished (did not run at all): {', '.join(gone[:4])}")
                (caught if led["rc"] != 0 else missed).append(suite)
        # a row NO exposed suite reddens means nothing in the repo would notice it shipping
        print(f"  --> caught by {len(caught)}/{len(todo)} exposed suites"
              + (f"; MISSED BY: {', '.join(missed)}" if missed else ""))
        if not caught:
            print("  !! NOTHING IN THE REPO NOTICES THIS BREAKAGE")

    # ---- report ----------------------------------------------------------
    print()
    print("=" * 78)
    print("FINDINGS")
    print("=" * 78)
    _section(f"\nTIER A — suite was EXPOSED to the breakage and still exited 0 ({len(tier_a)}):",
             tier_a, lambda bid, suite, why, n: f"  · {suite:<22} survived [{bid}] {why}")
    _section(f"\nTIER B — a check that NAMES the broken thing stayed green ({len(tier_b)}):",
             tier_b, lambda bid, suite, msg, why: f"  · {suite:<22} [{bid}] {msg[:88]}")
    _section(f"\nCOVERAGE DROPS — checks that stopped running under a breakage ({len(vanished)}):",
             vanished, lambda bid, suite, n: f"  · {suite:<22} [{bid}] {n} check(s) never ran")
    print(f"\nNOT EXPOSED (correctly skipped, not findings): {len(skipped)} suite/breakage pairs")
    return 1 if (tier_a or tier_b) else 0


# ---------------------------------------------------------------------------
# the slow sweep — the loaded runner, on this box, on purpose
# ---------------------------------------------------------------------------
#
# A check whose OWN MESSAGE is about time (frame budgets, motion) is SUPPOSED to move on
# a throttled renderer; those are set aside and counted, never dropped.
TIME_CLAIMS = re.compile(
    r"\bbudget|\bfps\b|frame time|\bms\b|milliseconds|\bslow(er|ly)?\b|"
    r"within \d|under \d|faster|latenc|throughput|elapsed|duration", re.I)


def slow_sweep(rate: int, only_suite=None, baseline_dir=None, timeout: int = 1800) -> int:
    """Run every suite against an UNTOUCHED site on a renderer throttled `rate`x; the
    tree stays byte-clean, so anything that reddens reddened because of the CLOCK."""
    suites = [s for s in SUITES if not only_suite or s[0] == only_suite]

    base, void = _baseline(suites, baseline_dir,
                           "BASELINE — the healthy page at full speed.", False)
    if void:
        print(f"\n!! already red on the healthy tree: {', '.join(void)} — findings there are void.")

    print()
    print("=" * 78)
    print(f"SLOW — the SAME site, renderer throttled {rate}x (nothing on disk is touched)")
    print("=" * 78)
    findings, timed, gone_all, notes_all = [], [], [], []
    for suite, granular, argv in suites:
        if suite in void:
            continue
        led = run_suite(suite, {"MOXIE_TEETH_CPU": str(rate)}, argv, timeout=timeout)
        # An instrument that did not apply is not evidence. Same contract as `stall`.
        if not led.get("cpuThrottled"):
            notes_all.append(suite)
            print(f"  SKIPPED      {suite:<22} the CPU throttle never applied — "
                  f"{'; '.join(led.get('notes') or ['no pages were instrumented'])}")
            continue
        b, a = by_key(base[suite]), by_key(led)
        flipped = [k for k, c in a.items() if not c["pass"] and b.get(k, {}).get("pass")]
        gone = [k for k in b if k not in a]
        print(f"  {'ok ' if led['rc'] == 0 else 'RED'} {suite:<22} rc={led['rc']}  "
              f"{len(a):>3} checks  {len(flipped):>3} flipped green->red  "
              f"{len(gone):>3} vanished  {led['secs']:>6}s")
        for k in flipped:
            row = (suite, k, a[k]["msg"])
            if TIME_CLAIMS.search(a[k]["msg"]):
                timed.append(row)
                print(f"      (time-claiming, set aside) {k}  {a[k]['msg'][:80]}")
            else:
                findings.append(row)
                print(f"      FLIPPED {k}  {a[k]['msg'][:88]}")
        if gone:
            gone_all.append((suite, len(gone), gone[:4]))
            print(f"      vanished (never ran): {', '.join(gone[:4])}")
        if led["rc"] != 0 and not flipped and not gone:
            print("      red with NO flipped check — the suite threw before it asserted:")
            print("      " + led["tail"].replace("\n", "\n      "))

    print()
    print("=" * 78)
    print("FINDINGS — green at full speed, RED on a slow renderer, same bytes on disk")
    print("=" * 78)
    _section("", findings, lambda suite, k, msg: f"  · {suite:<22} {k}\n      {msg[:110]}")
    _section(f"\nSET ASIDE — checks whose own message is about time ({len(timed)}):",
             timed, lambda suite, k, msg: f"  · {suite:<22} {k}  {msg[:80]}")
    _section(f"\nCOVERAGE DROPS — checks that stopped running when the page got slow "
             f"({len(gone_all)} suite(s)):", gone_all,
             lambda suite, n, sample: f"  · {suite:<22} {n} check(s) never ran: "
                                      f"{', '.join(sample)}")
    if notes_all:
        print(f"\n!! the throttle did not apply in: {', '.join(notes_all)} — those rows prove nothing.")
    return 1 if (findings or gone_all) else 0


# ---------------------------------------------------------------------------
# selftest — the tool must find a check that is KNOWN to have no teeth
# ---------------------------------------------------------------------------
#
# The fixture is a real, since-fixed toothless check: `test_docs_explorer.mjs` asserted
# `article h1, article h2, article p` with no wait, matching docs.html's static
# "Loading docs…" <p>. The selftest restores it, runs the `readme-404` row, and requires:
#   KNOWN POSITIVE — the restored check stays GREEN while the home document 404s;
#   KNOWN NEGATIVE — the shipped check goes RED (else the tool could "detect" by calling
#                    everything toothless, the worse error).
TOOTHLESS_ORIGINAL = '''  await page.waitForSelector("article h1, article h2", { timeout: 8000 }).catch(() => {});
  ok(await page.evaluate(() => {
    const a = document.querySelector("article");
    return !!a && !!a.querySelector("h1, h2") && !/^\\s*Loading/.test(a.textContent);'''
TOOTHLESS_PATCH = '''  ok(await page.evaluate(() => {
    const a = document.querySelector("article");
    return !!a && !!a.querySelector("h1, h2, p");'''

SELFTEST_SUITE = "test_docs_explorer"
SELFTEST_BREAK = ("readme-404", "delete", "sim/web/docs-bundle/_root/README.md")
SELFTEST_CHECK = "home document markdown should render"


def _find(led, needle):
    for c in led.get("checks", []):
        if needle in c["msg"]:
            return c
    return None


def selftest() -> int:
    src_path = WT / "sim" / f"{SELFTEST_SUITE}.mjs"
    original = src_path.read_text()
    if original.count(TOOTHLESS_ORIGINAL) != 1:
        print("SELFTEST CANNOT RUN — the anchor in test_docs_explorer.mjs moved.")
        print("  Re-anchor it here rather than deleting the selftest: a tool with no")
        print("  selftest reports 'no findings' for free.")
        return 1

    _, kind, target = SELFTEST_BREAK
    results = {}
    try:
        for arm, text in (("known-negative (shipped, fixed)", original),
                          ("known-positive (defect 4 restored)",
                           original.replace(TOOTHLESS_ORIGINAL, TOOTHLESS_PATCH, 1))):
            src_path.write_text(text)
            with Mutation(kind, target) as m:
                led = run_suite(SELFTEST_SUITE, m.env)
            c = _find(led, SELFTEST_CHECK)
            results[arm] = (led, c)
            state = "MISSING" if c is None else ("GREEN" if c["pass"] else "red")
            print(f"  {arm:<36} suite rc={led['rc']}  the check: {state}")
    finally:
        src_path.write_text(original)

    neg = results["known-negative (shipped, fixed)"][1]
    pos = results["known-positive (defect 4 restored)"][1]
    bad = []
    if pos is None or not pos["pass"]:
        bad.append("the KNOWN-TOOTHLESS check did not stay green — the tool cannot see "
                   "the defect family it was built for")
    if neg is None or neg["pass"]:
        bad.append("the FIXED check stayed green too — the tool would call a working "
                   "check toothless, which is the worse error")
    print()
    if bad:
        for b in bad:
            print(f"❌ SELFTEST FAILED — {b}")
        return 1
    print("✅ SELFTEST — the tool separates a toothless check from a fixed one:")
    print("   defect 4 restored + home document 404 -> the check stays GREEN (detected)")
    print("   defect 4 fixed    + home document 404 -> the check goes RED   (not reported)")

    # The --slow instrument: a throttle that silently did not apply reports "no findings",
    # and nothing on disk shows it, so prove the count on the cheapest real suite.
    print()
    print("── the --slow instrument ──")
    led = run_suite("test_api_headers", {"MOXIE_TEETH_CPU": "6"}, [])
    if not led.get("cpuThrottled"):
        print("❌ SELFTEST FAILED — the CPU throttle never applied: "
              f"{'; '.join(led.get('notes') or ['no pages were instrumented'])}")
        print("   Every `--slow` row would read 'no findings' about a browser that was "
              "never slowed down.")
        return 1
    print(f"✅ the CPU throttle applied to {led['cpuThrottled']} page(s) with no notes — "
          "a `--slow` row that reports nothing is reporting about a throttled browser")
    return 0


def check_tree(restore: bool = False) -> int:
    if JOURNAL.exists():
        line = JOURNAL.read_text().strip()
        print(f"!! a run was INTERRUPTED while a breakage was applied: {line}")
        if restore:
            target = line.split(None, 1)[-1]
            subprocess.run(["git", "checkout", "--", target], cwd=WT, check=False)
            JOURNAL.unlink(missing_ok=True)
            print(f"   restored {target} from the index")
        else:
            print("   re-run with --restore, or `git checkout -- <path>` by hand")
    r = subprocess.run(["git", "status", "--porcelain"], cwd=WT,
                       capture_output=True, text=True)
    # Only TRACKED files can be left behind by a breakage; untracked ones are listed only.
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    tracked = [l for l in lines if not l.startswith("??")]
    untracked = [l for l in lines if l.startswith("??")]
    for u in untracked:
        print("  (untracked, ignored) " + u[3:])
    if tracked:
        print("worktree is NOT clean — a breakage may not have been reverted:")
        for d in tracked:
            print("  " + d)
        return 1
    print("worktree clean (no tracked file modified)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--baseline-only", action="store_true")
    ap.add_argument("--check-tree", action="store_true")
    ap.add_argument("--restore", action="store_true",
                    help="with --check-tree: undo a breakage an interrupted run left behind")
    ap.add_argument("--suite")
    ap.add_argument("--breakage")
    ap.add_argument("--slow", type=int, metavar="RATE",
                    help="do not break the site — throttle the RENDERER RATEx and report "
                         "every check that was green at full speed and is red now")
    ap.add_argument("--baseline-dir",
                    help="cache the healthy run here and reuse it on the next call")
    a = ap.parse_args()
    if not shutil.which("node"):
        print("node not found — nothing to audit"); return 0
    if a.check_tree:
        return check_tree(a.restore)
    if a.selftest:
        return selftest()
    if a.slow:
        return slow_sweep(a.slow, a.suite, a.baseline_dir)
    return sweep(a.suite, a.breakage, a.baseline_only, a.baseline_dir)


if __name__ == "__main__":
    raise SystemExit(main())
