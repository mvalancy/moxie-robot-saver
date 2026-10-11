# 📁 launcher

The [`embodied.launcher`](../../../../../docs/reverse-engineering/protocol/proto-catalog.md#embodiedlauncher) package: each robot component's lifecycle state (`UNKNOWN`, `Running`, `NotRunning` or `Fault`), as reported to the Launcher.
[Boot and launcher](../../../../../docs/reverse-engineering/firmware/boot-and-launcher.md#components-bocomponent) explains the components and how the Launcher restarts one that reports `Fault` or `NotRunning`.

| File | Defines |
|---|---|
| [`ComponentState.proto`](ComponentState.proto) | `ComponentState`, `SetComponentState`; enums `State` |

---
📖 [embodied](../README.md) · [Docs index](../../../../../docs/README.md) · [Back to top](../../../../../README.md)
