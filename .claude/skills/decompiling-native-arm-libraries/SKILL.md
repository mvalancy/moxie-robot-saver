---
name: decompiling-native-arm-libraries
description: Inspect and decompile native ARM .so libraries — escalating from nm/strings to capstone to Ghidra via PyGhidra — to recover a device's native logic (dispatch tables, GOT-indirected strings, the hardware C API, obfuscation). Use when symbols alone aren't enough and you need what a native function actually does. Works on any ARM Android/Linux .so.
---

# Decompiling native ARM `.so` libraries

Escalate **light → heavy**. Most questions fall to the light tiers; reach for Ghidra only when data-flow
(GOT-indirected strings, `std::map` dispatch, hand-rolled obfuscation) defeats a disassembler.

## Tier 1 — symbols & strings (always first)
```bash
nm -D <lib>.so | c++filt          # exported functions, demangled → the API surface
readelf -d -sW <lib>.so           # NEEDED/SONAME + full symtab with sizes
strings -a <lib>.so | grep -i …   # log tags, file paths, format strings, class/method names, literals
```
This alone identifies a lib's role and its API. A demangled C++ symbol table often reveals the whole
class/method structure (`Namespace::Class::method`). **Caveat:** a `strings` hit is not proof a value is a
live dispatch key — verify before claiming.

## Tier 2 — disassembly (capstone)
Use capstone (pip-installable, no toolchain) for a single function's control flow:
`Cs(CS_ARCH_ARM, CS_MODE_THUMB)` (most Android ARM32 is Thumb-2). It resolves `ldr rX, [pc, #imm]` literal
pools and `movw`+`movt` immediate pairs to addresses/strings. **Blind to GOT-indirected data** — if a
function loads strings via the GOT, capstone won't see them (that needs Ghidra). Note: a stock `objdump`
built without ARM support will silently refuse (`architecture: UNKNOWN!`) — use capstone or an
`arm-*-objdump`, not the default.

## Tier 3 — Ghidra, driven by **PyGhidra** (the reliable headless path)
Ghidra's decompiler reconstructs C-like pseudocode and follows the data-flow the disassembler can't.
**Prefer PyGhidra over Ghidra's own Java/Jython scripts**, especially on a JRE-only host (no `javac` → Java
GhidraScripts won't compile; Ghidra 12 dropped Jython → `.py` GhidraScripts need PyGhidra too). PyGhidra
drives the same API from CPython:
```bash
GH=/path/to/ghidra
$VENV -m pip install "$GH"/Ghidra/Features/PyGhidra/pypkg/dist/pyghidra-*.whl   # once
# 1) import + auto-analyze ONCE (minutes + GBs RAM — run in the BACKGROUND, don't block a foreground call):
"$GH/support/analyzeHeadless" <projdir> <projname> -import <lib>.so -processor "ARM:LE:32:v7"
```
```python
# 2) reopen the SAVED project read-only from PyGhidra (fast — no re-analysis) and decompile named targets:
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
**Resolve string refs FROM the function, not TO the string.** `getReferencesTo(stringAddr)` is frequently
empty (GOT indirection). Instead iterate the function's instructions and follow
`ins.getReferencesFrom()` into `.rodata`, reading the C-string at each target — this is what turns
`report`/`endpoint_update`/`om`-style dispatch strings from guesses into facts.

### Gotchas that cost real time
- **One JVM at a time.** A killed run leaves the project **locked**; the next run then fails silently
  (no output file, no error). Before retrying: `pkill -9 -f ghidra` and confirm no stray `java` via
  `ps -eo pid,comm` (a `pgrep -f ghidra` also matches your own bash command — don't be fooled).
- **JVM startup is slow (~60–90 s).** Run analysis + long decompiles as a **tracked background task** and
  read the output file when it lands — don't foreground into a short shell timeout.
- If the `.so` carries **DWARF** (some vendor libs do), decompilation gets **real variable/type names** —
  worth confirming with `readelf -S | grep debug`.
- Decompile **named targets**, not a whole 60 MB module.

## Obfuscation (when strings are deliberately hidden)
If secrets aren't plaintext (e.g. a `getPassword()` that computes at runtime), two moves: (a) look for a
simple transform in the decompiled function — repeating-**XOR** against the caller's package name or a
constant is common; (b) if the logic is gnarly, **emulate** just that function with **Unicorn** (`unicorn`
+ `capstone`), feed the expected input, and read the output — no need to reimplement it.

## Worked example (Moxie)
30 native libs in `bo-android.apk`. `nm`/`strings` mapped the whole `libbo-*` roster + the `[DllImport]`
hardware C APIs (`liblizzerface` = the MCU, `librobinface` = LED I²C/GPIO). PyGhidra on `libbo-logger.so`
(which carries DWARF) decompiled `RightPoint::on_QRCommand` and, via the from-function string-ref trick,
proved the closed QR command set (`report`/`endpoint_update`/`om`). `libsecrets.so`'s factory creds were
repeating-XOR by package name — cracked, with a Unicorn extractor in `tools/robot-toolkit/secrets/`.
Ghidra lives at `work/tools/ghidra`; dumps in `work/firmware-re/ghidra-out/`.
