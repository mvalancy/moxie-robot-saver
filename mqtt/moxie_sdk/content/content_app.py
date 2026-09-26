"""
ContentApp — runs a content module through the AI seam
(docs/architecture/content-module-contract.md). Each turn: `globals[]` first (always-on
commands), else the active conversation — render its prompt over the volley, ask the
injected `chat(messages) -> str` brain, return a Reply.

Global handlers are registered Python callables or sandboxed extensions (`ext/`); a
module's `code` string is never executed.

**Memory.** `volley.persist_data` is loaded per turn from the durable `MemoryStore` and
rendered into the prompt. When a conversation ends (`on_session_end`: `<exit>`, module
switch or disconnect), a module with a declared `memory` block is summarized into its
namespace with provenance — OpenMoxie's MemoryChat `complete_handler`, declared rather
than scripted:

    "memory": {"namespace": "memory_chat", "summarize": true, "min_volleys": 2}

The second half of this file is the extension *host*: the only code that builds the
evaluator's fact base and applies its effects (sandboxed-extensions.md §4.4/§4.5).
"""
from __future__ import annotations
import hashlib
import json
import re
import time
from typing import Callable, Optional

from ..app import MoxieApp
from ..actions import parse_action_tags
from ..automarkup import annotate, enabled as _automarkup_enabled
from .. import automarkup as _automarkup
from .. import safety as _safety
from .. import vocab
from ..store import MemoryStore
from ..types import Turn, Reply, RobotContext, Action, ActionType
from .module import ContentModule
from .volley import Volley, Session
from .memory import default_classifier, note_used, provenance, wrap_facts
from .render import render_prompt
from . import ext
from .. import presence as _presence


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
                 ext_grants=None, ext_limits=None, clock=None, monotonic=None):
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

    # ---- MoxieApp ----
    def greeting(self, robot: RobotContext) -> Optional[Reply]:
        conv = self._active_conversation(Turn(robot=robot, speech=""))
        if conv and conv.opener:
            v = self._volley(Turn(robot=robot, speech=""))
            line = render_prompt(conv.opener.split("|")[0],
                                 {"volley": v, "session": Session(),
                                  "presence": _presence_vars(robot)})
            line = line.replace("<opener>", "").strip()   # strip inline tags
            if line:
                return Reply(text=line)
        return None

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
        # `presence` (read-only, vision.md) is available to the prompt template.
        system = render_prompt(conv.prompt, {"volley": v, "session": session,
                                             "presence": (turn.presence
                                                          or _presence_vars(turn.robot))})
        note_used(self.memory, turn.robot.device_id, system)   # decay's clock (memory.py)
        if self._persona:
            system = f"{self._persona}\n\n{system}" if system else self._persona
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


# --------------------------------------------------------------------------- #
# Sandboxed content extensions — the host half (sandboxed-extensions.md §4.4/§4.5)
#
# `ext/` is the pure evaluator; everything that touches the world lives here:
#   * `ext_facts()` builds a plain-JSON fact base — no live object to walk (X2).
#   * `apply_ext_effects()` applies effects only after the program ended, so a breach
#     leaves nothing half-applied (X11).
# --------------------------------------------------------------------------- #

#: Inbound caps on robot-supplied (untrusted) values, applied before the evaluator's own.
EXT_MAX_SPEECH = 2000
EXT_MAX_ENTITIES = 16
EXT_MAX_ENTITY_CHARS = 256
EXT_MAX_INPUT_VARS = 32
EXT_MAX_INPUT_VAR_CHARS = 512
EXT_MAX_MEMORY_BYTES = 32768

#: One `<mark …/>`, `<usel …>` or `<break …/>` tag, for the catalogue gate below.
_EXT_TAG = re.compile(r"<(?:mark|usel|/usel|spurt|break)\b[^>]*/?>", re.I)
_EXT_VAR_KEY = re.compile(r"^[A-Za-z_$][A-Za-z0-9_.$-]{0,63}$")


def _ext_json(value, depth: int = 0):
    """A plain-JSON copy (containers rebuilt, subclasses flattened, non-JSON → None) —
    the only path into the fact base, which is what X2 tests."""
    if depth > 12:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return None if isinstance(value, float) and value != value else value
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if not isinstance(k, str) or k.startswith("_"):
                continue                      # `_meta`/`_provenance` are the store's
            out[k] = _ext_json(v, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [_ext_json(v, depth + 1) for v in list(value)[:256]]
    return None


def ext_facts(volley: Volley, session: Session, *, namespace: str = "",
              grants=(), presence: Optional[dict] = None) -> dict:
    """The §4.4 fact base, built from primitives. `namespace` is chosen by the host; the
    grammar has no words for a namespace, device or path, so isolation is structural (X9)."""
    grants = set(grants or ())
    ents = [str(e)[:EXT_MAX_ENTITY_CHARS]
            for e in list(getattr(volley, "entities", None) or [])[:EXT_MAX_ENTITIES]]
    input_vars = {}
    for k, v in (getattr(volley, "request", None) or {}).get("input_vars", {}).items():
        if len(input_vars) >= EXT_MAX_INPUT_VARS:
            break
        if isinstance(k, str) and _EXT_VAR_KEY.match(k):
            input_vars[k.lstrip("$")] = str(v)[:EXT_MAX_INPUT_VAR_CHARS]
    pii = (getattr(volley, "config", None) or {}).get("child_pii") or {}
    child = {}
    if "child.nickname" in grants:
        child["nickname"] = str(pii.get("nickname") or "")
    if "child.profile" in grants:
        child.update(pronouns=str(pii.get("pronouns") or ""),
                     birthday=str(pii.get("birthday") or ""),
                     notes=str(pii.get("notes") or ""))
    memory = {}
    if "memory.read" in grants and namespace:
        block = (getattr(volley, "persist_data", None) or {}).get(namespace)
        memory = _ext_json(block) if isinstance(block, dict) else {}
        if len(json.dumps(memory, default=str)) > EXT_MAX_MEMORY_BYTES:
            memory = {}                      # too big to hand over is "nothing to read"
    facts = {
        "speech": str(getattr(volley, "speech", "") or "")[:EXT_MAX_SPEECH],
        "entities": ents,
        "input_vars": input_vars,
        "child": child,
        "memory": memory,
        "scratch": {},                        # per-turn, starts empty (§4.4)
        "session": {"total_volleys": int(getattr(session, "total_volleys", 0) or 0),
                    "is_empty": bool(session.is_empty()) if session else True,
                    "overflow": bool(getattr(session, "overflow", False))},
        "presence": {},
    }
    if "presence" in grants:
        p = presence or {}
        facts["presence"] = {"face_present": bool(p.get("face_present")),
                             "line": str(p.get("line") or "")}
    return facts


def ext_markup(markup: str) -> tuple:
    """`(clean, dropped)` — markup filtered tag by tag through the frozen `vocab.py`
    catalogue (M3); invalid tags are dropped and counted, text survives. `markup` reaches
    the robot's body, so it is never passed through unchecked (R4)."""
    if not markup:
        return "", 0
    dropped = 0
    out = []
    pos = 0
    for m in _EXT_TAG.finditer(markup):
        out.append(markup[pos:m.start()])
        pos = m.end()
        tag = m.group(0)
        if vocab.validate_markup(tag):
            dropped += 1
            _automarkup._drop("ext")          # the existing `dropped_ids()` counter
        else:
            out.append(tag)
    out.append(markup[pos:])
    return "".join(out), dropped


def _ext_set_path(block: dict, key: str, value):
    """Write a dotted key into a namespace block, creating maps as it goes."""
    parts = key.split(".")
    cur = block
    for seg in parts[:-1]:
        nxt = cur.get(seg)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[seg] = nxt
        cur = nxt
    cur[parts[-1]] = value
    return parts[0]


def _ext_del_path(block: dict, key: str) -> bool:
    parts = key.split(".")
    cur = block
    for seg in parts[:-1]:
        cur = cur.get(seg)
        if not isinstance(cur, dict):
            return False
    return cur.pop(parts[-1], _MISSING) is not _MISSING


_MISSING = object()


def apply_ext_effects(effects, *, volley: Volley, memory=None, device_id: str = "",
                      namespace: str = "", classifier=None, module_id: str = "",
                      content_id: str = "") -> dict:
    """Apply one extension's effects in order under the §6.3 caps; returns counts
    `{"spoke", "wrote", "dropped_markup", "blocked", "acted", "subscribed"}`.

    `say` passes the same output safety classifier as a model line (unsafe → redirect,
    M2). `remember`/`forget` name only a key; device and namespace come from the host (X9).
    """
    spoke = wrote = dropped = acted = subscribed = 0
    blocked = False
    for eff in effects or []:
        kind = eff.get("kind")
        if kind == "say":
            text = str(eff.get("text") or "")[:ext.MAX_SAY_CHARS]
            markup = eff.get("markup")
            if classifier is not None and text:
                try:
                    verdict = classifier.assess(text, role=_safety.MOXIE)
                except Exception:
                    verdict = None            # a broken classifier must not silence Moxie
                if verdict is not None and verdict.is_unsafe:
                    blocked = True
                    text = _safety.redirect_for(verdict, classifier=classifier).line
                    markup = None
            if markup:
                markup, n = ext_markup(str(markup)[:ext.MAX_MARKUP_CHARS])
                dropped += n
            volley.set_output(text, markup or None)
            spoke += 1
        elif kind == "markup":
            clean, n = ext_markup(str(eff.get("markup") or "")[:ext.MAX_MARKUP_CHARS])
            dropped += n
            volley.set_output(volley.output_text or "", clean or None)
        elif kind == "scratch":
            volley.local_data[str(eff["key"])] = eff.get("value")
        elif kind in ("remember", "forget"):
            if memory is None or not device_id or not namespace:
                continue
            try:
                data = memory.load(device_id)
                block = data.get(namespace)
                block = dict(block) if isinstance(block, dict) else {}
                if kind == "remember":
                    top = _ext_set_path(block, str(eff["key"]), eff.get("value"))
                    got = memory.merge(device_id, namespace, {top: block[top]},
                                       provenance=provenance(module_id=module_id,
                                                             content_id=content_id,
                                                             turns=1, reason="extension"))
                    wrote += 1 if got is not None else 0
                else:
                    if _ext_del_path(block, str(eff["key"])):
                        data[namespace] = block
                        wrote += 1 if memory.save(device_id, data) else 0
            except Exception as e:            # a broken memory file must not end a turn
                print(f"[ext] memory write failed ({e}); continuing", flush=True)
        elif kind == "act":
            # Second, host-side check on the closed `ACTION_WORDS` table (the load-time
            # check already ran) — bounded by the code that emits it (qr-launch-cards §P0-b).
            name = str(eff.get("name") or "")
            if name not in ext.ACTION_WORDS:      # pragma: no cover - load already refused
                print(f"[ext] {name!r} is not an action this appliance knows; "
                      f"ignored", flush=True)
                continue
            volley.add_execution_action(name, [str(a) for a in (eff.get("args") or [])])
            acted += 1
        elif kind == "subscribe":
            # Add, never replace: an extension may add events, never remove any. Names
            # are bounded again in `subscriptions_of`.
            events = [str(e) for e in (eff.get("events") or [])]
            volley.add_subscriptions(events)
            subscribed += len(events)
        elif kind == "brain":
            # Unreachable: `brain` is refused at load (P1). An explicit refusal, not a
            # silent drop.
            print(f"[ext] {kind} is not plumbed yet; ignored", flush=True)
    return {"spoke": spoke, "wrote": wrote, "dropped_markup": dropped, "blocked": blocked,
            "acted": acted, "subscribed": subscribed}


def robot_functions() -> frozenset:
    """The robot functions this appliance will ever name on the wire: exactly the keys of
    `ext.ACTION_WORDS` (one table; a safety bound, qr-launch-cards.md §P0-b, R1)."""
    return frozenset(ext.ACTION_WORDS)


def execution_actions_of(volley: Volley) -> list:
    """`volley.execution_actions` → `execute` `Action`s
    (`RemoteChatAction.ActionID.execute` + `function_id`/`function_args`,
    RemoteChat.proto:255-281), e.g.

        {"action": "execute", "function_id": "eb_enable_qr", "function_args": ["true"]}

    Names are re-checked against `robot_functions()` because a Python handler's
    `add_execution_action` never met the validator; unknown names are dropped loudly.
    """
    known = robot_functions()
    out = []
    for entry in getattr(volley, "execution_actions", None) or []:
        name = str((entry or {}).get("name") or "")
        if name not in known:
            print(f"[content] {name!r} is not a robot function this appliance names; "
                  f"dropped (see execution_actions_of)", flush=True)
            continue
        args = (entry or {}).get("args") or []
        if not isinstance(args, (list, tuple)):
            args = [args]
        out.append(Action(type=ActionType.EXECUTE, function=name,
                          args=[str(a) for a in args]))
    return out


def robot_events() -> frozenset:
    """The robot events this appliance will ever ask for: exactly
    `ext.SUBSCRIBE_EVENTS` (vision.md §1.1-1.2) — `robot_functions()`'s inbound twin."""
    return frozenset(ext.SUBSCRIBE_EVENTS)


def subscriptions_of(volley: Volley) -> list:
    """`volley.subscriptions` → the event names a `Reply` may carry: order kept,
    duplicates dropped, unknown names dropped loudly. The second vocabulary check, because
    a Python handler's `update_subscriptions` never met the validator."""
    known = robot_events()
    out: list = []
    for raw in getattr(volley, "subscriptions", None) or []:
        name = str(raw)
        if name not in known:
            print(f"[content] {name!r} is not a robot event this appliance names; "
                  f"dropped (see subscriptions_of)", flush=True)
            continue
        if name not in out:
            out.append(name)
    return out


def ext_namespace(kind: str, key: str, data: dict) -> str:
    """The memory namespace an extension owns, chosen by the host: a conversation's
    declared `memory.namespace`, else `ext:<kind:key>` slug (A13)."""
    if kind == "conversation":
        ns = str(((data or {}).get("memory") or {}).get("namespace") or "")
        if ns:
            return ns
    slug = re.sub(r"[^a-z0-9]+", "_", f"{kind}:{key}".lower()).strip("_")
    return f"ext:{slug or 'unnamed'}"


#: The bounded per-robot ring of extension breaches the console reads (like
#: `safety_events`, M4).
EXT_EVENTS_COLLECTION = "ext_events"
EXT_EVENTS_CAP = 50


#: What a shipped-by-us extension may be granted on top of `ext.DEFAULT_GRANTS`. Never
#: `child.profile` (highest-value PII; nothing needs it). Each `act.<name>` is added only
#: when a shipped program needs it — today just `eb_timer_request`, a recovered robot
#: function, for the shipped `Timer` global.
SHIPPED_EXTRA_GRANTS = frozenset({"clock", "random", "memory.read", "memory.write",
                                  "presence", "markup", "act.eb_timer_request"})


def _ext_digest(block: dict) -> str:
    """`sha256:…` over an extension's canonical bytes (same as a pack digest)."""
    from .packs import digest_of
    return digest_of(block or {})


def shipped_ext_digests(content_defaults) -> frozenset:
    """Digests of every extension in the shipped baseline. Empty baseline fails closed:
    nothing is trusted beyond the default grants."""
    out = set()
    for entry in (content_defaults or {}).values():
        data = (entry or {}).get("data") if isinstance(entry, dict) else None
        block = (data or {}).get("extension") if isinstance(data, dict) else None
        if block:
            out.add(_ext_digest(block))
    return frozenset(out)


def full_key_of(kind: str, key: str) -> str:
    """`kind:key` — the identity packs use, so an `ext_events` row names a findable item."""
    return f"{kind}:{key}"


def _clock_local(now: float) -> dict:
    """`clock.local` (§4.2), computed in the host so `ext/` imports no clock (X7)."""
    t = time.localtime(now)
    return {"hour": t.tm_hour, "minute": t.tm_min, "weekday": (t.tm_wday + 1) % 7,
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S", t)}
