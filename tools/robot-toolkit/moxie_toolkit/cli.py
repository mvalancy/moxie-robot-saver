"""moxie-qr CLI: generate / decode / validate Moxie QR codes, optionally render PNG/SVG.

Examples:
  python -m moxie_toolkit.cli endpoint OPEN_MOXIE --png redirect.png
  python -m moxie_toolkit.cli debug reset_network
  python -m moxie_toolkit.cli pair --ssid HomeNet --password s3cret --endpoint OPEN_MOXIE --secret-hex 00112233
  python -m moxie_toolkit.cli vpn VPN_ACTIVATE --url https://vpn/cfg --connect --png vpn.png
  python -m moxie_toolkit.cli decode 'PA...'    # or a JSON string
  python -m moxie_toolkit.cli validate
"""
import argparse, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/moxie_toolkit")
from moxie_toolkit import qr_codec as qc

def render(data, path):
    try:
        import segno
    except ImportError:
        print("(segno not installed; skipping image)", file=sys.stderr); return
    segno.make(data, error="m").save(path, scale=8, border=2)
    print(f"wrote {path}", file=sys.stderr)

def main(argv=None):
    ap = argparse.ArgumentParser(prog="moxie-qr")
    sub = ap.add_subparsers(dest="cmd", required=True)
    def add_png(p): p.add_argument("--png"); p.add_argument("--svg")

    pp = sub.add_parser("pair"); add_png(pp)
    pp.add_argument("--ssid", default=""); pp.add_argument("--password", default="")
    pp.add_argument("--secret-hex", default=""); pp.add_argument("--endpoint", default="IOT_DEFAULT")
    pp.add_argument("--band", default="ANY"); pp.add_argument("--staging", action="store_true")
    pp.add_argument("--wifi-only", action="store_true"); pp.add_argument("--hidden", action="store_true")

    wp = sub.add_parser("wifi"); add_png(wp)
    wp.add_argument("--ssid", required=True); wp.add_argument("--password", default="")
    wp.add_argument("--band", default="ANY"); wp.add_argument("--hidden", action="store_true")

    dp = sub.add_parser("debug"); add_png(dp)
    dp.add_argument("command"); dp.add_argument("param", nargs="?", default="")

    ep = sub.add_parser("endpoint"); add_png(ep); ep.add_argument("endpoint")

    vp = sub.add_parser("vpn"); add_png(vp)
    vp.add_argument("command"); vp.add_argument("--vpn-id", default=""); vp.add_argument("--url", default="")
    vp.add_argument("--username", default=""); vp.add_argument("--password", default=""); vp.add_argument("--connect", action="store_true")

    xp = sub.add_parser("decode"); xp.add_argument("data")
    sub.add_parser("validate")
    sub.add_parser("list-commands")

    a = ap.parse_args(argv)
    data = None
    if a.cmd == "pair":
        data = qc.encode_pairing(a.ssid, a.password, bytes.fromhex(a.secret_hex) if a.secret_hex else b"",
                                 is_staging=a.staging, wifi_only=a.wifi_only, is_hidden=a.hidden,
                                 band_select=qc.WifiBandSelect.Value(a.band), endpoint=qc.IOTEndpoint.Value(a.endpoint))
    elif a.cmd == "wifi":
        data = qc.encode_wifi(a.ssid, a.password, is_hidden=a.hidden, band_select=a.band)
    elif a.cmd == "debug":
        data = qc.encode_debug(a.command, a.param)
    elif a.cmd == "endpoint":
        data = qc.encode_endpoint_update(a.endpoint)
    elif a.cmd == "vpn":
        data = qc.encode_vpn(qc.VPNCommand.Value(a.command), vpn_id=a.vpn_id, url=a.url,
                             username=a.username, password=a.password, connect=a.connect)
    elif a.cmd == "decode":
        d = qc.decode(a.data)
        if "proto" in d: print(d["type"], "\n", d["proto"])
        else: print(d)
        return 0
    elif a.cmd == "validate":
        from moxie_toolkit import validate_qr; return validate_qr.main()
    elif a.cmd == "list-commands":
        print("Debug/factory command codes:")
        for k, v in qc.KNOWN_DEBUG_COMMANDS.items(): print(f"  {k:24} {v}")
        print("\nIOTEndpoints:", ", ".join(v.name for v in qc.IOTEndpoint.DESCRIPTOR.values))
        return 0
    print(data)
    if getattr(a, "png", None): render(data, a.png)
    if getattr(a, "svg", None): render(data, a.svg)
    return 0

if __name__ == "__main__":
    sys.exit(main())
