# 🎛️ Runtime control — imperative commands to a running brain (`v3.6.4-Zephyr` / OTA `v24.10.803`)

The **imperative bus commands** that change a *running* Moxie from outside: set the volume now, slow
the interaction for accessibility, force listening, gate barge-in, or reset the brain. Recovered from
`embodied/robotbrain/{System,Reset,ChatScriptState}.proto` (`package embodied.robotbrain`) in the
**v24.10.803** image; each is live in the C# brain (19–24 refs apiece).

Config ([`settings-schema.md`](../firmware/settings-schema.md),
[`RobotCloudConfig`](device-config-and-telemetry.md#robotcloudconfig-the-master-config-document-cloud-robot))
sets the *persisted default*; these change *live state this instant*. Inject them on the ZMQ bus
([robot-ipc-protocol](robot-ipc-protocol.md)) or over MQTT `/commands/zmq` ([cloud-protocol](cloud-protocol.md)).

```proto
message SystemVolumeModify      { sint32 volume; bool relative; }            // change volume now
message SystemVolumeState       { uint32 volume; }                           // brain reports current volume
message SystemSlowInputModify   { bool slow_input; }                         // toggle "slowinput" mode
message ChatbotListeningRequest { string user; string bot; bool listening; } // force listen on/off
message AllowCutoffEvent        { bool allow; }                              // permit / block barge-in
message SoftReset { }                                                        // reset conversational/session state
message HardReset { }                                                        // full brain restart
message ChatScriptReady         { string user; string bot; }
message ChatScriptException     { string message; bool restore_default; }
```

## Audio volume — `SystemVolume*`

`SystemVolumeModify`: `relative = false` sets volume to `volume`; `relative = true` adds `volume` as a
**signed** delta (−1 nudges down). The live lever versus the persisted `audio_volume` in
`RobotCloudConfig`/settings. `SystemVolumeState` reports the current level for a UI. (Clip-level playback
volume is separate: `AudioNotifVolumeChangeEventPB` in the [MAINAPP interface](unity-mainapp-interface.md#audio-out-tts-sfx-playback-control).)

## Accessibility pacing — `SystemSlowInputModify`

Toggles the **`slowinput`** mode: longer waits and slower turn cadence for children who need more time.
The live counterpart of the child profile's `input_speed`
([device config](device-config-and-telemetry.md#the-child-pii-encryption-boundary)).

## Listening and barge-in

- **`ChatbotListeningRequest`** forces the chatbot to start/stop listening for a `user`/`bot` pair (open or close the mic on demand).
- **`AllowCutoffEvent`** permits or blocks **barge-in** — the imperative form of the policy in
  [turn-taking](../runtime/turn-taking.md#barge-in-interruption). A module sets `allow=false` during a line that must not be interrupted.

## Reset — `SoftReset` / `HardReset`

Empty-payload signals. `SoftReset` clears conversational/session state (recover a stuck dialog);
`HardReset` restarts the brain. Both are *brain-level*, distinct from the device-level
`STATE_SILENT_REBOOT` / `RESTART_XMOS` in [power-and-system-events](power-and-system-events.md).

## ChatScript lifecycle

The local [ChatScript engine](../runtime/content-and-conversation.md) reports `ChatScriptReady` when
initialized for a `user`/`bot`, and `ChatScriptException` on error — `restore_default = true` asks the
brain to fall back to the default script (a self-heal path complementary to the
[offline fallback tree](offline-and-brain-state.md)). `WaitTimeout{ time }` is the generic "a wait of
`time` seconds elapsed" signal the dialog manager uses to move on.

## For the three goals

- **Server revival:** live control of a running robot — mute/adjust volume, flip pacing, open/close the mic, gate barge-in,
  recover a wedged session with `SoftReset` — all over MQTT `/commands/zmq`. Encoders: [`moxie_toolkit/bus.py`](../../../tools/robot-toolkit/moxie_toolkit/bus.py).
- **Custom firmware:** the control inputs a custom brain must honor to be driven like stock Moxie.
- **Pre-801:** no new lever ([network-trust](network-trust.md)).

---
📖 [Reverse-engineering index](../README.md) · [Settings schema](../firmware/settings-schema.md) · [Turn-taking](../runtime/turn-taking.md) · [Robot IPC protocol](robot-ipc-protocol.md) · [Power & system events](power-and-system-events.md) · [Device config & telemetry](device-config-and-telemetry.md)
