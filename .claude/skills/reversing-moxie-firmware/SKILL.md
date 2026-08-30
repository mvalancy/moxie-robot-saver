---
name: reversing-moxie-firmware
description: Guides the Moxie firmware reverse-engineering loop — picking the next unexplored thread, decompiling, and writing clean-room-complete findings into docs/reverse-engineering. Use when continuing the Moxie firmware deconstruction (RK3288/Android 9, firmware v24.10.803), analyzing its images/apps/native libs, or deciding what to document next.
---

# Reverse-engineering Moxie firmware — the working loop

The full method + tool tiers live in `docs/reverse-engineering/METHODOLOGY.md`; this is the actionable
loop plus the two lenses that found the most.

## Evidence base (under `work/`, one level up from the repo)
- Images: `work/firmware-re/{system.img,oem.img,parts/vendor.img,parts/boot.img}` — read with `debugfs -R "ls -R /path" img` and `debugfs -R "cat /path" img` (no mount).
- Apps: `work/firmware-re/extract/apps/*.apk`. DEX → Java: `work/tools/jadx/bin/jadx`.
- The Unity brain: `work/firmware-re/extract/csharp/src-asm/Assembly-CSharp.decompiled.cs` (grep this first for anything managed).
- Recovered protos: `docs/reverse-engineering/protocol/recovered-proto/**` (exhaustive; `proto-catalog.md` lists all messages).
- Native libs: inside each APK's `lib/armeabi-v7a/*.so`.

## The loop (every iteration)
1. Read `work/firmware-re/progress/PLAN.md`.
2. Check `COVERAGE.md` + `EXPLORATION-MAP.md` + existing docs. Do not re-document; pick the next genuinely unexplored thread.
3. RE with the lightest tool that answers it. For the heavy native/asset tiers, use the `decompiling-native-libs` skill.
4. Write highly-detailed, `v24.10.803`-stamped findings into the right `docs/reverse-engineering/**` subfolder.
5. Push the finding upward (subfolder README → section index → COVERAGE/EXPLORATION-MAP → root docs if it changes the headline), then rebuild + verify with the `publishing-moxie-docs` skill.
6. Commit, push, update `PLAN.md`.

## Two lenses that found the most
- **"Named but not enumerated."** Docs often name a mechanism (a markup verb, an event, a config key, an error) but leave the actual **set** in the binary. That set is the high-value fill:
  - `grep -oE "= new string\[[0-9]+\]" Assembly-CSharp.decompiled.cs` → hardcoded vocabularies (found the 52 vocal gestures).
  - `grep -nA20 "enum <Name>"` → enum values (found the audio `Channel`, `SayAsInterpretation`, `EBErrorCode` sets).
  - `commandLabel` / `available<X>` / `request.<field>` reveal command taxonomies + their data fields.
- **Clean-room sufficiency.** For every doc ask: *if all Moxie binaries/assets vanished, could someone rebuild this from the doc alone?* Capture the data (tables, constants, algorithms) — don't point at it. Track it honestly in `COVERAGE.md`'s "Clean-room self-sufficiency" register, which is also where the current in-scope gaps are listed.

## Discipline
- **Confirmed vs inferred.** A symbol name proves a *capability*; the exact value/trigger often needs the next tool tier. Never restate a guess as fact — escalate the tool or label it. Past "observed" values proved wrong once decompiled (e.g. an audio channel note said "0=music"; it is `FX=0`).
- Stamp `v24.10.803` on every robot-side page.
- Small + shippable each iteration. If a tick finds nothing genuinely new, say so — do not pad a commit.
- Commit messages with backticks/parens get shell-mangled — write the message to a file and `git commit -F <file>`.
