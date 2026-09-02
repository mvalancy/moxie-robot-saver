#!/usr/bin/env python3
"""Run the Moxie robot-cloud supervisor. Reads config.py (env-overridable)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "supervisor"))
import config
from moxie_sdk.types import ChildProfile
from supervisor.moxie_runtime import MoxieRuntime

def assemble(config):
    """Build the full runtime from config: the brain + optional STT + optional voice."""
    child = ChildProfile(nickname=config.CHILD_NICKNAME)
    rt = MoxieRuntime(config.build_app(), host=config.MQTT_HOST,
                      port=config.MQTT_PORT, child=child,
                      brain_budget_s=config.BRAIN_BUDGET_S)
    synth = config.build_synthesizer()
    if synth:
        rt.set_synthesizer(synth)
        print(f"[run] server voice enabled: {synth.name}")
    trans = config.build_transcriber()
    if trans:
        rt.set_transcriber(trans)
        print(f"[run] STT enabled: {trans.name}")
    return rt


if __name__ == "__main__":
    rt = assemble(config)
    print(f"[run] Moxie runtime · app={rt.app.name} · broker={config.MQTT_HOST}:{config.MQTT_PORT}")
    rt.run(status_port=config.STATUS_PORT)
