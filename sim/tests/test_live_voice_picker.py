"""
Live 🎚️ picker discovery — the dropdowns filled from the REAL gateway listing.

`test_voice_settings.py` proves every rule here against a fake `models.list()`, and each
of those tests would still pass if the gateway had stopped serving `piper-amy` tomorrow.
This file is the one that cannot: it asks the actual gateway what it serves and asserts
that **the "piper-amy when possible" default is still possible**, that the classifier
still splits the listing into voices and ears, and that an environment which names no
engine pins nothing — the state every ordinary deployment is in.

Why it is worth a live request at all: the picker's whole promise is "what this appliance
can *really* use right now", and the listing is the only input to it we do not own. A
rename on the gateway silently turns the console's default into "the first voice in the
list" with nothing failing anywhere.

**Budget: ONE request to `/v1/models`.** All four assertions share the one listing through
a `GatewayCatalog` built here, which is also the seam the appliance itself uses.

Runs when `MOXIE_VOICE_BASE_URL` and a key are set (mqtt/.env of this tree or the main
checkout); skips cleanly and instantly otherwise.

    .venv/bin/python -m pytest sim/tests/test_live_voice_picker.py -q -s
"""
import importlib
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.dirname(__file__))

pytest.importorskip("openai", reason="openai SDK not installed (live gateway test)")

from helpers_runtime import load_repo_dotenv                 # noqa: E402

load_repo_dotenv()          # mqtt/.env from this tree or the main checkout

BASE = (os.environ.get("MOXIE_VOICE_BASE_URL") or "").strip()
KEY = (os.environ.get("MOXIE_VOICE_API_KEY")
       or os.environ.get("MOXIE_LLM_API_KEY")
       or os.environ.get("LITELLM_MASTER_KEY") or "")

pytestmark = pytest.mark.skipif(
    not (BASE and KEY),
    reason="no gateway voice configured (set MOXIE_VOICE_BASE_URL + a key in mqtt/.env)")

_CACHE = {}


@pytest.fixture(scope="module")
def listed():
    """`VoiceEngines.available()` over ONE live listing, cached for the whole module."""
    import config
    from moxie_sdk import voice_settings as vs
    if "out" not in _CACHE:
        config = importlib.reload(config)
        calls = []

        def _once():
            calls.append(1)
            return config.gateway_model_ids()

        cat = vs.GatewayCatalog(_once, ttl_s=3600)
        # `settle_s` because the first snapshot is served from a background refresh —
        # the same bounded wait a console WRITE asks for.
        out = config.voice_engines(cat).available(settle_s=20)
        assert len(calls) == 1, f"the module spent {len(calls)} requests, budget is 1"
        _CACHE["out"] = out
    return _CACHE["out"]


def test_the_gateway_still_serves_the_voice_the_default_names(listed):
    from moxie_sdk import voice_settings as vs
    assert listed["gateway_error"] == "", listed["gateway_error"]
    speech = vs.option_ids(listed["available"][vs.SPEECH])
    print(f"\n[picker] {len(speech)} speech entries: {', '.join(speech[:6])} …")
    assert "gateway:piper-amy" in speech, \
        "the gateway no longer lists piper-amy — the owner's default voice is gone"


def test_the_untouched_picker_defaults_to_piper_amy_against_the_real_list(listed):
    """The acceptance criterion, with nothing stored and nothing faked: a console nobody
    has ever opened speaks with `piper-amy`."""
    from moxie_sdk import voice_settings as vs
    defaults = vs.resolve_defaults(listed["available"])
    print(f"[picker] default speech={vs.choice_id(defaults[vs.SPEECH])} "
          f"listening={vs.choice_id(defaults[vs.LISTENING])}")
    assert vs.choice_id(defaults[vs.SPEECH]) == "gateway:piper-amy"
    assert vs.choice_id(defaults[vs.LISTENING]) == "gateway:stt-whisper"


def test_the_listing_still_splits_into_voices_and_ears(listed):
    """The classifier reads names, not metadata (`audio_models.py`), so a gateway that
    renames its STT models would quietly serve them as voices."""
    from moxie_sdk import voice_settings as vs
    listening = vs.option_ids(listed["available"][vs.LISTENING])
    print(f"[picker] {len(listening)} listening entries: {', '.join(listening)}")
    assert "gateway:stt-whisper" in listening
    assert not any("stt" in i for i in vs.option_ids(listed["available"][vs.SPEECH])), \
        "an STT model is being offered as a voice"
    assert not any("piper" in i for i in listening), "a voice is being offered as ears"


def test_an_environment_that_names_no_engine_pins_nothing(listed):
    """Every ordinary deployment is in this state, and it is the one where the whole
    listing must reach the dropdown. The pinned direction is hermetic
    (`test_voice_settings.py`) because a pin removes entries rather than adding any —
    no gateway is needed to prove a shorter list."""
    from moxie_sdk import voice_settings as vs
    assert listed["pins"] == {vs.SPEECH: "", vs.LISTENING: ""}
    assert listed["pin_notes"] == {vs.SPEECH: "", vs.LISTENING: ""}
