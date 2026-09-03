# 📁 `logging`

Recovered `.proto` schemas for the **`embodied.logging`** package — the wire contract this part of the robot speaks. Recovered by reverse-engineering, not vendor source; field numbers and names are what the binaries actually use.

| File | Defines |
|---|---|
| [`Backup.proto`](Backup.proto) | `BackupStageRequest`, `BackupDataUpdate` |
| [`Cloud.proto`](Cloud.proto) | `Packet`, `Device`, `RobotStatus`, `UserAuthConfirm`, `TopicParam`, `RobotCloudRequest` …; enums `MoxieMode`, `CloudQuery`, `Model` … |
| [`CloudStatus.proto`](CloudStatus.proto) | `CloudStatusRequest`, `CloudStatus`; enums `UserState` |
| [`Family.proto`](Family.proto) | `FamilyInformation`; enums `FamilyRoles` |
| [`FileSync.proto`](FileSync.proto) | `FileEntry`, `FileListQuery`, `FileListResponse`, `FileRead`, `FileResponse`, `FileSyncState`; enums `SyncState`, `RootType` |
| [`Log.proto`](Log.proto) | `LogDevice`, `LogUser`, `LogcatTrace`, `DeviceSettings`, `PropsEntry`, `DeviceSettingsUpdate` … |
| [`LoggingState.proto`](LoggingState.proto) | `LoggingStateChangeRequest`, `LoggingStateUpdate` |
| [`SELUpdate.proto`](SELUpdate.proto) | `SELUpdate`, `SELUpdateSet` |
| [`SomethingHappened.proto`](SomethingHappened.proto) | `SomethingsNotRight` |
| [`SystemMetrics.proto`](SystemMetrics.proto) | `SystemState` |
| [`enums.proto`](enums.proto) | enums `LoggingState`, `LoggingPolicy`, `IOTEndpoint` |

---
📖 [Docs index](../../../../../../docs/README.md) · [Back to top](../../../../../../README.md)
