"""✍️ Authoring helpers: the phrase list a command is built from, and the shadow a name
order casts (backlog/content-authoring.md §4.3, §4.4)."""
from __future__ import annotations

import re

from .items import full_key, normalize_data, PackError, split_key
from .wire import _source_version


# The guided surface for a command is a list of phrases: `compile_phrases` builds the
# `pattern`, `phrases_of` reads one back. The round trip is deliberately partial — a
# hand-written regex does not decompile (brief §3.3).

#: `re.escape` escapes spaces; unescape them for readability (a space is not special).
def _escape_phrase(phrase: str) -> str:
    return re.escape(str(phrase or "").strip()).replace("\\ ", " ")


def compile_phrases(phrases) -> str:
    """A phrase list → one escaped, grouped alternation. Empty list → empty pattern (a
    global that never fires)."""
    parts = [_escape_phrase(p) for p in (phrases or []) if str(p or "").strip()]
    return "(" + "|".join(parts) + ")" if parts else ""


def phrases_of(pattern: str) -> list:
    """The inverse of `compile_phrases`, or `[]` unless re-compiling the parts
    reproduces the pattern byte for byte (never half-understand a hand-written regex)."""
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
    """Public name for `_source_version` (used by the authoring route)."""
    return _source_version(entry)


def shadow_check(draft: dict, installed: dict, phrases=None) -> list:
    """Which installed command answers the author's own phrases *before* this one.

    Globals are matched first-hit in `sorted(kind:key)` order, i.e. alphabetically by name
    (A4). Exact only for the phrases typed — not a claim about all utterances (A5), which
    the caller must say. One row per shadowed phrase. Pure.
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
