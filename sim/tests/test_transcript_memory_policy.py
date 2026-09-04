"""
The **rolling transcript** on disk, through the parent's privacy switch.

There are two memories in this appliance and they are not the same thing:

  * the **durable facts** a content module keeps between conversations
    (`moxie_sdk/store.py::MemoryStore`) — covered by `test_memory.py` and
    `test_memory_runtime.py`, and gated on `LoggingPolicy` since it was written;
  * the **rolling conversation transcript** — `MoxieRuntime.history`, written to
    `MOXIE_MEMORY_DIR/<device>.json` by `_save_memory` after every turn.

The second one was ungated. `docker-compose.yml` sets `MOXIE_MEMORY_DIR=/data/memory`,
so *every* `docker compose up` deployment persisted a child's conversation verbatim —
including on a robot whose parent had explicitly chosen `logging_policy=NO_DATA`, which
`moxie_runtime.py`'s own comments and `docs/architecture/content-module-contract.md`
both promise means nothing is written. This suite is that promise, asserted **against
the filesystem** rather than against a return value: a gate that returns False and
writes the file anyway is exactly the bug being fixed.

Every assertion here is `os.listdir` / `os.path.exists` on the real memory dir.

Hermetic: fake MQTT transport, fake brain, tmp storage, no sleeps, no network.
"""
from __future__ import annotations

import json
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))
sys.path.insert(0, os.path.join(REPO, "mqtt", "supervisor"))

import pytest  # noqa: E402

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
    """`NO_MEDIA` is `MEMORY_POLICY`, and for a transcript it means *write it*.

    A transcript is entirely text this server already holds in RAM to make conversation
    work; there is no opaque payload to withhold, which is the only thing `NO_MEDIA`
    strips from telemetry. So the choice is binary and it matches long-term memory's:
    written under `NO_MEDIA`, refused under `NO_DATA`."""
    rt, did = _runtime(tmp_path)
    assert rt.memory_policy(did) == moxie_runtime.MEMORY_POLICY == LoggingPolicy.NO_MEDIA
    assert rt.transcript_persists(did) is True
    _turn(rt, did)
    rt._pool.shutdown(wait=True)

    assert _files(memdir) == [f"{did}.json"]
    stored = json.load(open(memdir / f"{did}.json"))
    assert [m["content"] for m in stored] == [SAID, ANSWERED]


def test_full_writes_the_transcript(memdir, tmp_path):
    rt, did = _runtime(tmp_path)
    rt._config_overrides[did] = {"logging_policy": int(LoggingPolicy.FULL)}
    assert rt.transcript_persists(did) is True
    _turn(rt, did)
    rt._pool.shutdown(wait=True)
    assert _files(memdir) == [f"{did}.json"]


def test_no_data_leaves_nothing_on_disk(memdir, tmp_path):
    """The defect, asserted from the filesystem: a `NO_DATA` robot writes no file.

    RED before the gate landed — `_save_memory` was guarded by nothing but
    `if not self._memory_dir`, so this directory held the child's turn."""
    rt, did = _runtime(tmp_path)
    _no_data(rt, did)
    assert rt.transcript_persists(did) is False
    _turn(rt, did)
    rt._pool.shutdown(wait=True)
    assert _files(memdir) == []


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


def test_a_fleet_wide_no_data_rule_also_stops_the_transcript(memdir, tmp_path):
    """The gate resolves through `memory_policy`, which reads the **effective**
    `fleet ⊕ per-robot` config — so one house rule covers every robot on the box."""
    rt, did = _runtime(tmp_path)
    rt.update_fleet_config(logging_policy=int(LoggingPolicy.NO_DATA))
    assert rt.transcript_persists(did) is False
    _turn(rt, did)
    rt._pool.shutdown(wait=True)
    assert _files(memdir) == []


# ---------------------------------------------------------------------------
# what happens to a file that is ALREADY there
# ---------------------------------------------------------------------------

def test_flipping_to_no_data_removes_the_transcript_already_on_disk(memdir, tmp_path):
    """Refusing new writes while yesterday's transcript stays on disk is a half
    guarantee. Erase is never policy-gated, so closing the gate erases."""
    rt, did = _runtime(tmp_path)
    _turn(rt, did)
    rt._pool.shutdown(wait=True)
    assert _files(memdir) == [f"{did}.json"]

    rt.update_config(did, logging_policy=int(LoggingPolicy.NO_DATA))
    assert _files(memdir) == []


def test_a_no_data_transcript_is_not_rehydrated_by_a_restart(memdir, tmp_path):
    """A durable fleet rule outlives the process; the file must not.

    Without the boot sweep, a restart under a fleet-wide `NO_DATA` would read the old
    transcript straight back into RAM and feed it to the next prompt."""
    store_root = str(tmp_path / "data")
    rt, did = make_runtime(_Echo(), store=JsonStore(store_root))
    rt.client = LatchClient(runtime=rt)
    _turn(rt, did)
    rt._pool.shutdown(wait=True)
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
    _turn(rt, did)
    rt._pool.shutdown(wait=True)

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
    _turn(rt, did)
    rt._pool.shutdown(wait=True)
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
    _turn(rt, did)
    rt._pool.shutdown(wait=True)
    assert rt._forget_transcript(did) is False
    assert _files(memdir) == []
