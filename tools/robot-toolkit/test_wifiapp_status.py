#!/usr/bin/env python3
"""Round-trip test for the bo-wifi setup-app status helpers in moxie_toolkit.bus.

Builds a WifiAppStatus (the WifiAppReady=100 "ready to scan a QR" signal) and a
WifiAppBricked (setup-app failure), frames + re-parses them via the bus registry, and
checks the WIFI_APP_STATUS_CODES map. See
docs/reverse-engineering/qr-commands.md (The setup app's runtime status).

    python3 tools/robot-toolkit/test_wifiapp_status.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "moxie_toolkit"))

try:
    from embodied.wifiapp import WifiAppStatus_pb2 as S  # noqa: E402
    from embodied.wifiapp import WifiAppBricked_pb2 as B  # noqa: E402
    from moxie_toolkit import bus  # noqa: E402
except Exception as e:  # protobuf / bindings unavailable
    print(f"ℹ️  wifiapp-status toolkit test skipped — {e}")
    sys.exit(0)

fails = []
def ok(cond, msg):
    if not cond:
        fails.append(msg)

registry = {bus.full_name(c): c for c in bus.wifi_app_status_classes()}
ok(len(registry) == 4, f"expected 4 wifiapp status classes, got {len(registry)}")
ok("embodied.unity.WifiAppStatus" in registry, f"WifiAppStatus not registered: {list(registry)}")

# the status-code map is the recovered WifiAppStatusCodes enum
ok(bus.WIFI_APP_STATUS_CODES.get(100) == "WifiAppReady", "100 should be WifiAppReady")
ok(bus.WIFI_APP_STATUS_CODES.get(1) == "WifiAndUserGood" and bus.WIFI_APP_STATUS_CODES.get(1977) == "Alive",
   "status-code map incomplete")

# WifiAppReady round-trips through the bus framing
st = S.WifiAppStatus(code=100)
fn = bus.full_name(st)
rt = registry[fn](); rt.ParseFromString(st.SerializeToString())
ok(rt.code == 100 and bus.WIFI_APP_STATUS_CODES[rt.code] == "WifiAppReady",
   "WifiAppStatus(WifiAppReady) round-trip failed")

# a bricked setup app carries an error_code
br = B.WifiAppBricked(error_code=2)
rbr = B.WifiAppBricked(); rbr.ParseFromString(br.SerializeToString())
ok(rbr.error_code == 2, "WifiAppBricked error_code lost")

if fails:
    print("❌ wifiapp-status toolkit test FAILED:")
    for f in fails:
        print("   -", f)
    sys.exit(1)
print("✅ wifiapp-status toolkit test OK — WifiAppStatus(WifiAppReady=100) + WifiAppBricked(error_code) "
      "round-trip + WIFI_APP_STATUS_CODES map through the bo-wifi setup-app status protos")
