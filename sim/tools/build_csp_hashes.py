#!/usr/bin/env python3
"""Generate the SHA-256 `script-src` sources in `sim/web/_headers` from the pages on disk.

WHY THIS EXISTS AT ALL. `sim/web/_headers` is a STATIC file served by Cloudflare Pages, so
a per-response nonce is impossible and a hash is the only way an inline `<script>` can run
under `script-src 'self'`. A hash is a hostile thing to maintain by hand: when it drifts
from the block it covers the page does not degrade, it goes BLANK — and it goes blank in
production, because every browser suite in this repo would still be serving the old bytes.

So the rule this file enforces is: **the header is generated, never typed.** Run it after
touching any inline block; `--check` proves the committed header matches the pages, and
`sim/tests/test_csp_hashes.py` runs that check in CI.

THE SURFACE IS DELIBERATELY TINY. As of 2026-09-04 exactly ONE inline block remains on the
whole site — `sim.html`'s `<script type="importmap">`, which cannot be an external file in
any browser (the `src` form was dropped from the spec and never shipped). The other
thirteen were MOVED INTO FILES rather than hashed, because a file that does not exist
cannot drift. Keep it that way: if this tool starts reporting more than one hash, the right
fix is almost always to move the new block into a `.js` file, not to accept the hash.

    python3 sim/tools/build_csp_hashes.py            # rewrite _headers in place
    python3 sim/tools/build_csp_hashes.py --check    # exit 1 if it is out of date

Exit 0 = header matches the pages. Exit 1 = drift (or a block that no hash can rescue).
"""
import base64
import hashlib
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
WEB = os.path.join(REPO, "sim", "web")
HEADERS = os.path.join(WEB, "_headers")

#: An inline `<script>` is one with no `src=`. `type` is irrelevant: an importmap, a module
#: and a classic script are all hashed the same way, over their text content.
SCRIPT = re.compile(r"<script(?![^>]*\bsrc\s*=)([^>]*)>(.*?)</script>", re.S)

#: What NO hash can rescue. An inline event-handler ATTRIBUTE (`<b onclick="f()">`) or a
#: `javascript:` URL needs `'unsafe-hashes'` plus a hash per handler, and this site ships
#: neither. Note what is NOT matched: `el.onclick = function(){}` in a .js file assigns a
#: function OBJECT and is not an inline script at all — CSP has no opinion on it. That
#: distinction is why `'unsafe-hashes'` is not in the policy; see `sim/web/_headers`.
HANDLER_ATTR = re.compile(r"<[^>!][^>]*?\son[a-z]+\s*=\s*[\"'][^\"']*[\"'][^>]*>", re.S | re.I)
JS_URL = re.compile(r"(?:href|src|action|formaction)\s*=\s*[\"']\s*javascript:", re.I)


def sha256_source(text):
    """The CSP source expression for an inline block's text content."""
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode("utf-8")).digest()).decode() + "'"


def scan():
    """(hashes, blocks, problems) for every shipped page, in a stable order."""
    hashes, blocks, problems = [], [], []
    for name in sorted(os.listdir(WEB)):
        if not name.endswith(".html"):
            continue
        src = open(os.path.join(WEB, name), encoding="utf-8").read()
        for m in SCRIPT.finditer(src):
            line = src[:m.start()].count("\n") + 1
            h = sha256_source(m.group(2))
            blocks.append((name, line, (m.group(1) or "").strip(), h))
            if h not in hashes:
                hashes.append(h)
        for m in HANDLER_ATTR.finditer(src):
            problems.append("%s:%d inline event-handler ATTRIBUTE — no hash covers this "
                            "without 'unsafe-hashes'; use addEventListener in a .js file"
                            % (name, src[:m.start()].count("\n") + 1))
        for m in JS_URL.finditer(src):
            problems.append("%s:%d javascript: URL — refused by script-src; use a listener"
                            % (name, src[:m.start()].count("\n") + 1))
    return sorted(hashes), blocks, problems


def script_src(hashes, current):
    """The `script-src` the pages imply, given the one that is there now.

    THIS TOOL OWNS THE HASHES AND NOTHING ELSE. The host allowance in `script-src` —
    today exactly one, Cloudflare's injected analytics beacon — is a policy decision
    argued out in `sim/web/_headers` and pinned by `sim/test_csp.mjs`; a generator that
    rebuilt the whole directive from a constant in *this* file would silently overwrite
    that decision the first time it changed, and would put somebody's deployment hostname
    in shipped Python (which `sim/tests/test_no_deployment_defaults.py` forbids as a
    class). So every non-hash source is carried through in its existing order, and only
    the `'sha256-…'` list is regenerated — inserted right after `'self'`, which is where
    it reads.
    """
    keep = [t for t in current.split()[1:]
            if not t.startswith("'sha256-") and t not in ("'unsafe-inline'", "'unsafe-hashes'")]
    if "'self'" in keep:
        i = keep.index("'self'") + 1
    else:                       # no 'self' to anchor to: put the hashes first and say so
        keep.insert(0, "'self'")
        i = 1
    return " ".join(["script-src"] + keep[:i] + hashes + keep[i:])


def read_policy():
    """(full _headers text, the CSP line as written, its `script-src` value)."""
    text = open(HEADERS, encoding="utf-8").read()
    m = re.search(r"^\s+Content-Security-Policy:[ \t]*(.+)$", text, re.M)
    if not m:
        raise SystemExit("❌ no Content-Security-Policy line in sim/web/_headers")
    directive = next((d.strip() for d in m.group(1).split(";")
                      if d.strip().startswith("script-src")), None)
    if directive is None:
        raise SystemExit("❌ the CSP in sim/web/_headers has no script-src directive")
    return text, m.group(1), directive


def main(argv):
    check = "--check" in argv
    hashes, blocks, problems = scan()
    text, csp, current = read_policy()
    want = script_src(hashes, current)

    if problems:
        print("❌ CSP hash build FAILED — these cannot be covered by any hash this policy grants:")
        for p in problems:
            print("     ", p)
        return 1

    for bad in ("'unsafe-inline'", "'unsafe-hashes'"):
        if bad in current:
            if check:
                print("❌ script-src still carries %s — the hole this generator closed." % bad)
                return 1
            print("ℹ️  dropping %s from script-src" % bad)

    if current == want:
        print("✅ script-src is current — %d inline block(s), %d hash(es):"
              % (len(blocks), len(hashes)))
        for name, line, attrs, h in blocks:
            print("     %s:%d  <script %s>  %s" % (name, line, attrs, h))
        return 0

    if check:
        print("❌ script-src in sim/web/_headers is STALE — an inline block changed and the")
        print("   header did not. SHIPPING THIS BLANKS THE PAGE: the browser refuses a block")
        print("   whose hash is not listed, and no local suite serves the old header.")
        print("   Run:  python3 sim/tools/build_csp_hashes.py   and commit sim/web/_headers.")
        print("   have: %s" % current)
        print("   want: %s" % want)
        for name, line, attrs, h in blocks:
            print("     %s:%d  <script %s>  %s" % (name, line, attrs, h))
        return 1

    open(HEADERS, "w", encoding="utf-8").write(text.replace(csp, csp.replace(current, want), 1))
    print("✅ rewrote script-src in sim/web/_headers — %d inline block(s), %d hash(es):"
          % (len(blocks), len(hashes)))
    for name, line, attrs, h in blocks:
        print("     %s:%d  <script %s>  %s" % (name, line, attrs, h))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
