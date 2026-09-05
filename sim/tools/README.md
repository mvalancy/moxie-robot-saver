# `sim/tools/` — build helpers for the static site, and one proof

- **`build_docs_bundle.py`** — copies every Markdown doc under `docs/` (+ top-level `README`/`ROADMAP`)
  into [`../web/docs-bundle/`](../web/) and writes `../web/docs-index.json`, so the **docs explorer**
  ([`../web/docs.html`](../web/docs.html)) can browse them on a static Cloudflare Pages deploy with no
  server. Byte-for-byte copies; re-run it whenever docs change (`node ../test_docs.mjs` fails if the
  bundle is stale). The generated bundle **is committed** so the deploy needs no build step.
- **`prerender_audio.py`** — renders scripted session lines with Piper into `../web/audio/` for the
  static demo (both sides of the conversation). See [`../../docs/guides/deploy-cloudflare.md`](../../docs/guides/deploy-cloudflare.md).
- **`build_ext_conformance.py`** — regenerates
  [`../tests/data/ext_conformance.json`](../tests/data/README.md), the six hand-ported OpenMoxie
  hooks that are the golden set for [sandboxed content
  extensions](../../docs/architecture/backlog/sandboxed-extensions.md) §8. The goldens are
  committed and the test reads the *file*, never this script, so a bug here cannot quietly rewrite
  what it is meant to be checked against. Note the deliberate `sort_keys=False`: `let` is an
  **ordered** map, so sorting the keys would silently reorder every program in the file — the
  escape suite caught exactly that.
- **`ext_mutation_check.py`** — the other direction of *"a test for every feature"*. It removes each
  of **28 guards** the extension sandbox rests on — the `_`-segment path refusal, the fact-root
  refusal, the step and wall-clock budgets, the byte caps, both depth caps, the injected clock and
  seed, the NFKC identity check, the memory-key grammar, the host-supplied namespace, the two
  capability-equality checks, the all-or-nothing effect list, the jinja2 sandbox, the pattern cap —
  and requires the corresponding test to go **red**. All 28 are caught. Run it by hand after
  touching `ext.py`, `render.py`, `content_app.py`'s host half or `packs.py`'s pattern cap; a green
  suite proves a guard is *present*, and only this proves it is *load-bearing*.
- **`subscribe_mutation_check.py`** — the same proof for the `subscribe` capability's **25 guards**:
  the load-time event allowlist, the width of `ext.SUBSCRIBE_EVENTS`, the P1 gate, both host
  boundaries, both de-duplications, the two **merge-direction** rows, `MOXIE_VISION`, the pairing
  gate, four *set-but-never-sent* shapes, and — since the inbound half landed on 2026-09-05 — nine
  more for being **woken**: the subscribed-only gate, its module keying, both inbound gates, the
  record and *where* it is written, the module-exit forget, the empty-answer fall-through, and S17,
  which routes a perceived event to `app.respond` instead of to the pack's local evaluator. S17 is
  the sharpest row in the file because every visible behaviour survives it — the pack answers, the
  child hears a line, the wire is well formed — and the only thing that notices is
  `moxie_sdk.chat.model_calls()`. **25/25 caught.** A separate table from
  `ext_mutation_check.py` because half these guards live in `mqtt/supervisor/moxie_runtime.py`, which
  that checker's single-file runner cannot see. Two rows earn the file on their own: a merge in which a
  content pack's event list **replaces** the supervisor's fails *silently*, because
  `_vision_subscription` latches *"subscribed"* for a `(device, module)` at the moment it hands its
  list over — so presence, the greeting rule and launch cards would go quiet with nothing logged. The
  run also found a **weak test**: rows S12/S13 left the wire assertion green in its first draft,
  because every grantable event is in the runtime's own list and the assertion was satisfied without
  the pack's contribution at all.
- **`build_performance_goldens.py`** — regenerates
  [`../tests/goldens/performance.json`](../tests/goldens/), the behavior planner's 22
  dialog-act goldens as JSON `Performance` objects **plus** the markup each renders to
  ([`expressiveness.md`](../../docs/architecture/backlog/expressiveness.md) §2.5). The
  goldens are committed and both `sim/tests/test_performance.py` and
  `../test_performance_render.mjs` read the *file*, so this script can never quietly
  rewrite what it is checked against.
- **`performance_mutation_check.py`** / **`ext_mutation_check.py`** / **`brain_mutation_check.py`**
  — the "a guard is *present*" → "a guard is *load-bearing*" step for the planner (39
  mutations), the extension sandbox (28) and the brain registry. Each removes one guard at
  a time and requires a test to go red. Run by hand after touching the code they cover.
- **`first_audio_ab.py`** — the first-audio latency A/B across `MOXIE_EXPRESSIVE`. Boots
  the real stack (a real broker, `mqtt/run.py` as its own process), connects a
  protocol-faithful robot, and times from the robot's own `events/remote-chat` publish to
  the first `commands/remote_chat` carrying words and the first `commands/tts` carrying
  audio — one supervisor boot per arm. `--brain stub` streams a fixed answer at a fixed
  pace so the seam is measurable at high N for free; `--brain live` spends one chat
  completion per turn and is the only number comparable to PR #15's 1.52 s. Written
  because [`expressiveness.md`](../../docs/architecture/backlog/expressiveness.md) §2.7
  criterion (f) had a *bench* measurement and said so.
- **`build_csp_hashes.py`** — regenerates the SHA-256 sources in `sim/web/_headers`' `script-src`
  from the inline `<script>` blocks in `sim/web/*.html`, and fails the build on an inline
  `on*=` attribute or a `javascript:` URL (which no hash this policy grants can cover, and
  which fail *silently* — the handler never fires). `--check` proves the committed header
  still matches the pages; it runs as its own CI step, again from `sim/tests/test_csp_hashes.py`,
  and a third time inside `sim/test_csp.mjs`. Three places for one guard is deliberate: a hash
  that drifts from its block does not degrade, it **blanks the page**, and only Cloudflare Pages
  ever sends `_headers`, so nothing local would otherwise see it. It owns the hash list and
  nothing else — every other `script-src` source is carried through untouched.
- **`check_bundle_fresh.py`** — asserts the committed docs bundle matches `docs/`, so a
  doc edit that forgets `build_docs_bundle.py` fails locally instead of shipping stale.
- **`probe_demo_gateway.mjs`** — probes a deployed demo's gateway routes from outside,
  the way a visitor's browser reaches them.
- **`prove_broker_acl.py`** — the assertions behind [`../run_acl_proof.sh`](../run_acl_proof.sh)
  (broker hardening P0, [`security-broker-auth.md`](../../docs/architecture/backlog/security-broker-auth.md)
  §2). Driven against a throwaway mosquitto the shell script starts from the repo's own broker config and
  ACLs. Every check is **delivery-based**: MQTT 3.1.1 PUBACKs a publish the broker then drops for ACL
  reasons and SUBACKs a subscription it will never deliver on, so a proof written against acks would pass
  on a broker with no ACL at all.
- **`hardening_mutation_check.py`** — the same proof for [production
  hardening](../../docs/architecture/backlog/production-hardening.md) P0: **35 mutations** across
  `moxie_sdk/store.py`'s cross-process lock and the connection region of
  `supervisor/moxie_runtime.py`. Two of them are deliberately the *half-done fixes* the brief warns
  about rather than deleted guards — `connect_async` without `retry_first_connection=True` (a no-op
  under `loop_forever`, risk R2) and the lock moved from the `.lock` sidecar onto the data file
  (looks correct, serializes nothing, because `os.replace` swaps the inode — risk R1) — because that
  is what a plausible patch actually looks like. All 35 are caught; the run that got there found
  **five** holes, four of them the same disease: two guards each covering for the other's absence, so
  neither was individually load-bearing. Run it after touching either file.
- **`hardening_p1_mutation_check.py`** — the same proof for production hardening **P1**: **66
  mutations** across `moxie_sdk/roster.py`, `moxie_sdk/conn_telemetry.py`, `store.py::_append_path`, the
  connection/shutdown/onboarding regions of `supervisor/moxie_runtime.py` and the console's connection
  normalizer. Several are deliberately *plausible patches rather than deletions*, because that is what a
  regression looks like in review — the roster resume marking rostered robots as **connected** (a status
  field reporting a belief instead of an observation), `gap_since` returning `0.0` instead of `None` for a
  first connect, the shutdown row written *after* `disconnect()`, and `_stopping` hard-wired True, which
  passes *"a clean stop is not an outage"* while silently erasing every real outage. Four of them found
  real holes: a test that asserted the roster's key negative property about **a code
  path it never called**, a missing-lock mutation no single-writer test could ever see, and
  `JsonStore.append` ignoring its own write's return code — plus an `OverflowError` in the lock backoff that had been reported as a *flake* (`2 ** attempt` past 1024). One benign finding worth keeping: a property
  guarded **twice**, where neither guard is individually load-bearing — so the mutation had to remove both
  at once. The checker also reports a `-k` selector that matched **no test** as a NO-OP, because a renamed
  test is how a mutation table rots into reporting "caught" forever.
- **`turnstile_mutation_check.py`** — the same proof for the **Cloudflare Turnstile bot control**
  in front of `POST /api/chat` **and** `POST /api/transcribe`: **57 mutations** across
  [`functions/api/_lib/turnstile.js`](../../functions/api/_lib/turnstile.js), the guard step in
  [`chat.js`](../../functions/api/chat.js) and [`transcribe.js`](../../functions/api/transcribe.js),
  the budget refund in [`_lib/limits.js`](../../functions/api/_lib/limits.js), the sitekey's one
  delivery path in [`health.js`](../../functions/api/health.js), `_lib/env.js`'s config pair, the
  app-script cache list in [`sim/web/_headers`](../../sim/web/_headers), and the three browser files —
  [`sim/web/turnstile.js`](../../sim/web/turnstile.js),
  [`cloud-transport.js`](../../sim/web/cloud-transport.js) and [`mic.js`](../../sim/web/mic.js).
  **IT NEVER TOUCHES THE CHECKOUT**: it hardlink-copies `functions/` and `sim/` into a throwaway
  directory (~0.2 s, because hardlinks copy metadata and not bytes), replaces the files it mutates with
  real copies so no write can reach the original inode, and runs `node` there. The first version rewrote
  the live worktree and restored it in a `finally` — which left mandatory check 2 **disabled in the tree**
  after a run that was killed, and made two concurrent runs redden each other's suites on rows that had
  no defect behind them. **It is STRICTER than the five tables above,
  deliberately**: they run `pytest -k <selector>` and treat any non-zero exit as *caught*, which for a
  security control is too weak — a mutation that broke some unrelated assertion would read as caught
  while the guard it targeted went unexercised. This one runs `node sim/test_turnstile.mjs` and requires
  the selector to appear in **a failing check's own label**, so a row is caught only when the check that
  *names that guard* is the one that reddened. That strictness paid for itself on the first run: four
  rows came back **NOT CAUGHT or WRONG CHECK**, and each was a real hole in the tests — a `success:false`
  case that was actually being refused by the *action* check (so deleting the success check changed
  nothing), a suffix-matching mutation that only touched the `DEMO_TURNSTILE_HOSTS` branch no test
  exercised, a `!res.ok` deletion that fell open by accident because the 500 in the fixture had an empty
  body, and a `publicTurnstile` mutation invisible because no test configured a sitekey **without** a
  secret. Rows include both halves of the fail-open/fail-closed split (a control that lets the refused
  case through, and a Cloudflare outage that takes the whole demo down), the concurrency-slot release
  **and its negative control** — the release neutered in `limits.js`, so *"the in-flight count is back to
  zero"* cannot pass on a counter that is always zero — and the plausible-patch shape the other tables
  favour: the refusal hoisted *outside* the `try` whose `finally` returns the slot, which reads as tidier
  and leaks a slot for ever. One row is caught by **hanging** (no deadline on the verification call, which
  holds a concurrency slot) and says so, because "it never finished" is a different fact from "it went
  red". Pass a row name to re-check one without waiting out that hang:
  `python3 sim/tools/turnstile_mutation_check.py D3e`.
  The 2026-09-05 review pass added 29 rows and they found five more holes of the same kind: the action
  compared with `startsWith` or case-folded (both served a `chat-newsletter` token on the expensive
  route and both passed green), the ears verifying nothing at all, a refusal that kept the units
  admission charged (a *free* budget drain in place of a paid one), `/api/health` no longer publishing
  the sitekey — the browser's ONLY source of it — with **eleven** suites still green, and a client that
  memoised a failed script load and so disabled every turn for the life of the page. `sim/tests/
  test_mutation_tables.py` now also pins the row COUNT stated in the docs against the table, because a
  README that said 26 while the table held 28 is how a reader loses the ability to tell a table that grew
  from a selector that silently stopped matching.
- **`soak.py`** — the SIL soak behind [`../run_soak.sh`](../run_soak.sh)
  ([production hardening](../../docs/architecture/backlog/production-hardening.md) §5): real mosquitto in
  a container, a real `mqtt/run.py`, real virtual robots, `MOXIE_APP=echo` so nothing reaches a gateway.
  Three profiles (`smoke` ~1 min · `quick` ~5 min · `week` 60 min) and **twelve** numeric bars printed
  pass or fail, never inferred — with §5.4 under every report, because *"a week in an hour"* is a **rate
  substitution** and not a duration. Two design points: every turn is stamped with whether the broker was
  up **when it was issued** (A1 counts only those; a bare pass/fail could not tell a bug from an injected
  fault), and the contention probe checks an **identity** rather than a survival count —
  `attempted == on_disk + refused`, which is the only thing that distinguishes a *silent loss* (A5, must
  be 0) from the *recorded refusal* §3.2 point 4 explicitly accepts. It restarts the supervisor with
  **SIGTERM**, so the clean-shutdown path is exercised by the harness and not only by a unit test.
