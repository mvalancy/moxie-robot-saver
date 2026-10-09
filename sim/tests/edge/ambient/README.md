# 🎃 test_ambient sections

The sections of [`sim/test_ambient.mjs`](../../../test_ambient.mjs), in run order, over one
[`harness.mjs`](harness.mjs): the shipped `ambient.js`/`ambient.json`, a seeded random, and a stub
page that runs the REAL `sim/web/ambient.js` (and, for §4, the real `bridge/`) on a virtual clock,
its timers and `Date` both. Hours of idle self-talk run in a blink, the date is whatever a section
says, and no wall clock is read. The entry file itself checks every line's face, gesture, heart
colour, clip and optional fields first.

- [`01_season.mjs`](01_season.mjs) — §1: the October set comes up only in October by the visitor's local calendar, on both sides of both month boundaries (the zone is pinned west of UTC, so a UTC month fails).
- [`02_glitch.mjs`](02_glitch.mjs) — §2: the glitch beat. At most one per ten minutes with every chance taken, never before her fourth quip, never while she speaks, the visitor talks, the mic is open or the box holds words; a reply mid-flicker stops it; never on a degraded page; never from `moxieAmbient.say()`.
- [`03_signoff.mjs`](03_signoff.mjs) — §3: the post-goodbye aside. Only after `moxie-signoff`, once per sign-off, a few seconds after her goodbye has been said (late voice, no voice); dropped when the visitor did not leave or liveness goes off; held by words in the box and a hidden tab, never by focus alone.
- [`04_seam.mjs`](04_seam.mjs) — §4: which replies sign off through the real `bridge/` (the hosted goodbye's `end_turn` and the robot's `exit_module`; not a model-chosen wave), and the aside at the end of a real goodbye turn, bridge and `ambient.js` in one page.

---
📖 [Edge modules](../README.md) · [Back to top](../../../../README.md)
