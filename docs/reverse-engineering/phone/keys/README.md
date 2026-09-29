# 🔑 Recovered public keys

**Summary.** This folder is for **public** keys taken from the robot firmware (`v24.10.803`) for reference and signature checks. No private
keys exist, and none can be recovered from the images. **No key files are committed here yet.** The folder currently holds only this note.

| Key | On-device path | Role |
|---|---|---|
| `update-payload-key.pub.pem` (2048-bit RSA) | `/system/etc/update_engine/update-payload-key.pub.pem` | `update_engine` verifies A/B OTA `payload.bin` against it. A custom payload must be signed by the matching private key, or this file must first be replaced on the device (which needs `/system` write access / OEM unlock). See [`ota-and-recovery.md`](../../firmware/ota-and-recovery.md). |
| `releasekey.x509.pem` | inside `/system/etc/security/otacerts.zip` | recovery-sideload OTA cert, see [`firmware-image.md`](../../firmware/firmware-image.md) |
| AVB `vbmeta` key | `vbmeta` partition | verified boot, see [`firmware-image.md`](../../firmware/firmware-image.md) |

To use a key, re-extract it from a firmware image at the path above.

---
📖 [Phone-side index](../README.md) · [Reverse-engineering index](../../README.md)
