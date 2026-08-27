# 🏭 Factory provisioning — production apps, serials & secrets

> **What this is.** How Moxie was **provisioned on the assembly line**: the factory test apps baked
> into the firmware, the serial-number/barcode grammar they scan, the manufacturing part hierarchy,
> and where the "secret factory codes" (DB/FTP/Wi-Fi credentials) live and how to recover them.
> Reconstructed from the decompiled factory APKs shipped in `/system/priv-app`
> (`me.embodied.productiontesting.*`, `FabTestSoftware`) — observed facts, no Embodied source.

## The factory apps (shipped on every robot)

| App | Package | Stage |
|---|---|---|
| Internal assembly test | `me.embodied.productiontesting.internalassytest` | sub-assembly bring-up / burn-in |
| **Final test** | `me.embodied.productiontesting.finaltest` | end-of-line functional test |
| Life test | `me.embodied.productiontesting.lifetest` | reliability / cycle testing |
| Fab test | `FabTestSoftware` | board-level fab test |
| Service utilities | `bo_motor_test`, `bo_xmosupdate`, `xmosdfu`, `qcapp` | motor exercise, XMOS DFU, QC |

They share a common core (`me.embodied.productiontesting`): a **ZXing/ZBar barcode scanner**, a task
scheduler (`tasks/{Task,BasicTask,CompositeTask,Scheduler}`), motor/camera/audio test rigs
(`motor/*`, `video/Camera`, `perception/audio/USB`), and a **MySQL factory-DB** client
(`assy/DatabaseHelper`, `com.mysql.*`, `com.j256.ormlite`).

They run as **privileged system apps**, so on a stock robot they are a ready-made, signed lever for
motor/LED/camera/audio bring-up — useful for validating custom hardware bring-up before replacing the
app layer.

## Serial-number / barcode grammar (`SerialNumber.java`)

Two end-user modes (`Version.EndUser`):

- **Customer** builds: a serial is valid iff it is **exactly 13 chars**.
- **Factory** builds: full `SerialFormat` validation by **2-letter prefix** + length.

| Prefix | Length | Part |
|---|--:|---|
| `BT` | 13/14 | Battery package (13 = Harding `yyyyMMdd…`, 14 = GLW, digits) |
| `IB` | 18 | IMU PCBA |
| `PB` | 18 | Projector PCBA |
| `BP` | 18 | Battery assembly |
| `SA` | 18 | Speaker |
| `LB` | 18 | Lizard PCBA |
| `PA` | 18 | Projector assembly |
| `AB` | 18 | Android DAQ / Android PCBA |
| `CA` | 18 | Image sensor / Camera assembly |
| (plus `FA/FR/HA/IA/MB/PR/BA`) | 18 | other sub-assemblies |

- Robot / projector / Harding serials: **13 digits**; assembly serials: **18 chars**.
- Assembly serials embed a **date** (`yyyyMMdd`, validated non-lenient) — the line rejects
  mis-scanned or wrong-format barcodes with `CORE_INVALID_SERIAL` ("Try to rescan barcode").
- The finished-robot serial is persisted to `PERSISTENT_DATA_PATH/SerialNumber.txt` on the device.

## Manufacturing part hierarchy (`assy/Part.java`)

The line builds parts up a tree, each scanned and recorded, culminating in the finished robot:

```
BatteryPackage → IMUPCBA → ProjectorPCBA → BatteryAssembly → Speaker → LizardPCBA →
MicFPCA → UnfocusedProjector → Projector → ProjectorAssembly → AndroidDAQ → AndroidPCBA →
ImageSensor → CameraAssembly → MicAssembly → FrontHeadAssembly → HeadAssembly →
BodyAssembly → InternalAssembly → InternalAssemblyBI → FinishedRobot
```

`assy/{Assembler,Assembly,PartDB,DatabaseHelper}` record each part↔serial binding into the MySQL
factory DB; `assy/CustomerMode` + `Packout` handle the customer-facing "pack-out" step and
`GCPKey`/`assy/Assembler` provision cloud keys.

## The secrets (`Secrets` / `libsecrets.so`)

Factory credentials are **not** plaintext in the DEX. `me.embodied.productiontesting.Secrets` is a
JNI shim over a native `libsecrets.so` with six getters, keyed by package name:

```java
native String getDBUsername(String pkg);       native String getDBPassword(String pkg);
native String getEmbodiedPSK(String pkg);       native String getEmbodiedStaffPSK(String pkg);
native String getFTPUsername(String pkg);        native String getFTPPassword(String pkg);
```

- **`getDBUsername/Password`** → the MySQL factory DB, used with the DSN
  `jdbc:mysql://%s:%d/%s?user=%s&password=%s`.
- **`getEmbodiedPSK` / `getEmbodiedStaffPSK`** → the **factory / staff Wi-Fi PSKs** the robot joins on
  the line (the "secret factory Wi-Fi").
- **`getFTPUsername/Password`** → the FTP drop for logs / firmware artifacts.

`SecretsHelper.get("DBPassword")` reflects `Secrets.getDBPassword("me.embodied.productiontesting")` —
so the secrets are **derived from the caller's package name** inside the native lib (an obfuscation,
not real key separation).

### The obfuscation is repeating-XOR (cracked)

Emulating `libsecrets.so` under Unicorn (see
[`../../tools/robot-toolkit/secrets/`](../../tools/robot-toolkit/secrets/)) reveals the "encryption"
is trivial once you run the real code. Each getter holds an obfuscated blob and calls
`getOriginalKey(blob, len, packageName, JNIEnv*)`, whose Thumb disassembly is a plain repeating-key
XOR:

```
key    = GetStringUTFChars(packageName)      # "me.embodied.productiontesting"
out[i] = blob[i] XOR key[i % len(key)]       # ldrb / mod keylen / eor / strb
return NewStringUTF(out)
```

**So every factory secret = its embedded blob XOR the package-name string.** No real key management —
the package name *is* the key. The long PSK getters decrypt to clean printable strings; the shorter
DB/FTP getters need a blob-length validation pass (their blobs are built inline via VFP `vstr`).

### Recovering the secret values

`libsecrets.so` (ARMv7, ~18 KB) builds each string at runtime by XOR-ing an embedded blob with the package name (see above) — `strings` alone won't reveal them. Two
practical routes:

1. **Emulate the getter.** Load `lib/armeabi-v7a/libsecrets.so` under `qemu-arm` (or on any
   ARM/Android with the right JNI stub) and **call each `Java_..._get*` export** with the package
   name `me.embodied.productiontesting`; capture the returned string. This is the cleanest — the lib
   deobfuscates itself for you.
2. **Static ARM reversal.** Load into Ghidra/radare2 (ARM Thumb), follow each
   `Java_me_embodied_productiontesting_Secrets_get*` (offsets `0x10bd`–`0x1215`) through the decode
   routine to recover the constant table.

> The native lib is extracted to `work/firmware-re/extract/secrets/lib/armeabi-v7a/libsecrets.so`.
> Values are intentionally **not committed** to this repo; recover them locally with the method above.

## Why this matters for revival / custom

- The **serial + part grammar** lets you mint/validate serials a stock robot's factory apps accept —
  useful for re-provisioning or bench testing.
- The **factory/staff PSKs** and **DB/FTP creds** are the "factory codes"; with them the production
  apps run their full flows (which drive every actuator/sensor) on the bench.
- `GCPKey`/`Assembler` show the **cloud-key provisioning** step — the hook where a robot is bound to a
  cloud identity, i.e. exactly what you re-point when homing a robot to [`server/`](../../server/).

---
📖 [Reverse-engineering index](README.md) · [Firmware image](firmware-image.md) · [Docs index](../README.md) · [Back to top](../../README.md)
