# Map 11 — Robot Lifecycle (pairing / unpair / FACTORY RESET / restore / reboot / OTA / state model)

App: `com.embo.embodied.parent` v2.2.2 (decompiled). Cross-refs: map 01 (REST/auth), 02 (crypto/sealed keys), 03 (pairing).
All robot endpoints are relative to the API base URL (see map 01). All take `Authorization: Bearer <token>` via `@Header("Authorization")`.
`{id}` everywhere = the **robot id** (JSON:API robot resource id), NOT the serial number. The app takes it from `Robot.INSTANCE.getData().getId()` or `User…getRelationships().getRobots().getData().get(0).getId()`.

---

## 0. TL;DR — How to FACTORY-RESET a paired Moxie

**One HTTP call:**

```
DELETE robots/{id}?rfs=1
Authorization: Bearer <access_token>
```

- `rfs=1` = **restore-factory-settings**. This is the "Restore Factory Settings" / "Reset Moxie Back to New" path. It unpairs AND tells the backend to wipe/factory-reset the robot device (server pushes the wipe to the robot over its IoT channel).
- Plain `DELETE robots/{id}` (no query) = **unpair only** (dissociate robot from account; does not factory-wipe the device).

Both are the *same endpoint*; the ONLY difference is the `?rfs=1` query flag. In the app the two are literally two Retrofit methods pointing at `robots/{id}` vs `robots/{id}?rfs=1`, selected by a boolean `restoreFactorySettings`.

After success the app calls `Robot.INSTANCE.clear()` + `CryptoManager.INSTANCE.reset()` + `fetchUserInfo()`, and the robot returns to `MoxieStatus.UNPAIRED` → app shows the unpaired / QR-pairing screen. There is **no on-device hardware button reset documented anywhere in the app** (see §1.4).

---

## 1. FACTORY RESET vs UNPAIR

### 1.1 Endpoint definitions
`api/Config.java`:
```
API_DELETE_ROBOT          = "robots/{id}"        // unpair only
API_DELETE_ROBOT_RESTORE  = "robots/{id}?rfs=1"  // unpair + factory reset
```
`api/APIService.java`:
```java
@DELETE("robots/{id}")
Call<ResponseBody> unpairRobot(@Header("Authorization") String auth, @Path("id") String id);

@DELETE(Config.API_DELETE_ROBOT_RESTORE)          // robots/{id}?rfs=1
Call<ResponseBody> unpairRobotWithRestoreFactory(@Header("Authorization") String auth, @Path("id") String id);
```
`rfs` = **r**estore **f**actory **s**ettings (confirmed by string `restore_factory_settings` = "Restore Factory Settings" bound to the red-colored button in the unpair dialog, and by `RequestManager` logging `"unpair; rfs=" + restoreFactorySettings`).

### 1.2 RequestManager selector — `api/RequestManager.java`
```java
public final void unpairRobot(boolean restoreFactorySettings, ResponseCallback cb) {
    // id = Robot.INSTANCE.getData().getId()
    unpairRobot(id, restoreFactorySettings, cb);
}
public final void unpairRobot(String id, boolean restoreFactorySettings, ResponseCallback cb) {
    if (restoreFactorySettings) apiService.unpairRobotWithRestoreFactory(auth, id).enqueue(...);  // DELETE robots/{id}?rfs=1
    else                        apiService.unpairRobot(auth, id).enqueue(...);                    // DELETE robots/{id}
    Log.trackCrashlytics(TAG, "unpair; rfs=" + restoreFactorySettings);
}
```

### 1.3 UI flow — `BaseActivity.unpairMoxie()`  (the confirmation bottom-sheet)
`BaseActivity.java` builds a `BottomDialog` with three buttons:
| Button | string | action |
|---|---|---|
| header | `unpaired_moxie_dialog_description` = "Are you sure you want to unpair the robot?" | — |
| **Unpair** | `unpair` (colorAccent) | `unpairRobotRequest(false, cb)` → `DELETE robots/{id}` |
| **Restore Factory Settings** | `restore_factory_settings` (red_embo) | `unpairRobotRequest(true, cb)` → `DELETE robots/{id}?rfs=1` |
| Cancel | — | dismiss |

`unpairRobotRequest(boolean z, cb)` → `RequestManager.unpairRobot(z, ...)`. On success:
```java
Robot.INSTANCE.clear();          // wipes cached robot data/settings/restores/languageSupport
CryptoManager.INSTANCE.reset();  // drops the sealed secret-key material for the robot (see map 02)
fetchUserInfo();                 // reloads account → user now has no paired robot → UNPAIRED UI
```
On fail: shows `"Error: {code} msg: {msg}"`.

### 1.4 On-device / hardware reset
**None exists in the app.** Exhaustive grep of `strings.xml` and help content for `hold/press button`, `10 seconds`, `power button`, `hard reset`, `back of Moxie`, etc. returns **nothing** about a physical reset combo. All reset/unpair/factory-reset is server-driven via the DELETE endpoint above. The only physical "buttons" referenced are the app UI's Add/Switch/Plus buttons. Implication for the local-server project: to wipe/relinquish a robot you must replicate the backend behavior behind `DELETE robots/{id}?rfs=1` (the server relays the factory-reset command to the device over its IoT/MQTT link).

---

## 2. UNPAIR vs FACTORY RESET — what differs

| | Unpair (`DELETE robots/{id}`) | Factory reset (`DELETE robots/{id}?rfs=1`) |
|---|---|---|
| Robot ↔ account link | removed | removed |
| Device on-robot data | left as-is (backend keeps backup; robot keeps its local profile until re-provisioned) | backend instructs the robot to factory-wipe |
| App-side cleanup | identical: `Robot.clear()`, `CryptoManager.reset()`, `fetchUserInfo()` | identical |
| Child data on server | **Not deleted here.** Child records persist under the account; a subsequent backup can be restored to a new/reset robot. See note below. | same |
| Resulting `MoxieStatus` | `UNPAIRED` | `UNPAIRED` |

Note on the child: unpair/factory-reset act on the **robot**, not the **child**. Deleting a child is a separate flow (`DELETE children/{id}`, `deleteChildren`), and the app blocks it while paired: string `delete_child_warning_unpair` = "You cannot delete a child that is paired with Moxie: you must unpair first." So the enforced order is: unpair robot → then may delete child. Because the account keeps `has-backups`, the child's progress/settings survive an unpair and can be restored onto a fresh robot (§3).

---

## 3. RESTORE & BACKUP

### 3.1 Concept
Account-level flag `UserAttributes."has-backups"` (`hasBackups`) signals a restorable backup exists. When true, pairing/reset flows offer to **restore Moxie from a backup** (child's past activity + settings) vs **set up as new** (erase progress). The restored profile is keyed by `UserAttributes."last-restored-child-id"` (`lastRestoredChildId`). Restore is what re-seals/re-delivers the child's secret keys to the new robot (ties to `secret-key-collection`, map 02).

### 3.2 Create-restore endpoint — `robots/{id}/restores`
`Config.API_CREATE_RESTORE_ROBOT = "robots/{id}/restores"`
```java
@POST("robots/{id}/restores")
Call<ResponseBody> createRestore(@Header("Authorization") String auth, @Path("id") String id, @Body RestoreRobotModel model);
```
Body shape (`RestoreRobotModel` → `RestoreRobotStatus`):
```json
{ "restore": { "status": "initiated" } }     // user chose to restore from backup
{ "restore": { "status": "declined" } }      // user chose set-up-as-new / reset progress
```
`RequestManager.createRestore(id, userWantsToRestoreFromBackup, cb)`:
```java
Robot.RestoreStatus s = userWantsToRestoreFromBackup ? RestoreStatus.initiated : RestoreStatus.declined;
apiService.createRestore(auth, id, new RestoreRobotModel(new RestoreRobotStatus(s.name())));
```
`RestoreStatus` enum values (also the server-returned statuses): `initiated, declined, failed, succeeded`.

### 3.3 `userWantsToRestoreFromBackup` flag (`Config.userWantsToRestoreFromBackup`, default false)
Set true/false at these decision points:
- `PairMoxieActivity` (during WIFI_AND_PAIRING pairing): if `hasBackups==true` && `checkBackup` intent extra (`SharedKeys.CHECK_RESTORE_FROM_BACKUP`) set, shows `RestoreMoxieFragment`; its `onRestore()` → flag=true, `onSkip()` → flag=false, then proceeds to Wi-Fi pairing page. The pairing registration (`registerForPairing`, §5) sends `restore=<flag>`.
- `MoxieFragment.onRestoreTryAgainClicked` → flag=true then `createRestoreRequest`; cancel path sets flag=false.
- `BaseActivity` resets flag=false after handling.

### 3.4 `RestoreMoxieFragment` (`pair_moxie/RestoreMoxieFragment.java`)
Simple 2-button screen (`FragmentMoxieRestoreBinding`): **confirmButton** → `RestoreCallback.onRestore()`, **declineButton** → `RestoreCallback.onSkip()`. Copy strings: `restore_top_desc` ("We noticed your profile has been backed up!"), `restore_bottom_desc`, `restore_bottom_desc_2` ("…all of your child's progress with Moxie will be erased and Moxie will be reset as new."), `restore_moxie_from_backup_title/desc`, `no_reset_moxie_back_to_new` ("No, Reset Moxie Back to New"), `restore_reset_progress` ("No, I'd Like To Reset My Progress").

### 3.5 `createRestoreRequest` — `BaseActivity.java`
Picks `robots.getData().get(0).getId()` and calls `createRestore(id, Config.userWantsToRestoreFromBackup, …)`; on success re-fetches user. Used by the restore-failed "try again" UI.

### 3.6 Restore state read-back — `Robot.getRestoreStatus()`
Robot pulls `include=restore,robot-setting` (`ROBOT_INCLUDE`). The `restores` relationship deserializes to `IncludedRestores{ id, type, attributes }` where `RestoreAttributes`:
- `@SerializedName("status")` — one of `initiated/declined/failed/succeeded`
- `@SerializedName("created-at")`
- `@SerializedName("restore-type")` → enum `Robot.RestoreType { switch_child, new_child, restore, pairing }`

Mapping to UI status (only when `user.hasBackups==true` && a restore object exists):
- status == `failed` → `MoxieStatus.RESTORE_FAILED`
- status == `initiated` → `MoxieStatus.RESTORE_IN_PROGRESS`
Strings: `restore_in_progress`, `restore_pending`, `restore_progress`, `moxie_restore_in_progress_title/description`, `moxie_restore_failed_title/description`.

---

## 4. REBOOT & WAKEUP

### 4.1 Reboot — `robots/{id}/reboot`
`Config.API_REBOOT_ROBOT = "robots/{id}/reboot"`
```java
@POST(Config.API_REBOOT_ROBOT)
Call<ResponseBody> rebootRobot(@Header("Authorization") String auth, @Path("id") String id);   // no body
```
`RequestManager.rebootRobot(id, cb)`. Triggered from `MessageDetailsFragment.rebootRobotRequest()` (a server "message"/action card with `EPage.reboot` / `EActionView.reboot`). If `Robot.getData()==null` shows `error_title`/`moxie_unpaired_title`. Response parsed as `RebootMoxieResponseModel { code, title, body }` (all `@SerializedName`, strings) and shown in a dialog.

### 4.2 Wakeup — `robots/{id}/wakeup`
`Config.API_WAKE_UP_MOXIE = "robots/{id}/wakeup"`
```java
@POST(Config.API_WAKE_UP_MOXIE)
Call<ResponseBody> wakeupMoxie(@Header("Authorization") String auth, @Path("id") String id);   // no body
```
`RobotInfoViewModel.wakeupRobotRequest(WakeUpMoxieCallback cb)` → `RequestManager.wakeupMoxie(id, …)`. Response parsed as `WakeupMoxieResponseModel { code, title, body, error }` (all `@SerializedName`, strings). Callback interface `api/interfaces/WakeUpMoxieCallback` = `{ onResponse(WakeupMoxieResponseModel), onFail(int code) }`. If parse yields null or empty body → `onFail(code)`. Invoked from `MoxieFragment.onWakeUpMoxieClick()` (shown when robot is offline/asleep — `ImageState.sleeping`/`wake_button`).

Both reboot & wakeup are **empty-body POSTs**; the only variable is `{id}`.

---

## 5. PAIRING registration (context; full detail in map 03)
`Config.API_PAIRING_INFO = "pairing-info"`, `@POST` with `@QueryMap` params:
```
id       = <robot id>
restore  = "true"/"false"   (= userWantsToRestoreFromBackup)
user-id  = <user id>
child-id = <child id>
```
`RequestManager.registerForPairing(id, userWantsToRestoreFromBackup, user_id, child_id, cb)`. So the restore decision is carried into pairing here too.

---

## 6. OTA / FIRMWARE

### 6.1 Endpoint — `robots/{id}/ota_status`
`Config.API_OTA_STATUS = "robots/{id}/ota_status"`
```java
@GET(Config.API_OTA_STATUS)
Call<ResponseBody> getOtaStatus(@Header("Authorization") String auth, @Path("id") String id);
```
`RequestManager.getOtaStatus(robotId, cb)`; view layer `RobotInfoViewModel.getOtaStatus(cb: OtaStatusCallback)`. Response model `OtaStatusModel { @SerializedName("code") String; @SerializedName("percent") Integer; @SerializedName("remaining") String; @SerializedName("status") Robot.OtaStatus; @SerializedName("timestamp") Long }`.

### 6.2 OtaStatus enum (`Robot.OtaStatus`)
`idle, pending, uploading, downloading, flashing, finalizing, complete`

### 6.3 When the app shows OTA
`Robot.getMoxieStatus()`: if `RobotAttributes.otaRequired == true` AND `otaStatus ∉ {idle, complete}` AND `!FullScreenOtaStatusActivity.isSkippedManually` → `MoxieStatus.OTA_IN_PROGRESS`. `needToDisplayOtaStatus()` returns `eStatus == OTA_IN_PROGRESS` and drives `FullScreenOtaStatusActivity` / `MoxieOtaStatusFragment` (`FragmentMoxieOtaStatusBinding`). Copy: `ota_status_title` "Moxie is receiving its first update.", `ota_status_description` (~30 min), `ota_progress_status` "Updating Moxie", `moxie_update_note` "Moxie needs to download an update before turning on!", `moxie_update_desc` (~30 min).

### 6.4 Firmware version fields (on `RobotAttributes`)
- `@SerializedName("robot-firmware-version")` `robotFirmwareVersion`
- `@SerializedName("robot-version")` `robotVersion`
- `@SerializedName("android-version")` `androidVersion`
- `@SerializedName("ota-status")` `Robot.OtaStatus otaStatus`
- `@SerializedName("ota-required")` `Boolean otaRequired` (default false)

No firmware version **strings/constants are hardcoded** in the app (no `24.10.801/803` literals anywhere) — versions are display-only, read from `robot-firmware-version`. The app never enforces or pins a firmware number; it only reacts to `ota-required` + `ota-status`. (The community's 24.10.801/803 significance is a **server/device-side** concern, not encoded in this app.)

### 6.5 Account-level image state
`UserAttributes.@SerializedName("moxie-image-state")` → `Robot.ImageState` enum:
`on, off, not_paired, wake_button, restoring_switch, restoring, restore_failed, not_paired_restore_failed, sleeping` — selects which Moxie face/graphic the app renders per lifecycle state.

---

## 7. LANGUAGE / VOICE

### 7.1 Set-language endpoint — `robots/{id}/set-language`
`Config.API_ROBOT_SET_LANGUAGE = "robots/{id}/set-language"`
```java
@POST(Config.API_ROBOT_SET_LANGUAGE)
Call<ResponseBody> robotSetLanguage(@Header("Authorization") String auth, @Body RobotSetLanguageModel model, @Path("id") String id);
```
Body `RobotSetLanguageModel` (flat JSON, snake_case):
```json
{ "input_language_id": "...", "output_language_id": "...", "output_voice_id": "..." }
```
`RequestManager.robotSetLanguage(model, cb)` (id from `Robot.getData().getId()`).

### 7.2 Available languages/voices — `language-support`
`Config.API_PATH_LANGUAGE_SUPPORT = "language-support"` (fetched via `RequestManager.languageSupport`). Deserializes to `LanguageSupportModel`:
- `@SerializedName("current_input_language_id")`, `current_output_language_id`, `current_output_voice_id`
- `@SerializedName("input_languages")` `List<InputLanguage{ id, title }>`
- `@SerializedName("output_languages")` `List<OutputLanguage{ language, title, voices:List<VoiceItem{ id, title }> }>`

**No language or voice IDs are hardcoded** in the APK (code or resources). The full enumeration is entirely server-driven from `language-support`; the local server must supply the `{id,title}` lists and the current selections. (So there is no static table of e.g. en-US/es-US IDs to extract — you define them.)

---

## 8. ROBOT STATE MODEL

### 8.1 `RobotAttributes` (JSON:API `robots` `attributes`) — every field
| @SerializedName | field | type | meaning |
|---|---|---|---|
| `serial-number` | serialNumber | String | device serial |
| `embodied-robot-id` | embodiedRobotId | String | Embodied internal robot id |
| `is-online` | isOnline | boolean | online flag |
| `last-seen-at` | lastSeen | String (date) | last heartbeat |
| `last-updated-at` | lastUpdated | String | |
| `last-backup-at` | lastBackup | String | backup timestamp |
| `battery-level` | batteryLevel | Float (0.0–1.0, default 0.0) | |
| `mode` | mode | `Robot.Mode` = `idle, active, sleep` | |
| `ota-status` | otaStatus | `Robot.OtaStatus` | see §6.2 |
| `ota-required` | otaRequired | Boolean (default false) | |
| `robot-firmware-version` | robotFirmwareVersion | String | |
| `robot-version` | robotVersion | String | |
| `android-version` | androidVersion | String | |
| `wifi-ssid` | wifiSSID | String | |
| `public-key` | publicKey | String | robot's pairing/crypto pubkey (map 02) |
| `telehealth-supported` | telehealthSupported | Boolean (default false) | |
| `device-settings` | deviceSettings | `DeviceSettingsModel` | feature-flag props (below) |

`DeviceSettingsModel.@SerializedName("props")` → `DeviceSettingsProps` (all String "0"/"1"-style feature flags):
`app-language-support`, `audio-wake`, `debug`, `playzone`, `rewards-support`, `schedule-sensitive`, `touch-wake`, `wake-alarms`, `wake-button`. (App gates UI via `isLanguageSupportEnabled`, `isAudioWakeEnabled`, `isTouchWakeEnabled`, `isWakeButtonEnabled`, `isSchedulePlaydateEnabled` [= props.wakeAlarms=="1"], `isScheduleSensitiveEnabled`, `isRewardsEnabled`, etc.)

### 8.2 `RobotSettingsAttributes` (JSON:API `robot-settings` / `Robot.SETTINGS_TYPE="robot-settings"`)
Updated via `PUT robots/{id}` with body `{ "robot-settings": {…} }` (`UpdateRobotSettingsModel`).
| @SerializedName | field | meaning |
|---|---|---|
| `audio-volume` | audioVolume | Float |
| `screen-brightness` | brightness | Float |
| `audio-wake-set` | audioWakeSensitivity | `Robot.AudioWakeSensitivity` = `off, low, high` |
| `touch-wake-enabled` | touchWakeEnabled | Boolean |
| `wake-button-enabled` | wakeButtonEnabled | Boolean |
| `privacy-mode-enabled` | privacyModeEnabled | Boolean |
| `alarms` | wakeAlarms | `WakeAlarms` (wake schedule) |
| `weekday-bedtime-enabled` / `weekday-bedtime-starts-at` / `weekday-bedtime-ends-at` | | Boolean / time strings |
| `weekend-bedtime-enabled` / `weekend-bedtime-starts-at` / `weekend-bedtime-ends-at` | | Boolean / time strings |

### 8.3 Eye/face color, name — where they live
- **Eye color / face color**: NOT on the robot object. They are account-level capability flags `UserAttributes.@SerializedName("supports-eye-color")` and `supports-face-color`. The actual chosen color maps to a hex via constants in `Robot.java`:
  - `EYE_COLORS`: green `42D02B`, blue `8491EF`, purple `9437DE`, brown `443319`, gold `F4BF03`, teal `38ADAE` (enum `EyeColor{green,blue,purple,brown,gold,teal}`)
  - `FACE_COLORS`: blue `BBCFE1`, yellow `F0F055`, green `9BDB9B`, teal `7ED6DD`, pink `E1A2A2`, purple `C395D4` (enum `FaceColor{blue,yellow,green,teal,pink,purple}`)
  (These are child-customization values, persisted with the child/customization; robot renders them.)
- **Robot name / serial**: serial = `RobotAttributes.serial-number`; a friendly name is not a robot attribute in this model (the app labels by "Moxie" + child). `updateRobot` (`PUT robots/{id}`, body `{ "robot": {…RobotAttributes…} }`, `UpdateRobotModel`) is the generic robot-attribute updater.

### 8.4 `IncludedRobot` wrapper
`{ @SerializedName("id"), @SerializedName("type")="robots", @SerializedName("attributes")=RobotAttributes, @SerializedName("relationships")=RobotRelationships }`. Settings arrive as separate included `robot-settings` resource (`IncludedRobotSettings`), restores as included `restores` (`IncludedRestores`). Fetch include string `ROBOT_INCLUDE = "restore,robot-setting"`; user include `USER_INCLUDE = "mobile-devices,robots.restore,robots.robot-setting,child,identity-verification"`.

### 8.5 App-level status enum — `MoxieStatus` (`main/moxie/MoxieStatus.java`)
`UNPAIRED, PAIRED, RECONNECT, RESTORE_FAILED, RESTORE_IN_PROGRESS, OTA_IN_PROGRESS, OFFLINE`. Computed by `Robot.getMoxieStatus()` (precedence): restore-failed/in-progress → OTA_IN_PROGRESS → (if no data) UNPAIRED → OFFLINE → PAIRED. `Robot.eStatus` defaults `UNPAIRED`.

---

## 9. HEALTH / ONLINE DETECTION & NOTIFICATIONS

### 9.1 Online / offline
`Robot.getMoxieStatus()`: if `!isOnline` AND `now - parse(last-seen-at) > 900000 ms` (**15 minutes**) → `MoxieStatus.OFFLINE`; otherwise still treated as `PAIRED` (grace window). So the offline threshold = **15 min since last-seen-at**. `is-online` boolean is the primary signal; `last-seen-at` is the staleness fallback.

### 9.2 Polling
`BaseActivity` runs a `FetchUserTimer` (`userTimer.startTimer()` in `onResume`, cancel in `onPause`) that periodically re-fetches user+robot (`fetchUserInfo`) — that's how the app keeps `is-online`/battery/status fresh. Robot detail refresh uses `getRobot(id, include="restore,robot-setting")`.

### 9.3 Battery
`RobotAttributes.battery-level` is a Float 0.0–1.0. Thresholds in `Robot.java`:
- `LOW_BATTERY_MEDIUM_THRESHOLD = 0.39f`
- `LOW_BATTERY_MIN_THRESHOLD = 0.19f`
Battery notifications gated by `UserAttributes.@SerializedName("battery-notifications-enabled")`; mission notifications by `mission-notifications-enabled`. Copy: `moxie_mission_notification_desc` ("…when the battery is low or a new mission is assigned.").

### 9.4 Network / speed test flow
- `GET network-tests` (`getNetworkTests`) returns test spec `GetNetworkTestModel` containing `BandwidthTest{ name, download_from, download_cycles, upload_to, upload_size, upload_cycles }` and `AccessTest{ name, address, port, cycles }` + `Environment`.
- App runs downloads/uploads via `downloadTest(@Url)` and `uploadTest(@Url, @Body bytes)` (dynamic URLs from the spec).
- Results POSTed back: `POST network-tests` (`setNetworkTests`) with `TestResult{ result: SetNetworkTestModel{ Access_results:[AccessResult], bandwidth_results:[BandwidthResult{ name, downstream, upstream }], environment } }`.

---

## 10. Quick endpoint reference (this map)

| Action | Method + path | Body | Model |
|---|---|---|---|
| Unpair | `DELETE robots/{id}` | — | — |
| **Factory reset** | `DELETE robots/{id}?rfs=1` | — | — |
| Create restore | `POST robots/{id}/restores` | `{"restore":{"status":"initiated\|declined"}}` | RestoreRobotModel |
| Reboot | `POST robots/{id}/reboot` | — | resp RebootMoxieResponseModel{code,title,body} |
| Wakeup | `POST robots/{id}/wakeup` | — | resp WakeupMoxieResponseModel{code,title,body,error} |
| OTA status | `GET robots/{id}/ota_status` | — | OtaStatusModel{code,percent,remaining,status,timestamp} |
| Get robot | `GET robots/{id}?include=restore,robot-setting` | — | IncludedRobot(+settings,+restores) |
| Update robot attrs | `PUT robots/{id}` | `{"robot":{…RobotAttributes}}` | UpdateRobotModel |
| Update settings | `PUT robots/{id}` | `{"robot-settings":{…}}` | UpdateRobotSettingsModel |
| Set language | `POST robots/{id}/set-language` | `{input_language_id,output_language_id,output_voice_id}` | RobotSetLanguageModel |
| Register pairing | `POST pairing-info?id=&restore=&user-id=&child-id=` | — | (querymap) |
| Language support | `GET language-support` | — | LanguageSupportModel |
| Network test spec | `GET network-tests` | — | GetNetworkTestModel |
| Network test result | `POST network-tests` | TestResult | SetNetworkTestModel |
