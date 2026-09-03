# 🛠️ Decompiling the app layer

Decompile the app layer of an Android device — DEX/Java apps with jadx, and Unity apps' C# game logic (Assembly-CSharp, Mono vs IL2CPP) with ilspycmd. Use when you need to read what a device's apps actually do — the "brain" logic, setup/pairing flows, factory tests, or the managed side of a native call.

Invoke it by name (`decompiling-android-apps`) — it is a **shared agent skill**, so any agent working in this repo can load it instead of re-deriving the method.

| File | Purpose |
|---|---|
| [`SKILL.md`](SKILL.md) | The skill itself — instructions the agent follows. |

## What it covers

- DEX / Java apps → jadx
- Unity apps → the C# game assembly
- What to mine from the managed code
- Worked example (Moxie)

---
📖 [Skills](../README.md) · [Back to top](../../../README.md)
