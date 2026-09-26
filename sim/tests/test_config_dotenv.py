"""
"Nothing is configured" has to be able to MEAN nothing.

`mqtt/config.py` loads `mqtt/.env` with `setdefault` at import — right for an appliance,
wrong for a test suite: a test that deletes a variable and reloads the module had it
**refilled from the file**, so on any machine with a real `mqtt/.env` those tests asserted
that developer's configuration. Invisible where the suite normally runs (the file is
git-ignored: no CI runner or worktree has it). Playbook rule 20.

The opt-out, `MOXIE_SKIP_DOTENV`, is checked before the file is opened, and `MOXIE_DOTENV`
points the loader at another file — which lets this file test the loader against a real
dotenv without touching a developer's own. Both are read from the ENVIRONMENT only (a file
cannot carry the flag that decides whether it is read), and an env flag is the only
opt-out `importlib.reload` can reach: module-level `_load_env()` takes no arguments.
"""
import importlib
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MQTT = os.path.join(REPO, "mqtt")
sys.path.insert(0, MQTT)
sys.path.insert(0, os.path.join(MQTT, "supervisor"))

#: The repo's own git-ignored dotenv. Its EXISTENCE is all this file ever looks at.
REPO_DOTENV = os.path.join(MQTT, ".env")

#: A variable with no default and no other source, so its value can only have come from
#: whichever file the loader read.
PROBE = "MOXIE_WEBHOOK_ENDPOINT"
FROM_FILE = "http://127.0.0.1:9/from-the-dotenv"


def _fresh(monkeypatch, *, dotenv=None, skip=None):
    monkeypatch.delenv(PROBE, raising=False)
    monkeypatch.delenv("MOXIE_DOTENV", raising=False)
    monkeypatch.delenv("MOXIE_SKIP_DOTENV", raising=False)
    if dotenv is not None:
        monkeypatch.setenv("MOXIE_DOTENV", dotenv)
    if skip is not None:
        monkeypatch.setenv("MOXIE_SKIP_DOTENV", skip)
    import config as _c
    return importlib.reload(_c)


def test_a_dotenv_the_loader_can_see_is_still_suppressible(monkeypatch, tmp_path):
    """Both halves in ONE test: "with the flag, the variable is unset" alone passes
    against a loader that never read the file (a worktree's state), so the control half
    must fail with it or not at all."""
    f = tmp_path / "dotenv"
    f.write_text(f"# a comment, and a blank line follow\n\n{PROBE}={FROM_FILE}\n")

    # 1. the file really is read when nothing opts out …
    c = _fresh(monkeypatch, dotenv=str(f))
    assert c.WEBHOOK_ENDPOINT == FROM_FILE, "the loader ignored the file it was given"
    assert c.DOTENV_LOADED == str(f)

    # 2. … and MOXIE_SKIP_DOTENV makes that same file invisible.
    c = _fresh(monkeypatch, dotenv=str(f), skip="1")
    assert c.WEBHOOK_ENDPOINT == "", "a dotenv refilled a deliberately unset variable"
    assert c.DOTENV_LOADED is None


def test_an_explicit_variable_still_beats_the_file(monkeypatch, tmp_path):
    """The appliance behaviour that must not change: `setdefault`, not overwrite. A
    `docker run -e` or a shell export outranks whatever is in the file."""
    f = tmp_path / "dotenv"
    f.write_text(f"{PROBE}={FROM_FILE}\n")
    monkeypatch.setenv(PROBE, "http://127.0.0.1:9/from-the-environment")
    monkeypatch.setenv("MOXIE_DOTENV", str(f))
    monkeypatch.delenv("MOXIE_SKIP_DOTENV", raising=False)
    import config as _c
    c = importlib.reload(_c)
    assert c.WEBHOOK_ENDPOINT == "http://127.0.0.1:9/from-the-environment"


def test_the_flag_beats_even_an_explicitly_passed_path(monkeypatch, tmp_path):
    """"This process must see no file at all" has to be unconditional, or a helper that
    passes a path would quietly re-open the hole."""
    f = tmp_path / "dotenv"
    f.write_text(f"{PROBE}={FROM_FILE}\n")
    monkeypatch.delenv(PROBE, raising=False)
    monkeypatch.setenv("MOXIE_SKIP_DOTENV", "1")
    import config as _c
    assert _c._load_env(str(f)) is None
    assert PROBE not in os.environ


def test_a_falsy_flag_does_not_switch_the_loader_off(monkeypatch, tmp_path):
    """`MOXIE_SKIP_DOTENV=0` must not read as "skip" — the repo's switches all spell
    false the same way (`""`/`0`/`off`/`false`/`no`), and a flag that fired on any value
    would disable configuration for anyone who wrote the obvious thing."""
    f = tmp_path / "dotenv"
    f.write_text(f"{PROBE}={FROM_FILE}\n")
    for falsy in ("0", "off", "false", "no", ""):
        c = _fresh(monkeypatch, dotenv=str(f), skip=falsy)
        assert c.WEBHOOK_ENDPOINT == FROM_FILE, f"{falsy!r} was read as 'skip'"


def test_a_missing_file_is_not_an_error(monkeypatch, tmp_path):
    """A bare-metal supervisor with no dotenv at all is a supported deployment."""
    c = _fresh(monkeypatch, dotenv=str(tmp_path / "nope"))
    assert c.DOTENV_LOADED is None


# ---------------------------------------------------------------------------------
# The acceptance test. The ONLY thing here that touches the repo's own `mqtt/.env`: it
# asks whether the file exists and never opens it; elsewhere it skips, visibly.
# ---------------------------------------------------------------------------------

def test_the_repos_own_dotenv_is_invisible_to_a_test_that_opts_out(monkeypatch):
    import pytest
    if not os.path.exists(REPO_DOTENV):
        pytest.skip("no mqtt/.env in this checkout (a worktree or CI) — "
                    "the defect this pins is only visible in a main checkout")
    monkeypatch.delenv("MOXIE_DOTENV", raising=False)
    monkeypatch.setenv("MOXIE_SKIP_DOTENV", "1")
    import config as _c
    assert importlib.reload(_c).DOTENV_LOADED is None
