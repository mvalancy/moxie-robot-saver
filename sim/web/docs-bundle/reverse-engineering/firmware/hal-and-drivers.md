# 🧩 Vendor HALs, kernel drivers & co-processor firmware — Moxie `v3.6.4-Zephyr` (OTA `v24.10.803`)

The layer between the apps and the silicon in **v24.10.803** (`vendor.img` + `system.img`): the
Android **HAL** set, how kernel drivers are delivered, and the **firmware blobs** for the co-processors
and radio. It answers what a custom kernel/vendor image must provide for the hardware to come up.
- **Every HAL is stock AOSP or Rockchip; there is no Embodied HAL.** Custom hardware (RGB LEDs,
  projector fan, XMOS mic/speaker) is driven from **userspace**: the `ledctrld`/`projectorfanpid`
  daemons plus direct `/dev` access from the privileged brain ([security-policy](security-policy.md)).
  A custom build keeps the whole HAL/kernel layer and swaps the apps.
- **No loadable `.ko` modules:** the RK3288 kernel builds its drivers in-tree.
- **The XMOS voice-DSP firmware ships in the image** (`xmosdfu.bin` + a VAD variant), re-flashable by DFU.

## 1. HAL interfaces declared (VINTF `manifest.xml`)

`/vendor/etc/vintf/manifest.xml` (4,040 bytes) declares the vendor HAL implementations the framework
may bind. **21 HAL interfaces**, all standard:

| HAL interface | Ver | Notable for Moxie |
|---|---|---|
| `android.hardware.audio` (+ `audio.effect`) | 4.0 | audio path; **`audio.usb.default`** is how the **XMOS** USB-audio device is reached (mic array + speaker) |
| `android.hardware.soundtrigger` | 2.0 | hotword HAL is *present*, but Moxie's wake-word/VAD actually runs in the **XMOS DSP + `libbo-audio`**, not this HAL (see [`perception-pipeline.md`](../runtime/perception-pipeline.md#wake-word-vad-fully-on-device)) |
| `android.hardware.camera.provider` | 2.4 | OV2710 via `camera.rk30board` |
| `android.hardware.light` | 2.0 | present, but Moxie's RGB status LEDs go through the **`ledctrld`** daemon (PCA963x), not the light HAL |
| `android.hardware.boot` | 1.0 | A/B slot control (`bootctrl.rk30board`) — the OTA applier's seam |
| `android.hardware.keymaster` (3.0) + `gatekeeper` (1.0) | | hardware-backed keystore / lockscreen auth (`keystore.rk30board`) |
| `android.hardware.drm` + `cas` | 1.0 | **clearkey only** (`drm@1.1-service.clearkey`) — no Widevine L1 |
| `android.hardware.graphics.{allocator,mapper,composer}` | 2.0/2.1 | `gralloc.rk30board`, `hwcomposer.rk30board`, **`vulkan.rk3399.so`** (GPU) |
| `android.hardware.power` | 1.0 | `power.rk3288` |
| `android.hardware.health` | 2.0 | battery/charge |
| `android.hardware.media.omx` | 1.0 | hardware video codecs (Rockchip VPU) |
| `android.hardware.wifi` (+ `supplicant`, `hostapd`) | 1.0 | BCM4339; `hostapd` present (SoftAP capable) |
| `android.hardware.bluetooth` | 1.0 | BCM4339 BT |
| `android.hardware.configstore` | 1.1 | SurfaceFlinger config |

> **No `vendor.embodied.*` or custom `IEmbodied*` HAL exists.** Confirmed by grepping the manifest and
> `/vendor/lib/hw`. The only embodied-specific kernel-facing code is the two userspace daemons.

## 2. HAL implementations & services

**`/vendor/lib/hw`** (impl `.so`, loaded in-process) — 32-bit only (`ro.product.cpu.abi=armeabi-v7a`;
there is no `lib64/hw`):

```
gralloc.rk30board.so  hwcomposer.rk30board.so  vulkan.rk3399.so  gralloc.default.so
camera.rk30board.so   android.hardware.camera.provider@2.4-impl.so
audio.primary.default.so  audio.usb.default.so  audio.r_submix.default.so
android.hardware.audio@4.0-impl.so  android.hardware.audio.effect@4.0-impl.so
android.hardware.soundtrigger@2.0-impl.so  android.hardware.light@2.0-impl.so
power.rk3288.so  power.default.so  vibrator.default.so  local_time.default.so
bootctrl.rk30board.so  android.hardware.boot@1.0-impl.so
keystore.rk30board.so  gatekeeper.rk30board.so  android.hardware.{gatekeeper@1.0,keymaster@3.0}-impl.so
android.hardware.drm@1.0-impl.so  android.hardware.bluetooth@1.0-impl.so
android.hardware.graphics.{allocator@2.0,mapper@2.0,composer@2.1}-impl.so
```

**`/vendor/bin/hw`** (standalone hwservices, started by init): `android.hardware.audio@2.0-service`,
`…bluetooth@1.0-service`, `…boot@1.0-service`, `…camera.provider@2.4-service`, `…cas@1.0-service`,
`…configstore@1.1-service`, `…drm@1.0-service` + `…drm@1.1-service.clearkey`, `…gatekeeper@1.0-service`,
`…graphics.allocator@2.0-service`, `…graphics.composer@2.1-service`, `…health@2.0-service`,
`…keymaster@3.0-service`, `…light@2.0-service`, `…media.omx@1.0-service`, `…power@1.0-service`,
`…wifi@1.0-service`, plus **`hostapd`** and **`wpa_supplicant`**. (Full init service graph:
[`boot-and-launcher.md`](boot-and-launcher.md).)

## 3. Kernel drivers

No `.ko` modules exist in `vendor.img` (or elsewhere) — the **RK3288 Android-9 kernel (Linux 4.4-class
Rockchip BSP) builds its drivers in-tree**. Practical consequences for a custom build:

- A custom kernel must **compile in** the RK3288 drivers (MIPI-DSI/DLP display, I²C, USB, V4L2 for
  OV2710, the RK808/RK818 PMIC, SDIO Wi-Fi, etc.) — you can't just drop modules on the vendor image.
- Drivers pull their firmware from **`/vendor/etc/firmware`** (`firmware_class.path` on the kernel
  cmdline — see [`firmware-803-reference.md`](firmware-803-reference.md#bootimg-kernel-cmdline-verbatim)).
- The display panel is selected at runtime via `/dev/panel_name` (`emb_chardev_device`, sets
  `sys.embodied.displayhw`).

## 4. Co-processor & radio firmware blobs (`/vendor/etc/firmware`)

### XMOS voice DSP — the "second processor" (re-flashable, in the image)

The XMOS front-end (AEC / beamforming / VAD / DOA — [`perception-pipeline.md`](../runtime/perception-pipeline.md))
is a separate chip flashed over **DFU** by the XMOS updater (`bo_xmosupdate` / `xmosdfu`). Two selectable
images ship, with plain-text version files the updater compares against the running DSP:

| Blob | Version file | Version | Size (bytes) | SHA-256 |
|---|---|--:|--:|---|
| `xmosdfu.bin` | `xmosdfu_version.txt` | **5** | 425,472 | `cf73668294989a943d0abc882f7a025a5c9efd03bd052815d33c97356cb31f55` |
| `xmosdfu-vad.bin` | `xmosdfu-vad_version.txt` | **6** | 375,040 | `36d62b0c66720d135333fb29980b1f011ec1b45c9f2c18f53f7685e68d0922fb` |
| `otp.bin.z77` | — | — | 4,058 | (compressed OTP blob) |

> The **`-vad`** variant is a distinct DSP image with on-chip **voice-activity detection** — direct
> evidence that VAD runs on the XMOS silicon (not the SoC), matching the perception-pipeline finding.
> The blobs begin with a DFU-style header (`ed 15 ff 00 …`), carry no ASCII banner, and are the exact
> artifacts you'd re-flash to restore or rebuild the audio front-end.

### Radio (BCM4339 / AP6335)

The module is the **AmPak AP6335 (Broadcom BCM4339)** combo, selected in the DTB by
`wifi_chip_type="ap6335"` ([device-tree](../hardware/device-tree.md#connectivity-wi-fi-bluetooth)). The
Wi-Fi firmware is host-loaded over SDIO and BT over UART0; nothing is flashed into the module. The exact
files a custom build must ship:

| File | Use |
|---|---|
| `fw_bcm4339a0_ag.bin` | Wi-Fi STA (the `_ag`, a/g-band family) |
| `fw_bcm4339a0_ag_apsta.bin` | STA + SoftAP |
| `fw_bcm4339a0_ag_p2p.bin` | Wi-Fi Direct |
| `nvram_AP6335.txt` | AP6335 module calibration/NVRAM |
| `bcm4339a0.hcd` (57,291 bytes) | BT patchram |

The directory also carries the generic Rockchip-SDK grab-bag (~40 blobs for other Wi-Fi/BT parts:
`fw_bcm43…`, `fw_RK903…`, `RT2870*`, `ssv6051`, Realtek `8723`/`8188`… + `nvram_*.txt`/`*.hcd`), and
`/vendor/firmware/*.rkl` (RK1608 pre-ISP, OV2718/IMX327). None of these are used on this board.

## 5. What this means

For custom firmware, keep the HAL/kernel layer as-is: the RK3288 in-tree drivers, the `/vendor/lib/hw` +
`/vendor/bin/hw` HALs, and the blobs above. The XMOS images let the audio co-processor be re-flashed
independently of Android. The work is in the app layer and, optionally, the two daemons. Nothing here
affects server revival or the no-open question ([ota-and-recovery](ota-and-recovery.md)).

---
📖 [Reverse-engineering index](../README.md) · [Field guide](../FIELD-GUIDE.md) · [Hardware map](../hardware/hardware-map.md) · [Perception pipeline](../runtime/perception-pipeline.md) · [Firmware reference](firmware-803-reference.md)
