"""
Unit tests for the durable per-robot store (mqtt/moxie_sdk/store.py) — the stepping
stone under `mentor_behaviors` (openmoxie-feature-audit.md ADOPT #2/#8).

Pure: a tmp directory, no MQTT, no broker.
"""
import json
import os
import sys
import threading

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.store import JsonStore, data_dir, safe_name   # noqa: E402


def test_read_of_a_missing_store_returns_the_default(tmp_path):
    s = JsonStore(str(tmp_path / "nope"))                  # directory does not exist
    assert s.read("d_1", "mentor_behaviors", []) == []
    assert s.read("d_1", "mentor_behaviors") is None
    assert s.devices() == []


def test_write_then_read_roundtrips_and_creates_the_dir(tmp_path):
    s = JsonStore(str(tmp_path / "data"))
    assert s.write("d_1", "mentor_behaviors", [{"module_id": "DM"}]) is True
    assert s.read("d_1", "mentor_behaviors") == [{"module_id": "DM"}]
    assert os.path.isfile(s.path("d_1", "mentor_behaviors"))


def test_append_accumulates_and_caps_to_the_newest(tmp_path):
    s = JsonStore(str(tmp_path))
    for i in range(5):
        s.append("d_1", "mentor_behaviors", {"i": i}, cap=3)
    assert s.read("d_1", "mentor_behaviors") == [{"i": 2}, {"i": 3}, {"i": 4}]


def test_append_recovers_from_a_non_list_value(tmp_path):
    s = JsonStore(str(tmp_path))
    s.write("d_1", "mentor_behaviors", {"not": "a list"})
    assert s.append("d_1", "mentor_behaviors", {"i": 1}) == [{"i": 1}]


def test_a_corrupt_file_reads_as_the_default_instead_of_raising(tmp_path):
    """One damaged record must not take a robot's whole session down."""
    s = JsonStore(str(tmp_path))
    s.write("d_1", "mentor_behaviors", [])
    with open(s.path("d_1", "mentor_behaviors"), "w") as fh:
        fh.write("{ not json")
    assert s.read("d_1", "mentor_behaviors", []) == []


def test_robots_are_isolated_from_each_other(tmp_path):
    s = JsonStore(str(tmp_path))
    s.append("d_a", "mentor_behaviors", {"who": "a"})
    s.append("d_b", "mentor_behaviors", {"who": "b"})
    assert s.read("d_a", "mentor_behaviors") == [{"who": "a"}]
    assert s.read("d_b", "mentor_behaviors") == [{"who": "b"}]
    assert len(s.devices()) == 2


def test_unsafe_device_ids_cannot_escape_the_root(tmp_path):
    s = JsonStore(str(tmp_path))
    s.write("../../etc/passwd", "x", {"ok": True})
    assert os.path.commonpath([str(tmp_path), s.path("../../etc/passwd", "x")]) == str(tmp_path)
    # distinct ids that sanitize alike still get distinct directories
    assert safe_name("a/b") != safe_name("a:b")


def test_delete_removes_the_collection(tmp_path):
    s = JsonStore(str(tmp_path))
    s.write("d_1", "x", [1])
    assert s.delete("d_1", "x") is True
    assert s.delete("d_1", "x") is False
    assert s.read("d_1", "x", "gone") == "gone"


def test_write_is_atomic_ish_and_leaves_no_temp_files(tmp_path):
    s = JsonStore(str(tmp_path))
    s.write("d_1", "x", {"v": 1})
    s.write("d_1", "x", {"v": 2})
    leftovers = [f for f in os.listdir(s.device_dir("d_1")) if f.endswith(".tmp")]
    assert leftovers == []
    with open(s.path("d_1", "x")) as fh:
        assert json.load(fh) == {"v": 2}


def test_unserializable_value_fails_cleanly_without_clobbering(tmp_path):
    s = JsonStore(str(tmp_path))
    s.write("d_1", "x", {"v": 1})
    assert s.write("d_1", "x", {"bad": object()}) is False
    assert s.read("d_1", "x") == {"v": 1}                  # previous good value survives
    assert [f for f in os.listdir(s.device_dir("d_1")) if f.endswith(".tmp")] == []


def test_concurrent_appends_do_not_lose_records(tmp_path):
    """The runtime ingests reports on a worker pool — read-modify-write must be locked."""
    s = JsonStore(str(tmp_path))
    threads = [threading.Thread(target=s.append, args=("d_1", "mentor_behaviors", {"i": i}))
               for i in range(25)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(s.read("d_1", "mentor_behaviors")) == 25


def test_data_dir_honors_the_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("MOXIE_DATA_DIR", str(tmp_path / "elsewhere"))
    assert data_dir() == str(tmp_path / "elsewhere")
    monkeypatch.setenv("MOXIE_DATA_DIR", "")
    assert data_dir().endswith(os.path.join("mqtt", "data"))     # the default
