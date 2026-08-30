---
name: extracting-unity-assets
description: Extract and inventory a Unity Android app's assets with UnityPy — meshes and blendshapes, animation clips, textures/sprites, audio, animator state machines, and MonoBehaviour data. Use when a device's visuals/animations/behaviors live in Unity asset files and you need the actual geometry, clip names, or behavior data (not just the code that drives them).
---

# Extracting Unity assets (UnityPy)

A Unity app's *look and motion* live in its serialized asset files, not its code. **UnityPy** (pure
Python, pip-installable) reads them without Unity or AssetStudio.

## Where the assets are
Inside the APK under `assets/bin/Data/`:
- `sharedassets*.assets` — the main asset stores (often **split** into `.split0..N` on Android; reassemble
  with `cat sharedassets1.assets.split{0..N} > sharedassets1.assets`).
- `level0`, `level1`, … — the scenes (GameObjects, components, the composition).
- `globalgamemanagers.assets` — settings + the `MonoScript` registry (the class list).
- `.resource` / `.resS` — raw binary payloads (texture/audio bytes) referenced by the above.
- **Streamed/downloaded AssetBundles** are NOT in the base APK — those need a running unit or a content pull.

## Enumerate, then pull what you need
```python
import UnityPy, collections
env = UnityPy.load("sharedassets1.assets")
print(collections.Counter(o.type.name for o in env.objects))   # Mesh/AnimationClip/Texture2D/MonoBehaviour/AnimatorController/…
for o in env.objects:
    t = o.type.name
    if t == "AnimationClip":
        print("clip:", o.read().m_Name)
    elif t == "Mesh":
        m = o.read()
        chans = getattr(m.m_Shapes, "channels", None) or getattr(m.m_Shapes, "m_Channels", [])
        print("mesh:", m.m_Name, "blendshapes:", [getattr(c, "name", None) for c in chans])
    elif t == "Texture2D":
        img = o.read(); img.image.save(f"tex_{img.m_Name}.png")     # export the texture
```
- **Mesh → blendshape channel names** = the exact morph set (tiny sets mean expression is bone/clip-driven,
  not morphs — a key clean-room fact).
- **AnimationClip names** = the gesture/HUD/LED animation inventory.
- **AnimatorController** = the state machines (states/transitions/parameters).
- **MonoBehaviour** = component data; the `m_Script` class name identifies it (needs the type-tree/DLLs to
  fully deserialize — pair with the decompiled C#).

## Worked example (Moxie)
`sharedassets1.assets` (reassembled from 60 splits) gave `rig3_faceMesh01`'s **exactly 10 blendshapes**
(the 4 blink lids + cheek + happy/sad eyes — so most expression is bone/clip-driven), the 56 HUD/LED/
reward/whiteboard animation clips + their 7 AnimatorControllers, and the "Karu" rig codename. `level1` is
the composition scene (face mesh + per-board LED sprites + subtitles + post-FX). The `Bht_*` behavior-tree
graphs + the eyeseme/viseme clips live in the **streamed `rig3animations` bundle** (not the base APK) — the
one honestly-still-open clean-room gap. UnityPy runs from `work/firmware-re/extract/csharp/.venv`; findings
in `docs/reverse-engineering/firmware/unity-assets.md` + `runtime/unity-face-animation.md`.
