"""`no config pushed within timeout` must say WHICH: a supervisor starved of CPU was once
reported in the words reserved for a wedged one. Asking the status server (HTTP, a different
transport from the MQTT that went quiet) separates the three outcomes; none may be silent,
and the diagnosis must not throw on the failure path it serves.
"""
from __future__ import annotations

import http.server
import pathlib
import socket
import sys
import threading

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
virtual_moxie = pytest.importorskip("virtual_moxie")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Quiet(http.server.BaseHTTPRequestHandler):
    def do_GET(self):                                   # noqa: N802 - stdlib's spelling
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, *a):                          # keep pytest output readable
        pass


def _robot(status_url):
    return virtual_moxie.VirtualMoxie("127.0.0.1", 1, timeout=20.0, verbose=False,
                                      status_url=status_url)


def test_says_ALIVE_when_the_status_server_answers():
    """A reachable supervisor means the MQTT answer was lost or the process was starved."""
    port = _free_port()
    srv = http.server.HTTPServer(("127.0.0.1", port), _Quiet)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        why = _robot(f"http://127.0.0.1:{port}")._why_no_config()
    finally:
        srv.shutdown()
    assert "IS ALIVE" in why and "GET /status -> 200" in why, why   # verdict + evidence


def test_says_NOT_ANSWERING_when_nothing_is_listening():
    """Nothing on the port is the other verdict, and it must be stated as such."""
    why = _robot(f"http://127.0.0.1:{_free_port()}")._why_no_config()
    assert "did NOT answer /status" in why and "IS ALIVE" not in why, why


def test_says_it_did_not_check_when_it_was_not_told_where_to_look():
    """With no `--status-url` the honest answer is *I did not check*, not an implied verdict."""
    why = _robot(None)._why_no_config()
    assert "NOT CHECKED" in why and "IS ALIVE" not in why, why


@pytest.mark.parametrize("url", ["", "not-a-url", "http://", "http://127.0.0.1:99999"])
def test_the_diagnosis_never_raises(url):
    """It runs only on the failure path, so it must not replace a bad message with none."""
    why = _robot(url)._why_no_config()
    assert isinstance(why, str) and why, url
