"""The Wi-Fi tab's code is Wi-Fi ONLY: the first code of the re-home flow.

`docs/debugging/live-hardware-debug.md` (Confirmed facts) and `tools/pairing/moxie_qr.py`
(`encode_wifi_only`): the first code a robot scans on its way to this server must be
wifi-only (`StartPairingQR.wifi_only`, no `secret_key`); a pairing-key code sends it
looking for the original cloud. The web app's Wi-Fi tab made the pairing-key code
(`/local/pairing/prepare`), and the setup guides sent owners there. It now asks
`POST /local/wifi/payload`, and the pairing-key code needs an explicit option (the
original app's flow, or Simulate robot scan). Pinned here:

* the route's payload has no secret key and the wifi-only flag set, read by our codec and
  by the recovered `StartPairingQR` (in its own interpreter), and it is byte-identical to
  `moxie_qr.encode_wifi_only`, the form verified against OpenMoxie's Wi-Fi code;
* it registers nothing and needs no account;
* the pairing-key code is still `pairing/prepare`'s, unchanged, and only it can be scanned
  by Simulate robot scan.

Which code the tab asks for by default is a browser question: `sim/test_robot_claim.mjs`
(W1 and its teeth).
"""
import json
import os
import subprocess
import sys

import pytest

from helpers_console import REPO, console_app

sys.path.insert(0, os.path.join(REPO, "tools", "pairing"))
import moxie_qr  # noqa: E402

BENCH = {"ssid": "BenchNet", "password": "s3cret pass", "band": "24g", "hidden": True}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    TestClient, main = console_app(tmp_path_factory.mktemp("wifi-first") / "wifi.db")
    with TestClient(main.app) as c:
        yield c


def _wifi(client, body=BENCH):
    r = client.post("/local/wifi/payload", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_wifi_tabs_code_carries_no_pairing_key(client):
    out = _wifi(client)
    assert out["wifi_only"] is True
    d = moxie_qr.decode_proto(out["qr_payload"])
    assert d["secret_key"] is None
    assert d["hide_pair"] is True                       # field 5, StartPairingQR.wifi_only
    assert (d["ssid"], d["password"], d["is_hidden"]) == ("BenchNet", "s3cret pass", True)
    assert d["band"] == moxie_qr.Band.ONLY_24G
    assert d["iot_endpoint"] is None and d["dev"] is False
    assert out["qr_payload"] == moxie_qr.encode_wifi_only(moxie_qr.WifiInfo(
        "BenchNet", "s3cret pass", is_hidden=True, band=moxie_qr.Band.ONLY_24G))

    plain = moxie_qr.decode_proto(_wifi(client, {"ssid": "Home", "password": ""})["qr_payload"])
    assert plain["secret_key"] is None and plain["hide_pair"] is True
    assert plain["band"] == moxie_qr.Band.ANY and plain["is_hidden"] is False


ORACLE = """
import base64, json, sys
sys.path.insert(0, sys.argv[1])
from embodied.wifiapp import QRCommands_pb2 as W
payload = sys.argv[2]
pb = W.StartPairingQR()
pb.ParseFromString(base64.b64decode(payload[2:]))
# OpenMoxie's Wi-Fi code (MIT, site/hive/mqtt/moxie_server.py:393-401): wifi_only,
# ssid, password, is_hidden, band_select, and no key. Only true fields are set, because the
# recovered scalars are `optional` and an assigned default would be written out.
ref = W.StartPairingQR(ssid=pb.ssid, password=pb.password, wifi_only=True)
if pb.is_hidden:
    ref.is_hidden = True
if pb.band_select:
    ref.band_select = pb.band_select
print(json.dumps({
    "wifi_only": pb.wifi_only, "has_secret_key": pb.HasField("secret_key"),
    "ssid": pb.ssid, "password": pb.password, "is_hidden": pb.is_hidden,
    "band": W.StartPairingQR.WifiBandSelect.Name(pb.band_select),
    "prefix": payload[:2],
    "same_bytes": payload[2:] == base64.b64encode(ref.SerializeToString()).decode()}))
"""


def test_the_recovered_proto_reads_it_as_wifi_only(client):
    """A second opinion from the committed recovered `StartPairingQR`, run in its own
    interpreter so its `embodied` package never meets another test's imports."""
    pytest.importorskip("google.protobuf", reason="the recovered-proto oracle needs protobuf")
    payload = _wifi(client)["qr_payload"]
    got = json.loads(subprocess.run(
        [sys.executable, "-c", ORACLE,
         os.path.join(REPO, "tools", "robot-toolkit", "moxie_toolkit"), payload],
        capture_output=True, text=True, check=True).stdout)
    assert got == {"wifi_only": True, "has_secret_key": False, "ssid": "BenchNet",
                   "password": "s3cret pass", "is_hidden": True, "band": "ONLY_24G",
                   "prefix": "PA", "same_bytes": True}


def test_the_wifi_only_code_registers_nothing_and_needs_no_account(client):
    from moxie_server import db
    before = db.q1("SELECT COUNT(*) n FROM pairings")["n"]
    assert client.post("/local/wifi/payload", json=BENCH).status_code == 200   # no token
    assert db.q1("SELECT COUNT(*) n FROM pairings")["n"] == before
    assert client.post("/local/wifi/payload", json={"ssid": "  "}).status_code == 400


def test_the_pairing_key_code_is_still_there_behind_the_option(client):
    """The original app's code, unchanged: a seed in field 4, and the only one Simulate
    robot scan can complete."""
    tok = client.post("/local/quicklogin", json={"email": "keyed@wifi.lan"}).json()["token"]
    prep = client.post("/local/pairing/prepare", headers={"Authorization": f"Bearer {tok}"},
                       json=BENCH)
    assert prep.status_code == 200, prep.text
    keyed = moxie_qr.decode_proto(prep.json()["qr_payload"])
    assert len(keyed["secret_key"]) == 32 and keyed["hide_pair"] is False
    assert prep.json()["recovery_phrase"]
    scan = client.post("/local/simulate-robot-scan",
                       json={"qr_payload": _wifi(client)["qr_payload"]})
    assert scan.status_code == 400 and "wifi-only" in scan.json()["detail"]
