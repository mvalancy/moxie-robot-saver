# 🧪 Methodology — how we reverse-engineer Moxie (`v3.6.4-Zephyr` / OTA `v24.10.803`)

> How the facts in this folder are produced, so anyone can reproduce or extend them, and so each
> session works the **same disciplined loop** instead of re-deriving method every time. Everything here
> is clean-room: observed facts and schemas from the shipped, freely-distributed binaries — no Embodied
> source. All robot-side docs describe the analyzed build **v24.10.803**; keep that stamp on new pages.

## The evidence base

| Artifact | Where (under `work/`, one level up from the repo) | What it is |
|---|---|---|
| Partition images | `firmware-re/{system.img, oem.img, parts/vendor.img, parts/boot.img, …}` | the factory OS — read with `debugfs` (ext4) / `unpackbootimg` |
| Robot apps | `firmware-re/extract/apps/*.apk` (`bo-android`, `bo-wifi`, `productiontesting.*`, …) | the on-device APKs pulled from the images |
| Decompiled C# | `firmware-re/extract/csharp/src-asm/Assembly-CSharp.decompiled.cs` (7 MB) | the Unity **brain** (`bo-android`) — 2750 classes |
| Decompiled Java | `firmware-re/out/<app>/sources/…` | jadx output for the DEX apps |
| Recovered protos | `docs/reverse-engineering/protocol/recovered-proto/**` | the wire contract, all compile under `protoc` |
| Native libs | inside each APK's `lib/armeabi-v7a/*.so` | the `libbo-*` modules + support libs |

## The tool tiers — light to heavy

Reach for the **lightest tool that answers the question**; escalate only when it can't.

1. **Filesystem / images** — `debugfs -R 'ls -l /…' system.img`, `debugfs -R 'cat /path' …` to read
   files without mounting; `unpackbootimg` for `boot.img`. Inventory, init scripts, permissions, props.
2. **Android / Java** — `work/tools/jadx/bin/jadx` (DEX → readable Java); `apktool` for resources/manifest.
   The factory apps, `bo-wifi`, and Java services decompile cleanly here.
3. **Unity / C#** — `ilspycmd` (with `export DOTNET_ROOT=$HOME/.dotnet PATH=$HOME/.dotnet:$PATH
   DOTNET_ROLL_FORWARD=LatestMajor`, venv at `work/firmware-re/extract/csharp/.venv`) → the managed brain.
4. **Native — symbols & strings** — `nm -D` / `readelf -d -sW` / `strings -a` / `c++filt`. Exports,
   `SONAME`/`NEEDED`, log tags, demangled class & method names. Enough to *identify* a `.so` and its API
   (this is how the [native module roster](runtime/native-boundary.md#the-full-module-roster-what-each-remaining-bo-so-actually-is)
   and most of the [native boundary](runtime/native-boundary.md) were mapped).
5. **Native — disassembly** — **capstone** (in the `.venv`; `Cs(CS_ARCH_ARM, CS_MODE_THUMB)`). Reads
   instructions and resolves `ldr [pc]` / `movw+movt` literal loads to strings. Good for a single
   function's control flow; **blind to GOT-indirected data and dispatch tables** (a `strings` hit is not
   proof a value is a live dispatch key — verify, don't assume).
6. **Native — decompilation (Ghidra)** — the heavy tier for what capstone can't resolve: the full
   pseudocode of a native function, with cross-references, string/data flow, and (when the `.so` carries
   **DWARF**, as several `libbo-*` do) real variable and type names. This is the tool for the hard native
   questions — e.g. the exact `QRCommand`-string → handler dispatch inside `libbo-logger`'s cloud router.
   See [Using Ghidra](#using-ghidra-headless) below.

> **Confidence discipline.** Mark every finding as **confirmed** (read from the binary) or **inferred**
> (reasoned, not yet seen). A name in a symbol table proves the *capability* exists; the exact trigger or
> value often needs the next tier down. Never upgrade a guess to a fact by restating it — escalate the
> tool instead, or label it honestly. If a tick finds nothing genuinely new, say so; don't manufacture a
> commit.

## Using Ghidra (headless)

Ghidra (the NSA's open-source decompiler) is installed at **`work/tools/ghidra/`** (JDK 21+ required).
It reconstructs C-like pseudocode and resolves the data-flow the disassembler tiers cannot. Headless
recipe used for the native `.so` work:

```bash
GH=work/tools/ghidra/support/analyzeHeadless
"$GH" <project-dir> <project-name> \
  -import lib/armeabi-v7a/libbo-logger.so -processor "ARM:LE:32:v7" \
  -scriptPath <scripts> -postScript decompile_targets.py     # a Jython script that decompiles named funcs
```

A post-script uses `DecompInterface().decompileFunction(fn, timeout, monitor)` to dump the pseudocode of
specific functions (find them via `getFunctionManager().getFunctions(True)`). Analysis of a large
(≈60 MB) module takes minutes and gigabytes of RAM — run it in the background and pick up the result when
it lands, rather than blocking. Prefer decompiling **named targets** over reading a whole 60 MB module.

## The per-iteration loop (standard operating procedure)

Every reverse-engineering session runs the **same** disciplined cycle — this is the SOP, not a suggestion:

1. **Read** `work/firmware-re/progress/PLAN.md` (where we are / what's next).
2. **Check before writing** — [`COVERAGE.md`](COVERAGE.md) (goal-outcome view), [`EXPLORATION-MAP.md`](EXPLORATION-MAP.md)
   (source-surface view), and the existing docs. **Do not re-document** what's covered; pick the next
   genuinely **unexplored** thread.
3. **Reverse-engineer** with the lightest sufficient tool tier above; escalate to Ghidra for hard native
   questions.
4. **Write** highly-detailed, `v24.10.803`-stamped findings into `docs/reverse-engineering/**`, filed in
   the [right subfolder](README.md) (phone / protocol / runtime / firmware / hardware) with a back-link.
5. **Grow the tree, don't polish a leaf** — run the [top-down consistency pass](../README.md#-how-this-documentation-tree-is-maintained-sop):
   a new leaf must be reflected upward (its subfolder README, the RE README, COVERAGE/EXPLORATION-MAP,
   and — if it changes the story — `docs/README.md` and the root `README.md`). No contradictions between
   levels; one message from root to leaf.
6. **Rebuild + verify** — `python3 sim/tools/build_docs_bundle.py`, then `node sim/test_docs.mjs` (tree +
   README-hierarchy guard), `python3 scripts/check-doc-links.py` (links + anchors), and
   `python3 scripts/check-doc-consistency.py` (stale-message + version-stamp guard).
7. **Commit + push**, then update `PLAN.md`.

---
📖 [Reverse-engineering index](README.md) · [Coverage](COVERAGE.md) · [Exploration map](EXPLORATION-MAP.md) · [Docs index](../README.md)
