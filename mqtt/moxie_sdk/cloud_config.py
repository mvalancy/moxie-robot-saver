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

from . import brains


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

    `face` is the child's chosen appearance (audit ADOPT #9), carried in
    `ChildDecrypted.face_options = 17` (see `moxie_sdk/faces.py`). With no face the two
    extra keys are not emitted."""
    pii = {"nickname": child.nickname}
    if getattr(child, "birthday_iso", None):
        pii["birthday"] = child.birthday_iso
    if face:
        from moxie_sdk.faces import face_child_id, face_options_list, validate_face
        labels = face_options_list(validate_face(face))
        if labels:
            pii["face_options"] = labels
            # The cache-buster (ASSUMPTION, field-proven — see `faces.py`): the robot keys
            # its composited face on `child_pii.id`. Deterministic, so an unchanged face
            # re-pushes the same id.
            pii["id"] = face_child_id(labels, child_key=child.nickname)
    return pii


# --- The pairing gate: paired vs not-yet-permitted ----------------------------------
#
# `pairing_status` is read by the robot's config handler:
#   * **`"paired"`** — "MUST stay `paired` or robot won't run" (mqtt-and-conversation.md §3.6).
#   * **`"unpairing"`** — the not-paired value. Field-proven, not capture-proven: OpenMoxie
#     (MIT) uses exactly `paired` / `unpairing` (`site/hive/models.py:53-56`); no code was
#     copied. ASSUMPTION (config-and-telemetry-contract.md): what a physical Moxie shows
#     for it is unverified.
PAIRED_PAIRING_STATUS = "paired"
UNPAIRED_PAIRING_STATUS = "unpairing"


def build_unpaired_cloud_config() -> dict:
    """The **minimal** RobotCloudConfig for a device this appliance has not permitted.

    No `child_pii`, the not-paired `pairing_status`, `data_sharing = NO_DATA`, and a
    `settings` envelope with nothing about the household — notably **no `stt` prop**, so
    the device is never told to stream its microphone to us. Written out in full rather
    than subtracted from the paired build, which would be one forgotten key from a leak.
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

    `weekday_bedtime`/`weekend_bedtime` are optional ("HH:MM","HH:MM") tuples. `alarms`
    (`WakeSchedule`, field 24) and `schedule_preferences` (field 28) are normalized by
    `normalize_wake_schedule` / `normalize_schedule_preferences` and omitted when empty.
    `face` renders into `child_pii`. `pairing_status:"paired"` + `settings` are the
    wrapper the robot's config handler expects."""
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
# ASSUMPTIONS (the protos give types, not encodings; no capture survives — flagged in
# config-and-telemetry-contract.md):
#   * `days` — 0 = Monday … 6 = Sunday (`datetime.weekday()`), defined by `WAKE_DAY_NAMES`.
#   * `time` — "HH:MM" local time like the other wall-clock strings; the robot resolves it
#     against `timezone_id`.
#   * `scheduled_at` — epoch **seconds**; a value plainly in milliseconds is divided down.

WAKE_DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday",
                  "saturday", "sunday")           # index == the `days` uint32 we emit


def in_bedtime(cfg, now_local) -> bool:
    """Is this *effective* config inside its bedtime window at this local wall-clock time?

    Pure: `cfg` is the override stack, `now_local` a naive local `datetime`. Uses
    `weekday_bedtime` / `weekend_bedtime`; wraps midnight; `start == end` means no window.
    Shared by the greeting rule (stays quiet) and the telehealth card (warns).
    """
    if not isinstance(cfg, dict):
        return False
    window = cfg.get("weekend_bedtime" if now_local.weekday() >= 5 else "weekday_bedtime")
    if not isinstance(window, (list, tuple)) or len(window) != 2:
        return False
    start, end = str(window[0]), str(window[1])
    if start == end:
        return False
    cur = now_local.strftime("%H:%M")
    return (start <= cur < end) if start < end else (cur >= start or cur < end)

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

    The server pushes `defaults ⊕ fleet ⊕ per-robot`. Nested **objects** merge
    key-by-key; scalars and lists replace wholesale; an explicit `None` clears ("no
    bedtime" must be expressible). No input is mutated.

    *Credit:* fleet defaults under per-robot overrides is OpenMoxie's (MIT) idea
    (`HiveConfiguration` + `build_config`); this implementation is ours. See ATTRIBUTION.md.
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


# --- the child's name: the parent app's record, per robot ----------------------------
#
# The name Moxie says comes from the parent's account (`server/moxie_server/child_profile.py`
# posts it as `child: {nickname, birthday?}` on the robot's own layer); the appliance's
# profile (`MOXIE_CHILD_NICKNAME`) is the fallback when no account names the child. It is
# read into every brain prompt, pushed as `child_pii` and SPOKEN (the hello, the opener),
# so it is checked by ONE rule, `check_name`, which the Try it card uses for a name typed
# for a try as well (`tryit.py` imports it): the shape, then Moxie's safety table.

#: The longest name, counted once whitespace runs are collapsed (NFC code points).
NAME_MAX_CHARS = 40
#: Unicode letters and digits, spaces, periods, apostrophes, hyphens: no tags, no braces.
#: The shape of a name once the combining marks its letters carry are set aside
#: (`_name_shape_ok`).
NAME_RE = _re.compile(r"^[\w .'\-]+$")
#: Every line boundary `str.splitlines` knows: a name is one line.
_LINE_BREAK = _re.compile(r"[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")
_YMD = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: The words a refusal uses. Never the name itself: a refusal is echoed to the console.
NAME_RULE = (f"A child's name is one line of up to {NAME_MAX_CHARS} letters (with their "
             f"accents or vowel signs), digits, spaces, periods, apostrophes or hyphens.")
#: The refusal when Moxie's safety table cannot be read: no name can be checked, so none
#: is taken (and a saved one is not said) until it can.
NAME_UNCHECKED = ("Moxie's safety rules could not be read on this server, so no name can be "
                  "checked now and Moxie says its default name. Fix the rules file "
                  "(MOXIE_SAFETY_RULES), then save the name again.")


def _name_shape_ok(name: str) -> bool:
    r"""`NAME_RE` over `name` with the combining marks its letters carry set aside.

    A mark (`\p{M}`: an accent left decomposed, a Devanagari vowel sign or virama, a Thai
    tone mark) is allowed after a letter, or after another mark that follows one, so the
    scripts that write vowels as marks pass. A mark that starts the name, or follows a
    digit, a space or punctuation, refuses it."""
    import unicodedata
    base, after_letter = [], False
    for ch in name:
        kind = unicodedata.category(ch)[0]
        if kind == "M":
            if not after_letter:
                return False
            continue
        after_letter = kind == "L"
        base.append(ch)
    return NAME_RE.fullmatch("".join(base)) is not None


def _name_safety_refusal(name: str) -> str:
    """`""` when Moxie's safety table finds nothing in `name`, else the sentence a refusal
    says (the categories by their labels, never the name).

    The table is the one the runtime's own classifier reads (`moxie_sdk/safety.py` over
    `safety_rules.json`, or `MOXIE_SAFETY_RULES`), on the CHILD's side, where every
    category the table has either blocks or flags. A name the table merely flags (a
    single profanity, say) is refused as well: Moxie says the name over and over, and a
    flag is "let through once and tell a parent", not "say it every hello". A table that
    cannot be read refuses every name (`NAME_UNCHECKED`)."""
    from . import safety
    try:
        classifier = safety.default_classifier()
        verdict = classifier.assess(name, role=safety.CHILD)
    except Exception:                    # noqa: BLE001 \u2014 a table that cannot be read
        return NAME_UNCHECKED
    if not verdict:
        return ""
    labels = safety.category_labels(classifier)
    named = ", ".join(str(labels.get(c) or c) for c in verdict.categories)
    return (f"Moxie will not say that name: Moxie's safety rules flag it ({named}). "
            f"Please choose another name or a nickname.")


def check_name(name: str, *, refusal: str = NAME_RULE) -> str:
    """The one name rule, for a child's name (`clean_child_name`) and for a name typed on
    the Try it card (`tryit._try_name`) alike, so the two can never drift apart.

    `name` is one line with its whitespace already collapsed. It is NFC-normalized first
    (a decomposed `Jos\u00e9` is the same name as a composed one, and is kept composed), then
    it is at most `NAME_MAX_CHARS` characters of the shape `_name_shape_ok` checks, then
    nothing Moxie's safety table blocks or flags (`_name_safety_refusal`). Returns the NFC
    name, else ValueError: `refusal` for the shape, the table's sentence for safety;
    neither repeats the name, since a refusal is echoed to the console."""
    import unicodedata
    name = unicodedata.normalize("NFC", name)
    if len(name) > NAME_MAX_CHARS or not _name_shape_ok(name):
        raise ValueError(refusal)
    why = _name_safety_refusal(name)
    if why:
        raise ValueError(why)
    return name


def clean_child_name(raw) -> str:
    """The child's name as Moxie will say it, or ValueError (the console answers 400).

    Whitespace runs collapse to one space and the ends are trimmed, then the one name rule
    applies (`check_name`: NFC, the shape, Moxie's safety table). Stricter than a try twice
    over: a name is required (a try may leave it blank), and a line break is refused rather
    than folded into a space."""
    if not isinstance(raw, str):
        raise ValueError(f"The child's nickname must be text. {NAME_RULE}")
    if _LINE_BREAK.search(raw):
        raise ValueError(f"The child's nickname has a line break. {NAME_RULE}")
    name = " ".join(raw.split())
    if not name:
        raise ValueError("The child's nickname is empty: send a name, or null to clear it.")
    return check_name(name)


# --- the name kept out of the supervisor's log and activity feed ------------------------

#: What the supervisor's log and activity feed show where a child's name was said.
CHILD_MASK = "[child]"
#: What Moxie calls a child nobody named (`ChildProfile`'s default, `pick_greeting`'s
#: stand-in): a word, not a name, so it is never masked.
GENERIC_CHILD_NAME = "friend"
#: The accents a speech engine or a model may drop from a name (Combining Diacritical Marks).
_LATIN_ACCENTS = _re.compile("[\u0300-\u036f]")


def _name_forms(name: str) -> set:
    """The spellings of `name` a line may carry: the whole name, each part of it of two
    characters or more (split at spaces, periods, apostrophes and hyphens: `Mary-Kate`
    is also `Mary` and `Kate`), and each of those without its Latin accents (`Jos\u00e9` is
    also `Jose`). NFC; the generic `friend` is never one."""
    import unicodedata
    whole = unicodedata.normalize("NFC", " ".join(str(name).split()))
    forms = {whole} | {p for p in _re.split(r"[ .'\-]+", whole) if len(p) >= 2}
    bare = {unicodedata.normalize(
        "NFC", _LATIN_ACCENTS.sub("", unicodedata.normalize("NFD", f))) for f in forms}
    return {f for f in forms | bare
            if f.strip() and f.casefold() != GENERIC_CHILD_NAME}


import functools as _functools


@_functools.lru_cache(maxsize=32)
def _mask_pattern(names: tuple):
    forms = set()
    for name in names:
        forms |= _name_forms(name)
    if not forms:
        return None
    # Longest first, so `Mary-Kate` is masked whole before `Mary` could split it.
    return _re.compile(r"(?<!\w)(?:%s)(?!\w)" % "|".join(
        map(_re.escape, sorted(forms, key=lambda f: (-len(f), f)))), _re.IGNORECASE)


def mask_child_names(text, names) -> str:
    """`text` as the supervisor's log and activity feed may keep it: every one of `names`
    (and every spelling `_name_forms` gives it), standing as a word in any case, replaced
    by `CHILD_MASK`. The text is NFC-normalized first. Pure. Only for copies kept for
    people to read: what a robot hears is never passed through this."""
    import unicodedata
    text = unicodedata.normalize("NFC", "" if text is None else str(text))
    keys = tuple(sorted({n for n in names if isinstance(n, str) and n.strip()}))
    rx = _mask_pattern(keys) if keys else None
    return rx.sub(CHILD_MASK, text) if rx is not None else text


def _child(value):
    """`{"nickname", "birthday"?}` → a clean copy, or None to clear. Other keys (pronouns,
    notes) are dropped: nothing in the parent app collects them."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('child must be {"nickname": "…", "birthday": "YYYY-MM-DD"} '
                         'or null')
    out = {"nickname": clean_child_name(value.get("nickname"))}
    birthday = value.get("birthday")
    if birthday not in (None, ""):
        import datetime as _dt
        try:
            if not (isinstance(birthday, str) and _YMD.match(birthday)):
                raise ValueError
            _dt.date.fromisoformat(birthday)
        except ValueError:
            raise ValueError("The child's birthday must be a date, YYYY-MM-DD.") from None
        out["birthday"] = birthday
    return out


def child_profile_for(layer, default):
    """The `ChildProfile` a robot's config, brains, hello, `/status` and day plan use: the
    parent's record from this robot's own layer (`child`), else `default` (the appliance's
    profile, returned as is). Pure. A value the whitelist would refuse (a hand-edited
    record) is ignored, never spoken."""
    record = layer.get("child") if isinstance(layer, dict) else None
    if not isinstance(record, dict):
        return default
    try:
        clean = _child(record)
    except ValueError:
        return default
    import dataclasses
    return dataclasses.replace(default, nickname=clean["nickname"],
                               birthday_iso=clean.get("birthday"))


def sanitize_config_overrides(raw: dict) -> dict:
    """Parent-console config edit → clean, JSON-safe kwargs for build_robot_cloud_config.

    Whitelists parent-editable fields, validates them, drops unknown keys, and keeps values
    JSON-serializable (stored and echoed; never enums). Raises ValueError (→ 400)."""
    if not isinstance(raw, dict):
        raise ValueError("config overrides must be an object")
    out = {}
    for key in ("audio_volume", "screen_brightness"):
        if key in raw:
            v = float(raw[key])
            if v > 1:                               # accept a 0–100 percent slider
                v = v / 100.0
            out[key] = max(0.0, min(1.0, v))
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
            out[key] = _bedtime(raw[key])
    if "alarms" in raw:                                  # WakeSchedule (field 24)
        out["alarms"] = normalize_wake_schedule(raw["alarms"])
    if "schedule_preferences" in raw:                    # SchedulePreferences (field 28)
        out["schedule_preferences"] = normalize_schedule_preferences(
            raw["schedule_preferences"])
    if "face" in raw:                                    # ChildDecrypted.face_options (17)
        # A dict, so layers deep-merge per slot; `face: null` clears the whole selection.
        from moxie_sdk.faces import validate_face
        out["face"] = validate_face(raw["face"]) or None
    if brains.CONFIG_KEY in raw:                         # which brain answers this child
        # A scalar (a per-robot pick replaces the house rule; `null` clears the layer).
        # Validated against the positive list here; the env pin is enforced by the runtime.
        value = raw[brains.CONFIG_KEY]
        if value is None or (isinstance(value, str) and not value.strip()):
            out[brains.CONFIG_KEY] = None
        else:
            name = brains.sanitize_brain(value)
            if not name:
                raise ValueError(f"{str(value)!r} is not a brain this appliance knows. "
                                 f"Choose one of: {brains.offered()}.")
            out[brains.CONFIG_KEY] = name
    if "child" in raw:                                   # ChildDecrypted.nickname (3)
        # The parent's record for this robot's child (`child_profile_for`); `null` clears it.
        out["child"] = _child(raw["child"])
    return out


#: Config keys that ride the config layers but are never builder kwargs
#: (`build_robot_cloud_config` would raise `TypeError` on them): `brain` is the server's
#: business alone; `child` reaches the robot as `child_pii`, through `child_profile_for`.
SERVER_ONLY_KEYS = (brains.CONFIG_KEY, "child")


def robot_config_kwargs(cfg) -> dict:
    """`cfg` minus the keys that never travel to a robot (`SERVER_ONLY_KEYS`). Safe as a
    subtraction because the document itself is built from a kwarg whitelist."""
    if not isinstance(cfg, dict):
        return {}
    return {k: v for k, v in cfg.items() if k not in SERVER_ONLY_KEYS}


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
