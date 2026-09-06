"""Serve every browser suite a DELIBERATELY BROKEN site, and report which of its
checks stay green.

The mutation checkers next to this file (`ext_mutation_check.py`,
`brain_mutation_check.py`, …) delete a guard from the PRODUCT and require its test to
redden. This is the same proof turned on the other half of the browser suites' world:
it breaks the **page** — deletes a script, serves one 200 OK and inert, 404s a fetch,
empties a document, stalls one resource past every wait — and requires the suites that
load that page to notice.

WHY IT EXISTS. On 2026-09-06 five checks in this repo were found to pass against a
system that was actually broken, and **every one of them was found by luck** — a red on
an unrelated diff, or someone noticing while measuring something else:

  1. `test_bg_perf.mjs` sampled its "before" count from Node, charging a visible page's
     work to a hidden window (rule 30).
  2. `test_docs_explorer.mjs` read `img.naturalWidth` with no wait for decode.
  3. `test_csp.mjs` had the same unwaited sample behind a fixed sleep.
  4. `test_docs_explorer.mjs` asserted `article h1, article h2, article p` — and
     `sim/web/docs.html:211` ships a static `<p class="muted">Loading docs…</p>`, so
     `article p` matched the SPINNER. Abort the README fetch and the check stayed green.
  5. `check_deployed.mjs` collected `failed` and `consoleErrs`, PRINTED them, and
     asserted neither; deleting `qr.js` produced `fired: NOTHING` and exit 0.

Four of the five are the same shape — *the check samples a page that has not finished
being a page yet, or matches markup that is present whether or not the page worked.*
Nobody had ever swept for them. This is that sweep.

    python3 sim/tools/page_teeth_check.py --baseline-dir /tmp/teeth   # the full sweep
    python3 sim/tools/page_teeth_check.py --selftest      # prove the tool works, ~1 min
    python3 sim/tools/page_teeth_check.py --suite test_csp --breakage qr-inert
    python3 sim/tools/page_teeth_check.py --check-tree    # nothing was left mutated

The full sweep takes a couple of hours: it runs every exposed suite once per breakage,
and a suite whose waits all expire runs far longer broken than healthy (`test_mermaid`
went 37 s -> 448 s with `docs.js` inert). `--baseline-dir` caches the healthy run so a
single row can be re-read in a minute.

HOW A FINDING IS DECIDED, and why it is not just "the suite passed".

  · **Exposure is measured, not asserted.** `teeth_ledger.mjs` records every URL each
    suite's browser requested during its HEALTHY run. A suite is in scope for "delete
    `qr.js`" only if that suite actually fetched `qr.js`. A green under a breakage the
    suite never touched is noise, and noise here is worse than a miss: it sends someone
    to "fix" a working test.
  · **Two tiers.**
      TIER A — the suite was exposed to the breakage and still exited **0**. That is
               defect 5's exact shape and needs no judgement call.
      TIER B — the suite reddened overall, but a named check that CLAIMS to cover the
               broken thing (`claims` regex on the check's own message) stayed green.
               Curated, and every row must be hand-verified before it is believed.
  · **Vanished ≠ green.** A check that stopped running under the breakage is reported
    separately. It is not toothless; it is *absent*, which is its own smaller problem
    (`finish()` prints a count, so a silent drop in coverage is at least visible).

WHAT IT DOES NOT TOUCH. `sim/web/sim.html` and `sim/web/ambient.js` are RESERVED by
other live sessions and are never mutated, even transiently — where a breakage needs the
simulator's self-talk gone it deletes `ambient.json` (the data) instead, which kills it
just as dead. Every mutation is reverted in a `finally`, and `--check-tree` verifies the
worktree is byte-clean before and after.
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

# WHERE A MUTATION IN FLIGHT IS RECORDED, and why this file exists at all.
#
# Every breakage is reverted in a `finally` — which a SIGKILL does not run. On 2026-09-06
# the first full sweep was killed by its supervisor part-way through `hudjs-inert` and left
# `sim/web/hud.js` gutted in the worktree; `--check-tree` caught it, but only because
# somebody thought to ask. That is exactly the shape playbook rule 22 warns about: cleanup
# chained behind an action that can be interrupted.
#
# So the target is written here BEFORE it is touched and removed after it is restored, and
# `--check-tree` reads it. Recovery is total and needs no saved bytes: every target is a
# TRACKED file, so `git checkout -- <path>` is the whole repair, and `--restore` runs it.
# It lives inside the real git directory deliberately — a journal in the worktree would
# itself be an untracked file the audit then has to explain away. That path has to be
# ASKED FOR, not composed: in a linked worktree (which is how every agent here works)
# `WT/.git` is a FILE holding `gitdir: …`, so `WT/".git"/"page-teeth-active"` is a write
# into a path under a regular file. The first draft did exactly that inside a bare
# `except: pass`, so the journal silently never existed — the same swallowed-exception
# defect this tool exists to hunt, twice in one afternoon.
def _git_dir() -> pathlib.Path:
    r = subprocess.run(["git", "rev-parse", "--absolute-git-dir"], cwd=WT,
                       capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return pathlib.Path(r.stdout.strip())
    return WT                                   # not a checkout: keep it beside the tree


JOURNAL = _git_dir() / "page-teeth-active"

# Reserved by other sessions (see the module docstring). Refused as mutation targets at
# table-validation time rather than by convention, so a future row cannot quietly add one.
RESERVED = {
    "sim/web/sim.html",
    "sim/web/ambient.js",
    "sim/web/style.css",
    "sim/check_deployed.mjs",
}

# ---------------------------------------------------------------------------
# The suites. Every `sim/test_*.mjs` that launches a browser.
#
# `granular` records whether the suite routes its assertions through
# `browser_harness.makeChecks` — the three that do not (`test_mermaid`,
# `test_responsive`, `test_env_hosted`) roll their own counters, so the ledger sees no
# individual checks for them and the audit can only speak about their EXIT CODE. That is
# a real limitation of this tool and is printed in the report rather than hidden.
# ---------------------------------------------------------------------------
SUITES = [
    # (module stem, does it use makeChecks, argv)
    #
    # `check_deployed --selftest` is here even though it is not a `test_*.mjs` and is a
    # RESERVED file this pass may not edit. Auditing is not editing, and it is the most
    # on-point target in the repo: defect 5 — the one that started this — was ITS printed
    # `failed requests: 0  console errors: 0` with nothing asserting either. Its
    # `--selftest` is hermetic (four loopback servers under the real `_headers`, no
    # internet), so it can be swept like any other suite. If a fix belongs in it, this
    # tool's job is to say so and stop.
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
# The breakages.
#
#   kind    "delete"  the file is gone; every request for it 404s.
#           "gut"     a .js file is served 200 OK and is INERT. Strictly subtler than
#                     `delete`: the tag resolves, the network log is clean, and only a
#                     check that looks at what the script DID can tell.
#           "hollow"  an .html page keeps its <head> and loses its entire <body>,
#                     replaced by the same "Loading…" placeholder that made defect 4
#                     invisible. This is the "serve a placeholder document" breakage.
#           "stall"   ONE resource arrives long after every wait in every suite, while
#                     the rest of the page stays fast. This is the instrument for defects
#                     2, 3 and 4 — a check that samples the DOM before the thing it names
#                     has arrived passes on a fast loopback and can never fail there.
#
#                     It is done WITHOUT request interception, which matters: eleven of
#                     these suites intercept requests themselves and a second interceptor
#                     would change what they are testing. Instead the target file is
#                     padded to ~24 MB of comment and the browser is throttled to 1 MB/s
#                     (`emulateNetworkConditions`, from the ledger hook), so that one
#                     resource takes ~24 s and everything else on the page stays quick.
#                     The bytes are real and the response is a normal 200 — a suite
#                     cannot notice this by watching for failures, only by WAITING.
#
#   claims  a regex over a CHECK'S OWN MESSAGE. A green check whose message matches is a
#           TIER B finding — the check names the thing that is now broken. A green check
#           that does not match is collateral and is never reported as a finding.
#
#   tier_a  whether a WHOLE SUITE exiting 0 under this breakage is a finding by itself.
#           True where the breakage leaves the page objectively broken for everyone who
#           loads it. FALSE for `stall`, and that distinction is the difference between
#           an audit and a noise generator: one late asset leaves most of a suite
#           legitimately passing, so "the suite was green" is not evidence of anything.
#           Stall rows are read at TIER B instead, one named check at a time.
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
     # Deliberately NOT `docs` on its own: every `test_csp.mjs` check about the docs PAGE
     # is prefixed "docs.html:", including ones about HSTS, and matching those made the
     # first sweep's report 80 % page-name collisions. A claims regex has to match a claim.
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
    # Added 2026-09-06 with the clause that closes it. env.js paints EVERY mark the two
    # rows above are read through (`body[data-mode]`, the badge, the needs-backend marks),
    # so a row for it is what keeps those two honest: without it, a clause that reads a
    # mark env.js writes could be satisfied by env.js alone and nobody would have measured
    # the difference.
    ("envjs-inert", "gut", "sim/web/env.js", True,
     "env.js is served 200 OK and does nothing — no badge, no banner, no needs-backend marks",
     r"env\.js|badge|banner|needs-backend|hosted|local\b"),
    # Added 2026-09-06 with the fix for docs/architecture/backlog/turnstile-layout-collision.md.
    # `turnstile.js` is the ONLY file that creates `#turnstile-holder`, decides where the
    # challenge lands and re-enables pointer events on it, and it is now the file two blocks
    # of `test_mobile_layout.mjs` are aimed at — so it needs a row of its own. Gutted, the
    # sitekey is still published, `/api.js` is still requested by nobody, and no holder is
    # ever built: a check that only asks "is the page laid out" cannot tell, and one that
    # asks "where is the challenge" must.
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
    """`raw`, grown to STALL_BYTES, still valid for its own type.

    Markdown gets an HTML comment (marked renders nothing for it), JS/CSS a block
    comment, JSON a long string field, and anything else — PNG included — trailing bytes
    after its terminator, which every decoder in a browser ignores. The file has to stay
    USABLE: a stall must be indistinguishable from a slow network, and a corrupt payload
    would be caught by error handling that a late one is not.
    """
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
        # NOT in a try/except. If the journal cannot be written, an interrupted run leaves
        # a mutated tree with nothing recording it, and the whole point of the file is
        # gone. Refuse to mutate instead.
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
    # SIGTERM before SIGKILL, and the difference matters: `teeth_ledger.mjs` flushes the
    # ledger from a SIGTERM handler, so a suite that runs long under a breakage still
    # reports the checks it HAD reached. `subprocess.run(timeout=)` sends SIGKILL, which
    # would hand back an empty ledger — indistinguishable from "nothing stayed green".
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

    The audit's whole defence against noise. A suite that never loads `qr.js` is not
    "tolerating a broken QR renderer" when it passes with `qr.js` deleted — it simply has
    nothing to do with it, and reporting that would send someone to fix a test that works.
    Measured from the request log the ledger recorded, never inferred from file names.
    """
    blob = "\n".join(base.get("requests", []))
    return any(f in blob for f in frags)


# ---------------------------------------------------------------------------
# the sweep
# ---------------------------------------------------------------------------
def sweep(only_suite=None, only_breakage=None, baseline_only=False,
          baseline_dir=None) -> int:
    suites = [s for s in SUITES if not only_suite or s[0] == only_suite]
    breaks = [b for b in BREAKAGES if not only_breakage or b[0] == only_breakage]

    print("=" * 78)
    print("BASELINE — the healthy page. Every suite must be green here or the audit is void.")
    print("=" * 78)
    # `--baseline-dir` caches the healthy run. It is a convenience for re-reading ONE
    # breakage without paying twelve minutes again, and it is deliberately opt-in: a
    # cached baseline taken against a different tree would compare a suite to a page it
    # never saw, which is the exact class of error this tool exists to find.
    cache = pathlib.Path(baseline_dir) if baseline_dir else None
    if cache:
        cache.mkdir(parents=True, exist_ok=True)
    base = {}
    void = []
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
        n = len(led["checks"])
        flag = "ok " if led["rc"] == 0 else "RED"
        note = " (cached)" if was_cached else ""
        print(f"  {flag} {suite:<22} rc={led['rc']}  {n:>3} checks  "
              f"{led['secs']:>6}s{note}")
        if led["rc"] != 0:
            void.append(suite)
            print("      " + led["tail"].replace("\n", "\n      "))
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
                # An instrument that failed is not evidence of anything. `stall` depends on
                # the browser actually being throttled; if `emulateNetworkConditions` threw
                # (it did, silently, in this tool's first draft), every check would "stay
                # green" against a page that was never slow and the row would manufacture
                # a whole family of false findings.
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
                # TIER B is read on EVERY run, red or green. On a red suite it is the
                # detail view — which of the surviving checks name the broken thing. On a
                # green one it is the whole readout, and it is the only thing `stall` can
                # be read by: a stalled resource leaves most of a suite legitimately
                # passing, so "the suite was green" says nothing and "the check that
                # claims the hero DECODED was green while the hero was still on the wire"
                # says everything.
                for k in green:
                    if re.search(claims, a[k]["msg"], re.I):
                        tier_b.append((bid, suite, a[k]["msg"], why))
                        print(f"      TIER B green: {a[k]['msg'][:96]}")
                if gone:
                    vanished.append((bid, suite, len(gone)))
                    print(f"      vanished (did not run at all): {', '.join(gone[:4])}")
                (caught if led["rc"] != 0 else missed).append(suite)
        # WAS THE BREAKAGE DETECTABLE AT ALL? A row that NO exposed suite reddens is a
        # far stronger statement than one suite tolerating it: it says nothing in the
        # repository would notice this shipping. Printed per row rather than only in the
        # summary, because that is the number a reader of one row needs.
        print(f"  --> caught by {len(caught)}/{len(todo)} exposed suites"
              + (f"; MISSED BY: {', '.join(missed)}" if missed else ""))
        if not caught:
            print("  !! NOTHING IN THE REPO NOTICES THIS BREAKAGE")

    # ---- report ----------------------------------------------------------
    print()
    print("=" * 78)
    print("FINDINGS")
    print("=" * 78)
    print(f"\nTIER A — suite was EXPOSED to the breakage and still exited 0 ({len(tier_a)}):")
    for bid, suite, why, n in tier_a:
        print(f"  · {suite:<22} survived [{bid}] {why}")
    if not tier_a:
        print("  (none)")
    print(f"\nTIER B — a check that NAMES the broken thing stayed green ({len(tier_b)}):")
    for bid, suite, msg, why in tier_b:
        print(f"  · {suite:<22} [{bid}] {msg[:88]}")
    if not tier_b:
        print("  (none)")
    print(f"\nCOVERAGE DROPS — checks that stopped running under a breakage ({len(vanished)}):")
    for bid, suite, n in vanished:
        print(f"  · {suite:<22} [{bid}] {n} check(s) never ran")
    if not vanished:
        print("  (none)")
    print(f"\nNOT EXPOSED (correctly skipped, not findings): {len(skipped)} suite/breakage pairs")
    return 1 if (tier_a or tier_b) else 0


# ---------------------------------------------------------------------------
# selftest — the tool must find a check that is KNOWN to have no teeth
# ---------------------------------------------------------------------------
#
# Defect 4 is the fixture, because it is the one that was reconstructible: until
# 2026-09-06 `test_docs_explorer.mjs` asserted `article h1, article h2, article p` with no
# wait, and `sim/web/docs.html:211` ships `<p class="muted">Loading docs…</p>` as static
# markup — so the assertion matched the SPINNER and survived the home document never
# arriving at all. The fix (an `h1, h2`-only selector plus a `waitForSelector`) is in the
# tree; the selftest puts the defect back, runs the sweep's `readme-404` row against it,
# and requires BOTH directions:
#
#   KNOWN POSITIVE — with the old assertion restored, the check must stay GREEN while the
#                    home document 404s. If the tool cannot see that, it cannot see any of
#                    this family and its "no findings" would mean nothing.
#   KNOWN NEGATIVE — with the shipped assertion, the same check must go RED. Without this
#                    half the tool could "detect" the defect by calling everything
#                    toothless, which is the failure mode that matters most here: a false
#                    positive sends someone to fix a test that works.
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
        print("  The fixture is defect 4's fix; if the fix was rewritten, re-anchor it here")
        print("  rather than deleting the selftest. A tool with no selftest is a tool that")
        print("  reports 'no findings' for free.")
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
    # Only TRACKED files can be left behind by a breakage — every mutation edits a file
    # that is already in the index and restores it in a `finally`. Untracked files are
    # listed but are not a failure: a run leaves none, and refusing to run because someone
    # has a scratch file open would make the guard something people skip.
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
    ap.add_argument("--baseline-dir",
                    help="cache the healthy run here and reuse it on the next call")
    a = ap.parse_args()
    if not shutil.which("node"):
        print("node not found — nothing to audit"); return 0
    if a.check_tree:
        return check_tree(a.restore)
    if a.selftest:
        return selftest()
    return sweep(a.suite, a.breakage, a.baseline_only, a.baseline_dir)


if __name__ == "__main__":
    raise SystemExit(main())
