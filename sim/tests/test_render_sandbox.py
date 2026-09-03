"""A content pack's `prompt` is untrusted input, so its renderer must be a sandbox.

`moxie_sdk/content/packs.py` (PR #51) makes content **shareable**: a parent can import a
pack somebody else exported. A pack carries `prompt` and `opener` strings, and
`moxie_sdk/content/render.py` renders them. Under a plain `jinja2.Environment` that is
server-side code execution, and it was: on jinja2 3.1.2 in a dev checkout,
`{{ cycler.__init__.__globals__['os'].name }}` returned the host platform and
`''.__class__.__mro__[1].__subclasses__()` enumerated 364 host classes. The shipped
container happened to be safe only because it has no jinja2 at all
(`mqtt/requirements.txt`; `pyproject.toml`:25 gates it behind the `content` extra), so the
hole was live in every dev checkout and every `.[content]` / `.[all]` install.
**That accident is over:** `mqtt/requirements.txt` now lists `jinja2>=3.0` on purpose, because
`content-module-contract.md`:42 advertises `{% if %}` and the fallback cannot render it.
Shipping jinja2 into the container is only safe *because* of this
sandbox, so these probes now fence the renderer every real deployment runs.

Found by the research pass that specced `backlog/sandboxed-extensions.md` — which is the
irony worth recording: the brief's whole subject is "we never execute untrusted code", and
the audit's ⚠️ pointed at a pack's `code` field, which never runs, while saying nothing
about `prompt`, which does.

These tests are the regression fence. Each probe is a real escape technique, and each must
come back inert while ordinary templating keeps working — a sandbox that breaks
`Hi {{ nickname }}` would just get reverted.
"""
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.content import render as R  # noqa: E402

jinja2 = pytest.importorskip("jinja2", reason="the sandbox only exists when jinja2 does")

#: Substrings that prove a template reached off its own leash. Each is something no
#: legitimate child-facing prompt would ever contain.
LEAKS = ("posix", "nt", "/home/", "C:\\", "<class ", "Environment", "subprocess", "builtins")

ESCAPES = {
    "mro_subclasses": "{{ ''.__class__.__mro__[1].__subclasses__() | length }}",
    "cycler_globals_getcwd": "{{ cycler.__init__.__globals__.os.getcwd() }}",
    "cycler_globals_os_name": "{{ cycler.__init__.__globals__['os'].name }}",
    "template_reference_env": "{{ self._TemplateReference__context.environment.__class__ }}",
    "joiner_globals": "{{ joiner.__init__.__globals__ }}",
    "namespace_builtins": "{{ namespace.__init__.__globals__.__builtins__ }}",
    "attr_filter_walk": "{{ ''|attr('__class__')|attr('__mro__') }}",
    "config_items": "{{ config.items() }}",
}


@pytest.mark.parametrize("name", sorted(ESCAPES))
def test_the_escape_comes_back_inert(name):
    """No probe may return anything that names the host."""
    out = R.render_prompt(ESCAPES[name], {"nickname": "Sam"})
    assert isinstance(out, str)
    low = out.lower()
    for leak in LEAKS:
        assert leak.lower() not in low, f"{name} leaked {leak!r}: {out[:200]!r}"
    # A subclass list is enormous; an inert result is short.
    assert len(out) < 400, f"{name} returned {len(out)} chars: {out[:200]!r}"


def test_the_renderer_uses_the_sandboxed_environment():
    """Pin the mechanism, not just the symptom — a future refactor back to
    `jinja2.Environment` must fail here even if every probe above happens to be inert."""
    src = open(os.path.join(REPO, "mqtt", "moxie_sdk", "content", "render.py")).read()
    code = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith(("#", '"', "'", "*")))
    assert "SandboxedEnvironment" in code, "render.py must build a SandboxedEnvironment"
    assert "jinja2.Environment(" not in code, "render.py must not build a plain Environment"


def test_a_refused_template_is_counted_not_swallowed():
    """A hostile pack should be visible. `BLOCKED` rising is the only signal that
    separates 'somebody tried' from 'somebody typo'd'."""
    before = R.BLOCKED
    R.render_prompt("{{ cycler.__init__.__globals__['os'].name }}", {})
    assert R.BLOCKED > before


def test_a_refused_template_does_not_take_the_turn_down():
    """Moxie keeps talking. A refusal degrades to the minimal renderer, never raises."""
    out = R.render_prompt("Hi {{ nickname }} {{ ''.__class__ }}", {"nickname": "Sam"})
    assert isinstance(out, str) and "Sam" in out


def test_ordinary_templating_still_works():
    assert R.render_prompt("Hi {{ nickname }}, ready?", {"nickname": "Sam"}) == "Hi Sam, ready?"
    assert R.render_prompt("", {"nickname": "Sam"}) == ""
    assert R.render_prompt("no placeholders", {}) == "no placeholders"


def test_dotted_attribute_paths_still_resolve():
    """The feature the renderer exists for: `{{ session.turns }}` over real objects."""
    class Session:
        turns = 3
    assert R.render_prompt("{{ session.turns }}", {"session": Session()}) == "3"


def test_a_missing_name_is_empty_not_an_error():
    """`ChainableUndefined` is why a half-filled context does not break a turn."""
    assert R.render_prompt("Hi {{ nobody.here.at.all }}!", {}) == "Hi !"
