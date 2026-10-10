"""
🎙️ The bench recorder: a read-only copy of the robot bus, one JSON line per message.

For the day a real Moxie is on the bench (`docs/guides/bench-day-checklist.md`): start it
before the robot connects, so the first session is recorded, not watched. It ships in the
supervisor image and runs where the supervisor runs:

    docker compose exec supervisor python -m moxie_sdk.wire_record --out /data/wire/bench.jsonl

**What it does.** One paho client with its own client id (`wire-recorder-<hex>`: never a `d_`
id, so the supervisor's `CONNECT_RE` never takes it for a robot) connects the way the
supervisor connects: `config.MQTT_HOST` / `MQTT_PORT`, plain MQTT on the compose network (the
supervisor's own client uses no TLS), and `config.broker_credentials()`. It subscribes to two
filters, `FILTERS`, and nothing else: `/devices/#` (both directions of every robot's traffic)
and `$SYS/broker/log/#` (the broker's connect and disconnect lines). **It never publishes**:
nothing in this module calls `publish` (`sim/tests/test_wire_record.py` pins that in the
source and over a whole session). It is its own process, so the supervisor's turn path is
untouched. It sees what the broker delivered at QoS 0, not a publish the supervisor dropped
(those stay in the supervisor's connection ring).

**One line per message:** wall and monotonic time, the direction (`robot>cloud`,
`cloud>robot`, `broker`), the device id, the message name, the size and the body. A JSON body
is kept as sent, except `commands/tts` audio, which becomes `{bytes, sample_rate, channels}`.
A `zmq` frame goes through the SDK's own readers: a `zmqSTTRequest` becomes `{vad, uuid,
audio: {bytes, rms}}`, a `zmqSTTResponse` its fields, a `ProtoSubscribe` its proto names.
Raw audio, and any binary payload this module cannot name, is kept only with `--audio`.

**The file is private.** It holds what the child says, the child's name, and the robot's
address, id and Wi-Fi name. It is created `0600` and never overwritten, it lives in the
supervisor's data volume, it is never committed, and it is deleted after the session; the
start banner says so. Only a `python3 sim/tools/wire_timeline.py --share` copy, which holds no
identity and no words, may leave the machine. The recorder never writes the broker host it
connected to, and never prints it.

**Bounded.** paho's network thread only stamps and queues (a bounded queue); a writer thread
decodes and writes. A full queue drops messages and the file says how many. A size cap
(`--max-mb`, default 256) or a write that fails (a full disk) stops the recording with one
line.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import queue
import re
import secrets
import struct
import sys
import threading
import time
from typing import Callable, Optional

from . import stt as stt_seam

#: The two subscriptions, and nothing else.
FILTERS = ("/devices/#", "$SYS/broker/log/#")

#: The first line of every recording names its format; bumped if a line's shape changes.
FORMAT = "moxie-wire/1"

#: Directions, derived from the topic alone (`classify`).
ROBOT_TO_CLOUD = "robot>cloud"
CLOUD_TO_ROBOT = "cloud>robot"
BROKER = "broker"
UNKNOWN = "unknown"

#: The recorder's client id prefix. Never `d_`: the supervisor onboards only `d_` ids.
CLIENT_ID_PREFIX = "wire-recorder-"

#: Defaults for the CLI.
DEFAULT_MAX_MB = 256
QUEUE_MAX = 20000

#: Bytes kept free under the size cap for the one closing line.
STOP_RESERVE = 512

#: The bus's microphone format (stt.py: 16 kHz PCM16 mono).
STT_SAMPLE_RATE = 16000

#: A `d_` id inside a broker log line: the shape of the supervisor's `CONNECT_RE`.
_DEVICE_IN_LOG = re.compile(r"\b(d_[a-f0-9-]+)", re.I)

#: A protobuf descriptor full name, the prefix of every `zmq` frame (`b'<name>:' + bytes`).
_PROTO_NAME = re.compile(rb"[A-Za-z_][A-Za-z0-9_.]{0,200}")

#: Said when recording starts, and written as the file's first line.
BANNER = ("This file holds what the child says, the child's name, and the robot's address, "
          "id and Wi-Fi name. Keep it on this machine, delete it after the session, and "
          "share only a `python3 sim/tools/wire_timeline.py --share` copy.")

_STOP = object()          # the writer's sentinel


# --------------------------------------------------------------------------- #
# Pure helpers: what one message becomes
# --------------------------------------------------------------------------- #
def classify(topic: str) -> tuple:
    """`(direction, device_id, name)` for one topic, from the topic alone.

    `/devices/<id>/state` and `/devices/<id>/events/<name>` come from the robot;
    `/devices/<id>/config` and `/devices/<id>/commands/<name>` go to it
    (mqtt-and-conversation.md §3.2); `$SYS/broker/log/<level>` is the broker's own line.
    Anything else under `/devices/<id>/` is `unknown`: a topic the recovered map lacks.
    """
    if topic.startswith("$SYS/"):
        return BROKER, "", topic[len("$SYS/broker/"):] if topic.startswith("$SYS/broker/") \
            else topic[len("$SYS/"):]
    parts = topic.split("/")
    if len(parts) >= 4 and parts[0] == "" and parts[1] == "devices" and parts[2]:
        device, kind, rest = parts[2], parts[3], "/".join(parts[4:])
        if kind == "state" and not rest:
            return ROBOT_TO_CLOUD, device, "state"
        if kind == "events" and rest:
            return ROBOT_TO_CLOUD, device, rest
        if kind == "config" and not rest:
            return CLOUD_TO_ROBOT, device, "config"
        if kind == "commands" and rest:
            return CLOUD_TO_ROBOT, device, rest
        return UNKNOWN, device, "/".join(parts[3:])
    return UNKNOWN, "", topic


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def audio_summary(pcm: bytes) -> dict:
    """`{bytes, rms}` of one 16 kHz PCM16 clip: the level the honest ears judge
    (`stt.audio_stats`, RMS as a fraction of full scale)."""
    _, rms = stt_seam.audio_stats(pcm, STT_SAMPLE_RATE)
    return {"bytes": len(pcm), "rms": round(rms, 5)}


def _varint(data: bytes, i: int) -> tuple:
    shift = value = 0
    for _ in range(10):
        byte = data[i]
        i += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, i
        shift += 7
    raise ValueError("a varint longer than 10 bytes")


def proto_fields(data: bytes) -> Optional[list]:
    """`[(field, wire_type, value)]` of one protobuf message, or None when it does not parse.
    Varints are ints, fixed32/fixed64 and length-delimited fields are bytes."""
    out, i, n = [], 0, len(data)
    try:
        while i < n:
            tag, i = _varint(data, i)
            field, wire = tag >> 3, tag & 7
            if wire == 0:
                value, i = _varint(data, i)
            elif wire == 1:
                value, i = data[i:i + 8], i + 8
            elif wire == 2:
                length, i = _varint(data, i)
                value, i = data[i:i + length], i + length
            elif wire == 5:
                value, i = data[i:i + 4], i + 4
            else:
                return None
            if i > n or field == 0:
                return None
            out.append((field, wire, value))
    except (IndexError, ValueError):
        return None
    return out


def _text(value) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, (bytes, bytearray)) else ""


def _decode_stt_response(body: bytes) -> Optional[dict]:
    """A `zmqSTTResponse` (zmqSTT.proto): type=2, speech=3, confidence=4 (float), uuid=7,
    error_code=8, error_message=9; the fields this appliance writes or reads."""
    fields = proto_fields(body)
    if fields is None:
        return None
    out = {}
    for field, wire, value in fields:
        if field == 2 and wire == 0:
            out["type"] = {0: "PARTIAL", 1: "FINAL"}.get(value, value)
        elif field == 3 and wire == 2:
            out["speech"] = _text(value)
        elif field == 4 and wire == 5 and len(value) == 4:
            out["confidence"] = round(struct.unpack("<f", value)[0], 4)
        elif field == 7 and wire == 2:
            out["uuid"] = _text(value)
        elif field == 8 and wire == 0:
            out["error_code"] = value
        elif field == 9 and wire == 2:
            out["error_message"] = _text(value)
    out.setdefault("type", "PARTIAL")          # proto3: an unset enum is its zero value
    out.setdefault("speech", "")
    return out


def _decode_proto_subscribe(body: bytes) -> Optional[dict]:
    """A `ProtoSubscribe` (Log.proto): protos=2, a repeated string."""
    fields = proto_fields(body)
    if fields is None:
        return None
    return {"protos": [_text(v) for f, w, v in fields if f == 2 and w == 2]}


def _stt_request(vad, uuid, pcm: bytes, keep_audio: bool) -> dict:
    try:
        vad_name = stt_seam.VADState(int(vad)).name
    except (ValueError, TypeError):
        vad_name = str(vad)
    out = {"vad": vad, "vad_name": vad_name, "uuid": uuid or "", "audio": audio_summary(pcm)}
    if keep_audio and pcm:
        out["audio_b64"] = _b64(pcm)
    return out


def _decode_zmq(payload: bytes, keep_audio: bool) -> tuple:
    """`(as, body)` for one `events/zmq` or `commands/zmq` payload."""
    # The SIL and test doubles' JSON frame: `{vad, audio_content (base64), uuid}`.
    try:
        doc = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        doc = None
    if isinstance(doc, dict) and "vad" in doc:
        try:
            pcm = base64.b64decode(doc.get("audio_content") or "")
        except (ValueError, TypeError):
            pcm = b""
        return "zmq-json", _stt_request(doc.get("vad"), doc.get("uuid"), pcm, keep_audio)
    name, sep, body = payload.partition(b":")
    if not sep or not _PROTO_NAME.fullmatch(name):
        return _opaque(payload, keep_audio)
    full = name.decode("ascii")
    out = None
    if full == stt_seam.ZMQ_STT_REQUEST:
        frame = stt_seam.decode_zmq_stt_frame(payload)       # the supervisor's own reader
        if frame is not None:
            out = _stt_request(frame["vad"], frame["uuid"], frame["audio"], keep_audio)
    elif full == stt_seam.ZMQ_STT_RESPONSE:
        out = _decode_stt_response(body)
    elif full == stt_seam.PROTO_SUBSCRIBE:
        out = _decode_proto_subscribe(body)
    if out is None:                                 # a proto we do not read, or garbled
        out = {"bytes": len(body)}
        if keep_audio:
            out["b64"] = _b64(body)
    return "zmq", {"proto": full, **out}


def _opaque(payload: bytes, keep_audio: bool) -> tuple:
    """A payload that is not JSON: its text, or (binary) its size; bytes only with --audio."""
    try:
        return "text", {"text": payload.decode("utf-8")}
    except UnicodeDecodeError:
        out = {"bytes": len(payload)}
        if keep_audio:
            out["b64"] = _b64(payload)
        return "binary", out


def _tts_without_audio(doc, keep_audio: bool):
    """A `CloudTTSResponse` with its rendered audio replaced by `{bytes, sample_rate,
    channels}` (the buffer is kept as sent only with --audio)."""
    audio = doc.get("audio") if isinstance(doc, dict) else None
    if not isinstance(audio, dict) or "buffer" not in audio:
        return doc
    buf = audio.get("buffer") or ""
    try:
        size = len(base64.b64decode(buf)) if isinstance(buf, str) else 0
    except (ValueError, TypeError):
        size = 0
    summary = {"bytes": size, "sample_rate": audio.get("sample_rate"),
               "channels": audio.get("channels")}
    if keep_audio:
        summary["buffer"] = buf
    return {**doc, "audio": summary}


def decode_payload(direction: str, name: str, payload: bytes, *,
                   keep_audio: bool = False) -> tuple:
    """`(as, body)` for one message: `log` (a broker line's text), `json` (kept as sent,
    `commands/tts` audio summarized), `zmq` / `zmq-json` (decoded), `text` or `binary`."""
    if direction == BROKER:
        return "log", {"text": payload.decode("utf-8", "replace")}
    if name == "zmq":
        return _decode_zmq(payload, keep_audio)
    try:
        doc = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return _opaque(payload, keep_audio)
    if name == "tts" and direction == CLOUD_TO_ROBOT:
        doc = _tts_without_audio(doc, keep_audio)
    return "json", doc


def new_client_id() -> str:
    """`wire-recorder-<8 hex>`: unique, so two recorders do not displace each other."""
    return CLIENT_ID_PREFIX + secrets.token_hex(4)


def _connack_failed(rc) -> bool:
    """A CONNACK that refused us (a paho ReasonCode under VERSION2, an int under VERSION1)."""
    failed = getattr(rc, "is_failure", None)
    if failed is not None:
        return bool(failed)
    try:
        return int(rc) != 0
    except (TypeError, ValueError):
        return False


def _suback_failed(rc) -> bool:
    """One SUBACK entry that refused its filter (`0x80`, or a failure ReasonCode); a granted
    QoS of 0, 1 or 2 is a yes."""
    failed = getattr(rc, "is_failure", None)
    if failed is not None:
        return bool(failed)
    try:
        return int(rc) >= 128
    except (TypeError, ValueError):
        return False


def _reason(rc) -> str:
    try:
        import paho.mqtt.client as mqtt
        return str(mqtt.connack_string(rc))
    except Exception:
        return str(rc)


# --------------------------------------------------------------------------- #
# The recorder
# --------------------------------------------------------------------------- #
class WireRecorder:
    """Stamp and queue on paho's thread; decode and write on its own thread.

    `wall` and `mono` are the clocks (injected by tests, which read none); `say` prints one
    operator line; `on_record(record)` sees every line written (the SIL test waits on it).
    `fh` writes to an open text file instead of creating `out`.
    """

    def __init__(self, out: str = "", *, keep_audio: bool = False,
                 max_bytes: int = DEFAULT_MAX_MB * 2 ** 20, queue_max: int = QUEUE_MAX,
                 wall: Callable[[], float] = time.time,
                 mono: Callable[[], float] = time.monotonic,
                 say: Optional[Callable[[str], None]] = None,
                 on_record: Optional[Callable[[dict], None]] = None, fh=None):
        self.out = out
        self.keep_audio = bool(keep_audio)
        self.max_bytes = int(max_bytes)
        self._wall, self._mono = wall, mono
        self._say = say or (lambda line: print(line, flush=True))
        self._on_record = on_record
        self._fh = fh
        self._q: queue.Queue = queue.Queue(maxsize=max(1, int(queue_max)))
        self._lock = threading.Lock()
        self._dropped = 0                 # refused by the full queue, not yet written down
        self.dropped_total = 0
        self.written = 0                  # bytes in the file
        self.lines = 0                    # message lines written
        self.devices: list = []           # robots heard, in order (never printed)
        self._t0 = mono()
        self._writer: Optional[threading.Thread] = None
        self._closed = False
        self.client = None
        self.connects = 0
        #: Set once the recording has ended (the cap, a failed write, `stop()`).
        self.stopped = threading.Event()
        self.stop_reason = ""
        #: Set once the broker granted `/devices/#`.
        self.subscribed = threading.Event()
        #: Why the broker refused us; the CLI exits on it.
        self.refused = ""
        self._failing = False             # inside a connect-failure streak (said once)

    # ---- the file ----
    def open(self) -> "WireRecorder":
        """Create the file (0600, never an existing one) and write the banner."""
        if self._fh is None:
            folder = os.path.dirname(os.path.abspath(self.out))
            os.makedirs(folder, mode=0o700, exist_ok=True)
            fd = os.open(self.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            self._fh = os.fdopen(fd, "w", encoding="utf-8")
        self._emit({"kind": "banner", "format": FORMAT, "t": round(self._wall(), 3),
                    "mono": 0.0, "filters": list(FILTERS), "audio": self.keep_audio,
                    "max_bytes": self.max_bytes, "note": BANNER})
        where = f" to {self.out}" if self.out else ""
        self._say(f"[wire] 🔴 recording the robot bus{where}")
        self._say(f"[wire] {BANNER}")
        if self.keep_audio:
            self._say("[wire] --audio: the microphone's raw audio is in the file too")
        return self

    def start(self) -> "WireRecorder":
        """Start the writer thread (separate from `open` so a test can fill the queue)."""
        if self._writer is None:
            self._writer = threading.Thread(target=self._write_loop, name="wire-writer",
                                            daemon=True)
            self._writer.start()
        return self

    # ---- paho's thread: stamp and queue, nothing else ----
    def on_message(self, client, userdata, msg):
        if self.stopped.is_set():
            return
        self._put(("msg", self._wall(), self._mono(), msg.topic, bytes(msg.payload)))

    def _put(self, item, *, wait: float = 0.0):
        """Queue one item; never block paho for long. Messages a full queue refused are
        counted, and the count goes into the queue just ahead of the next item that fits,
        so the file says where the gap is."""
        try:
            with self._lock:
                pending = self._dropped
            if pending:
                self._q.put_nowait(("dropped", item[1], item[2], pending))
                with self._lock:
                    self._dropped -= pending
            if wait:
                self._q.put(item, timeout=wait)
            else:
                self._q.put_nowait(item)
        except queue.Full:
            with self._lock:
                self._dropped += 1
                first = self.dropped_total == 0
                self.dropped_total += 1
            if first:
                self._say("[wire] ⚠️  the writer fell behind and is dropping messages; "
                          "the file says how many")

    def _note(self, text: str):
        """A line about the recorder itself (connected, lost the broker), in order with
        the messages. Waits a moment for room rather than losing it."""
        self._put(("note", self._wall(), self._mono(), text), wait=1.0)

    def on_connect(self, client, userdata, flags, rc, properties=None):
        if _connack_failed(rc):
            self.refused = _reason(rc)
            self._note(f"the broker refused the recorder: {self.refused}")
            self._say(f"[wire] ⛔ the broker refused the recorder: {self.refused}")
            return
        self.connects += 1
        self._failing = False
        # One call: one SUBACK, two filters, and nothing else.
        client.subscribe([(topic, 0) for topic in FILTERS])
        self._note("connected to the broker" if self.connects == 1
                   else "reconnected to the broker")
        if self.connects > 1:
            self._say("[wire] reconnected to the broker")

    def on_subscribe(self, client, userdata, mid, reason_codes=None, properties=None):
        codes = list(reason_codes or [])
        refused = [FILTERS[i] for i, rc in enumerate(codes[:len(FILTERS)])
                   if _suback_failed(rc)]
        if FILTERS[0] in refused:
            self.refused = f"the broker refused the subscription {FILTERS[0]}"
            self._note(self.refused)
            self._say(f"[wire] ⛔ {self.refused}: nothing can be recorded")
            return
        if refused:
            self._note(f"the broker refused {', '.join(refused)}: no broker log lines "
                       f"(connects and disconnects) will be recorded")
            self._say(f"[wire] ⚠️  the broker refused {', '.join(refused)}: the robots' "
                      f"traffic is recorded, the broker's connect lines are not")
        else:
            self._note(f"subscribed to {' and '.join(FILTERS)}")
        if not self.subscribed.is_set():
            self.subscribed.set()
            self._say("[wire] listening; leave this running for the whole session "
                      "(Ctrl-C stops it)")

    def on_disconnect(self, client, userdata, flags=None, rc=None, properties=None):
        if self._closed:
            return
        why = str(rc) if getattr(rc, "is_failure", None) is not None else "connection lost"
        self._note(f"lost the broker ({why}); reconnecting")
        self._say("[wire] ⚠️  lost the broker; reconnecting (the file marks the gap)")

    def on_connect_fail(self, client, userdata=None):
        if self._failing:
            return
        self._failing = True
        self._note("could not reach the broker; retrying")
        self._say("[wire] ⛔ could not reach the broker; retrying")

    def connect(self, host: str, port: int, username: str = "", password: str = "", *,
                client_factory=None, keepalive: int = 30):
        """Connect as the supervisor does, and start paho's network thread. The host is used
        here and nowhere else: it is never written to the file or printed."""
        client_id = new_client_id()
        if client_factory is None:
            import paho.mqtt.client as mqtt
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        else:
            client = client_factory(client_id)
        if username and password:
            client.username_pw_set(username, password)
        client.on_connect = self.on_connect
        client.on_subscribe = self.on_subscribe
        client.on_message = self.on_message
        client.on_disconnect = self.on_disconnect
        client.on_connect_fail = self.on_connect_fail
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        client.connect_async(host, int(port), keepalive)
        client.loop_start()
        self.client = client
        return client

    # ---- the writer's thread ----
    def _write_loop(self):
        while True:
            item = self._q.get()
            if item is _STOP:
                return
            self._write_item(item)

    def drain(self):
        """Write whatever is queued, on the calling thread. For a recorder whose writer
        thread is not running (tests); the thread does this itself."""
        while True:
            try:
                item = self._q.get_nowait()
            except queue.Empty:
                return
            if item is not _STOP:
                self._write_item(item)

    def _dropped_line(self, wall, mono, count) -> dict:
        return {"kind": "dropped", "t": round(wall, 3), "mono": round(mono - self._t0, 3),
                "count": count}

    def _write_item(self, item):
        if self.stopped.is_set():
            return
        kind, wall, mono = item[0], item[1], item[2]
        if kind == "dropped":
            self._emit(self._dropped_line(wall, mono, item[3]))
            return
        if kind == "note":
            self._emit({"kind": "note", "t": round(wall, 3),
                        "mono": round(mono - self._t0, 3), "text": item[3]})
            return
        try:
            record = self.line_for(wall, mono, item[3], item[4])
        except Exception as e:            # one unreadable message never stops the writer
            direction, device, name = classify(item[3])
            record = {"kind": "msg", "t": round(wall, 3), "mono": round(mono - self._t0, 3),
                      "dir": direction, "device": device, "topic": item[3], "name": name,
                      "bytes": len(item[4]), "as": "error",
                      "body": {"error": type(e).__name__}}
        if self._emit(record):
            self.lines += 1
            device = record.get("device")
            if device and record["dir"] != BROKER and device not in self.devices:
                self.devices.append(device)
                self._say(f"[wire] heard robot #{len(self.devices)}")

    def line_for(self, wall: float, mono: float, topic: str, payload: bytes) -> dict:
        """The JSON line for one message (see the module doc)."""
        direction, device, name = classify(topic)
        as_, body = decode_payload(direction, name, payload, keep_audio=self.keep_audio)
        if direction == BROKER:
            found = _DEVICE_IN_LOG.search(body.get("text", ""))
            device = found.group(1) if found else ""
        return {"kind": "msg", "t": round(wall, 3), "mono": round(mono - self._t0, 3),
                "dir": direction, "device": device, "topic": topic, "name": name,
                "bytes": len(payload), "as": as_, "body": body}

    def _emit(self, record: dict, *, closing: bool = False) -> bool:
        """Write one line. Past the cap, end the recording instead (one line)."""
        if self._fh is None or (self.stopped.is_set() and not closing):
            return False
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        size = len(line.encode("utf-8"))
        reserve = 0 if closing else STOP_RESERVE
        if self.written + size + reserve > self.max_bytes:
            if not closing:
                self._finish(f"size cap reached ({self.max_bytes} bytes)", record)
            return False
        try:
            self._fh.write(line)
            self._fh.flush()
        except OSError as e:
            self._fail(e)
            return False
        self.written += size
        if self._on_record is not None:
            try:
                self._on_record(record)
            except Exception:
                pass
        return True

    def _finish(self, reason: str, last: Optional[dict] = None):
        """Write the one closing line and stop accepting messages."""
        if self.stopped.is_set():
            return
        self.stop_reason = reason
        mono = (last or {}).get("mono")
        if mono is None:
            mono = round(self._mono() - self._t0, 3)
        self._emit({"kind": "stop", "t": round(self._wall(), 3), "mono": mono,
                    "reason": reason, "lines": self.lines, "dropped": self.dropped_total},
                   closing=True)
        self.stopped.set()
        self._say(f"[wire] ⏹  stopped: {reason} · {self.lines} message lines · "
                  f"{self.written / 2 ** 20:.1f} MB · {self.dropped_total} dropped")

    def _fail(self, exc: OSError):
        """A write failed (a full disk): one line, and no more writes."""
        if self.stopped.is_set():
            return
        self.stop_reason = f"could not write the recording ({exc.strerror or exc})"
        self.stopped.set()
        self._say(f"[wire] ⛔ stopped: {self.stop_reason}")

    # ---- the end ----
    def close_client(self):
        """Disconnect paho (no more messages arrive). Never from a paho callback."""
        self._closed = True
        client, self.client = self.client, None
        if client is None:
            return
        for step in ("disconnect", "loop_stop"):
            try:
                getattr(client, step)()
            except Exception:
                pass

    def stop(self, reason: str = "stopped") -> str:
        """Write what is queued, then the closing line, and close the file. Idempotent;
        returns why the recording ended."""
        if self._writer is not None:
            self._q.put(_STOP)
            self._writer.join()
            self._writer = None
        else:
            self.drain()
        if not self.stopped.is_set():
            with self._lock:
                dropped, self._dropped = self._dropped, 0
            if dropped:                    # refused at the very end: nothing came after
                self._emit(self._dropped_line(self._wall(), self._mono(), dropped))
            self._finish(reason)
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None
        return self.stop_reason


# --------------------------------------------------------------------------- #
# The command line
# --------------------------------------------------------------------------- #
def broker_settings() -> tuple:
    """`(host, port, username, password)` exactly as the supervisor connects: `config.py`'s
    `MQTT_HOST` / `MQTT_PORT` and `broker_credentials()`. Without `config` (the SDK installed
    on its own), the same two variables, anonymous."""
    try:
        import config
    except ImportError:
        return (os.environ.get("MOXIE_MQTT_HOST", "127.0.0.1"),
                int(os.environ.get("MOXIE_MQTT_PORT", "1883")), "", "")
    username, password = config.broker_credentials()
    return config.MQTT_HOST, int(config.MQTT_PORT), username, password


def default_out() -> str:
    """`$MOXIE_DATA_DIR/wire/wire-<UTC time>.jsonl`, or "" when no data dir is set (the
    CLI then asks for `--out` rather than write a private file into a checkout)."""
    data_dir = os.environ.get("MOXIE_DATA_DIR", "").strip()
    if not data_dir:
        return ""
    stamp = time.strftime("%Y%m%d-%H%M%SZ", time.gmtime())
    return os.path.join(data_dir, "wire", f"wire-{stamp}.jsonl")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m moxie_sdk.wire_record",
        description="Record the robot bus to a private JSONL file (read-only: it never "
                    "publishes). Read it with sim/tools/wire_timeline.py.")
    ap.add_argument("--out", default="", help="the file to create (default: "
                    "$MOXIE_DATA_DIR/wire/wire-<UTC time>.jsonl); never overwritten")
    ap.add_argument("--minutes", type=float, default=0.0,
                    help="stop after this many minutes (default: until Ctrl-C)")
    ap.add_argument("--audio", action="store_true",
                    help="also keep the microphone's raw audio and any binary payload "
                         "(default: each clip's length and loudness only)")
    ap.add_argument("--max-mb", type=float, default=float(DEFAULT_MAX_MB),
                    help=f"stop at this file size (default {DEFAULT_MAX_MB})")
    args = ap.parse_args(argv)

    out = args.out or default_out()
    if not out:
        print("[wire] say where to write the recording: --out /data/wire/<name>.jsonl "
              "(MOXIE_DATA_DIR is not set)", flush=True)
        return 2
    recorder = WireRecorder(out, keep_audio=args.audio,
                            max_bytes=int(args.max_mb * 2 ** 20))
    try:
        recorder.open()
    except FileExistsError:
        print(f"[wire] {out} already exists; pick another --out (a recording is never "
              f"overwritten)", flush=True)
        return 2
    recorder.start()

    halt = threading.Event()
    try:
        import signal
        signal.signal(signal.SIGTERM, lambda *_: halt.set())
    except (ValueError, OSError, AttributeError):
        pass
    host, port, username, password = broker_settings()
    reason = "stopped by hand"
    deadline = time.monotonic() + args.minutes * 60 if args.minutes > 0 else None
    try:
        recorder.connect(host, port, username, password)
        while not recorder.stopped.is_set():
            if recorder.refused:
                reason = recorder.refused
                break
            if halt.is_set():
                reason = "stopped (SIGTERM)"
                break
            if deadline is not None and time.monotonic() >= deadline:
                reason = f"{args.minutes:g} minutes elapsed"
                break
            recorder.stopped.wait(0.5)
    except KeyboardInterrupt:
        reason = "stopped by hand (Ctrl-C)"
    finally:
        recorder.close_client()
        reason = recorder.stop(reason)
    return 1 if recorder.refused else 0


if __name__ == "__main__":                                   # pragma: no cover - CLI
    sys.exit(main())
