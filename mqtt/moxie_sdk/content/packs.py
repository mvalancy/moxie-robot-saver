"""
📦 Content packs — one file you can email, review before it installs, and undo.

A *pack* is the distribution unit for content (`docs/architecture/backlog/content-packs.md`,
audit ADOPT #5). Today a new Moxie activity is a file in our git repository; a pack makes it
something a parent, a teacher or a speech therapist can hand to somebody else. The same
mechanism upgrades the content *we* ship: a release that improves the starter chat is just a
newer pack, applied by exactly the rule that governs a stranger's.

Everything here is **pure**: stdlib only, no store, no HTTP, no clock except an injected
`now`. The runtime (`supervisor/moxie_runtime.py`) owns the three `JsonStore` collections and
the five routes; this module owns the format, the review and the merge.

The file::

    {"pack_format": 1, "id": "bedtime-wind-down", "name": "Bedtime wind-down",
     "details": "…", "author": "", "pack_version": 3,
     "created_at": "2026-09-02T19:40:00Z", "generator": "moxie-cloud",
     "items": [{"kind": "conversation", "key": "FREE_CHAT/default",
                "source_version": 3, "data": {…}}, …],
     "signatures": [], "digest": "sha256:9f2c…"}

Four decisions are load-bearing, and each differs from OpenMoxie's (MIT, described and
credited in `ATTRIBUTION.md` — never copied):

* **A flat `items[]`, keyed `kind:key`.** Upstream selects by array index against a pack
  that is re-posted between review and import; an index is only correct if the array is
  byte-identical to the one the reviewer saw. A key is idempotent and re-runnable.
* **A positive field allowlist, per kind.** Upstream exports `model_to_dict(exclude=['id'])`
  — a denylist, which leaks the first time somebody adds a column. On a child's appliance
  that is not an acceptable failure mode, so `FIELDS` is explicit and
  `sim/tests/test_content_packs.py` pins it against `dataclasses.fields()`.
* **A 2×2 review, not two integers.** Upstream compares `source_version` and nothing else,
  so ticking "upgrade" silently destroys a prompt *you* edited. We also keep `imported_rev`
  (the digest of an item's data at import time); `local_rev != imported_rev` means the item
  was edited here, and that half of the matrix defaults to un-ticked.
* **Checksummed, deliberately not signed.** A detached signature is only worth something
  against a *known publisher* — key distribution, trust roots, revocation. A LAN appliance
  with no account system can honestly provide none of the three, and a signature verified
  against a key that arrived in the same file is decoration that reads as a guarantee.
  `signatures: []` is reserved (adding it later is not a format break) and the security
  property packs actually need is delivered structurally instead: **an imported pack cannot
  execute anything.** `ContentApp` never `exec`s a module's `code`, so a `code` string
  travels as inert data behind a ⚠️ and stays in the store for a future sandboxed runtime
  (audit BEYOND #6). The honest cost is in `review_pack`'s warning text.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import time
from dataclasses import fields as _dc_fields

from .module import Conversation, Global, Schedule, load_modules
from . import ext

#: Reader contract. A pack that says a number we do not know is refused *readably*
#: rather than half-read — a format that cannot say what it is cannot be evolved.
PACK_FORMAT = 1

#: The three exportable kinds, in the order they are written and reviewed.
KINDS = ("conversation", "global", "schedule")

#: Longest regex a pack may carry. A compiled Python regex has no timeout in the stdlib,
#: so a pathological pattern can still stall the matching thread (R3 in the brief, named
#: rather than hidden); a length cap and a compile check are what P0 honestly delivers.
MAX_PATTERN_CHARS = 512

#: Default body cap for the HTTP layer (`MOXIE_PACK_MAX_BYTES`); 1 MiB.
DEFAULT_MAX_BYTES = 1024 * 1024

#: What an exporter stamps into `generator`. Free text, never trusted on read.
GENERATOR = "moxie-cloud"

# --------------------------------------------------------------------------- #
# The allowlist — what may leave this appliance, spelled out
# --------------------------------------------------------------------------- #
# Per kind: (field name, coercer, default). This is a POSITIVE list. It is the whole of
# §2.2's "never exported" guarantee: child_pii, memory contents, telemetry, safety events,
# telehealth transcripts, device ids, permits, config overrides and every credential live
# in other records and simply have no field here to ride out on.
#
# `Global._rx` (the compiled pattern) is derived state and never travels. `source_version`
# is not in `data` either — it is the ITEM's field, so a version bump is not a content
# change (which is what makes `FORK` detectable).
#
# test_content_packs.py::test_the_allowlist_is_pinned_to_the_dataclass_fields asserts these
# names and defaults against `dataclasses.fields()`, so a new field on `Conversation`
# cannot silently start shipping in everybody's packs (risk R6).

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

#: A row carrying `escalation` is defaulted **un-ticked** whatever its state, because the
#: incoming version asks for more than the installed one did. See `review_pack`.
ESCALATION_LABEL = "This update asks for more than the version you have"

#: Which states are ticked when the review is first shown. Only two: a genuinely new item
#: and a clean upgrade. Everything that could destroy work starts un-ticked — a parent has
#: to choose it, and `undo` exists for when they choose wrong.
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
    """The one serialization every digest is taken over.

    `sort_keys` + no whitespace + `ensure_ascii=False`, so the digest survives
    pretty-printing, key reordering and line-ending changes and fails on any content edit.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def digest_of(obj) -> str:
    """`sha256:<hex>` over `canonical(obj)`."""
    return "sha256:" + hashlib.sha256(canonical(obj)).hexdigest()


def pack_digest(pack: dict) -> str:
    """The pack's digest: `digest_of` the whole object **without** `digest`/`signatures`.

    Excluding both is what lets a signature be added later without invalidating the
    checksum of every pack already in the wild.
    """
    body = {k: v for k, v in (pack or {}).items() if k not in ("digest", "signatures")}
    return digest_of(body)


# --------------------------------------------------------------------------- #
# Items: normalize, key, validate
# --------------------------------------------------------------------------- #

def normalize_data(kind: str, data) -> dict:
    """One item's `data` through the allowlist, coerced to the dataclass's own types.

    Unknown keys are dropped (never stored, never re-exported) and missing ones take the
    dataclass default, so an item that survives this is *exactly* what the loader accepts
    and two equal items always hash equal.
    """
    if kind not in SPEC:
        raise PackError(f"unknown item kind {kind!r} (expected one of {', '.join(KINDS)})")
    if not isinstance(data, dict):
        raise PackError(f"{kind} item: `data` must be an object, "
                        f"got {type(data).__name__}")
    out = {}
    for name, coerce, default in SPEC[kind]:
        try:
            # A missing field takes the dataclass default — DEEP-COPIED, because a shared
            # `{}` would make two items' `memory` blocks the same object.
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
    """The stable identity of an item — upstream's keys, which is why packs interoperate.

    conversation → `module_id/content_id`; global and schedule → `name`. A rename reads as
    a *new* item; since P0 never deletes, the old one survives and the parent sees both
    (assumption A5).
    """
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

    Called by `review_pack` (so a bad item is named in the review, with the rest of the
    pack still usable) and enforced by `apply_pack` (so a caller cannot accept one anyway).
    A `pattern` is checked here rather than at `load_module` time — `Global.from_dict`
    compiles at load, and a pack that throws inside the loader takes down the reload.
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
        # `allow_p1` on purpose: a pack authored for a *later* appliance must still
        # install, exactly as one carrying `code` does — it simply will not run here, and
        # `extension_warnings` says so in the review. What is refused at import is a
        # program this appliance could never read at all.
        for reason in ext.validate(block, allow_p1=True)[:1]:
            reasons.append(f"extension: {reason}")
    sv = item.get("source_version", 1)
    if not isinstance(sv, int) or isinstance(sv, bool) or sv < 0:
        reasons.append(f"source_version must be a non-negative integer, got {sv!r}")
    return reasons


#: Module ids that are always resolvable whatever the catalog says — the onboarding spine
#: and the daily fixture, which `schedule.py` carries in DEFAULT_TEMPLATE rather than in
#: the rotation.
ALWAYS_KNOWN_MODULES = ("WELCOME", "TNT", "SYSTEMSCHECK", "DM")


def unknown_schedule_modules(data: dict, catalog=None) -> list:
    """`module_id`s in a schedule item that are not in the on-board catalog.

    A schedule is the one item kind that reaches the robot (as `ContentSchedule`), and
    **no physical robot has ever been served a pack-authored schedule** — what one does
    with an entry naming a module its firmware does not have is unobserved (brief §7). So
    the review warns rather than refuses. `moxie_sdk.schedule` is imported lazily and
    optionally, so this module stays importable on its own.
    """
    if catalog is None:
        try:
            from ..schedule import ONBOARD_MODULES
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


# --------------------------------------------------------------------------- #
# Export
# --------------------------------------------------------------------------- #

def export_pack(items, *, name: str, pack_id: str, details: str = "", author: str = "",
                pack_version: int = 1, generator: str = GENERATOR, now=None) -> dict:
    """Build a pack from installed items.

    `items` is either the store's mapping (`{"conversation:KEY": {"data", "provenance"}}`)
    or a list of `{"kind", "data", "source_version"?}`. Items are sorted by (kind, key), so
    the same content always exports to the same bytes — which is what makes the round-trip
    test byte-stable and a digest comparable between two appliances.
    """
    rows = []
    for kind, key, entry in _iter_items(items):
        data = normalize_data(kind, entry.get("data"))
        rows.append({"kind": kind, "key": key or item_key(kind, data),
                     "source_version": _source_version(entry),
                     "data": data})
    rows.sort(key=lambda r: (KINDS.index(r["kind"]), r["key"]))
    stamp = time.gmtime(now if now is not None else time.time())
    pack = {
        "pack_format": PACK_FORMAT,
        "id": sanitize_pack_id(pack_id),
        "name": str(name or ""),
        "details": str(details or ""),
        "author": str(author or ""),
        "pack_version": int(pack_version or 1),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", stamp),
        "generator": str(generator or GENERATOR),
        "items": rows,
        "signatures": [],                     # reserved, unread — see the module docstring
    }
    pack["digest"] = pack_digest(pack)
    return pack


def sanitize_pack_id(pack_id: str) -> str:
    """`[a-z0-9-]`, ≤ 64 — a pack id is a filename and a store key, never free text."""
    cleaned = re.sub(r"[^a-z0-9-]+", "-", str(pack_id or "").strip().lower()).strip("-")
    return (cleaned or "pack")[:64]


def dumps_pack(pack: dict) -> str:
    """A pack as a person receives it: pretty, stable key order, newline-terminated.

    Pretty-printing does not disturb the digest (`canonical` sorts and strips), which is
    the point of taking it over a canonical form rather than over the file's bytes.
    """
    return json.dumps(pack, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _source_version(entry) -> int:
    """An item's author-owned version counter: the entry's, else its provenance's, else 1."""
    if not isinstance(entry, dict):
        return 1
    for candidate in (entry.get("source_version"),
                      (entry.get("provenance") or {}).get("source_version")
                      if isinstance(entry.get("provenance"), dict) else None,
                      (entry.get("data") or {}).get("source_version")
                      if isinstance(entry.get("data"), dict) else None):
        if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate >= 0:
            return candidate
    return 1


def _iter_items(items):
    """`(kind, key, entry)` for either accepted `items` shape."""
    if isinstance(items, dict):
        for full, entry in sorted(items.items()):
            kind, key = split_key(full)
            yield kind, key, (entry if isinstance(entry, dict) else {"data": entry})
        return
    for entry in (items or []):
        if not isinstance(entry, dict):
            raise PackError("each item must be an object")
        kind = entry.get("kind")
        yield kind, entry.get("key") or "", entry


def scan_outgoing(items, known_names) -> list:
    """Names this appliance knows that appear in text a pack is about to carry.

    The exporter cannot know that a string is PII by looking at it — a parent may have
    edited a prompt to include their child's name. This checks the outgoing text against
    the nicknames the appliance *currently* knows and flags a hit so the export UI can say
    "this prompt mentions Ada — edit it or export anyway". **It catches the names we know
    and nothing else**, and it never blocks the export.
    """
    names = [str(n).strip() for n in (known_names or []) if str(n or "").strip()]
    hits = []
    for kind, key, entry in _iter_items(items):
        try:
            data = normalize_data(kind, entry.get("data"))
        except PackError:
            continue
        for field in TEXT_FIELDS:
            text = data.get(field)
            if not isinstance(text, str) or not text:
                continue
            for name in names:
                if re.search(r"\b%s\b" % re.escape(name), text, re.I):
                    hits.append({"kind": kind, "key": key or item_key(kind, data),
                                 "field": field, "name": name})
    return hits


# --------------------------------------------------------------------------- #
# Parse
# --------------------------------------------------------------------------- #

def parse_pack(raw) -> tuple:
    """`bytes | str | dict` → `(pack, meta)`; raises `PackError` with a readable reason.

    `meta` is `{"digest": "ok" | "mismatch" | "absent", "warnings": [...],
    "computed": "sha256:…", "claimed": "…"}`. A mismatch is **not** fatal — a hand-written
    pack is a legitimate thing — but `review_pack` then default-ticks nothing and the card
    says the file was changed after it was exported.

    The returned pack is *sanitized*: every item's `data` has been through the allowlist,
    so nothing a stranger invented can reach the store. The digest is checked against the
    body **as delivered**, before that, so tampering is still detected.
    """
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = bytes(raw).decode("utf-8")
        except UnicodeDecodeError as e:
            raise PackError(f"this file is not UTF-8 text: {e}")
    if isinstance(raw, str):
        try:
            body = json.loads(raw or "null")
        except ValueError as e:
            raise PackError(f"this file is not valid JSON: {e}")
    else:
        body = raw
    if not isinstance(body, dict):
        raise PackError("a pack must be a JSON object with `pack_format` and `items`")

    fmt = body.get("pack_format")
    if fmt is None:
        raise PackError("no `pack_format`: this file was not written as a content pack "
                        "(an OpenMoxie module file is not one — that is P2)")
    if not isinstance(fmt, int) or isinstance(fmt, bool) or fmt != PACK_FORMAT:
        raise PackError(f"pack_format {fmt!r} — this appliance reads format "
                        f"{PACK_FORMAT}. Update the appliance, or ask for an older pack.")

    claimed = body.get("digest")
    if claimed is None:
        state = "absent"
    else:
        state = "ok" if str(claimed) == pack_digest(body) else "mismatch"

    raw_items = body.get("items")
    if raw_items is None:
        raise PackError("no `items`: a pack with nothing in it cannot be installed")
    if not isinstance(raw_items, list):
        raise PackError(f"`items` must be a list, got {type(raw_items).__name__}")

    warnings = []
    items = []
    seen = set()
    for n, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            raise PackError(f"item {n}: expected an object, "
                            f"got {type(raw_item).__name__}")
        kind = raw_item.get("kind")
        if kind not in SPEC:
            raise PackError(f"item {n}: unknown kind {kind!r} "
                            f"(expected one of {', '.join(KINDS)})")
        data = normalize_data(kind, raw_item.get("data"))
        dropped = dropped_fields(kind, raw_item.get("data"))
        key = str(raw_item.get("key") or "") or item_key(kind, data)
        if full_key(kind, key) in seen:
            raise PackError(f"item {n}: {full_key(kind, key)} appears twice")
        seen.add(full_key(kind, key))
        if dropped:
            warnings.append(f"{full_key(kind, key)}: ignored unknown "
                            f"field(s) {', '.join(dropped)}")
        items.append({"kind": kind, "key": key,
                      "source_version": raw_item.get("source_version", 1),
                      "data": data})

    pack = {
        "pack_format": PACK_FORMAT,
        "id": sanitize_pack_id(body.get("id") or body.get("name") or "pack"),
        "name": str(body.get("name") or ""),
        "details": str(body.get("details") or ""),
        "author": str(body.get("author") or ""),
        "pack_version": _int_or(body.get("pack_version"), 1),
        "created_at": str(body.get("created_at") or ""),
        "generator": str(body.get("generator") or ""),
        "items": items,
        "signatures": [],
        "digest": str(claimed or ""),
    }
    return pack, {"digest": state, "warnings": warnings,
                  "computed": pack_digest(body), "claimed": str(claimed or "")}


def _int_or(value, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def pack_summary(pack: dict, *, imported_at=None) -> dict:
    """One row of the `fleet/content_packs.json` ledger the 📦 card lists."""
    p = pack or {}
    return {"id": p.get("id") or "", "name": p.get("name") or "",
            "details": p.get("details") or "", "author": p.get("author") or "",
            "pack_version": _int_or(p.get("pack_version"), 1),
            "digest": p.get("digest") or "",
            "imported_at": int(imported_at if imported_at is not None else time.time()),
            "item_count": len(p.get("items") or [])}


# --------------------------------------------------------------------------- #
# Review — the 2×2 that does not clobber
# --------------------------------------------------------------------------- #

def local_rev(entry) -> str:
    """`sha256(canonical(this item's data as it stands right now))`."""
    e = entry if isinstance(entry, dict) else {}
    prov = e.get("provenance") if isinstance(e.get("provenance"), dict) else {}
    kind = e.get("kind") or prov.get("kind") or ""
    try:
        return digest_of(normalize_data(kind, e.get("data")) if kind else e.get("data"))
    except PackError:
        return digest_of(e.get("data"))


def is_local_edited(entry) -> bool:
    """True when this item's data no longer matches what was installed.

    `imported_rev` is stamped at install time (by `apply_pack`, and by `shipped_items` for
    factory content). No `imported_rev` at all ⇒ we cannot tell, and an item we cannot
    attribute is treated as **edited** — the cautious half of the matrix, because the cost
    of being wrong that way is a tick a parent has to make by hand rather than work lost.
    """
    e = entry if isinstance(entry, dict) else {}
    prov = e.get("provenance") if isinstance(e.get("provenance"), dict) else {}
    imported = prov.get("imported_rev")
    if not imported:
        return True
    return local_rev({"kind": prov.get("kind") or e.get("kind"), "data": e.get("data")}) \
        != str(imported)


def diff_item(old, new) -> list:
    """Field-level difference between two `data` dicts.

    Prose (`prompt`, `opener`, `code`, …) gets a `difflib.unified_diff`; a scalar gets
    plain `old → new`. `old` may be None (a brand-new item), in which case every non-empty
    field is an addition — a parent installing a stranger's chat should see the whole
    prompt, not a summary (risk R4).
    """
    rows = []
    o = old if isinstance(old, dict) else {}
    n = new if isinstance(new, dict) else {}
    for field in sorted(set(o) | set(n)):
        before, after = o.get(field), n.get(field)
        if before == after:
            continue
        if isinstance(before, str) or isinstance(after, str):
            b, a = str(before or ""), str(after or "")
            if "\n" in b or "\n" in a or len(b) > 80 or len(a) > 80:
                lines = list(difflib.unified_diff(
                    b.splitlines(), a.splitlines(), lineterm="", n=1,
                    fromfile="installed", tofile="pack"))
                rows.append({"field": field, "kind": "text", "diff": lines,
                             "old": b, "new": a})
                continue
            rows.append({"field": field, "kind": "scalar", "old": b, "new": a})
            continue
        rows.append({"field": field, "kind": "scalar",
                     "old": json.dumps(before, sort_keys=True) if before is not None else "",
                     "new": json.dumps(after, sort_keys=True) if after is not None else ""})
    return rows


def review_pack(pack: dict, installed, *, digest: str = "ok", catalog=None) -> list:
    """Per-item review rows: what would happen, and what is ticked before a parent touches it.

    `installed` is the store's mapping (`{"kind:key": {"data", "provenance"}}`). Writes
    nothing, reads no clock — the whole point of the two-step flow is that reviewing is
    free and only `apply_pack` changes anything.

    `digest` is `parse_pack`'s verdict. Anything but `"ok"` ticks **nothing**: a file that
    was changed after it was exported gets no defaults, on any item.
    """
    trusted = digest == "ok"
    rows = []
    for item in (pack or {}).get("items") or []:
        kind = item.get("kind")
        key = item.get("key") or ""
        entry = (installed or {}).get(full_key(kind, key))
        reasons = validate_item(item)
        row = {
            "kind": kind, "key": key, "id": full_key(kind, key),
            "source_version": _int_or(item.get("source_version"), 1),
            "installed_version": None, "state": INVALID, "label": "",
            "default": False, "local_edited": False, "origin": "",
            "pack_id": "", "warnings": [], "reasons": reasons, "diff": [],
            "escalation": [],
        }
        if reasons:
            row["label"] = STATE_LABEL[INVALID]
            rows.append(row)
            continue
        data = normalize_data(kind, item.get("data"))
        row["name"] = data.get("name") or key
        incoming_rev = digest_of(data)
        if entry is None:
            row["state"] = NEW
            # A NEW row is the one the review **pre-ticks**, so it is the last row that
            # may show nothing of what it installs. `diff_item(None, …)` renders every
            # field as an addition, which for a stranger's chat is the whole prompt —
            # risk R4's answer, and what `diff_item`'s own contract already promised.
            row["diff"] = diff_item(None, data)
        else:
            prov = entry.get("provenance") if isinstance(entry.get("provenance"), dict) else {}
            installed_data = normalize_data(kind, entry.get("data"))
            row["installed_version"] = _source_version(entry)
            row["origin"] = str(prov.get("origin") or "")
            row["pack_id"] = str(prov.get("pack_id") or "")
            edited = is_local_edited({"kind": kind, **entry})
            row["local_edited"] = edited
            row["diff"] = diff_item(installed_data, data)
            row["state"] = _state(row["source_version"], row["installed_version"],
                                  incoming_rev, str(prov.get("imported_rev") or ""), edited)
            was = set(extension_capabilities(installed_data))
            now = set(extension_capabilities(data))
            row["escalation"] = sorted(now - was)
        row["label"] = _label(row)
        row["warnings"] = _warnings(kind, data, catalog=catalog)
        row["default"] = bool(trusted and row["state"] in DEFAULT_ACCEPT
                              and not row["escalation"])
        if row["escalation"]:
            # §7.3's load-bearing addition. The comparison is over the **capability set**,
            # independent of `source_version` and independent of `local_rev`: so a pack
            # cannot escalate privileges by bumping a version number, and it cannot
            # escalate them quietly on a machine where the parent never edited anything.
            # A *shrinking* set is not a conflict — less is always safe, so it defaults
            # ticked like any other upgrade.
            row["warnings"].insert(0, ESCALATION_LABEL + ": it now wants to "
                                   + _escalation_words(row["escalation"]) + ".")
        rows.append(row)
    return rows


def _escalation_words(caps) -> str:
    """"remember things about your child, and check the time" — the newly-asked-for
    capabilities as one clause a parent can decline on."""
    words = []
    for cap in caps:
        sentence = (ext.ACTION_WORDS.get(cap[4:]) if cap.startswith("act.")
                    else ext.CAPABILITY_WORDS.get(cap)) or cap
        words.append(sentence.replace("Can ", "", 1).replace("Can", "", 1).strip())
    if len(words) == 1:
        return words[0]
    return ", ".join(words[:-1]) + " and " + words[-1]


def _state(incoming_v: int, installed_v: int, incoming_rev: str, imported_rev: str,
           edited: bool) -> str:
    """§2.3's table, in one place. Version first, then whether the bytes moved."""
    if incoming_v > installed_v:
        return CONFLICT if edited else UPGRADE
    if incoming_v < installed_v:
        return DOWNGRADE_CONFLICT if edited else DOWNGRADE
    # Equal versions: did the AUTHOR change the content without bumping? (assumption A1)
    if imported_rev and incoming_rev != imported_rev:
        return FORK
    return KEEP_LOCAL if edited else SAME


def _label(row: dict) -> str:
    """The state as a sentence a parent can act on."""
    state, sv, iv = row["state"], row["source_version"], row["installed_version"]
    if state == NEW:
        return f"New — not installed here (v{sv})"
    if state == UPGRADE:
        return f"Upgrade v{iv} → v{sv}"
    if state == CONFLICT:
        return f"Upgrading v{iv} → v{sv} replaces the changes you made here"
    if state == SAME:
        return f"Already installed, unchanged (v{sv})"
    if state == KEEP_LOCAL:
        return f"You edited this one — importing v{sv} puts it back"
    if state == FORK:
        return f"Same version number (v{sv}), different content"
    if state == DOWNGRADE:
        return f"Older than what is installed (v{sv} < v{iv})"
    if state == DOWNGRADE_CONFLICT:
        return f"Older (v{sv} < v{iv}) and would replace the changes you made here"
    return STATE_LABEL.get(state, state)


def extension_warnings(data: dict) -> list:
    """What a parent is told about an item's `extension`, in plain language (§7.3).

    Three things, in this order, because a parent reads top-down:

    1. **The grant list** — one sentence per capability, from the fixed table in
       `ext.CAPABILITY_WORDS`. Never author-supplied text: a pack that could write its own
       grant sentence could write a reassuring lie.
    2. **`explain()`'s sentences** — one per rule. A grant list tells a parent what a pack
       *may* do; these tell them what it *will* do, which is the difference between a
       permissions dialog and a review.
    3. **An honest note when it will install but not run** — either because the program is
       malformed, or because it needs a capability this appliance cannot grant yet
       (`ext.P1_CAPABILITIES`: `subscribe`, `brain`, `schedule.request`; **`act` left that
       set on 2026-09-04** and no longer triggers the note). Saying nothing here would
       repeat the exact mistake the `code` warning exists to avoid.

       What this note does **not** cover is a capability the appliance *can* honour but has
       not *granted* — an imported pack asking for `clock`, or now for `act.<name>`. That
       one installs, reviews cleanly, and is refused at load by the grant check, which the
       parent sees through the `ext_events` ring. Which grants a parent may hand out is the
       console card, and that is still P1.
    """
    block = (data or {}).get("extension") or {}
    if not block:
        return []
    out = []
    if ext.validate(block, allow_p1=True):
        return ["carries a program this appliance cannot read, so the rest of this item "
                "installs and the program does not: "
                + ext.validate(block, allow_p1=True)[0]]
    out += ["this activity " + w[0].lower() + w[1:] for w in ext.grant_list(block)]
    out += ext.explain(block)
    p0 = ext.validate(block)
    if p0:
        out.append("…but not yet on this appliance: " + p0[0])
    return out


def extension_capabilities(data: dict) -> list:
    """The capability set an item's extension declares — the thing the escalation rule in
    `review_pack` compares across versions."""
    return ext.capabilities_of((data or {}).get("extension") or {})


def _warnings(kind: str, data: dict, *, catalog=None) -> list:
    """The things a parent should be told before an item installs."""
    out = []
    if data.get("code"):
        out.append("carries a `code` block (Python), which this appliance never runs — "
                   "see `extension` for behaviour this appliance can run")
    out += extension_warnings(data)
    if kind == "schedule":
        unknown = unknown_schedule_modules(data, catalog=catalog)
        if unknown:
            out.append("plans activities this robot's firmware may not have: "
                       + ", ".join(unknown))
    if kind == "global" and data.get("pattern"):
        out.append("listens for this on every turn: " + data["pattern"])
    return out


# --------------------------------------------------------------------------- #
# Apply
# --------------------------------------------------------------------------- #

def apply_pack(pack: dict, installed, accept, *, now=None) -> tuple:
    """Apply the accepted items and return `(items, summary)` — a NEW mapping, not a patch.

    `accept` is a list of `kind:key` ids. Selection is by key rather than by array index
    (upstream's shape) because the pack is re-posted between review and import: an index is
    only correct if the array is byte-identical to the one the reviewer saw, and nothing
    guarantees that. A key that is not in the pack is an **error**, never a silent skip —
    a parent who ticked something and got nothing deserves to be told.

    The caller writes the returned mapping once, atomically, after snapshotting the old one
    (R1): a crash leaves either the old set or the new one, never a mixture.
    """
    at = int(now if now is not None else time.time())
    by_id = {full_key(i.get("kind"), i.get("key") or ""): i
             for i in (pack or {}).get("items") or []}
    wanted = []
    for raw in (accept or []):
        if isinstance(raw, bool) or isinstance(raw, int):
            raise PackError(f"accept must name items as `kind:key`, not by index ({raw!r})")
        ident = str(raw)
        if ident not in by_id:
            raise PackError(f"{ident!r} is not in this pack "
                            f"(it has {len(by_id)} item(s))")
        if ident not in wanted:
            wanted.append(ident)

    out = {k: json.loads(json.dumps(v)) for k, v in (installed or {}).items()}
    applied, replaced = [], []
    for ident in wanted:
        item = by_id[ident]
        kind = item["kind"]
        reasons = validate_item(item)
        if reasons:
            raise PackError(f"{ident}: {reasons[0]}")
        data = normalize_data(kind, item.get("data"))
        sv = _int_or(item.get("source_version"), 1)
        if ident in out:
            replaced.append(ident)
        out[ident] = {
            "kind": kind, "key": item.get("key") or item_key(kind, data),
            "data": data,
            "provenance": {
                "kind": kind,
                "pack_id": (pack or {}).get("id") or "",
                "pack_version": _int_or((pack or {}).get("pack_version"), 1),
                "source_version": sv,
                "imported_at": at,
                "imported_rev": digest_of(data),
                "origin": "pack",
            },
        }
        applied.append(ident)
    summary = {
        "pack": pack_summary(pack, imported_at=at),
        "applied": applied,
        "replaced": replaced,
        "skipped": [i for i in sorted(by_id) if i not in applied],
        "count": len(applied),
    }
    return out, summary


def mark_edited(items: dict, ident: str, data: dict) -> dict:
    """A local edit to one installed item: replace its data, keep its provenance, and let
    `local_rev` drift away from `imported_rev` — which is exactly what makes the next
    import of that item report `KEEP LOCAL` / `CONFLICT` instead of clobbering it.

    P0 has no editor in the console; this is the seam the tests (and a future 📦 edit
    button) use, and it is the only supported way to change an installed item's content.
    """
    out = {k: json.loads(json.dumps(v)) for k, v in (items or {}).items()}
    kind, _ = split_key(ident)
    entry = out.get(ident) or {"kind": kind, "key": split_key(ident)[1],
                               "provenance": {"kind": kind, "origin": "local",
                                              "source_version": 1}}
    entry["data"] = normalize_data(kind, data)
    out[ident] = entry
    return out


# --------------------------------------------------------------------------- #
# ✍️ Authoring — the phrase list, and the shadow a name order casts
# (backlog/content-authoring.md §4.3, §4.4)
# --------------------------------------------------------------------------- #
# A parent will not write a regular expression, so the guided surface for a command is a
# **list of phrases**, and these two functions are the whole grammar of that surface: one
# compiles a list into the `pattern` the loader already understands, the other reads one
# back so an item can be re-opened in the guided view. Neither is a second validator —
# what they produce still goes through `normalize_data` and `validate_item` like anything
# else — and the round trip is deliberately *partial*: a pattern that was hand-written in
# the advanced field does not decompile, which is exactly the "the guided view can no
# longer round-trip this item" case the card has to say out loud (brief §3.3).

#: `re.escape` escapes a space, which is correct and unreadable — a parent who opens the
#: advanced field should see `(what time is it|the time)`, not `what\ time\ is\ it`. A
#: space is not a metacharacter, so putting it back changes nothing about what matches.
def _escape_phrase(phrase: str) -> str:
    return re.escape(str(phrase or "").strip()).replace("\\ ", " ")


def compile_phrases(phrases) -> str:
    """A parent's phrase list → one alternation the loader compiles.

    Every phrase is escaped, so a command called *"what's up?"* is a literal rather than a
    broken regex; the whole is wrapped in one group so `entity_groups` keeps meaning what
    it meant. An empty list is an empty pattern — a global with no pattern never fires,
    which is the honest answer to "a command with nothing to match".
    """
    parts = [_escape_phrase(p) for p in (phrases or []) if str(p or "").strip()]
    return "(" + "|".join(parts) + ")" if parts else ""


def phrases_of(pattern: str) -> list:
    """The inverse of `compile_phrases`, or `[]` when the pattern was not written by it.

    Decided by **re-compiling**: a pattern decompiles only if `compile_phrases` of the
    parts reproduces it byte for byte. Anything else — a hand-written regex, a character
    class, a quantifier — comes back empty rather than half-understood, because a guided
    surface that silently mangles an author's regex is worse than one that admits it
    cannot show it.
    """
    text = str(pattern or "")
    if not (text.startswith("(") and text.endswith(")")):
        return []
    parts = text[1:-1].split("|")
    out = []
    for part in parts:
        try:
            out.append(re.sub(r"\\(.)", r"\1", part))
        except re.error:
            return []
    return out if compile_phrases(out) == text else []


def source_version_of(entry) -> int:
    """An installed entry's author-owned version counter — the public name for
    `_source_version`, so the authoring route can hand `validate_item` the version an item
    already has instead of inventing one."""
    return _source_version(entry)


def shadow_check(draft: dict, installed: dict, phrases=None) -> list:
    """Which installed command answers the author's own phrases *before* this one does.

    `match_global` returns the **first** pattern that fires and `module_data` builds the
    list `sorted(kind:key)`, so a global's precedence is alphabetical by its `name`
    (assumption A4, proven by reading both). A parent naming a command *"Ask the time"*
    therefore beats one named *"Time"*, and nothing on the screen would say so.

    **The honest bound, which the caller must repeat to the author** (A5): this is exact
    for the phrases actually typed, and it is nothing more than that. Deciding whether two
    arbitrary regular expressions overlap is not something we will do, and a card that
    reported *"no conflicts"* as a statement about all utterances would be lying. One row
    per shadowed phrase, naming the earliest command that takes it.

    Pure: reads the mapping, compiles patterns, writes nothing, and takes no clock.
    """
    data = draft if isinstance(draft, dict) else {}
    name = str(data.get("name") or "")
    mine = full_key("global", name)
    typed = phrases if phrases is not None else phrases_of(data.get("pattern"))
    typed = [str(p) for p in (typed or []) if str(p or "").strip()]
    if not typed:
        return []

    earlier = []
    for full, entry in sorted((installed or {}).items()):
        if not full.startswith("global:") or full >= mine:
            continue                      # a later name loses the race — not a shadow
        e = entry if isinstance(entry, dict) else {}
        try:
            other = normalize_data("global", e.get("data"))
        except PackError:
            continue
        pattern = other.get("pattern") or ""
        if not pattern:
            continue
        try:
            earlier.append((full, other.get("name") or split_key(full)[1],
                            re.compile(pattern, re.I)))
        except re.error:
            continue                      # an uninstallable neighbour cannot shadow

    rows = []
    for phrase in typed:
        for full, other_name, rx in earlier:
            if rx.search(phrase):
                rows.append({
                    "phrase": phrase, "id": full, "name": other_name,
                    "sentence": f"\u201c{phrase}\u201d will be answered by "
                                f"{other_name} before this one gets a turn, "
                                f"because commands are tried in name order.",
                })
                break                     # the FIRST match is the one that wins
    return rows


# --------------------------------------------------------------------------- #
# The overlay: shipped defaults ⊕ installed items → one ContentModule
# --------------------------------------------------------------------------- #

def shipped_items(raw) -> dict:
    """A shipped module file (one dict, or a list of them) as an items mapping.

    Every record may carry `source_version` (default 1), and each gets
    `origin: "shipped"` provenance with an `imported_rev` — so upgrading *our* content
    across a release obeys the identical rule as a community pack (upstream's
    `init_data.py` idea, taken as behaviour), and a shipped item a parent has edited is
    not silently taken back.
    """
    modules = raw if isinstance(raw, list) else [raw or {}]
    out = {}
    for module in modules:
        if not isinstance(module, dict):
            continue
        for kind in KINDS:
            for record in (module.get(SECTION[kind]) or []):
                if not isinstance(record, dict):
                    continue
                data = normalize_data(kind, record)
                key = item_key(kind, data)
                if not key:
                    continue
                sv = _int_or(record.get("source_version"), 1)
                out[full_key(kind, key)] = {
                    "kind": kind, "key": key, "data": data,
                    "provenance": {"kind": kind, "pack_id": "", "pack_version": 1,
                                   "source_version": sv, "imported_at": 0,
                                   "imported_rev": digest_of(data),
                                   "origin": "shipped"},
                }
    return out


def items_from_module(module) -> dict:
    """A loaded `ContentModule` back into an items mapping (the export path from RAM).

    Used when nothing recorded the shipped baseline — the merge is idempotent, so a
    module that already has the overlay in it re-merges to itself.
    """
    out = {}
    if module is None:
        return out
    for kind in KINDS:
        for obj in getattr(module, SECTION[kind], None) or []:
            data = {f: getattr(obj, f, d) for f, _c, d in SPEC[kind]}
            data = normalize_data(kind, data)
            key = item_key(kind, data)
            if not key:
                continue
            sv = _int_or(getattr(obj, "source_version", 1), 1)
            out[full_key(kind, key)] = {
                "kind": kind, "key": key, "data": data,
                "provenance": {"kind": kind, "pack_id": "", "pack_version": 1,
                               "source_version": sv, "imported_at": 0,
                               "imported_rev": digest_of(data), "origin": "shipped"},
            }
    return out


def merge_items(defaults: dict, overlay: dict) -> dict:
    """**Effective content = shipped defaults, then the overlay by key.**

    The overlay never *deletes*: P0 has no remove-item operation, so an import only adds or
    replaces. A parent who wants an activity gone edits its schedule (removal is P2).
    """
    out = {k: v for k, v in (defaults or {}).items()}
    for k, v in (overlay or {}).items():
        out[k] = v
    return out


def module_data(items: dict) -> dict:
    """An items mapping → the module JSON `load_modules` reads.

    Ordering is by (kind, key), so the same overlay always builds the same module — and a
    global's match order is therefore stable across a reload rather than dict-insertion
    luck.
    """
    out = {SECTION[k]: [] for k in KINDS}
    for full, entry in sorted((items or {}).items()):
        kind, _key = split_key(full)
        if kind not in SPEC:
            continue
        e = entry if isinstance(entry, dict) else {}
        record = dict(normalize_data(kind, e.get("data")))
        record["source_version"] = _source_version(e)
        out[SECTION[kind]].append(record)
    return out


def build_module(defaults: dict, overlay: dict):
    """`defaults ⊕ overlay` → a live `ContentModule`. The one call `reload_content` makes."""
    return load_modules(module_data(merge_items(defaults, overlay)))


def inventory(items: dict, *, catalog=None, known_names=()) -> list:
    """The 📦 card's list: one row per installed item, with its provenance and flags.

    `known_names` runs `scan_outgoing`'s check per row, so the export picker can say
    *"this prompt mentions Ada"* before a parent sends the file to somebody — see the
    honest limits of that check in `scan_outgoing`.
    """
    rows = []
    for full, entry in sorted((items or {}).items()):
        kind, key = split_key(full)
        if kind not in SPEC:
            continue
        e = entry if isinstance(entry, dict) else {}
        prov = e.get("provenance") if isinstance(e.get("provenance"), dict) else {}
        try:
            data = normalize_data(kind, e.get("data"))
        except PackError:
            continue
        rows.append({
            "id": full, "kind": kind, "key": key,
            "name": data.get("name") or key,
            "source_version": _source_version(e),
            "origin": str(prov.get("origin") or ""),
            "pack_id": str(prov.get("pack_id") or ""),
            "imported_at": _int_or(prov.get("imported_at"), 0),
            "local_edited": is_local_edited({"kind": kind, **e}),
            "has_code": bool(data.get("code")),
            "warnings": _warnings(kind, data, catalog=catalog),
            "pii": [{"field": h["field"], "name": h["name"]}
                    for h in scan_outgoing([{"kind": kind, "key": key, "data": data}],
                                           known_names)],
        })
    return rows


def dataclass_fields(kind: str) -> tuple:
    """Every public field of a kind's dataclass except `source_version` — what the
    allowlist is pinned against (`FIELDS[kind]` must equal this, exactly)."""
    return tuple(f.name for f in _dc_fields(DATACLASS[kind])
                 if not f.name.startswith("_") and f.name != "source_version")
