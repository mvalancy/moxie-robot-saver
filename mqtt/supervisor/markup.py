"""Behavior-markup hook. v1 is a passthrough (Moxie speaks the plain text).

The expressive text→behavior engine (OpenMoxie's vendored `automarkup`, MIT) plugs
in here to make Moxie gesture/emote — a high-value drop-in. Keeping it as a seam so
the runtime stays model-agnostic."""

def make_markup(text: str) -> str:
    return text
