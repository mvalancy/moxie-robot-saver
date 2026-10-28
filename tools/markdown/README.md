# Public Markdown

Cloudflare continues to serve the committed `sim/web` directory without a build step.
The generator reads only the public HTML and the already-published docs and sample
console data. It writes `.md` counterparts, alternate links, MIME headers and
`llms.txt`; it does not read backend configuration or local accounts.

```sh
npm --prefix tools/markdown ci
npm --prefix tools/markdown run generate
npm --prefix tools/markdown test
npm --prefix tools/markdown run check
```

Regenerate and commit the outputs whenever public source changes. CI rejects stale
Markdown. `/index.md` is the homepage; `/docs.md` links the existing raw document
bundle; `/cloud.md` explicitly labels its fixture as sample data. The browser site
keeps its interactive controls. No `Accept` negotiation or paid Cloudflare feature
is required.
