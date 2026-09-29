"""
🎴 Launch cards — turning a scanned QR string into one launch, and nothing else.

A child holds a printed card up to Moxie; this decoder is the only thing between a
string a stranger can print and an action the robot performs.

**Source.** The runtime QR reader (armed by `EnableQRCode{run}`) surfaces as the
`eb-qr-event` vision event with the string in `input_vars['$eb_qr_value']`
(vision.md:73-74), arriving as the `speech` of a `RemoteChatRequest` — hence the one
caller, the runtime's `_on_vision_turn`. (The setup reader has a closed grammar and cannot
launch anything: protocol/qr-commands.md:24-43.)

**Payload** `GO<launch:MODULE[:CONTENT]>` is OpenMoxie's form (MIT, see ATTRIBUTION.md);
the implementation, refusals and allowlist are ours. Decoding reuses
`actions.parse_action_tags`, so a card and a brain agree on `<launch:DRAW>` by
construction, and a scanned string is never round-tripped through speech.

**Allowlist.** A QR code is unauthenticated input, so the catalog is a positive list and
a card may only *start an activity*: `<sleep>`, `<exit>` and `<launch_if_confirmed:…>`
are refused even though they parse. The catalog is derived from `schedule.py`, never
transcribed, so it can only rot toward refusing.

No physical Moxie has sent us an `eb-qr-event`; whether `eb_enable_qr` arms the reader
is inferred (backlog/qr-launch-cards.md §7 Q1-Q2).
"""
from __future__ import annotations

from typing import List, Optional

from . import presence as presence_seam
from . import schedule as schedule_seam
from .actions import LAUNCH_TAG, parse_action_tags, tag_names
from .types import Action, ActionType

#: The card marker. Literal and case-sensitive, never normalised, so no homoglyph or
#: lowercase variant can pass as a card.
CARD_PREFIX = "GO"

#: The one tag a card may carry — checked by tag NAME, because `launch_if_confirmed`
#: also parses to `ActionType.LAUNCH`.
CARD_TAG = LAUNCH_TAG

#: Longest string we look at: a QR symbol holds at most 2953 bytes, plus headroom.
MAX_CARD_LEN = 4096

#: Daily fixtures outside `ONBOARD_MODULES`, admitted only while `DEFAULT_TEMPLATE`
#: still schedules them.
_FIXTURE_MODULE_IDS = ("DM",)


def _scheduled_module_ids(template) -> frozenset:
    """Every `module_id` a template's `provided_schedule` names (junk yields nothing)."""
    if not isinstance(template, dict):
        return frozenset()
    rows = template.get("provided_schedule")
    if not isinstance(rows, (list, tuple)):
        return frozenset()
    return frozenset(str(r["module_id"]) for r in rows
                     if isinstance(r, dict) and r.get("module_id"))


def _catalog() -> frozenset:
    """The launchable ids, derived from `schedule.py`: `ONBOARD_MODULES` plus fixtures
    intersected with `DEFAULT_TEMPLATE` (so a dropped fixture shrinks the list). Read via
    the module so a test can swap the template."""
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

    An id outside the catalog raises. Its browser twin, `moxieQR.encodeCard` in
    `sim/web/qr.js`, is pinned byte for byte by `sim/test_qr.mjs`.
    """
    if not is_launchable(module_id):
        raise ValueError(f"{module_id!r} is not a launchable module id")
    tail = f":{content_id}" if content_id else ""
    return f"{CARD_PREFIX}<{CARD_TAG}:{module_id}{tail}>"


def decode(value) -> Optional[Action]:
    """One scanned string → the single launch it authorises, or `None`.

    Total and never raises (runs on the MQTT loop). Guards, each proven load-bearing by
    `sim/tools/launch_card_mutation_check.py`:

    1. a non-empty string no longer than `MAX_CARD_LEN`;
    2. the literal `GO` marker;
    3. the tags are exactly one `launch` (by name);
    4. the grammar yields exactly one `LAUNCH` action;
    5. no residue (trailing words or markup refuse);
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

    Only `eb-qr-event` can carry a card (a card-shaped ArUco/book value is not one).
    """
    name = event_name.strip() if isinstance(event_name, str) else ""
    if name != presence_seam.QR_EVENT:
        return None
    return decode(presence_seam.value_of(input_vars, name))
