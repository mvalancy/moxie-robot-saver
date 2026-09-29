# Brain picker — any brain, hot-swappable, per child

**Status:** P0 shipped — [`mqtt/moxie_sdk/brains.py`](../../../mqtt/moxie_sdk/brains.py),
[`mqtt/config.py`](../../../mqtt/config.py) (`BRAIN_BUILDERS`, `BrainEngines`, `BRAIN_ENV`),
[`mqtt/supervisor/moxie_runtime/brain.py`](../../../mqtt/supervisor/moxie_runtime/brain.py); tested by
`sim/tests/test_brains.py`, `test_brain_runtime.py`, `test_brain_console.py` and
[`sim/tools/brain_mutation_check.py`](../../../sim/tools/brain_mutation_check.py). P1 (persona binding,
per-child keys, cost accounting) is not started.

## What it does

[`ai-seam.md`](../ai-seam.md) (section ②, the brain) says Moxie's body is a shell any AI can wear. Before this, a brain was
chosen once, globally, by `MOXIE_APP` at import time. Now each robot on one appliance can be answered by
a different brain, changed from the console without a restart.

The console's 🧠 **Brain** card (beside 🎚️ Voice) shows the brains this appliance can run, each with a
blurb and the `MOXIE_*` variables it needs; an *applies to* choice — **this robot** or **every robot
(house rule)**; a button to clear a layer; and a row per robot such as *"Sam — Content modules
(content) — house rule"*. If the environment pinned the brain, that sentence comes first, because it is
why the dropdown is short.

## Design

1. **A closed positive list.** `brains.BRAINS` has exactly four entries — `llm`, `content`, `webhook`,
   `echo` — frozen as a literal in `test_brains.py`, so adding one needs a test edit. A name outside it is
   refused, never guessed; there is no deny-list to forget to extend.
2. **`brain` is an ordinary config key.** It rides the existing `defaults ⊕ fleet ⊕ per-robot` layering
   (`fleet/config.json` and the per-robot overrides), so `POST /config?scope=fleet` and
   `POST /config?device_id=` already set it and there is nothing new to back up.
   `cloud_config.SERVER_ONLY_KEYS` keeps it out of the config pushed to the robot, which has no field
   for it.
3. **Resolved once per turn.** `MoxieRuntime.app_for(device_id)` is called at the top of each turn and
   the app is carried through, so a Save lands on the child's **next** turn and a turn in flight
   finishes with the brain that heard the question. Apps are built on first use and cached by name; the
   lock covers the build only, never `respond()`.
4. **The operator's environment wins.** An explicit `MOXIE_APP` naming a brain **pins** it:
   `resolve_brain` returns it whatever the layers say, the card offers only that entry, and a stale
   page's cross-brain pick is refused with a sentence naming the variable. `MOXIE_APP=any` (or `auto`,
   or unset) pins nothing — "decide per child".

### Why the pin reads the raw environment

`config.MOXIE_APP` falls back to `llm` when the variable is unset. Pinning that resolved value would
lock every unconfigured box out of the picker. So the pin is computed from `config.BRAIN_ENV`, the raw
string, where `""` pins nothing. Unlike `MOXIE_TTS=tone` in the [voice picker](voice-picker.md), every
`MOXIE_APP` value is a real selection, so all four brain names pin.

## Interfaces

- `GET /brain` (status HTTP) — every brain this box can run (id, label, group, blurb, needed vars), the
  house rule, the pin and its note, and one row per robot with its brain and which layer decided
  (`default` / `fleet` / `robot` / `pin`, `brains.SOURCES`).
- `POST /brain?device_id=…` picks for one child; `POST /brain?scope=fleet` sets the house rule;
  `{"brain": null}` clears a layer. Both are validating front doors onto the existing
  `update_config` / `update_fleet_config`.
- Console: `server/moxie_server/routes/console.py` proxies `GET/POST /local/robots/{id}/brain`;
  `server/moxie_server/fleet/cards.py` (`normalize_brain`, `normalize_brain_option`,
  `normalize_brain_robot`) shapes the payload; the card is in `server/static/index.html` +
  `js/voice-brain.js`.

## Tests

| Property | Where |
|---|---|
| The table is exactly four brains | `test_brains.py` |
| Near-miss names (`gpt5`, `llm # the brain`, a dict) are refused; case and space normalised | `test_brains.py` |
| `resolve_brain` agrees with `merge_config_layers` itself over generated layer combinations | `test_brains.py` |
| An explicit `MOXIE_APP` beats a stored pick; an unset one pins nothing (`test_an_explicit_moxie_app_pins_and_an_unset_one_does_not`) | `test_brains.py` |
| Two robots on one appliance answered by two brains in one process | `test_brain_runtime.py` |
| A swap lands on the next turn; an in-flight turn keeps its brain | `test_brain_runtime.py` |
| A brain that will not build keeps the appliance talking and says so once | `test_brain_runtime.py` |
| `brain` never reaches the pushed `RobotCloudConfig` | both |
| The console normalizer renders a refusal, an unreachable supervisor and a truncated payload — never an empty card | `test_brain_console.py` |
| Deleting any of 22 guards turns a test red (mutation M9: reading the resolved `MOXIE_APP` instead of the raw one) | `brain_mutation_check.py` |

## Known gaps

- **No browser test clicks the card.** Its normalizer and wiring are tested; that is the same ceiling
  every console card has.
- **Our own compose default pins.** Both compose files set `MOXIE_APP: ${MOXIE_APP:-content}`, so a
  bare `docker compose up` arrives as an explicit `content` and pins. The card says so and names
  `MOXIE_APP=any`; silently ignoring an operator who really wrote `content` would be worse.
- **A brain instance is shared by every child using it** (keyed by name). Per-child state inside a
  brain — a second gateway, per-child keys, cost accounting — is P1 and needs a new secret.
- **No per-child persona.** Only the app half of "app + persona" is built; a persona is a `content`
  pack today.
- **`memory_store()` is appliance-level.** `/memory` reads the same files a per-child content brain
  writes, but through the appliance's app, not the child's.

---
📖 [Backlog index](README.md) · [AI seam](../ai-seam.md) · [Voice picker](voice-picker.md)
