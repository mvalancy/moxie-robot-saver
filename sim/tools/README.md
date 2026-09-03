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
