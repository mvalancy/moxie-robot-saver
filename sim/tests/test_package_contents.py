"""Packaging guards: what `pip install moxie-cloud-sdk` actually gets.

Every other test runs from the source tree, so it cannot see a subpackage missing from the
hand-written `[tool.setuptools] packages`, a data file no `package-data` glob covers, or a
module-scope import of an optional backend (a red fast tier, which runs without them).
"""
import fnmatch
import os
import subprocess
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MQTT = os.path.join(REPO, "mqtt")
SDK = os.path.join(MQTT, "moxie_sdk")
PYPROJECT = os.path.join(MQTT, "pyproject.toml")

tomllib = pytest.importorskip("tomllib", reason="python < 3.11 has no tomllib")

#: Everything in `[project.optional-dependencies]`, by import name. A module that needs
#: one of these must import it inside a function (the `client=`-style seam), never at
#: module scope.
OPTIONAL_IMPORTS = ("openai", "faster_whisper", "numpy", "piper", "jinja2")

#: Files that legitimately live beside the code and are NOT package data.
NOT_DATA = ("*.py", "*.pyc", "README.md", "*.md")


def _pyproject() -> dict:
    with open(PYPROJECT, "rb") as fh:
        return tomllib.load(fh)


def _packages_on_disk() -> set:
    """Every importable package under `moxie_sdk/`, dotted."""
    found = set()
    for root, dirs, files in os.walk(SDK):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        if "__init__.py" in files:
            rel = os.path.relpath(root, MQTT)
            found.add(rel.replace(os.sep, "."))
    return found


def _modules_on_disk() -> list:
    """Every importable module under `moxie_sdk/`, dotted, sorted."""
    out = []
    for root, dirs, files in os.walk(SDK):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        rel = os.path.relpath(root, MQTT).replace(os.sep, ".")
        for f in sorted(files):
            if f.endswith(".py"):
                out.append(rel if f == "__init__.py" else f"{rel}.{f[:-3]}")
    return sorted(set(out))


def _data_files() -> list:
    """`(package, filename)` for every non-code file that sits inside a package."""
    out = []
    for root, dirs, files in os.walk(SDK):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        pkg = os.path.relpath(root, MQTT).replace(os.sep, ".")
        for f in sorted(files):
            if not any(fnmatch.fnmatch(f, pat) for pat in NOT_DATA):
                out.append((pkg, f))
    return out


def test_the_declared_packages_are_exactly_the_packages_on_disk():
    declared = set(_pyproject()["tool"]["setuptools"]["packages"])
    assert declared == _packages_on_disk(), (
        f"not shipped: {sorted(_packages_on_disk() - declared)}; "
        f"stale: {sorted(declared - _packages_on_disk())}")


def test_every_data_file_would_actually_ship():
    package_data = _pyproject()["tool"]["setuptools"].get("package-data", {})
    uncovered = [f"{pkg}/{name}" for pkg, name in _data_files()
                 if not any(fnmatch.fnmatch(name, g) for g in package_data.get(pkg, []))]
    assert not uncovered, f"no package-data glob covers {uncovered} — pip drops it silently"


def test_the_version_comes_from_the_package_itself():
    data = _pyproject()
    assert "version" in data["project"]["dynamic"], data["project"]
    assert data["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "moxie_sdk.__version__"}
    sys.path.insert(0, MQTT)
    import moxie_sdk
    parts = moxie_sdk.__version__.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts), moxie_sdk.__version__


def test_no_module_needs_an_optional_dependency_to_import():
    """Every `moxie_sdk` module imports in a subprocess where the optional backends are
    made unimportable, even when installed."""
    modules = _modules_on_disk()
    assert len(modules) > 10, modules             # the walk actually found the package
    script = (
        "import sys\n"
        f"BLOCKED = {OPTIONAL_IMPORTS!r}\n"
        "class Block:\n"
        "    def find_module(self, name, path=None):\n"
        "        return self.find_spec(name, path) and self\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in BLOCKED:\n"
        "            raise ImportError('blocked optional dependency: ' + name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, Block())\n"
        "import importlib\n"
        f"for m in {modules!r}:\n"
        "    importlib.import_module(m)\n"
        "print('ok', len(sys.argv))\n")
    env = dict(os.environ, PYTHONPATH=MQTT)
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                         env=env, cwd=REPO)
    assert out.returncode == 0, (
        f"a moxie_sdk module needs an optional backend at import time:\n{out.stderr}")
