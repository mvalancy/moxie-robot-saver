#!/usr/bin/env python3
"""Guard: the committed docs bundle must match a fresh build of docs/.

`build_docs_bundle.py` is deterministic — sorted inputs, no clock, no commit hash — so
rebuilding an in-sync bundle produces byte-identical output. (Determinism used to be
credited to a `generated` content-hash stamp in `docs-index.json`. It never came from
that stamp, and the stamp is gone: it was the one line both sides of every merge
rewrote, which made every pair of doc-touching branches conflict. Nothing read it.)
This rebuilds and checks `git` reports no change to the bundle paths — catching the easy mistake of
editing a doc (or adding one) without re-running the bundler before committing.

    python3 sim/tools/check_bundle_fresh.py

Exit 0 = bundle is fresh (or git unavailable → skip). Exit 1 = bundle is stale.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
PATHS = ["sim/web/docs-bundle", "sim/web/docs-index.json", "sim/web/docs-search.json"]


def git(*args):
    return subprocess.run(["git", "-C", REPO, *args],
                          capture_output=True, text=True)


def main():
    # Skip cleanly if this isn't a git checkout (nothing to diff against).
    if git("rev-parse", "--is-inside-work-tree").returncode != 0:
        print("ℹ️  bundle-freshness check skipped — not a git work tree")
        return 0

    build = subprocess.run([sys.executable, os.path.join(HERE, "build_docs_bundle.py")],
                           capture_output=True, text=True)
    if build.returncode != 0:
        print("❌ bundle-freshness check FAILED — the bundler itself errored:")
        print(build.stderr or build.stdout)
        return 1

    diff = git("status", "--porcelain", "--", *PATHS)
    changed = [ln for ln in diff.stdout.splitlines() if ln.strip()]
    if changed:
        print("❌ docs bundle is STALE — docs/ changed but the bundle wasn't rebuilt.")
        print("   Run:  python3 sim/tools/build_docs_bundle.py   and commit the result.")
        print("   Out-of-sync paths:")
        for ln in changed:
            print("     ", ln)
        return 1

    print("✅ docs bundle is fresh — committed bundle matches a rebuild of docs/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
