# Sandboxed content extensions

**Status:** shipped (P0, plus P1's `act` and `subscribe`); the rest of P1 is open. The evaluator is
[`mqtt/moxie_sdk/content/ext/`](../../../mqtt/moxie_sdk/content/ext/), the host is
[`ext_host.py`](../../../mqtt/moxie_sdk/content/ext_host.py), and it is wired in through
[`content_app.py`](../../../mqtt/moxie_sdk/content/content_app.py). Tested by
[`test_ext_escapes.py`](../../../sim/tests/test_ext_escapes.py) (X1–X12),
[`test_ext.py`](../../../sim/tests/test_ext.py) (T1–T18),
[`test_ext_act.py`](../../../sim/tests/test_ext_act.py) and
[`test_ext_subscribe.py`](../../../sim/tests/test_ext_subscribe.py).

This covers [OpenMoxie feature audit](../openmoxie-feature-audit.md) §4.2 **BEYOND #6**. A content pack
can carry a small program, the `extension`. That lets a stranger's pack *do* things (set a timer, check
the clock, remember a score) without the appliance trusting whoever wrote it. The program is a
declarative rule list written in a closed JSON-AST expression language. It is total, metered,
capability-scoped and explainable in English. Our own pure-stdlib code interprets it, with no `exec`,
no parser, no loops and no host object the program can reach.

**Clean-room.** Wire claims come from our own recovered corpus
([`behavior-markup.md`](../../reverse-engineering/runtime/behavior-markup.md), the
[content-module contract](../content-module-contract.md)). We read OpenMoxie (MIT, © Justin Beghtol)
as prior art at commit `c8c2d380efd37d2e83761957587f5d08f73b3a63` and cite it by path. We port its
behaviour, not its code. See [`ATTRIBUTION.md`](../../../ATTRIBUTION.md).

---

## 0. The boundary: cloud-side only

**An extension runs in our cloud. It never runs on the robot.** An extension produces the same three
things the LLM path produces: text, behaviour markup and execution actions, carried in a
`RemoteChatResponse`. The robot cannot tell which one wrote a line. The sandbox therefore protects the
*appliance and its other children*: sibling memory, nicknames, the turn budget and the gateway key. Even
a fully trusted extension can only emit markup from the frozen [`vocab.py`](../../../mqtt/moxie_sdk/vocab.py)
catalogue and actions from a closed allowlist. A sandbox escape would compromise our server. It would
not compromise the robot.

## 1. Why

Packs ([`content-packs.md`](content-packs.md)) made content shareable, but a pack is declarative only,
and a module's `code` field is never run ([`content-packs.md`](content-packs.md) §2.2). Upstream's
`MoxieTime` or `MoxieTimers` therefore imports as a global that matches and then does nothing. Every
behaviour that needs a computation (count, time, a timer, a score) was out of reach. Running upstream-style
`exec` would turn every pack into an arbitrary-code-execution channel (§2.4). This design sets a
different ceiling: the worst a malicious pack can do is *stop working*.

## 2. What the design rests on

### 2.1 The seam

`ContentApp.respond()` runs globals first. Before the design, a matched global only produced a reply if
a registered Python handler existed, and otherwise fell through to the conversation. An extension fills
that socket. Its whole output surface is a `Volley`
([`volley.py`](../../../mqtt/moxie_sdk/content/volley.py)): `set_output`, `add_execution_action`,
`add_subscriptions`, module-namespaced `persist_data` and per-turn `local_data`. Handler output goes
through the same `parse_action_tags` + `annotate` path as model output. An extension's line, though,
may act only on an action tag written whole in its rule's own text (a `say` or `let` string literal):
the host takes any other `<exit>`, `<sleep>` or `<launch:…>` out of the line and tells the parent
(§4.5, §6.4), so the review's sentence for the rule (§5.4) names every exit, sleep or launch its
line can make the robot take ([content-module-contract.md](../content-module-contract.md), "What a
line's action tags may do"). A conversation's `opener` is not an extension, but it is held to the
same rule: an action tag in it acts only when written whole in the alternative said, as written
before it is rendered, and the pack review names each such tag in the opener's own row (the
contract's "A conversation's opener"). A catalogue mark in a line, in an opener or in the model's
line a conversation's prompt steers is held to the pack gate: only a mark this appliance could
mint itself (an expressive verb, catalogue ids, read whole) reaches the robot, a system verb never
does, and the parent is told (§4.5 `say`, §6.4; the contract's "What pack content may put on the
robot", which also says what the gate does not cover: a handler's line, the plain LLM brain, and
the ids of an expressive verb such as a system behaviour tree).

### 2.2 The pack format it rides in

The pack's field allowlist (`SPEC` in [`packs/items.py`](../../../mqtt/moxie_sdk/content/packs/items.py))
is positive and pinned against the dataclasses. The pack digest covers every item's `data`. The review
ticks nothing unless the digest verdict is `ok`. Packs are deliberately **unsigned**, and they could
afford that because an imported pack could not execute anything. Adding execution must replace that
guarantee with an equally structural one. §5 and §6 are the replacement: *the program cannot express the
harm*.

### 2.3 Machinery it must respect

The 6 s turn budget (`MOXIE_BRAIN_BUDGET_S` in [`mqtt/config.py`](../../../mqtt/config.py)), the
output-side safety classifier ([`safety.py`](../../../mqtt/moxie_sdk/safety.py)), and `MemoryStore`
(namespaced, bounded, dropped at the store under `LoggingPolicy.NO_DATA`). The precedent is the
declarative `memory` block, which already replaced upstream's `complete_handler` script.

### 2.4 Prior art: upstream's `exec`

OpenMoxie has exactly two `exec` sites: `site/hive/mqtt/global_responses.py`:55 (`METHOD` globals) and
`site/hive/mqtt/conversations.py`:271 (conversation hooks). Both pass the module's own `globals()`, so
a `code` string can reach `__builtins__`, the Django ORM, `settings` (the OpenAI key) and the
filesystem. The 10-second timeout wraps only the call to the harvested function in the first site.
Our reading is that `with ThreadPoolExecutor` still waits for the runaway on exit, so the timeout does
not bound the turn (inferred, A4). On an exception the child hears `"Script error: …"`. This is the
right call for a self-hosted server whose operator is also the author. It does not survive the word
*shareable*.

### 2.5 The requirements corpus

Upstream's entire executable library is **6 `code` strings, 9 hook functions and 4 modules**:
`MoxieTime`, `MoxieTimers`, `MemoryChat` and `MoxieGo` (A2). Together they need:

- **reads:** clock, random, `speech`, `entities`, `input_vars`, own-namespace memory, session accounting;
- **values:** numbers, strings, booleans, null, lists, string-keyed maps;
- **ops:** arithmetic (incl. `%`, `floor`), comparison, logic, a lazy conditional, string building,
  `int`/`round`, list and map access, bounded `repeat`, plural;
- **effects:** say, markup, one execution action, subscribe, memory write/delete, per-turn scratch,
  a *handled* flag, one brain call.

**None of the nine hooks loops, recurses or defines a function** (A3). The only construct a sandbox
must refuse outright is one `time.sleep(0.5)` (§5.3). So a language with no loops, no user functions
and a closed operator table covers the whole state of the art.

### 2.6 The Jinja hole (closed)

This hole was found while designing extensions. [`render.py`](../../../mqtt/moxie_sdk/content/render.py)
used a plain `jinja2.Environment`, and `prompt`/`opener` are pack-importable. A template walking
`volley.__init__.__globals__` reached `__builtins__` and ran arbitrary Python (A5). **Fixed:** the
renderer is now a `SandboxedEnvironment`. The dependency-free fallback `_minimal_render` refuses any
path segment that begins with `_`. The container ships `jinja2` deliberately, so its safety rests on the
sandbox rather than on a missing dependency. X3 pins it.

---

## 3. The choice

### 3.1 Options considered

| | Expresses | Escape surface | Runs in Python supervisor **and** a Worker | Parent-reviewable |
|---|---|---|---|---|
| **(a) Restricted JSON-AST language we interpret** | All of §2.5; no loops | Structurally none: JSON values only, no attribute access, no name resolves to a host object | Yes; a JS port is mechanical, pinned by shared vectors | Yes: `explain()` renders English |
| (b) Pure declarative behaviour tree | Not arithmetic over values (the timer sentence) | None | Yes | Best |
| (c) WebAssembly + host imports | Anything | Small and well understood | Worker yes; Python needs a native wheel, which breaks the slim image | No: an opaque blob |
| (d) Embedded VM with stdlib stripped (QuickJS/Starlark/Lua) | Anything | Largest: stripping is a deny-list, and every escape in this class is a reachable host object | No: a Worker cannot load a native VM, and its own `eval` exposes `fetch`, env bindings and the gateway key | No |

### 3.2 The decision

> **(a), shaped by (b): a declarative rule list over a total JSON-AST expression language, interpreted
> by our own pure-stdlib code, with no `exec`, no parser, no loops and no reachable host object.**

Why:

1. It is a **positive-list** design in a codebase whose safety already rests on positive lists
   (`SPEC`, `vocab.py`, the closed QR grammar). Option (d) would bring in the only deny-list, at the
   most dangerous point.
2. The escape tests (§9.1) are provable properties of a closed table, not bets against future CVEs.
3. **The AST is the artefact.** The pack digests it, `explain()` renders it, the evaluator walks it,
   and a future JS evaluator would walk the same object. No compile step can disagree with its input.
4. **Failure is boring.** Every op is total, so the evaluator always returns.

### 3.3 What we give up

- **Real programming.** There are no loops, user functions or recursion. A need for iteration becomes a
  new host primitive with its own capability, never a loop keyword.
- **Verbatim upstream portability.** Upstream hooks are **hand-ported** (§8). A Python→AST compiler is
  refused (§7.4).
- **Authoring ergonomics.** Hand-writing JSON is the P0 experience. A text surface that compiles *to*
  the AST, outside the trust boundary, is P1.
- **"Just let me run code".** A Wasm runtime is P2. It would sit behind the same capability table,
  opt-in, and be labelled un-reviewable. This is why §5 describes a host API, not evaluator features.
- **Asynchrony.** An extension is a pure function of one turn's facts. Long-lived timing belongs to
  the robot (`eb_timer_request`).

---

## 4. The design

### 4.1 Shape: a field on an item, not a new kind

`conversation` and `global` items each carry an optional `extension` block:

```json
{"ext_format": 1,
 "capabilities": ["clock", "memory.write", "act.eb_timer_request", "say", "handled"],
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
      {"handled": true}]}]}
```

The program is part of the item, not a separate item, because it is meaningless without its trigger.
That also lets the existing review diff and `local_rev` machinery cover it unchanged.

`on` selects the hook (`grammar.HOOKS`):

| `on` | Fires | Upstream equivalent | State |
|---|---|---|---|
| `global` | A matched `globals[]` pattern, before the conversation | `handle_volley` / `get_response` | shipped |
| `turn.before` | Before the prompt renders; `handled` suppresses the model. Also the only hook a subscribed robot event wakes (§8, G6) | `pre_process` | shipped |
| `turn.after` | After the model's text; may rewrite it | `post_process` | P1 (output-safety ordering unsettled) |
| `session.end` | At exit, beside the declarative `memory` block | `complete_handler` | P1 (must not double-write a namespace) |

### 4.2 The expression language: a closed grammar over JSON

An expression is exactly one of:

1. a JSON number, string, `true`, `false` or `null` (a literal);
2. `{"lit": <any JSON>}` (a list or map literal, or a string that would look like an op);
3. `{"var": "<path>"}`, a dotted lookup in the fact base (§4.4). A numeric segment indexes a list;
4. `{"<op>": [<expr>, ...]}`, one op from the closed table.

An object with more than one key, an unknown op or a wrong argument count is a **load-time refusal**.
So is a fact op or a path whose capability is not declared, and, when the host passes `grants`, one that
is not granted.

**The op table** (`grammar.OPS`, 53 ops, frozen as a literal in X1) is the audit surface:

| Group | Ops | Notes |
|---|---|---|
| Arithmetic | `+ - * / % floor ceil round abs min max` | `/` or `%` by zero gives the error value (§4.6) |
| Comparison | `== != < <= > >=` | Cross-type comparison is `false` (`!=` is `true`), never an error |
| Logic | `and or not` | `and`/`or` are lazy and n-ary |
| Conditional | `if` | `[test, then, else?]`, lazy in both branches |
| Strings | `concat lower upper trim len slice starts_with ends_with contains replace split join repeat format str plural` | `repeat` caps n at 16. `format` needs an explicit, bounded spec (`[0][width≤5 digits][.prec]` + `d`/`f`/`s`), so output is byte-stable across hosts |
| Numbers | `int num` | `int("banana")` is the error value, so junk capture groups fail loudly |
| Lists | `list get len compact reverse sort` | `get` is `[container, key, default?]`. `compact` drops nulls and empty strings. `sort` is scalars only |
| Maps | `get has keys` | `keys` is sorted. `has` is the one op that tests for the error value instead of propagating it |
| Facts | `clock.ms clock.local random.int random.pick presence.face_present session.total_volleys session.is_empty` | Each costs its capability (§5). `clock.local` returns a map `{hour, minute, weekday, iso}` |

Never in the table, and not addable without re-opening this design: name-to-object resolution,
attribute or index access on a non-JSON value, regex construction, unbounded string multiplication,
`eval`, or anything that returns a host handle.

**Load-time caps** (`grammar.py`): expression depth ≤ 32, statements per rule ≤ 32, rules ≤ 64, nodes
per extension ≤ 4096, capabilities ≤ 32, arguments per op ≤ 32. Capability and op names must already
be NFKC-normal and match `^[a-z0-9_.]+$`. A name that is not normal is **refused**, never folded, so a
homoglyph cannot pass as a real name (X8).

### 4.3 Rules, `let` and statements

```
extension := { ext_format: 1, capabilities[], on, rules[] }
rule      := { when?: expr, let?: {name: expr, ...}, do: [stmt, ...] }
stmt      := {say: expr} | {markup: expr} | {remember: {key, value}} | {forget: {key}}
           | {scratch: {key, value}} | {act: {name, args[]}} | {subscribe: [event, ...]}
           | {handled: bool} | {note: expr} | {brain: {prompt: expr}}   # brain: P1, refused
```

- Rules run in order. The **first** rule whose `when` is truthy runs its `do`, and the extension stops.
  A missing `when` means always. If no rule matches, the extension succeeded and did nothing.
- `let` is an ordered map of name to expression. Each binding is visible to the later ones, to `when` and
  to `do`. Bindings are values, never references.
- A `do` list is flat and straight-line: no nesting and no jumps. Its maximum cost is known at load.
- A `remember`/`forget`/`scratch` key is dot-segmented, uses `[A-Za-z0-9_-]` with no segment starting
  with `_`, and has no `/` and no empty segment (`grammar._KEY`).
- `note` replaces `print()`: one log line, capped, never spoken, never persisted.

### 4.4 The fact base: plain JSON, built by the host

The evaluator never sees a `Volley`, `Session`, `MemoryStore` or any other live object. Before
evaluating, `ext_host.ext_facts()` builds a plain dict. Every value goes through `_ext_json`, which
rebuilds containers, turns non-JSON into `None` and drops `_`-prefixed keys:

```python
facts = {
  "speech":     str,                # ≤ 2000 chars
  "entities":   [str, ...],         # ≤ 16, each ≤ 256 chars
  "input_vars": {str: str},         # robot-supplied, untrusted; ≤ 32, each ≤ 512 chars
  "child":      {"nickname": str},  # only if granted; pronouns/birthday/notes only with child.profile
  "memory":     {...},              # ONLY this item's namespace, if memory.read; {} if > 32 KiB
  "scratch":    {},                 # per-turn, starts empty
  "session":    {"total_volleys": int, "is_empty": bool, "overflow": bool},
  "presence":   {"face_present": bool, "line": str},   # only if presence granted
}
```

Three rules make this airtight:

1. **Primitives only.** X2 walks the whole structure and fails on anything other than
   `str|int|float|bool|None|list|dict`. There is nothing to attribute-walk to.
2. **A path segment starting with `_` is invalid at load.** `__class__` is not blocked at runtime; it is
   not a valid program.
3. **The host chooses the namespace.** `ext_host.ext_namespace()` uses a conversation's declared
   `memory.namespace`, or otherwise `ext:<slug of kind:key>`. The grammar has no words for a namespace,
   device, collection or path (A13).

### 4.5 Effects: applied after the program ends

The evaluator is pure. Statements append to an effect list, and `ext_host.apply_ext_effects()` applies
it **after** the program returns, in order, under the §6.3 caps. The caps are checked before *any*
effect is applied. A breach anywhere discards the list whole (X11), so nothing is ever half-applied.

| Statement | What the host does |
|---|---|
| `say` | Acts only on the action tags written whole in the rule's own text (`literal_actions`, read with the robot's own parse): any other `<exit>`, `<sleep>` or `<launch:…>` the line carries is taken out first (`actions.drop_action_tags`), counted and reported (§6.4), never said or acted on; so is a tag that would only form once the tags that stay are lifted (`<ex<sleep>it>` with its sleep kept would be spoken as `<exit>`). The line's markup reaches the robot only as `robot_markup` leaves it (the `markup` row), and a catalogue mark in the line itself is held to the pack gate (`pack_line`: a mark stays only when this appliance could have minted it, an expressive verb with catalogue ids read whole; every other `<mark` is cut, and a system verb is never sent and is told to the parent, §6.4; the contract's "What pack content may put on the robot"). Then `volley.set_output`, after the output-side safety classifier (a blocked line becomes a redirect) and after `annotate` if no markup was authored. The set a rule may act on is read once per program, linearly in its text (a megabyte of spaces after `<exit:` in 16 ms, measured) |
| `markup` | A `say`'s markup and a `markup` statement reach the robot only with no tag of ours and nothing the catalogue's check refuses, or not at all (`robot_markup`), checked on exactly what the robot path sends (our tags lifted once, the rest tidied): every tag of ours is lifted as the robot's own parse lifts them (`actions.lift_action_tags`, one pass, malformed ones too); then the gate (`ext_markup`, tag by tag against `vocab.py`: a tag with an id the catalogue refuses, a mark the catalogue's own mark pattern does not read whole, a tag cut short by a `>` inside its own quotes and a tag holding another `<` are dropped and counted, the rest of the markup kept); then the robot path's tidying (`tidy_spoken_text`); then, if a tag of ours, a tag of a form the gate drops, a catalogue tag left open or anything the catalogue's whole-text check refuses is left (a dropped tag stood between the pieces of another, `<ex<ex<mark name="cmd:zzz"/>it>it>`; a quoted `>` hid a value from the tag-by-tag read, `<spurt x" spurt_id="n>pe"/>`), the markup is dropped whole and the runtime's markup floor speaks the line. A usel or a spurt whose value the catalogue's patterns do not read (single quotes, spaces around `=`) is not refused, as on dev. Never a refusal or a breach: no action tag in markup is acted on. Every pass is linear, the whole-text check (`vocab.validate_markup`) included, since it runs only on tags of a form the gate keeps with none left open (before round 10 it read on from each mark the catalogue's pattern does not read whole: 15-27 ms on one 8 KB markup of the round-9 review's shape, 0.3 ms now, measured); five turns of four 8 KB markups of the shapes that were super-linear take 7-31 ms in all (five turns of four runs of `<mark` openings took 3.5 s before round 9, the fixpoint pass 2.1-3.7 s a turn before round 8). The gate also drops a mark whose verb pack content may not send (every catalogue verb outside `vocab.EXPRESSIVE_VERBS`, the system verbs `start-systemunpair` and `start-systemsuspend` first among them) and names the verb to the parent (`pack_markup`, §6.4). A mark written in a `say`'s line, or in a conversation's opener, reaches the robot as the line's own markup (the floor sends a line holding `<` as it is) and is held to the same rule before the line is kept (`pack_line`, `pack_spoken`; the contract's "What pack content may put on the robot") |
| `remember` / `forget` | `MemoryStore.merge` on `(device_id, namespace)`, both supplied by the host. Dropped at the store under `NO_DATA` |
| `scratch` | `volley.local_data`, per turn, never persisted |
| `act` | One `add_execution_action(name, args)`. `execution_actions_of` turns it into an `execute` `RemoteChatAction` with `function_id`/`function_args`. The name must be in `ACTION_WORDS`, checked at load **and** at the host boundary, and individually granted |
| `subscribe` | `volley.add_subscriptions`, which **adds** and never replaces, from the closed `SUBSCRIBE_EVENTS` vocabulary ([`vision.md`](../vision.md) §1.1–1.2). `subscriptions_of` re-bounds the names. The runtime's `_merge_subscriptions` merges them *into* its own vision subscription, so a pack can add a perception but never remove one the runtime depends on |
| `handled` | Suppresses the model call for this turn |
| `note` | One capped log line |

### 4.6 The error value

`/` by zero, `int("banana")` and a mixed-type `sort` each yield a distinguished, falsy **error
value** (`grammar.ERROR`). Any op given an error returns the error, except `has`, which tests for it.
If an error value reaches `say`, `markup`, `remember` or `act`, the extension fails (§6.4) instead of
speaking. A missing key or an out-of-range index yields `null`. The evaluator never raises.

---

## 5. The capability model

An extension **declares** its capabilities, the host **grants or refuses** them, and the parent **sees
them in plain words** at review. The check happens at load (`validate.py`) and runs **both ways**
(X10). Using an undeclared capability is refused, and so is declaring one the AST never uses. The
declared set equals the used set, so the list a parent reads is exactly what the program can do.

### 5.1 The surface

| Capability | Grants | Imported pack (`DEFAULT_GRANTS`) | Why |
|---|---|:--:|---|
| `say` | Set the spoken line (post-safety) | granted | Least dangerous; passes the classifier like a model line |
| `handled` | Suppress the model this turn | granted | Same as a matched global with a handler |
| `session` | `total_volleys`, `is_empty`, `overflow` | granted | Turn shape, no content |
| `child.nickname` | Nickname only | granted, named in review | Already in every prompt |
| `child.profile` | Pronouns, birthday, notes | refused | Highest-value PII; no corpus need |
| `clock` | `clock.ms`, `clock.local` | refused | A timing side-channel ("misbehave at 2 a.m.") |
| `random` | `random.int`, `random.pick` from a **seeded** PRNG | refused | Entropy defeats replay (§6.1) |
| `memory.read` / `memory.write` | Own namespace only | refused | The child's remembered life |
| `presence` | Face present, presence line | refused | A physical-world observation |
| `markup` | Author raw markup (catalogue-checked; the expressive verbs only, never a system verb) | refused | Reaches the robot's body |
| `act.<name>` | One execution action **per name** from `ACTION_WORDS` (`eb_timer_request`, `eb_enable_qr`, `eb_wake`) | refused | "Set a timer" and "turn on the camera" are different decisions |
| `subscribe` | Robot events from `SUBSCRIBE_EVENTS` | refused | Pairs with `act` (`MoxieGo` arms the QR scanner *and* listens for it). One sentence for the whole capability (*"Can listen for things the robot notices"*), not one per event. The most privacy-adjacent grant |
| `brain` | One model call per turn | refused, **P1** (`P1_CAPABILITIES`) | Costs money and latency; output not predictable from the AST |
| `schedule.request` | Ask to be offered in the day plan | refused, **P1/P2** | Requesting can be reviewed; deciding is not delegated |

**Who gets more than the default four.** `ContentApp` grants `DEFAULT_GRANTS` to imported extensions.
`ext_host.SHIPPED_EXTRA_GRANTS` adds `clock`, `random`, `memory.read`, `memory.write`, `presence`,
`markup` and `act.eb_timer_request` for **our shipped extensions only**. It is anchored to the
program's **digest**, not its name, so an imported pack that overrides a shipped key gets only the
default four (T18). No env var or console control widens grants. The supervisor passes no `ext_grants`.
The parent-facing grant flow is P1 (§10), and `subscribe` in particular is exercised only by tests today.

### 5.2 What no grant ever reaches

Network, filesystem, subprocess, environment variables, credentials, another device's store, another
module's namespace, another child, the safety rules, `LoggingPolicy`, robot config or permits,
telemetry, telehealth, the pack store, other extensions, the host runtime, and the host's own clock or
entropy. **No operator, statement or path names them**, so no config flag can switch them on. The
invariant X-tests check is this: *the set of strings that resolve to anything is `OPS` (§4.2) plus
`FACT_ROOTS` (§4.4), both finite and enumerated in `grammar.py`.*

### 5.3 `sleep` is not on the list at any level

The corpus asks for it once (`MoxieTimers.pre_process`), and the answer is permanently no. A sleep
spends the turn's 6 s to do nothing. The corpus-correct replacement is in the same hook:
`<break time="1s"/>`, which the **robot** honours during playback at no cost to the turn. Anything
longer-lived is a robot-side timer (`act.eb_timer_request`).

### 5.4 How the parent sees it

Both views are pure functions of the AST ([`explain.py`](../../../mqtt/moxie_sdk/content/ext/explain.py),
`validate.grant_list`):

1. **The grant list.** One plain sentence per capability, from the fixed `CAPABILITY_WORDS` /
   `ACTION_WORDS` tables, never from author text: *"Can speak to your child · Can check the time"*.
2. **`explain(ext)`.** One English sentence per rule: *"Whenever this activity is triggered: tells your
   child 'The time is …' and answers without asking the AI."* T13 requires every capability to have
   words, so a new capability cannot ship without them. A rule's sentence ends with what its line's
   action tags make happen: every tag written whole in the rule's own text, which is all the host lets
   the line act on (§4.5), so the sentence names every exit, sleep or launch the line can send, at
   least as *"sometimes"* (the full statement, and what the wording means, is in
   [content-module-contract.md](../content-module-contract.md)).

---

## 6. Limits

### 6.1 Determinism

Same AST + same facts + same seed ⇒ a byte-identical effect list (T7).

- **No ambient clock.** The host injects `now_ms` and `clock.local` once per turn. No module in `ext/`
  imports `time`, `random`, `os`, `datetime`, `secrets` or `subprocess`. The only imports it may make
  outside the package are `math`, `re`, `unicodedata` and `dataclasses` (X7 walks every file's AST).
- **No ambient entropy.** `random.*` draws from a PRNG seeded by the host with
  `sha256(device_id|speech|extension_id|second)`, so a turn can be replayed from its inputs.
- **Host-independent output.** `keys` sorts, `sort` is scalars only, `format` needs a spec, and `str`
  formats a float by one fixed rule. This is what would make a JS port checkable against the same
  vectors.

### 6.2 Budgets

Defaults are in `grammar.py`. Four of them are env vars in [`mqtt/config.py`](../../../mqtt/config.py).

| Limit | Env var | Default | Stops |
|---|---|:--:|---|
| Steps | `MOXIE_EXT_MAX_STEPS` | 10000 | One per node, statement and effect. A backstop, since cost is static |
| Wall clock | `MOXIE_EXT_BUDGET_S` | 0.25 s | Checked against an **injected** monotonic clock every 256 steps. No threads or signals, so it behaves the same in a Worker isolate |
| One value | `MOXIE_EXT_MAX_VALUE_BYTES` | 16384 | Any single string, list or map |
| All values | `MOXIE_EXT_MAX_TOTAL_BYTES` | 262144 | Death by a thousand 16 KiB strings |
| Depth / nodes | load-time constants | 32 / 4096 | Stack depth; the evaluator is depth-counted, so `RecursionError` cannot escape |
| Breaches | `MOXIE_EXT_MAX_BREACHES` | 3 | Quarantine (§6.4) |

**The budget comes out of the turn; it is not added to it.** `config.py` raises at import if
`MOXIE_EXT_BUDGET_S >= MOXIE_BRAIN_BUDGET_S` (T16).

### 6.3 Output caps

Checked before any effect applies: spoken text 1000 chars · markup 8192 chars · 4 execution actions ·
8 subscriptions · 8 memory writes · 4 `note` lines of 200 chars each. Over any cap, the whole effect list
is discarded.

### 6.4 On breach: fail the extension, not the turn

On any breach (steps, clock, size, output cap, refused capability, invalid AST, or an error value
reaching an effect), `evaluate()` returns `ExtResult(ok=False, breach=…)`, the effect list is
discarded, and `ContentApp` carries on as if no extension existed. A failed `global` falls through to
the conversation, and a failed `turn.before` lets the model run. **The child hears nothing about it.**

The parent does. `ContentApp._ext_breach` appends to the per-robot `ext_events` `JsonStore` ring (capped
at 50) **once per (device, extension, breach code)**, with a plain sentence from `BREACH_WORDS` (*"it took
too long"*). After `MOXIE_EXT_MAX_BREACHES` breaches for the same (device, extension), it is
**quarantined** and not evaluated again. Both counters live on the `ContentApp` instance: they last until
the process restarts and are **not** reset per chat session (a content reload swaps the module but keeps
the app).

An action tag the host took out of a line (§4.5: one the rule's own text does not write whole) is told
the same way, one row per (device, extension, reason `tag`), with *"it tried to make Moxie do something
its review did not name"*. It is not a breach and does not count towards quarantine: as with a markup
tag the catalogue drops, the line is said without it and the turn goes on. `ContentApp._ext_refusals`
counts them apart from `_ext_breaches`.

A catalogue command the pack gate cut from a line, an opener, a markup or the model's line under
a conversation (a mark whose verb is outside `vocab.EXPRESSIVE_VERBS`; the system verbs
`start-systemunpair` and `start-systemsuspend` never reach the robot through the content brain) is
told the same way, one row per (device, item, reason `command:<verb>`), hook `opener` for an
opener and `model` for the model's line, with *"it tried to send Moxie the system command
start-systemunpair, which would unpair Moxie from this home; no activity may, so Moxie said its line
without it"* (for the catalogue's other verbs: *"it tried to send Moxie the robot command scripted,
which an activity may not; Moxie said its line without it"*). The verb is the catalogue's own, never
author text; a mark naming no catalogue verb is a catalogue drop, counted and not told. Not a breach
either: `ContentApp._ext_commands_refused` counts them, and nothing here counts towards quarantine.

---

## 7. How an extension travels

### 7.1 Inside a pack, as a field on an item

`SPEC["conversation"]` and `SPEC["global"]` each include `("extension", _d, {})`. `_d` is
`json.loads(json.dumps(v))`, so a stored extension is provably JSON-only before the validator sees it
(A11). The `FIELDS` pin test (T15) fails if `extension` is in `SPEC` but not on the dataclass.

### 7.2 The digest covers it

An extension sits inside an item's `data`, so changing one operator changes the pack digest.
`parse_pack` then reports `mismatch` and `review_pack` pre-ticks nothing (T12).

### 7.3 The review, and the escalation rule

`review.extension_warnings()` adds three things to a review row: the grant list, `explain()`'s
sentences, and a note if the program installs but cannot run here (malformed, or it needs a P1
capability). It also adds **the escalation rule**:

> An incoming extension that declares a capability the installed version did not is **never
> pre-ticked**, whatever its state, and gets its own sentence: *"This update asks for more than the
> version you have: it now wants to …"*

The comparison is over the capability **set**, independent of `source_version` and of local edits. A
version bump cannot escalate quietly, and a shrinking set is not an escalation. The existing states
(see [`content-packs.md`](content-packs.md) §2.3) still apply on top, so `CONFLICT` + escalation gives
two sentences (T11).

### 7.4 `code` stays inert forever and is not a migration path

The `code` field keeps its warning: *"carries a `code` block (Python), which this appliance never runs
— see `extension` for behaviour this appliance can run"*. **Do not compile `code` into an AST.** A
Python→AST compiler is a parser for a Turing-complete language sitting in the trusted half. It would
bring back exactly the surface this design removes. Six hooks is a hand-port (§8). X3 also asserts that
no file under `content/` calls `exec`, `eval`, `compile` or `__import__`.

### 7.5 Storage and reload

There is no new storage. The extension lives in the item's `data` in the existing pack overlay, and
`reload_content()` makes it live on the next turn. `ContentApp.run_extension` **re-validates on every
turn**, not only at import (T17). A stored program that the current validator rejects is refused and
logged; it never runs under old rules. The only new collection is `ext_events` (§6.4).

---

## 8. Conformance: all six upstream hooks, hand-ported

[`sim/tests/data/ext_conformance.json`](../../../sim/tests/data/ext_conformance.json) (generated by
[`sim/tools/build_ext_conformance.py`](../../../sim/tools/build_ext_conformance.py)) holds
`(ast, facts, seed, expected_effects)` per row. It proves the grammar is expressive enough, it is the
regression suite, and it would be the contract for a JS evaluator.

| # | Upstream hook | Port | Capabilities | State |
|--:|---|---|---|---|
| G1 | `MoxieTime.get_response` | One rule: `clock.local` hour mod 12, "AY M"/"P M" | `clock` | passing; **ships** as the `What Time Is It` global in [`starter.json`](../../../mqtt/content_modules/starter.json) |
| G2 | `MoxieTimers` set | The §4.1 example | `clock`, `memory.write`, `act.eb_timer_request` | passing |
| G3 | `MoxieTimers` status/cancel | h/m/s sentence via `let`s + `compact` + `join`; cancel via `act` + `forget` | + `memory.read` | passing |
| G4 | `MoxieTimers` wake | `forget`, `scratch`, `markup` with `repeat` + `<break>`, `say`, `handled`; the `sleep` is dropped (§5.3) | `memory.write`, `markup` | passing |
| G5 | `MemoryChat` opener | `random.pick` over summaries + one `brain` call | `memory.read`, `random`, `brain` | **`xfail(strict=True)`** until `brain` exists (`test_ext.py::P1_REASON`) |
| G6 | `MoxieGo` QR | No speech → arm the scanner and subscribe; `eb-qr-event` starting with `GO` → say a slice; else re-arm | `act.eb_enable_qr`, `subscribe` | passing |

The strict `xfail` is deliberate. When a capability lands, the XPASS fails the suite and forces the
reason to be updated. That is how G2/G3 (`act`) and G6 (`subscribe`) turned green.

**G6's inbound half.** A perception event like `eb-qr-event` is diverted to `_on_vision_turn` before
any app sees it, and that divert is what keeps a face walking past from costing a model call
([`vision.md`](../vision.md) §7.1). `_on_vision_turn` offers the event to `MoxieApp.perceive`, and
`ContentApp.perceive` runs **only the local evaluator** on the active conversation's `turn.before`
extension. The event never reaches `respond`, never enters history, and never calls a model. The gate
is the outbound record read backwards. `_merge_subscriptions` records `_pack_subscribed[device][event]`
when it accepts a request, and only that record can wake a pack, so a pack cannot be woken by an event
it never asked for. The zero-model-call property is asserted from `moxie_sdk.chat.model_calls()`, with a
control turn in the same test.

A second shipped global, `Timer`, uses `act.eb_timer_request`. That is why that one action is in
`SHIPPED_EXTRA_GRANTS`.

---

## 9. Tests

Hermetic: no broker, robot, network or browser.

### 9.1 Escapes: [`test_ext_escapes.py`](../../../sim/tests/test_ext_escapes.py)

| # | Guarantee |
|--:|---|
| X1 | No op or path can name an import, a builtin or a dunder. The op and statement key sets are frozen literals |
| X2 | The fact base contains no host object, even from a hostile input |
| X3 | A prompt cannot run Python through Jinja (both renderers); nothing under `content/` calls `exec`/`eval`/`compile`/`__import__` |
| X4 | No loop or recursion construct exists; a costly AST hits the step budget; the wall-clock budget holds without threads or signals |
| X5 | A huge allocation fails the op, not the process; the total-allocation counter holds |
| X6 | Deep nesting is refused at load; the evaluator is depth-counted even without validation |
| X7 | The import audit over every `ext/` file; injected clock and seed; fact ops need their capability |
| X8 | Unicode tricks (dotless ı, zero-width, fullwidth, RTL, upper case) cannot change a capability or op name, or its rendered sentence |
| X9 | No reading another namespace; traversal keys refused at load; the store call uses the host-supplied namespace; a second robot's memory is byte-unchanged |
| X10 | Capabilities are checked in both directions; the default set is exactly four; P1 capabilities are rendered and refused; `act` names and `subscribe` events are bounded at load and at the host |
| X11 | All-or-nothing effects; an error value reaching an effect fails the extension; every bad input returns a value |
| X12 | The grammar has no regex op; `MAX_PATTERN_CHARS` still caps an item's `pattern` |

A final test asserts the §5.2 invariant as the enumerated union of `OPS` and `FACT_ROOTS`.

### 9.2 Behaviour: [`test_ext.py`](../../../sim/tests/test_ext.py), [`test_ext_act.py`](../../../sim/tests/test_ext_act.py), [`test_ext_subscribe.py`](../../../sim/tests/test_ext_subscribe.py)

`test_ext.py` T1–T6 are the §8 goldens. The rest cover: T7 determinism · T8 a breach never ends the turn
and writes nothing · T9 quarantine and a single event row · T10 pack round trip · T11 escalation matrix
· T12 digest coverage · T13 English with no JSON leaking, and words for every capability · T14 `NO_DATA`
drops writes and notes and still speaks · T15 allowlist pin · T16 budget inside the turn and env vars ·
T17 validation on every run and ungranted capabilities never run · T18 the shipped G1 runs end to end
with no model call, and a look-alike import does not inherit its grants. `test_ext_act.py` follows `act`
from the effect list to `wire.encode_action`. `test_ext_subscribe.py` covers the merge direction, the
wire and the inbound wake. [`test_sil_brains_and_ext.py`](../../../sim/tests/test_sil_brains_and_ext.py)
runs extensions over the SIL.

**Mutation checks.** [`sim/tools/ext_mutation_check.py`](../../../sim/tools/ext_mutation_check.py) and
[`subscribe_mutation_check.py`](../../../sim/tools/subscribe_mutation_check.py) delete each guard in turn
and require its test to go red. Run them by hand after touching `ext/`, `ext_host.py`, `render.py` or
the pattern cap.

### 9.3 What only a real deployment can settle

Whether a JS evaluator matches the Python one in `workerd` (number formatting, CPU accounting). Whether
a real parent reads the grant list and sentences correctly. Whether authors tolerate writing ASTs.

---

## 10. Acceptance criteria, and open work

P0's criteria, all met. Tests cite them by number.

1. `render_prompt` cannot run Python from a template, with or without `jinja2` (X3).
2. `extension` is a validated pack field: the digest covers it, the `FIELDS` pin includes it, and a
   malformed one is refused at import and at load with a readable reason.
3. X1–X12 pass, and each fails when its guard is removed (the mutation checks).
4. `capabilities[]` equals the set the AST uses, or it does not install (X10).
5. The default-granted set is exactly `{say, handled, session, child.nickname}`, and nothing widens it
   without a code change.
6. Nothing an extension can express reaches the network, filesystem, a subprocess, env vars,
   credentials, another namespace or device, or the safety rules. §5.2's invariant is asserted as the
   union of `OPS` and `FACT_ROOTS`.
7. A breach fails the extension and never the turn: nothing half-written, no error text to the child,
   one `ext_events` entry, quarantine after three (T8, T9, X11).
8. `MOXIE_EXT_BUDGET_S < MOXIE_BRAIN_BUDGET_S` is enforced at startup (T16).
9. All six §8 ASTs exist and validate; the not-yet-grantable one is a strict `xfail`; G1 ships (T18).
10. `explain()` gives one sentence per rule, and every capability has parent words (T13).
11. A pack round-trips with an extension inside; an escalation is un-ticked (T10, T11).
12. The [content-module contract](../content-module-contract.md) documents the format, and `code` is
    still never executed.

Still open:

- **The parent grant flow and console card.** This is the big one. Without it, `act` and `subscribe`
  on an imported pack are refused at load and reachable only from tests. Only our digest-anchored
  shipped programs get more than the default four. Widening grants is a decision about what a parent
  is agreeing to (see [`config-and-telemetry-contract`](../config-and-telemetry-contract.md)'s
  `LoggingPolicy`), not a one-line frozenset edit.
- **`brain`**, with its one-call-per-turn budget. This unblocks G5.
- **`turn.after` and `session.end`.**
- **A text surface** that compiles to the AST, outside the trust boundary.
- **A JS evaluator** passing the same conformance file in `workerd` (none exists yet).
- **P2:** a Wasm runtime behind the same capability table, labelled un-reviewable; publisher
  signatures once there is a trust root; a schedule-request channel; a pack asset store.

## 11. Risks

| # | Risk | Mitigation |
|--:|---|---|
| R1 | The op table grows until it is a language | `OPS` is a frozen literal in X1, so a new op needs a test edit and a reviewer. New capabilities need parent words (T13) |
| R2 | Authors leave rather than hand-write JSON | P1 text surface; the §8 ASTs are copy-paste starters |
| R3 | A JS port drifts from Python | The conformance file is the contract |
| R4 | A *valid* markup id still makes Moxie lurch or blare | `markup` is refused by default and named in review; on every channel (a line, an opener, a markup, the model's line a prompt steers) only a mark this appliance could mint itself passes, an expressive verb with catalogue ids, never a system verb, and the review names every command an item's text writes. Open: a system or test behaviour tree (`Bht_System_Suspend`, `Bht_Motor_Test`) is a catalogue id `behaviour-tree` accepts, as on dev (owner question) |
| R5 | 0.25 s is a guess; slow appliances may quarantine | Env var plus the `ext_events` ring, so it is visible |
| R6 | A pathological item `pattern` can still stall matching (stdlib regex has no timeout) | Named, not fixed; X12 marks the boundary |
| R7 | Readers assume `code` becomes `extension` | §7.4 and the review wording |

## 12. Assumptions

| # | Assumption | State |
|--:|---|---|
| A1 | Upstream has exactly two `exec` sites, both passing real `globals()` | proven (grep at `c8c2d38`) |
| A2 | Upstream's executable library is 6 `code` strings / 9 hooks / 4 modules | proven |
| A3 | No upstream hook loops, recurses or defines a function | proven; the premise of §3.2 |
| A4 | Upstream's 10 s timeout does not bound a runaway hook | inferred from code and stdlib semantics; we do not port the path |
| A5 | A plain Jinja `Environment` let a pack `prompt` run Python | proven by execution; fixed (§2.6), pinned by X3 |
| A7 | 0.25 s / 10000 steps / 16 KiB / 256 KiB / 3 breaches are right | chosen, not measured; all env vars |
| A8 | The JSON-AST evaluator ports to JS byte-for-byte | inferred; number formatting is the likely gap |
| A9 | A Worker's CPU limit fits a 0.25 s slice | unverified |
| A10 | A non-programmer acts correctly on the grant list and sentences | unverified; needs a human test |
| A11 | `_d` makes a stored extension JSON-only before validation | proven |
| A13 | The host can always choose a namespace | shipped as `ext:<slug(kind:key)>` for items without a declared `memory.namespace`, so same-named items in different kinds do not collide |
| A14 | The robot tolerates valid-but-unusual extension-authored markup | unverified; needs hardware |

---
📖 [Backlog index](README.md) · [OpenMoxie feature audit](../openmoxie-feature-audit.md) ·
[Content-module contract](../content-module-contract.md) · [Content packs](content-packs.md) ·
[Live Sim demo](live-sim-demo.md) · [The AI seam](../ai-seam.md) ·
[Behavior markup](../../reverse-engineering/runtime/behavior-markup.md) ·
[Attribution](../../../ATTRIBUTION.md)
