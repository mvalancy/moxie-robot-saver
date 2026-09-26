"""🩺 `no config pushed within timeout` must say WHICH — starved, or wedged.

A smoke failure under heavy load (robot subscribed and acknowledged BEFORE announcing, so
not the QoS-0 race `test_sil_supervisor_readiness.py` reproduces) turned out to be a
supervisor starved of CPU — reported in exactly the words reserved for a broken one,
because the wait could not tell the difference.

**The rule.** A wait whose expiry means *"we stopped waiting"* must not be phrased as a
verdict about the thing waited for.

**Why asking works.** The status server is a different transport (HTTP on localhost) from
the one that went quiet (MQTT), so an answer there separates *alive but did not push* from
*not answering anything at all* — which a longer MQTT timeout never could.

**Proved here:** the three outcomes are distinguishable, none is silent, and the diagnosis
cannot itself throw (a raising failure path would replace a misleading message with none).
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
    assert "IS ALIVE" in why, why
    assert "NOT a wedged appliance" in why, why
    # The evidence, not just the verdict: a reader must be able to check the claim.
    assert "GET /status -> 200" in why, why


def test_says_NOT_ANSWERING_when_nothing_is_listening():
    """Nothing on the port is the other verdict, and it must be stated as such."""
    why = _robot(f"http://127.0.0.1:{_free_port()}")._why_no_config()
    assert "did NOT answer /status either" in why, why
    assert "wedged, gone, or starved" in why, why


def test_says_it_did_not_check_when_it_was_not_told_where_to_look():
    """The third outcome is the one most easily faked: SILENCE.

    With no `--status-url` the honest answer is *I did not check*, not an implied verdict.
    A diagnosis that omits the un-run case reads as though it ran and found nothing.
    """
    why = _robot(None)._why_no_config()
    assert "NOT CHECKED" in why, why
    assert "starved vs wedged" in why, why


@pytest.mark.parametrize("url", ["", "not-a-url", "http://", "http://127.0.0.1:99999"])
def test_the_diagnosis_never_raises(url):
    """It runs only on the failure path, so it must not replace a bad message with none."""
    why = _robot(url)._why_no_config()
    assert isinstance(why, str) and why, url


def test_the_three_verdicts_are_distinguishable():
    """Belt and braces: no two outcomes share wording a reader could confuse."""
    port = _free_port()
    srv = http.server.HTTPServer(("127.0.0.1", port), _Quiet)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        alive = _robot(f"http://127.0.0.1:{port}")._why_no_config()
    finally:
        srv.shutdown()
    dead = _robot(f"http://127.0.0.1:{_free_port()}")._why_no_config()
    unchecked = _robot(None)._why_no_config()
    assert len({alive, dead, unchecked}) == 3
    assert "IS ALIVE" not in dead and "IS ALIVE" not in unchecked
