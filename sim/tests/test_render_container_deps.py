"""The container ships jinja2; the SDK's base dependencies still do not.

`content-module-contract.md`:42 promises a module author that `prompt` is Jinja2-templated
and names the block form (`{% if %}`). For two releases that promise was false in the one
place it mattered: `mqtt/requirements.txt` — the **container's** dependency list, and the
only thing `mqtt/Dockerfile` installs — listed `paho-mqtt` and `openai` and nothing else, so
every shipped appliance ran `render_prompt`'s dependency-free fallback and put literal
template syntax into the brain's system prompt.

Fixing that is a *split*, not a single edit, and both sides of the split are load-bearing in
opposite directions:

* **`requirements.txt` must contain jinja2** — otherwise the documented form silently
  degrades in production again. (Safe to ship only because `render_prompt` builds a
  `SandboxedEnvironment`; see `test_render_sandbox.py`.)
* **`pyproject.toml`'s base `dependencies` must NOT contain jinja2** — the SDK's "imports and
  unit-tests with no heavy dependencies" property is deliberate and is itself tested
  (`test_package_contents.py`). jinja2 stays an optional extra there (`content`).

A future maintainer "tidying up" will reach for one of two obvious moves — promote the extra
into base deps, or drop the container line as a duplicate of the extra — and both are wrong.
So this file pins the split in **both** directions, reading the real files rather than a
copy, because a comment in either file cannot fail a test run.
"""
from __future__ import annotations

import os
import re

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
REQUIREMENTS = os.path.join(REPO, "mqtt", "requirements.txt")
PYPROJECT = os.path.join(REPO, "mqtt", "pyproject.toml")
DOCKERFILE = os.path.join(REPO, "mqtt", "Dockerfile")


def _requirement_lines() -> list[str]:
    """The lines pip would actually act on — comments and blanks are not dependencies.

    The distinction matters here: the `# faster-whisper>=1.0` line is a *commented-out*
    future dep, and a test that grepped the raw text would call it installed.
    """
    with open(REQUIREMENTS) as fh:
        return [ln.strip() for ln in fh
                if ln.strip() and not ln.strip().startswith("#")]


def _requirement_names() -> set[str]:
    """Distribution names, lowercased, with any version specifier/extras stripped."""
    return {re.split(r"[<>=!~\[; ]", ln, 1)[0].strip().lower()
            for ln in _requirement_lines()}


def _pyproject_tables() -> tuple[list[str], dict[str, list[str]]]:
    """`(base dependencies, {extra: [deps]})` from the real pyproject.

    tomllib is 3.11+; `requires-python` is >=3.9, so fall back to a narrow parse of the
    two arrays we care about rather than skipping the assertion on an older interpreter.
    """
    try:
        import tomllib
    except ImportError:                                    # pragma: no cover - py<3.11
        text = open(PYPROJECT).read()

        def _array(after: str) -> list[str]:
            m = re.search(re.escape(after) + r"\s*=\s*\[(.*?)\]", text, re.S)
            return re.findall(r"[\"']([^\"']+)[\"']", m.group(1)) if m else []

        extras = {}
        block = re.search(r"\[project\.optional-dependencies\](.*?)(?=\n\[|\Z)", text, re.S)
        if block:
            for name, body in re.findall(r"(\w+)\s*=\s*\[(.*?)\]", block.group(1), re.S):
                extras[name] = re.findall(r"[\"']([^\"']+)[\"']", body)
        return _array("dependencies"), extras

    with open(PYPROJECT, "rb") as fh:
        data = tomllib.load(fh)
    project = data["project"]
    return project.get("dependencies", []), project.get("optional-dependencies", {})


def _names(specs) -> set[str]:
    return {re.split(r"[<>=!~\[; ]", s, 1)[0].strip().lower() for s in specs}


# ------------------------------------------------- the container side of the split --
def test_the_container_installs_jinja2():
    """Half 1 of the fix, as an assertion. Without this line the documented `{% if %}`
    form degrades in production and nowhere else."""
    assert "jinja2" in _requirement_names(), (
        f"{REQUIREMENTS} must list jinja2 — content-module-contract.md:42 promises "
        "Jinja2 templating and the container is where that promise is kept")


def test_the_container_pins_a_jinja2_floor_with_the_sandbox_in_it():
    """`SandboxedEnvironment` and `ChainableUndefined` are what make an untrusted pack
    `prompt` safe to render, so an unbounded `jinja2` line could resolve to a release
    that predates them. 3.0 is the floor `pyproject.toml`'s `content` extra already uses."""
    line = next(ln for ln in _requirement_lines() if ln.lower().startswith("jinja2"))
    assert re.search(r">=\s*3", line), f"jinja2 needs a >=3 floor, got {line!r}"


def test_the_dockerfile_is_what_installs_it():
    """The requirement line only reaches the image because the Dockerfile installs from
    this file. If a refactor ever switches to `pip install .` or a lockfile, half 1 of the
    fix evaporates without any other test noticing."""
    text = open(DOCKERFILE).read()
    assert re.search(r"pip install[^\n]*-r requirements\.txt", text), \
        "mqtt/Dockerfile must install from requirements.txt"


# ------------------------------------------------------- the SDK side of the split --
def test_the_sdk_base_dependencies_stay_dependency_free():
    """The property `test_package_contents.py` exists to protect: `import moxie_sdk` needs
    nothing heavy. Promoting jinja2 out of the extra would break that quietly — the SDK
    would still work, it would just stop being installable-anywhere."""
    base, _ = _pyproject_tables()
    assert _names(base) == {"paho-mqtt"}, (
        f"{PYPROJECT} base dependencies must stay minimal, got {base!r}")
    assert "jinja2" not in _names(base), \
        "jinja2 belongs in the `content` extra, never in base dependencies"


def test_jinja2_is_still_offered_as_the_content_extra():
    """The other direction: somebody could delete the extra as 'redundant now that the
    container ships it'. A bare-metal SDK user needs a documented way to get the full
    renderer, and `.[content]` is it."""
    _, extras = _pyproject_tables()
    assert "jinja2" in _names(extras.get("content", [])), \
        "pyproject.toml must keep jinja2 in the `content` extra"
    assert "jinja2" in _names(extras.get("all", [])), \
        "pyproject.toml's `all` extra must keep jinja2"


# --------------------------------------------------------- the split, stated once --
@pytest.mark.parametrize("dep", sorted(_requirement_names()))
def test_every_container_requirement_is_a_declared_sdk_extra_or_base_dep(dep):
    """No third list. Everything the container installs must be something the SDK already
    declares somewhere, so `pip install moxie-cloud-sdk[all]` reproduces the container's
    capabilities and the two files cannot drift into disagreeing about what exists."""
    base, extras = _pyproject_tables()
    declared = _names(base) | {n for specs in extras.values() for n in _names(specs)}
    assert dep in declared, (
        f"{dep!r} is installed into the container but declared nowhere in "
        f"{PYPROJECT} — add it to base deps or an extra")
