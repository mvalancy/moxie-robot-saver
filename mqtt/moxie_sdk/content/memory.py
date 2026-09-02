"""
Conversation memory — turning a finished chat into a few durable facts.

`docs/architecture/content-module-contract.md` → "The volley / session API" lists two
memory calls a content module may make:

    volley.persist_data      cross-session storage (this module's namespace)
    session.summarize(...)   LLM-summarize the transcript (memory)

`persist_data` is the *storage* (`moxie_sdk/store.py::MemoryStore` — bounded, namespaced,
policy-gated JSON under the robot's data dir). This module is the *summarizer*: the
prompt that asks the brain for a short, structured, kid-safe account of a conversation,
the tolerant parse of what comes back, and the filters that decide what is allowed to be
remembered at all.

What we ask for, and why it is structured
-----------------------------------------
A free-text summary is a blob: it cannot be shown to a parent item by item, cannot be
erased selectively, and cannot be capped. So we ask for JSON::

    {"facts": [...], "preferences": [...], "open_threads": [...], "summary": "..."}

…and treat anything else the model says as a plain `summary`. Every item is a short
third-person sentence about the child ("Sam has a dog named Pepper"), never a quote.

What is never remembered
------------------------
* anything the safety classifier would block (`moxie_sdk/safety.py`) — a memory file is
  the one place an unsafe line would live *forever* and get re-injected into every later
  prompt, so a blocked item is dropped, not redacted;
* the child's own words. The prompt forbids quoting, and `strip_verbatim` enforces it:
  an "insight" that is really a long span copied out of what the child said is dropped.
  This is a floor, not a guarantee — a paraphrase can still carry something private,
  which is exactly why the parent-facing read/erase endpoints exist.

Honest limits: the model can be wrong, and a wrong fact is *sticky* — it will be fed
back into later conversations until someone erases it. Facts carry provenance (which
conversation, when, how many turns) so a parent can see where one came from.

Pattern credit: OpenMoxie (MIT) ships `content_modules/MemoryChat.json`, whose
`complete_handler` calls `session.summarize()` and accumulates facts in
`volley.persist_data`; provenance-on-every-item and the "quarantine what you cannot
attribute" instinct come from its Fork A `conversation_memory.py`
(docs/architecture/openmoxie-feature-audit.md §3.2, §4.2 BEYOND #4). The ideas are
theirs; this prompt, this JSON contract, these filters and this code are ours.
"""
from __future__ import annotations

import json
import re
import time
from typing import Optional

from ..store import item_text

# The keys we keep out of a summary, in the order a parent reads them.
LIST_KEYS = ("facts", "preferences", "open_threads")

#: Longest span of the child's own words an item may repeat before it counts as a quote.
VERBATIM_SPAN = 30

#: The default summarization instruction. Short, structured, kid-safe, no quotes.
DEFAULT_SUMMARY_PROMPT = (
    "You are the memory of a robot friend named Moxie, writing down what to remember "
    "about the child after a conversation.\n"
    "Reply with ONLY a JSON object, no prose and no code fences:\n"
    '{"facts": [], "preferences": [], "open_threads": [], "summary": ""}\n'
    "- facts: at most 5 durable, useful things about the child (family, pets, school, "
    "friends, skills, big events). Only things that will still be true next week.\n"
    "- preferences: at most 3 likes/dislikes worth remembering.\n"
    "- open_threads: at most 2 things to follow up on next time.\n"
    "- summary: one short sentence about what the conversation was about.\n"
    "Rules: write short third-person sentences about the child. Never quote the child "
    "or copy their wording. Keep everything kid-safe and neutral. Do not record "
    "anything upsetting, medical, or about anyone's address or contact details. "
    "Leave a list empty rather than inventing something."
)


# ---------------------------------------------------------------------------
# rendering — a list of facts that prints nicely inside a Jinja prompt
# ---------------------------------------------------------------------------

class FactList(list):
    """A list of remembered items that renders as bullet lines inside a prompt.

    `persist_data` must be JSON (a list, so a parent browser can show and erase one
    item) *and* readable when a module writes `{{ volley.persist_data.ns.facts }}`
    (a list's `repr` in the middle of a prompt is noise). A `list` subclass with a
    `__str__` gives both, in real Jinja2 and in `render.py`'s minimal fallback, and
    `json.dump` still writes a plain array."""

    def __str__(self) -> str:                       # pragma: no cover - trivial
        return "\n".join(f"- {item}" for item in self)


def wrap_facts(data):
    """Recursively turn stored lists of items into `FactList` for prompt rendering.

    A stored item is either a bare string (files written before ids existed) or the
    record `moxie_sdk/store.py` writes now (`{id, text, _provenance, use_count, …}`).
    Both render as the same bullet line: the prompt gets the sentence and nothing else,
    so adding ids and provenance to the file changed no prompt anywhere."""
    if isinstance(data, dict):
        return {k: wrap_facts(v) for k, v in data.items()}
    if isinstance(data, list):
        texts = [item_text(x) for x in data]
        if all(t is not None for t in texts):     # empty stays a FactList: renders blank
            return FactList(texts)
        return [wrap_facts(x) for x in data]
    return data


# ---------------------------------------------------------------------------
# the transcript we hand the brain
# ---------------------------------------------------------------------------

def build_transcript(history: list, *, limit: int = 40) -> str:
    """`[{role, content}]` → "Child: …/Moxie: …" lines (the newest `limit` messages)."""
    lines = []
    for msg in list(history or [])[-limit:]:
        if not isinstance(msg, dict):
            continue
        text = str(msg.get("content") or "").strip()
        if not text:
            continue
        who = "Moxie" if msg.get("role") == "assistant" else "Child"
        lines.append(f"{who}: {text}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# parsing what comes back
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.M)


def parse_summary(text: str) -> dict:
    """The model's answer as `{facts, preferences, open_threads, summary}`.

    Tolerant on purpose: fenced JSON, JSON with prose around it, and a plain sentence
    all produce a usable dict — a memory feature that only works when the model emits
    perfect JSON would be off most of the time. Unparseable → the whole answer becomes
    `summary`, which is still something a parent can read and erase."""
    raw = (text or "").strip()
    out = {k: [] for k in LIST_KEYS}
    out["summary"] = ""
    if not raw:
        return out
    body = _FENCE.sub("", raw).strip()
    start, end = body.find("{"), body.rfind("}")
    parsed = None
    if start >= 0 and end > start:
        try:
            parsed = json.loads(body[start:end + 1])
        except ValueError:
            parsed = None
    if not isinstance(parsed, dict):
        out["summary"] = raw[:240]
        return out
    for key in LIST_KEYS:
        value = parsed.get(key)
        if isinstance(value, str):
            value = [v.strip(" -•\t") for v in value.splitlines()]
        if isinstance(value, list):
            out[key] = [str(v).strip() for v in value if str(v).strip()]
    summary = parsed.get("summary")
    out["summary"] = str(summary).strip()[:240] if summary else ""
    return out


# ---------------------------------------------------------------------------
# what may be remembered
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower())


def strip_verbatim(items: list, history: list, *, span: int = VERBATIM_SPAN) -> list:
    """Drop items that repeat a long span of the CHILD's own words.

    The prompt already forbids quoting; this is the check. A shingle of `span`
    normalized characters shared with any child utterance is treated as a quote."""
    child = [_norm(m.get("content")) for m in (history or [])
             if isinstance(m, dict) and m.get("role") not in ("assistant", "system")]
    child = [c for c in child if len(c) >= span]
    if not child:
        return list(items)
    kept = []
    for item in items:
        norm = _norm(item)
        shingles = {norm[i:i + span] for i in range(0, max(1, len(norm) - span + 1))}
        if any(any(s in utt for s in shingles if len(s) == span) for utt in child):
            continue
        kept.append(item)
    return kept


def _safe(text: str, classifier) -> bool:
    """False when the safety classifier would BLOCK this line (never remember it)."""
    if classifier is None or not (text or "").strip():
        return True
    try:
        from .. import safety as safety_seam
        verdict = classifier.assess(text, role=safety_seam.MOXIE)
        return not (verdict and verdict.action == safety_seam.BLOCK)
    except Exception:
        return True              # a broken classifier must not silently eat memory


def default_classifier():
    """The rule classifier, or None when its table can't be loaded (never raises)."""
    try:
        from .. import safety as safety_seam
        return safety_seam.default_classifier()
    except Exception:
        return None


def filter_summary(summary: dict, *, history: list = (), classifier=None,
                   max_items: int = 5) -> dict:
    """Apply the two "never remember this" rules + the per-list cap."""
    out = {}
    for key in LIST_KEYS:
        items = [str(x).strip() for x in (summary.get(key) or []) if str(x).strip()]
        items = strip_verbatim(items, history)
        items = [x for x in items if _safe(x, classifier)]
        out[key] = items[:max_items]
    line = str(summary.get("summary") or "").strip()
    if line and (not _safe(line, classifier) or not strip_verbatim([line], history)):
        line = ""
    out["summary"] = line
    return out


def is_empty(summary: dict) -> bool:
    """True when there is nothing worth writing down."""
    return not any(summary.get(k) for k in LIST_KEYS) and not summary.get("summary")


def check_text(text: str, *, history=(), classifier=None) -> bool:
    """May this line be stored as a memory item? (the parent's edit runs through here)

    The same two rules a model's summary faces, for the same reason: whatever ends up in
    `memory.json` is read back into **every** later prompt, so the safety classifier must
    not BLOCK it, and it must not be a long span of the child's own words. A parent typing
    a correction is trusted — but a text box that writes straight into the child's future
    conversations is exactly the hole those two rules exist to close, and the second one
    also catches the innocent mistake of pasting the transcript back in.

    `classifier=None` resolves the default rule classifier; a classifier that cannot be
    loaded means "allowed", never "silently eat the parent's correction"."""
    line = str(text or "").strip()
    if not line:
        return False
    if classifier is None:
        classifier = default_classifier()
    if not _safe(line, classifier):
        return False
    return bool(strip_verbatim([line], list(history or [])))


def note_used(store, device_id: str, rendered: str) -> int:
    """Decay's clock, as one call a caller can make from the render path.

    `ContentApp` renders a module's prompt and hands the result here; the store marks the
    items whose sentence actually appears in it (`MemoryStore.note_used`). Kept as a
    module-level function so the render path stays a single line and never has to know
    whether memory is configured, or care that a broken memory file must not end a turn."""
    if store is None or not device_id:
        return 0
    try:
        return store.note_used(device_id, rendered)
    except Exception as e:                        # a use counter is never worth a turn
        print(f"[memory] note_used failed: {e}", flush=True)
        return 0


# ---------------------------------------------------------------------------
# the call
# ---------------------------------------------------------------------------

def summarize_history(history: list, chat, *, prompt_base: str | None = None,
                      append_transcript: bool = True, classifier=None,
                      max_items: int = 5, max_retries: int = 2,
                      sleep=None, history_limit: int = 40) -> Optional[dict]:
    """Ask the brain to summarize `history`; return the filtered structured summary.

    Returns **None** when the brain could not be reached (after `call_with_backoff`) or
    when it produced nothing usable — the caller then writes nothing at all, which is
    the right failure mode for memory: a missing fact is recoverable, a wrong one is not.
    """
    if chat is None:
        return None
    prompt = prompt_base or DEFAULT_SUMMARY_PROMPT
    if append_transcript:
        transcript = build_transcript(history, limit=history_limit)
        if not transcript:
            return None
        prompt = f"{prompt}\n\nTranscript:\n{transcript}"
    from ..chat import call_with_backoff
    kwargs = {"max_retries": max_retries}
    if sleep is not None:
        kwargs["sleep"] = sleep
    try:
        text = call_with_backoff(lambda: chat([{"role": "user", "content": prompt}]),
                                 **kwargs)
    except Exception as e:
        print(f"[memory] summarize failed, remembering nothing: {e}", flush=True)
        return None
    summary = filter_summary(parse_summary(text or ""), history=history,
                             classifier=classifier, max_items=max_items)
    return None if is_empty(summary) else summary


def provenance(*, module_id: str = "", content_id: str = "", turns: int = 0,
               conversation_id: str = "", reason: str = "", clock=time.time) -> dict:
    """Where a remembered thing came from — stamped on every merge.

    Fork A's rule, adopted: a memory item without a source is not trustworthy enough to
    put back into a prompt, and a parent asking "why does Moxie think that?" deserves an
    answer more specific than "it learned it somewhere"."""
    now = clock()
    return {"at": round(float(now), 3),
            "date": time.strftime("%Y-%m-%d", time.localtime(now)),
            "module_id": module_id or "", "content_id": content_id or "",
            "conversation_id": conversation_id or "", "turns": int(turns or 0),
            "reason": reason or "", "source": "session.summarize"}
