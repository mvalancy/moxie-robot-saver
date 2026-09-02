# `sim/tools/` — build helpers for the static site, and one proof

- **`build_docs_bundle.py`** — copies every Markdown doc under `docs/` (+ top-level `README`/`ROADMAP`)
  into [`../web/docs-bundle/`](../web/) and writes `../web/docs-index.json`, so the **docs explorer**
  ([`../web/docs.html`](../web/docs.html)) can browse them on a static Cloudflare Pages deploy with no
  server. Byte-for-byte copies; re-run it whenever docs change (`node ../test_docs.mjs` fails if the
  bundle is stale). The generated bundle **is committed** so the deploy needs no build step.
- **`prerender_audio.py`** — renders scripted session lines with Piper into `../web/audio/` for the
  static demo (both sides of the conversation). See [`../../docs/guides/deploy-cloudflare.md`](../../docs/guides/deploy-cloudflare.md).
- **`prove_broker_acl.py`** — the assertions behind [`../run_acl_proof.sh`](../run_acl_proof.sh)
  (broker hardening P0, [`security-broker-auth.md`](../../docs/architecture/backlog/security-broker-auth.md)
  §2). Driven against a throwaway mosquitto the shell script starts from the repo's own broker config and
  ACLs. Every check is **delivery-based**: MQTT 3.1.1 PUBACKs a publish the broker then drops for ACL
  reasons and SUBACKs a subscription it will never deliver on, so a proof written against acks would pass
  on a broker with no ACL at all.
