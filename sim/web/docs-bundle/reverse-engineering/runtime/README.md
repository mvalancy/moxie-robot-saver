# 🧠 Runtime — the on-device brain, behavior & face engine

How Moxie decides, behaves, perceives and renders its face, recovered from the decompiled Unity brain
(`bo-android`, OTA `v24.10.803`). Read top-down: inputs → decision → arbitration → outputs; content and
perception are the server-facing edges.

| Doc | What it covers |
|---|---|
| [`behavior-input-events.md`](behavior-input-events.md) | The 163 `InputEvent` types (Farmer → `InputEngine` bus) and the 24 proto-serializable ones that cross the ZMQ bus |
| [`behavior-tree-engine.md`](behavior-tree-engine.md) | NodeCanvas (BT · Dialogue · FSM · FlowScript), the catalog of 65 `RobotBT_*` nodes, the 45 `Bht_*` trees, the logic/animation split |
| [`robot-actions.md`](robot-actions.md) | Top-level arbiter: `RobotActionManager` score ladder (Startup > handling > affection > activity > idle), reflexes, activity shells |
| [`task-scheduler.md`](task-scheduler.md) | `EBGameTask`/`EBGTManager` priority + resource arbitration; the 44 `RobotResourceFlags` outputs and the `RobotTaskPriority` ladder |
| [`unity-face-animation.md`](unity-face-animation.md) | Face render: avatar slots, `rig3` blendshapes, `EBAnimGrinder`, `StateVariables` blackboard, Eyeseme/blink, 41 visemes, `SensoryMode`, accessibility gates |
| [`gaze-and-attention.md`](gaze-and-attention.md) | Interest points → `AttentionTarget` → IK look-at with saccades; the published `robotbrain.Attention` state |
| [`turn-taking.md`](turn-taking.md) | `TurnTakingState` five axes, barge-in, DOA speaker scoring, response-wait timer |
| [`behavior-markup.md`](behavior-markup.md) | The `<mark name="cmd:…">` language (24 verbs), SSML dialect, mood/gesture/spurt vocabularies |
| [`content-and-conversation.md`](content-and-conversation.md) | ChatScript + LLM engines, content-module format, volley/session API, schedules, recommender, SEL taxonomy, telehealth |
| [`content-delivery.md`](content-delivery.md) | Dynamic AssetBundles: 3 sources (incl. remote), hash+version manifest, load lifecycle, 24 processors |
| [`perception-pipeline.md`](perception-pipeline.md) | Audio (XMOS → wake/VAD → STT → TTS) and vision (faces, pose, QR, camera activities); XMOS DSP firmware + DFU |
| [`native-boundary.md`](native-boundary.md) | P/Invoke (`liblizzerface` MCU API, `librobinface`, CereVoice, `libdevset`), JNI, and the out-of-process `libbo-*` bus modules |

---
📖 [Reverse-engineering index](../README.md) · [Exploration map](../EXPLORATION-MAP.md)
