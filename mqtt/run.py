#!/usr/bin/env python3
"""Run the Moxie robot-cloud supervisor. Reads config.py (env-overridable)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "supervisor"))
import config
from moxie_sdk.types import ChildProfile
from supervisor.moxie_runtime import MoxieRuntime

if __name__ == "__main__":
    app = config.build_app()
    child = ChildProfile(nickname=config.CHILD_NICKNAME)
    print(f"[run] Moxie runtime · app={app.name} · broker={config.MQTT_HOST}:{config.MQTT_PORT}")
    MoxieRuntime(app, host=config.MQTT_HOST, port=config.MQTT_PORT, child=child).run(
        status_port=config.STATUS_PORT)
