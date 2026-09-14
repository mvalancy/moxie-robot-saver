"""Executable tests for the post-promotion ancestry guard.

Every verdict is exercised against a real temporary git graph. Promotion PR presence is
deliberately absent from the model: PRs are opened only for owner-approved major milestones.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(REPO, "sim", "tools", "check_promotion_state.py")
TEMPLATE = os.path.join(REPO, "sim", "ci", "promotion.yml")
INSTALLED = os.path.join(REPO, ".github", "workflows", "promotion.yml")
NOW = 1_788_800_000
GRACE = 1800
IDENT = ["-c", "user.name=guard", "-c", "user.email=guard@example.invalid",
         "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main"]


def _git(cwd: str, *args: str, when: int | None = None) -> str:
    env = dict(os.environ)
    if when is not None:
        stamp = f"@{when} +0000"
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = stamp
    done = subprocess.run(["git", *IDENT, *args], cwd=cwd, env=env,
                          capture_output=True, text=True)
    assert done.returncode == 0, f"git {' '.join(args)} failed:\n{done.stderr}"
    return done.stdout.strip()


def _make_repo(tmp_path, *, behind: bool, squash_at: int) -> str:
    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    origin.mkdir()
    work.mkdir()
    _git(str(origin), "init", "--bare", "-q", ".")
    _git(str(work), "init", "-q", ".")
    _git(str(work), "remote", "add", "origin", str(origin))
    (work / "README.md").write_text("base\n")
    _git(str(work), "add", "-A")
    _git(str(work), "commit", "-qm", "base", when=squash_at - 100_000)
    _git(str(work), "branch", "-M", "main")
    _git(str(work), "checkout", "-q", "-b", "dev")
    (work / "feature.txt").write_text("work on dev\n")
    _git(str(work), "add", "-A")
    _git(str(work), "commit", "-qm", "feat: a slice", when=squash_at - 50_000)
    _git(str(work), "checkout", "-q", "main")
    (work / "feature.txt").write_text("work on dev\n")
    _git(str(work), "add", "-A")
    _git(str(work), "commit", "-qm", "dev -> main", when=squash_at)
    if not behind:
        _git(str(work), "checkout", "-q", "dev")
        _git(str(work), "merge", "main", "-X", "ours", "--no-edit", "-q",
             when=squash_at + 20)
    _git(str(work), "push", "-q", "origin", "main", "dev")
    _git(str(work), "fetch", "-q", "origin")
    return str(work)


def _run(repo: str, *, now: int = NOW, grace: int = GRACE,
         script: str = SCRIPT, fetch: bool = False) -> subprocess.CompletedProcess:
    args = [sys.executable, script, "--repo-dir", repo,
            "--now", str(now), "--grace-seconds", str(grace)]
    args.append("--fetch" if fetch else "--no-fetch")
    return subprocess.run(args, capture_output=True, text=True)


def test_stale_unreconciled_promotion_reddens(tmp_path):
    repo = _make_repo(tmp_path, behind=True, squash_at=NOW - 7200)
    got = _run(repo)
    assert got.returncode == 1, got.stdout
    assert "PROMOTION NOT FINISHED" in got.stdout
    assert "dev is 1 commit(s) behind main" in got.stdout


def test_reconciled_repo_needs_no_standing_pr(tmp_path):
    repo = _make_repo(tmp_path, behind=False, squash_at=NOW - 7200)
    got = _run(repo)
    assert got.returncode == 0, got.stdout
    assert "promotion finished" in got.stdout
    assert "No standing PR is required" in got.stdout


def test_post_squash_window_is_quiet(tmp_path):
    repo = _make_repo(tmp_path, behind=True, squash_at=NOW - 60)
    got = _run(repo)
    assert got.returncode == 0, got.stdout
    assert "within the post-squash window" in got.stdout


@pytest.mark.parametrize("age,expected", [(GRACE - 1, 0), (GRACE + 1, 1)])
def test_grace_window_is_bounded(tmp_path, age, expected):
    repo = _make_repo(tmp_path, behind=True, squash_at=NOW - age)
    assert _run(repo).returncode == expected


def test_fetch_repairs_a_missing_remote_ref(tmp_path):
    repo = _make_repo(tmp_path, behind=True, squash_at=NOW - 7200)
    _git(repo, "update-ref", "-d", "refs/remotes/origin/dev")
    assert _run(repo).returncode == 2
    healed = _run(repo, fetch=True)
    assert healed.returncode == 1, healed.stdout


def test_missing_branch_is_measurement_failure(tmp_path):
    repo = _make_repo(tmp_path, behind=True, squash_at=NOW - 7200)
    got = subprocess.run(
        [sys.executable, SCRIPT, "--repo-dir", repo, "--no-fetch", "--head", "missing",
         "--now", str(NOW)], capture_output=True, text=True)
    assert got.returncode == 2
    assert "CANNOT MEASURE" in got.stderr


def _mutant(tmp_path, name: str, old: str, new: str) -> str:
    src = open(SCRIPT).read()
    assert src.count(old) == 1, f"mutation anchor {old!r} is not unique"
    path = tmp_path / f"mutant_{name}.py"
    path.write_text(src.replace(old, new))
    return str(path)


def test_age_gate_is_load_bearing(tmp_path):
    repo = _make_repo(tmp_path, behind=True, squash_at=NOW - 60)
    mutant = _mutant(tmp_path, "no_grace",
                     "if age < args.grace_seconds:", "if False:")
    assert _run(repo, script=mutant).returncode == 1


def test_behind_count_is_load_bearing(tmp_path):
    repo = _make_repo(tmp_path, behind=True, squash_at=NOW - 7200)
    mutant = _mutant(
        tmp_path, "never_behind",
        'out = _run(["git", "rev-list", "--count",',
        'return 0  # MUTANT\n    out = _run(["git", "rev-list", "--count",')
    assert _run(repo, script=mutant).returncode == 0


def test_failure_message_carries_the_safe_reconcile(tmp_path):
    repo = _make_repo(tmp_path, behind=True, squash_at=NOW - 7200)
    message = _run(repo).stdout
    assert "git merge origin/main -X ours --no-edit" in message
    assert "git diff $PRE..HEAD --stat" in message
    assert "MUST PRINT NOTHING" in message
    assert "Do not recreate a standing PR" in message
    assert "owner-approved major milestone" in message


def test_installed_monitor_matches_its_template():
    assert open(TEMPLATE, "rb").read() == open(INSTALLED, "rb").read()


def test_monitor_is_read_only_and_invokes_the_guard():
    text = open(TEMPLATE).read()
    assert "contents: read" in text
    assert "pull-requests:" not in text
    assert "fetch-depth: 0" in text
    assert "python3 sim/tools/check_promotion_state.py" in text
    run_body = text.split("run: |", 1)[1]
    assert "${{ inputs." not in run_body
