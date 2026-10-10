"""
Frozen behavior-markup vocabularies — every asset id our server is allowed to emit.

The robot accepts **any** id its loaded content bundle defines (behavior-markup.md
:161-163, :228-230), so these lists are the **app-hardcoded subset** we recovered, not a
robot's full catalog. The catalog catches our typos and invented ids; it cannot prove a
bundle has an id, and whether a robot ignores or faults on an unknown mark is unknown. So
generators stick to these ids and `validate_markup()` is the gate every line passes.

Everything is cited to our own reverse-engineering notes (never the vendor app).

Sources (repo-relative)
-------------------------------------------------------
* `docs/reverse-engineering/runtime/behavior-markup.md`
    :16-27    the mark grammar — `<mark name="cmd:VERB,data:{…}"/>`, JSON with `+` for `"`
    :35-43    SSML layer — `<usel variant genre>`, `<break time>`, `<prosody>`, `<spurt>`
    :50-76    the 24 command verbs
    :80-95    `behaviour-tree` data schema (the workhorse)
    :97-102   `playaudio` — `SoundToPlay`, the `Channel` enum, and the 2 confirmed asset ids
    :104-105  `stopaudio` — the `Scope` enum
    :107-133  `playback-mood` — the authoritative `ePlaybackMood` 0-10 + `maxIntensity=2`
    :139-159  `icons-v2` schema + the 4 confirmed icon `value`s
    :183-189  `RemoteSignals.Signal` (9)
    :191-198  the 12 hardcoded `Gesture_*`
    :200-216  the 52 `VocalGestures.availableGestures` spurt ids
    :218-230  the app-hardcoded `Bht_*` subset + the "bundle-defined" honest limit
* `docs/reverse-engineering/runtime/behavior-tree-engine.md`
    :103-115  the 45 named `Bht_*` behavior trees (by group)
* `docs/reverse-engineering/protocol/remote-chat-protocol.md`
    :119-122  `RemoteDialog.DialogAct` (22)
    :123      `RemoteDialog.EmotionState` (7)
    :124-126  `RemoteSignals.Signal` (9)
* `docs/reverse-engineering/runtime/gaze-and-attention.md`
    :13-15,:48-53  gaze is on-device (interest points -> `AttentionTarget` -> IK look-at);
                   **there is no gaze verb**, so the only cloud handle is choosing a
                   look-bearing tree — see `GAZE_TREES`.

Corroboration: OpenMoxie (MIT) independently ships the same `ePlaybackMood` ids 0-10 in
the same order (`markup_types/markup_mood.py`); nothing of theirs is copied here.
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Moods — `ePlaybackMood`, recovered by NAME and VALUE from Assembly-CSharp
# (behavior-markup.md:107-133). `intensity` is 0-2 (`maxIntensity=2`, :107).
# --------------------------------------------------------------------------- #
MOODS: Dict[str, int] = {
    "neutral": 0,       # :117  188x in shipped content (resting)
    "happy": 1,         # :118  36x
    "sad": 2,           # :119  8x  ("I'm sorry...")
    "angry": 3,         # :120
    "shy": 4,           # :121  2x  ("Oops.")  <- NOT "embarrassed"; the enum settled it
    "surprised": 5,     # :122  14x ("Oh!")
    "afraid": 6,        # :123
    "concerned": 7,     # :124
    "confused": 8,      # :125
    "curious": 9,       # :126
    "embarrassed": 10,  # :127
}
MOOD_IDS = frozenset(MOODS.values())
MOOD_NAME_BY_ID = {v: k for k, v in MOODS.items()}
MAX_INTENSITY = 2       # behavior-markup.md:107 — `int intensity=0 (maxIntensity=2)`

#: Free-text mood labels accepted as *hints* -> `ePlaybackMood` (incl. the
#: `RemoteDialog.EmotionState` words). Anything else is **dropped**, never passed through.
MOOD_ALIASES: Dict[str, int] = {
    # our own older LLM prompt menu (mqtt/moxie_sdk/apps/llm_app.py, pre-floor)
    "positive": 1, "negative": 2, "oops": 4,
    # RemoteDialog.EmotionState (7) — remote-chat-protocol.md:94
    "joy": 1, "sadness": 2, "anger": 3, "fear": 6, "surprise": 5, "love": 1,
    # ordinary words
    "excited": 1, "glad": 1, "proud": 1, "sorry": 2, "upset": 2, "mad": 3,
    "worried": 7, "unsure": 9, "wondering": 9, "puzzled": 8, "thinking": 9,
    "bashful": 4, "scared": 6,
}
MOOD_ALIASES.update(MOODS)          # the canonical names are aliases of themselves

# --------------------------------------------------------------------------- #
# Gestures — the 12 `Gesture_*` hardcoded in `bo-android` (behavior-markup.md:191-198).
# Content bundles may add more; we emit only these.
# --------------------------------------------------------------------------- #
GESTURES: Tuple[str, ...] = (
    "Gesture_None", "Gesture_Talk", "Gesture_Think", "Gesture_Think_Subtle",
    "Gesture_Question", "Gesture_Point", "Gesture_Point_Right", "Gesture_Self",
    "Gesture_Higher", "Gesture_Lower", "Gesture_Large", "Gesture_Celebrate",
)
GESTURE_SET = frozenset(GESTURES)

#: Short names a brain may write -> a real `Gesture_*` (OpenMoxie-only ids excluded).
GESTURE_ALIASES: Dict[str, str] = {
    "none": "Gesture_None", "talk": "Gesture_Talk", "think": "Gesture_Think",
    "thinking": "Gesture_Think", "subtle": "Gesture_Think_Subtle",
    "question": "Gesture_Question", "point": "Gesture_Point",
    "point_right": "Gesture_Point_Right", "self": "Gesture_Self", "me": "Gesture_Self",
    "big": "Gesture_Large", "large": "Gesture_Large", "up": "Gesture_Higher",
    "high": "Gesture_Higher", "down": "Gesture_Lower", "low": "Gesture_Lower",
    "celebrate": "Gesture_Celebrate", "cheer": "Gesture_Celebrate",
}
GESTURE_ALIASES.update({g: g for g in GESTURES})

# --------------------------------------------------------------------------- #
# Behavior trees — `Bht_*`
# --------------------------------------------------------------------------- #
#: The 11 expression trees; `ePlaybackMood` *is* the face, and each value plays the
#: matching `Bht_Eyeseme_<name>` (behavior-markup.md:110-113, behavior-tree-engine.md:159).
EYESEME_TREES: Tuple[str, ...] = tuple(
    "Bht_Eyeseme_" + n for n in (
        "Afraid", "Angry", "Concerned", "Confused", "Curious", "Embarrassed",
        "Happy", "Neutral", "Sad", "Shy", "Surprised"))

#: The named trees from behavior-tree-engine.md:152-165 ("the 45"), group by group. Only
#: ids we can name are listed (the `Vg_` cell is a family, not one id).
NAMED_TREES: Tuple[str, ...] = EYESEME_TREES + tuple("Bht_" + n for n in (
    # Idle / attention — :160
    "Idle_Curious", "Idle_Listening", "Idle_Near_Focused", "Idle_Near_UnFocused",
    "Idle_Far_Unfocused", "Idle_SeekingState", "Idle_DisengagedState", "Idle_Earmuffs",
    # Gestures / talking — :161
    "Gesture_Greet", "Talking_Poses", "Talking_With_Gestures", "Vocal_Gestures",
    "Head", "Spin_360", "ooo_long", "Sign_off",
    # Physical reactions — :162
    "Robot_Pickup", "Robot_Putdown",
    # Sleep / sensory — :163
    "Sleep_Anim", "Sleep_Anim_Zero", "Sleeping_Anim", "SensoryIdle_Anim",
    "SensoryIdleStoryTime_Anim",
    # System / lifecycle — :164
    "System_Resume", "System_Suspend", "System_Suspend_Zero", "System_WifiRecover",
    "Active_Thinking", "Demo_Wake_Up",
    # Test / misc — :165
    "Motor_Test", "TestState", "Anim",
))

#: The app-hardcoded subset listed separately in behavior-markup.md:221-224, plus the
#: four content packs are known to reference by name (:226-227).
APP_TREES: Tuple[str, ...] = (
    "Bht_Idle_Active_Listening", "Bht_Idle_Curious", "Bht_Active_Thinking",
    "Bht_Vg_hmm_thinking", "Bht_VG", "Bht_Gesture_Celebrate", "Bht_Wing_Flap",
    "Bht_Bangle_on_off", "Bht_Sleep_Anim",
    "Bht_Demo_Wake_Up", "Bht_Search", "Bht_Spin_360", "Bht_Gesture_Greet",
)
TREES: Tuple[str, ...] = tuple(dict.fromkeys(NAMED_TREES + APP_TREES))
TREE_SET = frozenset(TREES)

#: **There is no gaze verb** — gaze lives on the robot (gaze-and-attention.md:4-7,
#: :29-38). The only cloud handle is choosing a **look-bearing tree**, so "gaze" is this
#: closed set, not a direction.
GAZE_TREES: Tuple[str, ...] = (
    "Bht_Search", "Bht_Idle_Curious", "Bht_Idle_Listening", "Bht_Idle_Near_Focused",
)

# --------------------------------------------------------------------------- #
# Vocal gestures / spurts — the 52 `VocalGestures.availableGestures`
# (behavior-markup.md:200-216). Assets are named `g0001_<id>`.
# --------------------------------------------------------------------------- #
SPURTS: Tuple[str, ...] = (
    # Laughs — :211
    "laugh", "laugh2", "laugh3", "laugh4", "giggle", "giggle2", "ha ha (sarcastic)",
    # Thinking / filler — :212
    "hmm question", "hmm yes", "hmm thinking", "umm", "umm2", "err", "err2",
    # Breaths / sighs — :213
    "breath in", "sharp intake of breath", "breath in through teeth", "sigh happy",
    "sigh sad", "yawn", "yawn2", "snore", "snore phew", "zzz",
    # Affirm / react — :214
    "ah positive", "ah negative", "oh positive", "oh negative", "yeah question",
    "yeah positive", "yeah resigned", "yay",
    # Displeasure — :215
    "argh", "argh2", "ugh", "ocht", "doh", "gasp", "sarcastic noise",
    # Bodily / misc — :216
    "tut", "tut tut", "cough", "cough2", "cough3", "clear throat", "sniff", "sniff2",
    "snort", "raspberry", "raspberry2", "brr cold", "null",
)
SPURT_SET = frozenset(SPURTS)

# --------------------------------------------------------------------------- #
# Screen icons — `cmd:icons-v2` (behavior-markup.md:139-159)
# --------------------------------------------------------------------------- #
#: The only icon `value`s seen in shipped content (:156-157). All four are calendar/event
#: cues, which is why icons are off by default.
ICON_VALUES: Tuple[str, ...] = (
    "School", "Birthday", "Medical", "Learning_About_Family_03_Heart_Family",
)
ICON_SET = frozenset(ICON_VALUES)
ICON_SHOW, ICON_CLEAR = 0, 2            # `command` — :145
ICON_SLOTS = 4                          # icon0..icon3 — :149

# --------------------------------------------------------------------------- #
# Audio — `cmd:playaudio` / `cmd:stopaudio` (behavior-markup.md:97-105)
# --------------------------------------------------------------------------- #
CHANNEL_FX, CHANNEL_BACKGROUND, CHANNEL_STINGER, CHANNEL_VOCALGESTURE = 0, 1, 2, 3
SCOPE_ALL, SCOPE_CHANNEL = 0, 1

#: **Exactly two** confirmed `SoundToPlay` ids (:97-98): a stinger and a looping music bed
#: (not for chat) — hence `sfx` is off by default.
SFX_STINGER = "sfx_twinkly_upbeat_stinger_1"
SFX_MUSIC_LOOP = "moxie_mu_cast_zarcona_theme_loop_v2"
SFX_IDS: Tuple[str, ...] = (SFX_STINGER, SFX_MUSIC_LOOP)
SFX_SET = frozenset(SFX_IDS)

# --------------------------------------------------------------------------- #
# SSML (behavior-markup.md:35-43)
# --------------------------------------------------------------------------- #
USEL_GENRES: Tuple[str, ...] = ("none", "question", "motivational", "intimate", "excited")
USEL_GENRE_SET = frozenset(USEL_GENRES)
#: `variant` is 0-8 (a recorded take); pinned to 0 — no evidence which take suits a line.
USEL_VARIANT = "0"
SAY_AS_VALUES: Tuple[str, ...] = (
    "characters", "cardinal", "ordinal", "digits", "fraction", "unit", "date", "time",
    "address", "telephone",
)

# --------------------------------------------------------------------------- #
# Verbs (behavior-markup.md:50-76) and the taxonomies on the chat wire
# --------------------------------------------------------------------------- #
VERBS: Tuple[str, ...] = (
    "behaviour-tree", "playback-mood", "vocal-gesture", "emotion", "idlestate",
    "playaudio", "stopaudio", "speech-playback", "animation", "blink-control",
    "dynamic-face-texture", "attachment", "attachment-animator", "attachment-particles",
    "icons-v2", "hud", "notification", "reward-star", "whiteboard", "composite",
    "scripted", "playback-save", "playback-restore", "start-systemsuspend",
    "start-systemunpair",
)
VERB_SET = frozenset(VERBS)

# --------------------------------------------------------------------------- #
# What a content pack may put on the robot (content/ext_host.py's gate)
# --------------------------------------------------------------------------- #
#: Words that name a system flow rather than a performance: a catalogue verb holding one
#: is a system verb, never sent from pack content however it is written (`is_system_verb`).
#: Read off `VERBS` by name, so a verb added to the catalogue later is a system verb on
#: its own; today exactly the two recovered ones (behavior-markup.md:75-76).
SYSTEM_VERB_WORDS: Tuple[str, ...] = (
    "start-system", "wifi", "pair", "reset", "update", "suspend", "shutdown", "reboot",
    "factory",
)


def is_system_verb(verb) -> bool:
    """`verb` names a system flow: it holds one of `SYSTEM_VERB_WORDS`, in any case."""
    v = str(verb or "").lower()
    return any(word in v for word in SYSTEM_VERB_WORDS)


SYSTEM_VERBS = frozenset(v for v in VERBS if is_system_verb(v))

#: The verbs pack content (an extension's line or markup, a conversation's opener) may send:
#: the ones this appliance mints for a line itself (`mark`'s callers below) and whose
#: payload `validate_markup` reads — a face, a gesture or whole-body tree, a sound, the
#: screen icons. Every other catalogue verb is cut from pack content and named to the
#: parent (`content/ext_host.py`); widening this is a reviewed code change.
EXPRESSIVE_VERBS = frozenset({
    "behaviour-tree", "playback-mood", "vocal-gesture", "playaudio", "stopaudio", "icons-v2",
})

#: A `<mark` opening and the verb it names, read tolerantly (any quoting, any case, spaces
#: around the `=`, data or none, a mark left open), for naming what a robot's reader might
#: make of pack text; `_MARK_RE` below is the strict form every mark this appliance mints
#: has. Reads from an opening to the next `<` or `>` at most, so a run of openings costs
#: each its own gap.
_MARK_VERB_RE = re.compile(r"<mark\b[^<>]*?\bname\s*=\s*[\"']?\s*cmd:\s*([A-Za-z0-9_-]+)", re.I)


def mark_verbs(text) -> List[str]:
    """Every verb a `<mark` in `text` names, as written, in order (`_MARK_VERB_RE`). The
    catalogue is case-sensitive, so a caller lower-cases only to ask whether a verb is a
    system verb in any spelling."""
    return _MARK_VERB_RE.findall(str(text or ""))


#: `RemoteDialog.DialogAct` (22) — remote-chat-protocol.md:93.
DIALOG_ACTS: Tuple[str, ...] = (
    "abandon", "apology", "apology_response", "appreciation", "backchannelling",
    "closing", "complaint", "opinion", "statement_non_opinion", "factual_question",
    "opinion_question", "hold", "opening", "yes_no_question", "pos_answer",
    "neg_answer", "other_answers", "command", "comment", "thanking", "other", "timeout",
)
#: `RemoteDialog.EmotionState` (7) — remote-chat-protocol.md:94. Distinct from
#: `ePlaybackMood`: this is the *perception* enum on the chat wire.
EMOTION_STATES: Tuple[str, ...] = (
    "sadness", "joy", "love", "anger", "fear", "surprise", "neutral",
)
#: `RemoteSignals.Signal` (9) — behavior-markup.md:183-189, remote-chat-protocol.md:95.
SIGNALS: Tuple[str, ...] = (
    "no_signal", "closing", "apology", "interrupted_speech", "complaint_clarification",
    "confirmation_agreement", "interest", "non_interest", "rejection_disagreement",
)

# --------------------------------------------------------------------------- #
# The ONE place a mark is minted
# --------------------------------------------------------------------------- #
# `data:{…}` is JSON with `+` for `"` (the mark lives in an XML attribute,
# behavior-markup.md:16-27). Every generator mints its marks here.

def mark(verb: str, data: Optional[dict] = None) -> str:
    """One `<mark name="cmd:VERB,data:{…}"/>` tag. `verb` must be a recovered verb."""
    if verb not in VERB_SET:
        raise ValueError(f"unknown markup verb {verb!r}")
    if not data:
        return f'<mark name="cmd:{verb}"/>'
    body = json.dumps(data, separators=(",", ":")).replace('"', "+")
    return f'<mark name="cmd:{verb},data:{body}"/>'


def mood_mark(mood: int, intensity: int = 1) -> str:
    """`cmd:playback-mood` — set the face + posture (behavior-markup.md:107-133)."""
    return mark("playback-mood", {"mood": int(mood),
                                  "intensity": max(0, min(MAX_INTENSITY, int(intensity)))})


def tree_mark(event_name: str = "Gesture_None", behaviour: str = "", *,
              category: str = "BehaviourTree", track: Optional[str] = "",
              transition: float = 0.5, duration: float = 1.0, repeat: int = 1,
              blocking: bool = False, action: int = 0) -> str:
    """`cmd:behaviour-tree` — the workhorse (behavior-markup.md:80-95).

    `event_name` carries a `Gesture_*`; `behaviour` carries a whole-body `Bht_*`.
    `track=None` omits the `Track` field entirely (some shipped marks do not carry it).
    """
    data = {"transition": transition, "duration": duration, "repeat": repeat,
            "blocking": blocking, "action": action, "eventName": event_name,
            "category": category, "behaviour": behaviour}
    if track is not None:
        data["Track"] = track
    return mark("behaviour-tree", data)


def icons_mark(values=(), *, command: int = ICON_SHOW, index: int = 0,
               transition: float = 0.25, volume: float = 1.0, highlight: int = 0) -> str:
    """`cmd:icons-v2` — up to four named icons on the face screen (:139-159).

    `command=0` shows, `command=2` clears; a turn pairs one of each around the line.
    """
    names = [v for v in list(values)[:ICON_SLOTS] if v]
    slots = {}
    for i in range(ICON_SLOTS):
        v = names[i] if i < len(names) else None
        slots[f"icon{i}"] = ({"iconType": 1, "value": v, "background": "Null"} if v
                             else {"iconType": 0, "value": "Null", "background": "Null"})
    return mark("icons-v2", {"command": command, "index": index,
                             "transition": transition, "volume": volume,
                             **slots, "highlight": highlight})


def audio_mark(sound: str, *, channel: int = CHANNEL_STINGER, loop: bool = False,
               volume: float = 1.0, fade_in: float = 0.0, fade_out: float = 0.0) -> str:
    """`cmd:playaudio` — one SFX on a channel (behavior-markup.md:97-102)."""
    return mark("playaudio", {"SoundToPlay": sound, "LoopSound": loop,
                              "channel": channel, "Volume": volume,
                              "FadeInTime": fade_in, "FadeOutTime": fade_out})


def usel(text: str, genre: str, variant: str = USEL_VARIANT) -> str:
    """`<usel variant genre>…</usel>` — the voice's delivery style (:37)."""
    return f'<usel variant="{variant}" genre="{genre}">{text}</usel>'


def break_mark(time: str = "0.35s") -> str:
    """`<break time="…"/>` — a pause (:38)."""
    return f'<break time="{time}"/>'


# --------------------------------------------------------------------------- #
# Validation — the gate every generated line passes
# --------------------------------------------------------------------------- #
#: Each pattern reads a tag from its opening to the next `<` or `>` and no further
#: (`[^<>]`), so `validate_markup` is linear in its text: a run of openings costs each one
#: its own gap. A mark's data holds no `<` or `>` (none this appliance mints does; one that
#: does is not read as a mark, and the extension host drops it for its form first), while a
#: usel's genre and a spurt's id are still read to their closing quote, a quoted `>`
#: included (`<spurt x" spurt_id="n>pe"/>` is a spurt with the id `n>pe`), which the host's
#: whole-text pass relies on. Before: `data:{.*?}` read on to the end of the text from every
#: unclosed opening (11, 44 and 188 ms at 8, 16 and 32 KB), `[^>]*` from every `<usel` or
#: `<spurt` opening (25-58 ms per 8 KB, four times longer per doubling), and a run of
#: `<usel genre="` read on from each `genre="` found on the way back (0.35 s, 2.6 s and
#: 20 s), measured on the build host; now 0.2-0.5 ms at 32 KB and 1.3-4.2 ms at 256 KB on
#: every shape tried (sim/tests/test_pack_markup_gate.py).
_MARK_RE = re.compile(r'<mark\s+name="cmd:([a-z0-9-]+)(?:,data:(\{[^<>]*\}))?"\s*/?>', re.I)
_USEL_RE = re.compile(r'<usel\b[^<>]*genre="([^"]*)"[^<>]*>', re.I)
_SPURT_RE = re.compile(r'<spurt\b[^<>]*spurt_id="([^"]*)"', re.I)


def _decode(body: str):
    """A mark's `data:{…}` payload back to a dict (`+` -> `"`), or None if it is not JSON."""
    try:
        return json.loads(body.replace("+", '"'))
    except Exception:
        return None


def validate_markup(markup: str) -> List[str]:
    """Every asset id in `markup` that is **not** in the frozen catalog above.

    Returns `"<slot>=<id>"` strings (empty = only recovered ids). Checks the verb, mood/
    intensity, tree `eventName`/`behaviour`, icon values, `SoundToPlay`, `<usel genre>`
    and `<spurt spurt_id>`. Linear in `markup` (the patterns above), so cheap enough for
    the hot path on any text. A system verb is a catalogue verb and passes here: whether
    pack content may send it is the extension host's gate (`SYSTEM_VERBS`,
    `EXPRESSIVE_VERBS`).
    """
    bad: List[str] = []
    if not markup:
        return bad
    for verb, body in _MARK_RE.findall(markup):
        if verb not in VERB_SET:
            bad.append(f"verb={verb}")
            continue
        data = _decode(body) if body else None
        if not isinstance(data, dict):
            if body:
                bad.append(f"data={body[:40]}")
            continue
        if verb == "playback-mood":
            m = data.get("mood")
            if m not in MOOD_IDS:
                bad.append(f"mood={m}")
            i = data.get("intensity", 0)
            if not isinstance(i, int) or not 0 <= i <= MAX_INTENSITY:
                bad.append(f"intensity={i}")
        elif verb == "behaviour-tree":
            ev = data.get("eventName", "")
            if ev and ev not in GESTURE_SET:
                bad.append(f"eventName={ev}")
            bh = data.get("behaviour", "")
            if bh and bh not in TREE_SET:
                bad.append(f"behaviour={bh}")
        elif verb == "icons-v2":
            for i in range(ICON_SLOTS):
                slot = data.get(f"icon{i}") or {}
                v = slot.get("value")
                if slot.get("iconType") and v not in ICON_SET:
                    bad.append(f"icon={v}")
        elif verb == "playaudio":
            s = data.get("SoundToPlay")
            if s not in SFX_SET:
                bad.append(f"SoundToPlay={s}")
        elif verb == "vocal-gesture":
            s = data.get("spurt_id") or data.get("gesture")
            if s and s not in SPURT_SET:
                bad.append(f"spurt_id={s}")
    for genre in _USEL_RE.findall(markup):
        if genre not in USEL_GENRE_SET:
            bad.append(f"genre={genre}")
    for spurt in _SPURT_RE.findall(markup):
        if spurt not in SPURT_SET:
            bad.append(f"spurt_id={spurt}")
    return bad
