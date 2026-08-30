# 🧩 Vendor HALs, kernel drivers & co-processor firmware — Moxie `v3.6.4-Zephyr` (OTA `v24.10.803`)

> Measured from the **v24.10.803** `vendor.img` + `system.img` (RK3288, Android 9). This is the layer
> *below* the apps and *above* the silicon: the Android **HAL** set the platform exposes, how the
> kernel drivers are delivered, and the **binary firmware blobs** for the co-processors and radio.
> It answers a custom-firmware question directly: **what must a custom kernel/vendor image provide for
> the robot's hardware to come up?** Pairs with [`hardware-map.md`](../hardware/hardware-map.md) (the silicon),
> [`perception-pipeline.md`](../runtime/perception-pipeline.md) (what the XMOS DSP does),
> [`security-policy.md`](security-policy.md) (why there's no custom HAL) and
> [`fcc-teardown.md`](../hardware/fcc-teardown.md) (the radio module).

## TL;DR

- **Every Android HAL is stock AOSP or Rockchip — there is NO embodied-authored HAL.** The custom
  hardware (RGB LEDs, projector fan, XMOS mic/speaker) is driven from **userspace**: the `ledctrld`
  and `projectorfanpid` daemons + direct `/dev` access from the **platform-signed** brain (see
  [`security-policy.md`](security-policy.md)). This reinforces the central finding: **the Moxie
  experience is an app-layer payload on an otherwise stock RK3288 Android-9 BSP.** A custom build keeps
  the whole HAL/kernel layer and swaps the apps.
- **The RK3288 kernel builds its drivers in-tree** — there are **no loadable `.ko` modules** anywhere
  in the image. A custom kernel must compile the same drivers in; `firmware_class.path=/vendor/etc/firmware`
  (kernel cmdline) points the drivers at the blob directory below.
- **The XMOS voice DSP firmware ships in the image** — two selectable images (`xmosdfu.bin` and a
  **VAD** variant `xmosdfu-vad.bin`), DFU-flashed at boot by the XMOS updater. Concrete, re-flashable
  artifacts for anyone rebuilding the audio front-end.

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

The actual module is the **AP6335 (Broadcom BCM4339)** combo — its BT patch ships as
**`bcm4339a0.hcd` (57,291 bytes)**. The directory *also* carries generic Rockchip-SDK firmware for
**dozens of other** Wi-Fi/BT modules (`fw_bcm43…`, `fw_RK903…`, `RT2870*`, `ssv6051`, Realtek
`8723`/`8188`… + matching `nvram_*.txt` / `*.hcd`) — none of which this board uses. See
[`fcc-teardown.md`](../hardware/fcc-teardown.md) for the exact radio blobs and the FCC-confirmed module.

## 5. What this means for the three goals

**① Custom firmware.** Keep the entire HAL/kernel layer as-is — it's stock Rockchip, no embodied
customization to reverse. You need: the RK3288 in-tree kernel drivers, the `/vendor/lib/hw` +
`/vendor/bin/hw` HALs, and these firmware blobs (XMOS DSP + BCM4339). The **XMOS images are here**, so
the audio co-processor can be re-flashed independently of the Android side. Your work is entirely in
the app layer + (optionally) the two userspace daemons.

**② Server revival.** Not relevant to the cloud contract — noted only to bound it out.

**③ Pre-801 revival.** No new lever; the flashing surface is covered in
[`hardware-access.md`](../hardware/hardware-access.md) / [`ota-and-recovery.md`](ota-and-recovery.md).

---
📖 [Reverse-engineering index](../README.md) · [Field guide](../FIELD-GUIDE.md) · [Hardware map](../hardware/hardware-map.md) · [Perception pipeline](../runtime/perception-pipeline.md) · [Firmware reference](firmware-803-reference.md)
