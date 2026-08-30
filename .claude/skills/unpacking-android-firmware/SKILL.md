---
name: unpacking-android-firmware
description: Acquire and unpack an Android device's firmware (OTA payload.bin or factory images) into readable partitions, and inventory its apps, native libs, init services, permissions, and device-tree. Use at the start of reverse-engineering an Android robot/appliance, or when you need a file off a system/vendor/oem/boot image.
---

# Unpacking Android firmware + first inventory

Goal: turn a firmware blob into a browsable filesystem, then a written inventory of every executable and
config that matters. First phase of `reverse-engineering-android-robots`.

## Acquire the firmware
- **OTA update** (`.zip`): contains an A/B `payload.bin` (the `update_engine` format). Extract partition
  images with a payload dumper (`payload_dumper payload.bin` → `system.img`, `vendor.img`, `boot.img`, …).
- **Factory image / eMMC dump:** already per-partition, or a full dump you split by GPT.
- **A running unit with adb** (best, if reachable): `adb pull /system`, `adb shell "su -c 'dd if=/dev/block/…'"`.
- On locked consumer units, `fastboot`/OEM-unlock are usually refused and AVB rejects tampered images —
  so acquisition is often the hard part. Document honestly what path exists (see the device's OTA/recovery reality).

## Unpack partitions (no mounting needed)
Android images are usually **ext4** (sometimes wrapped as a **sparse** image first):
```bash
simg2img system.img.sparse system.img        # if sparse ("ANDROID!"/0x3aff26ed magic)
debugfs -R "ls -R /" system.img               # walk the tree read-only
debugfs -R "cat /system/build.prop" system.img
debugfs -R "rdump /system/priv-app /out" system.img   # bulk-extract a dir
```
`boot.img`/`recovery.img`: `unpackbootimg` (or `abootimg -x`) → kernel + **ramdisk** (`cpio`/`gzip`) →
`init*.rc`, `default.prop`, SELinux `*_contexts`. `vbmeta.img` holds the AVB signing/hash chain.

## Inventory (write these down — they anchor everything)
- **Build identity:** `/system/build.prop` (`ro.build.*`, fingerprint, version) → stamp every doc with it.
- **Apps:** `/system/{app,priv-app}`, `/vendor/app`, `/oem/…`. For each APK decode `AndroidManifest.xml`
  (androguard/`aapt`) → package, versionName/Code, and the **signer** (PKCS#7) — signer identity separates
  the vendor's apps from stock AOSP.
- **Native binaries + libs:** `/system/bin`, `/system/lib*`, and the `lib/` inside each APK. `file`/`readelf -h`
  for arch; note the big vendor `.so`s (the brain/perception/audio blobs).
- **Init service graph:** every `service <name> <exec>` + its `class`/triggers/`seclabel` across all `*.rc`.
  The vendor's *own* daemons (non-AOSP) are the interesting few.
- **Permissions / sysconfig:** `/system/etc/permissions/*.xml`, `/system/etc/sysconfig/*.xml`,
  `privapp-permissions-*.xml`, `platform.xml`. Whether the vendor apps carry extra privilege / are
  platform-signed tells you the trust model.
- **Device-tree:** the `.dtb` in `boot`/`dtbo` → `dtc -I dtb -O dts` → the hardware map (I²C/UART/SPI
  buses, GPIOs, the panel/display path). → `mapping-robot-hardware`.

## Worked example (Moxie, v24.10.803)
Images at `work/firmware-re/{system.img,oem.img,parts/vendor.img,parts/boot.img}`; extracted tree under
`work/firmware-re/extract/`. RK3288/Android 9. The brain is `bo-android.apk` (Unity), setup is `bo-wifi.apk`,
plus `me.embodied.productiontesting.*` factory apps. Boot animation + face assets live on `oem.img`.
Results written to `docs/reverse-engineering/firmware/{firmware-803-reference,firmware-inventory,firmware-image,firmware-manifest}.md`
and the manifest TSVs under `firmware/manifests/`.
