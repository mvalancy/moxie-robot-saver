"""Behavior-markup seam — the one place a reply without its own markup gets performed.

v1 was a passthrough (Moxie read the line out like a speaker). It now delegates to the
**markup floor**, `moxie_sdk.automarkup.annotate` — a pure, deterministic, stdlib-only
generator that adds a mood, a `<usel>` delivery, arm gestures on the carrying words, a
pause at an internal sentence boundary and a closing `Gesture_None`, every id checked
against the frozen catalog in `moxie_sdk.vocab`.

The seam itself is unchanged on purpose: it runs once per spoken chunk, on the hot path
between the first token and the first audio, and the behavior *planner*
(docs/architecture/backlog/expressiveness.md §2) plugs in right here, behind the same
`make_markup(text, **kw)` signature, when it lands.

`MOXIE_AUTOMARKUP=0` restores the passthrough — a one-variable rollback.
"""
import sys, os

# The SDK is a sibling package of `supervisor/` in the image; the runtime already puts
# `mqtt/` on the path, but keep the seam importable on its own for tests and tools.
_MQTT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _MQTT not in sys.path:
    sys.path.insert(0, _MQTT)

from moxie_sdk.automarkup import annotate, enabled     # noqa: E402


def make_markup(text: str, **kw) -> str:
    """One spoken line -> behavior markup. `turn_key`/`chunk_index` keep a streamed
    answer stable; see `moxie_sdk.automarkup.annotate` for the rules."""
    if not enabled():
        return text
    return annotate(text, **kw)
