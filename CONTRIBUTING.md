# Contributing

For anyone changing this repo: a person opening a pull request, or an agent session working a
slice. Owners who only want to run Moxie need none of this; start at the
[project README](README.md).

## How we write docs

The Style Card. Every page in the tree is held to it, and a reviewer may quote any line of it.

1. Say who the page is for in its first lines; keep contributor and agent process (slices, waves, review rounds, cadences) out of owner pages.
2. Lead with what it is, what it does for the reader, how to use it, then why it is built that way.
3. Short first: one or two sentences, then detail under a heading that names the thing.
4. One idea per sentence, about 25 words or fewer; no nested parentheticals, no bullet walls.
5. State a fact in full once, on its canonical page; everywhere else one line plus a link.
6. Narrative history leaves the page (no 'round 4 found', 'the reviewer measured', 'until 2026-10-08 it was'); git holds it.
7. Evidence stays in the repo: measurements, dated verification stamps, negative results and user-impacting changes go in a closing Evidence section, not mid-prose.
8. Headings say what the section is, never a slogan.
9. Cite code as path plus symbol or heading anchor; use :line only with a pinned commit. Keep every citation when text moves.
10. A doc that code cites by line is edited line-neutrally above the cited lines, or the citations move in the same PR.
11. After changing a count or a name, grep every instance (docs, code comments, tests) and fix all of them in the same PR.
12. Never commit private infrastructure: LAN or tailnet IPs, private hosts or ports, which server backs an alias, a real robot's MAC.
13. Reverse-engineering facts are condensed and reorganized, never deleted; the repo stays self-sufficient (assume every link dies).
14. README house style: emoji + short title; one-line intro; a bullet list of files and subfolders with backtick links and em-dash notes; a footer line that links the parent README. Every folder has one (never under .github/).
15. Before committing docs: python3 sim/tools/build_docs_bundle.py; node sim/test_docs.mjs; python3 scripts/check-doc-links.py; python3 scripts/check-doc-consistency.py; pytest sim/tests/test_hosted_docs_truth.py sim/tests/test_no_offsite_images.py. Commit the doc with its rebuilt bundle, then python3 sim/tools/check_bundle_fresh.py.

## Doc guards

The canonical copy of the guard list; `docs/README.md` repeats its one-line form for the explorer's
readers. Run it from the repo root after any change under `docs/`, to `README.md` or to
`ROADMAP.md`. Commit the regenerated bundle (`sim/web/docs-bundle/**`, `sim/web/docs-index.json`,
`sim/web/docs-search.json`) in the same commit as the doc. Never hand-edit or hand-merge those
three; on a conflict, regenerate.

```sh
python3 sim/tools/build_docs_bundle.py
node sim/test_docs.mjs
python3 scripts/check-doc-links.py
python3 scripts/check-doc-consistency.py
python3 -m pytest -q sim/tests/test_hosted_docs_truth.py sim/tests/test_no_offsite_images.py
# commit the doc together with the regenerated bundle, then:
python3 sim/tools/check_bundle_fresh.py
```

`check_bundle_fresh.py` runs last because it compares a rebuild against `git status`: it passes
only once the doc and its rebuilt bundle are committed, and it fails if the bundle was left out of
the commit. Run before the commit, it reports a correct bundle as stale. CI runs it on a clean
checkout.

| Guard | What it proves |
|---|---|
| `build_docs_bundle.py` | Regenerates the explorer's copy of the docs: the bundle, the index and the search blob. It is deterministic, so a rebuild on unchanged docs is a no-op. |
| `test_docs.mjs` | Every `docs/` page is bundled; each section's reading order follows its README and no page is orphaned; every docs folder with two or more pages has a README; Moxie's docs lookup still cites the pinned pages. |
| `check-doc-links.py` | Every internal link and `#anchor` resolves, with anchors slugified the way the explorer does. |
| `check-doc-consistency.py` | No retired claim is stated as live in the reverse-engineering study, and no merge-conflict marker is committed anywhere in the repo. Robot-side pages missing the `v24.10.803` stamp are listed as advisory, not failed. |
| `test_hosted_docs_truth.py` | The hosted demo's docs say what its code does: no retired claim (a "global ceiling") made live, the deploy guide's mode table matches `env.js`, nothing cites a deleted file, and the per-visitor windows match the code. |
| `test_no_offsite_images.py` | No doc embeds an off-site image, and every doc image lives under `sim/web/`. |
| `check_bundle_fresh.py` | The committed bundle matches a rebuild of the docs: no doc was edited without regenerating, and the regenerated files went into the commit. |

When `README.md`, `docs/README.md`, `docs/guides/revive-your-moxie.md` or the reverse-engineering
tree changes, also run `node sim/test_docs_explorer.mjs` (headless Chrome) on a build host: it
opens the explorer and clicks the links it pins.

## Branches and PRs

- Every change is a `feat/*` branch in its own git worktree, with one PR into `dev`; nobody commits
  to `dev` or `main`. Promotion and releases: [`RELEASING.md`](RELEASING.md).
- The hard rules (clean room, secrets, merge only green) and the integration rules learned from real
  failures: [agent workflow](docs/architecture/agent-workflow.md).
- The merge gate is `scripts/pr-green.sh <pr>`: every check complete and passing, read in the same
  command that merges.
- Merge `origin/dev` into the branch and re-run the guards before opening the PR, and again before
  handing off.

## Hot files

Never edit a doc an open PR changes: two writers on one page lose work, and the second merge
rewrites the first. A file is hot while it is in any open PR's diff. List the hot set before you
start:

```sh
git fetch origin
gh pr list --state open --limit 200 --json headRefName --jq '.[].headRefName' |
  while read -r b; do git diff --name-only "$(git merge-base origin/dev "origin/$b")" "origin/$b"; done | sort -u
```

Fetch first, so every branch is current. `gh pr list` stops at 30 PRs without `--limit`, and a PR
it leaves out hides its files. Leave a hot file as it is, and say so in the PR body under
"Deferred (hot)".

---
[Project README](README.md) · [Releasing](RELEASING.md) · [Agent workflow](docs/architecture/agent-workflow.md) · [Docs index](docs/README.md)
