# SIL + static-site pytest automation

A comprehensive [Playwright](https://playwright.dev/python/) suite that drives the
whole static site in a real browser — every page at every standard resolution, and
the simulator through **all of its modes and controls**:

- **All pages × all resolutions** (`phone-portrait` → `ultrawide`): load clean, no
  console errors, no horizontal page scroll.
- **SIL**: click every expression chip (the 11 Eyeseme moods + sleep/thinking/blink);
  drag every motor slider; toggle **ALIVE**/liveness, axes, sound, heart-LED; run the
  speech path (pre-cached chip + free text); center-pose.
- **ALIVE loop**: on by default, stops on toggle-off, mirrors the panel checkbox, and
  a user-held joint is left alone.
- **Docs explorer**: full-text search filters, opening a hit, Mermaid renders.

## Run

```bash
sim/tests/run.sh              # sets up the venv on first run, then runs everything
sim/tests/run.sh -q -k alive  # pass any pytest args through
```

It reuses the locally-cached Chrome (`~/.cache/puppeteer/...`, the same binary the
node/puppeteer tests use), so nothing needs downloading. If no Chromium/Chrome is
available, the suite **skips cleanly** (exit 0) — like the node browser tests — so CI
stays green without a browser.
