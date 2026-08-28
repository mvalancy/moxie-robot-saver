# 📦 Firmware inventory — apps & binaries

> Complete manifest of the executables in **v3.6.4-Zephyr / OTA v24.10.803** (RK3288, Android 9).
> Companion to the [firmware reference](firmware-803-reference.md). Counts: **50 priv-app · 29 app ·
> 334 `/system/bin` entries**. Embodied-specific items are called out; the rest is stock AOSP 9 +
> Rockchip (kept minimal — no Google Play/GMS, telephony is vestigial from the tablet base).

## Embodied apps (the ones that make it a Moxie)

| App | Location | Role |
|---|---|---|
| **bo-android** | priv-app | the brain — conversation/vision/behavior + Unity face ([reference](firmware-803-reference.md)) |
| **bo-wifi** | priv-app | setup/QR/pairing (Unity) |
| **bo-firmwareUpdate** | priv-app | Lizard-MCU firmware DFU |
| **bo_motor_test** | priv-app | motor bring-up utility |
| **bo_xmosupdate** / **xmosdfu** | priv-app | XMOS audio-DSP DFU |
| **qcapp** | priv-app | QC utility |
| **OSUpdate** | priv-app | A/B OTA applier (`com.embodied.osupdate`) |
| **OSControl** | app | display-hardware control |
| **Launcher3Robot** | priv-app | custom home/launcher |
| **BurnInTest** | priv-app | burn-in |
| **FabTestSoftware** | priv-app | board-level fab test |
| **me.embodied.productiontesting.finaltest** | priv-app | end-of-line test ([catalog](factory-provisioning.md)) |
| **me.embodied.productiontesting.internalassytest** | priv-app | sub-assembly test |
| **me.embodied.productiontesting.lifetest** | priv-app | life/reliability test |

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
