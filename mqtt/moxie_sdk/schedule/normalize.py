"""Normalizing: keep only `ContentSchedule`/`Recommendation` fields, and validate."""
from __future__ import annotations


from .catalog import (RECOMMENDATION_FIELDS, _RECOMMENDATION_FIELDS_IN_SCHEDULE,
    SCHEDULE_FIELDS)


def _recommendation(item) -> dict | None:
    """Keep only `Recommendation` fields (RemoteChat.proto:26-34).

    Authoring templates carry extra keys — OpenMoxie's generated entries even ship a
    `category` on the wire — and unknown keys are at best ignored by a protobuf JSON
    parser. We strip instead of hoping."""
    if not isinstance(item, dict):
        return None
    rec = {k: item[k] for k in RECOMMENDATION_FIELDS if k in item and item[k] not in (None, "")}
    return rec or None


def _recommendation_list(items) -> list:
    return [r for r in (_recommendation(i) for i in (items or ())) if r]


def _normalize_schedule(sched: dict) -> dict:
    """Emit only ContentSchedule fields, with every Recommendation-typed value cleaned."""
    out = {k: v for k, v in sched.items() if k in SCHEDULE_FIELDS}
    out["provided_schedule"] = _recommendation_list(out.get("provided_schedule"))
    for key in _RECOMMENDATION_FIELDS_IN_SCHEDULE:
        if key in out:
            rec = _recommendation(out[key])
            if rec:
                out[key] = rec
            else:
                del out[key]
    hub = out.get("hub_config")
    if isinstance(hub, dict):                    # ContentSchedule.HubConfig{hubs, skipped_modules}
        cleaned = {"hubs": _recommendation_list(hub.get("hubs"))}
        if hub.get("skipped_modules"):
            cleaned["skipped_modules"] = [str(m) for m in hub["skipped_modules"]]
        out["hub_config"] = cleaned
    cfg = out.get("config")
    if isinstance(cfg, dict):                    # ScheduleConfig{day_one_schedule, promoted_content, …}
        cfg = dict(cfg)
        for key in ("day_one_schedule", "promoted_content"):
            if key in cfg:
                cfg[key] = _recommendation_list(cfg[key])
        out["config"] = cfg
    eos = out.get("end_of_session")
    if isinstance(eos, dict):                    # EndOfSessionConfig{chat_module, end_module, chat_count}
        eos = dict(eos)
        for key in ("chat_module", "end_module"):
            rec = _recommendation(eos.get(key))
            if rec:
                eos[key] = rec
            else:
                eos.pop(key, None)
        out["end_of_session"] = eos
    return out


def validate_schedule(sched) -> list:
    """Return a list of problems with a built schedule ("" = valid). Used by tests and
    callable by an author-facing tool; checks only what the recovered protos establish."""
    problems = []
    if not isinstance(sched, dict):
        return ["schedule is not an object"]
    for key in sched:
        if key not in SCHEDULE_FIELDS:
            problems.append(f"unknown ContentSchedule field {key!r}")
    plan = sched.get("provided_schedule")
    if not isinstance(plan, list) or not plan:
        problems.append("provided_schedule is empty — the robot has nothing to run")
        return problems
    for i, rec in enumerate(plan):
        if not isinstance(rec, dict):
            problems.append(f"provided_schedule[{i}] is not a Recommendation object")
            continue
        if not rec.get("module_id"):
            problems.append(f"provided_schedule[{i}] has no module_id")
        for key in rec:
            if key not in RECOMMENDATION_FIELDS:
                problems.append(f"provided_schedule[{i}] has unknown field {key!r}")
    return problems


# ------------------------------------------------------------------------ the plan ----
