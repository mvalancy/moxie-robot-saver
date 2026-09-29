"""Live suites must leave the process environment as they found it — hermetic guards.

A credentialed full run went red (9 failures, 4 errors) while every file passed alone:
`test_live_gateway.py` set `MOXIE_APP=content` / `MOXIE_STT=off` without restoring them, and
the voice picker two files later was judged against a deployment with no ears. Its fix
(`_assembly_env`) lives in a file that skips without a key, so it is guarded here. Not
named `test_live_*`: that prefix means "needs credentials" to conftest and CI.
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
        # …and `config` with it, which this file reloads on purpose
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
    """`mqtt/config.py` reloaded against `env`, as every live suite does."""
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    import config as _c
    return importlib.reload(_c)


# ------------------------------------------------------------------ the helper --
def test_the_assembly_env_sets_its_vars_and_restores_both_directions_even_on_error(env_sandbox):
    """Put back a var that was set, REMOVE one that was not (the bug's direction), and do
    both when the body raises — a failed live turn must not redden a second suite."""
    os.environ["MOXIE_APP"] = "sentinel-app"
    os.environ.pop("MOXIE_STT", None)
    with pytest.raises(RuntimeError):
        with _assembly_env():
            assert {k: os.environ.get(k) for k in _ASSEMBLY_ENV} == dict(_ASSEMBLY_ENV)
            raise RuntimeError("the live turn failed")
    assert os.environ["MOXIE_APP"] == "sentinel-app"
    assert "MOXIE_STT" not in os.environ


def test_the_environment_is_restored_before_config_is_left_alone(env_sandbox):
    """The body reloads `config`, so restoring the environment is not enough — its
    constants would still hold ours. The helper reloads it on the way out."""
    _config(MOXIE_APP="echo", MOXIE_STT=None)
    with _assembly_env():
        importlib.reload(sys.modules["config"])
        assert sys.modules["config"].MOXIE_APP == "content"
    assert sys.modules["config"].MOXIE_APP == "echo"
    assert sys.modules["config"].BRAIN_ENV == "echo"


# ------------------------------------- why a leak of THESE two variables is a bug --
def _listing(cfg):
    """`VoiceEngines.available()` over a FAKE gateway listing (no network): what is under
    test is the environment's effect on the answer."""
    from moxie_sdk import voice_settings as vs
    cat = vs.GatewayCatalog(lambda: ["piper-amy", "piper-ryan", "stt-whisper"],
                            submit=lambda fn: fn())
    return cfg.voice_engines(cat).available()


def test_a_leaked_MOXIE_STT_off_is_what_broke_the_voice_picker(env_sandbox):
    """The picker's three failing assertions, reproduced as facts about `config`."""
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
    """`MOXIE_APP` is also a brain pin, so a leak costs the next suite its brain."""
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
# `test_assemble.py` (hermetic, runs early) used to DELETE the LLM/voice endpoint + key and
# set `MOXIE_SKIP_DOTENV=1`, so `test_live_gateway_turn_e2e.py`'s supervisor inherited
# neither and its 4 tests errored. Its fence is a module-scoped autouse fixture; this is
# the fence's fence: run the file in a SUBPROCESS with sentinels and read the environment
# back. `pytest.main` in-process rather than a probe file outside the repo, which would move
# the rootdir to `/` and make collection ~60x slower.
_PROBE = """
import json, os, sys
import pytest
rc = pytest.main(["-q", "-p", "no:cacheprovider", sys.argv[1]])
with open(sys.argv[2], "w") as fh:
    json.dump({k: v for k, v in os.environ.items() if k.startswith("MOXIE_")}, fh)
sys.exit(int(rc))
"""

#: Set by `conftest` for every session (`isolated_data_dir` and the dotenv fence, which
#: the probe re-applies because this test unsets it) — not leaks by the file under test.
_PROBE_IGNORED = ("MOXIE_DATA_DIR", "MOXIE_SKIP_DOTENV")


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
