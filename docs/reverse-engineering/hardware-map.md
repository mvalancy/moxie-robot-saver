# 🦾 Hardware map — motors, sensors, LEDs, power

> **What this is.** Moxie's physical hardware, enumerated straight from the firmware's own MCU
> protobufs (`embodied.lizzerface`, recovered under [`recovered-proto/`](recovered-proto/)) and the
> vendor init scripts. The **"Lizard"** board is the microcontroller that owns motors, touch, IMU,
> battery, and LEDs; the RK3288 (Android) talks to it over UART and drives it with the messages below.
> This is the actuator/sensor contract a custom firmware must honor to move the robot.

## Hardware revisions (`Revision_Level`)

Codenames trace the program's history — **Bo → Karu → Moxie**:

| Enum | Value | Board |
|---|--:|---|
| `REVISION_D1_Bo` / `D2_Bo` | 209 / 210 | early "Bo" prototypes |
| `REVISION_D3_Karu1` | 211 | Karu1 |
| `REVISION_D4_Karu1Skel` | 212 | Karu1 skeleton |
| `REVISION_D5_MoxieP6L1` | 213 | Moxie pilot |
| `REVISION_D6_MoxieBlue` / `D7_MoxieBlue` | 214 / 215 | **shipping Moxie ("Blue")** |

## Motors / degrees of freedom (`Motor`)

| Enum | # | Joint |
|---|--:|---|
| `L_ARM_UP_DN` | 0 | left arm up/down |
| `L_ARM_IN_OUT` | 1 | left arm in/out |
| `R_ARM_UP_RN` | 2 | right arm up/down |
| `R_ARM_IN_OUT` | 3 | right arm in/out |
| `HEAD_UP_DN` | 4 | head pitch |
| `HEAD_L_R` | 5 | head yaw |
| `HEAD_TILT` | 6 | head roll/tilt |
| `SQUISH` | 7 | body "squish" |
| `MOT0`, `MOT1` | 8, 9 | spare/aux |
| `BASE_L_R` | 11 | base rotate |
| `TORSO_F_B` | 12 | torso lean |

`NUM_MOTORS=10` (the core face/arm/head set); `BASE_L_R`/`TORSO_F_B` are extended DOF.

### Driving a motor

```proto
message MotorSetPosEventPB   { Motor motor = 1; uint32 pos = 2; uint64 timestamp = 3; }
message ConfigureMotorEventPB{ Motor motor = 1; ConfigParam param = 2; uint32 val = 3; uint64 timestamp = 4; }
```

Position control is **set-point** (`pos`), with a per-motor **PID** configured live via `ConfigParam`:

```
CONFIG_MOTOR_RWD=0  CONFIG_KP=1  CONFIG_KI=2  CONFIG_KD=3  CONFIG_MAX_PWM=4
CONFIG_KI_LEAK=5    CONFIG_LIMIT=6  CONFIG_ADJ=7  CONFIG_MOTOR_FWD=8  CONFIG_WRITE=85
```

Feedback comes back as `ServoPosFdbackEventPB{ cservoName, pos }` and `ServoStallEventPB{ motor_id,
is_stalled }`.

### Native motion API (factory `libmotionlib` / `liblizardJNI`)

Besides the ZMQ/proto path above, the firmware carries a **native JNI motor API**, recovered from the
factory motor-test app (`bo_motor_test` → `libmotionlib.so`) and the shared `liblizardJNI.so`. This is
the lower-level path a Java/native component uses to drive the body directly:

```java
// com.embodied.motionlib.MotionPlanning (libmotionlib.so)
int  readMotorPosition(int idx);                 // current encoder position
void setMotorPositionDt(int idx, int pos, int dtMillis);   // move to pos over dt ms
void MoveToPositionVt(int idx, int curPos, int targetPos,  // segmented trajectory:
                      int milliSecs, int segmentTime, int motionPlanMode);
void register();                                 // attach to MCU comms (call first)
// com.embodied.robot.Lizard (liblizardJNI.so) — even lower level
void setMotorTarget(...); int getMotorPos(...);
int  getContactStatesNative();   // touch/limit switches bitfield
void waitForDC();                // block until charger (DC) event
void LogLizardErrorState();
```

**`libmotionlib` motor index (its own compact 0–6 space — from the app's comms loop):**

| idx | field | joint |
|--:|---|---|
| 0 | `laudCurrent` | left arm up/down |
| 1 | `laioCurrent` | left arm in/out |
| 2 | `raudCurrent` | right arm up/down |
| 3 | `raioCurrent` | right arm in/out |
| 4 | `headCurrent` | head |
| 5 | `baseCurrent` | base (rotate) |
| 6 | `bodyCurrent` | body (torso lean) |

> ⚠️ **This index is _not_ the `Motor` proto enum above.** It coincides for the four arm motors
> (0–3) but then diverges — `libmotionlib` 5/6 = base/body, whereas the proto enum 5/6 = `HEAD_L_R`/
> `HEAD_TILT`. Treat the two index spaces as distinct: the proto `Motor` enum is the runtime bus
> vocabulary; `libmotionlib`'s 0–6 is the factory app's own ordering. When driving motors, use the
> index that matches the API you're calling.

**Position units:** `MOTOR_MAX_POS = 32767` — positions are a 15-bit range (`0..32767`), rest ≈ `16384`.
`setMotorPositionDt`/`MoveToPositionVt` interpolate to the target over a millisecond duration (a
timed/trajectory move, not an instant set-point). A `SingleMotorTune` activity + a
record/`playBackRunnable` path let the factory capture and replay motor trajectories.

> ⚠️ **There are no joint angles (degrees) anywhere in the firmware.** Everything is **encoder counts**
> in the `0..32767` space; the *mechanical* end-stops are enforced per motor by the MCU via
> **`CONFIG_LIMIT`** (with `CONFIG_ADJ` for the zero/offset), set at factory calibration — so counts→degrees
> is a **per-unit calibration constant that isn't in the image**. Anyone building motion (custom firmware
> or a [simulator](../architecture/sil-and-cicd.md)) must derive the mapping empirically on a bench unit,
> or pick visually sensible angles. The count ranges below are the only travel data the firmware gives.

**Per-motor travel limits & timing** — from the engineering motor test (`MotorEngActivity`, which sweeps
each joint to its endpoints via `MoveToPositionVt(idx, cur, target, milliSecs, segmentTime, mode)`):

| idx | joint | tested travel | move time | notes |
|--:|---|---|--:|---|
| 0 | L arm up/down (shoulder) | **8191 – 24575** (≈ ±8192 around centre) | 1050 ms | shoulders use **~half** the full range |
| 1 | L arm in/out (elbow) | **0 – 32767** (full) | 700 ms | elbows use the full range, faster |
| 2 | R arm up/down (shoulder) | **8191 – 24575** | 1050 ms | |
| 3 | R arm in/out (elbow) | **0 – 32767** (full) | 700 ms | |

Both calls use **`segmentTime = 35 ms`** (the motion-planner tick) and **`motionPlanMode = 1`**. So the
**shoulders are software-limited to the middle half of the range** (don't drive them to 0 or 32767), while
the **elbows travel the full span**; a custom motion system (or the [SIL](../architecture/sil-and-cicd.md))
should clamp/scale per-joint accordingly. (Head/base/body indices 4–6 aren't swept by this arm-focused
test; treat their limits as bench-TBD.)

**For custom firmware / bench work:** this JNI API and the proto bus are two faces of the same MCU —
either drives the Lizard board. The proto/ZMQ path ([`robot-ipc-protocol.md`](robot-ipc-protocol.md))
is the cleaner seam for a replacement brain; `libmotionlib` documents the exact position units and
timed-move semantics to reproduce.

## Touch & switches

```proto
enum TouchID  { BACK=0; TUMMY=1; UNUSED=2; LEFTHAND=3; RIGHTHAND=4; }   // capacitive body-touch zones
enum SwitchID { SWITCH0=0; SWITCH1=1; SWITCH2=2; DC_PLUG=3; LEFT_ARM=16; RIGHT_ARM=17; }
message TouchEventPB  { TouchID  ID = 1; ... }
message SwitchEventPB { SwitchID ID = 1; bool State = 2; ... }
```

`DC_PLUG` = charger inserted; `LEFT_ARM`/`RIGHT_ARM` = arm limit/home switches; `BACK`/`TUMMY`/hands =
the body-touch surfaces the personality reacts to.

## IMU / motion (`MpuEventID`)

```
STABLE  NOT_STABLE  PICKED_UP  PUTDOWN  FORCE_PUTDOWN  TILT
```

Emitted as `MpuEventPB{ ID }` — this is how the robot knows it's been picked up, put down, or tilted.
`FlapEventPB{ Amplitude }` (the "flap"/ear or mouth flap sensor) and `LightEventPB{ State }` /
`LightAdcDataEventPB{ adcCounts }` (ambient light) round out the sensing.

## LEDs & the face

The **face pattern** LEDs are a small enum of moods (`LedrPattern`), driven by `SetLedrEventPB`:

```
F_BOOTUP_DEFAULT=0  F_RDY2LISTEN_BLUE=1  F_LISTEN_GREEN=2
F_PROCESS_YELLOW=3  F_LW_BAT=4          F_PRIV=5           // F_PRIV = privacy/"mic off"
```

```proto
message SetLedrEventPB { LedrPattern ledr = 1; bool inloop = 2; uint64 timestamp = 3; }
```

Physically these are a **PCA963x** I²C LED controller (`/sys/class/leds/pca963x:{red_1..6,
green_1..5, blue_1..5}`) driven by the `ledctrld` daemon. The **animated face itself** is a **TI
DLPC3430 DLP projector** at I²C `5-001b` (`led_out`/`rgb_out`/`brightness_alt`/`temperature`),
rendered by Unity — the LED patterns above are the status ring, not the projected face.

## Power rails (`PowerRail`)

```
POWER_12V=0  POWER_3V3=1  POWER_5V=2  POWER_LCOS=3  POWER_MUTE=4  POWER_SPEAKER=5
```

Rails are switched with `PowerEnableEventPB{ rail }` / `PowerDisableEventPB{ rail }`. `POWER_LCOS`
feeds the projector light engine; `POWER_MUTE`/`POWER_SPEAKER` gate audio; `PowerStateEventPB` and
`BatteryEventPB` report charge/discharge state.

## Lizard MCU firmware update (bootloader "GOBY")

The Lizard board is a separate **STM32F071VBT6** microcontroller (ARM Cortex-M0, LQFP-100, 128 KB flash — part number read directly off the die in the FCC internal photos, see [`fcc-teardown.md`](fcc-teardown.md)) with its own firmware, updated
from Android over UART by `bo-firmwareUpdate` / `me.embodied.firmwareupdatelib.fwUpdateLibEntry`
(native `libnative-lib.so`, class `lizardPktAssembler`).

| Aspect | Detail |
|---|---|
| Transport | **UART `/dev/ttyS3`** (`open UART` / `Uart closed`) |
| Bootloader | **"GOBY"** (`bo-firmwareUpdate` VERSION_NAME) |
| Image format | **Intel HEX** (`:` records), loaded to STM32 flash **`0x08000000`** (`:02000004 0800` ext-linear-address) |
| Native API (JNI) | `start()` · `getSystemInfo()` · `invalidateApp()` (erase) · `sendBootLoaderPkt(bytes,len)` · `resetRobotVersion()` · `closeUart()` |

### Version handshake
`getSystemInfo()` returns a packed int read from the MCU:

| Field | Bits |
|---|---|
| firmware **major** | `info & 0x7F` |
| firmware **minor** | `(info >> 8) & 0x7F` |
| **hardware** version | `(info >> 16) & 0x3F` |
| release flag | bit 22 (`(info>>16)&64`) |

`hardwareName = Hardware_Version[hwVersion]`, from the index table:

```
0:P5B 1:P5 2:P6A 3:P6B 4:P7 5:P8 6:P9 7:EP1 8:EP2 9:EP3 10:FEP 11:FEP2 12:PP 13:PS1 14:PS2 15:PS3
```
(P* = production board revs, EP/FEP = engineering/final-eng prototypes, PS = pilot, PP = pre-prod.)

### Flash sequence
1. `start()` → open `/dev/ttyS3`.
2. `getSystemInfo()` → read current MCU hw/fw version.
3. Pick the matching **Intel-HEX image** for the board rev.
4. `invalidateApp()` → erase (`InvalidateLizardApp` / `WaitForEraseFinish`).
5. `downloadLizardApp()` → stream each HEX record via `sendBootLoaderPkt` (progress %).
6. `resetRobotVersion()` → boot the new app; `closeUart()`.

### Shipped MCU firmware images (in `bo-firmwareUpdate.apk` `res/raw/`)
Eight per-revision Intel-HEX images (~220–310 KB each) — **extractable**:

| Image | For board rev |
|---|---|
| `v4_0_p6a_firmware` · `v4_0_p6b_firmware` · `v4_0_p7_firmware` | P6A / P6B / P7 (fw v4.0) |
| `v7_7_ep1_firmware` · `v7_7_fep_firmware` · `v7_7_fep2_firmware` | EP1 / FEP / FEP2 (fw v7.7) |
| `v7_7_p8_firmware` · `v7_7_p9_firmware` | P8 / P9 (fw v7.7) |

Each file begins with a **SHA-1 line** then Intel-HEX records. For custom firmware / a bench MCU, this
is the complete flash path (UART `/dev/ttyS3`, GOBY bootloader, Intel HEX @ `0x08000000`). The MCU's
runtime protocol (motors/sensors/LEDs) is the `embodied.lizzerface` set above; MCU faults surface as
`LizardErrorEventPB` (`FIRMWARE_*` = this DFU path's error space).

## MCU firmware & health (`LizardErrorEventPB.LizardErrorEventID`)

The Lizard board reports a rich error/status stream (`1000`–`1051`), including battery over-temp
(`1001`), motor-IC alerts (`1004`), IMU/LED-IC loss (`1006`/`1007`), the whole **firmware-download
state machine** (`FIRMWARE_*`, `1009`–`1031`), motor stall (`1045`), charging (`CHARGING_EVENT=1050`),
and **`WAKEUP_ANDROID_EVENT=1051`** — the MCU waking the Android SoC. `bo-firmwareUpdate` /
`RobotControlFirmwareEventPB{ CONTROL_RESET_MOTOR_IC }` drive MCU DFU over UART. Custom firmware that
replaces the Android side can leave the Lizard MCU stock and just speak this protocol.

---
📖 [Reverse-engineering index](README.md) · [IPC protocol](robot-ipc-protocol.md) · [Docs index](../README.md) · [Back to top](../../README.md)
