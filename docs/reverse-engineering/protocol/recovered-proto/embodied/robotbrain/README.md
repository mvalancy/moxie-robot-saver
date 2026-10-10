# 📁 robotbrain

Three packages share this folder: [`embodied.robotbrain`](../../../proto-catalog.md#embodiedrobotbrain) (35 files), [`embodied.robotbrain.tags`](../../../proto-catalog.md#embodiedrobotbraintags) (`Tags.proto`) and [`embodied.robotbrain.serialized`](../../../proto-catalog.md#embodiedrobotbrainserialized) (`EventsAndHolidaysTags.proto`).
The rest of the serialized package is in [`serialized/`](serialized/README.md).
Together they carry ChatScript, content modules and schedules, intents, contexts, idle/mentor/STAR, remote chat and users.
[Content and conversation](../../../../runtime/content-and-conversation.md) and [remote chat protocol](../../../remote-chat-protocol.md) explain them; [runtime control](../../../runtime-control.md) covers the volume, reset and ChatScript commands.

| File | Defines |
|---|---|
| [`BedTimeStatus.proto`](BedTimeStatus.proto) | `BedTimeStatus` |
| [`ChatResponse.proto`](ChatResponse.proto) | `ActivityUpdateData`, `ChatResponse`, `Engagement`, `InputVarsEntry`; enums `OutputType`, `FallbackType`, `BlockedType`, `ResponseSource` |
| [`ChatScriptError.proto`](ChatScriptError.proto) | `ChatScriptError` |
| [`ChatScriptState.proto`](ChatScriptState.proto) | `ChatScriptReady`, `ChatScriptException`, `ChatbotListeningRequest`, `AllowCutoffEvent` |
| [`ContentMetaTags.proto`](ContentMetaTags.proto) | `CognitiveTag`, `IntimacyTag`, `ContentMetaList` |
| [`ContentModule.proto`](ContentModule.proto) | `ContentDetail`, `LegacyDataEntry`, `ModuleDetail`; enums `ContentRules`, `ContentSource`, `FirstTimeRules`, `ModuleCategory` |
| [`ContentSchedule.proto`](ContentSchedule.proto) | `ContentModule`, `TagList`, `ScheduleConfig`, `EndOfSessionConfig`, `RewardsConfig`, `MissionConfig`, `ContentSchedule`, `HubConfig`, `ScheduleStart` |
| [`ContentTags.proto`](ContentTags.proto) | `Tag`, `ContentTag` |
| [`Contexts.proto`](Contexts.proto) | `Context`, `GlobalContext`, `EnvironmentContext`, `ConversationContext`, `Contexts` |
| [`DailySchedule.proto`](DailySchedule.proto) | `DailySchedule` |
| [`EnableBook.proto`](EnableBook.proto) | `EnableBook` |
| [`EnableDraw.proto`](EnableDraw.proto) | `EnableDraw` |
| [`EnableICModule.proto`](EnableICModule.proto) | `EnableICModule` |
| [`EnableQRCode.proto`](EnableQRCode.proto) | `EnableQRCode` |
| [`EventsAndHolidaysTags.proto`](EventsAndHolidaysTags.proto) | `EventsAndHolidaysData`, `Holiday` |
| [`Fallback.proto`](Fallback.proto) | `Fallback` |
| [`IdleStateChange.proto`](IdleStateChange.proto) | `IdleStateChange` |
| [`Intent.proto`](Intent.proto) | `IntentPB` |
| [`LineStore.proto`](LineStore.proto) | `LineStoreSerialState`, `LineStoreEntry` |
| [`LookAtMe.proto`](LookAtMe.proto) | `LookAtMeRequest` |
| [`MentorBehavior.proto`](MentorBehavior.proto) | `MentorBehavior`, `MentorBehaviorSet`; enums `MentorAction`, `EndedReason` |
| [`ModuleTag.proto`](ModuleTag.proto) | `ModuleTagInfo`, `ModuleTagData`, `ModuleTag`, `ContentInfo`, `ContentData` |
| [`PhraseHints.proto`](PhraseHints.proto) | `PhraseHints`, `NameHints`, `NativeHints` |
| [`PrimaryUserNameChange.proto`](PrimaryUserNameChange.proto) | `PrimaryUserNameChange` |
| [`RemoteChat.proto`](RemoteChat.proto) | `RemoteChatContext`, `ExecuteReturn`, `RecommendationContext`, `Recommendation`, `RemoteDataQuery`, `RemoteChatRequest`, `InputVarsEntry`, `RemoteDialog`, `RemoteSignals`, `MultiUtterSignals`, `TagScore`, `RemoteChatOutput`, `RemoteChatInput`, `InputSafety`, `RemoteConsistencyControl`, `RemoteChatMetrics`, `HighLevel`, `Entity`, `PosNegSet`, `RateSet`, `EngagementSet`, `Numerics`, `EntityCounts`, `Classifications`, `EventSubscription`, `RemoteChatAction`, `ActionArgsEntry`, `IntentResult`, `EntitiesEntry`, `IntentRank`, `RemoteDataBlock`, `FlowInfo`, `RemoteChatResponse`; enums `Urgency`, `Query`, `DialogAct`, `EmotionState`, `Signal`, `ActionID`, `ResultCode` |
| [`RemoteResponseData.proto`](RemoteResponseData.proto) | `RemoteResponseData` |
| [`Reset.proto`](Reset.proto) | `SoftReset`, `HardReset` |
| [`STARGoalState.proto`](STARGoalState.proto) | `STARGoalStateChange`, `STARGoalSuccess`, `STARGoalFailure` |
| [`SessionState.proto`](SessionState.proto) | `SessionUser`, `SessionState`; enums `RecordMode` |
| [`Starbits.proto`](Starbits.proto) | `StarBitsEarned` |
| [`System.proto`](System.proto) | `SystemVolumeModify`, `SystemVolumeState`, `SystemSlowInputModify` |
| [`Tags.proto`](Tags.proto) | `Tag`, `GoalLevel`, `Weight`, `SELTagInfo` |
| [`TargetUser.proto`](TargetUser.proto) | `TargetedUser`, `NoTargetedUser`, `WorldLocation`, `InterestPoint`, `Attention`; enums `AttentionState` |
| [`TopicChange.proto`](TopicChange.proto) | `TopicChange` |
| [`TurnTaking.proto`](TurnTaking.proto) | `TurnTakingState`; enums `TurnOwner`, `MentorState`, `MoxieState`, `EngagementState`, `TurnTakingAssistanceState` |
| [`UserRecognition.proto`](UserRecognition.proto) | `LearnUserState`; enums `State` |
| [`WaitTimeout.proto`](WaitTimeout.proto) | `WaitTimeout` |

## Subfolder

- [`serialized/`](serialized/README.md) — persisted brain state.

---
📖 [embodied](../README.md) · [Docs index](../../../../../../docs/README.md) · [Back to top](../../../../../../README.md)
