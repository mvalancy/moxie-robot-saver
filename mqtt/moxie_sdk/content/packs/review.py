"""Review — the 2×2 (version × edited-here) that never clobbers a local edit — and apply."""
from __future__ import annotations

import difflib
import json
import time

from .. import ext
from .items import (CONFLICT, DEFAULT_ACCEPT, digest_of, DOWNGRADE, DOWNGRADE_CONFLICT,
    ESCALATION_LABEL, FORK, full_key, INVALID, item_key, KEEP_LOCAL, NEW,
    normalize_data, PackError, SAME, split_key, STATE_LABEL, unknown_schedule_modules,
    UPGRADE, validate_item)
from .wire import _int_or, pack_summary, _source_version



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
    """True when this item's data no longer matches its install-time `imported_rev`.
    No `imported_rev` ⇒ treated as edited: the cautious default costs a tick, not work."""
    e = entry if isinstance(entry, dict) else {}
    prov = e.get("provenance") if isinstance(e.get("provenance"), dict) else {}
    imported = prov.get("imported_rev")
    if not imported:
        return True
    return local_rev({"kind": prov.get("kind") or e.get("kind"), "data": e.get("data")}) \
        != str(imported)


def diff_item(old, new) -> list:
    """Field-level difference between two `data` dicts: long/multi-line text as a
    unified diff, else `old → new`. `old=None` shows every field as added (R4)."""
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
    """Per-item review rows: what would happen, and what is pre-ticked. Pure — only
    `apply_pack` changes anything. A `digest` verdict other than "ok" pre-ticks nothing."""
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
            # A pre-ticked NEW row shows everything it installs (R4).
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
            # §7.3: compared over the capability set, independent of versions and edits,
            # so no version bump escalates quietly. A shrinking set is always safe.
            row["warnings"].insert(0, ESCALATION_LABEL + ": it now wants to "
                                   + _escalation_words(row["escalation"]) + ".")
        rows.append(row)
    return rows


def _escalation_words(caps) -> str:
    """The newly-asked-for capabilities as one clause a parent can decline on."""
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
    """What a parent is told about an item's `extension`, top-down (§7.3):

    1. the grant list (fixed-table sentences, never author text);
    2. `explain()`'s one sentence per rule — what it *will* do;
    3. a note when it installs but will not run here (malformed, or needs a
       `P1_CAPABILITIES` entry). An honourable-but-ungranted capability is not noted: it
       is refused at load and surfaces in the `ext_events` ring.
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


def opener_warnings(data: dict) -> list:
    """What a parent is told about a conversation's `opener`: one sentence naming what its
    action tags can make Moxie do as the conversation starts, or nothing when it writes
    none whole. The robot acts on an opener's tag only when it is written whole in the
    alternative said, unrendered (`content_app.said_opener`), so every tag written whole in
    any alternative is named, each as "sometimes" (which alternative is said, and what its
    template leaves in, varies); a tag that only forms as the opener renders never acts and
    is not named."""
    opener = (data or {}).get("opener")
    if not isinstance(opener, str) or not opener:
        return []
    from ..content_app import opener_alternatives
    effects = ext.written_effects(opener_alternatives(opener))
    if not effects:
        return []
    return ["When this conversation starts, Moxie says its opener; then "
            + " and ".join(effects) + "."]


def extension_capabilities(data: dict) -> list:
    """The capability set an item's extension declares (compared for escalation)."""
    return ext.capabilities_of((data or {}).get("extension") or {})


def _warnings(kind: str, data: dict, *, catalog=None) -> list:
    """The things a parent should be told before an item installs."""
    out = []
    if data.get("code"):
        out.append("carries a `code` block (Python), which this appliance never runs — "
                   "see `extension` for behaviour this appliance can run")
    out += extension_warnings(data)
    if kind == "conversation":
        out += opener_warnings(data)
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
    """Apply the accepted `kind:key` ids; return `(items, summary)` — a NEW mapping.

    An id not in the pack is an error, never a silent skip. The caller writes the result
    once, atomically, after snapshotting the old one (R1).
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
    """A local edit: replace one item's data and keep its provenance, so the next import
    reports `KEEP_LOCAL`/`CONFLICT` instead of clobbering it."""
    out = {k: json.loads(json.dumps(v)) for k, v in (items or {}).items()}
    kind, _ = split_key(ident)
    entry = out.get(ident) or {"kind": kind, "key": split_key(ident)[1],
                               "provenance": {"kind": kind, "origin": "local",
                                              "source_version": 1}}
    entry["data"] = normalize_data(kind, data)
    out[ident] = entry
    return out
