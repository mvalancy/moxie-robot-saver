#!/usr/bin/env python3
"""Check that a squash promotion was reconciled back into ``dev``.

Promotion PRs are opened only for owner-approved major milestones. There is deliberately
no permanently open ``dev -> main`` PR: it ran the full deep tier after every dev push and
turned routine integration into release-like churn. The one post-squash invariant that
remains is ancestry: ``dev`` must contain the new ``main`` tip before more work branches
from it.

The checker forgives the first 30 minutes after a squash. Across the first ten measured
promotions, reconciliation took 11s--990s (median 18s); the grace is 1.8x the maximum.

Exit codes: 0 = reconciled or inside the grace window; 1 = stale unreconciled promotion;
2 = the state could not be measured. The last state is never reported as green.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
DEFAULT_GRACE = 1800


class CannotMeasure(Exception):
    """The instrument failed. Exit 2, never 0."""


def _run(argv: list[str], cwd: str) -> str:
    try:
        done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise CannotMeasure(f"{argv[0]!r} is not on PATH")
    except subprocess.TimeoutExpired:
        raise CannotMeasure(f"{' '.join(argv)} timed out after 120s")
    if done.returncode != 0:
        raise CannotMeasure(
            f"`{' '.join(argv)}` exited {done.returncode}\n"
            f"  stdout: {done.stdout.strip()[:400]}\n"
            f"  stderr: {done.stderr.strip()[:400]}")
    return done.stdout.strip()


def commits_behind(repo: str, remote: str, head: str, base: str) -> int:
    """How many commits ``remote/base`` has that ``remote/head`` does not."""
    out = _run(["git", "rev-list", "--count",
                f"{remote}/{head}..{remote}/{base}"], repo)
    try:
        return int(out)
    except ValueError:
        raise CannotMeasure(f"git rev-list --count printed {out!r}, not a number")


def base_tip(repo: str, remote: str, base: str) -> tuple[str, int]:
    """Return the short SHA and committer epoch of the promoted-to branch tip."""
    out = _run(["git", "log", "-1", "--format=%h %ct", f"{remote}/{base}"], repo)
    parts = out.split()
    if len(parts) != 2 or not parts[1].isdigit():
        raise CannotMeasure(f"git log printed {out!r}, not `<sha> <epoch>`")
    return parts[0], int(parts[1])


def _ago(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    if seconds < 172800:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"


def failure_message(*, remote: str, head: str, base: str, behind: int,
                    sha: str, age: int, grace: int) -> str:
    """Actionable output for an operator encountering the alarm without context."""
    return "\n".join([
        "",
        "=" * 78,
        "PROMOTION NOT FINISHED — dev does not contain the latest main squash.",
        "=" * 78,
        "",
        f"{remote}/{base} tip {sha} is {_ago(age)} old, past the {_ago(grace)} grace.",
        f"{head} is {behind} commit(s) behind {base}:",
        f"  git rev-list --count {remote}/{head}..{remote}/{base}  ->  {behind}",
        "",
        "Reconcile it in a clean throwaway worktree:",
        "",
        f"  git fetch {remote}",
        f"  git worktree add ../wt-reconcile -b chore/reconcile {remote}/{head}",
        "  cd ../wt-reconcile",
        "  PRE=$(git rev-parse HEAD)",
        f"  git merge {remote}/{base} -X ours --no-edit",
        "  git diff $PRE..HEAD --stat        # MUST PRINT NOTHING",
        f"  git push {remote} HEAD:{head}",
        "",
        "If the diff is not empty, stop: `-X ours` swallowed a real change.",
        "Do not recreate a standing PR. Open the next dev -> main PR only for an",
        "owner-approved major milestone. See RELEASING.md.",
        "",
        "=" * 78,
        "",
    ])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Redden when dev was not reconciled after a main squash promotion.")
    ap.add_argument("--repo-dir", default=REPO)
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--base", default="main")
    ap.add_argument("--head", default="dev")
    ap.add_argument("--grace-seconds", type=int, default=DEFAULT_GRACE)
    ap.add_argument("--now", type=int, default=None)
    ap.add_argument("--fetch", dest="fetch", action="store_true", default=True)
    ap.add_argument("--no-fetch", dest="fetch", action="store_false")
    args = ap.parse_args(argv)
    now = args.now if args.now is not None else int(time.time())

    try:
        if args.fetch:
            # Explicit refspecs matter under actions/checkout's narrow fetch config.
            _run(["git", "fetch", "--quiet", args.remote,
                  f"+refs/heads/{args.base}:refs/remotes/{args.remote}/{args.base}",
                  f"+refs/heads/{args.head}:refs/remotes/{args.remote}/{args.head}"],
                 args.repo_dir)
        behind = commits_behind(args.repo_dir, args.remote, args.head, args.base)
        sha, tip_ct = base_tip(args.repo_dir, args.remote, args.base)
    except CannotMeasure as exc:
        print(f"CANNOT MEASURE the promotion state: {exc}", file=sys.stderr)
        print("Exiting 2, not 0 — a broken monitor is not an all-clear.", file=sys.stderr)
        return 2

    if behind == 0:
        print(f"promotion finished: {args.remote}/{args.head} is level with or ahead of "
              f"{args.remote}/{args.base} (behind=0). No standing PR is required.")
        return 0

    age = now - tip_ct
    if age < args.grace_seconds:
        print(f"within the post-squash window: {args.remote}/{args.base} tip {sha} is only "
              f"{_ago(age)} old (grace {_ago(args.grace_seconds)}), behind={behind}. "
              "Not reddening yet.")
        return 0

    print(failure_message(remote=args.remote, head=args.head, base=args.base,
                          behind=behind, sha=sha, age=age,
                          grace=args.grace_seconds))
    return 1


if __name__ == "__main__":
    sys.exit(main())
