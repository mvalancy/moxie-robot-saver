"""A copied `.env.example` must produce working values, not comment text.

`mqtt/.env.example` documents values with inline comments and the documented first step is
to copy it. Before this, `_load_env` took everything after `=`, so

    MOXIE_VOICE_BASE_URL=         # e.g. https://your-gateway/v1 (empty -> Piper/tone)

set the voice base URL to the string `"# e.g. https://…"` — **truthy garbage** that
`build_synthesizer` would then treat as a gateway URL — and `MOXIE_APP` became
`"llm            # llm | content | echo"`. The documented setup path produced a broken
appliance, silently. Found by the class guard shipped alongside the gateway-default fix.
"""
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

import config  # noqa: E402


@pytest.mark.parametrize("raw,want", [
    ("         # e.g. https://your-gateway/v1 (empty -> Piper/tone)", ""),
    ("llm            # llm | content | echo", "llm"),
    ("https://example.invalid/v1", "https://example.invalid/v1"),
    ('"quoted # with hash"', "quoted # with hash"),
    ("'single # quoted'", "single # quoted"),
    ("pass#word", "pass#word"),          # no preceding space: not a comment
    ("value\t# tabbed", "value"),
    ("  spaced  ", "spaced"),
    ("", ""),
    ("#", ""),
])
def test_the_value_half_survives_its_documentation(raw, want):
    assert config._dotenv_value(raw) == want


def test_the_shipped_example_yields_no_comment_text():
    """The real file, parsed the way the loader parses it. This is the regression: every
    value a copier would get must be free of the prose that documents it."""
    path = os.path.join(REPO, "mqtt", ".env.example")
    offenders = []
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, raw = line.partition("=")
        value = config._dotenv_value(raw)
        if "#" in value:
            offenders.append((key.strip(), value[:60]))
    assert not offenders, f"copying .env.example would set comment text: {offenders}"


def test_a_url_shaped_value_is_not_truncated():
    """The failure that would matter most: a real gateway URL must survive intact, since
    a half-parsed URL fails at request time rather than at startup."""
    url = "https://gw.example.invalid/v1"
    assert config._dotenv_value(f"{url}   # the gateway") == url
    assert config._dotenv_value(url) == url
