# 📁 `system`

Recovered `.proto` schemas for the **`embodied.power`** package — the wire contract this part of the robot speaks. Recovered by reverse-engineering, not vendor source; field numbers and names are what the binaries actually use.

| File | Defines |
|---|---|
| [`PowerEvents.proto`](PowerEvents.proto) | `SystemSuspendPB`, `SystemResumePB`, `SystemRecoverRequest`, `PowerStatePB`, `PowerStayAwakePB`; enums `ResumeCause`, `RecoveryTarget`, `State` |
| [`SystemEvents.proto`](SystemEvents.proto) | `WifiConnectionState`, `STTConnectionState`, `OTAStatus`, `WifiRecoverRequest`, `ShutdownRequest`, `SystemShutdown` …; enums `DisengageReason` |
| [`TimeEvents.proto`](TimeEvents.proto) | `TimeZoneInfo`, `UserAlarmRequest`, `UserAlarmTriggered`; enums `ReservedTimers` |

---
📖 [Docs index](../../../../../docs/README.md) · [Back to top](../../../../../README.md)
