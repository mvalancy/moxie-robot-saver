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
| **Child / user** | `child` (`ChildEncrypted` ciphertext), `child_pii` (`ChildDecrypted` plaintext — incl. **`face_options`**, the child's chosen appearance, see [§Appearance](#-appearance-the-childs-chosen-face)), `secret_key` (pairing seed), `num_children`, `max_children`, `switch_user_config` |
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

### 🎨 Appearance — the child's chosen face

Moxie's face is a **composite of independent layers**, not one picture, and which layers it
wears is part of the child profile. So appearance rides down inside `child_pii`, in
`ChildDecrypted.face_options` — `repeated string`, field **17**
([`Cloud.proto`](../reverse-engineering/protocol/recovered-proto/embodied/logging/Cloud.proto):166,
catalogued at [`proto-catalog.md`](../reverse-engineering/protocol/proto-catalog.md):334; the sealed
twin `ChildEncrypted.face_options = 16` is Cloud.proto:144 · proto-catalog.md:313). It is **not** one of
the encrypted fields: both
[`device-config-and-telemetry.md`](../reverse-engineering/protocol/device-config-and-telemetry.md):52-54
and [`crypto-and-keys.md`](../reverse-engineering/phone/crypto-and-keys.md):506-508 list it among the
*clear* metadata sitting beside the `*_encrypted` blobs, so a server fills it in directly.

**The anatomy — 14 layers, cited.**
[`unity-face-animation.md`](../reverse-engineering/runtime/unity-face-animation.md):34-42 records
`MoxieCustomizationType` as "14 independent, swappable slots" and names every one:

| Slot(s) | |
|---|---|
| `EyeColor` · `EyeDesign` · `EyeLid` | the eyes — the expressive core |
| `Brows` · `Mouth` · `Nose` · `Mustache` | brows and lower-face features |
| `FaceColor` · `FaceDesign` | base head colour + surface pattern |
| `Hair` · `Glasses` · `Stickers` · `Extras` · `Misc` | cosmetic add-on layers |

**The options — 12, across 2 of the 14, and that is all we have.** Our corpus lists concrete choices
for exactly two slots, and lists them *with hex*, which is why those two are the only ones the console
can preview ([`robot-lifecycle.md`](../features/robot-lifecycle.md):280-283 = the `Robot.java`
`EYE_COLORS`/`FACE_COLORS` constants; repeated at
[`feature-catalog.md`](../features/feature-catalog.md):238-241, which also gives the Channel-1 spelling
`ChildrenModel.eye-color`/`face-color` → `PUT children/{id}`, gated by the account flags
`supports-eye-color`/`supports-face-color`):

* `EyeColor{green 42D02B, blue 8491EF, purple 9437DE, brown 443319, gold F4BF03, teal 38ADAE}`
* `FaceColor{blue BBCFE1, yellow F0F055, green 9BDB9B, teal 7ED6DD, pink E1A2A2, purple C395D4}`

For the other **twelve** slots our corpus names the slot and stops. That is structural, not an oversight:
the customization art is loaded by `MoxieCustomizationAsset`/`MoxieCustomizationPreview` out of a
**streamed** bundle ([`content-delivery.md`](../reverse-engineering/runtime/content-delivery.md):79,
source `REMOTE_ASSETBUNDLES`) rather than the base APK, which is exactly why the UnityPy inventory in
[`unity-assets.md`](../reverse-engineering/firmware/unity-assets.md):19-67 found none of them; and
[`behavior-markup.md`](../reverse-engineering/runtime/behavior-markup.md):161-163 records that the
generators "accept **any** id the loaded bundle defines", so the id space is bundle-defined and cannot
be inferred *from our corpus*.

**So we ingested someone else's, as data (2026-09-02).** OpenMoxie (MIT) ships a 60-entry table of real
`MX_<nnn>_<Group>_<Detail>` labels harvested from robots its authors can run
(`site/hive/content/data.py::MOXIE_CUSTOMIZATIONS`). We transcribed **the id strings and nothing else** —
no code, no comments, no function bodies — into [`mqtt/moxie_sdk/face_assets.json`](../../mqtt/moxie_sdk/face_assets.json),
which carries the full citation inline (repo, path, symbol, commit `c8c2d380`, MIT, ingest date, entry
count, sha256 of the id list). The slot mapping — each group prefix to exactly one recovered
`MoxieCustomizationType` — and every human-readable label are ours; an id we could not place would sit in
`unmapped` rather than be guessed, and all 60 placed. **`moxie_sdk/faces.py` therefore ships 72 options
across 11 of the 14 slots and still zero invented ids**, each tagged with its `origin`:
`recovered-enum` (the 12 with hex, previewable) or `openmoxie-manifest` (the 60, every one carrying
`caution: true`, because upstream's own note beside the list records that some of these crashed Unity
**without saying which** — [`mqtt-and-conversation.md`](mqtt-and-conversation.md):824 says the same
independently, "some face customization assets crash Unity and are excluded").

`Stickers`, `Extras` and `Misc` remain listed and empty (`cited: false`) — neither source names a piece
for them. A parent who knows their robot's real labels supplies them verbatim through `face.custom`,
which we never rewrite. **The wire spelling depends on the origin:** a `recovered-enum` option is an enum
*member* name and is joined to the slot's `MoxieCustomizationType` spelling (`EyeColor_teal` — the
assumption below); an `openmoxie-manifest` option is already a whole asset label and travels verbatim
(`MX_010_Eyes_Hazel`). The mechanism and the ingest are both credited in `ATTRIBUTION.md`.

So the JSON on `/devices/{id}/config` is:

```json
"child_pii": { "nickname": "Sam",
               "face_options": ["EyeColor_teal", "FaceColor_pink"],
               "id": "a6f3609a-0e20-512c-ae72-a16153adf140" }
```

**Layering.** The parent-facing override is `face` — an object, so `merge_config_layers` deep-merges it
**per slot**: a fleet-default look ("all our robots are teal-eyed") survives a per-robot edit that only
changes the face colour, and a robot-layer `null` on one slot clears just that layer. An explicit
`face: null` from the robot layer clears the whole selection, and — like `weekday_bedtime` — beats an
inherited fleet look, so "this robot wears nothing" stays expressible. With no face chosen, neither
`face_options` nor `id` is emitted and the document is byte-for-byte what it was before appearance
existed.

> **Two honest assumptions**, each isolated behind one function in
> [`mqtt/moxie_sdk/faces.py`](../../mqtt/moxie_sdk/faces.py), and **neither observed on a physical
> robot — this project has none**:
>
> - **The label format.** `face_options` is `repeated string`; nothing in our corpus records what those
>   strings look like. `face_option_label()` joins two *cited* spellings — the `MoxieCustomizationType`
>   slot name and the enum member name — as `EyeColor` + `_` + `teal` → `"EyeColor_teal"`. Every
>   character is quoted from a document above; only the join is ours. `face.custom` bypasses it entirely.
> - **The cache-buster.** A layered face is composited into a texture, and a robot that has one has no
>   reason to redo the work. Our corpus does not record the cache key: it gives `ChildDecrypted.id = 14`
>   as the child's identity in the pushed config, `SwitchUserConfig{action, restore_id, child_id, force,
>   child_name}` as the user-switch lever (proto-catalog.md:341-347), and `USER_DATA_UPDATE` as both the
>   cloud-visible lifecycle state (device-config-and-telemetry.md:88) and the on-device disengage reason
>   for "the child's data/profile is being updated"
>   ([power-and-system-events.md](../reverse-engineering/protocol/power-and-system-events.md):85) — but
>   it never says the texture cache is keyed on `child_pii.id`. OpenMoxie's face editor does, from a
>   server that drives real robots, by writing a fresh `uuid4` there on every save. So this is
>   **field-proven, not capture-proven**, exactly like `UNPAIRED_PAIRING_STATUS`. We take the mechanism
>   and make it deterministic: `face_child_id()` is a **UUIDv5** over the child key + the rendered layer
>   list, so the same look re-pushes the same id (an idempotent push does not disturb the robot) and any
>   layer change yields a new one (a stale record cannot match). One function; a contradicting capture
>   is a one-line fix.

**Surface.** Supervisor: `POST /config?device_id=…` (or `?scope=fleet`) with `{"face": {…}}`, the same
whitelisted path every other setting uses; `GET /status` publishes `face_catalog` (the SDK's catalog, so
the console never keeps a second copy) and each robot's `face_cache_id`. Console: the 🎨 Moxie's look
card in the 🤖 Moxie tab, per-robot with the fleet look underneath. Owner guide:
[`../guides/moxies-look.md`](../guides/moxies-look.md).

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

**The same record also renders the broker ACL.** `mqtt/moxie_sdk/broker_acl.py::render_acl`
turns this file into a mosquitto ACL — the `%c` device floor plus one `user d_<uuid>` block
per permitted device. It is **generated and inert** today: no robot authenticates, so no
`user` block can match ([`backlog/security-broker-auth.md`](backlog/security-broker-auth.md)
§2.3). It exists so that when the broker gains a way to verify a device, `permits.json`
stays the one place that says which robots are ours — for service, for the ACL and for
broker auth alike.

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
| Moxie's look (the child's face) | `child_pii.face_options` (14 layers, 72 cited options across 11) + the `child_pii.id` cache-buster — the 🎨 card; see [§Appearance](#-appearance-the-childs-chosen-face) |
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
- [ ] Carries the child's chosen appearance in `child_pii.face_options`, and **changes
      `child_pii.id` whenever those layers change** so the robot cannot serve a stale face texture.
- [ ] Honors `LoggingPolicy` (`NO_DATA`/`NO_MEDIA`/`FULL`) before uploading or staging any telemetry.

Where it lives: [`../../mqtt/`](../../mqtt/) (publishes config, consumes state/telemetry) +
[`../../server/`](../../server/) (the console that reads/writes it).

---
📖 [Docs index](../README.md) · [REST contract (Channel 1)](rest-api-contract.md) · [MQTT & conversation (Channel 2)](mqtt-and-conversation.md) · [AI seam](ai-seam.md)
