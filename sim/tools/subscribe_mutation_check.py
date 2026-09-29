"""Break each `subscribe` guard; the three ext suites (one run, so a guard whose wire test
stays green shows up) must go red.

Two failures here are SILENT: a pack's list winning the merge over the supervisor's
vision subscription (the latch then never re-asks, and presence goes quiet with nothing
logged), and S17 — a subscribed event routed to `app.respond`, which works visibly but
turns every `eb-found-face` into a billed model call; only `chat.model_calls()` notices.
Runner and verdicts: `mutation_runner.py`.

    python3 sim/tools/subscribe_mutation_check.py [ROW ...]
"""
from mutation_runner import WT, node_verdict, pytest, run_table  # noqa: F401

XG = "mqtt/moxie_sdk/content/ext/grammar.py"
XL = "mqtt/moxie_sdk/content/ext/validate.py"
V = "mqtt/moxie_sdk/content/volley.py"
C = "mqtt/moxie_sdk/content/content_app.py"
HOST = "mqtt/moxie_sdk/content/ext_host.py"
R_CONNECTION = "mqtt/supervisor/moxie_runtime/connection.py"
R_PRESENCE = "mqtt/supervisor/moxie_runtime/presence.py"
R_TURNS = "mqtt/supervisor/moxie_runtime/turns.py"
TESTS = ["sim/tests/test_ext_subscribe.py", "sim/tests/test_ext.py",
         "sim/tests/test_ext_escapes.py"]

MUTATIONS = [
    # ---- the closed vocabulary: refused at load, and again at each boundary ----
    ("S1  the load-time event allowlist is gone — a pack may name any string", XL,
     "            if not isinstance(e, str) or e not in SUBSCRIBE_EVENTS:",
     "            if not isinstance(e, str):"),
    ("S2  the event vocabulary grows an entry the recovered catalog does not have", XG,
     'SUBSCRIBE_EVENTS = ("eb-found-face", "eb-lost-target", "eb-lost-face",\n'
     '                    "eb-qr-event", "eb-dr-event", "eb-br-event")',
     'SUBSCRIBE_EVENTS = ("eb-found-face", "eb-lost-target", "eb-lost-face",\n'
     '                    "eb-qr-event", "eb-dr-event", "eb-br-event", "eb-shell")'),
    ("S3  `subscribe` goes back to being refused at load (the P1 gate)", XG,
     'P1_CAPABILITIES = frozenset({"brain", "schedule.request"})',
     'P1_CAPABILITIES = frozenset({"brain", "schedule.request", "subscribe"})'),
    ("S4  the host boundary stops bounding the name", HOST,
     "        if name not in known:\n"
     '            print(f"[content] {name!r} is not a robot event this appliance names; "',
     "        if False:\n"
     '            print(f"[content] {name!r} is not a robot event this appliance names; "'),

    # ---- merged, never replaced: layer 1, inside one volley ----
    ("S5  an extension REPLACES the volley's subscriptions instead of adding to them", HOST,
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
    ("S7  the merge is INVERTED: a pack's list replaces the runtime's", R_PRESENCE,
     "        merged = list(mine or [])",
     "        merged = [] if asked else list(mine or [])"),
    ("S8  the merge stops de-duplicating, so an event goes out twice", R_PRESENCE,
     "                    if name not in merged:\n"
     "                        merged.append(name)",
     "                    if True:\n"
     "                        merged.append(name)"),

    # ---- the gates that apply to a pack's request and not to the runtime's ----
    ("S9  MOXIE_VISION=0 no longer covers a content pack's request", R_PRESENCE,
     "        if asked:\n"
     "            if not self.vision:",
     "        if asked:\n"
     "            if False:"),
    ("S10 the pairing gate no longer covers a content pack's request", R_PRESENCE,
     "            elif not self.is_permitted(device_id):",
     "            elif False:"),
    ("S11 the runtime asks for an event it could not route if it arrived", R_PRESENCE,
     "                    if name not in presence_seam.VISION_EVENTS:",
     "                    if False:"),

    # ---- set-but-never-sent: the shape this repo keeps re-finding ----
    ("S12 the merged list is computed and then not sent (the readiness-line bug)", R_TURNS,
     "        subscribe = self._merge_subscriptions(device_id, mine, subscribe)",
     "        subscribe = mine"),
    ("S13 the turn loop decodes `Reply.subscribe` and drops it on the floor", R_TURNS,
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
    ("S17 a perceived event is routed to `app.respond` — i.e. to a BRAIN", R_PRESENCE,
     "            reply = perceive(turn)",
     "            reply = app.respond(turn)"),
    ("S18 the pack request is never recorded, so nothing can ever wake it", R_PRESENCE,
     "                        self._pack_subscribed.setdefault(device_id, {})[name] = module",
     "                        pass"),
    # The subtle half of S18, and the reason the record is written where it is: an event
    # the runtime ALREADY subscribes to for its own presence work adds nothing to `merged`,
    # so recording inside that branch would leave a pack unwakeable by exactly the events
    # it is most likely to ask for.
    ("S19 the request is recorded only when it is NEW to the merged list", R_PRESENCE,
     "                    with self._presence_lock:\n"
     "                        self._pack_subscribed.setdefault(device_id, {})[name] = module\n"
     "                    if name not in merged:\n"
     "                        merged.append(name)",
     "                    if name not in merged:\n"
     "                        with self._presence_lock:\n"
     "                            self._pack_subscribed.setdefault(device_id, {})[name] = module\n"
     "                        merged.append(name)"),
    ("S20 a pack is woken by an event it never asked for", R_PRESENCE,
     "        if asked != module:\n"
     "            return False",
     "        if False:\n"
     "            return False"),
    ("S21 the request stops being keyed on the module that made it", R_PRESENCE,
     "        if asked != module:",
     "        if asked is None:"),
    ("S22 MOXIE_VISION=0 stops covering the way back IN", R_PRESENCE,
     "        if not self.vision:\n"
     "            return False\n"
     "        if not self.is_permitted(device_id):",
     "        if False:\n"
     "            return False\n"
     "        if not self.is_permitted(device_id):"),
    ("S23 the pairing gate stops covering the way back IN", R_PRESENCE,
     "        if not self.is_permitted(device_id):\n"
     "            return False\n"
     "        module = (getattr(robot, \"module_id\", None) or \"\")",
     "        if False:\n"
     "            return False\n"
     "        module = (getattr(robot, \"module_id\", None) or \"\")"),
    ("S24 a pack that answered with NOTHING swallows the event anyway", R_PRESENCE,
     "        if not text and not actions and not subscribe:\n"
     "            return False                          # answered with nothing: not an answer",
     "        if False:\n"
     "            return False                          # answered with nothing: not an answer"),
    ("S25 a module exit forgets the vision latch but not the pack's request", R_CONNECTION,
     "                self._vision_subscribed.pop(device_id, None)\n"
     "                self._pack_subscribed.pop(device_id, None)",
     "                self._vision_subscribed.pop(device_id, None)"),
]


if __name__ == "__main__":
    raise SystemExit(run_table(MUTATIONS, lambda r: pytest(TESTS), baseline=[pytest(TESTS)]))
