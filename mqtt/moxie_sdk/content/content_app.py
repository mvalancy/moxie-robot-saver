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
import json
import re
from typing import Callable, Optional

from ..app import MoxieApp
from ..actions import parse_action_tags
from ..automarkup import annotate, enabled as _automarkup_enabled
from .. import automarkup as _automarkup
from .. import safety as _safety
from .. import vocab
from ..store import MemoryStore
from ..types import Turn, Reply, RobotContext
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
                 safety_classifier=None, content_defaults=None):
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
        # M2: a global handler drives text/markup. Plumbing volley.execution_actions
        # (eb_timer_request etc.) into RemoteChatAction is a later slice.
        # Handler output goes through the same tag parse as model output, so a module
        # can end a session by writing "<exit>" into set_output (moxie_sdk/actions.py).
        text, actions = parse_action_tags(v.output_text or "")
        markup = parse_action_tags(v.output_markup)[0] if v.output_markup else None
        # A module may author its own markup — that is honoured as written. But a handler
        # that only set `output_markup` to a plain line would bypass the runtime's markup
        # seam (which fires on `markup is None`) and speak flat, so the markup floor runs
        # here for that one path. `annotate` returns anything already carrying a `<mark`
        # or `<usel` unchanged, so authored markup is never touched.
        if markup and _automarkup_enabled():
            markup = annotate(markup)
        return Reply(text=text, markup=markup, actions=actions)

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
            if handler:
                v = self._volley(turn, entities=entities)
                before = json.dumps(v.persist_data, sort_keys=True, default=str)
                session = self._session(turn, history=list(turn.history),
                                        persist_data=v.persist_data)
                handler(v, session)
                # A global (OpenMoxie's timer is the canonical one) may write durable
                # state; that is what `persist_data` is for.
                self._save_persist_data(turn.robot.device_id, v.persist_data, before)
                if v.output_text is not None or v.execution_actions:
                    return self._reply_from_volley(v)
            # matched but no handler produced output → fall through to conversation

        # 2) the active conversation module
        conv = self._active_conversation(turn)
        if conv is None:
            return Reply(text="Let's chat! What's on your mind?")
        v = self._volley(turn)
        session = self._session(turn, history=list(turn.history),
                                persist_data=v.persist_data, conv=conv)
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

    Returns `{"spoke", "wrote", "dropped_markup", "blocked"}` for the caller and the log.

    Two things are worth reading twice. **`say` goes through the same output-side safety
    classifier and the same `annotate` floor a model's line does** — an extension does not
    get a private channel to a child, and a blocked verdict is replaced by a
    `redirect_for()` line rather than refused (M2). And **`remember`/`forget` name only a
    key**: the `(device_id, namespace)` pair is supplied here, by us, so the write cannot
    reach another module's namespace or another child's robot (X9).
    """
    spoke = wrote = dropped = 0
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
        elif kind in ("act", "subscribe", "brain"):
            # Unreachable in P0 — the capability is refused at load (brief S5). Kept as an
            # explicit refusal rather than a silent drop so the day the wire lands, the
            # gap is a line to fill in and not a bug to find.
            print(f"[ext] {kind} is not plumbed yet; ignored", flush=True)
    return {"spoke": spoke, "wrote": wrote, "dropped_markup": dropped, "blocked": blocked}


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
