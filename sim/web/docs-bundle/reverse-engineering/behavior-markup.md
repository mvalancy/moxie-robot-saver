# 🎭 Behavior markup — making Moxie move & emote while it talks

> Analyzed build: **v3.6.4-Zephyr / OTA v24.10.803** (RK3288, Android 9) — see [`firmware-803-reference.md`](firmware-803-reference.md).

> **What this is.** The inline command language a server embeds in Moxie's speech to drive its **body,
> face, audio, and screen** in sync with words. One `ChatResponse`'s text carries `<mark
> name="cmd:…">` tags; the brain's markup engine turns them into behavior-tree, motor, LED, and audio
> actions. This is how a revival server makes Moxie *do things*, not just speak. Reconstructed from
> `Assembly-CSharp` (`CommandMarkUpGenerator` subclasses) and shipped content markup.

## Shape

Speech is SSML-ish. Interleave spoken text (optionally wrapped in `<usel>`), pauses (`<break>`), and
command marks:

```xml
<mark name="cmd:behaviour-tree,data:{+transition+:0.5,+duration+:1.0,+repeat+:1,+blocking+:false,
   +action+:0,+eventName+:+Gesture_Celebrate+,+category+:+BehaviourTree+,+behaviour+:+Bht_Demo_Wake_Up+,+Track+:+wake+}"/>
<usel variant="0" genre="excited"> I'm so excited!</usel>
<break time="1.5s"/>
<mark name="cmd:playback-mood,data:{+mood+:0,+intensity+:0}"/>
```

- The `data:{…}` object is JSON with **`+` used in place of `"`** (the marks live inside an XML
  attribute, so quotes are escaped to `+`). Decode by swapping `+`→`"`.
- `<usel variant="N" genre="…">` selects a voice unit-selection style (e.g. `excited`,
  `motivational`); `<break time="1.5s"/>` inserts a pause.

## Speech markup (SSML / CereVoice)

Alongside the `cmd:` behavior marks, the spoken text uses a **CereVoice SSML dialect** (the TTS engine,
[perception-pipeline](perception-pipeline.md#tts)). A revival server emits these to *style the voice*;
the tags below are the ones actually used in shipped content (with observed value sets):

| Tag | Attributes (observed) | Purpose |
|---|---|---|
| `<usel variant genre>` | `variant` 0–8; `genre` = `none` · `question` · `motivational` · `intimate` · `excited` | **unit-selection style** — picks the voice's delivery. `question`/`none` dominate; `variant` chooses among recorded takes. |
| `<break time>` | e.g. `0.75s`, `1.5s` | pause |
| `<prosody>` | `pitch` (`medium`), `rate` (`slow`/`medium`/`x-fast`), `volume` (`medium`) | pitch/rate/volume shaping |
| `<emphasis level>` | `strong` | stress a word |
| `<phoneme ph>` | dotted phones, e.g. `ph="r.iy.d"` (read), `ph="k.ih.p.uw.r"` | **pronunciation override** (names/rare words); phones are ARPAbet-ish, dot-separated |
| `<spurt>` | `spurt_id`, `speaker` | **non-verbal vocalization** (breath, laugh, "hmm") — CereVoice "spurt" |

So a full line interleaves three layers: **SSML** (how it's *said*: `usel`/`prosody`/`phoneme`),
**behavior marks** (`cmd:` — what the *body/face* does), and plain text (what's *said*). The
[`markup.py`](../../tools/robot-toolkit/moxie_toolkit/markup.py) helper emits the `cmd:` layer; the
SSML tags above are standard-ish and can be templated directly.

## The command verbs (24)

Each verb is a `CommandMarkUpGenerator` with a typed request (its `data` fields):

| Verb | Purpose |
|---|---|
| **`behaviour-tree`** | Play a named behavior tree / gesture (the main body-motion driver). |
| **`playback-mood`** | Set emotional mood + intensity (colors face + posture). |
| **`vocal-gesture`** | Non-word vocalizations ("ooo", laughs) as gestures. |
| **`emotion`** | Set an emotion state. |
| **`idlestate`** | Switch idle behavior state. |
| **`playaudio`** / **`stopaudio`** | Start/stop an SFX or music clip on a channel. |
| **`speech-playback`** | Play prerecorded speech. |
| **`animation`** | Play a face/character animation. |
| **`blink-control`** | Control blinking. |
| **`dynamic-face-texture`** | Swap the projected face texture. |
| **`attachment` / `-animator` / `-particles`** | Show/animate a screen attachment + particle FX. |
| **`icons-v2`** | Show up to **4 contextual icons** on the face screen (calendar/event cues). |
| **`hud`** | HUD overlay element. |
| **`notification`** | On-screen notification (`message`, `duration`). |
| **`reward-star`** | Reward-star animation (STAR system). |
| **`whiteboard`** | Whiteboard/drawing activity. |
| **`composite`** | Expand a named **composite** — a saved bundle of marks (alias → markup). |
| **`scripted`** | Run a scripted sequence. |
| **`playback-save` / `playback-restore`** | Save/restore playback state (e.g. duck music, then restore). |
| **`start-systemsuspend`** | Begin system suspend. |
| **`start-systemunpair`** | Begin unpair flow. |

## Data schemas (verified from production markup)

**`behaviour-tree`** — the workhorse:

| field | type | notes |
|---|---|---|
| `transition` | float | blend-in seconds |
| `duration` | float | play seconds |
| `repeat` | int | loop count |
| `layerBlendInTime` / `layerBlendOutTime` | float | layer blend |
| `blocking` | bool | block the turn until done |
| `action` | int | 0=start, 1=stop, 4=clear (observed) |
| `eventName` | string | e.g. `Gesture_Celebrate`, `Gesture_Small`, `Gesture_None` |
| `category` | string | `BehaviourTree`, `Bht_Vocal_Gestures`, `FTUE`, `None` |
| `behaviour` | string | tree id, e.g. `Bht_Demo_Wake_Up`, `Bht_Search`, `Bht_Spin_360`, `Bht_Gesture_Greet` |
| `variableName` / `variableValue` | string | tree blackboard var |
| `Track` | string | animation track, e.g. `wake`, `spin` |
| `lifetime` | int | |

**`playaudio`**: `SoundToPlay` (asset id, e.g. `sfx_twinkly_upbeat_stinger_1`,
`moxie_mu_cast_zarcona_theme_loop_v2`), `LoopSound` bool, `playInBackground` bool, `channel` int
(0=music, 2=sfx observed), `ReplaceCurrentSound` bool, `PlayImmediate` bool, `ForceQueue` bool,
`Volume` float, `FadeInTime`/`FadeOutTime` float, `AudioTimelineField` string.

**`stopaudio`**: `scope` int, `channel` int, `FadeOutTime` float, `ClearQueue` bool.

**`playback-mood`**: `PlaybackMoodRequest { ePlaybackMood mood; int intensity=0 (maxIntensity=2); }`;
`Submit()` calls `SpeechPlaybackBehavior.PlaybackMood.SetMood(mood, intensity)`. The **`ePlaybackMood`
enum is authoritative** — recovered by name **and** value from `Assembly-CSharp` (the .NET assembly keeps
its metadata; an earlier note that the names weren't recoverable was wrong). The same `ePlaybackMood`
type is the robot's **`RobotState_EyesemeState`** blackboard variable, so **mood *is* the face
expression**: each value plays the matching **`Bht_Eyeseme_<name>` behavior tree** (see
[`behavior-tree-engine.md`](behavior-tree-engine.md#the-45-named-behavior-trees-bht_)).

| `mood` | `ePlaybackMood` | viseme base¹ | in shipped content | → SIL face |
|--:|---|--:|--:|---|
| **0** | `Neutral` | 0 | 188× (resting) | `neutral` |
| **1** | `Happy` | 1 | 36× | `happy` |
| **2** | `Sad` | 5 | 8× ("I'm sorry…") | `sad` |
| **3** | `Angry` | 9 | — | `sad` |
| **4** | `Shy` | 13 | 2× ("Oops.") | `happy` |
| **5** | `Surprised` | 17 | 14× ("Oh!") | `surprised` |
| **6** | `Afraid` | 21 | — | `surprised` |
| **7** | `Concerned` | 25 | — | `sad` |
| **8** | `Confused` | 29 | — | `thinking` |
| **9** | `Curious` | 33 | — | `thinking` |
| **10** | `Embarrassed` | 37 | — | `happy` |

¹ `VisemeIndices[mood]` — the base frame index into the face viseme set (blocks of 4). Only moods
`0,1,2,4,5` appear in the shipped content sampled, but **all 11 are valid** to emit. Note the earlier
*inferred* reading mislabeled mood `4` as "embarrassed" — it is actually **`Shy`** (`Embarrassed` is
`10`), which is exactly the kind of error the authoritative enum resolves. The SIL maps the 11 moods
onto its 6 faces (right column); `sim/web/bridge.js` `MOOD_TO_FACE` carries this table.

**`idlestate`**: `idleState` int (e.g. 7).

**`notification`**: `message` string, `duration` float (default 2s).

**`icons-v2`**: shows a small row of icons on the face screen — used heavily by the **holiday/calendar
event** content ([content-and-conversation](content-and-conversation.md#context-assembly--topical-awareness)).
Recovered schema (from shipped content):

```
cmd:icons-v2, data:{
  command:    int,     # 0 = show/enter, 2 = hide/clear  (paired around the spoken line)
  index:      int,     # slot/page index
  transition: float,   # fade time
  volume:     float,   # associated cue volume (0.0–1.0)
  icon0..icon3: { iconType: int,   # 0 = empty, 1 = named icon
                  value:    string,# icon name, or "Null"
                  background: string },  # bg id, or "Null"
  highlight:  int }
```

Up to **four icon slots**; a turn typically emits `command:0` (show) before the sentence and `command:2`
(clear) after. Real icon `value`s seen in shipped content: **`School`**, **`Birthday`**, **`Medical`**,
**`Learning_About_Family_03_Heart_Family`** (icons are named assets in the character bundle, so the full
set is bundle-defined). A [SIL face](../architecture/sil-and-cicd.md) can render these as small badges on
the screen; a revival server that emits event reminders should pair each with the matching icon name.

> Values above are read from shipped content and the request classes. The full enum spaces (all gesture
> names, mood ids, behavior-tree ids) live in the character asset bundles; the generators accept any
> id the loaded bundle defines. `composite` aliases let content packs bundle common sequences.

## Enumerated vocabularies (recovered)

The named spaces are partly **hardcoded in the app** (recoverable) and partly **defined by the loaded
content bundle** (open-ended). What we can enumerate directly:

### Emotion / mood — `EmotionState` (`embodied.robotbrain`, `RemoteChat.proto`)
The robot's classified/expressed emotion space — the natural target for a face renderer:

```
EMOTION_UNKNOWN=0  sadness=1  joy=2  love=3  anger=4  fear=5  surprise=6  neutral=7
```

This `EmotionState` (8 values) is the **perception/classification** enum on the chat wire — distinct
from the **`ePlaybackMood`** (11 values, above) that `cmd:playback-mood` actually carries. A revival
server maps its LLM's sentiment → an `ePlaybackMood` for the face/posture, and may separately report an
`EmotionState` on the chat channel; a **[SIL face](../architecture/sil-and-cicd.md)** maps either → an
expression.

### Conversational signals — `RemoteSignals.Signal` (`RemoteChat.proto`)
Discourse acts the brain tags on a turn (drive acknowledgement gestures/prosody):

```
no_signal=0  closing=1  apology=2  interrupted_speech=3  complaint_clarification=4
confirmation_agreement=5  interest=6  non_interest=7  rejection_disagreement=8
```

### Gestures — `Gesture_*` (hardcoded in `bo-android`)
The app's built-in gesture set (content bundles may add more):

```
Gesture_None · Gesture_Talk · Gesture_Think · Gesture_Think_Subtle · Gesture_Question
Gesture_Point · Gesture_Point_Right · Gesture_Self · Gesture_Higher · Gesture_Lower
Gesture_Large · Gesture_Celebrate
```

### Behaviour trees — `Bht_*` (hardcoded in `bo-android`)
Idle/expressive body-motion trees baked into the app:

```
Bht_Idle_Active_Listening · Bht_Idle_Curious · Bht_Active_Thinking · Bht_Vg_hmm_thinking
Bht_VG · Bht_Gesture_Celebrate · Bht_Wing_Flap · Bht_Bangle_on_off · Bht_Sleep_Anim
```

Content packs reference **more** by name (e.g. `Bht_Demo_Wake_Up`, `Bht_Search`, `Bht_Spin_360`,
`Bht_Gesture_Greet` seen in shipped modules) — those resolve inside the character asset bundle, so the
full tree set is bundle-defined, not fixed in the binary. **Honest limit:** the Unity asset bundles
(`sharedassets1.assets`) don't expose these names as plain strings to `grep`, so the lists above are the
**app-hardcoded subset**, not the exhaustive animation catalog.

### Perception side — detected human emotion (`Face.proto`)
The vision pipeline also *reads* emotion off a person's face: `Face` carries `emotion` (uint64) +
`emotion_proba` (float) — i.e. Moxie classifies the child's expression, distinct from its own
`EmotionState` output. See [`perception-pipeline.md`](perception-pipeline.md).

## Toolkit — build marks programmatically

[`tools/robot-toolkit/moxie_toolkit/markup.py`](../../tools/robot-toolkit/moxie_toolkit/markup.py)
emits valid marks (handling the `+`-quoting) so a server can weave them into TTS text:

```python
from moxie_toolkit import markup as mk
text = ("Hi there!" + mk.behaviour_tree(behaviour="Bht_Gesture_Greet", category="BehaviourTree")
        + mk.playback_mood(mood=0) + " Let's play." + mk.playaudio("sfx_twinkly_upbeat_stinger_1", channel=2))
```

The brain consumes this over the `embodied.unity` CloudTTS / MarkUpToolMessages path (see
[`cloud-protocol.md`](cloud-protocol.md) and [`robot-ipc-protocol.md`](robot-ipc-protocol.md)).

---
📖 [Reverse-engineering index](README.md) · [Cloud protocol](cloud-protocol.md) · [IPC protocol](robot-ipc-protocol.md) · [Docs index](../README.md)
