# 🧾 `sim/tests/data/` — golden fixtures the tests read

Data a test asserts against, kept out of the test file so a tool can regenerate it without
anyone editing a `.py`.

- [`ext_conformance.json`](ext_conformance.json) — the conformance goldens for
  [sandboxed content extensions](../../../docs/architecture/backlog/sandboxed-extensions.md).
  Six rows `G1`–`G6`, each `(ast, facts, seed, clock, grants, expected_effects, explain,
  grant_list)`: OpenMoxie's six executable content hooks, re-authored in our AST. It is the
  proof the grammar is expressive enough, the regression set [`test_ext.py`](../test_ext.py)
  reproduces byte for byte (only `G5` is still `xfail(strict)`, waiting on the `brain`
  capability), and the cross-host contract a JavaScript evaluator must also reproduce (hence
  explicit `format` specs and sorted `keys`).

Regenerate with [`../../tools/build_ext_conformance.py`](../../tools/build_ext_conformance.py).
The test reads the committed file, not the generator, so a generator bug cannot rewrite its own
goldens. Clean-room: re-authored programs, never upstream source (OpenMoxie is MIT, © Justin
Beghtol; see [`ATTRIBUTION.md`](../../../ATTRIBUTION.md)).

- [`wire/`](wire/README.md) — shareable wire recordings: a `--share` copy of one synthetic bench
  session and its timeline golden, read by [`test_wire_record.py`](../test_wire_record.py), which
  also regenerates them (`--write-fixtures`).

---
📖 [Tests](../README.md) · [Back to top](../../../README.md)
