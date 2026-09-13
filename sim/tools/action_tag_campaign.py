"""Privacy-first accounting for the one bounded live action-tag campaign.

The recorder deliberately knows nothing about prompt or reply text.  It observes the
OpenAI-compatible client boundary and persists only allow-listed counts/categories so
the supervising process can still classify an interrupted child without retaining
model output.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


_CATEGORIES = {
    "connection", "timeout", "rate_limit", "server", "auth_config",
    "invalid_response", "unexpected",
}
_MEASUREMENTS = {"inconclusive", "completed", "skipped", "error"}
_ADHERENCE = {"not_evaluated", "pass", "fail"}
_TERMINATIONS = {
    "not_started", "running", "completed", "missing_prerequisite",
    "budget_exhausted", "connection", "timeout", "rate_limit", "server",
    "auth_config", "invalid_response", "unexpected", "incomplete",
    "instrument_error", "child_error",
}
_STATE_KEYS = {
    "campaign", "schema", "planned_trials", "attempt_limit", "attempts",
    "responses_received", "completed", "trials_observed", "exit_hits",
    "output_violations", "elapsed_ms", "measurement", "adherence",
    "termination", "last_upstream_category", "authoritative_attempts",
}


def error_category(exc: BaseException) -> str:
    """Map untrusted provider exceptions to a fixed, non-disclosing category."""
    names = {type(exc).__name__} | {base.__name__ for base in type(exc).__mro__}
    status = (getattr(exc, "status_code", None)
              or getattr(getattr(exc, "response", None), "status_code", None))
    if names & {"APITimeoutError", "Timeout", "TimeoutError"}:
        return "timeout"
    if names & {"APIConnectionError", "ConnectionError", "ConnectError"}:
        return "connection"
    if "RateLimitError" in names or status == 429:
        return "rate_limit"
    try:
        numeric_status = int(status) if status is not None else None
    except (TypeError, ValueError):
        numeric_status = None
    if numeric_status is not None and 500 <= numeric_status < 600:
        return "server"
    if status in {400, 401, 403, 404} or names & {"AuthenticationError", "PermissionDeniedError"}:
        return "auth_config"
    return "unexpected"


class CampaignRecord:
    """Counts-only state, flushed after every observable client event."""

    schema = 1

    def __init__(self, campaign: str, planned_trials: int, attempt_limit: int,
                 state_path: str | os.PathLike | None = None, *, clock=time.monotonic):
        self.campaign = campaign
        self.planned_trials = planned_trials
        self.attempt_limit = attempt_limit
        self.state_path = Path(state_path) if state_path else None
        self._clock = clock
        self._started = clock()
        self.attempts = 0
        self.responses_received = 0
        self.completed = 0
        self.trials_observed = 0
        self.exit_hits = 0
        self.output_violations = 0
        self.last_upstream_category = "none"
        self.authoritative_attempts = None
        self.measurement = "inconclusive"
        self.adherence = "not_evaluated"
        self.termination = "not_started"
        self._flush()

    def _summary(self) -> dict:
        elapsed_ms = max(0, int((self._clock() - self._started) * 1000))
        return {
            "campaign": self.campaign,
            "schema": self.schema,
            "planned_trials": self.planned_trials,
            "attempt_limit": self.attempt_limit,
            "attempts": self.attempts,
            "responses_received": self.responses_received,
            "completed": self.completed,
            "trials_observed": self.trials_observed,
            "exit_hits": self.exit_hits,
            "output_violations": self.output_violations,
            "elapsed_ms": elapsed_ms,
            "measurement": self.measurement,
            "adherence": self.adherence,
            "termination": self.termination,
            "last_upstream_category": self.last_upstream_category,
            "authoritative_attempts": self.authoritative_attempts,
        }

    def _flush(self) -> None:
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_path.with_name(f".{self.state_path.name}.{os.getpid()}.tmp")
        temp.write_text(json.dumps(self._summary(), sort_keys=True) + "\n")
        os.replace(temp, self.state_path)

    def begin_attempt(self) -> None:
        self.attempts += 1
        self.termination = "running"
        self._flush()

    def upstream_error(self, category: str) -> None:
        self.last_upstream_category = category if category in _CATEGORIES else "unexpected"
        self._flush()

    def response(self, eligible: bool) -> None:
        self.responses_received += 1
        if eligible:
            self.completed += 1
            self.last_upstream_category = "none"
        else:
            self.last_upstream_category = "invalid_response"
        self._flush()

    def trial(self, *, exit_hit: bool, output_ok: bool) -> None:
        self.trials_observed += 1
        self.exit_hits += int(exit_hit)
        self.output_violations += int(not output_ok)
        self._flush()

    def skipped(self) -> dict:
        self.measurement = "skipped"
        self.termination = "missing_prerequisite"
        self._flush()
        return self._summary()

    def finish(self, authoritative_attempts: int) -> dict:
        self.authoritative_attempts = authoritative_attempts
        if authoritative_attempts != self.attempts:
            self.measurement = "error"
            self.termination = "instrument_error"
        elif self.completed < self.planned_trials or self.trials_observed < self.planned_trials:
            self.measurement = "inconclusive"
            self.termination = ("budget_exhausted" if self.attempts >= self.attempt_limit
                                else self.last_upstream_category
                                if self.last_upstream_category != "none" else "incomplete")
        else:
            self.measurement = "completed"
            self.termination = "completed"
            passed = self.exit_hits >= 2 and self.output_violations == 0
            self.adherence = "pass" if passed else "fail"
        self._flush()
        return self._summary()

    def summary(self) -> dict:
        return self._summary()


class RecordingClient:
    """OpenAI-compatible proxy that stores counts/categories, never response text."""

    class _Chat:
        class _Completions:
            def __init__(self, create, record: CampaignRecord):
                self._create = create
                self._record = record

            def create(self, *args, **kwargs):
                self._record.begin_attempt()
                try:
                    response = self._create(*args, **kwargs)
                except Exception as exc:
                    self._record.upstream_error(error_category(exc))
                    raise
                try:
                    content = response.choices[0].message.content
                    eligible = isinstance(content, str) and bool(content.strip())
                except (AttributeError, IndexError, KeyError, TypeError):
                    eligible = False
                self._record.response(eligible)
                return response

        def __init__(self, client, record):
            self.completions = RecordingClient._Chat._Completions(
                client.chat.completions.create, record)

    def __init__(self, client, record: CampaignRecord):
        self.chat = self._Chat(client, record)


def sanitized_state(path: str | os.PathLike) -> dict | None:
    """Load only the exact allow-listed schema emitted by CampaignRecord."""
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(raw, dict) or set(raw) != _STATE_KEYS:
        return None
    counts = ("attempts", "responses_received", "completed", "trials_observed",
              "exit_hits", "output_violations", "elapsed_ms")
    if (raw.get("schema") != 1 or raw.get("campaign") != "goodbye"
            or raw.get("planned_trials") != 3 or raw.get("attempt_limit") != 6
            or any(type(raw.get(key)) is not int or raw[key] < 0 for key in counts)
            or raw["attempts"] > 6
            or not (raw["completed"] <= raw["responses_received"] <= raw["attempts"])
            or not (raw["exit_hits"] <= raw["trials_observed"] <= raw["completed"])
            or raw["output_violations"] > raw["trials_observed"]
            or raw.get("measurement") not in _MEASUREMENTS
            or raw.get("adherence") not in _ADHERENCE
            or raw.get("termination") not in _TERMINATIONS
            or raw.get("last_upstream_category") not in (_CATEGORIES | {"none"})):
        return None
    authoritative = raw.get("authoritative_attempts")
    if authoritative is not None and (type(authoritative) is not int or authoritative < 0):
        return None
    return {key: raw[key] for key in sorted(_STATE_KEYS)}
