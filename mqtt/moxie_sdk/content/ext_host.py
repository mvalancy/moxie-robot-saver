"""
The sandboxed-extension *host* (sandboxed-extensions.md §4.4/§4.5): the only code that
touches the world on the evaluator's behalf. `ext/` is the pure evaluator; here:

* `ext_facts()` builds a plain-JSON fact base — no live object to walk (X2).
* `apply_ext_effects()` applies effects only after the program ended, so a breach leaves
  nothing half-applied (X11), lets a spoken line act only on the action tags written
  whole in the rule's own text (`literal_actions()`), never on one it built at run time,
  and lets a `say`'s markup or a `markup` statement reach the robot only with no tag of
  ours and nothing the catalogue's check refuses (`robot_markup()`).
* `execution_actions_of()` / `subscriptions_of()` bound what a pack may put on the wire to
  the closed robot function / event tables.

`ContentApp` (content_app.py) is the only caller.
"""
from __future__ import annotations
import json
import re
import time
from typing import Optional

from .. import automarkup as _automarkup
from .. import safety as _safety
from .. import vocab
from ..actions import (drop_action_tags, lift_action_tags, parse_action_tags, tag_names,
                       tidy_spoken_text)
from ..types import Action, ActionType
from .memory import provenance
from .volley import Volley, Session
from . import ext


#: Inbound caps on robot-supplied (untrusted) values, applied before the evaluator's own.
EXT_MAX_SPEECH = 2000
EXT_MAX_ENTITIES = 16
EXT_MAX_ENTITY_CHARS = 256
EXT_MAX_INPUT_VARS = 32
EXT_MAX_INPUT_VAR_CHARS = 512
EXT_MAX_MEMORY_BYTES = 32768

#: One `<mark …/>`, `<usel …>` or `<break …/>` tag, for the catalogue gate below.
_EXT_TAG = re.compile(r"<(?:mark|usel|/usel|spurt|break)\b[^>]*/?>", re.I)
#: Where one of those tags opens, closed or not.
_EXT_OPEN = re.compile(r"<(?:mark|usel|/usel|spurt|break)\b", re.I)
#: A tag `_EXT_TAG` read as a mark (the same flags, so the same letters count as `mark`).
_EXT_MARK = re.compile(r"<mark\b", re.I)
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


def _malformed_tag(tag: str) -> bool:
    """A catalogue tag whose form the gate refuses, read before any id: one holding another
    `<` (`_EXT_TAG` reads from a `<mark`, `<usel`, `<spurt` or `<break` to the next `>`, so
    `<usel<usel…>` is one tag), one cut short by a `>` inside its own quotes (an odd number
    of `"` in what `_EXT_TAG` matched), or a mark that the catalogue's own mark pattern
    (`vocab._MARK_RE`) does not read whole. `_EXT_TAG` ends a tag at its first `>`, so
    `<spurt spurt_id="n>pe"/>` is read here as `<spurt spurt_id="n>`, which names no id and
    so has nothing to refuse, while the same catalogue check over the whole markup reads a
    spurt with the id `n>pe`, which it refuses; what the robot's own reader makes of such a
    tag is unverified, so it goes. A mark that pattern does not read whole (`<mark
    name='cmd:zzz'/>`, `<mark name = "cmd:zzz"/>`, `<mark name="cmd:a,data:{">`) is one the
    catalogue check passes without reading, so it would reach the robot unchecked; and in
    the whole markup the pattern reads on from its opening past the tag's end (`data:{.*?}`
    is lazy and runs on to the next `}"` that a `>` or `/>` closes), once per such mark, so
    a run of them cost that check quadratic time (`robot_markup`'s Cost). Read whole, a mark
    is read to its own end and no further. All three are read before the catalogue check,
    which is the costly one: its own patterns scan from every opening inside a tag to that
    tag's end (the gate took 26-78 ms on one 8 KB tag of `<usel` or `<spurt` openings,
    measured), and on a tag with one opening they read it once."""
    return ("<" in tag[1:] or tag.count('"') % 2 == 1
            or (_EXT_MARK.match(tag) is not None and vocab._MARK_RE.fullmatch(tag) is None))


def _refused_tag(tag: str) -> bool:
    """A catalogue tag the gate drops: a malformed one (`_malformed_tag`), or one with an id
    outside the frozen catalogue (`vocab.validate_markup`)."""
    return _malformed_tag(tag) or bool(vocab.validate_markup(tag))


def _tags_in(markup: str):
    """`_EXT_TAG`'s matches in `markup`. A tag ends at a `>`, so none starts after the
    last one, and the search stops there: from an opening with no `>` after it, `[^>]*`
    ran to the end of the markup once per opening (81-85 ms for 8 KB of `<mark`, four
    times longer per doubling, measured), and now nothing is read past the last `>`."""
    return _EXT_TAG.finditer(markup, 0, markup.rfind(">") + 1)


def ext_markup(markup: str) -> tuple:
    """`(clean, dropped)` — markup filtered tag by tag through the frozen `vocab.py`
    catalogue (M3); invalid tags are dropped and counted, text survives. `markup` reaches
    the robot's body, so it is never passed through unchecked (R4). One pass, linear in
    the markup (`_tags_in`, `_refused_tag`): dropping a tag can make the pieces around it
    meet (`<spu<usel genre="nope">rt spurt_id="nope"/>` leaves a spurt this pass never
    saw), which `robot_markup` catches."""
    if not markup:
        return "", 0
    dropped = 0
    out = []
    pos = 0
    for m in _tags_in(markup):
        out.append(markup[pos:m.start()])
        pos = m.end()
        tag = m.group(0)
        if _refused_tag(tag):
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


#: What the parent is told when a line's action tag was refused (`apply_ext_effects`): the
#: `ext_events` row's `reason` and its sentence. Not a breach: as with a markup tag the
#: catalogue drops (`ext_markup`), the line is said without it and the turn goes on, so a
#: refusal never counts towards quarantine (`ContentApp._ext_refused`).
REFUSED_TAG_REASON = "tag"
REFUSED_TAG_WORDS = "it tried to make Moxie do something its review did not name"


def _action_key(action: Action) -> tuple:
    """An `Action` as a set member: its type and every field exactly as parsed."""
    return (action.type, action.module_id, action.content_id, action.function,
            json.dumps(action.args, sort_keys=True, default=str))


def robot_markup(markup) -> tuple:
    """`(clean, dropped)`: `markup` as it may reach the robot, or `""` when it may not.

    The robot speaks its markup when it is given one, and no action tag in it is acted on
    (`ContentApp._reply_from_volley` keeps the text of its parse and none of its actions),
    so what the robot is sent must hold no tag of ours and nothing the catalogue refuses.
    This is the channel of a `say`'s markup and a `markup` statement only: a mark written in
    a line or in a conversation's opener reaches the robot's markup another way, unchecked
    (the runtime's markup floor sends a line holding `<` as it is). `_reply_from_volley` sends
    `actions.parse_action_tags(clean)[0]`: our tags lifted once, as from a line, then
    `tidy_spoken_text`, which takes out the space before a comma and so can join a tag's
    pieces (`<mark name="cmd:zzz ,data:{}"/>` would become a mark with the verb `zzz`; the
    gate drops that one first, since the catalogue's mark pattern does not read it whole).
    So: every tag with one of our names is lifted as that parse lifts them
    (`actions.lift_action_tags`, one pass, malformed ones too); then the gate
    (`ext_markup`), which drops tag by tag, the rest kept, what `_refused_tag` refuses: an
    id the catalogue refuses, a mark the catalogue's mark pattern does not read whole, a
    tag cut short by a `>` inside its own quotes, a tag holding another `<`; then
    `tidy_spoken_text`, so that what is checked last is what the robot is sent (the parse
    finds no tag of ours left to lift, and tidying twice changes nothing); then the markup
    is dropped whole, and the runtime's markup floor speaks the line, if what is left holds
    a tag of ours, a catalogue tag whose form the gate refuses (`_malformed_tag`), a
    catalogue tag opened with no `>` after it (left open), or anything the catalogue's own
    check over the whole text refuses (`vocab.validate_markup`, which reads a quoted `>`
    as part of the value: `<spurt x" spurt_id="n>pe"/>` is a spurt with the id `n>pe`).
    With every tag's form one the gate keeps and none left open, that whole-text check
    refuses every id the gate's tag-by-tag check would (each mark is read as itself; a
    usel's genre or a spurt's id is read as in its own tag, or read on past that tag's `>`,
    which no catalogue id holds), so the last pass does not read ids tag by tag again
    (pinned, `test_the_last_pass_reads_ids_once_and_refuses_what_a_tag_by_tag_read_would`).
    A tag of ours or one the
    gate would drop can be left only because a tag the gate dropped stood between the
    pieces of another (`<ex<ex<mark name="cmd:zzz"/>it>it>` would reach the robot as
    `<exit>`), or because tidying joined one; keeping any of it would need a pass the robot
    does not make, so nothing is kept.

    Cost: every pass is linear in the markup, the whole-text check included. That check is
    `vocab.py`'s, and it runs only when nothing above dropped the markup, so every catalogue
    tag left has a form the gate keeps (`_malformed_tag`) and none is left open. Each of
    its patterns then reads from an opening to that tag's end and no further (a mark is read
    whole), except that a usel's genre or a spurt's id whose opening quote is its tag's last
    is read on to the next `"` (and a usel's on to the next `>`): once per tag at most, and
    the search goes on past what it read. Measured with the 8 KB cap lifted, `robot_markup`
    took about twice as long per doubling, up to 128 KB, on every shape tried. Before round
    10 a mark that pattern does not read whole was kept, and the check read on from each one
    past its tag: on 8 KB of `<mark name="cmd:a,data:{">` tags followed by `}"`, 30
    ideographic spaces and `/x` over and over (the round-9 review's shape), `robot_markup`
    took 15-17 ms and five turns of four such markups 0.33-0.48 s, against 0.3 ms and
    12-23 ms now, measured (the review measured 20-27 ms and 0.48 s before). Before round 8
    the tags of ours were taken out to a fixpoint (`actions.drop_action_tags` with nothing
    kept) and the gate ran once after, which let a dropped tag's neighbours meet, and the
    fixpoint cost 0.6-1.0 s per 8 KB nest (2.1-3.7 s for four). `dropped` counts the tags
    the gate dropped, and one more for a markup dropped whole. Never reported to the parent:
    no action tag in markup is acted on."""
    lifted = lift_action_tags(str(markup or "")[:ext.MAX_MARKUP_CHARS])
    clean, dropped = ext_markup(lifted)
    clean = tidy_spoken_text(clean)
    exposed = (bool(tag_names(clean))
               or any(_malformed_tag(m.group(0)) for m in _tags_in(clean))
               or _EXT_OPEN.search(clean, clean.rfind(">") + 1) is not None
               or bool(vocab.validate_markup(clean)))
    if exposed:
        _automarkup._drop("ext")
        return "", dropped + 1
    return clean, dropped


def _strings_in(node, out: list) -> list:
    """Every string written in `node`, a JSON tree, map keys included, in document order."""
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, list):
        for item in node:
            _strings_in(item, out)
    elif isinstance(node, dict):
        for key, item in node.items():
            out.append(str(key))
            _strings_in(item, out)
    return out


def literal_actions(block) -> tuple:
    """Per rule, the actions written whole in the rule's own text: every action tag that
    `actions.parse_action_tags` (the parse the robot path uses) reads out of a string
    literal anywhere inside the rule's `say` statements or `let` values, as a frozenset of
    `_action_key`s. That is all a rule's spoken line may act on (`apply_ext_effects`). A
    tag built at run time from pieces, cased by `upper` or `lower`, cut out of a longer
    text by `get`, `slice`, `split`, `replace` or `reverse`, or read from what the child
    said, a memory or `input_vars` is not in it, whatever it parses to. The tag name is
    case-insensitive and the fields compare exactly, as the parse has it. Total: anything
    that is not a program allows nothing."""
    out = []
    rules = block.get("rules") if isinstance(block, dict) else None
    for rule in (rules if isinstance(rules, list) else []):
        texts: list = []
        if isinstance(rule, dict):
            do = rule.get("do")
            for s in (do if isinstance(do, list) else []):
                if isinstance(s, dict) and "say" in s:
                    _strings_in(s["say"], texts)
            binds = rule.get("let")
            if isinstance(binds, dict):
                _strings_in(list(binds.values()), texts)
        out.append(frozenset(_action_key(a) for text in texts
                             for a in parse_action_tags(text)[1]))
    return tuple(out)


def apply_ext_effects(effects, *, volley: Volley, memory=None, device_id: str = "",
                      namespace: str = "", classifier=None, module_id: str = "",
                      content_id: str = "", allowed=frozenset()) -> dict:
    """Apply one extension's effects in order under the §6.3 caps; returns counts
    `{"spoke", "wrote", "dropped_markup", "blocked", "acted", "subscribed"}` and
    `"refused"`, the actions of the tags a `say` was not allowed to act on.

    `say` acts only on an action tag whose action is in `allowed`, the matched rule's
    `literal_actions`: any other `<exit>`, `<sleep>` or `<launch:…>` the line carries is
    taken out before the line is kept (`actions.drop_action_tags`), so it is neither said
    nor acted on, and the caller reports it. Nothing is allowed unless the caller says so:
    the default is the empty set, so a line from a caller that passes nothing acts on no
    tag at all. A line that carries only allowed tags is kept exactly as written. It then
    passes the same output safety classifier as a model line (unsafe → redirect, M2).
    Markup (a `say`'s or a `markup` statement's) reaches the robot only as `robot_markup`
    leaves it: no tag of ours and nothing the catalogue's check refuses, or none at all.
    `remember`/`forget` name only a key; device and namespace come from the host (X9).
    """
    spoke = wrote = dropped = acted = subscribed = 0
    blocked = False
    refused: list = []
    for eff in effects or []:
        kind = eff.get("kind")
        if kind == "say":
            text = str(eff.get("text") or "")[:ext.MAX_SAY_CHARS]
            text, taken = drop_action_tags(text, lambda a: _action_key(a) in allowed)
            refused += taken
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
                markup, n = robot_markup(markup)
                dropped += n
            volley.set_output(text, markup or None)
            spoke += 1
        elif kind == "markup":
            clean, n = robot_markup(eff.get("markup"))
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
            "acted": acted, "subscribed": subscribed, "refused": refused}


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
