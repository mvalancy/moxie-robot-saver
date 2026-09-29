"""
Presence — Moxie's own eyes, folded into a small state machine the brain can read.

The robot runs vision on-device and sends only semantic event strings
(docs/architecture/vision.md:19-21, :63-88). This is the pure half: events in, a bounded
per-robot state + derived signals out. No I/O, no clock (the caller passes `now`); the
policy lives in the runtime (`mqtt/supervisor/moxie_runtime/presence.py`).

## The events we ingest (recovered catalog — INFERRED, never observed)

| event | carries | source |
|---|---|---|
| `eb-found-face` | nothing but the fact | vision.md:47, RemoteModuleAPI "Events" |
| `eb-lost-target` (a.k.a. `eb-lost-face`) | nothing but the fact | vision.md:48 |
| `eb-qr-event` | `input_vars['$eb_qr_value']` | vision.md:56 |
| `eb-dr-event` | `input_vars['$eb_dr_value']` — an ArUco id | vision.md:57 |
| `eb-br-event` | `input_vars['$eb_br_value']` — a Moxie book | vision.md:58 |

They carry found/lost only — no box, position, distance or identity (vision.md:51-53) —
so what can be built is *presence*: is someone there, since when, how long were they
gone. A subscribed event arrives as the `speech` of an ordinary `RemoteChatRequest`
(OpenMoxie `doc/RemoteModuleAPI.md` §Event Handling), after the brain subscribes via
`RemoteChatAction.EventSubscription` (remote-chat-protocol.md:81-84).

No physical robot has sent us one: the payload keys are cited, the flicker timing is a
guess — hence the hysteresis knobs below.

## The model

    absent ──eb-found-face──▶ present        (signal: arrived, away_s)
    present ──eb-lost-target──▶ absent       (signal: left, present_s)

with hysteresis so a face flickering at the frame edge cannot spam the brain:

* a `found` within `FLICKER_S` of the `lost` is a flicker: no `arrived`, clock kept;
* a `lost` ending a run shorter than `MIN_PRESENT_S` is a flicker: absent, but no `left`;
* a departure is announced once per presence (only a fresh `arrived` re-arms `left`).
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

#: Execute-actions aiming the face search at "someone close enough" (>=15% of frame
#: width, vision.md:40-45).
CUSTOM_FACE_SEARCH = "eb_custom_face_search"
BINNED_FACE_SEARCH = "eb_start_binned_face_search"
CLOSE_ENOUGH_ARGS = ["0.15", "0", "0", "true", "true"]


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except (TypeError, ValueError):
        return default


#: A face re-found within this many seconds is the same person flickering (a guessed knob).
FLICKER_S = _env_float("MOXIE_PRESENCE_FLICKER_S", 3.0)

#: A present-run shorter than this ends without a `left` — a one-frame false positive
#: should not read as "they walked out".
MIN_PRESENT_S = _env_float("MOXIE_PRESENCE_MIN_PRESENT_S", 2.0)

#: The rolling event log is a bounded window, not an archive.
HISTORY_MAX = 20

#: How recently an arrival still counts as "just now" in the prompt line.
JUST_ARRIVED_S = 60.0
#: How long nobody may be visible before the prompt line mentions it.
LONG_ABSENCE_S = 120.0


def new_state() -> dict:
    """A robot that has told us nothing yet. `face_present=None` means unknown, distinct
    from `False` ("told the face went away")."""
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
    """The semantic payload of a marker event from `input_vars` (`$`-prefixed or bare
    key). Never raises on a garbled payload."""
    key = VALUE_KEYS.get(event_name)
    if not key or not isinstance(payload, dict):
        return ""
    for k in (key, key.lstrip("$")):
        v = payload.get(k)
        if v not in (None, ""):
            return str(v)
    return ""


def _gap(now, then) -> float:
    """`now - then`, floored at 0 (a clock stepping backwards cannot go negative)."""
    if then is None:
        return 0.0
    try:
        return max(0.0, float(now) - float(then))
    except (TypeError, ValueError):
        return 0.0


def update_presence(state, event_name, payload=None, now=None):
    """Fold one vision event into `state`. Returns `(new_state, signals)`.

    Pure: `state` is never mutated.

    `signals` is a list of dicts, each `{"name": …, "at": now, …}`:

    | signal | extra | meaning |
    |---|---|---|
    | `arrived` | `away_s` (None on the first sighting) | absent → present, past the flicker window. `away_s` is the "returned after N seconds" the greeting rule keys off. |
    | `left` | `present_s` | present → absent, after a run of at least `MIN_PRESENT_S` |
    | `flicker` | `direction` (`found`/`lost`), `gap_s` | a blip that was deliberately NOT promoted to arrived/left |
    | `qr` / `marker` / `book` | `value` | a scanned code / ArUco id / recognized book |

    An unmodelled event name returns the state unchanged and no signals.
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
            # Flicker: keep the present-run clock.
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
            # Too short to be real, or already reported as over: one `left` per presence.
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

    Durations are resolved against `now`, so an app never sees the runtime's clock."""
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
    """A deliberately vague duration a prompt can say ("about ten minutes", not "612 s")."""
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
    """One short, kid-safe sentence for the system prompt — or `""` (the common case: only
    a change is worth saying). Descriptive, never imperative: an instruction here competes
    with the child's own turn; the persona decides what to do about it."""
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


# --- the greeting a runtime may speak when someone walks back in ---
# Short and warm, not demanding an answer; never the same line twice running.
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
