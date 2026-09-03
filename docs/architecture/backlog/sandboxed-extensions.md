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

