"""Break each honest-ears guard (ai-seam.md §1 "What the ears refuse to hear"); the ears
suites (one run) must go red.

The failure this table exists for is SILENT on the wire: a guard that stops dropping
Whisper's "Bye." on room tone still publishes a perfectly valid FINAL, the robot still
closes its turn, and a child who only paused loses the activity to a goodbye nobody said.
The opposite failure is just as quiet: a guard that drops too much eats a child's real,
quiet "okay". Runner and verdicts: `mutation_runner.py`.

    python3 sim/tools/ears_mutation_check.py [ROW ...]
"""
from mutation_runner import WT, pytest, run_table  # noqa: F401

STT = "mqtt/moxie_sdk/stt.py"
VOICE = "mqtt/supervisor/moxie_runtime/voice.py"
LIFECYCLE = "mqtt/supervisor/moxie_runtime/lifecycle.py"
TESTS = ["sim/tests/test_honest_ears.py", "sim/tests/test_stt_wire.py",
         "sim/tests/test_stt.py"]

MUTATIONS = [
    # ---- the no-call floor: nothing to hear, no engine asked ----
    ("E1  digital silence goes to the engine", STT,
     "        if rms < SILENCE_RMS:",
     "        if False:"),
    ("E2  a clip under 120 ms goes to the engine", STT,
     "        if ms < MIN_UTTERANCE_MS:",
     "        if False:"),

    # ---- a sound label alone is silence ----
    ("E3  sound labels are no longer stripped", STT,
     '        prev, flat = flat, _SOUND_LABEL.sub(" ", flat)',
     "        prev, flat = flat, flat"),
    ("E4  a label-only transcript is no longer a recorded drop", STT,
     "    if not clean_transcript(text):",
     "    if False:"),

    # ---- the phantom rule: canon AND not loud AND (quiet OR short) ----
    ("E5  a loud clip can be dropped on its text", STT,
     "    if rms >= LOUD_RMS:",
     "    if False:"),
    ("E6  room tone alone no longer drops a canon phrase", STT,
     "    if rms < room_tone_rms or ms < min_speech_ms:",
     "    if ms < min_speech_ms:"),
    ("E7  a short clip alone no longer drops a canon phrase", STT,
     "    if rms < room_tone_rms or ms < min_speech_ms:",
     "    if rms < room_tone_rms:"),
    ("E8  the canon grows a word a child really answers with", STT,
     '    "thanks for watching", "the end", "subtitles by the amara org community",',
     '    "thanks for watching", "the end", "subtitles by the amara org community", "okay",'),
    ("E9  the canon matches inside a sentence, not the whole transcript", STT,
     '    return key if key in PHANTOM_CANON else ""',
     '    return next((c for c in sorted(PHANTOM_CANON) if c in key.split()), "")'),
    ("E10 a kept transcript keeps its sound labels", STT,
     "        return clean_transcript(text)\n",
     "        return text.strip()\n"),

    # ---- local whisper's own voice detector ----
    ("E11 local whisper no longer runs its VAD", STT,
     "                                             vad_filter=True)",
     "                                             vad_filter=False)"),
    ("E12 a segment whisper rates as silence is kept", STT,
     "                        <= self.NO_SPEECH_PROB).strip()",
     "                        <= 1.0).strip()"),

    # ---- the kill switch ----
    ("E13 MOXIE_STT_PHANTOM_GATE=off is ignored", STT,
     '    return {"phantom_gate": gate not in _OFF,',
     '    return {"phantom_gate": True,'),
    ("E14 an EMPTY value turns the gate off", STT,
     '_OFF = ("0", "off", "false", "no")',
     '_OFF = ("", "0", "off", "false", "no")'),
    ("E15 the gate-off path cleans anyway (not byte for byte)", STT,
     '                return (self._t.transcribe(pcm, self._sr) or "").strip()',
     "                return clean_transcript(self._t.transcribe(pcm, self._sr))"),

    # ---- what the runtime and /status say about a drop ----
    ("E16 a drop is not counted", VOICE,
     '                robot.extra["stt_dropped"] = int(robot.extra.get("stt_dropped") or 0) + 1',
     '                robot.extra["stt_dropped"] = 0'),
    ("E17 a drop adds a second, 'heard' note", VOICE,
     "        else:\n"
     "            self._note(\"stt\", f\"👂 heard: '{transcript[:40]}'\")",
     "        if True:\n"
     "            self._note(\"stt\", f\"👂 heard: '{transcript[:40]}'\")"),
    ("E18 /status stops reporting the drops", LIFECYCLE,
     '                "stt_dropped": int(r.extra.get("stt_dropped") or 0),',
     '                "stt_dropped": 0,'),
]


if __name__ == "__main__":
    raise SystemExit(run_table(MUTATIONS, lambda r: pytest(TESTS), baseline=[pytest(TESTS)]))
