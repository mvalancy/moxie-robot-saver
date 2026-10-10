#!/usr/bin/env python3
"""Read a wire recording (`python -m moxie_sdk.wire_record`): a per-robot timeline of what
bench day needs to see, and a `--share` copy that holds no identity and no words.

    python3 sim/tools/wire_timeline.py bench.jsonl                     # the timeline
    python3 sim/tools/wire_timeline.py bench.jsonl --share bench.share.jsonl
    docker compose exec supervisor cat /data/wire/bench.jsonl | python3 sim/tools/wire_timeline.py -

**The timeline**, per robot and in time order: the broker's connect lines and the first
`/state`; each config push and the next `/state`; the ask for the microphone
(`ProtoSubscribe`) and the first audio frame after it; each utterance's length, loudness and
transcript (its latency after END_OF_SPEECH, or none); each turn's request, its reply chunks,
whether the robot asked again before the turn closed (a re-prompt, with the gap), how many
`notify` reports followed, the actions it carried and the next request's `module_id`; every
other message counted by name. It is always computed from the `--share` view, so it is safe
to paste into an issue.

**The `--share` copy** keeps the shape of every line and drops who and what: every `d_` id
becomes a placeholder in order of first appearance (`d_robot-1`, `d_robot-2`); a broker line
loses its address, port, IPv6 literal, MAC, hostname, any dotted name and the username of the
connect line; a body keeps numbers, booleans and the strings named in `KEEP_STRINGS` (event and
module ids, commands, result shapes, proto names), and every other string, which is where
speech, transcripts, names, markup and log text live, becomes `"[text]"` (an empty one stays
empty). The child's record (`child_pii`) is withheld whole, audio and public keys are removed,
and wall-clock times are dropped (`mono`, seconds since the recorder started, stays). Before
anything is written the copy is checked again for a `d_` id, an address, a MAC, a hostname or a
username (`identity_problems`); if one is left the tool refuses and names the kind, never the
value. Only this copy may be committed (`sim/tests/data/wire/`) or pasted into an issue.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
import re
import sys
from collections import Counter

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if os.path.join(REPO, "mqtt") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.presence import VISION_EVENTS                      # noqa: E402
from moxie_sdk.types import ResultCode                             # noqa: E402

ROBOT_TO_CLOUD, CLOUD_TO_ROBOT, BROKER = "robot>cloud", "cloud>robot", "broker"
PLACEHOLDER = "d_robot-{}"
TEXT = "[text]"
WITHHELD = "[withheld]"

#: Bytes per millisecond of the bus's 16 kHz PCM16 mono audio.
BYTES_PER_MS = 32.0

# --------------------------------------------------------------------------- #
# What identity looks like
# --------------------------------------------------------------------------- #
#: A robot's id anywhere: any `d_` id that is not already a placeholder.
DEVICE_ID = re.compile(r"(?<![A-Za-z0-9_])[dD]_(?!robot-\d+(?![A-Za-z0-9-]))[A-Za-z0-9][A-Za-z0-9-]*")
_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
#: An IPv4 literal, with its port when one follows.
IPV4 = re.compile(rf"(?<!\d)(?<!\d\.){_OCTET}(?:\.{_OCTET}){{3}}(?::\d{{1,5}})?(?!\d|\.\d)")
#: A MAC: six hex pairs with one separator, or Cisco's three dotted quads.
MAC = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{2}([:-])(?:[0-9A-Fa-f]{2}\1){4}[0-9A-Fa-f]{2}"
                 r"(?![0-9A-Fa-f])"
                 r"|(?<![0-9A-Fa-f.])[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}(?![0-9A-Fa-f.])")
#: Text that may be an IPv6 literal (validated by `ipaddress` in `_ipv6_spans`).
_V6_CANDIDATE = re.compile(r"\[?[0-9A-Fa-f]*:[0-9A-Fa-f:.]*(?:%[A-Za-z0-9_.-]+)?\]?(?::\d{1,5})?")
#: A name on a home network.
LAN_HOST = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\."
                      r"(?:local|lan|home|internal|localdomain|intranet|corp|home\.arpa)"
                      r"(?![A-Za-z0-9-])", re.I)
#: Any dotted name with a letter in it (a hostname, as a broker line may print one).
DOTTED = re.compile(r"(?<![A-Za-z0-9_.-])[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\.?(?::\d{1,5})?"
                    r"(?![A-Za-z0-9_-])")
#: A name followed by a port (`host:51234`).
HOST_PORT = re.compile(r"(?<![A-Za-z0-9_.-])[A-Za-z][A-Za-z0-9-]*:\d{2,5}(?![\d.])")
#: The username slot of mosquitto's connect line (`u'name'`).
USERNAME = re.compile(r"\bu'[^']*'?")
_CONNECT = re.compile(r"^(?P<head>.*?connected from )(?P<addr>.*?)(?P<as> as )(?P<cid>\S+)"
                      r"(?P<tail>.*)$")
_CONNECT_FLAG = re.compile(r"[pck]\d+")
_NEW_CONNECTION = re.compile(r"(New connection from )\S+( on port )\d+")
_ON_PORT = re.compile(r"\b(on port )\d+")


def _ipv6_spans(text: str) -> list:
    """`[(start, end)]` of every IPv6 literal (bracketed, zoned or with a port too)."""
    spans = []
    for m in _V6_CANDIDATE.finditer(text):
        cand = m.group(0)
        core = cand.strip("[]")
        if core.count(":") < 2 or not re.search(r"[0-9A-Fa-f]", core):
            continue
        for attempt in (core, re.sub(r"\]?:\d{1,5}$", "", core)):
            addr = attempt.strip("[]").split("%", 1)[0]
            try:
                ipaddress.IPv6Address(addr)
            except ValueError:
                continue
            spans.append((m.start(), m.end()))
            break
    return spans


def identity_problems(text: str, *, broker_line: bool = False) -> list:
    """The kinds of identity left in `text` (never the values). `broker_line` adds the
    checks only a broker line needs: any dotted name, and the connect line's slots."""
    kinds = []
    if DEVICE_ID.search(text):
        kinds.append("a d_ id")
    if IPV4.search(text):
        kinds.append("an IPv4 address")
    if _ipv6_spans(text):
        kinds.append("an IPv6 address")
    if MAC.search(text):
        kinds.append("a MAC address")
    if LAN_HOST.search(text):
        kinds.append("a hostname")
    if broker_line:
        if any(re.search(r"[A-Za-z]", m.group(0)) for m in DOTTED.finditer(text)):
            kinds.append("a dotted name")
        if HOST_PORT.search(text):
            kinds.append("a host and port")
        if USERNAME.search(text):
            kinds.append("a username")
        m = _CONNECT.match(text)
        if m and m.group("addr") != "[address]":
            kinds.append("a connect line's address")
    return kinds


# --------------------------------------------------------------------------- #
# The --share copy
# --------------------------------------------------------------------------- #
#: Strings a --share copy keeps (after its d_ ids are replaced): names of things on the wire,
#: never what anyone said or typed. Every other non-empty string becomes "[text]".
KEEP_STRINGS = frozenset({
    # a turn and where it runs
    "command", "backend", "event_id", "source_event_id", "module_id", "content_id",
    "query", "subtopic", "context_type", "notify_source", "content_day", "ended_reason",
    "request_id", "event_name", "event_type", "tag", "level",
    # the reply's shape
    "output_type", "action", "function_id", "mood", "dialog_act", "emotion",
    "single_signal", "volley_signal", "request_source", "type", "state", "mode",
    "pairing_status", "category", "intents", "input_intents", "rules", "source", "id",
    "version", "active",
    # the robot's build
    "software_version", "robot_firmware_version", "android_version", "module_name",
    # the bus
    "proto", "protos", "uuid", "vad_name",
})
#: Removed from a --share copy: audio, raw binary, keys.
DROP_KEYS = frozenset({"audio_b64", "b64", "buffer", "audio_content", "public_key",
                       "user_id_encrypted", "rsa_pub"})
#: Withheld whole: the child's record.
PRIVATE_KEYS = frozenset({"child_pii", "child"})
#: Wall-clock fields inside a body (a number becomes 0, a string "[text]").
_TIME_KEY = re.compile(r"(?:^|_)timestamp$|_at$")
#: Where people's words live: never kept, whatever they look like (a child's "yes" or "6").
FREE_TEXT_KEYS = frozenset({"speech", "text", "markup", "message", "nickname", "name",
                            "transcript", "error_message", "value", "line", "alternatives",
                            "original_speech", "original_alternatives", "user_data", "prefix",
                            "entry_line", "module_description", "title", "summary"})
#: Short setting values that cannot carry a name or an address ("4", "on", "0.6"), kept
#: outside `FREE_TEXT_KEYS`.
_INERT = re.compile(r"-?\d{1,6}(?:\.\d{1,6})?|on|off|true|false", re.I)
_TOKEN = re.compile(r"[A-Za-z0-9_.:/+-]{1,128}")
_KEY = re.compile(r"[A-Za-z0-9_.:/+$@-]{1,128}")
_PROTO_NAME = re.compile(r"embodied\.[A-Za-z0-9_.]{1,200}")
SHARE_NOTE = ("A --share copy of a wire recording: no robot id, address, hostname, username, "
              "and no words (every free-text field is [text]). Safe to paste into an issue.")


class Share:
    """Builds a --share copy, one record at a time, in file order (so `d_robot-1` is the
    first robot the recording names)."""

    def __init__(self):
        self.ids: dict = {}

    def device(self, raw: str) -> str:
        if not raw:
            return ""
        key = raw.lower()
        if re.fullmatch(r"d_robot-\d+", key):
            return key
        if key not in self.ids:
            self.ids[key] = PLACEHOLDER.format(len(self.ids) + 1)
        return self.ids[key]

    def ids_in(self, text: str) -> str:
        return DEVICE_ID.sub(lambda m: self.device(m.group(0)), text)

    def log_line(self, text: str) -> str:
        """A broker line (or a note) with every address, port, MAC, hostname and username
        gone, and its d_ ids replaced."""
        text = self.ids_in(text)
        m = _CONNECT.match(text)
        if m:
            cid = m.group("cid")
            if not re.fullmatch(r"d_robot-\d+", cid) and (identity_problems(cid)
                                                          or not _TOKEN.fullmatch(cid)):
                cid = "[client]"
            inner = re.search(r"\((.*)\)", m.group("tail"))
            flags = [t.strip() for t in (inner.group(1).split(",") if inner else [])
                     if _CONNECT_FLAG.fullmatch(t.strip())]
            text = (f"{m.group('head')}[address]{m.group('as')}{cid}"
                    + (f" ({', '.join(flags)})." if flags else "."))
        text = _NEW_CONNECTION.sub(r"\1[address]\2[port]", text)
        text = USERNAME.sub("", text)
        text = MAC.sub("[mac]", text)
        for start, end in reversed(_ipv6_spans(text)):
            text = text[:start] + "[address]" + text[end:]
        text = IPV4.sub("[address]", text)
        text = DOTTED.sub(lambda m: "[host]" if re.search(r"[A-Za-z]", m.group(0))
                          else m.group(0), text)
        text = LAN_HOST.sub("[host]", text)
        text = HOST_PORT.sub("[host]", text)
        return _ON_PORT.sub(r"\1[port]", text)

    def string(self, value: str, key: str) -> str:
        if not value:
            return ""
        s = self.ids_in(value)
        if key == "speech" and s.strip() in VISION_EVENTS:
            return s.strip()                       # an event name, not someone's words
        if key in FREE_TEXT_KEYS:
            return TEXT
        if _INERT.fullmatch(s):
            return s
        if key in KEEP_STRINGS and _TOKEN.fullmatch(s) and not identity_problems(s):
            dotted_name = any(re.search(r"[A-Za-z]", m.group(0)) for m in DOTTED.finditer(s))
            if not dotted_name or (key in ("proto", "protos") and _PROTO_NAME.fullmatch(s)):
                return s
        return TEXT

    def value(self, v, key: str = ""):
        if isinstance(v, dict):
            out = {}
            for k, x in v.items():
                k2 = self.ids_in(str(k))
                if identity_problems(k2) or not _KEY.fullmatch(k2 or "_"):
                    k2 = f"[key-{len(out) + 1}]"
                if k in DROP_KEYS:
                    continue
                out[k2] = WITHHELD if k in PRIVATE_KEYS else self.value(x, k)
            return out
        if isinstance(v, list):
            return [self.value(x, key) for x in v]
        if isinstance(v, str):
            return TEXT if _TIME_KEY.search(key) and v else self.string(v, key)
        if isinstance(v, bool) or v is None:
            return v
        if isinstance(v, (int, float)):
            return 0 if _TIME_KEY.search(key) else v
        return TEXT

    def topic(self, topic: str) -> str:
        parts = self.ids_in(topic).split("/")
        return "/".join(p if not identity_problems(p) else "[segment]" for p in parts)

    def record(self, rec: dict):
        """The --share form of one recorded line, or None for a line it does not know."""
        kind = rec.get("kind")
        if kind == "banner":
            return {"kind": "banner", "format": rec.get("format"), "share": True,
                    "mono": 0.0, "note": SHARE_NOTE}
        out = {"kind": kind, "mono": rec.get("mono", 0.0)}
        if kind == "msg":
            direction = rec.get("dir", "")
            out.update({"dir": direction, "device": self.device(rec.get("device") or ""),
                        "topic": self.topic(rec.get("topic", "")),
                        "name": self.topic(rec.get("name", "")),
                        "bytes": rec.get("bytes", 0), "as": rec.get("as", "")})
            body = rec.get("body")
            if direction == BROKER:
                text = body.get("text", "") if isinstance(body, dict) else ""
                out["body"] = {"text": self.log_line(text)}
            else:
                out["body"] = self.value(body)
        elif kind == "note":
            out["text"] = self.log_line(str(rec.get("text", "")))
        elif kind == "dropped":
            out["count"] = rec.get("count", 0)
        elif kind == "stop":
            out.update({"reason": self.log_line(str(rec.get("reason", ""))),
                        "lines": rec.get("lines", 0), "dropped": rec.get("dropped", 0)})
        else:
            return None
        return out


def share_records(records) -> list:
    """The --share copy of a recording (idempotent: a share copy shares to itself)."""
    s = Share()
    return [r for r in (s.record(rec) for rec in records) if r is not None]


def share_problems(records) -> list:
    """`[(line number, kind)]` of identity left in a --share copy (empty means safe)."""
    problems = []
    for n, rec in enumerate(records, 1):
        for kind in identity_problems(dump(rec)):
            problems.append((n, kind))
        if rec.get("kind") == "msg" and rec.get("dir") == BROKER:
            text = (rec.get("body") or {}).get("text", "")
            for kind in identity_problems(text, broker_line=True):
                problems.append((n, kind))
    return problems


def dump(rec: dict) -> str:
    return json.dumps(rec, ensure_ascii=False, separators=(",", ":"), sort_keys=False)


def load(source) -> list:
    """The records of a recording: a path, `-` for stdin, or an iterable of lines."""
    if isinstance(source, str):
        fh = sys.stdin if source == "-" else open(source, encoding="utf-8")
        lines = list(fh)
        if fh is not sys.stdin:
            fh.close()
    else:
        lines = list(source)
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


# --------------------------------------------------------------------------- #
# The timeline
# --------------------------------------------------------------------------- #
_RESULT_NAMES = {int(code): code.name for code in ResultCode}
_PENDING = int(ResultCode.REPLY_PENDING)


def _s(t) -> str:
    return f"{float(t):.2f} s"


def _gap(a, b) -> str:
    return f"+{float(b) - float(a):.2f} s"


def _short(event_id) -> str:
    return str(event_id or "?")[:8]


def _closing(body: dict) -> bool:
    result = body.get("result")
    done = (body.get("consistency_control") or {}).get("is_completed")
    return done is True or (result is not None and int(result) != _PENDING)


def _reply_word(body: dict, t0: float, t: float) -> str:
    result = body.get("result")
    chunk = body.get("chunk_num")
    gap = _gap(t0, t)
    if result is not None and int(result) not in (0, _PENDING):
        word = f"result {int(result)} {_RESULT_NAMES.get(int(result), '?')}"
        return f"{word} {gap}" if chunk is None else f"chunk {chunk} {word} {gap}"
    if chunk is None:
        return f"done {gap}"
    return f"chunk {chunk} {'done' if _closing(body) else 'pending'} {gap}"


def _actions(body: dict) -> list:
    entries = body.get("response_actions") or []
    return [e["action"] for e in entries if isinstance(e, dict) and e.get("action")]


def _kind_of_request(body: dict) -> str:
    if body.get("command") == "notify":
        return "notify"
    if body.get("backend") == "data":
        return "data"
    if str(body.get("speech") or "").strip() in VISION_EVENTS:
        return "vision"
    return "turn"


def _robot_lines(dev: str, msgs: list) -> list:
    """`[(mono, label, text)]` for one robot."""
    out = []
    mine = [m for m in msgs if m["device"] == dev]
    up = [m for m in mine if m["dir"] == ROBOT_TO_CLOUD]
    down = [m for m in mine if m["dir"] == CLOUD_TO_ROBOT]

    def after(seq, t, pred):
        return next((m for m in seq if m["mono"] >= t and pred(m)), None)

    for m in mine:
        if m["dir"] == BROKER:
            out.append((m["mono"], "broker", m["body"].get("text", "")))

    states = [m for m in up if m["name"] == "state"]
    if states:
        body = states[0]["body"] if isinstance(states[0]["body"], dict) else {}
        facts = [f"{k} {body[k]}" for k in ("software_version", "mode", "battery_level")
                 if k in body and body[k] != TEXT]
        out.append((states[0]["mono"], "state",
                    "first /state" + (": " + ", ".join(facts) if facts else "")
                    + f" ({len(states)} in all)"))

    for m in down:
        if m["name"] != "config":
            continue
        body = m["body"] if isinstance(m["body"], dict) else {}
        nxt = after(up, m["mono"], lambda x: x["name"] == "state")
        tail = f"next /state {_gap(m['mono'], nxt['mono'])}" if nxt else "no /state after it"
        out.append((m["mono"], "config",
                    f"pushed (pairing_status {body.get('pairing_status', '?')}); {tail}"))

    def is_frame(x):
        return x["name"] == "zmq" and x["as"] in ("zmq", "zmq-json") and "vad" in x["body"]

    for m in down:
        if m["name"] == "zmq" and m["body"].get("proto", "").endswith("ProtoSubscribe"):
            names = ", ".join(p.rsplit(".", 1)[-1] for p in m["body"].get("protos", []))
            first = after(up, m["mono"], is_frame)
            tail = (f"first audio frame {_gap(m['mono'], first['mono'])}" if first
                    else "no audio after it")
            out.append((m["mono"], "ears", f"ProtoSubscribe {names}; {tail}"))

    # Utterances: START_OF_SPEECH opens one, END_OF_SPEECH closes it (stt.py's VADState).
    utterances, current = [], None
    for m in (x for x in up if is_frame(x)):
        vad = m["body"].get("vad")
        if vad == 1 or current is None:
            current = {"start": m["mono"], "end": None, "bytes": 0, "sq": 0.0, "uuid": ""}
            utterances.append(current)
        audio = m["body"].get("audio") or {}
        n = int(audio.get("bytes") or 0)
        current["bytes"] += n
        current["sq"] += float(audio.get("rms") or 0.0) ** 2 * (n / 2.0)
        current["uuid"] = current["uuid"] or m["body"].get("uuid", "")
        if vad == 3:
            current["end"] = m["mono"]
            current = None
    replies = [x for x in down if x["name"] == "zmq"
               and x["body"].get("proto", "").endswith("zmqSTTResponse")]
    for u in utterances:
        samples = u["bytes"] / 2.0
        rms = math.sqrt(u["sq"] / samples) if samples else 0.0
        head = f"{u['uuid'][:8] or '(no uuid)'}: {u['bytes'] / BYTES_PER_MS:.0f} ms, rms {rms:.4f}"
        if u["end"] is None:
            out.append((u["start"], "utterance", f"{head}; no END_OF_SPEECH"))
            continue
        heard = next((r for r in replies if r["mono"] >= u["end"]
                      and r["body"].get("uuid", "") in (u["uuid"], "")), None)
        if heard is None:
            tail = "no transcript"
        else:
            b = heard["body"]
            gap = _gap(u["end"], heard["mono"])
            if b.get("error_code"):
                tail = f"transcription failed (error_code {b['error_code']}) {gap}"
            elif b.get("speech"):
                tail = f"{b.get('type', '?')} with words {gap} after END_OF_SPEECH"
            else:
                tail = f"{b.get('type', '?')} empty (heard nothing) {gap} after END_OF_SPEECH"
        out.append((u["start"], "utterance", f"{head}; {tail}"))

    # Requests, replies, re-prompts, notifies, actions.
    requests = [m for m in up if m["name"].startswith("remote-chat")
                and isinstance(m["body"], dict)]
    chat = [m for m in down if m["name"] == "remote_chat" and isinstance(m["body"], dict)]
    kinds = [(m, _kind_of_request(m["body"])) for m in requests]
    asked = [m for m, k in kinds if k == "turn"]
    notifies: Counter = Counter()
    for m, k in kinds:
        if k != "notify":
            continue
        source = m["body"].get("source_event_id")
        owner = next((t for t in asked if source and t["body"].get("event_id") == source), None)
        if owner is None:
            earlier = [t for t in asked if t["mono"] <= m["mono"]]
            owner = earlier[-1] if earlier else None
        if owner is not None:
            notifies[id(owner)] += 1
    for m, k in kinds:
        if k == "notify":
            continue
        body = m["body"]
        eid = body.get("event_id")
        mine_replies = [r for r in chat if r["body"].get("event_id") == eid
                        and r["mono"] >= m["mono"]]
        words = [_reply_word(r["body"], m["mono"], r["mono"]) for r in mine_replies]
        module = f" (module {body['module_id']})" if body.get("module_id") else ""
        if k == "data":
            mods = next((len((r["body"].get("query_data") or {}).get("modules") or [])
                         for r in mine_replies if r["body"].get("query_data")), None)
            what = f"data query {_short(eid)}: " + (" · ".join(words) or "no reply")
            if mods is not None:
                what += f" ({mods} module{'' if mods == 1 else 's'})"
            out.append((m["mono"], "data", what))
            continue
        if k == "vision":
            out.append((m["mono"], "vision", f"{body.get('speech')}{module}: "
                        + (" · ".join(words) or "no reply")))
            continue
        text = f"{body.get('command', 'prompt')} {_short(eid)}{module}: "
        closing = next((r for r in mine_replies if _closing(r["body"])), None)
        later = next((t for t in asked if t["mono"] > m["mono"]), None)
        text += " · ".join(words) if words else "no reply"
        if later is not None and (closing is None or closing["mono"] > later["mono"]):
            text += (f"; the robot asked again {float(later['mono']) - float(m['mono']):.2f} s "
                     f"later ({later['body'].get('command', 'prompt')} "
                     f"{_short(later['body'].get('event_id'))}) before this turn closed")
        if notifies[id(m)]:
            n = notifies[id(m)]
            text += f"; {n} notify report{'' if n == 1 else 's'}"
        acts = [a for r in mine_replies for a in _actions(r["body"])]
        if acts:
            last = next(r for r in reversed(mine_replies) if _actions(r["body"]))
            nxt = next((x for x, kk in kinds if x["mono"] > last["mono"] and kk != "notify"),
                       None)
            if nxt is None:
                where = "no request after it"
            else:
                where = (f"next request {_gap(last['mono'], nxt['mono'])} in module "
                         f"{nxt['body'].get('module_id') or '(none)'}")
            text += f"; actions {', '.join(dict.fromkeys(acts))}; {where}"
        out.append((m["mono"], "turn", text))

    # A reply nobody asked for: whether the cloud may speak first is open (§4.7).
    asked_ids = {m["body"].get("event_id") for m in requests}
    for r in chat:
        if r["body"].get("event_id") not in asked_ids:
            out.append((r["mono"], "unasked", f"a reply to no request the robot sent (event "
                        f"{_short(r['body'].get('event_id'))}, result {r['body'].get('result')})"))

    # The activity log: the day plan and history queries (answered on `query_result`),
    # finished-activity reports, telehealth state.
    for m in up:
        body = m["body"] if isinstance(m["body"], dict) else {}
        if m["name"] != "client-service-activity-log":
            continue
        if body.get("subtopic") == "query" or "query" in body:
            rid, query = body.get("request_id"), body.get("query")

            def answers(x, rid=rid, query=query):
                b = x["body"] if isinstance(x["body"], dict) else {}
                if x["name"] != "query_result":
                    return False
                return b.get("request_id") == rid if rid not in (None, "", TEXT) \
                    else b.get("query") == query
            ans = after(down, m["mono"], answers)
            out.append((m["mono"], "activity", f"query {query}; " + (
                f"answered {_gap(m['mono'], ans['mono'])}" if ans else "no answer")))
        elif "mentor_behavior" in body:
            out.append((m["mono"], "activity", "a finished-activity report (mentor_behavior)"))
        else:
            out.append((m["mono"], "activity", f"subtopic {body.get('subtopic') or '(none)'}"))

    traffic = Counter((m["dir"], m["name"]) for m in mine if m["dir"] != BROKER)
    for direction in (ROBOT_TO_CLOUD, CLOUD_TO_ROBOT, "unknown"):
        names = sorted((n, c) for (d, n), c in traffic.items() if d == direction)
        if names:
            out.append((math.inf, "traffic", f"{direction}: "
                        + ", ".join(f"{n} x{c}" for n, c in names)))
    return out


def timeline(records) -> str:
    """The timeline text of a recording (computed from its --share view)."""
    recs = share_records(records)
    msgs = [r for r in recs if r["kind"] == "msg"]
    end = max((float(r.get("mono") or 0.0) for r in recs), default=0.0)
    robots = []
    for m in msgs:
        if m["device"] and m["device"] not in robots:
            robots.append(m["device"])
    out = [f"Wire timeline: {len(msgs)} messages over {end:.2f} s, {len(robots)} robot(s). "
           f"Shareable view: no identity, no words."]
    banner = next((r for r in records if r.get("kind") == "banner"), {})
    events = []
    for r in recs:
        if r["kind"] == "note":
            events.append(f"{_s(r['mono'])} {r['text']}")
        elif r["kind"] == "dropped":
            events.append(f"{_s(r['mono'])} dropped {r['count']} message(s): the writer fell "
                          f"behind")
        elif r["kind"] == "stop":
            events.append(f"{_s(r['mono'])} stopped: {r['reason']}")
    audio = "raw audio kept (--audio)" if banner.get("audio") else "no raw audio"
    out.append(f"Recorder ({audio}):")
    out += [f"  {e}" for e in events] or ["  (no notes)"]
    stray = [m for m in msgs if m["dir"] == BROKER and not m["device"]]
    if stray:
        out.append("Broker lines naming no robot:")
        out += [f"  {_s(m['mono'])} {m['body'].get('text', '')}" for m in stray]
    unknown = sorted({m["topic"] for m in msgs if m["dir"] == "unknown"})
    if unknown:
        out.append("Topics the recovered map does not name: " + ", ".join(unknown))
    for dev in robots:
        out.append("")
        out.append(dev)
        for t, label, text in sorted(_robot_lines(dev, msgs), key=lambda x: x[0]):
            when = "" if t == math.inf else _s(t)
            out.append(f"  {when:>9}  {label:<9}  {text}")
    return "\n".join(out) + "\n"


def write_share(records, out_path: str) -> list:
    """Write the --share copy of `records` to `out_path` (`-` for stdout). Refuses, writing
    nothing, when identity survived the scrub; returns the problems (empty when written)."""
    shared = share_records(records)
    problems = share_problems(shared)
    if problems:
        return problems
    text = "".join(dump(r) + "\n" for r in shared)
    if out_path == "-":
        sys.stdout.write(text)
    else:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python3 sim/tools/wire_timeline.py",
        description="Print a wire recording's timeline, or write its --share copy.")
    ap.add_argument("recording", help="a wire_record JSONL file, or - for stdin")
    ap.add_argument("--share", metavar="OUT",
                    help="write the copy that holds no identity and no words (- for stdout)")
    args = ap.parse_args(argv)
    records = load(args.recording)
    if not records:
        print("no records in that file", file=sys.stderr)
        return 1
    if args.share:
        problems = write_share(records, args.share)
        if problems:
            kinds = sorted({k for _, k in problems})
            print(f"refused: the shared copy would still hold {', '.join(kinds)} "
                  f"(lines {', '.join(str(n) for n, _ in problems[:10])}); nothing written",
                  file=sys.stderr)
            return 1
        if args.share != "-":
            print(f"wrote {args.share}: no robot id, address, hostname, username or words",
                  file=sys.stderr)
        return 0
    sys.stdout.write(timeline(records))
    return 0


if __name__ == "__main__":                                   # pragma: no cover - CLI
    sys.exit(main())
