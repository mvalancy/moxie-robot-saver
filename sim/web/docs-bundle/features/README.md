# Features of the original app

What the original Moxie parent app did, including hidden and developer features, so the rebuild can
cover all of it. These are clean-room descriptions; the [reverse-engineering study](../reverse-engineering/README.md)
is the source of truth.

## Documents
- [`feature-catalog.md`](feature-catalog.md) — **the complete catalog** (15 areas + a hidden/developer
  section, endpoint inventory, enums, PII-encryption map, COPPA/Privo, GRL, teletherapy).
- [`robot-capabilities.md`](robot-capabilities.md) — **the robot's own capability catalog** (conversation, speech, vision, movement, activities, sessions, system) — indexed to the RE docs.
- [`robot-lifecycle.md`](robot-lifecycle.md) — pairing, unpair, **factory reset**, restore/backup,
  reboot, OTA, the full robot state model.

## Hidden and developer features
Surprising things buried in the app — useful for testing and for understanding intent:
- **`envchange`** — type it into the login email field to open a hidden switcher that retargets the
  *entire* REST backend (Production / Staging / Develop / China / Hong Kong) at runtime.
- **Demo-data toggle** (non-prod) — fakes Insights/Rewards, unlocks all reward assets, `auid()` → `"foo"`.
- **Debug long-presses** (non-prod) — long-press the battery % to jump into child editing; Skip-pairing
  and Skip-OTA buttons; raw error codes; a `dev=1` field in the QR.
- **Dead JSON-QR mode** — `PairQRMode.PAIR_JSON_TOKEN` is unreachable; the app only ever emits the
  proto QR. (Still worth trying against old firmware, since it needs no key material.)

Each catalog entry records: what it does, where it lives (class/fragment), the API endpoint(s), its
settings/parameters, and whether it's hidden/experimental.

---
[Docs index](../README.md)
