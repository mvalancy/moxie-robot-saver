#!/usr/bin/env python3
"""
Is the last `dev → main` promotion FINISHED?

THE DEFECT THIS EXISTS FOR, and why it is a script rather than a fifth paragraph of prose.
Squash-merging the standing `dev → main` PR leaves two things undone that `gh pr merge`
will not do for you:

  1. `dev` ends **one commit behind `main`** — the squash is a new commit `dev` has never
     seen, so the next promotion PR opens CONFLICTING rather than empty.
  2. **The standing PR is gone** — merging closes it and nothing re-opens it, so nothing
     tracks the `dev`/`main` relationship until a human notices.

Neither is visible from the merge output and nothing goes red. Measured on 2026-09-06: it
was missed after five of the last seven promotions (#174, #177, #190, #191, #197) by three
different actors — *after* it was already written down in four places (playbook rule 29 in
`docs/architecture/orchestration-plan.md`, `RELEASING.md`'s "After a promotion (squash) —
reconcile `dev`", the standing PR's own body, and the status log). Prose is not the fix, so
this is the mechanical one. It adds no fifth explanation: when it reddens it points at
`RELEASING.md`, which already carries the corrected order.

────────────────────────────────────────────────────────────────────────────────────────
THE HARD PART IS NOT DETECTION, IT IS THE TRANSIENT.

Between the squash and the reconcile there is a legitimate window where `dev` really is
behind `main` and the standing PR really is gone. A check that fires there is worse than no
check — it teaches people that this alarm means nothing. (This repo has already thrown away
a guard for exactly that reason.)

So the two conditions are gated on ONE CLOCK: the committer date of `main`'s tip, which for
a squash promotion *is* the moment both defects began. Inside `--grace-seconds` of it the
state is the normal post-promotion transient and this exits 0, saying so. Outside it, the
same state is an unfinished promotion and this exits 1.

THE BOUND, MEASURED rather than guessed. For the ten promotions in the repo's history at the
time of writing, the interval between the squash commit on `main` and the reconcile merge
commit on `dev` was:

    #197  96s   #194  21s   #191 132s   #190 990s   #186 11s
    #177  11s   #174  12s   #171 731s   #165 274s   #155 17s

Median 18s, maximum **990s (16m 30s)**. The default grace is **1800s (30 min)** — 1.8× the
worst observed reconcile, and ~100× the median. Nothing legitimate has ever taken that long,
and a promotion that genuinely needs longer than half an hour is one a human should be
looking at anyway.

The grace is a BOUND, not an exemption: `sim/tests/test_promotion_guard.py` asserts both
sides of it — silent at grace−1, red at grace+1 — over the full truth table, and carries a
negative control in which the age gate is removed and the in-window cases must go red.

────────────────────────────────────────────────────────────────────────────────────────
USAGE

    python3 sim/tools/check_promotion_state.py            # measure origin, real `gh`
    python3 sim/tools/check_promotion_state.py --no-fetch  # trust the refs already here

Exit codes — three, deliberately:

    0   finished, or inside the legitimate post-squash window
    1   UNFINISHED — one or both trailing steps were not done
    2   COULD NOT MEASURE (no `gh`, not authenticated, missing ref …)

2 is separate from 0 on purpose: a monitor that returns "all clear" when its instrument is
broken is the failure mode this repo spent a day deleting. If it cannot measure, it says so
and goes red.

Stdlib only, and it never writes to the working tree — `git fetch` moves remote-tracking
refs and nothing else.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))

DEFAULT_GRACE = 1800  # seconds; see the header for the measurement behind this number


class CannotMeasure(Exception):
    """The instrument failed. Exit 2, never 0."""


# --------------------------------------------------------------------------- #
# Probes
# --------------------------------------------------------------------------- #
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
    """How many commits `<remote>/<base>` has that `<remote>/<head>` does not."""
    out = _run(["git", "rev-list", "--count",
                f"{remote}/{head}..{remote}/{base}"], repo)
    try:
        return int(out)
    except ValueError:
        raise CannotMeasure(f"git rev-list --count printed {out!r}, not a number")


def base_tip(repo: str, remote: str, base: str) -> tuple[str, int]:
    """`(short sha, committer epoch)` of `<remote>/<base>`'s tip — the promotion clock."""
    out = _run(["git", "log", "-1", "--format=%h %ct", f"{remote}/{base}"], repo)
    parts = out.split()
    if len(parts) != 2 or not parts[1].isdigit():
        raise CannotMeasure(f"git log printed {out!r}, not `<sha> <epoch>`")
    return parts[0], int(parts[1])


def open_prs_to(repo: str, gh: str, base: str) -> list[dict]:
    """Every open PR whose base is `<base>`, as `[{number, headRefName}, …]`.

    Needs repo READ access only. In CI that is the workflow's own `GITHUB_TOKEN` with
    `pull-requests: read` — no PAT, no new secret, and nothing this can write to.
    """
    out = _run([gh, "pr", "list", "--base", base, "--state", "open",
                "--limit", "100", "--json", "number,headRefName"], repo)
    try:
        prs = json.loads(out or "[]")
    except json.JSONDecodeError as exc:
        raise CannotMeasure(f"`gh pr list` did not print JSON ({exc}): {out[:400]!r}")
    if not isinstance(prs, list):
        raise CannotMeasure(f"`gh pr list` printed {type(prs).__name__}, expected a list")
    return prs


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #
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
                    standing: dict | None, prs: list[dict], sha: str,
                    age: int, grace: int) -> str:
    """The product. Someone reads this at 2am with no context; it has to be enough."""
    def mark(bad: bool) -> str:
        return "[X]" if bad else "[ok]"

    others = ", ".join(f"#{p['number']} ({p['headRefName']})" for p in prs) or "none"
    lines = [
        "",
        "=" * 78,
        "PROMOTION NOT FINISHED — the two steps `gh pr merge` does not do for you.",
        "=" * 78,
        "",
        f"{remote}/{base} tip {sha} is {_ago(age)} old, past the {_ago(grace)} "
        f"post-squash grace,",
        "so this is not the normal window between a squash and its reconcile:",
        "",
        f"  {mark(behind > 0)} {head} is {behind} commit(s) behind {base}",
        f"        git rev-list --count {remote}/{head}..{remote}/{base}  ->  {behind}",
        f"  {mark(standing is None)} standing PR ({head} -> {base}): "
        + (f"ABSENT   [open PRs to {base}: {others}]" if standing is None
           else f"#{standing['number']}"),
        "",
        "-" * 78,
        "FIX, IN THIS ORDER — reconcile FIRST, then recreate.",
        "The order is not cosmetic: recreating first opens the standing PR CONFLICTING,",
        "and that window is avoidable for free. The full wording, and the reason, are in",
        "  RELEASING.md  ->  \"After a promotion (squash) — reconcile `dev`\"",
        "  docs/architecture/orchestration-plan.md  ->  playbook rule 29 (why it is a rule)",
        "-" * 78,
        "",
        f"  1. Reconcile {head}. Do it in a THROWAWAY worktree, never the shared checkout —",
        "     on 2026-09-06 the main checkout held another session's uncommitted files.",
        "",
        f"       git fetch {remote}",
        f"       git worktree add ../wt-reconcile -b chore/reconcile {remote}/{head}",
        "       cd ../wt-reconcile",
        "       PRE=$(git rev-parse HEAD)",
        f"       git merge {remote}/{base} -X ours --no-edit",
        "       git diff $PRE..HEAD --stat        # MUST PRINT NOTHING.",
        "                                         # If it prints anything, the `-X ours`",
        "                                         # swallowed a real change: the promotion",
        "                                         # needs unpicking, not pushing.",
        f"       git push {remote} HEAD:{head}",
        "",
        "  2. Recreate the standing PR:",
        "",
        f"       gh pr create --base {base} --head {head}",
        "",
        "  3. Re-run this check — it should print `promotion finished`:",
        "",
        "       python3 sim/tools/check_promotion_state.py",
        "",
        "=" * 78,
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Redden when a dev → main promotion was left half-finished.")
    ap.add_argument("--repo-dir", default=REPO,
                    help="git checkout to measure (default: this repo)")
    ap.add_argument("--remote", default="origin")
    ap.add_argument("--base", default="main", help="the promoted-to branch")
    ap.add_argument("--head", default="dev", help="the standing PR's head branch")
    ap.add_argument("--grace-seconds", type=int, default=DEFAULT_GRACE,
                    help=f"post-squash window to forgive (default {DEFAULT_GRACE}); "
                         "see this file's header for the measurement behind it")
    ap.add_argument("--gh", default=os.environ.get("MOXIE_GH_BIN", "gh"),
                    help="the GitHub CLI to ask about open PRs")
    ap.add_argument("--now", type=int, default=None,
                    help="epoch seconds to treat as now (tests pin the clock)")
    ap.add_argument("--fetch", dest="fetch", action="store_true", default=True,
                    help="update remote-tracking refs first (default)")
    ap.add_argument("--no-fetch", dest="fetch", action="store_false",
                    help="trust the refs already in the checkout (CI does its own fetch)")
    args = ap.parse_args(argv)

    now = args.now if args.now is not None else int(time.time())

    try:
        if args.fetch:
            # EXPLICIT REFSPECS, not `git fetch origin main dev`. The bare form relies on
            # `remote.origin.fetch` being the wildcard to update remote-tracking refs at
            # all; `actions/checkout` writes a NARROW refspec unless `fetch-depth: 0`, and
            # under that config the bare form updates FETCH_HEAD and leaves `origin/dev`
            # stale — so the count below would answer about yesterday's graph, quietly.
            _run(["git", "fetch", "--quiet", args.remote,
                  f"+refs/heads/{args.base}:refs/remotes/{args.remote}/{args.base}",
                  f"+refs/heads/{args.head}:refs/remotes/{args.remote}/{args.head}"],
                 args.repo_dir)
        behind = commits_behind(args.repo_dir, args.remote, args.head, args.base)
        sha, tip_ct = base_tip(args.repo_dir, args.remote, args.base)
        if not shutil.which(args.gh) and not os.path.isfile(args.gh):
            raise CannotMeasure(
                f"{args.gh!r} is not installed. This check needs repo READ access to ask "
                "which PRs are open; in CI that is the workflow's own GITHUB_TOKEN with "
                "`pull-requests: read`. Locally: install the GitHub CLI and `gh auth login`.")
        prs = open_prs_to(args.repo_dir, args.gh, args.base)
    except CannotMeasure as exc:
        print(f"CANNOT MEASURE the promotion state: {exc}", file=sys.stderr)
        print("Exiting 2, not 0 — a monitor that says `all clear` when its instrument is "
              "broken is worse than no monitor.", file=sys.stderr)
        return 2

    standing = next((p for p in prs if p.get("headRefName") == args.head), None)
    age = now - tip_ct
    unfinished = behind > 0 or standing is None

    if not unfinished:
        print(f"promotion finished: {args.remote}/{args.head} is level with or ahead of "
              f"{args.remote}/{args.base} (behind={behind}), and the standing PR "
              f"#{standing['number']} ({args.head} -> {args.base}) is open.")
        return 0

    if age < args.grace_seconds:
        print(f"within the post-squash window: {args.remote}/{args.base} tip {sha} is only "
              f"{_ago(age)} old (grace {_ago(args.grace_seconds)}), so "
              f"behind={behind} / standing PR "
              f"{'open' if standing else 'absent'} is the normal transient. "
              "Not reddening — but it will after the grace expires.")
        return 0

    print(failure_message(remote=args.remote, head=args.head, base=args.base,
                          behind=behind, standing=standing, prs=prs, sha=sha,
                          age=age, grace=args.grace_seconds))
    return 1


if __name__ == "__main__":
    sys.exit(main())
