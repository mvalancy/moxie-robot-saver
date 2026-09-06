#!/usr/bin/env python3
"""Bundle the repo's Markdown docs into the static site so the docs explorer
(sim/web/docs.html) can browse them on a Cloudflare Pages deploy with no server.

Walks docs/ (+ a few top-level .md), copies each file verbatim into
sim/web/docs-bundle/<same relative path>, and writes sim/web/docs-index.json:

    { "firmware": "v3.6.4-Zephyr / OTA v24.10.803",
      "files": [ { "path": "reverse-engineering/qr-commands.md",
                   "title": "QR commands", "section": "reverse-engineering",
                   "bytes": 1234, "mermaid": 2, "headings": ["...", "..."] }, ... ] }

The explorer fetches the index to build its tree/search, then fetches each .md on
click and renders it (marked + mermaid, both vendored). Idempotent; safe to re-run
every time docs change. Byte-for-byte copies — no transformation — so what the
explorer shows is exactly what's in the repo.

Usage:  python3 sim/tools/build_docs_bundle.py
"""
import json, os, re, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
WEB = os.path.join(REPO, "sim", "web")
OUT = os.path.join(WEB, "docs-bundle")
INDEX = os.path.join(WEB, "docs-index.json")
SEARCH = os.path.join(WEB, "docs-search.json")
FIRMWARE = "v3.6.4-Zephyr / OTA v24.10.803"

# What to include: the whole docs/ tree, plus these top-level docs.
TOP_LEVEL = ["README.md", "ROADMAP.md"]


def title_of(text, fallback):
    for line in text.splitlines():
        m = re.match(r"^#\s+(.*)", line.strip())
        if m:
            # drop leading emoji/symbols for a clean tree label
            return re.sub(r"^[^\w(]+", "", m.group(1)).strip() or fallback
    return fallback


def headings(text):
    hs = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = re.match(r"^(#{2,3})\s+(.*)", line)
        if m:
            hs.append(re.sub(r"^[^\w(]+", "", m.group(2)).strip())
    return hs[:40]


def collect():
    files = []
    roots = [(os.path.join(REPO, "docs"), "docs")]
    # Markdown docs (the curated pages) + the plain-text manifests/schemas the docs LINK to
    # (`.tsv`/`.dts`) so those links open in the explorer instead of 404-ing. Rendered as
    # syntax-highlighted code, not markdown (see `kind` below).
    TEXT_EXT = (".tsv", ".dts")
    for base, prefix in roots:
        for dirpath, _dirs, names in os.walk(base):
            _dirs.sort()  # deterministic traversal order (os.walk yields dirs in FS order)
            for n in sorted(names):
                if not (n.endswith(".md") or n.endswith(TEXT_EXT)):
                    continue
                full = os.path.join(dirpath, n)
                rel = os.path.relpath(full, base)          # e.g. reverse-engineering/qr-commands.md
                files.append((full, rel.replace(os.sep, "/")))
    # a couple of top-level docs, namespaced under _root/
    for n in TOP_LEVEL:
        full = os.path.join(REPO, n)
        if os.path.isfile(full):
            files.append((full, "_root/" + n))
    return files


def link_order(text):
    """The ordered list of local .md link *basenames* in a README's body — the curated
    reading order a section index defines. Deduped, first-occurrence wins."""
    order, seen = [], set()
    for target in re.findall(r"\]\(([A-Za-z0-9._/-]+\.md)\)", text):
        bn = target.rsplit("/", 1)[-1]
        if bn not in seen:
            seen.add(bn); order.append(bn)
    return {bn: i for i, bn in enumerate(order)}


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT, exist_ok=True)

    entries = []
    search = {}
    readme_rank = {}   # section -> {basename: position in that section's README}
    for full, rel in collect():
        with open(full, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        dst = os.path.join(OUT, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(full, dst)
        section = rel.split("/")[0] if "/" in rel else "docs"
        # A section's own README defines the curated within-section reading order.
        if rel == section + "/README.md":
            readme_rank[section] = link_order(text)
        is_md = rel.endswith(".md")
        entries.append({
            "path": rel,
            "title": title_of(text, os.path.basename(rel)) if is_md else os.path.basename(rel),
            "section": section,
            "bytes": len(text.encode("utf-8")),
            "mermaid": text.count("```mermaid") if is_md else 0,
            "headings": headings(text) if is_md else [],
            "kind": "md" if is_md else "text",
        })
        # full-text search blob: strip mermaid/code fences' language markers + collapse
        # whitespace so a client-side substring search over the prose + code works.
        search[rel] = re.sub(r"\s+", " ", text).strip()

    # Order docs WITHIN each section by that section's README link list (the curated
    # narrative), so the explorer's tree + prev/next pager read in the intended order
    # instead of alphabetically. The section index README pins to the top; docs the
    # README doesn't link fall after the listed ones, alphabetically. (Section ORDER
    # itself is decided by the explorer's SECTION_ORDER.) Self-maintaining: adding a
    # doc to a README reorders it here automatically.
    def sort_key(e):
        sec, path = e["section"], e["path"]
        bn = path.rsplit("/", 1)[-1]
        if path == sec + "/README.md":
            rank = -1                                   # the section index, first
        else:
            rank = readme_rank.get(sec, {}).get(bn, 10 ** 6)   # unlisted → after, A–Z
        return (sec, rank, path)
    entries.sort(key=sort_key)
    # ---------------------------------------------------------------------------
    # MERGEABILITY (read before changing either json.dump below).
    #
    # These two files are generated AND committed, so every branch that touches a
    # doc rewrites them. That is fine — as long as a plain 3-way *text* merge can
    # reconcile two branches that edited different docs. Two rules keep that true:
    #
    #   1. NO GLOBAL, CONTENT-DERIVED VALUE in a committed artifact. This file used
    #      to carry a top-level "generated": "docs-<sha256[:12]>" stamp over the whole
    #      doc set. Nothing ever read it (not docs.js, not sim/test_docs.mjs, not
    #      check_bundle_fresh.py — that one re-runs this script and diffs with git),
    #      but because it changed on BOTH sides of every merge it made this file
    #      conflict mechanically on every pair of doc-touching branches. Removed.
    #      Do not reintroduce one: any single line that both sides rewrite is an
    #      unconditional conflict, and it would re-impose that tax on every branch.
    #
    #   2. ONE DOC PER LINE. docs-search.json used to be one ~3 MB line, which gave
    #      the merge zero granularity: any two edits collided on that single line.
    #      Each doc's blob now gets its own line, blank-line separated, so edits to
    #      different docs land in different hunks and merge cleanly — and merge
    #      byte-identically to a rebuild, since the keys are sorted and each value
    #      is self-contained.
    #
    # Both files stay deterministic, so check_bundle_fresh.py is unaffected: it is
    # still the sole authority on whether the committed bundle matches docs/.
    # ---------------------------------------------------------------------------
    with open(INDEX, "w", encoding="utf-8") as fh:
        json.dump({"firmware": FIRMWARE, "files": entries},
                  fh, indent=1, ensure_ascii=False)
    # Separate, lazily-fetched full-text index (only loaded when the user searches).
    # One "path":"blob" pair per line; see rule 2 above. Hand-written rather than
    # json.dump(indent=...) because json's indent modes cannot emit a blank line.
    with open(SEARCH, "w", encoding="utf-8") as fh:
        # A BLANK LINE between entries is JSON-insignificant whitespace, and it is
        # what lets git merge edits to two *alphabetically adjacent* docs: without
        # a common line between them the two changed lines are one region, and one
        # region touched by both sides is a conflict. See rule 2 above.
        fh.write("{\n")
        for i, k in enumerate(sorted(search)):
            if i:
                fh.write(",\n\n")
            fh.write(json.dumps(k, ensure_ascii=False) + ":" +
                     json.dumps(search[k], ensure_ascii=False))
        fh.write("\n}\n")

    total_mermaid = sum(e["mermaid"] for e in entries)
    print(f"[docs-bundle] {len(entries)} markdown files, "
          f"{sum(e['bytes'] for e in entries)//1024} KiB, "
          f"{total_mermaid} mermaid diagrams, {sum(len(v) for v in search.values())//1024} KiB full-text "
          f"→ {os.path.relpath(OUT, REPO)}/ + docs-index.json + docs-search.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
