# 📁 lizzerface

The [`embodied.lizzerface`](../../../proto-catalog.md#embodiedlizzerface) package: commands to and events from the body microcontroller, the Lizard MCU.
Commands drive motors, power rails, sensors and LEDs; events report touch, switches, light, IMU, battery, servos and errors.
The [hardware map](../../../../hardware/hardware-map.md) explains every message except `LizardWakeupEventPB`, which reports what woke the MCU; [behavior input events](../../../../runtime/behavior-input-events.md) lists it.

| File | Defines |
|---|---|
| [`enums.proto`](enums.proto) | enums `PowerRail`, `LedrPattern`, `FirmwareControlID`, `Revision_Level`, `SensorPB`, `Motor`, `ConfigParam`, `MpuEventID`, `SwitchID`, `TouchID` |
| [`lizzerfaceinput.proto`](lizzerfaceinput.proto) | `MotorSetPosEventPB`, `ConfigureMotorEventPB`, `RobotEchoEventPB`, `PowerEnableEventPB`, `PowerDisableEventPB`, `SensorSetEnabledEventPB`, `SetLedrEventPB`, `RobotControlFirmwareEventPB` |
| [`lizzerfaceoutput.proto`](lizzerfaceoutput.proto) | `BangEventPB`, `FlapEventPB`, `LightAdcDataEventPB`, `LightEventPB`, `MpuEventPB`, `LizardErrorEventPB`, `ServoPosFdbackEventPB`, `ServoStallEventPB`, `SwitchEventPB`, `TouchEventPB`, `RevisionLevelEventPB`, `BatteryEventPB`, `PowerStateEventPB`, `LizardWakeupEventPB`; enums `LizardErrorEventID`, `PowerState`, `LizardWakeupEventID` |

---
📖 [embodied](../README.md) · [Docs index](../../../../../../docs/README.md) · [Back to top](../../../../../../README.md)
