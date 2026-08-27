"""
Moxie QR codec — encode/decode every QR the robot's Wifi App (`bo-wifi`) accepts.

Mirrors `WifiApp.dll` `QRData.ParseFromString(string)` exactly:

    prefix "PA" + Base64(StartPairingQR proto)   -> pairing (wifi + secret + endpoint)
    prefix "VN" + Base64(QRVPNConfig  proto)      -> VPN profile push
    raw JSON {wifi?, pair?, debug?}               -> wifi creds / legacy pair / debug-factory cmd

The `debug` block becomes a `QRCommand{code,param}` published to the brain over ZMQ; codes handled
by the Wifi App itself: serial_number_display, restore_factory, reset_network, bluetooth_pair.
Any other code (e.g. endpoint_update) is forwarded to bo-android.

Source of truth: docs/reverse-engineering/qr-commands.md
"""
import base64, json, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from embodied.wifiapp import QRCommands_pb2 as W
from embodied.logging import enums_pb2 as LOG

IOTEndpoint = LOG.IOTEndpoint
WifiBandSelect = W.StartPairingQR.WifiBandSelect
VPNCommand = W.QRVPNConfig.VPNCommand

PAIR_PREFIX = "PA"
VPN_PREFIX  = "VN"

# ---------- decode (what the robot does) ----------
def decode(data: str) -> dict:
    """Parse a QR string the way bo-wifi does. Returns a tagged dict."""
    if data is None:
        return {"type": "invalid", "reason": "null"}
    if data.startswith(PAIR_PREFIX):
        pb = W.StartPairingQR()
        pb.ParseFromString(base64.b64decode(data[2:]))
        return {"type": "pair", "proto": pb}
    if data.startswith(VPN_PREFIX):
        pb = W.QRVPNConfig()
        pb.ParseFromString(base64.b64decode(data[2:]))
        return {"type": "vpn", "proto": pb}
    obj = json.loads(data)  # raises on non-JSON, exactly like a failed parse in the app
    out = {"type": "json", "wifi": obj.get("wifi"), "pair": obj.get("pair"), "debug": obj.get("debug")}
    return out

# ---------- encode (what a phone / our server does) ----------
def encode_pairing(ssid="", password="", secret_key=b"", *, is_staging=False, wifi_only=False,
                   is_hidden=False, band_select=WifiBandSelect.ANY, endpoint=IOTEndpoint.IOT_DEFAULT) -> str:
    # Only set non-default fields, so the wire bytes stay minimal and byte-match the phone-side
    # encoder (tools/pairing/moxie_qr.py). The recovered proto marks scalars `optional` (explicit
    # presence), so assigning a default would otherwise emit a spurious present-but-false field.
    pb = W.StartPairingQR(ssid=ssid, password=password)
    if secret_key:  pb.secret_key = secret_key
    if is_staging:  pb.is_staging = True
    if wifi_only:   pb.wifi_only = True
    if is_hidden:   pb.is_hidden = True
    if band_select: pb.band_select = band_select
    if endpoint:    pb.endpoint = endpoint
    return PAIR_PREFIX + base64.b64encode(pb.SerializeToString()).decode()

def encode_vpn(command=VPNCommand.VPN_DOWNLOAD, *, vpn_id="", url="", username="", password="",
               connect=False) -> str:
    pb = W.QRVPNConfig(command=command, vpn_id=vpn_id, url=url, username=username, password=password,
                       connect=connect)
    return VPN_PREFIX + base64.b64encode(pb.SerializeToString()).decode()

def encode_wifi(ssid, password, *, is_hidden=False, band_select="ANY") -> str:
    return json.dumps({"wifi": {"ssid": ssid, "password": password,
                                "is_hidden": is_hidden, "band_select": band_select}})

def encode_debug(command: str, param: str = "") -> str:
    """Debug/factory command QR: {'debug':{'command':..,'param':..}}."""
    return json.dumps({"debug": {"command": command, "param": param}})

def encode_endpoint_update(endpoint) -> str:
    """Point the robot at a different cloud. endpoint = IOTEndpoint enum value or name."""
    if isinstance(endpoint, str):
        endpoint = IOTEndpoint.Value(endpoint)
    # endpoint_update is forwarded to the brain as a QRCommand carrying the IOTEndpoint;
    # over the QR/JSON channel it rides as a debug command with the endpoint name as param.
    return json.dumps({"debug": {"command": "endpoint_update",
                                 "param": IOTEndpoint.Name(endpoint)}})

# Known debug/factory command codes (from WifiApp.dll dispatch + forwarded set)
KNOWN_DEBUG_COMMANDS = {
    "serial_number_display": "Show the serial-number screen (Wifi App).",
    "restore_factory":       "Enter factory-restore flow (Wifi App).",
    "reset_network":         "Forget all Wi-Fi and reconnect (Wifi App).",
    "bluetooth_pair":        "Bluetooth-pair the device named in `param` (Wifi App).",
    "endpoint_update":       "Re-home the robot to the IOTEndpoint in `param` (forwarded to brain).",
}
