# 🖼️ The README hero is an off-site image, and the live docs page cannot load it

> **Filed 2026-09-04 from a production measurement · 🟢 SHIPPED 2026-09-04 · effort S.**
>
> **What shipped, and where the difficulty actually was.** The image is vendored at
> [`sim/web/img/sim-hero.png`](../../../sim/web/img/sim-hero.png) (1424×1251 PNG, 599 KiB — the
> fetched bytes re-encoded RGB, since the alpha channel was uniformly opaque; the pixels are
> unchanged). `img-src` was **not** touched. The copy was the easy half: `build_docs_bundle.py`
> rewrites nothing — it is a byte-for-byte copier, deliberately — so the depth problem in §2 was
> solved where the depth is actually known. [`sim/web/docs.js`](../../../sim/web/docs.js) now
> resolves a doc's `<img src>` against that doc's **repo** path (inverting the bundler's mapping)
> and strips the `sim/web/` prefix, because `sim/web` *is* the Pages output root. One rule,
> correct from any doc depth — which is why doc images live under `sim/web/img/`. Guards:
> [`sim/tests/test_no_offsite_images.py`](../../../sim/tests/test_no_offsite_images.py) (8 tests,
> browser-free, two planted negative controls and the code-fence exemption tested both ways),
> `sim/test_csp.mjs` 77 → **79** and `sim/test_docs_explorer.mjs` 22 → **25**. **3 checks redden on
> each pre-change tree**, measured by stashing — including the zero-violation assertion itself,
> which had been exempting `/user-attachments/` by substring, and the explorer's
> `blocked.n === 0`, which had been absorbing three aborted off-origin requests per run.

## The finding

`https://moxie.mattvalancy.com/docs.html` throws exactly one CSP violation, every load:

```
img-src :: https://github.com/user-attachments/assets/81f325da-725c-4902-9d1f-9233f0b5cf97
Loading the image '…' violates the Content Security Policy directive "img-src 'self' data: blob:"
```

Measured in a real browser against production on 2026-09-04, after the `script-src` hardening
promoted. It is **not** a regression from that work: `img-src 'self' data: blob:` is byte-identical
either side of [PR #137](https://github.com/mvalancy/moxie-robot-saver/pull/137), and production
served the same README under the same directive before it. The CSP pass simply made it visible, in
the same way it made the ambient-guard race visible.

## Why it is worth fixing rather than allow-listing

There is exactly **one** such URL in the whole repo — [`README.md`](../../../README.md):17, the SIL
simulator hero shot:

```html
<img width="712" alt="Moxie SIL simulator" src="https://github.com/user-attachments/assets/81f325da-…" />
```

Widening `img-src` to `https://github.com` would re-open an exfiltration channel for one picture.
Vendoring it closes the violation **and** satisfies the standing repo-self-sufficiency rule — the
repo alone must bring the robots to life, and a hero image hosted on someone else's CDN is a link
that rots. A GitHub user-attachment URL is not even a stable public address; it is tied to the
upload, not to the repo.

## The brief

1. Fetch the image once and commit it in-repo (suggested `docs/assets/sim-hero.png`; check the real
   content type first — do not assume PNG).
2. Rewrite `README.md`:17 to a repo-relative path. **It must render in both places**, which is the
   whole risk in this slice:
   - on GitHub's rendered README, and
   - in the docs explorer, which serves `README.md` from the generated bundle at
     `sim/web/docs-bundle/_root/README.md` — a *different* directory depth from the source, so a
     relative path that works in one may 404 in the other. Read `sim/tools/build_docs_bundle.py`
     to see whether it rewrites asset paths at all; if it does not, that is the actual work.
3. Do not touch `sim/web/_headers`. `img-src` stays as it is.

## Acceptance

- A real browser load of `docs.html` reports **zero** `securitypolicyviolation` events — assert on
  the event, not on the image looking present.
- The image element has non-zero `naturalWidth`, so a 404 that renders as a broken-image icon cannot
  pass.
- Extend `sim/test_csp.mjs` (77 checks) or `sim/test_docs_explorer.mjs` (22); the new assertion must
  be shown to fail on the pre-change tree.
- A guard that no tracked Markdown references an off-site image again — one grep, so the next one is
  caught at commit time rather than by a browser months later.

## Honest note

Cosmetic in impact — one broken image on one page. It is filed because it is the only measured
defect standing between the live site and a clean console, and because the guard in step 4 is worth
more than the image.
