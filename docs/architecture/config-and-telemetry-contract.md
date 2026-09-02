# ⚙️ Config & telemetry contract — what the server manages on the robot

> **Spec version 1 · robot side stamped to firmware v3.6.4-Zephyr / OTA v24.10.803.**
> The *implementation-facing* contract for the robot's **remotely-managed state**: the one config
> document the server pushes **down**, the status the robot reports **up**, and the telemetry +
> privacy-policy gate between them. This is the data model behind the **parent console** (bedtime,
> volume, alarms, OTA, privacy) and the robot-health view. Reads standalone; cites the study for
> provenance. Source: [`device-config-and-telemetry.md`](../reverse-engineering/protocol/device-config-and-telemetry.md),
> [`cloud-protocol.md`](../reverse-engineering/protocol/cloud-protocol.md),
> [`crypto-and-keys.md`](../reverse-engineering/phone/crypto-and-keys.md).

## The loop

Config/telemetry rides the same MQTT transport as the conversation ([Channel 2](mqtt-and-conversation.md)),
but it is a **separate concern** from the [AI seam](ai-seam.md): it's device management, not dialog.

```mermaid
flowchart LR
  console["🖥️ parent console<br/>(our web UI)"] -->|"writes settings"| server["🛂 server"]
  server -->|"/config · RobotCloudConfig"| robot(["🤖 Moxie"])
  robot -->|"/state · RobotStatus + SystemState"| server
  robot -->|"telemetry · Packet (policy-gated)"| server
  server -->|"reads status + insights"| console
  classDef s fill:#0e0e14,stroke:#00f0ff,color:#e8edf5;
  class console,server,robot s;
```

Two MQTT topics carry it ([topic map](../reverse-engineering/protocol/cloud-protocol.md#exact-topic-map-google-iot-core-convention-kept-post-migration)):
**`/devices/{id}/config`** (down) and **`/devices/{id}/state`** (up); telemetry `Packet`s upload as events.

---

## ① `/config` down — `RobotCloudConfig` (the thing the server must produce)

One document is the robot's entire remotely-managed runtime state. Change any knob = re-publish it.

| Group | Fields |
|---|---|
| **Child / user** | `child` (`ChildEncrypted` ciphertext), `child_pii` (`ChildDecrypted` plaintext), `secret_key` (pairing seed), `num_children`, `max_children`, `switch_user_config` |
| **Quiet hours** | `privacy_mode_enabled`, `weekday_bedtime_enabled` + `…_starts_at`/`…_ends_at`, `weekend_bedtime_*` |
| **Wake / alarms** | `alarms` (`WakeSchedule{ WakeEntry{days[], time}…, enabled }`), `wake_button_enabled`, `audio_wake_set`, `touch_wake_enabled`, `schedule_preferences` (`ParentRequest{module_id, scheduled_at}`) — **all built**, see [§Wake alarms & scheduled activities](#wake-alarms-scheduled-activities-the-json-we-emit) |
| **Device** | `audio_volume`, `screen_brightness`, `timezone_id`, `settings` (`DeviceSettings` k/v) |
| **OTA** | `ota_update {id, version}`, `forbid_otaver` |
| **Mode / privacy** | `moxie_mode` (`DEFAULT_MODE`/`TELEHEALTH`), `data_sharing`, `grl_connected`, `rc_topic` |
| **Meta** | `last_updated_at`, `timestamp` |

### Wake alarms & scheduled activities — the JSON we emit

Two of the parent's most visible settings — *"wake Moxie at 7:15 on school days"* and *"do the
drawing activity after school"* — are the config's `alarms` and `schedule_preferences`. Both are
**built** (`mqtt/moxie_sdk/cloud_config.py`: `build_robot_cloud_config(alarms=…,
schedule_preferences=…)`, `normalize_wake_schedule`, `normalize_schedule_preferences`) and both are
parent-editable through the console's ⚙️ Settings form.

The shapes are ours, from the recovered protos —
[`Cloud.proto`](../reverse-engineering/protocol/recovered-proto/embodied/logging/Cloud.proto):113-127
(catalogued in [`proto-catalog.md`](../reverse-engineering/protocol/proto-catalog.md):286-296),
carried by `RobotCloudConfig.alarms = 24` and `RobotCloudConfig.schedule_preferences = 28`:

```proto
message WakeSchedule {
  message WakeEntry { repeated uint32 days = 1; optional string time = 2; }
  repeated WakeEntry wakes = 1;  optional bool enabled = 2;
}
message SchedulePreferences {
  message ParentRequest { optional string module_id = 1; optional uint64 scheduled_at = 2; }
  repeated ParentRequest parent_requests = 1;
}
```

so the JSON on `/devices/{id}/config` is:

```json
"alarms": { "wakes": [ { "days": [0, 2, 4], "time": "07:15" } ], "enabled": true },
"schedule_preferences": { "parent_requests": [ { "module_id": "DRAW", "scheduled_at": 1788422400 } ] }
```

> **Three honest assumptions.** The protos give the *types*, not the *encodings*, and no capture of a
> real alarms push survives in our corpus (OpenMoxie never implemented these fields either, so there is
> no field-proven shape to follow). We chose, and isolated each choice behind one constant so it is a
> one-line change if a capture ever contradicts it:
> - **`days`** is `repeated uint32`, so 0-6 — we emit **0 = Monday … 6 = Sunday** (`datetime.weekday()`,
>   the convention the rest of this repo dates by). The single source is `cloud_config.WAKE_DAY_NAMES`;
>   the console's day checkboxes are ordered to match it.
> - **`time`** is a `string` beside the config's other wall-clock strings
>   (`weekday_bedtime_starts_at`, …), so **`"HH:MM"` local time**, validated by the same regex. The robot
>   resolves it against `timezone_id` — `TimeZoneInfo` → `UserAlarmRequest`
>   ([power & system events](../reverse-engineering/protocol/power-and-system-events.md)).
> - **`scheduled_at`** is a `uint64` with no stated unit — we emit **epoch seconds**, the unit this repo
>   already renders timestamps in (`Packet.recorded_at`). A value that is plainly milliseconds is divided
>   down rather than accepted at face value.
>
> `module_id` is *not* assumed: it is validated against the one on-board activity catalog
> (`moxie_sdk/schedule.py::ONBOARD_MODULES`), so a parent can only ask for an activity the robot has.

### Fleet defaults ⊕ per-robot overrides

One appliance can drive several robots, so the config the server pushes is layered
**`builder defaults ⊕ fleet ⊕ per-robot`** (`cloud_config.merge_config_layers`, a pure function):
nested objects merge key-by-key (`settings.props`, `alarms.enabled`), scalars and lists replace, and an
explicit `null` from the robot layer clears an inherited value. The fleet layer is one durable record,
`$MOXIE_DATA_DIR/fleet/config.json` (`store.py::read_shared`/`write_shared`), written by
`POST /config?scope=fleet` on the supervisor (the console's `POST /local/fleet/config`, the ⚙️ form's
*"Apply to all robots"*) and re-pushed to every connected robot at once. With no fleet record the push
is byte-for-byte what it was before the layer existed. *Credit:* the idea is OpenMoxie's
`HiveConfiguration` + `robot_data.py::build_config` deep-merge (MIT) — see `ATTRIBUTION.md`.

### The pairing gate — permits, and what a *pending* robot is sent

Our broker accepts anonymous connections ([mqtt §3b](mqtt-and-conversation.md)), so the
config push needs its own answer to *"is this my child's robot?"*. It is a **permit list,
closed by default** — `$MOXIE_DATA_DIR/fleet/permits.json`, beside `fleet/config.json`:

```jsonc
{ "allow_unverified_bots": false,                       // the appliance-wide switch
  "devices": { "d_<uuid>": { "permitted_at": 1788353318, "label": "Sam's Moxie" } } }
```

* **Permitted** (or `allow_unverified_bots`) → the full `RobotCloudConfig` above, exactly
  as it was before the gate: `pairing_status:"paired"` + `child_pii` + the parent's layers.
* **Not permitted** → the robot is *pending* and gets `build_unpaired_cloud_config()`:

```jsonc
{ "pairing_status": "unpairing",       // not "paired" ⇒ the robot does not run a session
  "data_sharing": "NO_DATA",           // LoggingPolicy shut: it may upload nothing to us
  "settings": { "props": { "gcp_upload_disable": "1", "default_loglevel": "warning" } } }
```

  **No `child_pii`, no `child`, no household settings, and no `stt` prop** (we never ask a
  device we do not know for its microphone). The document is written out in full in
  `cloud_config.py` rather than built by deleting keys from the paired one — a subtractive
  build is one forgotten key away from a leak. Everything else a pending robot asks for is
  refused or answered empty ([mqtt §3.7](mqtt-and-conversation.md)).

> **ASSUMPTION — the un-paired value is field-proven, not capture-proven.** Our corpus
> gives `CloudStatus.UserState` (`NONE`=1 = unpaired) for the robot's *upward* report, and
> §3.6's note that `pairing_status` must stay `"paired"` for the robot to run — but **no
> capture of Embodied's cloud pushing a non-`paired` `pairing_status`**. We push
> `"unpairing"` because that is the value OpenMoxie's device form writes and reads back as
> "Unpaired/Blocked" (`site/hive/models.py::MoxieDevice.is_paired`) in a server that drives
> real robots. What a *physical* Moxie displays on receiving it is **not verified** — we
> have no robot to observe. It sits behind one constant (`UNPAIRED_PAIRING_STATUS`), so a
> contradicting capture is a one-line fix.

*Credit:* the idea is OpenMoxie's `MoxieDevice.permit` + `HiveConfiguration.allow_unverified_bots`
(MIT — see `ATTRIBUTION.md`); no code was copied, and note that upstream stores the flag but
never enforces it on the MQTT path, so the enforcement here is ours.

**Switches.** `MOXIE_ALLOW_UNVERIFIED_BOTS=1` (env) restores the pre-gate behavior for a
deployment that was already running; `0` pins it shut. Precedence: constructor argument →
env → the stored fleet flag → **closed**. The console shows the flag *as enforced*
alongside the stored one, so an appliance opened by the environment cannot look closed.

**Surface.** Supervisor: `GET /permits`, `POST /permits {device_id, permitted, label}` or
`{allow_unverified_bots}`. Console: `GET /local/permits`, `POST /local/robots/{id}/permit`,
`POST /local/fleet/permits`; `GET /local/fleet` gains `allow_unverified_bots`, `pending[]`,
`pending_count`, and `permitted`/`pending`/`permit_label` per robot. Permitting a pending
robot re-pushes its full config immediately — no reconnect, no restart. Owner guide:
[`../guides/permitting-a-robot.md`](../guides/permitting-a-robot.md).

### The child-PII encryption boundary — and the revival shortcut
The child appears twice: **`child`** = `ChildEncrypted` (every field a `*_encrypted` blob +
`checksum`), **`child_pii`** = `ChildDecrypted` (plaintext `first_name`, `birthday`, `therapy_needs[]`,
…). The encrypted fields are unsealed with the pairing **`secret_key`** seed
([crypto](../reverse-engineering/phone/crypto-and-keys.md)) — the encryption exists to blind Embodied's
cloud, not the robot's own paired backend.

> **A self-hosted server IS the key-holder** (it ran pairing), so it can populate **`child_pii`
> directly and leave `child` empty**. You do not need to reproduce the E2E sealing to drive your own
> robot — that's a Channel-1 concern for blinding a third-party cloud.

---

## ② `/state` up — `RobotStatus` (the robot's self-report)

Published on `/devices/{id}/state`:

| | |
|---|---|
| `embodied_robot_id`, `mac` | `robot_firmware_version`, `android_version` |
| `battery_level`, `audio_volume`, `screen_brightness` | `wifi_ssid`, `mode` |
| `last_back_up_at`, `ota_reboot_required` | `public_key`, `user_id_encrypted` |
| `settings` (`DeviceSettings`) | `last_updated_at`, `timestamp` |

Live health rides separately as **`SystemState{CPULoad, RAMFree, DiskFree, Uptime, Temperature,
Battery, WifiRssi}`** ([health telemetry](../reverse-engineering/protocol/cloud-protocol.md#health-telemetry-backup-robot-cloud)).
Together these feed the console's "is my robot online / charged / up to date" view.

### `CloudStatus.UserState` — the pairing/OTA lifecycle the console shows
`CloudStatus{connected, user_state, endpoint}`; `user_state` ∈ `UNKNOWN`(0), `NONE`(1, unpaired),
`PAIRED_PENDING`(2), `PAIRED_VALID`(3, operating), `UNPAIR_REQUESTED`(4), `OTA_LOCK`(5),
`UNPAIR_WITH_RFS`(6, unpair+wipe), `USER_DATA_UPDATE`(7). This is the authoritative state a console
renders for "pairing status" and gates actions on.

---

## ③ Telemetry — `Packet` envelope + the privacy gate

Analytics/events upload inside a generic envelope:

```proto
message Packet {
  enum Model { UNKNOWN=0; SessionLog=1; Device=2; Event=3; Raw=4; }
  Model model = 1; uint32 version = 2; uint64 recorded_at = 3;
  string moxie_id = 4; string moxie_session_id = 5; string user_id = 6;
  string event_name = 7; bytes event_data = 8;   // typed payload
}
```

Scoped wrappers: **`LogDevice{deviceUUID, eventArgsTypename, eventArgs}`** and **`LogUser`** (adds
`userUUID`) — a self-describing typed-event pattern; **`LogcatTrace{…}`** carries raw Android logcat for
remote debugging. A minimal server may **ignore all telemetry**; a full one persists `Packet`s
per robot/session for the insights dashboard.

### `LoggingPolicy` — the privacy contract a server MUST honor
What may leave the device is gated by consent, **not** optional:
**`NO_DATA`(0)** · **`NO_MEDIA`(1)** (everything but audio/video) · **`FULL`(2)**. Tied to the account's
`RobotCloudConfig.data_sharing`. The recording session runs `LoggingState` `START→STARTED→STOP→STOPPED`
via `LoggingStateChangeRequest{state, path}`, reporting back the effective `upload_policy`.

> **This is the child-privacy contract, not a cosmetic flag.** A server (or custom firmware) MUST honor
> `NO_DATA`/`NO_MEDIA`. Staged files land under `/sdcard/EmbodiedData` and upload only per policy.

---

## What the parent console reads/writes (feature → field map)

| Console feature | Mechanism |
|---|---|
| Bedtime / quiet hours | `RobotCloudConfig` weekday/weekend bedtime windows + `privacy_mode_enabled` |
| Volume / brightness | `audio_volume`, `screen_brightness` (down); echoed in `RobotStatus` (up) |
| Wake alarms & wake toggles | `alarms` (`WakeSchedule`) + `wake_button_enabled`/`touch_wake_enabled`/`audio_wake_set` — weekday checkboxes + a time in the ⚙️ form |
| Timezone | `timezone_id` |
| Scheduled activities | `schedule_preferences` (`ParentRequest{module_id, scheduled_at}`) — module picker fed by the on-board catalog |
| House rules for every robot | the **fleet** layer: `POST /config?scope=fleet` → `fleet/config.json`, merged under each robot's own overrides |
| OTA target / hold | `ota_update{id,version}`, `forbid_otaver`; status via `ota_reboot_required` + `OTA_LOCK` |
| Privacy / data sharing | `data_sharing` → `LoggingPolicy` gate |
| Pairing status | `CloudStatus.UserState` |
| Which robots may be served | the **permit list** — `fleet/permits.json` + `allow_unverified_bots`; the 🔐 Robot access card lists pending robots and permits them in one click |
| Robot health | `RobotStatus` + `SystemState` |
| Insights / activity history | persisted `Packet` telemetry |

The console UI is the [REST/Channel-1 server](rest-api-contract.md)'s web surface; the settings it
writes become a `RobotCloudConfig` push, and the status it shows comes from `/state` + telemetry.

## Minimum viable vs full

- **Minimum:** publish a valid `RobotCloudConfig` on connect (with `child_pii`, `timezone_id`, volume,
  and `data_sharing=NO_DATA`), consume `/state` to know the robot is alive, and honor the logging
  policy (upload nothing). This is enough to operate a robot.
- **Full:** the whole console feature map above + persisted telemetry for an insights dashboard.

## Conformance checklist

- [ ] Publishes a well-formed `RobotCloudConfig` on `/devices/{id}/config` (populates `child_pii` directly as the key-holder).
- [ ] Re-publishes the config to change any managed setting (bedtime, volume, alarms, OTA, timezone).
- [ ] Emits `alarms` / `schedule_preferences` in the shapes above when a parent sets them (and omits them when unset).
- [ ] Consumes `RobotStatus` + `SystemState` from `/devices/{id}/state`.
- [ ] Tracks `CloudStatus.UserState` for pairing/OTA lifecycle.
- [ ] **Serves the child's config only to a permitted device** — an unknown robot gets the
      un-paired document with no `child_pii`, and nothing else.
- [ ] Honors `LoggingPolicy` (`NO_DATA`/`NO_MEDIA`/`FULL`) before uploading or staging any telemetry.

Where it lives: [`../../mqtt/`](../../mqtt/) (publishes config, consumes state/telemetry) +
[`../../server/`](../../server/) (the console that reads/writes it).

---
📖 [Docs index](../README.md) · [REST contract (Channel 1)](rest-api-contract.md) · [MQTT & conversation (Channel 2)](mqtt-and-conversation.md) · [AI seam](ai-seam.md)
