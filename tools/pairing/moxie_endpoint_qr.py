#!/usr/bin/env python3
"""
Moxie endpoint-config QR ("QR #2") — the SECOND QR a firmware-801/803 Moxie waits
for after it joins Wi-Fi. It repoints the robot from Embodied's dead cloud to YOUR
MQTT broker. Clean-room, hand-rolled protobuf (no protobuf runtime needed).

Wire format (verified against OpenMoxie's ServiceConfiguration2, MIT):
  QR string = UTF-8 JSON, no prefix:
    {"debug": {"command": "om", "param": "<base64(ServiceConfiguration2)>"}}

  ServiceConfiguration2 (embodied.logging), only 4 fields set:
    f1  gcp_project    string   (client-id prefix / JWT aud; shorten to "o" for QR density)
    f8  mqtt_host      string   (your broker's LAN/Tailscale IP or hostname — keep short!)
    f11 override_port  uint32   (8883)
    f12 disable_verify bool     (true → robot accepts a self-signed cert; works on fw 24.10.803)

Known-good vector (host 192.168.1.50):
  {"debug": {"command": "om", "param": "CgFvQgwxOTIuMTY4LjEuNTBYs0VgAQ=="}}
"""
from __future__ import annotations
import base64, json


def _varint(n: int) -> bytes:
    out = bytearray()
    while n & ~0x7F:
        out.append((n & 0x7F) | 0x80); n >>= 7
    out.append(n & 0x7F)
    return bytes(out)


def _tag(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _string_field(field: int, val: str) -> bytes:
    b = val.encode("utf-8")
    return _tag(field, 2) + _varint(len(b)) + b


def _varint_field(field: int, val: int) -> bytes:
    return _tag(field, 0) + _varint(val)


def build_service_config2(mqtt_host: str, port: int = 8883,
                          gcp_project: str = "o", disable_verify: bool = True) -> bytes:
    """Serialize the ServiceConfiguration2 protobuf (4 fields)."""
    out = _string_field(1, gcp_project)
    out += _string_field(8, mqtt_host)
    out += _varint_field(11, port)
    out += _varint_field(12, 1 if disable_verify else 0)
    return out


def build_endpoint_qr(mqtt_host: str, port: int = 8883,
                      gcp_project: str = "o", disable_verify: bool = True) -> str:
    """Return the full QR #2 JSON string to display to Moxie's camera."""
    param = base64.b64encode(
        build_service_config2(mqtt_host, port, gcp_project, disable_verify)
    ).decode("ascii")
    return json.dumps({"debug": {"command": "om", "param": param}})


def decode_endpoint_qr(qr: str) -> dict:
    """Decode a QR #2 string back to its fields (for verification/debugging)."""
    outer = json.loads(qr)
    raw = base64.b64decode(outer["debug"]["param"])
    fields, i = {}, 0
    while i < len(raw):
        tag = raw[i]; i += 1
        field, wire = tag >> 3, tag & 7
        if wire == 2:
            n = raw[i]; i += 1
            val = raw[i:i + n]; i += n
            fields[field] = val.decode("utf-8", "replace")
        elif wire == 0:
            shift = res = 0
            while True:
                b = raw[i]; i += 1
                res |= (b & 0x7F) << shift
                if not (b & 0x80): break
                shift += 7
            fields[field] = res
    return {"gcp_project": fields.get(1), "mqtt_host": fields.get(8),
            "override_port": fields.get(11), "disable_verify": bool(fields.get(12, 0)),
            "command": outer["debug"]["command"]}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] not in ("--test", "-t"):
        host = sys.argv[1]
        port = int(sys.argv[2]) if len(sys.argv) > 2 else 8883
        print(build_endpoint_qr(host, port))
    else:
        # self-test against the known-good vector
        qr = build_endpoint_qr("192.168.1.50")
        print("QR:", qr)
        expected = '{"debug": {"command": "om", "param": "CgFvQgwxOTIuMTY4LjEuNTBYs0VgAQ=="}}'
        assert qr == expected, f"MISMATCH\n got: {qr}\n exp: {expected}"
        d = decode_endpoint_qr(qr)
        print("decoded:", d)
        assert d["mqtt_host"] == "192.168.1.50" and d["override_port"] == 8883 and d["disable_verify"]
        print("✅ matches known-good vector exactly")
