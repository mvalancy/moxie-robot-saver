# Feature catalog

An exhaustive inventory of what the Moxie parent app does — every user-facing **and** hidden/
developer feature — so we can rebuild all of it, not just the happy path. Derived from the
decompiled app; the reverse-engineering maps in [`../reverse-engineering/`](../reverse-engineering/)
are the source of truth.

> Being compiled now from a full decompiled-source sweep. As sections land they are linked here:
>
> - `feature-catalog.md` — the complete catalog (all features, incl. hidden/dev).
> - `factory-reset-and-unpair.md` — factory reset vs unpair, exact API + UI flow.
> - `recovery-and-restore.md` — recovery phrase, backups, restore-to-new-robot.
> - `robot-control.md` — wakeup, reboot, OTA, language/voice, settings model.

Each entry records: what it does, where it lives (class/fragment), the API endpoint(s) it uses, its
settings/parameters, and whether it's hidden/experimental.
