#!/usr/bin/env python3
"""
Render the *validated* Moxie QR codes (from tools/robot-toolkit) as PNGs for display to a robot.

This is the pivot of this rig: instead of acoustically brute-forcing unknown codes, we now display
codes we KNOW the firmware parses (proven by tools/robot-toolkit schema round-trip + byte-parity),
and observe the robot's real reaction. Blind sweeping is deprecated — see README.

    python3 validated_codes.py --out ./deck        # writes one PNG per known-good code
"""
import argparse, os, sys
TOOLKIT = os.path.join(os.path.dirname(__file__), "..", "robot-toolkit")
sys.path.insert(0, os.path.join(TOOLKIT, "moxie_toolkit"))
sys.path.insert(0, TOOLKIT)
from moxie_toolkit import qr_codec as qc

def deck():
    d = {}
    d["endpoint_OPEN_MOXIE"]  = qc.encode_endpoint_update("OPEN_MOXIE")
    d["endpoint_EMBODIED_LOCAL"] = qc.encode_endpoint_update("EMBODIED_LOCAL")
    for cmd in qc.KNOWN_DEBUG_COMMANDS:
        d[f"debug_{cmd}"] = qc.encode_debug(cmd)
    d["wifi_example"] = qc.encode_wifi("HomeNet", "changeme")
    return d

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="./deck")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    try:
        import segno
    except ImportError:
        print("pip install segno to render PNGs; printing strings instead:\n")
        for k, v in deck().items(): print(f"{k}: {v}")
        return
    for k, v in deck().items():
        p = os.path.join(a.out, k + ".png")
        segno.make(v, error="m").save(p, scale=10, border=3)
        print(f"{p}\n    {v}")

if __name__ == "__main__":
    main()
