# 🎨 Unity assets & boot animation

The visual layer of **v3.6.4-Zephyr / OTA v24.10.803**: the Unity assets that render Moxie's face, HUD
and effects, plus the Android boot animation. `bo-android` and `bo-wifi` are **Unity 2020.3.48f1** (LTS)
apps (`libunity.so`); the inventory was extracted with UnityPy. The face is a Unity-rendered "Karu"
character with only 10 blendshapes; most expression is animation-clip driven.

## Engine & layout

| | |
|---|---|
| Unity version | **2020.3.48f1** (both `bo-android` and `bo-wifi`) |
| Asset storage | serialized `sharedassets*.assets` (not asset bundles); `sharedassets1.assets` ships **split into 60 parts** (`.split0`–`.split59`, ~58 MB) + a `.resource` blob |
| Streaming | `BetterStreamingAssets.dll` + `UnityEngine.AssetBundleModule` (runtime asset loading) |
| Scripting | Mono (`libmonobdwgc-2.0.so`); managed `Assembly-CSharp.dll` (see [`robot-ipc-protocol.md`](../protocol/robot-ipc-protocol.md)) |

To inspect: reassemble `cat sharedassets1.assets.split{0..59} > sharedassets1.assets`, keep the
`.resource` alongside, open with **UnityPy** or **AssetStudio**.

## `sharedassets1.assets` inventory (the UI / HUD / effects layer)

| Object type | count | Object type | count |
|---|--:|---|--:|
| Texture2D | 103 | Sprite | 92 |
| MonoBehaviour | 75 | GameObject | 63 |
| **AnimationClip** | **56** | RectTransform | 33 |
| Transform | 30 | Material | 17 |
| **AnimatorController** | 7 | Mesh | 5 |
| Shader | 4 | ParticleSystem | 2 |
| AudioClip | 2 | RenderTexture | 2 |

### Notable named assets (maps to features)
- **Face / body ("Karu" character):** materials `karuCurvedFace_Mat`, `karuEyeShell_Mat`,
  `karuBlend_Mat`, `KaruBodyMat`. "Karu" is the internal character/hardware codename
  (cf. `RobotIOFactoryKaru`, hardware rev `D3_Karu1`). The geometry (the 5 `Mesh` objects): **`rig3_faceMesh01`** (the face),
  **`rig3_bodyMesh01`** (the body), **`visorMesh_KaruGeo_01_karuRig_A01`** (the visor/face-screen), and
  `cup_geo_Capsule` / `lid_geo_Capsule` (capsule prop). The `rig3`/`karuRig` naming is the animation rig
  the face expressions drive. **`rig3_faceMesh01` carries exactly 10 blendshapes**,
  all under the `karuBlendShapes.` namespace: `rig3_bs_{L,R}_lowerLid01` · `…_upperLid01` (the 4 blink
  lids), `…_cheekAir01` (cheek puff, ×2), `…_happyEyes01` (×2), `…_sadEyes01` (×2). That is the whole
  morph set: only the lids, cheeks and happy/sad eye-squash are shape keys.
- **Event icons (`IconsScreenAnimator`):** the on-screen icon panel animation — the Unity side of the
  **`icons-v2`** markup verb ([`behavior-markup.md`](../runtime/behavior-markup.md)), which shows School/Birthday/
  Medical/Heart badges during calendar events.
- **Status LEDs (animator `ledBarMoxieAnimator` / `ledSpriteAnimator`):** `LED_turningOn`,
  `LED_listening`, `LED_imTalking(Loop)`, `LedProcessing_Clip`, `LedResponding_Clip`,
  `LED_airPlaneMode`, `LedSystemSuspend(Sleep)_Clip`, `LED_systemFailure` — the on-screen LED-ring
  states (mirror the MCU `LedrPattern`, see [`hardware-map.md`](../hardware/hardware-map.md)).
- **Reward / STAR system (`RewardStarAnimator`):** vortex/heart/explosion sprite sequences —
  the mentorship reward animation (`reward-star` markup verb, [`behavior-markup.md`](../runtime/behavior-markup.md)).
- **Whiteboard activity (`WhiteboardAnimationController`):** entry/exit/write/clear clips (the drawing
  activity; `whiteboard` markup verb).
- **Dreaming / sleep (`dreamingAnimator`):** `dreamingHUD_bikeIncoming`, `dreamingHUD_sheepIncoming`,
  start/hold/end — the sleep/dream sequence HUD.
- **Dev console:** a `EB Console Window` / `EB OSA Layout Entry` GameObject — the on-device console
  (`embodied.Robot.ConsoleCommandRequest`, see [`robot-ipc-protocol.md`](../protocol/robot-ipc-protocol.md)).
- **Lore:** `GRL_envelope_*`, `GRL_note_*` sprites — the **G**lobal **R**obotics **L**aboratory,
  Moxie's fictional origin.

> **Not in `sharedassets1`:** the **eyeseme mood + viseme animation clips** and the **`Bht_*` NodeCanvas
> trees** live in the streamed **`rig3animations`** bundle (not the base APK's `sharedassets`/`level*`).
> That is an open [clean-room gap](../EXPLORATION-MAP.md#clean-room-self-sufficiency-what-would-go-missing).

## Boot animation

Standard Android `bootanimation.zip` at **`/oem/media/bootanimation.zip`** (84 MB):

```
desc.txt:  640 480 30      # 640×480 @ 30 fps
           c 1 0 part0     # play once
           c 0 0 part1     # loop
           c 1 0 part2     # play once
```

| Part | Frames | Role |
|---|--:|---|
| `part0` | 125 | intro |
| `part1` | 61 | loop (`loop_final_00`–`59`) |
| `part2` | 54 | outro |

PNG sequence, ~375 KB/frame. Replaceable like any Android boot animation (repackage the zip, flash
`oem` — see [`hardware-access.md`](../hardware/hardware-access.md)).

---
📖 [Firmware reference](firmware-803-reference.md) · [Reverse-engineering index](../README.md) · [Docs index](../../README.md)
