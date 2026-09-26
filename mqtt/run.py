#!/usr/bin/env python3
"""Run the Moxie robot-cloud supervisor. Reads config.py (env-overridable)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "supervisor"))
import config
from moxie_sdk import brains, voice_settings
from moxie_sdk import store as store_mod
from moxie_sdk.store import JsonStore
from moxie_sdk.types import ChildProfile
from supervisor.moxie_runtime import MoxieRuntime


def boot_brain(config, store):
    """Which brain this appliance boots with — `defaults ⊕ fleet`, under the env's pin.

    The per-robot layer is resolved per turn (`MoxieRuntime.app_for`). Reading the fleet
    layer here makes a house rule survive a restart, and keeps a fleet `echo` box from
    demanding an `MOXIE_LLM_BASE_URL` it will never use.
    """
    fleet = store.read_shared(MoxieRuntime.FLEET_CONFIG_COLLECTION, {}) or {}
    return brains.resolve_brain(
        default=config.default_brain(),
        fleet=(fleet.get(brains.CONFIG_KEY) if isinstance(fleet, dict) else None),
        pin=config.brain_pin())


def _voice_line(kind, choice, engine, pin=""):
    """The 🎚️ startup line: which engine was installed and why ("none" is said aloud).

    A stored pick that an explicit `MOXIE_TTS`/`MOXIE_STT` pin overrides must not print as
    `chosen` — the environment won.
    """
    desc = engine.describe() if engine is not None else "none"
    if choice and not voice_settings.honours_pin(kind, choice, pin):
        return (f"{kind}: {desc} ({voice_settings.ENV_VAR[kind]}={pin} pins the engine — "
                f"the console's {voice_settings.choice_id(choice)} is not installed)")
    if choice:
        return voice_settings.boot_line(kind, choice, chosen=True, note=desc)
    return f"{kind}: {desc} (env default — nothing picked in the console)"


def assemble(config):
    """Build the full runtime from config: the brain + optional STT + optional voice."""
    child = ChildProfile(nickname=config.CHILD_NICKNAME)
    # 🧠 The store comes first (boot brain = defaults ⊕ fleet); builders are handed to the
    # runtime so it can build other brains per child without importing `config`.
    store = JsonStore()
    # Say once if this platform has no cross-process store locking (production-hardening §3.3).
    store_mod.warn_no_locking()
    booted = boot_brain(config, store)
    rt = MoxieRuntime(config.build_brain(booted["brain"]), host=config.MQTT_HOST,
                      port=config.MQTT_PORT, child=child, store=store,
                      brain_budget_s=config.BRAIN_BUDGET_S,
                      streaming=config.STREAMING)
    rt.set_brain_engines(config.brain_engines())
    print(f"[run] 🧠 {brains.boot_line(booted)}")
    # 🎚️ Voice picker (backlog/voice-picker.md): the fleet pick is read before building
    # either engine so it survives a restart; nothing stored → env-driven precedence.
    rt.set_voice_engines(config.voice_engines())
    picked = voice_settings.read_settings(rt.store)
    pins = config.engine_pins()          # what an explicit MOXIE_TTS/MOXIE_STT allows
    synth = config.build_synthesizer(override=picked.get(voice_settings.SPEECH))
    if synth:
        rt.set_synthesizer(synth)
        # describe(): names the wrapped voice and its standby, not the wrapper.
        print(f"[run] server voice enabled: {synth.describe()}")
    print(f"[run] 🎚️ {_voice_line(voice_settings.SPEECH, picked.get(voice_settings.SPEECH), synth, pins[voice_settings.SPEECH])}")
    trans = config.build_transcriber(override=picked.get(voice_settings.LISTENING))
    if trans:
        rt.set_transcriber(trans)
        # describe(): names the wrapped ears and their standby, not the wrapper.
        print(f"[run] STT enabled: {trans.describe()}")
    print(f"[run] 🎚️ {_voice_line(voice_settings.LISTENING, picked.get(voice_settings.LISTENING), trans, pins[voice_settings.LISTENING])}")
    return rt


if __name__ == "__main__":
    rt = assemble(config)
    print(f"[run] Moxie runtime · app={rt.app.name} · broker={config.MQTT_HOST}:{config.MQTT_PORT}")
    rt.run(status_port=config.STATUS_PORT)
