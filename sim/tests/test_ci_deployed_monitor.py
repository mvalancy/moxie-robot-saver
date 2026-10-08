"""The deployed-site monitor can go red, and its one paid turn stays one a day.

Measured 2026-10-07 with the real checker on five fixtures built from the real Pages
Functions: healthy, a dead brain, lost secrets, the kill switch and missing Functions all
passed `check_deployed.mjs` 24/24, and `GET /api/health` was the only request any of them
saw. So `deployed.yml` now runs a daily canary (`sim/check_live_turn.mjs`, one chat turn) on
its OWN cron, and each job picks its runs by the cron string that fired it
(`github.event.schedule`). Those strings are repeated in the jobs' `if:`, and a cron edited
without its `if:` silently stops a job: no run, so no red. This file reads that wiring, the
once-a-day ceiling the owner approved, and that the fast tier keeps running the canary's
`--selftest`.

Plus the same slice's repo hygiene: wrangler's local state (its cache holds the Cloudflare
account id and name) is git-ignored.
"""
from __future__ import annotations

import os
import re
import subprocess

import pytest

yaml = pytest.importorskip("yaml", reason="the workflow guards parse YAML")

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEPLOYED = os.path.join(REPO, "sim", "ci", "deployed.yml")
FAST = os.path.join(REPO, "sim", "ci", "ci.yml")
CANARY = "node sim/check_live_turn.mjs"


def _doc(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _on(doc: dict) -> dict:
    """The `on:` block — PyYAML reads bare `on` as the YAML 1.1 boolean `True`."""
    return doc.get("on", doc.get(True)) or {}


def _crons(doc: dict) -> list:
    return [entry["cron"] for entry in _on(doc).get("schedule") or []]


def _if(job: dict) -> str:
    return str(job.get("if") or "")


def _named(cond: str, op: str) -> list:
    """The cron strings an `if:` compares `github.event.schedule` against with `op`."""
    return re.findall(r"github\.event\.schedule\s*" + re.escape(op) + r"\s*'([^']+)'", cond)


def _runs(job: dict) -> list:
    return [str(s.get("run") or "") for s in job.get("steps") or []]


@pytest.fixture(scope="module")
def deployed() -> dict:
    return _doc(DEPLOYED)


def _canary(deployed: dict) -> dict:
    jobs = [j for j in deployed["jobs"].values() if any(CANARY in r for r in _runs(j))]
    assert len(jobs) == 1, f"exactly one job must run `{CANARY}` — found {len(jobs)}"
    return jobs[0]


def test_every_cron_a_job_names_is_really_scheduled(deployed):
    named = {c for job in deployed["jobs"].values() for op in ("==", "!=")
             for c in _named(_if(job), op)}
    assert named, "no job keys on github.event.schedule, so the canary has no cron of its own"
    assert named <= set(_crons(deployed)), \
        f"an `if:` names a cron that is not scheduled: {sorted(named - set(_crons(deployed)))}"


def test_the_canary_fires_once_a_day_on_its_own_cron_and_the_free_check_keeps_four(deployed):
    mine = _named(_if(_canary(deployed)), "==")
    assert len(mine) == 1, f"the canary must key on exactly one cron — {mine}"
    minute, hour, *rest = mine[0].split()
    assert minute.isdigit() and hour.isdigit() and rest == ["*", "*", "*"], \
        f"{mine[0]!r} must fire once a day: the owner approved one paid turn a day"
    free = [c for c in _crons(deployed) if c != mine[0]]
    assert len(free) == 1 and len(free[0].split()[1].split(",")) == 4, free
    # The free check sits out the canary's run, so a red in that slot means the brain.
    assert _named(_if(deployed["jobs"]["deployed"]), "!=") == mine


def test_the_canary_runs_only_on_its_cron_or_a_dispatch(deployed):
    assert set(_on(deployed)) == {"schedule", "workflow_dispatch"}, "never on a push or a PR"
    cond = _if(_canary(deployed))
    assert "workflow_dispatch" in cond and "inputs.canary" in cond, cond


def test_the_canary_spends_only_through_check_live_turn_and_holds_no_secret(deployed):
    job = _canary(deployed)
    assert [r for r in _runs(job) if r.strip()] == [CANARY], _runs(job)
    assert "secrets." not in yaml.safe_dump(job), "the deployment's own key pays; CI holds none"
    assert deployed.get("permissions") == {"contents": "read"}, deployed.get("permissions")


def test_the_fast_tier_runs_the_canarys_selftest():
    runs = [r for job in _doc(FAST)["jobs"].values() for r in _runs(job)]
    assert any(re.search(r"^\s*node sim/check_live_turn\.mjs --selftest\s*$", r, re.M) for r in runs), \
        "sim/ci/ci.yml must run `node sim/check_live_turn.mjs --selftest`: teeth nobody checks rot"


@pytest.mark.parametrize("path", [".wrangler", ".wrangler/cache/wrangler-account.json"])
def test_wranglers_local_state_is_git_ignored(path):
    res = subprocess.run(["git", "check-ignore", "-q", path], cwd=REPO,
                         capture_output=True, text=True)
    assert res.returncode == 0, \
        f"{path} is not git-ignored; wrangler's cache holds the Cloudflare account id and name"
