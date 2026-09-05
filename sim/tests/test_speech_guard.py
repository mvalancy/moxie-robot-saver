"""
The speech/tone guard, guarded — and the rule that keeps a numpy-free suite numpy-free.

WHAT THE SPEECH GUARD IS. Every live audio assertion in this repo has the same hole under
it: `moxie_sdk.tts.ToneSynthesizer` emits 22050 Hz mono PCM16 exactly like the gateway
voice and Piper do, so byte counts, sample rates and WAV headers prove nothing about who
spoke. `helpers_audio.is_real_speech` closes it with spectral flatness — a pure sine puts
all its energy in one bin, speech spreads it across formants, fricatives and silences —
and the observed separation is ten orders of magnitude around a floor of 1e-6.

WHY THIS FILE EXISTS (2026-09-05). That predicate needed numpy, and TWO of its callers are
numpy-free on purpose: `test_live_gateway_stt.py` and `test_live_hosted_ears.py` exist to
prove the CLOUD ears and voice work on a box that installed nothing but `openai`, because
that is what a hosted deployment is. The consequences were both live and both measured:

  · `test_live_gateway_stt.py` ran a complete, healthy live turn — word overlap 1.00, a
    real reply, 203 612 B @ 22050 Hz — and then failed with `ModuleNotFoundError: No module
    named 'numpy'` at `helpers_audio.py:157`, on the last assertion in the file, four
    gateway calls in. Its two siblings `importorskip("numpy")` at module scope, so the
    identical situation made THEM skip; the inconsistency was the whole defect.
  · `test_live_hosted_ears.py`'s first assertion — "the audio we are about to upload is
    speech" — sat inside `try: … except ImportError: pytest.skip(…)`, so on exactly the
    numpy-free machine the file is about, it skipped.

The obvious fix (a third `importorskip("numpy")`) was rejected: this repo's recorded trap
is that a missing package makes the tests that need it importorskip themselves away — a
skip that reads as a pass, which is worse than a loud failure — and here it would have
deleted the gateway-ears proof on the one deployment shape it exists to cover. So the
measurement grew a standard-library twin (`spectral_flatness_stdlib` /
`is_real_speech_stdlib`), exactly as `resample_pcm16` / `resample_pcm16_stdlib` already
had, for exactly the same reason.

A twin is only worth having if it agrees, so this file asserts that — hermetically, with no
credentials, no gateway, no model wheels and (for the load-bearing half) no numpy:

  1. the placeholder tone FAILS the stdlib guard;
  2. speech-shaped audio PASSES it;
  3. the two implementations return the same verdict, with orders of magnitude to spare;
  4. the stdlib one still computes with numpy forcibly unimportable, and the numpy one
     then raises a message that names the twin instead of a bare `ModuleNotFoundError`;
  5. a REAL recorded voice clears the floor on both implementations, by a margin this file
     also asserts — read from a committed mono PCM16 WAV with the `wave` module, so it
     needs no decoder. The first version of this test shelled out to `ffmpeg` and reddened
     CI (run 33985062379), which is the joke writing itself: a change about declaring every
     dependency once, depending on an undeclared external binary. See `RECORDED_VOICE`;
  6. **no numpy-free suite calls a numpy-only helper.** That one is the guard for the
     defect *class* rather than for the instance, and it is the one that fails on the
     pre-fix tree. Its numpy-only set is derived from `helpers_audio.py`'s own call graph,
     so a new helper that reaches numpy joins it without anyone remembering to;
  7. **no test in `sim/tests` shells out to an undeclared external binary** — the general
     form of the ffmpeg mistake, which is now impossible to repeat quietly.

Deliberately NOT named `test_sil_*`: both CI tiers select with `-k "not test_sil"`, so a
SIL-prefixed guard would be deselected in every tier it is meant to run in.
"""
from __future__ import annotations

import ast
import math
import os
import random
import struct
import subprocess
import sys
import wave

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HERE = os.path.dirname(os.path.abspath(__file__))
MQTT = os.path.join(REPO, "mqtt")
sys.path.insert(0, MQTT)
sys.path.insert(0, HERE)

import helpers_audio as A                                    # noqa: E402

#: The live suites that MUST run on a box which installed nothing but `openai`, because
#: each one is a proof about a hosted deployment and that is what a hosted deployment has.
#: Named rather than inferred: "numpy-free" is a design decision about these two files, and
#: a decision belongs somewhere a reviewer can read it.
NUMPY_FREE_SUITES = ("test_live_gateway_stt.py", "test_live_hosted_ears.py")


# --------------------------------------------------------------------------- #
# Fixtures: the two signals, both built here, both deterministic
# --------------------------------------------------------------------------- #
def _tone() -> bytes:
    """The actual placeholder the guard exists to reject — not an imitation of it."""
    from moxie_sdk.tts import ToneSynthesizer
    synth = ToneSynthesizer()
    assert synth.sample_rate == 22050, synth.sample_rate      # same rate as a real voice
    return synth.synthesize("Hi Sam, I am Moxie, and this is a whole sentence.")


def _speech_shaped(samples: int, sample_rate: int = 22050) -> bytes:
    """Audio with the three properties that make speech broadband, and nothing else.

    A voiced excitation whose f0 wanders, seven inharmonically-spaced overtones standing in
    for formants, additive noise standing in for fricatives, and an envelope that goes to
    silence a third of the time. Seeded, so it is the same buffer on every machine and in
    every run. It is NOT a recording — test 5 uses a real one, committed as a WAV — it is
    the positive control that needs no fixture at all.

    `samples` is passed in rather than defaulted so the caller can make this buffer exactly
    as long as the tone it is compared against; that turns the length control below into an
    equality instead of a tolerance.
    """
    rng = random.Random(20260905)
    out = bytearray()
    for i in range(int(samples)):
        t = i / float(sample_rate)
        envelope = 0.0 if (int(t * 3) % 3 == 2) else (0.5 + 0.5 * math.sin(2 * math.pi * 4 * t))
        f0 = 120.0 + 20.0 * math.sin(2 * math.pi * 1.5 * t)
        voiced = 0.0
        for harmonic, amplitude in ((1, 1.0), (2, .6), (3, .4), (5, .25),
                                    (8, .15), (13, .1), (21, .06)):
            voiced += amplitude * math.sin(2 * math.pi * f0 * harmonic * t)
        value = voiced / 2.6 + 0.25 * (rng.random() * 2 - 1)
        out += struct.pack("<h", int(max(-1.0, min(1.0, value * envelope)) * 20000))
    return bytes(out)


@pytest.fixture(scope="module")
def tone() -> bytes:
    return _tone()


@pytest.fixture(scope="module")
def speech(tone) -> bytes:
    return _speech_shaped(len(tone) // 2)


# --------------------------------------------------------------------------- #
# 1 + 2. the stdlib guard separates the two, with NO numpy needed to prove it
# --------------------------------------------------------------------------- #
def test_the_placeholder_tone_fails_the_stdlib_speech_guard(tone):
    """The direction that matters: if the tone could pass, every live speech assertion in
    the repo would be vacuous. Measured 2026-09-05: 8.968e-10 against a 1e-6 floor."""
    flat = A.spectral_flatness_stdlib(tone)
    assert not A.is_real_speech_stdlib(tone), (
        f"ToneSynthesizer output scored {flat:.3e}, above the {A.SPEECH_FLATNESS_FLOOR:.0e} "
        f"floor — the guard is useless and every live speech test is vacuous")
    assert flat < A.SPEECH_FLATNESS_FLOOR / 100, (
        f"the tone ({flat:.3e}) is within two orders of the floor; the margin used to be "
        f"four, so either the estimator or the floor drifted")


def test_speech_shaped_audio_passes_the_stdlib_speech_guard(speech):
    """The other direction, which a guard that simply returned False would also need to
    fail. Measured 2026-09-05: 1.177e-01."""
    flat = A.spectral_flatness_stdlib(speech)
    assert A.is_real_speech_stdlib(speech), (
        f"broadband voiced audio scored {flat:.3e}, below the floor — the guard would "
        f"fail every real voice and the live suites would be red for the wrong reason")
    assert flat > A.SPEECH_FLATNESS_FLOOR * 100, flat


def test_the_stdlib_guard_is_not_secretly_a_length_check(tone, speech):
    """Cheapest way for either half above to pass for the wrong reason is a predicate that
    keys on size. These two buffers are the SAME number of bytes — the speech fixture is
    built to the tone's length on purpose — and they land ten orders of magnitude apart, so
    length is provably not what is being measured."""
    assert len(tone) == len(speech), (len(tone), len(speech))
    ratio = A.spectral_flatness_stdlib(speech) / max(A.spectral_flatness_stdlib(tone), 1e-30)
    assert ratio > 1e6, f"only {ratio:.1e} between a tone and a voice"


# --------------------------------------------------------------------------- #
# 3. the twin agrees with the original
# --------------------------------------------------------------------------- #
def test_both_speech_guards_return_the_same_verdict(tone, speech):
    """A twin nobody compared is two predicates, and two predicates are two thresholds.

    The two are NOT bit-identical by construction — the numpy one windows the whole buffer
    in a single transform, the stdlib one averages eight 2048-sample frames — so what is
    asserted is the verdict, plus the fact that both sit on the same side of the floor with
    room to spare. Measured 2026-09-05: tone 8.968e-10 (stdlib) vs 5.415e-16 (numpy); speech
    1.177e-01 vs 1.396e-01; a real recorded clip 1.068e-02 vs 6.931e-03.
    """
    pytest.importorskip("numpy", reason="comparing the numpy implementation needs numpy")
    for label, pcm, expected in (("tone", tone, False), ("speech", speech, True)):
        stdlib, numpy_ = A.spectral_flatness_stdlib(pcm), A.spectral_flatness(pcm)
        print(f"[guard] {label:7s} stdlib={stdlib:.3e} numpy={numpy_:.3e} "
              f"floor={A.SPEECH_FLATNESS_FLOOR:.0e}")
        assert A.is_real_speech_stdlib(pcm) is expected, (label, stdlib)
        assert A.is_real_speech(pcm) is expected, (label, numpy_)


# --------------------------------------------------------------------------- #
# 4. …and it really does compute with numpy gone
# --------------------------------------------------------------------------- #
#: Import-time blocker, the same idiom `test_package_contents.py` uses to prove the SDK
#: imports without its optional backends: a `sys.meta_path` finder that refuses one name.
#: `ModuleNotFoundError` rather than a bare `ImportError`, because that is what an absent
#: wheel actually raises and the point is to reproduce an absent wheel faithfully.
_BLOCK_NUMPY = (
    "import sys\n"
    "class Block:\n"
    "    def find_spec(self, name, path=None, target=None):\n"
    "        if name.split('.')[0] == 'numpy':\n"
    "            raise ModuleNotFoundError(\"No module named 'numpy'\", name='numpy')\n"
    "        return None\n"
    "sys.meta_path.insert(0, Block())\n"
)


def _run_without_numpy(body: str) -> subprocess.CompletedProcess:
    script = _BLOCK_NUMPY + (
        f"sys.path.insert(0, {HERE!r})\n"
        f"sys.path.insert(0, {MQTT!r})\n"
        "import helpers_audio as A\n"
    ) + body
    return subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True, cwd=REPO)


def test_the_stdlib_speech_guard_computes_with_numpy_forcibly_absent():
    """THE test for the fix. It runs in a subprocess where numpy cannot be imported even
    though it is installed, so a full-fat venv catches a regression here too — the same
    reason `test_package_contents.py` blocks its imports rather than trusting the tier."""
    out = _run_without_numpy(
        "from moxie_sdk.tts import ToneSynthesizer\n"
        "tone = ToneSynthesizer().synthesize('Hi Sam, I am Moxie, and this is a sentence.')\n"
        "assert A.is_real_speech_stdlib(tone) is False\n"
        "print('TONE_REJECTED', '%.3e' % A.spectral_flatness_stdlib(tone))\n"
    )
    assert out.returncode == 0, (
        "the stdlib speech guard cannot run without numpy — which is the ONE property it "
        f"exists for:\n{out.stderr}")
    assert "TONE_REJECTED" in out.stdout, out.stdout


def test_the_numpy_only_predicate_names_its_stdlib_twin_when_numpy_is_absent():
    """The other half of the fix: five separate in-function `import numpy` statements used
    to produce a bare `ModuleNotFoundError` from the middle of a helper. There is one
    accessor now, and its message has to be actionable — otherwise the next agent to hit it
    spends the time this file was written to save."""
    out = _run_without_numpy(
        "try:\n"
        "    A.is_real_speech(b'\\x00\\x01' * 16000)\n"
        "except ModuleNotFoundError as exc:\n"
        "    print('MESSAGE:' + str(exc))\n"
        "else:\n"
        "    raise AssertionError('is_real_speech worked without numpy?')\n"
    )
    assert out.returncode == 0, out.stderr
    message = out.stdout.split("MESSAGE:", 1)[1] if "MESSAGE:" in out.stdout else ""
    assert "is_real_speech_stdlib" in message, (
        f"the numpy-absent message does not name the twin to call instead: {message!r}")
    assert "requirements" in message, (
        f"the numpy-absent message does not say where the dependency is declared: {message!r}")


# --------------------------------------------------------------------------- #
# 5. a real recorded voice — read with the standard library, no decoder needed
# --------------------------------------------------------------------------- #
#: A real voice, committed in a form `wave` can read: 0.75 s of the SIM's own prerendered
#: Moxie speech, mono PCM16 @ 22050 Hz, 33 118 B.
#:
#: IT USED TO BE THE MP3, DECODED BY SHELLING OUT TO `ffmpeg`, and that was wrong in this
#: PR of all PRs: a change about declaring every dependency exactly once, which added a
#: test that depended on an undeclared external binary. CI run 33985062379 duly failed with
#: `FileNotFoundError: [Errno 2] No such file or directory: 'ffmpeg'` — the runner has no
#: ffmpeg — with everything else green.
#:
#: Both easy fixes were refused for reasons this file already argues. A `shutil.which`
#: skip converts a loud red into a silent pass, which is precisely what
#: `test_a_numpy_free_suite_declares_itself_so` exists to prevent. `apt-get install ffmpeg`
#: puts a heavyweight system package on every run of the tier to decode ONE fixture, and it
#: cannot be declared in `requirements-hermetic.txt`, the single source of truth this PR
#: just created. So the dependency was REMOVED instead of skipped — the same move as
#: `spectral_flatness_stdlib` itself: store the fixture in a form the stdlib can read.
#:
#: WHY 0.75 s AND WHY THIS OFFSET: it is the shortest window that keeps a comfortable
#: margin in BOTH implementations. Measured across candidate trims of the loudest window —
#: 0.25 s scored 2.980e-04 (298x the floor), 0.50 s 3.878e-04 (388x), and this one
#: 3.073e-02 on the stdlib path (30 727x) and 3.200e-03 on the numpy path (3 200x), the
#: smaller of which is the one that matters. Anyone trimming this further must re-state the
#: margin here: a fixture whose flatness creeps toward 1e-6 makes the assertion vacuous
#: without failing, and `test_the_recorded_fixture_clears_the_floor_by_orders_of_magnitude`
#: below fails if the smaller margin drops under 100x.
RECORDED_VOICE = os.path.join(HERE, "goldens", "real_voice_22050_mono.wav")
RECORDED_RATE = 22050

#: The floor multiple the fixture must keep on the WEAKER of the two implementations.
RECORDED_MIN_MARGIN = 100


def _recorded_voice() -> bytes:
    """The fixture's PCM frames, via `wave` — stdlib, no subprocess, no optional package."""
    with wave.open(RECORDED_VOICE, "rb") as clip:
        assert clip.getnchannels() == 1, clip.getnchannels()
        assert clip.getsampwidth() == 2, clip.getsampwidth()
        assert clip.getframerate() == RECORDED_RATE, clip.getframerate()
        return clip.readframes(clip.getnframes())


def test_a_real_recorded_voice_clears_the_floor_on_both_implementations():
    """Synthetic broadband audio is a positive control, not a voice. This is a voice — the
    same Piper-family speech the SIM actually plays — and the stdlib half of this assertion
    runs UNCONDITIONALLY: no ffmpeg, no numpy, no network, nothing to skip on."""
    pcm = _recorded_voice()
    assert len(pcm) == 33074, len(pcm)          # the committed fixture, not a truncated read
    flat = A.spectral_flatness_stdlib(pcm)
    print(f"[guard] recorded stdlib={flat:.3e} floor={A.SPEECH_FLATNESS_FLOOR:.0e}")
    assert A.is_real_speech_stdlib(pcm), (
        f"a REAL recorded voice scored {flat:.3e}, below the floor — the stdlib estimator "
        f"would fail every live suite that uses it")
    numpy_flat = None
    try:
        numpy_flat = A.spectral_flatness(pcm)
    except ModuleNotFoundError:
        return                                  # tests 1-4 already stand without numpy
    print(f"[guard] recorded numpy ={numpy_flat:.3e}")
    assert A.is_real_speech(pcm), numpy_flat


def test_the_recorded_fixture_clears_the_floor_by_orders_of_magnitude():
    """The anti-vacuity half, and the guard against a future trim. A shorter or quieter clip
    would still PASS the test above while creeping toward the floor, at which point "a real
    voice is recognised as speech" stops being a measurement. Measured 2026-09-05: 30 727x
    (stdlib) and 3 200x (numpy)."""
    pcm = _recorded_voice()
    margins = [A.spectral_flatness_stdlib(pcm) / A.SPEECH_FLATNESS_FLOOR]
    try:
        margins.append(A.spectral_flatness(pcm) / A.SPEECH_FLATNESS_FLOOR)
    except ModuleNotFoundError:
        pass
    assert min(margins) > RECORDED_MIN_MARGIN, (
        f"the recorded fixture only clears the speech floor by {min(margins):.0f}x on its "
        f"weaker implementation. It cleared 3 200x when it was committed; a fixture this "
        f"close to the floor makes the assertion above vacuous without failing it. Restore "
        f"a longer/louder span and re-state the margin at RECORDED_VOICE.")


# --------------------------------------------------------------------------- #
# 6. THE CLASS GUARD — no numpy-free suite may call a numpy-only helper
# --------------------------------------------------------------------------- #
def _numpy_only_helpers() -> set:
    """The public names in `helpers_audio` that reach numpy, from its own call graph.

    Derived rather than listed, so a helper added tomorrow that calls `_np()` — directly or
    through another helper — is covered without anyone remembering this file exists. The
    graph is module-level `def`s and the plain `name(...)` calls in their bodies, which is
    all this module contains; a helper that reached numpy through `getattr` would escape,
    and that is worth knowing rather than pretending otherwise.
    """
    tree = ast.parse(open(os.path.join(HERE, "helpers_audio.py")).read())
    bodies = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    calls, direct = {}, set()
    for name, node in bodies.items():
        callees = {c.func.id for c in ast.walk(node)
                   if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        calls[name] = callees & set(bodies)
        if "_np" in callees:
            direct.add(name)
    # reverse-reachability: whoever calls a numpy-reaching function is one too
    tainted = set(direct)
    changed = True
    while changed:
        changed = False
        for name, callees in calls.items():
            if name not in tainted and (callees & tainted):
                tainted.add(name)
                changed = True
    return {n for n in tainted if not n.startswith("_")}


def test_the_call_graph_scan_found_the_helpers_we_know_reach_numpy():
    """A derived set that silently came back empty would make the guard below vacuous —
    the exact failure mode this whole file is about. So the derivation is checked against
    the four helpers we know for certain reach numpy, including one that only does so
    transitively (`is_real_speech` → `spectral_flatness` → `_np`)."""
    found = _numpy_only_helpers()
    for known in ("spectral_flatness", "is_real_speech", "resample_pcm16", "zcr_std"):
        assert known in found, (known, sorted(found))
    for twin in ("spectral_flatness_stdlib", "is_real_speech_stdlib",
                 "resample_pcm16_stdlib", "word_overlap", "duration_s"):
        assert twin not in found, (
            f"{twin} is reported as numpy-only; either it grew a numpy call or the "
            f"call-graph scan is wrong. Found: {sorted(found)}")


def _importorskipped(path: str) -> set:
    """Every module name the file passes to `pytest.importorskip`, from the AST.

    Playbook rule 17 — "a guard must assert over code, not over the whole file" — the hard
    way, again, and within a minute of writing this file: the first version was
    `'importorskip("numpy"' not in src`, which fired on `test_live_gateway_stt.py` because
    the comment I had just added there *explains* why an importorskip would be the wrong
    fix. Citing what you rejected is the house style, so a guard that cannot tell a comment
    from a call is a guard that punishes the style.
    """
    out = set()
    for node in ast.walk(ast.parse(open(path).read())):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "importorskip" and node.args):
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                out.add(first.value.split(".")[0])
    return out


@pytest.mark.parametrize("suite", NUMPY_FREE_SUITES)
def test_a_numpy_free_suite_declares_itself_so(suite):
    """Half one of the class guard: these files must NOT `importorskip("numpy")`. If one
    ever does, it has stopped being a proof about a numpy-free deployment and the guard
    below would start passing for the wrong reason."""
    assert "numpy" not in _importorskipped(os.path.join(HERE, suite)), (
        f"{suite} now skips itself when numpy is absent. That file exists to prove the "
        f"cloud voice/ears work on a box that installed only `openai`, so an importorskip "
        f"there deletes the proof on precisely the machine shape it is about.")


@pytest.mark.parametrize("suite", NUMPY_FREE_SUITES)
def test_a_numpy_free_suite_calls_no_numpy_only_helper(suite):
    """Half two, and the assertion that fails on the pre-fix tree: on 2026-09-05
    `test_live_gateway_stt.py`'s last line was `assert A.is_real_speech(...)` — a numpy-only
    predicate in a suite that requires no numpy — and it cost a full live turn (four gateway
    calls) to find out, every time."""
    numpy_only = _numpy_only_helpers()
    src = open(os.path.join(HERE, suite)).read()
    tree = ast.parse(src)
    lines = src.splitlines()
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in numpy_only:
            offenders.append(f"line {node.lineno}: {lines[node.lineno - 1].strip()}")
    assert not offenders, (
        f"{suite} is numpy-free by design but calls helpers that reach numpy "
        f"({', '.join(sorted(numpy_only))}). Use the `_stdlib` twin — NOT "
        f"`importorskip(\"numpy\")`, which hides this by deleting the suite:\n  "
        + "\n  ".join(offenders))


def test_every_other_caller_of_a_numpy_only_helper_requires_numpy():
    """The general direction: a file that is *not* on the numpy-free list may use the numpy
    helpers, but then it must say so at module scope, or it is a hard `ModuleNotFoundError`
    waiting for the first tier that does not happen to install numpy.

    (It is now installed in every tier — `sim/tests/requirements-hermetic.txt`, one
    declaration, enforced by `test_ci_workflows.py`. This asserts the *file* is honest
    regardless, because the tier's list is one edit away from changing and a suite should
    not depend on being lucky.)"""
    numpy_only = _numpy_only_helpers()
    unguarded = {}
    for name in sorted(os.listdir(HERE)):
        if not name.startswith("test_") or not name.endswith(".py"):
            continue
        if name in NUMPY_FREE_SUITES or name == os.path.basename(__file__):
            continue
        src = open(os.path.join(HERE, name)).read()
        if "helpers_audio" not in src:
            continue
        if "numpy" in _importorskipped(os.path.join(HERE, name)):
            continue
        hits = sorted({n.attr for n in ast.walk(ast.parse(src))
                       if isinstance(n, ast.Attribute) and n.attr in numpy_only})
        if hits:
            unguarded[name] = hits
    assert not unguarded, (
        "these files call numpy-only audio helpers without requiring numpy at module "
        f"scope, and are not on the numpy-free list either: {unguarded}")


# --------------------------------------------------------------------------- #
# 7. no test may shell out to an UNDECLARED external binary
# --------------------------------------------------------------------------- #
#: Every external program the suite is allowed to invoke, and why. A binary is not a python
#: package, so it cannot live in `sim/tests/requirements-hermetic.txt` — which means the only
#: honest place to declare one is a list a reviewer reads, with the tier that provides it
#: named beside it.
#:
#: THIS LIST EXISTS BECAUSE OF ONE LINE OF MINE. The first version of test 5 above decoded an
#: mp3 by calling `ffmpeg`, in a change whose entire subject was declaring dependencies once.
#: CI has no ffmpeg, so run 33985062379 failed with `FileNotFoundError` and nothing in the
#: repo had objected beforehand: the dependency guards in `test_ci_workflows.py` read
#: `pip install` lines, and an external binary is invisible to them by construction. So this
#: is the same closure one layer out — the workflows declare the python packages, this
#: declares the programs.
#:
#: `sys.executable` is deliberately absent: re-entering THIS interpreter is not an external
#: dependency, and it is how the numpy-blocked subprocess above works.
DECLARED_BINARIES = {
    # apt-get in sim/ci/ci.yml's sil job; helpers_stack falls back to docker without it.
    "mosquitto": "the real broker the SIL tests round-trip through",
    # preinstalled on ubuntu-latest runners and required by the deep tier's compose jobs.
    "docker": "the broker/compose fallback when no mosquitto binary is present",
    # preinstalled on the runners; the fast tier already runs ~20 `node sim/test_*.mjs` steps.
    "node": "the JS-side harnesses (safety probes, the hosted-ears transcribe harness)",
    # a git checkout is the premise of every job in every tier.
    "git": "listing tracked files, so a guard scans what we SHIP, not what is lying around",
    # the shell the SIL scripts are written in, and the one `run:` uses.
    "bash": "sim/run_smoke.sh and friends, driven as subprocesses by the SIL suites",
}


def _spawned_binaries() -> dict:
    """{binary: [files]} for every literal argv[0] the suite hands to `subprocess`.

    Only the literal, first-element string forms are resolved — `subprocess.run(["ffmpeg",
    ...])` and `run([node, ...])` where `node` is a module-level string constant. A binary
    named through a computed expression escapes, which is worth stating rather than
    implying; the point is to make the accidental case loud, and the accidental case is
    always a literal.
    """
    tests = os.path.join(REPO, "sim", "tests")
    found = {}
    for name in sorted(os.listdir(tests)):
        if not name.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(tests, name)).read())
        # module-level `NODE = "node"`-style constants, so `run([node, ...])` resolves
        consts = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                    and isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        consts[target.id] = node.value.value
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("run", "Popen", "check_output", "call",
                                           "check_call")
                    and node.args):
                continue
            argv = node.args[0]
            if not isinstance(argv, (ast.List, ast.Tuple)) or not argv.elts:
                continue
            head = argv.elts[0]
            if isinstance(head, ast.Constant) and isinstance(head.value, str):
                program = head.value
            elif isinstance(head, ast.Name) and head.id in consts:
                program = consts[head.id]
            else:
                continue                    # sys.executable, an f-string, a path expression
            program = os.path.basename(program)
            if program and not program.startswith(("/", ".")):
                found.setdefault(program, []).append(name)
    return found


def test_the_binary_scan_found_the_programs_we_know_the_suite_spawns():
    """Anti-vacuity, same as every other derived set in this file: a scan that came back
    empty would make the guard below pass forever. `mosquitto` and `docker` are certainties
    (`helpers_stack.py` boots a real broker one way or the other)."""
    found = _spawned_binaries()
    for known in ("mosquitto", "docker"):
        assert known in found, (known, sorted(found))


def test_no_test_shells_out_to_an_undeclared_external_binary():
    """The general form of the ffmpeg mistake. An external program is a dependency that no
    requirements file can carry, so it must be declared HERE, with the reason and the tier
    that provides it — or not used."""
    undeclared = {program: sorted(set(files))
                  for program, files in _spawned_binaries().items()
                  if program not in DECLARED_BINARIES}
    assert not undeclared, (
        f"these tests spawn external binaries that DECLARED_BINARIES does not name: "
        f"{undeclared}. CI runners have a specific, small set of programs; one that is not "
        f"there fails the job with FileNotFoundError, and no `pip install` guard can see it "
        f"coming. Either read the fixture with the standard library (see RECORDED_VOICE for "
        f"how that went) or add the program here with its reason and its provider.")

