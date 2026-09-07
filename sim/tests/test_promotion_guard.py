"""
Teeth for `sim/tools/check_promotion_state.py` — the post-promotion guard.

WHY THIS FILE IS AS LONG AS IT IS. The guard's whole value is that it reddens when a
`dev → main` promotion was left half-finished and stays silent otherwise, and this repo has
already shipped checks that could not fail (the puppeteer suites that printed "skipped" and
kept the badge green, PR #120). So the guard is not asserted by reading it: every case below
builds a REAL git repository — a bare `origin`, a `main` carrying a squash commit with a
pinned committer date, a `dev` that either has or has not merged it — and runs the real
script against it with a stubbed `gh`. `git rev-list --count` does the counting, not a mock.

Three things are proved here, and the third is the one that matters:

  1. It REDDENS on the constructed defect — `dev` behind `main`, no standing PR — and on
     each half of it alone.
  2. It is SILENT on a healthy repo, and silent inside the legitimate post-squash window.
  3. Every clause is LOAD-BEARING. Three mutants delete one measurement each (the age gate,
     the behind count, the standing-PR lookup) and the row that clause was holding must
     flip. Without these the truth table below could be satisfied by a script that hardcoded
     its answers.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(REPO, "sim", "tools", "check_promotion_state.py")
TEMPLATE = os.path.join(REPO, "sim", "ci", "promotion.yml")
INSTALLED = os.path.join(REPO, ".github", "workflows", "promotion.yml")

#: A fixed clock. Nothing here reads the wall clock, so a slow runner cannot change a verdict.
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


def _fake_gh(tmp_path, prs: list[dict], *, broken: bool = False) -> str:
    """A `gh` that answers `pr list --json …` with `prs` — or fails, for the
    cannot-measure case. Executable, on disk, invoked by the real subprocess call."""
    path = tmp_path / "fake-gh"
    if broken:
        body = ('#!/bin/sh\n'
                'echo "gh: To get started with GitHub CLI, please run: gh auth login" >&2\n'
                'exit 4\n')
    else:
        body = f"#!/bin/sh\ncat <<'JSON'\n{json.dumps(prs)}\nJSON\n"
    path.write_text(body)
    path.chmod(0o755)
    return str(path)


def _make_repo(tmp_path, *, behind: bool, squash_at: int) -> str:
    """A repo in a promotion state.

    `main` gets a squash commit dated `squash_at`. `dev` either has merged it (level) or
    has not (`behind`, exactly the state a `gh pr merge` leaves behind).
    """
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

    # The promotion: a squash commit on `main` sharing no ancestry with dev's history.
    _git(str(work), "checkout", "-q", "main")
    (work / "feature.txt").write_text("work on dev\n")
    _git(str(work), "add", "-A")
    _git(str(work), "commit", "-qm", "dev -> main (rolling) (#199)", when=squash_at)

    if not behind:
        _git(str(work), "checkout", "-q", "dev")
        _git(str(work), "merge", "main", "-X", "ours", "--no-edit", "-q",
             when=squash_at + 20)

    _git(str(work), "push", "-q", "origin", "main", "dev")
    _git(str(work), "fetch", "-q", "origin")
    return str(work)


def _run(repo: str, gh: str, *, now: int = NOW, grace: int = GRACE,
         script: str = SCRIPT) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, script, "--repo-dir", repo, "--gh", gh, "--no-fetch",
         "--now", str(now), "--grace-seconds", str(grace)],
        capture_output=True, text=True)


STANDING = [{"number": 200, "headRefName": "dev"}]
NO_PR: list[dict] = []


# --------------------------------------------------------------------------- #
# 1. The constructed defect reddens
# --------------------------------------------------------------------------- #
def test_it_reddens_on_the_constructed_defect(tmp_path):
    """The exact state `gh pr merge` leaves: `dev` one commit behind `main`, and no open
    PR targeting `main`. The squash is two hours old, well past the grace."""
    repo = _make_repo(tmp_path, behind=True, squash_at=NOW - 7200)
    got = _run(repo, _fake_gh(tmp_path, NO_PR))
    assert got.returncode == 1, f"the guard did not redden:\n{got.stdout}\n{got.stderr}"
    assert "PROMOTION NOT FINISHED" in got.stdout
    assert "dev is 1 commit(s) behind main" in got.stdout
    assert "standing PR (dev -> main): ABSENT" in got.stdout


def test_it_reddens_when_only_dev_is_behind(tmp_path):
    """Half the defect is still the defect: the standing PR was recreated, but nobody
    reconciled — so the PR reads CONFLICTING and the next promotion inherits it."""
    repo = _make_repo(tmp_path, behind=True, squash_at=NOW - 7200)
    got = _run(repo, _fake_gh(tmp_path, STANDING))
    assert got.returncode == 1, got.stdout
    assert "[X] dev is 1 commit(s) behind main" in got.stdout
    assert "[ok] standing PR (dev -> main): #200" in got.stdout  # this half is fine


def test_it_reddens_when_only_the_standing_pr_is_missing(tmp_path):
    """The other half: reconciled, but nothing tracks `dev` against `main` any more, so
    "is dev green?" has no answer to read."""
    repo = _make_repo(tmp_path, behind=False, squash_at=NOW - 7200)
    got = _run(repo, _fake_gh(tmp_path, NO_PR))
    assert got.returncode == 1, got.stdout
    assert "[ok] dev is 0 commit(s) behind main" in got.stdout
    assert "[X] standing PR (dev -> main): ABSENT" in got.stdout


def test_an_unrelated_pr_to_main_does_not_stand_in_for_the_standing_one(tmp_path):
    """`gh pr list --base main` being non-empty is NOT the same claim as "the standing PR
    exists". A hotfix `feat/* → main` PR would satisfy the looser test while the thing that
    tracks the `dev`/`main` relationship is still gone."""
    repo = _make_repo(tmp_path, behind=False, squash_at=NOW - 7200)
    other = [{"number": 201, "headRefName": "hotfix/cert-expiry"}]
    got = _run(repo, _fake_gh(tmp_path, other))
    assert got.returncode == 1, got.stdout
    assert "hotfix/cert-expiry" in got.stdout, "the message should name what IS open"


# --------------------------------------------------------------------------- #
# 2. Silent when it should be
# --------------------------------------------------------------------------- #
def test_it_is_silent_on_a_healthy_repo(tmp_path):
    """Reconciled and tracked: `dev` level with `main`, standing PR open. Nothing to say."""
    repo = _make_repo(tmp_path, behind=False, squash_at=NOW - 7200)
    got = _run(repo, _fake_gh(tmp_path, STANDING))
    assert got.returncode == 0, f"the guard reddened a healthy repo:\n{got.stdout}"
    assert "promotion finished" in got.stdout


def test_it_is_silent_inside_the_post_squash_window(tmp_path):
    """THE TRAP. Between the squash and the reconcile the defect state is legitimate. A
    guard that fires here trains people to ignore it, which is worse than no guard."""
    repo = _make_repo(tmp_path, behind=True, squash_at=NOW - 60)
    got = _run(repo, _fake_gh(tmp_path, NO_PR))
    assert got.returncode == 0, f"reddened one minute after a squash:\n{got.stdout}"
    assert "within the post-squash window" in got.stdout


@pytest.mark.parametrize("age,expected", [
    (GRACE - 1, 0),     # the last silent second
    (GRACE + 1, 1),     # the first red one
])
def test_the_window_is_a_bound_and_not_an_exemption(tmp_path, age, expected):
    """Both sides of the edge, one second apart. Forgiving the transient is only honest if
    the forgiveness actually ends."""
    repo = _make_repo(tmp_path, behind=True, squash_at=NOW - age)
    got = _run(repo, _fake_gh(tmp_path, NO_PR))
    assert got.returncode == expected, f"age={age}s gave {got.returncode}:\n{got.stdout}"


@pytest.mark.parametrize("behind", [True, False])
@pytest.mark.parametrize("prs", [NO_PR, STANDING], ids=["no-pr", "standing-pr"])
@pytest.mark.parametrize("age", [60, 7200], ids=["in-window", "stale"])
def test_the_whole_truth_table(tmp_path, behind, prs, age):
    """All eight combinations, spelled out rather than reasoned about. Healthy is always
    green; anything else is green inside the window and red outside it."""
    healthy = (not behind) and prs == STANDING
    expected = 0 if healthy or age < GRACE else 1
    repo = _make_repo(tmp_path, behind=behind, squash_at=NOW - age)
    got = _run(repo, _fake_gh(tmp_path, prs))
    assert got.returncode == expected, (
        f"behind={behind} prs={prs} age={age}s expected {expected}, got "
        f"{got.returncode}:\n{got.stdout}\n{got.stderr}")


def test_the_fetch_path_repairs_a_stale_remote_tracking_ref(tmp_path):
    """The default `--fetch` must actually refresh `origin/<base>` and `origin/<head>`.
    A bare `git fetch origin main dev` does NOT, under the narrow `remote.origin.fetch`
    that `actions/checkout` writes without `fetch-depth: 0` — it moves FETCH_HEAD and
    leaves the remote-tracking ref stale, so the count would answer about an older graph
    with exit code 0. This deletes the ref and requires the run to recover it."""
    repo = _make_repo(tmp_path, behind=True, squash_at=NOW - 7200)
    _git(repo, "update-ref", "-d", "refs/remotes/origin/dev")
    stale = subprocess.run(
        [sys.executable, SCRIPT, "--repo-dir", repo, "--gh", _fake_gh(tmp_path, NO_PR),
         "--no-fetch", "--now", str(NOW)], capture_output=True, text=True)
    assert stale.returncode == 2, "a missing ref must not read as a verdict"

    healed = subprocess.run(
        [sys.executable, SCRIPT, "--repo-dir", repo, "--gh", _fake_gh(tmp_path, NO_PR),
         "--now", str(NOW)], capture_output=True, text=True)
    assert healed.returncode == 1, (
        f"the fetch did not restore origin/dev:\n{healed.stdout}\n{healed.stderr}")
    assert "dev is 1 commit(s) behind main" in healed.stdout


# --------------------------------------------------------------------------- #
# 3. A broken instrument is not a green light
# --------------------------------------------------------------------------- #
def test_an_unauthenticated_gh_exits_2_and_not_0(tmp_path):
    """The failure this repo keeps re-learning: a check that returns "all clear" when it
    could not measure. Exit 2 is a third state on purpose."""
    repo = _make_repo(tmp_path, behind=False, squash_at=NOW - 7200)
    got = _run(repo, _fake_gh(tmp_path, [], broken=True))
    assert got.returncode == 2, f"a broken gh gave {got.returncode}:\n{got.stdout}"
    assert "CANNOT MEASURE" in got.stderr


def test_a_missing_gh_exits_2_and_names_the_permission_it_needs(tmp_path):
    repo = _make_repo(tmp_path, behind=False, squash_at=NOW - 7200)
    got = _run(repo, str(tmp_path / "there-is-no-gh-here"))
    assert got.returncode == 2, got.stdout
    assert "pull-requests: read" in got.stderr


def test_a_missing_branch_exits_2_and_not_0(tmp_path):
    """`origin/dev` gone (renamed, pruned) must not read as "nothing behind"."""
    repo = _make_repo(tmp_path, behind=True, squash_at=NOW - 7200)
    got = subprocess.run(
        [sys.executable, SCRIPT, "--repo-dir", repo, "--gh", _fake_gh(tmp_path, NO_PR),
         "--no-fetch", "--now", str(NOW), "--head", "no-such-branch"],
        capture_output=True, text=True)
    assert got.returncode == 2, got.stdout


# --------------------------------------------------------------------------- #
# 4. NEGATIVE CONTROLS — every clause is load-bearing
# --------------------------------------------------------------------------- #
def _mutant(tmp_path, name: str, old: str, new: str) -> str:
    src = open(SCRIPT).read()
    assert src.count(old) == 1, f"mutation anchor {old!r} is not unique in {SCRIPT}"
    path = tmp_path / f"mutant_{name}.py"
    path.write_text(src.replace(old, new))
    return str(path)


def test_without_the_age_gate_the_in_window_case_would_redden(tmp_path):
    """Proves the in-window greens above come from the CLOCK, not from the guard failing to
    see the defect. Delete the grace comparison and the same repo goes red."""
    repo = _make_repo(tmp_path, behind=True, squash_at=NOW - 60)
    healthy = _run(repo, _fake_gh(tmp_path, NO_PR))
    assert healthy.returncode == 0, healthy.stdout
    mutant = _mutant(tmp_path, "no_grace",
                     "if age < args.grace_seconds:", "if False:")
    got = _run(repo, _fake_gh(tmp_path, NO_PR), script=mutant)
    assert got.returncode == 1, (
        "removing the age gate did not change the verdict, so the grace window is not "
        f"what is holding this case green:\n{got.stdout}")


def test_without_the_behind_count_the_behind_only_case_would_pass(tmp_path):
    """Proves the `dev is behind` red comes from `git rev-list`, not from a constant."""
    repo = _make_repo(tmp_path, behind=True, squash_at=NOW - 7200)
    assert _run(repo, _fake_gh(tmp_path, STANDING)).returncode == 1
    mutant = _mutant(tmp_path, "never_behind",
                     'out = _run(["git", "rev-list", "--count",',
                     'return 0  # MUTANT\n    out = _run(["git", "rev-list", "--count",')
    got = _run(repo, _fake_gh(tmp_path, STANDING), script=mutant)
    assert got.returncode == 0, (
        "blinding the behind-count left the case red, so something other than the commit "
        f"graph was reddening it:\n{got.stdout}")


def test_without_the_standing_pr_lookup_the_missing_pr_case_would_pass(tmp_path):
    """Proves the missing-PR red comes from the `gh` answer, not from a constant."""
    repo = _make_repo(tmp_path, behind=False, squash_at=NOW - 7200)
    assert _run(repo, _fake_gh(tmp_path, NO_PR)).returncode == 1
    mutant = _mutant(
        tmp_path, "always_standing",
        'standing = next((p for p in prs if p.get("headRefName") == args.head), None)',
        'standing = {"number": 0}  # MUTANT')
    got = _run(repo, _fake_gh(tmp_path, NO_PR), script=mutant)
    assert got.returncode == 0, (
        "blinding the PR lookup left the case red, so the verdict was not coming from "
        f"`gh pr list`:\n{got.stdout}")


# --------------------------------------------------------------------------- #
# 5. THE FAILURE MESSAGE IS THE PRODUCT
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def red_message(tmp_path_factory) -> str:
    tmp_path = tmp_path_factory.mktemp("red")
    repo = _make_repo(tmp_path, behind=True, squash_at=NOW - 7200)
    got = _run(repo, _fake_gh(tmp_path, NO_PR))
    assert got.returncode == 1
    return got.stdout


def test_the_message_gives_both_commands_in_the_corrected_order(red_message):
    """Reconcile FIRST, then recreate. RELEASING.md was corrected to this order on
    2026-09-06 because recreating first opens the standing PR CONFLICTING; a message that
    reprints the old order would reintroduce the very window the correction closed."""
    reconcile = red_message.index("git merge origin/main -X ours --no-edit")
    recreate = red_message.index("gh pr create --base main --head dev")
    assert reconcile < recreate, (
        "the fix is printed recreate-first, which is the order RELEASING.md corrected "
        f"away from:\n{red_message}")


def test_the_message_carries_the_verification_that_makes_the_merge_safe(red_message):
    """`-X ours` can swallow a real change. RELEASING.md and rule 29 both require the diff
    check BEFORE the push, so the message must not hand someone a merge without it."""
    assert "git diff $PRE..HEAD --stat" in red_message
    assert "MUST PRINT NOTHING" in red_message
    push = red_message.index("git push origin HEAD:dev")
    assert red_message.index("git diff $PRE..HEAD --stat") < push


def test_the_message_points_at_the_docs_that_already_explain_it(red_message):
    """It deliberately does not become a fifth explanation. Four already exist; this names
    the operational one and the one carrying the rationale."""
    assert "RELEASING.md" in red_message
    assert "orchestration-plan.md" in red_message and "rule 29" in red_message


def test_the_message_says_what_it_measured_and_that_the_window_had_passed(red_message):
    """Someone at 2am needs to know this is not the transient before they act on it."""
    assert "git rev-list --count origin/dev..origin/main" in red_message
    assert "grace" in red_message and "2h 0m old" in red_message


# --------------------------------------------------------------------------- #
# 6. The script has a caller that needs nobody
# --------------------------------------------------------------------------- #
def test_the_workflow_actually_runs_the_checker():
    """A hermetic script with no caller is the failure mode this whole change exists to
    remove. `sim/tests/test_ci_workflows.py` holds the template and the installed copy
    byte-identical; this asserts the template does the thing."""
    yaml = pytest.importorskip("yaml")
    doc = yaml.safe_load(open(TEMPLATE))
    on = doc.get("on", doc.get(True)) or {}
    assert "schedule" in on, (
        "promotion.yml lost its schedule. A step in an existing tier cannot catch this: "
        "the missing reconcile PUSH is exactly the trigger such a step would wait for.")
    assert on["schedule"], on
    runs = "\n".join(s.get("run", "") for j in doc["jobs"].values()
                     for s in j.get("steps", []))
    assert "sim/tools/check_promotion_state.py" in runs, runs


def test_the_workflow_asks_for_read_access_only_and_uses_the_builtin_token():
    doc = yaml_or_skip()
    perms = doc.get("permissions") or {}
    assert perms == {"contents": "read", "pull-requests": "read"}, (
        f"promotion.yml's permissions changed to {perms}; it reads the commit graph and "
        "the open-PR list and must be able to do nothing else")
    text = open(TEMPLATE).read()
    assert "secrets.GITHUB_TOKEN" in text, "it must use the workflow's own token, not a PAT"
    assert "fetch-depth: 0" in text, (
        "without full history `git rev-list --count origin/dev..origin/main` answers a "
        "different question, quietly and with exit code 0")


def yaml_or_skip():
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(open(TEMPLATE))


def test_no_dispatch_input_is_interpolated_into_a_shell_line():
    """`${{ inputs.… }}` inside a `run:` body is substituted by Actions BEFORE the shell
    sees it, so a dispatch input of `$(…)` executes on the runner. Inputs go through
    `env:` and are read as `"$VAR"`. Only write-access actors can dispatch this workflow,
    so it is a small hole — but it is one, and closing it costs a line."""
    doc = yaml_or_skip()
    for job_id, job in doc["jobs"].items():
        for step in job.get("steps", []):
            body = step.get("run") or ""
            assert "${{ inputs." not in body, (
                f"{job_id} step {step.get('name')!r} interpolates a dispatch input straight "
                f"into a shell line:\n{body}")
    assert "GRACE_SECONDS" in open(TEMPLATE).read(), (
        "the grace input is no longer passed through env: — either it was inlined into the "
        "run: body (see above) or the dispatch knob was dropped")


def test_the_installed_copy_exists():
    """Also covered by test_ci_workflows.py's byte-identity guard once promotion.yml is in
    its TIERS tuple; asserted here too so this file stands alone."""
    assert os.path.exists(INSTALLED), (
        "sim/ci/promotion.yml has no installed copy — GitHub runs .github/workflows/, so "
        "the template alone fires nothing. Copy it across IN THE SAME COMMIT.")
    assert open(TEMPLATE, "rb").read() == open(INSTALLED, "rb").read()
