"""
Child safety — the `InputSafety` contract, enforced.

`RemoteChatInput.InputSafety{is_unsafe, blocked_by[], intents[], phrase_id}` is the one
moderation hook the recovered protocol gives us
(`docs/reverse-engineering/protocol/recovered-proto/embodied/robotbrain/RemoteChat.proto`
:180-186 — the message; :198 `RemoteChatInput.safety` field 12; :335
`RemoteChatResponse.input` field 17). `docs/architecture/ai-seam.md` §2 specifies it and
says a kid-facing backend SHOULD populate it. This module is that classifier, and the
runtime enforces its verdict on **both** sides of a turn:

  * **pre-inference** — the child's utterance is assessed BEFORE the brain is called, so a
    hard-blocked turn never reaches a model at all;
  * **post-inference** — every chunk the brain produces is assessed BEFORE it is
    published, because streaming puts a sentence on the wire while the rest of the answer
    does not exist yet.

**What v1 is, honestly.** A transparent rule engine: word-boundary word lists, a handful of
phrase regexes, and per-category false-positive guards, all in `safety_rules.json` — a file
a parent can open and read. It runs locally, in-process, with no cloud call and no model.

**What it is not.** A rule engine is a *floor*, not a filter. It cannot understand context,
sarcasm, or a harmful idea expressed in gentle words; it will miss novel phrasings and
every language its tables are not written in; and it will occasionally flag something
innocent. It is one layer under the model's own alignment and the persona's safety
instructions, not a replacement for either — and not a substitute for a parent.

**The drop-in seam.** `Classifier` is a protocol exactly like `moxie_sdk.stt.Transcriber` /
`moxie_sdk.tts.Synthesizer`: one method, `assess(text, role) -> InputSafety`. A local model
classifier drops in behind it without touching the runtime
(`MoxieRuntime(app, safety=MyClassifier())`).

Idea credit: OpenMoxie Fork A's `site/hive/mqtt/conversation_log.py` checks regex safety
categories *before* inference and gives a parent an acknowledge-reviewed queue, and is
honest in its own UI that keyword flags are a review aid rather than a filter
(`docs/architecture/openmoxie-feature-audit.md` §2.1, and BEYOND #2 which calls that "a
good floor"). The idea of pre-inference keyword flags + a parent review queue is theirs;
these categories, the role-aware block/flag policy, the post-inference per-chunk stage,
the wire mapping and this code are ours.
"""
from __future__ import annotations

import json
import os
import random
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# the verdict
# ---------------------------------------------------------------------------

#: What a category may do, per side of the conversation.
BLOCK, FLAG, ALLOW = "block", "flag", "allow"

#: Which side of the turn a piece of text came from.
CHILD, MOXIE = "child", "moxie"


@dataclass
class InputSafety:
    """One safety verdict — `RemoteChatInput.InputSafety` plus what a parent needs.

    The first four fields are the wire contract, in proto field order (RemoteChat.proto
    :181-186). `to_wire()` emits **only** those four.

    `is_unsafe` is true exactly when something **blocked** — a merely-flagged utterance is
    allowed through and recorded for a parent, and we do not assert on the wire that it was
    unsafe. `blocked_by` is then always non-empty when `is_unsafe`, which is how the proto
    pairs them ("whether the child's input was unsafe, which classifiers blocked it").
    Flagged categories live in `flagged_by`, which never reaches the robot.
    """

    is_unsafe: bool = False                 # proto field 1
    blocked_by: list = field(default_factory=list)   # proto field 2 — category ids
    intents: list = field(default_factory=list)      # proto field 3
    phrase_id: Optional[int] = None         # proto field 4 — the safety line spoken

    # --- ours, never on the wire ---
    flagged_by: list = field(default_factory=list)   # recorded, not blocked
    role: str = CHILD                       # which side produced the text
    escalate: bool = False                  # a parent should look at this one first
    phrase_set: str = "generic"             # which redirect family fits
    excerpt: str = ""                       # short, trigger-masked, for the parent queue

    # ---- derived ----
    @property
    def action(self) -> str:
        """`block` / `flag` / `allow` — what the runtime should do with this text."""
        if self.blocked_by:
            return BLOCK
        return FLAG if self.flagged_by else ALLOW

    @property
    def categories(self) -> list:
        """Every category that matched, blocking first."""
        return list(self.blocked_by) + [c for c in self.flagged_by
                                        if c not in self.blocked_by]

    def __bool__(self) -> bool:
        """Truthy when anything matched at all (block or flag)."""
        return bool(self.blocked_by or self.flagged_by)

    def to_wire(self) -> dict:
        """The `InputSafety` JSON object — the four proto fields, omitting empties.

        Emitted under `RemoteChatResponse.input.safety` (fields 17 → 12) by
        `moxie_sdk.wire.build_chat_response(safety=…)`."""
        out = {"is_unsafe": bool(self.is_unsafe)}
        if self.blocked_by:
            out["blocked_by"] = list(self.blocked_by)
        if self.intents:
            out["intents"] = list(self.intents)
        if self.phrase_id is not None:
            out["phrase_id"] = int(self.phrase_id)
        return out


@dataclass
class Redirect:
    """The line Moxie says instead of the blocked text (already markup-performed)."""
    text: str
    markup: str
    phrase_id: int


# ---------------------------------------------------------------------------
# normalization — one text in, several comparable forms out
# ---------------------------------------------------------------------------

# Always-on cleanups: curly apostrophes onto `'` (so `don't`/`don’t` are one word) and
# the zero-width characters used to split a word invisibly.
_ALWAYS = str.maketrans({
    "’": "'", "‘": "'", "ʼ": "'",
    "​": "", "‌": "", "‍": "", "﻿": "",
})

# Character substitutions people use to slip past a word list (`sh1t`, `$hit`, `f@ck`).
# Applied ONLY where the next character is a letter, so an ordinary `hi!` or `b4` is left
# alone — substituting a trailing `!` would turn `shoot!` into `shooti` and *break* a
# match rather than catch one.
_LEET = {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b",
         "9": "g", "@": "a", "$": "s", "!": "i", "|": "i", "+": "t"}
_LEET_RE = re.compile("[%s]" % re.escape("".join(_LEET)))

_RUN = re.compile(r"(.)\1{2,}")           # three or more of the same character


def normalize(text: str) -> str:
    """Casefolded, accent-stripped, de-leeted text with runs of whitespace collapsed.

    `NFKD` + dropping combining marks means `shít` / `ｓｈｉｔ` normalize onto `shit`; the
    leet map folds `sh1t` / `$hit`. Punctuation that carries meaning for the phrase
    regexes (`'`) is kept, and ordinary punctuation is left where it is so word
    boundaries stay where the writer put them.
    """
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", str(text))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.casefold().translate(_ALWAYS)

    def _sub(m):
        nxt = t[m.end():m.end() + 1]
        return _LEET[m.group(0)] if nxt.isalpha() else m.group(0)

    t = _LEET_RE.sub(_sub, t)
    return re.sub(r"\s+", " ", t).strip()


def _variants(text: str) -> tuple:
    """Normalized text plus its de-elongated forms (`fuuuuck`, `killlll`).

    A run of 3+ identical characters is collapsed to one AND to two, because either may
    be the real word (`fuuuuck` → `fuck`, `killlll` → `kill`). Both are cheap; matching
    against all three strings costs one extra regex scan each.
    """
    base = normalize(text)
    if not base:
        return ("",)
    one = _RUN.sub(r"\1", base)
    two = _RUN.sub(r"\1\1", base)
    seen, out = set(), []
    for v in (base, one, two):
        if v not in seen:
            seen.add(v)
            out.append(v)
    return tuple(out)


# ---------------------------------------------------------------------------
# the rule table
# ---------------------------------------------------------------------------

_RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "safety_rules.json")


def rules_path() -> str:
    """The rules file in force — `MOXIE_SAFETY_RULES`, else the shipped table."""
    return os.environ.get("MOXIE_SAFETY_RULES", "").strip() or _RULES_PATH


def load_rules(path: Optional[str] = None) -> dict:
    """Read (and lightly validate) the rules JSON. Raises on a broken file — a safety
    table that silently failed open would be worse than no table at all."""
    with open(path or rules_path()) as fh:
        data = json.load(fh)
    if not isinstance(data, dict) or not isinstance(data.get("categories"), list):
        raise ValueError(f"{path or rules_path()}: no `categories` list")
    return data


class _Category:
    """One compiled category from the rules file."""

    __slots__ = ("id", "label", "action", "escalate", "intents", "phrase_set",
                 "words", "phrases", "allow")

    def __init__(self, raw: dict):
        self.id = str(raw["id"])
        self.label = str(raw.get("label") or self.id)
        act = raw.get("action") or {}
        self.action = {CHILD: str(act.get(CHILD, ALLOW)), MOXIE: str(act.get(MOXIE, ALLOW))}
        self.escalate = bool(raw.get("escalate"))
        self.intents = [str(i) for i in (raw.get("intents") or [])]
        self.phrase_set = str(raw.get("phrase_set") or "generic")
        words = [w for w in (raw.get("words") or []) if w]
        # Word list → one alternation, longest first so `fucking` wins over `fuck`.
        self.words = (re.compile(r"\b(?:%s)\b" % "|".join(
            re.escape(normalize(w)) for w in sorted(words, key=len, reverse=True)))
            if words else None)
        self.phrases = [re.compile(p) for p in (raw.get("phrases") or [])]
        self.allow = [re.compile(p) for p in (raw.get("allow") or [])]

    def hits(self, variants: tuple) -> list:
        """The matched trigger strings in `variants`, or [] — allow-guarded.

        Each guard's span is **removed** before matching, so `shoot a photo` cannot
        trigger `violence_talk` while `shoot a photo then shoot him` still can.
        """
        found = []
        for text in variants:
            guarded = text
            for a in self.allow:
                guarded = a.sub(" ", guarded)
            if self.words is not None:
                found += [m.group(0) for m in self.words.finditer(guarded)]
            for p in self.phrases:
                found += [m.group(0) for m in p.finditer(guarded)]
            if found:
                break                       # one variant matching is enough
        return found


# ---------------------------------------------------------------------------
# the classifier seam
# ---------------------------------------------------------------------------

class Classifier:
    """The safety seam — one method, exactly like `Transcriber` / `Synthesizer`.

    Implement this to swap in a local model classifier (or a hybrid: rules first, model
    on the ambiguous middle) without touching the runtime::

        class MyClassifier(Classifier):
            name = "distil-safety"
            def assess(self, text, *, role=CHILD): ...
        MoxieRuntime(app, safety=MyClassifier())

    Contract: **pure and local** (no network — this is a child's device), fast enough to
    run per streamed chunk, and total (never raises; the runtime treats an exception as
    "allow", so a broken classifier must not silence Moxie).
    """

    name = "classifier"

    def assess(self, text: str, *, role: str = CHILD) -> InputSafety:  # pragma: no cover
        raise NotImplementedError


class RuleClassifier(Classifier):
    """v1: the transparent rule engine described in this module's docstring."""

    name = "rules"

    def __init__(self, rules: Optional[dict] = None, path: Optional[str] = None):
        self.rules = rules if rules is not None else load_rules(path)
        self.categories = [_Category(c) for c in self.rules["categories"]]
        self.phrase_sets = {k: list(v) for k, v in (self.rules.get("phrases") or {}).items()}

    # -- the verdict --
    def assess(self, text: str, *, role: str = CHILD) -> InputSafety:
        role = MOXIE if role == MOXIE else CHILD
        verdict = InputSafety(role=role)
        variants = _variants(text)
        if not variants or not variants[0]:
            return verdict
        triggers = []
        for cat in self.categories:            # file order = severity order
            action = cat.action.get(role, ALLOW)
            if action == ALLOW:
                continue
            hits = cat.hits(variants)
            if not hits:
                continue
            triggers += hits
            if action == BLOCK:
                verdict.blocked_by.append(cat.id)
                if not verdict.is_unsafe:      # first blocker owns the spoken line
                    verdict.phrase_set = cat.phrase_set
                verdict.is_unsafe = True
            else:
                verdict.flagged_by.append(cat.id)
            verdict.escalate = verdict.escalate or cat.escalate
            for i in cat.intents:
                if i not in verdict.intents:
                    verdict.intents.append(i)
        if verdict:
            verdict.excerpt = redact(text, triggers)
        return verdict

    # -- the line Moxie says instead --
    def redirect(self, verdict: InputSafety, *, last: str = "") -> Redirect:
        """Pick a kid-appropriate redirect for a blocked verdict, never repeating `last`.

        The chosen line's `id` becomes `InputSafety.phrase_id` — literally "a matched
        safety-phrase id" (remote-chat-protocol.md:113-115).
        """
        lines = self.phrase_sets.get(verdict.phrase_set) or self.phrase_sets.get("generic") or []
        if not lines:                          # a rules file with no phrases at all
            return Redirect(text="Let's talk about something else.",
                            markup="Let's talk about something else.", phrase_id=0)
        pool = [ln for ln in lines if ln.get("text") != last] or list(lines)
        line = random.choice(pool)
        text = str(line.get("text") or "")
        return Redirect(text=text,
                        markup=_performed(text, int(line.get("mood") or 0),
                                          str(line.get("gesture") or "")),
                        phrase_id=int(line.get("id") or 0))


# The shipped classifier, built once. `MOXIE_SAFETY_RULES` is read at first use.
_DEFAULT: Optional[RuleClassifier] = None


def default_classifier() -> RuleClassifier:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = RuleClassifier()
    return _DEFAULT


def assess(text: str, *, role: str = CHILD,
           classifier: Optional[Classifier] = None) -> InputSafety:
    """Assess one piece of text. `role="child"` for what the child said, `"moxie"` for
    what Moxie is about to say — the policy differs by side (a child swearing is flagged
    for a parent; Moxie swearing is blocked)."""
    return (classifier or default_classifier()).assess(text, role=role)


def redirect_for(verdict: InputSafety, *, last: str = "",
                 classifier: Optional[Classifier] = None) -> Redirect:
    """The redirect line for a blocked verdict (see `RuleClassifier.redirect`)."""
    c = classifier or default_classifier()
    fn = getattr(c, "redirect", None)
    if callable(fn):
        return fn(verdict, last=last)
    return default_classifier().redirect(verdict, last=last)


# ---------------------------------------------------------------------------
# what a parent sees — never the raw unsafe text
# ---------------------------------------------------------------------------

MAX_EXCERPT = 96


def redact(text: str, triggers=(), limit: int = MAX_EXCERPT) -> str:
    """A short excerpt for the review queue with the matched words masked.

    A parent needs enough to recognize the moment ("he asked about ***") without the
    queue becoming a searchable archive of the worst thing their child ever said — and
    without us ever echoing the unsafe words back into a UI. Masking is done on the
    ORIGINAL text (so the excerpt still reads naturally), matching each trigger
    case-insensitively; anything past `limit` characters is cut on a word boundary.
    """
    out = " ".join(str(text or "").split())
    trigs = sorted({t for t in triggers if t}, key=len, reverse=True)
    for trig in trigs:
        # The trigger came off the *normalized* text, so match it loosely against the
        # original: any letter may be separated by a little punctuation or a space.
        pattern = r"\b%s" % r"\W{0,3}".join(re.escape(c) for c in trig if not c.isspace())
        try:
            out = re.sub(pattern, "***", out, flags=re.I)
        except re.error:                       # a pathological trigger — mask crudely
            out = out.replace(trig, "***")
    # Hard guarantee: if a loose match failed (leet-spelling, say) and a trigger word is
    # still legible in the excerpt, there is no excerpt. We never echo it back.
    checked = normalize(out)
    if any(t in checked for t in trigs):
        return ""
    if len(out) > limit:
        cut = out[:limit].rsplit(" ", 1)[0] or out[:limit]
        out = cut + "…"
    return out


# ---------------------------------------------------------------------------
# markup (mirrors moxie_sdk/filler.py — the redirect is performed, not read)
# ---------------------------------------------------------------------------

def _mark(verb: str, data: Optional[dict] = None) -> str:
    """One `<mark name="cmd:…"/>` behavior tag; `+` stands in for `"` inside the XML
    attribute (docs/reverse-engineering/runtime/behavior-markup.md §Shape)."""
    if not data:
        return f'<mark name="cmd:{verb}"/>'
    body = json.dumps(data, separators=(",", ":")).replace('"', "+")
    return f'<mark name="cmd:{verb},data:{body}"/>'


def _performed(text: str, mood: int, gesture: str) -> str:
    """`<playback-mood/><behaviour-tree/> text` — the redirect, with a face and a body."""
    out = [_mark("playback-mood", {"mood": int(mood), "intensity": 1})]
    if gesture:
        out.append(_mark("behaviour-tree", {
            "transition": 0.5, "duration": 1.0, "repeat": 1, "blocking": False,
            "action": 0, "eventName": gesture, "category": "BehaviourTree",
            "behaviour": "", "Track": ""}))
    out.append(text)
    return "".join(out)


# ---------------------------------------------------------------------------
# the parent review queue (storage shape — the store itself is moxie_sdk/store.py)
# ---------------------------------------------------------------------------

#: Collections in the per-robot `JsonStore`.
EVENTS_COLLECTION = "safety_events"
COUNTS_COLLECTION = "safety_counts"

#: A rolling window, not an archive.
MAX_EVENTS = 200


def event_from(verdict: InputSafety, *, keep_excerpt: bool = True,
               now: Optional[float] = None, event_id: Optional[str] = None) -> dict:
    """One review-queue row from a verdict.

    `keep_excerpt=False` (LoggingPolicy `NO_DATA`) drops the only field that carries any
    of the child's words — the row still says *that* it happened, in which category, on
    which side, and when.
    """
    import time
    import uuid
    return {
        "id": event_id or f"sfe-{uuid.uuid4().hex[:10]}",
        "ts": float(now if now is not None else time.time()),
        "side": verdict.role,
        "action": verdict.action,
        "categories": verdict.categories,
        "intents": list(verdict.intents),
        "phrase_id": verdict.phrase_id,
        "escalate": bool(verdict.escalate),
        "excerpt": verdict.excerpt if keep_excerpt else "",
        "reviewed": False,
        "reviewed_at": None,
    }


def roll_up(counts: Optional[dict], verdict: InputSafety,
            now: Optional[float] = None) -> dict:
    """Fold one verdict into the counts-only rollup (the whole record under `NO_DATA`)."""
    import time
    c = dict(counts or {})
    by_cat = dict(c.get("by_category") or {})
    by_act = dict(c.get("by_action") or {})
    by_side = dict(c.get("by_side") or {})
    for cat in verdict.categories:
        by_cat[cat] = int(by_cat.get(cat, 0)) + 1
    by_act[verdict.action] = int(by_act.get(verdict.action, 0)) + 1
    by_side[verdict.role] = int(by_side.get(verdict.role, 0)) + 1
    return {"total": int(c.get("total", 0)) + 1, "by_category": by_cat,
            "by_action": by_act, "by_side": by_side,
            "last_ts": float(now if now is not None else time.time())}


def category_labels(classifier: Optional[Classifier] = None) -> dict:
    """`{category_id: human label}` for the console, straight from the rules file."""
    c = classifier or default_classifier()
    return {cat.id: cat.label for cat in getattr(c, "categories", [])}
