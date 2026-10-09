"""📦 Content packs — one file you can email, review before it installs, and undo
(docs/architecture/backlog/content-packs.md, audit ADOPT #5). A release that improves the
content *we* ship is just a newer pack, applied by the same rule as a stranger's.

Pure: stdlib only, no store, no HTTP, clock only via an injected `now`. The runtime owns
the `JsonStore` collections and the routes; this module owns the format, review and merge.

The file::

    {"pack_format": 1, "id": "bedtime-wind-down", "name": "Bedtime wind-down",
     "details": "…", "author": "", "pack_version": 3,
     "created_at": "2026-09-02T19:40:00Z", "generator": "moxie-cloud",
     "items": [{"kind": "conversation", "key": "FREE_CHAT/default",
                "source_version": 3, "data": {…}}, …],
     "signatures": [], "digest": "sha256:9f2c…"}

Load-bearing decisions (each differs from OpenMoxie, MIT — credited in ATTRIBUTION.md):

* **Flat `items[]` keyed `kind:key`**, not array indices: idempotent across the re-post
  between review and import.
* **A positive per-kind field allowlist** (`SPEC`), pinned against the dataclasses by
  `test_content_packs.py` — a denylist leaks the first time a field is added.
* **A 2×2 review**: `source_version` × whether the item was edited here (`imported_rev`
  vs the current digest), so an upgrade never silently destroys a local edit.
* **Checksummed, not signed**: a LAN appliance has no publisher trust roots, so a
  signature would be decoration. `signatures: []` is reserved. The real guarantee is
  structural: an imported pack cannot execute anything (`code` stays inert data).

Layout: `items` (format constants, the per-kind allowlist, digests, item validation),
`wire` (export + parse), `review` (the 2×2 review + apply), `authoring` (phrases, shadow
check), `overlay` (shipped ⊕ installed → one ContentModule). Import from
`moxie_sdk.content.packs` only.
"""
from __future__ import annotations

from .items import (ALWAYS_KNOWN_MODULES, canonical, CONFLICT, DATACLASS,
    DEFAULT_ACCEPT, DEFAULT_MAX_BYTES, digest_of, DOWNGRADE, DOWNGRADE_CONFLICT,
    dropped_fields, ESCALATION_LABEL, FIELDS, FORK, full_key, GENERATOR, INVALID,
    item_key, KEEP_LOCAL, KINDS, MAX_PATTERN_CHARS, NEW, normalize_data, pack_digest,
    PACK_FORMAT, PackError, SAME, SECTION, SPEC, split_key, STATE_LABEL, TEXT_FIELDS,
    unknown_schedule_modules, UPGRADE, validate_item)  # noqa: F401
from .wire import (dumps_pack, export_pack, pack_summary, parse_pack, sanitize_pack_id,
    scan_outgoing)  # noqa: F401
from .review import (apply_pack, diff_item, extension_capabilities, extension_warnings,
    is_local_edited, local_rev, mark_edited, opener_warnings, review_pack)  # noqa: F401
from .authoring import compile_phrases, phrases_of, shadow_check, source_version_of  # noqa: F401
from .overlay import (build_module, dataclass_fields, inventory, items_from_module,
    merge_items, module_data, shipped_items)  # noqa: F401
