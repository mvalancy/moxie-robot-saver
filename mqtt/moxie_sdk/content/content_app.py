"""
ContentApp — runs a content module through the AI seam (docs/architecture/
content-module-contract.md). This is where the pure engine (module/volley/render)
becomes a live MoxieApp: each turn it checks `globals[]` first (always-on commands),
otherwise runs the active `conversations[]` module — render its Jinja prompt over the
volley, hand it to the brain, return a Reply.

The brain is injected as a `chat(messages) -> str` callable (the AI-seam boundary),
so ContentApp is testable without a real LLM and works with any OpenAI-compatible
endpoint. Global handlers are registered Python callables keyed by the global's name
(arbitrary `code`-string execution from module JSON is deliberately NOT done here —
a sandboxing concern deferred; built-in/registered handlers cover the safe cases).

**Memory.** The contract's `volley.persist_data` is loaded here per turn from the
durable `MemoryStore` (one `memory.json` per robot) and rendered into the module's
prompt; `volley.local_data` stays per-exchange scratch. When a conversation *ends*
(`on_session_end` — the runtime calls it on `<exit>`, a module switch, or a
disconnect), a module that declares a `memory` block gets what OpenMoxie's MemoryChat
does from its `complete_handler`: `session.summarize()` → merge into `persist_data`
under the module's namespace, with provenance. Since we do not execute module `code`,
that behaviour is *declared* rather than scripted:

    "memory": {"namespace": "memory_chat", "summarize": true, "min_volleys": 2}
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
        # 📦 The SHIPPED baseline (`packs.shipped_items` of `MOXIE_CONTENT_MODULE`), kept
        # separately from `module` because `module` is *defaults ⊕ the imported overlay*.
        # `MoxieRuntime.reload_content()` needs the two apart: without it an `undo` could
        # not put a shipped item back after a pack replaced it. None ⇒ nobody recorded one
        # (see `MoxieRuntime._content_defaults` for what happens then).
        self.content_defaults = content_defaults
        self._chat = chat
        self._persona = persona
        self._default_module_id = default_module_id
        self._handlers: dict = dict(global_handlers or {})
        # Long-term memory (`volley.persist_data`). Built by default so the shipped
        # config path gets it without wiring; pass one in to point it at a tmp dir, or
        # `memory=False` to run with no durable memory at all.
        self.memory = (MemoryStore() if memory is None
                       else (memory or None))
        # Only used when a summary is written; the classifier decides what may never be
        # remembered. Resolved lazily so a missing rules file is not an import error.
        self._classifier = safety_classifier
        self._classifier_resolved = safety_classifier is not None
        # 🧬 Sandboxed extensions (BEYOND #6). `ext_grants` is the set of capabilities
        # this appliance will honour. It defaults to `{say, handled, session,
        # child.nickname}` and there is deliberately **no env var and no console control**
        # for it at P0: widening it is a code change, which is a reviewer. The
        # parent-facing grant flow is P1.
        self._ext_grants = (frozenset(ext.DEFAULT_GRANTS) if ext_grants is None
                            else frozenset(ext_grants))
        # 📦 Shipped-by-us extensions get a wider set, and the trust is anchored to the
        # **bytes of the program**, not to its name. `content_defaults` is the shipped
        # baseline (`packs.shipped_items` of `MOXIE_CONTENT_MODULE`), so an imported pack
        # that overrides `global:What Time Is It` does NOT inherit its grants — its AST
        # digest is different, and a different program is a different decision. A pack
        # that copies one of ours byte for byte does get them, which is correct: it is our
        # program, unchanged, and `explain()` renders it identically.
        self._ext_shipped_grants = (self._ext_grants | SHIPPED_EXTRA_GRANTS
                                    if ext_grants is None else self._ext_grants)
        self._ext_shipped = shipped_ext_digests(content_defaults)
        self._ext_limits = ext_limits
        # Clock and entropy are injected into the evaluator, never imported by it (X7).
        self._clock = clock or time.time
        self._monotonic = monotonic or time.monotonic
        #: `{(device_id, extension_id): breaches}` for this session. A broken extension
        #: may cost the child one turn's latency; it may not cost every turn's (§6.4).
        self._ext_breaches: dict = {}
        #: `{(device_id, extension_id, reason)}` already reported, so the parent gets one
        #: `ext_events` entry per problem per session rather than one per turn.
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
        # Handler output goes through the same tag parse as model output, so a module
        # can end a session by writing "<exit>" into set_output (moxie_sdk/actions.py).
        text, actions = parse_action_tags(v.output_text or "")
        # …and `volley.execution_actions` — what a global handler or a sandboxed
        # extension asked the robot to *run* — becomes `execute` RemoteChatActions.
        # Brief S5's gap, closed 2026-09-04; see `execution_actions_of`.
        actions += execution_actions_of(v)
        markup = parse_action_tags(v.output_markup)[0] if v.output_markup else None
        # A module may author its own markup — that is honoured as written. But a handler
        # that only set `output_markup` to a plain line would bypass the runtime's markup
        # seam (which fires on `markup is None`) and speak flat, so the markup floor runs
        # here for that one path. `annotate` returns anything already carrying a `<mark`
        # or `<usel` unchanged, so authored markup is never touched.
        if markup and _automarkup_enabled():
            markup = annotate(markup)
        return Reply(text=text, markup=markup, actions=actions)


    # ---- sandboxed extensions (BEYOND #6) ----
    def _ext_limits_now(self):
        """The budget, read from `config.py` when it is importable (the supervisor) and
        from `ext.py`'s own defaults when it is not (a bare SDK install)."""
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
        """Record one breach: quarantine after `MOXIE_EXT_MAX_BREACHES`, and tell the
        **parent** once — never the child (§6.4).

        The child hears nothing at all: the turn proceeds exactly as it does with no
        extension, so an `on: global` failure falls through to the conversation (S1) and an
        `on: turn.before` failure lets the model run. No `f"Script error: {e}"` — that is
        upstream's one bad output surface (U6), and the whole reason this design exists is
        that a broken pack should be boring.
        """
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
                # The plain-language half, so the console can say "the Bedtime pack's
                # timer stopped working, and Moxie carried on without it" without having
                # to know what a step budget is.
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
        """Run one item's extension for this turn, or return None.

        None means "nothing happened, carry on exactly as before" and is the answer for
        every failure as well as for no-extension-here, no-rule-matched and quarantined —
        which is what makes §6.4 true by construction rather than by care.
        """
        block = (data or {}).get("extension") or {}
        if not block or block.get("on") != hook:
            return None
        ext_id = full_key_of(kind, key)
        grants = (self._ext_shipped_grants
                  if _ext_digest(block) in self._ext_shipped else self._ext_grants)
        device_id = getattr(turn.robot, "device_id", "") or ""
        if self._ext_quarantined(device_id, ext_id):
            return None                       # already broken three times this session
        # Validation runs HERE, every turn, not only at import: an extension written
        # straight into the store, or one that would fail under a newer validator, simply
        # does not run (T17).
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
                    # 🧬 The socket S1 describes, filled by a pack instead of by us. A
                    # registered Python handler still wins — it is our own code, and a
                    # shipped default should not be displaced by an import.
                    self.run_extension(turn, v, session, hook="global",
                                       kind="global", key=g.name,
                                       data={"extension": g.extension,
                                             "name": g.name})
                # A global (OpenMoxie's timer is the canonical one) may write durable
                # state; that is what `persist_data` is for.
                self._save_persist_data(turn.robot.device_id, v.persist_data, before)
                if v.output_text is not None or v.execution_actions:
                    return self._reply_from_volley(v)
            # matched but nothing produced output → fall through to conversation

        # 2) the active conversation module
        conv = self._active_conversation(turn)
        if conv is None:
            return Reply(text="Let's chat! What's on your mind?")
        v = self._volley(turn)
        session = self._session(turn, history=list(turn.history),
                                persist_data=v.persist_data, conv=conv)
        # 🧬 `on: turn.before` — upstream's `pre_process`. It runs before the prompt is
        # rendered and may set `handled`, which suppresses the model call for this turn
        # (the True/False return of upstream's hook). A failure here is skipped and the
        # model runs, so the child is never left with silence.
        pre = self.run_extension(turn, v, session, hook="turn.before",
                                 kind="conversation",
                                 key=f"{conv.module_id}/{conv.content_id}",
                                 data={"extension": conv.extension,
                                       "memory": conv.memory})
        if pre is not None and pre.handled and (v.output_text is not None
                                                or v.execution_actions):
            # `or v.execution_actions` mirrors the globals path above: a rule that answers
            # the turn by *doing* something — arming the QR scanner, cancelling a timer —
            # has handled it just as much as one that spoke, and dropping the action here
            # would be the S5 gap reopening one branch lower down.
            self._save_persist_data(turn.robot.device_id, v.persist_data,
                                    json.dumps({}, sort_keys=True))
            return self._reply_from_volley(v)
        # `presence` — read-only: what Moxie's own eyes have told the server
        # (moxie_sdk/presence.py, docs/architecture/vision.md). A module template can say
        # `{% if presence.face_present %}` or drop `{{ presence.line }}` into its prompt.
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
            # Graceful degradation (ai-seam.md §2): unreachable → ERROR_OFFLINE (robot
            # local-fallback); still rate-limited after backoff → a gentle "one moment"
            # so the child isn't dropped; other soft error → keep them engaged.
            from ..chat import is_offline_error, is_rate_limit_error
            if is_offline_error(e):
                return Reply.offline()
            if is_rate_limit_error(e):
                return Reply(text="Give me one tiny second to think... okay, what were you saying?")
            return Reply(text="Hmm, my brain got fuzzy — say that again?")
        # The model may drive the robot from inside its own line (see actions.py):
        # lift the tags out as actions, speak only the remainder.
        text, actions = parse_action_tags(text)
        # A `turn.before` extension that acted but did **not** handle the turn still gets
        # its action out: the model answers the child, and the robot does the thing the
        # pack asked for. Losing it here would mean "act" only worked when a pack also
        # took the whole turn.
        actions += execution_actions_of(v)
        if not text and not actions:
            return Reply(text="Tell me more!")
        return Reply(text=text, actions=actions)

    # ---- end of conversation: write what is worth remembering ----
    def _memory_conversation(self, robot: RobotContext):
        """The conversation whose memory namespace a finished session belongs to."""
        return self._active_conversation(Turn(robot=robot, speech=""))

    def on_session_end(self, robot: RobotContext, history: list,
                       reason: str = "") -> None:
        """The contract's `complete_handler` moment: summarize the finished conversation
        into `persist_data` under this module's namespace, with provenance.

        Declared, not scripted — a conversation opts in with a `memory` block
        (`{"namespace": …, "summarize": true, "min_volleys": 2}`); OpenMoxie's MemoryChat
        expresses the same thing as a `complete_handler` Python string, which we do not
        execute (sandboxing, see this module's docstring).

        Nothing is written when: memory is off, the module declares no namespace, the
        chat was too short to be worth remembering, everything new was already
        summarized, the privacy policy is `NO_DATA`, or the brain failed. Failure is
        always "remember nothing" — never a half-written or invented memory."""
        conv = self._memory_conversation(robot)
        device_id = getattr(robot, "device_id", "")
        if self.memory is None or conv is None or not conv.summarizes or not device_id:
            return
        ns = conv.memory_namespace
        cfg = conv.memory or {}
        history = list(history or [])
        # Only the part we have not summarized yet (a module switch back and forth must
        # not re-summarize — and re-pay for — the same transcript).
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
# Sandboxed content extensions — the host half (BEYOND #6 P0)
#
# `moxie_sdk/content/ext.py` is the evaluator: pure, total, and blind to this process.
# Everything that touches the world lives here, and the split is the security argument
# (docs/architecture/backlog/sandboxed-extensions.md §4.4/§4.5):
#
#   * `ext_facts()` builds a **plain-JSON** dict from primitives. The evaluator never sees
#     a `Volley`, a `Session`, a `MemoryStore` or any other live object, so there is no
#     object for an attribute walk to reach (X2).
#   * `apply_ext_effects()` applies the returned effect list **after** the program ended.
#     A breach mid-program therefore leaves nothing half-applied — the list is discarded
#     whole by `ext.evaluate` and never gets here (X11).
#   * Every breach is boring: the extension fails, the turn does not. A `global` falls
#     through to the conversation exactly as a matched global with no handler does today
#     (S1); a `turn.before` is skipped and the model runs. The child hears no error text.
# --------------------------------------------------------------------------- #

#: Inbound caps. `speech`, `entities` and `input_vars` are robot-supplied — untrusted data
#: — so they are bounded before the evaluator's own byte caps ever see them.
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
    """A plain-JSON copy: `str/int/float/bool/None/list/dict` and nothing else.

    Rebuilds every container, so a `FactList` (a `list` subclass `memory.wrap_facts`
    returns) comes back a plain list, and anything that is not JSON at all comes back
    `None`. This is the function X2 is really testing: the fact base cannot contain a host
    object because this is the only thing that puts values into it.
    """
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
    """The §4.4 fact base — built by the host from primitives, never by exposing objects.

    `namespace` is supplied **here**, by us, from the item's identity. An extension cannot
    name a namespace, a device, a collection or a path: the words for those do not exist in
    the grammar, which is what makes "its own memory and nobody else's" structural rather
    than enforced (X9).
    """
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
    """`(clean, dropped)` — behaviour markup through the frozen `vocab.py` catalogue (M3).

    An unknown mark id, a malformed `cmd:` payload or an out-of-catalogue asset is
    **dropped**, tag by tag, and counted; the surrounding text survives. Never passed
    through unchecked, because `markup` is the one capability that reaches the robot's
    *body* — validation is what makes a bad id harmless, and the parent's explicit grant is
    what covers a valid id in poor taste (risk R4).
    """
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
    """Apply one extension's effect list, in order, subject to every cap in §6.3.

    Returns `{"spoke", "wrote", "dropped_markup", "blocked", "acted"}` for the caller and
    the log.

    Two things are worth reading twice. **`say` goes through the same output-side safety
    classifier and the same `annotate` floor a model's line does** — an extension does not
    get a private channel to a child, and a blocked verdict is replaced by a
    `redirect_for()` line rather than refused (M2). And **`remember`/`forget` name only a
    key**: the `(device_id, namespace)` pair is supplied here, by us, so the write cannot
    reach another module's namespace or another child's robot (X9).
    """
    spoke = wrote = dropped = acted = 0
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
            # The pack asked the robot to run something. The name was already checked
            # against `ext.ACTION_WORDS` at load *and* charged an `act.<name>` grant — this
            # is the second, host-side check on the same closed table, because the value
            # that ends up as a `function_id` on a wire to a robot in a child's room should
            # be bounded by the code that puts it there and not only by the code that let
            # it in (qr-launch-cards.md §P0-b).
            name = str(eff.get("name") or "")
            if name not in ext.ACTION_WORDS:      # pragma: no cover - load already refused
                print(f"[ext] {name!r} is not an action this appliance knows; "
                      f"ignored", flush=True)
                continue
            volley.add_execution_action(name, [str(a) for a in (eff.get("args") or [])])
            acted += 1
        elif kind in ("subscribe", "brain"):
            # Still unreachable — both capabilities are refused at load (`ext
            # .P1_CAPABILITIES` says which and why). Kept as an explicit refusal rather
            # than a silent drop so the day each one lands, the gap is a line to fill in
            # and not a bug to find.
            print(f"[ext] {kind} is not plumbed yet; ignored", flush=True)
    return {"spoke": spoke, "wrote": wrote, "dropped_markup": dropped, "blocked": blocked,
            "acted": acted}


def robot_functions() -> frozenset:
    """The robot-side functions this appliance will ever name on the wire — **the keys of
    `ext.ACTION_WORDS`, and nothing else**.

    One table, so a function is nameable exactly when somebody has written the sentence a
    parent reads before granting it: a name with no words cannot be declared, cannot be
    granted, and cannot be emitted. Two tables could drift; this one cannot.

    The bound is the safety property, not the tidiness one.
    `docs/architecture/backlog/qr-launch-cards.md` §P0-b makes the argument for the
    launch-card catalogue — *"a QR code is an input any stranger can print and leave on a
    table in front of a child"* — and an authored content pack is the same kind of input by
    a longer route. Widening this set is a code change in a file a reviewer reads, which is
    the brake risk R1 asks for.
    """
    return frozenset(ext.ACTION_WORDS)


def execution_actions_of(volley: Volley) -> list:
    """`volley.execution_actions` → `Action`s the wire can carry. Brief S5's other half.

    Every one goes out as **`execute` + `function_id`**, which is what the recovered
    contract actually defines: `RemoteChatAction.ActionID.execute` (= 6) with `function_id`
    (field 7) and `repeated function_args` (field 8) — RemoteChat.proto:255-281, read back
    by `remote-chat-protocol.md`:99 as *"`execute` — run a robot-side
    `function_id(function_args…)`"*. `wire.encode_action` spells it, and because `args` is
    a **list** it lands in `function_args` rather than `action_args` (the type-decided
    mapping #119 introduced; a dict would be the other one). So `eb_enable_qr` reaches a
    robot as `qr-launch-cards.md` §P0-a's worked example spells it:

        {"action": "execute", "function_id": "eb_enable_qr", "function_args": ["true"]}

    …and *not* as `ActionType.ENABLE_QR`, whose `"enable_qr"` is not an `ActionID` verb at
    all. That enum member is a known naming defect, filed in §P0-a and pinned by
    `test_actions_reach_the_robot.py`; this function simply does not use it.

    **The name is checked against the closed set here too.** `ext.validate` already refuses
    an unknown action at load and charges an `act.<name>` grant for a known one, so this is
    the second gate on the same table — deliberately, because this is the last function
    before a string becomes a `function_id` addressed to a robot, and a Python global
    handler calling `add_execution_action` never passed through the validator at all.
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


def ext_namespace(kind: str, key: str, data: dict) -> str:
    """The memory namespace an extension owns — chosen by the host, never by the pack.

    A conversation uses its declared `memory.namespace`; a global gets `ext:<kind:key>`.
    Keyed on the pack's own `kind:key` identity rather than on `name`, because two globals
    called "Timer" in two different packs must not share a namespace (brief A13).
    """
    if kind == "conversation":
        ns = str(((data or {}).get("memory") or {}).get("namespace") or "")
        if ns:
            return ns
    slug = re.sub(r"[^a-z0-9]+", "_", f"{kind}:{key}".lower()).strip("_")
    return f"ext:{slug or 'unnamed'}"


#: The bounded per-robot ring the console reads to say *"the Bedtime pack's timer stopped
#: working, and Moxie carried on without it"*. Same shape as `safety_events` (M4) — one
#: file per robot under `robots/<safe_name(device_id)>/`, newest-capped, never shared.
EXT_EVENTS_COLLECTION = "ext_events"
EXT_EVENTS_CAP = 50


#: What a **shipped-by-us** extension may be granted on top of `ext.DEFAULT_GRANTS`.
#:
#: Not `subscribe`/`brain` (still refused at load whoever asks — see `ext.P1_CAPABILITIES`)
#: and not `child.profile` (a birthday and free-text notes are the highest-value PII on the
#: appliance, and no shipped activity needs them).
#:
#: And **not `act.<name>` either, though it is now grantable.** Since 2026-09-04 an `act`
#: reaches the robot, so the old reason ("it is P1") has expired — but "the appliance can
#: honour it" is not "every program we ship may do it". Nothing we ship needs one yet, and
#: the day one does, adding that single `act.<name>` is a code change in a file a reviewer
#: reads — which is exactly the brake acceptance criterion 5 asks for.
SHIPPED_EXTRA_GRANTS = frozenset({"clock", "random", "memory.read", "memory.write",
                                  "presence", "markup"})


def _ext_digest(block: dict) -> str:
    """`sha256:…` over an extension's canonical bytes — the same canonicalisation a pack
    digest uses, so "is this the program we shipped?" has one answer everywhere."""
    from .packs import digest_of
    return digest_of(block or {})


def shipped_ext_digests(content_defaults) -> frozenset:
    """The digests of every extension in the shipped baseline.

    Empty when nobody recorded a baseline (a bare SDK install, or a test that did not pass
    one), which fails **closed**: no extension is trusted, so everything gets the four
    default grants and a clock-using activity simply does not run.
    """
    out = set()
    for entry in (content_defaults or {}).values():
        data = (entry or {}).get("data") if isinstance(entry, dict) else None
        block = (data or {}).get("extension") if isinstance(data, dict) else None
        if block:
            out.add(_ext_digest(block))
    return frozenset(out)


def full_key_of(kind: str, key: str) -> str:
    """`kind:key` — the same identity packs use, so an `ext_events` row names the item a
    parent can actually find in the console."""
    return f"{kind}:{key}"


def _clock_local(now: float) -> dict:
    """`clock.local` — the injected local-time map (§4.2).

    Computed **here**, in the host, and handed to the evaluator as four plain values. That
    is the whole reason `ext.py` can assert it imports no `time` and no `datetime`: the
    only clock in the system is this line.
    """
    t = time.localtime(now)
    return {"hour": t.tm_hour, "minute": t.tm_min, "weekday": (t.tm_wday + 1) % 7,
            "iso": time.strftime("%Y-%m-%dT%H:%M:%S", t)}
