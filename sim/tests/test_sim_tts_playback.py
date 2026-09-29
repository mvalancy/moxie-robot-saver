"""The virtual robot consumes a CloudTTSResponse on /commands/tts and records that Moxie
spoke. It decodes the wire itself (no server-SDK import), like real firmware."""
import base64
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "sim"))

pytest.importorskip("paho.mqtt.client")            # SIM client needs paho
from virtual_moxie import VirtualMoxie              # noqa: E402


def _vm():
    return VirtualMoxie(host="127.0.0.1", port=1, device_id="d_test", verbose=False)


@pytest.mark.parametrize("audio", [b"\x01\x02\x03\x04", b""])
def test_sim_plays_tts_and_records_it(audio):
    vm = _vm()
    marks = [{"type": "word", "value": "Hi"}]
    vm._play_tts({"request_source": "ROBOT_TTS_REQUEST", "event_id": "evt-9", "chunk_num": 0,
                  "audio": {"buffer": base64.b64encode(audio).decode(), "channels": 1,
                            "sample_rate": 22050},
                  "marks": marks})
    assert vm.got_tts.is_set() and not vm.errors
    assert vm.spoke == {"audio": audio, "sample_rate": 22050, "channels": 1,
                        "marks": marks, "event_id": "evt-9"}


def test_sim_tts_bad_payload_is_recorded_not_raised():
    vm = _vm()
    vm._play_tts({"audio": {"buffer": "!!!not base64!!!"}})   # must not raise
    assert vm.spoke is None and not vm.got_tts.is_set()
    assert vm.errors and "tts decode failed" in vm.errors[0]
