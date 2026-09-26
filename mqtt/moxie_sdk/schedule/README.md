# 🗓️ Schedule builder

The day plan (`ContentSchedule`) a Moxie pulls at the start of every session, built by a
deterministic, scored recommender. The full contract — the recovered proto shape, the
weight table, the time-of-day buckets and the credit to OpenMoxie — is the docstring of
[`__init__.py`](__init__.py). Callers import `moxie_sdk.schedule` only.

| Module | Holds |
|---|---|
| [`catalog.py`](catalog.py) | Wire field names, the on-board module catalog, and every planner weight and table (so a test can isolate one factor) |
| [`normalize.py`](normalize.py) | Keep only `ContentSchedule` / `Recommendation` fields; `validate_schedule` |
| [`signals.py`](signals.py) | The planner's inputs: clock helpers, mentor-behavior history, bedtime, parent requests, telemetry, template selection, `plan_inputs` |
| [`planner.py`](planner.py) | `score_module`, the parent-facing `explain`, `plan_day` / `plan` / `build_schedule` |

Pure: no store, no network; the clock is passed in (`now=`). The supervisor's side
(persisting the plan, serving `GET /schedule`) is `mqtt/supervisor/moxie_runtime/schedule.py`.
Tests: `sim/tests/test_schedule*.py`.

---
📖 [SDK](../README.md) · [Back to top](../../../README.md)
