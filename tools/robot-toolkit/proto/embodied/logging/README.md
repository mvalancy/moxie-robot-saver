# 📁 logging

The [`embodied.logging`](../../../../../docs/reverse-engineering/protocol/proto-catalog.md#embodiedlogging) package: the data model between the robot and its backend.
It covers config (`RobotCloudConfig`, `ServiceConfiguration`, `IOTEndpoint`), status, `Packet` telemetry, content queries, file sync, backup and SEL updates.
[Device config and telemetry](../../../../../docs/reverse-engineering/protocol/device-config-and-telemetry.md) explains the data model, and [cloud protocol](../../../../../docs/reverse-engineering/protocol/cloud-protocol.md) the transport and endpoint hosts.

| File | Defines |
|---|---|
| [`Backup.proto`](Backup.proto) | `BackupStageRequest`, `BackupDataUpdate` |
| [`Cloud.proto`](Cloud.proto) | `Packet`, `Device`, `RobotStatus`, `UserAuthConfirm`, `TopicParam`, `RobotCloudRequest`, `ContentPreferences`, `SELPreference`, `WakeSchedule`, `WakeEntry`, `SchedulePreferences`, `ParentRequest`, `ChildEncrypted`, `ChildDecrypted`, `OtaUpdate`, `SwitchUserConfig`, `RobotCloudConfig`, `ActivityContext`, `ActivityUpdate`, `EndpointConfiguration`, `ServiceConfiguration`, `EndpointStore`, `PairingComplete`, `RestoreResult`, `CloudQueryRequest`, `MetaDataResponse`, `CloudQueryResponse`, `IDFRecord`, `LicenseRecord`, `VersionedContextsEntry`, `DynamicLine`, `GRLTokenRequest`, `GRLTokenUpdate`; enums `MoxieMode`, `CloudQuery`, `Model`, `ErrorCondition`, `UserAction`, `ConnectionType`, `LicenseID`, `QueryResponseCode` |
| [`CloudStatus.proto`](CloudStatus.proto) | `CloudStatusRequest`, `CloudStatus`; enums `UserState` |
| [`Family.proto`](Family.proto) | `FamilyInformation`; enums `FamilyRoles` |
| [`FileSync.proto`](FileSync.proto) | `FileEntry`, `FileListQuery`, `FileListResponse`, `FileRead`, `FileResponse`, `FileSyncState`; enums `SyncState`, `RootType` |
| [`Log.proto`](Log.proto) | `LogDevice`, `LogUser`, `LogcatTrace`, `DeviceSettings`, `PropsEntry`, `DeviceSettingsUpdate`, `ProtoSubscribe`, `Ping` |
| [`LoggingState.proto`](LoggingState.proto) | `LoggingStateChangeRequest`, `LoggingStateUpdate` |
| [`SELUpdate.proto`](SELUpdate.proto) | `SELUpdate`, `SELUpdateSet` |
| [`SomethingHappened.proto`](SomethingHappened.proto) | `SomethingsNotRight` |
| [`SystemMetrics.proto`](SystemMetrics.proto) | `SystemState` |
| [`enums.proto`](enums.proto) | enums `LoggingState`, `LoggingPolicy`, `IOTEndpoint` |

---
📖 [embodied](../README.md) · [Docs index](../../../../../docs/README.md) · [Back to top](../../../../../README.md)
