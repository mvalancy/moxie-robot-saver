"""🎭 Be Moxie, 📅 Today's plan, 🎚️ the voice picker and 🧠 the brain picker."""
from __future__ import annotations
from typing import Optional

from ._coerce import _dict, _error, _int, _num, _seq, _strs, card_view

# --- 🎭 Be Moxie (puppet / telehealth) ---------------------------------------------------
#: `TeleHealth.RobotState`, recovered (docs/reverse-engineering/protocol/telehealth.md:36).
TELEHEALTH_STATES = ("UNKNOWN_STATE", "READY", "IN_SESSION", "EXITING")


def normalize_transcript_line(e: Optional[dict]) -> dict:
    """One `{who, text, at}` entry. Text only: no audio/video path exists by design."""
    e = e or {}
    return {"who": "operator" if e.get("who") == "operator" else "child",
            "text": str(e.get("text") or ""), "at": _num(e.get("at"))}


def normalize_telehealth(payload: Optional[dict]) -> dict:
    """Runtime `/telehealth` (GET or POST) → the 🎭 card.

    A state the robot never reported is NOT "READY": `reported` is false then, and
    `state_known` is false for a name outside the recovered enum, so the card shows what
    the robot said without pretending to understand it. A bedtime window is a warning,
    not a claim that the line was dropped."""
    p = _dict(payload)
    ok = bool(p.get("ok"))
    state = str(p.get("state") or "")
    out = {
        "ok": ok,
        "device_id": p.get("device_id"),
        "enabled": bool(p.get("enabled")) if ok else False,
        "online": bool(p.get("online")),
        "session_id": str(p.get("session_id") or "") if ok else "",
        "in_session": bool(p.get("in_session")) if ok else False,
        "state": state,
        "reported": bool(state),
        "state_known": state in TELEHEALTH_STATES,
        "state_at": _num(p.get("state_at")),
        "in_bedtime": bool(p.get("in_bedtime")) if ok else False,
        "transcript": [normalize_transcript_line(e) for e in (p.get("transcript") or [])
                       if isinstance(e, dict)],
        "moods": [{"id": str(m.get("id") or ""), "label": str(m.get("label") or ""),
                   "value": _int(m.get("value"))}
                  for m in (p.get("moods") or []) if isinstance(m, dict)],
        "max_intensity": _int(p.get("max_intensity"), 2),
        "error": None if ok else (p.get("error") or "supervisor not reachable"),
        "reason": p.get("reason") or None,
    }
    # a write's receipt: what was said, or why nothing was
    if p.get("spoke"):
        out["spoke"] = str(p["spoke"])
    if p.get("flagged"):
        out["flagged"] = [str(c) for c in p["flagged"]]
    if p.get("blocked") or p.get("categories"):
        out["blocked"] = bool(p.get("blocked"))
        out["categories"] = [str(c) for c in (p.get("categories") or [])]
        out["labels"] = [str(c) for c in (p.get("labels") or [])]
    return out


# --- 📅 Today's plan — "why this activity today" -----------------------------------------
# `explanations[i]` explains `schedule.provided_schedule[i]` (content-module-contract.md
# §"The explanation"). Three things are never invented: a module NAME (the id goes out
# verbatim when the wire's `module_name` is empty — the plain-English table lives in the
# SDK), a CLOCK TIME for the unslotted authored spine, and a TELEMETRY signal
# (`carries_module_signal` is carried through as the runtime reported it).

def normalize_schedule_entry(expl: Optional[dict], rec: Optional[dict]) -> dict:
    """One `explanations[i]` + the `provided_schedule[i]` it explains → a card row."""
    expl, rec = _dict(expl), _dict(rec)
    codes = [str(c) for c in (expl.get("reason_codes") or []) if c is not None]
    module_id = str(expl.get("module_id") or rec.get("module_id") or "")
    at = expl.get("at")
    return {
        "time_local": str(at) if at else None,
        "module_id": module_id,
        "name": str(rec.get("module_name") or "") or module_id,
        "why": str(expl.get("line") or ""),
        "pinned": "parent_request" in codes,
        "fixture": expl.get("slot") is None,     # the authored spine shows "—", not a time
        "reason_codes": codes,
    }


def _pair_explanations(expls: list, recs: list) -> list:
    """Join by position (the contract); a payload that broke it falls back to the first
    unused entry with the same module_id, then to nothing."""
    rows, used = [], set()
    for i, expl in enumerate(expls):
        mid = expl.get("module_id") if isinstance(expl, dict) else None
        rec, hit = (recs[i] if i < len(recs) else None), i
        if not (isinstance(rec, dict) and rec.get("module_id") == mid):
            rec, hit = next(((r, j) for j, r in enumerate(recs) if j not in used
                             and isinstance(r, dict) and r.get("module_id") == mid),
                            (None, None))
        if hit is not None:
            used.add(hit)
        rows.append(normalize_schedule_entry(expl, rec))
    return rows


@card_view("schedule", {
    "ok": False, "device_id": None, "day": "", "planned_at": "", "child_name": "",
    "served": False, "entries": [],
    "constraints": {"bedtime": {"enabled": False, "kind": ""},
                    "parent_request": {"count": 0, "pinned": []},
                    "telemetry_signal": False},
    "dropped_for_bedtime": 0, "error": "supervisor not reachable"})
def normalize_schedule_view(p: dict) -> dict:
    """Runtime `/schedule` → the 📅 card."""
    ok = bool(p.get("ok"))
    inputs = _dict(p.get("inputs"))
    rows = _pair_explanations(_seq(p.get("explanations")),
                              _seq(_dict(p.get("schedule")).get("provided_schedule")))
    bed = _dict(inputs.get("bedtime"))
    bedtime = {"enabled": bool(bed.get("enabled")), "kind": str(bed.get("kind") or "")}
    if bedtime["enabled"]:
        bedtime["starts_at"] = str(bed.get("starts_at") or "")
        bedtime["ends_at"] = str(bed.get("ends_at") or "")
    pinned = [{"module_id": str(r.get("module_id") or ""), "at": str(r.get("at") or "")}
              for r in _seq(inputs.get("parent_requests"))
              if isinstance(r, dict) and r.get("due_today") and r.get("slot") is not None]
    return {
        "ok": ok,
        "device_id": p.get("device_id"),
        "day": str(p.get("day") or inputs.get("day") or ""),
        "planned_at": str(p.get("planned_at") or ""),
        "child_name": str(inputs.get("child_name") or ""),
        # False = planned for the parent's read; the robot has not pulled its day yet
        "served": bool(p.get("served")),
        "entries": rows,
        "constraints": {
            "bedtime": bedtime,
            "parent_request": {"count": len(pinned), "pinned": pinned},
            "telemetry_signal": bool(_dict(inputs.get("telemetry")).get("carries_module_signal")),
        },
        "dropped_for_bedtime": _int(_dict(inputs.get("planned")).get("dropped_for_bedtime")),
        "error": None if ok else (p.get("error") or "no plan available"),
    }


# --- 🎚️ the voice picker ----------------------------------------------------------------
#: The two sides of the picker (mirrors `moxie_sdk.voice_settings.KINDS`; restated, not
#: imported, because the console process has no `mqtt/` on its path).
VOICE_KINDS = ("speech", "listening")


def normalize_voice_option(entry: Optional[dict]) -> dict:
    """One dropdown `<option>`: `{id, label, group, engine, model, default}`."""
    e = _dict(entry)
    return {**{k: str(e.get(k) or "") for k in ("id", "label", "group", "engine", "model")},
            "default": bool(e.get("default"))}


def _sides(value) -> dict:
    return {k: (list(value) if isinstance(value, list) else value) for k in VOICE_KINDS}


@card_view("voice", {
    "ok": False, "available": _sides([]), "selected": _sides(""), "labels": _sides(""),
    "installed": _sides(""), "chosen": _sides(False), "pins": _sides(""),
    "pin_notes": _sides(""), "discovering": False, "gateway_error": "", "updated_at": 0,
    "robots": [], "applied": None, "spoke": "", "reason": "",
    "error": "supervisor not reachable"})
def normalize_voice(p: dict) -> dict:
    """Runtime `/voice`, `POST /voice` or `/voice/test` → the 🎚️ card. An unreadable
    payload sets `error` rather than rendering two blank dropdowns that read as "this
    appliance cannot speak"."""
    avail = _dict(p.get("available"))
    ok = bool(p.get("ok"))

    def side(field, cast=lambda v: str(v or "")):
        src = _dict(p.get(field))
        return {k: cast(src.get(k)) for k in VOICE_KINDS}

    return {
        "ok": ok,
        "available": {k: [normalize_voice_option(e) for e in _seq(avail.get(k))
                          if isinstance(e, dict) and e.get("id")] for k in VOICE_KINDS},
        "selected": side("selected"),
        "labels": side("labels"),
        "installed": side("installed"),
        "chosen": side("chosen", bool),
        # an explicit MOXIE_TTS/MOXIE_STT pin already filtered the dropdown upstream; the
        # note is the only thing explaining why the other engines are gone
        "pins": side("pins"),
        "pin_notes": side("pin_notes"),
        "discovering": bool(p.get("discovering")),        # first gateway listing pending
        "gateway_error": str(p.get("gateway_error") or ""),   # exception class; ok stays
        "updated_at": _int(p.get("updated_at")),
        "robots": _strs(p.get("robots")),
        "applied": p.get("applied") if isinstance(p.get("applied"), dict) else None,
        "spoke": str(p.get("spoke") or ""),
        "reason": str(p.get("reason") or ""),
        "error": _error(p, ok, "no voice settings available", "error", "reason"),
    }


# --- 🧠 the brain picker -----------------------------------------------------------------
# Both fleet-level and per-robot — that difference is the feature — so one payload
# carries the house rule, the appliance's own brain, and which layer chose each robot's.

def normalize_brain_option(entry: Optional[dict]) -> dict:
    """One brain the card may offer. `needs` lists the `MOXIE_*` variables it cannot run
    without, shown under the option."""
    e = _dict(entry)
    return {**{k: str(e.get(k) or "") for k in ("id", "label", "group", "blurb")},
            "needs": _strs(e.get("needs")), "default": bool(e.get("default"))}


def normalize_brain_robot(entry: Optional[dict]) -> dict:
    """One robot's row; `source` is `default|fleet|robot|pin` — which layer chose it."""
    e = _dict(entry)
    return {k: str(e.get(k) or "") for k in ("device_id", "child", "brain", "source",
                                             "label", "override", "requested", "note",
                                             "line")}


@card_view("brain", {
    "ok": False, "available": [], "pin": "", "pin_note": "", "default": "", "fleet": "",
    "appliance": "", "env_var": "MOXIE_APP", "installed": [], "robots": [],
    "applied": None, "reason": "", "error": "supervisor not reachable"})
def normalize_brain(p: dict) -> dict:
    """Runtime `/brain` (GET or POST) → the 🧠 card."""
    ok = bool(p.get("ok"))
    return {
        "ok": ok,
        "available": [normalize_brain_option(e) for e in _seq(p.get("available"))
                      if isinstance(e, dict) and e.get("id")],
        # MOXIE_APP's pin already filtered `available`; the note says why it is short
        **{k: str(p.get(k) or "") for k in ("pin", "pin_note", "default", "fleet",
                                            "appliance")},
        "env_var": str(p.get("env_var") or "MOXIE_APP"),
        "installed": _strs(p.get("installed")),
        "robots": [normalize_brain_robot(r) for r in _seq(p.get("robots"))
                   if isinstance(r, dict)],
        "applied": p.get("applied") if isinstance(p.get("applied"), dict) else None,
        "reason": str(p.get("reason") or ""),
        "error": _error(p, ok, "no brain settings available", "error", "reason"),
    }
