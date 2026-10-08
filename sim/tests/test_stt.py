"""
STT seam tests (M3) — the VAD accumulator + transcriber interface + the bus-frame
encoders (checked byte for byte against the committed pb2 oracles). Pure (no audio
libs); the Whisper backend is exercised only for availability/skip.
"""
import pytest

from moxie_sdk import stt  # noqa: E402
from moxie_sdk.stt import (  # noqa: E402
    VADState, Transcriber, SttSession, encode_proto_subscribe, encode_zmq_stt_response,
    PROTO_SUBSCRIBE, ZMQ_STT_REQUEST, ZMQ_STT_RESPONSE,
)
from helpers_runtime import parse_zmq_frame, toolkit_pb2   # noqa: E402


class _FakeTranscriber(Transcriber):
    def __init__(self):
        self.got = None

    def transcribe(self, pcm, sample_rate=16000):
        self.got = pcm
        return f"heard {len(pcm)} bytes"


def test_vad_states_match_proto():
    assert VADState.START_OF_SPEECH == 1
    assert VADState.SPEECH == 2
    assert VADState.END_OF_SPEECH == 3


def test_accumulates_until_end_of_speech():
    t = _FakeTranscriber()
    s = SttSession(t)
    assert s.feed(VADState.START_OF_SPEECH, b"aa") is None
    assert s.feed(VADState.SPEECH, b"bb") is None
    assert s.feed(VADState.SPEECH, b"cc") is None
    out = s.feed(VADState.END_OF_SPEECH, b"dd")
    assert out == "heard 8 bytes"
    assert t.got == b"aabbccdd"        # everything concatenated, in order


def test_int_vad_values_accepted():
    s = SttSession(_FakeTranscriber())
    assert s.feed(1, b"x") is None     # START_OF_SPEECH as int
    assert s.feed(3, b"y") == "heard 2 bytes"


def test_new_utterance_resets_buffer():
    t = _FakeTranscriber()
    s = SttSession(t)
    s.feed(VADState.START_OF_SPEECH, b"first")
    s.feed(VADState.END_OF_SPEECH, b"")
    # a fresh utterance must not carry the previous audio
    s.feed(VADState.START_OF_SPEECH, b"NEW")
    s.feed(VADState.END_OF_SPEECH, b"")
    assert t.got == b"NEW"


def test_empty_utterance_yields_empty_string():
    s = SttSession(_FakeTranscriber())
    assert s.feed(VADState.END_OF_SPEECH, b"") == ""


def test_response_encoder_writes_what_protoc_writes(monkeypatch):
    """`encode_zmq_stt_response` is byte-identical to the compiled proto's own serializer
    (fields 1, 2, 3, 4, 7 in order) wrapped in the bus framing, for a FINAL, a PARTIAL
    and an empty transcript — the last still a FINAL, since the robot's turn ends on it."""
    pb = toolkit_pb2("embodied.perception.audio.zmqSTT_pb2")

    def oracle(uuid, speech, final, confidence, ts):
        r = pb.zmqSTTResponse()
        r.timestamp, r.speech, r.confidence, r.uuid = ts, speech, confidence, uuid
        r.type = r.FINAL if final else r.PARTIAL
        return ZMQ_STT_RESPONSE.encode() + b":" + r.SerializeToString()

    for uuid, speech, final, conf, ts in [("u-1", "hello moxie", True, 1.0, 1700000000000),
                                          ("u", "hi", False, 0.25, 1),
                                          ("u-2", "", True, 1.0, 2 ** 40 + 300)]:
        got = encode_zmq_stt_response(uuid, speech, final=final, confidence=conf,
                                      timestamp_ms=ts)
        assert got == oracle(uuid, speech, final, conf, ts), (uuid, speech, final)
        parsed = parse_zmq_frame(got, pb.zmqSTTResponse)
        assert (parsed.type, parsed.speech, parsed.uuid, parsed.timestamp) == \
            (parsed.FINAL if final else parsed.PARTIAL, speech, uuid, ts)
    # the default timestamp is the SDK's millisecond clock
    monkeypatch.setattr(stt, "now_ms", lambda: 4242)
    assert parse_zmq_frame(encode_zmq_stt_response("u", "x"), pb.zmqSTTResponse).timestamp == 4242


def test_response_encoder_writes_the_error_fields_protoc_writes():
    """A failed transcription is still a FINAL, carrying the failure in zmqSTT.proto's
    `error_code=8` and `error_message=9`; byte-identical to the compiled serializer.
    Without an error neither field is written (proto3: an unset field is absent)."""
    pb = toolkit_pb2("embodied.perception.audio.zmqSTT_pb2")
    r = pb.zmqSTTResponse()
    r.timestamp, r.type, r.speech, r.confidence, r.uuid = 4242, r.FINAL, "", 0.0, "u-err"
    r.error_code, r.error_message = stt.STT_ERROR_CODE, "RuntimeError: model not loaded"
    got = encode_zmq_stt_response("u-err", "", confidence=0.0, timestamp_ms=4242,
                                  error_code=stt.STT_ERROR_CODE,
                                  error_message="RuntimeError: model not loaded")
    assert got == ZMQ_STT_RESPONSE.encode() + b":" + r.SerializeToString()
    parsed = parse_zmq_frame(got, pb.zmqSTTResponse)
    assert (parsed.error_code, parsed.error_message) == (66, "RuntimeError: model not loaded")
    # the same frame without an error has no field 8 or 9
    ok = parse_zmq_frame(encode_zmq_stt_response("u", "hi", timestamp_ms=1), pb.zmqSTTResponse)
    assert not ok.HasField("error_code") and not ok.HasField("error_message")


def test_a_negative_varint_is_refused_not_looped():
    """`_write_varint` shifts until nothing is left, and a negative int never runs out
    (`-1 >> 7 == -1`). `timestamp_ms` is a public keyword of both encoders, so a bad
    clock must fail loudly instead of hanging the network thread."""
    with pytest.raises(ValueError):
        stt._write_varint(-1)
    with pytest.raises(ValueError):
        encode_proto_subscribe([ZMQ_STT_REQUEST], timestamp_ms=-1)
    with pytest.raises(ValueError):
        encode_zmq_stt_response("u", "x", timestamp_ms=-(2 ** 40))
    assert stt._write_varint(0) == b"\x00" and stt._write_varint(300) == b"\xac\x02"


def test_proto_subscribe_encoder_writes_what_protoc_writes(monkeypatch):
    """The ask for the microphone: `ProtoSubscribe{timestamp=1, protos=2}` naming
    `zmqSTTRequest`, framed `b'embodied.logging.ProtoSubscribe:' + bytes`."""
    Log_pb2 = toolkit_pb2("embodied.logging.Log_pb2")
    sub = Log_pb2.ProtoSubscribe()
    sub.timestamp = 1700000000000
    sub.protos.append(ZMQ_STT_REQUEST)
    assert encode_proto_subscribe([ZMQ_STT_REQUEST], timestamp_ms=1700000000000) == \
        PROTO_SUBSCRIBE.encode() + b":" + sub.SerializeToString()
    assert PROTO_SUBSCRIBE == Log_pb2.ProtoSubscribe.DESCRIPTOR.full_name
    monkeypatch.setattr(stt, "now_ms", lambda: 1_700_000_000_123)
    got = parse_zmq_frame(encode_proto_subscribe([ZMQ_STT_REQUEST]), Log_pb2.ProtoSubscribe)
    assert list(got.protos) == [ZMQ_STT_REQUEST]
    assert got.timestamp == 1_700_000_000_123


def test_now_ms_is_milliseconds_since_the_epoch():
    """An int in the 10^12 range (seconds would be 10^9, nanoseconds 10^18)."""
    ms = stt.now_ms()
    assert isinstance(ms, int) and 1_700_000_000_000 < ms < 10_000_000_000_000


from helpers_audio import pb_zmq_stt_frame as _pb_zmq_frame   # noqa: E402


def test_decode_zmq_stt_frame():
    from moxie_sdk.stt import decode_zmq_stt_frame
    frame = _pb_zmq_frame(3, b"audiobytes", "utt-7")
    got = decode_zmq_stt_frame(frame)
    assert got == {"vad": 3, "audio": b"audiobytes", "uuid": "utt-7"}


def test_decode_ignores_non_stt_frames():
    from moxie_sdk.stt import decode_zmq_stt_frame
    assert decode_zmq_stt_frame(b"embodied.other.Thing:\x08\x01") is None
    assert decode_zmq_stt_frame(b"no-colon-here") is None
