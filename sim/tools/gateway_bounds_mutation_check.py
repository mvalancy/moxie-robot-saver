"""Break each guard that keeps a gateway failure bounded and its standby honest; the row's
`-k` selector must go red.

What the rows guard: the streaming brain skipping its fallback on either of two grounds
(the open timed out, or the bound is spent), the fallback's backoff running on what is
left of the bound, the standby window's range (0 to a day, never inf), the window on a
monotonic clock with the wall clock only for the times a status line names, and each
voice line labelled with the rate of the voice that rendered it. M12, M13 and M15 are
the names the core-wave-3 plan gave the three rows that survived the whole hermetic
suite before their tests existed. Runner and verdicts: `mutation_runner.py`.

Every run (the baseline, each row, the control) first deletes every `__pycache__` under
`mqtt/` and `sim/`: a mutant restored within the same second at the same size can
otherwise be read back from stale bytecode. After the table, a control runs the clean
tree once more and checks each mutated file is byte-identical to what it was before.

    python3 sim/tools/gateway_bounds_mutation_check.py [ROW ...]
"""
import hashlib
import subprocess

from mutation_runner import ENV, WT, pytest, run_table  # noqa: F401

LLM = "mqtt/moxie_sdk/apps/llm_app.py"
CHAT = "mqtt/moxie_sdk/chat.py"
CONFIG = "mqtt/config.py"
STT = "mqtt/moxie_sdk/stt.py"
TTS = "mqtt/moxie_sdk/tts.py"
TESTS = ["sim/tests/test_gateway_timeouts.py", "sim/tests/test_tts.py",
         "sim/tests/test_streaming.py"]

MUTATIONS = [
    # ---- the streaming brain: one bound for the open AND its fallback ----
    ("M12 a timed-out open with time left still gets a fallback", LLM,
     "                if is_timeout_error(e) or remaining <= 0:",
     "                if remaining <= 0:",
     "connect_timeout_with_time_left"),
    ("M13 a refusal with no bound left still gets a fallback", LLM,
     "                if is_timeout_error(e) or remaining <= 0:",
     "                if is_timeout_error(e):",
     "refusal_that_comes_back_with_no_bound_left"),
    ("M15 the fallback backs off on the whole bound, not what is left", LLM,
     "            raw = call_with_backoff(_once, pacer=self._pacer, deadline_s=budget,",
     "            raw = call_with_backoff(_once, pacer=self._pacer, deadline_s=self._timeout_s,",
     "slow_busy_open"),

    # ---- the standby window: 0 to a day ----
    ("R1 config loads a window that never ends", CONFIG,
     'if not -float("inf") < ENGINE_RETRY_S <= 86400:',
     "if False:",
     "retry_window_that_never_ends_is_refused_at_startup"),
    ("R2 the engines accept a window that never ends", CHAT,
     '    if not -float("inf") < seconds <= MAX_RETRY_S:',
     "    if False:",
     "each_fallback_refuses_a_window_that_never_ends"),
    ("R3 a negative window no longer counts as 0", CHAT,
     "    return max(0.0, seconds)",
     "    return seconds",
     "each_fallback_refuses_a_window_that_never_ends"),

    # ---- the window on its own clock; the wall clock only names times ----
    ("W1 the ears' window runs on the wall clock", STT,
     "        self._clock, self._wall = clock, wall",
     "        self._clock, self._wall = wall, wall",
     "wall_clock_step_mid_outage and ears"),
    ("W2 the voice's window runs on the wall clock", TTS,
     "        self._clock, self._wall = clock, wall",
     "        self._clock, self._wall = wall, wall",
     "wall_clock_step_mid_outage and voice"),
    ("W3 the ears' default window clock is the wall clock", STT,
     "retry_s: Optional[float] = None, clock=time.monotonic, wall=time.time):",
     "retry_s: Optional[float] = None, clock=time.time, wall=time.time):",
     "monotonic_clock_by_default"),
    ("W4 the voice's default window clock is the wall clock", TTS,
     "retry_s: Optional[float] = None, clock=time.monotonic, wall=time.time):",
     "retry_s: Optional[float] = None, clock=time.time, wall=time.time):",
     "monotonic_clock_by_default"),
    ("W5 the ears name the latch on the window's clock, not the wall's", STT,
     "        return None if at is None else self._on_the_wall(at)\n",
     "        return None if at is None else at\n",
     "wall_clock_step_mid_outage and ears"),
    ("W6 the voice's status line names the window's clock, not the wall's", TTS,
     '(standby since {_hhmm(self._on_the_wall(at))} — "',
     '(standby since {_hhmm(at)} — "',
     "wall_clock_step_mid_outage and voice"),

    # ---- each line labelled with the voice that rendered it ----
    ("A1 the response reads the shared rate after the render", TTS,
     "    return build_cloud_tts_response(clip.pcm, event_id=event_id, channels=clip.channels,\n"
     "                                    sample_rate=clip.sample_rate, chunk_num=chunk_num)",
     "    return build_cloud_tts_response(clip.pcm, event_id=event_id, channels=synth.channels,\n"
     "                                    sample_rate=synth.sample_rate, chunk_num=chunk_num)",
     "keeps_the_rate_of_the_voice"),
    ("A2 the fallback labels the primary's line from its own attributes", TTS,
     "                self._adopt(clip)\n                return clip\n",
     "                self._adopt(clip)\n"
     "                return AudioClip(clip.pcm, self.sample_rate, self.channels)\n",
     "keeps_the_rate_of_the_voice"),
    ("A3 the fallback's synthesize() returns the clip, not bytes", TTS,
     "        return self.render(text, voice=voice).pcm",
     "        return self.render(text, voice=voice)",
     "still_returns_bytes"),
    ("A4 a voice with only synthesize() is asked for render()", TTS,
     '    render = getattr(engine, "render", None)',
     "    render = engine.render",
     "only_synthesize_still_speaks"),
]


def purged(cmd):
    """`cmd` run after every `__pycache__` under mqtt/ and sim/ is deleted."""
    return ["bash", "-c",
            'find mqtt sim -name __pycache__ -type d -prune -exec rm -rf {} + ; exec "$0" "$@"',
            *cmd]


def _digests():
    return {f: hashlib.sha256((WT / f).read_bytes()).hexdigest()
            for f in sorted({row[1] for row in MUTATIONS})}


def main() -> int:
    before = _digests()
    rc = run_table(MUTATIONS, lambda r: purged(pytest(TESTS, r[4], "-x")),
                   baseline=[purged(pytest(TESTS))], timeout=300)
    if _digests() != before:
        print("CONTROL: a mutated file was not restored; check `git status`")
        return 1
    proc = subprocess.run(purged(pytest(TESTS)), cwd=WT, capture_output=True, text=True,
                          env=ENV, timeout=300)
    tail = [ln for ln in proc.stdout.splitlines() if " in " in ln and "passed" in ln]
    print(f"CONTROL (the clean tree again, bytecode purged): "
          f"{'green' if proc.returncode == 0 else 'RED'}"
          + (f"  ({tail[-1].strip('= ')})" if tail else ""))
    return rc or int(proc.returncode != 0)


if __name__ == "__main__":
    raise SystemExit(main())
