"""
ContentApp — runs a content module through the AI seam
(docs/architecture/content-module-contract.md). Each turn: `globals[]` first (always-on
commands), else the active conversation — render its prompt over the volley, ask the
injected `chat(messages) -> str` brain, return a Reply. The brain is taught `<exit>` and
`<sleep>` only (`actions.LEAVE_TAG_PROMPT`); it is never told a module id to launch.

**Opener.** A `prompt` with no speech is the robot starting the conversation, and is
answered with the conversation's `opener` instead of the brain (OpenMoxie
`conversations.py` `handle_volley`). Any speech, `continue` and `reprompt` go to the brain.

Global handlers are registered Python callables or sandboxed extensions (`ext/`); a
module's `code` string is never executed.

**Memory.** `volley.persist_data` is loaded per turn from the durable `MemoryStore` and
rendered into the prompt. When a conversation ends (`on_session_end`: `<exit>`, module
switch or disconnect), a module with a declared `memory` block is summarized into its
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
import time
from typing import Callable, Optional

from ..app import MoxieApp
from ..actions import LEAVE_TAG_PROMPT, parse_action_tags
from ..automarkup import annotate, enabled as _automarkup_enabled
from ..memory_store import MemoryStore
from ..types import Turn, Reply, RobotContext
from .module import ContentModule
from .volley import Volley, Session
from .memory import default_classifier, note_used, provenance, wrap_facts
from .render import render_prompt
from . import ext
from .. import presence as _presence
from .ext_host import (apply_ext_effects, _clock_local, _ext_digest, EXT_EVENTS_CAP,
    EXT_EVENTS_COLLECTION, ext_facts, ext_namespace, execution_actions_of, full_key_of,
    SHIPPED_EXTRA_GRANTS, shipped_ext_digests, subscriptions_of)
from .ext_host import robot_events, robot_functions  # noqa: F401  (re-exported surface)


def _presence_vars(robot) -> dict:
    """The presence render variable for a call that has a `RobotContext` but no `Turn`
    (the opener). Same shape `Turn.presence` carries."""
    return _presence.snapshot(getattr(robot, "extra", {}).get("presence") or {})

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
        #: Already-reported `(device_id, extension_id, reason)`: one event per problem.
        self._ext_reported: set = set()
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
        seen = (device_id, ext_id, result.breach)
        if seen in self._ext_reported:
            return
        self._ext_reported.add(seen)
        print(f"[ext] {ext_id} ({hook}) stopped: {result.reason}; "
              f"Moxie carried on without it", flush=True)
        store = getattr(self.memory, "store", None)
        if store is None or not device_id:
            return
        try:
            store.append(device_id, EXT_EVENTS_COLLECTION, {
                "at": int(self._clock()), "extension": ext_id, "hook": hook,
                "reason": result.breach or "invalid",
                "sentence": result.sentence,
                "quarantined": count >= self._ext_max_breaches(),
            }, cap=EXT_EVENTS_CAP)
        except Exception as e:
            print(f"[ext] could not record the breach ({e})", flush=True)

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
        grants = (self._ext_shipped_grants
                  if _ext_digest(block) in self._ext_shipped else self._ext_grants)
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
        seed = int.from_bytes(hashlib.sha256(
            f"{device_id}|{turn.speech}|{ext_id}|{int(now)}".encode()).digest()[:4], "big")
        result = ext.evaluate(block, facts, grants=grants,
                              now_ms=int(now * 1000),
                              clock_local=_clock_local(now), seed=seed,
                              monotonic=self._monotonic,
                              limits=self._ext_limits_now())
        if not result.ok:
            self._ext_breach(device_id, ext_id, result, hook=hook)
            return None
        if not result.effects and not result.handled:
            return None                       # no rule matched: a success, not a failure
        apply_ext_effects(result.effects, volley=volley, memory=self.memory,
                          device_id=device_id, namespace=namespace,
                          classifier=self.classifier,
                          module_id=getattr(turn.robot, "module_id", "") or "",
                          content_id=getattr(turn.robot, "content_id", "") or "")
        for line in result.notes:
            print(f"[ext] {ext_id}: {line}", flush=True)
        return result

    # ---- the opener ----
    def _opener_reply(self, robot: RobotContext, conv, volley=None,
                      presence=None) -> Optional[Reply]:
        """`conv`'s opener as a Reply, or None when it has none. Never calls the brain.

        The `|`-alternatives rotate per device and never repeat back to back; a device
        hears the first alternative first. `<opener>` is stripped, and `<exit>`, `<sleep>`
        or `<launch:…>` become actions, as in a model's line."""
        if conv is None or not conv.opener:
            return None
        context = {"volley": volley or self._volley(Turn(robot=robot, speech="")),
                   "session": Session(), "presence": presence or _presence_vars(robot)}
        lines = []
        for alt in conv.opener.split("|"):
            line = render_prompt(alt, context).replace("<opener>", "").strip()
            if line and line not in lines:
                lines.append(line)
        if not lines:
            return None
        device_id = getattr(robot, "device_id", "") or ""
        last = self._last_opener.get(device_id)
        line = (lines[0] if last is None
                else self._rng.choice([x for x in lines if x != last] or lines))
        self._last_opener[device_id] = line
        text, actions = parse_action_tags(line)
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
