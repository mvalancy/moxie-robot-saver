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

## MCU firmware & health (`LizardErrorEventPB.LizardErrorEventID`)

The Lizard board reports a rich error/status stream (`1000`–`1051`), including battery over-temp
(`1001`), motor-IC alerts (`1004`), IMU/LED-IC loss (`1006`/`1007`), the whole **firmware-download
state machine** (`FIRMWARE_*`, `1009`–`1031`), motor stall (`1045`), charging (`CHARGING_EVENT=1050`),
and **`WAKEUP_ANDROID_EVENT=1051`** — the MCU waking the Android SoC. `bo-firmwareUpdate` /
`RobotControlFirmwareEventPB{ CONTROL_RESET_MOTOR_IC }` drive MCU DFU over UART. Custom firmware that
replaces the Android side can leave the Lizard MCU stock and just speak this protocol.

---
📖 [Reverse-engineering index](README.md) · [IPC protocol](robot-ipc-protocol.md) · [Docs index](../README.md) · [Back to top](../../README.md)
