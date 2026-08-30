# 🎛️ Runtime control — imperative commands to a running brain (`v3.6.4-Zephyr` / OTA `v24.10.803`)

> Recovered from `embodied/robotbrain/{System,Reset,ChatScriptState}.proto` (`package
> embodied.robotbrain`) in the **v24.10.803** image. These are the **imperative bus commands** that
> modify a *running* Moxie from outside — set the volume now, slow the interaction down for accessibility,
> force it to listen, allow a barge-in, or reset the brain. They're the counterpart to the **declarative**
> config in [`settings-schema.md`](../firmware/settings-schema.md) and
> [`RobotCloudConfig`](device-config-and-telemetry.md#robotcloudconfig-the-master-config-document-cloud-robot):
> config sets the *persisted default*; these change *live state this instant*. Injectable onto the ZMQ bus
> ([`robot-ipc-protocol.md`](robot-ipc-protocol.md)) or over MQTT `/commands/zmq`
> ([`cloud-protocol.md`](cloud-protocol.md)), so a server, the parent app, or a custom module can drive
> them. All confirmed live in the C# brain (each 19–24 refs).

## Audio volume — `SystemVolume*`

```proto
message SystemVolumeModify { sint32 volume; bool relative; }   // change the volume now
message SystemVolumeState  { uint32 volume; }                  // the brain reports current volume
```

- **`SystemVolumeModify`** sets volume live: `relative = false` → set to `volume`; `relative = true` →
  add `volume` (a **signed** delta, so −1 nudges down). This is the "turn it down right now" lever, versus
  the persisted `audio_volume` in `RobotCloudConfig`/settings.
- **`SystemVolumeState`** is the brain's report of the current level (for a UI/telemetry to reflect).

## Accessibility pacing — `SystemSlowInputModify`

```proto
message SystemSlowInputModify { bool slow_input; }   // toggle the "slowinput" interaction mode
```

Toggles the **`slowinput`** mode — the brain slows its interaction pacing (longer waits, slower turn
cadence) for children who need more time. It's the live toggle for the same intent as the child profile's
`input_speed` ([device-config-and-telemetry](device-config-and-telemetry.md#the-child-pii-encryption-boundary)):
an accessibility control a server or the parent app can flip mid-session.

## Listening & barge-in — `ChatbotListeningRequest`, `AllowCutoffEvent`

```proto
message ChatbotListeningRequest { string user; string bot; bool listening; }  // force listen on/off
message AllowCutoffEvent        { bool allow; }                               // permit / block barge-in
```

- **`ChatbotListeningRequest`** forces the chatbot to start/stop **listening** for a given `user`/`bot`
  pair — make Moxie open the mic on demand, or close it.
- **`AllowCutoffEvent`** permits or blocks **barge-in** (the child cutting Moxie off mid-line) — the
  imperative form of the barge-in policy in [`turn-taking.md`](../runtime/turn-taking.md#barge-in-interruption). A module sets
  `allow=false` during a line that must not be interrupted, `true` when interruption is welcome.

## Reset — `SoftReset` / `HardReset`

```proto
message SoftReset { }   // reset the brain's conversational/session state
message HardReset { }   // full brain restart
```

Two escalating resets of the brain (both empty-payload signals): **`SoftReset`** clears the conversational
/ session state (recover from a stuck dialog without a full restart), **`HardReset`** restarts the brain
outright. These are *brain-level* resets — distinct from the *device-level* power/recovery states
(`STATE_SILENT_REBOOT`, `RESTART_XMOS`) in [`power-and-system-events.md`](power-and-system-events.md).

## ChatScript lifecycle — `ChatScriptReady`, `ChatScriptException`

```proto
message ChatScriptReady     { string user; string bot; }
message ChatScriptException { string message; bool restore_default; }
```

The local [ChatScript engine](../runtime/content-and-conversation.md) reporting up: **`ChatScriptReady`** when it's
initialized for a `user`/`bot`, **`ChatScriptException`** on an error — `restore_default = true` asks the
brain to fall back to the default script (a self-heal path complementary to the
[offline fallback tree](offline-and-brain-state.md)). `WaitTimeout{ time }` is the generic "a wait of
`time` seconds elapsed" signal the dialog manager uses to move on.

## What this means for the three goals

**② Server revival — the useful part.** These give a self-hosted server or the parent app **live control
of a running robot**, not just config: mute/adjust volume this second (`SystemVolumeModify`), flip
accessibility pacing (`SystemSlowInputModify`), open/close the mic (`ChatbotListeningRequest`), gate
barge-in (`AllowCutoffEvent`), and recover a wedged session (`SoftReset`) without a reboot. Inject them
over MQTT `/commands/zmq` — the same path the toolkit already uses.

**① Custom firmware.** These are the control *inputs* a custom brain must honor (or emit) to be driven by
the app/server the way stock Moxie is — the imperative half of the config surface.

**③ Pre-801 revival.** No new lever; brain-side, over the bus/endpoint path bounded by
[`network-trust.md`](network-trust.md).

---
📖 [Reverse-engineering index](../README.md) · [Settings schema](../firmware/settings-schema.md) · [Turn-taking](../runtime/turn-taking.md) · [Robot IPC protocol](robot-ipc-protocol.md) · [Power & system events](power-and-system-events.md) · [Device config & telemetry](device-config-and-telemetry.md)
