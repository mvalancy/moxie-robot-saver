"""The readiness line must not outrun the subscriptions.

`helpers_stack.Supervisor.start()` waits for `[runtime] broker connected` before it lets
a robot announce itself. For as long as `_on_connect` printed that line *before* calling
`subscribe`, the supervisor advertised readiness it did not have: the SIL robot's single
`/state` could land in the gap and go unheard, and `test_live_gateway_turn_e2e` failed as
"no config pushed within timeout" — intermittently, and *more often on a quiet box*,
because a busy one is slow enough to lose the race.

Found by the sixth integration pass while verifying the week soak (2026-09-04). It is
playbook rule 23's shape inside the runtime: a signal that was true of an earlier moment.
The assertion is on the ORDER OF EFFECTS, not on the source text, so a refactor that keeps
the bug cannot pass it.
"""
import io, os, sys
from contextlib import redirect_stdout

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))

import pytest
pytest.importorskip("paho.mqtt.client")


class _OrderRecordingClient:
    """Records every subscribe, and what stdout had said by the time it arrived."""

    def __init__(self, out):
        self.out, self.subscribes = out, []

    def subscribe(self, topic):
        self.subscribes.append((topic, self.out.getvalue()))


def _fresh_runtime():
    import moxie_runtime
    from moxie_sdk.app import MoxieApp
    from moxie_sdk.types import ChildProfile

    class _App(MoxieApp):
        name = "echo"

    return moxie_runtime.MoxieRuntime(app=_App(), child=ChildProfile(nickname="Sam"))


def test_the_readiness_line_is_printed_only_after_every_subscription():
    rt = _fresh_runtime()
    out = io.StringIO()
    client = _OrderRecordingClient(out)
    with redirect_stdout(out):
        rt._on_connect(client, None, {}, 0)

    assert client.subscribes, "no subscription was made on a successful CONNACK"
    for topic, stdout_at_that_moment in client.subscribes:
        assert "broker connected" not in stdout_at_that_moment, (
            f"the runtime announced 'broker connected' BEFORE subscribing to {topic!r} — "
            "a harness that waits on that line will publish into a supervisor that is not "
            "listening yet (rule 23: a readiness signal true of an earlier moment)")
    assert "broker connected" in out.getvalue(), "it never announced readiness at all"


def test_a_refused_connack_neither_subscribes_nor_claims_connection():
    """The original bug this ordering lesson generalises from: rc=5 must do neither."""
    rt = _fresh_runtime()
    out = io.StringIO()
    client = _OrderRecordingClient(out)
    with redirect_stdout(out):
        rt._on_connect(client, None, {}, 5)
    assert client.subscribes == [], "a refused CONNACK subscribed anyway"
    assert "broker connected" not in out.getvalue(), "a refusal logged 'broker connected'"
