"""Break each behavior-planner guard (validate's positive list, degrade-to-floor, anti-twitch
caps, per-chunk mood, idempotence, budget breaker, scored-output precedence);
`test_performance.py` + `test_automarkup.py` must go red. Runner: `mutation_runner.py`.

    python3 sim/tools/performance_mutation_check.py [ROW ...]
"""
from mutation_runner import WT, node_verdict, pytest, run_table  # noqa: F401

P = "mqtt/moxie_sdk/performance.py"
M = "mqtt/supervisor/markup.py"
R_FLEET = "mqtt/supervisor/moxie_runtime/fleet.py"
R_TURNS = "mqtt/supervisor/moxie_runtime/turns.py"
W = "mqtt/moxie_sdk/wire.py"
TESTS = ["sim/tests/test_performance.py", "sim/tests/test_automarkup.py"]

MUTATIONS = [
    # ---- the positive list: a brain may suggest, it may never authorize -------------
    ("M1  validate lets any id through (the whole positive list)", P,
     "    if value in catalog:\n        return value",
     "    if True:\n        return value"),
    ("M2  validate stops checking the line-level taxonomies", P,
     '        dialog_act=_check(p.dialog_act, vocab.DIALOG_ACTS, "dialog_act", bad),',
     "        dialog_act=p.dialog_act,"),
    ("M3  an unknown beat mood survives validation", P,
     "    if isinstance(mood, bool) or (mood is not None and mood not in vocab.MOOD_IDS):",
     "    if False:"),
    ("M4  a dropped id is no longer counted", P,
     "def _drop(bad: List[str], what: str) -> None:\n    global _DROPPED\n    _DROPPED += 1",
     "def _drop(bad: List[str], what: str) -> None:\n    global _DROPPED\n    _DROPPED += 0"),
    ("M5  strict validate stops raising", P,
     "    if strict and bad:\n        raise ValueError",
     "    if False and bad:\n        raise ValueError"),
    # ---- always degrade to the floor ------------------------------------------------
    ("M6  a planner exception is no longer caught at the seam", M,
     "        try:\n            staged = _perf.validate(_perf.plan(text, ctx=_ctx(kw)))\n"
     "        except Exception as e:                # a planner failure may never cost a turn",
     "        try:\n            staged = _perf.validate(_perf.plan(text, ctx=_ctx(kw)))\n"
     "        except ZeroDivisionError as e:"),
    ("M7  a render exception is no longer caught", M,
     "        try:\n            markup = _perf.render(staged)\n        except Exception as e:",
     "        try:\n            markup = _perf.render(staged)\n        except ZeroDivisionError as e:"),
    ("M8  the budget breaker never latches", M,
     "    if _strikes >= PLAN_BUDGET_STRIKES and not _latched:",
     "    if False and _strikes >= PLAN_BUDGET_STRIKES and not _latched:"),
    ("M9b a hint that is not in the catalog is taken anyway", P,
     "        signal=(signal if signal in vocab.SIGNALS else profile.signal),",
     "        signal=(signal or profile.signal),"),
    ("M10 MOXIE_EXPRESSIVE=off no longer passes the line through", M,
     '    if mode == MODE_OFF:\n        return Staged(text, mode=MODE_OFF)',
     '    if False:\n        return Staged(text, mode=MODE_OFF)'),
    ("M11 MOXIE_EXPRESSIVE=floor renders with the planner after all", M,
     '    mode = os.environ.get("MOXIE_EXPRESSIVE", MODE_PLANNER).strip().lower()\n'
     "    return mode if mode in _MODES else MODE_PLANNER",
     '    os.environ.get("MOXIE_EXPRESSIVE", MODE_PLANNER)\n    return MODE_PLANNER'),
    # ---- the invariants the floor and the planner share -----------------------------
    ("M12 plan restages a line that already carries markup (S1)", P,
     '    if "<" in text or ">" in text:\n        return None\n    if len(text) > MAX_PLAN_CHARS:',
     "    if False:\n        return None\n    if len(text) > MAX_PLAN_CHARS:"),
    ("M13 the budget guard stops declining an enormous line", P,
     "    if len(text) > MAX_PLAN_CHARS:\n        return None",
     "    if False:\n        return None"),
    ("M14 render drops the closing return-to-rest", P,
     '    out.append(vocab.tree_mark("Gesture_None"))\n    if showed_icon:',
     "    if showed_icon:"),
    ("M15 render never clears the icons it showed", P,
     "    if showed_icon:\n        out.append(vocab.icons_mark([], command=vocab.ICON_CLEAR))",
     "    if False:\n        out.append(vocab.icons_mark([], command=vocab.ICON_CLEAR))"),
    ("M16 render puts a gesture INSIDE the <usel> span (bad nesting)", P,
     "        if b.gesture:\n            out.append(vocab.tree_mark(b.gesture))\n        if b.text:",
     "        if b.text:"),
    ("M17 a beat's words are dropped from the rendered line (S2)", P,
     "            if b.usel:\n                out.append(vocab.usel(b.text, b.usel))\n"
     "            else:\n                out.append(b.text)",
     "            if b.usel:\n                out.append(vocab.usel(b.text, b.usel))"),
    # ---- the anti-twitch rules ------------------------------------------------------
    ("M18 the mood may change on every clause", P,
     "        if chunk_index == 0 and moods_used < MAX_MOOD_MARKS:",
     "        if chunk_index == 0:"),
    ("M19 a streamed chunk past the first emits its own mood", P,
     "            if chunk_index == 0 and moods_used < MAX_MOOD_MARKS:",
     "            if moods_used < MAX_MOOD_MARKS:"),
    ("M20 the per-line gesture cap is gone", P,
     "                if (per_sentence >= MAX_GESTURES_PER_SENTENCE\n"
     "                        or emitted_gestures >= MAX_GESTURES_PER_LINE):\n"
     "                    carry_at, carry = None, None",
     "                if False:\n                    carry_at, carry = None, None"),
    ("M21 an act that performs WITHOUT arms gestures anyway", P,
     "            if not profile.no_gesture and not sentence_has_tree:",
     "            if not sentence_has_tree:"),
    ("M22 a whole-body tree gets an arm gesture stacked on it", P,
     "        sentence_has_tree = tree is not None and si == 0",
     "        sentence_has_tree = False"),
    ("M23 a break lands after the final word of the line", P,
     "            if ci == last_clause and si < len(sentences) - 1:",
     "            if ci == last_clause:"),
    # ---- the act classifier ---------------------------------------------------------
    ("M24 a brain's dialog_act is taken without checking it", P,
     '    if hint and str(hint) in vocab.DIALOG_ACTS:\n        return str(hint)',
     "    if hint:\n        return str(hint)"),
    ("M25 every line classifies as the default act", P,
     "    for act, pattern in _ACT_CUES:\n        if pattern.search(flat):\n            return act",
     "    for act, pattern in ():\n        if pattern.search(flat):\n            return act"),
    ("M26 a question no longer earns a question act", P,
     '    if flat.rstrip().endswith("?"):',
     "    if False:"),
    # ---- the scored wire ------------------------------------------------------------
    ("M27 the runtime stops scoring published turns", R_TURNS,
     "        staged = perform(text, turn_key=turn_key, chunk_index=chunk_index, **hints)\n"
     "        scored = dict(staged.scored)",
     "        staged = perform(text, turn_key=turn_key, chunk_index=chunk_index, **hints)\n"
     "        scored = {}"),
    ("M28 an app's own scored fields skip the positive list", R_TURNS,
     "            if value and value in catalog:\n                scored[key] = value",
     "            if value:\n                scored[key] = value"),
    ("M28b an app's own scoring is dropped instead of winning", R_TURNS,
     "            value = getattr(obj, key, None)\n            if value and value in catalog:",
     "            value = None\n            if value and value in catalog:"),
    ("M28c an out-of-range mood_intensity reaches the wire", R_TURNS,
     "        if strength and 0 < int(strength) <= vocab_seam.MAX_INTENSITY:",
     "        if strength:"),
    ("M35 validate lets a bool through as a mood", P,
     "    if isinstance(mood, bool) or (mood is not None and mood not in vocab.MOOD_IDS):",
     "    if (mood is not None and mood not in vocab.MOOD_IDS):"),
    # Anchor repaired 2026-09-05: the non-streamed publish grew a `subscribe=` argument
    # (a content pack's event subscription), so `scored=scored` is no longer the last one
    # and the old anchor stopped matching. Repaired rather than deleted — a stale row
    # proves nothing, and `sim/tests/test_mutation_tables.py` is what noticed.
    ("M37 a publish path forgets to score (the coverage guard)", R_TURNS,
     '                           is_completed=None if chunk is None else True,\n'
     "                           scored=scored,\n",
     "                           is_completed=None if chunk is None else True,\n"),
    ("M29 a streamed chunk publishes without its score", R_TURNS,
     "                           is_completed=None if solo else bool(final),\n"
     "                           scored=scored)",
     "                           is_completed=None if solo else bool(final))"),
    ("M30 an authored markup line is regenerated instead of spoken verbatim", R_TURNS,
     "        return (staged.markup if markup is None else markup), scored",
     "        return staged.markup, scored"),
    ("M31 build_chat_response drops emotion/signals again", W,
     '    if emotion:\n        output["emotion"] = emotion\n    if signals:',
     "    if False:\n        output[\"emotion\"] = emotion\n    if signals:"),
    # ---- the preview hook -----------------------------------------------------------
    ("M32 preview serves a pending device (shared _command_refusal)", R_FLEET,
     "        if not self.is_permitted(device_id):\n"
     '            return {"ok": False, "device_id": device_id, "published": False,\n'
     '                    "error": "robot is pending",',
     "        if False:\n"
     '            return {"ok": False, "device_id": device_id, "published": False,\n'
     '                    "error": "robot is pending",'),
    ("M32b preview accepts an empty line", R_FLEET,
     '                    "error": "empty line", "reason": "Type a line to rehearse."}',
     '                    "error": "empty line", "reason": "..."} if False else None'),
    ("M33 preview speaks even when nobody asked it to", R_FLEET,
     "        if speak:\n            self._maybe_synthesize(device_id, staged.markup, event_id, chunk_num=0)",
     "        if True:\n            self._maybe_synthesize(device_id, staged.markup, event_id, chunk_num=0)"),
    ("M34 preview skips the output-side safety classifier", R_FLEET,
     "        if verdict and verdict.action == safety_seam.BLOCK:",
     "        if False and verdict.action == safety_seam.BLOCK:"),
]


if __name__ == "__main__":
    raise SystemExit(run_table(MUTATIONS, lambda r: pytest(TESTS, None, "-x"), baseline=[pytest(TESTS)]))
