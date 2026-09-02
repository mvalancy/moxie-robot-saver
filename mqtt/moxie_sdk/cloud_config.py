"""
Config & telemetry (config-and-telemetry-contract.md) — the robot's remotely-managed
state. Build the `/config` (RobotCloudConfig) the server pushes down, parse the
`/state` (RobotStatus) the robot reports up, and the LoggingPolicy privacy gate.

Field names verbatim from embodied/logging/Cloud.proto + enums.proto. A self-hosted
server IS the pairing key-holder, so it populates `child_pii` (ChildDecrypted) directly
and leaves the encrypted `child` empty.
"""
from __future__ import annotations
from enum import IntEnum
from typing import Optional


class LoggingPolicy(IntEnum):
    """What may leave the device — the child-privacy gate (enums.proto)."""
    NO_DATA = 0
    NO_MEDIA = 1        # everything but audio/video
    FULL = 2


class MoxieMode(IntEnum):
    DEFAULT_MODE = 0
    TELEHEALTH = 1


def child_pii_from_profile(child, face=None) -> dict:
    """A ChildDecrypted (`child_pii`) from a ChildProfile — plaintext, as the paired
    server's own config (the encryption blinds a 3rd-party cloud, not our backend).

    `face` is the child's chosen appearance (audit ADOPT #9). It rides here because that
    is where the recovered protos put it: `ChildDecrypted.face_options = 17` (a *clear*
    field beside the sealed ones — see `moxie_sdk/faces.py` for the full citation trail).
    With no face chosen the two extra keys are simply not emitted, so the document is
    byte-for-byte what it was before appearance existed."""
    pii = {"nickname": child.nickname}
    if getattr(child, "birthday_iso", None):
        pii["birthday"] = child.birthday_iso
    if face:
        from moxie_sdk.faces import face_child_id, face_options_list, validate_face
        labels = face_options_list(validate_face(face))
        if labels:
            pii["face_options"] = labels
            # The cache-buster. ASSUMPTION (field-proven, not capture-proven) —
            # `faces.py`, "the cache-buster": Unity keeps a composited face record keyed
            # on `child_pii.id`, so a face change must change the id or the robot may
            # serve the stale texture. Deterministic, so an unchanged face re-pushes the
            # same id and the robot is not disturbed by an idempotent config push.
            pii["id"] = face_child_id(labels, child_key=child.nickname)
    return pii


# --- The pairing gate: paired vs not-yet-permitted ----------------------------------
#
# `pairing_status` is the string the robot's own config handler reads out of the pushed
# RobotCloudConfig. Two values are established:
#
#   * **`"paired"`** — the operating value. `mqtt-and-conversation.md` §3.6 records it as
#     "MUST stay `paired` or robot won't run": it is the wrapper that lets the robot run a
#     session at all.
#   * **`"unpairing"`** — the *not-paired* value. Our own RE corpus does not contain a
#     capture of Embodied's cloud pushing a non-`paired` status (the recovered protos give
#     `CloudStatus.UserState` — the robot's *upward* lifecycle report, `NONE`(1) = unpaired
#     — but not the downward config string), so this one is **field-proven rather than
#     capture-proven**: OpenMoxie (MIT) offers exactly `paired` / `unpairing` in its device
#     form and reads the same string back as `MoxieDevice.is_paired()`
#     (`site/hive/models.py:53-56`, `site/hive/templates/hive/moxie.html:15-16`) — a
#     revival server that drives real robots. `ATTRIBUTION.md` credits the idea; no code
#     was copied. **ASSUMPTION, flagged in `config-and-telemetry-contract.md`**: what a
#     *physical* Moxie shows on screen for `"unpairing"` is not verified here — we have no
#     robot to observe. Changing our mind is a one-line edit of this constant.
PAIRED_PAIRING_STATUS = "paired"
UNPAIRED_PAIRING_STATUS = "unpairing"


def build_unpaired_cloud_config() -> dict:
    """The **minimal** RobotCloudConfig for a device this appliance has not permitted.

    A home appliance must not hand the child's nickname and birthday to whatever manages
    to reach the broker port, so a robot that is not on the permit list gets a document
    with *no `child_pii` at all*, the not-paired `pairing_status`, and the privacy gate
    pinned shut (`data_sharing = NO_DATA`, so nothing may be uploaded to us either). The
    `settings` wrapper stays because the robot's config handler expects the envelope
    (`mqtt-and-conversation.md` §3.6); its props carry nothing about the household — in
    particular **no `stt` prop**, so the device is never told to stream its microphone to
    us.

    This is deliberately not `build_robot_cloud_config(...)` with fields removed: a
    subtractive build is one forgotten key away from a leak, so the un-paired document is
    written out in full, here, where it can be read in one breath.
    """
    return {
        "pairing_status": UNPAIRED_PAIRING_STATUS,
        "data_sharing": LoggingPolicy.NO_DATA.name,
        "settings": {"props": {"gcp_upload_disable": "1",
                               "default_loglevel": "warning"}},
    }


def build_robot_cloud_config(child, *, audio_volume: float = 0.6,
                             screen_brightness: float = 1.0,
                             timezone_id: str = "America/Los_Angeles",
                             logging_policy: LoggingPolicy = LoggingPolicy.NO_DATA,
                             moxie_mode: MoxieMode = MoxieMode.DEFAULT_MODE,
                             privacy_mode_enabled: bool = False,
                             weekday_bedtime: Optional[tuple] = None,
                             weekend_bedtime: Optional[tuple] = None,
                             wake_button_enabled: bool = True,
                             touch_wake_enabled: bool = True,
                             audio_wake_set: str = "off",
                             alarms=None, schedule_preferences=None, face=None,
                             num_children: int = 1, max_children: int = 1,
                             last_updated_at: str = "", timestamp: int = 0) -> dict:
    """The RobotCloudConfig document (JSON) pushed on /devices/{id}/config.

    `weekday_bedtime`/`weekend_bedtime` are optional ("HH:MM","HH:MM") start/end tuples.
    `alarms` is a `WakeSchedule` (field 24) and `schedule_preferences` a
    `SchedulePreferences` (field 28) — see `normalize_wake_schedule` /
    `normalize_schedule_preferences` for the accepted parent-facing spellings; both are
    omitted from the document when empty, exactly like the other optional fields.
    `face` is the child's chosen appearance (audit ADOPT #9) — a `{slot: option}` selection
    validated by `moxie_sdk.faces.validate_face`; it renders into `child_pii.face_options`
    plus the `child_pii.id` cache-buster, and is omitted entirely when nothing is chosen.
    `pairing_status:"paired"` + `settings` are the wrapper the robot's config handler
    expects (kept from the working minimal config)."""
    cfg = {
        "pairing_status": PAIRED_PAIRING_STATUS,
        "child_pii": child_pii_from_profile(child, face),
        "audio_volume": audio_volume,
        "screen_brightness": screen_brightness,
        "timezone_id": timezone_id,
        "data_sharing": LoggingPolicy(logging_policy).name,
        "moxie_mode": MoxieMode(moxie_mode).name,
        "privacy_mode_enabled": privacy_mode_enabled,
        "wake_button_enabled": wake_button_enabled,
        "touch_wake_enabled": touch_wake_enabled,
        "audio_wake_set": audio_wake_set,
        "num_children": num_children,
        "max_children": max_children,
        "settings": {"props": {
            "touch_wake": "1" if touch_wake_enabled else "0",
            "wake_alarms": "1", "wake_button": "1" if wake_button_enabled else "0",
            "doa_range": "80", "target_all": "1", "gcp_upload_disable": "1",
            "local_stt": "on", "max_enroll": "2", "audio_wake": "1",
            "cloud_schedule_reset_threshold": "5", "brain_entrances_available": "1",
            "default_loglevel": "warning", "stt": "4",
        }},
    }
    for tag, bt in (("weekday", weekday_bedtime), ("weekend", weekend_bedtime)):
        if bt:
            cfg[f"{tag}_bedtime_enabled"] = True
            cfg[f"{tag}_bedtime_starts_at"], cfg[f"{tag}_bedtime_ends_at"] = bt[0], bt[1]
        else:
            cfg[f"{tag}_bedtime_enabled"] = False
    wake_schedule = normalize_wake_schedule(alarms)
    if wake_schedule is not None:
        cfg["alarms"] = wake_schedule                       # WakeSchedule (field 24)
    prefs = normalize_schedule_preferences(schedule_preferences)
    if prefs is not None:
        cfg["schedule_preferences"] = prefs                 # SchedulePreferences (28)
    if last_updated_at:
        cfg["last_updated_at"] = last_updated_at
    if timestamp:
        cfg["timestamp"] = timestamp
    return cfg


import re as _re

_HHMM = _re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")     # 00:00–23:59


def _bedtime(v):
    """Validate an ["HH:MM","HH:MM"] start/end pair → a list, or None to clear."""
    if v in (None, "", False):
        return None
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise ValueError("bedtime must be [\"HH:MM\", \"HH:MM\"] or null")
    a, b = str(v[0]), str(v[1])
    if not (_HHMM.match(a) and _HHMM.match(b)):
        raise ValueError(f"bad bedtime time(s): {a!r}, {b!r} (expected HH:MM)")
    return [a, b]


# --- Wake alarms & parent-scheduled activities -------------------------------------
#
# `RobotCloudConfig.alarms = 24` is an `embodied.logging.WakeSchedule`
# (Cloud.proto:113-120 · proto-catalog.md:286-291)::
#
#     WakeSchedule { repeated WakeEntry wakes = 1; bool enabled = 2; }
#     WakeSchedule.WakeEntry { repeated uint32 days = 1; string time = 2; }
#
# and `RobotCloudConfig.schedule_preferences = 28` an `embodied.logging.SchedulePreferences`
# (Cloud.proto:121-127 · proto-catalog.md:292-296)::
#
#     SchedulePreferences { repeated ParentRequest parent_requests = 1; }
#     SchedulePreferences.ParentRequest { string module_id = 1; uint64 scheduled_at = 2; }
#
# ASSUMPTIONS (the protos give the *types*, not the *encodings*, and no capture of a real
# alarms push survives in our RE corpus — flagged in config-and-telemetry-contract.md):
#   * `days` — `repeated uint32`, so 0-6. We emit **0 = Monday … 6 = Sunday**
#     (`datetime.weekday()`, the convention the rest of this repo dates by). One constant,
#     `WAKE_DAY_NAMES`, defines it: flip that tuple and every producer/validator follows.
#   * `time` — a `string` alongside the config's other wall-clock strings
#     (`weekday_bedtime_starts_at`, …), so **"HH:MM"** local time, validated by the same
#     `_HHMM` regex. The robot resolves it against `timezone_id` (`TimeZoneInfo` →
#     `UserAlarmRequest`, power-and-system-events.md "Time, timezone & alarms").
#   * `scheduled_at` — `uint64` with no stated unit. We emit **epoch seconds**, the unit
#     this repo already renders timestamps in (`Packet.recorded_at`, telemetry). A value
#     that is plainly milliseconds is divided down rather than silently accepted.

WAKE_DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday",
                  "saturday", "sunday")           # index == the `days` uint32 we emit

MAX_WAKE_ENTRIES = 14          # two a day is already generous; bounds a console POST
MAX_PARENT_REQUESTS = 16
_MS_EPOCH_FLOOR = 10 ** 11     # >= this many "seconds" is really milliseconds


def _wake_days(value) -> list:
    """A `WakeEntry.days` list → sorted unique ints 0-6 (see `WAKE_DAY_NAMES`).

    Accepts weekday names ("Monday", "mon"), ints/numeric strings 0-6, or a single one
    of either — a console sends checkboxes, an API client sends numbers."""
    if value is None or value == "" or value == []:
        raise ValueError("wake entry needs at least one day")
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError("wake entry days must be a list of weekdays")
    if len(value) > len(WAKE_DAY_NAMES):
        raise ValueError(f"too many days (max {len(WAKE_DAY_NAMES)})")
    days = set()
    for d in value:
        n = None
        if isinstance(d, bool):
            pass
        elif isinstance(d, (int, float)):
            n = int(d)
        else:
            key = str(d).strip().lower()
            if key.lstrip("+-").isdigit():
                n = int(key)
            else:
                for i, name in enumerate(WAKE_DAY_NAMES):
                    if key in (name, name[:3]):
                        n = i
                        break
        if n is None or not 0 <= n <= 6:
            raise ValueError(f"bad weekday {d!r} (expected 0-6 or a weekday name)")
        days.add(n)
    if not days:
        raise ValueError("wake entry needs at least one day")
    return sorted(days)


def normalize_wake_schedule(raw):
    """Parent input → a JSON `WakeSchedule` (`{"wakes":[{"days":[…],"time":"HH:MM"}],
    "enabled":bool}`), or None when there is nothing to schedule (the builder then omits
    the field, exactly as before this existed).

    Accepts the wire object, a bare list of entries, or a single entry. Raises ValueError
    on a bad day/time or an oversized list — the console turns that into a 400."""
    if raw is None or raw is False or raw == "" or raw == [] or raw == {}:
        return None
    enabled = True
    if isinstance(raw, dict):
        entries = raw.get("wakes", raw.get("entries"))
        if entries is None:
            entries = [raw] if ("time" in raw or "days" in raw) else []
        if "enabled" in raw:
            enabled = bool(raw["enabled"])
    elif isinstance(raw, (list, tuple)):
        entries = list(raw)
    else:
        raise ValueError("alarms must be a WakeSchedule object or a list of wake entries")
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, (list, tuple)):
        raise ValueError("alarms.wakes must be a list of wake entries")
    if len(entries) > MAX_WAKE_ENTRIES:
        raise ValueError(f"too many wake entries (max {MAX_WAKE_ENTRIES})")
    wakes = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError('each wake entry must be an object {days, time}')
        t = str(entry.get("time") or "").strip()
        if not _HHMM.match(t):
            raise ValueError(f"bad wake time {entry.get('time')!r} (expected HH:MM)")
        wakes.append({"days": _wake_days(entry.get("days")), "time": t})
    if not wakes:
        return None                     # an empty schedule clears the field
    return {"wakes": wakes, "enabled": enabled}


def schedulable_module_ids() -> tuple:
    """The module ids a parent may ask for, sorted — the **one** on-board activity
    catalog, `moxie_sdk.schedule.ONBOARD_MODULES` (imported, never copied)."""
    from moxie_sdk.schedule import ONBOARD_MODULES
    return tuple(sorted(m["module_id"] for m in ONBOARD_MODULES))


def _scheduled_at(value) -> int:
    """`ParentRequest.scheduled_at` → epoch **seconds** (uint64).

    Accepts epoch seconds (int/float/numeric string), an ISO-8601 datetime (naive is read
    as UTC — a console sends `datetime-local`), or milliseconds, which are divided down."""
    if value is None or value == "" or isinstance(value, bool):
        raise ValueError("schedule preference needs a scheduled_at")
    if isinstance(value, (int, float)):
        n = int(value)
    else:
        text = str(value).strip()
        if text.lstrip("+-").isdigit():
            n = int(text)
        else:
            import datetime as _dt
            try:
                dt = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                raise ValueError(f"bad scheduled_at {value!r} "
                                 "(epoch seconds or an ISO-8601 datetime)")
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_dt.timezone.utc)
            n = int(dt.timestamp())
    if n >= _MS_EPOCH_FLOOR:
        n //= 1000                       # milliseconds slipped in; the field is seconds
    if not 0 < n < 2 ** 63:
        raise ValueError(f"scheduled_at out of range: {value!r}")
    return n


def normalize_schedule_preferences(raw):
    """Parent input → a JSON `SchedulePreferences`
    (`{"parent_requests":[{"module_id":…,"scheduled_at":…}]}`), or None when empty.

    Accepts the wire object, a bare list of requests, or a single request. `module_id`
    must be in `schedulable_module_ids()`; `scheduled_at` is normalized to epoch seconds."""
    if raw is None or raw is False or raw == "" or raw == [] or raw == {}:
        return None
    if isinstance(raw, dict):
        items = raw.get("parent_requests", raw.get("requests"))
        if items is None:
            items = [raw] if ("module_id" in raw or "scheduled_at" in raw) else []
    elif isinstance(raw, (list, tuple)):
        items = list(raw)
    else:
        raise ValueError("schedule_preferences must be an object or a list of requests")
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, (list, tuple)):
        raise ValueError("schedule_preferences.parent_requests must be a list")
    if len(items) > MAX_PARENT_REQUESTS:
        raise ValueError(f"too many schedule preferences (max {MAX_PARENT_REQUESTS})")
    catalog = schedulable_module_ids()
    requests = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each schedule preference must be an object "
                             "{module_id, scheduled_at}")
        module_id = str(item.get("module_id") or "").strip().upper()
        if module_id not in catalog:
            raise ValueError(f"unknown module_id {item.get('module_id')!r} "
                             "(not in the on-board activity catalog)")
        requests.append({"module_id": module_id,
                         "scheduled_at": _scheduled_at(item.get("scheduled_at"))})
    if not requests:
        return None
    return {"parent_requests": requests}


# --- config layers (fleet defaults under per-robot overrides) ------------------------

def merge_config_layers(*layers) -> dict:
    """Merge override layers left→right — **later wins** — into a new dict.

    The server pushes `defaults ⊕ fleet ⊕ per-robot`: the builder's own kwarg defaults,
    then the appliance-wide config a parent set once ("house rules"), then this robot's
    own overrides. Nested **objects** merge key-by-key, so a fleet-wide
    `settings.props`/`alarms` survives a per-robot edit that only sets one of its keys;
    scalars and lists (`weekday_bedtime`, `alarms.wakes`) replace wholesale, and an
    explicit `None` clears — "no bedtime" must be expressible from the robot layer.

    Pure and side-effect free: no input dict is mutated (a merged sub-dict is a copy).

    *Credit:* the idea of a fleet-level default config layered under per-robot overrides
    is OpenMoxie's (MIT) — `models.py::HiveConfiguration` (`common_config`/
    `common_settings`) merged with the device's own in `mqtt/robot_data.py::build_config`
    via `deepmerge`. The idea is theirs; this implementation is ours. See ATTRIBUTION.md.
    """
    out: dict = {}
    for layer in layers:
        if not layer:
            continue
        if not isinstance(layer, dict):
            raise ValueError("each config layer must be an object")
        for key, value in layer.items():
            if isinstance(value, dict) and isinstance(out.get(key), dict):
                out[key] = merge_config_layers(out[key], value)
            elif isinstance(value, dict):
                out[key] = merge_config_layers(value)          # a copy, never the caller's
            else:
                out[key] = value
    return out


def sanitize_config_overrides(raw: dict) -> dict:
    """Parent-console config edit → clean, JSON-safe kwargs for build_robot_cloud_config.

    Whitelists the parent-editable fields, coerces + validates types, and drops unknown
    keys. Values stay JSON-serializable (float/int/str/bool/list) because they are stored
    in `_config_overrides` and echoed in the status snapshot — never enums. Raises
    ValueError for a known field with an invalid value (→ the endpoint returns 400)."""
    if not isinstance(raw, dict):
        raise ValueError("config overrides must be an object")
    out = {}
    if "audio_volume" in raw:
        v = float(raw["audio_volume"])
        if v > 1:                                   # accept a 0–100 percent slider
            v = v / 100.0
        out["audio_volume"] = max(0.0, min(1.0, v))
    if "screen_brightness" in raw:
        v = float(raw["screen_brightness"])
        if v > 1:
            v = v / 100.0
        out["screen_brightness"] = max(0.0, min(1.0, v))
    if raw.get("timezone_id"):
        out["timezone_id"] = str(raw["timezone_id"])
    if "logging_policy" in raw:
        lp = raw["logging_policy"]
        out["logging_policy"] = int(LoggingPolicy[lp] if isinstance(lp, str)
                                    else LoggingPolicy(lp))   # store the int value
    for b in ("privacy_mode_enabled", "wake_button_enabled", "touch_wake_enabled"):
        if b in raw:
            out[b] = bool(raw[b])
    if "audio_wake_set" in raw:
        v = str(raw["audio_wake_set"]).lower()
        if v not in ("on", "off"):
            raise ValueError("audio_wake_set must be 'on' or 'off'")
        out["audio_wake_set"] = v
    for key in ("weekday_bedtime", "weekend_bedtime"):
        if key in raw:
            bt = _bedtime(raw[key])
            out[key] = bt if bt is not None else None
    if "alarms" in raw:                                  # WakeSchedule (field 24)
        out["alarms"] = normalize_wake_schedule(raw["alarms"])
    if "schedule_preferences" in raw:                    # SchedulePreferences (field 28)
        out["schedule_preferences"] = normalize_schedule_preferences(
            raw["schedule_preferences"])
    if "face" in raw:                                    # ChildDecrypted.face_options (17)
        # A dict, so `merge_config_layers` deep-merges it **per slot**: a fleet-default
        # face ("all our robots are teal") survives a per-robot edit that only changes the
        # eyes, and a robot-layer `null` on one slot clears just that layer. An explicit
        # `face: null` from the console clears the whole selection back to the default look
        # (and with it `face_options`/`id`, so the pushed document returns to what it was).
        from moxie_sdk.faces import validate_face
        face = validate_face(raw["face"])
        out["face"] = face or None
    return out


# RobotStatus (/state) fields we surface (embodied/logging/Cloud.proto message RobotStatus)
_STATUS_FIELDS = ("embodied_robot_id", "robot_firmware_version", "android_version",
                  "battery_level", "audio_volume", "screen_brightness", "mode",
                  "wifi_ssid", "last_back_up_at", "ota_reboot_required", "mac",
                  "timestamp", "last_updated_at", "software_version")


def parse_robot_status(payload) -> dict:
    """Parse a RobotStatus JSON (from /devices/{id}/state) into the known fields.
    Tolerant: `software_version` also serves as the firmware fallback."""
    import json
    data = payload if isinstance(payload, dict) else json.loads(payload)
    out = {k: data[k] for k in _STATUS_FIELDS if k in data}
    if "robot_firmware_version" not in out and data.get("software_version"):
        out["robot_firmware_version"] = data["software_version"]
    return out
