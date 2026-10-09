"""
A fake MQTT supervisor for the parent-console round-trip suites (`test_console_*.py`).

The console's fleet/config/telemetry endpoints are thin proxies over the supervisor's
status server (`mqtt/supervisor/moxie_runtime/status_http.py`). `FakeSupervisor` speaks
that contract on a free port — same routes, payload shapes and status codes — with the
REAL `sanitize_config_overrides` behind /config and a REAL `MoxieRuntime` behind /safety,
/memory, /telehealth, /voice, /brain, /content, /conn, /wakeup and DELETE /telemetry. Only
/status, /telemetry (GET), /permits and /schedule are hand-built, and
`test_console_roundtrip.py::test_fake_status_server_matches_the_real_runtime_shapes`
diffs those against the real runtime so drift fails there instead of silently turning
the suites into tests of themselves.

Test modules import the `supervisor` and `client` fixtures from here (module-scoped).
"""
import json
import os
import socket
import sys
import threading

import pytest

from helpers_console import console_app

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "server"))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

_cloud_config = pytest.importorskip("moxie_sdk.cloud_config", reason="SDK not importable")
sanitize_config_overrides = _cloud_config.sanitize_config_overrides
merge_config_layers = _cloud_config.merge_config_layers
schedulable_module_ids = _cloud_config.schedulable_module_ids
_faces = pytest.importorskip("moxie_sdk.faces", reason="SDK not importable")
face_catalog = _faces.face_catalog

DEVICE = "d_console_rt"
DEAD = "http://127.0.0.1:1/status"     # nothing listens on port 1
CONV = "conversation:FREE_CHAT/default"


# --------------------------------------------------------------------------- #
# The hand-built payloads (drift-checked against the real runtime)
# --------------------------------------------------------------------------- #

def _snapshot(overrides: dict, fleet: dict = None) -> dict:
    """MoxieRuntime.status_snapshot() for one connected robot: a healthy, idle appliance.

    `broker_subscribed` is True because the robot below is `seen_since_connect` — a robot
    can only have been heard through a subscription that exists. `connection_health` is
    `steady` because `recovered` would imply an outage this appliance has not had."""
    fleet = dict(fleet or {})
    effective = merge_config_layers(fleet, overrides)
    face = effective.get("face")
    cache_id = (_faces.face_child_id(_faces.face_options_list(face), "Sam")
                if face else "")
    return {
        "ok": True, "app": "content", "uptime_s": 12,
        "brain": "content", "brain_pin": "",
        "fleet_config": fleet,
        "allow_unverified_bots": False,
        "pending_count": 0,
        "schedule_modules": list(schedulable_module_ids()),
        "broker_connected": True, "broker_subscribed": True,
        "last_broker_connect": 0.0, "last_broker_disconnect": 0.0,
        "last_connect_error": "",
        "publish_drops": 0, "store_lock_timeouts": 0,
        "roster": {"known": 1, "oldest_first_seen": 1.0, "newest_last_seen": 1.0},
        "connection_health": {"state": "steady", "outages": 0, "refusals": 0,
                              "drops": 0, "lock_timeouts": 0},
        "face_catalog": face_catalog(),
        "robots": [{
            "device_id": DEVICE, "child": "Sam", "firmware": "3.6.4",
            "permitted": True, "pending": False, "permit_label": "",
            "seen_since_connect": True,
            "stt_subscribed_at": None,          # when the mic was last asked for (/status)
            "stt_dropped": 0,                   # utterances the honest ears dropped
            "battery_level": 91, "audio_volume": 0.4, "wifi_ssid": "Home",
            "mode": "normal", "ota_reboot_required": False,
            "config_overrides": dict(overrides),
            "config_effective": effective,
            "face_cache_id": cache_id,
            # which brain answers THIS child, and which layer decided
            "brain": effective.get("brain") or "content",
            "brain_source": ("robot" if overrides.get("brain")
                             else ("fleet" if fleet.get("brain") else "default")),
            "telemetry_count": 2,
            "safety_total": 2, "safety_unreviewed": 1,
        }],
        "recent": [{"t": 1, "kind": "chat", "text": "hi"}],
    }


PACKETS = [
    {"event_name": "conversation_start", "recorded_at": 100, "moxie_session_id": "s1"},
    {"event_name": "conversation_start", "recorded_at": 140, "moxie_session_id": "s1"},
    {"event_name": "battery_low", "recorded_at": 120, "moxie_session_id": "s1"},
]

#: A real `telemetry_daily.json` roll-up: three days, one empty, so the week has a zero
#: day to render and the "history since" footer has something to state.
_ROLLUP = {
    "days": {
        "2026-08-31": {"count": 5, "by_event": {"conversation_start": 4, "battery_low": 1},
                       "first": 1756600000, "last": 1756620000},
        "2026-09-02": {"count": 3, "by_event": {"conversation_start": 2, "battery_low": 1},
                       "first": 100, "last": 140},
    },
    "total": 11, "dropped_days": 2, "updated_at": 1756800000,
}


def telemetry(device_id: str, limit: int, days: int = 7) -> tuple:
    """MoxieRuntime.telemetry_view() + its HTTP status, built from the same pure helpers
    the runtime uses (`moxie_sdk.telemetry`) over `_ROLLUP`."""
    if device_id != DEVICE:
        return {"ok": False, "device_id": device_id,
                "error": f"unknown device_id {device_id!r}"}, 404
    from moxie_sdk.telemetry import (history_view, retention, rollup_totals,
                                     summarize_events)
    summary = summarize_events(PACKETS, limit=limit)
    return {"ok": True, "device_id": device_id,
            "summary": summary, "events": summary["latest"],
            "policy": "NO_MEDIA", "persisted": True, "connected": True,
            "retention": retention(),
            "history": history_view(_ROLLUP, days=days, today="2026-09-02"),
            "totals": rollup_totals(_ROLLUP)}, 200


#: A real `GET /schedule` body captured from mosquitto + `mqtt/run.py` + the virtual
#: robot, trimmed to one entry per distinct case (untimed FTUE spine, daily fixture, a
#: parent request that drifted to a later slot, a scored pick, a chat breather).
#: Recorded rather than computed because `schedule_view` re-plans against the wall clock.
SCHEDULE = {
    "ok": True, "device_id": DEVICE, "day": "2026-09-02",
    "planned_at": "2026-09-02T08:23:20", "served": True,
    "schedule": {
        "provided_schedule": [
            {"module_id": "WELCOME"},
            {"module_id": "DM"},
            {"module_id": "STORYTELLING"},
            {"module_id": "FREE_CHAT", "content_id": "default"},
            {"module_id": "SCAVENGERHUNT"},
        ],
        "chat_request": {"module_id": "FREE_CHAT", "content_id": "default"},
    },
    "explanations": [
        {"module_id": "WELCOME", "slot": None, "at": None, "reason_codes": ["ftue"],
         "line": "Welcome is part of Moxie's first-week onboarding, which is still "
                 "running.", "score": None, "factors": {}},
        {"module_id": "DM", "slot": None, "at": None, "reason_codes": ["fixture"],
         "line": "Daily Missions is a daily fixture — it runs every day.",
         "score": None, "factors": {}},
        {"module_id": "STORYTELLING", "slot": 4, "at": "09:03",
         "reason_codes": ["parent_request", "unseen"],
         "line": "Requested by a parent for 8:43 am — this session starts later than "
                 "that, so Storytelling is queued at 9:03 am instead.",
         "score": 4164,
         "factors": {"affinity": 100, "category_spread": 0, "coverage": 0,
                     "parent_request": 4000, "recency": 0, "tiebreak": 4,
                     "time_of_day": 60}},
        {"module_id": "FREE_CHAT", "slot": None, "at": None, "reason_codes": ["chat"],
         "line": "A free chat, so friend gets a breather between activities.",
         "score": None, "factors": {}},
        {"module_id": "SCAVENGERHUNT", "slot": 5, "at": "09:13",
         "reason_codes": ["unseen", "time_of_day", "variety"],
         "line": "Friend has not tried Scavenger hunt yet — new for today in the "
                 "morning slot.", "score": 242,
         "factors": {"affinity": 100, "category_spread": 0, "coverage": 0, "recency": 0,
                     "tiebreak": 22, "time_of_day": 120}},
    ],
    "inputs": {
        "device_id": DEVICE, "day": "2026-09-02", "now": "2026-09-02T08:23:20",
        "bucket": "morning", "slot_minutes": 10, "child_name": "friend",
        "bedtime": {"enabled": True, "kind": "weekday", "starts_at": "09:23",
                    "ends_at": "17:23"},
        "slots": [{"index": 0, "at": "09:03", "bucket": "morning", "in_bedtime": False},
                  {"index": 1, "at": "09:13", "bucket": "morning", "in_bedtime": False},
                  {"index": 2, "at": "09:23", "bucket": "morning", "in_bedtime": True}],
        "parent_requests": [{"module_id": "STORYTELLING", "scheduled_at": 1788363799,
                             "at": "08:43", "due_today": True, "slot": 0}],
        "ftue_skips": [], "history": {},
        "telemetry": {"count": 0, "by_event": {}, "sessions": 0, "active_buckets": {},
                      "carries_module_signal": False,
                      "note": "Packet.event_name is a free string in the recovered "
                              "proto; no module launch/exit vocabulary is established, "
                              "so completion affinity comes from mentor_behaviors only."},
        "planned": {"entries": 5, "activities": 2, "requested": 6,
                    "dropped_for_bedtime": 4},
    },
}


def schedule(device_id: str) -> tuple:
    """MoxieRuntime.schedule_view() + the status code its HTTP layer answers with."""
    if device_id != DEVICE:
        return {"ok": False, "error": f"unknown device_id {device_id!r}"}, 404
    return dict(SCHEDULE), 200


# --------------------------------------------------------------------------- #
# The real runtime behind the fake, with only its outer seams stubbed
# --------------------------------------------------------------------------- #

class RecordingClient:
    """The supervisor's MQTT seam, recorded; nothing talks to a broker."""

    def __init__(self):
        self.published = []

    def publish(self, topic, payload):
        self.published.append((topic, json.loads(payload)))

    def on(self, topic):
        return [p for (t, p) in self.published if t == topic]


#: What `GET /v1/models` really served — the list the voice picker classifies.
_GATEWAY_MODELS = ["piper-amy", "piper-ryan", "graphling-tts-narrator", "stt-whisper",
                   "graphling-stt", "tts-piper-amy", "graphling-medium"]


class _ConsoleVoiceSynth:
    """A voice for the Test button: 22050 Hz mono, remembers what it said."""
    name = "console-fake"
    channels = 1
    sample_rate = 22050

    def __init__(self, choice):
        self.choice = dict(choice)
        self.spoken = []

    def describe(self):
        return "fake-voice (%s:%s)" % (self.choice["engine"], self.choice["model"])

    def synthesize(self, text, voice=None):
        self.spoken.append(text)
        return b"\x21\x43" * 64


class _ConsoleVoiceEngines:
    """`config.VoiceEngines` with a scripted listing and recording builders; the runtime
    verbs behind it (validation, persistence, engine swap) are the real ones."""

    #: What an explicit `MOXIE_TTS`/`MOXIE_STT` pins. Empty except in the pin test.
    pins: dict = {}

    def available(self, *, refresh=False, settle_s=0.0):
        from moxie_sdk import voice_settings as _vs
        return {"available": _vs.filter_available(
                    _vs.build_available(_GATEWAY_MODELS,
                                        piper_voices=["en_US-amy-medium"],
                                        whisper_models=["base.en"]), self.pins),
                "pins": dict(self.pins),
                "pin_notes": {k: _vs.pin_note(k, self.pins.get(k) or "")
                              for k in _vs.KINDS},
                "discovering": False, "gateway_error": ""}

    def build_speech(self, choice):
        return None if choice["engine"] == "off" else _ConsoleVoiceSynth(choice)

    def build_listening(self, choice):
        return None


#: The shipped content the fake appliance boots with (the `starter.json` shape).
CONTENT_MODULE = {
    "conversations": [{"name": "Free Chat", "module_id": "FREE_CHAT",
                       "content_id": "default", "source_version": 1,
                       "prompt": "You are Moxie, talking to Sam.", "opener": "Hi!"}],
    "globals": [{"name": "Timer", "pattern": r"timer for (\d+)", "entity_groups": "1"}],
}


def install_content(rt):
    """The two attributes `config.build_content_app()` records, which a bare MoxieApp
    lacks — so the real content verbs have a shipped baseline and a live module."""
    from moxie_sdk.content import packs as _packs
    rt.app.content_defaults = _packs.shipped_items(CONTENT_MODULE)
    rt.app.module = _packs.build_module(rt.app.content_defaults, {})


def bare_runtime(**kw):
    """A real `MoxieRuntime` over a bare `content` app, with this file's robot placed."""
    sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))
    import moxie_runtime
    from moxie_sdk.app import MoxieApp
    from moxie_sdk.types import ChildProfile, RobotContext

    class _App(MoxieApp):
        name = "content"

    rt = moxie_runtime.MoxieRuntime(app=_App(), child=ChildProfile(nickname="Sam"), **kw)
    rt.robots[DEVICE] = RobotContext(device_id=DEVICE, child=rt.child)
    return rt


def _safety_runtime(root: str):
    """The fake supervisor's backend. `allow_unverified_bots=True` because this robot is
    hand-placed rather than let in through the allowlist; the permit gate has its own
    tests (`test_device_permits.py`, `test_telehealth_runtime.py`)."""
    from moxie_sdk import safety as S
    from moxie_sdk.store import JsonStore
    rt = bare_runtime(allow_unverified_bots=True)
    rt.store = JsonStore(root=root)
    rt.client = RecordingClient()
    rt.set_voice_engines(_ConsoleVoiceEngines())
    install_content(rt)
    rt._record_safety(DEVICE, S.assess("I want to kill myself"))
    rt._record_safety(DEVICE, S.assess("this is bullshit"))
    return rt


#: The fixed instant the seeded memory was "learned", passed to `merge(now=…)` too so
#: decay never ages this fixture out as the calendar moves.
SEED_AT = 1788352646.0


def seed_memory(rt):
    """Two activities' worth of facts written through the REAL `MemoryStore`."""
    from moxie_sdk.content.memory import provenance
    mem = rt.memory_store()
    mem.merge(DEVICE, "mchat",
              {"facts": ["Sam has a beagle named Pepper", "Sam is in year 2"],
               "preferences": ["Likes drawing"],
               "summaries": ["They talked about pets."]},
              provenance=provenance(module_id="MCHAT", content_id="default",
                                    turns=4, reason="exit", clock=lambda: SEED_AT),
              meta={"summarized_through": 6}, now=SEED_AT)
    mem.merge(DEVICE, "free_chat", {"facts": ["Sam's favourite colour is red"]},
              provenance=provenance(module_id="FREE_CHAT", turns=2, reason="switch",
                                    clock=lambda: SEED_AT - 646.0),
              now=SEED_AT)
    return mem


def reseed(supervisor):
    """Put the shared memory fixture back the way the other tests expect to find it."""
    supervisor.runtime.erase_memory(DEVICE)
    seed_memory(supervisor.runtime)


# --------------------------------------------------------------------------- #
# The status server
# --------------------------------------------------------------------------- #

def _arg(q, key, default=""):
    return (q.get(key) or [default])[0]


def _int_arg(q, key, default):
    try:
        return int(_arg(q, key, str(default)))
    except ValueError:
        return default


def _code(out, refused=400):
    """200 / 404 for an unknown device / `refused` — how `status_http` maps a verb result."""
    if out.get("ok"):
        return 200
    return 404 if "unknown device_id" in str(out.get("error")) else refused


def _telehealth_verb(rt, device_id, body):
    """Dispatch one operator verb the way `_start_status_server` does."""
    action = str(body.get("action") or "").strip().lower()
    if action in ("enable", "disable"):
        return rt.telehealth_enable(device_id, action == "enable")
    for names, verb in ((("start", "start_session"), "START_SESSION"),
                        (("end", "end_session"), "END_SESSION"),
                        (("state", "update_state"), "UPDATE_STATE")):
        if action in names:
            return rt.telehealth_session(device_id, verb)
    if action in ("speak", "play_output", "say"):
        return rt.telehealth_speak(device_id, body.get("text") or "",
                                   mood=body.get("mood"), intensity=body.get("intensity"))
    if action == "interrupt":
        return rt.telehealth_interrupt(device_id)
    raise ValueError("expected action: enable, disable, start, end, state, speak or "
                     "interrupt")


class FakeSupervisor:
    """Serves the endpoints the console proxies, on a free ephemeral port, and records
    every query/post so a test can assert what the console actually forwarded."""

    def __init__(self, safety_root: str):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from urllib.parse import parse_qs, urlparse
        self.overrides: dict = {}
        self.fleet: dict = {}
        # the `{allow_unverified_bots, devices}` record MoxieRuntime.permits() normalizes
        self.permits: dict = {"allow_unverified_bots": False, "devices": {}}
        for log in ("permit_posts", "config_posts", "telemetry_queries",
                    "telemetry_erases", "safety_queries", "memory_queries",
                    "memory_erases", "memory_edits", "telehealth_queries",
                    "schedule_queries", "conn_queries", "voice_queries", "voice_posts",
                    "wakeups", "brain_posts"):
            setattr(self, log, [])
        self.runtime = rt = _safety_runtime(safety_root)
        self.memory = seed_memory(rt)
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _out(self, payload, code=200):
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def _404(self):
                self.send_response(404)
                self.end_headers()

            def _raw(self):
                return self.rfile.read(int(self.headers.get("Content-Length") or 0)) \
                    or b"{}"

            def do_GET(self):
                u = urlparse(self.path)
                q = parse_qs(u.query)
                device_id = _arg(q, "device_id")
                if u.path == "/status":
                    return self._out(_snapshot(outer.overrides, outer.fleet))
                if u.path == "/permits":
                    return self._out(outer.permits_view())
                if u.path == "/config":
                    if _arg(q, "scope", "robot") == "fleet":
                        return self._out({"ok": True, "scope": "fleet",
                                          "fleet_config": dict(outer.fleet)})
                    if device_id != DEVICE:
                        return self._out(
                            {"ok": False, "error": f"unknown device_id {device_id!r}"}, 404)
                    return self._out({
                        "ok": True, "scope": "robot", "device_id": device_id,
                        "fleet_config": dict(outer.fleet),
                        "config_overrides": dict(outer.overrides),
                        "config_effective": merge_config_layers(outer.fleet,
                                                                outer.overrides)})
                if u.path == "/telemetry":
                    limit = _int_arg(q, "limit", 20)
                    outer.telemetry_queries.append((device_id, limit))
                    return self._out(*telemetry(device_id, limit, _int_arg(q, "days", 7)))
                if u.path == "/safety":
                    limit = _int_arg(q, "limit", 20)
                    outer.safety_queries.append((device_id, limit))
                    out = rt.safety_view(device_id, limit=limit)
                    return self._out(out, 200 if out.get("ok") else 404)
                if u.path == "/memory":
                    outer.memory_queries.append(device_id)
                    out = rt.memory_view(device_id)
                    return self._out(out, 200 if out.get("ok") else 404)
                if u.path == "/conn":
                    outer.conn_queries.append(u.query)
                    return self._out(rt.conn_view(limit=_int_arg(q, "limit", 30)))
                if u.path == "/telehealth":
                    outer.telehealth_queries.append(device_id)
                    out = rt.telehealth_view(device_id)
                    return self._out(out, 200 if out.get("ok") else 404)
                if u.path == "/voice":
                    refresh = _arg(q, "refresh", "0")
                    outer.voice_queries.append(_arg(q, "refresh"))
                    return self._out(rt.voice_view(
                        refresh=refresh not in ("", "0", "false")))
                if u.path == "/brain":
                    return self._out(rt.brain_view())
                if u.path == "/content":
                    return self._out(rt.content_view())
                if u.path == "/content/export":
                    keys = [k for part in (q.get("items") or [])
                            for k in part.split(",") if k.strip()]
                    try:
                        return self._out(rt.content_export(
                            keys, name=_arg(q, "name"), pack_id=_arg(q, "id"),
                            details=_arg(q, "details"), author=_arg(q, "author")))
                    except Exception as e:
                        return self._out({"ok": False, "error": str(e),
                                          "reason": str(e)}, 400)
                if u.path == "/schedule":
                    outer.schedule_queries.append((device_id, _arg(q, "refresh")))
                    return self._out(*schedule(device_id))
                self._404()

            def do_DELETE(self):
                """`DELETE /memory?device_id=…[&namespace=…[&item=…]]` and
                `DELETE /telemetry?device_id=…`, both through the REAL runtime verbs."""
                u = urlparse(self.path)
                q = parse_qs(u.query)
                device_id = _arg(q, "device_id")
                if u.path == "/telemetry":
                    outer.telemetry_erases.append(device_id)
                    if not device_id:
                        return self._out({"ok": False,
                                          "error": "device_id is required"}, 400)
                    out = rt.erase_telemetry(device_id)
                    return self._out(out, 200 if out.get("ok") else 404)
                if u.path != "/memory":
                    return self._404()
                namespace, item = _arg(q, "namespace"), _arg(q, "item")
                outer.memory_erases.append(
                    (device_id, f"{namespace}/{item}" if item else (namespace or "all")))
                out = rt.erase_memory(device_id, namespace or None, item or None)
                return self._out(out, 200 if out.get("ok") else 404)

            def do_POST(self):
                u = urlparse(self.path)
                q = parse_qs(u.query)
                device_id = _arg(q, "device_id")
                if u.path == "/wakeup":
                    outer.wakeups.append(device_id)
                    out = rt.wake_robot(device_id)
                    return self._out(out, _code(out, 409))
                if u.path == "/brain":
                    body = json.loads(self._raw()) or {}
                    outer.brain_posts.append((u.query, body))
                    out = rt.brain_update(body, device_id=device_id,
                                          scope=_arg(q, "scope", "robot"))
                    return self._out(out, _code(out))
                if u.path in ("/voice", "/voice/test"):
                    body = json.loads(self._raw()) or {}
                    outer.voice_posts.append((u.path, body))
                    if u.path == "/voice/test":
                        out = rt.voice_test(device_id, body.get("text") or "")
                        return self._out(out, _code(out))
                    out = rt.voice_update(body)
                    return self._out(out, 200 if out.get("ok") else 400)
                if u.path in ("/content/review", "/content/import", "/content/undo"):
                    raw = self._raw()
                    try:
                        if u.path == "/content/undo":
                            out = rt.content_undo()
                            return self._out(out, 200 if out.get("ok") else 404)
                        if u.path == "/content/review":
                            return self._out(rt.content_review(raw), 200)
                        body = json.loads(raw) or {}
                        out = rt.content_import(body.get("pack"), body.get("accept") or [],
                                                str(body.get("expect_digest") or ""))
                        return self._out(out, 200 if out.get("ok") else
                                         409 if out.get("conflict") else 400)
                    except Exception as e:
                        return self._out({"ok": False, "error": str(e),
                                          "reason": str(e)}, 400)
                if u.path == "/telehealth":
                    body = json.loads(self._raw()) or {}
                    try:
                        out = _telehealth_verb(rt, device_id, body)
                        return self._out(out, _code(out))
                    except Exception as e:
                        return self._out({"ok": False, "error": str(e),
                                          "reason": str(e)}, 400)
                if u.path not in ("/config", "/safety", "/permits", "/memory"):
                    return self._404()
                raw = self._raw()
                if u.path == "/memory":
                    # `{"edit": {namespace, item, text}}` — the runtime re-checks safety
                    edit = (json.loads(raw) or {}).get("edit") or {}
                    args = (edit.get("namespace"), edit.get("item"), edit.get("text"))
                    outer.memory_edits.append((device_id,) + args)
                    try:
                        out = rt.edit_memory_item(device_id, *args)
                        return self._out(out, 200 if out.get("ok") else 404)
                    except Exception as e:
                        return self._out({"ok": False, "error": str(e)}, 400)
                if u.path == "/permits":
                    body = json.loads(raw) or {}
                    outer.permit_posts.append(body)
                    try:
                        outer.apply_permit(body)
                        return self._out(outer.permits_view(), 200)
                    except Exception as e:
                        return self._out({"ok": False, "error": str(e)}, 400)
                if u.path == "/safety":
                    out = rt.acknowledge_safety(device_id,
                                                (json.loads(raw) or {}).get("event_id"))
                    return self._out(out, 200 if out.get("ok") else 404)
                scope = _arg(q, "scope", "robot")
                outer.config_posts.append((device_id or f"scope={scope}", raw.decode()))
                try:
                    self._out(*outer.apply_config(scope, device_id, json.loads(raw)))
                except Exception as e:
                    self._out({"ok": False, "error": str(e)}, 400)

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))          # a genuinely free port; never stomps
        self.port = sock.getsockname()[1]
        sock.close()
        self._srv = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def apply_config(self, scope, device_id, body) -> tuple:
        """`POST /config[?scope=fleet]` through the REAL sanitizer."""
        applied = sanitize_config_overrides(body)
        if scope == "fleet":
            self.fleet.update(applied)
            return {"ok": True, "scope": "fleet", "applied": applied,
                    "fleet_config": dict(self.fleet), "robots": [DEVICE]}, 200
        if device_id != DEVICE:
            raise ValueError(f"unknown device_id {device_id!r}")
        self.overrides.update(applied)
        return {"ok": True, "scope": "robot", "device_id": device_id,
                "applied": applied, "config_overrides": dict(self.overrides),
                "config_effective": merge_config_layers(self.fleet, self.overrides)}, 200

    def apply_permit(self, body):
        if "allow_unverified_bots" in body:
            self.permits["allow_unverified_bots"] = bool(body["allow_unverified_bots"])
        elif body.get("device_id"):
            if body.get("permitted", True):
                self.permits["devices"][body["device_id"]] = {
                    "permitted_at": 1, "label": body.get("label") or ""}
            else:
                self.permits["devices"].pop(body["device_id"], None)
        else:
            raise ValueError("expected {device_id, permitted, label} "
                             "or {allow_unverified_bots}")

    def permits_view(self) -> dict:
        """MoxieRuntime.permits_view() for this fake's state."""
        allow = self.permits["allow_unverified_bots"]
        return {"ok": True,
                "allow_unverified_bots": allow, "allow_unverified_bots_stored": allow,
                "permits": [{"device_id": d, "permitted_at": v.get("permitted_at"),
                             "label": v.get("label") or ""}
                            for d, v in sorted(self.permits["devices"].items())],
                "pending": [] if (allow or DEVICE in self.permits["devices"]) else [DEVICE],
                "connected": [DEVICE]}

    @property
    def status_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/status"

    def close(self):
        self._srv.shutdown()
        self._srv.server_close()


# --------------------------------------------------------------------------- #
# fixtures (import them into the test module)
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def supervisor(tmp_path_factory):
    s = FakeSupervisor(str(tmp_path_factory.mktemp("safety-journal")))
    yield s
    s.close()


@pytest.fixture(scope="module")
def client(supervisor, tmp_path_factory):
    """The console app in-process, pointed at the fake supervisor."""
    TestClient, main = console_app(tmp_path_factory.mktemp("console") / "console-test.db",
                                   supervisor.status_url)
    with TestClient(main.app) as c:
        yield c


def static(client, path):
    r = client.get(path)
    assert r.status_code == 200, f"{path} is not being served"
    return r.text


def console_js_served(client):
    """Every script index.html loads, fetched through the app."""
    import re
    html = static(client, "/index.html")
    return "\n".join(static(client, src) for src in re.findall(r'<script src="([^"]+)"', html))


def quicklogin(client, email):
    tok = client.post("/local/quicklogin", json={"email": email}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}
