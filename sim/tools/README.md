# `sim/tools/` — build helpers for the static site

- **`build_docs_bundle.py`** — copies every Markdown doc under `docs/` (+ top-level `README`/`ROADMAP`)
  into [`../web/docs-bundle/`](../web/) and writes `../web/docs-index.json`, so the **docs explorer**
  ([`../web/docs.html`](../web/docs.html)) can browse them on a static Cloudflare Pages deploy with no
  server. Byte-for-byte copies; re-run it whenever docs change (`node ../test_docs.mjs` fails if the
  bundle is stale). The generated bundle **is committed** so the deploy needs no build step.
- **`prerender_audio.py`** — renders scripted session lines with Piper into `../web/audio/` for the
  static demo (both sides of the conversation). See [`../../docs/guides/deploy-cloudflare.md`](../../docs/guides/deploy-cloudflare.md).
