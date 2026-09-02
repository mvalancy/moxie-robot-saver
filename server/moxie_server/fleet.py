"""
Fleet view (M6 parent-console) — normalize the MQTT supervisor's status snapshot into
the shape the console renders: one tidy record per connected robot (live state + config
overrides + telemetry count) plus a supervisor summary.

Pure + dependency-free (no fastapi/network here) so it unit-tests in the hermetic suite;
the /local/fleet endpoint in main.py is just: fetch STATUS_URL → normalize_fleet(...).
The snapshot shape comes from MoxieRuntime.status_snapshot().
"""
from __future__ import annotations
import re as _re
from typing import Optional


def _num(v):
    """Coerce to int/float when it looks numeric; else None (a bool isn't a number)."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return None


def robot_summary(r: dict) -> str:
    """A one-line human summary of a robot's live state for the console card."""
    bits = []
    # The pairing gate leads, because it changes what every other line means: a pending
    # robot's config is the minimal child-free one, and nothing else is being served to it.
    if r.get("pending"):
        bits.append("pending — not permitted")
    bat = r.get("battery_level")
    if bat is not None:
        bits.append(f"battery {bat}%" if isinstance(bat, (int, float)) and bat <= 100
                    else f"battery {bat}")
    if r.get("audio_volume") is not None:
        bits.append(f"vol {r['audio_volume']}")
    if r.get("wifi_ssid"):
        bits.append(f"Wi-Fi {r['wifi_ssid']}")
    if r.get("mode"):
        bits.append(f"mode {r['mode']}")
    if r.get("telemetry_count"):
        bits.append(f"{r['telemetry_count']} events")
    if r.get("safety_unreviewed"):
        bits.append(f"{r['safety_unreviewed']} safety flag"
                    f"{'' if r['safety_unreviewed'] == 1 else 's'} to review")
    if r.get("ota_reboot_required"):
        bits.append("OTA reboot pending")
    return " · ".join(bits) or "connected"


def config_sources(fleet_config: Optional[dict], overrides: Optional[dict]) -> dict:
    """`{key: "robot" | "fleet"}` — which layer each effective override came from.

    The console renders it as a "set for every robot" hint next to a field, so a parent
    can tell a house rule from a per-robot exception without opening two screens. Pure:
    the layering itself lives in `moxie_sdk.cloud_config.merge_config_layers`; this only
    labels it, top-level key by top-level key (a per-robot key wins the label even when
    the layers deep-merged, because that is the field the parent last touched here)."""
    out = {k: "fleet" for k in (fleet_config or {})}
    out.update({k: "robot" for k in (overrides or {})})
    return out


def normalize_robot(r: dict) -> dict:
    """One robot record from the snapshot → the console-facing shape (live + online)."""
    return {
        "device_id": r.get("device_id"),
        "child": r.get("child"),
        "firmware": r.get("firmware"),
        # Device allowlist. A snapshot from a supervisor that predates the gate carries
        # neither key — it served everything, so it reads back as permitted.
        "permitted": bool(r.get("permitted", True)),
        "pending": bool(r.get("pending", False)),
        "permit_label": str(r.get("permit_label") or ""),
        "battery_level": _num(r.get("battery_level")),
        "audio_volume": _num(r.get("audio_volume")),
        "wifi_ssid": r.get("wifi_ssid"),
        "mode": r.get("mode"),
        "ota_reboot_required": bool(r.get("ota_reboot_required")),
        "config_overrides": dict(r.get("config_overrides") or {}),
        # fleet ⊕ per-robot, as the supervisor computed it (falls back to the per-robot
        # layer for a pre-fleet snapshot, so an older supervisor still renders).
        "config_effective": dict(r.get("config_effective")
                                 or r.get("config_overrides") or {}),
        # The face cache-buster (`child_pii.id`) the next config push will carry — "" when
        # no look is chosen. A pre-face supervisor omits it, which reads back as "default".
        "face_cache_id": str(r.get("face_cache_id") or ""),
        "telemetry_count": int(r.get("telemetry_count") or 0),
        "safety_total": int(r.get("safety_total") or 0),
        "safety_unreviewed": int(r.get("safety_unreviewed") or 0),
        "online": True,                     # present in the live snapshot ⇒ connected
        "summary": robot_summary(r),
    }


_HEX = _re.compile(r"^#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")


def _face_catalog(raw) -> list:
    """The supervisor's face catalog → render-ready rows, defensively typed.

    Kept tolerant on purpose: this crosses a process boundary, and a console that throws
    on an odd catalog row would take the whole Moxie tab down with it."""
    out = []
    for slot in (raw or []):
        if not isinstance(slot, dict) or not slot.get("id"):
            continue
        options = []
        for opt in (slot.get("options") or []):
            if not isinstance(opt, dict) or not opt.get("id"):
                continue
            row = {"id": str(opt["id"]), "label": str(opt.get("label") or opt["id"])}
            # The swatch colour is interpolated into an inline `style=` in the console, so
            # it is shape-checked here rather than trusted: a value that is not a plain
            # `#rrggbb` is dropped and the option renders as a name.
            if _HEX.match(str(opt.get("hex") or "")):
                row["hex"] = str(opt["hex"])
            options.append(row)
        out.append({"id": str(slot["id"]), "type": str(slot.get("type") or ""),
                    "label": str(slot.get("label") or slot["id"]),
                    "note": str(slot.get("note") or ""),
                    "options": options, "cited": bool(options)})
    return out


def normalize_fleet(snapshot: Optional[dict]) -> dict:
    """Supervisor status snapshot → the console fleet view. Tolerates a None/error
    snapshot (supervisor down) by returning ok=False with an empty fleet."""
    snap = snapshot or {}
    ok = bool(snap.get("ok"))
    robots = [normalize_robot(r) for r in (snap.get("robots") or [])] if ok else []
    fleet_config = dict(snap.get("fleet_config") or {}) if ok else {}
    for r in robots:
        r["config_sources"] = config_sources(fleet_config, r["config_overrides"])
    return {
        "ok": ok,
        "app": snap.get("app"),
        "uptime_s": int(snap.get("uptime_s") or 0),
        "robot_count": len(robots),
        # appliance-wide defaults every robot inherits (audit ADOPT #6) + the on-board
        # module ids a parent may schedule, so the console never copies the catalog.
        "fleet_config": fleet_config,
        # The pairing gate (audit §3.1): the appliance-wide "serve anything that
        # connects" switch as the supervisor ENFORCES it (env included), plus the robots
        # waiting for a parent to click Permit. `pending` is the console's whole to-do
        # list, so it is lifted out of `robots` rather than left to be filtered client-side.
        "allow_unverified_bots": bool(snap.get("allow_unverified_bots")) if ok else False,
        "pending": [r["device_id"] for r in robots if r["pending"]],
        "pending_count": sum(1 for r in robots if r["pending"]),
        "schedule_modules": [str(m) for m in (snap.get("schedule_modules") or [])] if ok else [],
        # The appearance catalog for the 🎨 card (audit ADOPT #9), straight from the
        # supervisor's `moxie_sdk.faces` — the console never keeps its own copy, so it
        # cannot offer a slot or an option the SDK would then reject. Empty from a
        # supervisor that predates the card, and the card simply does not render.
        "face_catalog": _face_catalog(snap.get("face_catalog")) if ok else [],
        "robots": robots,
        "recent": list(snap.get("recent") or [])[-60:],
        "error": None if ok else (snap.get("error") or "supervisor not reachable"),
    }


# --- telemetry / insights view (M6) -------------------------------------------------
# The runtime's GET /telemetry returns {ok, device_id, summary, events}; the console
# wants a sorted count table + tidy event rows. Pure, so it unit-tests here.

def event_counts(summary: Optional[dict]) -> list:
    """summary{by_event,last_seen} → render-ready rows, most frequent first
    (ties broken by name so the table doesn't jitter between refreshes)."""
    s = summary or {}
    by = s.get("by_event") or {}
    seen = s.get("last_seen") or {}
    rows = [{"event": str(k), "count": int(v or 0), "last_seen": _num(seen.get(k))}
            for k, v in by.items()]
    rows.sort(key=lambda r: (-r["count"], r["event"]))
    return rows


def normalize_event(e: Optional[dict]) -> dict:
    """One stored Packet → the console's event row (tolerates a partial packet)."""
    e = e or {}
    return {
        "event_name": str(e.get("event_name") or "event"),
        "recorded_at": _num(e.get("recorded_at")),
        "session_id": e.get("moxie_session_id") or "",
        "model": e.get("model"),
    }


def normalize_telemetry(payload: Optional[dict]) -> dict:
    """Runtime `/telemetry` response → the console insights shape. Tolerates a
    None/error payload (supervisor down, unknown device) with ok=False + an empty view."""
    p = payload or {}
    ok = bool(p.get("ok"))
    summary = p.get("summary") or {}
    return {
        "ok": ok,
        "device_id": p.get("device_id"),
        "count": int(summary.get("count") or 0) if ok else 0,
        "by_event": event_counts(summary) if ok else [],
        "events": [normalize_event(e) for e in (p.get("events") or [])] if ok else [],
        "error": None if ok else (p.get("error") or "supervisor not reachable"),
    }


# --- safety review queue (ai-seam §2 InputSafety) ------------------------------------
# The runtime's GET /safety returns {ok, device_id, policy, detail, enabled, classifier,
# counts, unreviewed, labels, events}; the console wants a sorted category table and tidy
# rows it can render without knowing the storage shape. Pure, so it unit-tests here.

def safety_counts(view: Optional[dict]) -> list:
    """counts.by_category + labels → render-ready rows, most frequent first (ties broken
    by label so the table doesn't jitter between refreshes)."""
    v = view or {}
    by = (v.get("counts") or {}).get("by_category") or {}
    labels = v.get("labels") or {}
    rows = [{"category": str(k), "label": str(labels.get(k) or k), "count": int(n or 0)}
            for k, n in by.items()]
    rows.sort(key=lambda r: (-r["count"], r["label"]))
    return rows


def normalize_safety_event(e: Optional[dict], labels: Optional[dict] = None) -> dict:
    """One stored review-queue row → the console's event row.

    `excerpt` is already redacted by the runtime (`moxie_sdk.safety.redact` masks the
    matched words and drops the excerpt entirely if masking could not be verified), and
    it is absent under LoggingPolicy NO_DATA — so this never has raw unsafe text to
    handle. It is passed through as-is; the UI escapes it.
    """
    e = e or {}
    labels = labels or {}
    cats = [str(c) for c in (e.get("categories") or [])]
    return {
        "id": str(e.get("id") or ""),
        "ts": _num(e.get("ts")),
        "side": "moxie" if e.get("side") == "moxie" else "child",
        "action": "block" if e.get("action") == "block" else "flag",
        "categories": cats,
        "labels": [str(labels.get(c) or c) for c in cats],
        "escalate": bool(e.get("escalate")),
        "excerpt": str(e.get("excerpt") or ""),
        "reviewed": bool(e.get("reviewed")),
    }


def normalize_safety(payload: Optional[dict]) -> dict:
    """Runtime `/safety` response → the console's 🛡️ Safety panel shape. Tolerates a
    None/error payload (supervisor down, unknown device) with ok=false and an empty view."""
    p = payload or {}
    ok = bool(p.get("ok"))
    labels = p.get("labels") or {}
    counts = p.get("counts") or {}
    events = [normalize_safety_event(e, labels) for e in (p.get("events") or [])] if ok else []
    return {
        "ok": ok,
        "device_id": p.get("device_id"),
        "enabled": bool(p.get("enabled")) if ok else False,
        "classifier": p.get("classifier") if ok else None,
        "policy": p.get("policy") if ok else None,
        "detail": bool(p.get("detail")) if ok else False,
        "total": int(counts.get("total") or 0) if ok else 0,
        "blocked": int((counts.get("by_action") or {}).get("block") or 0) if ok else 0,
        "flagged": int((counts.get("by_action") or {}).get("flag") or 0) if ok else 0,
        "unreviewed": int(p.get("unreviewed") or 0) if ok else 0,
        "by_category": safety_counts(p) if ok else [],
        "events": events,
        "error": None if ok else (p.get("error") or "supervisor not reachable"),
    }


# --- long-term memory: "What Moxie remembers" (audit BEYOND #4) ----------------------
# The runtime's `GET /memory` returns
# `{ok, device_id, policy, writes_allowed, bytes, namespaces:{ns:{data:{facts,…},
# provenance:[…]}}}` (moxie_sdk/store.py::MemoryStore.view). A parent does not think in
# namespaces-of-lists; they think "what does Moxie believe about my kid, when did it
# learn that, and from which activity". So this flattens each namespace into dated rows
# and counts them. Pure, so it unit-tests in the hermetic suite.
#
# Every row carries the item's **id** (what the per-item erase and the inline edit act
# on), its own provenance (stamped at merge time, so two conversations' facts in one
# activity no longer share one date), whether a parent has `pinned` it by correcting it,
# and how often a prompt has actually rendered it. A bare string is still accepted — a
# `memory.json` written before ids existed — and comes through with an empty id and the
# namespace's newest provenance as its fallback date.

#: The lists a namespace may hold, in the order a parent reads them, and the singular
#: noun the UI puts on one row. Mirrors `content/memory.py::LIST_KEYS` + `summaries`.
MEMORY_KINDS = (
    ("facts", "fact"),
    ("preferences", "preference"),
    ("open_threads", "open thread"),
    ("summaries", "summary"),
)


def memory_provenance(p: Optional[dict]) -> dict:
    """One `_provenance` entry → the fields a parent row shows (never raises)."""
    p = p if isinstance(p, dict) else {}
    return {
        "date": str(p.get("date") or ""),
        "at": _num(p.get("at")),
        "module_id": str(p.get("module_id") or ""),
        "content_id": str(p.get("content_id") or ""),
        "turns": int(_num(p.get("turns")) or 0),
        "reason": str(p.get("reason") or ""),
    }


def _memory_block(block) -> tuple:
    """One stored namespace → `(data, provenance, meta)`, for either shape it arrives in.

    The parent view wraps a namespace as `{data, provenance}`; the raw `memory.json`
    block keeps the lists at the top level with `_provenance` / `_meta` beside them.
    Accepting both means this also renders a file read straight off disk."""
    if not isinstance(block, dict):
        return {}, [], {}
    if isinstance(block.get("data"), dict) and isinstance(block.get("provenance"), list):
        data, prov = block["data"], block["provenance"]
        meta = block.get("meta") if isinstance(block.get("meta"), dict) else {}
    else:
        data = {k: v for k, v in block.items() if not str(k).startswith("_")}
        prov = block.get("_provenance")
        meta = block.get("_meta") if isinstance(block.get("_meta"), dict) else {}
    return data, [p for p in (prov or []) if isinstance(p, dict)], meta


def _memory_item(value, kind: str, fallback: dict) -> dict:
    """One remembered value → a console row.

    `{kind, text, id, pinned, use_count, last_used, provenance}`. A bare string is still
    accepted (a `memory.json` written before ids existed, read straight off disk) and
    simply has no id — the card then offers the activity-level erase and no per-item ✕,
    which is honest: without an id there is nothing for the runtime to delete."""
    prov, item_id, pinned, uses, used_at = fallback, "", False, 0, None
    if isinstance(value, dict):
        text = value.get("text") or value.get("value") or ""
        own = value.get("_provenance") or value.get("provenance")
        if isinstance(own, list):
            own = own[0] if own else None
        if isinstance(own, dict):
            prov = own
        item_id = str(value.get("id") or "")
        pinned = bool(value.get("pinned"))
        uses = int(_num(value.get("use_count")) or 0)
        used_at = _num(value.get("last_used_at"))
    else:
        text = value
    return {"kind": kind, "text": str(text), "id": item_id, "pinned": pinned,
            "use_count": uses, "last_used": used_at,
            "provenance": memory_provenance(prov)}


def _memory_sort_key(item: dict) -> tuple:
    """Newest first: the provenance timestamp, then its date string."""
    p = item.get("provenance") or {}
    return (p.get("at") if isinstance(p.get("at"), (int, float)) else 0.0,
            p.get("date") or "")


def normalize_namespace(namespace: str, block) -> dict:
    """One namespace (one activity's memory) → `{namespace, counts, items, …}`."""
    data, prov, meta = _memory_block(block)
    newest = prov[0] if prov else {}
    items, counts = [], {}
    for key, kind in MEMORY_KINDS:
        values = data.get(key)
        values = values if isinstance(values, list) else ([values] if values else [])
        rows = [_memory_item(v, kind, newest) for v in values if v not in (None, "")]
        counts[key] = len(rows)
        items.extend(rows)
    # …plus anything a module stored under a name we do not know about, so a parent is
    # never shown a count that hides rows they cannot see.
    known = {k for k, _ in MEMORY_KINDS}
    for key in sorted(k for k in data if k not in known):
        values = data[key]
        values = values if isinstance(values, list) else [values]
        rows = [_memory_item(v, str(key), newest) for v in values if v not in (None, "")]
        if rows:
            counts[str(key)] = len(rows)
            items.extend(rows)
    items.sort(key=_memory_sort_key, reverse=True)
    counts["total"] = len(items)
    through = _num(meta.get("summarized_through")) if isinstance(meta, dict) else None
    return {
        "namespace": str(namespace),
        "counts": counts,
        # the newest attribution for the namespace, so a card header can say
        # "learned 2026-09-02 from MEMORY_CHAT" without walking the rows
        "last_learned": memory_provenance(newest),
        "conversations": len(prov),
        "summarized_through": int(through) if through is not None else None,
        "items": items,
    }


def normalize_memory(raw: Optional[dict]) -> dict:
    """Runtime `/memory` response → the console's 🧠 "What Moxie remembers" shape.

    Tolerates a None/error payload (supervisor down, unknown device) with `ok:false` and
    an empty view, a partial namespace, and a bare `memory.json` dict. JSON-safe: every
    value out of here is a str/int/float/bool/list/dict.
    """
    p = raw if isinstance(raw, dict) else {}
    if "namespaces" in p or "ok" in p or "error" in p or not p:
        ok = bool(p.get("ok"))
        blocks = p.get("namespaces") if isinstance(p.get("namespaces"), dict) else {}
    else:
        ok, blocks = True, p              # a raw robots/<id>/memory.json off disk
    rows = [normalize_namespace(ns, blocks[ns])
            for ns in sorted(blocks) if not str(ns).startswith("_")] if ok else []
    rows.sort(key=lambda r: (r["last_learned"].get("at") or 0.0,
                             r["last_learned"].get("date") or ""), reverse=True)
    through = [r["summarized_through"] for r in rows if r["summarized_through"]]
    out = {
        "ok": ok,
        "device_id": p.get("device_id"),
        # the parent's privacy switch as the runtime resolved it (fleet ⊕ per-robot):
        # NO_DATA means nothing new is written, while reads and erase still work.
        "policy": p.get("policy") if ok else None,
        "writes_allowed": bool(p.get("writes_allowed", True)) if ok else False,
        "bytes": int(_num(p.get("bytes")) or 0) if ok else 0,
        "namespaces": rows,
        "namespace_count": len(rows),
        "total": sum(r["counts"]["total"] for r in rows),
        "summarized_through": max(through) if through else None,
        "error": None if ok else (p.get("error") or "supervisor not reachable"),
    }
    if "erased" in p or "edited" in p:    # an erase/edit reply carries its confirmation
        if "erased" in p:
            out["erased"] = bool(p.get("erased"))
        if "edited" in p:
            out["edited"] = bool(p.get("edited"))
        out["namespace"] = str(p.get("namespace") or "all")
        if p.get("item"):
            out["item"] = str(p.get("item"))
    return out


# --- 🎭 "Be Moxie" — puppet / telehealth mode (audit ADOPT #7) ------------------------
# The runtime's `GET /telehealth` returns `{ok, device_id, enabled, online, session_id,
# in_session, state, state_at, in_bedtime, transcript[], moods[], max_intensity}` and a
# `POST` returns the same view plus what just happened (`spoke`, `markup`, `flagged`) or a
# refusal (`error`, `reason`, `categories`). The card needs one shape for both, and it
# needs to be honest about two things the runtime is careful about: a state the robot has
# never reported is NOT "READY", and a bedtime window is a warning, not a claim that the
# line was dropped.
#
# Pure, so it unit-tests in the hermetic suite (`sim/tests/test_telehealth_view.py`).

#: `TeleHealth.RobotState`, recovered — the only state names we recognise
#: (docs/reverse-engineering/protocol/telehealth.md:36).
TELEHEALTH_STATES = ("UNKNOWN_STATE", "READY", "IN_SESSION", "EXITING")


def normalize_transcript_line(e: Optional[dict]) -> dict:
    """One `{who, text, at}` transcript entry → the console's row. Text only: no audio
    and no video path exists in this phase, by design (backlog/telehealth.md §2.5)."""
    e = e or {}
    who = "operator" if e.get("who") == "operator" else "child"
    return {"who": who, "text": str(e.get("text") or ""), "at": _num(e.get("at"))}


def normalize_telehealth(payload: Optional[dict]) -> dict:
    """Runtime `/telehealth` response → the console's 🎭 "Be Moxie" card shape.

    Tolerates a None/error payload (supervisor down, unknown device, a robot that is not
    permitted) with `ok:false` and an empty-but-renderable view — the card then shows the
    reason instead of a dead control.

    `state_known` is false for a name outside the recovered `RobotState` enum, so the card
    can show what the robot actually said without pretending to understand it; `reported`
    is false when the robot has said nothing at all, which the card renders as *"never
    reported"* rather than inventing a state.
    """
    p = payload if isinstance(payload, dict) else {}
    ok = bool(p.get("ok"))
    state = str(p.get("state") or "")
    moods = [{"id": str(m.get("id") or ""), "label": str(m.get("label") or ""),
              "value": int(_num(m.get("value")) or 0)}
             for m in (p.get("moods") or []) if isinstance(m, dict)]
    lines = [normalize_transcript_line(e) for e in (p.get("transcript") or [])
             if isinstance(e, dict)]
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
        "transcript": lines,
        "moods": moods,
        "max_intensity": int(_num(p.get("max_intensity")) or 2),
        "error": None if ok else (p.get("error") or "supervisor not reachable"),
        "reason": p.get("reason") or None,
    }
    # A write's receipt: what was said (or why nothing was), carried through so the card
    # can confirm the line rather than infer it from the transcript refreshing.
    if p.get("spoke"):
        out["spoke"] = str(p["spoke"])
    if p.get("flagged"):
        out["flagged"] = [str(c) for c in p["flagged"]]
    if p.get("blocked") or p.get("categories"):
        out["blocked"] = bool(p.get("blocked"))
        out["categories"] = [str(c) for c in (p.get("categories") or [])]
        out["labels"] = [str(c) for c in (p.get("labels") or [])]
    return out


# --- 📅 Today's plan — the recommender's "why this activity today" (audit BEYOND #7) --
# The supervisor's `GET /schedule?device_id=…` answers with the day this robot was served
# and a parallel audit trail:
#
#   {ok, device_id, day, planned_at, served,
#    schedule:{provided_schedule:[Recommendation…], chat_request, …},
#    explanations:[{module_id, slot, at, reason_codes, line, score, factors}…],
#    inputs:{child_name, bedtime, slots, parent_requests, telemetry, planned, history, …}}
#
# `explanations` is **in the same order** as `schedule.provided_schedule`
# (`docs/architecture/content-module-contract.md` §"The explanation"), which is what lets
# a name from the wire and a reason from the audit trail be joined without guessing.
#
# Three things this normalizer refuses to invent, because the card's whole promise is that
# a parent is reading the real reason:
#
#   * a **name**. `Recommendation.module_name` (RemoteChat.proto:26-34) is the only name on
#     the wire, and the planner leaves it empty for the on-board catalog. When it is empty
#     the id goes out verbatim — the plain-English table lives in the SDK, on the other
#     side of the seam, and copying it here would let the two drift into two different
#     names for one module.
#   * a **clock time**. Only the scored fill gets a slot; the authored spine (a daily
#     fixture like `DM`, an onboarding step, a `FREE_CHAT` breather) is ordered but not
#     timed, and `time_local` is None for those rather than a made-up hour.
#   * a **telemetry signal**. `inputs.telemetry.carries_module_signal` is the runtime
#     saying out loud that finish/abandon comes from the robot's `mentor_behaviors`
#     reports and not from telemetry; it is carried through, never quietly dropped.
#
# Pure, so it unit-tests in the hermetic suite (`sim/tests/test_schedule_view.py`).

def normalize_schedule_entry(expl: Optional[dict], rec: Optional[dict]) -> dict:
    """One `explanations[i]` + the `provided_schedule[i]` it explains → a card row."""
    expl = expl if isinstance(expl, dict) else {}
    rec = rec if isinstance(rec, dict) else {}
    codes = [str(c) for c in (expl.get("reason_codes") or []) if c is not None]
    module_id = str(expl.get("module_id") or rec.get("module_id") or "")
    at = expl.get("at")
    return {
        "time_local": str(at) if at else None,
        "module_id": module_id,
        # `module_name` when the template supplied one, else the id verbatim.
        "name": str(rec.get("module_name") or "") or module_id,
        "why": str(expl.get("line") or ""),
        "pinned": "parent_request" in codes,
        # No slot = the authored spine, not a scored pick: a daily fixture, an onboarding
        # step or a breather chat. Those are the rows that show "—" instead of a time.
        "fixture": expl.get("slot") is None,
        "reason_codes": codes,
    }


def normalize_schedule_view(payload: Optional[dict]) -> dict:
    """Runtime `/schedule` response → the console's 📅 Today's plan card shape.

    Tolerates anything: a None payload (supervisor down), an unknown device's `ok:false`,
    a truncated or mistyped body. It never raises — a payload it cannot read comes back as
    `{"ok": False, "error": …}` with an empty-but-renderable view, so the card shows the
    reason rather than a blank list that looks like "no plan".
    """
    empty = {"ok": False, "device_id": None, "day": "", "planned_at": "",
             "child_name": "", "served": False, "entries": [],
             "constraints": {"bedtime": {"enabled": False, "kind": ""},
                             "parent_request": {"count": 0, "pinned": []},
                             "telemetry_signal": False},
             "dropped_for_bedtime": 0, "error": "supervisor not reachable"}
    try:
        p = payload if isinstance(payload, dict) else {}
        if not p:
            return empty
        ok = bool(p.get("ok"))
        inputs = p.get("inputs") if isinstance(p.get("inputs"), dict) else {}
        sched = p.get("schedule") if isinstance(p.get("schedule"), dict) else {}
        # `or []` is not enough: a string is iterable, and one bad field must not turn
        # into a row per character.
        recs = list(sched.get("provided_schedule") or []) \
            if isinstance(sched.get("provided_schedule"), (list, tuple)) else []
        expls = list(p.get("explanations") or []) \
            if isinstance(p.get("explanations"), (list, tuple)) else []

        # Same order, by contract — but a payload that broke the contract still renders:
        # fall back to the first unused entry with the same module_id, then to nothing.
        rows, used = [], set()
        for i, expl in enumerate(expls):
            mid = (expl or {}).get("module_id") if isinstance(expl, dict) else None
            rec, hit = (recs[i] if i < len(recs) else None), i
            if not (isinstance(rec, dict) and rec.get("module_id") == mid):
                rec, hit = None, None
                for j, r in enumerate(recs):
                    if j not in used and isinstance(r, dict) and r.get("module_id") == mid:
                        rec, hit = r, j
                        break
            if hit is not None:
                used.add(hit)
            rows.append(normalize_schedule_entry(expl, rec))

        bed = inputs.get("bedtime") if isinstance(inputs.get("bedtime"), dict) else {}
        bedtime = {"enabled": bool(bed.get("enabled")), "kind": str(bed.get("kind") or "")}
        if bedtime["enabled"]:
            bedtime["starts_at"] = str(bed.get("starts_at") or "")
            bedtime["ends_at"] = str(bed.get("ends_at") or "")
        reqs = inputs.get("parent_requests")
        pinned = [{"module_id": str(r.get("module_id") or ""),
                   "at": str(r.get("at") or "")}
                  for r in (reqs if isinstance(reqs, (list, tuple)) else [])
                  if isinstance(r, dict) and r.get("due_today") and r.get("slot") is not None]
        tel = inputs.get("telemetry") if isinstance(inputs.get("telemetry"), dict) else {}
        planned = inputs.get("planned") if isinstance(inputs.get("planned"), dict) else {}

        return {
            "ok": ok,
            "device_id": p.get("device_id"),
            "day": str(p.get("day") or inputs.get("day") or ""),
            "planned_at": str(p.get("planned_at") or ""),
            "child_name": str(inputs.get("child_name") or ""),
            # False = this plan was built for the parent's read; the robot has not pulled
            # its day yet this run. The card says so instead of implying Moxie ran it.
            "served": bool(p.get("served")),
            "entries": rows,
            "constraints": {
                "bedtime": bedtime,
                "parent_request": {"count": len(pinned), "pinned": pinned},
                "telemetry_signal": bool(tel.get("carries_module_signal")),
            },
            "dropped_for_bedtime": int(_num(planned.get("dropped_for_bedtime")) or 0),
            "error": None if ok else (p.get("error") or "no plan available"),
        }
    except Exception as e:                      # a card must never be a 500
        return {**empty, "error": f"unreadable schedule payload: {e}"}
