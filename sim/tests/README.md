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

## The non-browser tests in here

The directory has grown a second, larger family: plain pytest files that need no
browser at all and carry the hermetic suite CI actually runs.

```bash
.venv/bin/python -m pytest sim/tests -q -k "not test_sil and not test_docs" \
  --ignore=sim/tests/test_live_gateway.py      # the hermetic suite
```

- **`helpers_runtime.py`** — the shared harness for anything that drives a turn
  through the real `MoxieRuntime`: a `FakeClient` that records publishes,
  `make_runtime` / `drive_turn` / `drive_once`, and `assert_spec_response` (the
  RemoteChatResponse conformance check). Import this rather than growing a fifth
  private copy of it.
- **`test_console_roundtrip.py`** — the parent console ⇄ supervisor contract, driven
  in-process against a status-server double whose payload keys are diffed against the
  real runtime. Needs `fastapi` + `httpx`; skips cleanly without them (CI has neither).
- **Live tests** (`test_live_gateway.py`, `test_live_action_tags.py`,
  `test_live_content_e2e.py`) — real completions through the LLM gateway. They run
  only when `MOXIE_LLM_API_KEY` (or `LITELLM_MASTER_KEY`) is present, e.g. from the
  git-ignored `mqtt/.env`, and **skip** otherwise, so the hermetic run stays fast and
  CI stays green with no key. `test_live_action_tags.py` asserts a *rate* (2 of 3
  sampled turns) rather than a single sample, because the brain runs at temperature
  0.8 — see its docstring for the measured numbers.

## Run


```bash
sim/tests/run.sh              # sets up the venv on first run, then runs everything
sim/tests/run.sh -q -k alive  # pass any pytest args through
```

It reuses the locally-cached Chrome (`~/.cache/puppeteer/...`, the same binary the
node/puppeteer tests use), so nothing needs downloading. If no Chromium/Chrome is
available, the suite **skips cleanly** (exit 0) — like the node browser tests — so CI
stays green without a browser.
