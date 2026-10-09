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
CONFIG = "mqtt/config.py"
WIRE_TESTS = "sim/tests/test_stt_wire.py"
TESTS =["sim/tests/test_honest_ears.py", "sim/tests/test_stt_wire.py",
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
     '    gate = (os.environ.get("MOXIE_STT_PHANTOM_GATE") or "").strip().lower() or "on"',
     '    gate = (os.environ.get("MOXIE_STT_PHANTOM_GATE") or "").strip().lower() or "off"'),
    ("E15 the gate-off path cleans anyway (not byte for byte)", STT,
     '                return (self._t.transcribe(pcm, self._sr) or "").strip()',
     "                return clean_transcript(self._t.transcribe(pcm, self._sr))"),

    # ---- what the runtime and /status say about a drop ----
    ("E16 a drop is not counted", VOICE,
     '                robot.extra["stt_dropped"] = int(robot.extra.get("stt_dropped") or 0) + 1',
     '                robot.extra["stt_dropped"] = 0'),
    ("E17 a drop adds a second, 'heard' note", VOICE,
     "        else:\n"
     "            self._note(\"stt\", f\"👂 heard: '{self._masked(transcript, 40)}'\")",
     "        if True:\n"
     "            self._note(\"stt\", f\"👂 heard: '{self._masked(transcript, 40)}'\")"),
    ("E18 /status stops reporting the drops", LIFECYCLE,
     '                "stt_dropped": int(r.extra.get("stt_dropped") or 0),',
     '                "stt_dropped": 0,'),

    # ---- the first review's survivors: each edge pinned from both sides ----
    ("E19 config.py reads a blank gate as off (the two readers disagree)", CONFIG,
     'STT_PHANTOM_GATE = (((os.environ.get("MOXIE_STT_PHANTOM_GATE") or "").strip().lower()\n'
     '                     or "on") not in _OFF)',
     'STT_PHANTOM_GATE = ((os.environ.get("MOXIE_STT_PHANTOM_GATE") or "on").strip().lower()\n'
     '                    not in _OFF)'),
    ("E20 an engine that heard nothing is recorded as a sound-label drop", STT,
     '    if not str(text or "").strip():',
     "    if False:"),
    ("E21 the digital-silence floor rises (fewer clips reach an engine)", STT,
     "SILENCE_RMS = 0.001",
     "SILENCE_RMS = 0.0035"),
    ("E22 the digital-silence floor falls", STT,
     "SILENCE_RMS = 0.001",
     "SILENCE_RMS = 0.0005"),
    ("E23 the loud edge falls (a quieter clip is immune)", STT,
     "LOUD_RMS = 0.05",
     "LOUD_RMS = 0.021"),
    ("E24 the loud edge rises (a loud clip can be dropped)", STT,
     "LOUD_RMS = 0.05",
     "LOUD_RMS = 0.06"),
    ("E25 local whisper's silence cut rises", STT,
     "    NO_SPEECH_PROB = 0.6",
     "    NO_SPEECH_PROB = 0.89"),
    ("E26 local whisper's silence cut falls", STT,
     "    NO_SPEECH_PROB = 0.6",
     "    NO_SPEECH_PROB = 0.5"),
    ("E27 DEL and C1 control characters are kept", STT,
     r'_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]+")',
     r'_CONTROL = re.compile(r"[\x00-\x1f]+")'),
    ("E28 SttSession ignores an explicit phantom_gate=", STT,
     '        self.phantom_gate = (knobs["phantom_gate"] if phantom_gate is None',
     '        self.phantom_gate = (knobs["phantom_gate"] if True'),
    ("E29 a drop leaves no line in the supervisor's log", VOICE,
     '            print(f"[runtime] 👂 {device_id} heard nothing: {line}", flush=True)',
     "            pass"),

    # ---- the kill switch reaches inside local whisper ----
    ("E30 MOXIE_STT_PHANTOM_GATE=off leaves local whisper's VAD on", STT,
     "        if not self.phantom_gate:                    # the engine as it was before the gate",
     "        if False:"),
    ("E31 WhisperTranscriber ignores an explicit phantom_gate=", STT,
     '        self.phantom_gate = (ears_knobs()["phantom_gate"] if phantom_gate is None',
     '        self.phantom_gate = (ears_knobs()["phantom_gate"] if True'),

    # ---- a robot is never left waiting ----
    ("E32 building the listening session can raise past the robot's FINAL", VOICE,
     "        session = None\n"
     "        try:\n"
     "            session = self._stt_session(device_id)\n",
     "        session = self._stt_session(device_id)\n"
     "        try:\n"),

    # ---- the tests' own instrument: the goodbye loopback must be able to see an EXIT ----
    ("E33 the loopback reads EXIT by a name the wire never carries", WIRE_TESTS,
     '            if a.get("action") == "exit_module"]',
     '            if "EXIT" in json.dumps(a)]'),
]


if __name__ == "__main__":
    raise SystemExit(run_table(MUTATIONS, lambda r: pytest(TESTS), baseline=[pytest(TESTS)]))
