"""
The ROLLING TRANSCRIPT on disk, through the parent's privacy switch.

Two memories: the durable facts (`MemoryStore`, see `test_memory*.py`) and the rolling
transcript (`MoxieRuntime.history`, written to `MOXIE_MEMORY_DIR/<device>.json` by
`_save_memory` every turn — and compose sets `MOXIE_MEMORY_DIR`). Under
`logging_policy=NO_DATA` nothing may be written, asserted against the FILESYSTEM
(`os.listdir` / `os.path.exists`), not a return value.

Hermetic: fake MQTT transport, fake brain, tmp storage, no sleeps.
"""
from __future__ import annotations

import json
import os

import pytest

from helpers_runtime import CHAT_TOPIC, LatchClient, make_runtime  # noqa: E402
import moxie_runtime  # noqa: E402
from moxie_sdk.app import MoxieApp  # noqa: E402
from moxie_sdk.cloud_config import LoggingPolicy  # noqa: E402
from moxie_sdk.store import JsonStore  # noqa: E402
from moxie_sdk.types import Reply  # noqa: E402

#: Deliberately content-free stand-ins. This repo is public, and a fixture is not the
#: place to write down what a child says — the test needs a token it can grep for on
#: disk, not a transcript.
SAID = "marker-in"
ANSWERED = "marker-out"


class _Echo(MoxieApp):
    """Answers every turn with one fixed line."""
    name = "echo"

    def respond(self, turn):
        return Reply(text=ANSWERED)


# ---------------------------------------------------------------------------
# harness
# ---------------------------------------------------------------------------

@pytest.fixture
def memdir(tmp_path, monkeypatch):
    """`MOXIE_MEMORY_DIR` pointed at a tmp dir. Read by `MoxieRuntime.__init__`, so it
    has to be set before the runtime is built."""
    d = tmp_path / "memory"
    monkeypatch.setenv("MOXIE_MEMORY_DIR", str(d))
    return d


def _runtime(tmp_path, device_id="d_test"):
    """A real runtime with a fake transport, a tmp data dir and one robot connected."""
    rt, did = make_runtime(_Echo(), device_id=device_id,
                           store=JsonStore(str(tmp_path / "data")))
    rt.client = LatchClient(runtime=rt)
    return rt, did


def _turn(rt, did, speech=SAID, event_id="evt"):
    """One real `events/remote-chat` turn, waiting for the published reply. Does NOT
    shut the pool down, so a test can drive several turns on one runtime."""
    topic = CHAT_TOPIC.format(device_id=did)
    before = len([1 for t, _ in rt.client.published if t == topic])
    rt._on_remote_chat(did, rt.robots[did], json.dumps(
        {"command": "prompt", "backend": "router", "event_id": event_id,
         "speech": speech}))
    assert rt.client.wait_for(
        lambda pubs: len([1 for t, _ in pubs if t == topic]) > before, timeout=15), \
        "no reply published"


def _only_turn(rt, did):
    """One turn, then drain the pool so every write has landed."""
    _turn(rt, did)
    rt._pool.shutdown(wait=True)


def _files(memdir):
    """What is actually on disk. The whole point of this suite."""
    try:
        return sorted(os.listdir(memdir))
    except FileNotFoundError:
        return []


def _no_data(rt, did):
    rt._config_overrides[did] = {"logging_policy": int(LoggingPolicy.NO_DATA)}


# ---------------------------------------------------------------------------
# what each policy value does to the transcript ON DISK
# ---------------------------------------------------------------------------

def test_no_media_is_the_default_and_it_writes_the_transcript(memdir, tmp_path):
    """`NO_MEDIA` (= `MEMORY_POLICY`) writes the transcript: it is all text with no opaque
    payload to withhold, so the choice is binary, like long-term memory's."""
    rt, did = _runtime(tmp_path)
    assert rt.memory_policy(did) == moxie_runtime.MEMORY_POLICY == LoggingPolicy.NO_MEDIA
    assert rt.transcript_persists(did) is True
    _only_turn(rt, did)

    assert _files(memdir) == [f"{did}.json"]
    stored = json.load(open(memdir / f"{did}.json"))
    assert [m["content"] for m in stored] == [SAID, ANSWERED]


def _full(rt, did):
    rt._config_overrides[did] = {"logging_policy": int(LoggingPolicy.FULL)}


def _fleet_no_data(rt, did):
    # the gate reads the EFFECTIVE fleet ⊕ robot config: one house rule covers every robot
    rt.update_fleet_config(logging_policy=int(LoggingPolicy.NO_DATA))


@pytest.mark.parametrize("policy, persists", [
    (_full, True),
    # the defect, asserted from the filesystem: `_save_memory` was once guarded only by
    # `if not self._memory_dir`, so a NO_DATA robot's turn landed on disk
    (_no_data, False),
    (_fleet_no_data, False),
], ids=["full", "no_data", "fleet_no_data"])
def test_the_policy_decides_whether_the_transcript_reaches_disk(memdir, tmp_path, policy,
                                                               persists):
    rt, did = _runtime(tmp_path)
    policy(rt, did)
    assert rt.transcript_persists(did) is persists
    _only_turn(rt, did)
    assert _files(memdir) == ([f"{did}.json"] if persists else [])


def test_no_data_stops_the_notify_path_too(memdir, tmp_path):
    """`_ingest_notify` is the transcript's other writer (the robot's own speech
    report). Gating one caller and not the other would be no gate at all."""
    rt, did = _runtime(tmp_path)
    _no_data(rt, did)
    rt._ingest_notify(did, {"extra_lines": [{"context_type": "input", "text": SAID}],
                            "speech": ANSWERED})
    assert _files(memdir) == []
    # ...and the same call on an ungated robot does write, so the test is not vacuous
    rt._config_overrides.pop(did)
    rt._ingest_notify(did, {"extra_lines": [], "speech": ANSWERED})
    assert _files(memdir) == [f"{did}.json"]


def _notify(rt, did):
    """The robot's notify for the turn just answered, as it arrives on `events/remote-chat`."""
    rt._on_remote_chat(did, rt.robots[did], json.dumps(
        {"command": "notify", "backend": "router", "event_id": "n1", "speech": ANSWERED,
         "extra_lines": [{"context_type": "input", "text": SAID}]}))


def test_a_turn_and_its_notify_are_written_once(memdir, tmp_path):
    """The notify reports the turn the transcript already holds (test_notify_history.py):
    reconciled, so the file holds it once, not twice."""
    rt, did = _runtime(tmp_path)
    _only_turn(rt, did)
    _notify(rt, did)
    stored = json.load(open(memdir / f"{did}.json"))
    assert [m["content"] for m in stored] == [SAID, ANSWERED]


def test_no_data_keeps_a_notified_turn_off_the_disk_and_once_in_memory(memdir, tmp_path):
    """Under NO_DATA the reconciled notify still writes nothing, and the conversation
    Moxie holds in RAM has the turn once."""
    rt, did = _runtime(tmp_path)
    _no_data(rt, did)
    _only_turn(rt, did)
    _notify(rt, did)
    assert _files(memdir) == []
    assert [m["content"] for m in rt.history[did]] == [SAID, ANSWERED]


# ---------------------------------------------------------------------------
# what happens to a file that is ALREADY there
# ---------------------------------------------------------------------------

def test_a_no_data_transcript_is_not_rehydrated_by_a_restart(memdir, tmp_path):
    """A durable fleet rule outlives the process; the file must not.

    Without the boot sweep, a restart under a fleet-wide `NO_DATA` would read the old
    transcript straight back into RAM and feed it to the next prompt."""
    store_root = str(tmp_path / "data")
    rt, did = make_runtime(_Echo(), store=JsonStore(store_root))
    rt.client = LatchClient(runtime=rt)
    _only_turn(rt, did)
    assert _files(memdir) == [f"{did}.json"]
    rt.update_fleet_config(logging_policy=int(LoggingPolicy.NO_DATA))
    assert _files(memdir) == []                    # gone the moment the rule was set

    # and even if it had survived (hand-written file, a crash mid-flip), the next boot
    # removes it instead of loading it
    os.makedirs(memdir, exist_ok=True)
    (memdir / f"{did}.json").write_text(json.dumps([{"role": "user", "content": SAID}]))
    rt2, _ = make_runtime(_Echo(), store=JsonStore(store_root))
    assert _files(memdir) == []
    assert rt2.history.get(did) in (None, [])


def test_an_ungated_transcript_is_still_restored_by_a_restart(memdir, tmp_path):
    """The other direction, so the sweep is not just "delete everything at boot"."""
    store_root = str(tmp_path / "data")
    rt, did = make_runtime(_Echo(), store=JsonStore(store_root))
    rt.client = LatchClient(runtime=rt)
    _only_turn(rt, did)

    rt2, _ = make_runtime(_Echo(), store=JsonStore(store_root))
    assert [m["content"] for m in rt2.history[did]] == [SAID, ANSWERED]


# ---------------------------------------------------------------------------
# the gate is live: no restart, and short-term memory is untouched
# ---------------------------------------------------------------------------

def test_a_policy_change_takes_effect_without_a_restart(memdir, tmp_path):
    """The config is pushed to a live robot; a parent flipping the switch must not have
    to restart the appliance. The gate is resolved per write, so it does not."""
    rt, did = _runtime(tmp_path)
    _turn(rt, did, event_id="e1")
    assert _files(memdir) == [f"{did}.json"]

    rt.update_config(did, logging_policy=int(LoggingPolicy.NO_DATA))
    _turn(rt, did, event_id="e2")
    assert _files(memdir) == []                    # same process, same runtime object

    rt.update_config(did, logging_policy=int(LoggingPolicy.NO_MEDIA))
    _turn(rt, did, event_id="e3")
    rt._pool.shutdown(wait=True)
    assert _files(memdir) == [f"{did}.json"]       # ...and back on again


def test_no_data_does_not_take_away_short_term_memory(memdir, tmp_path):
    """This is a **persistence** gate, not a memory gate. A robot that could not hold
    the thread of the conversation it is having would not be private, it would be
    broken — and nothing about the child leaves the process either way."""
    rt, did = _runtime(tmp_path)
    _no_data(rt, did)
    _turn(rt, did, event_id="e1")
    _turn(rt, did, event_id="e2")
    rt._pool.shutdown(wait=True)
    assert [m["content"] for m in rt.history[did]] == [SAID, ANSWERED, SAID, ANSWERED]
    assert _files(memdir) == []


# ---------------------------------------------------------------------------
# erasure is never gated
# ---------------------------------------------------------------------------

def test_erasure_still_works_under_no_data(memdir, tmp_path):
    """"Reads and erase always work" (content-module-contract.md). A parent who has
    turned recording off must still be able to delete what was recorded before."""
    rt, did = _runtime(tmp_path)
    mem = rt.memory_store()
    mem.merge(did, "mchat", {"facts": ["a fact"]})
    _only_turn(rt, did)
    assert _files(memdir) == [f"{did}.json"] and mem.load(did)

    # the real parent path: the console edits the config, which pushes to the robot
    rt.update_config(did, logging_policy=int(LoggingPolicy.NO_DATA))
    assert _files(memdir) == []                    # the transcript went with the switch

    out = rt.erase_memory(did)                     # ...and the durable facts still go
    assert out["ok"] is True and out["erased"] is True
    assert rt.memory_store().load(did) == {}
    assert rt.memory_view(did)["ok"] is True       # reads still answer under NO_DATA
    assert rt.memory_view(did)["writes_allowed"] is False


def test_forgetting_a_transcript_survives_a_missing_file(memdir, tmp_path):
    """Idempotent and best-effort: the erase path runs on the MQTT thread, and a file
    that is not there must not cost the child their turn."""
    rt, did = _runtime(tmp_path)
    _no_data(rt, did)
    assert rt._forget_transcript(did) is False     # nothing to remove
    _only_turn(rt, did)
    assert rt._forget_transcript(did) is False
    assert _files(memdir) == []
