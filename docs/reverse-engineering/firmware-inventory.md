# 📦 Firmware inventory — apps & binaries

> Complete manifest of the executables in **v3.6.4-Zephyr / OTA v24.10.803** (RK3288, Android 9).
> Companion to the [firmware reference](firmware-803-reference.md). Counts: **50 priv-app · 29 app ·
> 334 `/system/bin` entries**. Embodied-specific items are called out; the rest is stock AOSP 9 +
> Rockchip (kept minimal — no Google Play/GMS, telephony is vestigial from the tablet base).

## Embodied apps (the ones that make it a Moxie)

Package, version, and signer decoded from each APK's binary `AndroidManifest.xml` + PKCS#7 (androguard
+ `openssl`). Full machine-readable table: [`manifests/embodied-apps.tsv`](manifests/embodied-apps.tsv).

| App (dir) | package | version | signer | Role |
|---|---|---|---|---|
| **bo-android** | `com.embodied.bo_unity` | **24.10.803** | Android Debug | the brain — conversation/vision/behavior + Unity face ([reference](firmware-803-reference.md)) |
| **bo-wifi** | `com.embodied.bo_unity_wifi` | **24.6.100** | Android Debug | setup/QR/pairing (Unity) — *older than the 803 OTA (not rebuilt)* |
| **bo-firmwareUpdate** | `me.embodied.bo_firmwareupdate` | `GOBY` (code 7) | Embodied Inc | Lizard-MCU firmware DFU (versionName = the **GOBY** bootloader name) |
| **bo_motor_test** | `com.embodied.bo_motor_test` | 1.0 | Embodied Inc | motor bring-up utility ([native motion API](hardware-map.md#native-motion-api-factory-libmotionlib--liblizardjni)) |
| **bo_xmosupdate** | `me.embodied.bo_xmosupdate` | 2.0 | Embodied Inc | XMOS audio-DSP DFU |
| **xmosdfu** | `me.embodied.xmosdfu` | 1.1 | Embodied Inc | XMOS DFU (low-level) |
| **qcapp** | `me.embodied.productiontesting.qc` | 3005004-PP | Embodied Inc | QC utility (factory suite) |
| **OSUpdate** | `com.embodied.osupdate` | 1.1 | **Embodied** | A/B OTA applier |
| **Launcher3Robot** | `com.android.launcher3` | 9 | **Embodied** | **stock AOSP Launcher3, re-signed** — *not* a custom launcher (the launcher state machine is inside bo-android) |
| **BluetoothSpeaker** | `com.embodied.bluetoothtest` | 1.0 | **Embodied** | Bluetooth speaker/test app |
| **BurnInTest** | `me.embodied.productiontesting.burnintest` | 3005004-PP | Embodied Inc | burn-in (factory suite) |
| **FabTestSoftware** | `me.embodied.fab.fabtestsoftware` | 1.0 | Android Debug | board-level fab test |
| **finaltest** | `me.embodied.productiontesting.finaltest` | 3005004-PP | Embodied Inc | end-of-line test ([catalog](factory-provisioning.md)) |
| **internalassytest** | `me.embodied.productiontesting.internalassytest` | 3005004-PP | Embodied Inc | sub-assembly test |
| **lifetest** | `me.embodied.productiontesting.lifetest` | 3005004-PP | Embodied Inc | life/reliability test |

**Three signing identities, split by trust tier** (completes the [firmware-image](firmware-image.md#code-signing--app-trust) table with full per-app assignment):
- **`CN=Embodied`** (the release/verified-boot key): `OSUpdate`, `Launcher3Robot`, `BluetoothSpeaker` — the most-trusted core system apps.
- **`CN=Embodied Inc`**: every factory/service/updater app (`bo-firmwareUpdate`, `bo_motor_test`, `bo_xmosupdate`, `xmosdfu`, and the whole `productiontesting.*` suite — all one build `3005004-PP`).
- **`CN=Android Debug`**: the two main experience apps (`bo-android`, `bo-wifi`) **and** `FabTestSoftware` — notably the *least*-trusted key signs the brain (see [firmware-image.md](firmware-image.md#code-signing--app-trust) for why that still yields full privileges via priv-app placement).

**Version-skew note:** `bo-android` is the only app at the OTA's `24.10.803`; `bo-wifi` lags at `24.6.100` (setup surface is the 24.6 vintage); the factory suite is one unified `3005004-PP` build.

## Full priv-app list (50)

`BackupRestoreConfirmation BlockedNumberProvider BluetoothSpeaker` **`bo-android bo-firmwareUpdate
bo_motor_test bo-wifi bo_xmosupdate BurnInTest`** `CalendarProvider ContactsProvider
CtsShimPrivPrebuilt DefaultContainerService DownloadProvider DownloadProviderUi
ExternalStorageProvider ExtServices` **`FabTestSoftware`** `FusedLocation InputDevices` **`Launcher3Robot`**
`ManagedProvisioning MediaProvider` **`me.embodied.productiontesting.{finaltest,internalassytest,lifetest}`**
`MmsService MtpDocumentsProvider MusicFX OneTimeInitializer` **`OSUpdate`** `PackageInstaller Provision
ProxyHandler` **`qcapp`** `Settings SettingsIntelligence SettingsProvider SharedStorageBackup Shell
StatementService StorageManager SystemUI Telecom TelephonyProvider TeleService UserDictionaryProvider
VpnDialogs WallpaperCropper` **`xmosdfu`**

## Full app list (29)

`BasicDreams Bluetooth BluetoothMidiService BuiltInPrintService Camera2 CaptivePortalLogin
CertInstaller CompanionDeviceManager CtsShimPrebuilt EasterEgg ExtShared HTMLViewer KeyChain LatinIME
LiveWallpapersPicker NfcNci` **`OSControl`** `PacProcessor PhotoTable PrintRecommendationService
PrintSpooler SecureElement SimAppDialog SoundRecorder Traceur WallpaperBackup WallpaperPicker
WAPPushManager webview`

## Native binaries

- **`/system/bin`** — 334 entries. Embodied-added: **`ledctrld`**, **`projectorfanpid`** (everything
  else is AOSP/Rockchip: `update_engine`, `uncrypt`, `recovery`, `vold`, `surfaceflinger`, toolbox…).
  See the [init service graph](boot-and-launcher.md) for which run as daemons.
- **`/vendor/bin`** — Rockchip HAL binaries + `rockchip.drmservice`, `rk_store_keybox` (Widevine),
  `tee-supplicant` (OP-TEE), `insmod`/`modprobe`.

## Observations

- **No GMS / Play Services / Google apps** beyond `CaptivePortalLogin` + `webview` — a locked-down
  appliance image.
- **Telephony stack present but vestigial** (`Telecom`, `TelephonyProvider`, `TeleService`,
  `MmsService`, `SimAppDialog`) — inherited from the RK3288 tablet base, unused by the robot.
- **NFC** (`NfcNci`, `SecureElement`) present but not part of the Moxie experience.
- The factory apps (`productiontesting.*`, `FabTestSoftware`, `BurnInTest`) ship on **retail units** —
  a standing service/bring-up surface (reachable via `Launcher.OnFactoryTestRequest`, see
  [boot-and-launcher](boot-and-launcher.md)).

---
📖 [Firmware reference](firmware-803-reference.md) · [Reverse-engineering index](README.md) · [Docs index](../README.md)
