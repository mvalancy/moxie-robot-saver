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
GET /telemetry, GET+POST /safety, POST /config — same payload shapes, same status codes,
and the REAL `sanitize_config_overrides` behind /config so validation is not mocked away
and the REAL `MoxieRuntime.safety_view`/`acknowledge_safety` behind /safety), point
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

sanitize_config_overrides = pytest.importorskip(
    "moxie_sdk.cloud_config", reason="SDK not importable").sanitize_config_overrides

DEVICE = "d_console_rt"


# --------------------------------------------------------------------------- #
# The fake supervisor: MoxieRuntime._start_status_server's contract, no broker.
# --------------------------------------------------------------------------- #

def _snapshot(overrides: dict) -> dict:
    """MoxieRuntime.status_snapshot() for one connected robot."""
    return {
        "ok": True, "app": "content", "uptime_s": 12,
        "robots": [{
            "device_id": DEVICE, "child": "Sam", "firmware": "3.6.4",
            "battery_level": 91, "audio_volume": 0.4, "wifi_ssid": "Home",
            "mode": "normal", "ota_reboot_required": False,
            "config_overrides": dict(overrides), "telemetry_count": 2,
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

    rt = moxie_runtime.MoxieRuntime(app=_App(), child=ChildProfile(nickname="Sam"))
    rt.store = JsonStore(root=root)
    rt.robots[DEVICE] = RobotContext(device_id=DEVICE, child=rt.child)
    rt._record_safety(DEVICE, S.assess("I want to kill myself"))
    rt._record_safety(DEVICE, S.assess("this is bullshit"))
    return rt


class FakeSupervisor:
    """Serves the endpoints the console proxies, on a free ephemeral port."""

    def __init__(self, safety_root: str):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from urllib.parse import parse_qs, urlparse
        self.overrides: dict = {}
        self.config_posts: list = []
        self.telemetry_queries: list = []
        self.safety_queries: list = []
        self.runtime = _safety_runtime(safety_root)
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
                    return self._out(_snapshot(outer.overrides))
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
                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                u = urlparse(self.path)
                if u.path not in ("/config", "/safety"):
                    self.send_response(404)
                    self.end_headers()
                    return
                device_id = (parse_qs(u.query).get("device_id") or [""])[0]
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}"
                if u.path == "/safety":
                    body = json.loads(raw or b"{}") or {}
                    out = outer.runtime.acknowledge_safety(device_id, body.get("event_id"))
                    return self._out(out, 200 if out.get("ok") else 404)
                outer.config_posts.append((device_id, raw.decode()))
                try:
                    applied = sanitize_config_overrides(json.loads(raw))
                    if device_id != DEVICE:
                        raise ValueError(f"unknown device_id {device_id!r}")
                    outer.overrides.update(applied)
                    return self._out({"ok": True, "device_id": device_id,
                                      "applied": applied,
                                      "config_overrides": dict(outer.overrides)}, 200)
                except Exception as e:
                    return self._out({"ok": False, "error": str(e)}, 400)

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))          # a genuinely free port; never stomps
        self.port = sock.getsockname()[1]
        sock.close()
        self._srv = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()

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
# the double is honest
# --------------------------------------------------------------------------- #

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
    fake = _snapshot({})
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

    # /safety is not doubled at all — the fake supervisor runs a REAL MoxieRuntime behind
    # it — so the only thing to guard is that its unknown-device shape still 404s.
    assert rt.safety_view("d_nope")["ok"] is False
    assert rt.acknowledge_safety("d_nope", "sfe-nope")["ok"] is False
