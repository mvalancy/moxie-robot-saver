# 📦 Firmware inventory — apps, binaries and files

A complete inventory of **v3.6.4-Zephyr / OTA v24.10.803** (RK3288, Android 9): which apps and native
binaries ship, which are Embodied's, and the whole-image file counts.
- **Counts:** 50 priv-app · 29 app · 334 `/system/bin` entries · 2,250 system files · 507 vendor files.
- **Embodied's layer is 15 APKs + 2 daemons.** Everything else is stock AOSP 9 + Rockchip. It is a
  locked-down image: no Google Play/GMS, and telephony is vestigial from the tablet base.
- The `bo-android` native libraries and the partition hashes are in the [firmware reference](firmware-803-reference.md).

## Embodied apps (the ones that make it a Moxie)

Package, version and signer were decoded from each APK's binary `AndroidManifest.xml` + PKCS#7
(androguard + `openssl`). Machine-readable table: [`manifests/embodied-apps.tsv`](manifests/embodied-apps.tsv).

| App (dir) | package | version | signer | Role |
|---|---|---|---|---|
| **bo-android** | `com.embodied.bo_unity` | **24.10.803** | Android Debug | the brain: conversation/vision/behavior + Unity face |
| **bo-wifi** | `com.embodied.bo_unity_wifi` | **24.6.100** | Android Debug | setup/QR/pairing (Unity, `WifiApp.dll`). Older than the 803 OTA (not rebuilt) |
| **bo-firmwareUpdate** | `me.embodied.bo_firmwareupdate` | `GOBY` (code 7) | Embodied Inc | Lizard-MCU/XMOS firmware DFU (versionName = the **GOBY** bootloader name) |
| **bo_motor_test** | `com.embodied.bo_motor_test` | 1.0 | Embodied Inc | motor bring-up ([native motion API](../hardware/hardware-map.md#native-motion-api-factory-libmotionlib-liblizardjni)) |
| **bo_xmosupdate** | `me.embodied.bo_xmosupdate` | 2.0 | Embodied Inc | XMOS audio-DSP DFU |
| **xmosdfu** | `me.embodied.xmosdfu` | 1.1 | Embodied Inc | XMOS DFU (low-level) |
| **qcapp** | `me.embodied.productiontesting.qc` | 3005004-PP | Embodied Inc | QC utility (factory suite) |
| **OSUpdate** | `com.embodied.osupdate` | 1.1 | **Embodied** | A/B OTA applier ([ota-and-recovery](ota-and-recovery.md)) |
| **Launcher3Robot** | `com.android.launcher3` | 9 | **Embodied** | **stock AOSP Launcher3, re-signed**. Not a custom launcher: the launcher state machine is inside bo-android |
| **BluetoothSpeaker** | `com.embodied.bluetoothtest` | 1.0 | **Embodied** | Bluetooth speaker/test app |
| **BurnInTest** | `me.embodied.productiontesting.burnintest` | 3005004-PP | Embodied Inc | burn-in (factory suite) |
| **FabTestSoftware** | `me.embodied.fab.fabtestsoftware` | 1.0 | Android Debug | board-level fab test |
| **finaltest** | `me.embodied.productiontesting.finaltest` | 3005004-PP (code 3005004) | Embodied Inc | end-of-line test ([catalog](factory-provisioning.md)) |
| **internalassytest** | `me.embodied.productiontesting.internalassytest` | 3005004-PP | Embodied Inc | sub-assembly test |
| **lifetest** | `me.embodied.productiontesting.lifetest` | 3005004-PP | Embodied Inc | life/reliability test |

`OSControl` (in `/system/app`, not priv-app) is Embodied's display-hardware control app.

**Three signing identities, split by trust tier** (fingerprints and implications:
[firmware-image](firmware-image.md#code-signing-app-trust)):
- **`CN=Embodied`** (the release/verified-boot key): `OSUpdate`, `Launcher3Robot`, `BluetoothSpeaker`.
- **`CN=Embodied Inc`**: every factory/service/updater app (`bo-firmwareUpdate`, `bo_motor_test`,
  `bo_xmosupdate`, `xmosdfu`, and the `productiontesting.*` suite, all one build `3005004-PP`).
- **`CN=Android Debug`**: `bo-android`, `bo-wifi` and `FabTestSoftware`. The least-trusted key signs the brain.

**Version skew:** `bo-android` is the only app at the OTA's `24.10.803`; `bo-wifi` lags at `24.6.100`
(the setup surface is 24.6 vintage even on an 803 robot); the factory suite is one `3005004-PP` build.

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

- **`/system/bin`**: 334 entries. Embodied added only **`ledctrld`** and **`projectorfanpid`**. The rest
  is AOSP/Rockchip (`update_engine`, `uncrypt`, `recovery`, `vold`, `surfaceflinger`, toolbox…). See the
  [init service graph](boot-and-launcher.md#init-service-graph-native-daemons) for which run as daemons.
- **`/vendor/bin`**: Rockchip HAL binaries + `rockchip.drmservice`, `rk_store_keybox` (Widevine),
  `tee-supplicant` (OP-TEE), `insmod`/`modprobe`.
- **`bo-android` native libs**: 30 `.so`s, tabled with sizes in the
  [firmware reference](firmware-803-reference.md#bo-android-native-libraries-the-brain-libarmeabi-v7a).

### The on-device ML stack — four frameworks
Moxie runs **four ML runtimes on-device**:
- **Apache MXNet** (`libmxnet`): deep nets with an **Embedding** backward op, i.e. **face-recognition
  embeddings** (the enrollment/user-recognition path in [content-and-conversation](../runtime/content-and-conversation.md#session-sleep-lifecycle)).
- **TensorFlow Lite** + **GPU delegate**: lightweight inference for wake-word (TRILLsson) and VAD.
- **XGBoost** (`libxgb`): gradient-boosted trees for tabular classification/scoring (e.g. recommender/SEL).
- **OpenCV** (`libbsk`): classical Haar/LBP **cascade classifiers** for fast face/object detection.

For custom firmware, a replacement brain must either bundle equivalents or offload perception/ASR to a
server. The MXNet/TFLite weights ship *inside* these `.so`s, not as loose files. The thin interface
libs (`liblizzerface`, `libnative-lib`, `libbo-dispatch`) are the clean seams to keep: they speak the
MCU and ZMQ bus this repo documents.

## File manifest — every file, by the numbers

From read-only loop mounts of the partition images. The full `size⇥path` tables are in
[`manifests/`](manifests/README.md).

| Partition | Files | Size |
|---|--:|--:|
| `system.img` | 2,250 | ~2,085 MB |
| `vendor.img` | 507 | ~1,186 MB |
| `oem.img` | 4 | ~81 MB (boot animation) |

**`/system` by top directory**

| Dir | Size | Notes |
|---|--:|---|
| `priv-app` | **1,527 MB** | privileged apps; **bo-android alone is 962 MB** |
| `lib` | 177 MB | 32-bit shared libs |
| `framework` | 150 MB | AOSP framework (`.jar`/`framework-res.apk`) |
| `app` | 105 MB | regular apps (webview 49 MB, …) |
| `fonts` | 68 MB | incl. NotoSerifCJK (24 MB) |
| `usr` | 26 MB | icu, keychars, share |
| `bin` | 15 MB | 334 native binaries |
| `media` | 9 MB | audio/ui media |

**Largest files (system)**

| Size | Path |
|--:|---|
| 962 MB | `priv-app/bo-android/bo-android.apk` (native ML libs dominate) |
| 144 MB | `priv-app/bo-wifi/bo-wifi.apk` (Unity setup app) |
| 133 MB | `priv-app/FabTestSoftware/FabTestSoftware.apk` |
| 49 MB | `app/webview/webview.apk` |
| 46 MB | `priv-app/Settings/Settings.apk` |
| 39 MB | `framework/framework-res.apk` |
| 28/27/27 MB | `productiontesting.{finaltest,internalassytest,lifetest}` |

**File types (system):** `.so` 673 · `.ogg` 217 · no extension 209 · `.ttf` 196 · `.0` 194 · `.vdex`
120 · `.odex` 105 · `.apk` 80 · `.rc` 49 · `.jar` 43. The `.vdex`/`.odex` files are AOT-compiled app
bytecode (dexpreopt). The 673 `.so` are the native surface (RK3288 HALs + the `bo-*` brain libs). The
217 `.ogg` are system/UI sounds. The 194 `.0` are the CA trust store ([network-trust](../protocol/network-trust.md)).

**Embodied file hashes:** SHA-256 of the Embodied apps/binaries is in
[`manifests/embodied-sha256.tsv`](manifests/embodied-sha256.tsv). Examples: `bo-android.apk`
`04d6aa6745a8e629728cb95819fdb3fd…`, `ledctrld` `36bb1d3e7eb38326e9084fffc8dba384…`. Partition-image
hashes are in the [firmware reference](firmware-803-reference.md#partition-images).

## Observations

- **No GMS / Play Services / Google apps** beyond `CaptivePortalLogin` + `webview`.
- **Telephony is present but vestigial** (`Telecom`, `TelephonyProvider`, `TeleService`, `MmsService`,
  `SimAppDialog`), inherited from the RK3288 tablet base.
- **NFC** (`NfcNci`, `SecureElement`) is present but not part of the Moxie experience.
- The factory apps (`productiontesting.*`, `FabTestSoftware`, `BurnInTest`) ship on **retail units**, a
  standing service/bring-up surface reachable via `Launcher.OnFactoryTestRequest`
  ([boot-and-launcher](boot-and-launcher.md#factory-test-entry-factorytest)).

---
📖 [Firmware reference](firmware-803-reference.md) · [Reverse-engineering index](../README.md) · [Docs index](../../README.md)
