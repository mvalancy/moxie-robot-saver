---
name: publishing-moxie-docs
description: Rebuilds the static docs-explorer bundle and runs every doc and site guard, keeping the Moxie doc tree consistent top to bottom. Use after editing anything under docs/ or the explorer (sim/web/docs.html), before committing.
---

# Docs bundle + guards — publish and verify

The docs are published through a static explorer (`sim/web/docs.html`) that reads a committed bundle.
After any docs edit, run this so the explorer, links, and tree stay correct.

## Rebuild + verify (in order — all must pass before committing)
```bash
python3 sim/tools/build_docs_bundle.py     # regenerate sim/web/docs-bundle/ + docs-index.json + docs-search.json
python3 scripts/check-doc-links.py         # every internal link AND #anchor resolves (replicates the explorer slugify)
python3 scripts/check-doc-consistency.py   # no stale/retired claims; robot-side pages carry the v24.10.803 stamp
node sim/test_docs.mjs                      # bundle covers every docs/*.md; mermaid counts; README order/orphan guard
node sim/test_mermaid.mjs                   # all ```mermaid fences render clean
node sim/test_docs_explorer.mjs             # headless: tree, search, mermaid, highlight, deep-links, sub-heads
```
The bundle is reproducible (content-hash stamp), so a rebuild on an unchanged doc set is a 0-diff no-op —
`git status` after rebuilding tells you whether you actually changed anything.

## The standing rules the guards enforce
- **One message, root to leaf.** A finding that changes the story is pushed upward in the same pass — the leaf, its subfolder README, the section index, and (if it changes the headline) `docs/README.md` + root `README.md`. Two levels must never disagree. Full SOP: `docs/README.md` → "How this documentation tree is maintained".
- **Every folder is navigable.** Each `docs/` subfolder with ≥2 pages has a `README.md` indexing it and linking up; parents link down. New pages join the curated order, not an A–Z tail.
- **Retire, don't strand.** When a finding supersedes an old belief, update or banner the old page in the same commit — `check-doc-consistency.py` has a stale-claim denylist.
- **Anchors:** the slugify is `lowercase → strip to [a-z0-9_- ] → spaces→hyphen → collapse dashes`; an emoji, em-dash, or `(…)` in a heading drops out (`## Goal ② — X` → `#goal-x`). Trust `check-doc-links.py` over hand-guessing an anchor.

## Gotchas
- Commit messages with backticks or parens get shell-mangled — write the message to a file and `git commit -F <file>`.
- Non-`.md` files (`.tsv`/`.dts` manifests) are bundled as `kind:"text"` and rendered as code; the order/orphan guard is scoped to `.md`. Raw `.proto` files are deliberately not in the tree yet (120 would clutter; they need a collapsible nested sub-tree — the sub-head routing for `manifests/`/`recovered-proto/`/`keys/` is the groundwork).
- **No CDN.** Every web dep is vendored under `sim/web/vendor/`. Off-host refs are only canonical URLs + Google Fonts.
- **Headless verify** uses puppeteer + local chrome (`~/.cache/puppeteer`) with `--use-gl=swiftshader --enable-unsafe-swiftshader`; assert zero console errors.

## Completeness bar
Apply the clean-room sufficiency test (see the `reversing-moxie-firmware` skill): could someone rebuild this piece from the doc alone? Capture the data; don't point at the binary. Track it in `COVERAGE.md`.
