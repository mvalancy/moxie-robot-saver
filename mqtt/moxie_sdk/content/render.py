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


def render_prompt(template: str, context: dict) -> str:
    """Render `template` over `context` (e.g. {'volley': v, 'session': s}).

    Prefers Jinja2 if available; the minimal fallback handles `{{ dotted.path }}`."""
    if not template:
        return ""
    try:
        import jinja2  # optional
        env = jinja2.Environment(undefined=jinja2.ChainableUndefined,
                                 autoescape=False, keep_trailing_newline=True)
        return env.from_string(template).render(**context)
    except Exception:
        return _minimal_render(template, context)
