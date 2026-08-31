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

The 15 formats each bind a **2-letter prefix** to a `Part` (`BT`→BatteryPackage, `IB`→IMUPCBA,
`PB`→ProjectorPCBA, `BP`→BatteryAssembly, `SA`→Speaker, `PA`→ProjectorAssembly, `AB`→AndroidDAQ,
`CA`→ImageSensor, `MB`→MicFPCA, `FA`→FrontHeadAssembly, `HA`→HeadAssembly, `BA`→BodyAssembly,
`IA`→InternalAssembly, `PR`→Projector, `FR`→FinishedRobot). The length rule is **content-based**, not
fixed per prefix (verified in the `isValidFormat` overrides):

| Serial content | Length | Rule |
|---|--:|---|
| **all digits** | **13** | date-prefixed `yyyyMMdd` + 5-digit sequence (the "Harding" format), `SimpleDateFormat` non-lenient |
| all digits (battery `BT` / "GLW") | 14 | digits from offset 4 (`substring(4)`) |
| **contains letters** | **18** | the alphanumeric assembly serial |
| **`FR` FinishedRobot** | 13 | digits-only **and a valid EAN-13 checksum** (`Validator.EAN13`) — the finished robot's serial is a real **EAN-13 barcode** |

- The generic rule is literally `isDigitsOnly ? length==13 : length==18`, with the named-format lookup
  additionally enforcing `serialLength` when set.
- **Customer** builds skip all of this — any exactly-13-char serial is accepted.
- Mis-scans are rejected with `CORE_INVALID_SERIAL` ("Try to rescan barcode"); the finished-robot serial
  is persisted to `PERSISTENT_DATA_PATH/SerialNumber.txt`.

### The scanner + the factory→robot command QR

The stations scan with **ZXing** via `com.journeyapps.barcodescanner.DecoratedBarcodeView`
(`qr/Scanner.java`, `decodeSingle`), validating GS1/EAN product codes (`ExpandedProductParsedResult`,
`qr/Validator.EAN13`). The apps also **generate** a QR to *show the robot* (`qr/QR.java` → `QRGEncoder`),
driven by `qr/Codes.java` — whose **only shipped entry** is
`{"debug":{"command":"serial_number_display"}}`. So a "manufacturing QR command" is just a
[debug-command QR](../protocol/qr-commands.md#json-debugfactory-commands) on the same channel `bo-wifi`
scans — there is **no hidden factory command catalog**, only this one generated code plus the barcode
*reading* above.

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

### Factory-DB schema (ORMLite → MySQL, v24.10.803)

Recovered from the `@DatabaseTable`/`@DatabaseField` annotations in the decompiled factory apps — the
exact tables the line writes over the `jdbc:mysql://…` connection above:

| Table | Column | Type / constraint | Meaning |
|---|---|---|---|
| **`parts`** | `id` | `BIGINT` auto‑PK | row id |
| | `parent` | FK → `parts.id` (self, auto‑refresh **8 levels**) | the **assembly tree** — a part points at its parent sub‑assembly, up to the 8‑deep [part hierarchy](#manufacturing-part-hierarchy-assypartjava) |
| | `part_name` | `VARCHAR` not‑null | the `Part` enum name (e.g. `LizardPCBA`, `FinishedRobot`) |
| | `pass` | `BOOLEAN` not‑null | did this part pass its station test |
| | `serial` | `VARCHAR` not‑null | the scanned barcode serial |
| **`customer_mode_parts`** | *(same 5 columns as `parts`)* | | the **customer‑mode** mirror (the retail/service pack path, `assy/CustomerMode`) |
| **`packout`** | `id` | `BIGINT` auto‑PK | row id |
| | `isPacked` | `BOOLEAN` not‑null | boxed for shipment |
| | `serial` | `VARCHAR` not‑null | finished‑robot serial |
| | `timestamp` | `DATETIME` not‑null | when it was packed out |

So the whole build is captured as a **tree of `parts` rows** (each part↔serial↔pass, linked to its
parent) culminating in a `FinishedRobot` row, plus a `packout` row when it ships. Nothing here is needed
to revive a robot — it's the manufacturing side — but it completes the factory data model and confirms
the serial/part grammar above is exactly what the DB stores.

## Factory test catalog (`finaltest` — end of line)

`ActivityFinalTest.DoTest()` runs the end-of-line functional test as an ordered sequence, mixing
**native JNI tests** (in `librobotTesting.so`/`libfinalTest.so`) with operator prompts. This is the
authoritative hardware-bring-up checklist for the robot:

| # | Step | Kind | Exercises / error code |
|--:|---|---|---|
| 1 | Camera init | check | camera opens · `CORE_CAMERA` |
| 2 | Lizard error-state + projector-attempts log | native | MCU health baseline |
| 3 | **RSSITest** | native | Wi-Fi signal ≥ `RSSI_MIN` over `RSSI_NUM_SAMPLES` |
| 4 | **CheckProjectorConfig** | native | DLP projector config valid |
| 5 | Touch: `GetTouchSensor("BACK")` + **TestTouchSensors** | native | capacitive zones (`FINAL_TOUCH_NONE`) |
| 6 | Close-door prompt → **ProjCamTest** (+ `ArucoAligner`, `DUTAlignment`) | native | projector renders a pattern, camera reads it, **ArUco markers** align the device-under-test |
| 7 | **RingTest** | native | LED ring, verified through the camera |
| 8 | **AudioTest** | native | speaker + mic |
| 9 | **ScreenSharpnessCheck** / **ScreenDirtCheck** | operator | projected image sharp / clean (`FINAL_SHARP_BAD`, `FINAL_DIRT`) |
| 10 | Touch zones again | native | re-check |
| 11 | User-start motor → **TestMotor / DoMotorsTest** (×3), arm connect/disconnect | native + prompt | motors + arm limit switches (`FINAL_MTR_NOT_RUN`, `FINAL_ARM_DISCON`) |
| 12 | Store serial as barcode to disk | check | `SerialNumber.txt` (`CORE_INVALID_SERIAL`, `MISC_FILE_WRITE`) |

### Native test primitives (JNI)
The factory native lib exposes reusable hardware pokes — a ready-made bring-up API. This list is the
**complete set of 15 `Java_…ActivityFinalTest_*` exports** in `libfinalTest.so` (verified via `nm -D`):

`ArmConnect` · `ArmDisconnect` · `CheckPluggedIn` · `CheckProjectorConfig` · `GetMPUState` (IMU) ·
`GetTouchSensor` · `ArucoAligner` · `AudioTest` · `DUTAlignment` · `DoMotorsTest` · `Fan` ·
`ProjCamTest` · `RingTest` · `TurnFront` / `TurnBack` (base rotation).

The station uses a **closed test enclosure** (open/close-door prompts), **ArUco fiducials** for
camera↔projector alignment, and reads the LED ring / projected image back through the camera —
i.e. the robot self-validates its own face optics. For custom firmware / hardware bring-up, these are
the exact routines that prove each subsystem.

### `internalassytest` — sub-assembly bring-up (`InternalAssyTest.DoTest()`)

A **distinct**, shorter sequence run at an earlier station (before final assembly), verified from the
decompiled `me.embodied.productiontesting.internalassytest` (firmware **v24.10.803**). Ordered steps:

| # | Step | What it proves |
|---|---|---|
| 1 | `CheckNoTouch` → `CheckTouchSensors` (×2) | capacitive touch zones read correctly with/without contact |
| 2 | `ArucoAligner` | align the device-under-test in the fixture via **ArUco** markers + camera |
| 3 | `ArucoOnScreen` | the **projector** renders an ArUco pattern the camera reads back (face optics) |
| 4 | `LEDTest` (through camera) | status LEDs |
| 5 | `RingTest` (through camera) | the LED ring |
| 6 | `FanRunning` → `FanNoise` | the DLP projector fan spins + isn't rattling |
| 7 | `AECTest` | acoustic echo cancellation (mic array + speaker) |
| 8 | `Spin` | base yaw rotation (re-checks ArUco alignment after spinning) |
| 9 | `ReRun(TestMotor, ×3)` | the motor set, **repeated 3×** |

So the assembly station is optics/touch/fan/motor focused; the **`finaltest` end-of-line run above adds**
the Wi-Fi/RSSI, camera-cover, audio speaker and arm connect/disconnect checks.

### `lifetest` — a **550-hour reliability soak** (`ActivityLifeTest.DoTest()`)

Not a pass/fail station test at all — it's a **burn-in**: `TimePeriod(550L, TimeUnit.HOURS)` (~23 days).
It **requires the charger** (`Lizard.waitForDC(30000)`, else `ErrorCode.ROBOT_NO_CHARGER` — *"Charger must
be connected"*) and then runs a `Scheduler` that **cycles the test primitives** for the whole duration
("LifeTest start.\nCradle…" → "LifeTest end.\n… to grave."). This is the routine that would cycle a
bench unit's motors/optics/audio for reliability data — useful context for how much duty these actuators
were validated for.

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
[`../../tools/robot-toolkit/secrets/`](../../../tools/robot-toolkit/secrets/)) reveals the "encryption"
is trivial once you run the real code. Each getter holds an obfuscated blob and calls
`getOriginalKey(blob, len, packageName, JNIEnv*)`, whose Thumb disassembly is a plain repeating-key
XOR:

```
keybuf = ASCII( hex( sha256(packageName) ) ) # 64 hex chars, packageName = "me.embodied.productiontesting"
out[i] = blob[i] XOR keybuf[i % 64]          # ldrb / mod 64 / eor / strb
return NewStringUTF(out)
```

**So every factory secret = its embedded blob XOR the hex-SHA256 of the package name** — a fixed
64-char keystream. All six getters recover clean values (a SQL `SA` login, the factory Wi-Fi PSK
`Embodied<3robots!`, a 62-char staff PSK, an FTP `test-station` account, etc.). The extractor emulates
each getter to capture its assembled blob, then derives the key with Python (the lib's own SHA256
miscomputes under Unicorn). **Values are recovered locally, not committed.**

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
  cloud identity, i.e. exactly what you re-point when homing a robot to [`server/`](../../../server/).

---
📖 [Reverse-engineering index](../README.md) · [Firmware image](firmware-image.md) · [Docs index](../../README.md) · [Back to top](../../../README.md)
