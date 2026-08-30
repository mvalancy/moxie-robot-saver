---
name: running-layered-session-loops
description: Structure and run a long-horizon project as layered autonomous agent loops (recurring, scoped, independent) that make steady safe progress for days without supervision. Use when setting up or operating unattended recurring work — a build/test/audit/deep-work cadence coordinated through a shared plan file. This is how the Moxie deconstruction sustained 322 commits.
---

# Running layered autonomous session loops

The operating model that drove this project: **several recurring loops, each with a tight scope and its own
cadence, running independently for days** and coordinating only through a shared plan file. It works
because each loop does *one small, verified, honest* thing per fire and never depends on another loop being
mid-flight. Set it up once; it compounds.

## The layers (tune cadence to the work)
Give each loop a **single scope** and a **cadence**. A proven set:
- **Deep-work loop** (frequent, e.g. 30 min) — the core value creation. One thread per iteration: read the
  plan → pick the next genuinely-unexplored item → do it → document → verify → commit → update the plan.
- **BUILD tier** (hourly) — advance the shippable deliverable by the *smallest* slice; verify it (headless/tests); commit.
- **TEST / guard tier** (every ~3 h) — run the full suite; fix reds, or record them honestly as Blockers; add a small test for any untested surface.
- **PLAN / AUDIT tier** (every ~12 h) — step back: run cross-checks, reconcile contradictions between docs, confirm the milestone is progressing, update checkboxes, post a short status summary.
The exact tiers depend on the project — the principle is **separation of concerns across cadences**, not these specific four.

## The rules that make it safe to run unattended
1. **Smallest shippable slice.** Each fire does one bounded thing and leaves the tree green. Never a big risky change autonomously.
2. **Don't manufacture work.** If a fire finds nothing genuinely new, it says so and stops — it does not pad a commit to look busy. (This one rule prevents most autonomous drift.)
3. **Verify before commit, every time.** Reproducible artifacts + mechanical guards (tests, link/consistency checkers, headless "no console errors") catch what no reviewer is watching for. A loop with no guards is a loop that quietly rots.
4. **Honesty over green.** If something is red and can't be fixed this fire, record it in **Blockers** with the exact error — don't hide it. Report outcomes faithfully.
5. **Idempotent + interruptible.** A fire must be safe to run again and safe to interrupt; use a busy-port override / re-check state rather than assume a clean slate.
6. **One thing at a time; don't stomp.** Don't edit files another running loop owns; prefer append-only shared state.

## The shared state (the coordination substrate)
A single **plan file** (here `work/firmware-re/progress/PLAN.md`) is how loops hand off across days:
- **Status / Next / Blockers** — the live picture, kept current. Convert relative dates to **absolute** (a
  loop firing next week must read "2026-08-30", not "yesterday").
- A **"Recent"** append-log — one tight entry per iteration (what shipped, verified, pushed). This is the
  memory a fresh session reads to avoid re-doing work.
Complement it with **durable memory/doctrine files** for cross-session knowledge (techniques, decisions,
"we proved X is closed") so lessons survive context resets — and standing skills (like these) for *how*.

## The per-fire template (every loop, every time)
```
1. Read the plan file (status / next / blockers).
2. Check the coverage/exploration map + existing work — do NOT redo what's done; pick the next real gap.
3. Do ONE small thing.
4. Verify it (run the guards; headless if it's UI).
5. If green: commit + push. If red: fix, or record honestly in Blockers.
6. Update the plan (Recent entry + Next/Blockers). Post a short status if it's an audit fire.
```

## Why it worked (the payoff)
Days of unattended, layered loops produced a 322-commit clean-room reverse-engineering + a working
revival stack — because each fire was small, verified, honest, and coordinated through durable state, so
progress accumulated without a human in the loop and without silent rot. The deep-work loop found the
threads; the build tier shipped the deliverable; the test tier held quality; the audit tier kept the whole
thing coherent. Set the scopes and cadences well, enforce the six rules with mechanical guards, and let it run.

See also: `continuing-moxie-re` (the deep-work loop's content) and `publishing-moxie-docs` (the guards a fire runs).
