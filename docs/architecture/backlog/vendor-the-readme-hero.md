# Doc images are vendored, never off-site

**Status:** fixed — hero vendored at [`sim/web/img/sim-hero.png`](../../../sim/web/img/sim-hero.png),
doc images resolved by [`sim/web/docs.js`](../../../sim/web/docs.js) (`fixImages`), guarded by
[`sim/tests/test_no_offsite_images.py`](../../../sim/tests/test_no_offsite_images.py),
`sim/test_csp.mjs` and `sim/test_docs_explorer.mjs`.

## The defect

The live docs explorer (`docs.html`) threw one CSP violation on every load. The README's hero image
pointed at a GitHub upload:

```html
<img width="712" alt="Moxie SIL simulator" src="https://github.com/user-attachments/assets/81f325da-725c-4902-9d1f-9233f0b5cf97" />
```

and the shipped policy is `img-src 'self' data: blob:`, so the browser refused it. That URL was the
only off-site image in the repo. (It is quoted here inside a code fence on purpose: the guard treats
fenced and inline code as quotations, and a test reads this file to prove that exemption works on a
real doc.)

## Decisions

- **Vendor the image, do not widen `img-src`.** Allowing `https://github.com` would reopen an
  exfiltration channel for one picture. A user-attachment URL is also tied to an upload, not the repo,
  so it breaks the rule that the repo alone must be enough to run and read everything.
  `sim/web/_headers` was not changed.
- **Images live under `sim/web/img/`.** `sim/web` is the Pages output root (`wrangler.toml`
  `pages_build_output_dir`), so any file under it has a served URL; a file anywhere else does not.
- **Fix paths in the browser, not the bundler.** `sim/tools/build_docs_bundle.py` is a deliberate
  byte-for-byte copier. The explorer serves docs from `docs-bundle/`, a different depth than the repo,
  so a repo-relative `<img src>` would 404 there. `docs.js` resolves each `src` against the doc's
  **repo** path (inverting the bundle mapping, with the two top-level docs under `_root/`) and strips
  the `sim/web/` prefix. One rule, correct from any doc depth, and the same Markdown still renders on
  GitHub.

The README now references `sim/web/img/sim-hero.png`.

## The guard

`sim/tests/test_no_offsite_images.py` (browser-free) enforces two rules over every tracked Markdown
file, including the generated bundle's content:

1. No image reference to another origin (`https://…`, `//…`).
2. Every image a source doc references resolves to an existing file under `sim/web/`.

It carries planted negative controls, tests the code-fence exemption in both directions, and
`test_the_backlog_entry_that_quotes_the_bad_url_is_green` reads this file: it must still contain the
string `user-attachments` and must still pass rule 1.

In the browser, `sim/test_csp.mjs` asserts zero `securitypolicyviolation` events on `docs.html` (on the
event, not on the image looking present), and `sim/test_docs_explorer.mjs` asserts the image has a
non-zero `naturalWidth` and that no off-origin request was blocked. Both were shown to fail on the
pre-change tree.

---
📖 [Backlog index](README.md) · [Image assets](../../../sim/web/img/README.md)
