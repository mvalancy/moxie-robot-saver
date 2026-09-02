#!/usr/bin/env python3
"""
Fetch the two Piper voices the LIVE VOICE tier needs — pinned, verified, idempotent.

`sim/tests/test_live_talk_e2e.py` speaks with **Amy** (Moxie's own voice) and plays the
child with **Lessac**, deliberately a *different* voice so a transcript can never be an
echo of Moxie's own audio. Both are 63 MB ONNX models, so `sim/tts/voices/*.onnx` is
git-ignored (`.gitignore`) — a fresh clone, a `git worktree`, and every CI runner start
without them, which is exactly why that whole tier used to skip in CI.

This script closes that gap without putting 126 MB in git:

* **Pinned.** URLs point at the `v1.0.0` **tag** of `rhasspy/piper-voices` (the official
  Piper voice repository), never at `main` — a moving ref would silently change the voice
  under the acceptance thresholds in `test_live_talk_e2e.py`.
* **Verified.** Every file is checked against a recorded **sha256** *and* byte size. The
  hashes below are Hugging Face's own `x-linked-etag` for those LFS objects (which *is*
  the sha256) and match the copies this repo was developed against, byte for byte.
* **Idempotent.** A file already present with the right size and hash is left alone and
  costs nothing — so a warm `actions/cache` hit, or a developer's existing checkout, does
  not re-download 126 MB. Use `--force` to redownload anyway.
* **Dependency-free.** Standard library only (`urllib`), so it runs before any
  `pip install` in a CI step and in any Python 3.8+ environment.

The small `.onnx.json` configs Piper reads beside each model **are** committed (they are
~5 KB), so normally only the two `.onnx` files are fetched; the configs are listed here
too so the script can also bootstrap a directory that has neither.

    python3 sim/ci/fetch_piper_voices.py                 # into sim/tts/voices/
    python3 sim/ci/fetch_piper_voices.py --dest /some/dir
    python3 sim/ci/fetch_piper_voices.py --check         # verify only, never download

Exit status is 0 only when every file is present and verified.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
DEFAULT_DEST = os.path.join(REPO, "sim", "tts", "voices")

#: Pinned to a TAG, not a branch — see the module docstring.
BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US"

#: filename -> (url, sha256, size_bytes)
VOICES = {
    "en_US-amy-medium.onnx": (
        f"{BASE}/amy/medium/en_US-amy-medium.onnx",
        "b3a6e47b57b8c7fbe6a0ce2518161a50f59a9cdd8a50835c02cb02bdd6206c18",
        63201294,
    ),
    "en_US-amy-medium.onnx.json": (
        f"{BASE}/amy/medium/en_US-amy-medium.onnx.json",
        "95a23eb4d42909d38df73bb9ac7f45f597dbfcde2d1bf9526fdeaf5466977d77",
        4882,
    ),
    "en_US-lessac-medium.onnx": (
        f"{BASE}/lessac/medium/en_US-lessac-medium.onnx",
        "5efe09e69902187827af646e1a6e9d269dee769f9877d17b16b1b46eeaaf019f",
        63201294,
    ),
    "en_US-lessac-medium.onnx.json": (
        f"{BASE}/lessac/medium/en_US-lessac-medium.onnx.json",
        "efe19c417bed055f2d69908248c6ba650fa135bc868b0e6abb3da181dab690a0",
        4885,
    ),
}


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verified(path: str, sha: str, size: int) -> bool:
    """True when `path` is already the pinned file. Size is checked first because it is
    free — hashing 63 MB only happens for a file that is at least the right length."""
    return (os.path.isfile(path) and os.path.getsize(path) == size
            and sha256_of(path) == sha)


def download(url: str, path: str, sha: str, size: int) -> None:
    """Fetch to `path.part`, verify, then rename — so an interrupted run never leaves a
    truncated model that a later run would treat as cached."""
    tmp = path + ".part"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as resp, open(tmp, "wb") as out:
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            out.write(chunk)
    got_size, got_sha = os.path.getsize(tmp), sha256_of(tmp)
    if got_size != size or got_sha != sha:
        os.remove(tmp)
        raise SystemExit(
            f"::error::{os.path.basename(path)} failed verification\n"
            f"  url      {url}\n"
            f"  expected {size} B  sha256 {sha}\n"
            f"  got      {got_size} B  sha256 {got_sha}")
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--dest", default=os.environ.get("MOXIE_VOICES_DIR") or DEFAULT_DEST,
                    help="directory to place the voices in (default: sim/tts/voices)")
    ap.add_argument("--check", action="store_true",
                    help="verify what is present; never download")
    ap.add_argument("--force", action="store_true",
                    help="redownload even when the file already verifies")
    args = ap.parse_args(argv)

    dest = os.path.abspath(args.dest)
    os.makedirs(dest, exist_ok=True)
    missing = 0
    for name, (url, sha, size) in VOICES.items():
        path = os.path.join(dest, name)
        if not args.force and verified(path, sha, size):
            print(f"ok       {name}  ({size} B, sha256 verified, not re-downloaded)")
            continue
        if args.check:
            print(f"MISSING  {name}  (expected {size} B sha256 {sha[:16]}…)")
            missing += 1
            continue
        print(f"fetching {name}  <- {url}")
        download(url, path, sha, size)
        print(f"ok       {name}  ({size} B, sha256 verified)")
    if missing:
        print(f"\n{missing} voice file(s) missing under {dest}", file=sys.stderr)
        return 1
    print(f"\nPiper voices ready in {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
