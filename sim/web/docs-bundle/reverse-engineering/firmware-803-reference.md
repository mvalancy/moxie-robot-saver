# 📇 Firmware reference — Moxie `v3.6.4-Zephyr` (OTA `v24.10.803`)

> **This document describes ONE specific firmware build** — the exact partition images analyzed in
> this repo. Every fact below is measured from these images. A different build may differ; the
> fingerprints in the identity block let you confirm you have this one.

## Version identity

| Field | Value |
|---|---|
| Embodied version | **`v3.6.4-Zephyr`** (`sys.embodied.version`) |
| OTA version | **`v3.6.4-24_12_28-18_42-master-a90cfdee72-v24.10.803-rls-robot`** (`sys.embodied.otaver`) |
| Marketing "gen" | **803** (post-Google / AWS-endpoint era) |
| Build date | **2024-12-28 20:12:02 UTC** (`ro.build.date`, `.utc=1735416722`) |
| Build fingerprint | `rockchip/rk3288/rk3288:9/PQ2A.190305.002/cloud12282012:user/release-keys` |
| Build incremental | `201231` · **`ro.build.type=user`** · `release-keys` |
| Build type / cert | `sys.embodied.buildtype=customer` · `sys.embodied.certtype=release` |
| Hardware type | `sys.embodied.hwtype=robot` |
| Build host | `ro.build.host=e0d7b92d7a34` · `ro.build.user=cloud` |
| Git commit (fw) | `a90cfdee72` (from the otaver string) |

## Platform

| | |
|---|---|
| SoC | **Rockchip RK3288** (`ro.board.platform=rk3288`, `ro.product.board=rk30sdk`) |
| CPU | ARMv7 32-bit, Cortex-A15 (`armeabi-v7a,armeabi`; `dalvik.vm.isa.arm.variant=cortex-a15`) |
| Android | **9 (Pie)**, API 28, security patch **2019-04-05** |
| Rockchip SDK | `RK30_ANDROID9-SDK-v1.00.00` (`ro.rksdk.version`) |
| Boot model | A/B seamless (`ro.build.ab_update=true`), system-as-root (`ro.build.system_root_image=true`) |
| Verified boot | AVB 1.1 (`avbtool 1.1.0`), `androidboot.veritymode=enforcing` |
| Security | `ro.secure=1`, `ro.adb.secure=1`, `ro.debuggable=0`, **`ro.oem_unlock_supported=1`** |
| Data | `/data` f2fs, `forceencrypt=/cache/key_file` |
| Camera / display | OV2710 (`sys.embodied.camhw=ov2710`, V4L2); DLP projector (DLPC3430) |

## Partition images (this build)

Full set from `moxie-prod-partitions_ESpktu.zip`. **SHA-256** identifies the exact image:

| Partition | Size (bytes) | SHA-256 |
|---|--:|---|
| `boot.img` | 67,108,864 | `ecd70e23e66d958051018ff700292b7b9ee9fb42239829beee9d18dc17b66e9b` |
| `vbmeta.img` | 4,096 | `c020bc051469ed24c4a8f34c1ca9828645f724f875c2c8bfd5fff88aada496cc` |
| `dtbo.img` | 4,194,304 | `1d145a63a4cd460789ab4e93ab9f97cc3eed8acf3aaca9ed7093c0fe2faf9328` |
| `uboot.img` | 4,194,304 | `420ce457f58b66d6041019cab986cb87b32669b82f396f6b6b20c37e2afe00f9` |
| `trust.img` | 4,194,304 | `a48f6753b0e8d1239b43ba6cc47d1683be9fe0d153e17cbefe33de29f83d1219` |
| `oem.img` | 402,653,184 | (ext4; `/oem/media/bootanimation.zip` + fs_config) |
| `system.img` | 3,648,389,120 | (ext4/ext2, UUID `c6f93bf6-9ff0-54b0-b187-144c06b8eb19`) |
| `vendor.img` | 5,085,593,600 | (ext4; Rockchip HALs + `fstab.rk30board` + hw init) |

### boot.img kernel cmdline (verbatim)
```
console=ttyFIQ0 androidboot.baseband=N/A androidboot.wificountrycode=US
androidboot.veritymode=enforcing androidboot.hardware=rk30board
androidboot.console=ttyFIQ0 firmware_class.path=/vendor/etc/firmware
init=/init rootwait ro init=/init buildvariant=user
```
Kernel load `0x10008000`, ramdisk `0x11000000`, page size 2048. Kernel: RK3288 Android-9 (Linux 4.4-class Rockchip BSP; version string is compressed inside the kernel image).

### fstab (`/vendor/etc/fstab.rk30board`)
```
/dev/block/by-name/system    /     ext4  ro,barrier=1                  wait,avb,slotselect
/dev/block/by-name/userdata  /data f2fs  noatime,discard,inline_xattr  wait,check,notrim,forceencrypt=/cache/key_file,quota,reservedsize=128M
```

## Installed apps (this build)

### Embodied apps (`/system/priv-app`)
| App | Package | Role |
|---|---|---|
| **bo-android** | *(the brain)* | conversation/vision/behavior + Unity face; 29 native libs (below) |
| **bo-wifi** | *(setup/Unity)* | QR scan, Wi-Fi, pairing (`WifiApp.dll`) |
| bo-firmwareUpdate | | MCU/XMOS DFU |
| bo_motor_test, bo_xmosupdate, xmosdfu, qcapp | | factory/service utilities |
| OSControl, OSUpdate | `com.embodied.osupdate` | display-hw control; A/B OTA applier |
| Launcher3Robot | | custom home/launcher |
| me.embodied.productiontesting.{finaltest,internalassytest,lifetest}, FabTestSoftware | | factory line; **finaltest VERSION_NAME `3005004-PP` (code 3005004)** |

### bo-android native libraries (the "brain", `lib/armeabi-v7a/`)
Sizes are from this build's APK:

| Library | Size | Purpose |
|---|--:|---|
| `libbo-audio.so` | 184.7 MB | audio: STT, TTS glue, XMOS |
| `libbo-brain.so` | 154.3 MB | ChatScript + ML conversation brain |
| `libbo-analytics.so` | 93.5 MB | analytics |
| `libbo-vision.so` | 91.8 MB | computer vision (faces/people/QR) |
| `libwatchdog.so` | 71.6 MB | watchdog/health |
| `libbo-logger.so` | 63.2 MB | logging + **MQTT (Paho) + ServiceConfiguration/EndpointStore** |
| `libmxnet.so` | 48.7 MB | MXNet ML inference |
| `libcerevoice_eng.so` | 44.4 MB | **CereProc CereVoice TTS** (local) |
| `libunity.so` | 43.5 MB | Unity engine (face render) |
| `libbo-fusion.so` | 40.7 MB | perception fusion |
| `libbo-system-monitor.so` | 35.5 MB | system monitor |
| `libchatscript.so` | 26.7 MB | ChatScript engine |
| `libbsk.so` | 22.7 MB | behavior SDK |
| `libdevset.so` | 20.7 MB | device settings |
| `libbo-dispatch.so` | 8.6 MB | **ZeroMQ message bus** (`ZMQEventBroadcaster`) |
| `libtensorflowlite(_gpu).so` | 2.5 / 6.4 MB | TFLite inference |
| `libxgb.so` | 0.9 MB | XGBoost |
| `libzbar.so` | 0.5 MB | ZBar QR/barcode |
| `liblizzerface.so` | ~30 KB | **Lizard MCU** UART bridge — the `Lizzerface` native layer for motors/sensors/power/LED ([hardware-map](hardware-map.md#raw-uart-command-set-lizzerfacecommands)), NOT a face renderer |
| `librobinface.so` | ~30 KB | companion native lib (unconfirmed; paired with `liblizzerface`) |
| (+ `libmonobdwgc-2.0.so`, `libMonoPosixHelper.so`, `libiconv.so`, `librfc.so`, `libusb.so`, `libev.so`, `libmain.so`, `libnative-lib.so`, `libc++_shared.so`) | | Mono runtime + support |

### Managed assemblies (cleartext .NET IL)
- `bo-android`: `Assembly-CSharp.dll` (4.4 MB), `Embodied.Protos.dll` (1.7 MB), `bo-unity-core.dll`.
- `bo-wifi`: `WifiApp.dll`, `WifiApp.Protos.dll`, `WifiApp.Plugins.dll`.
These carry the embedded protobuf `FileDescriptor`s → the [120 recovered `.proto` files](recovered-proto/).

## Key properties (`build.prop` / `prop.default`)
```
sys.embodied.otaver   = v3.6.4-24_12_28-18_42-master-a90cfdee72-v24.10.803-rls-robot
sys.embodied.version  = v3.6.4-Zephyr        sys.embodied.hwtype   = robot
sys.embodied.buildtype= customer             sys.embodied.certtype = release
sys.embodied.qrsetup  = 0                     sys.embodied.auto_unity = 0
sys.embodied.camtype  = v4l2  camhw = ov2710  displayhw = unknown
sys.embodied.boot_reason = normal            sys.embodied.boot_error = 0
sys.embodied.quiet_boot_done = 0             sys.embodied.wifi.freq_pref = 0
persist.sys.disable_rescue = 1               persist.sys.usb.config = mtp,adb (vendor) / none (boot)
ro.oem_unlock_supported = 1                  ro.board.platform = rk3288
ro.rksdk.version = RK30_ANDROID9-SDK-v1.00.00
ro.build.fingerprint = rockchip/rk3288/rk3288:9/PQ2A.190305.002/cloud12282012:user/release-keys
```
> `boot_reason`/`boot_error`/`quiet_boot_done` are **runtime boot-state** flags the launcher sets
> (normal vs recovery/factory boot, and a boot-fault code) — useful telemetry when diagnosing a robot
> that won't come up. `wifi.freq_pref` is the band preference (0 = auto; pairs with the QR `band_select`).


## Embodied daemons & hardware hooks (init)
- `ledctrld` (`/system/bin/ledctrld`) → **PCA963x** I²C LEDs (`/sys/class/leds/pca963x:{red,green,blue}_N`).
- `projectorfanpid` → DLP projector PID fan controller.
- DLP face: **TI DLPC3430** at I²C `5-001b` (`led_out`/`rgb_out`/`brightness_alt`/`temperature`).
- `/dev/panel_name` selects `sys.embodied.displayhw`.

## What's notable in THIS build
- **`OPEN_MOXIE` endpoint is built in** (`{"endpoint":"openmoxie"}`, `DEFAULT_ENDPOINT_NAME` in `libbo-logger`) — first-class community-server support (see [`cloud-protocol.md`](cloud-protocol.md)).
- **`ServiceConfiguration.disable_verify`** present (maps to `CURLOPT_SSL_VERIFYPEER=0`) — see [`network-trust.md`](network-trust.md).
- Post-Google/AWS era: `client-service-*-api.embodied.com` REST + Paho MQTT + Deepgram STT.

## Deep dives (all measured from this build unless noted)
[`firmware-image.md`](firmware-image.md) · [`firmware-inventory.md`](firmware-inventory.md) · [`boot-and-launcher.md`](boot-and-launcher.md) ·
[`ota-and-recovery.md`](ota-and-recovery.md) · [`cloud-protocol.md`](cloud-protocol.md) ·
[`network-trust.md`](network-trust.md) · [`robot-ipc-protocol.md`](robot-ipc-protocol.md) ·
[`hardware-map.md`](hardware-map.md) · [`perception-pipeline.md`](perception-pipeline.md) ·
[`behavior-markup.md`](behavior-markup.md) · [`content-and-conversation.md`](content-and-conversation.md) ·
[`factory-provisioning.md`](factory-provisioning.md) · [`qr-commands.md`](qr-commands.md)

---
📖 [Reverse-engineering index](README.md) · [Field guide](FIELD-GUIDE.md) · [Docs index](../README.md)
