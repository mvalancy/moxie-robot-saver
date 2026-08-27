#!/usr/bin/env python3
"""
moxie_pair — generate a Moxie pairing QR code (clean-room, from the decompiled
parent app v2.2.2). Shows the QR that the robot's camera scans to receive WiFi
credentials + a pairing secret.

Examples:
  # PROTO mode (embeds an Ed25519 signing key; key auto-generated if omitted)
  ./moxie_pair.py --ssid HomeNet --password 's3cr3t!' --mode proto \
      --iot-endpoint 0 --out moxie_qr.png

  # supply your own 32-byte key seed (hex) so it matches what your server expects
  ./moxie_pair.py --ssid HomeNet --password pw --mode proto \
      --secret-key-hex 000102...1f --out qr.png

  # JSON mode (embeds a cloud user_token) — for a server that issues tokens
  ./moxie_pair.py --ssid HomeNet --password pw --mode json \
      --user-token "eyJhbGciOi..." --out qr.png

The QR is also printed to the terminal as ASCII so you can hold the phone/screen
up to the robot without saving a file.

Requires: segno  (pip install segno).  Key generation uses PyNaCl if available,
else Python's os.urandom (a raw 32-byte Ed25519 seed).
"""
import argparse, os, sys, binascii
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from moxie_qr import WifiInfo, Band, encode_proto, encode_json, decode_proto

def gen_seed() -> bytes:
    # The signing key embedded in the QR is an Ed25519 key (libsodium). A 32-byte
    # seed fully determines an Ed25519 keypair. See docs/ for how the server must
    # store SHA-256(key bytes) to match the app's registerForPairing() handshake.
    return os.urandom(32)

def main():
    ap = argparse.ArgumentParser(description="Generate a Moxie pairing QR code")
    ap.add_argument("--ssid", required=True)
    ap.add_argument("--password", default="")
    ap.add_argument("--band", choices=["any", "5g", "24g"], default="any")
    ap.add_argument("--hidden", action="store_true", help="SSID is hidden")
    ap.add_argument("--mode", choices=["proto", "json"], default="proto")
    ap.add_argument("--iot-endpoint", type=int, default=0,
                    help="proto: iot endpoint index handed to the robot")
    ap.add_argument("--secret-key-hex", help="proto: 32-byte Ed25519 seed as hex "
                                             "(default: random, printed out)")
    ap.add_argument("--hide-pair", action="store_true",
                    help="proto: omit the key (pair proto without secret)")
    ap.add_argument("--user-token", help="json: cloud user_token to embed")
    ap.add_argument("--dev", action="store_true", help="proto: set non-production dev flag")
    ap.add_argument("--out", help="write PNG here (e.g. qr.png)")
    ap.add_argument("--no-ascii", action="store_true", help="don't print QR to terminal")
    args = ap.parse_args()

    band = {"any": Band.ANY, "5g": Band.ONLY_5G, "24g": Band.ONLY_24G}[args.band]
    wifi = WifiInfo(args.ssid, args.password, is_hidden=args.hidden, band=band)

    if args.mode == "proto":
        if args.hide_pair:
            key = None
        elif args.secret_key_hex:
            key = binascii.unhexlify(args.secret_key_hex.strip())
        else:
            key = gen_seed()
            print(f"[*] generated Ed25519 seed (hex): {key.hex()}", file=sys.stderr)
            print(f"[*] register this with your server as SHA-256(key)=<hash>", file=sys.stderr)
        payload = encode_proto(wifi, key, hide_pair=args.hide_pair,
                               iot_endpoint=args.iot_endpoint, dev=args.dev)
    else:
        payload = encode_json(wifi, args.user_token)

    print(f"[*] QR payload ({args.mode}): {payload}", file=sys.stderr)

    try:
        import segno
    except ImportError:
        sys.exit("segno not installed — run: pip install segno")
    qr = segno.make(payload, error="m")
    if not args.no_ascii:
        qr.terminal(compact=True)
    if args.out:
        qr.save(args.out, scale=8, border=4)
        print(f"[*] wrote {args.out}", file=sys.stderr)

if __name__ == "__main__":
    main()
