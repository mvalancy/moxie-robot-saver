"""
ContentApp — runs a content module through the AI seam
(docs/architecture/content-module-contract.md). Each turn: `globals[]` first (always-on
commands), else the active conversation — render its prompt over the volley, ask the
injected `chat(messages) -> str` brain, return a Reply. The brain is taught `<exit>` and
`<sleep>` only (`actions.LEAVE_TAG_PROMPT`); it is never told a module id to launch.

**Opener.** A `prompt` with no speech is the robot starting the conversation, and is
answered with the conversation's `opener` instead of the brain (OpenMoxie
`conversations.py` `handle_volley`). Any speech, `continue` and `reprompt` go to the brain.
An opener's action tags act only when written whole in the alternative said, as the pack
review names them, and none is said (`said_opener`).

Global handlers are registered Python callables or sandboxed extensions (`ext/`); a
module's `code` string is never executed.

**Memory.** `volley.persist_data` is loaded per turn from the durable `MemoryStore` and
rendered into the prompt. When a conversation ends (`on_session_end`: `<exit>`, `<sleep>`,
module switch or disconnect), a module with a declared `memory` block is summarized into its
namespace with provenance — OpenMoxie's MemoryChat `complete_handler`, declared rather
than scripted:

    "memory": {"namespace": "memory_chat", "summarize": true, "min_volleys": 2}

The extension *host* — the only code that builds the evaluator's fact base and applies
its effects — is `ext_host.py`.
"""
from __future__ import annotations
import hashlib
import json
import random
import re
import time
from collections import Counter
from typing import Callable, Optional

from ..app import MoxieApp
from ..actions import (LEAVE_TAG_PROMPT, lift_every_action_tag, parse_action_tags,
                       tidy_spoken_text)
from ..automarkup import annotate, enabled as _automarkup_enabled
from ..memory_store import MemoryStore
from ..types import Turn, Reply, RobotContext
from .module import ContentModule
from .volley import Volley, Session
from .memory import default_classifier, note_used, provenance, wrap_facts
from .render import render_prompt
from . import ext
from .. import presence as _presence
from .ext_host import (_action_key, apply_ext_effects, _clock_local, _ext_digest,
    EXT_EVENTS_CAP, EXT_EVENTS_COLLECTION, ext_facts, ext_namespace, execution_actions_of,
    full_key_of, literal_actions, REFUSED_TAG_REASON, REFUSED_TAG_WORDS,
    SHIPPED_EXTRA_GRANTS, shipped_ext_digests, subscriptions_of)
from .ext_host import robot_events, robot_functions  # noqa: F401  (re-exported surface)


def _presence_vars(robot) -> dict:
    """The presence render variable for a call that has a `RobotContext` but no `Turn`
    (the opener). Same shape `Turn.presence` carries."""
    return _presence.snapshot(getattr(robot, "extra", {}).get("presence") or {})


#: The start of a template construct (`{{ … }}`, `{% … %}`, `{# … #}`; render.py's
#: grammar), or an alternative separator.
_OPENER_TOKEN = re.compile(r"\{[{%#]|\|")
#: Where each construct ends: the first closer after its opener.
_CLOSER = {"{{": "}}", "{%": "%}", "{#": "#}"}


def opener_alternatives(opener: str) -> list:
    """`opener`'s `|`-separated alternatives, unrendered. A `|` inside a template construct
    is a Jinja filter (`{{ name | upper }}`) or comment text, not a separator, so the
    alternatives are exactly `str.split("|")`'s unless a construct holds one.

    One pass, linear in the length: a construct is skipped whole, and a closer missing
    from the rest of the text is never searched for again. A regex over every construct
    was quadratic on an unclosed `{{` (5 s for a 100 KB opener)."""
    alts, start, pos, unclosed = [], 0, 0, set()
    while True:
        m = _OPENER_TOKEN.search(opener, pos)
        if m is None:
            break
        token = m.group()
        if token == "|":
            alts.append(opener[start:m.start()])
            start = pos = m.end()
            continue
        end = -1 if token in unclosed else opener.find(_CLOSER[token], m.end())
        if end >= 0:
            pos = end + 2
        else:
            # Plain text, as is every later one like it; its second character may still
            # start a construct (`{{%`).
            unclosed.add(token)
            pos = m.start() + 1
    alts.append(opener[start:])
    return alts


def _shuffled(n: int, rng):
    """`range(n)` in a random order, drawn lazily (Fisher-Yates): a caller that stops at
    the first draw pays for one."""
    order = list(range(n))
    for k in range(n):
        j = rng.randrange(k, n)
        order[k], order[j] = order[j], order[k]
        yield order[k]


def _pick(opener: str, context: dict, last: Optional[str] = None,
          rng=random) -> Optional[tuple]:
    """`(alternative, line)`: `pick_opener`'s line, and the alternative, unrendered, that it
    was rendered from."""
    alts, seen = [], set()
    for alt in opener_alternatives(opener):
        key = alt.replace("<opener>", "").strip()
        if key and key not in seen:
            seen.add(key)
            alts.append(alt)
    again = None
    for i in (range(len(alts)) if last is None else _shuffled(len(alts), rng)):
        line = render_prompt(alts[i], context).replace("<opener>", "").strip()
        if line and line != last:
            return alts[i], line
        again = again or ((alts[i], line) if line else None)
    return again


def pick_opener(opener: str, context: dict, last: Optional[str] = None,
                rng=random) -> Optional[str]:
    """The opener line to say, rendered over `context` with `<opener>` stripped and any
    `<exit>`/`<sleep>`/`<launch:…>` still in; None when no alternative says anything.

    With no `last` it is the first alternative that says something: what a robot hears
    first, and the `opener` the content preview route returns. Otherwise it is a random other
    one, and `last` again only when nothing else says anything. Alternatives are rendered one
    at a time, as they are drawn, until one says something: an opener with thousands of
    alternatives costs one render when they say something, and one more for each drawn
    alternative that says nothing (5,000 that say nothing took 0.6 s per empty prompt, and
    50,000 took 6.5-7.1 s, measured)."""
    picked = _pick(opener, context, last, rng)
    return picked[1] if picked else None


def spoken_opener(line: str) -> str:
    """What a robot says for the opener `line`: every tag of ours lifted, and every one that
    forms once those are lifted (`actions.lift_every_action_tag`), so none is said, however
    the template built it; then tidied as a line is. The same as the robot path's own
    one-pass parse speaks, unless a tag of ours forms only once the tags around it are
    lifted."""
    return tidy_spoken_text(lift_every_action_tag(line))


def said_opener(alternative: str, line: str) -> tuple:
    """`(text, actions)`: what a robot says and does for `line`, the opener `alternative`
    rendered. The actions are the robot path's own parse of `line`, kept only where the same
    action (its type and every field as parsed, `ext_host._action_key`) is written whole in
    `alternative`'s own text, unrendered, and at most as many times as it is written there,
    read with the same parse. So a tag that only forms as the template renders (`{{ '<la' ~
    'unch:DRAW>' }}`, a filter, a `{% set %}`, pieces joined around a comment or across what
    reads as two alternatives, a loop's copies) adds no action: what acts is at most what the
    alternative writes whole, which the pack review names (`packs.review.opener_warnings`).
    The text is `spoken_opener`'s."""
    written = Counter(_action_key(a) for a in parse_action_tags(alternative)[1])
    actions = []
    for action in parse_action_tags(line)[1]:
        key = _action_key(action)
        if written[key] > 0:
            written[key] -= 1
            actions.append(action)
    return spoken_opener(line), actions

ChatFn = Callable[[list], str]          # messages [{role,content}] -> assistant text
GlobalHandler = Callable[[Volley, Session], None]   # sets volley.output / actions


def _child_pii(robot: RobotContext) -> dict:
    """The child profile as the volley/prompt sees it (`volley.config.child_pii`)."""
    c = robot.child
    return {"nickname": c.nickname, "pronouns": c.pronouns,
            "birthday": c.birthday_iso, "notes": c.notes}


class ContentApp(MoxieApp):
    name = "content"

    def __init__(self, module: ContentModule, chat: ChatFn, *, persona: str = "",
                 default_module_id: Optional[str] = None,
                 global_handlers: Optional[dict] = None,
                 memory: Optional[MemoryStore] = None,
                 safety_classifier=None, content_defaults=None,
                 ext_grants=None, ext_limits=None, clock=None, monotonic=None,
                 rng=None):
        self.module = module
        # 📦 The shipped baseline, kept apart from `module` (= defaults ⊕ overlay) so a
        # content `undo` can restore a shipped item. None ⇒ none recorded.
        self.content_defaults = content_defaults
        self._chat = chat
        self._persona = persona
        self._default_module_id = default_module_id
        self._handlers: dict = dict(global_handlers or {})
        # Long-term memory; built by default, `memory=False` disables it.
        self.memory = (MemoryStore() if memory is None
                       else (memory or None))
        # Decides what may never be remembered; resolved lazily (see `classifier`).
        self._classifier = safety_classifier
        self._classifier_resolved = safety_classifier is not None
        # 🧬 Capabilities granted to imported extensions (default `ext.DEFAULT_GRANTS`).
        # No env var or console control: widening it is a reviewed code change (P1).
        self._ext_grants = (frozenset(ext.DEFAULT_GRANTS) if ext_grants is None
                            else frozenset(ext_grants))
        # Our shipped extensions get a wider set, trusted by the program's digest, not its
        # name: a pack overriding a shipped global does not inherit its grants.
        self._ext_shipped_grants = (self._ext_grants | SHIPPED_EXTRA_GRANTS
                                    if ext_grants is None else self._ext_grants)
        self._ext_shipped = shipped_ext_digests(content_defaults)
        self._ext_limits = ext_limits
        # Clock and entropy are injected into the evaluator, never imported by it (X7).
        self._clock = clock or time.time
        self._monotonic = monotonic or time.monotonic
        #: `{(device_id, extension_id): breaches}` this session — quarantine counter (§6.4).
        self._ext_breaches: dict = {}
        #: `{(device_id, extension_id): tags refused}`: a line's tags the review did not
        #: name, taken out (`ext_host.apply_ext_effects`). Counted apart from breaches: a
        #: refusal never quarantines.
        self._ext_refusals: dict = {}
        #: Already-reported `(device_id, extension_id, reason)`: one event per problem.
        self._ext_reported: set = set()
        #: `{digest: literal_actions(program)}`, the tags each rule of a program wrote whole,
        #: read once per program (by its content, so a different program under the same
        #: name never inherits them) rather than on every turn.
        self._ext_literal: dict = {}
        #: `{device_id: the opener line it heard last}`, so an opener never repeats back
        #: to back; `rng` picks among the others (injectable for tests).
        self._last_opener: dict = {}
        self._rng = rng or random

    def register_global(self, name: str, handler: GlobalHandler) -> None:
        self._handlers[name] = handler

    # ---- memory ----
    @property
    def classifier(self):
        if not self._classifier_resolved:
            self._classifier = default_classifier()
            self._classifier_resolved = True
        return self._classifier

    def persist_data(self, device_id: str) -> dict:
        """This robot's durable `persist_data`, ready to render into a prompt."""
        if self.memory is None or not device_id:
            return {}
        try:
            return wrap_facts(self.memory.load(device_id))
        except Exception as e:                    # a broken memory file must not end a turn
            print(f"[content] memory load failed ({e}); continuing without it", flush=True)
            return {}

    def _save_persist_data(self, device_id: str, data: dict, before: str) -> None:
        """Write `persist_data` back if module code changed it this turn (the contract's
        "cross-session storage"; `local_data` is deliberately never written)."""
        if self.memory is None or not device_id:
            return
        try:
            if json.dumps(data, sort_keys=True, default=str) == before:
                return
            self.memory.save(device_id, data)     # a NO_DATA policy drops it here
        except Exception as e:
            print(f"[content] memory save failed ({e})", flush=True)

    # ---- helpers ----
    def _volley(self, turn: Turn, entities=None) -> Volley:
        return Volley(speech=turn.speech, config={"child_pii": _child_pii(turn.robot)},
                      request={"input_vars": turn.input_vars}, entities=entities or [],
                      persist_data=self.persist_data(turn.robot.device_id))

    def _session(self, turn: Turn, *, history, persist_data, conv=None) -> Session:
        """The conversation object module code sees — carrying the brain, so the
        contract's `session.summarize(...)` can actually call it."""
        return Session(history=history, persist_data=persist_data,
                       max_volleys=conv.max_volleys if conv else 40,
                       chat=self._chat,
                       module_id=(conv.module_id if conv else turn.robot.module_id) or "",
                       content_id=(conv.content_id if conv else turn.robot.content_id) or "")

    def _active_conversation(self, turn: Turn):
        mid = turn.robot.module_id or self._default_module_id
        conv = self.module.conversation(mid, turn.robot.content_id or "") if mid else None
        if conv is None and self.module.conversations:
            conv = self.module.conversations[0]      # fall back to the first
        return conv

    @staticmethod
    def _reply_from_volley(v: Volley) -> Reply:
        # Same tag parse as model output, so a handler can write "<exit>" (actions.py).
        text, actions = parse_action_tags(v.output_text or "")
        # What a handler/extension asked the robot to *run* → `execute` actions.
        actions += execution_actions_of(v)
        markup = parse_action_tags(v.output_markup)[0] if v.output_markup else None
        # Plain markup from a handler would bypass the runtime's markup seam (which fires
        # on `markup is None`), so apply the floor here; authored tags pass unchanged.
        if markup and _automarkup_enabled():
            markup = annotate(markup)
        # What it asked to *perceive*; the runtime merges this into its own subscription.
        return Reply(text=text, markup=markup, actions=actions,
                     subscribe=subscriptions_of(v))

    # ---- sandboxed extensions (BEYOND #6) ----
    def _ext_limits_now(self):
        """The budget from `config.py` (supervisor) or `ext/`'s defaults (bare SDK)."""
        if self._ext_limits is not None:
            return self._ext_limits
        try:
            import config as _cfg
            return ext.Limits(max_steps=_cfg.EXT_MAX_STEPS,
                              budget_s=_cfg.EXT_BUDGET_S,
                              max_value_bytes=_cfg.EXT_MAX_VALUE_BYTES,
                              max_total_bytes=_cfg.EXT_MAX_TOTAL_BYTES)
        except Exception:
            return ext.Limits()

    def _ext_breach(self, device_id: str, ext_id: str, result, *, hook: str) -> None:
        """Record one breach: quarantine after `MOXIE_EXT_MAX_BREACHES` and tell the
        parent once, never the child — the turn proceeds as if there were no extension
        (§6.4; unlike upstream's spoken "Script error", U6)."""
        key = (device_id, ext_id)
        self._ext_breaches[key] = self._ext_breaches.get(key, 0) + 1
        count = self._ext_breaches[key]
        self._ext_report(device_id, ext_id, hook=hook, reason=result.breach or "invalid",
                         sentence=result.sentence,
                         line=f"stopped: {result.reason}; Moxie carried on without it",
                         quarantined=count >= self._ext_max_breaches())

    def _ext_refused(self, device_id: str, ext_id: str, refused: list, *,
                     hook: str) -> None:
        """A line carried an action tag the rule's own text does not write whole, so the
        host took it out (`ext_host.apply_ext_effects`): count it and tell the parent
        once, as a breach is told. Not a breach: the line was said without the tag and the
        turn went on, as with a markup tag the catalogue drops, so it never counts towards
        quarantine. The log names the tag's kind only, never its text, which may be what
        the child said."""
        key = (device_id, ext_id)
        self._ext_refusals[key] = self._ext_refusals.get(key, 0) + len(refused)
        kinds = ", ".join(a.type.name.lower() for a in refused)
        self._ext_report(device_id, ext_id, hook=hook, reason=REFUSED_TAG_REASON,
                         sentence=REFUSED_TAG_WORDS,
                         line=f"took {len(refused)} tag(s) out of its line ({kinds}): not "
                              f"written whole in the rule's own text, so its review names "
                              f"no such thing; the line was said without them",
                         quarantined=self._ext_quarantined(device_id, ext_id))

    def _ext_report(self, device_id: str, ext_id: str, *, hook: str, reason: str,
                    sentence: str, line: str, quarantined: bool) -> None:
        """Tell the parent once per (device, extension, reason), never the child: one log
        line, and one row in the bounded `ext_events` ring the console reads (M4)."""
        seen = (device_id, ext_id, reason)
        if seen in self._ext_reported:
            return
        self._ext_reported.add(seen)
        print(f"[ext] {ext_id} ({hook}) {line}", flush=True)
        store = getattr(self.memory, "store", None)
        if store is None or not device_id:
            return
        try:
            store.append(device_id, EXT_EVENTS_COLLECTION, {
                "at": int(self._clock()), "extension": ext_id, "hook": hook,
                "reason": reason, "sentence": sentence, "quarantined": quarantined,
            }, cap=EXT_EVENTS_CAP)
        except Exception as e:
            print(f"[ext] could not record the breach ({e})", flush=True)

    def _ext_allowed(self, digest: str, block: dict, rule: int) -> frozenset:
        """The actions the matched rule's spoken line may act on: the tags written whole
        in that rule's own text (`ext_host.literal_actions`), read once per program and
        kept by the program's digest. No rule, or none that matched: nothing."""
        sets = self._ext_literal.get(digest)
        if sets is None:
            if len(self._ext_literal) >= 256:
                self._ext_literal.clear()         # a bound, not a policy: packs are few
            sets = self._ext_literal[digest] = literal_actions(block)
        return sets[rule] if 0 <= rule < len(sets) else frozenset()

    @staticmethod
    def _ext_max_breaches() -> int:
        try:
            import config as _cfg
            return int(_cfg.EXT_MAX_BREACHES)
        except Exception:
            return ext.DEFAULT_MAX_BREACHES

    def _ext_quarantined(self, device_id: str, ext_id: str) -> bool:
        return self._ext_breaches.get((device_id, ext_id), 0) >= self._ext_max_breaches()

    def run_extension(self, turn: Turn, volley: Volley, session: Session, *,
                      hook: str, kind: str, key: str, data: dict):
        """Run one item's extension for this turn, or return None — "carry on as before",
        the answer for every failure, no match, no extension and quarantine (§6.4)."""
        block = (data or {}).get("extension") or {}
        if not block or block.get("on") != hook:
            return None
        ext_id = full_key_of(kind, key)
        digest = _ext_digest(block)
        grants = (self._ext_shipped_grants
                  if digest in self._ext_shipped else self._ext_grants)
        device_id = getattr(turn.robot, "device_id", "") or ""
        if self._ext_quarantined(device_id, ext_id):
            return None                       # already broken three times this session
        # Validated every turn, not only at import (T17).
        reasons = ext.validate(block, grants=grants)
        if reasons:
            self._ext_breach(device_id, ext_id,
                             ext.ExtResult(ok=False, reason=reasons[0], breach="invalid"),
                             hook=hook)
            return None
        namespace = ext_namespace(kind, key, data)
        facts = ext_facts(volley, session, namespace=namespace,
                          grants=grants,
                          presence=turn.presence or _presence_vars(turn.robot))
        now = self._clock()
        zone = (getattr(turn.robot, "extra", None) or {}).get("timezone_id")  # its push's
        seed = int.from_bytes(hashlib.sha256(
            f"{device_id}|{turn.speech}|{ext_id}|{int(now)}".encode()).digest()[:4], "big")
        result = ext.evaluate(block, facts, grants=grants,
                              now_ms=int(now * 1000),
                              clock_local=_clock_local(now, zone), seed=seed,
                              monotonic=self._monotonic,
                              limits=self._ext_limits_now())
        if not result.ok:
            self._ext_breach(device_id, ext_id, result, hook=hook)
            return None
        if not result.effects and not result.handled:
            return None                       # no rule matched: a success, not a failure
        # A line acts only on the tags the matched rule wrote whole (the ones its review
        # names); any other tag it carries is taken out and reported, never acted on.
        stats = apply_ext_effects(result.effects, volley=volley, memory=self.memory,
                                  device_id=device_id, namespace=namespace,
                                  classifier=self.classifier,
                                  module_id=getattr(turn.robot, "module_id", "") or "",
                                  content_id=getattr(turn.robot, "content_id", "") or "",
                                  allowed=self._ext_allowed(digest, block, result.rule))
        if stats["refused"]:
            self._ext_refused(device_id, ext_id, stats["refused"], hook=hook)
        for line in result.notes:
            print(f"[ext] {ext_id}: {line}", flush=True)
        return result

    # ---- the opener ----
    def _opener_reply(self, robot: RobotContext, conv, volley=None,
                      presence=None) -> Optional[Reply]:
        """`conv`'s opener as a Reply, or None when it has none. Never calls the brain.

        The `|`-alternatives rotate per device and never repeat back to back; a device
        hears the first alternative first (`pick_opener`). `<opener>` is stripped, and
        `<exit>`, `<sleep>` or `<launch:…>` become actions only when written whole in the
        alternative said, as its pack review names them; no tag of ours is said
        (`said_opener`). The same for every opener, shipped or imported: the shipped ones
        write no tag."""
        if conv is None or not conv.opener:
            return None
        context = {"volley": volley or self._volley(Turn(robot=robot, speech="")),
                   "session": Session(), "presence": presence or _presence_vars(robot)}
        device_id = getattr(robot, "device_id", "") or ""
        picked = _pick(conv.opener, context, self._last_opener.get(device_id), self._rng)
        if picked is None:
            return None
        alternative, line = picked
        self._last_opener[device_id] = line
        text, actions = said_opener(alternative, line)
        return Reply(text=text, actions=actions)

    # ---- MoxieApp ----
    def greeting(self, robot: RobotContext) -> Optional[Reply]:
        return self._opener_reply(robot,
                                  self._active_conversation(Turn(robot=robot, speech="")))

    def respond(self, turn: Turn) -> Reply:
        # 1) globals first — always-on commands (timers, "stop", …)
        hit = self.module.match_global(turn.speech)
        if hit is not None:
            g, entities = hit
            handler = self._handlers.get(g.name)
            if handler or getattr(g, "extension", None):
                v = self._volley(turn, entities=entities)
                before = json.dumps(v.persist_data, sort_keys=True, default=str)
                session = self._session(turn, history=list(turn.history),
                                        persist_data=v.persist_data)
                if handler:
                    handler(v, session)
                else:
                    # 🧬 A pack's extension fills the socket (a registered handler wins).
                    self.run_extension(turn, v, session, hook="global",
                                       kind="global", key=g.name,
                                       data={"extension": g.extension,
                                             "name": g.name})
                self._save_persist_data(turn.robot.device_id, v.persist_data, before)
                # An action or subscription alone is output too; falling through would
                # rebuild the volley and drop it.
                if (v.output_text is not None or v.execution_actions
                        or v.subscriptions):
                    return self._reply_from_volley(v)
            # matched but nothing produced output → fall through to conversation

        # 2) the active conversation module
        conv = self._active_conversation(turn)
        if conv is None:
            return Reply(text="Let's chat! What's on your mind?")
        v = self._volley(turn)
        session = self._session(turn, history=list(turn.history),
                                persist_data=v.persist_data, conv=conv)
        # 🧬 `on: turn.before` (upstream's `pre_process`): may set `handled` to skip the
        # model; a failure is skipped and the model runs.
        pre = self.run_extension(turn, v, session, hook="turn.before",
                                 kind="conversation",
                                 key=f"{conv.module_id}/{conv.content_id}",
                                 data={"extension": conv.extension,
                                       "memory": conv.memory})
        if pre is not None and pre.handled and (v.output_text is not None
                                                or v.execution_actions
                                                or v.subscriptions):
            self._save_persist_data(turn.robot.device_id, v.persist_data,
                                    json.dumps({}, sort_keys=True))
            return self._reply_from_volley(v)
        # An empty `prompt` starts the conversation: its opener, not the model (OpenMoxie
        # conversations.py handle_volley). A conversation with no opener still asks the model.
        if turn.command == "prompt" and not (turn.speech or "").strip():
            opener = self._opener_reply(turn.robot, conv, v, turn.presence)
            if opener is not None:
                # A `turn.before` extension's act/subscribe go out with it, as with a model line.
                opener.actions += execution_actions_of(v)
                opener.subscribe = subscriptions_of(v)
                return opener
        # `presence` (read-only, vision.md) is available to the prompt template.
        system = render_prompt(conv.prompt, {"volley": v, "session": session,
                                             "presence": (turn.presence
                                                          or _presence_vars(turn.robot))})
        note_used(self.memory, turn.robot.device_id, system)   # decay's clock (memory.py)
        if self._persona:
            system = f"{self._persona}\n\n{system}" if system else self._persona
        # The leave-taking tags go last, after the module's own prompt (actions.py).
        system = f"{system}\n\n{LEAVE_TAG_PROMPT}" if system else LEAVE_TAG_PROMPT
        messages = [{"role": "system", "content": system}]
        messages += turn.history[-conv.max_history:]
        messages.append({"role": "user", "content": turn.speech})
        try:
            text = (self._chat(messages) or "").strip()
        except Exception as e:
            # ai-seam.md §2: offline → robot local fallback; rate-limited → "one moment";
            # anything else → keep the child engaged.
            from ..chat import is_offline_error, is_rate_limit_error
            if is_offline_error(e):
                return Reply.offline()
            if is_rate_limit_error(e):
                return Reply(text="Give me one tiny second to think... okay, what were you saying?")
            return Reply(text="Hmm, my brain got fuzzy — say that again?")
        # Lift action tags out of the model's line (actions.py); speak the remainder.
        text, actions = parse_action_tags(text)
        # A `turn.before` extension's act/subscribe still go out when the model answers.
        actions += execution_actions_of(v)
        subscribe = subscriptions_of(v)
        if not text and not actions:
            return Reply(text="Tell me more!", subscribe=subscribe)
        return Reply(text=text, actions=actions, subscribe=subscribe)

    # ---- being woken by the robot's own eyes (vision.md §7.1, brief §8 G6) ----
    def perceive(self, turn: Turn) -> Optional[Reply]:
        """A subscribed robot event (`turn.speech` is its name), offered to the pack's
        local evaluator only — never the model, so a face walking into frame can never
        become a model call (vision.md §7.1).

        Only `on: turn.before` (§8 G6), never `global`: an event is not something a child
        said. Returns a Reply only when a rule produced a line, `act` or `subscribe`;
        `handled` is not required (there is no model to suppress). Memory-only effects
        apply and return None.
        """
        conv = self._active_conversation(turn)
        if conv is None or not getattr(conv, "extension", None):
            return None
        v = self._volley(turn)
        before = json.dumps(v.persist_data, sort_keys=True, default=str)
        session = self._session(turn, history=list(turn.history),
                                persist_data=v.persist_data, conv=conv)
        result = self.run_extension(turn, v, session, hook="turn.before",
                                    kind="conversation",
                                    key=f"{conv.module_id}/{conv.content_id}",
                                    data={"extension": conv.extension,
                                          "memory": conv.memory})
        if result is None:
            return None                       # no rule matched, or it breached (§6.4)
        if v.output_text is None and not v.execution_actions and not v.subscriptions:
            return None                       # it acted on memory only; say nothing
        self._save_persist_data(turn.robot.device_id, v.persist_data, before)
        return self._reply_from_volley(v)

    # ---- end of conversation: write what is worth remembering ----
    def _memory_conversation(self, robot: RobotContext):
        """The conversation whose memory namespace a finished session belongs to."""
        return self._active_conversation(Turn(robot=robot, speech=""))

    def on_session_end(self, robot: RobotContext, history: list,
                       reason: str = "") -> None:
        """The contract's `complete_handler` moment: summarize the finished conversation
        into the module's `memory` namespace, with provenance.

        Writes nothing when memory is off, no namespace is declared, the chat was too short
        or already summarized, the policy is `NO_DATA`, or the brain failed — failure is
        always "remember nothing"."""
        conv = self._memory_conversation(robot)
        device_id = getattr(robot, "device_id", "")
        if self.memory is None or conv is None or not conv.summarizes or not device_id:
            return
        ns = conv.memory_namespace
        cfg = conv.memory or {}
        history = list(history or [])
        # Only the not-yet-summarized tail (never re-pay for the same transcript).
        block = self.memory.load(device_id).get(ns) or {}
        done = int(((block.get("_meta") or {}) if isinstance(block, dict) else {})
                   .get("summarized_through", 0) or 0)
        fresh = history[done:] if 0 < done <= len(history) else history
        volleys = sum(1 for m in fresh if isinstance(m, dict) and m.get("role") == "user")
        if volleys < int(cfg.get("min_volleys", 2) or 0):
            return
        session = Session(history=fresh, persist_data=self.persist_data(device_id),
                          max_volleys=conv.max_volleys, chat=self._chat,
                          module_id=conv.module_id, content_id=conv.content_id)
        summary = session.summarize(prompt_base=cfg.get("prompt") or None,
                                    classifier=self.classifier,
                                    max_items=int(cfg.get("max_items", 5) or 5))
        if not summary:
            return
        values = {k: v for k, v in summary.items() if k != "summary"}
        if summary.get("summary"):
            values["summaries"] = [summary["summary"]]
        wrote = self.memory.merge(
            device_id, ns, values,
            provenance=provenance(module_id=conv.module_id, content_id=conv.content_id,
                                  turns=volleys, reason=reason or "end"),
            meta={"summarized_through": len(history)})
        if wrote is None:
            print(f"[content] memory: {device_id} is NO_DATA — nothing remembered",
                  flush=True)
        else:
            print(f"[content] 🧠 remembered {len(summary.get('facts', []))} fact(s) "
                  f"for {device_id} in '{ns}' ({reason or 'end'})", flush=True)
