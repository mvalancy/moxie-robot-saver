# 📁 system

Two packages share this folder: [`embodied.power`](../../../proto-catalog.md#embodiedpower) (`PowerEvents.proto`) and [`embodied.sys`](../../../proto-catalog.md#embodiedsys) (`SystemEvents.proto`, `TimeEvents.proto`).
`embodied.power` carries suspend, resume, recovery, stay-awake and power state.
`embodied.sys` carries Wi-Fi, STT and OTA status, shutdown, unpair and debug requests, time zone and alarms.
[Power and system events](../../../power-and-system-events.md) explains both.

| File | Defines |
|---|---|
| [`PowerEvents.proto`](PowerEvents.proto) | `SystemSuspendPB`, `SystemResumePB`, `SystemRecoverRequest`, `PowerStatePB`, `PowerStayAwakePB`; enums `ResumeCause`, `RecoveryTarget`, `State` |
| [`SystemEvents.proto`](SystemEvents.proto) | `WifiConnectionState`, `STTConnectionState`, `OTAStatus`, `WifiRecoverRequest`, `ShutdownRequest`, `SystemShutdown`, `DebugConfigureRequest`, `UnpairUserRequest`, `UnpairUserReady`; enums `DisengageReason` |
| [`TimeEvents.proto`](TimeEvents.proto) | `TimeZoneInfo`, `UserAlarmRequest`, `UserAlarmTriggered`; enums `ReservedTimers` |

---
📖 [embodied](../README.md) · [Docs index](../../../../../../docs/README.md) · [Back to top](../../../../../../README.md)
