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


def normalize_history(rows) -> list:
    """The runtime's durable daily roll-up → the console's history rows.

    One row per calendar day, oldest→newest and **zero-filled** by the runtime, so a
    quiet day renders as a quiet day rather than vanishing from the week. `share` is the
    day's count against the busiest day in the window (0.0–1.0), which is all the 📈
    card's bars need — no chart library, no second request."""
    out = []
    for r in (rows if isinstance(rows, list) else []):
        if not isinstance(r, dict) or not r.get("day"):
            continue
        # `_num` not `int()`: this is another process's JSON, and a non-numeric count
        # must render as an empty day rather than raise inside the console.
        out.append({"day": str(r["day"]), "count": max(0, int(_num(r.get("count")) or 0)),
                    "top_event": (str(r["top_event"]) if r.get("top_event") else None)})
    peak = max([r["count"] for r in out] or [0])
    for r in out:
        r["share"] = round(r["count"] / peak, 4) if peak else 0.0
    return out


def normalize_telemetry(payload: Optional[dict]) -> dict:
    """Runtime `/telemetry` response → the console insights shape. Tolerates a
    None/error payload (supervisor down, unknown device) with ok=False + an empty view.

    Since telemetry became durable (2026-09-02) this also carries what the card needs to
    be honest about its own history: `history` (the daily roll-up), `totals` (the lifetime
    count behind a sliding window, and how far back the store really reaches), `retention`
    (the caps), and `policy`/`persisted` — because under `LoggingPolicy.NO_DATA` the card
    must say "nothing is being kept" rather than show an empty week as if it were quiet.
    """
    # A non-dict body (a bare JSON string from a proxy, say) is the same kind of "the
    # other process gave us something odd" this function already promises to tolerate.
    p = payload if isinstance(payload, dict) else {}
    ok = bool(p.get("ok"))
    summary = p.get("summary") if isinstance(p.get("summary"), dict) else {}
    totals = p.get("totals") if isinstance(p.get("totals"), dict) else {}
    retention = p.get("retention") if isinstance(p.get("retention"), dict) else {}
    return {
        "ok": ok,
        "device_id": p.get("device_id"),
        "count": int(summary.get("count") or 0) if ok else 0,
        "by_event": event_counts(summary) if ok else [],
        "events": [normalize_event(e) for e in (p.get("events") or [])] if ok else [],
        "history": normalize_history(p.get("history")) if ok else [],
        "policy": str(p.get("policy") or "") if ok else "",
        # `persisted` is only ever True when the runtime said so: a payload that predates
        # durable telemetry (or an error body) must not be rendered as if it had history.
        "persisted": bool(p.get("persisted")) if ok else False,
        "connected": bool(p.get("connected")) if ok else False,
        "totals": {
            "total": int(_num(totals.get("total")) or 0),
            "days_kept": int(_num(totals.get("days_kept")) or 0),
            "first_day": totals.get("first_day") or None,
            "last_day": totals.get("last_day") or None,
            "dropped_days": int(_num(totals.get("dropped_days")) or 0),
        },
        "retention": {"packets": int(_num(retention.get("packets")) or 0),
                      "days": int(_num(retention.get("days")) or 0)},
        "error": None if ok else (p.get("error") or "supervisor not reachable"),
    }


# --- console actions: which buttons are real -----------------------------------------
# Three parent-console endpoints used to report success for things they never did:
# `POST robots/{id}/wakeup` and `POST robots/{id}/reboot` both published nothing and
# returned `{"error": null}`, and `GET robots/{id}/ota_status` returned a hard-coded
# `{"status": "up_to_date"}`. A button that lies is worse than a button that is missing,
# so each was decided from our own recovered corpus and nothing was invented:
#
#   wakeup  → REAL. `/devices/{id}/commands/wakeup` + `{"command":"wakeup"}`
#             (`docs/architecture/mqtt-and-conversation.md` §3.5, on the command-topic
#             shape `docs/reverse-engineering/protocol/cloud-protocol.md`:147 establishes).
#             It now publishes, and reports `published`, never "awake" — no
#             acknowledgement for it exists anywhere in the corpus.
#   reboot  → UNSUPPORTED. Nothing in the corpus is a cloud→robot reboot command.
#             `STATE_SILENT_REBOOT` is a value of the robot's own on-device power state
#             machine, and `ShutdownRequest`/`SystemShutdown` are listed as system status
#             events the robot *emits* on its ZMQ bus — neither establishes that a
#             cloud-injected one is honored, nor what `recover_type` would have to be.
#             Publishing a guess at a child's robot to make a button feel real is not a
#             trade we make, so it answers 501 and the console shows it as unavailable.
#   ota_status → REAL DATA, HONEST VERDICT. We know only what the robot reports up in
#             `RobotStatus` (`robot_firmware_version`, `ota_reboot_required` —
#             `config-and-telemetry-contract.md`:300) and that this appliance implements
#             no `api/ota` (`cloud-protocol.md`:45), so it can never truthfully say
#             "up_to_date". It reports the firmware the robot told us and says plainly
#             that no update server is configured.

#: Actions the console offers that our corpus does not establish a command for. Keyed by
#: the REST verb; every value carries the reason a parent reads and the evidence a
#: maintainer checks, so the "why" travels with the refusal instead of living in a doc.
UNSUPPORTED_ACTIONS = {
    "reboot": {
        "reason": "Rebooting Moxie remotely is not something this appliance can do.",
        "detail": "No cloud-to-robot reboot command has been recovered from the robot's "
                  "firmware. Turn Moxie off and on at the button instead.",
        "evidence": "docs/reverse-engineering/protocol/power-and-system-events.md — "
                    "STATE_SILENT_REBOOT is an on-device power state and "
                    "ShutdownRequest/SystemShutdown are events the robot emits, not "
                    "commands the cloud is known to be able to send.",
    },
}


def unsupported_action(name: str) -> dict:
    """The honest body for a console action we have no recovered command for.

    `ok:false` with a reason a parent can act on. Never `{"error": null}`: the console's
    old `wakeup`/`reboot` returned exactly that while publishing nothing, which is the
    bug this whole shape exists to make impossible."""
    known = UNSUPPORTED_ACTIONS.get(str(name)) or {}
    return {"ok": False, "supported": False, "action": str(name),
            "error": "unsupported",
            "reason": known.get("reason") or f"{name} is not supported.",
            "detail": known.get("detail") or "",
            "evidence": known.get("evidence") or ""}


def ota_status_view(snapshot: Optional[dict], device_id: Optional[str] = None) -> dict:
    """The honest `ota_status` body, from the supervisor's own `/status` snapshot.

    Never `"up_to_date"`. What we actually know is what the robot last reported in
    `RobotStatus` — its firmware version and whether it is holding a reboot for an
    update — and that this appliance serves no `api/ota`, so "up to date" is not a claim
    we are in a position to make about anything.

    `status` is one of:
      * `reboot_required` — the robot said `ota_reboot_required` (the one OTA fact the
        recovered protocol gives us directly);
      * `unknown` — we have the robot's live state but no update server to compare it to;
      * `unavailable` — the supervisor is down or that robot has never connected.
    """
    snap = snapshot if isinstance(snapshot, dict) else {}
    robots = [r for r in (snap.get("robots") or []) if isinstance(r, dict)]
    robot = None
    if device_id:
        robot = next((r for r in robots if r.get("device_id") == device_id), None)
    elif len(robots) == 1:
        robot = robots[0]
    note = ("This appliance runs no OTA server, so it cannot tell you whether a newer "
            "Moxie firmware exists — only what your robot last reported about itself.")
    if not snap.get("ok") or robot is None:
        return {"status": "unavailable", "version": None, "ota_reboot_required": None,
                "ota_server": False, "supported": False, "device_id": device_id,
                "reason": "No live state for this robot (it has not connected to this "
                          "appliance, or the supervisor is not running).", "note": note}
    pending = bool(robot.get("ota_reboot_required"))
    return {
        "status": "reboot_required" if pending else "unknown",
        # `version` keeps the original API's field name and now carries a real value: the
        # firmware string the robot reported, not None.
        "version": robot.get("firmware") or None,
        "ota_reboot_required": pending,
        "ota_server": False, "supported": False,
        "device_id": robot.get("device_id"),
        "reason": ("Moxie is holding a reboot to finish an update it already downloaded."
                   if pending else
                   "Moxie's reported firmware is below; whether that is the newest build "
                   "is not something this appliance can know."),
        "note": note,
    }


def resolve_device_id(robot_attrs: Optional[dict],
                      snapshot: Optional[dict]) -> tuple:
    """`(device_id, how)` — the MQTT identity behind a parent-app robot record.

    The two halves of this system learn a robot's identity at different moments and the
    gap is real, not an oversight: the pairing QR carries Wi-Fi and a pairing seed but no
    device id, so `POST pairing-complete` mints a record id (`rid`) while the robot's
    MQTT client id (`d_<uuid>`) only exists once it reaches the broker. A command has to
    be addressed to the latter.

    Resolution, most trustworthy first:
      * `"record"`      — the record itself carries `mqtt-device-id` (written at
                          pair-complete when the console knew it);
      * `"sole-served"` — the supervisor serves exactly one permitted robot, so there is
                          no ambiguity about who the button means. Same inference the
                          console's own live panel makes (`app.js`: `served[0]`);
      * `None, "ambiguous"` / `None, "none"` — several robots or no robot. The caller
                          must say so rather than pick one.
    """
    attrs = robot_attrs if isinstance(robot_attrs, dict) else {}
    stored = str(attrs.get("mqtt-device-id") or "").strip()
    if stored:
        return stored, "record"
    snap = snapshot if isinstance(snapshot, dict) else {}
    served = [r for r in (snap.get("robots") or [])
              if isinstance(r, dict) and r.get("device_id") and not r.get("pending")]
    if len(served) == 1:
        return str(served[0]["device_id"]), "sole-served"
    return None, ("ambiguous" if served else "none")


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


# --- 🎚️ the voice picker (docs/architecture/backlog/voice-picker.md) -----------------
#: The two sides of the picker, named once (mirrors `moxie_sdk.voice_settings.KINDS` —
#: this module stays dependency-free of the supervisor package, so it re-states the two
#: strings rather than importing them across the process boundary).
VOICE_KINDS = ("speech", "listening")


def normalize_voice_option(entry: Optional[dict]) -> dict:
    """One dropdown `<option>`: `{id, label, group, engine, model, default}`."""
    e = entry if isinstance(entry, dict) else {}
    return {"id": str(e.get("id") or ""), "label": str(e.get("label") or ""),
            "group": str(e.get("group") or ""), "engine": str(e.get("engine") or ""),
            "model": str(e.get("model") or ""), "default": bool(e.get("default"))}


def normalize_voice(payload: Optional[dict]) -> dict:
    """Runtime `/voice` (or `/voice/test`) response → the console's 🎚️ card shape.

    Tolerates anything: a None payload (supervisor down), a refusal, a truncated body. It
    never raises and it never returns *empty* lists silently — an unreadable payload comes
    back with `error` set so the card prints the reason instead of two blank dropdowns
    that look like "this appliance cannot speak".

    A `POST` response is the same shape plus `applied` / the test's `spoke`, so one
    normalizer serves all three routes and the card re-renders from whatever came back.
    """
    empty = {"ok": False, "available": {k: [] for k in VOICE_KINDS},
             "selected": {k: "" for k in VOICE_KINDS},
             "labels": {k: "" for k in VOICE_KINDS},
             "installed": {k: "" for k in VOICE_KINDS},
             "chosen": {k: False for k in VOICE_KINDS},
             "discovering": False, "gateway_error": "", "updated_at": 0,
             "robots": [], "applied": None, "spoke": "", "reason": "",
             "error": "supervisor not reachable"}
    try:
        p = payload if isinstance(payload, dict) else {}
        if not p:
            return empty
        avail = p.get("available") if isinstance(p.get("available"), dict) else {}
        options = {}
        for kind in VOICE_KINDS:
            raw = avail.get(kind)
            options[kind] = [normalize_voice_option(e)
                             for e in (raw if isinstance(raw, (list, tuple)) else [])
                             if isinstance(e, dict) and e.get("id")]
        ok = bool(p.get("ok"))

        def _side(field, cast):
            src = p.get(field) if isinstance(p.get(field), dict) else {}
            return {k: cast(src.get(k)) for k in VOICE_KINDS}

        applied = p.get("applied") if isinstance(p.get("applied"), dict) else None
        robots = p.get("robots")
        return {
            "ok": ok,
            "available": options,
            "selected": _side("selected", lambda v: str(v or "")),
            "labels": _side("labels", lambda v: str(v or "")),
            "installed": _side("installed", lambda v: str(v or "")),
            "chosen": _side("chosen", bool),
            # True only until the first listing lands; the card says "Discovering…" and
            # keeps whatever entries it already has rather than blanking.
            "discovering": bool(p.get("discovering")),
            # The exception CLASS the supervisor saw, e.g. "APIConnectionError". Present
            # AND `ok` is normal: the local options still work.
            "gateway_error": str(p.get("gateway_error") or ""),
            "updated_at": int(_num(p.get("updated_at")) or 0),
            "robots": [str(r) for r in robots] if isinstance(robots, (list, tuple)) else [],
            "applied": applied,
            "spoke": str(p.get("spoke") or ""),
            "reason": str(p.get("reason") or ""),
            "error": None if ok else (p.get("error") or p.get("reason")
                                      or "no voice settings available"),
        }
    except Exception as e:                      # a card must never be a 500
        return {**empty, "error": f"unreadable voice payload: {e}"}


# --- 📦 content packs (docs/architecture/backlog/content-packs.md) --------------------
# Content stops being a file in our repository: a pack is one JSON file a parent can be
# handed, review before it changes anything, and undo afterwards. These three normalizers
# keep the same defensive contract as `normalize_schedule_view` — they never raise, and a
# payload they cannot read renders as `{ok: False, error: …}` with an empty-but-renderable
# view, so the card shows the REASON rather than a blank list that reads as "no content".
#
# Dependency-free of the supervisor package on purpose: this module is imported by the
# console process, which does not have `mqtt/` on its path, so the review states are
# re-stated here rather than imported from `moxie_sdk.content.packs`. `test_fleet.py` and
# the console round trip diff these shapes against the real runtime.

#: The review states, in the order the card sorts them: things to do first, then noise.
CONTENT_STATES = ("conflict", "downgrade_conflict", "new", "upgrade", "fork",
                  "downgrade", "keep_local", "same", "invalid")

#: The three decisions a parent can make per row in the 📦 review table.
CONTENT_DECISIONS = ("accept", "keep", "skip")


def normalize_content_item(entry: Optional[dict]) -> dict:
    """One inventory row: what it is, where it came from, and what to warn about."""
    e = entry if isinstance(entry, dict) else {}
    warnings = e.get("warnings")
    pii = e.get("pii")
    return {
        "id": str(e.get("id") or ""),
        "kind": str(e.get("kind") or ""),
        "key": str(e.get("key") or ""),
        "name": str(e.get("name") or e.get("key") or ""),
        "source_version": int(_num(e.get("source_version")) or 1),
        "origin": str(e.get("origin") or ""),
        "pack_id": str(e.get("pack_id") or ""),
        "imported_at": int(_num(e.get("imported_at")) or 0),
        "local_edited": bool(e.get("local_edited")),
        "has_code": bool(e.get("has_code")),
        "warnings": [str(w) for w in warnings] if isinstance(warnings, (list, tuple)) else [],
        "pii": [{"field": str((h or {}).get("field") or ""),
                 "name": str((h or {}).get("name") or "")}
                for h in pii if isinstance(h, dict)] if isinstance(pii, (list, tuple)) else [],
    }


def normalize_content_view(payload: Optional[dict]) -> dict:
    """Runtime `GET /content` → the 📦 card's shape: inventory, ledger, undo."""
    empty = {"ok": False, "items": [], "packs": [], "counts": {},
             "undo_available": False, "undo_label": "", "max_bytes": 0,
             "pack_format": 0, "error": "supervisor not reachable"}
    try:
        p = payload if isinstance(payload, dict) else {}
        if not p:
            return empty
        raw_items = p.get("items")
        raw_packs = p.get("packs")
        counts = p.get("counts") if isinstance(p.get("counts"), dict) else {}
        ok = bool(p.get("ok"))
        return {
            "ok": ok,
            "items": [normalize_content_item(i)
                      for i in (raw_items if isinstance(raw_items, (list, tuple)) else [])
                      if isinstance(i, dict)],
            "packs": [normalize_pack_row(r)
                      for r in (raw_packs if isinstance(raw_packs, (list, tuple)) else [])
                      if isinstance(r, dict)],
            "counts": {str(k): int(_num(v) or 0) for k, v in counts.items()},
            "undo_available": bool(p.get("undo_available")),
            "undo_label": str(p.get("undo_label") or ""),
            "max_bytes": int(_num(p.get("max_bytes")) or 0),
            "pack_format": int(_num(p.get("pack_format")) or 0),
            "error": None if ok else (p.get("error") or p.get("reason")
                                      or "no content available"),
        }
    except Exception as e:                      # a card must never be a 500
        return {**empty, "error": f"unreadable content payload: {e}"}


def normalize_pack_row(entry: Optional[dict]) -> dict:
    """One row of the installed-packs ledger."""
    e = entry if isinstance(entry, dict) else {}
    return {"id": str(e.get("id") or ""), "name": str(e.get("name") or ""),
            "details": str(e.get("details") or ""), "author": str(e.get("author") or ""),
            "pack_version": int(_num(e.get("pack_version")) or 1),
            "digest": str(e.get("digest") or ""),
            "imported_at": int(_num(e.get("imported_at")) or 0),
            "item_count": int(_num(e.get("item_count")) or 0)}


def normalize_content_row(entry: Optional[dict]) -> dict:
    """One review row: the state, the pre-set decision, the diff and the warnings.

    `decision` is what the card's radio group starts on — `accept` for the two states the
    runtime pre-ticks, `keep` for anything that would replace a local edit (so the safe
    choice is the one already selected), `skip` otherwise. A row the runtime marked
    `invalid` cannot be accepted at all, and the card renders it disabled with its reason.
    """
    e = entry if isinstance(entry, dict) else {}
    state = str(e.get("state") or "")
    diff = e.get("diff")
    warnings, reasons = e.get("warnings"), e.get("reasons")
    default = bool(e.get("default"))
    if state == "invalid":
        decision = "skip"
    elif default:
        decision = "accept"
    elif bool(e.get("local_edited")):
        decision = "keep"
    else:
        decision = "skip"
    installed = e.get("installed_version")
    return {
        "id": str(e.get("id") or ""),
        "kind": str(e.get("kind") or ""),
        "key": str(e.get("key") or ""),
        "name": str(e.get("name") or e.get("key") or ""),
        "state": state if state in CONTENT_STATES else (state or "unknown"),
        "label": str(e.get("label") or ""),
        "default": default,
        "decision": decision,
        "installable": state != "invalid",
        "local_edited": bool(e.get("local_edited")),
        "source_version": int(_num(e.get("source_version")) or 1),
        "installed_version": (None if installed is None
                              else int(_num(installed) or 0)),
        "origin": str(e.get("origin") or ""),
        "pack_id": str(e.get("pack_id") or ""),
        "warnings": [str(w) for w in warnings] if isinstance(warnings, (list, tuple)) else [],
        "reasons": [str(r) for r in reasons] if isinstance(reasons, (list, tuple)) else [],
        "diff": [normalize_content_diff(d)
                 for d in (diff if isinstance(diff, (list, tuple)) else [])
                 if isinstance(d, dict)],
    }


def normalize_content_diff(entry: Optional[dict]) -> dict:
    """One field-level difference — a unified diff for prose, `old → new` for a scalar."""
    e = entry if isinstance(entry, dict) else {}
    lines = e.get("diff")
    return {"field": str(e.get("field") or ""),
            "kind": str(e.get("kind") or "scalar"),
            "old": str(e.get("old") or ""), "new": str(e.get("new") or ""),
            "diff": [str(ln) for ln in lines] if isinstance(lines, (list, tuple)) else []}


def normalize_content_review(payload: Optional[dict]) -> dict:
    """Runtime `POST /content/review` → the review table.

    `digest` is the honest one: `ok`, `mismatch` (the file was changed after it was
    exported — nothing is pre-selected) or `absent` (hand-written, flagged not refused).
    """
    empty = {"ok": False, "pack": {}, "digest": "", "expect_digest": "", "items": [],
             "accept": [], "counts": {}, "warnings": [],
             "error": "supervisor not reachable"}
    try:
        p = payload if isinstance(payload, dict) else {}
        if not p:
            return empty
        raw_items = p.get("items")
        accept = p.get("accept")
        counts = p.get("counts") if isinstance(p.get("counts"), dict) else {}
        warnings = p.get("warnings")
        ok = bool(p.get("ok"))
        return {
            "ok": ok,
            "pack": normalize_pack_row(p.get("pack")),
            "digest": str(p.get("digest") or ""),
            "expect_digest": str(p.get("expect_digest") or ""),
            "items": [normalize_content_row(i)
                      for i in (raw_items if isinstance(raw_items, (list, tuple)) else [])
                      if isinstance(i, dict)],
            "accept": [str(a) for a in accept] if isinstance(accept, (list, tuple)) else [],
            "counts": {str(k): int(_num(v) or 0) for k, v in counts.items()},
            "warnings": [str(w) for w in warnings]
                        if isinstance(warnings, (list, tuple)) else [],
            "error": None if ok else (p.get("error") or p.get("reason")
                                      or "this file could not be read as a content pack"),
        }
    except Exception as e:                      # a card must never be a 500
        return {**empty, "error": f"unreadable review payload: {e}"}


def normalize_content_result(payload: Optional[dict]) -> dict:
    """Runtime `POST /content/import` or `/content/undo` → what actually happened.

    `conflict` is the 409 case — the file changed between the review and the import — and
    the card says so rather than reporting a failure it cannot explain.
    """
    empty = {"ok": False, "applied": [], "replaced": [], "skipped": [], "count": 0,
             "restored": 0, "conflict": False, "undo_available": False, "pack": {},
             "reload": {}, "label": "", "error": "supervisor not reachable"}
    try:
        p = payload if isinstance(payload, dict) else {}
        if not p:
            return empty

        def _ids(field):
            v = p.get(field)
            return [str(x) for x in v] if isinstance(v, (list, tuple)) else []

        reload = p.get("reload") if isinstance(p.get("reload"), dict) else {}
        ok = bool(p.get("ok"))
        return {
            "ok": ok,
            "applied": _ids("applied"), "replaced": _ids("replaced"),
            "skipped": _ids("skipped"),
            "count": int(_num(p.get("count")) or 0),
            "restored": int(_num(p.get("restored")) or 0),
            "conflict": bool(p.get("conflict")),
            "undo_available": bool(p.get("undo_available")),
            "pack": normalize_pack_row(p.get("pack")),
            "reload": {str(k): (v if isinstance(v, bool) else int(_num(v) or 0))
                       for k, v in reload.items()},
            "label": str(p.get("label") or ""),
            "error": None if ok else (p.get("reason") or p.get("error")
                                      or "the import did not go through"),
        }
    except Exception as e:                      # a card must never be a 500
        return {**empty, "error": f"unreadable import payload: {e}"}
