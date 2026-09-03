"""
Packaging guards: what `pip install moxie-cloud-sdk` actually gets.

Two failures are invisible to every other test in this repo, because every other test
runs against the *source tree*:

  1. **A new subpackage ships empty (or not at all).** `[tool.setuptools] packages` is a
     hand-written list. Add `moxie_sdk/telehealth/` and forget the line and the module is
     simply absent from the wheel — `import` works all day in the repo and fails on an
     appliance.
  2. **A new data file does not ship.** `[tool.setuptools.package-data]` maps
     `moxie_sdk = ["*.json"]` — one package, one glob. `safety_rules.json` is loaded at
     runtime by the safety classifier; a sibling `moxie_sdk/apps/*.json` or a JSON in a
     subpackage matches nothing and is dropped silently.

The third guard is the SDK's own promise (`pyproject.toml`: "the SDK imports … with no
heavy dependencies"), which is also playbook rule 9's rule: the fast CI tier runs the
whole suite with none of the optional backends installed, so a module that imports
`openai`/`numpy`/`jinja2`/`piper`/`faster_whisper` at module scope is a red push, not a
caught bug. Here it is checked directly — in a subprocess where those imports are made
to fail even when they *are* installed, so the full-fat venv catches it too.

Verified against the real artifact on 2026-09-02 (v0.7.0 RC): `python -m build` →
`moxie_cloud_sdk-0.7.0.{tar.gz,whl}`, the wheel carrying `moxie_sdk/safety_rules.json`,
installed into a bare venv where `import moxie_sdk` and `from moxie_sdk.schedule import
plan_day` both work with only `paho-mqtt` present.
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


# --------------------------------------------------------------------------- #
# 1. every package on disk is declared, and every declared package exists
# --------------------------------------------------------------------------- #
def test_every_package_on_disk_is_declared_in_pyproject():
    declared = set(_pyproject()["tool"]["setuptools"]["packages"])
    missing = _packages_on_disk() - declared
    assert not missing, (
        f"{sorted(missing)} exist(s) under mqtt/moxie_sdk/ but is not in "
        f"[tool.setuptools] packages — it would not ship in the wheel")


def test_every_declared_package_exists_on_disk():
    declared = set(_pyproject()["tool"]["setuptools"]["packages"])
    stale = declared - _packages_on_disk()
    assert not stale, f"[tool.setuptools] packages names {sorted(stale)}, which is gone"


# --------------------------------------------------------------------------- #
# 2. every data file inside a package is covered by a package-data glob
# --------------------------------------------------------------------------- #
def test_every_data_file_would_actually_ship():
    package_data = _pyproject()["tool"]["setuptools"].get("package-data", {})
    uncovered = []
    for pkg, name in _data_files():
        globs = package_data.get(pkg, [])
        if not any(fnmatch.fnmatch(name, g) for g in globs):
            uncovered.append(f"{pkg}/{name}")
    assert not uncovered, (
        f"{uncovered} sit(s) inside a package but matches no "
        f"[tool.setuptools.package-data] glob — pip would drop it silently "
        f"(current map: {package_data})")


def test_the_safety_rule_table_is_declared_data():
    """The one data file the runtime cannot start without, named explicitly so a
    refactor that widens the globs still has to keep this one."""
    package_data = _pyproject()["tool"]["setuptools"]["package-data"]
    assert any(fnmatch.fnmatch("safety_rules.json", g)
               for g in package_data.get("moxie_sdk", [])), package_data
    assert os.path.isfile(os.path.join(SDK, "safety_rules.json"))


# --------------------------------------------------------------------------- #
# 3. the version is one value, and it is the one the build reads
# --------------------------------------------------------------------------- #
def test_the_version_comes_from_the_package_itself():
    data = _pyproject()
    assert "version" in data["project"]["dynamic"], data["project"]
    assert data["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "moxie_sdk.__version__"}
    sys.path.insert(0, MQTT)
    import moxie_sdk
    parts = moxie_sdk.__version__.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts), moxie_sdk.__version__


# --------------------------------------------------------------------------- #
# 4. the SDK imports with none of the optional backends
# --------------------------------------------------------------------------- #
def test_no_module_needs_an_optional_dependency_to_import():
    """Import every `moxie_sdk` module in a subprocess where the optional backends are
    made unimportable. This is the fast-tier contract (playbook rule 9) asserted here
    instead of discovered on a red push."""
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
