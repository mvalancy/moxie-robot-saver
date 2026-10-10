# Content packs

**Status:** shipped (P0 engine + routes, and the P1 console card). The pure engine is
[`mqtt/moxie_sdk/content/packs/`](../../../mqtt/moxie_sdk/content/packs/). The store and routes are in
[`mqtt/supervisor/moxie_runtime/content.py`](../../../mqtt/supervisor/moxie_runtime/content.py) and
[`status_http.py`](../../../mqtt/supervisor/moxie_runtime/status_http.py). Tested by
[`test_content_packs.py`](../../../sim/tests/test_content_packs.py),
[`test_content_packs_runtime.py`](../../../sim/tests/test_content_packs_runtime.py) and
[`test_content_pack_sandbox.py`](../../../sim/tests/test_content_pack_sandbox.py).

This covers [OpenMoxie feature audit](../openmoxie-feature-audit.md) §4.1 **ADOPT #5**. A **pack** is one
self-describing JSON file of conversations, globals and schedules. You can email it, review it item by
item before anything installs, import it live, and undo it. The same rule upgrades the content *we*
ship, since a release is just a newer pack. Packs are the distribution half. The editor that writes
items into the same store is [`content-authoring.md`](content-authoring.md), and a pack item's
executable `extension` is [`sandboxed-extensions.md`](sandboxed-extensions.md).

**Prior art.** OpenMoxie (MIT, © Justin Beghtol, commit `c8c2d380`) has the pack-as-one-file idea, an
author-owned `source_version`, a review-then-apply flow, and "only upgrade shipped data when its
version is newer" (`site/hive/data_import.py`, `site/hive/views.py::export_data`,
`management/commands/init_data.py`). We port those behaviours, never code
([`ATTRIBUTION.md`](../../../ATTRIBUTION.md)). Where we differ, and why:

| Upstream | Ours | Why |
|---|---|---|
| No format version | `pack_format: 1` | A format that cannot name itself cannot evolve |
| No integrity check | `digest` over a canonical serialization | Names a truncated or edited file in the review |
| Selection by array index | Selection by `kind:key` | The pack is re-posted between review and import; an index need not survive that |
| `model_to_dict(exclude=['id'])`, a deny-list | Positive per-kind field allowlist | A deny-list leaks the first field somebody adds |
| `"Replace vN"` for everything not an upgrade | A 2×2 over version **and** local edits | Upstream cannot see that *you* edited the prompt |
| No undo | One-slot pre-import snapshot | Import is the one operation that destroys work |
| `code` is `exec`'d | `code` travels as inert data | Installing a pack must not be code execution |

---

## 0. What the engine loads

A content module is JSON with `conversations[]`, `globals[]` and `schedules[]`, loaded by
[`module.py`](../../../mqtt/moxie_sdk/content/module.py) (see the
[content-module contract](../content-module-contract.md)). Those three dataclasses are the exportable
surface. `Global.from_dict` compiles its regex at load, so an uncompilable pattern must be refused
**before** it reaches the loader (§2.5, test 9). A pack is **server-side data**: it changes which prompt
the brain gets and which regexes match, and it sends nothing new to the robot. The one exception is a
`schedule`, which is served to the robot as `ContentSchedule` (§7).

## 1. Where it plugs in

```
 Content card (server/static/js/content.js)
        │  /local/content* proxies (server/moxie_server/routes/content.py)
        ▼
 supervisor status HTTP, localhost only (moxie_runtime/status_http.py)
   GET /content · GET /content/export · POST /content/review · /import · /undo
   (+ POST /content/item and /content/render, which belong to content authoring)
        ▼
 moxie_sdk/content/packs/  — pure, stdlib only
        ▼
 shipped defaults (MOXIE_CONTENT_MODULE)  ⊕  fleet/content_items.json  =  app.module
```

## 2. The design

### 2.1 The pack file

```jsonc
{
  "pack_format": 1,                    // anything else: a readable refusal
  "id": "bedtime-wind-down",           // sanitized to [a-z0-9-], ≤ 64 (wire.sanitize_pack_id)
  "name": "Bedtime wind-down", "details": "…",
  "author": "",                        // free text, displayed, never trusted
  "pack_version": 3,                   // the pack's own release counter, display only
  "created_at": "2026-09-02T19:40:00Z", "generator": "moxie-cloud",
  "items": [
    {"kind": "conversation", "key": "FREE_CHAT/default", "source_version": 3, "data": {…}},
    {"kind": "global",       "key": "Timer",             "source_version": 1, "data": {…}}
  ],
  "signatures": [],                    // reserved, unread
  "digest": "sha256:…"
}
```

- **A flat `items[]` keyed `kind:key`**, not three index-addressed sections, so "import exactly these"
  is a set of keys and applying it is idempotent. Identity (`items.item_key`) is upstream's: a
  conversation is `module_id/content_id`, and a global or schedule is its `name`. Duplicate keys are
  refused.
- **The digest** is `sha256` over the canonical serialization (`sort_keys`, `(",", ":")` separators,
  `ensure_ascii=False`, UTF-8) of the whole body **minus `digest` and `signatures`**. It survives
  reformatting and fails on any content edit. `parse_pack` reports `ok`, `mismatch` or `absent`.
  A mismatch is not fatal, since hand-written packs are legitimate, but **nothing is pre-ticked**.
- **Checksummed, not signed.** A signature is only worth something against a known publisher, and a LAN
  appliance has no trust roots. A key that arrives in the same file is decoration. The guarantee packs
  actually rely on is structural: an imported pack cannot execute anything (§2.2).
- `parse_pack` accepts `bytes`, `str` or a dict. The console sends the **raw file text**, because a
  browser re-encoding `1.0` as `1` would make a good file look tampered. A file with no `pack_format`
  (for example an upstream module file) is refused; reading upstream packs is P2.

### 2.2 What goes in, what never leaves, and why `code` is inert

**In a pack:** exactly the fields in `SPEC` ([`items.py`](../../../mqtt/moxie_sdk/content/packs/items.py)),
a positive per-kind allowlist with a coercer and default for each field:

| Kind | Fields |
|---|---|
| `conversation` | `name`, `module_id`, `content_id`, `prompt`, `opener`, `model`, `max_tokens`, `temperature`, `max_history`, `max_volleys`, `code`, `memory`, `extension` |
| `global` | `name`, `pattern`, `entity_groups`, `action`, `code`, `extension` |
| `schedule` | `name`, `schedule` |

Unknown incoming fields are dropped with a named warning and never stored. Test 7 pins `FIELDS` against
`dataclasses.fields()`, so a new dataclass field cannot silently start shipping. A `memory` block travels
(it names a namespace) but remembered data does not.

**Never exported:** child PII (nickname, pronouns, birthday, notes), anything Moxie remembers, telemetry,
safety events, telehealth transcripts, device ids, permits, config overrides, and any credential or
endpoint.

**The residual leak, named.** A parent may have typed their child's name into a prompt. `scan_outgoing`
flags outgoing text that contains a name the appliance knows (connected children's nicknames plus
name-like fleet config values), and the card warns before download. It catches only the names we know.

**`code` is data, never behaviour.** A `code` string round-trips as an opaque field, the review warns
*"carries a `code` block (Python), which this appliance never runs — see `extension` for behaviour this
appliance can run"*, and `ContentApp` has no call site that runs it. The honest cost: upstream's
`MoxieTime`/`MoxieTimers` import as globals that match and do nothing. Behaviour a pack *can* run is
the sandboxed [`extension`](sandboxed-extensions.md).

### 2.3 `source_version`, `local_rev`, and a review that does not clobber

Each installed item keeps provenance: `pack_id`, `pack_version`, `source_version` (the author's
counter), `imported_at`, `imported_rev` (the digest of `data` at import) and `origin`
(`shipped` / `pack` / `local`). [`module.py`](../../../mqtt/moxie_sdk/content/module.py) carries
`source_version` on each dataclass (default 1) purely so the review can tell an upgrade from a re-import.

`local_rev` is the digest of the current `data`. **`local_rev != imported_rev` means the item was edited
here.** An item with no `imported_rev` counts as edited, which is how a locally authored item is
protected. The review state (`review._state`) is:

| Incoming vs installed | Not edited here | Edited here |
|---|---|---|
| not installed | `NEW`, **ticked** | — |
| higher `source_version` | `UPGRADE`, **ticked** | `CONFLICT` ("replaces the changes you made here"), un-ticked |
| equal version, same upstream bytes | `SAME`, un-ticked | `KEEP_LOCAL`, un-ticked |
| equal version, different upstream bytes | `FORK`, un-ticked | `FORK`, un-ticked |
| lower `source_version` | `DOWNGRADE`, un-ticked | `DOWNGRADE_CONFLICT`, un-ticked |
| fails `validate_item` | `INVALID`, never installable, with reasons | |

Only `NEW` and a clean `UPGRADE` are ever pre-ticked (`DEFAULT_ACCEPT`), and only when the digest is
`ok`, the item's extension asks for no new capability (the escalation rule,
[`sandboxed-extensions.md`](sandboxed-extensions.md) §7.3) and no `<mark` in the item's text names a
system command (`system_commands`; the gate never sends one, from a line, an opener, a markup or
the model's line a prompt steers, and the row names every robot command the text writes; a prompt
that asks the model for a mark in other words names nothing here, and the gate on the model's line
is what holds then, the contract's "What pack content may put on the robot"). `FORK` exists because authors may not bump
`source_version` (A1). **Re-importing a pack after a local edit never clobbers it** (test 5). Every row
carries a field-level diff, including a `NEW` row, which shows everything it would install.

### 2.4 Storage and merge order

Three fleet-scoped `JsonStore` collections:

| File | Holds |
|---|---|
| `fleet/content_items.json` | The installed overlay, `{"items": {"kind:key": {"data", "provenance"}}}`, the only source of truth for what differs from shipped |
| `fleet/content_packs.json` | The pack ledger the card lists |
| `fleet/content_backup.json` | The one-slot pre-import snapshot (overlay + ledger + label) for undo |

**Effective content = shipped defaults, then the overlay by `kind:key`**
(`overlay.merge_items` → `module_data` → `build_module`). `build_content_app()` in
[`mqtt/config.py`](../../../mqtt/config.py) records the shipped baseline on the app (`content_defaults`)
separately from the live `module`, so undo can restore a shipped item. Shipped records carry
`source_version`, so our own content upgrades across a release by the same rule as a stranger's pack.
A local edit keeps its original `origin`; the edit is detected by `local_rev`, not by a flag.
`module_data` orders records by `kind:key`, so a global's match order is stable across reloads. **The
overlay never deletes**: there is no remove-item operation.

### 2.5 Runtime: routes and one live swap

The pure API (`moxie_sdk.content.packs`): `export_pack`, `parse_pack`,
`review_pack(pack, installed, *, digest="ok", catalog=None)`, `diff_item`,
`apply_pack(pack, installed, accept, *, now=None)`, `scan_outgoing`, `inventory`, `build_module`. It has
no clock except an injected `now`, no store and no HTTP.

Supervisor routes (localhost-only status server):

| Route | Does | Writes |
|---|---|---|
| `GET /content` | Inventory (with an "edited here" flag, `code` flag and PII flag per row) + ledger + `undo_available` | no |
| `GET /content/export?items=…&name=…&id=…` | The pack JSON; an uninstalled key is an error | no |
| `POST /content/review` | Per-item rows + the `expect_digest` to echo back | **no** |
| `POST /content/import` | `{"pack", "accept", "expect_digest"}`. **409** if the body's digest is not the reviewed one; otherwise snapshot → `apply_pack` → one atomic overlay write → `reload_content()` | yes |
| `POST /content/undo` | Restore the snapshot (overlay and ledger); the slot is then used up. 404 when empty | yes |

A body over `MOXIE_PACK_MAX_BYTES` (default 1 MiB) gets **413** before it is buffered. An `accept` naming
a key not in the pack is a 400, not a silent skip. The server holds no state between review and import.
`expect_digest` is what stops "review one file, import another".

**The swap.** `reload_content()` rebuilds a `ContentModule` and assigns it to `app.module` on every live
content app, including per-child brains. A turn in flight finishes on the module it started with, and the
next turn uses the new one. There is deliberately **no lock** in the turn loop. Nothing is published to
the robot and `_push_config` is never called.

### 2.6 The console card

The **Content** card ([`index.html`](../../../server/static/index.html),
[`js/content.js`](../../../server/static/js/content.js)) sits behind thin proxies in
[`routes/content.py`](../../../server/moxie_server/routes/content.py), with defensive normalizers in
[`fleet/content.py`](../../../server/moxie_server/fleet/content.py). A normalizer never raises: an
unreadable payload renders as a reason, not a blank list. The card offers an inventory with badges, export
with inline PII flags, and a file picker that leads to a review table (state chip, collapsible diff, tick
pre-set to the default). It also offers undo, shown only while a snapshot exists. The same card hosts the
authoring editor.

### 2.7 No SIM handler

Packs never touch the wire, so there is no SIM handler, `bridge.js` change or `virtual_moxie.py` verb.
Don't go looking for one.

### 2.8 Import-path hardening

[`test_content_pack_sandbox.py`](../../../sim/tests/test_content_pack_sandbox.py) fences the whole path a
pack travels: `parse_pack` → review → apply → store → reload → `render_prompt` → brain. Two findings are
now fixed:

- **`_minimal_render` was an attribute-chain escape.** On an install without `jinja2`, the fallback
  renderer walked a dotted path with `getattr` over live objects. `{{ session.__class__.__repr__.__globals__… }}`
  reached `os.environ`, API key included, and put it into the system prompt. It now refuses any
  `_`-leading segment and counts it in `render.BLOCKED`, matching the Jinja sandbox. The container, which
  ships `jinja2`, was never exposed.
- **A `NEW` row showed no diff**, even though it is the one state that is pre-ticked. It now shows every
  field.

The round trip is tested as a full circle: export → parse → apply into an empty appliance → export gives
the same bytes, including across two runtimes with separate data dirs.

---

## 3. Tests

Row numbers are cited from the test files. All hermetic; no gateway calls.

| # | What is proven | Where |
|--:|---|---|
| 1 | Round trip is identity and byte-stable; canonical form ignores key order; field types survive JSON | `test_content_packs.py` |
| 2 | Tamper detection: `mismatch` pre-ticks nothing; no digest is `absent`, flagged, not refused | `test_content_packs.py` |
| 3 | Format guard: wrong `pack_format`, missing or non-list `items`, duplicate keys, non-UTF-8 give readable refusals | `test_content_packs.py` |
| 4 | The review matrix: every cell of §2.3, state **and** default tick; review writes nothing | `test_content_packs.py` |
| 5 | The clobber test: edit locally, re-import v1 gives `KEEP_LOCAL`, v2 gives `CONFLICT`, undo restores the edit byte for byte | `test_content_packs.py`, `test_content_packs_runtime.py` |
| 6 | Selection by key: unknown key is an error, index-shaped `accept` rejected, idempotent | `test_content_packs.py` |
| 7 | Nothing private leaves; the allowlist is pinned to `dataclasses.fields()` | `test_content_packs.py` |
| 8 | `code` stays inert: imports with a warning, kept in the store, never executed | `test_content_packs.py`, `test_content_app.py` |
| 9 | Hostile input: uncompilable or over-long (`MAX_PATTERN_CHARS` = 512) pattern refused at review; unknown schedule module warns | `test_content_packs.py` |
| 10 | Runtime: live next turn, in-flight turn unaffected, defaults ⊕ overlay both ways, undo, ledger | `test_content_packs_runtime.py` |
| 11 | HTTP: every route, 404 unknown, 409 digest mismatch, 400 bad key, 413 oversize, two-appliance re-export | `test_content_packs_runtime.py` |
| 12 | Console round trip through `/local/content*` | `test_console_roundtrip.py` |

## 4. Acceptance criteria

All met. Tests cite them by number.

1. An operator can export a named pack over HTTP: one file with `pack_format`, `items[]` and `digest`.
2. Re-imported into a clean appliance, it reproduces those items exactly, attributed to the pack.
3. A locally edited item is never silently replaced (`KEEP_LOCAL` / `CONFLICT`, un-ticked).
4. Undo restores the pre-import content byte for byte.
5. A pack edited after export is reported, and nothing is pre-selected.
6. No exported pack contains child PII, memory, telemetry, safety events, permits, config overrides or
   credentials (test 7).
7. An imported pack cannot execute anything: `code` is stored, shown with a warning, never run.
8. Imported content is live on the **next** turn with no restart; an in-flight turn is unaffected.
9. With nothing imported, the shipped defaults load unchanged.
10. The console card lists, exports, reviews with a diff, imports and undoes, and a down supervisor
    renders a reason.

## 5. Known gaps and P2

- **ReDoS.** A compile check and a 512-char cap are the only defence. A compiled Python regex has no
  timeout, so a pathological imported `pattern` can still stall the matching thread. The review shows
  every pattern (*"listens for this on every turn: …"*).
- **One undo slot.** An import or an authored save overwrites it.
- **Not scheduled (P2):** removing an item; face/config items in a pack (they would touch `_push_config`);
  a bundled-pack directory applied at boot; **reading an OpenMoxie pack** (no `pack_format`, three
  section arrays: map it as a v0 pack, `code` inert); detached signatures once a publisher identity
  exists; multi-module composition.

## 6. Assumptions

| # | Assumption | If wrong |
|--:|---|---|
| A1 | Authors bump `source_version` | `FORK` catches an un-bumped change; nothing clobbers |
| A2 | One content module per appliance, plus an overlay | Composition is P2 and needs a precedence rule |
| A3 | `app.module` (on each live content app) is the only live holder | A second holder would keep stale content; test 10 would catch it |
| A5 | Item identity is stable across versions | A rename reads as a new item; the old one survives |
| A6 | JSON round-trips our field types | Test 1 fails loudly |
| A9 | The review UX is right | Inferred: no real community pack has been imported yet |
| A10 | 1 MiB is a sane cap | `MOXIE_PACK_MAX_BYTES` |

## 7. What only a physical robot can settle

1. **A pack-authored `schedule`**, the one kind that reaches the robot. No physical Moxie has been served
   one, so what firmware does with an unknown `module_id` is unknown. The review therefore warns on any
   entry outside the recovered on-board catalog (`items.unknown_schedule_modules`, checked against
   `moxie_sdk/schedule.py::ONBOARD_MODULES`).
2. **Swapping content mid-activity.** Our process switches on the next turn. Whether the robot's own
   activity state notices is unobserved.

Everything else here is provable in CI.

---
📖 [Backlog index](README.md) · [OpenMoxie feature audit](../openmoxie-feature-audit.md) · [Content-module contract](../content-module-contract.md) · [Orchestration plan](../agent-workflow.md) · [Attribution](../../../ATTRIBUTION.md)
