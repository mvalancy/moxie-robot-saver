"""Scoring, parent-facing explanations, and the planner itself."""
from __future__ import annotations

import datetime

from .catalog import (AFFINITY_FLOOR, AFFINITY_MAX, AFFINITY_NEUTRAL,
    CATEGORY_REPEAT_PENALTY, DEFAULT_CHILD_NAME, DEFAULT_TEMPLATE,
    FTUE_COMPLETION_COUNTS, MAX_TIER, RECENCY_3_DAY, RECENCY_SAME_DAY,
    RECENCY_WINDOW_DAYS, SLOT_MINUTES, TIME_FIT, W_FTUE, W_PARENT_REQUEST, W_TIER)
from .normalize import _normalize_schedule, _recommendation, _recommendation_list
from .signals import (_age_days, category_energy, _hhmm, in_bedtime, module_label,
    _parse_iso, plan_inputs, select_template, _tiebreak, time_bucket)


def score_module(module, *, inputs, slot, used_categories=(), now=None) -> tuple:
    """Score one candidate for one slot. Returns `(score, factors, reason_codes)`.

    Every term is named in `factors` so `GET /schedule` can show a parent the arithmetic
    and a test can isolate one factor by zeroing its neighbours.
    """
    mid = module.get("module_id", "")
    category = str(module.get("category") or "UNASSIGNED").upper()
    now = now or _parse_iso(inputs["now"])
    hist = (inputs.get("history") or {}).get(mid) or {}
    factors, codes = {}, []

    if mid in FTUE_COMPLETION_COUNTS and mid not in set(inputs.get("ftue_skips") or ()):
        factors["ftue"] = W_FTUE
        codes.append("ftue")

    seen = int(hist.get("seen") or 0)
    factors["coverage"] = -W_TIER * min(seen, MAX_TIER)
    if seen == 0:
        codes.append("unseen")

    age = _age_days(hist.get("last_ts"), now)
    if age is None:
        factors["recency"] = 0
    elif age < 1:
        factors["recency"] = RECENCY_SAME_DAY
        codes.append("just_played")
    elif age < RECENCY_WINDOW_DAYS:
        factors["recency"] = RECENCY_3_DAY
        codes.append("played_recently")
    else:
        factors["recency"] = 0
        if seen:
            codes.append("rested")

    completed, abandoned = int(hist.get("completed") or 0), int(hist.get("abandoned") or 0)
    if completed or abandoned:
        rate = completed / float(completed + abandoned)
        factors["affinity"] = AFFINITY_FLOOR + int(round((AFFINITY_MAX - AFFINITY_FLOOR)
                                                         * rate))
        codes.append("finishes" if rate >= 0.5 else "abandons")
    else:
        factors["affinity"] = AFFINITY_NEUTRAL

    at = now + datetime.timedelta(minutes=slot * SLOT_MINUTES)
    energy = category_energy(category)
    factors["time_of_day"] = TIME_FIT.get(time_bucket(at), {}).get(energy, 0)
    if factors["time_of_day"] >= max(TIME_FIT.get(time_bucket(at), {}).values() or [0]):
        codes.append("time_of_day")

    repeats = list(used_categories).count(category)
    factors["category_spread"] = -CATEGORY_REPEAT_PENALTY * repeats
    factors["tiebreak"] = _tiebreak(inputs.get("device_id", ""), inputs.get("day", ""), mid)
    return sum(factors.values()), factors, codes


# -------------------------------------------------------------------- explanations ----

def clock_label(value) -> str:
    """"16:00" → "4:00 pm" — the way a parent reads a time. Built by hand rather than with
    `%-I`, which is not portable."""
    try:
        hour, _, minute = str(value).partition(":")
        h, m = int(hour), int(minute)
    except ValueError:
        return str(value)
    suffix = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}"


def _sentence(text: str) -> str:
    """A line a parent reads: first letter capitalized (the child's nickname may not be —
    `ChildProfile.nickname` defaults to "friend")."""
    return text[:1].upper() + text[1:] if text else text


def explain(rec, *, reason_codes, inputs, hist=None, at=None, requested_at="") -> str:
    """One short, parent-readable sentence for one entry in the day."""
    child = inputs.get("child_name") or DEFAULT_CHILD_NAME
    label = module_label(rec)
    hist = hist or {}
    when = f" in the {time_bucket(at)} slot" if at is not None else ""
    if "parent_request" in reason_codes:
        asked = clock_label(requested_at) if requested_at else "today"
        drift = None
        if at is not None and requested_at:
            try:
                drift = (at.hour * 60 + at.minute) - _hhmm(requested_at)
            except ValueError:
                drift = None
        if drift is not None and abs(drift) > SLOT_MINUTES:
            side = "starts later than that" if drift > 0 else "ends before that"
            return _sentence(
                f"Requested by a parent for {asked} — this session {side}, so {label} "
                f"is queued at {clock_label(at.strftime('%H:%M'))} instead.")
        return _sentence(f"Requested by a parent for {asked} — {label} is pinned to "
                         f"that slot.")
    if "ftue" in reason_codes:
        return _sentence(f"{label} is part of Moxie's first-week onboarding, "
                         f"which is still running.")
    if "fixture" in reason_codes:
        return _sentence(f"{label} is a daily fixture — it runs every day.")
    if "chat" in reason_codes:
        return _sentence(f"A free chat, so {child} gets a breather between activities.")
    if "finishes" in reason_codes:
        done = int(hist.get("completed") or 0)
        times = "once" if done == 1 else f"{done} times"
        return _sentence(f"{child} finished {label} {times} — "
                         f"scheduling it{when or ' again today'}.")
    if "abandons" in reason_codes:
        left = int(hist.get("abandoned") or 0)
        times = "once" if left == 1 else f"{left} times"
        return _sentence(f"{child} has left {label} early {times} — kept in the "
                         f"rotation for variety, but no longer a top pick.")
    if "unseen" in reason_codes:
        return _sentence(f"{child} has not tried {label} yet — new for today{when}.")
    if "rested" in reason_codes:
        return _sentence(f"{label} has had a rest since it last came up — "
                         f"bringing it back{when}.")
    return _sentence(f"{label} fits{when or ' today'}.")


# --------------------------------------------------------------------- the planner ----

def _interleave(activities: list, chats: list) -> list:
    """Spread `chats` evenly between `activities` (a chat should never bookend the day)."""
    if not chats:
        return list(activities)
    if not activities:
        return list(chats)
    out = list(activities)
    gap = max(1, len(activities) // (len(chats) + 1))
    offset = 0
    for chat in chats:
        pos = min(len(out), offset + gap)
        out.insert(pos, chat)
        offset = pos + 1
    return out


def schedule_template(content_module=None, name: str = "") -> dict:
    """The authoring template to plan from: a `schedules[]` entry of a loaded content
    module (`moxie_sdk.content.module.ContentModule`), by `name` or the first one.
    Falls back to `DEFAULT_TEMPLATE`. Read-only — the module is never mutated."""
    return select_template(getattr(content_module, "schedules", None), name=name)


def plan_day(inputs: dict) -> tuple:
    """`inputs` (from `plan_inputs`) → `(ContentSchedule, explanations)`.

    Pure and deterministic: same inputs, same bytes, in any process. The schedule is
    byte-compatible with what the pre-recommender builder emitted — only `ContentSchedule`
    fields, only `Recommendation` keys inside them. `explanations` is a parallel list
    `[{module_id, slot, at, reason_codes, line, score, factors}]`, one per entry in
    `provided_schedule`, and never goes on the wire.
    """
    now = _parse_iso(inputs["now"])
    template = dict(inputs.get("template") or DEFAULT_TEMPLATE)
    gen = template.pop("generate", None) or {}
    skips = set(inputs.get("ftue_skips") or ())
    history = inputs.get("history") or {}
    window = inputs.get("bedtime") or {}
    requests = inputs.get("parent_requests") or []
    pinned = {r["slot"]: r for r in requests if r.get("slot") is not None}
    requested_ids = {r["module_id"]: r for r in requests if r.get("due_today")}

    # 1. the pinned spine, exactly as before: authored order, FTUE pruned.
    entries = []                                  # [(Recommendation, explanation)]
    for rec in _recommendation_list(template.get("provided_schedule")):
        mid = rec["module_id"]
        if mid in skips:
            continue
        codes = ["ftue"] if mid in FTUE_COMPLETION_COUNTS else ["fixture"]
        req = requested_ids.get(mid)
        if req:
            codes.insert(0, "parent_request")
        entries.append((rec, {"module_id": mid, "slot": None, "at": None,
                              "reason_codes": codes,
                              "line": explain(rec, reason_codes=codes, inputs=inputs,
                                              hist=history.get(mid),
                                              requested_at=(req or {}).get("at", "")),
                              "score": None, "factors": {}}))
    prefix_len = len(entries)
    scheduled = {r["module_id"] for r, _ in entries}

    # 2. the scored fill.
    if gen:
        excluded = set(gen.get("excluded_module_ids") or ()) | skips | scheduled
        pool = {m["module_id"]: dict(m) for m in (inputs.get("catalog") or ())
                if m.get("module_id") and m["module_id"] not in excluded}
        for extra in gen.get("extra_modules") or ():
            rec = _recommendation(extra)
            if rec and rec["module_id"] not in excluded:
                pool[rec["module_id"]] = {**rec,
                                          "category": (extra or {}).get("category", "USER")}
        # A parent request outranks the template's own exclusions: if they asked for it
        # today and it is not already in the spine, it goes back in the pool.
        for mid, req in requested_ids.items():
            if req.get("slot") is not None and mid not in pool and mid not in scheduled:
                base = next((dict(m) for m in (inputs.get("catalog") or ())
                             if m.get("module_id") == mid), None)
                if base:
                    pool[mid] = base

        activities = []
        used_categories: list = []
        last_category = None
        for slot in range(int(gen.get("module_count", 0) or 0)):
            at = now + datetime.timedelta(minutes=(prefix_len + slot) * SLOT_MINUTES)
            if in_bedtime(at, window):
                break                              # never plan into bedtime
            pin = pinned.get(slot)
            chosen = None
            if pin and pin["module_id"] in pool:
                chosen = pool[pin["module_id"]]
                score, factors, codes = score_module(
                    chosen, inputs=inputs, slot=prefix_len + slot,
                    used_categories=used_categories, now=now)
                factors["parent_request"] = W_PARENT_REQUEST
                score += W_PARENT_REQUEST
                codes = ["parent_request"] + [c for c in codes if c != "parent_request"]
            else:
                # A module pinned to a LATER slot is held back for it; otherwise an
                # earlier slot could score it top, consume it, and the parent's pin would
                # silently vanish (depending on the hour the planner ran).
                held = {r["module_id"] for at_slot, r in pinned.items()
                        if at_slot > slot}
                free = [m for m in pool.values() if m["module_id"] not in held]
                if not free:                       # more pins than slots left to fill
                    free = list(pool.values())
                candidates = [m for m in free
                              if str(m.get("category") or "") != last_category]
                if not candidates:
                    candidates = list(free)
                best = None
                for m in candidates:
                    s, f, c = score_module(m, inputs=inputs, slot=prefix_len + slot,
                                           used_categories=used_categories, now=now)
                    key = (s, m["module_id"])
                    if best is None or key > best[0]:
                        best = (key, m, s, f, c)
                if best is None:
                    break
                _, chosen, score, factors, codes = best
                if len(candidates) < len(pool):
                    codes = codes + ["variety"]
            pool.pop(chosen["module_id"], None)
            rec = _recommendation(chosen)
            if not rec:
                continue
            category = str(chosen.get("category") or "")
            used_categories.append(category.upper())
            last_category = category
            req = requested_ids.get(rec["module_id"]) if "parent_request" in codes else None
            activities.append((rec, {
                "module_id": rec["module_id"], "slot": prefix_len + slot,
                "at": at.strftime("%H:%M"), "reason_codes": codes,
                "line": explain(rec, reason_codes=codes, inputs=inputs,
                                hist=history.get(rec["module_id"]), at=at,
                                requested_at=(req or {}).get("at", "")),
                "score": score, "factors": factors}))

        chat_modules = _recommendation_list(gen.get("chat_modules"))
        chat_count = int(gen.get("chat_count", 0) or 0)
        chats = []
        for i in range(chat_count if chat_modules else 0):
            rec = dict(chat_modules[i % len(chat_modules)])
            chats.append((rec, {"module_id": rec["module_id"], "slot": None, "at": None,
                                "reason_codes": ["chat"],
                                "line": explain(rec, reason_codes=["chat"], inputs=inputs),
                                "score": None, "factors": {}}))
        entries += _interleave(activities, chats)

    template["provided_schedule"] = [rec for rec, _ in entries]
    return _normalize_schedule(template), [expl for _, expl in entries]


def plan(device_id: str = "", *, template=None, mentor_behaviors=(), day: str = "",
         now=None, effective_config=None, telemetry_summary=None, telemetry_packets=(),
         content_schedules=None, catalog=None, child_name: str = "") -> tuple:
    """`plan_inputs` + `plan_day` in one call → `(schedule, explanations, inputs)`.
    This is what the runtime uses; `build_schedule` is the schedule-only shorthand."""
    inputs = plan_inputs(device_id, now, mentor_behaviors=mentor_behaviors,
                         telemetry_summary=telemetry_summary,
                         telemetry_packets=telemetry_packets,
                         effective_config=effective_config,
                         content_schedules=content_schedules, catalog=catalog,
                         template=template, day=day, child_name=child_name)
    sched, explanations = plan_day(inputs)
    gen = (inputs.get("template") or {}).get("generate") or {}
    wanted = int(gen.get("module_count", 0) or 0)
    got = sum(1 for e in explanations if e.get("slot") is not None)
    inputs["planned"] = {
        "activities": got, "requested": wanted,
        "dropped_for_bedtime": max(0, wanted - got) if inputs.get(
            "bedtime", {}).get("enabled") else 0,
        "entries": len(explanations),
    }
    return sched, explanations, inputs


def build_schedule(template: dict | None = None, *, mentor_behaviors=(),
                   device_id: str = "", day: str = "", now=None,
                   effective_config=None, telemetry_summary=None,
                   telemetry_packets=(), catalog=None, child_name: str = "") -> dict:
    """Build one session's `ContentSchedule` (the value of `CloudQueryResponse.schedule`).

    `device_id` + `day` seed the plan (stable for a day); `now` lays out slot times
    (defaults to the wall clock); `effective_config` supplies parent requests and bedtime.
    Use `plan()` when the explanations are wanted too.
    """
    sched, _, _ = plan(device_id, template=template, mentor_behaviors=mentor_behaviors,
                       day=day, now=now, effective_config=effective_config,
                       telemetry_summary=telemetry_summary,
                       telemetry_packets=telemetry_packets, catalog=catalog,
                       child_name=child_name)
    return sched
