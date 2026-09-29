# 🦾 Hardware map — motors, sensors, LEDs, power (`v3.6.4-Zephyr` / OTA `v24.10.803`)

Moxie's actuators, sensors, LEDs and power, enumerated from the firmware's own MCU protobufs
(`embodied.lizzerface`, in [`recovered-proto/`](../protocol/recovered-proto/)), the factory motor apps and
the vendor init scripts. This is the contract a custom firmware must honor to move the robot.
- The **"Lizard"** board is an **STM32F071VBT6** MCU that owns motors, touch, IMU, battery and LEDs. The
  RK3288 talks to it over **UART `/dev/ttyS3`** with a byte-opcode protocol, wrapped as protobuf messages on the ZMQ bus.
- Motor positions are **encoder counts `0..32767`**; there are no joint angles anywhere in the firmware.
- There are **two motor index spaces** (proto `Motor` enum vs `libmotionlib` 0–6). They diverge after index 3.

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

### Arm anatomy — what `ARM_IN_OUT` actually is

The proto enum names motions, not anatomy, so the joint structure is inferred:

- The engineering motor test (`MotorEngActivity`) sweeps the **shoulders (0/2) both ways** (to `8191`
  *and* `24575`), but drives the **elbows (1/3) only toward `MOTOR_MAX_POS`**. A joint powered in one
  direction only is the signature of a **spring/gravity return**.
- Reading (matches observed hardware): the **shoulder is the actuated joint** and the **forearm is
  spring-returned**. The spring pulls the elbow closed and the body pushes it open when the arm rests
  against the side, so forearm position is a function of shoulder angle, with the motor only adding fold.
- **Not proven from the image:** there is no kinematic description and no joint angles, only counts.
  Confirming spring-loaded vs damped vs free needs a bench unit.

> **False lead:** `FabTestSoftware.apk` contains `L Shoulder / L Elbow / R Shoulder / R Elbow / L Wrist /
> R Wrist / L Hip / R Knee / Nose / Neck`. These are **human pose-estimation keypoints** (Moxie has no
> wrists, hips or knees), the vision model's skeleton labels, not motor names.

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

A lower-level **native JNI motor API**, from the factory motor-test app (`bo_motor_test` →
`libmotionlib.so`) and the shared `liblizardJNI.so`:

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

> **This index is not the `Motor` proto enum.** It matches for the four arm motors (0–3), then
> diverges: `libmotionlib` 5/6 = base/body, proto 5/6 = `HEAD_L_R`/`HEAD_TILT`. The proto enum is the
> runtime bus vocabulary; 0–6 is the factory app's ordering. Use the index of the API you call. The
> board silkscreen confirms it ([fcc-teardown](fcc-teardown.md#cross-check-the-motor-list-vs-our-two-index-spaces)).

**Position units:** `MOTOR_MAX_POS = 32767` — positions are a 15-bit range (`0..32767`), rest ≈ `16384`.
`setMotorPositionDt`/`MoveToPositionVt` interpolate to the target over a millisecond duration (a
timed/trajectory move, not an instant set-point). A `SingleMotorTune` activity + a
record/`playBackRunnable` path let the factory capture and replay motor trajectories.

> **No joint angles (degrees) exist in the firmware.** Mechanical end-stops are enforced per motor by
> the MCU via **`CONFIG_LIMIT`** (with `CONFIG_ADJ` for zero/offset), set at factory calibration, so
> counts→degrees is a per-unit constant not in the image. Custom firmware or a
> [simulator](../../architecture/sil-and-cicd.md) must measure it on a bench unit or pick sensible angles.

**Per-motor travel limits & timing** — from the engineering motor test (`MotorEngActivity`, which sweeps
each joint to its endpoints via `MoveToPositionVt(idx, cur, target, milliSecs, segmentTime, mode)`):

| idx | joint | tested travel | move time | notes |
|--:|---|---|--:|---|
| 0 | L arm up/down (shoulder) | **8191 – 24575** (≈ ±8192 around centre) | 1050 ms | shoulders use **~half** the full range |
| 1 | L arm in/out (elbow) | **0 – 32767** (full) | 700 ms | elbows use the full range, faster |
| 2 | R arm up/down (shoulder) | **8191 – 24575** | 1050 ms | |
| 3 | R arm in/out (elbow) | **0 – 32767** (full) | 700 ms | |

Both calls use **`segmentTime = 35 ms`** (the motion-planner tick) and **`motionPlanMode = 1`**. Clamp the
shoulders to the middle half of the range; the elbows travel the full span. Head/base/body (4–6) are
not swept by this test; their limits are bench-TBD. The JNI API and the proto bus drive the same MCU;
the proto/ZMQ path ([robot-ipc-protocol](../protocol/robot-ipc-protocol.md)) is the cleaner seam for a new brain.

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

### Semantic handling events (`embodied.unity`)

Above the raw `MpuEventPB{ID}`, the brain publishes richer **handling events**
(`embodied/unity/MpuPickup.proto`), which is why Moxie giggles when shaken or settles when set down:

| Message | Payload | Meaning |
|---|---|---|
| `MpuPickedUpEventPB` | — | lifted off the surface |
| `MpuPickedUpShakenEventPB` | `shakeDirection` (`MpuShakeDirection`) | **shaken** — *how* it was shaken |
| `MpuPickUpStatusEventPB` | `pitch` (int) | continuous **pitch angle while held** (how it's oriented in a hand) |
| `MpuTiltEventPB` | — | tilted past threshold |
| `MpuPutDownEventPB` | — | set back down |
| `MpuIsNoisyEventPB` | `state` (bool) | the **IMU-noise gate** (below) |

**`MpuShakeDirection`** (the shake axis):
`Up` (0), `Roll` (1), `Pitch` (2), `Yaw` (3), `LeftRight` (4), `ForwardBack` (5), `Invalid` (6).

**`MpuIsNoisyEventPB{state}`**: when Moxie's own motors move, the IMU sees it too. This flag goes `true`
while the signal is untrustworthy so the handling detector **gates itself off during self-motion**. A
custom build that skips it reports phantom "picked up" events on every gesture. These events arrive on
the bus like any other input ([behavior-input-events](../runtime/behavior-input-events.md)), so a server
puppeting Moxie ([telehealth](../protocol/telehealth.md)) can react to being shaken or held.

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

The Lizard board is an **STM32F071VBT6** (ARM Cortex-M0, LQFP-100, 128 KB flash; part number read off
the chip in the FCC photos, [fcc-teardown](fcc-teardown.md)) with its own firmware, updated from Android
over UART by `bo-firmwareUpdate` / `me.embodied.firmwareupdatelib.fwUpdateLibEntry`
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

Each file begins with a **SHA-1 line** then Intel-HEX records. DFU faults surface as
`LizardErrorEventPB` `FIRMWARE_*` codes. The MCU also has a direct SWD/UART header
([fcc-teardown](fcc-teardown.md#stm32f071vbt6-the-lizard-motor-mcu)).

## MCU firmware & health (`LizardErrorEventPB.LizardErrorEventID`)

The Lizard board reports a rich error/status stream (`1000`–`1051`), including battery over-temp
(`1001`), motor-IC alerts (`1004`), IMU/LED-IC loss (`1006`/`1007`), the whole **firmware-download
state machine** (`FIRMWARE_*`, `1009`–`1031`), motor stall (`1045`), charging (`CHARGING_EVENT=1050`),
and **`WAKEUP_ANDROID_EVENT=1051`** — the MCU waking the Android SoC. `bo-firmwareUpdate` /
`RobotControlFirmwareEventPB{ CONTROL_RESET_MOTOR_IC }` drive MCU DFU over UART. Custom firmware can
leave the Lizard MCU stock and just speak this protocol.

### The remaining MCU control messages

The last `embodied.lizzerface` control messages (closing the namespace):

| Message | Meaning |
|---|---|
| `RobotControlFirmwareEventPB` (`FirmwareControlID`) | firmware-level control — the only op is **`CONTROL_RESET_MOTOR_IC`** (reset the motor-driver IC) |
| `SensorSetEnabledEventPB { SensorPB sensor, bool enabled }` | toggle an MCU sensor — the only enumerated sensor is **`FLAP_SENSOR`** (the ear/mouth flap) |
| `RobotEchoEventPB { string message }` | echo/ping the MCU with a payload string (the round-trip link test; native `robot_echo`, [native-boundary](../runtime/native-boundary.md#1-in-process-native-dllimport)) |
| `RevisionLevelEventPB { Revision_Level level }` | the MCU reporting its **hardware revision** (`Revision_Level`, [hardware revisions](#hardware-revisions-revision_level)) |
| `BangEventPB {}` | a **payload-less MCU event** — a discrete "bang"/impact-style trigger (no data, just the edge) |

## Raw UART command set (`Lizzerface.Commands`)

Below the `embodied.lizzerface` proto/ZMQ abstraction, the Android side (`Lizzerface` static class →
`liblizzerface.so`/`liblizardJNI.so`) speaks a **byte-opcode UART protocol** to the MCU on `/dev/ttyS3`.
The opcode enum (recovered verbatim from `Assembly-CSharp`) — most are **MCU→host** events, the last two
are **host→MCU** commands:

| Opcode | Command | Dir | Meaning |
|--:|---|:--:|---|
| `160` | `CMD_TKEY_EVENT` | ← | capacitive touch-key (`TouchID`: BACK/TUMMY/hands) |
| `161` | `CMD_SWITCH_EVENT` | ← | switch (`SwitchID`: SWITCH0-2, `DC_PLUG`=3, `LEFT_ARM`=16, `RIGHT_ARM`=17) |
| `164` | `CMD_MOTOR_STALL_EVENT` | ← | a motor stalled |
| `165` | `CMD_MPU_EVENT` | ← | IMU gesture (`MpuEventID`: PICKED_UP/PUT_DOWN/TILT/FORCE_PUTDOWN) |
| `167` | `CMD_WAKEUP_ADR_EVENT` | ← | wake the Android SoC (`WakeupID`: `TOUCH_WAKEUP`/`PICKUP_WAKEUP`/`DCPLUG_WAKEUP`) |
| `169`/`170` | `CMD_BATTERY_LEVEL` / `CMD_BATTERY_TEMP` | ← | battery telemetry |
| `176`–`190` | `CMD_ERR_*` | ← | fault codes (battery over-temp `176`, discharge over-current `177`, battery lost `178`, motor-IC alert `179`, motor fail-boot `180`, IMU lost `181`, LED-IC lost `182`, host-messy-cmd `183`, bstate `184`, IMU-update-timeout `185`, FW-bug-len2/4 `186`/`187`, base-motor-spin `188`, battery-PEC `189`, body-touch `190`) — surfaced to Android as `LizardErrorEventPB` (`1000`+) |
| `196` | `CMD_SEND_WATCHDOG_INFO` | ← | watchdog status |
| `220` | `CMD_RESTART_MOTOR_POS_FD` | ⇄ | motor-position **feedback** frame (body/waist/head values) — the loop below |
| `221` | `CMD_SET_POWER_STATE` | → | set the power state (below) |

**Host→MCU command API** (`Lizzerface` methods → UART frames):

| Call | Effect |
|---|---|
| `motor_set_pos(byte motor, ushort pos)` | set one motor's target position (16-bit, `0…MOTOR_MAX` = 32767) |
| `SetPowerState(Power_State ps)` | `POWER_STATE_ACTIVE / SLEEPING / STANDBY / SHUTDOWN` (opcode `221`) |
| `SetHeartBrightness(byte brightness)` | chest heart-LED brightness (the MCU owns the LED IC) |
| `reset_xmos()` | the MCU hard-resets the **XMOS** audio DSP (see [`hal-and-drivers.md`](../firmware/hal-and-drivers.md)) |
| `SetQuietBoot(bool isQuiet, bool isDone)` | quiet-boot flag (pairs with `sys.embodied.quiet_boot_done`) |
| `PublishBatteryStatus()` | request/emit a battery report |

**Real-time motor loop.** `Lizzerface` runs a dedicated `Lizzeface.EventReciever` thread at a fixed
**16.667 ms cadence (≈60 Hz)** (`MSPERLOOP = 16.666668`). Each tick it reads the motor-position feedback
frame (opcode `220`, i.e. `0xDC`: body/waist/head values), republishes it on the **ZeroMQ** bus
([robot-ipc-protocol](../protocol/robot-ipc-protocol.md)), and paces the next command. A flood guard
(`HOST_MESSY_CMD`, ≥40 cmds / 1.5 s → fault `183`) protects the MCU. A custom Android side drives the
body by writing `motor_set_pos` frames at ~60 Hz and consuming the `220` feedback; the position servo
lives in the MCU, not the SoC.

---
📖 [Reverse-engineering index](../README.md) · [IPC protocol](../protocol/robot-ipc-protocol.md) · [Docs index](../../README.md) · [Back to top](../../../README.md)
