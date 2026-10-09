"""
Sandboxed content extensions — a total, JSON-AST expression language a stranger's
content pack may carry (docs/architecture/backlog/sandboxed-extensions.md, BEYOND #6).

A declarative rule list over a closed operator table, interpreted by pure-stdlib Python:
no `exec`/`eval`, no parser (the program *is* JSON), no loop, no user function, no
recursion, and no name that resolves to a host object. Values are JSON scalars, lists and
string-keyed maps. OpenMoxie's executable modules (MIT, read as prior art only — see
ATTRIBUTION.md) use none of those constructs, so this covers what people actually write
while keeping zero escape surface and an English rendering a parent can read.

The four safety properties, each pinned in `sim/tests/test_ext_escapes.py`:

1. *The fact base is plain JSON built by the host* (X2).
2. *Every op is total*: division by zero is the error **value**, a missing key is null
   (§4.6), so the evaluator always returns.
3. *Clock and entropy are injected*: no module in this package imports `time`, `random`,
   `os`, `datetime`, `secrets` or `subprocess`, and none imports anything outside the
   package but `math`/`re`/`unicodedata`/`dataclasses` (X7, asserted over every file here
   by walking its AST), so a turn is replayable and no sibling can smuggle in a host door.
4. *Capabilities are checked at load in both directions*: an undeclared use and an unused
   declaration are both refused (X10), so the parent's grant list equals what the program
   can do.

On any breach `evaluate()` returns `ExtResult(ok=False, …)` and discards the whole effect
list; the caller carries on as if there were no extension. The child never hears an error.
A pack's `code` field stays inert data forever; `extension` is a separate field and is
never compiled from `code` (brief §7.4).

Layout (one package so the X7 import boundary is a directory, not a file):
`grammar` (the language: names, capabilities, limits, op/statement tables), `values`
(total value helpers + PRNG), `validate` (load-time checks), `explain` (English),
`machine` (the evaluator). Callers import from `moxie_sdk.content.ext` only.
"""

from __future__ import annotations

from .grammar import (ACTION_WORDS, BREACH_WORDS, CAPABILITY_WORDS, DEFAULT_BUDGET_S,
    DEFAULT_GRANTS, DEFAULT_MAX_BREACHES, DEFAULT_MAX_STEPS, DEFAULT_MAX_TOTAL_BYTES,
    DEFAULT_MAX_VALUE_BYTES, ERROR, ERROR_TRANSPARENT, EXT_FORMAT, ExtResult,
    FACT_ROOTS, HOOKS, LAZY_OPS, Limits, MAX_ACTIONS, MAX_ARGS, MAX_CAPABILITIES,
    MAX_DEPTH, MAX_MARKUP_CHARS, MAX_MEMORY_WRITES, MAX_NODES, MAX_NOTE_CHARS,
    MAX_NOTES, MAX_REPEAT, MAX_RULES, MAX_SAY_CHARS, MAX_STATEMENTS_PER_RULE,
    MAX_SUBSCRIPTIONS, OPS, P1_CAPABILITIES, STATEMENTS, SUBSCRIBE_EVENTS, SYMBOLIC_OPS,
    _is_p1, is_error, normal_name, normal_op)  # noqa: F401
from .values import (_format)  # noqa: F401
from .validate import (capabilities_of, grant_list, validate)  # noqa: F401
from .explain import (explain, written_effects)  # noqa: F401
from .machine import (_Breach, _Machine, evaluate)  # noqa: F401

# Private names re-exported above (`_Machine`, `_Breach`, `_format`, `_is_p1`) are for the
# escape tests, which drive the evaluator below `evaluate()`'s validate-first door.
