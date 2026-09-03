# 📁 `robotbrain`

Recovered `.proto` schemas for the **`embodied.robotbrain`** package — the wire contract this part of the robot speaks. Recovered by reverse-engineering, not vendor source; field numbers and names are what the binaries actually use.

| File | Defines |
|---|---|
| [`BedTimeStatus.proto`](BedTimeStatus.proto) | `BedTimeStatus` |
| [`ChatResponse.proto`](ChatResponse.proto) | `ActivityUpdateData`, `ChatResponse`, `Engagement`, `InputVarsEntry`; enums `OutputType`, `FallbackType`, `BlockedType` … |
| [`ChatScriptError.proto`](ChatScriptError.proto) | `ChatScriptError` |
| [`ChatScriptState.proto`](ChatScriptState.proto) | `ChatScriptReady`, `ChatScriptException`, `ChatbotListeningRequest`, `AllowCutoffEvent` |
| [`ContentMetaTags.proto`](ContentMetaTags.proto) | `CognitiveTag`, `IntimacyTag`, `ContentMetaList` |
| [`ContentModule.proto`](ContentModule.proto) | `ContentDetail`, `LegacyDataEntry`, `ModuleDetail`; enums `ContentRules`, `ContentSource`, `FirstTimeRules` … |
| [`ContentSchedule.proto`](ContentSchedule.proto) | `ContentModule`, `TagList`, `ScheduleConfig`, `EndOfSessionConfig`, `RewardsConfig`, `MissionConfig` … |
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
| [`RemoteChat.proto`](RemoteChat.proto) | `RemoteChatContext`, `ExecuteReturn`, `RecommendationContext`, `Recommendation`, `RemoteDataQuery`, `RemoteChatRequest` …; enums `Urgency`, `Query`, `DialogAct` … |
| [`RemoteResponseData.proto`](RemoteResponseData.proto) | `RemoteResponseData` |
| [`Reset.proto`](Reset.proto) | `SoftReset`, `HardReset` |
| [`STARGoalState.proto`](STARGoalState.proto) | `STARGoalStateChange`, `STARGoalSuccess`, `STARGoalFailure` |
| [`SessionState.proto`](SessionState.proto) | `SessionUser`, `SessionState`; enums `RecordMode` |
| [`Starbits.proto`](Starbits.proto) | `StarBitsEarned` |
| [`System.proto`](System.proto) | `SystemVolumeModify`, `SystemVolumeState`, `SystemSlowInputModify` |
| [`Tags.proto`](Tags.proto) | `Tag`, `GoalLevel`, `Weight`, `SELTagInfo` |
| [`TargetUser.proto`](TargetUser.proto) | `TargetedUser`, `NoTargetedUser`, `WorldLocation`, `InterestPoint`, `Attention`; enums `AttentionState` |
| [`TopicChange.proto`](TopicChange.proto) | `TopicChange` |
| [`TurnTaking.proto`](TurnTaking.proto) | `TurnTakingState`; enums `TurnOwner`, `MentorState`, `MoxieState` … |
| [`UserRecognition.proto`](UserRecognition.proto) | `LearnUserState`; enums `State` |
| [`WaitTimeout.proto`](WaitTimeout.proto) | `WaitTimeout` |

## Subfolders

- [`serialized/`](serialized/) — see its own README.

---
📖 [Docs index](../../../../../../docs/README.md) · [Back to top](../../../../../../README.md)
