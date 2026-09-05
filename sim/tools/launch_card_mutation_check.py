#!/usr/bin/env python3
"""🎴 Delete one launch-card guard at a time and require a test to go red.

The house rule is that a feature's tests are proven in BOTH directions: green with the
guard, red without it. It matters more here than anywhere else in this repo, because the
thing under test is what stands between a QR code **any stranger can print and leave on
a table in front of a child** and an activity starting on that child's robot. A green
suite says the refusals are present; only this says they are load-bearing.

Each row removes exactly one guard from `moxie_sdk/launch_cards.py` or from the runtime's
one call site, runs the two card suites, and restores the file. **A mutation that leaves
the suite GREEN is a hole in the tests, not a pass** — and for the allowlist rows, "green"
would mean a card naming any module id at all starts that module.

Deliberately no `-x`: the run reports HOW MANY tests each mutation reddens, because "one
test went red" and "the refusal is pinned from four directions" are different facts and
the second is the one worth recording in the brief.

    python3 sim/tools/launch_card_mutation_check.py     # from the repo root

Sibling of `brain_mutation_check.py` / `ext_mutation_check.py`; the anchors are held
honest by `sim/tests/test_mutation_tables.py`, which fails if a refactor makes any row
below a no-op.
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = ROOT / ".venv/bin/python"
if not PY.exists():
    PY = pathlib.Path(sys.executable)
TESTS = ["sim/tests/test_launch_cards.py", "sim/tests/test_launch_cards_runtime.py"]

L = "mqtt/moxie_sdk/launch_cards.py"
A = "mqtt/moxie_sdk/actions.py"
R = "mqtt/supervisor/moxie_runtime.py"

MUTATIONS = [
    # ---- the allowlist itself: the safety property this feature exists for ----
    ("M1  the allowlist check is gone — any module id launches", L,
     "    if not is_launchable(action.module_id):\n        return None",
     "    if False:\n        return None"),
    ("M2  the allowlist is a truthiness test, not membership", L,
     "    return isinstance(module_id, str) and module_id in LAUNCHABLE_MODULE_IDS",
     "    return bool(module_id)"),
    ("M3  the catalog admits everything the default template schedules", L,
     "    return frozenset(onboard | {m for m in _FIXTURE_MODULE_IDS if m in scheduled})",
     "    return frozenset(onboard | set(_FIXTURE_MODULE_IDS) | scheduled)"),

    # ---- the card's shape: one launch, and nothing else ----
    ("M4  'exactly one action, of type LAUNCH' is gone", L,
     "    if len(actions) != 1 or actions[0].type is not ActionType.LAUNCH:\n"
     "        return None",
     "    if not actions:\n        return None"),
    ("M5  the tag-name gate is gone — launch_if_confirmed rides in as a launch", L,
     "    if set(names) != {CARD_TAG}:\n        return None",
     "    if not names:\n        return None"),
    ("M6  leftover text no longer refuses the card", L,
     "    if residue:\n        return None",
     "    if residue and False:\n        return None"),

    # ---- the marker, and the medium's own ceiling ----
    ("M7  the GO marker is optional", L,
     "    if not text or not text.startswith(CARD_PREFIX):\n"
     "        return None\n"
     "    remainder = text[len(CARD_PREFIX):]",
     "    if not text:\n"
     "        return None\n"
     "    remainder = (text[len(CARD_PREFIX):] if text.startswith(CARD_PREFIX) else text)"),
    ("M8  the GO marker is matched case-insensitively", L,
     "    if not text or not text.startswith(CARD_PREFIX):",
     "    if not text or not text.upper().startswith(CARD_PREFIX):"),
    ("M9  a value longer than a QR symbol can hold is parsed anyway", L,
     "    if not isinstance(value, str) or len(value) > MAX_CARD_LEN:",
     "    if not isinstance(value, str):"),

    # ---- only the QR reader scans paper ----
    ("M10 any marker event may carry a card (ArUco ids, book covers)", L,
     "    if name != presence_seam.QR_EVENT:\n        return None",
     "    if not name:\n        return None"),

    # ---- the accessor the name gate is built on ----
    ("M11 tag_names stops normalising case, so <LAUNCH:DM> is no longer a card", A,
     "    return [m.group(1).lower() for m in _TAG_RE.finditer(text or \"\")\n"
     "            if m.group(1).lower() in KNOWN_TAGS]",
     "    return [m.group(1) for m in _TAG_RE.finditer(text or \"\")\n"
     "            if m.group(1).lower() in KNOWN_TAGS]"),

    # ---- the call site ----
    ("M12 the runtime decodes the card and then drops it on the floor", R,
     "                           actions=[card] if card is not None else None,",
     "                           actions=[],"),
    ("M13 a refused card answers SUCCESS instead of NOREPLY_ACK", R,
     "        if greeting is None and card is None:",
     "        if greeting is None and card is None and False:"),
    ("M14 the card is decoded as if every vision event were the QR one", R,
     "        card = cards_seam.decode_event(name, input_vars)",
     "        card = cards_seam.decode_event(\"eb-qr-event\", input_vars)"),
]


def run():
    proc = subprocess.run([str(PY), "-m", "pytest", *TESTS, "-q", "--no-header"],
                          cwd=ROOT, capture_output=True, text=True,
                          env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                               "HOME": os.environ.get("HOME", "/tmp"),
                               # Blanked explicitly: a bare run finds the main worktree's
                               # `mqtt/.env` and would spend real gateway calls.
                               "MOXIE_LLM_API_KEY": "", "MOXIE_LLM_BASE_URL": "",
                               "MOXIE_VOICE_BASE_URL": "", "MOXIE_STT_BASE_URL": "",
                               "MOXIE_SKIP_DOTENV": "1"})
    return proc.returncode, proc.stdout.strip().splitlines()[-1] if proc.stdout else ""


def main():
    caught, missed = 0, []
    for label, rel, old, new in MUTATIONS:
        path = ROOT / rel
        backup = path.read_text()
        if backup.count(old) != 1:
            missed.append(f"{label}: anchor not unique ({backup.count(old)})")
            continue
        path.write_text(backup.replace(old, new, 1))
        try:
            code, tail = run()
        finally:
            path.write_text(backup)
        if code == 0:
            missed.append(f"{label}: STILL GREEN — {tail}")
        else:
            caught += 1
            print(f"✅ {label} → {tail}")
    print(f"\n{caught}/{len(MUTATIONS)} mutations caught")
    for m in missed:
        print("❌ " + m)
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
