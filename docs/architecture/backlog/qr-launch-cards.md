# Printable launch cards — a QR a child shows Moxie, and an activity starts

**Status:** partial — decoder and route shipped ([`mqtt/moxie_sdk/launch_cards.py`](../../../mqtt/moxie_sdk/launch_cards.py),
called from [`mqtt/supervisor/moxie_runtime/presence.py`](../../../mqtt/supervisor/moxie_runtime/presence.py)),
printable sheet shipped ([`mqtt/moxie_sdk/launch_sheet.py`](../../../mqtt/moxie_sdk/launch_sheet.py)),
browser encoder shipped ([`sim/web/qr.js`](../../../sim/web/qr.js)); tested by `sim/tests/test_launch_cards*.py`,
`sim/tests/test_launch_sheet.py`, `sim/test_qr.mjs`. Open: the `ENABLE_QR` wire spelling (P0-a), a console
or browser UI that makes a card, and anything on hardware.

A parent prints a sheet; a child holds a card up to Moxie's face; the robot starts that activity.
OpenMoxie (MIT) ships the paper idea — `site/data/qr/extract.py` renders 24 PNGs of `GO<launch:MODULE_ID>`
— which we credit and do not copy.

**The honest ceiling.** No physical Moxie has ever sent us an `eb-qr-event`, and nobody has printed a sheet
and scanned it with a robot. What is proven is the ink and the software path, not the optics.

## 0. Why the scope is what it is

### 0.1 The setup scanner cannot launch anything

The setup app's QR grammar is closed ([`qr-commands.md`](../../reverse-engineering/protocol/qr-commands.md)):
`QRData.ParseFromString` has three branches (`PA` pairing, `VN` VPN, else JSON `{wifi?, pair?, debug?}`),
`debug.command` matches exactly four literals, and anything else shows a diagnostic screen. A launch card
shown to that reader does nothing. No generator of ours may emit a launch card through the `debug` grammar.

### 0.2 The runtime scanner reaches us

The robot has two QR readers on the same camera:

| Reader | Owner | What it does |
|---|---|---|
| setup / config | `bo-wifi` | the closed grammar in §0.1 |
| runtime / content | `bo-android` + `libbo-analytics` | armed by `EnableQRCode{run}`; publishes `QRPB{qrcode}` |

The runtime reader surfaces to a remote brain as the **`eb-qr-event`** vision event, with the scanned
string in `input_vars['$eb_qr_value']` ([`vision.md`](../vision.md)). Vision events arrive as the `speech`
of an ordinary `RemoteChatRequest`, are intercepted by the runtime before any brain sees them, and are
never written to history.

## 1. The three pieces

```
      card                robot                          our cloud
  ┌──────────┐      ┌──────────────────┐        ┌─────────────────────────────┐
  │GO<launch:│─────▶│ QR reader        │───────▶│ _on_vision_turn(eb-qr-event) │
  │   DM>    │ scan │ (armed? — P0-a)  │ speech │   $eb_qr_value ── P0-b ──▶   │
  └──────────┘      │                  │◀───────│ RemoteChatAction{launch,DM}  │
       ▲            └──────────────────┘ launch └─────────────────────────────┘
       │  P0-c
   the sheet
```

### P0-a — the arm (`eb_enable_qr` on the wire) · partial

The runtime reader is not always scanning; content turns it on for a moment. The recovered shape
([`RemoteChat.proto`](../../reverse-engineering/protocol/recovered-proto/embodied/robotbrain/RemoteChat.proto)
lines 255–281) is `ActionID.execute = 6` with `function_id` (field 7) and `repeated function_args` (field 8):

```json
{"output_type": "GLOBAL", "action": "execute",
 "function_id": "eb_enable_qr", "function_args": ["true"]}
```

**Shipped.** [`wire.py`](../../../mqtt/moxie_sdk/wire.py) `encode_action` emits `function_id` plus, by the
argument's type, `function_args` (a list) or `action_args` (a dict of `{key, value}` entries, field 10);
both are omitted when empty. Content packs reach it through `ext_host.execution_actions_of`, bounded by
the closed `ext.ACTION_WORDS` table ([`ext/grammar.py`](../../../mqtt/moxie_sdk/content/ext/grammar.py)),
which currently names `eb_timer_request`, `eb_enable_qr` and `eb_wake`. Both clients decode it:
`sim/virtual_moxie.py` and the browser bridge's `applyAction` read `function_id` and both arg spellings,
record the action, and run nothing.

**Still open — a wire-spelling defect, pinned not fixed.** `ActionType.ENABLE_QR` still serializes as the
string `enable_qr` ([`types.py`](../../../mqtt/moxie_sdk/types.py)), which is not a name in the recovered
`ActionID` enum (`launch`, `launch_if_confirmed`, `exit_module`, `request_next`, `abort_module`, `execute`,
`sleep`, `tangent`). It should serialize as the `execute` above, or be deleted in favour of `EXECUTE`.
`test_the_naming_defects_p0a_still_owns_are_pinned_here_not_fixed` in
[`test_actions_reach_the_robot.py`](../../../sim/tests/test_actions_reach_the_robot.py) pins today's
spelling so the fix must turn it red. `EXIT = "exit"` has the same problem (the enum says `exit_module`);
see §7 R3.

### P0-b — the route (`$eb_qr_value` → a launch action) · shipped

`launch_cards.decode_event(name, input_vars)` is called from `_on_vision_turn` in
[`presence.py`](../../../mqtt/supervisor/moxie_runtime/presence.py), the only place a QR value is in scope
while a reply is built. A valid card answers that turn's own `event_id` with `SUCCESS` and exactly one
`RemoteChatAction{action: "launch", module_id}`; anything else answers `NOREPLY_ACK` with no action — an
unknown card is silence, never a stall. No brain call, no TTS, no history; the value still reaches the
presence record.

**The decoder's rules** (all refusals return `None`, never raise):

1. Only `eb-qr-event` may carry a card. `eb-dr-event` (ArUco) and `eb-br-event` (book cover) arrive in the
   same shape and are ignored.
2. Length cap: `MAX_CARD_LEN = 4096` (QR's own ceiling is 2,953 bytes).
3. The literal prefix `GO`, case-sensitive and un-normalized — `go<`, fullwidth or Cyrillic look-alikes are
   refused.
4. The remainder is parsed by the existing tag grammar, `actions.parse_action_tags`, so a card and a brain
   agree on `<launch:DRAW>` by construction.
5. The recognized tag **names** must be exactly `{"launch"}` (`actions.tag_names`). This is needed because
   `launch_if_confirmed` collapses to the same `ActionType.LAUNCH`, so a type check alone would let
   `GO<launch_if_confirmed:DM>` launch.
6. Exactly one action, with an empty residue (a card is a tag, not a sentence — refuses trailing text, a
   smuggled `<mark/>`, a trailing NUL), and a `module_id` in the allowlist.

**The allowlist is a safety property.** A QR code is input any stranger can print and leave in front of a
child. `LAUNCHABLE_MODULE_IDS` (24) is **derived** from [`schedule/`](../../../mqtt/moxie_sdk/schedule/): the
23 `ONBOARD_MODULES` plus `DM`, intersected with what `DEFAULT_TEMPLATE` actually schedules. If a future edit
drops an id the list shrinks — it can only rot toward refusing. `WELCOME`/`TNT`/`SYSTEMSCHECK` are not
launchable (a card is not a way to re-run setup). `<sleep>`, `<exit>` and `<launch_if_confirmed:…>` are
refused even though they parse: a card may start an activity and nothing else. A `content_id`
(`GO<launch:DM:x>`) is accepted from a hand-written card; no recovered content id is catalogued, so nothing
we print carries one.

A card and a greeting cannot co-occur in practice (only `eb-found-face` produces the `arrived` signal a
greeting needs), but the runtime composes both onto one reply rather than choosing, and that composition is
pinned white-box.

### P0-c — the sheet · shipped

`python3 -m moxie_sdk.launch_sheet -o cards.html`, open, print. One self-contained HTML page: six cards per
sheet (2 × 3, 90 × 80 mm), four sheets, fits A4 and US Letter. Each card shows the friendly label from
`schedule`'s `MODULE_LABELS` (or the id itself where no plain-English name is recorded — we do not invent
product names) and the literal payload under the symbol. Every payload comes from `launch_cards.encode`, so
an out-of-catalog id raises instead of producing paper.

Decisions:

1. **Inline SVG sized in millimetres**, not PNGs. A raster QR is resampled by the printer and module edges
   blur; SVG is rasterized at the device's own resolution. "Save as PDF" gives a PDF.
2. **`segno` as the SDK's optional `cards` extra** ([`mqtt/pyproject.toml`](../../../mqtt/pyproject.toml)),
   imported lazily. Always `segno.make_qr`, never `segno.make` (which can return a Micro QR).
3. **Error level Q, one pinned version for the deck, 56 mm symbols, 4-module quiet zone** drawn inside the
   SVG (about 1.5 mm per module; tests enforce `MIN_MODULE_MM = 0.6`). A card lives in a child's hands;
   Q costs one symbol version at these payload sizes. These are generic print/scan guidance, not
   measurements of Moxie's camera.

**Not in the console.** The generator is a CLI. `server/` does not import `moxie_sdk` for this, and a
console route would be the first such crossing — a bigger decision than a print sheet. The install-free
alternative is a static page driven by `qr.js`'s already-pinned `encodeCard`.

**Browser encoder.** [`sim/web/qr.js`](../../../sim/web/qr.js) has `cardPayload` / `encodeCard` and a
transcribed `LAUNCHABLE_MODULE_IDS`; `encodeCard` throws on an id outside it. No page control calls it yet,
so a person cannot make a card in a browser.

## 2. Vocabularies

| Vocabulary | Source | Size |
|---|---|---|
| Launchable module ids | `schedule.ONBOARD_MODULES` + `DM` (derived in `launch_cards._catalog`) | 24 |
| Labels | `schedule.MODULE_LABELS` | per id, falling back to the id |
| Card payload | `GO` + `<launch:MODULE[:CONTENT]>` | 1 form |
| Action wire shape | `RemoteChat.proto` `ActionID`, `function_id`, `function_args` | 2 verbs used (`launch`, `execute`) |
| Event / key | `eb-qr-event` / `$eb_qr_value` | 1 |

## 3. Prior art — OpenMoxie

| Theirs | What we take | What we do differently |
|---|---|---|
| `site/data/qr/extract.py` → 24 `launch_*.png` | the idea and the `GO<launch:MOD>` form | render from our own derived catalog, on demand, as SVG |
| `MoxieGo` content module: arm the scanner, read `$eb_qr_value`, slice the payload into the reply text so tag-ingest turns it into an action | the arm → read → re-arm shape | decode to a typed `Action` in the runtime; a scanned string is never round-tripped through speech |

Attribution lives in [`ATTRIBUTION.md`](../../../ATTRIBUTION.md).

## 4. Tests

All hermetic unless noted.

| # | Property | Where |
|:--:|---|---|
| T1 | `GO<launch:DM>` → one launch for `DM`; a content id is carried | [`test_launch_cards.py`](../../../sim/tests/test_launch_cards.py) |
| T2 | all 24 catalog ids round-trip through `encode` → `decode` | same, and through the runtime |
| T3 | an id outside the catalog is refused by the allowlist (the grammar is shown to accept it first) | same |
| T4 | `sleep`, `exit`, `launch_if_confirmed`, over-long tags, two tags → refused, one named test each | same |
| T5 | no `GO`, empty, huge, deeply nested, NULs, look-alikes, non-strings → `None`, no exception | same |
| T6–T8 | a card answers `SUCCESS` + one launch on its own `event_id`, no history/brain/TTS; a non-card answers `NOREPLY_ACK`; the card+greeting composition | [`test_launch_cards_runtime.py`](../../../sim/tests/test_launch_cards_runtime.py) |
| T9 | `Action(EXECUTE, function="eb_enable_qr", args=["true"])` serializes to exactly the P0-a JSON; the `ENABLE_QR`/`EXIT` spellings are pinned as open defects | [`test_actions_reach_the_robot.py`](../../../sim/tests/test_actions_reach_the_robot.py), [`test_ext_act.py`](../../../sim/tests/test_ext_act.py) |
| T10 | SIL round trip: real runtime + `sim/virtual_moxie.py` over loopback; the robot publishes `eb-qr-event` and ends up holding the launch in `action_stats()`; refusals leave nothing applied | [`test_launch_cards_sil.py`](../../../sim/tests/test_launch_cards_sil.py) |
| T11 | the sheet: one card per id, each rendered module matrix read back like a scanner ([`helpers_qr_matrix.py`](../../../sim/tests/helpers_qr_matrix.py)) and decoded; out-of-catalog ids raise; no literal in the generator spells the card grammar | [`test_launch_sheet.py`](../../../sim/tests/test_launch_sheet.py) |
| T12 | browser↔Python parity: 25 payloads byte-for-byte against `launch_cards.encode`, decoded by the real `decode`; catalogs compared id for id; refusals cross the boundary | [`sim/test_qr.mjs`](../../../sim/test_qr.mjs) |
| T13 | mutation: every guard removed in turn (server and client) turns a test red | [`sim/tools/launch_card_mutation_check.py`](../../../sim/tools/launch_card_mutation_check.py) |
| T14 | real rasterizer (browser-backed): the page prints as 4 pages on A4 and Letter; all 24 symbols rasterized at 300 dpi decode back | `test_launch_sheet.py`, `sim/test_qr.mjs` |

Harness support: `sim/virtual_moxie.py --face-event eb-qr-event --face-value 'GO<launch:DM>'` sends a card
(the value is routed to the right `input_vars` key by `VirtualMoxie.EVENT_VALUE_KEYS`) and records the
applied actions via `action_stats()`. A real-broker run of that command is a manual reproduction; the stack
smoke has no card mode.

## 5. Acceptance

1. A `GO<launch:MOD>` value on an `eb-qr-event` turn produces exactly one launch action on that turn's own
   `event_id` — **met**.
2. A non-card produces no action and `NOREPLY_ACK` — **met**.
3. An id outside the 24-id catalog is refused by a positive list — **met** (T3).
4. `eb_enable_qr` reaches the wire as an `execute` with `function_id`/`function_args`, and no action we emit
   carries a verb absent from `ActionID` — **half met** (`execute` works; `ENABLE_QR`/`EXIT` spellings open).
5. A parent can print the sheet from the console — **not met** (CLI only).

## 6. Files

| Piece | Files |
|---|---|
| P0-a | [`wire.py`](../../../mqtt/moxie_sdk/wire.py), [`types.py`](../../../mqtt/moxie_sdk/types.py), [`content/ext_host.py`](../../../mqtt/moxie_sdk/content/ext_host.py), [`content/ext/grammar.py`](../../../mqtt/moxie_sdk/content/ext/grammar.py) |
| P0-b | [`launch_cards.py`](../../../mqtt/moxie_sdk/launch_cards.py), [`actions.py`](../../../mqtt/moxie_sdk/actions.py) (`tag_names`), [`moxie_runtime/presence.py`](../../../mqtt/supervisor/moxie_runtime/presence.py) |
| P0-c | [`launch_sheet.py`](../../../mqtt/moxie_sdk/launch_sheet.py), [`sim/web/qr.js`](../../../sim/web/qr.js) |
| Clients | [`sim/virtual_moxie.py`](../../../sim/virtual_moxie.py), [`sim/web/bridge/`](../../../sim/web/bridge/) |

`presence.py`'s state machine is untouched: the QR value was already carried correctly; the route reads it.

## 7. Risks and the honest ceiling

*No physical Moxie has ever sent us a vision event.* "A child holds up a card and Moxie starts drawing" is
unprovable on hardware by us today. What is proven: the decoder's behavior and every refusal, the runtime's
reply, the SIL robot receiving and holding the launch, the browser/Python payload parity, and the printed
symbols surviving a real rasterizer. What is not: that a real robot fires `eb-qr-event` for our card, reads
it at a child's distance and lighting, is armed by our `execute`, or accepts our JSON spelling of
`ActionID`. The browser SIM leg (a person holding a card up to the SIM) is also still open.

| # | Risk / assumption | What would settle it |
|:--:|---|---|
| Q1 | **Arming is inferred.** That `eb_enable_qr` (the module-API name) is the same lever as the robot-internal `EnableQRCode{run}` is inferred from [`qr-commands.md`](../../reverse-engineering/protocol/qr-commands.md) and [`content-and-conversation.md`](../../reverse-engineering/runtime/content-and-conversation.md), never observed | one robot, one `execute`, one scan |
| Q2 | **When to arm.** Upstream arms inside a MOXIE_GO activity. "Cards work whenever Moxie is awake" needs the camera scanning far more often, with privacy and battery costs no doc prices | hardware, or a parent decision to arm only inside a named activity |
| Q3 | **`ActionID` JSON spelling.** We emit enum names (`launch`); nothing proves the robot's decoder accepts names rather than ints | a robot, or a captured genuine `remote_chat` |
| Q4 | **A card is unauthenticated input in a child's room.** The allowlist bounds the damage to "a stranger's card can start one of 24 on-board activities". Whether that is acceptable (e.g. a console toggle, default off) is a parent-facing decision not made here | ask parents |
| Q5 | **Print fidelity.** Level, version and module size are chosen, not measured against a held card | print the sheet and scan it with any phone |
| R1 | **Closed allowlists are safety bounds.** Both the launchable-module list and the `function_id`s a content pack may emit (`ext.ACTION_WORDS`, `ext_host.robot_functions()`) are positive lists, re-checked where the wire is built | — (invariant; mutation-tested) |
| R3 | **Wire spellings `exit` and `enable_qr`** are not recovered `ActionID` names (`exit_module`; `execute` + `function_id`). Both our clients (`virtual_moxie.ACTION_KINDS`, `bridge/actions.js`) agree with our server rather than the proto, held equal by `test_sim_client_parity.py`. Renaming is a contract change for the wire, not this feature | a robot capture, then one coordinated rename |

---
📖 [Backlog index](README.md) · [OpenMoxie feature audit](../openmoxie-feature-audit.md) · [Vision](../vision.md) · [QR command grammar](../../reverse-engineering/protocol/qr-commands.md)
