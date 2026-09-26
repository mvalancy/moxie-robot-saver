"""Pack format constants, the per-kind field allowlist, canonical digests, and item
normalize/key/validate. What may leave this appliance, spelled out."""
from __future__ import annotations

import hashlib
import json
import re

from ..module import Conversation, Global, Schedule
from .. import ext


#: Reader contract: an unknown format number is refused readably, never half-read.
PACK_FORMAT = 1

#: The three exportable kinds, in the order they are written and reviewed.
KINDS = ("conversation", "global", "schedule")

#: Longest regex a pack may carry. Stdlib regexes have no timeout, so a pathological
#: pattern can still stall matching (R3); a length cap + compile check is the P0 bound.
MAX_PATTERN_CHARS = 512

#: Default body cap for the HTTP layer (`MOXIE_PACK_MAX_BYTES`); 1 MiB.
DEFAULT_MAX_BYTES = 1024 * 1024

#: What an exporter stamps into `generator`. Free text, never trusted on read.
GENERATOR = "moxie-cloud"

# --------------------------------------------------------------------------- #
# The allowlist — what may leave this appliance, spelled out
# --------------------------------------------------------------------------- #
# Per kind: (field name, coercer, default). A POSITIVE list and the whole of §2.2's
# "never exported" guarantee: child PII, memory, telemetry, device ids, credentials etc.
# have no field here to ride out on. `source_version` is the item's field, not `data`'s,
# so a version bump is not a content change (what makes `FORK` detectable). Pinned to the
# dataclass fields by test_content_packs.py (R6).

def _s(v):
    return str(v if v is not None else "")


def _opt_s(v):
    return None if v is None else str(v)


def _i(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        raise PackError(f"expected an integer, got {v!r}")


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        raise PackError(f"expected a number, got {v!r}")


def _d(v):
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise PackError(f"expected an object, got {type(v).__name__}")
    return json.loads(json.dumps(v))          # a deep copy that is provably JSON-safe


SPEC = {
    "conversation": (
        ("name", _s, ""), ("module_id", _s, ""), ("content_id", _s, ""),
        ("prompt", _s, ""), ("opener", _s, ""), ("model", _opt_s, None),
        ("max_tokens", _i, 200), ("temperature", _f, 0.8),
        ("max_history", _i, 40), ("max_volleys", _i, 40),
        ("code", _s, ""), ("memory", _d, {}), ("extension", _d, {}),
    ),
    "global": (
        ("name", _s, ""), ("pattern", _s, ""), ("entity_groups", _s, ""),
        ("action", _i, 0), ("code", _s, ""), ("extension", _d, {}),
    ),
    "schedule": (
        ("name", _s, ""), ("schedule", _d, {}),
    ),
}

#: `{kind: (field, …)}` — the allowlist as plain names, for the pin test and the docs.
FIELDS = {k: tuple(f for f, _c, _d in spec) for k, spec in SPEC.items()}

#: The dataclass behind each kind, and the JSON section `module_data` writes it into.
DATACLASS = {"conversation": Conversation, "global": Global, "schedule": Schedule}
SECTION = {"conversation": "conversations", "global": "globals", "schedule": "schedules"}

#: Fields that carry prose — diffed line by line, and scanned for a child's name.
TEXT_FIELDS = ("prompt", "opener", "code", "name", "pattern")

# ---- review states (§2.3's 2×2 over `source_version` × `local_rev`) -------------------
NEW = "new"                       # not installed here at all
UPGRADE = "upgrade"               # newer source_version, nothing edited locally
CONFLICT = "conflict"             # newer source_version AND edited locally
SAME = "same"                     # same version, same bytes, untouched
KEEP_LOCAL = "keep_local"         # same version, same upstream bytes, edited locally
FORK = "fork"                     # same version number, different content
DOWNGRADE = "downgrade"           # older source_version
DOWNGRADE_CONFLICT = "downgrade_conflict"     # older AND edited locally
INVALID = "invalid"               # the item cannot be installed at all (see `validate_item`)

#: A row with `escalation` (it asks for more capabilities) is defaulted un-ticked.
ESCALATION_LABEL = "This update asks for more than the version you have"

#: States pre-ticked in the review. Anything that could destroy work starts un-ticked.
DEFAULT_ACCEPT = (NEW, UPGRADE)

STATE_LABEL = {
    NEW: "New", UPGRADE: "Upgrade", CONFLICT: "Conflict",
    SAME: "Already installed", KEEP_LOCAL: "Keep mine", FORK: "Fork",
    DOWNGRADE: "Downgrade", DOWNGRADE_CONFLICT: "Downgrade over your edits",
    INVALID: "Cannot install",
}


class PackError(ValueError):
    """A pack this appliance refuses to read, with a reason a person can act on."""


# --------------------------------------------------------------------------- #
# Canonical bytes + the digest
# --------------------------------------------------------------------------- #

def canonical(obj) -> bytes:
    """The one serialization every digest is taken over: sorted keys, no whitespace,
    UTF-8 — stable under pretty-printing and key reordering, broken by any content edit."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def digest_of(obj) -> str:
    """`sha256:<hex>` over `canonical(obj)`."""
    return "sha256:" + hashlib.sha256(canonical(obj)).hexdigest()


def pack_digest(pack: dict) -> str:
    """`digest_of` the pack without `digest`/`signatures` (so signatures can be added
    later without invalidating existing checksums)."""
    body = {k: v for k, v in (pack or {}).items() if k not in ("digest", "signatures")}
    return digest_of(body)


# --------------------------------------------------------------------------- #
# Items: normalize, key, validate
# --------------------------------------------------------------------------- #

def normalize_data(kind: str, data) -> dict:
    """One item's `data` through the allowlist, coerced to the dataclass types. Unknown
    keys are dropped and missing ones defaulted, so equal items always hash equal."""
    if kind not in SPEC:
        raise PackError(f"unknown item kind {kind!r} (expected one of {', '.join(KINDS)})")
    if not isinstance(data, dict):
        raise PackError(f"{kind} item: `data` must be an object, "
                        f"got {type(data).__name__}")
    out = {}
    for name, coerce, default in SPEC[kind]:
        try:
            # Defaults are deep-copied so two items never share one `{}`.
            out[name] = (coerce(data[name]) if name in data
                         else (json.loads(json.dumps(default))
                               if isinstance(default, (dict, list)) else default))
        except PackError as e:
            raise PackError(f"{kind}.{name}: {e}")
    return out


def dropped_fields(kind: str, data) -> list:
    """Keys `normalize_data` would throw away — shown in the review, never installed."""
    if not isinstance(data, dict):
        return []
    allowed = set(FIELDS.get(kind, ())) | {"source_version"}
    return sorted(k for k in data if k not in allowed)


def item_key(kind: str, data: dict) -> str:
    """An item's stable identity (upstream's keys): conversation → `module_id/content_id`,
    global/schedule → `name`. A rename reads as a new item (A5)."""
    d = data or {}
    if kind == "conversation":
        return f"{d.get('module_id', '')}/{d.get('content_id', '')}"
    return str(d.get("name", ""))


def full_key(kind: str, key: str) -> str:
    """`kind:key` — the id used in `accept` lists, the store and the console."""
    return f"{kind}:{key}"


def split_key(full: str) -> tuple:
    """`"conversation:FREE_CHAT/default"` → `("conversation", "FREE_CHAT/default")`."""
    kind, _, key = str(full or "").partition(":")
    return kind, key


def validate_item(item) -> list:
    """Every reason this item cannot be installed, as sentences. Empty ⇒ installable.

    Named in `review_pack`, enforced by `apply_pack`. Patterns are compiled here because a
    bad one inside the loader would take down the whole reload.
    """
    reasons = []
    if not isinstance(item, dict):
        return ["item is not an object"]
    kind = item.get("kind")
    if kind not in SPEC:
        return [f"unknown kind {kind!r}"]
    try:
        data = normalize_data(kind, item.get("data"))
    except PackError as e:
        return [str(e)]
    if not item_key(kind, data):
        reasons.append("no identity: a conversation needs module_id, "
                       "a global or schedule needs a name")
    if kind == "global":
        pattern = data.get("pattern") or ""
        if len(pattern) > MAX_PATTERN_CHARS:
            reasons.append(f"pattern is {len(pattern)} characters "
                           f"(the limit is {MAX_PATTERN_CHARS})")
        elif pattern:
            try:
                re.compile(pattern, re.I)
            except re.error as e:
                reasons.append(f"pattern does not compile: {e}")
    block = data.get("extension") or {}
    if block:
        # `allow_p1`: a pack for a later appliance still installs (it just won't run
        # here, and the review says so); only an unreadable program is refused.
        for reason in ext.validate(block, allow_p1=True)[:1]:
            reasons.append(f"extension: {reason}")
    sv = item.get("source_version", 1)
    if not isinstance(sv, int) or isinstance(sv, bool) or sv < 0:
        reasons.append(f"source_version must be a non-negative integer, got {sv!r}")
    return reasons


#: Always-resolvable module ids: the onboarding spine and daily fixture (`schedule.py`).
ALWAYS_KNOWN_MODULES = ("WELCOME", "TNT", "SYSTEMSCHECK", "DM")


def unknown_schedule_modules(data: dict, catalog=None) -> list:
    """`module_id`s in a schedule item that are not in the on-board catalog.

    Warned, not refused: what a real robot does with an unknown module is unobserved
    (brief §7). `moxie_sdk.schedule` is imported lazily so this module stands alone.
    """
    if catalog is None:
        try:
            from ...schedule import ONBOARD_MODULES
            catalog = {m.get("module_id") for m in ONBOARD_MODULES}
        except Exception:
            return []
    known = set(catalog) | set(ALWAYS_KNOWN_MODULES)
    out = []
    sched = (data or {}).get("schedule") or {}
    for entry in (sched.get("provided_schedule") or []):
        mid = (entry or {}).get("module_id") if isinstance(entry, dict) else None
        if mid and mid not in known and mid not in out:
            out.append(mid)
    return out
