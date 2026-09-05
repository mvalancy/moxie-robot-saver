# 🎴 Printable launch cards — a QR a child shows Moxie, and an activity starts

> **Audit §4.4 #9 · re-scoped 2026-09-03 · 🟢 build-ready.**
> A parent prints a sheet; a child holds a card up to Moxie's face; the robot starts that activity.
> Upstream ships the *paper* (`site/data/qr/extract.py` → 24 PNGs of `GO<launch:MODULE_ID>`, MIT) and
> **we ship none of it** — not the sheet, and not the three server-side hops that would make a scanned
> card mean anything.
>
> **This brief exists because the audit row was wrong, not stale.** Until 2026-09-03 §4.4 #9 read
> *"the action-tag parser already understands `<launch:MOD[:CID]>`; what is missing is a sheet a parent
> prints"* and priced it **S**. That is a real mechanism pointed the wrong way: `parse_action_tags` is
> called on **a brain's own reply text** — [`llm_app.py`](../../../mqtt/moxie_sdk/apps/llm_app.py):407,
> :442, :480 · [`content_app.py`](../../../mqtt/moxie_sdk/content/content_app.py):182, :394 ·
> [`webhook_app.py`](../../../mqtt/moxie_sdk/apps/webhook_app.py):59 — which is the **cloud→robot**
> direction. A scanned card arrives in the opposite direction, as
> `input_vars['$eb_qr_value']` on a vision turn that
> [`moxie_runtime.py::_on_vision_turn`](../../../mqtt/supervisor/moxie_runtime.py):2669-2690 answers
> **without ever consulting it**. Print the sheet alone and you have produced cards nothing acts on.
>
> Effort: **M** — three small pieces, not one, and one of them is an ADOPT item of its own.
>
> **State 2026-09-04:** P0-a 🟡 half done · **P0-b ✅ shipped** · P0-c ⬜ not started. A scanned card now
> becomes a launch against a closed, derived 24-id allowlist, and **T10 has now travelled a wire**: a
> `sim/virtual_moxie.py` robot publishes an `eb-qr-event` carrying a card and ends up *holding* the
> launch in its own client state (`test_launch_cards_sil.py`, 45 tests). Say that precisely — it is
> **proven between our runtime and our simulated client**, not proven. **The ceiling did not move:** no
> physical Moxie has ever sent us an `eb-qr-event`, and a SIL robot is not a robot — it has no camera,
> starts no module, and records actions rather than running them. The browser-SIM leg (T12) is still open.

## 0. The two facts that set the scope

Both were re-verified against `origin/dev` on 2026-09-03 before this brief was written.

### 0.1 The setup app's QR grammar is closed, so a launch card cannot ride it

[`qr-commands.md`](../../reverse-engineering/protocol/qr-commands.md):87-100 — `QRData.ParseFromString`
is one function with exactly three branches (`PA` pairing, `VN` VPN, else JSON `{wifi?, pair?, debug?}`),
`debug.command` is matched against **exactly four** literals (`serial_number_display`,
`restore_factory`, `reset_network`, `bluetooth_pair`), and every other value hits a literal
`else → SetAppState(State.QRDiagnostic)`. There is **no fifth app-side handler** in `bo-wifi`, and
`Assembly-CSharp` carries **zero** references to `QRCommand`.

So the scanner a parent already uses — the one that reads the pairing and endpoint codes — will show a
**diagnostic screen** for `GO<launch:DM>`, not launch anything. Nothing we build changes that, and no
generator we write should ever emit a launch card through the `debug` grammar.

### 0.2 The second scanner is the real path, and it reaches us

There are two QR readers on the robot, same camera, different consumers
([`qr-commands.md`](../../reverse-engineering/protocol/qr-commands.md):305-319):

| Reader | Owner | What it does with the string |
|---|---|---|
| setup / config | `bo-wifi` | the closed grammar in §0.1 |
| **runtime / content** | `bo-android` + `libbo-analytics` | armed by `embodied.robotbrain.EnableQRCode{run}`, decodes with `wechat_qrcode` + ZBar, publishes `embodied.perception.vision.QRPB{qrcode}` |

The runtime reader is what surfaces to a **remote brain** as the `eb-qr-event` vision event, whose
scanned string rides `input_vars['$eb_qr_value']` ([`vision.md`](../vision.md):73-74;
[`presence.py`](../../../mqtt/moxie_sdk/presence.py):18, :65, :75). Subscribed vision events are not a
topic of their own — they arrive as the **`speech` of an ordinary `RemoteChatRequest`**
([`moxie_runtime.py`](../../../mqtt/supervisor/moxie_runtime.py):2612-2632, :2908-2909).

**We already extract the value and then drop it on the floor.** `presence.value_of` folds it into the
per-robot presence record; `sim/tests/test_presence_runtime.py`:125-128 drives a QR event carrying the
literal `GO<launch:DM>` and asserts only that the *string* is remembered. `_on_vision_turn` then answers
with a greeting or `ResultCode.NOREPLY_ACK` and **never** carries an action.

## 1. The seam — exactly what is missing, in three pieces

```
      card                robot                          our cloud
  ┌──────────┐      ┌──────────────────┐        ┌────────────────────────────┐
  │GO<launch:│─────▶│ QR reader        │───────▶│ _on_vision_turn(eb-qr-event)│
  │   DM>    │ scan │ (armed? — P0-a)  │ speech │   $eb_qr_value  ── P0-b ──▶ │
  └──────────┘      │                  │◀───────│ RemoteChatAction{launch,DM} │
       ▲            └──────────────────┘ launch └────────────────────────────┘
       │  P0-c
   the sheet
```

### P0-a — the arm (`eb_enable_qr` onto the wire) · **S** — 🟡 **half done (2026-09-04)**

> **The wire half of this piece is built and shipped.**
> [`wire.py`](../../../mqtt/moxie_sdk/wire.py) grew `encode_action`, which emits the recovered shape
> below: `function_id` (field 7) plus, **by the argument's type**, `function_args` (field 8, `repeated
> string`) for a list or `action_args` (field 10, `repeated ActionArgsEntry{key, value}`) for a dict.
> Both are omitted when empty, so no existing response changed. `sim/virtual_moxie.py` decodes all four
> spellings and records the name it was asked to run — it still calls nothing and sends no
> `execute_returns[]`. The test written to pin the defect
> (`sim/tests/test_actions_reach_the_robot.py`) was flipped into the assertion of the fixed behaviour.
>
> **Three things this piece still owed**, and none of them is the wire. **Two are still open:**
> 1. **`ENABLE_QR` still serialises as the string `enable_qr`** and is *not* routed through
>    `execute` + `function_id: "eb_enable_qr"`. The rename below is untouched and is now **pinned by a
>    test named for it**, so making it will turn a test red and have to say why. `EXIT` likewise (§7 R3).
> 2. ~~**`volley.execution_actions` is still not plumbed**~~ — **closed later the same day by PR #121**,
>    and the arithmetic is worth keeping because it was wrong twice. Measured immediately after the wire
>    landed, **all four** `xfail(strict)` rows still xfailed, refused at load with *"needs something this
>    appliance cannot grant yet: `act.eb_timer_request`"* — so *"this slice flips four rows green"* was a
>    claim and the wire was **necessary, not sufficient**: the gate was
>    [`ext.py`](../../../mqtt/moxie_sdk/content/ext.py)'s `_is_p1` / `P1_CAPABILITIES` plus
>    `content_app._reply_from_volley`.
> 3. ~~**The browser SIM still cannot read it.**~~ — **fixed 2026-09-04.**
>    [`bridge.js::applyAction`](../../../sim/web/bridge.js) now reads `function_id` before the SIM's
>    older `function`, and decodes `function_args` (proto field 8, a list) and `action_args` (field 10,
>    a `{key, value}` list) into the same `args` the SIL robot records — falling through on
>    **absence**, not falsiness, so a `function_args` a server really sent is never silently replaced
>    by the `action_args` beneath it. It keeps the reference client's discipline exactly: it records,
>    and it does not pretend. Nothing is called, no module starts, and no `execute_returns[]` is
>    published. **A second drop site turned up while fixing it** and is the finding worth keeping —
>    `moxieBridge.actionStats()` re-projected each applied action onto four keys, so a correct
>    `applyAction` would still have handed every caller an `execute` with no arguments. The writer's
>    half of a contract is not the whole contract. Held from both ends by
>    [`sim/test_action_payload.mjs`](../../../sim/test_action_payload.mjs) — the real bridge driven
>    over the same golden script the SIL robot is driven over, with a negative control that reverts
>    the fix and must go red — and by §5 of
>    [`test_sim_client_parity.py`](../../../sim/tests/test_sim_client_parity.py).
>    `content_app._reply_from_volley`. PR #121 then built the remainder — `content_app
>    .execution_actions_of` turns an effect into an `execute` `Action` — and measured **two of four**, not
>    four: G2/G3 pass in `test_t1_t6_conformance_act`, while **G5 needs `brain`** and **G6 needs
>    `subscribe`**, whose effect still has no host (nothing joins `Volley.subscriptions` to
>    `wire.build_chat_response(subscribe_events=…)`). *Two* predictions, *two* measurements, and neither
>    prediction survived — which is why this brief now states measured counts and not expected ones.
> 3. **The browser SIM still cannot read it.** [`bridge.js`](../../../sim/web/bridge.js):258 reads
>    `entry.function` only — not `function_id`, and no args at all — so an armed `execute` renders as
>    `(unnamed)` there while the SIL robot names it. Two clients that disagree is exactly what DoD
>    criterion 4 forbids. ⚠️ **Being fixed in `feat/client-parity` as of 2026-09-04 — do not take this
>    piece.** The rest of P0-a (the `ENABLE_QR` spelling) and P0-c are unclaimed; **P0-b shipped
>    2026-09-04.**

The runtime reader **is not always scanning**: the brain turns it on for a moment of content
([`qr-commands.md`](../../reverse-engineering/protocol/qr-commands.md):309-311, and
[`mqtt-and-conversation.md`](../mqtt-and-conversation.md):1135-1136 — *"Robot scans it during a
MOXIE_GO/QR-enabled module (`eb_enable_qr` + `eb-qr-event` subscription)"*). Today we cannot ask:

* [`volley.py`](../../../mqtt/moxie_sdk/content/volley.py):96-98 **captures** `add_execution_action`
  and nothing ever reads `Volley.execution_actions` onto the wire — the audit's §3.2 *"Execution
  actions … not plumbed onto the wire"* row (ADOPT, **S**), and the blocker
  [`sandboxed-extensions.md`](sandboxed-extensions.md) calls **S5** (its `act.*` capabilities are
  *refused at load* because of it — [`ext.py`](../../../mqtt/moxie_sdk/content/ext.py):177-181).
* ~~[`wire.py::build_chat_response`](../../../mqtt/moxie_sdk/wire.py) builds each action as
  `{output_type, action, module_id, content_id}` and **silently drops** `Action.function` /
  `Action.args`~~ — **fixed 2026-09-04**; see the box above.

**The recovered shape is already pinned, so this is transcription, not research.**
[`RemoteChat.proto`](../../reverse-engineering/protocol/recovered-proto/embodied/robotbrain/RemoteChat.proto):255-281
gives `ActionID.execute = 6` with `function_id` (field 7) and `repeated function_args` (field 8);
[`remote-chat-protocol.md`](../../reverse-engineering/protocol/remote-chat-protocol.md):99 reads it back
as *"`execute` — run a robot-side `function_id(function_args…)`"*. So:

```json
{"output_type": "GLOBAL", "action": "execute",
 "function_id": "eb_enable_qr", "function_args": ["true"]}
```

**A defect this piece must fix, not inherit.** `ActionType.ENABLE_QR = "enable_qr"`
([`types.py`](../../../mqtt/moxie_sdk/types.py):61) is **not a name in the recovered `ActionID` enum**
(`launch`, `launch_if_confirmed`, `exit_module`, `request_next`, `abort_module`, `execute`, `sleep`,
`tangent` — [`proto-catalog.md`](../../reverse-engineering/protocol/proto-catalog.md):2091). Emitting it
would put a verb on the wire the robot's own proto cannot name. `ENABLE_QR` must serialise **as an
`execute` with `function_id: "eb_enable_qr"`**, or be deleted in favour of `EXECUTE`. Do not invent a
wire value the enum does not define — the same rule `actions.py` already applies to
`launch_if_confirmed` ([`actions.py`](../../../mqtt/moxie_sdk/actions.py):44-56).

> ⚠️ `ActionType.EXIT = "exit"` has the same smell (the enum's name is `exit_module`) and the browser
> SIM agrees with us rather than with the proto ([`bridge.js`](../../../sim/web/bridge.js):206). **Do
> not change it in this slice** — it is a shipped, field-untested spelling on a different verb, and
> changing it would put an unrelated regression inside a delight feature. Record it; see §7 R3.

### P0-b — the route (`$eb_qr_value` → a launch action) · **S/M** — ✅ **shipped 2026-09-04**

> **What landed.** A pure decoder, [`launch_cards.py`](../../../mqtt/moxie_sdk/launch_cards.py), and one
> call-site change in [`_on_vision_turn`](../../../mqtt/supervisor/moxie_runtime.py). A scanned
> `GO<launch:DM>` now answers that turn's own `event_id` with `SUCCESS` and exactly one
> `RemoteChatAction{action: "launch", module_id: "DM"}`; anything else answers `NOREPLY_ACK` and carries
> no action. `_publish_chat` already took `actions=`, so no plumbing was added.
>
> **The catalog is derived, and it is 24.** `_catalog()` reads
> [`schedule.py`](../../../mqtt/moxie_sdk/schedule.py) through the *module* (not `from … import`), so it
> is a live function of that file: 23 ids out of `ONBOARD_MODULES`, plus `DM` — and `DM` is
> **intersected** with what `DEFAULT_TEMPLATE.provided_schedule` actually names rather than trusted. If a
> future edit drops or renames it the allowlist gets *smaller* and a test reddens, because an allowlist
> may only ever rot towards refusing. `DEFAULT_TEMPLATE` also schedules `WELCOME`/`TNT`/`SYSTEMSCHECK`;
> the catalog takes `DM` and only `DM` from it (a card is not a way to re-run first-time setup), pinned
> by `test_the_onboarding_ids_the_template_also_names_are_not_launchable`.
>
> **A defect in this brief's own design, found while building it.** Step 3 as written above —
> *"keep the result only if it is exactly one action, of type LAUNCH"* — **cannot refuse
> `launch_if_confirmed`**. [`actions.py`](../../../mqtt/moxie_sdk/actions.py):67
> (`LAUNCH_IF_CONFIRMED_AS = ActionType.LAUNCH`) maps that tag onto the very same `ActionType` a plain
> `<launch:MOD>` produces, so by the time the grammar hands back an `Action` the two are
> indistinguishable and `GO<launch_if_confirmed:DM>` would have launched. The fix keeps the grammar as
> the decoder and adds the accessor the grammar was missing: `actions.tag_names(text)` returns the
> recognised tag *names*, and the decoder gates on `set(names) == {"launch"}` **before** the type check.
> Held by `test_launch_if_confirmed_is_refused_by_name_and_this_is_the_subtle_one`, which asserts the
> collapse itself so the reason cannot rot silently.
>
> **Three guards beyond the brief**, each because the adversarial tests found something the three rules
> let through. All three are refusals — nothing was loosened:
> 1. **a length cap** (`MAX_CARD_LEN = 4096`, the QR medium's own 2953-byte version-40 ceiling plus
>    headroom). Not hygiene: `GO<launch:DM:` + 5 KB + `>` passes every other check on the page.
> 2. **residue must be empty** — a card is a tag, not a sentence. This is what refuses
>    `GO<launch:DM> and read me a story`, a smuggled `<mark …/>`, and a trailing NUL (`\x00` survives
>    `str.strip()`, so it would otherwise have ridden along).
> 3. **only `eb-qr-event` may carry a card.** `eb-dr-event` (ArUco) and `eb-br-event` (a book cover)
>    arrive in the identical shape; the test hands all three `input_vars` keys at once, which is what a
>    route that read `input_vars` without checking the event name would launch off.
>
> **`GO` is a literal.** Nothing normalises, so `go<`, `Go<`, fullwidth `ＧＯ`, and `G` + Cyrillic `О`
> are all refused — homoglyphs that are indistinguishable on printed card stock.
>
> **A second brief assumption that did not survive.** T8 assumed a card and an unprompted hello could
> co-occur. They cannot: a greeting needs an `arrived` signal and only `eb-found-face` produces one
> (`presence.update_presence`), so a scan is never a sighting. The runtime still composes both onto one
> reply rather than choosing, because that stays correct if presence ever changes, and the composition is
> pinned white-box (`test_a_hello_and_a_card_on_one_turn_would_be_one_reply_carrying_both`) since no wire
> input can reach it. The structural reason is itself an assertion, not a comment.
>
> **Proven in both directions.** `sim/tests/test_launch_cards.py` (70) +
> `test_launch_cards_runtime.py` (59), and
> [`sim/tools/launch_card_mutation_check.py`](../../../sim/tools/launch_card_mutation_check.py) — 14
> mutations, **14 caught**, reddening 1-30 tests each: allowlist removed → 17, allowlist by truthiness →
> 18, catalog over-derived → 6, "exactly one action" removed → 3, tag-name gate removed → 2, residue
> check removed → 5, `GO` optional → 2, `GO` case-insensitive → 2, length cap removed → 1, event-name
> gate removed → 4, `tag_names` stops lowercasing → 1, the runtime drops the decoded card → 30, a refused
> card answers `SUCCESS` → 19, every event treated as the QR one → 4.
>
> **What it still does not prove, and cannot.** *Nothing here makes a physical robot scan anything.* No
> Moxie has ever sent us an `eb-qr-event`; every test drives the value in by hand through the real
> runtime over a fake transport. Whether `eb_enable_qr` arms the runtime reader (§7 Q1), when it should
> be armed (Q2), and whether the robot accepts our JSON spelling of `ActionID` (Q3) are all exactly as
> open as they were before this shipped. The SIL round trip (T10) and the browser SIM leg were **not**
> built — they need the `sim/virtual_moxie.py` harness changes in §4, which stayed out of this slice.


`_on_vision_turn` ([`moxie_runtime.py`](../../../mqtt/supervisor/moxie_runtime.py):2669-2690) is the
only place a QR value is ever in scope with a reply being built, and `_publish_chat` **already** takes
`actions=` ([`moxie_runtime.py`](../../../mqtt/supervisor/moxie_runtime.py):4367-4396). So this is a
pure decoder plus one call-site change. It is **not** reachable from a content module today: a vision
event is intercepted before any brain sees it (`:2908-2909` — *"never handed to a brain, never written
to history"*), and `MoxieApp.on_event` returns `None` and cannot shape a reply
([`app.py`](../../../mqtt/moxie_sdk/app.py):55-57). The route belongs in the runtime.

**The decoder is pure and belongs in its own module** (`mqtt/moxie_sdk/launch_cards.py`), in the idiom
this codebase already uses for closed vocabularies (`content/ext.py::OPS`, `brains.BRAINS`,
`vocab.py`):

```
decode(value) -> Action | None
  1. strip whitespace; require the literal `GO` prefix (upstream's marker, and the only thing that
     distinguishes our card from a cereal box the child happens to wave at Moxie)
  2. hand the remainder to actions.parse_action_tags — the existing grammar, reused as the DECODER
  3. keep the result only if it is exactly one action, of type LAUNCH, whose module_id is in the
     closed catalog; otherwise None
```

**As built, that is rules 1-3 of six** (see the box above). Rule 3 alone cannot refuse
`launch_if_confirmed`, so a gate on the tag *name* precedes it; and the adversarial tests bought three
more refusals — a length cap, an empty-residue requirement, and "only `eb-qr-event` carries a card".

**Reusing the tag grammar is the one true thing in the old row** — it is the right decoder and it is
already tested (`sim/tests/test_action_tags.py`). What it was never wired to is an inbound value.

**The catalog is a closed allowlist, and this is a safety property, not tidiness.** A QR code is an
input any stranger can print and leave on a table in front of a child. The permitted module ids are the
23 in [`schedule.py::ONBOARD_MODULES`](../../../mqtt/moxie_sdk/schedule.py):124-148 plus `DM`
(carried separately in `DEFAULT_TEMPLATE`, per `:118-123`). `<sleep>`, `<exit>` and
`<launch_if_confirmed:…>` on a card are **refused** even though the grammar parses them: a card may
start an activity and may do nothing else.

### P0-c — the sheet · **S**

A printable page of cards, one per module, each with its QR and its friendly label from
[`schedule.py::MODULE_LABELS`](../../../mqtt/moxie_sdk/schedule.py):223-233. The rendering problem is
already solved twice in this repo and neither needs a new dependency:

* server-side PNG — `segno`, exactly as
  [`main.py`](../../../server/moxie_server/main.py):473-479 and `:505-513` render the endpoint and
  Wi-Fi codes;
* browser-side canvas — [`sim/web/qr.js`](../../../sim/web/qr.js) `render(canvas, text, scale)`, which
  already drives the simulator's *Revive a robot* panel with no install at all.

**Where it lives:** the parent console (`server/static/index.html`) as a 🎴 card, beside 🎨 Moxie's look
and 📦 Content — the same surface every other parent-facing feature chose, and the one that already
proxies the supervisor. A `?print=1` view is a plain print stylesheet; do not build a PDF pipeline.

## 2. The vocabularies (nothing outside them reaches the wire or the paper)

| Vocabulary | Source, cited | Size |
|---|---|---|
| Module ids a card may launch | [`schedule.py`](../../../mqtt/moxie_sdk/schedule.py):124-148 (`ONBOARD_MODULES`, transcribed from [`mqtt-and-conversation.md`](../mqtt-and-conversation.md):1123-1129) + `DM` | 24 |
| Friendly labels | [`schedule.py`](../../../mqtt/moxie_sdk/schedule.py):223-233 (`MODULE_LABELS`) | 24 |
| Card payload grammar | `GO` + `<launch:MODULE[:CONTENT]>` — [`mqtt-and-conversation.md`](../mqtt-and-conversation.md):1133-1137, upstream `site/data/qr/extract.py` | 1 form |
| Action wire shape | [`RemoteChat.proto`](../../reverse-engineering/protocol/recovered-proto/embodied/robotbrain/RemoteChat.proto):255-281 (`ActionID`, `function_id`, `function_args`) | 9 verbs, 2 used |
| Event name + payload key | `eb-qr-event` / `$eb_qr_value` — [`vision.md`](../vision.md):73-74, [`presence.py`](../../../mqtt/moxie_sdk/presence.py):65-78 | 1 |

`content_id` is accepted by the grammar (`GO<launch:DM:mission_3>`) but **no recovered content id is
catalogued for any on-board module**, so the sheet generator must not invent one. A hand-written card
carrying one is passed through; nothing we print carries one.

## 3. Prior art — OpenMoxie (MIT), described and credited, never copied

| Theirs | What we take | What we do differently |
|---|---|---|
| `site/data/qr/extract.py` → 24 `launch_*.png` | the **idea** and the payload form `GO<launch:MOD>` | we render from our own cited catalog, on demand, with a print view — not 24 committed PNGs |
| `MoxieGo` content module: arm the scanner, read `$eb_qr_value`, `startswith("GO")`, slice the payload into the reply so the module's own tag-ingest turns it into an action (described in [`sandboxed-extensions.md`](sandboxed-extensions.md):193, :766) | the **three-rule shape**: arm → read → re-arm | the slice-into-the-reply trick is a *content-module* accident of their architecture. Ours decodes to a typed `Action` in the runtime and never round-trips a scanned string through text a child could hear |

Their `MoxieGo` hook is also the **G6 golden** the extensions conformance file already carries
(`sim/tests/data/ext_conformance.json`:1081-1121) — currently `xfail(strict)` for want of the same
`act`/`subscribe` wire this brief's P0-a lands. **Shipping P0-a turned two of those four rows green**
(measured 2026-09-04; `subscribe` and `brain` still gate the other two), which is why
it is worth doing here rather than deferring it again.

Attribution goes in [`ATTRIBUTION.md`](../../../ATTRIBUTION.md) with the rest.

## 4. Tests

Hermetic first; nothing below needs a broker, a network or a sleep.

| # | Property | Where |
|:--:|---|---|
| T1 | `GO<launch:DM>` decodes to exactly one `LAUNCH` action for `DM`; `GO<launch:DRAW:x>` carries the content id | ✅ `test_launch_cards.py` |
| T2 | Every one of the 24 catalog ids round-trips: payload → decode → the same id | ✅ `test_launch_cards.py` (parametrised ×24, both there and through the runtime). The label half moved to P0-c with the sheet; `encode()` lives beside `decode()` so the printing side can never emit a payload the reading side refuses |
| T3 | A module id **outside** the catalog decodes to `None` — the allowlist, not a regex | ✅ `test_launch_cards.py`; the test first asserts the grammar *accepts* `<launch:NOPE>`, so the refusal is provably the allowlist. Mutation M1/M2 redden 17/18 tests |
| T4 | `GO<sleep>`, `GO<exit>`, `GO<launch_if_confirmed:DM>`, `GO<launch:A:B:C>`, two tags in one card → `None` each | ✅ `test_launch_cards.py`, one test **per refusal, named for it**. `launch_if_confirmed` needed a design change — see the box |
| T5 | A card with no `GO` prefix, an empty value, 4 KB of junk, and a plain English sentence → `None`, no exception | ✅ `test_launch_cards.py`, **widened**: a megabyte, 200 repeated tags, 500-deep brackets, embedded newlines and NULs, five unicode look-alikes for `GO`, seven one-character-off near-misses, and non-strings (`None`, `int`, `bytes`, `range`) |
| T6 | A QR turn carrying a valid card publishes `result=SUCCESS` with **one** launch action on the robot's own `event_id`, and writes **nothing** to conversation history (the invariant `test_presence_runtime.py`:110-113 pins for face events) | ✅ `test_launch_cards_runtime.py`, plus: no brain call, no TTS, no spoken line at all, and the value still reaches the presence record |
| T7 | A QR turn carrying an unrecognised value still answers `NOREPLY_ACK` — an unknown card is silence, never a stall | ✅ `test_launch_cards_runtime.py`, parametrised over nine non-cards |
| T8 | A card scanned during an absence does not also fire the unprompted greeting twice (`_greeting_for` and the launch are independent) | ⚠️ **the premise was wrong** — a scan is never a sighting, so the two cannot co-occur at all (`presence.update_presence` emits `arrived` only for `eb-found-face`). Asserted as such, and the would-be composition pinned white-box | 
| T9 | `Action(EXECUTE, function="eb_enable_qr", args=["true"])` serialises to `{"action":"execute","function_id":"eb_enable_qr","function_args":["true"]}` — ✅ **done 2026-09-04**, asserted key for key in `test_actions_reach_the_robot.py::test_the_briefs_own_worked_example_is_the_shape_that_goes_out`; `ENABLE_QR` serialising to the **same** shape and never to the string `enable_qr` is ❌ **still owed**, and the current (wrong) spelling is pinned by `…::test_the_naming_defects_p0a_still_owns_are_pinned_here_not_fixed` | `test_actions_reach_the_robot.py` (wire half, shipped) · `test_launch_cards.py` (the `ENABLE_QR` half) |
| T10 | SIL round trip: the real `MoxieRuntime` + the real `sim/virtual_moxie.py` over `helpers_runtime.loopback()` — the robot publishes an `eb-qr-event` carrying `GO<launch:DM>` and ends up **holding** the launch action in the recovered shape | ✅ **proven between our runtime and our simulated client** 2026-09-04 — `test_launch_cards_sil.py`, 45 tests. The robot's own `action_stats()` ends at `{"action": "launch", "module_id": "DM", "content_id": "", "function": "", "args": []}`, written by `virtual_moxie._apply_action` off a payload that arrived on `/devices/<id>/commands/remote_chat`. **The refusals travel the same wire**: `launch_if_confirmed`, `sleep`, `exit`, an id outside the catalog, a lowercased marker, an over-long value and two smuggling shapes each leave `applied == []` *and* answer `NOREPLY_ACK` on the scan's own `event_id`, so a refusal is silence rather than a stall. **Also run once over a real broker** (mosquitto in docker + `mqtt/run.py` as its own process), because the hermetic loopback is still one process: `sim/virtual_moxie.py --face-event eb-qr-event --face-value 'GO<launch:DM>'` printed `✅ eb-qr-event: SUCCESS (silent) 🎬 launch DM` and the supervisor logged `🎴 … scanned a launch card -> DM`, while `launch_if_confirmed`, `sleep` and `NOPE` each printed `NOREPLY_ACK` with no 🎬. That run is a **manual reproduction, not a CI gate** — `sim/run_smoke.sh` has no card mode. Not proven: any of it on hardware |
| T11 | The sheet route returns one card per catalog id, each payload decodes back to its own id, and the page contains no id outside the catalog | `test_launch_cards.py` |
| T12 | Browser↔Python byte parity for the card payload string, the way `sim/test_qr.mjs` already asserts it for the seven revival payloads | `sim/test_qr.mjs` |
| T13 | Mutation run: every guard deleted one at a time turns a test red | ✅ `sim/tools/launch_card_mutation_check.py` — **19 rows, 19 caught**, 1-61 tests red each. M15-M19 mutate the **client** (`sim/virtual_moxie.py`), because M1-M14 all mutate the server and a SIL test could survive every one of them while still reading the runtime's own publish record. 18 of the 19 redden `test_launch_cards_sil.py` *on its own*; the exception is **M3**, and honestly so — M3 widens the derived catalog and the SIL suite is parametrised *over* that catalog, so the derivation is a property only the unit suite can see (6 red there). Anchors held honest by `test_mutation_tables.py` |

**Two harness changes the tests need** (small, and both are the missing half of something already
built) — **one of the two turned out to be already built**, see the correction under them:

1. ✅ **done 2026-09-04.** `sim/virtual_moxie.py` — `send_face_event(kind, input_vars)` already accepted
   a payload but `run_face_events` never passed one and the CLI had no flag. `--face-value` now carries
   the marker payload for all three value-bearing events, routed to the right `input_vars` key by
   `VirtualMoxie.EVENT_VALUE_KEYS` / `value_vars` — a deliberate second copy of `presence.VALUE_KEYS`,
   because the robot half must not import the server SDK. `run_face_events` also records the actions
   applied during each event's turn, so `--face-event eb-qr-event --face-value 'GO<launch:DM>'` prints
   `✅ eb-qr-event: SUCCESS (silent) 🎬 launch DM`.
2. ⚠️ **this item was already true when it was written.** It says the file "ignores `response_actions`
   **entirely** (zero references)". It has not since **`470ffd9` (PR #116, 2026-09-04)**, which added
   `_on_actions` / `_apply_action` / `action_stats()` and the `self.actions` record — the file carries
   ~30 references today, and `test_actions_reach_the_robot.py` already asserts against them. The same
   item's closing instruction — *fix `test_e2e_actions_to_robot.py`'s stale "and so does the browser
   SIM's bridge" line* — was fixed in **that same commit**, and the docstring now carries a paragraph
   saying so. **The brief was drafted against a tree that PR #116 had not yet landed on.** Nothing was
   needed here for T10; the SIL round trip asserts against `action_stats()` exactly as this item asked,
   and what T10 *added* was five mutation rows (M15-M19) proving those recordings are load-bearing —
   without them a SIL test can read the runtime's publish record and look identical.

## 5. Acceptance criteria

1. A parent opens the console's 🎴 card, prints the sheet, and every card on it names a real activity in
   words a child recognises. No card is blank, no id is invented, no `content_id` is guessed.
2. A `GO<launch:MOD>` value arriving as `$eb_qr_value` on an `eb-qr-event` turn produces **exactly one**
   `RemoteChatAction{action: "launch", module_id: MOD}` on the reply to that turn's own `event_id`.
3. A value that is not a card produces **no action** and a `NOREPLY_ACK` — the robot is never left
   waiting, and a child never hears a decoding artefact.
4. A card naming anything outside the 24-id catalog is refused, and the refusal is a **positive-list**
   miss, provable by T3 rather than by reading a regex.
5. `eb_enable_qr` reaches the wire as an `execute` action with `function_id`/`function_args`, matching
   `RemoteChat.proto`:271-279 field for field — and no action our code can emit carries a verb absent
   from `ActionID`.
6. The four `xfail(strict)` `act`/`subscribe` rows in `sim/tests/data/ext_conformance.json` are flipped
   green, or the brief says in its own words why they were not.
7. Every claim in the audit row this slice closes names a **file and a test**, per the audit's own
   editing rule.

## 6. Effort and the files an agent touches

| Piece | Effort | Files |
|---|:--:|---|
| P0-a the arm | **S** | [`mqtt/moxie_sdk/wire.py`](../../../mqtt/moxie_sdk/wire.py) (`build_chat_response`, the action loop) · [`mqtt/moxie_sdk/types.py`](../../../mqtt/moxie_sdk/types.py) (`ActionType.ENABLE_QR`) |
| P0-b the route ✅ | **S/M** | **new** [`mqtt/moxie_sdk/launch_cards.py`](../../../mqtt/moxie_sdk/launch_cards.py) · [`mqtt/supervisor/moxie_runtime.py`](../../../mqtt/supervisor/moxie_runtime.py) (`_on_vision_turn` only) · [`mqtt/moxie_sdk/actions.py`](../../../mqtt/moxie_sdk/actions.py) (`tag_names`, additive — see the box) |
| P0-c the sheet | **S** | [`server/moxie_server/main.py`](../../../server/moxie_server/main.py) (two routes: the sheet, one PNG) · [`server/static/index.html`](../../../server/static/index.html) (the 🎴 card + a print stylesheet) · optionally [`sim/web/qr.js`](../../../sim/web/qr.js) for the install-free browser generator |
| Harness ✅ | — | [`sim/virtual_moxie.py`](../../../sim/virtual_moxie.py) — `--face-value` + `EVENT_VALUE_KEYS`/`value_vars` landed 2026-09-04; `response_actions` were already recorded (PR #116) |
| Tests | — | `sim/tests/test_launch_cards.py`, `test_launch_cards_runtime.py`, `test_launch_cards_sil.py`, `sim/tools/launch_card_mutation_check.py`, `sim/test_qr.mjs` |
| Docs | — | this page's state, [`../openmoxie-feature-audit.md`](../openmoxie-feature-audit.md) §4.4 #9 + §4.3, [`README.md`](README.md), [`../../../ATTRIBUTION.md`](../../../ATTRIBUTION.md) |

**Do not touch** `presence.py`'s state machine. The QR value is already carried correctly; this slice
reads it, it does not re-model it.

## 7. Risks, and the honest ceiling

**The ceiling first, because it does not move.** *No physical Moxie has ever sent us a vision event.*
That is the audit's own standing limit on BEYOND #9 and it is inherited here whole: the end-to-end claim
— *a child holds up a card and Moxie starts drawing* — is **unprovable on hardware whatever we build**,
by us, today.

| Provable, and must be | Not provable by us |
|---|---|
| the decoder's whole behaviour, including every refusal (T1–T5) | that a real robot ever fires `eb-qr-event` for a card we printed |
| that the runtime answers a QR turn with a correctly-shaped launch on the right `event_id` (T6–T8) | that the runtime reader decodes *our* PNG at *a child's* holding distance, in a lounge, at their lighting |
| that the **SIL robot** receives it and holds it — ✅ T10, 2026-09-04, refusals on the same wire | that `eb_enable_qr` as an `execute` action actually arms that reader |
| that the **browser SIM** receives it and visibly acts on it — ⬜ still open, T12's other half | that a SIL robot receiving a launch tells us anything about a robot with a camera |
| byte parity between our two generators (T12) | that the robot's JSON spelling of `ActionID` matches ours |

| # | Risk / assumption | Why it is open | What would settle it |
|:--:|---|---|---|
| Q1 | **The scanner must be armed and we have never armed one.** `EnableQRCode{run}` is a robot-internal proto; `eb_enable_qr` is the *module-API* name for the same idea. That they are the same lever is **inferred** from [`qr-commands.md`](../../reverse-engineering/protocol/qr-commands.md):309-311 + [`content-and-conversation.md`](../../reverse-engineering/runtime/content-and-conversation.md):377-390, not observed | no capture of a cloud arming a robot | one robot, one `execute` action, one scan |
| Q2 | **When to arm.** Upstream arms inside a MOXIE_GO activity. A general-purpose "cards work whenever Moxie is awake" needs the scanner on far more of the time — with a camera-on cost, a privacy story and a battery story none of our docs price | our corpus documents the mechanism, never a duty cycle | hardware, or a parent-facing decision to arm only inside a named activity |
| Q3 | **`ActionID` JSON spelling.** We emit enum *names* (`launch`); nothing in the corpus proves the robot's JSON decoder accepts names rather than ints, and two of our five `ActionType` values (`exit`, `enable_qr`) are not names in the enum at all | `test_e2e_actions_to_robot.py` proves delivery, never acceptance | a robot, or a captured genuine `remote_chat` from Embodied's cloud |
| Q4 | **A card is an unauthenticated input in a child's room.** The allowlist bounds the damage to *"a stranger's printed card can start one of 24 on-board activities"*. Whether even that is acceptable is a **parent-facing decision this brief does not make** — a console toggle (default off?) may be the honest answer | no parent has been asked | the same parent question §4.4 #6's A9–A11 are blocked on |
| Q5 | **Print fidelity.** Module count 24 at a legible card size is a layout question; error-correction level and quiet-zone width for a *held* code are not the same as for a *screen* code. `ec="l"` is our current default (`main.py`:474) and is chosen, not measured | never printed | print the sheet and scan it with any phone — a 10-minute experiment that needs no robot |
| R3 | `ActionType.EXIT = "exit"` vs the enum's `exit_module` (§P0-a's warning) | shipped and unrelated to cards | out of scope here; file it against the wire, not this feature |

**What this slice is worth despite the ceiling.** The three hops are each independently useful: P0-a is
an ADOPT row of its own and the blocker under the extensions brief's `act`/`subscribe`; P0-b is the
first time anything in our cloud *acts* on a perception event rather than merely noticing it; P0-c is a
thing a parent can hold. If the day ever comes that a robot is on our broker, this is one of the
cheapest things on the list to falsify — hold up a card.

---
📖 [Backlog index](README.md) · [OpenMoxie feature audit](../openmoxie-feature-audit.md) · [Vision](../vision.md) · [QR command grammar](../../reverse-engineering/protocol/qr-commands.md)
