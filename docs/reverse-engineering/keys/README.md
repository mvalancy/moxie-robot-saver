# 🔑 Recovered public keys

**Public** keys extracted from the robot firmware, for reference and verification. No private keys
exist here (and none are recoverable from the images).

- `update-payload-key.pub.pem` — the RSA public key `update_engine` uses to verify A/B OTA
  `payload.bin`. A custom OTA payload must be signed by the matching **private** key, or this file
  must be replaced on-device first (needs `/system` write / OEM-unlock). See
  [`../ota-and-recovery.md`](../ota-and-recovery.md).

The recovery-sideload OTA cert (`releasekey.x509.pem`, from `/system/etc/security/otacerts.zip`) and
the AVB `vbmeta` key are described in [`firmware-image.md`](../firmware-image.md); this folder holds
only what's useful to re-derive or verify signatures.
