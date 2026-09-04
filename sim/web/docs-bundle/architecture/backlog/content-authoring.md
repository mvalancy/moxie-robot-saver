# ✍️ Content authoring — the verb packs did not ship (§4.4 #6)

> **Backlog brief v1 · 2026-09-03.** The build document for
> [OpenMoxie feature audit](../openmoxie-feature-audit.md) **§4.4 #6** — *"Packs made content
> **shippable**; nothing made it **writable**. A parent can install a stranger's conversation and diff
> it against their own, and still cannot compose one without editing JSON — and upstream's browser chat
> harness (`/hive/interact`) remains a genuinely better authoring loop than ours."*
>
> The row was 🟠 **needs-a-spec** and this page exists to remove the decision it owed. **§3 makes the
> choice** — authoring lives in the parent console, as a second verb on the 📦 card — **§4** says what a
> non-programmer may and may not write, **§5** costs the loop in gateway calls, and **§6** shows that an
> authored item passes through *exactly* the functions an imported one does, with no second validator
> anywhere.
>
> **Clean-room.** Every claim about our code below was read on `origin/dev` at `56ac5e5` and cites a
> file and a symbol. **OpenMoxie** (MIT, © Justin Beghtol) is read as prior art and cited by path: we
> describe what `/hive/interact` does and port the **behaviour** of its loop. No upstream code enters
> this tree. See [`ATTRIBUTION.md`](../../../ATTRIBUTION.md).

---

## 0. The ceiling, stated first

**No parent has ever authored anything on this appliance, and this brief cannot change that.** Every
other section is a design; the question this page most wants answered — *does a parent, handed a form,
write a conversation that makes their child's Moxie better?* — is a human test with a human subject and
we have neither. It is filed as **A9** and **A10** in §10, both marked *needs a real parent*, and
nothing in P0, P1 or P2 moves them.

Two more ceilings, up front, so no reader has to find them in a risk table:

- **A schedule is the one item kind that reaches the robot**, and no physical Moxie has ever been
  served a pack-authored one ([content-module contract](../content-module-contract.md), *Unsettled
  without hardware*). This brief therefore **does not let a parent author a schedule at all** — §4.5.
- **The hosted Sim cannot author.** `moxie.mattvalancy.com` runs a deliberately stateless, childless
  edge tier ([`live-sim-demo.md`](live-sim-demo.md)); there is no store to write into. A visitor will
  be able to *hear* authored content and never write it, and that is demo-mode working as designed,
  not a gap scheduled for later.

---

## 1. Why this is 🟠 today, and exactly what a spec has to remove

The audit's own framing is the honest one: **packs shipping is what exposed this.** Before
2026-09-02 there was no way to move content between machines either, so "you cannot write content"
read as one item on a long list. Now there is an inventory card, a review table, a field-level diff, a
digest, an undo and five routes — and the thing all of it moves is still only ever produced by editing
`mqtt/content_modules/*.json` in a git checkout.

A build agent sent at *"content authoring"* today has to decide four things before writing a line, and
each of them is a design argument rather than an implementation:

| # | The owed decision | Where this brief settles it |
|--:|---|---|
| D1 | **Where authoring lives** — console, a separate tool, or the SIM | §3.1 compares four options against six criteria; §3.2 chooses the console and defends it |
| D2 | **What a parent edits** — a form, a guided flow, a text box; and what is off-limits | §4.2's field table, §4.3's trigger decision, §4.5's refusals |
| D3 | **The loop, and what it costs** — edit → hear it → keep it, in gateway calls | §5's five rungs, §5.2's budget, §5.3's `try` route |
| D4 | **How it stays safe** without a second validation path | §6's six gates, all existing functions, and the one `if` that is missing today |

**If a reader finishes §3–§6 and still has to choose something before building, this brief failed** and
the audit row stays 🟠. §11 says which of the four are settled and which are not.

---

## 2. What is actually true today — verified on `origin/dev`, 2026-09-03

### 2.1 The content model, exactly

Three dataclasses in [`mqtt/moxie_sdk/content/module.py`](../../../mqtt/moxie_sdk/content/module.py),
and the pack allowlist `SPEC` in [`packs.py`](../../../mqtt/moxie_sdk/content/packs.py):123-141 is
pinned against `dataclasses.fields()`, so these two lists cannot drift.

| Kind | Fields (`packs.FIELDS`) | Identity (`item_key`) |
|---|---|---|
| `conversation` | `name`, `module_id`, `content_id`, `prompt`, `opener`, `model`, `max_tokens`, `temperature`, `max_history`, `max_volleys`, `code`, `memory`, `extension` | `module_id/content_id` |
| `global` | `name`, `pattern`, `entity_groups`, `action`, `code`, `extension` | `name` |
| `schedule` | `name`, `schedule` | `name` |

Two of those fields are not text a person types:

- **`prompt` is a sandboxed Jinja2 template.**
  [`render.py`](../../../mqtt/moxie_sdk/content/render.py)`::render_prompt(template, context)` is a
  pure function over a plain dict; with jinja2 present it runs a `SandboxedEnvironment` with
  `ChainableUndefined`, and without it a dependency-free `_minimal_render` whose refusals are counted
  in the module-level `render.BLOCKED` and whose silent degradations are counted in `render.STRIPPED`.
  Both counters are **process-global integers**, which matters in §5.1. The context a template sees is
  **exactly three top-level names** — `volley`, `session` and `presence` (`content_app.py`:312 for the
  opener, :371 for the prompt) — which is what makes §4.3's chip list closeable at all.
- **`extension` is a JSON-AST program** validated by
  [`ext.py`](../../../mqtt/moxie_sdk/content/ext.py)`::validate()` — 53 frozen operators, no `exec`, no
  loops, and capabilities checked in both directions at load
  ([`sandboxed-extensions.md`](sandboxed-extensions.md)).

And one is inert on purpose: **`code` has never been executed** and never will be
(`content_app.py`; contract, *"`code` is data, never behaviour"*).

### 2.2 The store, the overlay, and the seam that already exists

Effective content is `shipped defaults ⊕ overlay`, keyed `kind:key`
(`packs.merge_items` → `module_data` → `build_module`), held in three fleet `JsonStore` collections
(`fleet/content_items.json`, `fleet/content_packs.json`, `fleet/content_backup.json`), and swapped live
by `reload_content()` reassigning one attribute.

**The editor's write path is already written, and it is waiting for this brief.**
`packs.py`:865 `mark_edited(items, ident, data)` — replace an item's `data`, keep its provenance, let
`local_rev` drift from `imported_rev`. Its own docstring says so:

> *"P0 has no editor in the console; this is the seam the tests (and a future 📦 edit button) use, and
> it is the only supported way to change an installed item's content."*

Two consequences fall straight out of code that already exists, and both are exactly what authoring
needs:

1. **An authored item is permanently `local_edited`.** `is_local_edited` treats *no `imported_rev`* as
   edited, deliberately (`packs.py`:577). So a stranger's pack that later carries the same key reports
   `CONFLICT`, defaults un-ticked, and cannot clobber a parent's own work — **with no change to the
   review logic at all**.
2. **`module_data` sorts by `kind:key`**, so a global's match order is alphabetical by its `name`
   (`ContentModule.match_global` returns the *first* pattern that fires, `module.py`:175). Stable
   across reloads — and, for an author, invisible. §4.4.

### 2.3 The routes that exist, and the one that does not

Supervisor status HTTP (localhost-only), proxied by the console at `/local/content*`
([`server/moxie_server/main.py`](../../../server/moxie_server/main.py):1068-1122):

| Route | Exists | Writes |
|---|:--:|---|
| `GET /content` — inventory + pack ledger + `undo_available` | ✅ | no |
| `GET /content/export` — build a pack from ticked items | ✅ | no |
| `POST /content/review` — what *would* happen, per item, with the diff | ✅ | **no** |
| `POST /content/import` — apply exactly the accepted `kind:key` ids | ✅ | yes, + backup |
| `POST /content/undo` — restore the one-slot snapshot | ✅ | yes |
| **`POST /content/item` — save one authored item** | ❌ | — |
| **`POST /content/render` — resolve a draft prompt, no model** | ❌ | — |
| **`POST /content/try` — one volley against a draft** | ❌ | — |

Everything above the line is the distribution half. The three below it are this brief.

### 2.4 The rehearsal loop we already have — and what it does *not* rehearse

`POST /local/robots/{id}/preview` (`main.py`:645) → the supervisor's `/preview` → `rt.preview()`:
it runs the **output-side safety classifier** on the line, stages it through the behavior planner and
**publishes it as an ordinary `remote_chat`**, so whatever is subscribed as that device performs it —
the browser SIM, `virtual_moxie.py`, or a robot paired for rehearsal. There is deliberately no
SIM-specific API ([`sim-as-a-client.md`](../sim-as-a-client.md)). It calls **no brain**, writes no
history, and `speak` is off by default so it spends **no voice call** either. The console's
🎬 Rehearsal card (`index.html`:281, `app.js`:873 `pvRun`) is its whole UI, and it does not poll —
*"a rehearsal happens when someone asks for one"*, which is the same rule §5.2 applies to a try.

**What it rehearses is a *line*.** It answers *"how does Moxie perform this sentence?"* It cannot
answer *"what does Moxie say when my child says X to the conversation I just wrote?"*, because it never
renders a prompt and never calls a brain. That second question is the one upstream's harness answers
and ours does not, and §5.3 is the route that closes it.

### 2.5 Prior art — `/hive/interact`, read closely

Upstream's `InteractionView` (`site/hive/views.py`, `templates/hive/interact.html`, jQuery AJAX)
is a text box bound to one conversation module plus the global matcher, running the real
`SingleContextChatSession` against a real OpenAI call, on a page you reach from the dashboard beside
the module list. It is ~40 lines of view code and it is genuinely better than anything we have.

**What it gets right, and what we should port as behaviour:**

- **The loop is one page away from the content it edits.** You are looking at the module list; you
  click; you talk to it. No second tool, no restart, no file.
- **Globals are matched too**, so *"moxie what time is it"* can be tried in the same box as the chat —
  which is how you discover that your new global shadows an old one.
- **It is honest about being a harness**: no robot, no audio, no session persistence. Nobody mistakes
  it for the product.

**What it gets wrong, and what we should not port:**

- **It is a chat box, not an editor.** You still author in the Django admin (or a JSON file), then
  switch pages to try it. The loop is *try*, not *edit → try*.
- **Every keypress-to-answer costs a real model call** and there is no budget, no counter and no cap.
  On a shared appliance that is a bill; on ours it is also a live-Sim rate-limit problem
  ([`live-sim-demo.md`](live-sim-demo.md)).
- **It runs the `code` field**, because upstream `exec`s it (`conversations.py`:271). We never will
  (§6.5), so our harness answers a *smaller* question — and must say which.

---

## 3. Where authoring lives — four options, evaluated, then one choice

The six questions that actually decide it: **is the author already there**; **can it reach the content
store at all**; **does it inherit the existing validation path or need a new one**; **can the author
hear the result in the same loop**; **what does it cost to build and to keep, including on the hosted
static Sim**; and **does it turn a parent surface into a developer environment**.

### 3.1 The comparison

| | **(a) The parent console** (FastAPI + the existing cards) | **(b) A separate authoring tool** (second web app, desktop app, or `moxie-content` CLI) | **(c) The SIM** (`sim/web`, beside the 3D robot) | **(d) The file + git loop, made nicer** (JSON Schema, a linter, a watcher) |
|---|---|---|---|---|
| **Author already there?** | **Yes.** It is the only surface a parent has ever opened — pairing, look, plan, memory, safety, voice, packs. The 📦 card already lists the very items this would edit. | No. A second thing to download, run, and keep in step with the appliance's version. The audit's own BEYOND #10 complaint is *"one appliance, one identity, one command"*; this adds a second. | Partly — but the SIM's visitor is a *developer or a demo watcher*, not the parent who owns the robot. | No. It is a git checkout, which is precisely the barrier this row exists to remove. |
| **Reaches the store + overlay?** | **Yes**, today: `/local/content*` already proxies all five pack routes to the supervisor that owns `fleet/content_items.json`. | Only by re-implementing those routes, or by emitting a pack file the parent then imports — which is authoring *into* the distribution channel, a strictly longer loop than the one we have. | **No.** The browser SIM has no store; the hosted edge tier is stateless and childless **by design**. Authoring there means inventing a second content store on the edge. | Yes — by editing the shipped module files. But a shipped file is not the overlay, so a parent's edit is a working-tree change, not appliance state. |
| **Inherits the safety path?** | **Free.** `normalize_data` / `validate_item` / `render_prompt` / `ext.validate` are all one process away and already on the import path (§6). | Free **only** if it round-trips through a pack. Any direct write is a second validator, which §6's rule forbids. | n/a — nothing persists, so nothing is validated. | Free at *load*; nothing checks a file before it is on disk, so a bad regex is discovered by a failed reload. |
| **The loop (hear it)?** | **Best available.** 🎬 Rehearsal is already on this page and `POST /preview` publishes to whatever is subscribed as the device — the browser SIM included. The author gets the SIM's advantage without moving authoring into it. | Needs its own preview, or a hop back to the console — which is upstream's *edit here, try there* split, the thing we are trying to beat. | **Best "hear it", worst "keep it".** The robot is right there; there is nowhere to save. | None. Edit, restart, listen, repeat. |
| **Build + maintenance cost** | One card grows two verbs; three routes; **no new infrastructure, no new store, no new auth story**. | A second app, a second release artefact, a second version-skew surface, a second thing to document. | A second content store on the edge, plus a merge story back to the appliance. Directly contradicts the demo-mode non-negotiable. | A JSON Schema and a linter are cheap and genuinely useful — and benefit **only** people who already have a checkout. |
| **Turns a parent surface into a dev env?** | **Yes — and this is the real cost.** Answered in §4 by *what the form contains*, not by where it lives. | No. This is (b)'s one genuine advantage, and §3.2 point 4 is why it does not win. | The SIM is already a developer surface, so no new cost — but it also has no parent. | No. |

### 3.2 The choice

> **(a) — the parent console, as a second verb on the existing 📦 card rather than a tenth card.**

The one-sentence reason: **the console is the only surface that already holds the content store, the
validation path and the rehearsal hook, so authoring there is a form over three functions we have,
while every other option is a second copy of at least one of them.**

Longer form, because a build agent will be asked to defend it:

1. **The seam is pre-declared in code.** `packs.mark_edited` exists, is tested, and its docstring names
   *"a future 📦 edit button"* as its consumer. Building anywhere else abandons a decision this
   codebase already made and paid for.
2. **Distribution and authoring are one workflow, not two features.** Inventory → edit → export →
   hand to a friend → they review and import. Split across two surfaces, a parent who edits a prompt
   cannot see the *"edited here"* badge their own edit just created, and cannot tick it into an export
   without switching tools. Folding the editor into the 📦 card (renamed **📦 Content**) makes the
   badge, the diff and the export picker do double duty for free.
3. **The rehearsal advantage is portable; the store is not.** `POST /preview` publishes an ordinary
   turn, so the SIM performs an authored opener whether or not the author is *in* the SIM. The reverse
   is not true: no amount of console work gives the SIM a place to save. Option (c) trades the thing
   that cannot be moved for the thing that already is.
4. **"The console is not a dev environment" is an objection to the *form*, not to the *location*.** A
   card whose default surface is a name, an opening line and a text box with insert-chips is not a
   developer environment. A card with a JSON textarea would be one wherever it lived — including in
   option (b). §4 is therefore where that objection is actually answered, and it is answered by
   refusing to put a JSON editor in the default surface at all.
5. **It is the only option that leaves the appliance a single command.** BEYOND #10 already complains
   that we have two registries reconciled by an env var; shipping a second application to fix the
   content gap would make it three surfaces and two installs.

**What would flip this decision** (write it down now, so a later reader can check it rather than
re-argue it): if a real parent test (**A9**) shows that authoring is done by *someone other than the
account holder* — a speech therapist, a teacher, a grandparent working from their own machine — then
the authoring surface needs an identity the console does not have, and (b) becomes right. That is a
finding about people, not code, and it is exactly what §10's unverified rows are for.

### 3.3 What we are giving up, said plainly

- **Authoring on the hosted Sim.** `moxie.mattvalancy.com` is stateless and childless on purpose. A
  stranger will hear authored content and will never write it. Not a phase-3 item — a property.
- **A second author.** One console, one account, one undo slot. No collaboration, no review-my-draft,
  no two people editing the same conversation. If two do, the last save wins and the loser has one
  `undo` between them.
- **Version history.** `source_version` is the *author's own* counter and a local edit deliberately
  does not bump it (that is what makes `local_rev` meaningful). So there is **no history**: edit twice
  and the first version is gone, because `undo` holds one pre-import snapshot, not a stack. Naming
  this now is cheaper than a parent discovering it.
- **Real regular expressions, for most authors.** §4.3 compiles a phrase list; a phrase list cannot
  express what a regex can. An author who needs one opens the advanced view and owns the result — and
  the guided view can no longer round-trip that item, which the card must say out loud.
- **Extension authoring.** A parent will not write a JSON-AST program in this card, in any phase. The
  text→AST surface is [`sandboxed-extensions.md`](sandboxed-extensions.md) §11 P1's job, and building a
  second compiler here would be the same mistake that brief refused in its own §7.4. This card
  **shows** an extension (`explain()` sentences + `grant_list()`) and never edits one.
- **Schedule authoring.** §0. The one kind that reaches the robot, unobserved on hardware.
- **Deletion.** `merge_items` has no remove operation — the overlay only adds or replaces
  (`packs.py`:946). An authored conversation can be emptied but not deleted, and the card must not
  offer a ✕ that does not exist. Removal is the packs brief's own P2.
- **A rehearsal with nothing connected.** Rung 2 publishes to a device, so with no robot and no SIM there is nothing to publish to and the button is disabled. A device-free `stage`-only variant is cheap and is P1's, not P0's — §5.4.
- **Authoring `code`.** Forever. §6.5.

---

## 4. What a parent edits — and what they cannot

### 4.1 Three surfaces, one editor

The card opens one editor with three progressively-disclosed surfaces. **Progressive disclosure is the
whole answer to "the console is not a dev environment"**: the developer surface exists, and a parent
never sees it.

| Surface | Who opens it | What it shows |
|---|---|---|
| **Guided** (default, always open) | anyone | A name; an opening line; the prompt as a text box with insert-chips; for a command, a list of phrases. Nothing that looks like code. |
| **Advanced** (a `<details>`, closed) | a parent who has a reason | `model`, `max_tokens`, `temperature`, `max_history`, `max_volleys`, the compiled `pattern` as an editable field, `entity_groups`, `action`, the memory namespace toggle. |
| **Raw** (a second `<details>`, closed, read-only in P0) | a developer | The item's `data` as pretty JSON, plus its `kind:key`, `source_version`, provenance and `local_rev`. **Read-only** — this is a window, not an editor, and P1 is where it becomes writable if anyone asks. |

### 4.2 The field table

Every field of every kind, decided. *Guided* = in the default surface. *Advanced* = behind the
disclosure. *Shown* = rendered read-only, with an explanation. *Refused* = not in this editor at all.

| Kind | Field | Verdict | Note |
|---|---|:--:|---|
| conversation | `name` | **guided** | Free text. Also the row label in the inventory. |
| conversation | `module_id` · `content_id` | **guided, then locked** | Editable only while the item is new; after the first save they are the item's identity (`item_key`) and changing them is a *new item*, not an edit. The card says so rather than silently forking. |
| conversation | `opener` | **guided** | One line per alternative in a small list; joined with `\|` on save — the author never types the delimiter. Inline tags (`<opener>`, `<exit>`, `<launch:MOD>`) stay literal and are shown in a hint, not offered as chips (they are model-agency tags, not authoring vocabulary). |
| conversation | `prompt` | **guided** | The main text box. §4.3's chips. |
| conversation | `model` · `max_tokens` · `temperature` | **advanced** | `model` is a free string served to *your* brain ([`ai-seam.md`](../ai-seam.md)), not a vendor list; the card must not pretend to validate it. |
| conversation | `max_history` · `max_volleys` | **advanced** | Integers with the dataclass defaults pre-filled (40 / 40). |
| conversation | `memory` | **advanced, structured** | A namespace field and an on/off, never free JSON. |
| conversation | `code` | **shown** | Read-only, with the review's own wording: *"carries a `code` block (Python), which this appliance never runs — see `extension` for behaviour this appliance can run"*. Survives a save untouched. |
| conversation | `extension` | **shown** | `ext.explain()` sentences + `ext.grant_list()`. Read-only. Survives a save untouched. |
| global | `name` | **guided** | Also the identity **and** the match order (§4.4). |
| global | `pattern` | **guided as phrases; advanced as regex** | §4.3. |
| global | `entity_groups` · `action` | **advanced** | Meaningless without a handler or an extension; the card says so beside them. |
| global | `code` · `extension` | **shown** | As above. |
| schedule | *all* | **refused** | §0 and §4.5. |

**Survives a save untouched** is a hard requirement, not a nicety: the editor round-trips the *whole*
normalized `data`, so opening a shipped item that carries `code`, changing its `name`, and saving must
not drop the `code`. The test for it is T4.

### 4.3 The prompt box, and the closed chip list

A prompt is a Jinja2 template, and a parent will not learn Jinja2. The editor offers a fixed set of
**insert-chips** that write a template fragment at the cursor:

| Chip | Inserts | Why it is on the list |
|---|---|---|
| the child's name | `{{ volley.config.child_pii.nickname }}` | The one substitution every authored prompt wants. |
| what Moxie remembers | `{{ volley.persist_data.<ns>.facts }}` | `<ns>` is filled from the item's own memory namespace, so the chip cannot name another module's memory. |
| someone is in the room | `{% if presence.face_present %} … {% endif %}` | The vision snapshot the runtime already carries into the prompt. |
| the chat has run long | `{% if session.overflow %} … {% endif %}` | The documented session flag. |

**The chip list is closed, and it is closed at exactly the contract's documented forms** — a bare
`{{ dotted.path }}` and an `{% if dotted.path %}`. That is not an arbitrary restriction; it is the
intersection the dependency-free fallback renders identically to the sandbox
([content-module contract](../content-module-contract.md), *"For a module author"*). So **every
prompt authored through the guided surface renders the same on an appliance with jinja2 and on a bare
`pip install moxie-cloud-sdk` without the `content` extra** — by construction, not by discipline. An
author who types a richer construct by hand is not prevented; they are *told*, by §5.1's render panel
showing a non-zero `STRIPPED`.

### 4.4 The shadow rule — an authored command's precedence is its name

`match_global` returns the **first** pattern that fires, and `module_data` builds the list
`sorted(kind:key)` — so for globals the match order is **alphabetical by `name`**. A parent naming a
command *"Ask the time"* beats one named *"Time"*, and nothing on the screen would say so.

**The check, and its honest bound.** On save, compile the draft pattern and run **the author's own
phrase list** against every installed global's pattern in `sorted` order. If any installed global
matches one of those phrases and sorts *before* the draft, the card says, in one sentence:

> *"'moxie what time is it' will be answered by **Time** before this one gets a turn, because commands
> are tried in name order."*

This is exact for the phrases the author actually typed — which is the case that matters — and it is
**nothing more than that**. Deciding whether two arbitrary regexes overlap is not a thing we will do,
and the card must not imply it has. Stated as **A5**.

### 4.5 What this editor will never author

The §13-style list, deliberately explicit:

- **A schedule.** It is the one kind that reaches the robot as `ContentSchedule`, and no physical Moxie
  has ever been served a pack-authored one. Authoring one would put an unobserved wire behaviour behind
  a parent-facing button.
- **An extension.** The AST stays read-only. [`sandboxed-extensions.md`](sandboxed-extensions.md) P1
  owns the text surface, and its compiler is deliberately *outside* the trust boundary — the wrong
  thing to reimplement here.
- **A `code` block.** Not editable, not creatable, not runnable. Read-only forever, with the ⚠️
  wording the review already uses. §6.5.
- **A capability grant.** Grants come from an extension's declared set, checked in both directions at
  load. There is no screen on which a parent widens one, because there is no extension editor.
- **Anything outside `SPEC`.** The editor's field list is derived from `packs.FIELDS[kind]` at build
  time, so a new dataclass field cannot appear in the form until someone adds it to the allowlist —
  the same pin that already guards exports.
- **Another robot's or another child's data.** Content is fleet-scoped; the editor never sees
  `child_pii`, memory, telemetry, permits or config.

---

## 5. The loop — edit → hear it → keep it, and what it costs

Upstream's advantage is the *loop*, not the editor. Ours has five rungs and **exactly one of them
spends a gateway call.**

### 5.1 The five rungs

| Rung | Fires on | Gateway calls | Store writes | Route |
|--:|---|:--:|:--:|---|
| 0 · **type** | keystroke | **0** | none | — |
| 1 · **see what the brain will be told** | keystroke, debounced 400 ms, and on demand | **0** | none | `POST /content/render` *(new)* |
| 2 · **hear the opener performed** | an explicit *Rehearse* click | **0** (brain) · **1 TTS** only if *"say it out loud"* is ticked | none | `POST /local/robots/{id}/preview` *(exists)* |
| 3 · **try the conversation** | an explicit *Try it* click, one press = one turn | **exactly 1 brain call** | none | `POST /content/try` *(new)* |
| 4 · **keep it** | an explicit *Save* click | **0** | overlay + backup, then `reload_content()` | `POST /content/item` *(new)* |

**Rung 1 is the highest-value free feedback we can give and nothing today shows it.**
`render_prompt(template, context)` is pure and takes a plain dict, so the route builds a synthetic
context (a sample nickname, a sample memory block, `presence.face_present` toggleable, `session`
flags) and returns the resolved text. The panel is *the actual system prompt the brain would receive* —
the thing a prompt author most needs and currently cannot see at all.

**One implementation trap, named because a reviewer will miss it.** `render.BLOCKED` and
`render.STRIPPED` are **process-global counters**, so a before/after delta around one call is polluted
by any concurrent turn. P0 reports the delta and labels it **advisory** in the response
(`"counts_advisory": true`); the clean fix is an optional out-parameter on `render_prompt` so a caller
gets its own counts, and that is the **one line of `render.py` this brief touches** — see §9's file
list. Do not "fix" it by taking a lock around the renderer: the turn loop calls it too.

### 5.2 Why a preview never fires on a keystroke, and the budget that proves it

*A preview that spends a model call per keystroke is not shippable* — so the design makes that
structurally hard rather than merely intended:

1. **`/content/try` is called from exactly one place in `app.js`, and it is a click handler.** No
   debounce, no `oninput`, no `onblur`, no timer, no retry. A source-level test (T9) asserts the call
   site count is 1 and that it is bound to a button — the same idiom `ext.py` uses when it asserts over
   its own source with `ast`.
2. **A budget, visible before it bites.** `MOXIE_AUTHOR_TRY_BUDGET` (default **40**) tries per rolling
   hour per appliance. Every response carries `remaining`, the card renders it beside the button
   (*"37 tries left this hour"*), and over budget is a **429 with a plain sentence**, not a stack trace.
   The budget is an appliance-level counter, not per-item, because the bill is per-appliance.
   **It counts calls, not tokens, and that is forced rather than chosen:** there is no token accounting
   anywhere in this codebase — [`chat.py`](../../../mqtt/moxie_sdk/chat.py) captures no `usage`, keeps
   no spend total and caches no response; its `Pacer` is an *adaptive self-throttle* that reacts to the
   gateway's 429s, which is a politeness mechanism and not a budget. Counting presses is the only lever
   that exists today, and a reader should not mistake it for cost control (**A6**).
3. **`max_tokens` comes from the draft and is capped.** A try honours the item's own `max_tokens`
   (that is part of what is being authored) but never exceeds `MOXIE_AUTHOR_TRY_MAX_TOKENS`
   (default 300), so a mistyped `max_tokens: 100000` costs a normal turn, not a large one.
4. **Trying a *command* costs nothing.** A global try is `match_global` + `explain()` + the extension
   evaluator — **zero** gateway calls, always. The card should say so, because it changes how an author
   works: commands are free to iterate, conversations are not.

### 5.3 `POST /content/try`, exactly

Request: `{"kind": "conversation", "data": {…the draft…}, "speech": "…", "history": [...], "device_id": "…"}`.

It **must**:

- `normalize_data("conversation", data)` then `Conversation.from_dict(...)` — the draft is normalized
  before anything touches it, exactly as an imported item is;
- render the prompt with the same `render_prompt` and return the resolved text alongside the reply, so
  the answer and its cause are on screen together;
- call the brain through the same `ChatFn` the runtime uses, so the try uses the robot's *actual*
  selected brain ([`brain-picker.md`](brain-picker.md)) and not a second configuration;
- carry conversation state **in the request** — `history` is echoed back and held by the browser;
- **run the same output-side safety assessment `preview` already runs** before the reply is shown, so
  the authoring harness cannot become the one path around the classifier. `preview` does this today
  (`_assess(line, …)`, returning the reason to the *author* rather than a child-facing redirect) and
  `try` inherits the behaviour rather than reinventing it.

It **must not**: write the overlay, write `persist_data`, write memory, write telemetry, count as a
turn, fire execution actions, publish anything to MQTT, or run `code`. It is a *pure* function of the
draft plus the request, one brain call wide. T6 and T7 assert the absence of each write by comparing
the store's bytes before and after.

**The honest asymmetry:** a try is not a turn. It skips the safety classifier's turn-level bookkeeping,
the memory merge, the telemetry write and the planner's publish. What it proves is *what the brain says
to this prompt*, which is the question that was missing. It does not prove *what the child experiences*
— that is rung 2 plus a real session. The card says so in one line, the way upstream's harness is
honest about being a harness.

### 5.4 What "hear it" means with no robot in the house — and the one rung that needs a device

Rung 2 publishes an ordinary `remote_chat` to a **device id**, so it is performed by whatever is
subscribed as that device. With a robot: the robot. With the browser SIM connected: the SIM's 3D Moxie,
with face, arms and gaze. Either way the response also carries the staged `Performance` JSON, and the
card renders the beats as text — mood, dialog act, gesture, gaze, and `dropped`, every id the validator
refused — so an author who cannot *watch* still reads what would have played.

**With neither, rung 2 does not exist**, and a build agent must not be told otherwise.
`MoxieRuntime.preview` 404s an unknown `device_id` and 400s a robot that is still pending, and the
console's `refreshPreview(deviceId)` (`app.js`:838) simply hides the 🎬 card when `refreshLive` passes
`null`. So the honest rule for the editor is the same one every other card already follows:

- **Rungs 0, 1 and 4 — type, see the resolved prompt, save — work with no device at all.** They are
  the whole of P0, which is why P0 ships without touching `preview`.
- **Rung 2 is enabled only when a device is live**, and when it is not the button is disabled with a
  sentence (*"connect Moxie or open the simulator to watch her perform it"*) rather than absent —
  an author should learn the capability exists.
- **Rung 3 (`try`) needs no device**, because it never publishes: it renders, calls the brain and
  returns text. An author with no robot can still iterate on a prompt.

**A device-free rehearsal is cheap and is deliberately deferred.** `perform()` (the planner) is already
a separate call from `_publish_chat()` inside `preview`, so a `stage`-only variant that returns the
`Performance` without publishing is a small change — but it is a **new route with a new contract**, and
P0 does not need it. It is named here so P1 can take it as one line of scope rather than rediscovering
it, and it is the reason §9 keeps `preview` out of P0's file list entirely.

---

## 6. How it stays safe — one validation path, not two

### 6.1 The rule

> **An authored item is exactly as untrusted as an imported one, because it enters through the same
> functions.** There is no "we wrote this one, so it is fine" branch anywhere in the design.

This is not a slogan about intent. It is a claim about call sites, and §6.2 lists them.

### 6.2 The six gates, all of them existing code

| # | Gate | Function | Already on the import path? |
|--:|---|---|:--:|
| G1 | **The positive allowlist**, plus JSON-only coercion (`_d` does `json.loads(json.dumps(v))`) | `packs.normalize_data(kind, data)` | ✅ |
| G2 | **Installability** — identity present, `pattern` ≤ `MAX_PATTERN_CHARS` and `re.compile`-able, `extension` passes `ext.validate(..., allow_p1=True)`, `source_version` a non-negative int | `packs.validate_item(item)` | ✅ (`review_pack` names it; `apply_pack` enforces it) |
| G3 | **The template sandbox**, on every turn and on every render preview | `render.render_prompt` | ✅ (unchanged) |
| G4 | **The extension validator + capability check**, at load, in both directions | `ext.validate` / `ContentApp.run_extension` | ✅ (unchanged — and the editor cannot author an extension at all) |
| G5 | **`code` is never executed** | `content_app.py` — there is no call site | ✅ (unchanged) |
| G6 | **The output-side safety classifier**, on a rehearsed line and on a tried reply | `MoxieRuntime._assess` (already on `preview`) | ✅ (unchanged — `try` inherits it, §5.3) |

**Nothing new is validated by anything new.** The editor's contribution to safety is a `try`/`except`
around G2 and the sentences it already returns.

### 6.3 The one gap `mark_edited` leaves, and the line that closes it

`mark_edited` calls `normalize_data` (G1) and **does not call `validate_item`** (G2) —
`apply_pack` does that itself before writing (`packs.py`:803). So:

> **`POST /content/item` must call `validate_item({"kind", "key", "data", "source_version"})` and refuse
> on a non-empty result, before `mark_edited`, exactly as `apply_pack` does.**

That is one `if`, and it is the single most important line in the build. Without it an authored global
with a non-compiling `pattern` reaches `Global.from_dict`, which compiles at load — and a throw inside
the loader takes down `reload_content()`. `validate_item`'s own docstring says that is why the check
lives there. **T2** asserts the refusal with `validate_item`'s own sentence; the mutation run deletes
the call and requires T2 to go red.

### 6.4 Undo, provenance, and the review that already works

- **The same one-slot undo.** A save snapshots `fleet/content_backup.json` exactly as an import does,
  so `POST /content/undo` restores an authored save with no new mechanism. One slot, shared — saving
  twice loses the first version (§3.3).
- **Provenance stays honest.** `mark_edited` on an item that does not exist creates it with
  `{"origin": "local", "source_version": 1}` and no `imported_rev`; on one that does, it keeps the
  original provenance and lets `local_rev` drift. Both make `is_local_edited` true, which is correct:
  a later pack carrying that key reports `CONFLICT` and defaults un-ticked. **The review's 2×2 needs no
  new state and no new verb.**
- **PII is flagged on export, not on save — deliberately.** `scan_outgoing` flags a prompt containing a
  known nickname. Authoring is precisely where a child's name legitimately enters a prompt, so the
  editor must **not** nag on save; the export picker already asks the right question at the right
  moment (*"this prompt mentions Ada — edit it or export anyway"*). Recorded here because a reader
  would otherwise "fix" it in the wrong direction.

### 6.5 What authoring must never gain

- **A path that executes `code`.** Not a flag, not a dev mode, not "only for locally authored items".
  The property that makes a pack safe is structural, and it is worth more than the convenience.
- **A second normalizer or a second field list.** The form is generated from `packs.FIELDS[kind]`; a
  hand-maintained list would drift and the drift would be silent.
- **A write path that skips `reload_content()`**, leaving disk and memory disagreeing about what Moxie
  will say next.
- **An authenticated remote surface.** These routes are localhost-only supervisor routes behind the
  console's existing session, like every other `/local/*`. Authoring does not open a new front door.

---

## 7. Tests

Hermetic first. `sim/tests/test_content_authoring.py` unless named otherwise.

| # | Test | Asserts |
|--:|---|---|
| T1 | `test_authored_item_round_trips` | Save a new conversation → `GET /content` lists it with `origin: "local"`, `local_edited: true`, and `build_module` yields a `Conversation` whose `prompt` is byte-identical |
| T2 | `test_a_bad_pattern_is_refused_with_validate_items_own_sentence` | A global whose `pattern` does not compile → 400, and the message **is** `validate_item`'s string. Goes red if the `validate_item` call is deleted |
| T3 | `test_a_field_outside_the_allowlist_never_lands` | A save carrying `secret: "…"` → the stored `data` has exactly `FIELDS["conversation"]` and nothing else |
| T4 | `test_saving_a_name_change_preserves_code_and_extension` | Open a shipped item carrying both, change `name`, save → `code` and `extension` are byte-identical |
| T5 | `test_authored_then_imported_reports_conflict` | Author `global:Time`, then review a pack carrying `global:Time` at a higher `source_version` → state is `CONFLICT`, `accept` defaults exclude it |
| T6 | `test_try_writes_nothing` | Byte-compare every `fleet/*` collection before and after a `/content/try`; zero differences, with a **negative control** (a `/content/item` in the same test *does* change bytes, so the probe can see a write) |
| T7 | `test_try_never_runs_code` | A draft whose `code` defines `post_process` that would raise → the try returns a normal reply |
| T8 | `test_try_budget_returns_429_with_remaining` | Budget set to 2 → the third call is 429 and `remaining` is 0 on all three |
| T9 | `test_try_has_exactly_one_call_site_and_it_is_a_click` | Source assertion over `server/static/app.js`: one occurrence of `/content/try`, inside a click handler, none inside an `oninput`/`setTimeout`/`setInterval` |
| T10 | `test_render_route_calls_no_brain` | A fake `ChatFn` that raises if called; `/content/render` returns resolved text |
| T11 | `test_render_reports_stripped_for_a_construct_the_fallback_drops` | A `{% for %}` in the draft → the response's advisory counts show a `STRIPPED` increment |
| T12 | `test_shadow_warning_names_the_earlier_command` | Install `global:Time`; author `global:When is it` whose phrase also matches `Time`'s pattern → the save response names `Time` and says commands are tried in name order |
| T13 | `test_no_shadow_warning_when_nothing_shadows` | The same shape with a disjoint phrase → no warning (so T12 cannot pass vacuously) |
| T14 | `test_undo_restores_an_authored_save` | Author → save → `POST /content/undo` → the item is gone and the pack ledger is unchanged |
| T15 | `test_schedule_is_refused_by_the_editor_route` | `POST /content/item` with `kind: "schedule"` → 400, naming §0's reason |
| T16 | `test_extension_and_code_are_not_writable` | A save that changes `extension` on an item that has one → refused, with the sentence pointing at the extensions brief |
| T17 | `test_the_authoring_routes_are_declared` | The console's route decorators pinned as **literal source strings** in `main.py` — the idiom `sim/tests/test_brain_console.py`:179 already uses for `/local/robots/{device_id}/brain`, so a route that quietly moves fails a test rather than a parent |
| T18 | `test_try_runs_the_safety_classifier` | A draft whose reply would be flagged → the try surfaces the reason to the author (G6), proving the harness is not a path around the classifier |
| M1 | `sim/tools/authoring_mutation_check.py` | Deletes each guard in turn (the `validate_item` call, the kind refusal, the budget check, the `extension`/`code` write refusal, the `reload_content` call) and requires a named test to go red |

**What only a real deployment or a real parent can settle** is §10's A9/A10/A11 — and no test above
touches them.

---

## 8. Acceptance criteria

1. A parent can create a conversation from the 📦 card, with **no JSON visible** in the default
   surface, and Moxie uses it on the next turn with no restart.
2. Every authored item passes `normalize_data` **and** `validate_item` before it is written — provable
   by deleting either call and watching a named test go red (M1).
3. A `/content/try` costs **exactly one** brain call and writes **zero** bytes to the store (T6, with
   its negative control).
4. `/content/try` has exactly one call site in the console and it is a click handler (T9).
5. Trying a **command** costs zero gateway calls.
6. The render panel shows the resolved system prompt with no model call (T10).
7. An authored item is `local_edited` and a later pack carrying its key reports `CONFLICT`,
   un-ticked (T5) — **with no change to `review_pack`**.
8. `code` and `extension` round-trip a save untouched and are not editable (T4, T16).
9. `kind: "schedule"` is refused by the editor route (T15).
10. The guided surface can only emit the two template forms the dependency-free fallback also renders,
    so a guided prompt renders identically with and without jinja2.
11. Saving twice and pressing undo once restores the *previous* save, and the card says one slot.
12. The audit's §4.4 row 6 and [`backlog/README.md`](README.md) are flipped in the same PR (house rule).

---

## 9. Effort and the file list

### P0 — **S/M**, one agent, one sitting, shippable alone

*The editor for what already exists: save, and the free feedback.*

| Order | File | Change |
|--:|---|---|
| 1 | `sim/tests/test_content_authoring.py` | T1–T5, T10–T15 first; they fail |
| 2 | `mqtt/moxie_sdk/content/render.py` | **One line only**: an optional out-parameter so a caller gets its own `blocked`/`stripped` counts (§5.1). The sandbox is untouched |
| 3 | `mqtt/moxie_sdk/content/packs.py` | `shadow_check(draft, installed)` — pure, phrase-list-exact, §4.4. No change to `SPEC`, `validate_item`, `review_pack` or `apply_pack` |
| 4 | `mqtt/supervisor/moxie_runtime.py` | `POST /content/item` (validate → `mark_edited` → snapshot → write → `reload_content`) and `POST /content/render`. Both fleet-level, beside the five that exist |
| 5 | `mqtt/config.py` | `MOXIE_AUTHOR_TRY_BUDGET`, `MOXIE_AUTHOR_TRY_MAX_TOKENS` (declared in P0, consumed in P1) |
| 6 | `server/moxie_server/main.py` | `/local/content/item` and `/local/content/render` proxies, beside `content_review` / `content_import` |
| 7 | `server/moxie_server/fleet.py` | `normalize_content_item_result` — the same shape as `normalize_content_result` |
| 8 | `server/static/index.html` | The 📦 card renamed **📦 Content**; a `＋ New` row and a per-item ✏️; the editor panel with its three `<details>` surfaces |
| 9 | `server/static/app.js` | `openEditor` / `saveItem` / `renderDraftPrompt` / `renderChips`, beside `renderContentCard` (:1355) and `reviewContentFile` (:1444). The editor joins the card lifecycle the same way every other card does — a `refreshContent`-shaped hook called from `refreshLive()` (:141), which polls `GET /local/fleet` and passes `null` when there is no live robot. All `/local/*` calls pass `auth:false`, like every existing one |
| 10 | `server/static/style.css` | The editor panel under an `.ed-` class prefix, following the per-card prefix convention (`.pv-*` for 🎬 Rehearsal). No new design language, no new custom properties |
| 11 | `sim/tools/authoring_mutation_check.py` | M1. Also add the new suite to the CI coverage ratchet, whose lists can only shrink |
| 12 | `docs/architecture/content-module-contract.md` | An *Authoring* section under **Content packs**: the three routes, the chip list, the field verdicts |
| 13 | `docs/architecture/openmoxie-feature-audit.md` · `docs/architecture/backlog/README.md` | Flip row 6 and this brief's state, same PR |

**Not in P0, deliberately:** `/content/try`, any brain call, extension editing, schedule editing, a
writable raw surface, deletion, and any second author.

### P1 — **M**

*The paid rung, and the loop closing.* `POST /content/try` (§5.3) with its budget, counter and 429 · the
transcript panel and the *Try it* button · a *Rehearse this opener* button wiring the editor to the
existing `POST /local/robots/{id}/preview` · *Export just this one* straight from the editor (a
one-item `export_pack`) · the free command-try (`match_global` + `explain()`, zero calls) · T6–T9, T16.

### P2 — **L**

*What needs somebody else's decision first.* A writable raw JSON surface behind a developer toggle ·
starter templates a parent can begin from (which is a content question, not a code one) · extension
authoring — **only** by adopting [`sandboxed-extensions.md`](sandboxed-extensions.md) P1's text→AST
compiler, never a second one · item removal (needs `merge_items` to gain a delete, the packs brief's
own P2) · schedule authoring (**blocked on a physical robot**, §0) · a second author with an identity,
which is really BEYOND #10's registry question wearing a different hat.

### Risks

| # | Risk | Mitigation |
|--:|---|---|
| R1 | **The card becomes a developer environment anyway.** One `<details>` at a time, and in a year the default surface is a JSON blob. | The raw surface is **read-only in P0** and the field list is generated from `packs.FIELDS`. Widening it needs a code change and a reviewer, not a config flag. |
| R2 | An author writes a prompt that renders fine on the appliance and thinly on a bare-metal SDK install. | The chip list is exactly the two portable forms (§4.3), and the render panel surfaces `STRIPPED` for anything else. |
| R3 | **A try is mistaken for a turn** and an author ships a conversation that behaves differently for a child. | §5.3's honest line in the card, and the safety/memory/telemetry absences asserted in T6/T7 rather than described. |
| R4 | The budget is wrong — 40/hour is a guess and a real author burns it in ten minutes. | It is an env var, `remaining` is on screen before it bites, and **A6** says plainly that it is chosen, not measured. |
| R5 | The shadow check gives false comfort — an author reads *"no conflicts"* as a guarantee about all utterances. | The sentence is scoped to the phrases the author typed (§4.4), and the card says so in the same breath. |
| R6 | `validate_item` is called in the console proxy instead of the supervisor route, so a direct `curl` bypasses it. | The check belongs to the route that **writes**, never to the proxy. T2 drives the supervisor route directly. |
| R7 | Two browser tabs edit the same item; the second save silently discards the first. | The save carries the `local_rev` it opened with; a mismatch is a **409** with the same wording the import conflict uses. One slot of undo is *not* a fix for this and must not be described as one. |
| R8 | An authored conversation's `module_id` names a module the robot's firmware lacks. | The same warning the review already emits (`unknown_schedule_modules`' sibling for conversations) — warn, never refuse, because the catalog is ours and the firmware is theirs. |

---

## 10. Assumption ledger

**Twelve rows: five proven, four inferred, three unverified.** **Three of the three unverified
(A9, A10, A11) need a real parent**; **one (A8) needs a physical robot.** That is the honest ceiling on
this area and no phase moves it.

| # | Assumption | State | How it gets settled |
|--:|---|:--:|---|
| A1 | `packs.mark_edited` is the only supported way to change an installed item's content, and it exists for exactly this consumer | **proven** | `packs.py`:865 and its docstring — *"a future 📦 edit button"* |
| A2 | `mark_edited` normalizes but does **not** validate, so an editor route must call `validate_item` itself | **proven, by reading both** | `mark_edited` calls `normalize_data` only; `apply_pack`:803 calls `validate_item` before writing. This is §6.3's whole point, and M1 keeps it true |
| A3 | An authored item is `local_edited` for free, so a later pack reports `CONFLICT` with **no change to `review_pack`** | **proven** | `is_local_edited`:577 — no `imported_rev` ⇒ edited, deliberately. T5 pins it |
| A4 | A global's match order is alphabetical by `name` | **proven** | `module_data` sorts by `kind:key` (`packs.py`:958); `item_key` for a global **is** its `name`; `match_global` returns the first hit (`module.py`:175) |
| A5 | Testing the author's own phrases against installed patterns is the strongest shadow check that is decidable | **inferred** | Regex-overlap in general is not something we will decide. The check is exact for the typed phrases and claims nothing more (§4.4). A counter-example would be a *typed* phrase the check missed — T12/T13 bound it |
| A6 | 40 tries/hour and a 300-token cap are the right numbers | **unverified — chosen, not measured** | Both are env vars. A week of a real author's use settles them; until then the only defensible claim is *"one press, one call, and the count is on screen"* |
| A7 | A parent's browser and the supervisor agree on what a draft is, so `local_rev`-based conflict detection is enough for two tabs | **inferred** | The same mechanism the import 409 already uses. R7 names the residual: two tabs are detected, not merged |
| A7b | Rung 2 (rehearse) requires a **live device**; rungs 0, 1, 3 and 4 do not | **proven** | `MoxieRuntime.preview` 404s an unknown `device_id`; `refreshPreview` (`app.js`:838) hides the 🎬 card on `null`. §5.4 states the consequence rather than promising a device-free rehearsal |
| A8 | An authored conversation naming an unknown `module_id` is ignored by a robot rather than fatal | **unverified — needs hardware** | The same unknown the pack review already warns about for schedules. Nothing in our corpus states it; the editor warns and does not refuse |
| A9 | The person who authors is the account holder at the console, not a therapist or teacher on their own machine | **unverified — needs a real parent** | Ask five owners who would write content. This is the **one finding that would flip §3.2** to option (b), and it is a question about people |
| A10 | A non-programmer can write a useful prompt given a text box, four chips and a render panel | **unverified — needs a real parent** | A human test. Every test in §7 proves the surface *exists*, never that it *lands* — the same class as `sandboxed-extensions.md` A10 and `live-sim-demo.md`'s own parent row |
| A11 | Seeing the resolved system prompt (rung 1) is worth more to an author than a faster paid try (rung 3) | **unverified — needs a real parent** | The whole cost argument in §5 rests on it. If it is false, P1's budget is the wrong lever and the answer is caching, not counting |

---

## 11. Did this brief remove the decision it owed?

Checked against §1's four, honestly:

| | Owed decision | Removed? |
|---|---|:--:|
| D1 | Where authoring lives | ✅ §3.2 chooses the console, defends it in five points, and names the one finding (**A9**) that would flip it |
| D2 | What a parent edits | ✅ §4.2 decides every field of every kind; §4.3 decides the prompt surface; §4.5 lists the refusals |
| D3 | The loop and its cost | ✅ §5's five rungs, with the gateway cost of each and a structural reason rung 3 cannot fire on a keystroke |
| D4 | How it stays safe | ✅ §6's six gates are all existing functions; the only new safety code is one `if` (§6.3), and M1 keeps it |

**A build agent can open a worktree and start on P0 without owing a design argument first.** The audit
row is flipped to 🟢 **build-ready** on that basis. What remains open is not a decision but a set of
*human* unknowns (A9–A11), and none of them blocks P0 — they would change P1's budget lever and, at
most, revisit §3.2 later with evidence instead of opinion.

---

## 12. What this brief is not

It is not a studio, an IDE, or a JSON editor with a nicer font. It is not a claim that a parent will
author anything — §0 and A9–A11 say the opposite three times, on purpose. It is not a way to run code a
parent wrote, and §6.5 closes that door in every phase.

It is the smaller, checkable thing: **the day a parent wants Moxie to have a bedtime conversation about
their child's actual week, they can write one, hear how she performs it, ask her a question and read the
answer, and keep it — without a git checkout, without a restart, and without a single line of the safety
path being written twice.**

---
📖 [Backlog index](README.md) · [OpenMoxie feature audit](../openmoxie-feature-audit.md) ·
[Content-module contract](../content-module-contract.md) · [Content packs](content-packs.md) ·
[Sandboxed extensions](sandboxed-extensions.md) · [Brain picker](brain-picker.md) ·
[Live Sim demo](live-sim-demo.md) · [Expressiveness](expressiveness.md) ·
[The AI seam](../ai-seam.md) · [The SIM as a client](../sim-as-a-client.md) ·
[Moxie as a platform](../moxie-as-a-platform.md) · [Attribution](../../../ATTRIBUTION.md)
