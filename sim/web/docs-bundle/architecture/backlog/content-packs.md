# 📦 Content packs — export, import-with-review, and an upgrade that does not clobber (ADOPT #5)

**Status:** build-ready brief (2026-09-02). **Covers:** [`../openmoxie-feature-audit.md`](../openmoxie-feature-audit.md)
§4.1 **ADOPT #5**, ranked **#1** of the open backlog in that file's §4.4.
**Owner outcomes:** ② *scrape OpenMoxie's best* (this is the last ADOPT item that changes what the
appliance is for) and ③ *ten levels beyond* (an authoring studio, a per-child content library and
anything resembling a marketplace all need a distribution unit first).
**Split:** **P0 is headless and shippable alone** (pure module + store + five status-HTTP routes +
a live reload) in one ~60–90 minute slice; **P1** is the 📦 console card. **P2** is named in §5 and
deliberately not scheduled.

> ## ✅ P0 shipped 2026-09-02 (+ the P1 card)
>
> Built as specified, on `feat/content-packs`. What landed: the pure
> [`mqtt/moxie_sdk/content/packs.py`](../../../mqtt/moxie_sdk/content/packs.py); the three
> fleet `JsonStore` collections; the five status-HTTP routes; `reload_content()`;
> `build_content_app()`'s defaults ⊕ overlay merge; **and** the 📦 console card of §2.6,
> which the split calls P1 — it was in the same brief and is shipped in the same slice.
> 145 new tests (`sim/tests/test_content_packs.py`, `test_content_packs_runtime.py`, 13 in
> `test_console_roundtrip.py`, one in `test_content_app.py`).
>
> **Deviations from this brief, all deliberate:**
>
> | § | Brief says | Shipped | Why |
> |---|---|---|---|
> | 2.5 | `review_pack(pack, installed)` | `review_pack(pack, installed, *, digest="ok", catalog=None)` | The review has to know `parse_pack`'s verdict to tick nothing on a tampered file; both extras are keyword-only with defaults, so the stated signature still calls. |
> | 2.5 | `scan_outgoing` used by an export UI | also runs per row in `GET /content` (`inventory(…, known_names=…)`) | `GET /content/export` returns *the pack JSON* (this brief's shape), which leaves no room for a flag; computing it per inventory row lets the card warn **before** the download instead of after. |
> | 2.5 | `content_import(pack_body, …)` | `pack` may be the object **or the file's raw text** | A browser re-encoding a pack turns `1.0` into `1` and makes a good file report as tampered. The card sends the bytes it read. |
> | 3.8 | "extend the existing no-exec assertion in `test_content_app.py`" | **added** one | There was no such assertion to extend — the promise lived only in a docstring. |
> | 5 | `mqtt/content_modules/*.json` "add `source_version`" | added explicitly as `1` | A no-op at runtime (the default), kept because the file a person edits should name the concept. |
> | 2.4 | `origin: "local"` | provenance keeps its `origin` (`shipped`/`pack`) through a local edit; the edit is detected by `local_rev != imported_rev` | The digest comparison is the load-bearing signal; a second, redundant flag could disagree with it. `mark_edited` is the seam that performs an edit. |
>
> **Gaps that stayed open, on purpose:** no remove-item (P0 has none by design); R3 (ReDoS)
> is still mitigated only by a compile check and a length cap, as §5 says; A9 stands —
> nobody has yet imported a real community pack, so the card's shape is still inferred.

**Reserved-region note up front.** P0 touches the status-HTTP handler block in
[`../../../mqtt/supervisor/moxie_runtime.py`](../../../mqtt/supervisor/moxie_runtime.py)
(`_start_status_server`) and adds one runtime method region. It **must not touch `_push_config`, the
turn/streaming loop, or the safety gates** — nothing a content pack carries in P0 changes
`RobotCloudConfig`, which is exactly why face/config packs are P2 (§5). Playbook rule 10: check
`git diff --name-only` against the in-flight agents' reserved list before opening a PR.

---

## Why this is worth building

Today a new Moxie activity is a file in *our* git repository. To add one you clone the repo, write
JSON, set `MOXIE_CONTENT_MODULE` and restart the supervisor. That is a fine story for us and a
non-story for the person who actually owns the robot: content is the one part of this appliance a
parent, a teacher or a speech therapist has real expertise in, and right now they cannot touch it
without being a developer.

A pack is the smallest thing that fixes that. It is one file. You can email it, put it in a gist, or
publish twenty of them. The appliance shows you what is inside before it installs anything, tells you
which items you have already edited yourself, and lets you undo. The same mechanism upgrades the
content *we* ship: a release that improves the starter chat is just a newer pack, applied by exactly
the same rule that governs a stranger's.

And it is the piece that makes ③ possible at all. "A content-authoring studio for data-driven modules"
([`../orchestration-plan.md`](../orchestration-plan.md), WS-C) has nothing to author *into* until
content has an identity, a version and a way to move between machines.

---

## 0. What our corpus establishes

### 0.1 The content engine, as it stands

[`../content-module-contract.md`](../content-module-contract.md) defines a module as JSON with three
optional sections, and [`../../../mqtt/moxie_sdk/content/module.py`](../../../mqtt/moxie_sdk/content/module.py)
is the loader. The exportable surface is exactly its three dataclasses:

| Kind | Dataclass | Fields (`module.py`) | Identity |
|---|---|---|---|
| `conversation` | `Conversation` | `name`, `module_id`, `content_id`, `prompt`, `opener`, `model`, `max_tokens`, `temperature`, `max_history`, `max_volleys`, `code`, `memory` | `module_id` + `/` + `content_id` |
| `global` | `Global` | `name`, `pattern`, `entity_groups`, `action`, `code` | `name` |
| `schedule` | `Schedule` | `name`, `schedule` | `name` |

Three properties of that loader matter to this design:

1. **`load_modules(data)` already merges a list of module dicts into one `ContentModule`**
   (`module.py`, `load_modules`) — so "the installed content" can be assembled from more than one
   source without new machinery.
2. **`code` strings are never executed.** `ContentApp`'s docstring
   ([`../../../mqtt/moxie_sdk/content/content_app.py`](../../../mqtt/moxie_sdk/content/content_app.py))
   states it: *"arbitrary `code`-string execution from module JSON is deliberately NOT done here"*.
   Behaviour that upstream scripts is **declared** instead (the `memory` block). This is the single
   most important fact in this brief — see §2.2 and the audit's BEYOND #6.
3. **`Global.from_dict` compiles the regex at load time** (`re.compile(g.pattern, re.I)`), so a bad
   pattern in an imported pack throws inside the loader unless the importer validates first.

### 0.2 Where content is loaded from, and where it lives

- One file, chosen by env: `CONTENT_MODULE = os.environ.get("MOXIE_CONTENT_MODULE", "content_modules/starter.json")`
  (`mqtt/config.py`:77), read once by `build_content_app()` (`mqtt/config.py`, ~:196-210).
- The runtime holds the loaded module as **`self.app.module`** and already reads it at
  `moxie_runtime.py`:1941 (`getattr(getattr(self.app, "module", None), "schedules", None)`) to serve
  the day plan. That is the one attribute a reload has to swap.
- Durable state is [`../../../mqtt/moxie_sdk/store.py`](../../../mqtt/moxie_sdk/store.py)'s `JsonStore`:
  atomic `os.replace` writes, a thread lock around read-modify-write, `read`/`write` per device and
  `read_shared`/`write_shared` for appliance-wide collections. Seven collections use it today
  (`mentor_behaviors`, `memory`, `schedule_explain`, `safety_events`, `safety_counts`, and the fleet
  `config` and `permits`). Packs add three more shared ones. It is not a database and this design does
  not need one (audit ADOPT #8).

### 0.3 What the wire cares about

Almost nothing. A content pack is **server-side data**: it changes which prompt the brain is handed and
which regexes match, and none of that is visible to the robot as a new message. The two exceptions,
both real:

- A pack may carry a **`schedule`**, and a schedule *is* served to a robot as `ContentSchedule`
  (`../content-module-contract.md`, "How a `schedules[]` entry becomes the day the robot runs"). A
  pack-authored schedule naming a `module_id` a robot's firmware does not have is the one way a pack
  can reach hardware and fail there — §7.
- Importing changes what Moxie *says next*. It does not interrupt a turn in progress (§2.5).

### 0.4 The ledger — proven / assumed / unknown

| | Claim |
|---|---|
| **Proven (our tree, read 2026-09-02)** | The three dataclasses and their fields; `load_modules` merges a list; `ContentApp` never `exec`s `code`; `Global.from_dict` compiles at load; `self.app.module` is the single holder; `JsonStore` is atomic + locked; `build_content_app` reads exactly one file. |
| **Proven (OpenMoxie, MIT, commit `c8c2d380`)** | Their pack shape, their `source_version` comparison, their two-step review, their index-based selection — all cited by file and line in §1. |
| **Assumed** | That pack authors bump `source_version` (A1, §6); that item keys are stable across versions (A5); that a JSON round-trip is lossless for our field types (A6). Each is named in §6 with what the design does when the assumption fails. |
| **Unknown** | What a real community pack looks like, because there is no community yet. Every UX judgement below is inferred from upstream's form and from our own console idiom, and §6/A9 says so. |

---

## 1. The seam it plugs into

```
                     ┌──────────── P1: 📦 console card ────────────┐
                     │ inventory · export picker · review table    │
                     └───────────────────┬─────────────────────────┘
                                         │ thin proxies (server/moxie_server/main.py)
   ┌─────────────────────────────────────▼──────────────────────────────────┐
   │  status HTTP (moxie_runtime.py::_start_status_server)  — P0            │
   │  GET /content · GET /content/export · POST /content/review             │
   │  POST /content/import · POST /content/undo                             │
   └─────────────────────────────────────┬──────────────────────────────────┘
                                         │
   ┌─────────────────────────────────────▼──────────────────────────────────┐
   │  moxie_sdk/content/packs.py  (NEW — pure, stdlib only)                 │
   │  export_pack · parse_pack · review_pack · diff_item · apply_pack       │
   └─────────────────────────────────────┬──────────────────────────────────┘
                                         │
   ┌──────────────────────┐   ┌──────────▼───────────┐   ┌──────────────────┐
   │ shipped defaults     │ + │ JsonStore (fleet)    │ = │ ContentModule    │
   │ MOXIE_CONTENT_MODULE │   │ content_items.json   │   │ self.app.module  │
   └──────────────────────┘   └──────────────────────┘   └──────────────────┘
```

What it costs today, precisely: `build_content_app()` reads one file and the result is immutable for
the life of the process. There is no inventory, no provenance, no second source, and no way to change
content without a restart.

### Prior art — what OpenMoxie actually does

[jbeghtol/openmoxie](https://github.com/jbeghtol/openmoxie), MIT, © Justin Beghtol, at commit
`c8c2d380`. Described, never copied — see [`../../../ATTRIBUTION.md`](../../../ATTRIBUTION.md).

- **The pack file** is `{"name", "details", "globals": [], "schedules": [], "conversations": []}` —
  their four shipped packs under `content_modules/` (`MemoryChat.json`, `MoxieGo.json`,
  `MoxieTime.json`, `MoxieTimers.json`) confirm the shape. There is **no envelope version, no
  checksum, no author and no timestamp**; the file is whatever `export_data` wrote.
- **Export** is `site/hive/views.py::export_data` (:345-368): for each selected primary key,
  `model_to_dict(r, exclude=['id'])` and append to the right section. It is a *denylist* of one field
  (`id`), so any column added to a model henceforth ships in every future pack automatically.
- **Review** is `site/hive/data_import.py::update_import_status` (:5-30). For each incoming record it
  looks up the installed one by key — `GlobalResponse.name`, `MoxieSchedule.name`,
  `SinglePromptChat(module_id, content_id)` — and stamps `meta_state`:
  `"New"` when absent, `"Upgrade from vN"` when `installed.source_version < incoming.source_version`,
  and `"Replace vN"` otherwise. **That is the whole comparison: two integers.**
- **Import** is `data_import.py::import_content` (:33-79), driven from `views.py::import_data`
  (:393-406). Selection is by **array index** (`g_list`, `s_list`, `c_list`), against a copy of the
  pack JSON that was round-tripped through a hidden form field (`upload_import_data` renders
  `json_data_str`; `import_data` re-parses `request.POST["json_data"]`). Existing records are updated
  with `def_chat.__dict__.update(rec)`.
- **The same rule upgrades their shipped defaults**:
  `site/hive/management/commands/init_data.py` (:20-28, :39-45) applies a bundled file only when
  `installed.source_version < shipped.source_version`. `source_version` is therefore *one* mechanism
  serving both community packs and factory content — that is the good idea here.
- After an import they call `get_instance().update_from_database()` (`views.py`:404) to rebuild the
  live caches, and `/hive/reload_database` exists for the same reason by hand.

**What we port:** the pack-as-one-JSON-file idea; `source_version` as an author-owned integer; the
two-step *review then apply* flow; per-item selection; and `init_data.py`'s "only upgrade when the
shipped version is newer" rule for our own defaults.

**What we deliberately do differently, and why:**

| Theirs | Ours | Reason |
|---|---|---|
| No envelope version | `pack_format: 1` | A format that cannot say what it is cannot be evolved without breaking every reader. |
| No integrity check | `digest` over a canonical serialization | Detects a truncated or edited file and *names* it in the review. |
| Selection by array index | Selection by `kind:key` | An index is only correct if the array is byte-identical to the one the reviewer saw; the pack is re-posted between review and import, so it need not be. |
| `model_to_dict(exclude=['id'])` — a denylist | A positive per-kind field allowlist | A denylist leaks the first time a field is added. On a child's appliance that is not an acceptable failure mode (§2.2). |
| `"Replace vN"` for everything that is not an upgrade | A 2×2 over version **and** local edits (§2.3) | Their review cannot see that *you* edited the prompt; ticking "Upgrade" silently destroys it. |
| No undo | One-slot pre-import snapshot | An import is the only operation here that destroys work. |
| `code` is `exec`'d elsewhere in their runtime | `code` travels as inert data behind a ⚠️ | Installing a stranger's pack must not be arbitrary code execution. |

---

## 2. The design

### 2.1 The pack file — versioned, self-describing, checksummed

```jsonc
{
  "pack_format": 1,                       // reader contract; unknown → refuse, readably
  "id": "bedtime-wind-down",              // stable across versions; [a-z0-9-], ≤ 64
  "name": "Bedtime wind-down",
  "details": "Three calm activities and a slower free chat for the last hour.",
  "author": "",                           // free text, never trusted, always displayed
  "pack_version": 3,                      // the PACK's own release counter (display only)
  "created_at": "2026-09-02T19:40:00Z",
  "generator": "moxie-cloud 0.7.0",
  "items": [
    {"kind": "conversation", "key": "FREE_CHAT/default", "source_version": 3, "data": { … }},
    {"kind": "global",       "key": "Timer",             "source_version": 1, "data": { … }},
    {"kind": "schedule",     "key": "wind_down",         "source_version": 2, "data": { … }}
  ],
  "signatures": [],                       // reserved, unread in P0 — see below
  "digest": "sha256:9f2c…"
}
```

**A flat `items[]`, not three sections.** Every operation here — review state, diff, selection,
conflict, provenance — is *per item*. A flat list makes "import exactly these" a set of keys instead
of three parallel arrays of indexes, which is what makes the selection idempotent and re-runnable.

**The digest.** `sha256` over the canonical serialization of the whole object **with `digest` and
`signatures` removed**:

```python
canonical = json.dumps(body, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False).encode("utf-8")
```

So it survives pretty-printing, key reordering and line-ending changes, and it fails on any content
edit. `parse_pack` reports `digest: "ok" | "mismatch" | "absent"`; a mismatch is **not** fatal (a
hand-written pack is a legitimate thing) but nothing is default-ticked in the review and the card says
*"this file was changed after it was exported."*

**Signed? No — checksummed, and here is the justification.** A detached signature is only worth
anything against a *known publisher*, which needs key distribution, trust roots and revocation. A LAN
appliance with no account system cannot honestly provide any of the three, and a signature verified
against a key that arrived in the same file is decoration that reads as a security guarantee — worse
than nothing. So: `signatures: []` is reserved in the format (adding it later is not a break), and the
security property packs actually need is delivered structurally instead — **an imported pack cannot
execute anything** (§2.2). That is a stronger claim than "signed by someone you have never met."

### 2.2 What goes in a pack, what never leaves, and why `code` is inert

**Exported (P0):** `conversation`, `global`, `schedule` items — built by copying a **fixed, explicit
field list per kind**, exactly the dataclass fields in §0.1 minus derived state (`Global._rx` never
travels). `memory` blocks travel with their conversation: they are content, they name a namespace, and
they carry no remembered data.

**Never exported, ever:**

| Not in a pack | Where it lives |
|---|---|
| `child_pii` in any form — nickname, pronouns, birthday, notes | `ChildProfile`, `robots/*/…` |
| Anything Moxie remembers | `robots/<id>/memory.json` |
| Telemetry, safety events, telehealth transcripts | `robot.extra`, `safety_events`, `safety_counts` |
| Device ids, permits, `mentor_behaviors` | `fleet/permits.json`, `robots/<id>/…` |
| Config overrides (bedtime, volume, alarms, the house look) | `fleet/config.json`, `_config_overrides` |
| Every credential and endpoint | `mqtt/.env`, `MOXIE_*` |

**Allowlist, not denylist** — a denylist leaks the first time somebody adds a field, and test 7 (§3)
pins the allowlist against `dataclasses.fields()` so a new field cannot silently start shipping.

**The residual leak, named honestly.** A parent may have *edited a prompt* to include their child's
name — the exporter cannot know that a string is PII by looking at it. Mitigation: `scan_outgoing`
checks the outgoing text fields against the nicknames the appliance currently knows
(`ChildProfile.nickname`, plus any name in `fleet/config.json`) and flags a hit so the export UI can
say *"this prompt mentions Ada — edit it or export anyway."* It catches the names we know and nothing
else, and the doc must say exactly that.

**`code` is data, never behaviour.** Our engine does not execute module `code` (§0.1), and this design
keeps it that way: a `code` string round-trips as an opaque field, the review marks the item ⚠️
*"carries a `code` block, which this appliance never runs"*, and it stays in the store so a future
sandboxed runtime (audit BEYOND #6) can start executing it *behind a capability declaration* without a
re-import. The honest cost: importing upstream's `MoxieTime` or `MoxieTimers` gives you a global that
matches the utterance and then does nothing, because their behaviour *is* the `code`. Say so in the
review.

### 2.3 `source_version`, `local_rev`, and the review that does not clobber

For every **installed** item the store keeps a provenance record:

```jsonc
"provenance": {
  "pack_id": "bedtime-wind-down", "pack_version": 3,
  "source_version": 2,             // the author's counter, as imported
  "imported_at": 1788400000,
  "imported_rev": "sha256:1a3f…",  // digest of `data` AT import time
  "origin": "pack" | "shipped" | "local"
}
```

`local_rev = sha256(canonical(current data))`. **`local_rev != imported_rev` ⟹ the item has been
edited on this appliance.** That one comparison is the whole difference between our review and
upstream's, and it costs a hash.

The review state of each incoming item is therefore a 2×2, not a 1×3:

| incoming vs installed | local untouched | **locally edited** |
|---|---|---|
| not installed | `NEW` — default **ticked** | *(n/a)* |
| `source_version` **greater** | `UPGRADE v2 → v3` — default **ticked** | ⚠️ `CONFLICT` — *"upgrading v2 → v3 replaces your edits"* — default **un-ticked** |
| `source_version` **equal**, same digest | `SAME` — default un-ticked, nothing to do | `KEEP LOCAL` — default un-ticked |
| `source_version` **equal**, different digest | `FORK` — *"same version number, different content"* — default un-ticked | `FORK` + local edits — default un-ticked |
| `source_version` **lower** | `DOWNGRADE v3 → v1` — default un-ticked | `DOWNGRADE` + `CONFLICT` — default un-ticked |

Upstream collapses the last three rows into `"Replace vN"` and never computes the right-hand column at
all (`data_import.py`:8-11 compares two integers and nothing else). **The requirement this satisfies:
re-importing the same upstream pack after a local edit never clobbers it** — the item comes back
`CONFLICT` or `KEEP LOCAL`, default un-ticked, and a parent who ticks it anyway has `POST /content/undo`.

`FORK` exists because A1 (authors bump `source_version`) is an assumption, not a guarantee: an author
who never bumps would otherwise make every re-import look like a no-op.

### 2.4 Storage and the merge order

Three new **fleet-scoped** `JsonStore` collections (`read_shared`/`write_shared`):

| File | Holds |
|---|---|
| `fleet/content_items.json` | `{"items": {"conversation:FREE_CHAT/default": {"data": {…}, "provenance": {…}}, …}}` — the installed overlay, and the only source of truth for effective content |
| `fleet/content_packs.json` | `{"packs": [{"id", "name", "pack_version", "digest", "imported_at", "item_count"}]}` — the ledger the card lists |
| `fleet/content_backup.json` | the one-slot pre-import snapshot of `content_items.json` + a label, for `undo` |

**Effective content = shipped defaults, then the overlay by key.** `build_content_app()` grows one
step: load `MOXIE_CONTENT_MODULE` as today (each record may carry `source_version`, defaulting to 1),
then apply `fleet/content_items.json` on top by `kind:key`. Because shipped records carry a version,
upgrading *our* content across a release obeys the identical rule as a community pack — upstream's
`init_data.py` idea, taken as behaviour. A shipped item the parent has edited is `origin: "local"` in
the overlay and a release does not silently take it back.

The overlay never *deletes*: P0 has no remove-item operation (an import only adds or replaces). A
parent who wants an activity gone edits its schedule; removal is P2.

### 2.5 Runtime — five routes and one live swap

New pure module `mqtt/moxie_sdk/content/packs.py` (stdlib only, no runtime imports, fully unit-testable):

```python
export_pack(items, *, name, pack_id, details="", author="", now=None) -> dict
parse_pack(raw: bytes | str) -> tuple[dict, dict]     # (pack, {"digest": ok|mismatch|absent, ...})
review_pack(pack, installed) -> list[dict]            # per item: state, defaults, warnings, diff
diff_item(old, new) -> list[dict]                     # field-level; difflib for prompt/opener/code
apply_pack(pack, installed, accept: list[str], *, now=None) -> tuple[dict, dict]  # (items, summary)
scan_outgoing(items, known_names) -> list[dict]       # the PII flag of §2.2
```

Every one of these is a pure function of its arguments — no clock except an injected `now`, no store,
no HTTP. `diff_item` uses `difflib.unified_diff` from the stdlib for `prompt` / `opener` / `code` and
plain `old → new` for scalars; no dependency is added anywhere in this slice.

Runtime methods on `MoxieRuntime` (one contiguous region, away from the turn loop):

- `content_view()` → the inventory + the pack ledger + whether an undo snapshot exists.
- `content_export(keys, name, …)` → the pack dict (the HTTP layer serializes it).
- `content_review(pack_body)` → `review_pack(...)` against the current overlay. **Writes nothing.**
- `content_import(pack_body, accept, expect_digest)` → refuse with **409** if `expect_digest` (the
  digest the reviewer was shown) does not match the digest of the body now being imported; else
  snapshot → `apply_pack` → one `write_shared` → `reload_content()` → summary.
- `content_undo()` → restore the snapshot → `reload_content()`.
- `reload_content()` → rebuild a `ContentModule` from defaults ⊕ overlay and assign `self.app.module`.

**The swap, and what it does not do.** `reload_content()` reassigns one attribute; a turn already in
flight finishes on the module object it started with, and the *next* turn uses the new one. There is
no lock in the turn loop — the same rule the voice picker adopted for engine swaps
([`voice-picker.md`](voice-picker.md)) — and this is documented behaviour, not an oversight. A
conversation session already running keeps its `Conversation` for that session.

Status-HTTP, in the existing `_start_status_server` idiom (localhost-only, same `_json_out`):

| Route | Body / query | Returns |
|---|---|---|
| `GET /content` | — | inventory + ledger + `undo_available` |
| `GET /content/export` | `?items=<kind:key,…>&name=…&id=…` | the pack JSON |
| `POST /content/review` | the pack | per-item review rows; **no writes** |
| `POST /content/import` | `{"pack": {…}, "accept": [...], "expect_digest": "sha256:…"}` | applied/skipped summary, or 409 |
| `POST /content/undo` | — | restored summary |

Body size is capped at `MOXIE_PACK_MAX_BYTES` (default 1 MiB) — refuse larger with **413** rather than
buffering a hostile upload. `POST /content/import` re-sends the pack (the server holds no session
state), which is upstream's shape; the `expect_digest` check is what closes the review-one-file,
import-another gap their hidden form field leaves open.

### 2.6 The console — 📦 Content packs (P1)

Pure normalizer `normalize_content_view(payload)` in
[`../../../server/moxie_server/fleet.py`](../../../server/moxie_server/fleet.py) — same defensive
contract as `normalize_schedule_view`: never raises, a payload it cannot read renders as
`{ok: false, error: …}` with an empty-but-renderable view, so the card shows the reason rather than a
blank list that looks like "no content". Thin proxies in
[`../../../server/moxie_server/main.py`](../../../server/moxie_server/main.py):
`GET /local/content`, `GET /local/content/export`, `POST /local/content/review`,
`POST /local/content/import`, `POST /local/content/undo`.

The card in [`../../../server/static/index.html`](../../../server/static/index.html) +
[`app.js`](../../../server/static/app.js) + `style.css`, mirroring the 📅/🎨 fetch-and-render idiom:

- **Inventory** — one row per installed item: kind glyph, key, `v3`, the pack it came from, an
  *"edited here"* badge when `local_rev != imported_rev`, and a ⚠️ when it carries `code`.
- **Export** — tick rows, type a pack name, **Download**. Any PII flag from `scan_outgoing` appears
  inline above the button and does not block the export.
- **Import** — file picker → review table: state chip (`NEW` / `UPGRADE` / ⚠️`CONFLICT` / `SAME` /
  `FORK` / `DOWNGRADE`), the field-level diff collapsed to a few lines with a *show all* toggle,
  and a tick pre-set to the default in §2.3. Then **Import** / **Cancel**.
- **Undo** — visible only while a snapshot exists, labelled with what it will restore.
- No emoji in new copy beyond the card glyph, per the house convention.

### 2.7 The SIM handler: none, and that is the finding

Content packs never touch the wire (§0.3), so there is **no new SIM handler, no `bridge.js` change and
no `virtual_moxie.py` verb**. The one SIM-visible effect — after an import the next turn runs the new
prompt — is asserted by the existing SIL path rather than by new plumbing: import a pack whose
conversation answers with a fixed sentinel, take one turn, and check the SIM heard it (test 11, §3).
Recording a *no new handler* decision is the point; an agent should not go looking for one.

---

## 3. Tests

Hermetic first; every row is a test a build agent writes, not an aspiration.

| # | Test | Asserts |
|--:|---|---|
| 1 | Pure round-trip | `export_pack(items) → serialize → parse_pack` is identity on the item set; canonical serialization is stable under dict reordering |
| 2 | Tamper detection | flip one byte inside a `prompt` → `parse_pack` reports `digest: "mismatch"`; the review default-ticks **nothing**; a pack with no `digest` parses with `"absent"` and is flagged, not refused |
| 3 | Format guard | `pack_format: 2` → a readable refusal, not a traceback; a missing `items` key, a non-list `items`, a wrong-typed field → the same |
| 4 | Review matrix | one case per cell of §2.3: `NEW`, `UPGRADE`, `CONFLICT`, `SAME`, `FORK`, `KEEP LOCAL`, `DOWNGRADE`, `DOWNGRADE+CONFLICT` — state **and** default tick |
| 5 | The clobber test | import v1 → edit the prompt locally → re-import the same v1 pack ⇒ `KEEP LOCAL`, nothing changes; re-import v2 ⇒ `CONFLICT`, un-ticked; tick it ⇒ applied; `undo` ⇒ the edited version byte-for-byte |
| 6 | Selection by key | `accept` naming a key not in the pack is an **error**, not a silent skip; applying the same import twice is idempotent; an index-shaped `accept` is rejected |
| 7 | Nothing private leaves | seed `child_pii`, memory, telemetry, a safety event, a telehealth transcript, config overrides and a fake key; export everything exportable; assert the serialized bytes contain **none** of the sentinels, and assert the per-kind allowlist equals the intended subset of `dataclasses.fields()` so a new field cannot start shipping unnoticed |
| 8 | `code` stays inert | a pack carrying `code` imports; the review row is ⚠️; the string is present in the store; `ContentApp` still never executes it (extend the existing no-exec assertion in `test_content_app.py`) |
| 9 | Hostile input | a `pattern` that does not compile is refused at review with the item named (never at `load_module` time); `pattern` longer than the cap is refused; a >1 MiB body gets 413 |
| 10 | Runtime | `reload_content()` swaps `self.app.module` and the next fake turn renders the new prompt; a turn started before the swap finishes on the old module; the merge order defaults ⊕ overlay is asserted both ways |
| 11 | HTTP | all five routes through the runtime's own status server: shapes, 404 on unknown, **409 on a review/import digest mismatch**, 413 on oversize |
| 12 | Console round-trip (P1) | `test_console_roundtrip.py` with `importorskip("fastapi")`, extending the existing fake status server: export through `/local/content/export` → feed back to `/local/content/review` → import with an `accept` list → read the inventory back and see the new item with its provenance |
| 13 | Both venv shapes | fast-shaped (no optional deps) **and** full; plus the four doc guards green (playbook rule 13: require the ✅ line, not the tail) |

No live gateway calls are needed anywhere in this slice — budget **0**.

---

## 4. Acceptance criteria

1. An operator can export a named pack of chosen items over HTTP and get one self-describing JSON file
   with `pack_format`, `items[]` and a `digest`.
2. That file, re-imported into a clean appliance, reproduces exactly those items — same fields, same
   `source_version` — and the inventory attributes each to the pack.
3. **A locally edited item is never silently replaced**: re-importing the pack it came from reports
   `KEEP LOCAL` (same version) or `CONFLICT` (newer version) and is un-ticked by default.
4. A parent who imports anyway can `undo` and get the pre-import content back byte-for-byte.
5. A pack edited after export is reported as such, and nothing is pre-selected for import.
6. No exported pack contains child PII, memory, telemetry, safety events, permits, config overrides or
   any credential — asserted by test 7, not by inspection.
7. An imported pack cannot execute anything: `code` is stored, displayed with a warning, never run.
8. Imported content takes effect on the **next** turn with no restart; an in-flight turn is unaffected.
9. The runtime's shipped defaults still load unchanged when no pack has ever been imported — a fresh
   appliance behaves exactly as it does today.
10. (P1) The 📦 card lists the inventory, exports, reviews with a per-item diff, imports and undoes;
    a supervisor that is down renders the reason, not a blank card.

---

## 5. Effort, files, risks

**Effort: P0 ≈ one 60–90 minute slice (S/M).** The engine is pure and the store already exists; the
work is one new module, three collections, five handlers in an established idiom, and the tests. It is
shippable on its own: `curl` can export, review and import, and the acceptance criteria 1-9 are all
reachable headless. **P1 (the card) is a second slice of similar size** and depends only on P0's routes.

**P2 — named, not scheduled** (each is a separate brief-worthy decision):
face/config items in a pack (they touch `_push_config`, which P0 will not);
removing an item; a bundled-pack directory applied at boot;
**reading an OpenMoxie pack** (detect: no `pack_format` + the three section arrays ⇒ treat as an
upstream v0 pack, map their keys, land `code` inert — their four shipped packs become installable,
with the honest caveat of §2.2); detached signatures, if a publisher identity ever exists.

**Files to touch**

| File | Change |
|---|---|
| `mqtt/moxie_sdk/content/packs.py` | **new** — the six pure functions of §2.5 |
| `mqtt/moxie_sdk/content/__init__.py` | export them |
| `mqtt/moxie_sdk/content/module.py` | accept + preserve `source_version` on the three dataclasses (default 1) |
| `mqtt/config.py` | `build_content_app()` applies the overlay after the shipped file |
| [`../../../mqtt/supervisor/moxie_runtime.py`](../../../mqtt/supervisor/moxie_runtime.py) | the content region + five status-HTTP routes + `reload_content()`. **Not** `_push_config`, **not** the turn loop |
| `mqtt/content_modules/*.json` + `README.md` | add `source_version` to the shipped records; document the overlay |
| `sim/tests/test_content_packs.py`, `test_content_packs_runtime.py` | **new** — tests 1-11 |
| `sim/tests/test_content_app.py` | extend test 8 |
| [`../../../server/moxie_server/fleet.py`](../../../server/moxie_server/fleet.py), [`main.py`](../../../server/moxie_server/main.py), [`server/static/`](../../../server/static/) | P1: normalizer, five proxies, the 📦 card |
| `sim/tests/test_console_roundtrip.py` | P1: test 12 |
| [`../content-module-contract.md`](../content-module-contract.md) | a "Packs" section: the file format, `source_version`, the overlay, the conformance line |
| [`../openmoxie-feature-audit.md`](../openmoxie-feature-audit.md) | flip ADOPT #5's Status in the same PR (backlog house rule) |
| [`../../../ATTRIBUTION.md`](../../../ATTRIBUTION.md) | already credits the pack/`source_version` mechanism — extend only if more is taken |

**Risks**

| # | Risk | Mitigation / honesty |
|--:|---|---|
| R1 | A half-applied import | The merged overlay is written **once** through `JsonStore.write_shared`'s atomic `os.replace`, after the snapshot; a crash leaves either the old set or the new one, never a mixture |
| R2 | Import racing a turn | `JsonStore` serializes its own writes; the swap is a single attribute assignment. An in-flight turn keeps the old module — documented (§2.5), not locked |
| R3 | **ReDoS** — an imported `pattern` that backtracks catastrophically | P0 rejects a pattern that fails to compile and caps pattern length, and the review shows every pattern. **Un-mitigated in P0:** a compiled Python regex has no timeout in the stdlib, so a pathological pattern can still stall the matching thread. Named here rather than hidden; a real fix belongs with BEYOND #6's resource limits |
| R4 | A hostile pack rewrites Moxie's persona for a child | It cannot execute, but it *can* change what Moxie says — which is precisely why import is review-gated and the review shows the **full prompt diff**, not a summary |
| R5 | `source_version` is advisory | An author who never bumps makes every re-import a `FORK`; that is why `local_rev` exists and why `FORK` is a distinct state instead of a silent no-op |
| R6 | Field drift — a new dataclass field silently ships in packs | Test 7 pins the allowlist against `dataclasses.fields()`, so adding a field fails the test until someone decides |
| R7 | The review UX is inferred | No real community pack has ever existed; §6/A9 |

---

## 6. The assumption ledger

Every assumption this design rests on, and what happens when it is wrong.

| # | Assumption | If it is wrong |
|--:|---|---|
| A1 | `source_version` is an integer the **pack author** owns and bumps (upstream default 1, `site/hive/models.py`:22,30,117) | `FORK` catches an un-bumped edit; nothing clobbers |
| A2 | The appliance runs **one** content module (`MOXIE_CONTENT_MODULE`, `mqtt/config.py`:77) and P0 keeps it that way — an overlay, not multi-module composition | Composition is P2 and needs its own precedence rule |
| A3 | `self.app.module` is the **only** live holder of the loaded module (`moxie_runtime.py`:1941) — verified by grep, not by a running robot | A second holder would keep stale content after reload; test 10 would catch it |
| A4 | No robot-visible config changes at import time; `_push_config` is untouched in P0 | A face/config pack (P2) breaks this deliberately and must be briefed separately |
| A5 | Item identity is stable: conversation = `module_id/content_id`, global = `name`, schedule = `name` (upstream's keys, `data_import.py`:5,15,25) | A rename reads as a new item; since P0 never deletes, the old one survives and the parent sees both |
| A6 | JSON round-trips our field types losslessly (`temperature` float, `action` int, `memory` dict) | Test 1 fails loudly rather than silently coercing |
| A7 | The exportable surface is the three dataclasses; derived state (`Global._rx`) never travels | Test 7's `dataclasses.fields()` assertion is the tripwire |
| A8 | An OpenMoxie pack is *readable* but its `METHOD`/hook behaviour is **not** portable, because we do not execute `code` | Stated in the review UI; P2 at best gives prompts and regexes, never their timers |
| A9 | The review UX (what a parent wants to see before installing) is inferred from upstream's two-step form and our console idiom — **no real community pack has ever been imported by anyone into this system** | The first real pack will change the card; the pure functions and the store shape are what must not have to change |
| A10 | 1 MiB is a sane pack cap | Configurable (`MOXIE_PACK_MAX_BYTES`); upstream has no cap at all and round-trips the pack through a form field twice |

---

## 7. What only a physical robot can settle

Content packs are server-side data, so — honestly — this brief is almost entirely settleable without
hardware, which is unusual for this backlog and worth stating plainly. The exceptions:

1. **A pack-authored `schedule`.** A schedule is the one item kind that reaches the robot, as
   `ContentSchedule`. Our planner validates `module_id`s against the recovered on-board catalog
   (`moxie_sdk/schedule.py::ONBOARD_MODULES`), but **no physical robot has ever been served a
   pack-authored schedule**, so what a real robot does with an entry naming a module its firmware does
   not have is unknown: ignore it, skip the day, or fail the query. Until that is observed, the review
   should warn on any schedule entry whose `module_id` is outside the catalog.
2. **Whether swapping content mid-session confuses a robot that is already inside an activity.** Our
   design says the next turn uses the new module and the current session keeps its `Conversation`;
   that is a statement about *our* process. Whether the robot's own activity state machine notices
   anything is unobserved.

Everything else in §4 — the format, the digest, the allowlist, the review matrix, the undo, the
no-exec guarantee — is provable in CI on a laptop, today.

---
📖 [Backlog index](README.md) · [OpenMoxie feature audit](../openmoxie-feature-audit.md) · [Content-module contract](../content-module-contract.md) · [Orchestration plan](../orchestration-plan.md) · [Attribution](../../../ATTRIBUTION.md)
