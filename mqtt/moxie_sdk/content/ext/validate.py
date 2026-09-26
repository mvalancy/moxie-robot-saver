"""Load-time validation — everything decidable without running anything (X1, X6, X10)."""

from __future__ import annotations

from .grammar import (ACTION_WORDS, CAPABILITY_WORDS, EXT_FORMAT, FACT_ROOTS, HOOKS,
    _is_p1, _KEY, MAX_ARGS, MAX_CAPABILITIES, MAX_DEPTH, MAX_NODES, MAX_RULES,
    MAX_STATEMENTS_PER_RULE, MAX_SUBSCRIPTIONS, normal_name, normal_op, OPS, _PATH,
    _path_capability, STATEMENTS, SUBSCRIBE_EVENTS)


class _Validator:
    def __init__(self):
        self.reasons: list[str] = []
        self.used: set[str] = set()
        self.nodes = 0
        #: `let` names visible so far: a binding sees earlier ones; `when`/`do` see all.
        self.binds: set[str] = set()

    def fail(self, msg: str) -> None:
        if len(self.reasons) < 12:
            self.reasons.append(msg)

    # ---- expressions ----
    def expr(self, node, where: str, depth: int = 1) -> None:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            return self.fail(f"the whole extension is more than {MAX_NODES} nodes")
        if depth > MAX_DEPTH:
            return self.fail(f"{where}: nested deeper than {MAX_DEPTH}")
        if node is None or isinstance(node, (bool, int, float, str)):
            return                                    # a literal is itself
        if not isinstance(node, dict):
            return self.fail(f"{where}: a list is not an expression "
                             f"(use {{\"list\": [...]}} or {{\"lit\": [...]}})")
        if len(node) != 1:
            return self.fail(f"{where}: an expression is exactly one key, "
                             f"got {len(node)}")
        key = next(iter(node))
        arg = node[key]
        if key == "lit":
            return self._lit(arg, where)
        if key == "var":
            return self._var(arg, where)
        name = normal_op(key)
        if not name or name not in OPS:
            return self.fail(f"{where}: unknown operator {key!r}")
        lo, hi, cap = OPS[name]
        if cap:
            self.used.add(cap)
        if not isinstance(arg, list):
            return self.fail(f"{where}: {name} takes a list of arguments")
        if not (lo <= len(arg) <= hi):
            return self.fail(f"{where}: {name} takes {lo}..{hi} arguments, "
                             f"got {len(arg)}")
        for i, sub in enumerate(arg):
            self.expr(sub, f"{where}.{name}[{i}]", depth + 1)

    def _lit(self, arg, where: str) -> None:
        """A literal is only data — no nested op is evaluated inside one, so `lit` cannot
        become a second, unchecked grammar."""
        stack = [(arg, 1)]
        while stack:
            v, d = stack.pop()
            self.nodes += 1
            if self.nodes > MAX_NODES:
                return self.fail(f"the whole extension is more than {MAX_NODES} nodes")
            if d > MAX_DEPTH:
                return self.fail(f"{where}: literal nested deeper than {MAX_DEPTH}")
            if isinstance(v, dict):
                for k, sub in v.items():
                    if not isinstance(k, str):
                        return self.fail(f"{where}: a literal map needs string keys")
                    stack.append((sub, d + 1))
            elif isinstance(v, list):
                for sub in v:
                    stack.append((sub, d + 1))
            elif not (v is None or isinstance(v, (bool, int, float, str))):
                return self.fail(f"{where}: a literal must be plain JSON")

    def _var(self, arg, where: str) -> None:
        if not isinstance(arg, str) or not _PATH.match(arg):
            return self.fail(f"{where}: {arg!r} is not a fact path")
        for seg in arg.split("."):
            if seg.startswith("_"):
                # An invalid program, not a runtime block (§4.4).
                return self.fail(f"{where}: a path segment may not begin with '_' "
                                 f"({arg!r})")
        root = arg.split(".")[0]
        if root in self.binds:
            return                                  # a `let` value, not a fact
        if root not in FACT_ROOTS:
            return self.fail(f"{where}: {root!r} is not a fact "
                             f"(known: {', '.join(sorted(FACT_ROOTS))})")
        cap = _path_capability(arg)
        if cap:
            self.used.add(cap)

    # ---- statements ----
    def stmt(self, s, where: str) -> None:
        self.nodes += 1
        if not isinstance(s, dict) or not s:
            return self.fail(f"{where}: a statement is an object")
        keys = set(s)
        if keys == {"say", "markup"}:
            head = "say"
        elif len(keys) == 1:
            head = next(iter(keys))
        else:
            return self.fail(f"{where}: a statement is one of {sorted(STATEMENTS)}, "
                             f"got {sorted(keys)}")
        name = normal_name(head)
        if not name or name not in STATEMENTS:
            return self.fail(f"{where}: unknown statement {head!r}")
        cap = STATEMENTS[name]
        if cap:
            self.used.add(cap)
        getattr(self, f"_st_{name.replace('.', '_')}")(s, where)

    def _st_say(self, s, where):
        self.expr(s["say"], f"{where}.say")
        if "markup" in s:
            self.used.add("markup")
            self.expr(s["markup"], f"{where}.markup")

    def _st_markup(self, s, where):
        self.expr(s["markup"], f"{where}.markup")

    def _st_note(self, s, where):
        self.expr(s["note"], f"{where}.note")

    def _st_handled(self, s, where):
        if not isinstance(s["handled"], bool):
            self.fail(f"{where}.handled: expected true or false")

    def _key_value(self, body, where, *, value: bool):
        if not isinstance(body, dict):
            return self.fail(f"{where}: expected an object with a key")
        allowed = {"key", "value"} if value else {"key"}
        if set(body) != allowed:
            return self.fail(f"{where}: expected exactly {sorted(allowed)}")
        k = body.get("key")
        if not isinstance(k, str) or not _KEY.match(k):
            return self.fail(f"{where}.key: {k!r} is not a memory key "
                             f"(letters, digits, '_', '-', dot-separated)")
        if value:
            self.expr(body["value"], f"{where}.value")

    def _st_remember(self, s, where):
        self._key_value(s["remember"], f"{where}.remember", value=True)

    def _st_forget(self, s, where):
        self._key_value(s["forget"], f"{where}.forget", value=False)

    def _st_scratch(self, s, where):
        self._key_value(s["scratch"], f"{where}.scratch", value=True)

    def _st_act(self, s, where):
        body = s["act"]
        if not isinstance(body, dict) or set(body) - {"name", "args"} or "name" not in body:
            return self.fail(f"{where}.act: expected {{name, args}}")
        name = normal_name(body.get("name"))
        if not name or name not in ACTION_WORDS:
            return self.fail(f"{where}.act.name: {body.get('name')!r} is not an "
                             f"action this appliance knows "
                             f"({', '.join(sorted(ACTION_WORDS))})")
        self.used.add(f"act.{name}")
        args = body.get("args", [])
        if not isinstance(args, list) or len(args) > MAX_ARGS:
            return self.fail(f"{where}.act.args: expected a list of at most {MAX_ARGS}")
        for i, a in enumerate(args):
            self.expr(a, f"{where}.act.args[{i}]")

    def _st_brain(self, s, where):
        body = s["brain"]
        if not isinstance(body, dict) or set(body) != {"prompt"}:
            return self.fail(f"{where}.brain: expected {{prompt}}")
        self.expr(body["prompt"], f"{where}.brain.prompt")

    def _st_subscribe(self, s, where):
        """`{"subscribe": [event, …]}` — bounded by `SUBSCRIBE_EVENTS` at load, like
        `_st_act`. `content_app.subscriptions_of` re-checks host-side (a Python handler
        never meets this validator).

        Compared literally, not via `normal_name`: these are hyphenated wire strings, and
        membership in the tuple already refuses homoglyphs (e.g. U+2011 hyphens).
        """
        events = s["subscribe"]
        if not isinstance(events, list) or not events or len(events) > MAX_SUBSCRIPTIONS:
            return self.fail(f"{where}.subscribe: expected 1..{MAX_SUBSCRIPTIONS} events")
        for e in events:
            if not isinstance(e, str) or e not in SUBSCRIBE_EVENTS:
                return self.fail(f"{where}.subscribe: {e!r} is not an event this "
                                 f"appliance can ask the robot for "
                                 f"({', '.join(SUBSCRIBE_EVENTS)})")


def validate(ext, *, grants=None, allow_p1: bool = False) -> list:
    """Every reason this extension cannot be installed, as sentences. Empty ⇒ installable.

    Run at import (`packs.validate_item`) and again at every load, so a program written
    straight into the store, or invalid under a newer validator, stops loading (T17).

    `allow_p1` checks grammar only, skipping the refusal of `P1_CAPABILITIES` (used to
    prove §8's conformance ASTs); never pass it on a path that then evaluates. `grants`,
    when given, makes an ungranted declared capability a load refusal too (§4.2).
    """
    v = _Validator()
    if not isinstance(ext, dict):
        return ["extension: expected an object"]
    if not ext:
        return []                                   # `{}` = no extension at all
    unknown = sorted(set(ext) - {"ext_format", "capabilities", "on", "rules"})
    if unknown:
        v.fail(f"extension: unknown key(s) {', '.join(unknown)}")
    if ext.get("ext_format") != EXT_FORMAT:
        v.fail(f"extension: ext_format must be {EXT_FORMAT}, "
               f"got {ext.get('ext_format')!r}")
    on = ext.get("on")
    if on not in HOOKS:
        v.fail(f"extension: `on` must be one of {', '.join(HOOKS)}, got {on!r}")

    declared_raw = ext.get("capabilities")
    declared: set[str] = set()
    if not isinstance(declared_raw, list) or len(declared_raw) > MAX_CAPABILITIES:
        v.fail(f"extension: `capabilities` must be a list of at most "
               f"{MAX_CAPABILITIES} names")
        declared_raw = []
    for raw in declared_raw:
        name = normal_name(raw)
        if not name:
            v.fail(f"capability {raw!r} is not a capability name")
            continue
        if name.startswith("act."):
            if name[4:] not in ACTION_WORDS:
                v.fail(f"capability {name!r} names an action this appliance does not know")
                continue
        elif name not in CAPABILITY_WORDS:
            v.fail(f"capability {name!r} is not one this appliance has")
            continue
        if name in declared:
            v.fail(f"capability {name!r} is declared twice")
        declared.add(name)

    rules = ext.get("rules")
    if not isinstance(rules, list) or not rules:
        v.fail("extension: `rules` must be a non-empty list")
        rules = []
    if len(rules) > MAX_RULES:
        v.fail(f"extension: {len(rules)} rules (the limit is {MAX_RULES})")
        rules = rules[:MAX_RULES]
    for ri, rule in enumerate(rules):
        where = f"rules[{ri}]"
        if not isinstance(rule, dict):
            v.fail(f"{where}: expected an object")
            continue
        extra = sorted(set(rule) - {"when", "let", "do"})
        if extra:
            v.fail(f"{where}: unknown key(s) {', '.join(extra)}")
        v.binds = set()
        binds = rule.get("let")
        if binds is not None:
            if not isinstance(binds, dict):
                v.fail(f"{where}.let: expected an object of name → expression")
            else:
                for bn, bexpr in binds.items():
                    if not isinstance(bn, str) or not _PATH.match(bn) or "." in bn:
                        v.fail(f"{where}.let: {bn!r} is not a binding name")
                        continue
                    if bn in FACT_ROOTS:
                        v.fail(f"{where}.let: {bn!r} is a fact and cannot be rebound")
                    v.expr(bexpr, f"{where}.let.{bn}")
                    v.binds.add(bn)
        if "when" in rule:
            v.expr(rule["when"], f"{where}.when")
        do = rule.get("do")
        if not isinstance(do, list) or not do:
            v.fail(f"{where}.do: expected a non-empty list of statements")
            continue
        if len(do) > MAX_STATEMENTS_PER_RULE:
            v.fail(f"{where}.do: {len(do)} statements "
                   f"(the limit is {MAX_STATEMENTS_PER_RULE})")
            continue
        for si, s in enumerate(do):
            v.stmt(s, f"{where}.do[{si}]")

    if v.reasons:
        return v.reasons

    # Two-directional capability rule (§5, X10): equality, not containment.
    missing = sorted(v.used - declared)
    if missing:
        v.fail("uses things it did not declare: " + ", ".join(missing))
    spare = sorted(declared - v.used)
    if spare:
        v.fail("declares things it never uses: " + ", ".join(spare))

    p1 = sorted(c for c in declared if _is_p1(c))
    if p1 and not allow_p1:
        v.fail("needs something this appliance cannot grant yet: " + ", ".join(p1)
               + " (see `P1_CAPABILITIES` for what each one is still waiting on)")

    if grants is not None:
        ungranted = sorted(declared - set(grants))
        if ungranted:
            v.fail("has not been granted: " + ", ".join(ungranted))
    return v.reasons


def capabilities_of(ext) -> list:
    """The declared capability names, normalized and sorted. `[]` for a non-extension."""
    if not isinstance(ext, dict):
        return []
    out = set()
    for raw in ext.get("capabilities") or []:
        name = normal_name(raw)
        if name:
            out.add(name)
    return sorted(out)


def grant_list(ext) -> list:
    """One plain sentence per (normalized) capability, from the fixed tables (§5.4)."""
    out = []
    for name in capabilities_of(ext):
        if name.startswith("act."):
            words = ACTION_WORDS.get(name[4:])
        else:
            words = CAPABILITY_WORDS.get(name)
        out.append(words or f"Can do something this appliance does not have words for "
                            f"({name})")
    return out
