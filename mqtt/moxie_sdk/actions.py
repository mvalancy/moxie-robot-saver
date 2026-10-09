"""
Action tags — giving the model agency over the robot.

A brain replies with text. This module lets that text *also* drive the robot: the
model may write a small tag inline (`<exit>`, `<sleep>`, `<launch:MOD>`,
`<launch:MOD:CID>`, `<launch_if_confirmed:MOD[:CID]>`) and we turn it into a real
`Action` on the `Reply`, which the runtime already puts on the wire as
`response_actions` (see `wire.py::build_chat_response` and
docs/architecture/ai-seam.md §2 "RemoteChatAction — the brain drives navigation").
The tag itself is stripped, so nothing leaks into what Moxie speaks.

Pattern from OpenMoxie (MIT, `volley.py::ingest_action_tags`, see ATTRIBUTION.md); this
implementation and its rules are ours.

Grammar (see `parse_action_tags` for the exact rules)
----------------------------------------------------
    <exit>                              end the current module
    <sleep>                             go to sleep
    <launch:MODULE>                     start a module
    <launch:MODULE:CONTENT>             start a module at a content id
    <launch_if_confirmed:MODULE[:CONTENT]>   propose a launch (see caveat below)

Tag *names* are case-insensitive (`<EXIT>` works); module/content ids keep their
case, because the robot's ids are case-sensitive (`DRAW`, not `draw`). Whitespace
around the name and around each `:`-separated field is tolerated.

Tolerance policy (decided here, tested in sim/tests/test_action_tags.py)
-----------------------------------------------------------------------
* A tag with one of our names is always stripped, even when malformed (then it yields
  no action) — a child must never hear it.
* Any other `<...>` is left alone (`<mark/>` markup and `<opener>` are live syntax).
* Extra fields make a launch malformed (`<launch:A:B:C>` → no action) rather than
  truncated — a wrong module is worse than none.

Contract caveat — `launch_if_confirmed`
---------------------------------------
The contract defines `ActionID.launch_if_confirmed` (= 2, proto-catalog.md), but
`ActionType` has no confirm member yet, so the tag maps to LAUNCH — lossy (no
confirmation). `LAUNCH_IF_CONFIRMED_AS` is the one-line fix.
"""
from __future__ import annotations
import re
from typing import List, Tuple

from .types import Action, ActionType

# The four tag names we claim. Anything else in angle brackets is not ours.
EXIT_TAG = "exit"
SLEEP_TAG = "sleep"
LAUNCH_TAG = "launch"
LAUNCH_IF_CONFIRMED_TAG = "launch_if_confirmed"
KNOWN_TAGS = (EXIT_TAG, SLEEP_TAG, LAUNCH_TAG, LAUNCH_IF_CONFIRMED_TAG)

# See "Contract caveat" above: no confirm variant in ActionType yet → plain LAUNCH.
LAUNCH_IF_CONFIRMED_AS = ActionType.LAUNCH

# <name> or <name:field:field>, tolerant of whitespace. `[^<>]` keeps a tag from
# swallowing the next one when the model writes two in a row. No two neighbouring repeats
# can take the same character (the fields are greedy and run up to the `>` itself, so the
# spaces before it are theirs and `_fields` strips them), which keeps every match linear in
# the text: with lazy fields followed by `\s*>`, a run of spaces after `<exit:` or `<exit`
# with no `>` after it was scanned again for each of its characters (0.35 s at 16,000
# spaces, about four times longer per doubling, still running after 8 s at a megabyte,
# measured through the extension host, which reads every string a pack writes).
_TAG_RE = re.compile(r"<\s*([A-Za-z_][A-Za-z0-9_]*)\s*((?::[^<>]*)?)>")

_HSPACE_RE = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"[ \t]+([,.!?;])")
_TRAILING_WS_RE = re.compile(r"[ \t]+$", re.M)
_BLANK_RUN_RE = re.compile(r"\n{3,}")


def _fields(raw_args: str) -> List[str]:
    """`':DRAW: default '` → `['DRAW', 'default']`; trailing empties dropped."""
    if not raw_args:
        return []
    parts = [p.strip() for p in raw_args[1:].split(":")]
    while parts and parts[-1] == "":
        parts.pop()
    return parts


def _action_for(name: str, fields: List[str]):
    """One parsed tag → an `Action`, or None when the tag is malformed."""
    if name in (EXIT_TAG, SLEEP_TAG):
        if fields:                                  # <exit:now> — we define no args
            return None
        return Action(type=ActionType.EXIT if name == EXIT_TAG else ActionType.SLEEP)
    # launch / launch_if_confirmed: MODULE, optional CONTENT
    if not fields or not fields[0] or len(fields) > 2:
        return None
    kind = ActionType.LAUNCH if name == LAUNCH_TAG else LAUNCH_IF_CONFIRMED_AS
    return Action(type=kind, module_id=fields[0],
                  content_id=fields[1] if len(fields) == 2 else None)


def tidy_spoken_text(text: str) -> str:
    """Close the gaps a removed tag leaves behind, without reflowing real content."""
    text = _HSPACE_RE.sub(" ", text)
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    text = _TRAILING_WS_RE.sub("", text)
    text = _BLANK_RUN_RE.sub("\n\n", text)
    return text.strip()


def parse_action_tags(text: str) -> Tuple[str, List[Action]]:
    """Split a model's line into what Moxie *says* and what Moxie *does*.

    Returns `(clean_text, actions)`. `actions` is in the order the tags appeared.
    Every tag we recognise by name is removed from `clean_text` (malformed ones
    included — they just yield no action); tags we do not own are left in place.
    Pure and side-effect free: safe to call on any text, tagged or not.
    """
    if not text:
        return "", []
    actions: List[Action] = []

    def _sub(m: re.Match) -> str:
        name = m.group(1).lower()
        if name not in KNOWN_TAGS:
            return m.group(0)                       # not ours — leave it alone
        action = _action_for(name, _fields(m.group(2)))
        if action is not None:
            actions.append(action)
        return ""

    return tidy_spoken_text(_TAG_RE.sub(_sub, text)), actions


def lift_action_tags(text: str) -> str:
    """`text` with every tag that has one of our names lifted out in one pass, malformed
    ones too: what `parse_action_tags` speaks before `tidy_spoken_text`, with no action
    read. Linear in `text`. The sandboxed-extension host clears a line's markup with it
    (`ext_host.robot_markup`)."""
    return _TAG_RE.sub(lambda m: "" if m.group(1).lower() in KNOWN_TAGS else m.group(0),
                       text or "")


def _lift_known(text: str) -> Tuple[str, List[int]]:
    """`text` as `parse_action_tags` would speak it, every tag with one of our names lifted
    in one pass (malformed ones too), and for each character kept its index in `text`."""
    out: List[str] = []
    origin: List[int] = []
    pos = 0
    for m in _TAG_RE.finditer(text):
        if m.group(1).lower() not in KNOWN_TAGS:
            continue
        out.append(text[pos:m.start()])
        origin.extend(range(pos, m.start()))
        pos = m.end()
    out.append(text[pos:])
    origin.extend(range(pos, len(text)))
    return "".join(out), origin


def drop_action_tags(text: str, keep) -> Tuple[str, List[Action]]:
    """`text` with every action tag whose action `keep(action)` refuses taken out, and
    those actions in the order they appeared.

    A kept tag, a malformed one and a tag that is not ours stay in the text exactly as
    written, so `parse_action_tags` reads what is left as it always did. Taking a tag out
    can make the pieces around it meet (`<ex<sleep>it>` loses its sleep and reads `<exit>`),
    and so can the parse itself, which lifts every tag of ours in one pass (`<ex<sleep>it>`
    with its sleep kept would be *spoken* as `<exit>`, and never acted on): so the pass
    repeats until nothing more comes out and nothing of ours is left in what would be
    spoken. A tag that forms only once the parse has lifted the tags around it is cut out
    with the pieces it was made of, whatever `keep` says of it (the robot path would never
    act on it), and is in the result only when `keep` refuses it. Whatever the text then
    parses to, `keep` allowed, and what is spoken holds no tag of ours. Each pass takes at
    least one character out, so the passes are bounded by the text, and the cost by its
    square: the worst 1,000-character line (165 nested `<ex … it>` around a malformed
    tag) takes 8-16 ms, and a turn can carry four such lines, each filtered on its own,
    45-68 ms a turn through the host, measured on two runs, one under other load. The
    sandboxed-extension host uses it to let a pack's line act only on the tags written
    whole in the pack's own text (`ext_host.apply_ext_effects`); a line's markup, where
    nothing is kept, is cleared in linear time instead (`ext_host.robot_markup`).
    """
    dropped: List[Action] = []
    while text:
        found: List[Action] = []

        def _sub(m: re.Match) -> str:
            name = m.group(1).lower()
            if name not in KNOWN_TAGS:
                return m.group(0)                   # not ours — leave it alone
            action = _action_for(name, _fields(m.group(2)))
            if action is None or keep(action):
                return m.group(0)
            found.append(action)
            return ""

        text = _TAG_RE.sub(_sub, text)
        if found:
            dropped += found
            continue
        # What the robot would speak once the tags that stay are lifted: a tag of ours that
        # only forms there would be said aloud, so it goes, with the characters it is made
        # of (the tags inside it stay).
        spoken, origin = _lift_known(text)
        cut: set = set()
        for m in _TAG_RE.finditer(spoken):
            name = m.group(1).lower()
            if name not in KNOWN_TAGS:
                continue
            cut.update(origin[m.start():m.end()])
            action = _action_for(name, _fields(m.group(2)))
            if action is not None and not keep(action):
                found.append(action)
        if not cut:
            break
        dropped += found
        text = "".join(c for i, c in enumerate(text) if i not in cut)
    return text, dropped


def tag_names(text: str) -> List[str]:
    """The names of the tags we recognise in `text`, lowercased, in the order they appear.

    Needed because `launch_if_confirmed` parses to the same LAUNCH as `launch`
    (`launch_cards.decode` must tell them apart). Malformed tags are listed too.
    """
    return [m.group(1).lower() for m in _TAG_RE.finditer(text or "")
            if m.group(1).lower() in KNOWN_TAGS]


# The paragraph that teaches the model the tags; explicit that tags are silent. Built one
# line per tag, so a brain that may use only some of them (`LEAVE_TAG_PROMPT`) states each
# rule in the same words.
_TAG_INTRO = (
    "You can control the robot with tags. Write a tag on its own inside your spoken "
    "line and it is removed before anyone hears it — never say the tag out loud, never "
    "mention tags to the child, and never use more than one per reply.\n")
EXIT_RULE = "  <exit> - use when the child says goodbye, is done, or asks to stop.\n"
SLEEP_RULE = "  <sleep> - use only if the child asks you to go to sleep.\n"
_LAUNCH_RULE = (
    "  <launch:MODULE> or <launch:MODULE:CONTENT> - start an activity, and ONLY with "
    "a module name you have actually been told about in this conversation.\n")
_TAG_OUTRO = "If none of these apply, just talk normally and use no tag at all."

ACTION_TAG_PROMPT = _TAG_INTRO + EXIT_RULE + SLEEP_RULE + _LAUNCH_RULE + _TAG_OUTRO

#: The goodbye rule as an obligation, shared by both brains (llm_app.py lists it among its
#: REQUIRED tags).
EXIT_REQUIRED_RULE = ("  * Child says goodbye / is done / asks to stop -> your reply MUST "
                      "begin with <exit>.\n")

#: The tags for a brain that is never told any module ids (the content brain): `<exit>` and
#: `<sleep>` only, because a `<launch:…>` it made up would reach the robot unchecked.
#: Appended after the module's prompt, it is also what DEFAULT_PERSONA's "tags described
#: below" refers to. A module prompt that asks for a follow-up question is overruled for
#: the one reply that should not have one.
LEAVE_TAG_PROMPT = (
    "--- Robot controls ---\n" + _TAG_INTRO + EXIT_RULE + SLEEP_RULE + _TAG_OUTRO +
    "\nThe goodbye tag is REQUIRED when it applies, not optional:\n" + EXIT_REQUIRED_RULE +
    "Write the tag first, then a short warm goodbye. A goodbye asks no question."
)
