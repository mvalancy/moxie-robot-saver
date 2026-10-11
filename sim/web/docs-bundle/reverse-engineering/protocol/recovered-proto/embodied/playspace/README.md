# 📁 playspace

The [`embodied.playspace`](../../../proto-catalog.md#embodiedplayspace) package: the play-space model.
Its messages cover a session's connect, start, end and disconnect, queries and responses, game data, outputs, triggers and metrics.
Coverage is partial. [Content and conversation](../../../../runtime/content-and-conversation.md) explains its turn-state, age-group, trigger and exit-code enums; the [exploration map](../../../../EXPLORATION-MAP.md#proto-namespaces) defers the rest.

| File | Defines |
|---|---|
| [`PlaySpace.proto`](PlaySpace.proto) | `PlaySpaceHeader`, `PlaySpaceConnect`, `PlaySpaceQuery`, `PlaySpaceStart`, `GameDataEntry`, `PlaySpaceEnd`, `PlaySpaceDisconnect`, `PlaySpaceResponse`, `GameData`, `PlaySpaceState`, `PlaySpaceMoxieState`, `Output`, `PlaySpaceOutput`, `PlaySpaceTrigger`, `PlaySpaceInput`, `EntitiesEntry`, `PlaySpaceMetrics`, `MetricsEntry`; enums `Source`, `ResponseCode`, `MoxieState`, `TurnState`, `ExitCode`, `AgeGroup`, `TriggerAction`, `TriggerDuration`, `Query` |

---
📖 [embodied](../README.md) · [Docs index](../../../../../../docs/README.md) · [Back to top](../../../../../../README.md)
