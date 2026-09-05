"""
🎴 Launch cards — turning a scanned QR string into one launch, and nothing else.

A parent prints a card, a child holds it up to Moxie's face, and the activity starts.
The paper is not built yet (P0-c); **this module is the decoder in the middle**, and it
is the only thing between a string a stranger can print and an action a robot performs.

Where the string comes from
---------------------------
The robot has two QR readers on the same camera. The *setup* one (`bo-wifi`) has a
provably closed grammar and cannot be made to launch anything
(`docs/reverse-engineering/protocol/qr-commands.md`:87-100). The *runtime* one
(`bo-android` + `libbo-analytics`, armed by `EnableQRCode{run}`) is the one that reaches
us: it surfaces as the `eb-qr-event` vision event, whose scanned string rides
`input_vars['$eb_qr_value']` (`docs/architecture/vision.md`:73-74). A subscribed vision
event is not a topic of its own — it arrives as the `speech` of an ordinary
`RemoteChatRequest`, which is why the one caller of this module is
`mqtt/supervisor/moxie_runtime.py::_on_vision_turn`.

The payload form `GO<launch:MODULE[:CONTENT]>` is upstream OpenMoxie's (MIT,
(c) Justin Beghtol — `site/data/qr/extract.py` prints the sheet, the `MoxieGo` content
module reads the value). We take the **idea and the payload form** and credit them in
`ATTRIBUTION.md`; the implementation, every refusal below and the allowlist are ours.
Their module slices the scanned payload back into a reply so a content module's own
tag-ingest turns it into an action; ours decodes to a typed `Action` in the runtime and
never round-trips a scanned string through text a child could hear.

Why the decoder *is* the action-tag grammar
-------------------------------------------
`actions.parse_action_tags` already parses `<launch:MOD[:CID]>` — it is written, tested
(`sim/tests/test_action_tags.py`) and used on every brain reply. What it had never been
pointed at is an **inbound** value. Reusing it means a card and a brain agree on what
`<launch:DRAW>` means by construction, and it means this file adds a *vocabulary*, not a
second parser.

The allowlist is a safety property, not tidiness
------------------------------------------------
**A QR code is an unauthenticated input any stranger can print and leave on a table in
front of a child.** So the catalog is a positive list: a module id that is not in it is
refused because it was never permitted, never because a pattern happened not to match.
And a card may **start an activity and do nothing else** — `<sleep>`, `<exit>` and
`<launch_if_confirmed:…>` are refused *even though the grammar parses all three*.

The catalog is **derived**, never transcribed. A hand-copied list of 23 ids rots the
moment `schedule.py` changes, and a rotted allowlist rots in the *permissive* direction —
which is the one direction that matters here. See `LAUNCHABLE_MODULE_IDS`.

What this module does NOT establish
-----------------------------------
Nothing here makes a physical robot scan anything. **No Moxie has ever sent us an
`eb-qr-event`**; the whole path is exercised by the SIL robot and the browser SIM, and
whether `eb_enable_qr` actually arms the runtime reader is still inferred rather than
observed (`docs/architecture/backlog/qr-launch-cards.md` §7 Q1-Q2). The decoder's own
behaviour, including every refusal, is provable and is proven.
"""
from __future__ import annotations

from typing import List, Optional

from . import presence as presence_seam
from . import schedule as schedule_seam
from .actions import LAUNCH_TAG, parse_action_tags, tag_names
from .types import Action, ActionType

#: Upstream's marker, and the only thing that distinguishes our card from a cereal box a
#: child happens to wave at the camera. **Literal and case-sensitive**: `go<launch:DM>`
#: is not a card, and neither is any of the unicode look-alikes for these two letters
#: (`Ｇ`, Cyrillic `О`, Greek `Ο`, …). Nothing here normalises, precisely so that a
#: homoglyph cannot be folded into the marker.
CARD_PREFIX = "GO"

#: The one tag a card may carry. Compared against `actions.tag_names`, **not** against the
#: parsed `Action.type`, because `actions.LAUNCH_IF_CONFIRMED_AS` maps
#: `<launch_if_confirmed:MOD>` onto `ActionType.LAUNCH` as well — by the time the grammar
#: hands back an `Action` the two tags are indistinguishable.
CARD_TAG = LAUNCH_TAG

#: The longest string we will look at. A QR symbol physically cannot carry more than 2953
#: bytes (version 40, byte mode, EC level L), so this is the medium's own ceiling plus
#: headroom — not a guess. It is a real guard, not hygiene: without it a card could carry
#: a five-kilobyte `content_id` through every other check on this page.
MAX_CARD_LEN = 4096

#: Ids carried outside the rotation. `ONBOARD_MODULES` is the variety catalog; `DM` (Daily
#: Missions) is a daily fixture that `schedule.py` keeps in `DEFAULT_TEMPLATE` instead
#: (`schedule.py`:118-123, and the same split in
#: `docs/architecture/mqtt-and-conversation.md`:1123-1129). Named here, but **admitted
#: only if `DEFAULT_TEMPLATE` really still schedules it** — see below.
_FIXTURE_MODULE_IDS = ("DM",)


def _scheduled_module_ids(template) -> frozenset:
    """Every `module_id` a schedule template's `provided_schedule` names.

    Total on junk: a template that is not a dict, a `provided_schedule` that is not a
    list, an entry that is not a dict or has no id, all yield nothing rather than raise.
    """
    if not isinstance(template, dict):
        return frozenset()
    rows = template.get("provided_schedule")
    if not isinstance(rows, (list, tuple)):
        return frozenset()
    return frozenset(str(r["module_id"]) for r in rows
                     if isinstance(r, dict) and r.get("module_id"))


def _catalog() -> frozenset:
    """The launchable ids, derived from `schedule.py` — never transcribed.

    Two sources, and the second one is intersected rather than trusted: `DM` is admitted
    **only while `DEFAULT_TEMPLATE` still schedules it**. That is deliberate. If a future
    edit renames or drops it, the allowlist gets *smaller* — an id stops being launchable
    and a test goes red — instead of keeping a stale id nothing else in the codebase
    recognises. An allowlist may only ever rot towards refusing.

    Read through the *module* rather than through `from … import`, so the derivation is a
    live function of `schedule.py` and a test can prove the intersection by swapping the
    template out. `LAUNCHABLE_MODULE_IDS` freezes the answer once, at import.
    """
    onboard = {str(m["module_id"]) for m in schedule_seam.ONBOARD_MODULES
               if isinstance(m, dict) and m.get("module_id")}
    scheduled = _scheduled_module_ids(schedule_seam.DEFAULT_TEMPLATE)
    return frozenset(onboard | {m for m in _FIXTURE_MODULE_IDS if m in scheduled})


#: The closed catalog: every module id a printed card is allowed to launch. 24 today —
#: the 23 in `schedule.ONBOARD_MODULES` plus `DM`.
LAUNCHABLE_MODULE_IDS = _catalog()


def is_launchable(module_id) -> bool:
    """Positive-list membership, safe on anything. The refusal in `decode` is this."""
    return isinstance(module_id, str) and module_id in LAUNCHABLE_MODULE_IDS


def encode(module_id: str, content_id: Optional[str] = None) -> str:
    """The card payload for one catalog id — the exact inverse of `decode`.

    Here rather than in the (unbuilt) sheet generator so that the printing side can never
    emit a payload the reading side refuses: an id outside the catalog raises instead of
    producing paper nothing will act on.
    """
    if not is_launchable(module_id):
        raise ValueError(f"{module_id!r} is not a launchable module id")
    tail = f":{content_id}" if content_id else ""
    return f"{CARD_PREFIX}<{CARD_TAG}:{module_id}{tail}>"


def decode(value) -> Optional[Action]:
    """One scanned string → the single launch it authorises, or `None`.

    Total: every input that is not exactly one permitted launch card returns `None`, and
    **nothing here raises** — this runs on the MQTT loop against bytes a stranger chose.

    The guards, in order, each of which some test proves is load-bearing
    (`sim/tools/launch_card_mutation_check.py`):

    1. a string, non-empty once stripped, and no longer than a QR symbol can hold;
    2. the literal `GO` marker (case-sensitive, never normalised);
    3. the tags present are **exactly one `launch`** — this is what refuses `<sleep>`,
       `<exit>`, `<launch_if_confirmed:…>` and any mixture, by NAME, before the parsed
       `Action` has a chance to make `launch_if_confirmed` look like a launch;
    4. the grammar produced exactly one action and it is a `LAUNCH` (a malformed
       `<launch>` names the right tag and yields no action);
    5. nothing is left over — a card is a tag and not a sentence, so trailing words, a
       stray NUL or smuggled `<mark …/>` markup all refuse rather than ride along;
    6. the module id is in the closed catalog.
    """
    if not isinstance(value, str) or len(value) > MAX_CARD_LEN:
        return None
    text = value.strip()
    if not text or not text.startswith(CARD_PREFIX):
        return None
    remainder = text[len(CARD_PREFIX):]
    names: List[str] = tag_names(remainder)
    if set(names) != {CARD_TAG}:
        return None
    residue, actions = parse_action_tags(remainder)
    if len(actions) != 1 or actions[0].type is not ActionType.LAUNCH:
        return None
    if residue:
        return None
    action = actions[0]
    if not is_launchable(action.module_id):
        return None
    return Action(type=ActionType.LAUNCH, module_id=action.module_id,
                  content_id=action.content_id)


def decode_event(event_name, input_vars) -> Optional[Action]:
    """The runtime's entry point: a vision event's name + `input_vars` → a launch or None.

    Only `eb-qr-event` can carry a card. The other two marker events reach us through the
    identical shape — `eb-dr-event` carries an ArUco id, `eb-br-event` a book cover
    (`presence.VALUE_KEYS`) — and a value that happens to read as a card on one of those
    is still not a card. Extraction is `presence.value_of`, so the `$`-prefixed spelling
    and the bare one the module API warns about are both accepted, in one place.
    """
    name = event_name.strip() if isinstance(event_name, str) else ""
    if name != presence_seam.QR_EVENT:
        return None
    return decode(presence_seam.value_of(input_vars, name))
