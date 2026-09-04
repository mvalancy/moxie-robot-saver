# `sim/web/img/` — site imagery

Rendered from the live 3D simulator (`../moxie.js`) via headless Chrome — real frames, not stock art.
- `hero-moxie-cute.png` — front, happy expression · used on the hub hero.
- `hero-moxie-think.png` — thinking pose · used on the docs explorer overview.
- `sim-hero.png` — a full frame of the SIL page (`../sim.html`) in hosted-demo mode ·
  the top-of-README hero. Vendored 2026-09-04 from the GitHub user-attachment it used to
  be embedded from, which `img-src 'self' data: blob:` correctly refused on every
  `docs.html` load (see `docs/architecture/backlog/vendor-the-readme-hero.md`).
  1424×1251, re-encoded RGB (the source's alpha channel was uniformly opaque) — the
  pixels are unchanged, the file is 84 KB smaller.

Regenerate by loading the simulator, posing via `window.moxie`, and screenshotting `#stage`.

## Why doc images live HERE and not next to the doc

**This directory is the site root's `img/`.** A `<img>` in a Markdown doc is written
repo-relative so GitHub renders it, but the docs explorer serves that same Markdown from
`../docs-bundle/` at a different depth — so the raw string would 404 there. `../docs.js`
resolves a doc's image against the doc's own repo path and maps the `sim/web/` prefix onto
the site root, which is exactly one rule and only works for images under this directory.
`sim/tests/test_no_offsite_images.py` enforces both halves: no off-site image, and every
referenced image under `sim/web/`.
