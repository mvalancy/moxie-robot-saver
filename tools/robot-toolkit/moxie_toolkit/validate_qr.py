"""
QR validation suite — replaces acoustic brute-forcing with schema round-trip validation.

For every QR type the robot accepts, we encode -> decode -> assert the fields survive, exactly as
`bo-wifi`'s QRData.ParseFromString would parse them. This proves our encoders emit codes the robot
will actually understand, without touching hardware. Run: python -m moxie_toolkit.validate_qr
"""
import sys, os, base64, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/moxie_toolkit")
from moxie_toolkit import qr_codec as qc

PASS, FAIL = 0, 0
def check(name, cond, detail=""):
    global PASS, FAIL
    ok = bool(cond)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail and not ok else ""))
    PASS += ok; FAIL += (not ok)

def main():
    print("Moxie QR validation (schema round-trip)\n")

    print("Pairing QR (PA + StartPairingQR):")
    p = qc.encode_pairing(ssid="MyWifi", password="pw12345", secret_key=bytes(range(8)),
                          is_staging=True, band_select=qc.WifiBandSelect.ONLY_50G,
                          endpoint=qc.IOTEndpoint.OPEN_MOXIE)
    check("prefix is PA", p.startswith("PA"))
    d = qc.decode(p)
    check("decodes as pair", d["type"] == "pair")
    check("ssid round-trips", d["proto"].ssid == "MyWifi")
    check("password round-trips", d["proto"].password == "pw12345")
    check("secret_key round-trips", d["proto"].secret_key == bytes(range(8)))
    check("is_staging round-trips", d["proto"].is_staging is True)
    check("band_select round-trips", d["proto"].band_select == qc.WifiBandSelect.ONLY_50G)
    check("endpoint=OPEN_MOXIE round-trips", d["proto"].endpoint == qc.IOTEndpoint.OPEN_MOXIE)
    check("base64 body is valid", base64.b64encode(base64.b64decode(p[2:])).decode() == p[2:])

    print("\nVPN QR (VN + QRVPNConfig):")
    v = qc.encode_vpn(qc.VPNCommand.VPN_ACTIVATE, vpn_id="home", url="https://vpn.example/cfg.ovpn",
                      username="u", password="pw", connect=True)
    check("prefix is VN", v.startswith("VN"))
    dv = qc.decode(v)
    check("decodes as vpn", dv["type"] == "vpn")
    check("command round-trips", dv["proto"].command == qc.VPNCommand.VPN_ACTIVATE)
    check("url round-trips", dv["proto"].url == "https://vpn.example/cfg.ovpn")
    check("connect round-trips", dv["proto"].connect is True)

    print("\nWi-Fi JSON QR:")
    w = qc.encode_wifi("HomeNet", "s3cret!", is_hidden=True, band_select="ONLY_24G")
    dw = qc.decode(w)
    check("decodes as json", dw["type"] == "json")
    check("wifi.ssid present", dw["wifi"]["ssid"] == "HomeNet")
    check("wifi.password present", dw["wifi"]["password"] == "s3cret!")
    check("is_hidden present", dw["wifi"]["is_hidden"] is True)

    print("\nDebug/factory command QRs:")
    for cmd in qc.KNOWN_DEBUG_COMMANDS:
        s = qc.encode_debug(cmd, "PARAM")
        ds = qc.decode(s)
        check(f"debug '{cmd}' round-trips",
              ds["type"] == "json" and ds["debug"]["command"] == cmd and ds["debug"]["param"] == "PARAM")
    eu = qc.encode_endpoint_update("OPEN_MOXIE")
    deu = qc.decode(eu)
    check("endpoint_update carries OPEN_MOXIE",
          deu["debug"]["command"] == "endpoint_update" and deu["debug"]["param"] == "OPEN_MOXIE")

    print("\nNegative cases (must be rejected the way the app would):")
    try:
        qc.decode("not-json-and-no-prefix{{"); check("garbage rejected", False)
    except Exception:
        check("garbage rejected", True)
    check("null rejected", qc.decode(None)["type"] == "invalid")

    print("\nCross-check vs phone-side encoder (tools/pairing/moxie_qr.py):")
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "pairing"))
        import moxie_qr as m
        wifi = m.WifiInfo(ssid="HomeNet", password="s3cret!", band=m.Band.ONLY_5G)
        key = bytes(range(32))
        their = m.encode_proto(wifi, secret_key=key, iot_endpoint=qc.IOTEndpoint.OPEN_MOXIE)
        mine  = qc.encode_pairing(ssid="HomeNet", password="s3cret!", secret_key=key,
                                  band_select=qc.WifiBandSelect.ONLY_50G, endpoint=qc.IOTEndpoint.OPEN_MOXIE)
        check("PA payload byte-identical to phone-side tool", their == mine,
              f"{their!r} != {mine!r}")
    except Exception as e:
        check("phone-side cross-check ran", False, str(e))

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0

if __name__ == "__main__":
    sys.exit(main())
