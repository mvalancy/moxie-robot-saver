# 📈 shared_ceilings sections

The sections of [`sim/tests/helpers_shared_ceilings.mjs`](../../helpers_shared_ceilings.mjs)
(run by [`test_shared_ceilings.py`](../../test_shared_ceilings.py), which asserts each lettered
section's count), over one [`harness.mjs`](harness.mjs): a per-section ledger, the shared
`fakeCache`, and admissions with an explicit clock.

- [`01_bind.mjs`](01_bind.mjs) — A–E: the no-store seam; the per-IP hour and day and the unit
  budget's day binding across isolates; a refunded request publishing nothing.
- [`02_fail_open.mjs`](02_fail_open.mjs) — F–G: the wide window and the day budget fail OPEN,
  every failure mode by name.
- [`03_keys_cost.mjs`](03_keys_cost.mjs) — H–K: the keys, the cost in round trips, the uncapped
  seam, and which direction each refusal errs in.

Labels are matched by `sim/tools/unit_budget_mutation_check.py` (the `CEILINGS` rows).

---
📖 [Edge modules](../README.md) · [Back to top](../../../../README.md)
