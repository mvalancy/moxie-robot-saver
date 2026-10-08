# Content authoring

**Status:** P0 shipped (save + a free prompt preview in the parent console). P1's paid *Try it* rung
shipped as the console's 💬 **Try it** card, for **installed** items: any conversation, free chat, or
any brain the appliance offers, through the robot's real brain, with no robot (§5.3). Trying an
**unsaved draft**, the rehearse button and P2 are proposals. The routes are in
[`mqtt/supervisor/moxie_runtime/content.py`](../../../mqtt/supervisor/moxie_runtime/content.py)
(`content_save_item`, `content_render`) and
[`moxie_runtime/tryit.py`](../../../mqtt/supervisor/moxie_runtime/tryit.py) (`tryit_view`,
`tryit_turn`), the phrase compiler and shadow check are in
[`packs/authoring.py`](../../../mqtt/moxie_sdk/content/packs/authoring.py), and the editor and the card
are in [`server/static/js/content.js`](../../../server/static/js/content.js) and
[`tryit.js`](../../../server/static/js/tryit.js). Tested by
[`test_content_authoring.py`](../../../sim/tests/test_content_authoring.py),
[`test_console_tryit.py`](../../../sim/tests/test_console_tryit.py),
[`sim/test_console_tryit.mjs`](../../../sim/test_console_tryit.mjs) and
[`sim/tools/authoring_mutation_check.py`](../../../sim/tools/authoring_mutation_check.py).

This covers [OpenMoxie feature audit](../openmoxie-feature-audit.md) §4.4 **#6**. Packs made content
shareable; this makes it **writable**. A parent can create or edit a conversation or a command in the
Content card without editing JSON or restarting anything. An authored item enters the store through
exactly the functions an imported one does ([`content-packs.md`](content-packs.md)), so there is no
second validator. OpenMoxie's browser harness `/hive/interact` (MIT, © Justin Beghtol; cited by path,
not copied) is the prior art for the *edit → try* loop. Its lessons: keep the loop one click from the
content, match globals too, and be honest that a harness is not the product. Its mistakes: it spends a
model call per try with no budget, and it runs `code`.

---

## 0. Ceilings, stated first

- **No parent has authored anything yet.** Whether a parent handed this form writes content that makes
  their child's Moxie better is a human test (A9–A11). Nothing here settles it.
- **Schedules cannot be authored.** A schedule is the one item kind that reaches the robot
  (`ContentSchedule`), and no physical Moxie has been served a pack-authored one. The editor route
  refuses `kind: "schedule"` with that reason.
- **The hosted Sim cannot author.** `moxie.mattvalancy.com` is a stateless, childless edge tier
  ([`live-sim-demo.md`](live-sim-demo.md)) with no store to write into.

## 2. Background

The content model, the overlay (`shipped defaults ⊕ fleet/content_items.json`), the review states and
the one-slot undo are all defined in [`content-packs.md`](content-packs.md) §2. Three facts from there
carry the authoring design:

- `packs.mark_edited(items, ident, data)` is the one supported way to change an installed item. It
  replaces `data`, keeps provenance and lets `local_rev` drift. **It normalizes but does not validate**
  (§6.3).
- An item with no `imported_rev` counts as locally edited. So an authored item is permanently
  "edited here", and a later pack carrying the same key reviews as `CONFLICT`, un-ticked, with no change
  to `review_pack` (A3).
- A prompt template sees exactly three names: `volley`, `session` and `presence`. With `jinja2` it runs
  in a `SandboxedEnvironment`. Without it, the dependency-free `_minimal_render` resolves only a bare
  `{{ dotted.path }}` and `{% if dotted.path %}`, and counts what it refuses (`render.BLOCKED`) and
  silently drops (`render.STRIPPED`).

## 3. Where authoring lives: the parent console

**Decision:** authoring is a second verb on the existing card (now titled **Content**). It is not
a separate app, not in the SIM, and not a nicer file-plus-git loop.

Why: the console is the only surface that already holds the content store, the validation path and the
rehearsal hook. Authoring there is a form over functions we already have. Every alternative needs a
second copy of at least one of them: a second app needs its own routes and version skew, the SIM has no
store, and a git loop keeps the checkout barrier. Distribution and authoring are one workflow
(inventory → edit → export → share), so one card lets the "edited here" badge, the diff and the export
picker serve both. **What would flip this:** a finding (A9) that the author is usually someone other
than the account holder, such as a therapist or teacher on their own machine. That needs an identity
the console does not have.

### 3.3 What we give up

- **Authoring on the hosted Sim.** This is a property, not a later phase.
- **A second author, and history.** There is one account and one undo slot, shared with imports. A save
  snapshots the overlay first, so **saving twice and undoing once restores the previous save**. Nothing
  older survives. A local edit never bumps `source_version`.
- **Real regular expressions for most authors.** The guided view compiles a phrase list
  (`authoring.compile_phrases`). A hand-written regex does not decompile, so the guided view cannot
  round-trip it.
- **Extension, schedule and `code` authoring** (§4.5), and **deletion**. The overlay never deletes, so
  there is no delete button.

## 4. What a parent edits

### 4.1 Three surfaces, one editor

Progressive disclosure is the answer to "the console is not a developer environment":

| Surface | Shows |
|---|---|
| **Guided** (default) | Name; opening lines (one per alternative, joined with `\|` on save); the prompt with insert-chips; for a command, a list of phrases |
| **Advanced** (`<details>`, closed) | `model`, `max_tokens`, `temperature`, `max_history`, `max_volleys`, the compiled `pattern`, `entity_groups`, `action`, the memory namespace |
| **Raw** (`<details>`, closed) | The item's `data` as JSON plus key, version, provenance and `local_rev`. **Read-only** (R1) |

### 4.2 Field verdicts

| Field | Verdict |
|---|---|
| `name` | Guided. For a global it is also the identity **and** the match order (§4.4) |
| `module_id`, `content_id` | Guided while new, then locked. Changing a saved item's identity is refused ("save it under the new name"), because a rename is a new item |
| `opener`, `prompt` | Guided |
| `model`, `max_tokens`, `temperature`, `max_history`, `max_volleys`, `memory` | Advanced. `model` is a free string for *your* brain, not validated against a vendor list |
| `pattern` | Guided as phrases, advanced as a regex |
| `entity_groups`, `action` | Advanced |
| `code`, `extension` | **Shown, never written.** `extension` shows `ext.explain()` sentences and the grant list. Both must survive a save byte-for-byte, and a save that changes either is refused (T4, T16) |
| any `schedule` field | Refused (§0) |

The form's field list comes from `packs.FIELDS[kind]`, so nothing outside the allowlist can appear.

### 4.3 The prompt box and the closed chip list

The chips (`ED_CHIPS` in `content.js`) insert template fragments at the cursor:

| Chip | Inserts |
|---|---|
| the child's name | `{{ volley.config.child_pii.nickname }}` |
| what Moxie remembers | `{{ volley.persist_data.<ns>.facts }}`, with `<ns>` filled from the item's own namespace |
| someone is in the room | `{% if presence.face_present %} … {% endif %}` |
| the chat has run long | `{% if session.overflow %} … {% endif %}` |

**The list is closed to the two forms the fallback renderer resolves identically to the sandbox.** So a
prompt built from chips renders the same with or without `jinja2` by construction (acceptance criterion
10). An author who types a richer construct by hand is not stopped. They are *told*, because the render
panel (§5.1) shows it is not portable (R2).

Commands get the same treatment. The guided surface is a list of plain phrases, and
`authoring.compile_phrases` escapes them and builds the `pattern`. `phrases_of` reads a pattern back
only when it was built that way, so a hand-written regex opens in the advanced view (§3.3).

### 4.4 The shadow rule: a command's precedence is its name

Globals are tried first-hit in `sorted(kind:key)` order, which is ordering by `name` (by code point, so
case-sensitive) (A4). On save, `authoring.shadow_check` runs **the author's own phrases** against every
installed global that sorts earlier. If one matches, the save response names it:
*"'…' will be answered by **Time** before this one gets a turn, because commands are tried in name
order."* This is exact for the typed phrases and **nothing more**. Deciding whether two arbitrary regexes
overlap is out of scope, and the card says so (A5). The check is advice; it never blocks a save.

### 4.5 What the editor will never author

A schedule (§0). An extension: the text-to-AST surface belongs to
[`sandboxed-extensions.md`](sandboxed-extensions.md), whose compiler must sit outside the trust boundary.
A `code` block, which is never runnable. A capability grant. Anything outside `SPEC`. Another robot's or
child's data: the editor never sees PII, memory, telemetry, permits or config.

## 5. The loop: edit → see → hear → try → keep

| Rung | Fires on | Gateway calls | Writes | Route | State |
|--:|---|:--:|:--:|---|---|
| 0 type | keystroke | 0 | none | — | shipped |
| 1 see what the brain will be told | keystroke, 400 ms debounce | 0 | none | `POST /content/render` | shipped |
| 2 hear the opener performed | a *Rehearse* click | 0 brain (1 TTS if spoken) | none | existing `POST /local/robots/{id}/preview` | **P1** (not wired into the editor) |
| 3 try the conversation | a *Send* click (or Enter), one press = one turn | **1** on a healthy brain; 0 for a command | none | `POST /local/tryit` → supervisor `POST /tryit` | shipped for installed items; a draft is **P1** |
| 4 keep it | a *Save* click | 0 | overlay + backup, then `reload_content()` | `POST /content/item` | shipped |

### 5.1 Rung 1: the resolved prompt, free

`content_render` normalizes the draft, builds a **synthetic** plain-dict context (sample nickname,
sample memory facts, toggleable `face_present`/`overflow`) and renders the prompt twice: with the
installed renderer and with `_minimal_render`. It returns both texts, `portable_identical`, and the
blocked/stripped counts. It calls no brain and touches no store (T10). `render_prompt` takes an
optional `counts=` dict so a caller gets its own counts. The response still sets
`"counts_advisory": true`, because the module-level `BLOCKED`/`STRIPPED` counters are process-global and
move with concurrent turns. **Do not add a lock around the renderer**: the turn loop calls it too.

### 5.2 Rung 3: a paid try that cannot fire on a keystroke

What shipped, item by item:

1. **One call site.** `trySend` in [`tryit.js`](../../../server/static/js/tryit.js) is the only place
   the console calls the brain, bound to the *Send* button and the Enter key (not the Enter that
   commits an IME composition), never a debounce, `oninput` or timer. Pinned twice: `test_the_card_is_labelled_a_preview_and_its_one_brain_call_is_click_bound`
   (one `postJson('/local/tryit'` and no timer in the file) and the browser suite
   [`sim/test_console_tryit.mjs`](../../../sim/test_console_tryit.mjs) (typing alone makes no call; a
   send-as-you-type copy of the file must redden it). The editor's own guard,
   `test_no_timer_in_the_editor_can_reach_a_model`, covers `tryit.js` too, because it loads after
   `content.js`.
2. **A visible budget.** `MOXIE_AUTHOR_TRY_BUDGET` (default 40) tries per rolling hour per appliance,
   read per call by `TryItMixin.try_budget`. Every response carries `budget.remaining` and the card
   shows it; going over is a 429 with a sentence. A try is charged once if it made at least one model
   request (counted where the request is made, `chat.note_model_call`), never per token or retry; a
   command, `echo` and `webhook` (which makes no model request of ours) cost nothing, and a try that
   timed out stays charged. It is not cost control (A6).
3. `MOXIE_AUTHOR_TRY_MAX_TOKENS` is still declared and unread: it caps a **draft's** own `max_tokens`,
   and a draft cannot be tried yet. An installed item answers with the brain's own settings.
4. **Trying a command is free:** it runs `match_global` and the extension evaluator inside the content
   brain, exactly as a turn does, and costs no gateway call (`model_calls: 0`).

### 5.3 The 💬 Try it card: `GET/POST /local/tryit`, exactly

The console proxies `GET/POST /local/tryit` ([`routes/console.py`](../../../server/moxie_server/routes/console.py),
views in [`fleet/tryit.py`](../../../server/moxie_server/fleet/tryit.py)) to the supervisor's
`GET/POST /tryit` ([`moxie_runtime/tryit.py`](../../../mqtt/supervisor/moxie_runtime/tryit.py)). The route
is not `/content/try` because it is not content-only: a parent can try free chat or any brain the
appliance offers, as well as a conversation.

**Request:** `{"speech", "history"?, "device_id"?, "brain"?, "module"?, "nickname"?}`. `speech` is at
most 500 characters (the hosted demo's cap); `history` is the session the card holds, a list of
`{role: user|assistant, content}` lines, cut to the robot's own transcript length
(`MOXIE_MEMORY_TURNS`); a body over 64 KiB is a 413, refused unread. With no `device_id` the appliance's
own child and brain answer, so **no robot is needed**.

**What runs is a robot's turn, minus its transport.** The brain is the one `app_for` would pick for that
robot (`brain_for`), or the one named in `brain`, refused by the same registry and `MOXIE_APP` pin as
the 🧠 card (`normalize_brain_patch`) and built and cached by the same `app_named`. `module` sets the
`module_id`/`content_id` the content brain reads, from the live module, so a pick resolves exactly as
`_active_conversation` would. The `Turn` is assembled as `_on_remote_chat` assembles one; the brain
streams when the runtime streams (`MOXIE_STREAMING`) and the app can; each piece passes the same
output classifier and redirect (a blocked sentence of a streamed answer is replaced and ends it, as on
a robot); a brain that raises is answered by the robot's own fallback, `_safe_respond`; and each piece
is staged by the same `_stage` at the same chunk index as the published stream. Under one turn key,
each piece is byte-identical to what that robot is sent (`test_a_try_stages_exactly_what_a_robot_is_sent`,
`test_each_streamed_piece_is_staged_at_its_own_chunk_index`,
`test_an_unsafe_sentence_in_a_streamed_answer_is_replaced_and_ends_it`), except the markup the free
brain writes itself, which it seeds with the device id (below); for that brain the scored fields match
piece by piece (`test_a_streamed_try_is_chunked_like_the_published_stream`). The tests pin the turn
key; in use a try has its own. The child's line passes the input classifier first; a blocked line is
answered with the redirect and never reaches the brain, and only Moxie's line joins the session, as on
a robot.

**Where a try is not that robot's turn.** Four inputs differ, so a try shows what the brain says, not a
replay of what the robot would do:

- **No device id.** It is what keeps every store path closed (below), and it is also a seed: the free
  brain spaces its talking gestures by device id and line (`LLMApp._turn_key`), and a content extension
  seeds its random choices with it. Where her gestures fall, and an extension's random pick, can
  differ from that robot's.
- **Its own turn key**, `tryit-<ms>` where a robot has its event id. It seeds where `_stage` places
  talking gestures in the markup it builds.
- **Empty presence.** The `Turn` carries the presence of a robot never heard from (nothing known, no
  face in view), not that robot's live one, so a prompt or an extension that reads `presence` sees
  nobody there.
- **The redirect line** is a random pick from the same phrase set, avoiding the session's last line
  rather than the robot's last redirect.

**What never runs:** no MQTT publish, no filler, no transcript (`self.history`), no long-term memory,
no `persist_data`, no telemetry, no safety journal (a verdict is reported to the parent, not filed as
the child's), and no action is carried out (an `<exit>`, `<launch:…>` or `<sleep>` is shown, with the
`RemoteChatAction` it would be). The turn runs as a robot with **no device id**, and every store path a
brain takes is keyed by the device id and does nothing without one, so a try writes zero bytes
(`test_a_try_writes_nothing_and_a_real_turn_through_the_same_runtime_does`, with a real turn as the
negative control). The cost of that: a module whose prompt renders what Moxie remembers sees it
empty.

**Answer:** per piece, the spoken text, the markup, the scored fields, and a readout of the markup
(`read_markup`: faces in order, gestures, whole-body behaviours, voice styles, icons, sounds, pauses,
and any id outside the recovered catalog); the actions; the safety verdicts; the next `history`;
`model_calls` (exact for this try: the turn runs on its own thread, and `moxie_sdk/chat.py` keeps a
per-thread count); `elapsed_ms`; the budget.

**The session lives in the card.** Every send carries it, and only a real answer moves it on: a failed
line stays in the box to send again. Start over, a change of who answers (the brain, the activity or
the child's name) and another robot each start a new session, and an answer still on its way when that
happens is set aside: it is neither shown nor carried, and the card says so. A refresh (after a save,
say) keeps the parent's activity pick, "no particular activity" included; the robot's current activity
fills an empty pick only when the card is first filled or another robot connects. A line typed while
she was answering stays in the box. The browser suite pins each of these with an answer held in flight
(steps 6–8, with teeth).

**Errors, each with a sentence:** 400 (`empty`, `too_long`, `bad_request`, `bad_brain`,
`unknown_module`), 404 (`unknown_device`), 409 (`pending`), 413 (`too_large`: the session has
outgrown 64 KiB, so the sentence says to Start over; the console forwards it as UTF-8, not `\u`
escapes), 429 (`budget`, `busy`:
at most two tries in flight), 503 (`brain_unavailable`: the brain cannot be built here; `unreachable`:
no supervisor), 502 (`brain_unreachable`, `brain_refused`, `brain_error`), 504 (`timeout`, after
30 s) and 500 (`internal`: a fault in the try itself, answered rather than dropped, so the card never
mistakes it for a missing supervisor). A brain failure is told apart from a real answer by how the try's last model request ended
(`chat.last_call_error`, per thread): the app has already turned it into a line for the child, so the
answer carries both that line and the reason (status, error type, and a message with endpoints and
key-shaped runs scrubbed), and the session does not advance. A brain that raises is shown the same way:
the robot's stock line from `_safe_respond`, and what was raised. A try holds the supervisor's console API
(a single-threaded server) for as long as the brain takes, as a voice test does.

A try shows *what the brain says*, not *what the child experiences*, and the card says so: it is
labelled a preview, and nothing it does reaches a robot.

**Still P1: trying an unsaved draft.** The proposal stands: normalize the draft like an import, return
the resolved prompt beside the reply, cap `max_tokens` at `MOXIE_AUTHOR_TRY_MAX_TOKENS`. Today an
author saves (one-slot undo) and then tries.

### 5.4 Rung 2 needs a live device

`preview` publishes an ordinary `remote_chat` to a device id, so it needs a robot or a connected browser
SIM. It 404s an unknown device and 400s a pending one. Rungs 0, 1, 3 and 4 need no device. When P1 wires
rung 2 in, the button should be *disabled with a sentence* when nothing is connected, not hidden. A
device-free "stage only" variant (the planner without the publish) is a small new route and is left to P1.

## 6. How it stays safe: one validation path

### 6.1 The rule

> **An authored item is exactly as untrusted as an imported one, because it enters through the same
> functions.** There is no "we wrote this one" branch.

### 6.2 The gates, all existing code

| # | Gate | Function |
|--:|---|---|
| G1 | Positive allowlist + JSON-only coercion | `packs.normalize_data` |
| G2 | Installability: identity, `pattern` ≤ 512 chars and compilable, `extension` passes `ext.validate(..., allow_p1=True)`, `source_version` a non-negative int | `packs.validate_item` |
| G3 | The template sandbox, every turn and every preview | `render.render_prompt` |
| G4 | Extension validation and capability check, every run | `ext.validate` / `ContentApp.run_extension` |
| G5 | `code` is never executed | no call site exists |
| G6 | Safety classifier on a rehearsed line, and on both sides of a tried turn (reported, never journaled) | `MoxieRuntime._assess` |

### 6.3 The one `if`

`mark_edited` calls `normalize_data` (G1) but **not** `validate_item` (G2). `apply_pack` validates before
writing, and so must the editor:

> **`content_save_item` calls `validate_item` and refuses on any reason, before `mark_edited`.**

Without it, an authored global with an uncompilable `pattern` reaches `Global.from_dict`, which compiles
at load, and the throw takes down `reload_content()` for every item. T2 asserts the refusal returns
`validate_item`'s own sentence. The mutation check deletes the call and requires T2 to go red. The call
lives in the **supervisor route that writes**, never in the console proxy, so a direct `curl` at the
supervisor cannot skip it (R6).

### 6.4 Undo, provenance, conflicts, PII

- A save takes the **same one-slot snapshot** an import does, so `POST /content/undo` restores it
  (`undo_slots: 1` in the response).
- A new item gets provenance `{"origin": "local", "source_version": 1}` and no `imported_rev`. An edited
  item keeps its original provenance. Either way `is_local_edited` is true.
- **Two tabs.** The editor sends the `local_rev` it opened with. A mismatch is a **409**
  (*"this is not the version you opened"*). Two tabs are detected, never merged.
- **PII is flagged on export, not on save.** Authoring is exactly where a child's name legitimately
  enters a prompt. `scan_outgoing` asks the right question at export time. Don't "fix" this.

### 6.5 What authoring must never gain

A path that runs `code` (no flag, no dev mode). A second normalizer or field list. A write path that skips
`reload_content()`, which would leave disk and memory disagreeing. A new remote front door: the routes are
localhost supervisor routes behind the console's existing session.

## 7. Tests

[`sim/tests/test_content_authoring.py`](../../../sim/tests/test_content_authoring.py) covers P0 and
[`sim/tests/test_console_tryit.py`](../../../sim/tests/test_console_tryit.py) the 💬 Try it card. `T`
numbers follow the original plan.

| # | Test | Asserts |
|--:|---|---|
| T1 | `test_authored_item_round_trips` | Save → listed as `local`, edited, prompt byte-identical |
| T2 | `test_a_bad_pattern_is_refused_with_validate_items_own_sentence` | §6.3 |
| T3 | `test_a_field_outside_the_allowlist_never_lands` | Only `FIELDS` are stored |
| T4 | `test_saving_a_name_change_preserves_code_and_extension` | Byte-identical after a rename |
| T5 | `test_authored_then_imported_reports_conflict` | `CONFLICT`, un-ticked |
| T10 | `test_render_route_calls_no_brain` | Rung 1 is free |
| T11 | `test_render_reports_stripped_for_a_construct_the_fallback_drops` | `{% for %}` is flagged non-portable; counts advisory |
| T12, T13 | `test_shadow_warning_names_the_earlier_command`, `test_no_shadow_warning_when_nothing_shadows` | §4.4, both ways |
| T14 | `test_undo_restores_an_authored_save` (+ `test_the_undo_slot_holds_one_save_and_the_route_says_so`) | §6.4 |
| T15 | `test_schedule_is_refused_by_the_editor_route` | §0 |
| T16 | `test_extension_and_code_are_not_writable` | §4.5 |
| T17 | `test_the_authoring_routes_are_declared` | Routes pinned as source literals; `/content/try` absent |
| — | `test_a_second_tab_cannot_silently_discard_the_first`, `test_the_supervisor_route_owns_the_validation_not_the_proxy`, `test_the_chip_list_is_closed_to_the_two_portable_forms`, `test_no_timer_in_the_editor_can_reach_a_model`, the console proxy tests | 409, R6, AC10, the P0 half of T9, end-to-end proxying |
| T6 | `test_a_try_writes_nothing_and_a_real_turn_through_the_same_runtime_does` | Zero bytes written, with a real turn as the negative control |
| T8 | `test_a_busy_appliance_and_a_spent_budget_are_429s` | Budget 429 with `remaining`; a try with no model call is not charged |
| T9 | `test_the_card_is_labelled_a_preview_and_its_one_brain_call_is_click_bound` + `sim/test_console_tryit.mjs` | One click-bound call site; typing alone, or an IME Enter, never calls (teeth) |
| T18 | `test_a_blocked_line_is_redirected_without_the_brain_or_the_journal`, `test_an_unsafe_answer_is_replaced_before_anyone_sees_it_as_hers`, `test_an_unsafe_sentence_in_a_streamed_answer_is_replaced_and_ends_it` | Both classifiers run, streamed or not; nothing is journaled |
| — | `test_a_try_stages_exactly_what_a_robot_is_sent`, `test_a_streamed_try_is_chunked_like_the_published_stream`, `test_each_streamed_piece_is_staged_at_its_own_chunk_index`, `test_a_brain_that_raises_shows_the_robots_own_stock_line_and_why` and the rest of `test_console_tryit.py` | Same brain, prompt, fallback and staging as a published turn; history threading; module and brain choice; actions; every error kind |
| — | `sim/test_console_tryit.mjs` steps 6–8 | A new session (Start over, another brain, activity or robot) wins over an answer still on its way; a refresh keeps the pick (teeth) |
| P1 | T7 a draft try never runs `code` | not yet (a draft cannot be tried) |

The mutation check deletes each guard in turn (the `validate_item` call, the schedule refusal, the
`code`/`extension` refusal, the `reload_content` call, the undo snapshot, the two-tab 409, the
allowlist, the shadow check, the portability probe) and requires a named test to go red.

## 8. Acceptance criteria

P0 criteria are met. Criteria 3–5 are met for installed items (§5.3).

1. A parent can create a conversation from the card with no JSON in the default surface, live on the
   next turn with no restart.
2. Every authored item passes `normalize_data` **and** `validate_item` before it is written.
3. A try costs one brain call on a healthy brain (a failing one is retried with backoff, and each
   attempt counts) and writes zero bytes.
4. A try has exactly one call site, a click handler (the Send button, or Enter in the box).
5. Trying a command costs zero gateway calls.
6. The render panel shows the resolved prompt with no model call.
7. An authored item is locally edited, and a later pack with its key reports `CONFLICT`, un-ticked,
   with no change to `review_pack`.
8. `code` and `extension` survive a save untouched and are not editable.
9. `kind: "schedule"` is refused by the editor route.
10. The guided surface emits only the two template forms the fallback also renders, so a guided prompt
    renders identically with and without `jinja2`.
11. Saving twice and undoing once restores the previous save, and the response says one slot.

## 9. Phases

**P0 (shipped).** The supervisor's `POST /content/item` (validate → snapshot → `mark_edited` → write →
`reload_content`) and `POST /content/render`, in
[`moxie_runtime/content.py`](../../../mqtt/supervisor/moxie_runtime/content.py) and
[`status_http.py`](../../../mqtt/supervisor/moxie_runtime/status_http.py). The console proxies
`/local/content/item` and `/local/content/render` in
[`routes/content.py`](../../../server/moxie_server/routes/content.py), with normalizers
`normalize_content_item_result` / `normalize_content_render` in
[`fleet/content.py`](../../../server/moxie_server/fleet/content.py). `render_prompt(..., counts=)` and
`packs.shadow_check`. In [`content.js`](../../../server/static/js/content.js), the editor's four
seams: `openEditor`, `saveItem`, `renderDraftPrompt` and `renderChips` (pinned by name in a test), plus
`.ed-*` styles in `style.css`. **Not in P0, deliberately:** `/content/try`, any brain call, extension or
schedule editing, a writable raw surface, deletion, and a second author.

**P1, shipped.** The 💬 Try it card (§5.2–5.3): supervisor `GET/POST /tryit` in
[`moxie_runtime/tryit.py`](../../../mqtt/supervisor/moxie_runtime/tryit.py) with its budget, counter and
429; console `GET/POST /local/tryit`; the transcript panel and Send button in
[`tryit.js`](../../../server/static/js/tryit.js); the free command try. T6, T8, T9, T18.

**P1, still a proposal.** Trying an unsaved draft (`MOXIE_AUTHOR_TRY_MAX_TOKENS`, the resolved prompt
beside the reply, T7). *Rehearse this opener* wired to the existing preview route (§5.4). *Export just
this one* from the editor.

**P2 (proposal).** A writable raw JSON surface behind a developer toggle. Starter templates. Extension
authoring, **only** via the sandboxed-extensions text-to-AST compiler. Item removal (needs the overlay to
gain a delete). Schedule authoring (blocked on a physical robot). A second author with an identity.

## 10. Risks and open assumptions

| # | Risk / assumption | Handling |
|--:|---|---|
| R1 | The card drifts into a developer environment | Raw is read-only; fields come from `packs.FIELDS`; widening needs a code change |
| R2 | A prompt renders fine on the appliance and thinly on a bare SDK install | Chips are the two portable forms; rung 1 shows `portable_identical` |
| R3 | A try is mistaken for a turn | The card is labelled a preview and says nothing reaches a robot; T6 asserts the absent writes |
| R6 | Validation placed in the proxy, bypassed by `curl` | It is in the supervisor route; a test asserts the proxy has none |
| R7 | Two tabs edit the same item; the second save discards the first | The save carries its opening `local_rev`; a mismatch is a 409 (§6.4). One undo slot is not a fix |
| R8 | An authored `module_id` names a module the firmware lacks | Warn, never refuse (A8) |
| A1 | `packs.mark_edited` is the one supported way to change an installed item | Proven by its docstring and the store path; the editor uses nothing else |
| A3 | An authored item reviews as `CONFLICT` with no change to `review_pack` | Proven: no `imported_rev` means edited (T5) |
| A4 | Global match order is `name` order | Proven: `module_data` sorts by `kind:key`; `match_global` is first-hit |
| A5 | Checking the typed phrases is the strongest decidable shadow check | Inferred; the check claims nothing more |
| A6 | 40 tries/hour and 300 tokens are right | Chosen, not measured; env vars |
| A8 | A robot ignores an unknown `module_id` rather than failing | Needs hardware |
| A9 | The author is the account holder at the console | Needs a real parent; would flip §3 |
| A10 | A non-programmer can write a useful prompt with a text box, four chips and a render panel | Needs a real parent |
| A11 | Seeing the resolved prompt is worth more than a faster paid try | Needs a real parent; if false, P1's lever is caching, not counting |

---
📖 [Backlog index](README.md) · [OpenMoxie feature audit](../openmoxie-feature-audit.md) ·
[Content-module contract](../content-module-contract.md) · [Content packs](content-packs.md) ·
[Sandboxed extensions](sandboxed-extensions.md) · [Brain picker](brain-picker.md) ·
[Live Sim demo](live-sim-demo.md) · [Expressiveness](expressiveness.md) ·
[The AI seam](../ai-seam.md) · [The SIM as a client](../sim-as-a-client.md) ·
[Moxie as a platform](../moxie-as-a-platform.md) · [Attribution](../../../ATTRIBUTION.md)
