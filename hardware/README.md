# `hardware/` — the robot itself

Reference facts about the Moxie hardware, for revival without teardown.

## What Moxie is
- A **Qualcomm-based Android device** (Lantronix/Intrinsyc Open-Q class board) with a **locked
  bootloader, Android Verified Boot, and SELinux**. Not publicly rooted.
- The animated face is a **Unity app** projected onto a fresnel-lens faceplate.
- On-device: `bo-wifi.apk` (the Wi-Fi/pairing app that reads our QR codes), `bo-launcher-j`
  (supervisor), and a native C++ cloud layer (`embodied::logging::cloud::RightPoint`).
- Wi-Fi/BT is an **AMPAK** combo module — useful for spotting Moxie on your LAN by MAC vendor.

## Revival is camera-QR only
Everything this project does reaches Moxie through its **camera** (QR codes) and the **network**
(Wi-Fi + MQTT). We deliberately avoid disassembly and serial/UART access.

## ADB (optional, not required)
The robot's filesystem is readable over ADB if ever needed (e.g. to read its MQTT identity):
`/sdcard/EmbodiedStaticData/PERSISTENT_DATA/uuid.txt` and `.../rightpoint/RS256.key`. A normal setup
does **not** need this.

## Finding Moxie on your network
See [`../docs/guides/find-moxie-on-lan.md`](../docs/guides/find-moxie-on-lan.md).
