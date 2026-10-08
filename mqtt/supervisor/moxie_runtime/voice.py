"""Server voice (TTS), the voice picker, and the STT extension point."""
from __future__ import annotations
import json, time

from moxie_sdk import telehealth as telehealth_seam
from moxie_sdk import voice_settings as voice_seam
from markup import make_markup


class VoiceMixin:
    # ---- TTS (ai-seam §3): server voice for the SIM ----
    def set_synthesizer(self, synth):
        """Install a server-side TTS engine (`moxie_sdk.tts.Synthesizer`). SIM-only: a
        real robot self-synthesizes."""
        self._synth = synth

    def _maybe_synthesize(self, device_id, markup, event_id="", chunk_num=0):
        """Render the line and publish a CloudTTSResponse to `/devices/{id}/commands/tts`
        if a synthesizer is set. Never breaks the turn. `chunk_num` keeps a multi-chunk
        turn in playback order."""
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

    # ---- the voice picker (backlog/voice-picker.md) ----
    # Speech + Listening over what this box can really use (gateway audio models, local
    # engines, built-ins). The pick is fleet-level (`fleet/voice.json`) and read by run.py
    # at boot. Discovery never blocks a turn (cached, refreshed in the background); a swap
    # rebinds one attribute, so it applies from the next turn and the lock below only
    # serializes swaps. An explicit MOXIE_TTS/MOXIE_STT pins the engine and the card says so.

    DEFAULT_VOICE_TEST_LINE = "Hi, I'm Moxie."
    #: How long a console WRITE may wait for the first gateway listing (`voice_update` only).
    VOICE_SETTLE_S = 10.0

    def set_voice_engines(self, engines):
        """Install the engine builders + discovery (`config.voice_engines()`). Without
        them the picker offers only `tone` / `off`."""
        self._voice_engines = engines

    def _voice_discovery(self, *, refresh: bool = False,
                         settle_s: float = 0.0) -> dict:
        """`{available, discovering, gateway_error, pins, pin_notes}` — never raises.

        A failing discovery is reported beside the local entries rather than emptying the
        card. Only `voice_update` passes `settle_s` (a write must be judged against the
        real list); reads return whatever is cached, instantly.
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

        # What an explicit MOXIE_TTS/MOXIE_STT removed, travelling with its reason.
        return {"available": out.get("available") or voice_seam.build_available(),
                "discovering": bool(out.get("discovering")),
                "gateway_error": str(out.get("gateway_error") or ""),
                "pins": _side("pins"), "pin_notes": _side("pin_notes")}

    def voice_settings(self) -> dict:
        """The stored fleet record (`fleet/voice.json`) — `{}` when nobody has picked."""
        return voice_seam.read_settings(self.store)

    def voice_view(self, *, refresh: bool = False) -> dict:
        """The voice card: every option, which is in force, the defaults, discovery and
        gateway status. A stored pick the gateway cannot currently confirm stays current
        (an outage must not revert a parent's choice)."""
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
        """Persist a parent's pick and swap the live engines to match. Validated against
        what is available now; validate -> persist -> install, so a crash mid-swap comes
        back with the saved choice."""
        with self._voice_lock:
            disc = self._voice_discovery(settle_s=self.VOICE_SETTLE_S)
            stored = voice_seam.read_settings(self.store)
            try:
                settings = voice_seam.normalize_voice_settings(
                    patch, disc["available"], current=stored)
            except ValueError as e:
                # If the env pin removed the option, say so rather than blame the gateway.
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

        A failed build keeps the engine already speaking (`off` is the one intentional
        None). A choice the env pin (`pins`) will override gets a note saying so.
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
        """The card's Test button: speak one line with the installed speech engine and
        send it to one robot as a `CloudTTSResponse` (the wire the SIM plays)."""
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

    # ---- STT (ai-seam §1) ----
    def set_transcriber(self, transcriber):
        """Install an STT engine (`moxie_sdk.stt.Transcriber`); without one audio frames are
        ignored. Live VAD sessions are dropped: they captured the old engine. Every
        permitted robot we know of that is not yet asked for its microphone this session
        is asked now: the Listening picker may turn the ears on after the robots
        connected. Deliberately every robot in `self.robots`, not only those confirmed on
        this connection: a ghost (served before our socket dropped, silent since) may well
        still be connected, and it never announces itself again (the broker log is
        live-only), so leaving it out would leave it deaf. The ask to a ghost costs one
        QoS 0 message if it is gone and is not recorded (`_subscribe_stt`). `None` (the
        picker's `off`) asks nobody and keeps each record: the robot was asked and, as
        far as we know, still streams."""
        self._transcriber = transcriber
        self._stt_sessions.clear()
        for device_id in list(self.robots):
            self._subscribe_stt(device_id)

    # The robot streams mic audio only once the cloud subscribes to `zmqSTTRequest` on its
    # bus: a `ProtoSubscribe` frame on `commands/zmq`, sent after the config push — the
    # order the field-proven community server uses (OpenMoxie
    # site/hive/mqtt/moxie_server.py `on_device_connect`, framed by `send_zmq_to_bot`;
    # mqtt-and-conversation.md §3.4). Latched per robot session on the RobotContext
    # (`extra["stt_subscribed_at"]`, shown by `/status`) — but only for a robot confirmed
    # on this broker connection. An ask to a ghost (the roster resume, the picker, a wake
    # or a Permit while the robot is away after a broker restart) may have reached nobody,
    # so it is sent but not recorded; otherwise the robot's return would find the latch
    # set and never be asked (crossed ears, community signal C4, in the ordinary
    # broker-restart order: the supervisor reconnects first). A wake or a re-permit asks
    # again because a sleeping robot drops its subscriptions, and `_forget_robot_state`
    # clears the latch with the rest of our beliefs. Built to the contract and
    # OpenMoxie's behaviour; not yet verified on our own hardware.
    def _subscribe_stt(self, device_id, *, again: bool = False) -> bool:
        """Ask one robot to stream its microphone, once per robot session (`again=True`
        asks regardless). Nothing without a transcriber — nobody would hear the audio —
        and nothing for a pending robot. Returns whether the ask was published."""
        if self._transcriber is None or not self.is_permitted(device_id):
            return False
        robot = self.robots.get(device_id)
        if robot is not None and robot.extra.get("stt_subscribed_at") and not again:
            return False
        from moxie_sdk.stt import ZMQ_STT_REQUEST, encode_proto_subscribe
        ok, _ = self._publish(f"/devices/{device_id}/commands/zmq",
                              encode_proto_subscribe([ZMQ_STT_REQUEST]),
                              device_id=device_id, what="stt_subscribe")
        if ok:
            # Recorded only with live evidence of the session it was sent into.
            if robot is not None and device_id in self._seen_since_connect:
                robot.extra["stt_subscribed_at"] = time.time()
            self._note("stt", f"👂 asked {device_id} to stream its microphone")
            print(f"[runtime] 👂 → asked {device_id} to stream its microphone "
                  f"(ProtoSubscribe {ZMQ_STT_REQUEST})", flush=True)
        return ok

    def _forget_stt_ask(self, device_id):
        """Drop the record of the ask for a robot that is no longer permitted. Nothing
        withdraws a `ProtoSubscribe` (the recovered `Log.proto` has no such message), so
        the robot may well keep streaming to the broker, where the permit gate drops the
        audio; but a pending robot is never asked, so `/status` and the card must not say
        `mic asked`. The next Permit asks again and records it."""
        robot = self.robots.get(device_id)
        if robot is not None:
            robot.extra.pop("stt_subscribed_at", None)

    def _stt_session(self, device_id):
        from moxie_sdk.stt import SttSession
        s = self._stt_sessions.get(device_id)
        if s is None:
            s = SttSession(self._transcriber)
            self._stt_sessions[device_id] = s
        return s

    def feed_stt(self, device_id, vad, audio: bytes = b"", uuid: str = ""):
        """Feed one VAD-tagged audio frame; on END_OF_SPEECH transcribe and publish the
        FINAL `zmqSTTResponse` on `/devices/{id}/commands/zmq` in the bus framing the
        robot reads (`b'<full_name>:' + protobuf`; an empty transcript is still a FINAL).
        Returns the final transcript, else None. No transcriber -> no-op. An engine that
        raises still gets the robot its FINAL (no speech, the failure in the recovered
        `error_code`/`error_message` fields, as the field-proven server answers: OpenMoxie
        `zmq_stt_handler.py:70-73`): a robot is never left waiting on a turn that ended.

        What the honest ears drop (digital silence, a sound label, one of Whisper's
        silence phrases on a quiet or short clip; `moxie_sdk.stt.SttSession`) is still a
        FINAL with no speech and the utterance's uuid; it adds one console note with the
        fixed reason, the canon phrase and the numbers (never the audio, never the
        transcript) and counts in `/status` as `stt_dropped`."""
        if self._transcriber is None:
            return None
        from moxie_sdk.stt import STT_ERROR_CODE, describe_drop, encode_zmq_stt_response
        if uuid:
            self._stt_uuid[device_id] = uuid          # frames of one utterance share it
        session = None
        try:
            session = self._stt_session(device_id)
            transcript = session.feed(vad, audio)
        except Exception as e:                        # noqa: BLE001 — any engine failure
            why = f"{type(e).__name__}: {e}"
            frame = encode_zmq_stt_response(self._stt_uuid.pop(device_id, device_id), "",
                                            error_code=STT_ERROR_CODE, error_message=why)
            self._publish(f"/devices/{device_id}/commands/zmq", frame,
                          device_id=device_id, what="stt_result")
            self._note("error", f"👂 could not transcribe for {device_id}: {why[:80]}")
            print(f"[runtime] ⚠️  STT failed for {device_id} ({why}); sent the robot a "
                  f"FINAL with error_code={STT_ERROR_CODE}", flush=True)
            return None
        if transcript is None:
            return None
        frame = encode_zmq_stt_response(self._stt_uuid.pop(device_id, device_id), transcript)
        self._publish(f"/devices/{device_id}/commands/zmq", frame,
                      device_id=device_id, what="stt_result")
        drop = getattr(session, "last_drop", None)
        if drop:
            robot = self.robots.get(device_id)
            if robot is not None:
                robot.extra["stt_dropped"] = int(robot.extra.get("stt_dropped") or 0) + 1
            line = describe_drop(drop)
            self._note("stt", f"👂 heard nothing: {line}")
            print(f"[runtime] 👂 {device_id} heard nothing: {line}", flush=True)
        else:
            self._note("stt", f"👂 heard: '{transcript[:40]}'")
        # During telehealth the operator sees the child's side as text (a read of what
        # STT already produced; backlog/telehealth.md §2.5).
        if self._telehealth.get(device_id, {}).get("session_id"):
            self._telehealth_note(device_id, telehealth_seam.CHILD, transcript)
        return transcript

    def handle_zmq(self, device_id, payload):
        """STT audio on `events/zmq`: the robot's `b'<proto.full_name>:' + zmqSTTRequest`
        frame, or a JSON `{vad, audio_content(base64), uuid}` frame (SIL/tests)."""
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
        # Real robot: `b'<full_name>:' + zmqSTTRequest` protobuf.
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
