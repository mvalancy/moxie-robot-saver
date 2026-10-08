# 🖼️ `sim/web/img/` — site imagery

Real frames rendered from the 3D simulator (`../moxie.js`) in headless Chrome, not stock art.

- [`hero-moxie-cute.png`](hero-moxie-cute.png) — front, happy expression; the hub hero's source
  frame (1200x1470, 1.08 MB) and its `og:image`. The hub shows it as a `<picture>`:
  [`hero-moxie-cute.avif`](hero-moxie-cute.avif) (54 KB, preloaded), then
  [`hero-moxie-cute.webp`](hero-moxie-cute.webp) (111 KB) for a browser without AVIF; only a
  browser with neither downloads the PNG. `sim/test_hub_weight.mjs` holds the hub's first load
  under 400 KB with the beacon production adds, the WebP a browser without AVIF gets included.
- [`hero-moxie-think.png`](hero-moxie-think.png) — thinking pose; the docs explorer overview.
- [`sim-hero.png`](sim-hero.png) — a full SIL page (`../sim.html`) frame in hosted-demo mode; the
  top-level README hero (1424x1251, RGB). Vendored because `img-src 'self' data: blob:` refused the
  old off-site URL ([why](../../../docs/architecture/backlog/vendor-the-readme-hero.md)).

Regenerate: load the simulator, pose via `window.moxie`, screenshot `#stage`. Then re-encode the
hub hero from its PNG with `sharp` (`npm install --no-save sharp`; wrangler pulls it in too):

```sh
node -e 'const s=require("sharp"),i="sim/web/img/hero-moxie-cute";
s(i+".png").avif({quality:88,effort:9,chromaSubsampling:"4:4:4"}).toFile(i+".avif");
s(i+".png").webp({quality:95,effort:6,smartSubsample:true}).toFile(i+".webp")'
```

The face carries a faint scanline texture, and it is what a low quality smooths away first. AVIF
q88 keeps all of it (row-to-row luma energy 1.03, the PNG's 1.03; q80 keeps 86 %). WebP keeps
55 % at q95, the highest quality that holds a browser without AVIF under the 400 KB budget (q97
keeps 84 % at 131 KB). Rendered on the hub at 390x844 and 1440x900, the AVIF and the PNG differ
by at most 15 of 255 levels on any channel (PSNR above 50 dB).

**Doc images live here, not next to the doc.** This directory is the site root's `img/`. Markdown
writes images repo-relative so GitHub renders them; the docs explorer serves the same Markdown from
`../docs-bundle/`, so `../docs.js` maps the `sim/web/` prefix onto the site root, a rule that only
works for images under this directory. `sim/tests/test_no_offsite_images.py` enforces both: no
off-site image, and every referenced image under `sim/web/`.

📖 [sim/web](../README.md) · [Back to top](../../../README.md)
