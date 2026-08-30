---
name: decompiling-native-libs
description: Decompiles native ARM .so libraries with Ghidra/PyGhidra and capstone, and extracts Unity assets with UnityPy, on a JRE-only host. Use when nm/strings are not enough — resolving GOT-indirected strings, native dispatch tables, native function logic, or pulling meshes/animation-clips/blendshapes out of a Unity Android app.
---

# Native + Unity-asset extraction

Escalate light → heavy. Most questions are answered by the light tiers; reach for Ghidra only when the
data flow (GOT-indirected strings, `std::map` dispatch) defeats a disassembler.

## Tier 1 — symbols & strings (try first)
```bash
nm -D <lib>.so | c++filt        # exported functions, demangled
readelf -d -sW <lib>.so         # NEEDED/SONAME + symtab with sizes
strings -a <lib>.so | grep -i … # log tags, paths, literals
```
Identifies a lib's role and API. A `strings` hit is not proof a value is a live dispatch key — verify.

## Tier 2 — disassembly (capstone, in the venv)
`work/firmware-re/extract/csharp/.venv` has capstone: `Cs(CS_ARCH_ARM, CS_MODE_THUMB)`. Resolves
`ldr [pc]` and `movw+movt` literal loads to strings; good for one function's control flow. Blind to
GOT-indirected data. (System `objdump` here has NO ARM support — don't use it.)

## Tier 3 — Ghidra via PyGhidra (NOT Java/Jython)
This host is JRE-only (no `javac`), so Ghidra Java scripts won't compile, and Ghidra 12 dropped Jython —
`.py` GhidraScripts also need PyGhidra. Drive Ghidra's API from CPython instead:
```bash
GH=work/tools/ghidra
VENV=work/firmware-re/extract/csharp/.venv/bin/python
$VENV -m pip install "$GH"/Ghidra/Features/PyGhidra/pypkg/dist/pyghidra-*.whl   # once
# Import + auto-analyze ONCE (minutes + GBs RAM — run in the BACKGROUND, don't block):
"$GH/support/analyzeHeadless" <projdir> <projname> -import <lib>.so -processor "ARM:LE:32:v7"
```
```python
# Reopen the SAVED project read-only from PyGhidra (fast — no re-analysis):
import pyghidra; pyghidra.start()
from ghidra.base.project import GhidraProject
from ghidra.app.decompiler import DecompInterface
from ghidra.util.task import ConsoleTaskMonitor
proj = GhidraProject.openProject("<projdir>", "<projname>", True)
program = proj.openProgram("/", "<lib>.so", False)
di = DecompInterface(); di.openProgram(program)
for f in program.getFunctionManager().getFunctions(True):
    if f.getName() in TARGETS:
        print(di.decompileFunction(f, 180, ConsoleTaskMonitor()).getDecompiledFunction().getC())
```
**Resolve string refs from the function, not to the string** (the trick that cracked the QR router):
iterate a function's instructions and follow `ins.getReferencesFrom()` into `.rodata`, reading the
C-string at each target. `getReferencesTo(stringAddr)` is often empty (GOT indirection).

### PyGhidra gotchas (each cost real time)
- One JVM at a time. A killed run leaves the project locked; the next run fails silently with no output file. Before retrying: `pkill -9 -f ghidra_1`, then confirm no stray `java` via `ps -eo pid,comm` (a `pgrep -f ghidra` matches your own bash command).
- JVM start is slow (~60–90 s). Run as a tracked background task and read the output file when it lands; don't foreground it into the 120 s Bash timeout.
- Several `libbo-*.so` carry DWARF → decompilation yields real variable/type names.
- Decompile named targets, not a whole 60 MB module.

## Unity assets — UnityPy (same venv)
```python
import UnityPy, collections
env = UnityPy.load("work/firmware-re/extract/unity/sharedassets1.assets")
print(collections.Counter(o.type.name for o in env.objects))       # Mesh/AnimationClip/MonoBehaviour/…
for o in env.objects:
    if o.type.name == "Mesh":
        m = o.read()
        chans = getattr(m.m_Shapes,'channels',None) or getattr(m.m_Shapes,'m_Channels',[])
        print(m.m_Name, [getattr(c,'name',None) for c in chans])    # blendshape names
```
Base-APK assets are in `sharedassets*.assets` (split `.split0..N` → `cat` to reassemble), `level0/1`,
`globalgamemanagers.assets`. Streamed/downloaded bundles (e.g. `rig3animations`) are NOT in the base APK.

Save large decompiler dumps to `work/firmware-re/ghidra-out/` (gitignored); put the distilled facts, labeled confirmed vs inferred, in the docs.
