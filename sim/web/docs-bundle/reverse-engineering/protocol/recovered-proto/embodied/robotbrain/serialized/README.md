# 📁 serialized

The [`embodied.robotbrain.serialized`](../../../../proto-catalog.md#embodiedrobotbrainserialized) package: what the robot saves to disk so it survives reboots and keeps talking offline.
That is the offline fallback tree (`FallbackInfo`), the resume point (`CSData`) and the recommender's history (`UserRecommendationData`).
[Offline and brain state](../../../../offline-and-brain-state.md) explains them.
The package's fourth file, `EventsAndHolidaysTags.proto`, sits in the parent [`robotbrain/`](../README.md) folder.

| File | Defines |
|---|---|
| [`CSData.proto`](CSData.proto) | `CSData` |
| [`FallbackInfo.proto`](FallbackInfo.proto) | `NodeFallback`, `ContentIDFallback`, `ModuleFallback`, `FallbackInfo`; enums `FallbackOptions` |
| [`UserRecommendationData.proto`](UserRecommendationData.proto) | `UserRecommendationData`, `SparseValues`, `TagHistory`, `TagHistoryEntry`, `RandomTagState` |

---
📖 [robotbrain](../README.md) · [Docs index](../../../../../../../docs/README.md) · [Back to top](../../../../../../../README.md)
