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
- **`prove_broker_acl.py`** — the assertions behind [`../run_acl_proof.sh`](../run_acl_proof.sh)
  (broker hardening P0, [`security-broker-auth.md`](../../docs/architecture/backlog/security-broker-auth.md)
  §2). Driven against a throwaway mosquitto the shell script starts from the repo's own broker config and
  ACLs. Every check is **delivery-based**: MQTT 3.1.1 PUBACKs a publish the broker then drops for ACL
  reasons and SUBACKs a subscription it will never deliver on, so a proof written against acks would pass
  on a broker with no ACL at all.
