"""A content pack's `prompt` is untrusted input, so its renderer must be a sandbox.

Packs are shareable, and `prompt`/`opener` are rendered by `moxie_sdk/content/render.py`.
Under a plain `jinja2.Environment` that is server-side code execution
(`{{ cycler.__init__.__globals__['os'].name }}`, `''.__class__.__mro__[1].__subclasses__()`).
The container ships jinja2 on purpose (`content-module-contract.md`:42 advertises
`{% if %}`), which is safe only because of this sandbox.

Each probe is a real escape technique and must come back inert, while ordinary templating
(`Hi {{ nickname }}`) keeps working.
"""
import pytest

from moxie_sdk.content import render as R

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
    from jinja2.sandbox import SandboxedEnvironment
    assert isinstance(R._sandbox(), SandboxedEnvironment)


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
