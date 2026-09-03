# 🧬 Sandboxed content extensions — a pack that can *do* something, written by someone you don't trust (BEYOND #6)

> **Backlog brief v1 · 2026-09-02.** The build document for
> [OpenMoxie feature audit](../openmoxie-feature-audit.md) **§4.2 BEYOND #6** — *"Their `METHOD` globals
> and conversation `code` fields are `exec()` with a 10-second timeout — powerful, and un-shareable. A
> capability-scoped module runtime (declared permissions, no filesystem/network by default, resource
> limits) makes community content packs safe to install."* — effort **L**, and the audit's own
> [§4.4 re-rank](../openmoxie-feature-audit.md) puts it **third**: *"The moment packs are shareable, 'we
> never execute `code`' flips from a safety win to the ceiling on what a pack can do."*
>
> **Clean-room.** Every wire claim below comes from **our own** recovered corpus — chiefly
> [`behavior-markup.md`](../../reverse-engineering/runtime/behavior-markup.md) and the
> [content-module contract](../content-module-contract.md) — never from the vendor app.
> **OpenMoxie** (MIT, © Justin Beghtol) is read as prior art and cited by path **and commit**: we
> describe what its `exec` path does, we quote the shape of what its authors *wrote*, and we port the
> **behaviours**. No upstream code enters this tree. See [`ATTRIBUTION.md`](../../../ATTRIBUTION.md).

---

## 0. The boundary, stated first — this is cloud-side, and only cloud-side

**An extension runs in our cloud. It never runs on the robot.** Nothing here loads code onto a
Moxie, changes its firmware, or reaches its Android side.

The robot's only relationship to an extension is the one it already has with every turn: it
publishes a `RemoteChatRequest` and receives a `RemoteChatResponse` carrying **text**, **behaviour
markup** and **execution actions** — the same three fields the LLM path produces today
([content-module contract](../content-module-contract.md), "Execution actions"). An extension is a
*producer* of those three fields, sitting exactly where a registered Python `global_handler` sits in
[`content_app.py`](../../../mqtt/moxie_sdk/content/content_app.py) right now. The robot cannot tell
whether the sentence it is about to say came from a model, a hand-written handler, or a stranger's
extension, and it does not need to.

Three consequences a build agent must not blur:

1. **Our corpus establishes nothing about running arbitrary code on the robot.** It establishes a
   protocol. Any future reader who takes this page as licence for on-device execution is reading it
   wrong; there is no such design here and no evidence to build one on.
2. **The sandbox's job is protecting the *appliance and its other children*, not the robot.** The
   threat is an imported pack reading a sibling's memory, exfiltrating a nickname, burning the turn
   budget, or reaching the gateway key — all of which live on the appliance (or a Cloudflare Worker),
   never on the robot.
3. **The blast radius of a *successful* extension is still bounded by the wire.** Even a perfectly
   trusted extension can only emit markup from the frozen catalogue in
   [`vocab.py`](../../../mqtt/moxie_sdk/vocab.py) and actions from a closed allowlist. A sandbox
   escape is a compromise of our server; it is not a compromise of the robot.

---

## 1. Why this is the ceiling, right now

Content packs shipped on 2026-09-02 ([`content-packs.md`](content-packs.md),
[`packs.py`](../../../mqtt/moxie_sdk/content/packs.py)). A pack is a single file a parent, a teacher or
a speech therapist can hand to somebody else: it carries conversations, globals and schedules, it is
digest-covered, it exports through a positive field allowlist, and its import is reviewed item by item
in a 2×2 over `source_version` × `local_rev`.

Everything in it is **declarative**, and that was deliberate. The contract says so in as many words
([content-module contract](../content-module-contract.md), *"`code` is data, never behaviour"*):

> The engine has never executed a module's `code`, and packs make that a security property rather than
> a deferral: a `code` string round-trips as an opaque field, the review marks the item ⚠️ *"carries a
> `code` block, which this appliance never runs"*, and it stays in the store so a future sandboxed
> runtime (audit BEYOND #6) could start running it behind a capability declaration without a
> re-import. **The honest cost: importing upstream's `MoxieTime` or `MoxieTimers` gives you a global
> that matches an utterance and then does nothing, because their behaviour *is* the `code`.**

That last sentence is the ceiling, stated by the system about itself. Packs made content *shareable*;
the refusal to execute makes shared content *inert past a certain point*. A pack can teach Moxie a new
personality, a new prompt, a new day plan and a new trigger phrase. It cannot teach Moxie to **set a
timer**, **read a QR card**, **count something**, **check the clock**, or **remember a score** — because
each of those is a computation, and we have no way to run a stranger's computation safely.

So the choice is not "sandbox or no sandbox". It is:

| | What a pack can be |
|---|---|
| Today | A prompt library. Real, useful, and a hard ceiling. |
| With `exec` (upstream's answer) | A platform, and an arbitrary-code-execution channel into a child's appliance. See §2.4 — it is worse than the audit's one-liner suggests. |
| With this brief | A platform whose programs are **total, metered, capability-scoped and reviewable in English** — and whose worst case is *"that activity stopped working"*, not *"a stranger read your child's memory"*. |

---

## 2. What our corpus and our code already give you

### 2.1 The seam — where an extension plugs in

| # | Fact | Source |
|---|---|---|
| S1 | `ContentApp.respond()` runs **globals first** (`self.module.match_global(turn.speech)`), and a matched global only produces a reply if a **registered Python callable** is found in `self._handlers[g.name]`. A match with no handler *falls through* to the conversation. This is the exact socket an extension fills. | [`content_app.py`](../../../mqtt/moxie_sdk/content/content_app.py) `respond()` |
| S2 | The class's own docstring names the gap: *"arbitrary `code`-string execution from module JSON is deliberately NOT done here — a sandboxing concern deferred; built-in/registered handlers cover the safe cases."* | [`content_app.py`](../../../mqtt/moxie_sdk/content/content_app.py) module docstring |
| S3 | A `Volley` is the whole outbound surface: `set_output(text, markup)`, `add_execution_action(name, args)`, `update_subscriptions(events)`, plus `persist_data` (durable, module-namespaced) and `local_data` (per-turn scratch, *never* written anywhere). | [`volley.py`](../../../mqtt/moxie_sdk/content/volley.py) |
| S4 | Inbound context is `speech`, `entities` (regex capture groups), `request["input_vars"]` (robot-supplied), `config["child_pii"]` and `persist_data`. That is the complete fact base an extension could ever read. | [`volley.py`](../../../mqtt/moxie_sdk/content/volley.py) · [`content_app.py`](../../../mqtt/moxie_sdk/content/content_app.py) `_volley()` |
| S5 | **`volley.execution_actions` is not yet plumbed to the wire.** `_reply_from_volley` says so: *"M2: a global handler drives text/markup. Plumbing `volley.execution_actions` (`eb_timer_request` etc.) into `RemoteChatAction` is a later slice."* | [`content_app.py`](../../../mqtt/moxie_sdk/content/content_app.py) `_reply_from_volley()` |
| S6 | A global handler that writes `persist_data` has it saved for it, guarded by a before/after JSON comparison — so "did this program change durable state?" is already a solved question. | [`content_app.py`](../../../mqtt/moxie_sdk/content/content_app.py) `_save_persist_data()` |
| S7 | Handler output goes through the same `parse_action_tags` + `annotate` path as model output, so an extension writing `"<exit>"` ends the session and an extension writing a bare markup line still gets the markup floor. | [`content_app.py`](../../../mqtt/moxie_sdk/content/content_app.py) `_reply_from_volley()` |

**S5 is the single most important scoping fact in this brief.** Because actions are not on the wire
yet, a P0 extension runtime that shipped `act` would be shipping a capability that cannot do anything.
P0 therefore *validates and refuses* `act`/`subscribe` and lands them in P1 alongside the
`RemoteChatAction` plumbing. That is what keeps P0 to one sitting.

### 2.2 The pack format — what an extension will ride inside

| # | Fact | Source |
|---|---|---|
| P1 | `SPEC` is a **positive, per-kind field allowlist** with a coercer and a default per field; `FIELDS` derives the plain names, and `test_the_allowlist_is_pinned_to_the_dataclass_fields` asserts them against `dataclasses.fields()` so a new field cannot silently start shipping in everybody's packs. | [`packs.py`](../../../mqtt/moxie_sdk/content/packs.py) `SPEC` / `FIELDS` |
| P2 | `pack_digest()` is `digest_of()` over the whole pack body minus `digest`/`signatures`, using one canonical serialization (`sort_keys`, no whitespace, `ensure_ascii=False`). Items are a **flat `items[]` keyed `kind:key`**, explicitly so a re-post between review and import cannot swap what you ticked. | [`packs.py`](../../../mqtt/moxie_sdk/content/packs.py) `canonical()` / `pack_digest()` / docstring |
| P3 | `review_pack(..., digest=...)` ticks **nothing** unless the digest verdict is `"ok"`. | [`packs.py`](../../../mqtt/moxie_sdk/content/packs.py) `review_pack()` |
| P4 | The review is a 2×2 over `source_version` (the author's counter) × `local_rev` vs `imported_rev` (did *this* appliance edit it?): `NEW`, `UPGRADE`, `CONFLICT`, `KEEP_LOCAL`, `DOWNGRADE`, `DOWNGRADE_CONFLICT`. `CONFLICT` and `DOWNGRADE_CONFLICT` default **un-ticked**. | [`packs.py`](../../../mqtt/moxie_sdk/content/packs.py) review states |
| P5 | The review already emits a per-item warning for a `code` block: *"carries a `code` block, which this appliance never runs"*. There is a place to put a capability list, and prose to change. | [`packs.py`](../../../mqtt/moxie_sdk/content/packs.py) `review_pack()` |
| P6 | Packs are **checksummed, deliberately not signed**, and the docstring gives the reason: a signature verified against a key that arrived in the same file *"is decoration that reads as a guarantee"*. `signatures: []` is reserved. **The security property packs actually rely on today is structural: an imported pack cannot execute anything.** | [`packs.py`](../../../mqtt/moxie_sdk/content/packs.py) docstring |
| P7 | `MAX_PATTERN_CHARS = 512` exists because *"a compiled Python regex has no timeout in the stdlib, so a pathological pattern can still stall the matching thread"* — a named, accepted risk. | [`packs.py`](../../../mqtt/moxie_sdk/content/packs.py) |
| P8 | Applying a pack calls `reload_content()`, an attribute swap — an import is live on the **next turn** with no restart. Effective content = shipped defaults ⊕ the imported overlay. | [content-module contract](../content-module-contract.md) "Storage, the overlay, and the reload" |

**P6 is the load-bearing one.** Packs are unsigned *because* they are inert. Introducing execution
without replacing that property with an equally structural one would quietly cash a cheque the pack
format wrote. §5 and §6 are that replacement: not "we trust the author", but "the program cannot
express the harm".

### 2.3 The rest of the machinery an extension model must respect

| # | Fact | Source |
|---|---|---|
| M1 | **The turn budget is 6 seconds**: `BRAIN_BUDGET_S = _env_float("MOXIE_BRAIN_BUDGET_S", 6.0)`. Anything an extension spends is spent out of a child's patience. | [`config.py`](../../../mqtt/config.py) `BRAIN_BUDGET_S` |
| M2 | The **safety classifier** is a local, dependency-free `RuleClassifier` over `safety_rules.json`, producing `InputSafety{is_unsafe, blocked_by[], intents[], phrase_id}` on **both** sides of a turn, with `redirect_for()` instead of a refusal and a durable review queue. `Classifier` is a protocol, so a model can drop in. | [`safety.py`](../../../mqtt/moxie_sdk/safety.py) |
| M3 | **automarkup** is one pure deterministic generator (`annotate`) over a frozen, doc-cited catalogue (`vocab.py`), with byte-exact goldens; it returns text already carrying `<mark`/`<usel` unchanged, so authored markup is honoured as written. | [`automarkup.py`](../../../mqtt/moxie_sdk/automarkup.py) · [`vocab.py`](../../../mqtt/moxie_sdk/vocab.py) |
| M4 | **`JsonStore`** is one file per `(device_id, collection)` under `robots/<safe_name(device_id)>/`, plus `fleet/<collection>.json` for shared records — *"never under `robots/`, so it can never collide with a device id"*. `safe_name()` is the only thing between a caller and a path. | [`store.py`](../../../mqtt/moxie_sdk/store.py) `JsonStore` |
| M5 | **`MemoryStore`** is module-namespaced, bounded, provenance-stamped per merge, decays on a use clock, and supports per-item `find`/`edit`/`erase`. `writes_allowed()` gates on policy. | [`store.py`](../../../mqtt/moxie_sdk/store.py) `MemoryStore` · [`memory.py`](../../../mqtt/moxie_sdk/content/memory.py) |
| M6 | **`LoggingPolicy{NO_DATA=0, NO_MEDIA=1, FULL=2}`** is the child-privacy gate; under `NO_DATA` memory writes are dropped at the store, not at the caller. | [`cloud_config.py`](../../../mqtt/moxie_sdk/cloud_config.py) `LoggingPolicy` |
| M7 | Memory behaviour that upstream expresses as a `complete_handler` **Python string** is **declared** in our format instead: `"memory": {"namespace": …, "summarize": true, "min_volleys": 2, "max_items": 5}`. The precedent for "replace a script with a declaration" is already set, and shipped. | [`module.py`](../../../mqtt/moxie_sdk/content/module.py) · [content-module contract](../content-module-contract.md) "Declaring it" |
| M8 | `presence` is a read-only render variable (`presence.face_present`, `presence.line`) folded from the robot's own vision events with hysteresis. | [`vision.md`](../vision.md) §7 |

**M7 is the design precedent this brief extends.** We have already done this once, for exactly one
behaviour (end-of-conversation memory), and it worked: the declaration is smaller than the script, a
parent can read it, and nothing executes. An extension is the same move generalised — with the
difference that generalising it *does* require an evaluator, and §3 is the argument about which kind.

### 2.4 Prior art, verified — upstream's `exec` is worse than the audit's one-liner

Verified against **[jbeghtol/openmoxie](https://github.com/jbeghtol/openmoxie)** at commit
**`c8c2d380efd37d2e83761957587f5d08f73b3a63`** (`Added warning about alt openmoxie (#58)`,
2026-01-15), read in a scratch clone outside this tree. Two `exec` call sites exist in the whole
repository, and no others:

| # | Finding | Where |
|---|---|---|
| U1 | **`site/hive/mqtt/global_responses.py`:55** — `exec(self._source.code, globals(), loc)` inside `MethodPattern.create_response`. This is the `METHOD` global path. `GlobalAction.METHOD = 4`; `GlobalResponse.clean()` requires `code` when the action is `METHOD`. | `global_responses.py`:55 · `models.py`:100‑122 |
| U2 | **`site/hive/mqtt/conversations.py`:271** — `exec(source.code, globals(), loc)` inside `SinglePromptDBChatSession.__init__`, harvesting `pre_process`, `post_process`, `complete_handler`, `notify_handler`. This is the conversation-hook path. | `conversations.py`:263‑277 |
| U3 | **The audit's "with a 10-second timeout" applies to one of the two, and only to part of it.** The `ThreadPoolExecutor(max_workers=1)` + `future.result(timeout=10.0)` in U1 bounds the **call to the harvested function**. The `exec` of the code block itself runs inline on the calling thread with **no bound**, and U2 has no timeout of any kind — a conversation hook's module-level code runs unmetered at session construction. | `global_responses.py`:52‑72 |
| U4 | **Worse: the 10-second bound does not actually bound the turn.** `future.result(timeout=10.0)` raises `TimeoutError` after 10 s, and then the `with ThreadPoolExecutor(...)` block exits — which calls `shutdown(wait=True)` and blocks until the runaway function returns. A `while True:` in a `METHOD` hook therefore hangs the handling thread indefinitely; the visible symptom is *"Script error: Timeout exceeded"* never arriving. *(Read of the code plus documented stdlib semantics; not executed against a live upstream instance — see the ledger, A4. We are not porting this path, so nobody needs to confirm it.)* | `global_responses.py`:58‑79 |
| U5 | **The `globals()` argument is the real problem, not the timeout.** Both sites pass the *module's own* globals, so a `code` block starts life holding `re`, `logging`, `traceback`, `partial`, `Volley`, and — at U1 — `GlobalResponse` and `GlobalAction`, i.e. live Django model classes. From any of those, `__builtins__`, `__import__`, the ORM, the database, `settings` (and therefore the OpenAI key), and the filesystem are one attribute away. A `code` string is **not** a sandboxed script that happens to lack limits; it is unrestricted server-side Python with the application's full authority. | `global_responses.py`:55 · `conversations.py`:271 |
| U6 | Failure is handled gracefully and *visibly to the child*: on any exception the child hears `f"Script error: {e}"`. That is a nice instinct (the turn survives) with a bad output surface (a stack-trace fragment spoken to a seven-year-old). | `global_responses.py`:74‑82 |
| U7 | Upstream's own docstring warns that a `.*` pattern *"will usurp ALL inputs and make your bot non-functional"* — the author knew the surface was sharp and documented it honestly. | `global_responses.py`:1‑13 |

**None of this is a criticism of a project we are grateful for.** OpenMoxie's `exec` is the right
engineering call for a self-hosted server whose operator *is* the author: it made the robot do things
years before anyone else managed it, and half this repository exists because that path proved the robot
could be revived at all. It is simply not a call that survives contact with the word *shareable* — which
is precisely how the audit classified it.

### 2.5 The complete requirements corpus — every upstream `code` block, and what it needs

This is the most useful thing in this brief, so a build agent should not skip it. Upstream's entire
executable content library is **6 `code` strings containing 9 hook functions across 4 modules** —
`MoxieTime`, `MoxieTimers`, `MemoryChat`, `MoxieGo` (verified by walking every `content_modules/*.json`
at `c8c2d38`; `site/data/default_conversations.json` carries none). If our runtime can express these
six, it can express the whole state of the art. Every one of them was read, and here is what each
actually requires:

| Hook | What it does | Primitives it needs |
|---|---|---|
| `MoxieTime` / `get_response` | Reads the wall clock, converts to a 12-hour string, says *"The time is 3:05 P M"* (with `"AY M"`/`"P M"` spelled for the TTS). | clock · `%` · conditional · string format |
| `MoxieTimers` / `handle_volley` (set) | `int(entities[0])` × a `{second,minute,hour}→ms` table + now → an expiry; fires `eb_timer_request ["1", expiry]`; writes `persist_data["timers"]["1"]`; says *"Starting timer for 5 minutes"* with correct pluralisation. | clock · int coercion · dict lookup · arithmetic · **action** · memory write · plural |
| `MoxieTimers` / `handle_volley` (status/cancel) | Branches on `entities[0] == "status"`; decomposes remaining ms into h/m/s; builds a parts list and `", ".join`s it; or cancels: `eb_timer_request ["1","0"]` + deletes the key. | comparison · `floor` · list build · `join` · **action** · memory delete |
| `MoxieTimers` / `pre_process` + `notify_handler` | On the wake turn: `session.is_empty()`, reads `input_vars["eb_timer_id"]`, pops it from `persist_data`, writes `local_data`, `time.sleep(0.5)`, emits a **markup** string that repeats a `cmd:playaudio` mark three times with `<break time="1s"/>`, then `add_launch_or_exit()`. | session accounting · `input_vars` · memory delete · scratch · *(sleep)* · markup + bounded repeat |
| `MemoryChat` / `post_process` + `complete_handler` | `random.choice` over stored summaries; calls `session.summarize(prompt_base=…)` — **a brain call** — to author an opener; on completion summarizes again, inserts at the head of a list, trims to 10, and conditionally runs a second summarize with a long instruction. | random · memory read/write · list ops with a cap · **brain call** |
| `MoxieGo` / `pre_process` + `complete_handler` | If no speech: `eb_enable_qr ['true']` + `update_subscriptions(['eb-qr-event'])`. If the speech *is* `eb-qr-event`: reads `$eb_qr_value`, `startswith("GO")`, slices the payload into the reply, else re-arms the scanner. Returns `True`/`False` = *"I handled this turn"*. | `input_vars` · `starts_with` · slice · **action** · **subscribe** · a *handled* flag |

Union of everything required, de-duplicated:

**reads** — clock, random, `speech`, `entities`, `input_vars`, own-namespace memory, session accounting.
**values** — numbers, strings, booleans, null, lists, string-keyed maps.
**operations** — arithmetic incl. `%` and `floor`; comparison; `and`/`or`/`not`; a lazy conditional;
`concat`/`slice`/`len`/`lower`/`starts_with`/`join`/`replace`/`format`; `int`/`round`; list build and
index; map lookup and `has`; a bounded `repeat`; a plural helper.
**effects** — say text, author markup, one execution action, subscribe to an event, write/delete
own-namespace memory, write per-turn scratch, set a *handled* flag, one brain call.

**What is conspicuously absent:** a loop. Not one of the nine hooks iterates. No `while`, no `for`
(the nested `def`s are inlining, not iteration). No user-defined recursion. No I/O beyond the volley.
No imports beyond `time`, `math`, `random`, `datetime`. **The one `time.sleep(0.5)` is the only thing
in the whole corpus that a sandbox should refuse outright** — and §5 shows the corpus-correct
replacement was sitting in the same function all along (`<break time="1s"/>`, which the *robot*
honours, at zero cost to the turn).

A language with no loops, no user functions and a closed operator table covers 100% of the state of the
art. That is not a coincidence: content hooks are *reactions*, and a reaction is a decision tree over a
fact base. §3 is where that observation becomes a decision.

### 2.6 A live hole we found while writing this — the Jinja environment

**Proven, on this machine, today.** [`render.py`](../../../mqtt/moxie_sdk/content/render.py) renders a
conversation's `prompt` and `opener` with a plain `jinja2.Environment`:

```python
env = jinja2.Environment(undefined=jinja2.ChainableUndefined,
                         autoescape=False, keep_trailing_newline=True)
return env.from_string(template).render(**context)
```

`prompt` and `opener` are both **pack-importable fields** ([`packs.py`](../../../mqtt/moxie_sdk/content/packs.py)
`SPEC["conversation"]`), and a plain Jinja `Environment` permits attribute access on the objects in its
context. With `jinja2` importable, a template of the form
`{{ volley.__init__.__globals__['__builtins__']['__import__']('os')… }}` executes arbitrary Python — we
ran the probe against `render_prompt` with a real `Volley` and it returned the process working
directory. Template injection through a `prompt` is server-side code execution.

Scope, honestly:

- **It is not live in the shipped container.** `mqtt/requirements.txt` does not list `jinja2`, the
  `Dockerfile`'s `EXTRAS` build-arg defaults to empty, and `pyproject.toml`:25 puts it behind the
  optional `content` extra. With `jinja2` absent, `render_prompt` falls back to `_minimal_render` — a
  `{{ dotted.path }}` regex substitution with no attribute-call surface, which is safe.
- **It is live on any install that has `jinja2`**, which is every developer checkout in this repo (we
  measured `jinja2 3.1.2`), anything installed as `.[content]` or `.[all]`, and any environment where
  another dependency pulls Jinja in. The docstring's *"Uses real Jinja2 when it's installed"* means the
  security posture of the appliance **silently depends on what happens to be in site-packages**.
- **The review points the ⚠️ at the wrong field.** `review_pack` warns about `code`, which never runs.
  It says nothing about `prompt`, which — on a Jinja-enabled install — does.

This changes the shape of the work in a useful way. "We never `exec` anything" is not quite true once
packs exist, so **P0's first commit is not the new runtime; it is closing this.** One-line fix
(`jinja2.sandbox.SandboxedEnvironment`, which is exactly what it is for) plus a refusal of template
paths beginning `_`, plus escape test **X3**, which fails before the change and passes after. It ships
alone, it is a security fix rather than a feature, and it means the capability model in §5 is the
appliance's *only* execution surface instead of its second one.

---

## 3. The four real options, evaluated — then one choice

The five questions each option must answer are the ones that actually decide this: **what can it
express**, **what does it cost to build and keep**, **what is its escape surface**, **can it run in a
Cloudflare Worker as well as in the Python supervisor** (the hosted demo is the headline goal —
[`live-sim-demo.md`](live-sim-demo.md)), and **can a parent who is not a programmer review a pack that
contains one**.

### 3.1 The comparison

| | **(a) A restricted expression language you interpret** | **(b) A purely declarative behaviour tree** | **(c) WebAssembly + host imports** | **(d) An embedded VM, stdlib stripped** (QuickJS · Starlark · Lua) |
|---|---|---|---|---|
| **Expresses** | The whole of §2.5, with no loops or user functions. Arithmetic, branching, string building, bounded list work. Not: iteration, recursion, general algorithms. | Trigger → canned response → named effect. **Cannot** express `count × ms_per[unit] + now`, nor the h/m/s sentence — those need arithmetic over a value. | Anything. It is a general-purpose machine. | Anything, in a language authors already know. |
| **Build cost** | **~550 LOC of pure stdlib Python**, plus a validator and an English renderer. No parser (the program *is* JSON). | ~150 LOC, and then a new verb per feature forever. | Host embedding is small; the **toolchain, ABI, and the marshalling layer** are not. Authors need Rust/C/AssemblyScript. | Host embedding is small. Stripping the stdlib *correctly* is the whole job, and it is never finished. |
| **Maintenance** | Every op is total and hand-audited; the op table is the audit surface and it is one screen long. | Trivial — but the roadmap is "add another verb", i.e. we ship features, not a platform. | Track a native runtime's CVEs; per-arch wheels. | Track an interpreter's CVEs **and** re-audit the deny-list on every upgrade. |
| **Escape surface** | **Structurally none.** Values are JSON scalars/lists/maps only; there is no op that takes an object, no attribute access, no name that resolves to a host object. The evaluator's own module imports neither `os`, `time`, nor `random`. | None (nothing is evaluated). | Small and well-understood: linear memory, no ambient authority, imports are explicit. Historically the strongest of the four. | **The largest.** Every escape in this class comes from a reachable host object — Lua's string metatable, JS prototype pollution, a forgotten builtin. Stripping is a **deny-list**, and [`packs.py`](../../../mqtt/moxie_sdk/content/packs.py) already rejects deny-lists for exactly this reason: *"a denylist … leaks the first time somebody adds a column."* |
| **Worker + supervisor?** | **Yes, cleanly.** The AST is JSON; a JS port of a 550-LOC total evaluator is mechanical and pinned by a shared conformance vector file. Neither host needs `eval`. | Yes trivially. | Yes — Workers run Wasm natively. **But not in the Python supervisor without a native wheel** (`wasmtime`/`wasmer`), which breaks the *"slim, pinned, no native deps"* Dockerfile and the multi-arch image promise. | **No.** A Worker cannot install a native QuickJS/Starlark wheel; using the Worker's own `eval`/`new Function` hands the extension the isolate's globals — `fetch`, the env bindings, **the gateway key** — which is precisely [`live-sim-demo.md`](live-sim-demo.md) §4.2's threat model. QuickJS-compiled-to-Wasm sidesteps this, and then you are in column (c) with an extra interpreter. |
| **Parent-reviewable?** | **Yes.** A rule is `when … then …`; a pure `explain()` renders each rule as one English sentence — the same idiom as the 📅 card's *"why this activity today"*. | Yes, best of the four. | **No, and not by us either.** A digest over an opaque binary is provenance, not review. A `.wasm` blob in a pack's positive field allowlist is a base64 field nobody can read. | **No.** Reviewing a JS program for what it *does* is the task we are trying to spare a parent. |

### 3.2 The choice for P0

> **(a), shaped by (b): a declarative rule list over a total, JSON-AST expression language,
> interpreted by ~550 lines of our own pure-stdlib code, with no `exec`, no parser, no loops and no
> reachable host object.**

The one-sentence reason: **§2.5 proves the entire state of the art needs no loops and no host objects,
so the option with structurally zero escape surface is also the option that is expressive enough — and
it is the only one of the four that runs unchanged in both the Python supervisor and a Cloudflare
Worker while still rendering back to English for a parent.**

Longer form, because a build agent will be asked to defend it:

1. **It is a positive-list design in a codebase whose safety already rests on positive lists.**
   `SPEC`/`FIELDS` in `packs.py`, the frozen `vocab.py` catalogue, the closed QR grammar. Option (d)
   would introduce the codebase's only deny-list at its most dangerous point.
2. **The escape tests in §9 can actually pass.** For (a) they are assertions about a closed table and a
   plain-JSON fact base — provable properties. For (d), `test_no_escape` is not a test; it is a
   standing bet against every future CVE in an interpreter we do not maintain.
3. **The AST is the artefact, so review, portability and determinism are the same property.** The
   thing digested by the pack, the thing rendered into English for the parent, the thing the Python
   evaluator walks, and the thing the JS evaluator walks are one object. There is no compile step
   whose output can disagree with its input.
4. **Failure is boring.** A total language has no exceptions to leak: division by zero yields an error
   *value*, a missing key yields null, an out-of-range index yields null. There is no state in which
   the evaluator does not return.

### 3.3 What we are giving up, said plainly

- **Real programming.** No loops, no user-defined functions, no recursion, no local mutation beyond
  ordered `let` bindings. An author who wants to write an algorithm cannot. If a future activity genuinely
  needs iteration, the answer is a **new host primitive** with its own capability — not a loop keyword.
- **Verbatim portability from upstream.** Importing OpenMoxie's `MoxieTimers` will *still* not run its
  Python. It has to be **re-authored** in our format. §7.4 is emphatic that a Python→AST compiler is the
  wrong instinct: that is a parser for a Turing-complete language, and it reintroduces exactly the audit
  surface this design deletes. The mitigation is a migration appendix (§8) that hand-ports all six
  upstream hooks — which doubles as the conformance golden set, so the port is done once, by us, and
  checked forever.
- **Authoring ergonomics, at P0.** Hand-writing a JSON AST is worse than writing Python. P1 adds a small
  text surface that *compiles to* the AST, with the compiler living **outside** the trust boundary — the
  evaluator only ever sees an AST, so a parser bug can produce a wrong program but never an unsafe one.
- **The long tail of "just let me run code".** Some author, eventually, will want the real thing. That
  door is **P2, option (c)**: QuickJS-compiled-to-Wasm, or a hand-written Wasm module, behind the *same*
  capability table declared the same way, opt-in per appliance, and honestly labelled in the review as
  *"this pack contains a program this appliance cannot show you"*. Designing the capability table now so
  a second runtime can be dropped behind it later is why §5 describes a **host API**, not a set of
  evaluator features.
- **An asynchronous or multi-turn extension.** An extension is a pure function of one turn's facts. It
  cannot wait, poll, or run in the background. Multi-turn state is `persist_data`, and the *robot* keeps
  time via `eb_timer_request` — which is how the corpus's only long-lived behaviour (a timer) already
  works.

---

## 4. The design

### 4.1 The shape — an extension is a field on an item, not a new kind

```json
{"kind": "global", "key": "global/Timer Set", "source_version": 2,
 "data": {
   "name": "Timer Set", "pattern": "(set|start) a timer for (\\d+) (second|minute|hour)s?",
   "entity_groups": "2,3", "action": 4, "code": "",
   "extension": {
     "ext_format": 1,
     "capabilities": ["clock", "memory.read", "memory.write", "act.eb_timer_request"],
     "on": "global",
     "rules": [
       {"let": {"count": {"int": [{"var": "entities.0"}]},
                "unit_ms": {"get": [{"lit": {"second": 1000, "minute": 60000, "hour": 3600000}},
                                    {"var": "entities.1"}, 1000]},
                "expiry": {"+": [{"clock.ms": []}, {"*": [{"var": "count"}, {"var": "unit_ms"}]}]}},
        "do": [
          {"remember": {"key": "timers.1", "value": {"var": "expiry"}}},
          {"act": {"name": "eb_timer_request", "args": ["1", {"str": [{"var": "expiry"}]}]}},
          {"say": {"concat": ["Starting timer for ", {"str": [{"var": "count"}]}, " ",
                              {"plural": [{"var": "entities.1"}, {"var": "count"}]}]}},
          {"handled": true}
        ]}
     ]
   }}}
```

Why a **field on the existing item** rather than a new pack `kind`:

- An extension is meaningless without the trigger that fires it. A `global`'s regex and its program are
  one unit; splitting them across two items would let a review tick one and not the other.
- The 2×2 review already diffs item fields, so an edited extension is detected by the *existing*
  `local_rev` machinery with no new concept (P4).
- `SPEC["conversation"]` and `SPEC["global"]` each gain one entry, `("extension", _d, {})` — the same
  provably-JSON-safe deep-copy coercer `memory` and `schedule` already use — and the `FIELDS` pin test
  (P1) is what makes that change safe rather than silent.

`on` selects the hook point, mapping 1:1 onto what upstream's four hook names meant:

| `on` | Fires | Upstream equivalent |
|---|---|---|
| `global` | A matched `globals[]` pattern, before the conversation. The socket S1 describes. | `handle_volley` / `get_response` |
| `turn.before` | Before the prompt is rendered. May set `handled` to suppress the model entirely. | `pre_process` |
| `turn.after` | After the model's text, before markup and the wire. May rewrite the line. | `post_process` |
| `session.end` | At `<exit>` / module switch / disconnect, beside the declared `memory` block. | `complete_handler` |

P0 ships `global` and `turn.before`. `turn.after` and `session.end` are P1 — `turn.after` because
rewriting a model line needs the output-safety ordering settled, `session.end` because it overlaps the
already-shipped declarative `memory` block (M7) and the two must not both write the same namespace.

### 4.2 The expression language — a closed grammar over JSON

An **expression** is one of exactly four things:

1. A JSON number, string, `true`, `false` or `null` → itself, a literal.
2. `{"lit": <any JSON>}` → itself, for a list/map literal or a string that would otherwise look like an op.
3. `{"var": "<path>"}` → a lookup in the **fact base** (§4.4), dotted, with numeric segments indexing lists.
4. `{"<op>": [<expr>, ...]}` → one operator from the table below, applied to evaluated arguments.

An object with more than one key, an unknown op, or a wrong argument count is a **load-time refusal** —
not a runtime error. Validation happens once, at import and at content load; a stored extension that
fails validation is never evaluated at all.

**The operator table is closed, and it is the audit surface.** Everything in it is total: it returns a
value for every input, including the bad ones.

| Group | Ops | Notes |
|---|---|---|
| Arithmetic | `+ - * / % floor ceil round abs min max` | `/` or `%` by zero → the error value (§4.6), never an exception. Integer-vs-float is decided by the ops, not the host: `/` is float, `floor`/`int` narrow. |
| Comparison | `== != < <= > >=` | Cross-type comparison is `false` (or, for `!=`, `true`) — never an ordering error. |
| Logic | `and or not` | `and`/`or` are **lazy** and n-ary. |
| Conditional | `if` | `{"if": [test, then, else]}`, lazy in both branches; `else` optional (default `null`). |
| Strings | `concat lower upper trim len slice starts_with ends_with contains replace split join repeat format str plural` | `repeat` caps n at 16 (the corpus's `snd * 3`). `format` takes an explicit spec — never a bare float repr — so output is byte-stable across hosts. |
| Numbers | `int num` | `int` on a non-number string → the error value, which is how a regex group that captured junk fails *loudly* instead of becoming `0`. |
| Lists | `list get len compact reverse sort` | `get` is `[list_or_map, key, default]`. `compact` drops nulls and empty strings — the corpus's `parts` idiom. `sort` is a total order over scalars only. |
| Maps | `get has keys` | `keys` returns **sorted** keys, so iteration order is host-independent (a determinism requirement, not a convenience). |
| Facts | `clock.ms clock.local random.int random.pick presence.face_present session.total_volleys session.is_empty` | Each requires its capability (§5). Present in the table only when granted; **absent, not refused, when not** — so an extension using one it did not declare fails validation, not the turn. |

Deliberately **not** in the table, and not addable without re-opening this brief: any name-to-object
resolution, any attribute or index access on a non-JSON value, string multiplication (`repeat` is
bounded instead), regex construction (patterns live on the *item*, capped at `MAX_PATTERN_CHARS`, P7),
`eval` of any kind, and anything that returns a host handle.

**Nesting caps, checked at load:** expression depth ≤ 32, nodes per expression ≤ 512, statements per
rule ≤ 32, rules per extension ≤ 64, total nodes per extension ≤ 4096. A pathological AST is therefore
refused at import time, which is the only moment at which refusing it costs nobody anything.

### 4.3 Rules, `let`, and the statement list

```
extension := { ext_format, capabilities[], on, rules[] }
rule      := { when?: expr, let?: {name: expr, ...}, do: [stmt, ...] }
stmt      := {say: expr, markup?: expr} | {markup: expr} | {remember: {key, value}}
           | {forget: {key}} | {scratch: {key, value}} | {act: {name, args[]}}
           | {subscribe: [event, ...]} | {handled: bool} | {note: expr}
```

- Rules run **in order**; the **first** rule whose `when` evaluates truthy runs its `do` and the
  extension stops. Missing `when` = always true. No rule matching = the extension did nothing, which
  is a *success* (the turn proceeds exactly as it does today).
- `let` is an **ordered** map of name → expression, each visible to the ones after it and to `when`
  and `do`. Bindings are values, never references. This is what removes the need for loops in the
  h/m/s case: three bindings, no iteration.
- A `do` list is a **flat, bounded, straight-line sequence**. No nesting, no jumps, no early exit
  other than the end of the list. Consequence: the maximum cost of an extension is **statically
  computable at load**, so the step budget in §6 is a backstop rather than the primary control.
- `{"note": expr}` writes one string to the extension's own log line — the debugging affordance that
  replaces `print()`. Never spoken, never persisted, capped at 200 chars, and dropped entirely under
  `LoggingPolicy.NO_DATA`.

### 4.4 The fact base — a pre-built plain-JSON dict, and why that is the whole security argument

Before a single node is evaluated, the host builds a **plain JSON dictionary** and hands the evaluator
that. The evaluator never sees a `Volley`, a `Session`, a `MemoryStore`, a `RobotContext`, or any other
live object. `{"var": "..."}` walks that dict and nothing else.

```python
facts = {
  "speech":      str,                 # capped
  "entities":    [str, ...],          # capped in count and length
  "input_vars":  {str: str},          # robot-supplied → untrusted data, capped
  "child":       {"nickname": str},   # only if child.nickname granted; more only if child.profile
  "memory":      {...},               # ONLY this module's namespace, deep-copied, if memory.read
  "scratch":     {},                  # per-turn, starts empty
  "session":     {"total_volleys": int, "is_empty": bool, "overflow": bool},
  "presence":    {"face_present": bool, "line": str},   # only if presence granted
}
```

Three rules make this airtight, and each is a test in §9:

1. **The dict is built by the host from primitives, not by exposing objects.** A recursive type
   assertion (`X2`) walks the whole structure and fails if it finds anything that is not
   `str|int|float|bool|None|list|dict`. There is no object to walk *to*.
2. **A path segment beginning `_` is refused at load.** So `__class__`, `__init__`, `_meta` are not
   "blocked at runtime"; they are not valid programs. Combined with (1) they are also pointless.
3. **The namespace is supplied by the host, never by the extension.** `facts["memory"]` is
   `deepcopy(MemoryStore.load(device_id).get(own_namespace))`. An extension cannot name a namespace,
   a device, a collection or a path — the words for those do not exist in the grammar.

`own_namespace` is derived from the item, exactly as the `memory` block's namespace is today: a
conversation's declared `memory.namespace`, or for a global, `ext:<slug(name)>`. It is never taken from
the pack's own suggestion, because a pack that could choose its namespace could choose *someone
else's*.

### 4.5 Effects — applied after the program ends, never during

The evaluator is **pure**. Statements do not touch the world; they append to an **effect list**, and
the host applies that list after the program returns, in order, subject to every cap in §6:

| Statement | Effect the host applies |
|---|---|
| `say` | `volley.set_output(text, markup)` after the **output-side safety classifier** (M2) and after `annotate` (M3) if no markup was authored. A `blocked` verdict replaces the line with a `redirect_for()` line — an extension does not get a private channel to a child. |
| `markup` | Validated against `vocab.py` (M3): an unknown mark id, a malformed `cmd:` payload or an out-of-catalogue asset is **dropped**, and the drop is counted (`automarkup.dropped_ids()` already exists for this). Never passed through unchecked. |
| `remember` / `forget` | `MemoryStore.merge` / `erase_item` on `(device_id, own_namespace)`. Bounded and provenance-stamped by the store (M5); a `NO_DATA` policy drops it *at the store* (M6) and the extension is told so it can still speak. |
| `scratch` | `volley.local_data` — per-turn, never written anywhere (S3). |
| `act` | One `volley.add_execution_action(name, args)`. **P1**, because S5. Name must be in the closed action allowlist *and* individually granted. |
| `subscribe` | `volley.update_subscriptions(events)` from the closed event vocabulary ([`vision.md`](../vision.md) §7). **P1.** |
| `handled` | Suppresses the model call for this turn (the `True`/`False` return of upstream's `pre_process`). |
| `note` | One capped log line. |

Applying effects *after* the program means a breach mid-program leaves **nothing** half-applied: the
effect list is discarded whole. A partially-executed extension cannot write half a memory record.

### 4.6 The error value

`/` by zero, `int("banana")`, `sort` over mixed types: each yields a distinguished **error value** that
is falsy, propagates through every op (any op with an error argument returns the error), and — if it
reaches a `say`, `remember` or `act` — **fails the extension** per §6 rather than speaking the word
"error" at a child (U6's mistake). `{"has": [...]}` and `if` can test for it, so an author who wants to
handle a bad capture group can.

---

## 5. The capability model

An extension **declares** what it needs. The host **grants or refuses**. The parent **sees the grants
in plain language at pack-import review**, before anything runs.

The mechanism is deliberately not a runtime check. `capabilities[]` is validated against the ops and
statements the AST actually uses, **at load**:

- An AST that uses an op or statement its `capabilities[]` does not cover is **refused at load**.
- A `capabilities[]` entry the AST does not use is **refused at load** too — so a pack cannot ask for
  more than it needs to make the review look scarier or to leave a door open for a later "upgrade".
  The declared set and the used set are equal, or the extension does not install.

The consequence a parent gets: the list they were shown is exactly, provably, what the program can do.

### 5.1 The surface, enumerated, with defaults

| Capability | What it grants | Default | Why that default |
|---|---|:--:|---|
| `say` | Set the spoken line (post-safety, post-automarkup) | **granted** | An extension that cannot speak cannot do anything. It is also the least dangerous effect: it passes the same classifier a model's line does. |
| `handled` | Suppress the model call for this turn | **granted** | Already the semantics of a matched global with a handler (S1). Worst case is a turn that says only what the extension said. |
| `session` | `total_volleys`, `is_empty`, `overflow` | **granted** | Turn accounting. Three integers about the shape of a conversation, no content. |
| `child.nickname` | The child's nickname only | **granted, and named in the review** | It is why personalisation exists, it is already in every rendered prompt, and refusing it by default would make the first thing every author does be "ask for PII". The review says, in words: *"can use your child's first name"*. |
| `child.profile` | `pronouns`, `birthday`, `notes` | **refused** | A birthday and free-text notes are the highest-value PII on the appliance, and no behaviour in §2.5 needs them. A pack that wants them must say why, in a review a parent can decline. |
| `clock` | `clock.ms`, `clock.local{hour,minute,weekday,iso}` | **refused** | Harmless-looking and genuinely needed (2 of 6 hooks), but it is also the timing side-channel and the "only misbehave at 2 a.m." vector. Cheap to grant knowingly; wrong to grant silently. |
| `random` | `random.int(lo,hi)`, `random.pick(list)` — from a **seeded** PRNG | **refused** | Not for secrecy — for determinism. An extension with entropy cannot be replayed, and replay is how §9's goldens work. |
| `memory.read` | Read **its own namespace** of `persist_data` | **refused** | This is the child's remembered life ([what-moxie-remembers](../../guides/what-moxie-remembers.md)). It should cost a parent a deliberate tick. |
| `memory.write` | Merge / delete in **its own namespace** | **refused** | Writing is how a pack makes a wrong fact sticky. Bounded by the store, dropped under `NO_DATA`. |
| `presence` | `presence.face_present`, `presence.line` | **refused** | Whether a child is in the room is a physical-world observation. No corpus behaviour needs it; something will, and it should be asked for. |
| `markup` | Author raw behaviour markup (catalogue-validated) | **refused** | The one capability that reaches the robot's *body*. Validation makes a bad id harmless; a *valid* id can still make Moxie lurch or blare, so a human should agree to it. |
| `act.<name>` | One execution action, **per name**, from a closed allowlist (`eb_timer_request`, `eb_enable_qr`, `eb_wake`, …) | **refused**, **P1** | Per-name, not per-category: "can set a timer" and "can turn on the camera" are not the same sentence to a parent. Blocked on S5 regardless. |
| `subscribe` | Subscribe to robot events from the closed vocabulary | **refused**, **P1** | Pairs with `act`; a scanner you cannot read from is pointless, and vice versa. |
| `brain` | **One** model call per turn, prompt built from the extension's template, both sides safety-checked, charged to the turn budget | **refused**, **P1** | It costs money, it costs latency inside a 6 s budget (M1), and it is the one capability whose output is not predictable from the AST. Rate: 1 per turn, hard. |
| `schedule.request` | Ask that a module be *offered* (feeds the recommender's parent-request channel; never sets the day) | **refused**, **P1** | A pack that could set the day could fill it with itself. Requesting is reviewable; deciding is not delegated. |

### 5.2 What no grant level ever reaches

These are not "refused by default". **There is no operator, statement or path that names them**, so
refusing them is not a policy decision that could be reversed by a config flag:

network · filesystem · subprocess · environment variables · any credential or API key ·
another device's store · another module's memory namespace · another child's anything ·
the safety rule table · `LoggingPolicy` itself · robot config or permits · telemetry ·
telehealth · the pack store · other extensions · the host's Python or JS runtime · the clock or
entropy sources of the host process (both are injected values, §6).

Say it as an invariant a test can check: **the set of strings that resolve to anything at all is the
op table (§4.2) plus the fact base (§4.4), and both are finite and enumerated in our own source.**

### 5.3 `sleep` is not on the list, at any level

The corpus asks for it once — `time.sleep(0.5)` in `MoxieTimers.pre_process` — and the answer is no,
permanently. A sleep spends a child's attention and the turn's 6 seconds (M1) to do nothing, and it is
the simplest denial-of-service in any sandbox.

The corpus-correct replacement was in the same function: that hook also emits `<break time="1s"/>`,
which the **robot** honours during playback ([`behavior-markup.md`](../../reverse-engineering/runtime/behavior-markup.md)).
Timing belongs on the robot's playback clock, where it is free; not on our turn clock, where it is not.
Anything longer-lived is a timer, which is a robot-side wake event — the mechanism `MoxieTimers` itself
uses.

### 5.4 How the parent sees it

Two renderings, both pure functions of the AST, both P0:

1. **The grant list**, one plain sentence per capability, no jargon:
   *"Can speak · Can use your child's first name · Can check the time · Can remember things from this
   activity"*. Generated from a fixed table keyed by capability name — never from author-supplied text,
   which would be a place to lie.
2. **`explain(ext) -> list[str]`**, one English sentence per rule, from the AST:
   *"When your child asks to start a timer: remember when it should go off, ask Moxie to set a timer,
   and say 'Starting timer for 5 minutes'."* The idiom is already proven in this codebase — the 📅
   card's *"why this activity today"* line is the same trick over the recommender's inputs.

A parent reads sentences. The JSON is available behind a disclosure for the one parent in a hundred who
wants it, and the pack's field-level diff already shows it on an upgrade.

---

## 6. The limits that make it safe

Every number below is an env var with a default, in [`config.py`](../../../mqtt/config.py) beside
`BRAIN_BUDGET_S`. The defaults are chosen, not measured — see the ledger (A7).

### 6.1 Determinism

Same AST + same fact base + same seed → **byte-identical** effect list. Guaranteed by:

- **No ambient clock.** `clock.ms` returns an injected value captured once per turn, so two `clock.ms`
  calls in one program return the same number. The evaluator module contains **no `import time`** — an
  assertion over our own source (X7), which is cheap and does not rot.
- **No ambient entropy.** `random.*` draws from a PRNG seeded with `sha256(turn_key || extension_id)`.
  A turn is replayable from its inputs; the child still perceives variety.
- **Host-independent ordering.** `keys` sorts. `sort` is a total order over scalars only.
- **Host-independent number formatting.** `format` requires an explicit spec; `str` on a float uses one
  fixed rule. No language's default float repr ever reaches output — which is also what makes the JS
  port checkable against the same conformance vectors.

### 6.2 The budgets

| Limit | Env var | Default | What it stops |
|---|---|:--:|---|
| Steps | `MOXIE_EXT_MAX_STEPS` | `10000` | One step per node visited, per statement, per effect. Since there are no loops, the *static* maximum is known at load; this is the backstop. |
| Wall clock | `MOXIE_EXT_BUDGET_S` | `0.25` | Checked against an injected monotonic clock every 256 steps. **No threads and no signals** — so it behaves identically in the supervisor's handler thread and in a Worker isolate. |
| Value size | `MOXIE_EXT_MAX_VALUE_BYTES` | `16384` | Any single intermediate string/list/map. `concat`, `join`, `repeat`, `format` fail the *op* at the ceiling. |
| Total allocation | `MOXIE_EXT_MAX_TOTAL_BYTES` | `262144` | A running counter over all values produced; stops death by a thousand 16 KiB strings. |
| Expression depth | (load-time constant) | `32` | Stack depth. The evaluator is depth-counted, so a `RecursionError` can never escape. |
| Nodes per extension | (load-time constant) | `4096` | A giant AST is refused at import, not evaluated slowly. |
| Breaches per session | `MOXIE_EXT_MAX_BREACHES` | `3` | Quarantine (§6.4). |

**The wall-clock budget is carved *out of* the turn, not added to it.** `MOXIE_EXT_BUDGET_S` must be
strictly less than `BRAIN_BUDGET_S` (M1), and a startup assertion enforces it. An extension is 4% of a
turn; if `global` and `turn.before` both run, 8%. That is the honest deal: an extension gets a slice of
the child's patience, not a claim on it.

### 6.3 Output caps

| Cap | Default |
|---|:--:|
| Spoken text | 1000 chars |
| Markup | 8192 chars |
| Execution actions | 4 per turn |
| Subscriptions | 8 |
| Memory writes | 8 per turn |
| Memory value size | whatever `MemoryStore`'s existing bounds allow (M5) — no new path |
| `note` lines | 4 per turn, 200 chars each |

### 6.4 On breach: fail the extension, not the turn

**Moxie must keep talking.** That is the requirement, and it dictates the whole failure design.

On *any* breach — step budget, wall clock, a value or output cap, a refused capability, a malformed
AST that somehow reached evaluation, or an error value reaching an effect — the evaluator returns
`ExtResult(ok=False, reason=..., breach=...)`, the effect list is **discarded whole**, and
`ContentApp` proceeds **exactly as it does today**:

- an `on: global` extension that fails behaves like a matched global with no handler — it **falls
  through to the conversation** (S1), and the child gets a normal answer;
- an `on: turn.before` extension that fails is skipped, and the model runs;
- nothing is written to memory, no action is emitted, no markup reaches the wire.

The child never hears about it. No `f"Script error: {e}"` (U6).

The **parent** does hear about it, once. A bounded `ext_events` `JsonStore` collection (the same shape
as `safety_events`, M4) records one entry per `(extension_id, reason)` per session, and the console can
say: *"the Bedtime pack's timer stopped working, and Moxie carried on without it"* — with the reason in
plain language (*"it took too long"*, *"it asked for something it isn't allowed to do"*).

After `MOXIE_EXT_MAX_BREACHES` breaches in one session, the extension is **quarantined** for the rest
of that session (a flag in the per-robot ring). A broken extension may cost the child one turn's worth
of latency; it may not cost every turn's.

