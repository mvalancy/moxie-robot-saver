---
name: decompiling-android-apps
description: Decompile the app layer of an Android device — DEX/Java apps with jadx, and Unity apps' C# game logic (Assembly-CSharp, Mono vs IL2CPP) with ilspycmd. Use when you need to read what a device's apps actually do — the "brain" logic, setup/pairing flows, factory tests, or the managed side of a native call.
---

# Decompiling the app layer

On these devices the app layer is usually where the **product logic** lives — the conversational brain,
the setup/pairing flow, the factory tests. Two kinds: ordinary DEX/Java, and Unity C#.

## DEX / Java apps → jadx
```bash
jadx -d out_dir the_app.apk           # decompiles classes.dex to readable Java under out_dir/sources
grep -rn "SomeClass\|some_string" out_dir/sources
```
Good for setup apps, services, factory tools. Read `AndroidManifest.xml` for the entry points
(activities/services/receivers) and the exported IPC surface. jadx recovers control flow well; note it
mangles some enums (it says so in a comment) — cross-check enum values against the smali if it matters.

## Unity apps → the C# game assembly
A Unity app ships `assets/bin/Data/`. Two build modes — check which:
- **Mono** (managed): `assets/bin/Data/Managed/Assembly-CSharp.dll`. Decompile to C# with **ilspycmd**:
  ```bash
  export DOTNET_ROOT=$HOME/.dotnet PATH=$HOME/.dotnet:$PATH DOTNET_ROLL_FORWARD=LatestMajor
  ilspycmd Assembly-CSharp.dll > Assembly-CSharp.decompiled.cs
  ```
  You get near-source C#. This is the jackpot — **grep it first for anything managed** (class names,
  string literals, enum values, `[DllImport]` native calls, protobuf type names).
- **IL2CPP** (AOT-compiled to native): there's no `Assembly-CSharp.dll` — logic is in `libil2cpp.so` +
  `global-metadata.dat`. Recover symbols with **Il2CppDumper** (→ a `dummy dll` + a Ghidra/IDA script that
  names the functions), then work it as a native lib (`decompiling-native-arm-libraries`).

## What to mine from the managed code
- **`enum` definitions and hardcoded `new string[N]{…}` arrays** → the real command/event/error
  vocabularies ("named but not enumerated").
- **`[DllImport("lib…")]`** → the exact native C API the app calls (the hardware/IPC boundary).
- **Protobuf class names** (`FooPB`, `*Request`/`*Response`) → the message set; recover the schemas with
  `recovering-protobuf-schemas`.
- **String constants**: endpoints, topic templates, file paths, config keys, markup verbs.

## Worked example (Moxie)
`bo-android.apk` is **Mono** Unity — `Assembly-CSharp.decompiled.cs` (7 MB, ~2750 classes) is *the brain*
and the single most productive artifact in the whole project: it yielded the behavior engine, the face
animation, the `[DllImport]` native boundary (`liblizzerface` etc.), the QR grammar, the 52 vocal
gestures, the audio/error enums, and every protobuf type name. `bo-wifi.apk` gave the QR setup grammar;
the `productiontesting.*` apps gave the factory-test catalog + the serial/barcode format. jadx handled the
Java setup/factory apps; ilspycmd handled the Unity brain. Sources: `work/firmware-re/extract/csharp/`
and `work/firmware-re/out/<app>/sources/`.
