# 🧬 Sandboxed extension evaluator

The total JSON-AST language a stranger's content pack may carry as `extension`
([brief](../../../../docs/architecture/backlog/sandboxed-extensions.md)). Callers import
`moxie_sdk.content.ext` only; the split below is for reading, not for API.

| Module | Holds |
|---|---|
| [`__init__.py`](__init__.py) | The public surface (re-exports) and the four safety properties |
| [`grammar.py`](grammar.py) | The language as data: error value, name normalizers, capability words, fact roots, limits, `ExtResult`, the closed `OPS`/`STATEMENTS` tables |
| [`values.py`](values.py) | Total value helpers the ops are built from (a bad input is the error *value*) and the seeded PRNG |
| [`validate.py`](validate.py) | Load-time validation: grammar, depth, node count, capabilities declared ⇔ used, host grants |
| [`explain.py`](explain.py) | `explain()` — the program as English for the parent's review |
| [`machine.py`](machine.py) | `evaluate()`: the step-, wall-clock- and byte-budgeted interpreter |

**The import boundary is the package.** Every file here may import only `math`, `re`,
`unicodedata`, `dataclasses`, `__future__` and its own siblings (`from .grammar import …`).
No clock, no entropy, no I/O, and no `from ..` — a parent-package import would let a sibling
hand the evaluator a store or a network client. `sim/tests/test_ext_escapes.py` (X7) walks
every `.py` here, so a new module is inside the boundary the moment it exists, and its
self-test proves the audit refuses each way out. The clock and the PRNG seed are injected
by the host (`content_app.py`).

Guards are made load-bearing by `sim/tools/ext_mutation_check.py` and
`subscribe_mutation_check.py`, whose rows anchor on exact lines in these files.

---
📖 [Content engine](../README.md) · [Back to top](../../../../README.md)
