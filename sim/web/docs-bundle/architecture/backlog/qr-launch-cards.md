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

### P0-a — the arm (`eb_enable_qr` onto the wire) · **S**

The runtime reader **is not always scanning**: the brain turns it on for a moment of content
([`qr-commands.md`](../../reverse-engineering/protocol/qr-commands.md):309-311, and
[`mqtt-and-conversation.md`](../mqtt-and-conversation.md):1135-1136 — *"Robot scans it during a
MOXIE_GO/QR-enabled module (`eb_enable_qr` + `eb-qr-event` subscription)"*). Today we cannot ask:

* [`volley.py`](../../../mqtt/moxie_sdk/content/volley.py):96-98 **captures** `add_execution_action`
  and nothing ever reads `Volley.execution_actions` onto the wire — the audit's §3.2 *"Execution
  actions … not plumbed onto the wire"* row (ADOPT, **S**), and the blocker
  [`sandboxed-extensions.md`](sandboxed-extensions.md) calls **S5** (its `act.*` capabilities are
  *refused at load* because of it — [`ext.py`](../../../mqtt/moxie_sdk/content/ext.py):177-181).
* [`wire.py::build_chat_response`](../../../mqtt/moxie_sdk/wire.py):82-85 builds each action as
  `{output_type, action, module_id, content_id}` and **silently drops** `Action.function` /
  `Action.args` ([`types.py`](../../../mqtt/moxie_sdk/types.py):66-71).

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

### P0-b — the route (`$eb_qr_value` → a launch action) · **S/M**

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
`act`/`subscribe` wire this brief's P0-a lands. **Shipping P0-a turns those rows green**, which is why
it is worth doing here rather than deferring it again.

Attribution goes in [`ATTRIBUTION.md`](../../../ATTRIBUTION.md) with the rest.

## 4. Tests

Hermetic first; nothing below needs a broker, a network or a sleep.

| # | Property | Where |
|:--:|---|---|
| T1 | `GO<launch:DM>` decodes to exactly one `LAUNCH` action for `DM`; `GO<launch:DRAW:x>` carries the content id | `sim/tests/test_launch_cards.py` |
| T2 | Every one of the 24 catalog ids round-trips: label → payload → decode → the same id | `test_launch_cards.py` |
| T3 | A module id **outside** the catalog decodes to `None` — the allowlist, not a regex | `test_launch_cards.py` |
| T4 | `GO<sleep>`, `GO<exit>`, `GO<launch_if_confirmed:DM>`, `GO<launch:A:B:C>`, two tags in one card → `None` each | `test_launch_cards.py` |
| T5 | A card with no `GO` prefix, an empty value, 4 KB of junk, and a plain English sentence → `None`, no exception | `test_launch_cards.py` |
| T6 | A QR turn carrying a valid card publishes `result=SUCCESS` with **one** launch action on the robot's own `event_id`, and writes **nothing** to conversation history (the invariant `test_presence_runtime.py`:110-113 pins for face events) | `test_launch_cards_runtime.py` |
| T7 | A QR turn carrying an unrecognised value still answers `NOREPLY_ACK` — an unknown card is silence, never a stall | `test_launch_cards_runtime.py` |
| T8 | A card scanned during an absence does not also fire the unprompted greeting twice (`_greeting_for` and the launch are independent) | `test_launch_cards_runtime.py` |
| T9 | `Action(EXECUTE, function="eb_enable_qr", args=["true"])` serialises to `{"action":"execute","function_id":"eb_enable_qr","function_args":["true"]}`; `ENABLE_QR` serialises to the **same** shape and never to the string `enable_qr` | `test_launch_cards.py` (wire half) |
| T10 | SIL round trip: the real `MoxieRuntime` + the real `sim/virtual_moxie.py` over `helpers_runtime.loopback()` — the robot publishes an `eb-qr-event` carrying `GO<launch:DM>` and ends up **holding** the launch action in the recovered shape | `test_launch_cards_sil.py`, in the idiom of `test_e2e_actions_to_robot.py` |
| T11 | The sheet route returns one card per catalog id, each payload decodes back to its own id, and the page contains no id outside the catalog | `test_launch_cards.py` |
| T12 | Browser↔Python byte parity for the card payload string, the way `sim/test_qr.mjs` already asserts it for the seven revival payloads | `sim/test_qr.mjs` |
| T13 | Mutation run: every guard deleted one at a time turns exactly one test red | `sim/tools/launch_card_mutation_check.py` |

**Two harness changes the tests need** (small, and both are the missing half of something already
built):

1. `sim/virtual_moxie.py` — `send_face_event(kind, input_vars)` already accepts a payload (`:282-292`)
   but `run_face_events` never passes one (`:294-315`) and the CLI has no flag. Add `--face-value`.
2. `sim/virtual_moxie.py` ignores `response_actions` **entirely** (zero references), so today the SIL
   robot cannot show it *received* a launch — only `test_e2e_actions_to_robot.py`'s reader can. Record
   the actions on the robot object so T10 can assert against the client's own state. The browser SIM
   already does this (`bridge.js::applyAction`, `:217-265`, including `enable_qr` and `execute`), which
   means **`test_e2e_actions_to_robot.py`'s docstring is stale** where it says *"and so does the browser
   SIM's bridge"* — fix that line while you are there.

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
| P0-b the route | **S/M** | **new** `mqtt/moxie_sdk/launch_cards.py` · [`mqtt/supervisor/moxie_runtime.py`](../../../mqtt/supervisor/moxie_runtime.py) (`_on_vision_turn` only) |
| P0-c the sheet | **S** | [`server/moxie_server/main.py`](../../../server/moxie_server/main.py) (two routes: the sheet, one PNG) · [`server/static/index.html`](../../../server/static/index.html) (the 🎴 card + a print stylesheet) · optionally [`sim/web/qr.js`](../../../sim/web/qr.js) for the install-free browser generator |
| Harness | — | [`sim/virtual_moxie.py`](../../../sim/virtual_moxie.py) (`--face-value`, record `response_actions`) |
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
| that the SIL robot and the browser SIM receive it (T10) and the browser SIM visibly acts on it | that `eb_enable_qr` as an `execute` action actually arms that reader |
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
