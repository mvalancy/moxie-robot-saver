# 🖼️ `sim/web/img/` — site imagery

Real frames rendered from the 3D simulator (`../moxie.js`) in headless Chrome, not stock art.

- [`hero-moxie-cute.png`](hero-moxie-cute.png) — front, happy expression; the hub hero.
- [`hero-moxie-think.png`](hero-moxie-think.png) — thinking pose; the docs explorer overview.
- [`sim-hero.png`](sim-hero.png) — a full SIL page (`../sim.html`) frame in hosted-demo mode; the
  top-level README hero (1424x1251, RGB). Vendored because `img-src 'self' data: blob:` refused the
  old off-site URL ([why](../../../docs/architecture/backlog/vendor-the-readme-hero.md)).

Regenerate: load the simulator, pose via `window.moxie`, screenshot `#stage`.

**Doc images live here, not next to the doc.** This directory is the site root's `img/`. Markdown
writes images repo-relative so GitHub renders them; the docs explorer serves the same Markdown from
`../docs-bundle/`, so `../docs.js` maps the `sim/web/` prefix onto the site root, a rule that only
works for images under this directory. `sim/tests/test_no_offsite_images.py` enforces both: no
off-site image, and every referenced image under `sim/web/`.

📖 [sim/web](../README.md) · [Back to top](../../../README.md)
