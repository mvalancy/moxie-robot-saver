"""The hosted demo's docs say what its code does.

Measured 2026-10-08 (review lane l5, re-checked by W3-S14): the roadmap, also served to every
visitor by the docs explorer, promised "per-visitor and global rate limits" while
`functions/api/_lib/limits.js` says in capitals that its counters are not a global ceiling;
`.dev.vars.example` called `DEMO_ENABLED=0` "the fastest incident response there is", though
Pages applies a variable only to the NEXT deployment and never to an older deployment's own
URL; the deploy guide listed a `busy` mode that `env.js::modeOf` never returns; and seven
comments in `functions/` cited `sim/web/audio.js`, which was split into `sim/web/voice/` on
2026-09-26. Every guard passed, because none of them reads what a sentence claims.

Four checks, each with a control that keeps it from passing vacuously:

1. A retired claim may still be MENTIONED, but only in a sentence that retires it ("they are
   not a global ceiling"), the rule `scripts/check-doc-consistency.py` applies to the RE study.
2. The deploy guide's mode table names exactly the modes `/api/health` can answer, plus the
   page's own `offline`.
3. Nothing cites a deleted file, except the lines pinned in `PENDING` with their reason.
4. Every default a doc states for a per-visitor window (chat, speech, transcribe; a minute, an
   hour, a day) is the one `env.js` sets, and a table that gives one of a route's windows gives
   all three. Merging W3-S14 into L1 (the voice's windows raised to 15 / 120 / 450) conflicted
   on the spec's §5 rows: keeping dev's side whole left 10 / 80 / 300 there, keeping L1's
   dropped the transcribe day, and every other guard passed either way.
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


# ------------------------------------------------------- 4. the per-visitor windows --

#: The per-visitor windows as `env.js` DEFAULTS names them: chat turns, speech calls (one a
#: voice chunk) and transcribe uploads, each counted a minute, an hour and a UTC day.
ROUTES = {"CHAT": "chat", "SPEECH": "speech", "STT": "transcribe"}
SPANS = ("MIN", "HOUR", "DAY")
WINDOWS = tuple(f"DEMO_{r}_PER_{s}" for r in ROUTES for s in SPANS)

#: A window row's first cell: the config tables' `DEMO_SPEECH_PER_MIN` / `_HOUR` / `_DAY`,
#: and the spec's §4.1 `Per-IP speech`.
_ENV_CELL = re.compile(r"`DEMO_(CHAT|SPEECH|STT)_PER_MIN`((?:\s*/\s*`_(?:HOUR|DAY)`)*)")
_PER_IP_CELL = re.compile(r"Per-IP (chat|speech|transcribe)")


def window_defaults() -> dict:
    """What `env.js` DEFAULTS sets each window to."""
    src = read("functions/api/_lib/env.js")
    body = src[src.index("export const DEFAULTS"):]
    body = body[:body.index("\n});")]
    hits = {k: re.search(rf"^\s*{k}:\s*(\d+),", body, re.M) for k in WINDOWS}
    return {k: int(m.group(1)) for k, m in hits.items() if m}


def _number(text: str):
    digits = re.sub(r"\s", "", text)
    return int(digits) if digits.isdigit() else None


def stated_windows(text: str) -> list:
    """`(where, window, value)` for each default `text` states for a window: a row of a table
    whose second column is `Default` (`` `DEMO_SPEECH_PER_MIN` / `_HOUR` / `_DAY` | 15 / 120 /
    450 ``, or the spec's §4.1 `Per-IP speech | 15/min · 120/hour · 450/day`), or a `KEY=value`
    in the paragraph that opens "Everything else has a default". `where` names a table by its
    header's line, so each table is checked whole on its own."""
    out, table, listing = [], None, False
    by_name = {name: r for r, name in ROUTES.items()}
    for n, line in enumerate(text.splitlines(), 1):
        if not line.lstrip().startswith("|"):
            table = None
        else:
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if table is None:
                table = f"the table at line {n}" if cells[1:2] == ["Default"] else ""
            elif table:
                env = _ENV_CELL.fullmatch(cells[0])
                per_ip = _PER_IP_CELL.fullmatch(cells[0])
                if env:
                    spans = ["MIN"] + re.findall(r"`_(HOUR|DAY)`", env.group(2))
                    vals = [_number(v) for v in cells[1].split("/")]
                    vals = vals if len(vals) == len(spans) else [None] * len(spans)
                    out += [(table, f"DEMO_{env.group(1)}_PER_{s}", v)
                            for s, v in zip(spans, vals)]
                elif per_ip:
                    route = by_name[per_ip.group(1)]
                    out += [(table, f"DEMO_{route}_PER_{s.upper()}", _number(v))
                            for v, s in re.findall(r"([\d\s]+)/(min|hour|day)\b", cells[1])]
        listing = ("Everything else has a default" in line
                   or (listing and bool(line.strip("#*/ \t"))))
        if listing:
            assigned = re.findall(r"DEMO_(CHAT|SPEECH|STT)_PER_(MIN|HOUR|DAY)=(\d+)", line)
            out += [("the defaults paragraph", f"DEMO_{r}_PER_{s}", int(v))
                    for r, s, v in assigned]
    return out


def window_problems(text: str, want: dict) -> list:
    """Each wrong statement in `text`: a value that is not `want`'s, or a table that gives one
    of a route's windows and leaves out another."""
    stated = stated_windows(text)
    bad = [f"{where}: {key} is {val}, the default is {want[key]}"
           for where, key, val in stated if val != want[key]]
    for where in sorted({w for w, _, _ in stated if w.startswith("the table")}):
        keys = {k for w, k, _ in stated if w == where}
        for r in ROUTES:
            route = {f"DEMO_{r}_PER_{s}" for s in SPANS}
            if keys & route and not route <= keys:
                bad.append(f"{where}: states {sorted(keys & route)} "
                           f"but not {sorted(route - keys)}")
    return bad


def test_the_window_check_has_teeth():
    """Either side of the §5 conflict kept whole is caught; the line-by-line one is not."""
    want = dict(zip(WINDOWS, (5, 40, 150, 15, 120, 450, 10, 60, 225)))
    table = ("| Variable | Default | Range / notes |\n|---|--:|---|\n"
             "| `DEMO_CHAT_PER_MIN` / `_HOUR` / `_DAY` | 5 / 40 / 150 | ≥ 1 |\n"
             "| `DEMO_SPEECH_PER_MIN` / `_HOUR` / `_DAY` | {} | ≥ 1 / ≥ 1 / 0..10 000 000 |\n"
             "| `DEMO_STT_PER_MIN` / `_HOUR`{} | {} | ≥ 1 |\n")
    devs_side = table.format("10 / 80 / 300", " / `_DAY`", "10 / 60 / 225")
    ours_side = table.format("15 / 120 / 450", "", "10 / 60")
    resolved = table.format("15 / 120 / 450", " / `_DAY`", "10 / 60 / 225")
    assert len(window_problems(devs_side, want)) == 3, window_problems(devs_side, want)
    assert window_problems(ours_side, want) == [
        "the table at line 1: states ['DEMO_STT_PER_HOUR', 'DEMO_STT_PER_MIN'] but not "
        "['DEMO_STT_PER_DAY']"]
    assert window_problems(resolved, want) == []
    # The spec's §4.1 rows and the template's paragraph are read too; a table whose second
    # column is not the default (a production setting, say) states no default.
    per_ip = ("| Control | Default | Why |\n|---|--:|---|\n"
              "| Per-IP speech | 10/min · 80/hour · 300/day | two |\n")
    listing = ("# Everything else has a default: DEMO_CHAT_PER_MIN=5,\n"
               "# DEMO_SPEECH_PER_DAY=300 (each visitor's daily voice)\n")
    production = ("| Variable | Production |\n|---|---|\n"
                  "| `DEMO_SPEECH_PER_MIN` / `_HOUR` | 99 / 99 |\n")
    assert len(window_problems(per_ip, want)) == 3, window_problems(per_ip, want)
    assert len(window_problems(listing, want)) == 1, window_problems(listing, want)
    assert window_problems(production, want) == []


def test_every_default_stated_for_a_per_visitor_window_is_the_one_env_js_sets():
    want = window_defaults()
    assert sorted(want) == sorted(WINDOWS), f"env.js DEFAULTS parsed as {want}"
    # Not vacuous: the places that state them are still read, each still states the voice's
    # three windows, and the spec still has its two tables (§4.1 and §5).
    voice = {f"DEMO_SPEECH_PER_{s}" for s in SPANS}
    for rel, tables in (("docs/architecture/backlog/live-sim-demo.md", 2),
                        ("docs/guides/deploy-cloudflare.md", 1), (".dev.vars.example", 0)):
        got = stated_windows(read(rel))
        assert voice <= {k for _, k, _ in got}, f"{rel} does not state {sorted(voice)}"
        assert len({w for w, _, _ in got if w.startswith("the table")}) >= tables, rel
    bad = [f"{rel}, {p}" for rel in described_in() for p in window_problems(read(rel), want)]
    assert not bad, (
        "A doc states a per-visitor window's default that env.js DEFAULTS does not set. Make "
        "the doc say what the code does (after a merge conflict on such a row, resolve it line "
        "by line, never one side whole):\n  " + "\n  ".join(bad))
