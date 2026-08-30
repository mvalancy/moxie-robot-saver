# 🧩 Behavior-node catalog — Moxie's behavior-programming vocabulary (`v3.6.4-Zephyr` / OTA `v24.10.803`)

> Reverse-engineered from the decompiled `Assembly-CSharp.dll` (`bo-android`) in the **v24.10.803** image —
> every `RobotBT_*` NodeCanvas task. [`behavior-tree-engine.md`](behavior-tree-engine.md) explains the
> *engine* (NodeCanvas BT/FSM/Dialogue over a Blackboard); **this is the instruction set** — the **65
> `RobotBT_*` nodes** that authored trees are actually built from. These are the API a content author
> scripts Moxie in, and the vocabulary a custom brain must implement to run stock content trees. Each node
> reads or writes the [`StateVariables` blackboard](unity-face-animation.md#4-the-blackboard-bridge-statevariables)
> — so this doc is where the [face engine](unity-face-animation.md), [gaze](gaze-and-attention.md),
> [turn-taking](turn-taking.md), and [action arbiter](robot-actions.md) become *authorable*.

## The node model

A tree is built from NodeCanvas **`ActionTask`** (does something) and **`ConditionTask`** (tests
something) nodes; embodied's are all `RobotBT_*`. Every parameter is a **`BBParameter<T>`** — it binds
either to a **constant** or to a **blackboard variable** (`RobotState_*`), so a node can be driven live by
the running state. Trees come in two flavours the nodes distinguish (`RobotBT_IsLogicBehaviorTree` /
`…IsAnimationBehaviorTree`): a **logic** tree (decisions) and an **animation** tree (expression/motion),
run in parallel over the same blackboard.

## Expression & mood → the face

Set what Moxie feels; the [face engine](unity-face-animation.md#5-the-eyes-eyeseme-blink) renders it.

| Node | Kind | Effect |
|---|---|---|
| `RobotBT_PlaybackMood { BBParameter<ePlaybackMood> Mood }` | action | set `RobotState_PlaybackMood` (one of the [11 moods](unity-face-animation.md#5-the-eyes-eyeseme-blink)) |
| `RobotBT_PlaybackIntensity` | action | set the mood intensity |
| `RobotBT_EyesemeState` | action | set the eye-expression layer's mood |
| `RobotBT_OldPlaybackMood` / `RobotBT_HasMoodChanged` | cond | the previous mood / whether it just changed |
| `RobotBT_EyeGazeEnabled` / `RobotBT_HasEyesemeEnableChanged` | cond | eyeseme gating |

## Gaze → where Moxie looks

Drive the [attention/look-at](gaze-and-attention.md) layer from a tree.

| Node | Effect |
|---|---|
| `RobotBT_GazeControlTarget` | point autonomous gaze at a target |
| `RobotBT_GazeControlManualTarget` | force a manual gaze target |
| `RobotBT_GazeDisabler` | suspend autonomous gaze (for a scripted look) |

## Animation, camera & audio → the outputs

These become [`EBGameTask`s](task-scheduler.md) claiming the relevant [resources](task-scheduler.md#the-outputs-robotresourceflags).

| Node | Effect |
|---|---|
| `RobotBT_PlayAnimation` | play a composite animation clip |
| `RobotBT_CameraShake { bool enabled }` | toggle the [virtual-camera shake](../protocol/unity-mainapp-interface.md#the-virtual-camera-moxies-self-view) |
| `RobotBT_PlayScreenSaver` | play the screensaver/idle visual |
| `RobotBT_PlaySound { BBParameter<Channel> SoundChannel (=FX), BBParameter<string> SoundName }` | play an SFX on a channel |
| `RobotBT_PlayBackgroundSound` | play/stop a background audio bed |

## Sensory idle → the ambient layer

Select the [`SensoryMode`](robot-actions.md) ambient behavior.

| Node | Effect |
|---|---|
| `RobotBT_SensoryMode { BBParameter<SensoryMode> Mode }` | set the sensory mode (`NoTarget`/`Engaged`/`Listening`/`Talking`/`Seeking`/`Earmuffs`…) |
| `RobotBT_SensoryModeDuration` / `RobotBT_TimeSinceSensoryMode` | how long in the mode / since it changed |
| `RobotBT_HasSensoryModeChanged` | cond: mode just changed |

## Turn-taking conditions → react to the conversation

Twelve conditions expose the [`TurnTakingState`](turn-taking.md) five-axis machine to trees, so a tree can
branch on who's talking, engagement, and timing:

| Node | Tests |
|---|---|
| `RobotBT_TurnTakingInTurn` | who owns the turn (Mentor/Moxie) |
| `RobotBT_TurnTakingInMoxieState` / `…InMentorState` | the speaker's state (Idle/Listening/Thinking/Speaking/Interrupted) |
| `RobotBT_TurnTakingInEngagementState` | Earmuffs/Engaged/Seeking/Disengaged |
| `RobotBT_TurnTakingInAssistState` | None/Advanced assist |
| `RobotBT_TurnTakingIsResponseMissing` | the child hasn't responded |
| `RobotBT_TurnTakingTimeIn{Turn,MoxieState,MentorState,EngagementState,AssistState}` | time-in-state (for timeouts) |
| `RobotBT_TurnTakingWaitingForResponseTime` | how long waiting for a reply (re-prompt timer) |

## Accessibility globals → the gates

Set the [`GlobalSettings_*` gates](unity-face-animation.md#9-accessibility-global-gates) from a tree
(also settable by config):

`RobotBT_RobotGlobalSettings` + the five toggles — `…_HideRobotHUDAnimatorAttachments`,
`…_HideRobotVisualEffects`, `…_LessMotion`, `…_MuteRobotSoundEffects`, `…_SlowSpeech`.

## Tree control & flow → structure

Compose and switch trees, and the generic control-flow decorators:

| Node | Effect |
|---|---|
| `RobotBT_SetBehaviourTree { BBParameter<string> BehaviourTreeResourceName }` | switch to a named tree (by resource) |
| `RobotBT_SetAnimationBehaviourTree` / `RobotBT_SetLogicBehaviourTree` | set the animation / logic tree specifically |
| `RobotBT_ConditionalBehaviourTreeState` | gate a sub-tree on a condition |
| `RobotBT_EnableNodeCanvas` / `RobotBT_IsNodeCanvasEnabled` | enable/query the whole tree runtime |
| `RobotBT_EndTreeDisabler` | stop a tree from ending (hold it) |
| `RobotBT_Is{Current,Animation,Logic}BehaviorTree` | which tree is running |
| `RobotBT_Repeater` | repeat a child |
| `RobotBT_TimeoutRandom { BBParameter<float> timeoutMin, timeoutMax }` | a **randomised** timeout (natural-feeling pauses) |
| `RobotBT_TimeSinceStarted` / `RobotBT_IsFirstRun` | timing / first-execution guards |
| `RobotBT_BoolConstant` / `RobotBT_LogMessage` / `RobotBT_TestAction` | literal / debug helpers |

## Markup & events → content hooks

| Node | Effect |
|---|---|
| `RobotBT_IsMarkupGraph` / `RobotBT_IsPlayingMarkupGraph` / `RobotBT_IsMarkupTool` | whether a [markup graph](behavior-markup.md) is active |
| `RobotBT_CheckMarkupVariable` | read a variable set by markup |
| `RobotBT_SendEventAsset` / `RobotBT_CheckEventAsset` | fire / test an [input-event asset](behavior-input-events.md) |
| `RobotBT_CompleteEvent` | signal a behavior/activity complete |

## What this means for the three goals

**① Custom firmware — the headline.** This is the **instruction set** a custom brain must provide to run
Moxie's stock content: content trees are assembled from exactly these 65 nodes over the
[`RobotState_*` blackboard](unity-face-animation.md#4-the-blackboard-bridge-statevariables). Implement
these (mood/gaze/animation/audio/sensory setters, the turn-taking/sensory conditions, the tree-flow
control) and authored behavior "just runs"; author *new* behavior by composing them. This is where all the
subsystem docs become one authorable API.

**② Server revival.** Content a server delivers ([content-delivery](content-delivery.md)) is behavior-tree
assets built from these nodes; a server doesn't run them (they execute on-device) but knowing the
vocabulary is how you author or validate the content you ship.

**③ Pre-801 revival.** No new lever; brain-side, above the network boundary.

---
📖 [Reverse-engineering index](../README.md) · [Behavior-tree engine](behavior-tree-engine.md) · [Face-animation engine](unity-face-animation.md) · [Gaze & attention](gaze-and-attention.md) · [Turn-taking](turn-taking.md) · [Task scheduler](task-scheduler.md) · [Behavior markup](behavior-markup.md)
