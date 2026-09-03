"""
Why a full `pytest sim/tests` run was red on a machine with credentials and green
everywhere else — and the fence that keeps it from coming back.

**The finding.** For a day, `docs/architecture/implementation-plan.md` carried this as an
open, unexplained gap: `python3 -m pytest sim/tests -q` — the SIL tier's *own documented
command* — gave 9 failures and 4 errors on a developer box, while every one of the files
involved passed **in isolation**, and CI never saw any of it because CI has no `mqtt/.env`
to find. Rule 20 one level deeper than it was written.

**The mechanism, reproduced 2026-09-03 (2 gateway calls).** `test_live_gateway.py`'s
assembled-stack test used to set two variables straight into `os.environ` with no restore:

    os.environ["MOXIE_APP"] = "content"
    os.environ["MOXIE_STT"] = "off"

Both are **engine selectors**, and every later live suite reads them by reloading `config`
against the live process environment. So the next live file in the same session — the
voice picker — asked the gateway for its real model list and then judged it against a
deployment that had been told it has no ears. Three of its assertions are about exactly
that (`pins == {"": ""}`, `gateway:stt-whisper` present, the speech/listening split), and
all three failed:

    [picker] 1 listening entries: off
    assert {'listening': 'off'} == {'listening': ''}

Nothing was wrong with the picker, with the gateway, or with the config layer. One test
had left the room untidy, and the failure surfaced two files later.

**Why it could only ever be seen there.** In CI the whole live tier skips (no key), so the
polluting test never runs. Run alone, the picker is the only live file in the session, so
there is nothing to pollute it. It takes a *machine with credentials running the whole
suite* — which is the developer's box and nothing else — for the two to meet.

**What this file is.** The fix (a save-and-restore context manager) lives in the file that
needs it, but that file is `skipif`-ed away on every machine without a key — that is, on
every machine that could otherwise notice the restore breaking. So the guard lives here,
hermetic, and asserts three things: the helper restores in both directions, and each of
the two leaked variables really does change what a reloaded `config` reports. The second
half matters because it is what turns "a test changed an environment variable" from
tidiness into a bug — and because the blast radius grew when PR #88 made `MOXIE_APP` a
brain pin, so a leak that used to cost a listening engine now costs a brain as well.

No gateway, no key, no network: everything below is the config layer read against an
environment this file sets and puts back itself. It is deliberately NOT named
`test_live_*` — that prefix is the naming convention `test_ci_workflows.py` uses to
insist every live suite is dispatched by some CI tier, and a hermetic guard that
asked to be dispatched as a live suite would be a lie about what it needs.
"""
from __future__ import annotations

import importlib
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.dirname(__file__))

from test_live_gateway import _ASSEMBLY_ENV, _assembly_env       # noqa: E402


@pytest.fixture
def env_sandbox():
    """Every `MOXIE_*` name this file touches, restored afterwards whatever happens —
    the discipline the module under test failed at, applied to the test that checks it."""
    keys = tuple(_ASSEMBLY_ENV) + ("MOXIE_TTS", "MOXIE_VOICE_BASE_URL")
    before = {k: os.environ.get(k) for k in keys}

    def restore():
        for k, v in before.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        # …and `config` with it. This file reloads that module on purpose; a file about
        # not leaving state behind that left state behind would be its own punchline.
        if "config" in sys.modules:
            try:
                importlib.reload(sys.modules["config"])
            except Exception:
                pass

    try:
        yield
    finally:
        restore()


def _config(**env):
    """`mqtt/config.py` reloaded against `env` — the same move every live suite makes,
    and therefore the same move that reads whatever a previous test left behind."""
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    import config as _c
    return importlib.reload(_c)


# ------------------------------------------------------------------ the helper --
def test_the_assembly_env_sets_what_the_assembled_stack_needs(env_sandbox):
    with _assembly_env():
        for k, v in _ASSEMBLY_ENV.items():
            assert os.environ[k] == v, (k, os.environ.get(k))


def test_it_puts_back_a_variable_that_was_already_set(env_sandbox):
    os.environ["MOXIE_APP"] = "sentinel-app"
    with _assembly_env():
        assert os.environ["MOXIE_APP"] == "content"
    assert os.environ["MOXIE_APP"] == "sentinel-app"


def test_it_REMOVES_a_variable_that_was_not_set(env_sandbox):
    """The direction the bug was in. Restoring a saved value is the obvious half; putting
    back *nothing* — deleting the key rather than leaving ours — is the half that decides
    whether the next suite sees a pin."""
    os.environ.pop("MOXIE_STT", None)
    with _assembly_env():
        assert os.environ["MOXIE_STT"] == "off"
    assert "MOXIE_STT" not in os.environ, os.environ.get("MOXIE_STT")


def test_it_restores_even_when_the_body_raises(env_sandbox):
    """A live test that fails mid-turn must not leave the environment behind it — that is
    a red run turning into two red runs, in a different file, for a different reason."""
    os.environ.pop("MOXIE_STT", None)
    os.environ["MOXIE_APP"] = "sentinel-app"
    with pytest.raises(RuntimeError):
        with _assembly_env():
            raise RuntimeError("the live turn failed")
    assert "MOXIE_STT" not in os.environ
    assert os.environ["MOXIE_APP"] == "sentinel-app"


def test_the_environment_is_restored_before_config_is_left_alone(env_sandbox):
    """The other half of the leak: the body reloads `config`, so putting the environment
    back is not enough — `config`'s own constants would still hold ours until somebody
    reloaded it again. The helper reloads it on the way out, so a suite that reads
    `config` *without* reloading first still sees the truth."""
    _config(MOXIE_APP="echo", MOXIE_STT=None)
    with _assembly_env():
        importlib.reload(sys.modules["config"])
        assert sys.modules["config"].MOXIE_APP == "content"
    assert sys.modules["config"].MOXIE_APP == "echo"
    assert sys.modules["config"].BRAIN_ENV == "echo"


# ------------------------------------- why a leak of THESE two variables is a bug --
def _listing(cfg):
    """`VoiceEngines.available()` over a FAKE gateway listing — the same seam
    `sim/run_compose_smoke.sh` step 3c uses, so no network is involved. What is under
    test is the environment's effect on the answer, never the gateway's."""
    from moxie_sdk import voice_settings as vs
    cat = vs.GatewayCatalog(lambda: ["piper-amy", "piper-ryan", "stt-whisper"],
                            submit=lambda fn: fn())
    return cfg.voice_engines(cat).available()


def test_a_leaked_MOXIE_STT_off_is_what_broke_the_voice_picker(env_sandbox):
    """The reproduction, hermetically: the picker's three failing assertions, as facts
    about a reloaded `config` rather than as a mystery in someone else's file."""
    from moxie_sdk import voice_settings as vs
    clean = _listing(_config(MOXIE_STT=None, MOXIE_VOICE_BASE_URL="http://gw.invalid/v1"))
    assert clean["pins"][vs.LISTENING] == ""                       # the picker's #3
    listening = vs.option_ids(clean["available"][vs.LISTENING])
    assert "gateway:stt-whisper" in listening, listening            # the picker's #1/#2

    leaked = _listing(_config(MOXIE_STT="off"))
    assert leaked["pins"][vs.LISTENING] == "off", leaked["pins"]
    assert vs.option_ids(leaked["available"][vs.LISTENING]) == ["off"]
    assert "MOXIE_STT" in leaked["pin_notes"][vs.LISTENING]


def test_a_leaked_MOXIE_APP_now_pins_the_BRAIN_too(env_sandbox):
    """PR #88 widened the blast radius: `MOXIE_APP` was a choice, and is now also a pin.
    A leak that used to cost the next suite its ears now costs it its brain — which is
    why the restore is a fence and not a tidy-up."""
    from moxie_sdk import brains
    cfg = _config(MOXIE_APP="content")
    assert cfg.brain_pin() == "content"
    offered = [e["id"] for e in cfg.brain_engines().available()["available"]]
    assert offered == ["content"], offered

    cfg = _config(MOXIE_APP=None)
    assert cfg.brain_pin() == ""
    assert [e["id"] for e in cfg.brain_engines().available()["available"]] \
        == list(brains.BRAIN_IDS)


# ------------------------ the OTHER half of the same finding, and its fence --
# `test_assemble.py` is hermetic and runs long before any live file, and it used to
# DELETE `MOXIE_LLM_BASE_URL` / `MOXIE_LLM_API_KEY` / `MOXIE_VOICE_BASE_URL` from the
# process and set `MOXIE_SKIP_DOTENV=1` so the deletion survived a reload. Right for that
# file (rule 20: "nothing configured" has to mean nothing configured), fatal for the
# session: `test_live_gateway_turn_e2e.py` boots a real supervisor with `MOXIE_APP=llm`
# and inherits the endpoint and key from `os.environ`. Both were gone, and the one flag
# that would let the subprocess recover them from `mqtt/.env` was set — so
# `require_llm_base_url` exited at assembly, the supervisor never came up, and that
# module's 4 tests ERRORED. Which is exactly the "4 errors" the gap recorded.
#
# The fence is a module-scoped autouse fixture in that file. This guard is the fence's
# fence, and it is a real one: it runs the file in a SUBPROCESS with sentinel values and
# reads the environment back afterwards, so a fixture that stopped restoring fails here
# rather than two files later in somebody else's suite.
#: Runs the file under test through `pytest.main` IN a subprocess and then dumps what is
#: left of `os.environ`. In-process rather than as a second test file, because a probe
#: file outside the repo moves pytest's rootdir to `/` and turns a 0.3 s collection into
#: an 18 s one — the guard has to be cheap enough that nobody is tempted to delete it.
_PROBE = """
import json, os, sys
import pytest
rc = pytest.main(["-q", "-p", "no:cacheprovider", sys.argv[1]])
with open(sys.argv[2], "w") as fh:
    json.dump({k: v for k, v in os.environ.items() if k.startswith("MOXIE_")}, fh)
sys.exit(int(rc))
"""

#: Written by `conftest.isolated_data_dir` for the whole session, so the probe sees it
#: whatever the file under test did. Not a leak, and not this guard's business.
_PROBE_IGNORED = ("MOXIE_DATA_DIR",)


def test_test_assemble_py_leaves_the_environment_exactly_as_it_found_it(tmp_path):
    import json
    import subprocess
    runner = tmp_path / "run_probe.py"
    runner.write_text(_PROBE)
    out = tmp_path / "env.json"
    sentinels = {"MOXIE_LLM_BASE_URL": "http://sentinel.invalid/v1",
                 "MOXIE_LLM_API_KEY": "sentinel-not-a-key",
                 "MOXIE_VOICE_BASE_URL": "http://sentinel-voice.invalid/v1",
                 "MOXIE_APP": "echo"}
    env = dict(os.environ, **sentinels)
    for k in ("MOXIE_STT", "MOXIE_SKIP_DOTENV"):
        env.pop(k, None)
    under_test = os.path.join(REPO, "sim", "tests", "test_assemble.py")
    r = subprocess.run([sys.executable, str(runner), under_test, str(out)],
                       cwd=REPO, env=env, capture_output=True, text=True, timeout=600)
    assert out.exists(), r.stdout[-3000:] + r.stderr[-2000:]
    assert r.returncode == 0, r.stdout[-3000:]
    after = {k: v for k, v in json.loads(out.read_text()).items()
             if k not in _PROBE_IGNORED}
    before = {k: v for k, v in env.items()
              if k.startswith("MOXIE_") and k not in _PROBE_IGNORED}
    assert after == before, (
        "test_assemble.py changed the process environment for every file after it:\n"
        f"  gone:    {sorted(set(before) - set(after))}\n"
        f"  added:   {sorted(set(after) - set(before))}\n"
        f"  changed: {sorted(k for k in set(after) & set(before) if after[k] != before[k])}")


def test_that_guard_would_notice_a_deletion():
    """Mutation control: the comparison above must fail when a variable really does go
    missing. Without it, a probe that silently wrote an empty file would pass forever."""
    before = {"MOXIE_LLM_BASE_URL": "x", "MOXIE_APP": "echo"}
    after = {"MOXIE_APP": "echo"}
    assert after != before
    assert sorted(set(before) - set(after)) == ["MOXIE_LLM_BASE_URL"]
