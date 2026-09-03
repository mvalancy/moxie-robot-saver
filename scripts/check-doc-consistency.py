#!/usr/bin/env python3
"""
Documentation-tree consistency guard.

The docs are a tree we grow, not a pile we polish (see docs/README.md "How this documentation tree is
maintained"). This enforces the parts of that SOP a machine can check, so message-drift is caught before
it ships:

  1. STALE CLAIMS — phrases asserting things we have since disproven must not appear as live statements.
     A hit is allowed only if the same line also carries a retirement marker (superseded / retired /
     bounded / closed / historical / "no hidden" …), i.e. the phrase is being described *as* retired.
  2. VERSION STAMP — robot-side RE pages (firmware/runtime/protocol/hardware) should carry the analyzed
     firmware stamp so every robot-side claim is anchored to the build. Reported; not fatal by default.

Exit non-zero if any stale-claim check fails. Run from the repo root:
    python3 scripts/check-doc-consistency.py
"""
import os
import re, sys, pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
RE_DIR = REPO / "docs" / "reverse-engineering"
STAMP = "v24.10.803"

# Phrases that assert a belief we've disproven. Each may appear ONLY when the same line also contains an
# allow-marker (so a doc may still *mention* the retired idea to explain that it's retired).
STALE_CLAIMS = [
    r"QR[- ]command discovery",
    r"hunt (?:for )?undocumented",
    r"undocumented(?:/| or )?factory QR code",
    r"exploratory rig",
    r"rig(?: is)? running",
    r"guess(?:ing)? (?:the )?command string",
    r"only\s+\W?om\W?\s+is (?:confirmed|known)",
]
ALLOW_MARKERS = re.compile(
    r"retired|superseded|deprecated|historical|provably|bounded|closed grammar|no hidden|"
    r"no (?:fifth|second|other) (?:app-side )?handler|not an open|no longer|pre-decompilation",
    re.I,
)
# Skip machine-generated / vendored / snapshot trees.
SKIP = ("recovered-proto/", "manifests/", "docs-bundle/", "/keys/")
ROBOT_SIDE = ("firmware/", "runtime/", "protocol/", "hardware/")

def md_files():
    for f in RE_DIR.rglob("*.md"):
        rel = f.relative_to(REPO).as_posix()
        if any(s in rel for s in SKIP):
            continue
        yield f, rel


def check_no_conflict_markers(root):
    """No file may carry a committed merge-conflict marker.

    Added 2026-09-03 because it happened: PR #93 merged with `<<<<<<< HEAD` /
    `=======` / `>>>>>>> origin/dev` committed into
    `docs/architecture/openmoxie-feature-audit.md` and its docs-bundle copy — a
    duplicated row 3 and a lost row 4 — and **every guard passed**. The doc bundle
    built, 3308 links resolved, `test_docs.mjs` was green, the suite was green: none
    of them look at the text of a line. A conflict marker is the one defect that is
    both trivially detectable and invisible to every other check we run.

    Scanned as whole lines at the start of a line, so prose *about* markers (this
    docstring included) cannot trip it — the same comment-stripping lesson the
    `functions/` json-import guard learned.
    """
    import re as _re
    # ONLY `<<<<<<<` and `>>>>>>>`. A bare `=======` is a legitimate reStructuredText
    # underline — pytest's own sources carry one — and a real conflict always brings all
    # three markers, so dropping the ambiguous one costs no detection and removes the
    # false positive that fired the first time this guard met a venv.
    bad, pat = [], _re.compile(r"^(<{7}|>{7})(\s|$)")
    skip = {".git", "node_modules", "__pycache__", "dist", ".pytest_cache", "site-packages"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in skip and not d.startswith(".venv") and not d.startswith("venv")]
        for fn in filenames:
            if not fn.endswith((".md", ".py", ".js", ".mjs", ".json", ".yml", ".yaml", ".sh")):
                continue
            full = os.path.join(dirpath, fn)
            try:
                with open(full, encoding="utf-8", errors="replace") as fh:
                    for n, line in enumerate(fh, 1):
                        if pat.match(line):
                            bad.append(f"{os.path.relpath(full, root)}:{n}: {line.rstrip()[:60]}")
            except OSError:
                continue
    if bad:
        print("conflict markers committed:")
        for b in bad[:20]:
            print("  " + b)
        return False
    return True


def main():
    stale_hits, missing_stamp = [], []
    stale_res = [re.compile(p, re.I) for p in STALE_CLAIMS]
    for f, rel in md_files():
        text = f.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if ALLOW_MARKERS.search(line):
                continue
            for rx in stale_res:
                if rx.search(line):
                    stale_hits.append((rel, i, rx.pattern, line.strip()[:100]))
        # version stamp: robot-side leaf docs (not the folder index READMEs, which are pure nav)
        if any(seg in rel for seg in ROBOT_SIDE) and not rel.endswith("/README.md"):
            if STAMP not in text and "firmware-803-reference" not in text:
                missing_stamp.append(rel)

    ok = True
    if not check_no_conflict_markers(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
        ok = False
    if stale_hits:
        ok = False
        print("STALE CLAIMS (assert something we've disproven, with no retirement marker on the line):")
        for rel, ln, pat, snippet in stale_hits:
            print(f"   {rel}:{ln}  /{pat}/  -> {snippet}")
    if missing_stamp:
        print(f"\nVERSION-STAMP (robot-side pages with no '{STAMP}' stamp — anchor them to the build):")
        for rel in missing_stamp:
            print(f"   {rel}")

    n = sum(1 for _ in md_files())
    if ok:
        extra = f" ({len(missing_stamp)} unstamped — advisory)" if missing_stamp else ""
        print(f"\nconsistency OK - {n} RE docs, no stale claims{extra}")
        return 0
    print(f"\nconsistency FAILED - {len(stale_hits)} stale claim(s) across {n} RE docs")
    return 1

if __name__ == "__main__":
    sys.exit(main())
