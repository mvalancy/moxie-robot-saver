# 🔄 OTA & recovery — can a robot be upgraded without opening it?

> **The hard question for reviving old robots.** A pre-801 Moxie (Google-IoT firmware) is stranded:
> its cloud is dead, so it never gets told to update. Can we upgrade it to 803 (or push custom
> software) **without mechanical disassembly**? This doc lays out the update machinery honestly —
> what's software-only, and where opening the shell still appears unavoidable. Reconstructed from
> `OSUpdate.apk`, `me.embodied.services.BoUpdater`, the A/B fstab, and the update_engine keys.

## How OTA actually works (803 firmware)

Moxie uses **stock Android A/B seamless updates** (`update_engine`), staged from **internal storage**:

```mermaid
flowchart LR
  cloud["☁️ cloud"] -->|image + version| dl["brain downloads to<br/>/sdcard/EmbodiedData/otaImages/"]
  dl --> info["otaInfo.txt<br/>(target + min version)"]
  info --> bu["BoUpdater service<br/>(gates, disk budget)"]
  bu --> osu["OSUpdate<br/>UpdateEngine.applyPayload()"]
  osu --> ue["update_engine<br/>verify sig → write inactive slot"]
  ue --> reboot["bootctl → reboot to new slot"]
  classDef d fill:#e3eaf2,stroke:#607d8b,color:#263238;
  class cloud,dl,info,bu,osu,ue,reboot d;
```

- **Staging area:** `/sdcard/EmbodiedData/otaImages/` + `otaInfo.txt` (target version, minimum-OTA
  version), `otaLog.txt`, and a `DISABLE_OTA` sentinel file. `BoUpdater` enforces a min-version gate
  (can refuse downgrades) and a ~200 MB data-overage budget.
- **`/sdcard` is internal emulated storage** (`export EXTERNAL_STORAGE /sdcard`; `/mnt/shell/emulated`
  → `/data/media`), **not** the removable microSD. So "put a file on /sdcard" means internal storage,
  reachable by the robot's own downloader, by ADB/MTP, or by an app — *not* by popping a card.
- **Applier:** `OSUpdate` (`com.embodied.osupdate`) waits for **`/sdcard/update.zip`**, unpacks
  `payload.bin` + `payload_properties.txt`, and calls `android.os.UpdateEngine.applyPayload(
  "file:///sdcard/osupdate-tmp/payload.bin", …)`. update_engine writes the **inactive** A/B slot and
  `bootctl` switches to it on reboot. No partitions are touched in place; a bad update rolls back.

## The signing gate (this is what stops arbitrary custom firmware)

`update_engine` verifies every payload against a baked-in public key:

- `/system/etc/update_engine/update-payload-key.pub.pem` — 2048-bit RSA (recovered; committed under
  `keys/` for reference). Only payloads signed by the matching **private** key apply.
- `/system/etc/security/otacerts.zip` → `releasekey.x509.pem` — the recovery-sideload OTA cert.

**Implications:**
- You can apply a **genuine Embodied-signed** OTA (e.g. the real 803 `update.zip`) to any robot whose
  update_engine trusts that key — no disassembly, if you can get the file onto `/sdcard` and trigger
  OSUpdate.
- You **cannot** apply a self-built custom payload until you replace `update-payload-key.pub.pem`,
  which requires already having write access to `/system` (or an unlocked bootloader). Chicken-and-egg
  — the first foothold has to come from a genuine signed image or from flashing.

## No-disassembly vectors — honest status

| Vector | Needs opening? | Status |
|---|---|---|
| **QR re-home** (`endpoint_update` → OPEN_MOXIE/EMBODIED_LOCAL) | ❌ no | ✅ Works on **803 / 801+** — the endpoints exist in firmware. Redirects the cloud; does **not** upgrade firmware or run custom code on-device. See [`qr-commands.md`](qr-commands.md). |
| **QR re-home on pre-801** | ❌ no | ❌ **Does not work.** Pre-801 pins the endpoint to `mqtt.googleapis.com` with hostname-checked TLS; QR can't relocate it (see [`../debugging/live-hardware-debug.md`](../debugging/live-hardware-debug.md)). |
| **Serve a genuine signed OTA** to a robot we control the network of | ❌ no | ⚠️ Plausible on 801+ (point it at our server via QR, serve the real `update.zip`). Blocked on pre-801 because we can't get it to connect to us (TLS pinning). **Also needs a genuine signed 803 `update.zip`, which we do not currently have** (we have raw partition images, not a signed payload). |
| **ADB push `update.zip` + launch OSUpdate** | ❔ depends on an externally reachable USB port | ⚠️ `ro.adb.secure=1`, `ro.debuggable=0`: ADB needs an authorized key, and first-auth needs an on-screen "allow" the projector face can't show. Recovery-mode ADB sideload bypasses that auth but needs a button combo (unknown for Moxie) — **and reaching the port may itself require opening the shell.** |
| **MTP copy `update.zip` + launch OSUpdate** | ❔ depends on a reachable USB port | ⚠️ The USB gadget offers **MTP** (`persist.sys.usb.config=mtp,adb`), which needs **no adb authorization** — a host could copy a file to `/sdcard` over MTP. But you still must *launch* `com.embodied.osupdate` (needs adb/an app/an intent), and the port may be internal. Promising *if* a port is reachable. |
| **Rockchip maskrom + `rkdeveloptool`** | ✅ **yes** | ✅ Always works, but requires physical access to the board / USB — i.e. disassembly. This is today's only reliable pre-801 path. |

## Where this leaves pre-801 revival

**Unsolved without opening the shell, as of this analysis.** The blockers stack:
1. Pre-801 won't relocate off Google's dead cloud (TLS hostname pinning) → we can't reach it over the
   network to hand it an OTA.
2. Even if we could, we'd need a genuine Embodied-signed 803 `update.zip` (not just partition images).
3. ADB/recovery delivery hinges on an externally reachable USB port and a recovery entry method we
   haven't confirmed on the hardware.

### Open leads worth chasing (tracked for the loop)

- **Find Moxie's recovery key-combo / an external USB port.** If recovery ADB sideload is reachable
  without opening, and it accepts an `otacerts`-signed package, that's a clean no-open upgrade.
- **Source a genuine signed 803 `update.zip`** (community mirrors / Embodied's final OTA). With one,
  the 801+ "QR re-home → serve OTA" path becomes real.
- **Pre-801 setup-mode behavior:** does a pre-801 unit that can't reach cloud fall into the Wifi App's
  QR mode at all? If it accepts *any* QR (even just Wi-Fi), that's a wedge to study.
- **Downgrade/attest quirks:** whether update_engine on pre-801 trusts the same payload key as 803
  (if so, a signed 803 payload applies directly once delivered).

## Building custom firmware (once you have a foothold)

After a genuine upgrade + OEM-unlock (or a maskrom reflash), replace
`update-payload-key.pub.pem` with your own and you can sign and OTA your own payloads normally. Full
partition/boot/AVB details: [`firmware-image.md`](firmware-image.md).

---
📖 [Reverse-engineering index](README.md) · [Firmware image](firmware-image.md) · [QR commands](qr-commands.md) · [Docs index](../README.md)
