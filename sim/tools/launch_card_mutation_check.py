"""Break each launch-card guard — what stands between a QR code any stranger can print and
an activity starting on a child's robot; the card suites (unit, runtime and SIL in ONE
run, so a guard whose wire test stays green shows up) must go red. No `-x`: the tail
reports how many tests each mutation reddens. Runner and verdicts: `mutation_runner.py`.

    python3 sim/tools/launch_card_mutation_check.py [ROW ...]
"""
from mutation_runner import WT, node_verdict, pytest, run_table  # noqa: F401

L = "mqtt/moxie_sdk/launch_cards.py"
A = "mqtt/moxie_sdk/actions.py"
R_PRESENCE = "mqtt/supervisor/moxie_runtime/presence.py"
V = "sim/virtual_moxie.py"
TESTS = ["sim/tests/test_launch_cards.py", "sim/tests/test_launch_cards_runtime.py",
         "sim/tests/test_launch_cards_sil.py"]

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
    ("M12 the runtime decodes the card and then drops it on the floor", R_PRESENCE,
     "                           actions=[card] if card is not None else None,",
     "                           actions=[],"),
    ("M13 a refused card answers SUCCESS instead of NOREPLY_ACK", R_PRESENCE,
     "        if greeting is None and card is None:",
     "        if greeting is None and card is None and False:"),
    ("M14 the card is decoded as if every vision event were the QR one", R_PRESENCE,
     "        card = cards_seam.decode_event(name, input_vars)",
     "        card = cards_seam.decode_event(\"eb-qr-event\", input_vars)"),

    # ---- the CLIENT half (T10): the SIL robot's own end of the round trip ----
    # M1-M14 all mutate the SERVER. A SIL test could pass every one of them and still be
    # reading the runtime's own publish record rather than the robot's state, so these
    # five break the ROBOT instead. A row here that leaves the suite green means the SIL
    # test is not on the wire.
    ("M15 the SIL robot stops consuming response_actions — back to reading only text", V,
     "        self._on_actions(payload)",
     "        _ = payload  # self._on_actions(payload)"),
    ("M16 a launch is recorded without the module id — WHICH activity is lost", V,
     "            self.actions[\"module_id\"] = module_id\n"
     "            self.actions[\"content_id\"] = content_id",
     "            self.actions[\"module_id\"] = \"\"\n"
     "            self.actions[\"content_id\"] = content_id"),
    ("M17 send_face_event drops the marker payload — the card never leaves the robot", V,
     "        if input_vars is None:\n"
     "            input_vars = self.value_vars(name, value)",
     "        if input_vars is None and False:\n"
     "            input_vars = self.value_vars(name, value)"),
    ("M18 every marker payload is published under the QR key (an ArUco id reads as a card)",
     V,
     "        key = cls.EVENT_VALUE_KEYS.get(name)",
     "        key = \"$eb_qr_value\" if name in cls.EVENT_VALUE_KEYS else None"),
    ("M19 --face-value is accepted and then not sent", V,
     "                self.send_face_event(kind, value=value)",
     "                self.send_face_event(kind)"),
]


if __name__ == "__main__":
    raise SystemExit(run_table(MUTATIONS, lambda r: pytest(TESTS), baseline=[pytest(TESTS)]))
