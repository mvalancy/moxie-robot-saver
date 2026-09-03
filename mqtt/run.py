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

    The per-robot layer is resolved per turn (`MoxieRuntime.app_for`); the *boot* brain is
    the two layers that are known before any robot connects. Reading the fleet layer here
    is what makes a house rule survive a restart — the same reason `assemble` reads
    `fleet/voice.json` before building either voice engine — and it is what keeps a box
    whose fleet brain is `echo` from exiting at startup over an `MOXIE_LLM_BASE_URL` that
    nothing on it was ever going to use.
    """
    fleet = store.read_shared(MoxieRuntime.FLEET_CONFIG_COLLECTION, {}) or {}
    return brains.resolve_brain(
        default=config.default_brain(),
        fleet=(fleet.get(brains.CONFIG_KEY) if isinstance(fleet, dict) else None),
        pin=config.brain_pin())


def _voice_line(kind, choice, engine, pin=""):
    """The 🎚️ startup line: which engine was installed and WHY it is that one.

    `speech: piper-amy (gateway, chosen)` when a parent picked it in the console;
    `speech: <describe()> (env default — nothing picked in the console)` otherwise, which
    is every deployment that has never opened the card. "No engine" is said out loud
    rather than left as an absent line, because silence is the failure people report.

    `pin` is what an explicit `MOXIE_TTS`/`MOXIE_STT` allows. A stored pick that the pin
    ignores must NOT print as `chosen` — the operator's environment won, and a log that
    named the pick would send someone hunting for a swap that never happened.
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
    # 🧠 The brain picker (`moxie_sdk/brains.py`). The store is built first because the
    # boot brain is `defaults ⊕ fleet`, and the builders are handed over so the runtime can
    # bring up another brain for another child later without ever importing `config`.
    store = JsonStore()
    # One line, once, if this platform cannot do cross-process store locking (no `fcntl`).
    # A silent downgrade is how somebody ships a Windows appliance believing two processes
    # are safe on one data directory — production-hardening.md §3.3 #4.
    store_mod.warn_no_locking()
    booted = boot_brain(config, store)
    rt = MoxieRuntime(config.build_brain(booted["brain"]), host=config.MQTT_HOST,
                      port=config.MQTT_PORT, child=child, store=store,
                      brain_budget_s=config.BRAIN_BUDGET_S,
                      streaming=config.STREAMING)
    rt.set_brain_engines(config.brain_engines())
    print(f"[run] 🧠 {brains.boot_line(booted)}")
    # 🎚️ The console's voice picker (backlog/voice-picker.md). The engine builders and the
    # cached gateway discovery are handed to the runtime here — it never imports `config`
    # itself — and the fleet record is read BEFORE either engine is built, so a choice a
    # parent made in the console survives a restart. Nothing stored → `override=None`,
    # i.e. exactly the env-driven precedence this file has always had.
    rt.set_voice_engines(config.voice_engines())
    picked = voice_settings.read_settings(rt.store)
    pins = config.engine_pins()          # what an explicit MOXIE_TTS/MOXIE_STT allows
    synth = config.build_synthesizer(override=picked.get(voice_settings.SPEECH))
    if synth:
        rt.set_synthesizer(synth)
        # `describe()` rather than `.name`: a gateway voice is wrapped in a
        # FallbackSynthesizer, whose own name is the wrapper's — the startup log should
        # say which voice is speaking and what stands by if it fails.
        print(f"[run] server voice enabled: {synth.describe()}")
    print(f"[run] 🎚️ {_voice_line(voice_settings.SPEECH, picked.get(voice_settings.SPEECH), synth, pins[voice_settings.SPEECH])}")
    trans = config.build_transcriber(override=picked.get(voice_settings.LISTENING))
    if trans:
        rt.set_transcriber(trans)
        # `describe()` rather than `.name`: the gateway ears are wrapped in a
        # FallbackTranscriber, whose own name is the wrapper's — the startup log should
        # say which engine is listening, on which model, and what stands by behind it.
        print(f"[run] STT enabled: {trans.describe()}")
    print(f"[run] 🎚️ {_voice_line(voice_settings.LISTENING, picked.get(voice_settings.LISTENING), trans, pins[voice_settings.LISTENING])}")
    return rt


if __name__ == "__main__":
    rt = assemble(config)
    print(f"[run] Moxie runtime · app={rt.app.name} · broker={config.MQTT_HOST}:{config.MQTT_PORT}")
    rt.run(status_port=config.STATUS_PORT)
