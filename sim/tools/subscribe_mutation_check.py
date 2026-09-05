#!/usr/bin/env python3
"""👁️  Delete one `subscribe` guard at a time and require a test to go red.

The house rule is that a feature's tests are proven in BOTH directions: green with the
guard, red without it. A green suite says the guards are present; only this says they are
**load-bearing**, and *"a guard never observed to fail is not a guard"*.

It matters twice over here, because two of the rows below break something whose failure is
**silent**. `moxie_runtime._merge_subscriptions` merges a content pack's requested events
INTO the supervisor's own vision subscription and never over it; and
`_vision_subscription` **latches** (`_vision_subscribed[device] = module`) at the moment it
hands its list over. So an implementation in which a pack's list wins sets the latch,
publishes a list without the vision events in it, and never asks again for that
`(device, module)`. Presence goes quiet, the greeting rule stops firing, launch cards stop
decoding — and nothing is logged, because as far as the runtime is concerned it subscribed.
That is playbook rule 23's *"a cached belief about a moving thing"*, which is the most
common bug this project has produced, so the direction of that merge gets a mutation of its
own rather than a comment.

    python3 sim/tools/subscribe_mutation_check.py      # from the repo root

Since 2026-09-05 the table covers both directions of `subscribe`. S1-S16 are the outbound
half — a pack's request reaching `EventSubscription.active[]` without ever displacing the
supervisor's own list. **S17-S25 are the inbound half**, and S17 is the row with the
sharpest teeth in this file: a subscribed event must be answered by the pack's *local*
evaluator and never by a brain, because `eb-found-face` fires every time a child moves
around a room and a model call per event would turn presence into a billing event
(`docs/architecture/vision.md` §7.1). Route the event to `app.respond` instead — which
looks exactly like sensible reuse — and every visible behaviour still works: the pack
answers, the child hears a line, the wire is well-formed. The only thing that notices is
`moxie_sdk.chat.model_calls()`, the recorded counter, which is why that counter exists at
all and why deleting it is not a refactor.

Sibling of `ext_mutation_check.py` (which owns the sandbox's escape guards and runs only
`test_ext_escapes.py`) and of `launch_card_mutation_check.py`, whose multi-file runner this
copies. A separate table rather than more rows in `ext_mutation_check.py` because half of
these guards live in the *runtime*, and the ext checker cannot see a runtime test.

The anchors are held honest by `sim/tests/test_mutation_tables.py`, which fails if a
refactor makes any row below a no-op — repair the anchor, never delete the row.
"""
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
PY = ROOT / ".venv/bin/python"
if not PY.exists():
    PY = pathlib.Path(sys.executable)

#: All three suites in ONE run, deliberately, for the same comparative reason
#: `launch_card_mutation_check.py` gives: a guard that reddens the unit chain but leaves
#: the wire test green is a guard whose wire test is not actually asserting on the wire,
#: and only one run can show that.
TESTS = ["sim/tests/test_ext_subscribe.py",     # the chain, the merge, the wire
         "sim/tests/test_ext.py",               # the G6 conformance row
         "sim/tests/test_ext_escapes.py"]       # the three load gates

X = "mqtt/moxie_sdk/content/ext.py"
V = "mqtt/moxie_sdk/content/volley.py"
C = "mqtt/moxie_sdk/content/content_app.py"
R = "mqtt/supervisor/moxie_runtime.py"

MUTATIONS = [
    # ---- the closed vocabulary: refused at load, and again at each boundary ----
    ("S1  the load-time event allowlist is gone — a pack may name any string", X,
     "            if not isinstance(e, str) or e not in SUBSCRIBE_EVENTS:",
     "            if not isinstance(e, str):"),
    ("S2  the event vocabulary grows an entry the recovered catalog does not have", X,
     'SUBSCRIBE_EVENTS = ("eb-found-face", "eb-lost-target", "eb-lost-face",\n'
     '                    "eb-qr-event", "eb-dr-event", "eb-br-event")',
     'SUBSCRIBE_EVENTS = ("eb-found-face", "eb-lost-target", "eb-lost-face",\n'
     '                    "eb-qr-event", "eb-dr-event", "eb-br-event", "eb-shell")'),
    ("S3  `subscribe` goes back to being refused at load (the P1 gate)", X,
     'P1_CAPABILITIES = frozenset({"brain", "schedule.request"})',
     'P1_CAPABILITIES = frozenset({"brain", "schedule.request", "subscribe"})'),
    ("S4  the host boundary stops bounding the name", C,
     "        if name not in known:\n"
     '            print(f"[content] {name!r} is not a robot event this appliance names; "',
     "        if False:\n"
     '            print(f"[content] {name!r} is not a robot event this appliance names; "'),

    # ---- merged, never replaced: layer 1, inside one volley ----
    ("S5  an extension REPLACES the volley's subscriptions instead of adding to them", C,
     "            volley.add_subscriptions(events)",
     "            volley.update_subscriptions(events)"),
    ("S6  `add_subscriptions` stops de-duplicating", V,
     "            if e not in self.subscriptions:\n"
     "                self.subscriptions.append(e)",
     "            if True:\n"
     "                self.subscriptions.append(e)"),

    # ---- merged, never replaced: layer 2, against the supervisor's own set ----
    # The row this whole file exists for. Written as "when a pack asked, drop the
    # runtime's list" rather than as a blanket `merged = []`, because that is the
    # *plausible* wrong implementation — `asked or mine` — and it leaves the
    # nothing-asked case green, so only the direction tests may redden.
    ("S7  the merge is INVERTED: a pack's list replaces the runtime's", R,
     "        merged = list(mine or [])",
     "        merged = [] if asked else list(mine or [])"),
    ("S8  the merge stops de-duplicating, so an event goes out twice", R,
     "                    if name not in merged:\n"
     "                        merged.append(name)",
     "                    if True:\n"
     "                        merged.append(name)"),

    # ---- the gates that apply to a pack's request and not to the runtime's ----
    ("S9  MOXIE_VISION=0 no longer covers a content pack's request", R,
     "        if asked:\n"
     "            if not self.vision:",
     "        if asked:\n"
     "            if False:"),
    ("S10 the pairing gate no longer covers a content pack's request", R,
     "            elif not self.is_permitted(device_id):",
     "            elif False:"),
    ("S11 the runtime asks for an event it could not route if it arrived", R,
     "                    if name not in presence_seam.VISION_EVENTS:",
     "                    if False:"),

    # ---- set-but-never-sent: the shape this repo keeps re-finding ----
    ("S12 the merged list is computed and then not sent (the readiness-line bug)", R,
     "        subscribe = self._merge_subscriptions(device_id, mine, subscribe)",
     "        subscribe = mine"),
    ("S13 the turn loop decodes `Reply.subscribe` and drops it on the floor", R,
     "                           subscribe=reply.subscribe)",
     "                           subscribe=None)"),
    ("S14 `_reply_from_volley` builds a Reply that forgets what it asked to perceive", C,
     "        return Reply(text=text, markup=markup, actions=actions,\n"
     "                     subscribe=subscriptions_of(v))",
     "        return Reply(text=text, markup=markup, actions=actions)"),
    ("S15 a pack that subscribed but did not take the turn loses its subscription", C,
     "        return Reply(text=text, actions=actions, subscribe=subscribe)",
     "        return Reply(text=text, actions=actions)"),
    ("S16 a global that only subscribed falls through and loses it", C,
     "                if (v.output_text is not None or v.execution_actions\n"
     "                        or v.subscriptions):",
     "                if (v.output_text is not None or v.execution_actions):"),

    # ---- the INBOUND half: a subscribed event wakes the pack that asked for it ----
    # Added 2026-09-05 with `_wake_subscribed_pack`. S17 is the row this half exists to
    # protect and the only one here whose failure costs money rather than behaviour: the
    # event must reach the pack's LOCAL evaluator and never a brain, because
    # `eb-found-face` fires every time a child moves around the room (vision.md §7.1). It
    # is written as "route the event to `respond` instead", which is the *plausible* wrong
    # implementation — it looks like reuse, and it produces a perfectly good reply.
    #
    # **S17 corrected the test it was meant to protect, which is the whole argument for
    # running these by hand.** In its first draft the zero-model-call test drove only a
    # QR event that the pack HAD a rule for — and under S17 that spends nothing, because
    # `ContentApp.respond` runs the same `turn.before` extension, the rule handles the
    # turn and the model is never reached. The counter agreed with the wrong
    # implementation and the row was caught by six unrelated behaviour tests instead. The
    # case that actually costs money is a subscribed event with **no** matching rule: the
    # extension matches nothing, the conversation runs, and a brain answers a robot's eye
    # — which is `eb-found-face` on any pack that subscribed to it, i.e. every time a
    # child walks back into frame. That turn is now step 4 of the test, with the counter
    # asserted BEFORE the reply shape so the counter is provably the guard. Measured
    # after the fix: `an unmatched perception event spent 1 model call(s)`.
    ("S17 a perceived event is routed to `app.respond` — i.e. to a BRAIN", R,
     "            reply = perceive(turn)",
     "            reply = app.respond(turn)"),
    ("S18 the pack request is never recorded, so nothing can ever wake it", R,
     "                        self._pack_subscribed.setdefault(device_id, {})[name] = module",
     "                        pass"),
    # The subtle half of S18, and the reason the record is written where it is: an event
    # the runtime ALREADY subscribes to for its own presence work adds nothing to `merged`,
    # so recording inside that branch would leave a pack unwakeable by exactly the events
    # it is most likely to ask for.
    ("S19 the request is recorded only when it is NEW to the merged list", R,
     "                    with self._presence_lock:\n"
     "                        self._pack_subscribed.setdefault(device_id, {})[name] = module\n"
     "                    if name not in merged:\n"
     "                        merged.append(name)",
     "                    if name not in merged:\n"
     "                        with self._presence_lock:\n"
     "                            self._pack_subscribed.setdefault(device_id, {})[name] = module\n"
     "                        merged.append(name)"),
    ("S20 a pack is woken by an event it never asked for", R,
     "        if asked != module:\n"
     "            return False",
     "        if False:\n"
     "            return False"),
    ("S21 the request stops being keyed on the module that made it", R,
     "        if asked != module:",
     "        if asked is None:"),
    ("S22 MOXIE_VISION=0 stops covering the way back IN", R,
     "        if not self.vision:\n"
     "            return False\n"
     "        if not self.is_permitted(device_id):",
     "        if False:\n"
     "            return False\n"
     "        if not self.is_permitted(device_id):"),
    ("S23 the pairing gate stops covering the way back IN", R,
     "        if not self.is_permitted(device_id):\n"
     "            return False\n"
     "        module = (getattr(robot, \"module_id\", None) or \"\")",
     "        if False:\n"
     "            return False\n"
     "        module = (getattr(robot, \"module_id\", None) or \"\")"),
    ("S24 a pack that answered with NOTHING swallows the event anyway", R,
     "        if not text and not actions and not subscribe:\n"
     "            return False                          # answered with nothing: not an answer",
     "        if False:\n"
     "            return False                          # answered with nothing: not an answer"),
    ("S25 a module exit forgets the vision latch but not the pack's request", R,
     "                self._vision_subscribed.pop(device_id, None)\n"
     "                self._pack_subscribed.pop(device_id, None)",
     "                self._vision_subscribed.pop(device_id, None)"),
]


def run():
    proc = subprocess.run([str(PY), "-m", "pytest", *TESTS, "-q", "--no-header",
                           "-p", "no:cacheprovider"],
                          cwd=ROOT, capture_output=True, text=True,
                          env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                               "HOME": os.environ.get("HOME", "/tmp"),
                               # Blanked explicitly: a bare run finds the main worktree's
                               # `mqtt/.env` and would spend real gateway calls.
                               "MOXIE_LLM_API_KEY": "", "MOXIE_LLM_BASE_URL": "",
                               "MOXIE_VOICE_BASE_URL": "", "MOXIE_STT_BASE_URL": "",
                               "MOXIE_SKIP_DOTENV": "1",
                               # Without it a `__pycache__` entry from an earlier mutation
                               # can shadow a later one, and a guard reads as un-caught
                               # when it is fine (`ext_mutation_check.py`'s lesson).
                               "PYTHONDONTWRITEBYTECODE": "1"})
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout else ""
    return proc.returncode, tail


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
