#!/usr/bin/env python3
"""Run the Moxie robot-cloud supervisor. Reads config.py (env-overridable)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "supervisor"))
import config
from moxie_sdk import voice_settings
from moxie_sdk.types import ChildProfile
from supervisor.moxie_runtime import MoxieRuntime


def _voice_line(kind, choice, engine):
    """The 🎚️ startup line: which engine was installed and WHY it is that one.

    `speech: piper-amy (gateway, chosen)` when a parent picked it in the console;
    `speech: <describe()> (env default — nothing picked in the console)` otherwise, which
    is every deployment that has never opened the card. "No engine" is said out loud
    rather than left as an absent line, because silence is the failure people report.
    """
    desc = engine.describe() if engine is not None else "none"
    if choice:
        return voice_settings.boot_line(kind, choice, chosen=True, note=desc)
    return f"{kind}: {desc} (env default — nothing picked in the console)"


def assemble(config):
    """Build the full runtime from config: the brain + optional STT + optional voice."""
    child = ChildProfile(nickname=config.CHILD_NICKNAME)
    rt = MoxieRuntime(config.build_app(), host=config.MQTT_HOST,
                      port=config.MQTT_PORT, child=child,
                      brain_budget_s=config.BRAIN_BUDGET_S,
                      streaming=config.STREAMING)
    # 🎚️ The console's voice picker (backlog/voice-picker.md). The engine builders and the
    # cached gateway discovery are handed to the runtime here — it never imports `config`
    # itself — and the fleet record is read BEFORE either engine is built, so a choice a
    # parent made in the console survives a restart. Nothing stored → `override=None`,
    # i.e. exactly the env-driven precedence this file has always had.
    rt.set_voice_engines(config.voice_engines())
    picked = voice_settings.read_settings(rt.store)
    synth = config.build_synthesizer(override=picked.get(voice_settings.SPEECH))
    if synth:
        rt.set_synthesizer(synth)
        # `describe()` rather than `.name`: a gateway voice is wrapped in a
        # FallbackSynthesizer, whose own name is the wrapper's — the startup log should
        # say which voice is speaking and what stands by if it fails.
        print(f"[run] server voice enabled: {synth.describe()}")
    print(f"[run] 🎚️ {_voice_line(voice_settings.SPEECH, picked.get(voice_settings.SPEECH), synth)}")
    trans = config.build_transcriber(override=picked.get(voice_settings.LISTENING))
    if trans:
        rt.set_transcriber(trans)
        # `describe()` rather than `.name`: the gateway ears are wrapped in a
        # FallbackTranscriber, whose own name is the wrapper's — the startup log should
        # say which engine is listening, on which model, and what stands by behind it.
        print(f"[run] STT enabled: {trans.describe()}")
    print(f"[run] 🎚️ {_voice_line(voice_settings.LISTENING, picked.get(voice_settings.LISTENING), trans)}")
    return rt


if __name__ == "__main__":
    rt = assemble(config)
    print(f"[run] Moxie runtime · app={rt.app.name} · broker={config.MQTT_HOST}:{config.MQTT_PORT}")
    rt.run(status_port=config.STATUS_PORT)
