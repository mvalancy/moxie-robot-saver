#!/usr/bin/env python3
"""
Moxie pairing-QR codec (clean-room, derived from decompiled ProtoPairing.java /
JSONPairing.java in the Embodied "Moxie Robot" parent app v2.2.2).

The Moxie robot's camera scans a QR code the parent app displays. That QR carries
the WiFi credentials (so the robot can join your network) and either a cloud
"user_token" (JSON mode) or an Ed25519 signing key (protobuf mode) used to bind
the robot to an account on the backend.

Two on-the-wire formats exist:

  * JSON mode  (PairQRMode.PAIR_JSON_TOKEN): a UTF-8 JSON string, no prefix.
        {"wifi":{"ssid","password","is_hidden","band_select"},
         "pair":{"user_token": "<token>"}}

  * PROTO mode (PairQRMode.PAIR_PROTO_KEY): the ASCII prefix "PA" followed by
        Base64( protobuf ). The protobuf is a hand-rolled message; field/tag map
        below is taken verbatim from ProtoPairing.toQRString():

        tag 0x0A  f1  LEN     ssid            (utf-8)
        tag 0x12  f2  LEN     password        (utf-8)
        tag 0x18  f3  VARINT  dev flag = 1    (ONLY emitted in non-PRODUCTION builds)
        tag 0x22  f4  LEN     secret_key      (Ed25519 signing key bytes) -- mutually
        tag 0x28  f5  VARINT  hide_pair = 1     exclusive with f4 (proto w/o key)
        tag 0x30  f6  VARINT  is_hidden = 1
        tag 0x38  f7  VARINT  band            (1 = 5GHz only, 2 = 2.4GHz only)
        tag 0x40  f8  VARINT  iot_endpoint    (int index; written as a single byte)

NOTE: the app uses Android Base64.DEFAULT (flag 0) which wraps at 76 cols with '\n'
and appends a trailing '\n'. Decoders should ignore whitespace. encode() below
emits unwrapped base64 by default (accepted by lenient decoders); pass
android_default=True to reproduce the app's exact wrapped bytes.
"""
from __future__ import annotations
import base64, json
from dataclasses import dataclass
from enum import IntEnum

PROTO_PAIR_HEADER = "PA"

class Band(IntEnum):
    ANY = 0        # omit field entirely
    ONLY_5G = 1
    ONLY_24G = 2

def _varint(n: int) -> bytes:
    out = bytearray()
    while n & ~0x7F:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n & 0x7F)
    return bytes(out)

def _read_varint(buf: bytes, i: int):
    shift = 0; result = 0
    while True:
        b = buf[i]; i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7

@dataclass
class WifiInfo:
    ssid: str
    password: str
    is_hidden: bool = False
    band: Band = Band.ANY

# ---------- PROTO mode ----------
def encode_proto(wifi: WifiInfo, secret_key: bytes | None = None, *,
                 hide_pair: bool = False, iot_endpoint: int = 0,
                 dev: bool = False, android_default: bool = False) -> str:
    """Build "PA"+Base64(protobuf). Provide secret_key (Ed25519 key bytes) OR
    set hide_pair=True (proto pairing that omits the key). dev=True adds the
    non-production dev flag (field 3)."""
    b = bytearray()
    # f1 ssid
    s = wifi.ssid.encode("utf-8"); b += b"\x0a" + _varint(len(s)) + s
    # f2 password
    p = wifi.password.encode("utf-8"); b += b"\x12" + _varint(len(p)) + p
    # f3 dev flag (non-production only)
    if dev:
        b += b"\x18\x01"
    # f4 secret_key XOR f5 hide_pair
    if hide_pair:
        b += b"\x28\x01"
    else:
        if secret_key is None:
            raise ValueError("secret_key required unless hide_pair=True")
        b += b"\x22" + _varint(len(secret_key)) + bytes(secret_key)
    # f6 hidden
    if wifi.is_hidden:
        b += b"\x30\x01"
    # f7 band
    if wifi.band == Band.ONLY_5G:
        b += b"\x38\x01"
    elif wifi.band == Band.ONLY_24G:
        b += b"\x38\x02"
    # f8 iot endpoint (app writes it as a single byte)
    b += b"\x40" + bytes([iot_endpoint & 0xFF])

    if android_default:
        enc = base64.encodebytes(bytes(b)).decode("ascii")  # wraps at 76 + trailing \n
    else:
        enc = base64.b64encode(bytes(b)).decode("ascii")
    return PROTO_PAIR_HEADER + enc

def decode_proto(qr: str) -> dict:
    if not qr.startswith(PROTO_PAIR_HEADER):
        raise ValueError("not a PA-proto QR")
    raw = base64.b64decode("".join(qr[len(PROTO_PAIR_HEADER):].split()))
    out = {"ssid": None, "password": None, "dev": False, "secret_key": None,
           "hide_pair": False, "is_hidden": False, "band": Band.ANY, "iot_endpoint": None}
    i = 0
    while i < len(raw):
        tag = raw[i]; i += 1
        if tag == 0x0A:
            n, i = _read_varint(raw, i); out["ssid"] = raw[i:i+n].decode("utf-8"); i += n
        elif tag == 0x12:
            n, i = _read_varint(raw, i); out["password"] = raw[i:i+n].decode("utf-8"); i += n
        elif tag == 0x18:
            v, i = _read_varint(raw, i); out["dev"] = bool(v)
        elif tag == 0x22:
            n, i = _read_varint(raw, i); out["secret_key"] = raw[i:i+n]; i += n
        elif tag == 0x28:
            v, i = _read_varint(raw, i); out["hide_pair"] = bool(v)
        elif tag == 0x30:
            v, i = _read_varint(raw, i); out["is_hidden"] = bool(v)
        elif tag == 0x38:
            v, i = _read_varint(raw, i); out["band"] = Band(v)
        elif tag == 0x40:
            v, i = _read_varint(raw, i); out["iot_endpoint"] = v
        else:
            raise ValueError(f"unknown tag 0x{tag:02x} at offset {i-1}")
    return out

# ---------- Wi-Fi-only (OpenMoxie-style relocation flow) ----------
def encode_wifi_only(wifi: WifiInfo, android_default: bool = False) -> str:
    """Wi-Fi-ONLY pairing QR, byte-matching OpenMoxie's StartPairingQR(wifi_only=True):
    fields ssid(1), password(2), wifi_only(5)=1, is_hidden(6), band_select(7).
    No secret_key, no endpoint field — the robot just joins Wi-Fi, then the endpoint
    QR relocates it. This is the correct first QR for the self-hosted revival flow
    (a pairing key would send the robot chasing Embodied's dead cloud)."""
    b = bytearray()
    s = wifi.ssid.encode("utf-8"); b += b"\x0a" + _varint(len(s)) + s
    p = wifi.password.encode("utf-8"); b += b"\x12" + _varint(len(p)) + p
    b += b"\x28\x01"                                   # field 5 wifi_only = true
    if wifi.is_hidden:
        b += b"\x30\x01"                               # field 6 is_hidden = true
    if wifi.band == Band.ONLY_5G:
        b += b"\x38\x01"
    elif wifi.band == Band.ONLY_24G:
        b += b"\x38\x02"                               # field 7 band_select
    enc = (base64.encodebytes if android_default else base64.b64encode)(bytes(b)).decode("ascii")
    return PROTO_PAIR_HEADER + enc


# ---------- JSON mode ----------
def encode_json(wifi: WifiInfo, user_token: str | None) -> str:
    band_map = {Band.ANY: "ANY", Band.ONLY_5G: "ONLY_50G", Band.ONLY_24G: "ONLY_24G"}
    model = {"wifi": {"ssid": wifi.ssid, "password": wifi.password,
                      "is_hidden": wifi.is_hidden, "band_select": band_map[wifi.band]}}
    model["pair"] = None if user_token is None else {"user_token": user_token}
    return json.dumps(model, separators=(",", ":"))

if __name__ == "__main__":
    import sys
    print("Self-test: round-trip proto QR")
    w = WifiInfo("HomeNet", "s3cr3t!", is_hidden=True, band=Band.ONLY_24G)
    key = bytes(range(32))
    qr = encode_proto(w, key, iot_endpoint=3, dev=False)
    print("QR:", qr)
    d = decode_proto(qr)
    print("decoded:", {k: (v.hex() if isinstance(v, bytes) else v) for k, v in d.items()})
    assert d["ssid"] == "HomeNet" and d["password"] == "s3cr3t!"
    assert d["is_hidden"] and d["band"] == Band.ONLY_24G
    assert d["secret_key"] == key and d["iot_endpoint"] == 3
    # android_default wrapping must decode identically
    qr2 = encode_proto(w, key, iot_endpoint=3, android_default=True)
    assert decode_proto(qr2) == d
    print("JSON:", encode_json(w, "user-token-abc123"))
    print("ALL OK")
