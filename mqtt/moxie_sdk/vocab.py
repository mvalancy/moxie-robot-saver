"""
Frozen behavior-markup vocabularies — every asset id our server is allowed to emit.

Why a frozen catalog
--------------------
`behavior-markup.md` is explicit that the generators on the robot accept **any** id the
loaded content bundle defines (docs/reverse-engineering/runtime/behavior-markup.md:161-163,
:228-230), so the lists below are the **app-hardcoded subset** we recovered — not the
exhaustive animation catalog of a given robot. Two consequences we live with:

  * this catalog catches **our** typos and invented ids; it can never prove a particular
    robot's bundle has an id, and whether a robot ignores an unknown mark or faults is
    **unknown** (no hardware has ever played our markup);
  * so the markup floor sticks to app-hardcoded ids only, and `validate_markup()` is the
    one gate every generated line passes.

Everything here is cited to the recovered page and line it came from. Nothing was read
from the vendor app: these are our own reverse-engineering notes.

Sources (repo-relative, line numbers as of this commit)
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

Corroboration: OpenMoxie (MIT, (c) Justin Beghtol) ships the same `ePlaybackMood` ids 0-10
in the same order in `site/hive/automarkup/markup_types/markup_mood.py`. Two independent
recoveries agreeing is the strongest evidence we have for any enum in this project. Their
code and data tables are **not** copied here.
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

#: Free-text mood labels a brain (or an older prompt) may hand us -> `ePlaybackMood`.
#: The canonical names above always win; these are the aliases we accept as *hints*.
#: The emotion-word column mirrors `RemoteDialog.EmotionState`
#: (remote-chat-protocol.md:123) and the ordinary words a model reaches for. An alias
#: that is not here is **dropped**, never passed through (see `automarkup.annotate`).
MOOD_ALIASES: Dict[str, int] = {
    # our own older LLM prompt menu (mqtt/moxie_sdk/apps/llm_app.py, pre-floor)
    "positive": 1, "negative": 2, "oops": 4,
    # RemoteDialog.EmotionState (7) — remote-chat-protocol.md:123
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

#: Short names a brain may write -> a real `Gesture_*`. Deliberately does NOT contain
#: OpenMoxie's `AUTO_GESTURE_ME` / `AUTO_GESTURE_YOU` / `Gesture_We` / `Gesture_Small` /
#: `Gesture_Discard`: those are *their* ids and are not in our recovered catalog.
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
#: matching `Bht_Eyeseme_<name>` (behavior-markup.md:110-113, behavior-tree-engine.md:109).
EYESEME_TREES: Tuple[str, ...] = tuple(
    "Bht_Eyeseme_" + n for n in (
        "Afraid", "Angry", "Concerned", "Confused", "Curious", "Embarrassed",
        "Happy", "Neutral", "Sad", "Shy", "Surprised"))

#: The named trees from behavior-tree-engine.md:103-115 ("the 45 named behavior trees"),
#: transcribed group by group. Honest note: the page's table enumerates 43 distinct ids —
#: its "Vocal_Gestures (`Vg_`)" cell names a *family* rather than one id, which is where
#: the headline count of 45 comes from. We list only ids we can name.
NAMED_TREES: Tuple[str, ...] = EYESEME_TREES + tuple("Bht_" + n for n in (
    # Idle / attention — :110
    "Idle_Curious", "Idle_Listening", "Idle_Near_Focused", "Idle_Near_UnFocused",
    "Idle_Far_Unfocused", "Idle_SeekingState", "Idle_DisengagedState", "Idle_Earmuffs",
    # Gestures / talking — :111
    "Gesture_Greet", "Talking_Poses", "Talking_With_Gestures", "Vocal_Gestures",
    "Head", "Spin_360", "ooo_long", "Sign_off",
    # Physical reactions — :112
    "Robot_Pickup", "Robot_Putdown",
    # Sleep / sensory — :113
    "Sleep_Anim", "Sleep_Anim_Zero", "Sleeping_Anim", "SensoryIdle_Anim",
    "SensoryIdleStoryTime_Anim",
    # System / lifecycle — :114
    "System_Resume", "System_Suspend", "System_Suspend_Zero", "System_WifiRecover",
    "Active_Thinking", "Demo_Wake_Up",
    # Test / misc — :115
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

#: **There is no gaze verb.** Gaze lives on the robot: weighted interest points ->
#: `AttentionTarget` -> IK look-at (gaze-and-attention.md:13-15,:48-53), driven from trees
#: by `RobotBT_GazeControl*` nodes (behavior-nodes.md). The only cloud-side handle on where
#: Moxie looks is choosing a **look-bearing tree**, so "gaze" is this closed 4-value set —
#: not a direction. Widening it needs a markup verb we have not found, or the robot-side
#: `LookAtMeRequest` IPC (perception-pipeline.md). Recorded, not papered over.
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
#: The only icon `value`s we have actually seen in shipped content (:156-157). The set is
#: bundle-defined, so this is a floor, not a ceiling — and every one of the four is a
#: **calendar/event** cue, which is why icons are off by default in the markup floor.
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

#: **Exactly two** confirmed `SoundToPlay` asset ids (:97-98). This is the thinnest
#: catalog on the page by a wide margin, and the honest reason `sfx=True` is off by
#: default: one of the two is a stinger (usable from a spoken line), the other is a
#: looping music bed for a cast segment (not something chat should start). Widening this
#: needs the robot's asset-bundle manifest, which we do not have.
SFX_STINGER = "sfx_twinkly_upbeat_stinger_1"
SFX_MUSIC_LOOP = "moxie_mu_cast_zarcona_theme_loop_v2"
SFX_IDS: Tuple[str, ...] = (SFX_STINGER, SFX_MUSIC_LOOP)
SFX_SET = frozenset(SFX_IDS)

# --------------------------------------------------------------------------- #
# SSML (behavior-markup.md:35-43)
# --------------------------------------------------------------------------- #
USEL_GENRES: Tuple[str, ...] = ("none", "question", "motivational", "intimate", "excited")
USEL_GENRE_SET = frozenset(USEL_GENRES)
#: `variant` is 0-8 (a recorded take). We pin 0: we have no evidence about which take
#: suits which line, so choosing one would be invention.
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

#: The verbs the markup **floor** may mint. Anything else is the planner's business.
FLOOR_VERBS = frozenset({"playback-mood", "behaviour-tree", "icons-v2", "playaudio"})

#: `RemoteDialog.DialogAct` (22) — remote-chat-protocol.md:119-122.
DIALOG_ACTS: Tuple[str, ...] = (
    "abandon", "apology", "apology_response", "appreciation", "backchannelling",
    "closing", "complaint", "opinion", "statement_non_opinion", "factual_question",
    "opinion_question", "hold", "opening", "yes_no_question", "pos_answer",
    "neg_answer", "other_answers", "command", "comment", "thanking", "other", "timeout",
)
#: `RemoteDialog.EmotionState` (7) — remote-chat-protocol.md:123. Distinct from
#: `ePlaybackMood`: this is the *perception* enum on the chat wire.
EMOTION_STATES: Tuple[str, ...] = (
    "sadness", "joy", "love", "anger", "fear", "surprise", "neutral",
)
#: `RemoteSignals.Signal` (9) — behavior-markup.md:183-189, remote-chat-protocol.md:124-126.
SIGNALS: Tuple[str, ...] = (
    "no_signal", "closing", "apology", "interrupted_speech", "complaint_clarification",
    "confirmation_agreement", "interest", "non_interest", "rejection_disagreement",
)

# --------------------------------------------------------------------------- #
# The ONE place a mark is minted
# --------------------------------------------------------------------------- #
# The `data:{…}` object is JSON with `+` standing in for `"`, because the mark lives
# inside an XML attribute (behavior-markup.md:16-27). Every generator in the tree —
# the markup floor, the filler lines, the LLM app — mints its marks here, so validation
# is total and there is exactly one string format to get right.

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
_MARK_RE = re.compile(r'<mark\s+name="cmd:([a-z0-9-]+)(?:,data:(\{.*?\}))?"\s*/?>', re.I | re.S)
_USEL_RE = re.compile(r'<usel\b[^>]*genre="([^"]*)"[^>]*>', re.I)
_SPURT_RE = re.compile(r'<spurt\b[^>]*spurt_id="([^"]*)"', re.I)


def _decode(body: str):
    """A mark's `data:{…}` payload back to a dict (`+` -> `"`), or None if it is not JSON."""
    try:
        return json.loads(body.replace("+", '"'))
    except Exception:
        return None


def validate_markup(markup: str) -> List[str]:
    """Every asset id in `markup` that is **not** in the frozen catalog above.

    Returns a list of `"<slot>=<id>"` strings — empty means the line only references ids
    we have actually recovered. Cheap enough for a debug assert on the hot path (it is a
    handful of regex scans over a line of speech), and it is what the corpus tests assert
    is empty across every app path.

    What it checks: the `cmd:` verb, `playback-mood` `mood`/`intensity`,
    `behaviour-tree` `eventName`/`behaviour`, `icons-v2` icon `value`s, `playaudio`
    `SoundToPlay`, `<usel genre>` and `<spurt spurt_id>`.
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
