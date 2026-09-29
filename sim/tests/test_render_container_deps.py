"""The container ships jinja2; the SDK's base dependencies still do not.

`mqtt/requirements.txt` (the only thing `mqtt/Dockerfile` installs) once lacked jinja2, so
every appliance ran the fallback renderer and fed literal `{% if %}` syntax to the brain.
Both obvious "tidy-ups" are wrong — promote the `content` extra to base (breaks the SDK's
no-heavy-deps promise) or drop the container line as a duplicate — so the split is pinned
in both directions against the real files.
"""
from __future__ import annotations

import os
import re

import pytest

tomllib = pytest.importorskip("tomllib", reason="python < 3.11 has no tomllib")

MQTT = os.path.join(os.path.dirname(__file__), "..", "..", "mqtt")


def _names(specs) -> set[str]:
    return {re.split(r"[<>=!~\[; ]", s, 1)[0].strip().lower() for s in specs}


def _requirement_lines() -> list[str]:
    """Lines pip acts on — a commented-out `# faster-whisper` is not installed."""
    with open(os.path.join(MQTT, "requirements.txt")) as fh:
        return [ln.strip() for ln in fh if ln.strip() and not ln.strip().startswith("#")]


def _project() -> dict:
    with open(os.path.join(MQTT, "pyproject.toml"), "rb") as fh:
        return tomllib.load(fh)["project"]


def test_the_container_installs_jinja2_with_the_sandbox_floor():
    """`SandboxedEnvironment` is what makes an untrusted pack prompt safe to render."""
    line = next((ln for ln in _requirement_lines() if ln.lower().startswith("jinja2")), "")
    assert re.search(r">=\s*3", line), f"requirements.txt needs jinja2>=3, got {line!r}"
    dockerfile = open(os.path.join(MQTT, "Dockerfile")).read()
    assert re.search(r"pip install[^\n]*-r requirements\.txt", dockerfile)


def test_the_sdk_keeps_jinja2_an_extra_and_its_base_minimal():
    project = _project()
    assert _names(project.get("dependencies", [])) == {"paho-mqtt"}
    extras = project.get("optional-dependencies", {})
    assert "jinja2" in _names(extras.get("content", [])) & _names(extras.get("all", []))


def test_every_container_requirement_is_a_declared_sdk_dependency():
    """No third list: `pip install moxie-cloud-sdk[all]` reproduces the container."""
    project = _project()
    declared = _names(project.get("dependencies", [])) | {
        n for specs in project.get("optional-dependencies", {}).values() for n in _names(specs)}
    assert not _names(_requirement_lines()) - declared
