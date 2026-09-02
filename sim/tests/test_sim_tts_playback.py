"""
SIM audio-playback test — the virtual robot consumes a CloudTTSResponse on
/commands/tts and records that Moxie spoke (bytes + rate + marks), closing the
talk-e2e loop on the client side. The SIM decodes the wire directly (no server-SDK
import), like a real robot's firmware, so this also guards client/server independence.
"""
import base64
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "sim"))

pytest.importorskip("paho.mqtt.client")            # SIM client needs paho
from virtual_moxie import VirtualMoxie              # noqa: E402


def _tts_wire(audio: bytes, *, rate=22050, event_id="e1", marks=None):
    return {
        "request_source": "ROBOT_TTS_REQUEST",
        "audio": {"buffer": base64.b64encode(audio).decode(), "channels": 1,
                  "sample_rate": rate},
        "marks": marks or [],
        "event_id": event_id,
        "chunk_num": 0,
    }


def _client():
    vm = VirtualMoxie(host="127.0.0.1", port=1, device_id="d_test", verbose=False)
    return vm


def test_sim_plays_tts_and_records_it():
    vm = _client()
    vm._play_tts(_tts_wire(b"\x01\x02\x03\x04", rate=22050, event_id="evt-9",
                           marks=[{"type": "word", "value": "Hi"}]))
    assert vm.got_tts.is_set()
    assert vm.spoke is not None
    assert vm.spoke["audio"] == b"\x01\x02\x03\x04"
    assert vm.spoke["sample_rate"] == 22050 and vm.spoke["event_id"] == "evt-9"
    assert vm.spoke["marks"] == [{"type": "word", "value": "Hi"}]
    assert not vm.errors


def test_sim_tts_handles_empty_audio():
    vm = _client()
    vm._play_tts(_tts_wire(b"", event_id="quiet"))
    assert vm.got_tts.is_set() and vm.spoke["audio"] == b"" and not vm.errors


def test_sim_tts_bad_payload_is_recorded_not_raised():
    vm = _client()
    vm._play_tts({"audio": {"buffer": "!!!not base64!!!"}})   # must not raise
    # tolerant decode: either records an error or yields empty audio, never crashes
    assert vm.spoke is None or vm.spoke["audio"] == b"" or vm.errors
