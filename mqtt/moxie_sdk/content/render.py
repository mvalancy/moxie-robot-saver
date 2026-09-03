"""
Prompt rendering — a content module's `prompt` is Jinja2-templated over the
volley/session (docs/architecture/content-module-contract.md).

Uses real Jinja2 when it's installed (full `{% if %}`/filters), and otherwise falls
back to a minimal, safe `{{ dotted.path }}` substitution so the SDK has no hard
templating dependency. The fallback covers the common personalization case
(`{{ volley.config.child_pii.nickname }}`, `{{ volley.persist_data.* }}`).
"""
from __future__ import annotations
import re

_VAR = re.compile(r"\{\{\s*([a-zA-Z_][\w.]*)\s*\}\}")


def _resolve(path: str, context: dict):
    """Walk a dotted path over dicts/objects; missing → ''."""
    cur = context
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            cur = getattr(cur, part, None)
        if cur is None:
            return ""
    return cur


def _minimal_render(template: str, context: dict) -> str:
    return _VAR.sub(lambda m: str(_resolve(m.group(1), context)), template)


#: Incremented whenever a template asks for something the sandbox refuses. A
#: content pack that trips this is either broken or hostile, so it is worth
#: seeing rather than swallowing — `render_prompt` still returns safe text.
BLOCKED = 0


def _sandbox():
    """A `SandboxedEnvironment` that counts what it refuses. Raises `ImportError` when
    jinja2 is absent, which is the shipped container's situation."""
    from jinja2.sandbox import SandboxedEnvironment
    from jinja2 import ChainableUndefined

    class _CountingSandbox(SandboxedEnvironment):
        def is_safe_attribute(self, obj, attr, value):
            ok = super().is_safe_attribute(obj, attr, value)
            if not ok:
                global BLOCKED
                BLOCKED += 1
            return ok

    return _CountingSandbox(undefined=ChainableUndefined, autoescape=False,
                            keep_trailing_newline=True)


def render_prompt(template: str, context: dict) -> str:
    """Render `template` over `context` (e.g. {'volley': v, 'session': s}).

    Prefers Jinja2 if available; the minimal fallback handles `{{ dotted.path }}`.

    **The environment is sandboxed, and that is load-bearing.** A template here is
    *untrusted input*: `prompt` and `opener` travel inside a content pack
    (`moxie_sdk/content/packs.py`), so anyone who can hand a parent a pack can choose
    this string. Under a plain `jinja2.Environment` that is server-side code
    execution — proven by execution on jinja2 3.1.2, where
    `{{ cycler.__init__.__globals__['os'].name }}` returned the host's platform and
    `''.__class__.__mro__[1].__subclasses__()` enumerated 364 host classes.
    `SandboxedEnvironment` refuses that attribute walk; `sim/tests/test_render_sandbox.py`
    pins each probe.

    **How the refusal actually behaves** (measured, not assumed): the sandbox does not
    raise on an unsafe attribute. `is_safe_attribute` returns False and Jinja substitutes
    an *undefined* object, which with `ChainableUndefined` renders as an empty string. So
    a hostile template comes back inert and the turn is never interrupted — Moxie keeps
    talking. That silence is good for the child and bad for us, so `_CountingSandbox`
    counts every refusal in `BLOCKED`: a pack that trips it is broken or hostile, and
    either way it is worth seeing. A `SecurityError` can still be raised for unsafe
    *operations* rather than attributes, and that falls through to the minimal renderer,
    which only substitutes `{{ dotted.path }}` and can reach nothing."""
    if not template:
        return ""
    try:
        env = _sandbox()
    except ImportError:
        return _minimal_render(template, context)
    try:
        return env.from_string(template).render(**context)
    except Exception:                           # SecurityError, TemplateSyntaxError, …
        return _minimal_render(template, context)
