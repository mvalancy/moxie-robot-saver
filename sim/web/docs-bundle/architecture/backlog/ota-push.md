# OTA push: a proposal, and the case for not building it yet

**Status:** proposed, and gated on the owner. Nothing is built. `grep -rn "ota_update\|forbid_otaver\|ota_updates\|MOXIE_OTA" mqtt/ server/ sim/ --include=*.py` finds nothing. `build_robot_cloud_config` has no OTA keyword, and no `mqtt/moxie_sdk/ota.py` exists.

## Problem

The audit row "OTA push (801→803 lever)" ([OpenMoxie feature audit](../openmoxie-feature-audit.md)
§3.1) asks whether our appliance should offer a firmware update to a robot. The config document can name
an update target, so the mechanism is almost free to add: *one dictionary key*. The risk is not. This is
the only backlog item that changes firmware on hardware nobody in this project owns, and its worst case
is a robot that only opening the shell can recover. Recovery through maskrom needs a test point held low
with *"the board open"*
([`flashing-runbook.md`](../../reverse-engineering/firmware/flashing-runbook.md)).

**Who it would serve is narrow.** Pre-801 robots hardcode `mqtt.googleapis.com` and cannot reach us at
all; their route is opening the case and flashing
([`ota-and-recovery.md`](../../reverse-engineering/firmware/ota-and-recovery.md)). The feature would
serve only 801/802 robots that have already been re-homed to our broker and whose owner wants 803. We
know of none.

**Nothing exists to serve.** We do not have a genuine Embodied-signed 803 `update.zip`. We have raw
partition images, which are a different artifact. The robot applies only payloads signed by Embodied's
key.

## What we know

| Label | Fact | Source |
|---|---|---|
| proven | `RobotCloudConfig` carries `OtaUpdate ota_update = 13` (fields `id = 1`, `version = 2`, both strings; no URL, digest or signature) and `string forbid_otaver = 17` | [`Cloud.proto`](../../reverse-engineering/protocol/recovered-proto/embodied/logging/Cloud.proto), [`proto-catalog.md`](../../reverse-engineering/protocol/proto-catalog.md) |
| proven | Up on `/state`: `robot_firmware_version` and `ota_reboot_required` (already parsed by `cloud_config.parse_robot_status`) | [`device-config-and-telemetry.md`](../../reverse-engineering/protocol/device-config-and-telemetry.md) |
| proven | `CloudStatus.UserState.OTA_LOCK = 5`: the robot quiesces itself for an OTA | same |
| proven | Updates are stock Android A/B: `update_engine` verifies the payload against Embodied's RSA key, writes the **inactive** slot, and rolls back a bad update. `BoUpdater` adds a min-version gate, a data budget and a `DISABLE_OTA` sentinel. | [`ota-and-recovery.md`](../../reverse-engineering/firmware/ota-and-recovery.md) |
| proven | The `om` relocation QR can set `webservice_root`, the REST base the robot uses | [`qr-commands.md`](../../reverse-engineering/protocol/qr-commands.md) |
| second-hand | A version that differs from the robot's own makes it request an HTTP token (`client-service-http-token`) and then `GET {webservice_root}/api/ota_updates/{id}/url`, expecting `{"url": …}`. A placeholder token sufficed, and a static JSON file worked. | OpenMoxie `doc/RemoteModuleAPI.md` (one person's upgrade), summarised in [`mqtt-and-conversation.md`](../mqtt-and-conversation.md) |
| unknown | U1: what `forbid_otaver` means. U2: how `version` is compared, and whether a downgrade is reachable. U3: whether `OTAStatus` progress ever reaches MQTT; it has no recovered enum. U4: what the robot does with a URL it cannot fetch. | none in our corpus |

**Where the brick risk actually is.** A bad payload cannot brick the robot, because the signature
check, the inactive slot and the rollback all belong to the robot and we never touch them. The one way
to brick it is to **defeat the signature gate**: replace `update-payload-key.pub.pem` or `otacerts.zip`,
or flash a `--disable-verification` vbmeta. That must never be reachable from a network service, in any
phase.

## Proposed approach

Put the refusals in place first, and add the transmitter last. An OTA target would reach the wire only
by passing eight gates. Each gate is a pure check over data we already hold, and any gate that cannot
evaluate its input refuses:

| Gate | Rule |
|---|---|
| G1 | Permitted devices only. An unpaired robot's document can never carry an OTA key. |
| G2 | Per robot, never fleet-wide. `ota_update` is kept out of `sanitize_config_overrides`, which the fleet `POST /config?scope=fleet` shares, and lives in its own arm record. |
| G3 | The target version must be on a hard-coded allowlist, which today would hold the single 803 string. |
| G4 | The target must differ from the version in a `/state` we have actually received. No `/state` means refuse. |
| G5 | The artifact is served only if its SHA-256 matches a pinned manifest (`OtaUpdate` has no digest field of its own). |
| G6 | `id` is a single path segment we minted (`^[a-z0-9][a-z0-9_-]{0,31}$`), never text a parent typed. |
| G7 | Offer only when the robot is quiet: not mid-session, not in a wake window. We cannot make the robot wait; we can only decline to offer. |
| G8 | An owner-armed arm is one-shot and expires (1 h by default). It rides exactly one config push and clears on expiry or when `/state` reports the target version. |

G8 exists because `_push_config` republishes the whole document on every connect and every edit. A
target stored as an ordinary setting would be re-sent forever. Three more rules sit above the gates:

- Never weaken the robot's own verification.
- Use `forbid_otaver` only to name a version we want refused. Under every reading of U1 that use is
  either correct or does nothing.
- A refused arm produces a config document byte-identical to the unarmed one.

**Phases:**

- **P0 (S): refusals, no transmitter.** A pure `mqtt/moxie_sdk/ota.py` holding the allowlist, the id
  pattern and a function that returns either a fragment or a typed refusal naming the gate. A guard test
  that no code path can put `ota_update` on the wire. A read-only console line showing what firmware the
  robot reports and "no update server is configured", which keeps the promise in
  [`config-and-telemetry-contract.md`](../config-and-telemetry-contract.md) true.
- **P1 (M): serving, still disconnected.** A local artifact store with a pinned manifest,
  `GET /api/ota_updates/{id}/url` for permitted devices only (anything else gets a plain 404), and a
  `client-service-http-token` handler. It can be exercised against the SIL robot. No real robot is ever
  told to ask.
- **P2 (M): the arm. Owner-gated; an agent must not start it.** The arm record, `MOXIE_OTA_ARM` (off by
  default), a typed confirmation in the console, and an audit line per arm, push and clear. P0's
  "nothing can transmit" guard would invert.

**Testing ceiling.** All of this proves our software only. No test can answer U1–U4. The whole wire flow
is second-hand. The console must report *offered*, never *installed*: the only honest completion signal
is `robot_firmware_version` changing on a later `/state`.

## Open questions for the owner

1. **Is there an image at all?** Should we pursue sourcing a signed 803 `update.zip`, and are you willing
   to redistribute it?
2. **Who hosts it, and under which CA?** The robot validates by CA chain. A self-signed appliance may not
   be accepted for the download, which could force a publicly trusted certificate.
3. **What is a token to us?** Should we mint real per-device tokens, or accept that the token is theatre
   and say so in the code?
4. **Should `forbid_otaver` be pinned down (U1)**, or should we use only its inert direction?
5. **Is this worth doing** for a population we know of none of, against the steer that anything
   improving the public SIM outranks work for a robot none of us has?

**Recommendation: keep this as a specification.** There is no signed image, the population is
hypothetical, and the wire flow is second-hand with four unknowns. If the owner wants anything, P0 is
the candidate: it ships no capability, gives a parent an honest firmware readout, and installs the guard
that makes an accidental one-key OTA go red.

---

📖 [Backlog index](README.md) · [Architecture index](../README.md) ·
[OpenMoxie feature audit](../openmoxie-feature-audit.md) ·
[Config & telemetry contract](../config-and-telemetry-contract.md) ·
[OTA & recovery (RE)](../../reverse-engineering/firmware/ota-and-recovery.md) ·
[Network trust (RE)](../../reverse-engineering/protocol/network-trust.md) ·
[Docs index](../../README.md)
