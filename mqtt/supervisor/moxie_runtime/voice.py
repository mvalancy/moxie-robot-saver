"""Server voice (TTS), the voice picker, and the STT extension point."""
from __future__ import annotations
import json, time

from moxie_sdk import telehealth as telehealth_seam
from moxie_sdk import voice_settings as voice_seam
from markup import make_markup


class VoiceMixin:
    # ---- TTS (AI seam §3) — server voice for the SIM ----
    def set_synthesizer(self, synth):
        """Install a server-side TTS engine (moxie_sdk.tts.Synthesizer). The SIM plays
        the resulting audio; a real robot self-synthesizes so this is SIM-only."""
        self._synth = synth

    def _maybe_synthesize(self, device_id, markup, event_id="", chunk_num=0):
        """If a synthesizer is set, render the line and publish a CloudTTSResponse to
        /devices/{id}/commands/tts. TTS failure never breaks the turn. `chunk_num` keeps
        a multi-chunk turn (filler then answer) in playback order for the client."""
        if self._synth is None:
            return None
        try:
            from moxie_sdk.tts import synthesize_cloud_tts
            resp = synthesize_cloud_tts(self._synth, markup, event_id=event_id,
                                        chunk_num=chunk_num)
            self._publish(f"/devices/{device_id}/commands/tts", resp,
                          device_id=device_id, what="tts")
            return resp
        except Exception as e:
            print(f"[runtime] TTS synth failed (non-fatal): {e}", flush=True)
            return None

    # ---- 🎚️ the voice picker (backlog/voice-picker.md) ----
    # Two dropdowns — **Speech** and **Listening** — over what this appliance can really
    # use: the gateway's audio models, the local engines installed on the box, and the two
    # built-ins. The pick is FLEET-level (`fleet/voice.json`), because a voice is a
    # property of the house rather than of one robot, and it survives a restart because
    # `run.py` reads the same record before it builds either engine.
    #
    # Two properties are load-bearing and neither costs the turn loop anything:
    #   * **Discovery never blocks a turn.** `voice_settings.GatewayCatalog` caches one
    #     `GET /v1/models` for `MOXIE_VOICE_DISCOVERY_TTL_S` and refreshes it on a
    #     background thread; the first call after boot answers with the local entries and
    #     `discovering: true`.
    #   * **A swap takes effect on the NEXT turn.** `set_synthesizer` / `set_transcriber`
    #     rebind one attribute; a turn already in flight finishes on the engine it started
    #     with. That is the whole reason there is no lock inside the turn loop — the lock
    #     below serializes concurrent *swaps*, nothing else.
    #
    # And one thing the card is NOT allowed to do: overrule the operator. An explicit
    # `MOXIE_TTS`/`MOXIE_STT` pins the engine (`voice_settings.pin_for_env`), the pinned
    # side's dropdown offers only that engine's entries, and `pin_notes` carries the
    # sentence that says which variable did it. A picker that silently moved a deployment
    # off local Piper would be a bug — see the owner rule in `voice_settings`' header.

    DEFAULT_VOICE_TEST_LINE = "Hi, I'm Moxie."
    #: How long a console WRITE may wait for the first gateway listing (seconds). Only
    #: `voice_update` uses it — see `_voice_discovery`. Generous because it is paid once,
    #: by a parent who just pressed Save, and the alternative is refusing their pick.
    VOICE_SETTLE_S = 10.0

    def set_voice_engines(self, engines):
        """Install the appliance's engine builders + discovery (`config.voice_engines()`).

        Without one the picker still works and offers `tone` / `off` — an honest floor
        rather than a card that claims models this box cannot build."""
        self._voice_engines = engines

    def _voice_discovery(self, *, refresh: bool = False,
                         settle_s: float = 0.0) -> dict:
        """`{available, discovering, gateway_error}` — never raises.

        A discovery that throws is reported as `gateway_error` beside the local entries,
        because a card that empties itself when a proxy hiccups is worse than one that
        says the gateway is unreachable next to the options it already had.

        `settle_s` is the only way this waits, it is bounded, and only `voice_update`
        passes it: a WRITE has to be judged against the real list, or a supervisor that
        booted three seconds ago refuses a perfectly good pick with "choose one of: tone"
        (seen live on 2026-09-02). Reads — the card's poll, and anything a turn touches —
        pass 0 and get whatever is cached, instantly.
        """
        blank = {k: "" for k in voice_seam.KINDS}
        engines = self._voice_engines
        if engines is None:
            return {"available": voice_seam.build_available(), "discovering": False,
                    "gateway_error": "", "pins": dict(blank), "pin_notes": dict(blank)}
        try:
            out = engines.available(refresh=refresh, settle_s=settle_s)
        except Exception as e:              # noqa: BLE001 — any failure is local-only
            return {"available": voice_seam.build_available(), "discovering": False,
                    "gateway_error": type(e).__name__,
                    "pins": dict(blank), "pin_notes": dict(blank)}

        def _side(field):
            src = out.get(field) if isinstance(out.get(field), dict) else {}
            return {k: str(src.get(k) or "") for k in voice_seam.KINDS}

        # `pins`/`pin_notes` are what an explicit `MOXIE_TTS`/`MOXIE_STT` has taken off
        # the table (`config.VoiceEngines.available`). They travel with the availability
        # they explain, so the card can never show a filtered list without its reason.
        return {"available": out.get("available") or voice_seam.build_available(),
                "discovering": bool(out.get("discovering")),
                "gateway_error": str(out.get("gateway_error") or ""),
                "pins": _side("pins"), "pin_notes": _side("pin_notes")}

    def voice_settings(self) -> dict:
        """The stored fleet record (`fleet/voice.json`) — `{}` when nobody has picked."""
        return voice_seam.read_settings(self.store)

    def voice_view(self, *, refresh: bool = False) -> dict:
        """What the 🎚️ card renders: every option, which one is in force, which one is
        the default, whether discovery is still running and whether the gateway answered.

        `current` is what is IN FORCE — a stored pick when there is one, otherwise the
        default computed from this moment's availability. A stored pick the gateway can no
        longer confirm stays current on purpose (`voice_settings.sanitize_choice`): an
        outage must not silently revert a parent's choice.
        """
        disc = self._voice_discovery(refresh=refresh)
        stored = voice_seam.read_settings(self.store)
        resolved = voice_seam.resolve_settings(stored, disc["available"])
        installed = {
            voice_seam.SPEECH: (self._synth.describe() if self._synth is not None else ""),
            voice_seam.LISTENING: (self._transcriber.describe()
                                   if self._transcriber is not None else ""),
        }
        return {"ok": True,
                "available": voice_seam.mark_defaults(disc["available"],
                                                      resolved["defaults"]),
                "current": resolved["current"], "defaults": resolved["defaults"],
                "chosen": resolved["chosen"],
                "selected": {k: voice_seam.choice_id(resolved["current"][k])
                             for k in voice_seam.KINDS},
                "labels": {k: voice_seam.describe_choice(resolved["current"][k])
                           for k in voice_seam.KINDS},
                "installed": installed,
                "pins": disc["pins"], "pin_notes": disc["pin_notes"],
                "discovering": disc["discovering"],
                "gateway_error": disc["gateway_error"],
                "updated_at": int(stored.get("updated_at") or 0),
                "robots": [d for d in self.robots if self.is_permitted(d)]}

    def voice_update(self, patch) -> dict:
        """Persist a parent's pick and swap the live engines to match.

        The patch is checked against what is available RIGHT NOW
        (`normalize_voice_settings`), so a stale page cannot install a model this gateway
        stopped serving; the refusal carries the sentence the card shows. Order —
        validate, persist, install — means a supervisor that dies mid-swap comes back with
        the choice a parent was told was saved.
        """
        with self._voice_lock:
            disc = self._voice_discovery(settle_s=self.VOICE_SETTLE_S)
            stored = voice_seam.read_settings(self.store)
            try:
                settings = voice_seam.normalize_voice_settings(
                    patch, disc["available"], current=stored)
            except ValueError as e:
                # A refusal that names only the surviving options reads as "the gateway
                # lost your voice"; when the environment is what removed it, say that.
                notes = " ".join(n for k, n in sorted(disc["pin_notes"].items())
                                 if n and k in (patch if isinstance(patch, dict) else {}))
                why = f"{e} {notes}".strip()
                return {"ok": False, "error": why, "reason": why}
            voice_seam.write_settings(self.store, settings)
            resolved = voice_seam.resolve_settings(settings, disc["available"])
            applied = self._install_voice(resolved["current"], chosen=resolved["chosen"],
                                          pins=disc["pins"])
        out = self.voice_view()
        out["applied"] = applied
        return out

    def _install_voice(self, current: dict, *, chosen: dict | None = None,
                       pins: dict | None = None) -> dict:
        """Build both engines for `current` and bind them. Returns one report per side.

        **A build that fails keeps the engine that is already speaking.** Losing the voice
        because a newly chosen one could not be constructed would be a downgrade caused by
        an *attempt to improve things*, which is the worst shape a failure can take. `off`
        is the one intentional `None`, so it is spelled out rather than inferred.

        `pins` is what `MOXIE_TTS`/`MOXIE_STT` allow (`config.engine_pins`). It changes
        nothing here — the builders enforce it themselves — but a choice the pin will
        ignore gets a note saying so, because a log line reading `speech: piper-ryan
        (gateway, chosen)` next to a box that is speaking with Piper is a lie.
        """
        chosen, pins = chosen or {}, pins or {}
        engines = self._voice_engines
        report = {}
        for kind in voice_seam.KINDS:
            choice = current.get(kind) or voice_seam.make_choice(
                voice_seam.BUILTIN_ENGINE[kind])
            engine, note = None, ""
            if not voice_seam.honours_pin(kind, choice, pins.get(kind) or ""):
                note = (f"{voice_seam.ENV_VAR[kind]} pins the engine to "
                        f"{pins.get(kind)} — this pick is not installed")
            if engines is None:
                note = "no engine builders installed (set_voice_engines)"
            else:
                build = (engines.build_speech if kind == voice_seam.SPEECH
                         else engines.build_listening)
                try:
                    engine = build(dict(choice))
                except SystemExit as e:      # an env engine that refuses to be built
                    note = str(e)
                except Exception as e:       # noqa: BLE001 — a bad pick must not kill us
                    note = f"{type(e).__name__}: {e}"
            silent = choice["engine"] in ("off",)
            if engine is not None or silent:
                if kind == voice_seam.SPEECH:
                    self.set_synthesizer(engine)
                else:
                    self.set_transcriber(engine)
            elif not note:
                note = "could not be built on this box — keeping the current engine"
            line = voice_seam.boot_line(kind, choice, chosen=bool(chosen.get(kind)),
                                        note=note)
            report[kind] = {"id": voice_seam.choice_id(choice), "choice": dict(choice),
                            "label": voice_seam.describe_choice(choice),
                            "installed": engine.describe() if engine is not None else "",
                            "note": note, "line": line}
            self._note("voice", f"🎚️ {line}")
            print(f"[runtime] 🎚️ {line}", flush=True)
        return report

    def voice_test(self, device_id, text: str = "") -> dict:
        """Speak one line with the CURRENT speech engine and send it to one robot.

        This is the card's **Test** button, and it is the only honest answer to "did my
        pick work": it exercises the engine that is actually installed, on the wire the
        SIM really plays (`commands/tts`, a `CloudTTSResponse`), rather than reporting the
        record back to the page that just wrote it.
        """
        line = str(text or "").strip() or self.DEFAULT_VOICE_TEST_LINE
        if not self.is_permitted(device_id):
            return {"ok": False, "device_id": device_id, "error": "not permitted",
                    "reason": "This robot is waiting to be permitted. Let it in on the "
                              "Robot access card first."}
        if device_id not in self.robots:
            return {"ok": False, "device_id": device_id,
                    "error": f"unknown device_id {device_id!r}",
                    "reason": "That robot is not connected."}
        if self._synth is None:
            return {"ok": False, "device_id": device_id, "error": "no voice",
                    "reason": "No speech engine is installed — pick one, or check "
                              "MOXIE_TTS."}
        event_id = f"voice-test-{int(time.time())}"
        markup = make_markup(line, turn_key=event_id, chunk_index=0)
        resp = self._maybe_synthesize(device_id, markup, event_id=event_id, chunk_num=0)
        if not resp:
            return {"ok": False, "device_id": device_id, "error": "synthesis failed",
                    "reason": "The voice engine could not speak that line — see the "
                              "supervisor log."}
        audio = resp.get("audio") or {}
        self._note("voice", f"🎚️ test '{line[:40]}' → {device_id}")
        return {"ok": True, "device_id": device_id, "spoke": line, "event_id": event_id,
                "engine": self._synth.describe(),
                "sample_rate": int(audio.get("sample_rate") or 0),
                "channels": int(audio.get("channels") or 1),
                "bytes": len(audio.get("buffer") or "")}

    # ---- STT extension point ----
    # ---- STT (AI seam §1) ----
    def set_transcriber(self, transcriber):
        """Install an STT engine (moxie_sdk.stt.Transcriber). Without one, audio
        frames are ignored (text turns still work).

        Live VAD accumulators are dropped with the old engine: an `SttSession` captures the
        transcriber it was built with, so a 🎚️ swap mid-utterance would otherwise finish
        that utterance on the engine a parent just replaced. Losing a half-spoken sentence
        at the exact moment someone changes the ears is the right trade."""
        self._transcriber = transcriber
        self._stt_sessions.clear()

    def _stt_session(self, device_id):
        from moxie_sdk.stt import SttSession
        s = self._stt_sessions.get(device_id)
        if s is None:
            s = SttSession(self._transcriber)
            self._stt_sessions[device_id] = s
        return s

    def feed_stt(self, device_id, vad, audio: bytes = b"", uuid: str = ""):
        """Feed one VAD-tagged audio frame; on END_OF_SPEECH, transcribe and publish a
        zmqSTTResponse back to the robot (/devices/{id}/commands/zmq). Returns the
        transcript when final, else None. No transcriber → no-op."""
        if self._transcriber is None:
            return None
        from moxie_sdk.stt import build_stt_response
        if uuid:
            self._stt_uuid[device_id] = uuid          # frames of one utterance share it
        transcript = self._stt_session(device_id).feed(vad, audio)
        if transcript is None:
            return None
        resp = build_stt_response(self._stt_uuid.pop(device_id, device_id), transcript)
        self._publish(f"/devices/{device_id}/commands/zmq", resp,
                      device_id=device_id, what="stt_result")
        self._note("stt", f"👂 heard: '{transcript[:40]}'")
        # 🎭 During a telehealth session the child's side of the conversation is the only
        # thing the operator can see (text only — no audio and no video reach them this
        # phase; `backlog/telehealth.md` §2.5). This is a READ of transcript the STT path
        # already produced, not a new capture: outside a session nothing is kept.
        if self._telehealth.get(device_id, {}).get("session_id"):
            self._telehealth_note(device_id, telehealth_seam.CHILD, transcript)
        return transcript

    def handle_zmq(self, device_id, payload):
        """STT audio arrives on events/zmq. The real robot sends
        `b'<proto.full_name>:' + zmqSTTRequest_bytes` (needs the compiled proto to
        decode — the remaining wire step). A JSON frame
        `{vad, audio_content(base64), uuid}` is accepted here too, so the STT pipeline
        (accumulate → transcribe → publish zmqSTTResponse) is exercised end-to-end."""
        try:
            data = json.loads(payload)
        except Exception:
            data = None
        if isinstance(data, dict) and "vad" in data:
            import base64
            audio = b""
            if data.get("audio_content"):
                try:
                    audio = base64.b64decode(data["audio_content"])
                except Exception:
                    audio = b""
            return self.feed_stt(device_id, data["vad"], audio, data.get("uuid", ""))
        # real robot: `b'<full_name>:' + zmqSTTRequest` protobuf
        raw = payload if isinstance(payload, (bytes, bytearray)) else str(payload).encode()
        from moxie_sdk.stt import decode_zmq_stt_frame
        frame = decode_zmq_stt_frame(raw)
        if frame is not None:
            return self.feed_stt(device_id, frame["vad"], frame["audio"], frame["uuid"])
        if not getattr(self, "_warned_stt", False):
            note = ("STT audio but no transcriber is set (text turns still work)"
                    if not self._transcriber else "unrecognized events/zmq frame")
            print(f"[runtime] ⚠️  {note}; see handle_zmq().")
            self._warned_stt = True
