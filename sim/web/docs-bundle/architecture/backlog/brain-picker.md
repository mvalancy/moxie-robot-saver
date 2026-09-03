# 🧠 Any brain, hot-swappable, per child

> **Audit BEYOND #3 · ranked #7 · P0 SHIPPED 2026-09-03.**
> `ai-seam.md` §2 has always said Moxie's body is a shell and any AI can wear it. That was true
> of the drawing and false of the appliance: a brain was chosen **once, globally**, by `MOXIE_APP`
> at import time. This is the registry and the selection that make it an operation.
>
> Files: [`mqtt/moxie_sdk/brains.py`](../../../mqtt/moxie_sdk/brains.py) ·
> [`mqtt/config.py`](../../../mqtt/config.py) (`BRAIN_BUILDERS`, `BrainEngines`) ·
> [`moxie_runtime.py`](../../../mqtt/supervisor/moxie_runtime.py) (`app_for`, `brain_for`,
> `brain_view`, `brain_update`).
> Console: [`fleet.py::normalize_brain`](../../../server/moxie_server/fleet.py) + two proxy routes
> + the 🧠 card in [`server/static/`](../../../server/static/index.html).
> Tests: [`test_brains.py`](../../../sim/tests/test_brains.py) (82) +
> [`test_brain_runtime.py`](../../../sim/tests/test_brain_runtime.py) (31) +
> [`test_brain_console.py`](../../../sim/tests/test_brain_console.py) (13), plus
> [`brain_mutation_check.py`](../../../sim/tools/brain_mutation_check.py) — 22 guards deleted, 22 red.

## 1. What was actually missing

Both hard parts already existed, which is why this is a registry and a card rather than an
architecture:

| Hard part | Where it already worked |
|---|---|
| A live engine swap with **no restart** | the 🎚️ voice picker's `voice_update` (PR #48) and `reload_content()`'s attribute swap |
| A **per-robot override layer** | `defaults ⊕ fleet ⊕ per-robot` — audit ADOPT #6, `cloud_config.merge_config_layers`, `fleet/config.json`, `POST /config?scope=fleet` |

What did not exist was any registry of any kind (a repo-wide search for `BRAINS` /
`register_brain` / `brain_registry` returned nothing) and any way for two children on one
appliance to be answered by two brains.

## 2. The design, in four sentences

1. **A positive list.** `brains.BRAINS` is a closed table — `llm`, `content`, `webhook`, `echo` —
   in the idiom this codebase already relies on (`content/packs.py::SPEC`, `content/ext.py::OPS`,
   the frozen `vocab.py`). A name in it resolves to a builder; **a name that is not in it is
   refused, never guessed.** There is no deny-list, so nothing can be admitted by forgetting to
   exclude it.
2. **`brain` is an ordinary config key.** It rides `fleet/config.json ⊕ the per-robot overrides`,
   the one layering this codebase has, so `POST /config?scope=fleet` and `POST /config?device_id=`
   already set it and there is nothing new to back up or migrate. `cloud_config.SERVER_ONLY_KEYS`
   keeps it out of the document pushed to the robot, which has no field for it.
3. **The swap is resolved once per turn.** `MoxieRuntime.app_for(device_id)` is called at the top of
   `_handle_turn` and the app is carried through, so a parent's Save lands on the child's **next**
   turn and a turn already in flight finishes with the brain that heard the question.
4. **The operator's environment wins.** An explicit `MOXIE_APP` **pins** the appliance's brain
   (PR #77's rule for `MOXIE_TTS`/`MOXIE_STT`): the card offers only that entry, `resolve_brain`
   returns it whatever the layers say, and a stale page's cross-brain pick is refused **naming the
   variable**. `MOXIE_APP=any` is the explicit "decide per child".

### 2.1 Why the pin reads the *raw* environment

PR #77's lesson is that **a value which is a permission rather than a selection must not pin**
(`MOXIE_TTS=tone` opts the built-in beep in as the last rung; it does not choose it, and both
compose files default to it).

`MOXIE_APP` has no permission-shaped value — `build_app()` branches on the four names and each
returns exactly that app — so all four pin. What it *does* have is a **fall-through**:
`config.MOXIE_APP` is `os.environ.get("MOXIE_APP", "llm")`, so an unset variable already reads as
`llm`. Pinning that resolved value would have locked every box nobody configured out of the picker
— the same accident in a different costume. So the pin is computed from `config.BRAIN_ENV`, the raw
string, and `""` pins nothing. `sim/tests/test_brains.py::test_an_explicit_moxie_app_pins_and_an_unset_one_does_not`
is the guard, and mutation **M9** (read `MOXIE_APP` instead) turns it red.

## 3. What a parent sees

The 🧠 **Brain** card, beside 🎚️ Voice in the console: a dropdown of the brains this appliance can
actually run (with each one's blurb and the `MOXIE_*` variables it needs), an *applies to* choice —
**this robot** or **every robot (house rule)** — a **Use the layer underneath** button that clears a
layer, and a row per robot reading *"Sam — Content modules (content) — house rule"*. When the
environment has pinned the brain, that sentence is the **first** thing in the card, because it is the
reason the dropdown looks short.

Underneath, `GET /brain` renders it: every brain this box can run (id, label, group, blurb, the
`MOXIE_*` variables it needs), the house rule, the pin and its note, and **one row per robot**
saying which brain answers that child and *which layer decided* (`default` / `fleet` / `robot` /
`pin`). `POST /brain?device_id=…` picks for one child, `?scope=fleet` sets the house rule,
`{"brain": null}` clears a layer. Both are thin, validating front doors onto `update_config` /
`update_fleet_config` — the store and the push are the ones that already existed.

## 4. Tests

| # | Property | Where |
|:--:|---|---|
| 1 | The table is exactly four brains, frozen as a literal | `test_brains.py` |
| 2 | Every near-miss name (`gpt5`, `llm # the brain`, a dict) is refused; case and space are normalised | `test_brains.py` |
| 3 | `resolve_brain` agrees with `merge_config_layers` **itself** over generated layer combinations — the guard against a second layering | `test_brains.py` |
| 4 | An explicit `MOXIE_APP` beats a stored pick; an unset one pins nothing | `test_brains.py` |
| 5 | Two robots on one appliance answered by two brains, in one process | `test_brain_runtime.py` |
| 6 | A swap lands on the next turn; an in-flight turn keeps its brain | `test_brain_runtime.py` |
| 7 | A brain that will not build keeps the appliance talking, and says so once | `test_brain_runtime.py` |
| 8 | `brain` never reaches the pushed `RobotCloudConfig` | both |
| 9 | The console normalizer renders a refusal, an unreachable supervisor and a truncated payload — never an empty card that reads as "no brains" | `test_brain_console.py` |
| 10 | 22 guards deleted one at a time, 22 tests go red | `sim/tools/brain_mutation_check.py` |

## 5. Honest gaps (P1+)

* **The card has no browser harness.** Its normalizer and its id/driver wiring are tested
  (`test_brain_console.py`), and `test_console_roundtrip.py` proves the console app still imports and
  serves — but no test *clicks* it. That is the same ceiling every other card in this console has.
* **Our own compose default pins.** `docker-compose.yml` interpolates `MOXIE_APP:
  ${MOXIE_APP:-content}`, so a `docker compose up` with nothing set arrives as an explicit `content`
  and pins — the shape #77 warned about. It is **told, not hidden**: the card prints the pin note
  and names `MOXIE_APP=any` as the way to hand the choice back. Excluding `content` from the pin
  table would have silently ignored the operator who really did write it, which is worse.
* **A brain is shared by every child on it**, keyed by name — exactly today's semantics (one app
  object serves every robot). Per-child *state* inside a brain (a second gateway, per-child keys,
  cost accounting) is explicitly out of P0: all three need a new secret.
* **No per-child persona.** BEYOND #3's full form is "app **+ persona** binding"; only the app half
  is built. A persona is a `content` pack today.
* **`memory_store()` is still fleet-level.** `/memory` reads the same files a per-child content
  brain writes, so it answers correctly — but it reads them through the *appliance's* app, not the
  child's.
