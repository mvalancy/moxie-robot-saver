"""
Action tags — giving the model agency over the robot.

A brain replies with text. This module lets that text *also* drive the robot: the
model may write a small tag inline (`<exit>`, `<sleep>`, `<launch:MOD>`,
`<launch:MOD:CID>`, `<launch_if_confirmed:MOD[:CID]>`) and we turn it into a real
`Action` on the `Reply`, which the runtime already puts on the wire as
`response_actions` (see `wire.py::build_chat_response` and
docs/architecture/ai-seam.md §2 "RemoteChatAction — the brain drives navigation").
The tag itself is stripped, so nothing leaks into what Moxie speaks.

Pattern from OpenMoxie (MIT) — `site/hive/mqtt/volley.py::ingest_action_tags`. The
idea is theirs; this implementation, its grammar rules and its tolerance policy are
ours. Credited in ../../ATTRIBUTION.md and docs/architecture/openmoxie-feature-audit.md
§4.1 row 4.

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
* A tag whose **name is one of ours** is always removed from the spoken text, even
  when its arguments are malformed (`<exit:now>`, `<launch>`, `<launch::x>`). A
  child should never hear "less-than launch greater-than"; a malformed tag simply
  produces no action.
* A tag whose **name is not one of ours** is left alone — text and all. That is
  deliberate: the robot's own behavior markup is `<mark .../>` and content openers
  use `<opener>`, so a blanket "strip every `<...>`" would eat live syntax. We only
  claim the four names we define.
* Unrecognised trailing fields make a launch malformed (`<launch:A:B:C>` → no
  action) rather than being silently truncated — a wrong module is worse than none.

Contract caveat — `launch_if_confirmed`
---------------------------------------
Our recovered contract *does* define `RemoteChatAction.ActionID.launch_if_confirmed`
(= 2; docs/reverse-engineering/protocol/proto-catalog.md, ai-seam.md §2), but our
`ActionType` enum has no confirm variant yet, so we map the tag to
`ActionType.LAUNCH`. **This is lossy**: the robot launches immediately instead of
asking the child to confirm first. We do not invent a wire value the enum does not
define. The mapping lives in `LAUNCH_IF_CONFIRMED_AS` below — the day `ActionType`
gains a confirm member, that one line is the whole fix. Tracked in
docs/architecture/implementation-plan.md (Known gaps → ai-seam).
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
# swallowing the next one when the model writes two in a row.
_TAG_RE = re.compile(r"<\s*([A-Za-z_][A-Za-z0-9_]*)\s*((?::[^<>]*?)?)\s*>")

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


# The paragraph we show the model so it actually uses the tags. Kept short, kid-safe,
# and explicit that tags are silent — a model that explains the tag out loud is worse
# than one that never uses it.
ACTION_TAG_PROMPT = (
    "You can control the robot with tags. Write a tag on its own inside your spoken "
    "line and it is removed before anyone hears it — never say the tag out loud, never "
    "mention tags to the child, and never use more than one per reply.\n"
    "  <exit> - use when the child says goodbye, is done, or asks to stop.\n"
    "  <sleep> - use only if the child asks you to go to sleep.\n"
    "  <launch:MODULE> or <launch:MODULE:CONTENT> - start an activity, and ONLY with "
    "a module name you have actually been told about in this conversation.\n"
    "If none of these apply, just talk normally and use no tag at all."
)
