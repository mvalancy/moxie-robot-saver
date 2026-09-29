# 📇 Firmware reference — Moxie `v3.6.4-Zephyr` (OTA `v24.10.803`)

This page pins the **one firmware build** every robot-side doc in this repo describes. Every fact here
was measured from that build's partition images. Use the identity block and SHA-256s to check that a
robot or image set is this exact build. It is also the canonical home of the partition table, kernel
cmdline, fstab, key properties and the `bo-android` native-library table. Other docs link here and do
not repeat them.

## Version identity

| Field | Value |
|---|---|
| Embodied version | **`v3.6.4-Zephyr`** (`sys.embodied.version`) |
| OTA version | **`v3.6.4-24_12_28-18_42-master-a90cfdee72-v24.10.803-rls-robot`** (`sys.embodied.otaver`) |
| Marketing "gen" | **803** (post-Google / AWS-endpoint era, the generation OpenMoxie targets) |
| Build date | **2024-12-28 20:12:02 UTC** (`ro.build.date`, `.utc=1735416722`) |
| Build fingerprint | `rockchip/rk3288/rk3288:9/PQ2A.190305.002/cloud12282012:user/release-keys` |
| Build incremental | `201231` · **`ro.build.type=user`** · `release-keys` |
| Build type / cert | `sys.embodied.buildtype=customer` (vs `eng`) · `sys.embodied.certtype=release` (vs `dev`) |
| Hardware type | `sys.embodied.hwtype=robot` |
| Build host | `ro.build.host=e0d7b92d7a34` · `ro.build.user=cloud` |
| Git commit (fw) | `a90cfdee72` (from the otaver string) |

## Platform

| | |
|---|---|
| SoC | **Rockchip RK3288** (`ro.board.platform=rk3288`, `ro.product.board=rk30sdk`); board DTB `rk3288-robot-gen1p5` |
| CPU | ARMv7 32-bit (`armeabi-v7a,armeabi`; `dalvik.vm.isa.arm.variant=cortex-a15`) |
| Android | **9 (Pie)**, API 28, security patch **2019-04-05** |
| Rockchip SDK | `RK30_ANDROID9-SDK-v1.00.00` (`ro.rksdk.version`); an unmodified RK3288 Android-9 BSP with the `bo-*` apps on top |
| Boot model | A/B seamless (`ro.build.ab_update=true`), system-as-root (`ro.build.system_root_image=true`) |
| Verified boot | AVB 1.1 (`avbtool 1.1.0`), `androidboot.veritymode=enforcing` |
| Security | `ro.secure=1`, `ro.adb.secure=1`, `ro.debuggable=0`, **`ro.oem_unlock_supported=1`** |
| Data | `/data` f2fs, `forceencrypt=/cache/key_file` |
| Camera / display | OV2710 (`sys.embodied.camhw=ov2710`, V4L2); DLP projector (TI DLPC3430) |

## Partition images

Full set from `moxie-prod-partitions_ESpktu.zip`. The **SHA-256** identifies the exact image.

| Partition | Size (bytes) | SHA-256 / contents |
|---|--:|---|
| `uboot.img` | 4,194,304 (4 MiB) | `420ce457f58b66d6041019cab986cb87b32669b82f396f6b6b20c37e2afe00f9` — Rockchip SPL + U-Boot |
| `trust.img` | 4,194,304 (4 MiB) | `a48f6753b0e8d1239b43ba6cc47d1683be9fe0d153e17cbefe33de29f83d1219` — OP-TEE secure world (`ro.tee.storage=rkss`) |
| `dtbo.img` | 4,194,304 (4 MiB) | `1d145a63a4cd460789ab4e93ab9f97cc3eed8acf3aaca9ed7093c0fe2faf9328` — overlay; an empty 72-byte stub |
| `vbmeta.img` | 4,096 (4 KiB) | `c020bc051469ed24c4a8f34c1ca9828645f724f875c2c8bfd5fff88aada496cc` — signed AVB hashtree descriptors for system/vendor |
| `boot.img` | 67,108,864 (64 MiB) | `ecd70e23e66d958051018ff700292b7b9ee9fb42239829beee9d18dc17b66e9b` — kernel + ramdisk (recovery-as-boot) |
| `oem.img` | 402,653,184 (384 MiB) | ext4: `/oem/media/bootanimation.zip` (84 MB), `fs_config_files`/`fs_config_dirs`, `/oem/etc/package_performance.xml` |
| `system.img` | 3,648,389,120 (3.4 GiB) | ext4/ext2, UUID `c6f93bf6-9ff0-54b0-b187-144c06b8eb19`; Android `/` incl. all `bo-*` apps |
| `vendor.img` | 5,085,593,600 (4.7 GiB) | ext4: Rockchip HALs, `fstab.rk30board`, hw init `.rc`, firmware blobs |

The on-eMMC `by-name` partition list (with A/B suffixes) is in
[hardware-access](../hardware/hardware-access.md#partition-table-flash-targets). File counts per image are in
[firmware-inventory](firmware-inventory.md#file-manifest-every-file-by-the-numbers).

### boot.img kernel cmdline (verbatim)
```
console=ttyFIQ0 androidboot.baseband=N/A androidboot.wificountrycode=US
androidboot.veritymode=enforcing androidboot.hardware=rk30board
androidboot.console=ttyFIQ0 firmware_class.path=/vendor/etc/firmware
init=/init rootwait ro init=/init buildvariant=user
```
Kernel load `0x10008000`, ramdisk `0x11000000`, page size 2048. Kernel: RK3288 Android-9 (Linux
4.4-class Rockchip BSP; the version string is compressed inside the kernel image). No
`androidboot.selinux=permissive`.

### fstab (`/vendor/etc/fstab.rk30board`)
```
/dev/block/by-name/system    /                    ext4  ro,barrier=1                     wait,avb,slotselect
/dev/block/by-name/cache     /cache               ext4  noatime,nosuid,nodev,discard     wait,check
/dev/block/by-name/metadata  /mnt/vendor/metadata ext4  noatime,nosuid,nodev,discard     wait
/dev/block/by-name/misc      /misc                emmc  defaults                         defaults
/dev/block/by-name/userdata  /data                f2fs  noatime,discard,inline_xattr     wait,check,notrim,forceencrypt=/cache/key_file,quota,reservedsize=128M
```
`wait,avb,slotselect` on `/` means A/B slot selection plus AVB hashtree verification. There is no
`fs_mgr` `verify` with an on-device key. Integrity is enforced entirely by `vbmeta`/AVB.

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
ro.oem_unlock_supported = 1                  ro.board.platform = rk3288 / ro.product.board = rk30sdk
ro.rksdk.version = RK30_ANDROID9-SDK-v1.00.00
ro.build.fingerprint = rockchip/rk3288/rk3288:9/PQ2A.190305.002/cloud12282012:user/release-keys
```

| Property | Meaning |
|---|---|
| `buildtype` / `certtype` | flip `customer`↔`eng` and `release`↔`dev`; factory/eng builds relax constraints |
| `qrsetup` / `auto_unity` | QR-only setup-mode toggle; whether the Unity experience auto-launches |
| `boot_reason` / `boot_error` / `quiet_boot_done` | runtime boot-state flags the launcher sets (normal vs recovery/factory boot, a boot-fault code, quiet-boot done). Useful when diagnosing a robot that won't come up |
| `wifi.freq_pref` | Wi-Fi band preference (0 = auto); pairs with the QR `band_select` |

## bo-android native libraries (the "brain", `lib/armeabi-v7a/`)

`bo-android.apk` (962 MB) ships **30 native `.so`s**. These are the modules a custom brain would replace
or reuse. Sizes are decimal MB from this build's APK. Embedded ML models inflate several of them. Full
roster with per-module findings: [native-boundary](../runtime/native-boundary.md#the-full-module-roster-what-each-remaining-bo-so-actually-is).

| Library | Size | Role |
|---|--:|---|
| `libbo-audio.so` | 184.7 MB | audio pipeline: STT/Kaldi models, DSP, AEC, TTS glue, XMOS. The heaviest lib |
| `libbo-brain.so` | 154.3 MB | ChatScript + ML conversation brain; protobuf (`embodied.logging.CloudQueryResponse`) |
| `libbo-analytics.so` | 93.5 MB | on-device analytics / media analysis |
| `libbo-vision.so` | 91.8 MB | computer vision (faces/people/pose/QR) |
| `libwatchdog.so` | 71.6 MB | watchdog/health (bundles models/assets) |
| `libbo-logger.so` | 63.2 MB | logging/telemetry buffering + **MQTT (Paho) + `ServiceConfiguration`/`EndpointStore`** |
| `libmxnet.so` | 48.7 MB | **Apache MXNet** runtime (`_backward_Embedding` → face embeddings) |
| `libcerevoice_eng.so` | 44.4 MB | **CereProc CereVoice** DNN TTS (local) |
| `libunity.so` | 43.5 MB | Unity engine (face render) |
| `libbo-fusion.so` | 40.7 MB | sensor/perception fusion |
| `libbo-system-monitor.so` | 35.5 MB | `BoSystemMonitor` health metrics ([cloud-protocol](../protocol/cloud-protocol.md)) |
| `libchatscript.so` | 26.7 MB | **ChatScript** offline dialog ([content-and-conversation](../runtime/content-and-conversation.md)) |
| `libbsk.so` | 22.7 MB | "bsk" module; contains **OpenCV** `cv::CascadeClassifier` (Haar/LBP detection) |
| `libdevset.so` | 20.7 MB | device settings / provisioning |
| `libbo-dispatch.so` | 8.6 MB | **ZeroMQ message bus** (`ZMQEventBroadcaster`, [robot-ipc-protocol](../protocol/robot-ipc-protocol.md)) |
| `libtensorflowlite.so` / `libtensorflowlite_gpu_delegate.so` | 2.5 / 6.4 MB | **TensorFlow Lite** (+ GPU delegate): wake-word / VAD ([perception-pipeline](../runtime/perception-pipeline.md)) |
| `libxgb.so` | 0.9 MB | **XGBoost** gradient-boosted trees |
| `librfc.so` | 0.6 MB | classifier helper (random-forest class) |
| `libzbar.so` | 0.5 MB | **ZBar** QR/barcode decode |
| `libusb.so` | 317 KB | libusb (XMOS DFU) |
| `libnative-lib.so` | 102 KB | `lizardPktAssembler` (MCU DFU packetizer) |
| `liblizzerface.so` | ~30 KB | **Lizard MCU** UART bridge: motors/sensors/power/LED ([hardware-map](../hardware/hardware-map.md#raw-uart-command-set-lizzerfacecommands)). Not a face renderer |
| `librobinface.so` | ~30 KB | `LEDA_*` LED-array face driver over I²C + GPIO ([native-boundary](../runtime/native-boundary.md#librobinfaceso-the-physical-led-face-driver)) |
| `libmonobdwgc-2.0.so`, `libMonoPosixHelper.so`, `libmain.so`, `libc++_shared.so`, `libev.so`, `libiconv.so` | — | Mono/Unity runtime + support |

The four on-device ML frameworks (MXNet, TFLite, XGBoost, OpenCV) are summarized in
[firmware-inventory](firmware-inventory.md#the-on-device-ml-stack-four-frameworks).

### Managed assemblies (cleartext .NET IL)
- `bo-android`: `Assembly-CSharp.dll` (4.4 MB), `Embodied.Protos.dll` (1.7 MB), `bo-unity-core.dll`.
- `bo-wifi`: `WifiApp.dll`, `WifiApp.Protos.dll`, `WifiApp.Plugins.dll`.

These carry the embedded protobuf `FileDescriptor`s that yield the
[120 recovered `.proto` files](../protocol/recovered-proto/).

## Embodied daemons and hardware hooks (init)
- `ledctrld` (`/system/bin/ledctrld`) drives the **PCA963x** (DTB: PCA9635) I²C LEDs,
  `/sys/class/leds/pca963x:{red_1..6, green_1..5, blue_1..5}`.
- `projectorfanpid` is the DLP projector PID fan controller, toggled via `/sdcard/scripts.config`.
- DLP face: **TI DLPC3430** at I²C `5-001b` (`led_out`/`rgb_out`/`brightness_alt`/`temperature`).
- `/dev/panel_name` selects `sys.embodied.displayhw`.

Service details: [boot-and-launcher](boot-and-launcher.md#init-service-graph-native-daemons). SELinux domains: [security-policy](security-policy.md).

## What's notable in this build
- **`OPEN_MOXIE` endpoint is built in** (`{"endpoint":"openmoxie"}`, `DEFAULT_ENDPOINT_NAME` in `libbo-logger`), so community servers are first-class ([cloud-protocol](../protocol/cloud-protocol.md)).
- **`ServiceConfiguration.disable_verify`** is present and maps to `CURLOPT_SSL_VERIFYPEER=0` ([network-trust](../protocol/network-trust.md)).
- Post-Google/AWS era: `client-service-*-api.embodied.com` REST + Paho MQTT + Deepgram STT.
- Version skew: only `bo-android` is at `24.10.803`; `bo-wifi` is `24.6.100` ([firmware-inventory](firmware-inventory.md)).

## Deep dives
[firmware-image](firmware-image.md) · [firmware-inventory](firmware-inventory.md) · [boot-and-launcher](boot-and-launcher.md) ·
[ota-and-recovery](ota-and-recovery.md) · [cloud-protocol](../protocol/cloud-protocol.md) ·
[network-trust](../protocol/network-trust.md) · [robot-ipc-protocol](../protocol/robot-ipc-protocol.md) ·
[hardware-map](../hardware/hardware-map.md) · [perception-pipeline](../runtime/perception-pipeline.md) ·
[behavior-markup](../runtime/behavior-markup.md) · [content-and-conversation](../runtime/content-and-conversation.md) ·
[factory-provisioning](factory-provisioning.md) · [qr-commands](../protocol/qr-commands.md)

---
📖 [Reverse-engineering index](../README.md) · [Field guide](../FIELD-GUIDE.md) · [Docs index](../../README.md)
