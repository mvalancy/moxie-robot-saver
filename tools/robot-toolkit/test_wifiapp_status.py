#!/usr/bin/env python3
"""The bo-wifi setup-app status helpers in moxie_toolkit.bus: the registry, the recovered
WifiAppStatusCodes map (WifiAppReady=100 is "ready to scan a QR") and the status/bricked
field names. See docs/reverse-engineering/protocol/qr-commands.md (The setup app's runtime
status).

    python3 tools/robot-toolkit/test_wifiapp_status.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "moxie_toolkit"))
from _harness import ok, report  # noqa: E402

try:
    from embodied.wifiapp import WifiAppStatus_pb2 as S  # noqa: E402
    from embodied.wifiapp import WifiAppBricked_pb2 as B  # noqa: E402
    from moxie_toolkit import bus  # noqa: E402
except Exception as e:  # protobuf / bindings unavailable
    print(f"ℹ️  wifiapp-status toolkit test skipped — {e}")
    sys.exit(0)

registry = {bus.full_name(c) for c in bus.wifi_app_status_classes()}
ok(len(registry) == 4 and "embodied.unity.WifiAppStatus" in registry,
   f"wifiapp registry wrong: {sorted(registry)}")
codes = bus.WIFI_APP_STATUS_CODES
ok((codes.get(100), codes.get(1), codes.get(1977)) == ("WifiAppReady", "WifiAndUserGood", "Alive"),
   "status-code map incomplete")
S.WifiAppStatus(code=100)
B.WifiAppBricked(error_code=2)

report("wifiapp-status", "registry + WIFI_APP_STATUS_CODES + WifiAppStatus/WifiAppBricked fields")
