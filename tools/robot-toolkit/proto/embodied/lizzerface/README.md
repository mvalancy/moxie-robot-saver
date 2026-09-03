# 📁 `lizzerface`

Recovered `.proto` schemas for the **`embodied.lizzerface`** package — the wire contract this part of the robot speaks. Recovered by reverse-engineering, not vendor source; field numbers and names are what the binaries actually use.

| File | Defines |
|---|---|
| [`enums.proto`](enums.proto) | enums `PowerRail`, `LedrPattern`, `FirmwareControlID` … |
| [`lizzerfaceinput.proto`](lizzerfaceinput.proto) | `MotorSetPosEventPB`, `ConfigureMotorEventPB`, `RobotEchoEventPB`, `PowerEnableEventPB`, `PowerDisableEventPB`, `SensorSetEnabledEventPB` … |
| [`lizzerfaceoutput.proto`](lizzerfaceoutput.proto) | `BangEventPB`, `FlapEventPB`, `LightAdcDataEventPB`, `LightEventPB`, `MpuEventPB`, `LizardErrorEventPB` …; enums `LizardErrorEventID`, `PowerState`, `LizardWakeupEventID` |

---
📖 [Docs index](../../../../../docs/README.md) · [Back to top](../../../../../README.md)
