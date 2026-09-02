"""
Parent-console <-> supervisor round trip — the console↔runtime contract, end to end.

The console's fleet/config/telemetry endpoints are thin proxies: they HTTP-call the
MQTT supervisor's little status server, normalize what comes back, and hand it to the
UI. `test_fleet.py` unit-tests the normalizers and `test_telemetry.py` the roll-up,
but nothing until now exercised the *seam* — the URL the console builds, the query
string it appends, the method and body it forwards, and what it does with a 400 or a
404 from the other side.

So: stand a tiny status server on a free port that speaks exactly what
`mqtt/supervisor/moxie_runtime.py`'s `_start_status_server` speaks (GET /status,
GET /telemetry, GET+POST /safety, GET+DELETE /memory, POST /config — same payload
shapes, same status codes, and the REAL `sanitize_config_overrides` behind /config so
validation is not mocked away, the REAL `MoxieRuntime.safety_view`/`acknowledge_safety`
behind /safety and the REAL `memory_view`/`erase_memory` behind /memory), point
`MOXIE_SUPERVISOR_STATUS` at it, and drive the FastAPI app in-process. No broker, no
robot, no gateway — but a genuine two-process contract.

`test_fake_status_server_matches_the_real_runtime_shapes` guards the obvious risk of
a hand-written double: it diffs the fake's payload keys against the real
`MoxieRuntime.status_snapshot()` / `telemetry_view()`, so runtime drift fails here
instead of silently making this suite meaningless.

Skips cleanly when fastapi/httpx (or the server's own deps) are absent — CI's test
env has neither.
"""
import json
import os
import socket
import sys
import threading

import pytest

pytest.importorskip("fastapi", reason="console tests need fastapi")
pytest.importorskip("httpx", reason="console tests need httpx (fastapi TestClient)")

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "server"))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

_cloud_config = pytest.importorskip("moxie_sdk.cloud_config", reason="SDK not importable")
sanitize_config_overrides = _cloud_config.sanitize_config_overrides
merge_config_layers = _cloud_config.merge_config_layers
schedulable_module_ids = _cloud_config.schedulable_module_ids
_faces = pytest.importorskip("moxie_sdk.faces", reason="SDK not importable")
face_catalog = _faces.face_catalog
face_child_id = _faces.face_child_id
face_options_list = _faces.face_options_list

DEVICE = "d_console_rt"


# --------------------------------------------------------------------------- #
# The fake supervisor: MoxieRuntime._start_status_server's contract, no broker.
# --------------------------------------------------------------------------- #

def _snapshot(overrides: dict, fleet: dict = None) -> dict:
    """MoxieRuntime.status_snapshot() for one connected robot."""
    fleet = dict(fleet or {})
    effective = merge_config_layers(fleet, overrides)
    face = effective.get("face")
    cache_id = face_child_id(face_options_list(face), "Sam") if face else ""
    return {
        "ok": True, "app": "content", "uptime_s": 12,
        "fleet_config": fleet,
        "allow_unverified_bots": False,
        "pending_count": 0,
        "schedule_modules": list(schedulable_module_ids()),
        "face_catalog": face_catalog(),
        "robots": [{
            "device_id": DEVICE, "child": "Sam", "firmware": "3.6.4",
            "permitted": True, "pending": False, "permit_label": "",
            "battery_level": 91, "audio_volume": 0.4, "wifi_ssid": "Home",
            "mode": "normal", "ota_reboot_required": False,
            "config_overrides": dict(overrides),
            "config_effective": effective,
            "face_cache_id": cache_id,
            "telemetry_count": 2,
            "safety_total": 2, "safety_unreviewed": 1,
        }],
        "recent": [{"t": 1, "kind": "chat", "text": "hi"}],
    }


_PACKETS = [
    {"event_name": "conversation_start", "recorded_at": 100, "moxie_session_id": "s1"},
    {"event_name": "conversation_start", "recorded_at": 140, "moxie_session_id": "s1"},
    {"event_name": "battery_low", "recorded_at": 120, "moxie_session_id": "s1"},
]


def _telemetry(device_id: str, limit: int) -> tuple:
    """MoxieRuntime.telemetry_view() + the status code its HTTP layer answers with."""
    if device_id != DEVICE:
        return {"ok": False, "device_id": device_id,
                "error": f"unknown device_id {device_id!r}"}, 404
    from moxie_sdk.telemetry import summarize_events
    summary = summarize_events(_PACKETS, limit=limit)
    return {"ok": True, "device_id": device_id,
            "summary": summary, "events": summary["latest"]}, 200


#: 📅 A REAL `GET /schedule?device_id=…` body, captured 2026-09-02 from a real mosquitto +
#: `mqtt/run.py` + `sim/virtual_moxie.py --query schedule` on free ports, with a fleet
#: bedtime an hour out and one `ParentRequest` — then re-keyed to this file's device and
#: trimmed to the entries that carry a distinct case (an FTUE spine with no clock time, a
#: daily fixture, a parent request that drifted to a later slot, a scored pick, a chat
#: breather). `test_fake_status_server_matches_the_real_runtime_shapes` diffs its keys
#: against the live `MoxieRuntime.schedule_view`, so runtime drift fails there.
#:
#: Recorded rather than computed on purpose: `schedule_view` re-plans against the wall
#: clock, so a live call here would make these assertions depend on the hour CI runs at.
_SCHEDULE = {
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
         "line": "Daily Missions is a daily fixture \u2014 it runs every day.",
         "score": None, "factors": {}},
        {"module_id": "STORYTELLING", "slot": 4, "at": "09:03",
         "reason_codes": ["parent_request", "unseen"],
         "line": "Requested by a parent for 8:43 am \u2014 this session starts later than "
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
         "line": "Friend has not tried Scavenger hunt yet \u2014 new for today in the "
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


def _schedule(device_id: str) -> tuple:
    """MoxieRuntime.schedule_view() + the status code its HTTP layer answers with."""
    if device_id != DEVICE:
        return {"ok": False, "error": f"unknown device_id {device_id!r}"}, 404
    return dict(_SCHEDULE), 200


class _RecordingClient:
    """The supervisor's MQTT seam, recorded. `/config` and `commands/telehealth` publishes
    are what the 🎭 round-trip proves reached the wire; nothing here talks to a broker."""

    def __init__(self):
        self.published = []

    def publish(self, topic, payload):
        self.published.append((topic, json.loads(payload)))

    def on(self, topic):
        return [p for (t, p) in self.published if t == topic]


def _safety_runtime(root: str):
    """A REAL `MoxieRuntime` with a couple of recorded verdicts, used as the /safety
    backend of the fake supervisor — so the queue the console reads is the queue the
    runtime actually writes, not a hand-drawn copy of it."""
    sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))
    import moxie_runtime
    from moxie_sdk import safety as S
    from moxie_sdk.app import MoxieApp
    from moxie_sdk.store import JsonStore
    from moxie_sdk.types import ChildProfile, RobotContext

    class _App(MoxieApp):
        name = "content"

    # `allow_unverified_bots=True` for the same reason `helpers_runtime.make_runtime`
    # pins it: this file's robot is hand-placed rather than let in through the allowlist,
    # and the 🎭 telehealth verbs (which DO check the gate) would otherwise refuse it.
    # The gate has its own tests — `test_device_permits.py`, and the pending-robot cases
    # in `test_telehealth_runtime.py`.
    rt = moxie_runtime.MoxieRuntime(app=_App(), child=ChildProfile(nickname="Sam"),
                                    allow_unverified_bots=True)
    rt.store = JsonStore(root=root)
    rt.robots[DEVICE] = RobotContext(device_id=DEVICE, child=rt.child)
    rt.client = _RecordingClient()
    rt._record_safety(DEVICE, S.assess("I want to kill myself"))
    rt._record_safety(DEVICE, S.assess("this is bullshit"))
    return rt


#: The fixed instant the seeded memory was "learned". Passed to `merge(now=…)` as well as
#: to the provenance clock, so decay judges these items against the day they were written
#: rather than against whatever today is — otherwise this fixture would quietly age out
#: 90 days from now and take the whole memory section of this file with it.
_SEED_AT = 1788352646.0


def _reseed(supervisor):
    """Put the shared fixture back the way the other tests expect to find it."""
    supervisor.runtime.erase_memory(DEVICE)
    _seed_memory(supervisor.runtime)


def _seed_memory(rt):
    """Write two activities' worth of durable facts through the REAL `MemoryStore` the
    runtime serves `/memory` from — so the console reads the shape the store actually
    writes (namespaced lists of `{id, text, _provenance}` items + a `_provenance` log),
    never a hand-drawn copy of it."""
    from moxie_sdk.content.memory import provenance
    mem = rt.memory_store()
    mem.merge(DEVICE, "mchat",
              {"facts": ["Sam has a beagle named Pepper", "Sam is in year 2"],
               "preferences": ["Likes drawing"],
               "summaries": ["They talked about pets."]},
              provenance=provenance(module_id="MCHAT", content_id="default",
                                    turns=4, reason="exit", clock=lambda: _SEED_AT),
              meta={"summarized_through": 6}, now=_SEED_AT)
    mem.merge(DEVICE, "free_chat", {"facts": ["Sam's favourite colour is red"]},
              provenance=provenance(module_id="FREE_CHAT", turns=2, reason="switch",
                                    clock=lambda: _SEED_AT - 646.0),
              now=_SEED_AT)
    return mem


class FakeSupervisor:
    """Serves the endpoints the console proxies, on a free ephemeral port."""

    def __init__(self, safety_root: str):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from urllib.parse import parse_qs, urlparse
        self.overrides: dict = {}
        self.fleet: dict = {}
        # The device allowlist the console's 🔐 Robot access card drives — the same
        # `{allow_unverified_bots, devices}` record MoxieRuntime.permits() normalizes.
        self.permits: dict = {"allow_unverified_bots": False, "devices": {}}
        self.permit_posts: list = []
        self.config_posts: list = []
        self.telemetry_queries: list = []
        self.safety_queries: list = []
        self.memory_queries: list = []
        self.memory_erases: list = []
        self.memory_edits: list = []
        self.telehealth_queries: list = []
        self.schedule_queries: list = []
        self.telehealth_posts: list = []
        self.runtime = _safety_runtime(safety_root)
        self.memory = _seed_memory(self.runtime)
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

            def do_GET(self):
                u = urlparse(self.path)
                if u.path == "/status":
                    return self._out(_snapshot(outer.overrides, outer.fleet))
                if u.path == "/permits":
                    return self._out(outer.permits_view())
                if u.path == "/config":
                    q = parse_qs(u.query)
                    if (q.get("scope") or ["robot"])[0] == "fleet":
                        return self._out({"ok": True, "scope": "fleet",
                                          "fleet_config": dict(outer.fleet)})
                    device_id = (q.get("device_id") or [""])[0]
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
                    q = parse_qs(u.query)
                    device_id = (q.get("device_id") or [""])[0]
                    try:
                        limit = int((q.get("limit") or ["20"])[0])
                    except ValueError:
                        limit = 20
                    outer.telemetry_queries.append((device_id, limit))
                    return self._out(*_telemetry(device_id, limit))
                if u.path == "/safety":
                    q = parse_qs(u.query)
                    device_id = (q.get("device_id") or [""])[0]
                    try:
                        limit = int((q.get("limit") or ["20"])[0])
                    except ValueError:
                        limit = 20
                    outer.safety_queries.append((device_id, limit))
                    out = outer.runtime.safety_view(device_id, limit=limit)
                    return self._out(out, 200 if out.get("ok") else 404)
                if u.path == "/memory":
                    q = parse_qs(u.query)
                    device_id = (q.get("device_id") or [""])[0]
                    outer.memory_queries.append(device_id)
                    out = outer.runtime.memory_view(device_id)
                    return self._out(out, 200 if out.get("ok") else 404)
                if u.path == "/telehealth":
                    q = parse_qs(u.query)
                    device_id = (q.get("device_id") or [""])[0]
                    outer.telehealth_queries.append(device_id)
                    out = outer.runtime.telehealth_view(device_id)
                    return self._out(out, 200 if out.get("ok") else 404)
                if u.path == "/schedule":
                    # 📅 Recorded, not re-planned: `schedule_view` reads the wall clock,
                    # and a live call would make the console's assertions depend on the
                    # hour CI runs at. The keys are diffed against the real runtime in
                    # `test_fake_status_server_matches_the_real_runtime_shapes`.
                    q = parse_qs(u.query)
                    device_id = (q.get("device_id") or [""])[0]
                    outer.schedule_queries.append(
                        (device_id, (q.get("refresh") or [""])[0]))
                    return self._out(*_schedule(device_id))
                self.send_response(404)
                self.end_headers()

            def do_DELETE(self):
                """`DELETE /memory?device_id=…[&namespace=…[&item=…]]` — the parent's
                erase, finest cut first: one item, one activity, or everything."""
                u = urlparse(self.path)
                if u.path != "/memory":
                    self.send_response(404)
                    self.end_headers()
                    return
                q = parse_qs(u.query)
                device_id = (q.get("device_id") or [""])[0]
                namespace = (q.get("namespace") or [""])[0]
                item = (q.get("item") or [""])[0]
                outer.memory_erases.append(
                    (device_id, f"{namespace}/{item}" if item else (namespace or "all")))
                out = outer.runtime.erase_memory(device_id, namespace or None,
                                                 item or None)
                return self._out(out, 200 if out.get("ok") else 404)

            def do_POST(self):
                u = urlparse(self.path)
                if u.path == "/telehealth":
                    # 🎭 One operator verb, dispatched exactly the way the real
                    # `_start_status_server` dispatches it — the REAL runtime does the
                    # permit check, the mode gate, the safety classifier and the publish.
                    device_id = (parse_qs(u.query).get("device_id") or [""])[0]
                    raw = self.rfile.read(
                        int(self.headers.get("Content-Length") or 0)) or b"{}"
                    body = json.loads(raw or b"{}") or {}
                    outer.telehealth_posts.append((device_id, body))
                    action = str(body.get("action") or "").strip().lower()
                    rt = outer.runtime
                    try:
                        if action in ("enable", "disable"):
                            out = rt.telehealth_enable(device_id, action == "enable")
                        elif action in ("start", "start_session"):
                            out = rt.telehealth_session(device_id, "START_SESSION")
                        elif action in ("end", "end_session"):
                            out = rt.telehealth_session(device_id, "END_SESSION")
                        elif action in ("state", "update_state"):
                            out = rt.telehealth_session(device_id, "UPDATE_STATE")
                        elif action in ("speak", "play_output", "say"):
                            out = rt.telehealth_speak(
                                device_id, body.get("text") or "", mood=body.get("mood"),
                                intensity=body.get("intensity"))
                        elif action == "interrupt":
                            out = rt.telehealth_interrupt(device_id)
                        else:
                            raise ValueError("expected action: enable, disable, start, "
                                             "end, state, speak or interrupt")
                        code = (200 if out.get("ok") else
                                404 if "unknown device_id" in str(out.get("error"))
                                else 400)
                    except Exception as e:
                        out, code = {"ok": False, "error": str(e), "reason": str(e)}, 400
                    return self._out(out, code)
                if u.path not in ("/config", "/safety", "/permits", "/memory"):
                    self.send_response(404)
                    self.end_headers()
                    return
                device_id = (parse_qs(u.query).get("device_id") or [""])[0]
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}"
                if u.path == "/memory":
                    # `{"edit": {namespace, item, text}}` — a parent correcting one line.
                    # The REAL runtime does the work (and the safety re-check), like GET.
                    edit = (json.loads(raw or b"{}") or {}).get("edit") or {}
                    outer.memory_edits.append((device_id, edit.get("namespace"),
                                               edit.get("item"), edit.get("text")))
                    try:
                        out = outer.runtime.edit_memory_item(
                            device_id, edit.get("namespace"), edit.get("item"),
                            edit.get("text"))
                        code = 200 if out.get("ok") else 404
                    except Exception as e:
                        out, code = {"ok": False, "error": str(e)}, 400
                    return self._out(out, code)
                if u.path == "/permits":
                    body = json.loads(raw or b"{}") or {}
                    outer.permit_posts.append(body)
                    try:
                        if "allow_unverified_bots" in body:
                            outer.permits["allow_unverified_bots"] = bool(
                                body["allow_unverified_bots"])
                        elif body.get("device_id"):
                            if body.get("permitted", True):
                                outer.permits["devices"][body["device_id"]] = {
                                    "permitted_at": 1, "label": body.get("label") or ""}
                            else:
                                outer.permits["devices"].pop(body["device_id"], None)
                        else:
                            raise ValueError("expected {device_id, permitted, label} "
                                             "or {allow_unverified_bots}")
                        return self._out(outer.permits_view(), 200)
                    except Exception as e:
                        return self._out({"ok": False, "error": str(e)}, 400)
                if u.path == "/safety":
                    body = json.loads(raw or b"{}") or {}
                    out = outer.runtime.acknowledge_safety(device_id, body.get("event_id"))
                    return self._out(out, 200 if out.get("ok") else 404)
                scope = (parse_qs(u.query).get("scope") or ["robot"])[0]
                outer.config_posts.append((device_id or f"scope={scope}", raw.decode()))
                try:
                    applied = sanitize_config_overrides(json.loads(raw))
                    if scope == "fleet":
                        outer.fleet.update(applied)
                        return self._out({"ok": True, "scope": "fleet",
                                          "applied": applied,
                                          "fleet_config": dict(outer.fleet),
                                          "robots": [DEVICE]}, 200)
                    if device_id != DEVICE:
                        raise ValueError(f"unknown device_id {device_id!r}")
                    outer.overrides.update(applied)
                    return self._out({"ok": True, "scope": "robot",
                                      "device_id": device_id,
                                      "applied": applied,
                                      "config_overrides": dict(outer.overrides),
                                      "config_effective": merge_config_layers(
                                          outer.fleet, outer.overrides)}, 200)
                except Exception as e:
                    return self._out({"ok": False, "error": str(e)}, 400)

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))          # a genuinely free port; never stomps
        self.port = sock.getsockname()[1]
        sock.close()
        self._srv = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

    def permits_view(self) -> dict:
        """MoxieRuntime.permits_view() for this fake's state."""
        return {"ok": True,
                "allow_unverified_bots": self.permits["allow_unverified_bots"],
                "allow_unverified_bots_stored": self.permits["allow_unverified_bots"],
                "permits": [{"device_id": d, "permitted_at": v.get("permitted_at"),
                             "label": v.get("label") or ""}
                            for d, v in sorted(self.permits["devices"].items())],
                "pending": [] if (self.permits["allow_unverified_bots"]
                                  or DEVICE in self.permits["devices"]) else [DEVICE],
                "connected": [DEVICE]}

    @property
    def status_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/status"

    def close(self):
        self._srv.shutdown()
        self._srv.server_close()


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def supervisor(tmp_path_factory):
    s = FakeSupervisor(str(tmp_path_factory.mktemp("safety-journal")))
    yield s
    s.close()


@pytest.fixture(scope="module")
def client(supervisor, tmp_path_factory):
    """The console app in-process, pointed at the fake supervisor.

    `MOXIE_DB` is redirected first: `moxie_server.main` calls `db.init()` at import
    time and would otherwise create `server/moxie.db` in the working tree.
    """
    os.environ["MOXIE_DB"] = str(tmp_path_factory.mktemp("console") / "console-test.db")
    os.environ["MOXIE_SUPERVISOR_STATUS"] = supervisor.status_url
    try:
        from fastapi.testclient import TestClient
        from moxie_server import main
    except Exception as e:                      # pynacl/segno/... not in this env
        pytest.skip(f"console app not importable: {e}")
    # main.STATUS_URL is read from the env at import; set it explicitly too, so the
    # test is correct even if another test imported the module first.
    main.STATUS_URL = supervisor.status_url
    with TestClient(main.app) as c:
        yield c


# --------------------------------------------------------------------------- #
# GET /local/fleet
# --------------------------------------------------------------------------- #

def test_fleet_normalizes_the_supervisors_snapshot(client):
    r = client.get("/local/fleet")
    assert r.status_code == 200
    f = r.json()
    assert f["ok"] is True and f["app"] == "content" and f["robot_count"] == 1
    robot = f["robots"][0]
    assert robot["device_id"] == DEVICE and robot["child"] == "Sam"
    assert robot["online"] is True and robot["firmware"] == "3.6.4"
    assert robot["battery_level"] == 91 and robot["wifi_ssid"] == "Home"
    assert robot["telemetry_count"] == 2 and robot["summary"]
    assert "battery 91%" in robot["summary"]
    assert f["recent"] == [{"t": 1, "kind": "chat", "text": "hi"}]
    assert f["error"] is None


def test_fleet_is_graceful_when_the_supervisor_is_down(client, supervisor, monkeypatch):
    """Nothing listening on the status URL → ok:false with an empty fleet, never a 500."""
    from moxie_server import main
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    port = dead.getsockname()[1]
    dead.close()
    monkeypatch.setattr(main, "STATUS_URL", f"http://127.0.0.1:{port}/status")
    f = client.get("/local/fleet").json()
    assert f["ok"] is False and f["robots"] == [] and f["robot_count"] == 0
    assert f["error"]


# --------------------------------------------------------------------------- #
# POST /local/robots/{id}/config
# --------------------------------------------------------------------------- #

def test_config_edit_forwards_and_returns_the_applied_overrides(client, supervisor):
    r = client.post(f"/local/robots/{DEVICE}/config",
                    json={"audio_volume": 60, "weekday_bedtime": ["20:30", "07:00"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["device_id"] == DEVICE
    # the runtime's own sanitizer ran: a 0-100 slider became a 0-1 float
    assert body["applied"]["audio_volume"] == pytest.approx(0.6)
    assert body["config_overrides"]["audio_volume"] == pytest.approx(0.6)
    assert "weekday_bedtime" in body["applied"]
    # the console really forwarded the device id + the raw body, not a rewrite of it
    device_id, raw = supervisor.config_posts[-1]
    assert device_id == DEVICE
    assert json.loads(raw) == {"audio_volume": 60,
                               "weekday_bedtime": ["20:30", "07:00"]}


def test_a_config_edit_shows_up_in_the_next_fleet_read(client):
    """The full loop: console POST → supervisor stores → console GET sees it."""
    f = client.get("/local/fleet").json()
    assert f["robots"][0]["config_overrides"]["audio_volume"] == pytest.approx(0.6)


def test_bad_config_input_surfaces_the_supervisors_400(client):
    """A value the runtime's sanitizer rejects must reach the parent as a 400 with the
    reason — not a 200 that silently dropped the edit."""
    r = client.post(f"/local/robots/{DEVICE}/config", json={"audio_wake_set": "maybe"})
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["ok"] is False and "audio_wake_set" in body["error"]


def test_config_for_an_unknown_device_is_a_400(client):
    r = client.post("/local/robots/d_nope/config", json={"audio_volume": 50})
    assert r.status_code == 400, r.text
    assert r.json()["ok"] is False


def test_an_alarm_edit_round_trips_in_the_recovered_wakeschedule_shape(client, supervisor):
    """The console's weekday checkboxes + time → `WakeSchedule` on the wire, and back out
    of the next fleet read in the same shape the robot was pushed."""
    r = client.post(f"/local/robots/{DEVICE}/config",
                    json={"alarms": {"wakes": [{"days": ["mon", 6], "time": "07:15"}],
                                     "enabled": True}})
    assert r.status_code == 200, r.text
    assert r.json()["applied"]["alarms"] == {
        "wakes": [{"days": [0, 6], "time": "07:15"}], "enabled": True}
    f = client.get("/local/fleet").json()
    assert f["robots"][0]["config_effective"]["alarms"]["wakes"][0]["days"] == [0, 6]


# --------------------------------------------------------------------------- #
# POST /local/fleet/config — appliance-wide defaults (audit ADOPT #6)
# --------------------------------------------------------------------------- #

def test_fleet_config_edit_forwards_with_scope_fleet(client, supervisor):
    r = client.post("/local/fleet/config", json={"timezone_id": "America/Chicago"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["scope"] == "fleet"
    assert body["fleet_config"]["timezone_id"] == "America/Chicago"
    # the console forwarded the raw body to the *fleet* scope, not to a device
    where, raw = supervisor.config_posts[-1]
    assert where == "scope=fleet"
    assert json.loads(raw) == {"timezone_id": "America/Chicago"}


def test_the_fleet_default_shows_up_in_every_robots_effective_config(client):
    f = client.get("/local/fleet").json()
    assert f["fleet_config"]["timezone_id"] == "America/Chicago"
    robot = f["robots"][0]
    assert robot["config_effective"]["timezone_id"] == "America/Chicago"
    assert "timezone_id" not in robot["config_overrides"]        # inherited, not local
    assert robot["config_sources"]["timezone_id"] == "fleet"
    assert robot["config_sources"]["audio_volume"] == "robot"    # set per robot earlier


def test_a_per_robot_override_beats_the_fleet_default_through_the_console(client):
    client.post("/local/fleet/config", json={"audio_volume": 20})
    client.post(f"/local/robots/{DEVICE}/config", json={"audio_volume": 90})
    f = client.get("/local/fleet").json()
    assert f["fleet_config"]["audio_volume"] == pytest.approx(0.2)
    assert f["robots"][0]["config_effective"]["audio_volume"] == pytest.approx(0.9)


def test_bad_fleet_config_input_is_a_400(client):
    r = client.post("/local/fleet/config",
                    json={"alarms": [{"days": ["funday"], "time": "07:00"}]})
    assert r.status_code == 400, r.text
    assert r.json()["ok"] is False


def test_the_console_learns_the_module_catalog_from_the_supervisor(client):
    """The activity picker's options come from the one on-board catalog, never a copy."""
    f = client.get("/local/fleet").json()
    assert "JOKE" in f["schedule_modules"]
    assert set(f["schedule_modules"]) == set(schedulable_module_ids())


# --------------------------------------------------------------------------- #
# 🎨 Moxie's look — face customization (audit ADOPT #9)
# --------------------------------------------------------------------------- #

def test_the_console_learns_the_face_catalog_from_the_supervisor(client):
    """Same rule as the module picker: the 🎨 card renders the SDK's catalog, so it can
    never offer a slot or an option `validate_face` would then reject."""
    f = client.get("/local/fleet").json()
    assert [s["id"] for s in f["face_catalog"]] == [s["id"] for s in face_catalog()]
    eyes = next(s for s in f["face_catalog"] if s["id"] == "eye_color")
    assert eyes["cited"] is True
    assert {o["id"] for o in eyes["options"]} == {"green", "blue", "purple",
                                                 "brown", "gold", "teal"}
    assert all(o["hex"].startswith("#") for o in eyes["options"])   # swatch-able
    # the twelve slots our docs name but list no options for say so rather than lying
    assert next(s for s in f["face_catalog"] if s["id"] == "hair")["cited"] is False


def test_a_face_edit_round_trips_and_changes_the_texture_key(client, supervisor):
    """The whole promise of the card: a picked look reaches the supervisor, comes back in
    the effective config, and moves the cache-buster the robot keys its texture on."""
    before = client.get("/local/fleet").json()["robots"][0]["face_cache_id"]
    r = client.post(f"/local/robots/{DEVICE}/config",
                    json={"face": {"eye_color": "teal", "face_color": "pink"}})
    assert r.status_code == 200, r.text
    assert r.json()["applied"]["face"] == {"eye_color": "teal", "face_color": "pink"}
    assert json.loads(supervisor.config_posts[-1][1])["face"]["eye_color"] == "teal"
    robot = client.get("/local/fleet").json()["robots"][0]
    assert robot["config_effective"]["face"]["face_color"] == "pink"
    assert robot["face_cache_id"] and robot["face_cache_id"] != before

    # a *different* look must not reuse the same texture key
    client.post(f"/local/robots/{DEVICE}/config", json={"face": {"eye_color": "gold"}})
    after = client.get("/local/fleet").json()["robots"][0]["face_cache_id"]
    assert after != robot["face_cache_id"]


def test_an_option_the_catalog_does_not_offer_is_a_400(client):
    r = client.post(f"/local/robots/{DEVICE}/config",
                    json={"face": {"eye_color": "chartreuse"}})
    assert r.status_code == 400, r.text
    assert "chartreuse" in r.json()["error"]


def test_a_fleet_face_is_the_house_look_and_one_robot_can_restyle_a_layer(client):
    """PR #24's layering, applied to appearance. The house sets teal eyes; this robot is
    already wearing gold ones from the test above, so the robot layer wins that slot —
    then it restyles a *different* slot and ends up wearing both layers at once, which is
    what "each an independent layer" has to mean once two config layers exist."""
    client.post("/local/fleet/config", json={"face": {"eye_color": "teal"}})
    f = client.get("/local/fleet").json()
    robot = f["robots"][0]
    assert f["fleet_config"]["face"] == {"eye_color": "teal"}
    assert robot["config_effective"]["face"]["eye_color"] == "gold"   # robot beats house
    assert robot["config_sources"]["face"] == "robot"

    client.post(f"/local/robots/{DEVICE}/config", json={"face": {"face_color": "pink"}})
    robot = client.get("/local/fleet").json()["robots"][0]
    # the robot layer replaced wholesale, so the eyes fall back through to the house look
    assert robot["config_effective"]["face"] == {"eye_color": "teal",
                                                "face_color": "pink"}


def test_reset_to_default_clears_the_look_and_the_texture_key(client):
    client.post(f"/local/robots/{DEVICE}/config", json={"face": {"eye_color": "gold"}})
    assert client.get("/local/fleet").json()["robots"][0]["face_cache_id"]
    r = client.post(f"/local/robots/{DEVICE}/config", json={"face": None})
    assert r.status_code == 200, r.text
    assert r.json()["applied"]["face"] is None
    client.post("/local/fleet/config", json={"face": None})
    robot = client.get("/local/fleet").json()["robots"][0]
    assert robot["config_effective"]["face"] is None
    assert robot["face_cache_id"] == ""


# --------------------------------------------------------------------------- #
# GET /local/robots/{id}/telemetry
# --------------------------------------------------------------------------- #

def test_telemetry_returns_the_normalized_summary(client, supervisor):
    r = client.get(f"/local/robots/{DEVICE}/telemetry?limit=5")
    assert r.status_code == 200, r.text
    t = r.json()
    assert t["ok"] is True and t["device_id"] == DEVICE and t["count"] == 3
    # counts, most frequent first
    assert t["by_event"][0] == {"event": "conversation_start", "count": 2,
                                "last_seen": 140}
    assert [row["event"] for row in t["by_event"]] == ["conversation_start", "battery_low"]
    # newest-first event rows
    assert [e["event_name"] for e in t["events"]] == [
        "battery_low", "conversation_start", "conversation_start"]
    assert all(e["session_id"] == "s1" for e in t["events"])
    # the console forwarded the limit it was asked for
    assert supervisor.telemetry_queries[-1] == (DEVICE, 5)


def test_telemetry_for_an_unknown_device_is_a_404(client):
    r = client.get("/local/robots/d_nope/telemetry")
    assert r.status_code == 404, r.text
    t = r.json()
    assert t["ok"] is False and t["by_event"] == [] and t["events"] == []
    assert t["error"]


# --------------------------------------------------------------------------- #
# GET / POST /local/robots/{id}/safety — the parent review queue
# --------------------------------------------------------------------------- #

def test_safety_queue_reaches_the_console(client, supervisor):
    r = client.get(f"/local/robots/{DEVICE}/safety?limit=5")
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["ok"] is True and s["device_id"] == DEVICE and s["enabled"] is True
    assert s["classifier"] == "rules" and s["total"] == 2
    assert s["blocked"] == 1 and s["flagged"] == 1 and s["unreviewed"] == 2
    # counts carry the human labels straight from the rules file
    assert {row["label"] for row in s["by_category"]} >= {"Self-harm", "Profanity"}
    # newest first, and the child's words are masked before they ever leave the runtime
    top = s["events"][0]
    assert top["side"] == "child" and top["excerpt"] and "***" in top["excerpt"]
    assert "bullshit" not in json.dumps(s)
    assert supervisor.safety_queries[-1] == (DEVICE, 5)


def test_acknowledging_one_event_clears_it_for_the_parent(client):
    before = client.get(f"/local/robots/{DEVICE}/safety").json()
    target = [e for e in before["events"] if e["action"] == "block"][0]
    r = client.post(f"/local/robots/{DEVICE}/safety", json={"event_id": target["id"]})
    assert r.status_code == 200, r.text
    after = r.json()
    assert after["ok"] is True and after["unreviewed"] == before["unreviewed"] - 1
    assert [e for e in after["events"] if e["id"] == target["id"]][0]["reviewed"] is True
    # ...and the empty body acknowledges the rest
    every = client.post(f"/local/robots/{DEVICE}/safety", json={}).json()
    assert every["unreviewed"] == 0


def test_safety_for_an_unknown_device_is_a_404(client):
    r = client.get("/local/robots/d_nope/safety")
    assert r.status_code == 404, r.text
    s = r.json()
    assert s["ok"] is False and s["events"] == [] and s["total"] == 0 and s["error"]


def test_safety_is_graceful_when_the_supervisor_is_down(client, monkeypatch):
    from moxie_server import main
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    port = dead.getsockname()[1]
    dead.close()
    monkeypatch.setattr(main, "STATUS_URL", f"http://127.0.0.1:{port}/status")
    s = client.get(f"/local/robots/{DEVICE}/safety").json()
    assert s["ok"] is False and s["events"] == [] and s["error"]


# --------------------------------------------------------------------------- #
# the device allowlist (pairing gate) — 🔐 Robot access
# --------------------------------------------------------------------------- #

def test_permitting_a_pending_robot_reaches_the_supervisor(client, supervisor):
    """The console's one-click Permit: `POST /local/robots/{id}/permit` → the
    supervisor's `POST /permits`, which stores it and re-pushes that robot's config."""
    supervisor.permits["devices"].clear()
    r = client.post(f"/local/robots/{DEVICE}/permit", json={"label": "Sam's Moxie"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert supervisor.permit_posts[-1] == {
        "device_id": DEVICE, "permitted": True, "label": "Sam's Moxie"}
    assert DEVICE in supervisor.permits["devices"]
    assert [p["device_id"] for p in r.json()["permits"]] == [DEVICE]


def test_revoking_forwards_permitted_false(client, supervisor):
    supervisor.permits["devices"][DEVICE] = {"permitted_at": 1, "label": ""}
    r = client.post(f"/local/robots/{DEVICE}/permit", json={"permitted": False})
    assert r.status_code == 200
    assert supervisor.permit_posts[-1]["permitted"] is False
    assert DEVICE not in supervisor.permits["devices"]


def test_the_open_toggle_round_trips(client, supervisor):
    r = client.post("/local/fleet/permits", json={"allow_unverified_bots": True})
    assert r.status_code == 200 and r.json()["allow_unverified_bots"] is True
    assert supervisor.permits["allow_unverified_bots"] is True
    client.post("/local/fleet/permits", json={"allow_unverified_bots": False})
    assert supervisor.permits["allow_unverified_bots"] is False


def test_permit_is_graceful_when_the_supervisor_is_down(client, monkeypatch):
    """A parent clicking Permit with the supervisor stopped must get a readable answer,
    not a stack trace — the same 503-with-a-body contract as the config endpoints."""
    from moxie_server import main
    monkeypatch.setattr(main, "STATUS_URL", "http://127.0.0.1:1/status")
    r = client.post(f"/local/robots/{DEVICE}/permit", json={})
    assert r.status_code == 503 and r.json()["ok"] is False
    r = client.get("/local/permits")
    assert r.status_code == 503 and r.json()["pending"] == []


def test_the_console_lists_the_allowlist(client, supervisor):
    supervisor.permits["devices"][DEVICE] = {"permitted_at": 7, "label": "Sam's Moxie"}
    r = client.get("/local/permits")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["permits"][0]["label"] == "Sam's Moxie"
    supervisor.permits["devices"].clear()


def test_pairing_through_the_console_auto_permits_the_robot(client, supervisor):
    """The parent app's own pairing flow already IS the parent saying "this robot is
    mine", so completing it must not leave the robot sitting in the pending list. The
    QR carries Wi-Fi + the pairing seed and no device id, so the caller supplies the
    robot's MQTT `d_<uuid>`; `simulate-robot-scan` then permits it as part of pairing."""
    supervisor.permits["devices"].clear()
    tok = client.post("/local/quicklogin",
                      json={"email": "permit-test@local"}).json()["token"]
    auth = {"Authorization": f"Bearer {tok}"}
    prep = client.post("/local/pairing/prepare",
                       json={"ssid": "Home", "password": "hunter2"}, headers=auth)
    assert prep.status_code == 200, prep.text
    payload = prep.json()["qr_payload"]

    r = client.post("/local/simulate-robot-scan",
                    json={"qr_payload": payload, "device_id": "d_just_paired"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["permitted"] is True and body["permit_error"] is None
    assert "d_just_paired" in supervisor.permits["devices"]
    assert supervisor.permits["devices"]["d_just_paired"]["label"] == "paired via console"


def test_pairing_without_a_device_id_still_pairs(client, supervisor):
    """A robot whose MQTT id we do not know yet is paired in the REST database and shows
    up as pending on the MQTT side — the honest path for a real robot, which only
    reveals its `d_<uuid>` when it connects to the broker."""
    before = dict(supervisor.permits["devices"])
    tok = client.post("/local/quicklogin",
                      json={"email": "permit-test2@local"}).json()["token"]
    prep = client.post("/local/pairing/prepare", json={"ssid": "Home", "password": "x"},
                       headers={"Authorization": f"Bearer {tok}"})
    r = client.post("/local/simulate-robot-scan",
                    json={"qr_payload": prep.json()["qr_payload"]})
    assert r.status_code == 200
    assert r.json()["permitted"] is False and r.json()["robot_id"]
    assert supervisor.permits["devices"] == before      # nothing was permitted blindly


# --------------------------------------------------------------------------- #
# GET / DELETE /local/robots/{id}/memory — 🧠 what Moxie remembers (BEYOND #4)
# --------------------------------------------------------------------------- #

def test_memory_reaches_the_console_as_dated_rows(client, supervisor):
    """The floor: a parent can *read* every durable fact, with the day and the activity
    it came from, without a shell."""
    r = client.get(f"/local/robots/{DEVICE}/memory")
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["ok"] is True and m["device_id"] == DEVICE
    assert m["policy"] == "NO_MEDIA" and m["writes_allowed"] is True and m["bytes"] > 0
    assert m["namespace_count"] == 2 and m["total"] == 5
    ns = {n["namespace"]: n for n in m["namespaces"]}
    assert set(ns) == {"mchat", "free_chat"}
    assert ns["mchat"]["counts"] == {"facts": 2, "preferences": 1, "open_threads": 0,
                                     "summaries": 1, "total": 4}
    texts = [i["text"] for i in ns["mchat"]["items"]]
    assert "Sam has a beagle named Pepper" in texts
    assert "They talked about pets." in texts
    top = ns["mchat"]["items"][0]
    assert top["provenance"]["module_id"] == "MCHAT" and top["provenance"]["turns"] == 4
    assert top["provenance"]["date"] and top["provenance"]["reason"] == "exit"
    # newest activity first (mchat's provenance is later than free_chat's)
    assert [n["namespace"] for n in m["namespaces"]] == ["mchat", "free_chat"]
    assert supervisor.memory_queries[-1] == DEVICE
    # `view()` carries `_meta` now, so the card can say how far the transcript was written
    # down instead of silently implying it was all of it.
    assert m["summarized_through"] == 6
    assert ns["mchat"]["summarized_through"] == 6
    # ...and every row carries the id the per-item ✕ and the inline ✏️ act on
    assert all(i["id"] for i in ns["mchat"]["items"])
    assert len({i["id"] for i in ns["mchat"]["items"]}) == len(ns["mchat"]["items"])
    assert all(i["pinned"] is False for i in ns["mchat"]["items"])


def test_erasing_one_activity_forwards_and_the_next_read_reflects_it(client, supervisor):
    r = client.delete(f"/local/robots/{DEVICE}/memory/mchat")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["erased"] is True and body["namespace"] == "mchat"
    assert supervisor.memory_erases[-1] == (DEVICE, "mchat")
    # the console really erased it on the other side, not just in its own reply
    after = client.get(f"/local/robots/{DEVICE}/memory").json()
    assert [n["namespace"] for n in after["namespaces"]] == ["free_chat"]
    assert after["total"] == 1
    assert supervisor.memory.load(DEVICE).get("mchat") is None


def test_erasing_everything_empties_the_store(client, supervisor):
    r = client.delete(f"/local/robots/{DEVICE}/memory")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["erased"] is True and body["namespace"] == "all"
    assert body["namespaces"] == [] and body["total"] == 0
    assert supervisor.memory_erases[-1] == (DEVICE, "all")
    assert supervisor.memory.load(DEVICE) == {}
    # ...and the empty state is an empty view, not an error
    again = client.get(f"/local/robots/{DEVICE}/memory").json()
    assert again["ok"] is True and again["total"] == 0 and again["error"] is None
    _seed_memory(supervisor.runtime)          # leave the fixture as we found it


def test_correcting_one_line_forwards_and_pins_it(client, supervisor):
    """The other half of BEYOND #4: our own live run stored "Puppy sleeps on **his** bed"
    for "my bed". A parent fixes the pronoun from the card instead of erasing the
    activity — and the correction is pinned, so decay never takes it."""
    m = client.get(f"/local/robots/{DEVICE}/memory").json()
    ns = {n["namespace"]: n for n in m["namespaces"]}["mchat"]
    row = [i for i in ns["items"] if i["text"].startswith("Sam has a beagle")][0]
    r = client.post(f"/local/robots/{DEVICE}/memory/mchat/{row['id']}",
                    json={"text": "Sam has a beagle named Peppa"})
    assert r.status_code == 200, r.text
    assert r.json()["edited"] is True and r.json()["item"] == row["id"]
    assert supervisor.memory_edits[-1] == (DEVICE, "mchat", row["id"],
                                           "Sam has a beagle named Peppa")
    after = client.get(f"/local/robots/{DEVICE}/memory").json()
    fixed = [i for i in {n["namespace"]: n for n in after["namespaces"]}["mchat"]["items"]
             if i["id"] == row["id"]][0]
    assert fixed["text"] == "Sam has a beagle named Peppa" and fixed["pinned"] is True
    assert after["total"] == m["total"]              # nothing else was touched
    _reseed(supervisor)


def test_a_correction_the_safety_check_refuses_is_a_400_with_a_reason(client, supervisor):
    """A text box that writes into every later prompt must not be a way around the safety
    filter — and the card has to be able to say why, not just fail silently."""
    m = client.get(f"/local/robots/{DEVICE}/memory").json()
    row = {n["namespace"]: n for n in m["namespaces"]}["mchat"]["items"][0]
    r = client.post(f"/local/robots/{DEVICE}/memory/mchat/{row['id']}",
                    json={"text": "I want to kill myself"})
    assert r.status_code == 400, r.text
    assert r.json()["ok"] is False and r.json()["error"]
    unchanged = client.get(f"/local/robots/{DEVICE}/memory").json()
    assert unchanged["total"] == m["total"]
    texts = {i["text"] for n in unchanged["namespaces"] for i in n["items"]}
    assert "I want to kill myself" not in texts


def test_forgetting_one_item_leaves_the_rest_of_the_activity(client, supervisor):
    m = client.get(f"/local/robots/{DEVICE}/memory").json()
    ns = {n["namespace"]: n for n in m["namespaces"]}["mchat"]
    row, kept = ns["items"][0], ns["counts"]["total"] - 1
    r = client.delete(f"/local/robots/{DEVICE}/memory/mchat/{row['id']}")
    assert r.status_code == 200, r.text
    assert r.json()["erased"] is True and r.json()["item"] == row["id"]
    assert supervisor.memory_erases[-1] == (DEVICE, f"mchat/{row['id']}")
    after = {n["namespace"]: n
             for n in client.get(f"/local/robots/{DEVICE}/memory").json()["namespaces"]}
    assert after["mchat"]["counts"]["total"] == kept
    assert row["id"] not in {i["id"] for i in after["mchat"]["items"]}
    assert "free_chat" in after                     # the other activity is untouched
    # ...and the store really lost that one line, not just the console's copy
    stored = supervisor.memory.load(DEVICE)["mchat"]
    assert row["id"] not in {i.get("id") for v in stored.values()
                             if isinstance(v, list) for i in v if isinstance(i, dict)}
    assert stored["_meta"] == {"summarized_through": 6}   # not re-summarized after
    _reseed(supervisor)


def test_memory_for_an_unknown_device_is_a_404(client):
    r = client.get("/local/robots/d_nope/memory")
    assert r.status_code == 404, r.text
    m = r.json()
    assert m["ok"] is False and m["namespaces"] == [] and m["total"] == 0 and m["error"]


def test_memory_is_graceful_when_the_supervisor_is_down(client, monkeypatch):
    """Reading *or* erasing with the supervisor stopped must be a readable ok:false —
    never a 500, and never a UI that silently claims the memory is gone."""
    from moxie_server import main
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    port = dead.getsockname()[1]
    dead.close()
    monkeypatch.setattr(main, "STATUS_URL", f"http://127.0.0.1:{port}/status")
    r = client.get(f"/local/robots/{DEVICE}/memory")
    assert r.status_code == 503
    assert r.json()["ok"] is False and r.json()["namespaces"] == [] and r.json()["error"]
    d = client.delete(f"/local/robots/{DEVICE}/memory/mchat")
    assert d.status_code == 503 and d.json()["ok"] is False
    assert "erased" not in d.json()           # nothing was erased, so nothing is claimed


# --------------------------------------------------------------------------- #
# 📅 Today's plan — the recommender's "why this activity today" (audit BEYOND #7)
# --------------------------------------------------------------------------- #
# The supervisor plans the day and keeps one parent-readable sentence per entry. This is
# the seam that finally reads it: the URL the console builds, the `refresh` flag it
# forwards, and the claim the card is worth anything for — that the rows a parent reads
# are the entries the ROBOT was served, in that order, each with its own reason.

def test_todays_plan_reaches_the_console_with_a_reason_per_entry(client, supervisor):
    before = len(supervisor.schedule_queries)
    r = client.get(f"/local/robots/{DEVICE}/schedule")
    assert r.status_code == 200, r.text
    s = r.json()
    assert supervisor.schedule_queries[before:] == [(DEVICE, "")]
    assert s["ok"] is True and s["error"] is None
    assert s["device_id"] == DEVICE and s["day"] == "2026-09-02"
    assert s["child_name"] == "friend" and s["served"] is True
    # the rows ARE the served day, in order — the whole point of the card
    assert [e["module_id"] for e in s["entries"]] == [
        e["module_id"] for e in _SCHEDULE["schedule"]["provided_schedule"]]
    assert all(e["why"] for e in s["entries"])


def test_untimed_fixtures_show_no_clock_time_and_scored_picks_do(client):
    rows = {e["module_id"]: e for e in
            client.get(f"/local/robots/{DEVICE}/schedule").json()["entries"]}
    assert rows["DM"]["time_local"] is None and rows["DM"]["fixture"] is True
    assert rows["FREE_CHAT"]["time_local"] is None
    assert rows["WELCOME"]["time_local"] is None
    assert rows["STORYTELLING"]["time_local"] == "09:03"
    assert rows["SCAVENGERHUNT"]["time_local"] == "09:13"
    assert rows["SCAVENGERHUNT"]["fixture"] is False


def test_the_constraints_the_planner_reported_reach_the_footer(client):
    c = client.get(f"/local/robots/{DEVICE}/schedule").json()
    assert c["constraints"]["bedtime"] == {"enabled": True, "kind": "weekday",
                                           "starts_at": "09:23", "ends_at": "17:23"}
    assert c["dropped_for_bedtime"] == 4
    assert c["constraints"]["parent_request"] == {
        "count": 1, "pinned": [{"module_id": "STORYTELLING", "at": "08:43"}]}
    assert [e["module_id"] for e in c["entries"] if e["pinned"]] == ["STORYTELLING"]
    # the runtime says telemetry carries no module signal; the console must not lose it
    assert c["constraints"]["telemetry_signal"] is False


def test_refresh_is_forwarded_so_a_parent_can_re_plan_the_day(client, supervisor):
    before = len(supervisor.schedule_queries)
    r = client.get(f"/local/robots/{DEVICE}/schedule", params={"refresh": "true"})
    assert r.status_code == 200
    assert supervisor.schedule_queries[before:] == [(DEVICE, "1")]


def test_schedule_for_an_unknown_device_is_a_404(client):
    r = client.get("/local/robots/d_nope/schedule")
    assert r.status_code == 404, r.text
    s = r.json()
    assert s["ok"] is False and s["entries"] == [] and "d_nope" in s["error"]


def test_schedule_is_graceful_when_the_supervisor_is_down(client, monkeypatch):
    """A plan nobody can fetch must read as "supervisor unreachable", never as an empty
    day — a parent would take a blank list for "Moxie has nothing planned"."""
    from moxie_server import main
    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    port = dead.getsockname()[1]
    dead.close()
    monkeypatch.setattr(main, "STATUS_URL", f"http://127.0.0.1:{port}/status")
    r = client.get(f"/local/robots/{DEVICE}/schedule")
    assert r.status_code == 503
    s = r.json()
    assert s["ok"] is False and s["entries"] == [] and s["error"]
    assert s["constraints"]["bedtime"] == {"enabled": False, "kind": ""}


# --------------------------------------------------------------------------- #
# the double is honest
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
# 🎭 "Be Moxie" — puppet / telehealth (audit ADOPT #7)
# --------------------------------------------------------------------------- #
# The console↔runtime seam for the one card where a mistake is *audible in a child's
# room*: the URL, the verb, the body, and — the part worth the whole file — what the
# console does with the supervisor's 400 when the safety classifier refuses a line.

@pytest.fixture()
def puppet(client, supervisor):
    """Puppet mode on and a session open, torn back down afterwards."""
    client.post(f"/local/robots/{DEVICE}/telehealth", json={"action": "enable"})
    client.post(f"/local/robots/{DEVICE}/telehealth", json={"action": "start"})
    supervisor.runtime.client.published.clear()
    yield
    client.post(f"/local/robots/{DEVICE}/telehealth", json={"action": "disable"})


def _telehealth_wire(supervisor):
    return [p["message"] for p in supervisor.runtime.client.on(
        f"/devices/{DEVICE}/commands/telehealth")]


def test_the_card_reads_the_supervisors_telehealth_view(client, supervisor):
    r = client.get(f"/local/robots/{DEVICE}/telehealth")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["device_id"] == DEVICE
    assert len(body["moods"]) == 11 and body["max_intensity"] == 2
    # nothing has been reported by the robot, and the card must not invent a state
    assert body["reported"] is False and body["state"] == ""
    assert supervisor.telehealth_queries[-1] == DEVICE


def test_enable_speak_interrupt_disable_round_trips(client, supervisor, puppet):
    say = client.post(f"/local/robots/{DEVICE}/telehealth",
                      json={"action": "speak", "text": "Hello from Grandma.",
                            "mood": "happy", "intensity": 2})
    assert say.status_code == 200
    body = say.json()
    assert body["ok"] is True and body["spoke"] == "Hello from Grandma."
    assert body["in_session"] is True
    cut = client.post(f"/local/robots/{DEVICE}/telehealth", json={"action": "interrupt"})
    assert cut.status_code == 200 and cut.json()["ok"] is True

    wire = _telehealth_wire(supervisor)
    assert [m["action"] for m in wire] == ["PLAY_OUTPUT", "INTERRUPT"]
    assert wire[0]["output"]["text"] == "Hello from Grandma."
    assert "+mood+:1" in wire[0]["output"]["markup"]
    assert "+intensity+:2" in wire[0]["output"]["markup"]
    assert "output" not in wire[1]          # INTERRUPT carries no line


def test_the_operators_line_comes_back_in_the_transcript(client, supervisor, puppet):
    client.post(f"/local/robots/{DEVICE}/telehealth",
                json={"action": "speak", "text": "Time to brush your teeth."})
    body = client.get(f"/local/robots/{DEVICE}/telehealth").json()
    assert [(l["who"], l["text"]) for l in body["transcript"]][-1] == (
        "operator", "Time to brush your teeth.")


def test_a_line_the_safety_check_refuses_is_a_400_with_a_reason_and_is_never_spoken(
        client, supervisor, puppet):
    """The acceptance criterion, through the console's own seam: the operator is told
    why, and the robot hears nothing."""
    r = client.post(f"/local/robots/{DEVICE}/telehealth",
                    json={"action": "speak", "text": "you are a fucking idiot"})
    assert r.status_code == 400
    body = r.json()
    assert body["ok"] is False and body["blocked"] is True
    assert body["categories"] == ["profanity"]
    assert "Profanity" in body["reason"]
    assert _telehealth_wire(supervisor) == []


def test_speaking_with_the_mode_off_is_a_400_the_card_can_act_on(client, supervisor):
    client.post(f"/local/robots/{DEVICE}/telehealth", json={"action": "disable"})
    supervisor.runtime.client.published.clear()
    r = client.post(f"/local/robots/{DEVICE}/telehealth",
                    json={"action": "speak", "text": "Hello."})
    assert r.status_code == 400 and "Be Moxie" in r.json()["reason"]
    assert _telehealth_wire(supervisor) == []


def test_enable_re_pushes_the_config_with_the_mode_set(client, supervisor):
    supervisor.runtime.client.published.clear()
    client.post(f"/local/robots/{DEVICE}/telehealth", json={"action": "enable"})
    cfg = supervisor.runtime.client.on(f"/devices/{DEVICE}/config")[-1]
    assert cfg["moxie_mode"] == "TELEHEALTH"
    client.post(f"/local/robots/{DEVICE}/telehealth", json={"action": "disable"})
    cfg = supervisor.runtime.client.on(f"/devices/{DEVICE}/config")[-1]
    assert cfg["moxie_mode"] == "DEFAULT_MODE"


def test_the_robots_own_reported_state_reaches_the_card(client, supervisor, puppet):
    supervisor.runtime._on_activity(DEVICE, json.dumps(
        {"subtopic": "telehealth",
         "message": {"state": "IN_SESSION", "timestamp": 1700000000000}}))
    body = client.get(f"/local/robots/{DEVICE}/telehealth").json()
    assert body["state"] == "IN_SESSION" and body["state_known"] is True
    assert body["state_at"] == 1700000000.0


def test_telehealth_for_an_unknown_device_is_a_404(client):
    r = client.get("/local/robots/d_nope/telehealth")
    assert r.status_code == 404 and r.json()["ok"] is False


def test_an_unknown_verb_is_a_400(client):
    r = client.post(f"/local/robots/{DEVICE}/telehealth", json={"action": "dance"})
    assert r.status_code == 400 and r.json()["ok"] is False


def test_telehealth_is_graceful_when_the_supervisor_is_down(client, monkeypatch):
    import moxie_server.main as main
    monkeypatch.setattr(main, "STATUS_URL", "http://127.0.0.1:1/status")
    r = client.get(f"/local/robots/{DEVICE}/telehealth")
    assert r.status_code == 503
    body = r.json()
    assert body["ok"] is False and body["transcript"] == []
    assert body["max_intensity"] == 2


def test_fake_status_server_matches_the_real_runtime_shapes():
    """Diff the fake's payloads against the REAL MoxieRuntime, so runtime drift fails
    here rather than quietly turning this file into a test of itself."""
    pytest.importorskip("paho.mqtt.client")
    sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))
    import moxie_runtime
    from moxie_sdk.app import MoxieApp
    from moxie_sdk.types import ChildProfile, RobotContext

    class _App(MoxieApp):
        name = "content"

    rt = moxie_runtime.MoxieRuntime(app=_App(), child=ChildProfile(nickname="Sam"))
    rt.robots[DEVICE] = RobotContext(device_id=DEVICE, child=rt.child)
    rt.robots[DEVICE].extra["telemetry"] = list(_PACKETS)

    real = rt.status_snapshot()
    fake = _snapshot({}, {})
    assert set(fake) == set(real), "status snapshot top-level keys drifted"
    assert set(fake["robots"][0]) == set(real["robots"][0]), "robot record keys drifted"

    real_t = rt.telemetry_view(DEVICE, limit=5)
    fake_t, code = _telemetry(DEVICE, 5)
    assert code == 200 and set(fake_t) == set(real_t)
    assert set(fake_t["summary"]) == set(real_t["summary"])
    assert fake_t["summary"]["by_event"] == real_t["summary"]["by_event"]

    real_missing = rt.telemetry_view("d_nope")
    fake_missing, code = _telemetry("d_nope", 20)
    assert code == 404 and set(fake_missing) == set(real_missing)

    # the fleet-config seam: same endpoint, `scope=fleet`, same sanitizer
    assert rt.fleet_config() == {} or isinstance(rt.fleet_config(), dict)
    assert set(rt.effective_config(DEVICE)) == set(
        merge_config_layers(rt.fleet_config(), {}))

    # /safety and /memory are not doubled at all — the fake supervisor runs a REAL
    # MoxieRuntime behind both — so the only thing to guard is the unknown-device shape
    # (still a 404) and that the memory view keeps the keys the console reads.
    assert rt.safety_view("d_nope")["ok"] is False
    assert rt.acknowledge_safety("d_nope", "sfe-nope")["ok"] is False
    assert rt.memory_view("d_nope")["ok"] is False
    # 🎭 /telehealth is not doubled either — the fake runs the REAL runtime behind both
    # verbs — so what is guarded is the unknown-device shape and the keys the card reads.
    assert rt.telehealth_view("d_nope")["ok"] is False
    rt._allow_unverified_bots = True     # every telehealth verb checks the permit gate
    assert set(rt.telehealth_view(DEVICE)) == {
        "ok", "device_id", "enabled", "online", "session_id", "in_session",
        "state", "state_at", "in_bedtime", "transcript", "moods", "max_intensity"}
    assert set(rt.memory_view(DEVICE)) == {"ok", "device_id", "namespaces", "bytes",
                                           "writes_allowed", "policy"}
    # The per-item seam the console now drives: an id-carrying view, an erase that takes
    # an `item`, and an edit. Doubling any of these would let the card claim a control the
    # runtime does not have, which on this card is the whole promise.
    import tempfile
    from moxie_sdk.store import JsonStore as _JS
    rt.store = _JS(root=tempfile.mkdtemp())
    rt.memory_store().merge(DEVICE, "mchat", {"facts": ["has a dog", "likes red"]},
                            provenance={"module_id": "MCHAT", "turns": 2,
                                        "at": _SEED_AT},
                            meta={"summarized_through": 4}, now=_SEED_AT)
    view_ns = rt.memory_view(DEVICE)["namespaces"]["mchat"]
    assert set(view_ns) == {"data", "provenance", "meta"}
    one, two = [f["id"] for f in view_ns["data"]["facts"]]
    assert view_ns["meta"] == {"summarized_through": 4}
    edited = rt.edit_memory_item(DEVICE, "mchat", one, "has a beagle")
    assert set(edited) >= {"edited", "namespace", "item", "namespaces"}
    assert edited["namespaces"]["mchat"]["data"]["facts"][0]["pinned"] is True
    dropped = rt.erase_memory(DEVICE, "mchat", two)
    assert set(dropped) >= {"erased", "namespace", "item", "namespaces"}
    assert [f["text"] for f in
            dropped["namespaces"]["mchat"]["data"]["facts"]] == ["has a beagle"]
    erased = rt.erase_memory(DEVICE)
    assert erased["ok"] is True and set(erased) >= {"erased", "namespace", "namespaces"}

    # 📅 /schedule IS doubled (recorded — see `_SCHEDULE`), so its keys are diffed against
    # the live planner: the view, one explanation, and the inputs summary the card's
    # footer reads. `schedule_view` re-plans when nothing is stored, which is exactly the
    # path a parent hits before the robot has pulled its day.
    real_s = rt.schedule_view(DEVICE)
    fake_s, code = _schedule(DEVICE)
    assert code == 200 and set(fake_s) == set(real_s), "schedule view keys drifted"
    assert set(fake_s["schedule"]) <= set(real_s["schedule"])
    assert set(fake_s["explanations"][0]) == set(real_s["explanations"][0])
    assert set(fake_s["inputs"]) == set(real_s["inputs"]), "inputs summary keys drifted"
    assert set(fake_s["inputs"]["telemetry"]) == set(real_s["inputs"]["telemetry"])
    assert real_s["inputs"]["telemetry"]["carries_module_signal"] is False
    assert set(fake_s["inputs"]["planned"]) == set(real_s["inputs"]["planned"])
    real_missing = rt.schedule_view("d_nope")
    fake_missing, code = _schedule("d_nope")
    assert code == 404 and set(fake_missing) == set(real_missing)
