# 🏭 Factory provisioning — production apps, serials & secrets

How Moxie was **provisioned on the assembly line** (v24.10.803): the factory test apps baked into every
robot, the serial/barcode grammar they scan, the part hierarchy and factory-DB schema, the end-of-line
test sequences, and where the factory credentials live. Reconstructed from the decompiled factory APKs
in `/system/priv-app` (`me.embodied.productiontesting.*`, `FabTestSoftware`).
- The factory apps are a ready-made, signed bring-up toolkit for motors/LEDs/camera/audio.
- The factory secrets are a **repeating-XOR** with the hex SHA-256 of the package name (cracked).

## The factory apps (shipped on every robot)

| App | Package | Stage |
|---|---|---|
| Internal assembly test | `me.embodied.productiontesting.internalassytest` | sub-assembly bring-up / burn-in |
| **Final test** | `me.embodied.productiontesting.finaltest` | end-of-line functional test |
| Life test | `me.embodied.productiontesting.lifetest` | reliability / cycle testing |
| Fab test | `FabTestSoftware` | board-level fab test |
| Burn-in | `me.embodied.productiontesting.burnintest` | burn-in |
| Service utilities | `bo_motor_test`, `bo_xmosupdate`, `xmosdfu`, `qcapp` | motor exercise, XMOS DFU, QC |

They share a common core (`me.embodied.productiontesting`): a **ZXing/ZBar barcode scanner**, a task
scheduler (`tasks/{Task,BasicTask,CompositeTask,Scheduler}`), motor/camera/audio test rigs
(`motor/*`, `video/Camera`, `perception/audio/USB`), and a **MySQL factory-DB** client
(`assy/DatabaseHelper`, `com.mysql.*`, `com.j256.ormlite`).

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

The generic rule is literally `isDigitsOnly ? length==13 : length==18`, with the named-format lookup
also enforcing `serialLength` when set. Mis-scans are rejected with `CORE_INVALID_SERIAL` ("Try to rescan barcode"); the finished-robot serial
  is persisted to `PERSISTENT_DATA_PATH/SerialNumber.txt`.

### The scanner + the factory→robot command QR

The stations scan with **ZXing** via `com.journeyapps.barcodescanner.DecoratedBarcodeView`
(`qr/Scanner.java`, `decodeSingle`), validating GS1/EAN product codes (`ExpandedProductParsedResult`,
`qr/Validator.EAN13`). The apps also **generate** a QR to *show the robot* (`qr/QR.java` → `QRGEncoder`),
driven by `qr/Codes.java`, whose **only shipped entry** is `{"debug":{"command":"serial_number_display"}}`.
A "manufacturing QR command" is just a [debug-command QR](../protocol/qr-commands.md#json-debugfactory-commands)
on the channel `bo-wifi` scans. There is **no hidden factory command catalog**.

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

The whole build is a **tree of `parts` rows** (part↔serial↔pass, linked to its parent) ending in a
`FinishedRobot` row, plus a `packout` row when it ships. Not needed for revival, but it confirms the
serial/part grammar above is exactly what the DB stores.

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

The station uses a **closed test enclosure** (open/close-door prompts) and **ArUco fiducials**, and
reads the LED ring and projected image back through the camera: the robot self-validates its own optics.

### `internalassytest` — sub-assembly bring-up (`InternalAssyTest.DoTest()`)

A distinct, shorter sequence run at an earlier station, before final assembly:

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

The assembly station focuses on optics/touch/fan/motors; `finaltest` adds the Wi-Fi/RSSI, camera-cover,
audio speaker and arm connect/disconnect checks.

### `lifetest` — a **550-hour reliability soak** (`ActivityLifeTest.DoTest()`)

A burn-in, not a pass/fail station: `TimePeriod(550L, TimeUnit.HOURS)` (~23 days). It **requires the
charger** (`Lizard.waitForDC(30000)`, else `ErrorCode.ROBOT_NO_CHARGER`, *"Charger must be connected"*)
and runs a `Scheduler` that **cycles the test primitives** for the whole duration ("LifeTest start.\nCradle…"
→ "LifeTest end.\n… to grave."). It indicates the duty the actuators were validated for.

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

`SecretsHelper.get("DBPassword")` reflects `Secrets.getDBPassword("me.embodied.productiontesting")`: the
secrets are derived from the caller's package name (obfuscation, not real key separation).

### The obfuscation is repeating-XOR (cracked)

`libsecrets.so` (ARMv7, ~18 KB) builds each string at runtime, so `strings` won't reveal them. Each
getter (`Java_me_embodied_productiontesting_Secrets_get*`, offsets `0x10bd`–`0x1215`) holds an
obfuscated blob and calls `getOriginalKey(blob, len, packageName, JNIEnv*)`, whose Thumb disassembly is
a plain repeating-key XOR:

```
keybuf = ASCII( hex( sha256(packageName) ) ) # 64 hex chars, packageName = "me.embodied.productiontesting"
out[i] = blob[i] XOR keybuf[i % 64]          # ldrb / mod 64 / eor / strb
return NewStringUTF(out)
```

So every factory secret is its embedded blob XOR a fixed 64-char keystream. All six getters recover
clean values: a SQL `SA` login, the factory Wi-Fi PSK `Embodied<3robots!`, a 62-char staff PSK, an FTP
`test-station` account, etc. The extractor in
[`tools/robot-toolkit/secrets/`](../../../tools/robot-toolkit/secrets/README.md) emulates each getter under
Unicorn to capture its blob, then derives the key in Python (the lib's own SHA256 miscomputes under
Unicorn). Static reversal in Ghidra/radare2 (ARM Thumb) of the same exports also works. Apart from the
PSK quoted above, the **values are recovered locally and not committed**.

## Why this matters for revival / custom

- The **serial + part grammar** lets you mint/validate serials a stock robot's factory apps accept —
  useful for re-provisioning or bench testing.
- The **factory/staff PSKs** and **DB/FTP creds** are the "factory codes"; with them the production
  apps run their full flows (which drive every actuator/sensor) on the bench.
- `GCPKey`/`Assembler` show the **cloud-key provisioning** step — the hook where a robot is bound to a
  cloud identity, i.e. exactly what you re-point when homing a robot to [`server/`](../../../server/).

---
📖 [Reverse-engineering index](../README.md) · [Firmware image](firmware-image.md) · [Docs index](../../README.md) · [Back to top](../../../README.md)
