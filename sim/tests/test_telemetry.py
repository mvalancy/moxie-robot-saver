"""
Telemetry tests (M5 remainder) — the Packet envelope (build/parse) + the LoggingPolicy
upload-gate. Field names verified against embodied/logging/Cloud.proto.
"""
import base64
import json
import os
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "mqtt"))

from moxie_sdk.telemetry import (  # noqa: E402
    PacketModel, build_packet, parse_packet, should_upload, summarize_events,
)
from moxie_sdk.cloud_config import LoggingPolicy  # noqa: E402


def test_packet_model_values_match_proto():
    assert (PacketModel.UNKNOWN, PacketModel.SessionLog, PacketModel.Device,
            PacketModel.Event, PacketModel.Raw) == (0, 1, 2, 3, 4)


def test_build_packet_shape_and_base64():
    p = build_packet("wake", b"\x01\x02", moxie_id="d_x", session_id="s1",
                     recorded_at=123)
    assert p["model"] == "Event" and p["event_name"] == "wake"
    assert p["moxie_id"] == "d_x" and p["moxie_session_id"] == "s1"
    assert p["recorded_at"] == 123 and p["version"] == 1
    assert base64.b64decode(p["event_data"]) == b"\x01\x02"


def test_logging_policy_upload_gate():
    # NO_DATA → nothing leaves
    assert should_upload(LoggingPolicy.NO_DATA) is False
    assert should_upload(LoggingPolicy.NO_DATA, is_media=True) is False
    # NO_MEDIA → everything but audio/video
    assert should_upload(LoggingPolicy.NO_MEDIA) is True
    assert should_upload(LoggingPolicy.NO_MEDIA, is_media=True) is False
    # FULL → everything
    assert should_upload(LoggingPolicy.FULL, is_media=True) is True


def test_parse_packet_round_trip():
    p = build_packet("said", "hello", moxie_id="d_y")
    got = parse_packet(json.dumps(p))
    assert got["event_name"] == "said" and got["moxie_id"] == "d_y"
    assert got["model"] == "Event"


def test_parse_packet_ignores_unknown_fields():
    got = parse_packet(json.dumps({"event_name": "e", "moxie_id": "d", "junk": 1}))
    assert "junk" not in got and got["event_name"] == "e"


# --- summarize_events (M6 insights view) ---

def _pkts():
    return [build_packet("wake", b"", moxie_id="d1", recorded_at=100),
            build_packet("said", "hi", moxie_id="d1", recorded_at=200),
            build_packet("wake", b"", moxie_id="d1", recorded_at=300)]


def test_summarize_events_counts_and_last_seen():
    s = summarize_events(_pkts())
    assert s["count"] == 3
    assert s["by_event"] == {"wake": 2, "said": 1}
    assert s["last_seen"] == {"wake": 300, "said": 200}


def test_summarize_events_latest_is_newest_first_and_capped():
    s = summarize_events(_pkts(), limit=2)
    assert [p["event_name"] for p in s["latest"]] == ["wake", "said"]   # newest first
    assert len(s["latest"]) == 2 and s["count"] == 3                    # count is total


def test_summarize_events_empty_and_none_are_safe():
    for empty in ([], None):
        s = summarize_events(empty)
        assert s == {"count": 0, "by_event": {}, "last_seen": {}, "latest": []}


def test_summarize_events_tolerates_partial_packets():
    """Packets may arrive without event_name/recorded_at; non-dicts are skipped."""
    s = summarize_events([{"moxie_id": "d1"},                      # no event_name
                          {"event_name": "said"},                  # no recorded_at
                          {"event_name": "said", "recorded_at": "77"},  # stringy ts
                          "not-a-packet", None])
    assert s["count"] == 3                                  # the two non-dicts dropped
    assert s["by_event"] == {"event": 1, "said": 2}         # unnamed → "event"
    assert s["last_seen"] == {"said": 77}                   # only stamped events appear


def test_summarize_events_limit_zero_returns_no_rows():
    s = summarize_events(_pkts(), limit=0)
    assert s["latest"] == [] and s["count"] == 3


# ---------------------------------------------------------------------------
# Durable, bounded telemetry — the privacy gate, the caps, the day arithmetic
# ---------------------------------------------------------------------------
# These are the pure half of the "telemetry survives a restart" slice. The runtime side
# (write → restart → read back) is `test_telemetry_runtime.py`; the console side is
# `test_fleet.py` + `test_console_roundtrip.py`.

from moxie_sdk.telemetry import (  # noqa: E402
    DAILY_COLLECTION, MAX_DAY_EVENTS, OTHER_EVENT, PACKETS_COLLECTION,
    history_view, max_packets, max_rollup_days, new_rollup, packet_day, policy_value,
    retention, roll_up_packet, rollup_totals, storable_packet,
)


def test_collections_are_distinct_and_filesystem_safe():
    """Two records, not one: a ring for "just now" and day rows for "last week"."""
    assert PACKETS_COLLECTION != DAILY_COLLECTION
    for name in (PACKETS_COLLECTION, DAILY_COLLECTION):
        assert name.replace("_", "").isalnum()


# --- the privacy gate: one test per LoggingPolicy value ---
# Being wrong here is a privacy incident, not a bug, so each of the three values is
# pinned on its own rather than sharing one parametrized assertion.

def test_no_data_persists_absolutely_nothing():
    """`NO_DATA` → None. Not a redacted packet, not a count: nothing reaches the disk.
    `config-and-telemetry-contract.md` §③ — a server MUST honor it."""
    pkt = build_packet("wake", b"\x01\x02", moxie_id="d_x", session_id="s1")
    assert storable_packet(pkt, LoggingPolicy.NO_DATA) is None
    assert storable_packet(pkt, 0) is None
    assert storable_packet(pkt, "NO_DATA") is None


def test_no_media_keeps_the_envelope_and_withholds_every_payload():
    """`NO_MEDIA` → no audio/video payload may be stored. `Packet.event_data` is opaque
    `bytes` with no recovered type vocabulary, so the gate withholds EVERY payload rather
    than guessing which blobs are media — and says so in the record it keeps."""
    pkt = build_packet("wake", b"\x01\x02", moxie_id="d_x", session_id="s1",
                       recorded_at=42)
    row = storable_packet(pkt, LoggingPolicy.NO_MEDIA)
    assert "event_data" not in row, "an opaque payload was written under NO_MEDIA"
    assert row["event_data_withheld"] == "NO_MEDIA"
    # the envelope a parent's insights actually need is intact
    assert (row["event_name"], row["recorded_at"], row["moxie_session_id"],
            row["moxie_id"], row["model"]) == ("wake", 42, "s1", "d_x", "Event")
    # a media-shaped event name is not treated differently: the rule is the payload
    assert "event_data" not in storable_packet(
        build_packet("audio_upload", b"RIFF....", moxie_id="d_x"),
        LoggingPolicy.NO_MEDIA)


def test_full_keeps_the_payload_but_truncates_a_huge_one():
    """`FULL` → everything, bounded. One packet must not be able to blow up the ring."""
    small = storable_packet(build_packet("said", b"hi", moxie_id="d_x"),
                            LoggingPolicy.FULL)
    assert base64.b64decode(small["event_data"]) == b"hi"
    assert "event_data_truncated" not in small
    huge = storable_packet(build_packet("blob", b"\x00" * 8000, moxie_id="d_x"),
                           LoggingPolicy.FULL)
    assert len(huge["event_data"]) == 2048 and huge["event_data_truncated"] is True


def test_an_unknown_policy_falls_back_to_withholding_the_payload():
    """Fail closed: a policy we cannot read is treated as NO_MEDIA, never as FULL."""
    for bogus in (None, "SHARE_EVERYTHING", True, object()):
        row = storable_packet(build_packet("e", b"x", moxie_id="d"), bogus)
        assert row is not None and "event_data" not in row


def test_policy_value_reads_enums_ints_and_names():
    assert policy_value(LoggingPolicy.NO_DATA) == 0
    assert policy_value("no_media") == 1 and policy_value(2) == 2
    assert policy_value("FULL") == 2
    assert policy_value(None) is None and policy_value(True) is None
    assert policy_value("nonsense") is None


def test_storable_packet_never_mutates_the_caller_and_drops_junk_fields():
    pkt = build_packet("e", b"x", moxie_id="d")
    pkt["junk"] = 1
    row = storable_packet(pkt, LoggingPolicy.NO_MEDIA)
    assert "event_data" in pkt, "the live buffer's packet was mutated"
    assert "junk" not in row
    assert storable_packet("not a packet", LoggingPolicy.FULL) is None


# --- the day arithmetic ---
# Every test below reads the clock through `time.strftime(..., time.localtime(<FIXED
# epoch>))`, never through a real "now": the epoch is a literal, and strftime is used to
# compute the EXPECTATION the same way `telemetry.packet_day` computes the answer. That
# makes them timezone-aware (deliberately — the roll-up is keyed on the LOCAL calendar
# day, so a hard-coded "2026-09-02" would fail west of UTC) and hour-independent. Do not
# "fix" them into string literals: that would silently pin the runner's timezone.

def test_packet_day_uses_recorded_at_when_it_is_plausible():
    ts = 1756800000                                  # 2026-09-02 in the local zone
    assert packet_day({"recorded_at": ts}, now=ts + 60) == time.strftime(
        "%Y-%m-%d", time.localtime(ts))


def test_packet_day_falls_back_to_arrival_when_the_clock_lies():
    """Device clocks lie and `recorded_at` is optional — a missing, pre-2020 or
    far-future stamp is not usable, so arrival time is used instead."""
    now = 1756800000.0
    today = time.strftime("%Y-%m-%d", time.localtime(now))
    for bad in ({}, {"recorded_at": None}, {"recorded_at": "soon"},
                {"recorded_at": 5}, {"recorded_at": now + 999999}):
        assert packet_day(bad, now=now) == today


# --- the roll-up arithmetic ---

def _roll(n, *, name="wake", day_ts, rollup=None, max_days=None):
    r = rollup if rollup is not None else new_rollup()
    for _ in range(n):
        r = roll_up_packet(r, {"event_name": name, "recorded_at": day_ts},
                           now=day_ts + 1, max_days=max_days)
    return r


def test_roll_up_counts_a_day_by_event_and_tracks_its_span():
    r = new_rollup()
    r = roll_up_packet(r, {"event_name": "wake", "recorded_at": 1756800000},
                       now=1756800100)
    r = roll_up_packet(r, {"event_name": "said", "recorded_at": 1756800050},
                       now=1756800100)
    r = roll_up_packet(r, {"event_name": "wake", "recorded_at": 1756800010},
                       now=1756800100)
    day = time.strftime("%Y-%m-%d", time.localtime(1756800000))
    row = r["days"][day]
    assert row["count"] == 3 and row["by_event"] == {"wake": 2, "said": 1}
    assert (row["first"], row["last"]) == (1756800000, 1756800050)
    assert r["total"] == 3 and r["dropped_days"] == 0


def test_roll_up_keeps_the_newest_days_and_counts_what_it_retired():
    """The window slides; `total` is a lifetime count and must NOT slide with it, or a
    parent's "all time" number would shrink every night."""
    r = new_rollup()
    day = 86400
    for i in range(10):                      # ten consecutive days, one packet each
        r = roll_up_packet(r, {"event_name": "e", "recorded_at": 1756800000 + i * day},
                           now=1756800000 + i * day + 1, max_days=4)
    assert len(r["days"]) == 4, "the day cap did not hold"
    assert r["dropped_days"] == 6 and r["total"] == 10
    # exactly the four newest days survived, and they are the last four we rolled up
    newest = [time.strftime("%Y-%m-%d", time.localtime(1756800000 + i * day))
              for i in range(6, 10)]
    assert sorted(r["days"]) == newest


def test_a_day_caps_its_distinct_event_names_without_losing_the_count():
    """`event_name` is a free string in the recovered proto, so a robot can mint
    unbounded names. The overflow is counted under `(other)`, never dropped."""
    r = new_rollup()
    ts = 1756800000
    for i in range(MAX_DAY_EVENTS + 7):
        r = roll_up_packet(r, {"event_name": f"e{i}", "recorded_at": ts}, now=ts + 1)
    day = time.strftime("%Y-%m-%d", time.localtime(ts))
    row = r["days"][day]
    assert len(row["by_event"]) == MAX_DAY_EVENTS + 1      # the names + "(other)"
    assert row["by_event"][OTHER_EVENT] == 7
    assert sum(row["by_event"].values()) == row["count"] == MAX_DAY_EVENTS + 7


def test_roll_up_survives_a_corrupt_or_hand_edited_record():
    """A damaged JSON file must never take a robot's session down."""
    for junk in (None, [], "nope", {"days": "nope"},
                 {"days": {"2026-09-02": {"count": "x", "by_event": {"a": None}}}}):
        r = roll_up_packet(junk, {"event_name": "e", "recorded_at": 1756800000},
                           now=1756800001)
        assert r["total"] >= 1 and isinstance(r["days"], dict)


def test_roll_up_ignores_a_non_packet():
    r = roll_up_packet(new_rollup(), "not a packet")
    assert r["total"] == 0 and r["days"] == {}


# --- the history view the 📈 card renders ---

def test_history_view_zero_fills_the_week():
    """A day the robot said nothing on is a real answer. Skipping it would render a week
    with two busy days as a two-day week."""
    r = {"days": {"2026-08-31": {"count": 5, "by_event": {"wake": 3, "said": 2}},
                  "2026-09-02": {"count": 1, "by_event": {"said": 1}}},
         "total": 6, "dropped_days": 0, "updated_at": 1}
    rows = history_view(r, days=4, today="2026-09-02")
    assert [x["day"] for x in rows] == ["2026-08-30", "2026-08-31",
                                        "2026-09-01", "2026-09-02"]
    assert [x["count"] for x in rows] == [0, 5, 0, 1]
    assert rows[1]["top_event"] == "wake" and rows[0]["top_event"] is None


def test_history_view_breaks_a_tie_by_name_so_a_refresh_does_not_jitter():
    r = {"days": {"2026-09-02": {"count": 4, "by_event": {"zeta": 2, "alpha": 2}}}}
    assert history_view(r, days=1, today="2026-09-02")[0]["top_event"] == "alpha"


def test_history_view_edge_cases_are_safe():
    assert history_view(None, days=0, today="2026-09-02") == []
    assert history_view({}, days=2, today="not-a-date") == []
    assert len(history_view({}, days=7, today="2026-09-02")) == 7


def test_rollup_totals_states_how_far_back_the_store_really_goes():
    r = {"days": {"2026-08-31": {"count": 5, "by_event": {}},
                  "2026-09-02": {"count": 1, "by_event": {}}},
         "total": 99, "dropped_days": 3, "updated_at": 7}
    t = rollup_totals(r)
    assert t == {"total": 99, "days_kept": 2, "first_day": "2026-08-31",
                 "last_day": "2026-09-02", "dropped_days": 3, "updated_at": 7}
    empty = rollup_totals(None)
    assert empty["days_kept"] == 0 and empty["first_day"] is None


# --- the caps are configurable, with sane defaults ---

def test_caps_default_and_read_the_environment(monkeypatch):
    assert (max_packets(), max_rollup_days()) == (500, 35)
    assert retention() == {"packets": 500, "days": 35}
    monkeypatch.setenv("MOXIE_TELEMETRY_MAX_PACKETS", "20")
    monkeypatch.setenv("MOXIE_TELEMETRY_MAX_DAYS", "3")
    assert retention() == {"packets": 20, "days": 3}
    monkeypatch.setenv("MOXIE_TELEMETRY_MAX_PACKETS", "not-a-number")
    assert max_packets() == 500                    # unparseable → the default, not a crash
    monkeypatch.setenv("MOXIE_TELEMETRY_MAX_PACKETS", "-5")
    assert max_packets() == 0                      # explicit zero-retention is honored
