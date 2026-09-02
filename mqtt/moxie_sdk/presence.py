"""
Presence — Moxie's own eyes, folded into a small state machine the brain can read.

The robot runs its vision **on-device** and never sends pixels; what it can send is a
handful of semantic event strings (docs/architecture/vision.md:19-21, :63-88). Until now
nobody consumed them — not OpenMoxie, not us (openmoxie-feature-audit.md:497). This
module is the pure half of "a Moxie that notices you walked in": events in, a bounded
per-robot state + a list of **derived signals** out. No I/O, no clock of its own (the
caller passes `now`), no transport — so it unit-tests exactly, and the runtime keeps all
the policy (`mqtt/supervisor/moxie_runtime.py`).

## The events we ingest (recovered catalog — INFERRED, never observed)

| event | carries | source |
|---|---|---|
| `eb-found-face` | nothing but the fact | vision.md:47, RemoteModuleAPI "Events" |
| `eb-lost-target` (a.k.a. `eb-lost-face`) | nothing but the fact | vision.md:48 |
| `eb-qr-event` | `input_vars['$eb_qr_value']` | vision.md:56 |
| `eb-dr-event` | `input_vars['$eb_dr_value']` — an ArUco id | vision.md:57 |
| `eb-br-event` | `input_vars['$eb_br_value']` — a Moxie book | vision.md:58 |

**Granularity is the whole story**: these carry *found/lost only* — "**No bounding box, no
(x,y) position, no distance, no face embedding/identity is delivered to the module/cloud**"
(vision.md:51-53). So the richest thing a server can build from them is *presence*: is
someone there, since when, and how long were they gone. That is what this module models.

**How they arrive.** They are not a separate topic: a subscribed event is delivered to the
brain **as the `speech` of an ordinary `RemoteChatRequest`** — "instead of the modules
receiving something the user said, it receives a special event string like
`eb-found-face`" (OpenMoxie `doc/RemoteModuleAPI.md` §Event Handling, MIT; the same shape
our `docs/reverse-engineering/runtime/content-and-conversation.md`:385-390 shows for QR).
A brain only receives them after it *subscribes*, via
`RemoteChatAction.EventSubscription{clear, active[], passive[]}`
(remote-chat-protocol.md:103-106, ai-seam.md §2(b)). The runtime does both.

**Honesty.** No physical robot has ever sent us one of these. Everything here is built
from the recovered catalog and the module API doc; the payload *keys* are cited, the
*timing* (how fast a real robot flickers found/lost) is guesswork, which is exactly why
the hysteresis constants below are knobs rather than magic numbers.

## The model

    absent ──eb-found-face──▶ present        (signal: arrived, away_s)
    present ──eb-lost-target──▶ absent       (signal: left, present_s)

with two hysteresis rules so a face that flickers at the edge of the frame cannot spam
the brain (and cannot trigger a greeting per blink):

* a `found` less than `FLICKER_S` after the matching `lost` is a **flicker**, not an
  arrival: the present-run clock is *not* restarted and no `arrived` is emitted;
* a `lost` that ends a present-run shorter than `MIN_PRESENT_S` is a **flicker** too, not
  a departure — the state still goes absent (the face really is gone), but no `left`;
* and a departure is announced **once per presence**: after a `left`, only a fresh
  `arrived` re-arms it, so a face blinking at the edge of the frame produces one `left`
  and one `arrived` no matter how many times the tracker changes its mind.
"""
from __future__ import annotations

import os

# --- the recovered event vocabulary (vision.md §1.1-1.2) --------------------------
FOUND_FACE = "eb-found-face"
LOST_TARGET = "eb-lost-target"
LOST_FACE = "eb-lost-face"          # the alias RemoteModuleAPI lists (vision.md:48)
QR_EVENT = "eb-qr-event"
MARKER_EVENT = "eb-dr-event"        # ArUco fiducial
BOOK_EVENT = "eb-br-event"          # a Moxie book cover

#: Every vision event this module understands — and exactly what the runtime asks the
#: robot to push us via `EventSubscription.active[]`.
VISION_EVENTS = (FOUND_FACE, LOST_TARGET, LOST_FACE, QR_EVENT, MARKER_EVENT, BOOK_EVENT)

#: `input_vars` key per marker event. RemoteModuleAPI's own "Minor note: Some variable
#: names have a leading $ and some do not" is why both spellings are accepted.
VALUE_KEYS = {QR_EVENT: "$eb_qr_value",
              MARKER_EVENT: "$eb_dr_value",
              BOOK_EVENT: "$eb_br_value"}

#: The execute-action that aims the face search at "someone close enough": floats as a
#: proportion of the image view, `["0.15","0","0","true","true"]` = fire at >=15% of frame
#: width (vision.md:40-45). Exposed as a constant so the runtime never re-types it.
CUSTOM_FACE_SEARCH = "eb_custom_face_search"
BINNED_FACE_SEARCH = "eb_start_binned_face_search"
CLOSE_ENOUGH_ARGS = ["0.15", "0", "0", "true", "true"]


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


#: A face re-found within this many seconds is the SAME person flickering, not an
#: arrival. Nothing in the recovered corpus tells us how twitchy the on-device tracker
#: is (it already waits "for an extended period" before it calls a target lost —
#: vision.md:48), so this is a knob, defaulted conservatively.
FLICKER_S = _env_float("MOXIE_PRESENCE_FLICKER_S", 3.0)

#: A present-run shorter than this ends without a `left` — a one-frame false positive
#: should not read as "they walked out".
MIN_PRESENT_S = _env_float("MOXIE_PRESENCE_MIN_PRESENT_S", 2.0)

#: The rolling event log is a window, not an archive (same rule as the runtime's mentor
#: behaviors). Bounded so a robot left running for a week cannot grow this without limit.
HISTORY_MAX = 20

#: How recently an arrival still counts as "just now" in the prompt line.
JUST_ARRIVED_S = 60.0
#: How long nobody may be visible before the prompt line mentions it.
LONG_ABSENCE_S = 120.0


def new_state() -> dict:
    """A robot that has told us nothing yet. `face_present=None` means *unknown* — the
    robot may simply not have vision subscribed — which is deliberately different from
    `False` ("we were told the face went away")."""
    return {"face_present": None,     # None = never heard from, True/False = told
            "last_seen_at": None,     # last eb-found-face
            "last_lost_at": None,     # last eb-lost-target/eb-lost-face
            "present_since": None,    # start of the current present run
            "absent_since": None,     # start of the current absent run
            "faces_seen": 0,          # arrivals (flickers excluded)
            "arrival_away_s": None,   # how long they were gone before the last arrival
            "announced": None,        # last transition we actually reported: arrived/left
            "flickers": 0,
            "events": 0,
            "qr": None, "marker": None, "book": None,   # {"value", "at"} each
            "history": [],            # bounded [{event, at}]
            "updated_at": None}


def is_vision_event(name) -> bool:
    """True for an event string this module models (safe on None/non-str)."""
    return isinstance(name, str) and name.strip() in VISION_EVENTS


def value_of(payload, event_name: str) -> str:
    """The semantic payload of a marker event, from `RemoteChatRequest.input_vars`.

    Accepts the `$`-prefixed spelling the catalog documents and the bare one it warns
    about, and tolerates a payload that is not a dict at all (an unknown/garbled event
    must never raise on the MQTT loop)."""
    key = VALUE_KEYS.get(event_name)
    if not key or not isinstance(payload, dict):
        return ""
    for k in (key, key.lstrip("$")):
        v = payload.get(k)
        if v not in (None, ""):
            return str(v)
    return ""


def _gap(now, then) -> float:
    """`now - then`, floored at 0 so a clock that steps backwards cannot make a
    duration negative (and cannot turn an absence into a fake arrival)."""
    if then is None:
        return 0.0
    try:
        return max(0.0, float(now) - float(then))
    except (TypeError, ValueError):
        return 0.0


def update_presence(state, event_name, payload=None, now=None):
    """Fold one vision event into `state`. Returns `(new_state, signals)`.

    **Pure**: `state` is never mutated — a fresh dict (and a fresh history list) comes
    back, so a caller can diff, and a failed publish can drop the update.

    `signals` is a list of dicts, each `{"name": …, "at": now, …}`:

    | signal | extra | meaning |
    |---|---|---|
    | `arrived` | `away_s` (None on the first sighting) | absent → present, past the flicker window. `away_s` is the "returned after N seconds" the greeting rule keys off. |
    | `left` | `present_s` | present → absent, after a run of at least `MIN_PRESENT_S` |
    | `flicker` | `direction` (`found`/`lost`), `gap_s` | a blip that was deliberately NOT promoted to arrived/left |
    | `qr` / `marker` / `book` | `value` | a scanned code / ArUco id / recognized book |

    An event name we do not model returns the state unchanged and no signals, so a
    future firmware string can never corrupt presence.
    """
    if now is None:
        import time
        now = time.time()
    name = event_name.strip() if isinstance(event_name, str) else ""
    if name not in VISION_EVENTS:
        return (dict(state) if isinstance(state, dict) else new_state()), []

    st = dict(state) if isinstance(state, dict) else new_state()
    for k, v in new_state().items():                 # tolerate a partial/old record
        st.setdefault(k, v)
    st["history"] = list(st.get("history") or [])[-(HISTORY_MAX - 1):]
    st["history"].append({"event": name, "at": now})
    st["events"] = int(st.get("events") or 0) + 1
    st["updated_at"] = now
    signals = []

    if name == FOUND_FACE:
        was = st["face_present"]
        away = _gap(now, st["last_lost_at"]) if st["last_lost_at"] is not None else None
        st["last_seen_at"] = now
        if was is True:
            pass                                     # a repeat found: refresh, say nothing
        elif away is not None and away < FLICKER_S:
            # Hysteresis: the same person, still there, the tracker just blinked. The
            # present-run clock is left alone so "how long have they been here" survives.
            st["flickers"] = int(st["flickers"]) + 1
            signals.append({"name": "flicker", "direction": "found",
                            "gap_s": away, "at": now})
        else:
            st["faces_seen"] = int(st["faces_seen"]) + 1
            st["present_since"] = now
            st["arrival_away_s"] = away
            st["announced"] = "arrived"
            signals.append({"name": "arrived", "away_s": away, "at": now})
        st["face_present"] = True
        st["absent_since"] = None

    elif name in (LOST_TARGET, LOST_FACE):
        was = st["face_present"]
        present_s = _gap(now, st["present_since"]) if st["present_since"] is not None else None
        st["last_lost_at"] = now
        if was is not True:
            pass                                     # already absent (or never present)
        elif (st.get("announced") == "left"
              or (present_s is not None and present_s < MIN_PRESENT_S)):
            # Either a run too short to have been a real presence, or a presence we have
            # ALREADY reported as over — a face blinking at the edge of the frame must
            # produce one `left`, not one per blink.
            st["flickers"] = int(st["flickers"]) + 1
            signals.append({"name": "flicker", "direction": "lost",
                            "gap_s": present_s if present_s is not None else 0.0,
                            "at": now})
        else:
            st["announced"] = "left"
            signals.append({"name": "left", "present_s": present_s or 0.0, "at": now})
        st["face_present"] = False
        st["absent_since"] = now

    else:                                            # qr / dr / br — semantic markers
        slot = {QR_EVENT: "qr", MARKER_EVENT: "marker", BOOK_EVENT: "book"}[name]
        value = value_of(payload, name)
        st[slot] = {"value": value, "at": now}
        signals.append({"name": slot, "value": value, "at": now})

    return st, signals


def snapshot(state, now=None) -> dict:
    """The small, JSON-safe presence context a `Turn` carries into the brain.

    Durations are resolved here (against `now`) rather than shipped as timestamps, so an
    app never has to know what clock the runtime used."""
    if now is None:
        import time
        now = time.time()
    st = state if isinstance(state, dict) else new_state()
    present = st.get("face_present")
    out = {
        "known": present is not None,
        "face_present": bool(present),
        "present_s": (_gap(now, st.get("present_since"))
                      if present is True and st.get("present_since") is not None else None),
        "away_s": (_gap(now, st.get("last_lost_at"))
                   if present is False and st.get("last_lost_at") is not None else None),
        "since_seen_s": (_gap(now, st.get("last_seen_at"))
                         if st.get("last_seen_at") is not None else None),
        "faces_seen": int(st.get("faces_seen") or 0),
        "arrival_away_s": st.get("arrival_away_s"),
        "flickers": int(st.get("flickers") or 0),
        "events": int(st.get("events") or 0),
        "last_qr": (st.get("qr") or {}).get("value", ""),
        "last_marker": (st.get("marker") or {}).get("value", ""),
        "last_book": (st.get("book") or {}).get("value", ""),
    }
    out["line"] = prompt_line(st, now)
    return out


def human_duration(seconds) -> str:
    """A duration a system prompt can say out loud — deliberately vague, because the
    numbers themselves are noise to a child ("about ten minutes", never "612 s")."""
    try:
        s = max(0.0, float(seconds))
    except (TypeError, ValueError):
        return "a moment"
    if s < 45:
        return "a few seconds"
    if s < 90:
        return "about a minute"
    if s < 3600:
        return f"about {int(round(s / 60.0))} minutes"
    if s < 5400:
        return "about an hour"
    return f"about {int(round(s / 3600.0))} hours"


def prompt_line(state, now=None) -> str:
    """One short, kid-safe sentence for the system prompt — **or `""`**.

    Empty is the common case on purpose. A line every turn would be a standing tax on
    the context window and would teach the model to narrate the camera; the brain only
    needs telling when the situation actually changed (someone just walked up, or the
    room has been empty for a while).

    Deliberately **descriptive, never imperative**. An early draft ended "Greet them
    warmly, briefly." and a live turn came back with a bare action tag and no spoken
    words — the persona already knows how to be warm, and a second instruction arriving
    as *situational context* competes with the turn the child actually started. Context
    lines say what is true; the persona decides what to do about it."""
    if now is None:
        import time
        now = time.time()
    st = state if isinstance(state, dict) else {}
    present = st.get("face_present")
    if present is None:
        return ""                                    # vision has told us nothing
    if present:
        since = _gap(now, st.get("present_since")) if st.get("present_since") else None
        if since is None or since > JUST_ARRIVED_S:
            return ""                                # settled — nothing worth saying
        gap = st.get("arrival_away_s")
        if gap:
            return (f"A child just came back in front of you — nobody had been visible "
                    f"for {human_duration(gap)}.")
        return "A child has just come into view in front of you."
    away = _gap(now, st.get("last_lost_at")) if st.get("last_lost_at") else 0.0
    if away >= LONG_ABSENCE_S:
        return (f"Nobody has been visible to you for {human_duration(away)} — you may be "
                f"talking to someone you cannot see.")
    return ""


# --- the greeting a runtime may speak when someone walks back in ------------------
#
# Short, warm, and not a conversation opener that demands an answer: the child has
# walked into the room, not started a turn. Rotated so it never lands twice running
# (same rule as `filler.py`), and performed through the markup floor
# (`moxie_sdk.automarkup`) like any other line the brain did not author markup for.
GREETINGS = (
    "Oh! Hi {name}! I was wondering where you went.",
    "Hey {name}, there you are! I missed you.",
    "{name}! You came back. Hi!",
    "Oh hello {name}! It is so good to see you again.",
    "There you are, {name}! Hi hi hi.",
)


def pick_greeting(nickname: str = "friend", last: str = "", *, rng=None) -> str:
    """One greeting line for `nickname`, never the one equal to `last`."""
    import random
    rng = rng or random
    name = (nickname or "friend").strip() or "friend"
    lines = [g.format(name=name) for g in GREETINGS]
    choices = [g for g in lines if g != last] or lines
    return rng.choice(choices)
