"""`explain()` — the AST as English, which matters as much as `evaluate()`."""

from __future__ import annotations

import re

from .values import (_text)


#: Avoids every bare capability identifier so a rendered sentence can never be mistaken
#: for (or grepped as) a permission (T13).
_FACT_WORDS = {
    "speech": "what your child said",
    "entities": "part of what your child said",
    "input_vars": "something the robot sent",
    "scratch": "a note from earlier this turn",
    "child": "your child's details",
    "memory": "something it remembered",
    "session": "how far into the chat you are",
    "presence": "whether somebody is in front of Moxie",
}

_OP_WORDS = {
    "==": "is", "!=": "is not", "<": "is less than", ">": "is more than",
    "<=": "is at most", ">=": "is at least",
    "and": "and", "or": "or", "not": "it is not the case that",
    "starts_with": "starts with", "ends_with": "ends with", "contains": "contains",
    "clock.ms": "the current time", "clock.local": "today's date and time",
    "session.is_empty": "the chat has just started",
    "session.total_volleys": "how many turns you have had",
    "presence.face_present": "somebody is in front of Moxie",
    "random.pick": "one of them, picked unpredictably",
    "random.int": "a number picked unpredictably",
}


#: The action tags a spoken line may carry (`moxie_sdk/actions.py`'s grammar, restated
#: because this package imports nothing outside itself). A parent never reads one: it is
#: lifted out of quoted text, and the sentence says what it makes happen instead.
_TAG = re.compile(r"<\s*(exit|sleep|launch_if_confirmed|launch)\s*((?::[^<>]*?)?)\s*>",
                  re.I)


def _tag_effects(text) -> list:
    """What the action tags in one line make happen, in a parent's words. A malformed tag
    does nothing, as in `actions.parse_action_tags`."""
    out = []
    for m in _TAG.finditer(str(text)):
        name = m.group(1).lower()
        fields = [f.strip() for f in m.group(2)[1:].split(":")] if m.group(2) else []
        while fields and not fields[-1]:
            fields.pop()
        if name in ("exit", "sleep") and not fields:
            out.append("the conversation ends" if name == "exit" else "Moxie goes to sleep")
        elif name.startswith("launch") and fields and fields[0] and len(fields) <= 2:
            out.append(f"Moxie starts the {_plain(fields[0])} activity")
    return out


def _plain(text: str) -> str:
    """Author text made safe for a parent-facing sentence: no action tags, braces or quotes,
    one line, ≤ 80 chars — never JSON-looking, however hostile the input (T13)."""
    out = "".join(" " if c in "{}\"\n\r\t" else c for c in _TAG.sub(" ", str(text)))
    out = " ".join(out.split())
    return out[:80] + ("…" if len(out) > 80 else "")


def _picked_lines(value):
    """The fixed lines a `random.pick` chooses among, or None when they are computed."""
    if not isinstance(value, dict) or len(value) != 1 or "random.pick" not in value:
        return None
    arg = value["random.pick"]
    lit = (arg[0].get("lit") if isinstance(arg, list) and len(arg) == 1
           and isinstance(arg[0], dict) else None)
    if isinstance(lit, list) and lit and all(isinstance(x, str) for x in lit):
        return lit
    return None


def _say_effects(value) -> list:
    """What a `say` makes happen through its lines' tags. An effect only some of the
    picked lines carry happens "sometimes"."""
    lines = [value] if isinstance(value, str) else (_picked_lines(value) or [])
    each = [_tag_effects(x) for x in lines]
    out = []
    for effects in each:
        for e in effects:
            phrase = e if all(e in other for other in each) else f"sometimes {e}"
            if phrase not in out:
                out.append(phrase)
    return out


#: Ops that shape a value without changing what a parent would call it; described by
#: their argument.
_TRANSPARENT_OPS = ("lower", "upper", "trim", "str", "int", "num", "abs", "floor",
                    "ceil", "round")


def _describe(node, depth: int = 0, binds=None) -> str:
    """One expression as a short English phrase; never JSON (T13). `binds` are the rule's
    `let` names, described by what they were bound to."""
    binds = binds or {}
    if depth > 4:
        return "a value it works out"
    if node is None:
        return "nothing"
    if node is True:
        return "yes"
    if node is False:
        return "no"
    if isinstance(node, (int, float)):
        return _text(node)
    if isinstance(node, str):
        return f"'{_plain(node)}'" if node.strip() else "an empty phrase"
    if not isinstance(node, dict) or len(node) != 1:
        return "a value it works out"
    key = next(iter(node))
    arg = node[key]
    picked = _picked_lines(node)
    if picked:
        return (_describe(picked[0], depth, binds) if len(picked) == 1
                else f"one of {len(picked)} options (picked unpredictably)")
    if key == "lit":
        return ("a fixed list of options" if isinstance(arg, (list, dict))
                else _describe(arg, depth, binds))
    if key == "var":
        root = str(arg).split(".")[0]
        if root in binds and "." not in str(arg):
            return _describe(binds[root], depth + 1)          # a `let`, not a fact
        base = _FACT_WORDS.get(root, "something it can read")
        rest = str(arg).partition(".")[2]
        return f"{base} ({rest})" if rest and root in ("memory", "input_vars",
                                                       "entities", "child") else base
    if key in _TRANSPARENT_OPS and isinstance(arg, list) and arg:
        return _describe(arg[0], depth, binds)
    if key in _OP_WORDS and isinstance(arg, list):
        words = _OP_WORDS[key]
        if len(arg) == 0:
            return words
        if len(arg) == 1:
            return f"{words} {_describe(arg[0], depth + 1, binds)}"
        if len(arg) == 2 and key in ("==", "!=", "<", ">", "<=", ">=",
                                     "starts_with", "ends_with", "contains"):
            return (f"{_describe(arg[0], depth + 1, binds)} {words} "
                    f"{_describe(arg[1], depth + 1, binds)}")
        joined = f" {words} ".join(_describe(a, depth + 1, binds) for a in arg)
        return joined
    if key == "if" and isinstance(arg, list) and len(arg) >= 2:
        return (f"{_describe(arg[1], depth + 1, binds)} when "
                f"{_describe(arg[0], depth + 1, binds)}"
                + (f", otherwise {_describe(arg[2], depth + 1, binds)}" if len(arg) > 2 else ""))
    if key == "concat" and isinstance(arg, list):
        # The gist, not the recipe: quote the literal words only.
        lits = [a.strip() for a in arg if isinstance(a, str) and re.search("[A-Za-z]", a)]
        if lits:
            gist = " … ".join(_plain(x) for x in lits[:3])
            return f"'{gist} …'" if len(lits) < len(arg) else f"'{gist}'"
    return "a value it works out"


def _describe_stmt(s, binds=None) -> str:
    keys = set(s)
    if "say" in keys:
        lines = _picked_lines(s["say"])
        if lines and len(lines) > 1:
            # Lines that all end the conversation are goodbyes, whatever their words.
            noun = ("goodbyes" if all("the conversation ends" in _tag_effects(x)
                                      for x in lines) else "lines")
            return f"says one of {len(lines)} {noun} (picked unpredictably)"
        return f"tells your child {_describe(s['say'], 0, binds)}"
    if "markup" in keys:
        return "makes Moxie move or play a sound"
    if "remember" in keys:
        return f"remembers {_plain(s['remember'].get('key'))}"
    if "forget" in keys:
        return f"forgets {_plain(s['forget'].get('key'))}"
    if "scratch" in keys:
        return f"keeps {_plain(s['scratch'].get('key'))} for the rest of this turn"
    if "act" in keys:
        return f"asks Moxie to {_plain(s['act'].get('name', '')).replace('_', ' ')}"
    if "subscribe" in keys:
        return "starts listening for something the robot notices"
    if "brain" in keys:
        return "asks the AI a question of its own"
    if "handled" in keys:
        return ("answers without asking the AI" if s["handled"]
                else "lets the AI answer as usual")
    if "note" in keys:
        return "writes one line to this appliance's log"
    return "does something"


def explain(ext) -> list:
    """One English sentence per rule (§5.4). The grant list says what a pack *may* do;
    these say what it *will* do, and the pack review shows both."""
    if not isinstance(ext, dict) or not ext.get("rules"):
        return []
    out = []
    for rule in ext["rules"]:
        if not isinstance(rule, dict):
            continue
        do = [s for s in (rule.get("do") or []) if isinstance(s, dict)]
        binds = rule.get("let") if isinstance(rule.get("let"), dict) else {}
        acts = [_describe_stmt(s, binds) for s in do]
        if not acts:
            continue
        if len(acts) == 1:
            body = acts[0]
        else:
            body = ", ".join(acts[:-1]) + " and " + acts[-1]
        if "when" in rule:
            head = f"When {_describe(rule['when'], 0, binds)}"
        else:
            head = "Whenever this activity is triggered"
        # What the spoken line's tags make happen, said last: it happens after the line.
        effects = []
        for s in do:
            for e in (_say_effects(s["say"]) if "say" in s else []):
                if e not in effects:
                    effects.append(e)
        then = f"; then {' and '.join(effects)}" if effects else ""
        out.append(f"{head}: {body}{then}.")
    return out
