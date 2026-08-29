# 🗣️ Content & conversation — ChatScript, content modules, the volley API

> **What this is.** How Moxie decides what to *say and do* — the two-layer dialog engine, the
> content-module format a server delivers, and the `volley`/`session` hooks that let content run code
> and drive the robot. This is the payload side of client/server revival (goal #2): once the transport
> ([`cloud-protocol.md`](cloud-protocol.md)) is up, this is what you fill it with. Reconstructed from
> the `embodied.robotbrain` protos, `libbo-brain`/`libchatscript` symbols, and the working
> OpenMoxie content-module format (which implements the same `RemoteChat` contract).

## Two dialog engines

```mermaid
flowchart TB
  user["user speech / event"] --> brain["bo-android brain"]
  brain --> cs["on-device ChatScript\n(libchatscript.so) — LOCAL"]
  brain --> remote["cloud LLM chat\n(RemoteChat) — REMOTE_CHAT"]
  cs & remote --> resp["ChatResponse + <mark cmd:...> markup"]
  resp --> body["speech + gestures/face/audio"]
  classDef d fill:#e3eaf2,stroke:#607d8b,color:#263238;
  class user,brain,cs,remote,resp,body d;
```

- **ChatScript (LOCAL):** the classic rule-based engine runs *on the robot* (`ChatscriptEngine`,
  `chatscript::api`), compiling topic/concept rules. Fast, offline, deterministic. Errors surface as
  `ChatScriptError`/`ChatScriptException`; readiness as `ChatScriptReady`.
- **Cloud chat (REMOTE_CHAT):** an LLM answers a `RemoteChatRequest` and returns a `ChatResponse`.
- **HYBRID:** a module can mix both (`ModuleDetail.ContentSource = LOCAL | REMOTE_CHAT | HYBRID`).

A **module** (`ModuleDetail`) is an activity/content pack: `ContentDetail` entries, a
`ModuleCategory` (CREATIVITY, REGULATION, MOVEMENT, READING, PLAYFUL_GAME, PUZZLE_GAME, FUN_TIDBIT,
LISTENING, MISSION, CONVERSATION, UTILITY), delivery `ContentRules` (ORDERED, RANDOM,
`*_EXHAUST[_SEEN]`, DAILY_MISSION, CALENDAR), and `min_api_version`. `LineStoreSerialState` tracks
which lines a child has seen (the `masks`) so `*_EXHAUST` rules don't repeat.

## Content-module format (what a server serves)

A module is JSON with three optional sections — `conversations`, `globals`, `schedules`:

### `conversations[]` — LLM-driven chats
```json
{ "name":"Basic Memory Chat", "module_id":"OPENMOXIE_CHAT", "content_id":"memory",
  "max_history":40, "max_volleys":40, "opener":"Let's have a chat.|Anything on your mind?<opener>",
  "prompt":"You are a robot named Moxie ... talking to {{volley.config.child_pii.nickname}}. FACTS:\n{{volley.persist_data.memory_chat.facts}}",
  "model":"gpt-4o", "max_tokens":100, "temperature":0.5,
  "code":"def post_process(volley, session): ..." }
```
- `prompt` is **Jinja2-templated** over `volley`/`session` (`{{…}}`, `{% if %}`). Common vars:
  `volley.config.child_pii.nickname`, `volley.persist_data.*`, `session.overflow`.
- `opener` supports `|`-alternatives and inline tags (`<opener>`, `<exit>`, `<sleep>`, `<launch:XX>`).
- `code` defines Python hooks run around each turn: `pre_process`, `post_process`,
  `complete_handler`, `notify_handler` (and `handle_volley` for globals).

### `globals[]` — regex-triggered commands (always on)
```json
{ "name":"Timer Start", "pattern":"^(moxie|moxy) (start|set) a? timer for? (\\d+) (minute|hour|second)s?$",
  "entity_groups":"3,4", "action":4, "code":"def handle_volley(volley): ..." }
```
Regex over the utterance; capture groups become `volley.entities`. `action` selects how the match is
handled; the `code` builds the response and fires execution actions.

### `schedules[]` — what to offer when
```json
{ "name":"moxie_go_hub_timers",
  "schedule":{ "provided_schedule":[{"module_id":"ENROLLCONVO"},{"module_id":"DM"}],
    "generate":{"chat_count":2,"module_count":6,"chat_modules":[...]},
    "hub_config":{"hubs":[{"module_id":"MOXIE_GO","content_id":"default"}]},
    "alarm_module":{"module_id":"ALARM","content_id":"fire"} } }
```
Mirrors `embodied.robotbrain.ContentSchedule` (`ScheduleConfig.day_one_schedule`, `promoted_content`,
`MissionConfig`, `RewardsConfig`, `EndOfSessionConfig`).

## TTS & dialog engine internals (formats & data)

Both engines are **native libs baked into the firmware**, but their **data (voice model, dialog
content) is delivered as synced content — not in the image**. This is the key revival fact: you supply
your own voice/dialog; you can't extract (and don't need) Embodied's licensed assets.

### CereVoice TTS (`libcerevoice_eng.so`, ~44 MB)
- **CereProc CereVoice**, build **v5.0.2**, **DNN parametric synthesis** (`CPDNN` — deep-neural-net
  spurt generation, not classic unit-selection).
- Voice model format: a **`.voice` file** = "**CEREPROC TPDATABASE v 1.0**" (`CEREVOICE HEADER`), a
  licensed text-processing DB + DNN weights. The voice file is **not on the firmware image** — it's
  delivered as content; the engine performs a license check (`src/license/bigdigits.c`).
- **Revival:** irrelevant to reproduce exactly — the brain requests TTS via `CloudTTSRequest` and the
  **server returns rendered PCM** (`CloudTTSResponse.audio`, see [`perception-pipeline.md`](perception-pipeline.md)),
  so any TTS (or a CereProc voice you license) drops in. Local CereVoice is the on-device path/fallback.

### ChatScript (`libchatscript.so`, ~27 MB)
- **Bruce Wilcox's ChatScript** rule engine (prints `ChatScript Version %s compiled %s`). Symbol
  legend confirmed in the binary: `$` variables · `@` factsets · `_` match-vars · `^` macros ·
  `~` topics/concepts.
- Content authored as **`.top` topic files** (topics, `~concepts`, `table`/facts) and **compiled** to
  a runtime dictionary + topic store (`AddTopicCode`, `AllocateTopicMemory`, `$cs_topicretrylimit`).
  The compiled content is **not in the firmware** — it's synced content.
- Drives the **LOCAL** dialog path and the always-on **global commands** (regex `globals`, above);
  errors surface as `ChatScriptError`/`ChatScriptException`, readiness as `ChatScriptReady` on the bus.

### ChatScript authoring — the real format & pipeline

The abstract "`.top` files" above have a concrete shape, recovered from an **ex-Embodied game
designer's public sample repo** ([`nhertanto/Embodied-Moxie`](https://github.com/nhertanto/Embodied-Moxie);
facts captured here per the [self-sufficiency doctrine](external-sources.md)). Embodied authored content
through a **three-layer pipeline**, not by hand-writing ChatScript:

1. **Python node classes** (`*.py`) — subclass `FlexibleInteractions`/`FlexibleNodeData` to declare a
   node's authoring-tool **UI properties** (fixed-choice dropdowns, text+markup fields, "move-on"
   transitions). This is what a designer edits in the **in-house visual tool**.
2. **Jinja2 templates** (`*.jinja`) — turn that property data into ChatScript. A `base_topic.jinja`
   emits `topic:`/`t:` blocks and expands **text variations** and **markup variations** (multiple
   `[ … ]` alternatives so a response isn't identical every time); templates `{% import %}` shared
   `Macros/node_utility.jinja`.
3. **Generated `.top` files** (`Generated-*.top`) — the compiled-to output that ships as synced content.

The **ChatScript `.top` syntax** itself (Bruce Wilcox's engine) as Embodied uses it:

```chatscript
# reusable intent pattern (what the child said to trigger an activity)
patternmacro: ^P_JOKES_userTellJoke()
[ (!~negation [can could may] I *~2 tell {you} a *~2 joke)
  (!~negation I [have know] *~2 joke) ]

# a topic = a dialog node; CS flags then robot-brain [flags]
topic: ~my_topic keep repeat [FLAG]
  t: PROMPT() ^keep() ^repeat()        # a gambit/output rule
     [ Here is one variation of the line. ]   # text/markup variations in [ ]
     [ Here is another phrasing. ]
```

Operator legend: `[ a b ]` = alternates · `{ x }` = optional · `*~N` = up to N-word gap · `!~negation`
= must-not-precede · `~concept` = a concept set (`~want`, `~silly`, `~botname`, `~intensifier`) ·
`%tense=present` · `< … >` = sentence bounds · `^macro()` = call a pattern/output macro (e.g.
`^intentPattern_request()`). A revival server that wants **offline/local** dialog authors `.top` files
in exactly this form and compiles them into the on-device ChatScript store.

**The named global commands** (always-listening voice controls, from the sample's
`FlexibleGlobalCommand1` choice list): **`Sleep`, `WakeUp`, `Hello`, `ListenToMe`, `Earmuffs`,
`HoldOn`, `RepeatThat`, `SpeakLouder`, `SpeakSofter`, `SomethingElse`** — these are the phrases Moxie
recognizes at any time (independent of the active activity); `Earmuffs` also appears as
`ENGAGEMENTSTATE_EARMUFFS` in [`proto-catalog.md`](proto-catalog.md), cross-confirming it.

### Where the data lives
Voice, ChatScript, and content modules are **synced to `/sdcard/EmbodiedData` / `/sdcard/EmbodiedStaticData`**
over the MQTT **file-sync** channel (`MQTT_FILE_SYNC`, see [`cloud-protocol.md`](cloud-protocol.md)) —
the same mechanism that delivers OTA images and content modules. A revival server hosts/serves these;
the base firmware ships only the engines.

## Context assembly & topical awareness

How the brain builds what the LLM sees, and stays topical:

- **Context blocks** (`embodied.robotbrain`): `GlobalContext`, `EnvironmentContext`, and
  `ConversationContext{context, content_tags, goal_levels, properties, prompt[]}` — each a
  `Context{id, text}`. These assemble into the LLM prompt (they map to
  `RemoteChatRequest.global_context/conversation_context/prompt_context`, [`cloud-protocol.md`](cloud-protocol.md)).
  A server fills these to steer the LLM (who's present, what activity, what's been said).
- **Holiday / event awareness** — `EventsAndHolidaysData{holidays[]}` with
  `Holiday{event_uid, holiday_id, name, tag, date, region}`: **region-specific, dated events** the robot
  uses for topical content (birthdays, holidays). A server can supply this for seasonal behavior.
- **Content tags** — `Tag{uuid, name}` / `ContentTag{replaced, finalized, review}` — the tag lifecycle
  that gates/curates which content is offered (with `TagList` allow/deny, above).
- **NLU & fallback** — `IntentPB{intent, input}` (intent classification), `Fallback{topic, module,
  userInput, fallbackType}` (what fired when Moxie didn't understand — maps to `ChatResponse.FallbackType`,
  [`cloud-protocol.md`](cloud-protocol.md)), and `IdleStateChange{state}` (idle-behavior transitions, the
  `idlestate` markup verb).

## Session & sleep lifecycle

- **Session** — `SessionState{inSession, record_mode, user, outSessionReason, prev_active}` with
  **`SessionUser{user_age, num_children, max_children}`**. Notably **`num_children`/`max_children` mean
  Moxie supports *group* sessions** (multiple kids at once — ties to the multi-party `MP_*` settings,
  [`settings-schema.md`](settings-schema.md)). A session begins on engagement and ends with an
  `outSessionReason`.
- **Bedtime / sleep** — parents set a **`WakeSchedule`** (`embodied.logging`):
  `weekday_bedtime_enabled` + `weekday_bedtime_starts_at`/`ends_at`, and the same for `weekend_*`
  (HH:MM strings). During a bedtime window Moxie **sleeps** and won't fully wake; **`BedTimeStatus{status,
  status_plus_20}`** reports whether it's currently bedtime (`status_plus_20` = a 20-minute grace/warning
  window). A revival server pushes the `WakeSchedule`; the robot enforces it locally.
- **Users** — `TargetedUser{targeted_user_id, targeted_user_face_id}` (who Moxie is attending to),
  `LearnUserState` (face-based **user enrollment/recognition** — Moxie learns family members by face,
  paired with the audio speaker-ID in [`perception-pipeline.md`](perception-pipeline.md)).

## Embodiment & activity runtime (PlaySpace, turn-taking, orientation)

Between the content layer and the physical robot sits the **activity runtime** — how an activity runs,
whose turn it is, and how Moxie orients its body. Two proto groups:

### `PlaySpace` — the activity/turn-taking state machine (`embodied.playspace`, 15 msgs)
- **`MoxieState`** — sync signals content waits on: `READY`, `STARTED_SPEAKING`, `DONE_SPEAKING`,
  `DONE_MOVING`, `PAUSED`. (A module can gate a step on "done speaking".)
- **`TurnState`** — conversational **turn-taking**: `USER` (child's turn) vs `SYSTEM` (Moxie's turn) vs
  `UNKNOWN`. Drives when to listen vs speak (with the VAD/wake stack, [`perception-pipeline.md`](perception-pipeline.md)).
- **`AgeGroup`** — **age-adaptive content**: `AGE_0_4`, `AGE_5_6`, `AGE_7_8`, `AGE_9_10`, `AGE_11_PLUS`
  (Moxie tailors difficulty/tone to the child's bracket; ties to `RemoteChatRequest.user_age`).
- **Triggers** — `TriggerAction {SET, CLEAR, CLEAR_ALL}` × `TriggerDuration {ONE_SHOT, PASSIVE, ACTIVE}`:
  content arms conditions that fire callbacks.
- `Source {ROBOT, PORTAL}` (local vs cloud-driven), `ExitCode {QUIT, ERROR, COMPLETE}` (how an
  activity ends).

### Spatial orientation & handling (`embodied.unity`)
- **`RobotPosition`** — the Unity face camera's 3D pose (`camera_center/target/up` xyz) — where the
  projected face "looks from/at" in world space.
- **`RobotEngageTurn{turning}`** / **`RobotTurnToOutOfViewChatTarget{is_turning}`** — Moxie **physically
  turns its body** (the `BASE_L_R` motor, [`hardware-map.md`](hardware-map.md)) to face the person it's
  talking to, or to seek an engaged speaker who moved out of camera view.
- **`MpuPickedUpEventPB`** / **`MpuPickedUpShakenEventPB{shakeDirection}`** / `MpuPickUpStatusEventPB{pitch}`
  + **`RobotCameraShake{shaking}`** — from the IMU: Moxie reacts to being **picked up** and **shaken**
  (with direction), including a face/camera-shake effect.

**Revival note (goal #2):** most of this is on-device — the server sends content and speech; the robot
handles turn-taking, orientation, and pickup reactions itself. A server mainly cares about `TurnState`
(when to expect input) and `AgeGroup`/`user_age` (to tailor content).

## Scheduling, progression & rewards (what to offer next)

Above individual modules sits the pedagogy layer — how the robot/server decides **what content to
offer**, tracks a child's progress, and rewards them. All `embodied.robotbrain`:

### Content days & schedule (`ContentSchedule`, `DailySchedule`)
- **`DailySchedule{csv_day_name, featured_module, modules[]}`** — content is organized into **"content
  days"**; each day has a featured activity + a module list. A child advances through `content_day`s.
- **`ScheduleConfig`** — `day_one_schedule` (onboarding), `promoted_content`, a `prompt_template`/
  `prompt_lm`; **`MissionConfig{mission_id}`** (daily missions), **`RewardsConfig{module_id,
  min_content_day}`**, **`EndOfSessionConfig{chat_module, end_module, chat_count}`**.
- **`ContentModule{module_id, allowed, denied_ids}`** + **`TagList{allowed, denied}`** gate which
  modules/tags are permitted (parental controls).

### The recommender
Which module to surface is ranked by weighted signals (the `RECOMMENDATION_*` settings,
[`settings-schema.md`](settings-schema.md)): `RECOMMENDATION_MP_PARENT_WEIGHT`,
`..._SENTIMENT_WEIGHT`, `..._RANDOM_WEIGHT`, `RECOMMENDATION_TAGHISTORY_ALPHA`,
`RECOMMENDATION_RANDOM_SEED/MODIFIER/UPDATE_PROB`, `RECOMMENDATION_BY_SEL`. Recommendations arrive as
`RecommendationContext.Recommendation{module_id, content_id, entry_line, seen, skip_hub}`
(see [`cloud-protocol.md`](cloud-protocol.md)'s `RemoteChatRequest.recommend`).

#### Content metadata & tagging taxonomy
What the recommender ranks *over* — each content item is categorized along four dimensions
(`ContentMetaList`, `embodied.robotbrain`), the vocabulary a revival server tags its own content with:

| Dimension | Tag type | Meaning |
|---|---|---|
| **`cognitive_load`** | `CognitiveTag{name, uuid, value}` | how mentally demanding (the `value` is a numeric load level) |
| **`intimacy_level`** | `IntimacyTag{name, uuid, order}` | how emotionally close/personal (`order` ranks the levels) |
| **`topics`** | `Tag{name, uuid}` | subject matter |
| **`genres`** | `Tag{name, uuid}` | style/format |

Per item, **`ContentInfo{_content_id, _csv_dict: ContentData}`** maps a content id to its
**`ContentData{UUID, content_tags, sel_tags}`** — the item's topic/genre `content_tags` plus its
**`sel_tags`** (which Social-Emotional-Learning goals it serves, tying content to the [STAR
curriculum](#star-goals-the-sel-curriculum)). The `cognitive_load`/`intimacy_level` dimensions let the
recommender pace difficulty and emotional intensity over a session; `content_tags`/`sel_tags` drive the
tag-history weighting (`RECOMMENDATION_TAGHISTORY_ALPHA`, `..._BY_SEL`). A server that serves its own
modules populates these so the recommender can rank them.

### STAR goals (the SEL curriculum)
Moxie's socio-emotional-learning goals: **`STARGoalStateChange{goal, goal_level, prompt_level,
activated}`** with **`STARGoalSuccess`/`STARGoalFailure`**. Content targets a `goal` at a `goal_level`;
success advances levels (`RECOMMENDATION_BY_SEL` weights recommendations by SEL goal).

### Rewards & history
- **`StarBitsEarned{earned, total, latest_unlocked}`** — **StarBits**, the reward currency a child
  earns to unlock content (the `RewardStar`/`reward-star` animation + markup, [`unity-assets.md`](unity-assets.md)).
- **`MentorBehavior{module_id, content_id, content_day, action, ended_reason}`** + `MentorBehaviorSet`
  — the **history** of what the child did (completed/abandoned, when). The robot **requests it** on
  session start (`client-service-activity-log subtopic=query, query=mentor_behaviors`,
  [`cloud-protocol.md`](cloud-protocol.md)) and **reports** new behaviors back — the server persists it
  and feeds it to the recommender + parent reports.

**Revival (goal #2):** a minimal server can ship a fixed `DailySchedule`/hub and ignore the
recommender (OpenMoxie does — a static `provided_schedule` + a hub module, [`content-and-conversation`](#schedulesschedules--what-to-offer-when)).
Full parity means persisting MentorBehavior + StarBits + STAR-goal state per child and ranking modules.

## Telehealth / remote puppet mode

Besides ChatScript (LOCAL) and the LLM (REMOTE_CHAT), there's a **third response source: a live human
operator** puppeting Moxie in real time (the telehealth / remote-caregiver feature). The robot enters
**`STATE_TELEBRAIN`** ([`boot-and-launcher.md`](boot-and-launcher.md)) — perception + Unity face run,
but **the local brain is off**; every utterance/behavior comes from the operator.

Protocol (`embodied.telehealth`, over MQTT — the `client-service-activity-log` `subtopic=telehealth`
and `/commands/telehealth`, see [`cloud-protocol.md`](cloud-protocol.md)):

```proto
enum Action { START_SESSION=1; PLAY_OUTPUT=2; END_SESSION=3; UPDATE_STATE=4; INTERRUPT=5; }
enum RobotState { READY=1; IN_SESSION=2; EXITING=3; }
message Output  { string line_id=1; repeated string line_params=2; string text=3; string markup=4; }
message TelehealthMessage { Action action=2; Output output=3; RobotState state=4; string session_id=5; }
TelehealthRobotCommand { string command; TelehealthMessage message; }   // cloud → robot
TelehealthRobotEvent   { string subtopic; TelehealthMessage message; }   // robot → cloud (status)
```

Flow: `START_SESSION` → robot goes `IN_SESSION`; the operator sends **`PLAY_OUTPUT`** with `text` +
**`markup`** (the same `<mark cmd:…>` behavior markup, [`behavior-markup.md`](behavior-markup.md)) —
so the operator drives **speech *and* face/motion** live; **`INTERRUPT`** stops current output;
`END_SESSION` returns to normal. `TelehealthStatus{telehealth_active, session_active}` reports state.

**Revival use (goal #2):** trivial to implement — a server publishes `PLAY_OUTPUT{text, markup}` to let
a parent speak through Moxie (type or TTS a line + optional gestures). OpenMoxie's server exposes
exactly this (`send_telehealth` / `PLAY_OUTPUT` / `INTERRUPT`). It reuses the TTS + markup paths, so no
new robot-side work is needed.

## The `volley` / `session` API (server-side hooks)

Each turn hands your `code` a **`volley`** (this exchange) and **`session`** (the conversation):

| Call | Effect |
|---|---|
| `volley.set_output(text, markup)` | Set the spoken text and optional `<mark cmd:…>` markup (see [`behavior-markup.md`](behavior-markup.md)). |
| `volley.entities` | Regex capture groups (for globals). |
| `volley.persist_data` / `volley.local_data` | Cross-session / this-turn scratch storage. |
| `volley.request.get("input_vars", {})` | Inbound vars (maps to `RemoteChatRequest.input_vars`). |
| `volley.add_execution_action(name, args)` | Ask the robot to *do* something (bridge to native). |
| `volley.update_subscriptions([...])` | Subscribe to robot events for later turns. |
| `session.summarize(...)` | LLM-summarize the transcript (memory). |
| `session.total_volleys`, `session.is_empty()`, `session.overflow` | Turn accounting. |

### Execution actions (content → robot bridge)
Named actions the brain executes, e.g.:
- `eb_timer_request [id, expiration_ms]` — set/cancel a timer (fires a wake event on expiry).
- `eb_enable_qr [true]` — turn on the camera QR scanner for this activity.
- `eb_wake` — wake state.

They map to `RemoteChatRequest.execute_returns` / `ChatResponse` action fields, and are the same
`activity_ids`/`input_vars` plumbing the proto exposes.

### QR inside content (ties to the QR toolkit)
Content can request a scan and read the value, so activities ship their own "launch cards":
```python
volley.add_execution_action('eb_enable_qr', ['true'])
volley.update_subscriptions(['eb-qr-event'])
# next turn:
qr = volley.request["input_vars"].get("$eb_qr_value", "")
if qr.startswith("GO"): volley.set_output(f"You got it!{qr[2:]}", None)
```
These are ordinary content QRs (a text payload the vision QR pipeline decodes), distinct from the
setup/`bo-wifi` grammar in [`qr-commands.md`](qr-commands.md) — same camera, different consumer.

## Revival implications

- A server implements `RemoteChat` (over the MQTT `RC_TOPIC`) by loading these modules, rendering the
  Jinja prompt, calling an LLM, running the `code` hooks, and returning `ChatResponse` text + markup.
- ChatScript content packs can stay on-device for offline/global commands; new activities are pure
  server-side modules. OpenMoxie is a working reference for the module format; this repo's
  [`server/`](../../server/) is where our implementation lives.
- Because `code` runs arbitrary Python server-side and `add_execution_action` reaches native robot
  functions, the content layer is fully programmable without touching firmware.

---
📖 [Reverse-engineering index](README.md) · [Cloud protocol](cloud-protocol.md) · [Behavior markup](behavior-markup.md) · [Docs index](../README.md)
