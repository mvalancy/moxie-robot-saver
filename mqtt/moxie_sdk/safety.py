"""
Child safety — the `InputSafety` contract, enforced.

`RemoteChatInput.InputSafety{is_unsafe, blocked_by[], intents[], phrase_id}` is the one
moderation hook the recovered protocol gives us (`RemoteChat.proto`:180-186; carried in
`RemoteChatInput.safety` field 12 and `RemoteChatResponse.input` field 17).
`docs/architecture/ai-seam.md` §2 says a kid-facing backend SHOULD populate it. The
runtime enforces this classifier's verdict on **both** sides of a turn:

  * **pre-inference** — the child's utterance is assessed BEFORE the brain is called, so a
    hard-blocked turn never reaches a model;
  * **post-inference** — every chunk is assessed BEFORE it is published (streaming puts a
    sentence on the wire before the rest exists).

**v1** is a transparent rule engine — word lists, phrase regexes and false-positive guards
in `safety_rules.json`, a file a parent can read — running locally with no model.

**It is a floor, not a filter**: it misses context, sarcasm, gentle phrasings and other
languages, and occasionally flags something innocent. It sits under the model's own
alignment and the persona's instructions, and is no substitute for a parent.

**The seam.** `Classifier` has one method, `assess(text, role) -> InputSafety`, like
`Transcriber`/`Synthesizer`; a model classifier drops in via
`MoxieRuntime(app, safety=MyClassifier())`.

Credit: pre-inference keyword flags plus a parent review queue is OpenMoxie Fork A's idea
(`site/hive/mqtt/conversation_log.py`; `openmoxie-feature-audit.md` §2.1, BEYOND #2).
The categories, role-aware policy, per-chunk post-inference stage, wire mapping and code
are ours.
"""
from __future__ import annotations

import json
import os
import random
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from . import vocab

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

    The first four fields are the wire contract (RemoteChat.proto:181-186); `to_wire()`
    emits only those. `is_unsafe` is true exactly when something **blocked** (so
    `blocked_by` is non-empty); flagged-only text passes and is recorded in `flagged_by`,
    which never reaches the robot.
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

# Always-on cleanup: curly apostrophes onto `'`, so `don't`/`don’t` are one word.
_ALWAYS = str.maketrans({"’": "'", "‘": "'", "ʼ": "'"})

#: The four glyphless Hangul fillers: category `Lo` (so the `Cf` sweep misses them) but
#: invisible, so they split a word like a ZWSP. (NFKD folds two of them onto U+1160 first;
#: all four are listed for clarity.)
_HANGUL_FILLERS = frozenset("\u115f\u1160\u3164\uffa0")


def _is_invisible(ch: str) -> bool:
    r"""True for a character that occupies no width and so can split a word unseen.

    By **category**, not a hand-picked list: a list of four zero-width code points let a
    U+00AD SOFT HYPHEN or U+2060 WORD JOINER between letters defeat the pre-inference
    block. `Cf` is the closed set of formatting characters (matches V8, incl. U+180E).
    Marks are tested by category (`M*`), not `unicodedata.combining()`: U+034F is `Mn`
    with combining class 0 and must go too — this is what `\p{M}` means in the JS twin
    (`functions/api/_lib/safety.js`).

    `Zs` spaces are deliberately kept: NFKD folds them onto U+0020, so an exotic space
    becomes a real one (intra-letter spacing stays a known limit; see `_variants`).
    """
    cat = unicodedata.category(ch)
    return cat == "Cf" or cat[0] == "M" or ch in _HANGUL_FILLERS

# Leet substitutions (`sh1t`, `$hit`, `f@ck`), applied ONLY before a letter so `shoot!`
# does not become `shooti` and break a match.
_LEET = {"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b",
         "9": "g", "@": "a", "$": "s", "!": "i", "|": "i", "+": "t"}
_LEET_RE = re.compile("[%s]" % re.escape("".join(_LEET)))

_RUN = re.compile(r"(.)\1{2,}")           # three or more of the same character

#: Non-alphanumerics with a letter/digit on **both** sides, so `s.u.i.c.i.d.e` folds onto
#: the word. Lookahead on the right flank so consecutive separators all fold.
_INWORD_PUNCT = re.compile(r"([a-z0-9])[^a-z0-9 ]+(?=[a-z0-9])")


def normalize(text: str) -> str:
    r"""Casefolded, accent-stripped, de-leeted text with whitespace runs collapsed.

    NFKD + dropping marks folds `shít` / `ｓｈｉｔ` onto `shit`; the leet map folds `sh1t`;
    invisible characters are deleted. Punctuation stays so word boundaries hold
    (intra-word separators are folded in `_variants`). A MATCHING transform only — it
    never reaches a child, log or prompt; a parent's excerpt masks the ORIGINAL text.
    """
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", str(text))
    t = "".join(c for c in t if not _is_invisible(c))
    t = t.casefold().translate(_ALWAYS)

    def _sub(m):
        nxt = t[m.end():m.end() + 1]
        return _LEET[m.group(0)] if nxt.isalpha() else m.group(0)

    t = _LEET_RE.sub(_sub, t)
    return re.sub(r"\s+", " ", t).strip()


def _variants(text: str) -> tuple:
    r"""Normalized text plus its de-elongated and de-punctuated forms.

    Runs of 3+ identical characters collapse to one AND two (`fuuuuck`, `killlll`); the
    fourth form folds intra-word separators (`s.u.i.c.i.d.e`).

    Why the narrow `_INWORD_PUNCT` and not stripping all punctuation: that also deletes
    sentence boundaries, and on the innocent corpus (`test_safety.py::INNOCENT`) turns
    "that's what i want. To die of laughter…" into a `self_harm` block. Requiring
    alphanumerics on both sides keeps `want. To` apart: zero false positives.

    Forms are OR-ed, so adding one can only add matches — the false-positive corpus is
    therefore the gate. Known open: intra-letter *spacing* (`s u i c i d e`), a visible
    evasion this floor cannot catch without deleting every space.
    """
    base = normalize(text)
    if not base:
        return ("",)
    one = _RUN.sub(r"\1", base)
    two = _RUN.sub(r"\1\1", base)
    punct = _INWORD_PUNCT.sub(r"\1", base)
    seen, out = set(), []
    for v in (base, one, two, punct):
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
                 "words", "phrases", "allow", "allow_moxie", "phrases_moxie")

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
        # Her side only. A guard for a refusal that quotes the request, a warning, an
        # idiom or a story character is right where SHE speaks and wrong on the child's
        # side, where it would let a second, harmful clause through ("my grades fell and
        # now i want to hurt myself"); and some words a child may say she never does.
        self.allow_moxie = [re.compile(p) for p in (raw.get("allow_moxie") or [])]
        self.phrases_moxie = [re.compile(p) for p in (raw.get("phrases_moxie") or [])]

    def hits(self, variants: tuple, role: str = CHILD, *, her_guards: bool = True) -> list:
        """The matched trigger strings in `variants`, or [] — allow-guarded: each guard's
        span is removed first (`shoot a photo` passes, `… then shoot him` still hits).

        The child's side matches `words` + `phrases` under `allow`. Her side (`MOXIE`)
        also matches `phrases_moxie`, under `allow` + `allow_moxie` — or under `allow`
        alone when `her_guards` is false, for a caller judging a fact about the child in
        her voice (the content brain's memory filter) rather than her own line."""
        hers = role == MOXIE
        guards = self.allow + self.allow_moxie if hers and her_guards else self.allow
        phrases = self.phrases + self.phrases_moxie if hers else self.phrases
        found = []
        for text in variants:
            guarded = text
            for a in guards:
                guarded = a.sub(" ", guarded)
            if self.words is not None:
                found += [m.group(0) for m in self.words.finditer(guarded)]
            for p in phrases:
                found += [m.group(0) for m in p.finditer(guarded)]
            if found:
                break                       # one variant matching is enough
        return found


# ---------------------------------------------------------------------------
# the classifier seam
# ---------------------------------------------------------------------------

class Classifier:
    """The safety seam — one method, like `Transcriber` / `Synthesizer`::

        class MyClassifier(Classifier):
            name = "distil-safety"
            def assess(self, text, *, role=CHILD): ...
        MoxieRuntime(app, safety=MyClassifier())

    Contract: **pure and local** (no network), fast enough per streamed chunk, and total
    (the runtime treats an exception as "allow" so a broken classifier cannot silence Moxie).

    `her_guards=False` is an OPTIONAL keyword a classifier MAY take (`RuleClassifier`
    does): judge `role=MOXIE` text without her-side-only guards, for a caller weighing a
    fact about the child rather than her own line (the content brain's memory filter). A
    caller that passes it falls back to the plain call on `TypeError`.
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
    def assess(self, text: str, *, role: str = CHILD,
               her_guards: bool = True) -> InputSafety:
        """The verdict for `text` on one side. `her_guards=False` (her side only) leaves
        `allow_moxie` out, so a story or idiom guard excuses nothing: see `Classifier`."""
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
            hits = cat.hits(variants, role, her_guards=her_guards)
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
        safety-phrase id" (remote-chat-protocol.md:88-89).
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
    """A short review-queue excerpt with the matched words masked — enough to recognize
    the moment, never the unsafe words. Masks the ORIGINAL text; cut at `limit` on a word
    boundary."""
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

def _performed(text: str, mood: int, gesture: str) -> str:
    """`<playback-mood/><behaviour-tree/> text` — the redirect, performed. Not run through
    the markup floor (it must stay as written); marks come from `vocab`."""
    out = [vocab.mood_mark(int(mood), 1)]
    if gesture:
        out.append(vocab.tree_mark(gesture))
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
    """One review-queue row from a verdict. `keep_excerpt=False` (`NO_DATA`) drops the
    only field carrying the child's words; the row still records what/when/which side."""
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
