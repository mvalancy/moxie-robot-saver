# Pairing, Wi-Fi Provisioning & Robot Control (parent app)

**Summary.** The phone provisions the robot **only through a QR code shown on its screen**. There is no BLE, no SoftAP,
and no local socket, and the phone never talks to the robot directly. The phone (1) registers
`hex(SHA-256(seed))` with `POST pairing-info`, (2) shows a `PA`+protobuf QR holding Wi-Fi credentials and the raw 32-byte seed,
and (3) polls `GET users/me` every 2 s (up to 60 times) until a `robots` relationship appears. It then calls
`GET robots/{id}`. After pairing, the phone controls the robot entirely through cloud REST (`robots/{id}/…`). This
page is canonical for the pairing sequence, Wi-Fi rules, robot control bodies, and restore flows. It links to
[`qr-format.md`](qr-format.md) for the QR bytes, [`crypto-and-keys.md`](crypto-and-keys.md) for keys, and
[`rest-api.md`](rest-api.md) for base URLs, auth, and the full endpoint list. Our implementation lives in
[`server/moxie_server/routes/pairing.py`](../../../server/moxie_server/routes/pairing.py) and
[`routes/robots.py`](../../../server/moxie_server/routes/robots.py).

> Clean-room notes on the original app (`EmbodiedParentApp/v2.2.2`). Class names and `File.java:NN` line numbers point
> into that app's decompiled sources. They are not files in this repo.

---

## 1. Pairing modes and entry points

`PairQRMode` is always `PAIR_PROTO_KEY` in v2.2.2, so the legacy JSON/`user_token` QR is dead code. See
[`qr-format.md`](qr-format.md#mode-selection). The switch that matters is **`PairingMode { WIFI_ONLY, WIFI_AND_PAIRING }`**
(intent extra `pairing_mode`):

- `WIFI_AND_PAIRING` is a full pairing, for a new robot or a re-pair.
- `WIFI_ONLY` is "Edit Wi-Fi" for a robot that is already paired. The QR carries only Wi-Fi and `hide_pair`, with no key or token, and
  `pairing-info` is not called.

Entry point: `BaseActivity.switchToPairMoxieActivity(PairingMode, boolean checkRestoreFromBackup)` (line 441).

| Caller | Mode | checkBackup |
|---|---|---|
| `MoxieFragment:392` ("Pair Moxie" main button) | WIFI_AND_PAIRING | `true` |
| `MoxieFragment:493` / `:531` ("Edit WiFi" / restore-cancel) | WIFI_ONLY / WIFI_AND_PAIRING | `false` |
| `MoxieFragment:514` (restore "try again" after `createRestore`) | WIFI_AND_PAIRING | `false` |
| `TroubleshootDialog:53`, `BaseActivity:872` | WIFI_ONLY | `false` |

---

## 2. `iot-endpoint`

`UserAttributes.java:37-38` declares `@SerializedName("iot-endpoint") Integer iotEndpoint`. It is a small integer the server
assigns on the **user** record. The app never interprets it. It copies the value into QR field 8, or `0` when the value is absent.
It is the only `iot` string in the whole app: a grep for `mqtt|websocket|graphql|greengrass|amazonaws|broker|pubsub`
finds nothing else. The app is 100% Retrofit/OkHttp REST plus Firebase (FCM, Crashlytics, Analytics) plus ExoPlayer.

The robot side resolves the value. Field 8 is the firmware's **`IOTEndpoint` enum**
(`IOT_DEFAULT=0` … `EMBODIED_PRODUCTION=6`, `EMBODIED_HIPAA=7`, `EMBODIED_LOCAL=8`, `EMBODIED_CHINA=9`, `EMBODIED_HK=10`,
`OPEN_MOXIE=11`). The robot maps the value to hosts it already knows, and re-homes if the value differs from its current endpoint.
See [`qr-commands.md`](../protocol/qr-commands.md#pairing-qr-pa-startpairingqr) and the enum in [`proto-catalog.md`](../protocol/proto-catalog.md). This channel
cannot carry a hostname. It can only pick one of the endpoints the firmware already knows.

---

## 3. The pairing sequence

```
 PHONE (app)                       CLOUD (client-service-api)                ROBOT
 [MoxieFragment] "Pair Moxie"
 PairMoxieActivity  (pairing_mode=WIFI_AND_PAIRING, check_restore_from_backup=true)
      | if user.attributes["has-backups"] && checkBackup
 RestoreMoxieFragment (Yes/No) -> Config.userWantsToRestoreFromBackup
 PairMoxieWifiFragment  (SSID / password / hidden / band -> static WifiNetworkInfo)
      | goToPairing(): PROTO && mode != WIFI_ONLY
   POST /api/pairing-info?id=<sha256hex(seed)>&restore=&user-id=&child-id=
                                   ------> pending pairing record keyed by hash
                                   <------ 200/201   (failure: Retry|Skip dialog)
 PairInstructionFragment (step 2/5 — "look for QR box on Moxie's face")
 PairMoxieQrCodeFragment (step 3/5)
   QR = "PA"+base64(proto{ssid,pw,secret_key,...,iot_endpoint}); FLAG_KEEP_SCREEN_ON
   AttemptCounter(max=60, delay=2000ms)                          camera scans QR
      |                                                          joins Wi-Fi
      |                            <------ robot registers, proving the seed;
      |                                    cloud matches sha256 -> binds robot to user+child
      |-- every 2 s: GET /api/users/me?include=…robots…
      |                            <------ relationships.robots[] appears
   GET /api/robots/{id}?include=restore,robot-setting
   pairingState = success -> GET /api/robots/{id}/ota_status
        OTA_IN_PROGRESS -> MoxieOtaStatusFragment, else SetupSpaceFragment / MoxieConnectedFragment
```

**A. Restore gate** (`PairMoxieActivity.initViews`, lines 46-67). The gate shows when the mode is `WIFI_AND_PAIRING`, the user
has `has-backups == true`, and `checkBackup` is set. `RestoreMoxieFragment` is a plain Yes/No screen with no crypto and no
network. `onRestore()`/`onSkip()` set `Config.userWantsToRestoreFromBackup` to true/false, then call `showPairingWifiPage()`.

**B. Wi-Fi entry** (`PairMoxieWifiFragment.setupWifiNetworkInfo`, lines 256-267). Validation: an empty password shows the
*"Are you sure your network has no password?"* dialog (`network_no_password_title`). A password of 1–7 characters is rejected with *"The password should be
at least 8 characters or longer."* (`MINIMUM_PASSWORD_LENGTH = 8`). SSID auto-fill (`Utils.getConnectedWifiSSID()`) needs
`ACCESS_FINE_LOCATION` and the network location provider (`checkLocationSettings`). Field rules are in §4.

**C. `registerForPairing`** (`goToPairing`, lines 270-303, logs *"First time proto pairing registration"*). This runs only in proto
mode and not for `WIFI_ONLY`:

```java
RequestManager.INSTANCE.registerForPairing(
    ProtoPairing.serectHashFromKey(CryptoHelper.getInstance().getSigningKey().toBytes()),   // sha256 hex
    Config.userWantsToRestoreFromBackup,
    User.INSTANCE.getData().getId(),
    User.INSTANCE.getData().getRelationships().getChild().getData().getId(),
    responseCallback);
```

`RequestManager.registerForPairing` (lines 580-596) puts `id`, `restore` (`String.valueOf` gives `"true"`/`"false"`), `user-id`, and
`child-id` into a `@QueryMap` for `APIService.pairingInfo(@Header("Authorization"), @QueryMap)`. Because it is a QueryMap, **these are
query parameters and the body is empty**:

```
POST /api/pairing-info?id=<64 hex>&restore=false&user-id=<uuid>&child-id=<uuid> HTTP/1.1
Authorization: Bearer <access_token>
User-Agent: EmbodiedParentApp/v2.2.2 android/<ver>
Content-Length: 0
```

Success is HTTP 200/201/204, and the app moves to the instructions page. Failure shows `register_pairing_error_title`
*"Problem connecting to Embodied"* / `register_pairing_error_desc` *"It's possible that your network is down, or
Embodied's servers may be having trouble."* with Retry and Skip buttons. **Skip still continues to the QR**, so the robot's own callback
carries enough information without this pre-registration.

**D. QR and polling** (`showPairingView`, lines 193-213). The QR is built as in [`qr-format.md`](qr-format.md). The poll uses
`AttemptCounter` (`utils/AttemptCounter.java`, `DEFAULT_DELAY_MSEC = 2000`) with `setAttemptMaxCount(60)`, so it **polls every 2 s for about 120 s**.
After that, `fail()` sets `pairingState = unknown` and shows `PairingErrorDialog`. A "Having trouble?" button appears after
`TROUBLE_DELAY_MSEC = 10000`. Polling pauses in `onPause()` and resumes in `onResume()`. A tick is skipped when
`pairingState == pairing`.

**E. The poll** is `checkUserInfoForRobotPairing` → `fetchUserInfo` →
`GET /api/users/me?include=mobile-devices,robots.restore,robots.robot-setting,child,identity-verification`.

**F. Success detection** (`PairMoxieQrCodeFragment$3.onSuccess`, lines 224-307):

- *WIFI_ONLY* (lines 270-286): the robot's `wifi-ssid` must equal the SSID the user typed, **and** `last-seen-at` must be
  no more than 300000 ms (5 min, `WIFI_VALIDITY_TIMEOUT`) old. The app logs *"success on edit WIFI"*. In non-proto mode it
  calls `updateKeysInServer`. So `robots/{id}` `wifi-ssid` and `last-seen-at` are liveness signals the server must maintain.
- *WIFI_AND_PAIRING* (lines 287-301): success means **`users/me` now returns a non-empty `relationships.robots.data[]`**
  (*"success on pairing Moxie"*). In proto mode, the app logs *"Requesting Robot to complete Pairing in PROTO mode."* and calls
  `GET robots/{id}` on the first robot. That GET probably signals the backend that the phone has seen the pairing. In JSON mode
  it calls `updateKeysInServer` instead. Otherwise it sets `pairingState = unknown` and polls again.

**G. JSON-mode-only tail (dead in v2.2.2).** `updateKeysInServer` calls `CryptoManager.updateKeys`, which sends `PUT secret-key-collection`. For
`WIFI_AND_PAIRING`, the app then sends `POST robots/{id}/restores` (`createRestore`) and `GET robots/{id}`. In proto mode neither step happens here.
The restore intent already went out as `restore=` on `pairing-info`, and the key went out inside the QR.

**H. After success** (`finish()` → `showFinalSetupPagesIfNeeded()`). `RobotInfoViewModel.getOtaStatus` stores the status. If
`Robot.needToDisplayOtaStatus()`, the app opens the OTA page; otherwise it runs the next step. **Bug:** `runnable.run()` is also called
unconditionally after the async call, so the callback path can fire twice. After that come `SetupSpaceFragment` (on WIFI_AND_PAIRING success) or
`PairingCallback.onFinish(success)` / `switchToMainActivity()`.

**Error UI.** `PairingErrorDialog` offers `editWifi()` (pops 2 fragments back to Wi-Fi entry), `instruction()` (pops 1),
`finishSetup()`, `tryAgain()` (resets the counter and resumes polling), and `needHelp()` (opens `Config.URL_TROUBLESHOOTING_QR_PAIRING`).
`PairingHavingTroubleDialog` sets the transient `incorrectSSID`/`incorrectPassword` flags, which only drive inline error text and
are never sent. `ConnectingTipsAndTricksDialog` shows connection tips.

---

## 4. Wi-Fi provisioning — what the robot needs

Model: `api/models/wifi/WifiNetworkInfo.java`. The wire encodings are in [`qr-format.md`](qr-format.md).

| Field | Type | JSON key | Proto field | Constraint |
|---|---|---|---|---|
| SSID | String | `ssid` | 1 | Required. The Next button is disabled while it is empty. Trimmed and **case sensitive** (`wifi_ssid_note`: *"Note: SSID/Wi-Fi Name is Case Sensitive"*). |
| Password | String | `password` | 2 | Trimmed. Empty is allowed after the confirmation dialog; otherwise at least 8 characters. WPA-PSK assumed. |
| Hidden | boolean | `is_hidden` | 6 | Proto emits it only when `true`. |
| Band | `WifiBand` | `band_select` | 7 | `ANY` (default, omitted), `ONLY_50G` → 1, `ONLY_24G` → 2 |

The UI strings are `band_any` "Any", `band_only_50g` "Only 5.0GHz", and `band_only_24g` "Only 2.4GHz", from
`R.array.wireless_frequency_selector` under a collapsed "Advanced Wi-Fi Settings" section (`advanced_wifi_settings`), with default
index 0. The app puts **no** limits on band. Band is only a hint to the robot for dual-band routers that share one SSID. The Wi-Fi model has **no** field for security type, EAP identity, static IP, DNS,
proxy, or country code, and nothing supports WPA-Enterprise or captive portals. The robot-side view is in
[`qr-commands.md`](../protocol/qr-commands.md#wi-fi-provisioning-support-what-networks-work).

---

## 5. Robot control API

All calls use the base URL and `Authorization` header from [`rest-api.md` §1](rest-api.md#1-transport-base-urls-headers-client-credentials).
The endpoint list is in [`rest-api.md` §3.4](rest-api.md#34-robot-pairing).

### 5.1 Endpoints (`APIService` method names)

| Method | Path | `APIService` | Body |
|---|---|---|---|
| GET | `robots/{id}?include=restore,robot-setting` | `getRobot` | — (`ROBOT_INCLUDE = "restore,robot-setting"`) |
| PUT | `robots/{id}` | `updateRobot` | `UpdateRobotModel { "robot": RobotAttributes }`. It is **PUT, not PATCH**. |
| PUT | `robots/{id}` | `updateRobotSettings` | `UpdateRobotSettingsModel { "robot-settings": RobotSettingsAttributes }` |
| DELETE | `robots/{id}` | `unpairRobot` | — |
| DELETE | `robots/{id}?rfs=1` | `unpairRobotWithRestoreFactory` | — (unpair + factory reset) |
| POST | `robots/{id}/wakeup` | `wakeupMoxie` | — |
| POST | `robots/{id}/reboot` | `rebootRobot` | — |
| GET | `robots/{id}/ota_status` | `getOtaStatus` | — |
| POST | `robots/{id}/set-language` | `robotSetLanguage` | `RobotSetLanguageModel` |
| POST | `robots/{id}/restores` | `createRestore` | `RestoreRobotModel` (§6.1) |
| POST | `pairing-info` | `pairingInfo` | query only (§3 step C) |
| GET / POST | `network-tests` | `getNetworkTests` / `setNetworkTests` | §5.8 |
| GET | `help/language-support` | `helpApi("language-support")` | — |

### 5.2 `GET robots/{id}` response (JSON:API)

`RobotDataModel { data: IncludedRobot, included: [IncludedModel] }`, where
`IncludedRobot { id, type: "robots", attributes: RobotAttributes, relationships: { "robot-setting", "restore" } }`.

`RobotAttributes` (`api/models/robot/RobotAttributes.java`):

| Key | Type | Notes |
|---|---|---|
| `android-version` | String | |
| `battery-level` | Float | default 0.0 |
| `device-settings` | `{ "props": {…} }` | **feature-flag map** (all String): `app-language-support`, `audio-wake`, `debug`, `playzone`, `rewards-support`, `schedule-sensitive`, `touch-wake`, `wake-alarms`, `wake-button`. It gates whole sections of the UI. |
| `embodied-robot-id` | String | |
| `is-online` | boolean | |
| `last-backup-at`, `last-updated-at` | String (timestamp) | |
| `last-seen-at` | String (timestamp) | pairing liveness check |
| `mode` | enum | `idle`, `active`, `sleep` |
| `ota-required` | Boolean | default false |
| `ota-status` | enum | `idle`, `pending`, `uploading`, `downloading`, `flashing`, `finalizing`, `complete` |
| `public-key` | String | base64 X25519 public key (the target of the sealed `secret-key-collection` entry) |
| `robot-firmware-version`, `robot-version`, `serial-number` | String | |
| `telehealth-supported` | Boolean | default false |
| `wifi-ssid` | String | pairing liveness check |

`included[]` entries of type `robot-settings` carry `RobotSettingsAttributes`:

```
"audio-volume" Float        "audio-wake-set" enum {off, low, high}     "screen-brightness" Float
"privacy-mode-enabled" Boolean   "touch-wake-enabled" Boolean   "wake-button-enabled" Boolean
"alarms" { "enabled": Boolean, "wakes": [ { "enabled": Boolean, "days": [int], "time": "HH:mm" } ] }
"weekday-bedtime-enabled" Boolean  "weekday-bedtime-starts-at" "HH:mm"  "weekday-bedtime-ends-at" "HH:mm"
"weekend-bedtime-enabled" Boolean  "weekend-bedtime-starts-at" "HH:mm"  "weekend-bedtime-ends-at" "HH:mm"
```

Entries of type `restores` carry `RestoreAttributes { "status": String, "created-at": String, "restore-type": enum }`.

### 5.3 `PUT robots/{id}`

The wrapper key picks the variant. `{"robot": {…any RobotAttributes…}}` is `updateRobot`.
`{"robot-settings": {"audio-volume": 0.7, "screen-brightness": 0.5, "privacy-mode-enabled": false, …}}` is `updateRobotSettings`
(`@SerializedName(Robot.SETTINGS_TYPE)` = `"robot-settings"`). The response is a `RobotDataModel`, which is passed back into
`RobotInfoViewModel.updateData()`.

### 5.4 / 5.5 `wakeup` and `reboot`

Neither sends a request body. `wakeup` returns `WakeupMoxieResponseModel {code, title, body, error}` (from
`RobotInfoViewModel.wakeupRobotRequest(WakeUpMoxieCallback)`). The app shows `title`/`body` in a dialog. A non-null `error` means
failure **even on HTTP 200**, which suggests the device did not acknowledge. `reboot` returns `RebootMoxieResponseModel {code, title, body}`.
A phone HTTP call cannot reach a robot behind NAT, so the cloud must forward these over the robot's persistent MQTT connection.
See [`cloud-protocol.md`](../protocol/cloud-protocol.md#2-mqtt-the-live-bus-eclipse-paho-mutual-tls).

### 5.6 `GET robots/{id}/ota_status`

```json
{ "status": "idle|pending|uploading|downloading|flashing|finalizing|complete",
  "percent": 0, "remaining": "…human readable…", "code": "...", "timestamp": 1690000000 }
```

`percent` ranges 0-100. `Robot.setOtaStatus()` builds a UI string from this response. `needToDisplayOtaStatus()` is true only for `OTA_IN_PROGRESS`, which requires
`ota-required == true` **and** `ota-status ∉ {idle, complete}` (`Robot.getMoxieStatus()`, lines 229-256).

### 5.7 `POST robots/{id}/set-language`

`{"input_language_id": "...", "output_language_id": "...", "output_voice_id": "..."}`. Unlike the dashed JSON:API attributes, these keys are
**snake_case**. The candidate ids come from `GET help/language-support` → `LanguageSupportModel` (`InputLanguage` /
`OutputLanguage` / `VoiceItem`). The robot id comes from `Robot.INSTANCE.getData().getId()`. The robot must read the values back
from the server.

### 5.8 `network-tests`

`GET network-tests` returns `GetNetworkTestModel`:

```json
{ "access_tests":    [ { "name": "...", "address": "host", "port": 443, "cycles": 3 } ],
  "bandwidth_tests": [ { "name": "...", "download_from": "https://…", "download_cycles": 3,
                         "upload_to": "https://…", "upload_cycles": 3, "upload_size": 1048576 } ] }
```

`POST network-tests` sends a `TestResult` body and gets back `TestResultResponseModel {code, id, message, title}`:

```json
{ "result": {
    "Access_results":    [ { "name": "...", "ping_success": 1, "ping_time": 12.3 } ],
    "bandwidth_results": [ { "name": "...", "downstream": 24.5, "upstream": 3.1 } ],
    "environment":       { "wifi_ssid": "...", "wifi_band": "...", "bearer": "..." } } }
```

The capital `A` in `"Access_results"` is literal. The **phone** runs these tests (`main/account/help/NetworkTest.java`), using a raw
`Socket` for access tests and `@GET/@POST @Url` for bandwidth tests. The host list comes entirely from the server, so a replacement can
return an empty list or its own hosts. Warning string `network_test_connect_wifi_warning`: *"Your phone is currently not connected to
Wi-Fi. Please make sure to use the same network as Moxie."*

---

## 6. Restore flows

Two different things are both called "restore".

### 6.1 Data restore (cloud backup of the child's data)

This is the flag set by `RestoreMoxieFragment` (§3 A). The flag reaches the server in one of two ways:

- **Proto mode:** as `restore=true|false` on `POST pairing-info`. The app creates no separate restore record.
- **JSON mode / retry:** `RequestManager.createRestore(robotId, flag, cb)` sends `POST robots/{id}/restores` with
  `{"restore":{"status":"initiated"}}` or `{"restore":{"status":"declined"}}`.

Enums: `Robot.RestoreStatus = { initiated, declined, failed, succeeded }` and
`Robot.RestoreType = { switch_child, new_child, restore, pairing }`. Progress arrives through the `restores` include.
`Robot.getRestoreStatus()` maps `failed` to `MoxieStatus.RESTORE_FAILED` and `initiated` to `RESTORE_IN_PROGRESS`. The retry path is
`MoxieFragment.onRestoreTryAgainClicked` → `BaseActivity.createRestoreRequest()` → `POST robots/{id}/restores` →
`fetchUserInfo()` → `PairMoxieActivity(WIFI_AND_PAIRING, false)`.

### 6.2 Key restore (recovery passphrase)

The recovery passphrase regenerates the same seed and is checked on the phone against `users/me` `public-key`. Details are in
[crypto §4c](crypto-and-keys.md#4c-export-enter-and-silent-restore). A restored key produces the same SHA-256 the cloud already
knows. "Continue without recovery key" produces a new key, which means pairing again.

### 6.3 First pairing vs. restore vs. edit Wi-Fi

| | First pairing | Restore-from-backup | Edit Wi-Fi |
|---|---|---|---|
| `PairingMode` | WIFI_AND_PAIRING | WIFI_AND_PAIRING | WIFI_ONLY |
| `RestoreMoxieFragment` shown | only if has-backups | **yes** | no |
| `userWantsToRestoreFromBackup` | false | **true** | n/a |
| `pairing-info?restore=` | `false` | **`true`** | *not called* |
| QR contents | ssid+pw+**secret_key**+iot | same | ssid+pw+**hide_pair=1** |
| Success criterion | `users/me` gains `robots[]` | same | `wifi-ssid` matches **and** `last-seen-at` ≤ 5 min |
| Signing key | new or restored from passphrase | usually restored (hash matches) | unchanged |

### 6.4 Unpair

`BaseActivity.unpairMoxie()` shows two choices. **Unpair** sends `DELETE robots/{id}`. **Restore factory settings** (`restore_factory_settings`, shown in red)
sends `DELETE robots/{id}?rfs=1`. Afterwards `RobotInfoViewModel.unpairRobotRequest()` clears `Robot.INSTANCE`.

---

## 7. What the robot must do once it is on Wi-Fi

The app never speaks to the robot, but the app's behaviour constrains the robot and the cloud:

1. **The robot registers itself.** The QR gives it only Wi-Fi, the seed, and an `IOTEndpoint`. The cloud must accept a registration that
   proves the seed and match it against the `pairing-info` hash. On the robot side this is `UserPairingRequest`, and the robot also
   registers its RSA device key. See [`cloud-protocol.md`](../protocol/cloud-protocol.md#robot-authentication-device-identity).
2. **The robot must be able to set the robot-record state the app reads:** `is-online`, `last-seen-at`, `wifi-ssid`,
   `battery-level`, `mode`, `ota-status`, `ota-required`, `public-key`, `serial-number`, `robot-firmware-version`,
   `android-version`, `embodied-robot-id`, `device-settings.props`.
3. **The robot must publish an X25519 `public-key`.** Without it the sealed `secret-key-collection` entry for the robot cannot exist.
4. `wakeup`/`reboot` go over MQTT (§5.4). OTA runs over a separate channel that the robot fetches from (see [`ota-and-recovery.md`](../firmware/ota-and-recovery.md)).
   The robot reads language and voice back from the server.

The minimum server surface that makes pairing *appear* to succeed is in [`rest-api.md` §5](rest-api.md#5-minimum-server-surface-for-a-pairable-session).

## 8. What this APK cannot tell you

- The robot-side protocol, the endpoint table, and how the robot proves the seed. These come from the firmware
  ([`cloud-protocol.md`](../protocol/cloud-protocol.md), [`qr-commands.md`](../protocol/qr-commands.md)).
- The actual `network-tests` hosts. The server supplies them at runtime.
- Whether a given robot firmware still accepts the legacy JSON QR. It needs no key material, just a token from your server, so
  it is cheap to try on old firmware.

---

## 9. File map (inside the original app)

```
pair_moxie/
  PairMoxieActivity.java          entry; intent extras pairing_mode / check_restore_from_backup
  RestoreMoxieFragment.java       Yes/No backup-restore prompt (75 lines, no crypto/network)
  PairMoxieWifiFragment.java      Wi-Fi form, band spinner, registerForPairing(), 8-char rule
  PairInstructionFragment.java    step 2/5 instructions
  PairMoxieQrCodeFragment.java    QR generation + 2 s x60 poll + success detection   <-- core
  MoxieConnectedFragment.java     post-success UI only; MoxieConnectedProFragment = clinician variant
  JSONPairing.java / ProtoPairing.java   legacy / current QR builders (+ serectHashFromKey)
  PairingMode.java / WifiBand.java / PairingCallback.java (onFinish(boolean))
  PairingErrorDialog.java, PairingHavingTroubleDialog.java, ConnectingTipsAndTricksDialog.java
api/
  Config.java, APIService.java, RequestManager.java, ResponseManager.java, CryptoManager.java
  crypto/{CryptoHelper,RecoveryKey,SealBox,SecretBox,diceware/Passphrase}.java
  models/wifi/{WifiNetworkInfo,PairingInfo,PairingModel}.java
  models/robot/*.java (RobotAttributes, OtaStatusModel, Wakeup/RebootMoxieResponseModel, …)
  models/network_tests/*.java; models/Robot.java (enums + MoxieStatus from is-online / last-seen-at)
recovery_key/{Enter,Export}RecoveryKeyFragment.java
viewmodel/RobotInfoViewModel.java (wakeup / ota_status / settings), viewmodel/UserInfoViewModel.java (the poll)
utils/AttemptCounter.java         2000 ms x N polling primitive
```

---
📖 [Phone-side index](README.md) · [QR format](qr-format.md) · [REST API](rest-api.md) · [Crypto & keys](crypto-and-keys.md) · [Reverse-engineering index](../README.md)
