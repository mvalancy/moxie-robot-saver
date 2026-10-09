"""The hosted demo's docs say what its code does.

Measured 2026-10-08 (review lane l5, re-checked by W3-S14): the roadmap, also served to every
visitor by the docs explorer, promised "per-visitor and global rate limits" while
`functions/api/_lib/limits.js` says in capitals that its counters are not a global ceiling;
`.dev.vars.example` called `DEMO_ENABLED=0` "the fastest incident response there is", though
Pages applies a variable only to the NEXT deployment and never to an older deployment's own
URL; the deploy guide listed a `busy` mode that `env.js::modeOf` never returns; and seven
comments in `functions/` cited `sim/web/audio.js`, which was split into `sim/web/voice/` on
2026-09-26. Every guard passed, because none of them reads what a sentence claims.

Three checks, each with a control that keeps it from passing vacuously:

1. A retired claim may still be MENTIONED, but only in a sentence that retires it ("they are
   not a global ceiling"), the rule `scripts/check-doc-consistency.py` applies to the RE study.
2. The deploy guide's mode table names exactly the modes `/api/health` can answer, plus the
   page's own `offline`.
3. Nothing cites a deleted file, except the lines pinned in `PENDING` with their reason.
"""
import os
import re

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

#: Generated or vendored trees, and the RE study (its own guard is check-doc-consistency).
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".wrangler", "vendor",
              "docs-bundle", "reverse-engineering"}
_SKIP_FILES = {"sim/web/docs-index.json", "sim/web/docs-search.json"}


def read(rel: str) -> str:
    with open(os.path.join(REPO, rel), encoding="utf-8", errors="replace") as fh:
        return fh.read()


def tracked(exts) -> list:
    """Repo-relative paths with one of `exts`, outside the skipped trees, sorted."""
    out = []
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [d for d in dirnames
                       if d not in _SKIP_DIRS and not d.startswith((".venv", "venv"))]
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), REPO).replace(os.sep, "/")
            if rel in _SKIP_FILES:
                continue
            if fn.endswith(exts) or fn == ".dev.vars.example":
                out.append(rel)
    return sorted(out)


def described_in() -> list:
    """Where the hosted demo is described: every markdown page, the Functions (comments are
    where `limits.js` says what it is), the public pages, and the variables template."""
    return [rel for rel in tracked((".md", ".js", ".html"))
            if rel.endswith(".md") or rel.startswith("functions/") or rel == ".dev.vars.example"
            or (rel.startswith("sim/web/") and rel.endswith(".html") and rel.count("/") == 2)]


# ----------------------------------------------------------------- 1. retired claims --

#: (the claim, what retires it inside the same sentence, why the claim is false).
RETIRED_CLAIMS = [
    (re.compile(r"\bglobal\b(?:[\s/-]+\w+){0,2}?[\s/-]+"
                r"(?:rate[ -]limits?|ceilings?|caps?|budgets?|limits?)\b", re.I),
     re.compile(r"\b(?:not|no|none|never|nothing|nor|isn't|aren't|cannot|can't)\b", re.I),
     "the rate limits and the unit budget are counted per isolate and per colo and fail open "
     "(live-sim-demo.md §4.6); only a budget on the gateway key is a global ceiling"),
    (re.compile(r"\bfastest incident response\b", re.I),
     re.compile(r"\bnot\b[\s\w]{0,12}\bfastest\b", re.I),
     "DEMO_ENABLED applies only to the next deployment and never reaches an older "
     "deployment's own URL; revoking the key at the gateway reaches every deployment at once "
     "(deploy-cloudflare.md section 7)"),
]


def sentences(text: str) -> list:
    """Prose as sentences: comment markers dropped, wrapped lines joined, each table row on
    its own (a row is one statement, and rows do not end with a full stop)."""
    units, buf = [], []
    for line in text.splitlines():
        s = re.sub(r"^(?:\*+|//+|#+)\s?", "", line.strip()).strip()
        if not s or s.startswith("|"):
            if buf:
                units.append(" ".join(buf))
                buf = []
            if s:
                units.append(s)
            continue
        buf.append(s)
    if buf:
        units.append(" ".join(buf))
    return [snt for u in units for snt in re.split(r"(?<=[.!?;])\s+", u) if snt]


def live_claims(text: str) -> list:
    """`(sentence, why)` for each retired claim `text` makes as a live statement."""
    out = []
    for snt in sentences(text):
        for claim, retired, why in RETIRED_CLAIMS:
            if claim.search(snt) and not retired.search(snt):
                out.append((snt, why))
    return out


def test_the_claim_check_has_teeth():
    """The two sentences that were shipped are caught; their retirements are not."""
    caught = [
        "- A static hosted version on Cloudflare Pages with a real brain, voice and ears, "
        "per-visitor and\n  global rate limits, a capacity indicator, and a scripted fallback.",
        "# The kill switch. `0` forces every route to `gateway_not_configured` WITHOUT deleting\n"
        "# the secret — the fastest incident response there is.",
    ]
    for text in caught:
        assert live_claims(text), f"not caught: {text!r}"
    retired = [
        "The edge rate-limit counters are per-colo and fail open, so they are not a\n"
        "  global spending ceiling.",
        " * **NOT A GLOBAL CEILING, AND NOTHING MAY CALL IT ONE.**",
        "| 25 | The counters are not a global ceiling | proven (§4.6) |",
        "# the secret. It is NOT the fastest incident response: like every variable it applies",
    ]
    for text in retired:
        assert not live_claims(text), f"a retirement was flagged: {text!r}"


def test_no_retired_claim_is_made_as_a_live_statement():
    files = described_in()
    # The scope is not empty, and it reaches the files that carry the retirements.
    for rel in ("ROADMAP.md", "functions/api/_lib/limits.js", ".dev.vars.example",
                "docs/guides/deploy-cloudflare.md"):
        assert rel in files, f"{rel} fell out of the scanned set"
    seen = sum(1 for rel in files for snt in sentences(read(rel))
               if RETIRED_CLAIMS[0][0].search(snt))
    assert seen >= 5, f"only {seen} 'global ceiling' sentences seen: is the scan still reading?"

    bad = [f"{rel}: {snt[:160]!r}\n      why it is false: {why}"
           for rel in files for snt, why in live_claims(read(rel))]
    assert not bad, (
        "A retired claim is made as a live statement. Say what the code does instead, or "
        "retire the claim in the same sentence (`not a global ceiling`):\n  "
        + "\n  ".join(bad))


# --------------------------------------------------------------- 2. the mode table --

def health_modes() -> set:
    """The `mode` values `env.js::modeOf` can return: what `/api/health` answers."""
    src = read("functions/api/_lib/env.js")
    body = src[src.index("export function modeOf"):]
    body = body[:body.index("\n}\n")]
    return set(re.findall(r'\bmode:\s*"(\w+)"', body))


def guide_modes() -> set:
    """The first column of the deploy guide's `mode` table."""
    lines = read("docs/guides/deploy-cloudflare.md").splitlines()
    start = next(i for i, ln in enumerate(lines)
                 if re.match(r"^\|\s*`mode`\s*\|", ln))
    out = set()
    for ln in lines[start + 2:]:
        if not ln.startswith("|"):
            break
        out.update(re.findall(r"^\|\s*`(\w+)`", ln))
    return out


def test_the_deploy_guide_lists_the_modes_the_code_returns():
    returned = health_modes()
    assert {"live", "degraded"} <= returned, f"modeOf parsed as {sorted(returned)}"
    # `offline` is the page's verdict on a missing route, never a health answer.
    assert 'setState("offline"' in read("sim/web/mode.js"), "mode.js no longer has offline"
    want = returned | {"offline"}
    got = guide_modes()
    assert got == want, (
        f"docs/guides/deploy-cloudflare.md's mode table lists {sorted(got)}; the code can "
        f"produce {sorted(want)} (env.js::modeOf, plus mode.js's own `offline`). Extra: "
        f"{sorted(got - want)}; missing: {sorted(want - got)}")


# ------------------------------------------------------------ 3. deleted files cited --

#: A file that no longer exists, how a citation of it reads, and where it went.
DELETED = {
    "sim/web/audio.js": (re.compile(r"(?<![\w./-])audio\.js\b|sim/web/audio\.js"),
                         "split into sim/web/voice/ (cloud.js decodes CloudTTSResponse, "
                         "local.js owns skipProbe) on 2026-09-26"),
}

#: Citations left in files another open change owned when this guard was written, pinned
#: by their exact text so nothing new can hide behind them. Each is a comment, listed for
#: the integrator in the W3-S14 PR; delete the entry when the line is fixed.
PENDING = {
    ("functions/api/_lib/env.js", "The only audio formats `audio.js` can decode"),
    ("sim/ci/ci.yml", "chatter talking over a gateway answer (sim/web/ambient.js + audio.js)"),
    (".github/workflows/ci.yml",
     "chatter talking over a gateway answer (sim/web/ambient.js + audio.js)"),
}


def test_nothing_cites_a_deleted_file():
    files = tracked((".md", ".js", ".mjs", ".py", ".yml", ".html", ".sh"))
    assert "functions/api/_lib/wav.js" in files and "sim/ci/ci.yml" in files
    bad = []
    for gone, (cite, where) in DELETED.items():
        assert not os.path.exists(os.path.join(REPO, gone)), f"{gone} exists again"
        assert cite.search("`audio.js::decodeCloudTTS`") and not cite.search("sim/web/voice/x.js")
        for rel in files:
            if rel == "sim/tests/test_hosted_docs_truth.py":
                continue
            for n, line in enumerate(read(rel).splitlines(), 1):
                if cite.search(line) and not any(rel == f and pin in line for f, pin in PENDING):
                    bad.append(f"{rel}:{n}: {line.strip()[:120]}\n      ({gone}: {where})")
    assert not bad, "A deleted file is cited:\n  " + "\n  ".join(bad)
