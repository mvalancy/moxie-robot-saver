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
   See [Using Ghidra](#using-ghidra-via-pyghidra) below.

> **Clean-room sufficiency test.** For every doc ask: *if all Moxie binaries, images, and assets
> vanished, could someone rebuild this piece from the doc alone?* Capture the data (schemas, tables,
> constants, algorithms) — don't just point at it. A doc that names a subsystem but leaves its actual
> spec in the binary isn't done. Track the honest answer per subsystem in
> [`COVERAGE.md`](COVERAGE.md#clean-room-self-sufficiency-what-would-go-missing); legitimately-out-of-scope
> data (voice, content, ML weights a revival replaces) is a gap only if the doc *pretends* to cover it.
>
> **Confidence discipline.** Mark every finding as **confirmed** (read from the binary) or **inferred**
> (reasoned, not yet seen). A name in a symbol table proves the *capability* exists; the exact trigger or
> value often needs the next tier down. Never upgrade a guess to a fact by restating it — escalate the
> tool instead, or label it honestly. If a tick finds nothing genuinely new, say so; don't manufacture a
> commit.

## Using Ghidra (via PyGhidra)

Ghidra (the NSA's open-source decompiler) is installed at **`work/tools/ghidra/`**. It reconstructs
C-like pseudocode and resolves the data-flow the disassembler tiers cannot — GOT-indirected strings,
`std::map` dispatch, protobuf/JSON plumbing.

**Use PyGhidra, not Ghidra's own scripts.** This box has a **JRE, not a JDK** (no `javac`), so Ghidra
*Java* scripts won't compile, and Ghidra 12 dropped Jython so *`.py` GhidraScripts* need PyGhidra anyway.
PyGhidra (installed from Ghidra's bundled wheel into `work/firmware-re/extract/csharp/.venv`) drives the
same Ghidra API from CPython and needs no compiler:

```bash
VENV/bin/pip install work/tools/ghidra/Ghidra/Features/PyGhidra/pypkg/dist/pyghidra-*.whl   # once
GHIDRA_INSTALL_DIR=work/tools/ghidra  VENV/bin/python decompile.py
```
```python
import pyghidra; pyghidra.start()
from ghidra.base.project import GhidraProject
from ghidra.app.decompiler import DecompInterface
proj = GhidraProject.openProject(PROJ_DIR, "proj", True)      # reuse a prior analysis (fast)
program = proj.openProgram("/", "libbo-logger.so", False)
di = DecompInterface(); di.openProgram(program)
for f in program.getFunctionManager().getFunctions(True):
    if f.getName() in TARGETS:
        print(di.decompileFunction(f, 180, monitor).getDecompiledFunction().getC())
```

**Workflow.** Import + auto-analyze once with `analyzeHeadless <proj> <name> -import <so> -processor
ARM:LE:32:v7` (minutes + GBs of RAM for a ~60 MB module — run it in the background, don't block), then
re-open that saved project read-only from PyGhidra to decompile **named targets** or resolve string
references (follow `getReferencesFrom()` into `.rodata`). One JVM at a time — a killed run leaves the
project open; clear stray `java` procs before retrying.

**Worked example.** This is how the [QR command router](protocol/qr-commands.md#the-effective-command-set-native-dispatch-rightpointon_qrcommand)
was closed: capstone showed `on_QRCommand` spawned a worker but couldn't resolve the dispatch strings;
PyGhidra decompiled the body and its `.rodata` refs, proving the exact closed set (`report` /
`endpoint_update` / `om`, else *"Unknown QR Diagnostic Command"*).

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
