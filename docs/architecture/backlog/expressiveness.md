# Expressiveness — the markup floor and the behavior planner

**Status:** shipped (floor + planner P1) — [`mqtt/supervisor/markup.py`](../../../mqtt/supervisor/markup.py),
[`mqtt/moxie_sdk/automarkup.py`](../../../mqtt/moxie_sdk/automarkup.py),
[`mqtt/moxie_sdk/performance.py`](../../../mqtt/moxie_sdk/performance.py), tested by
[`sim/tests/test_automarkup.py`](../../../sim/tests/test_automarkup.py),
[`sim/tests/test_performance.py`](../../../sim/tests/test_performance.py) and
[`sim/tests/test_sil_performance_e2e.py`](../../../sim/tests/test_sil_performance_e2e.py). P2 (learned /
model-assisted planning) is a proposal (§2.7).

Moxie's voice is synthesized **on the robot**, from markup. The cloud has no TTS to improve
([`mqtt-and-conversation.md`](../mqtt-and-conversation.md) §5.3), so "better speech" means "better markup",
and markup is the only lever the cloud has over how alive the robot feels. This page covers the two
generators that sit behind one seam: a deterministic **markup floor** (ADOPT #3 in the
[OpenMoxie feature audit](../openmoxie-feature-audit.md)) and the **behavior planner** that replaced it as
the default (BEYOND #1).

**Clean-room.** Every id and grammar comes from our own reverse-engineering pages —
[`behavior-markup.md`](../../reverse-engineering/runtime/behavior-markup.md),
[`behavior-tree-engine.md`](../../reverse-engineering/runtime/behavior-tree-engine.md),
[`behavior-nodes.md`](../../reverse-engineering/runtime/behavior-nodes.md),
[`remote-chat-protocol.md`](../../reverse-engineering/protocol/remote-chat-protocol.md). OpenMoxie (MIT) is
read as prior art and cited by path (§1.4); no code or data table was copied.

---

## 0. The seam

One function turns a spoken line without its own markup into a performance. `MOXIE_EXPRESSIVE` picks the
generation behind it (`markup.py` docstring):

| `MOXIE_EXPRESSIVE` | What answers | Scored fields on the wire |
|---|---|---|
| `planner` (default) | `performance.render(validate(plan(…)))` | yes |
| `floor` | `automarkup.annotate` | yes (the planner still scores; only rendering rolls back) |
| `off` | passthrough — Moxie reads the line like a speaker | no |

An unrecognized value means `planner` (a typo in a rollback lever must not take the voice away).
`MOXIE_AUTOMARKUP=0` is the legacy alias for `off`.

| Piece | File | Role |
|---|---|---|
| The seam | [`mqtt/supervisor/markup.py`](../../../mqtt/supervisor/markup.py) | `perform()` returns a `Staged` (markup + scored fields + the `Performance`); budget breaker |
| Caller | [`mqtt/supervisor/moxie_runtime/turns.py`](../../../mqtt/supervisor/moxie_runtime/turns.py) `_stage` | the single place a published line becomes a scored turn — single replies and every streamed chunk |
| Floor | [`mqtt/moxie_sdk/automarkup.py`](../../../mqtt/moxie_sdk/automarkup.py) | pure `annotate()` |
| Planner | [`mqtt/moxie_sdk/performance.py`](../../../mqtt/moxie_sdk/performance.py) | `Beat`/`Performance`, `plan`/`validate`/`render` |
| Catalog | [`mqtt/moxie_sdk/vocab.py`](../../../mqtt/moxie_sdk/vocab.py) | frozen id sets, each citing the RE page + line it came from; the one place a mark is minted |
| LLM app | [`mqtt/moxie_sdk/apps/llm_app.py`](../../../mqtt/moxie_sdk/apps/llm_app.py) | `build_markup()` routes the model's mood/gesture choice into `annotate` as *hints* |
| Hand-authored | [`mqtt/moxie_sdk/filler.py`](../../../mqtt/moxie_sdk/filler.py) | "let me think" lines; authored, but minted through `vocab` |
| Renderer | [`sim/web/bridge/`](../../../sim/web/bridge/) | the browser SIM's `applyMarkup()` — the only renderer we can assert against |
| Stripper | [`mqtt/moxie_sdk/tts.py`](../../../mqtt/moxie_sdk/tts.py) | `strip_markup()` before an external TTS speaks the words |

The seam runs once per spoken chunk on the hot path, so both generators are pure, stdlib-only and
deterministic.

---

## 1. The markup floor

### 1.1 What it does

`annotate(text, *, mood_hint, gesture_hint, turn_key, chunk_index, icons=False, sfx=False)` turns one line
into markup drawn only from recovered vocabularies — cheap enough for every streamed chunk, deterministic
enough for byte-exact goldens. It is the planner's fallback and the `floor` rollback.

### 1.2 Call sites

- `llm_app.build_markup(text, mood, gesture, …)` calls `annotate` with the model's choice as a hint. A hint
  wins over the rules; an **unknown** hint is dropped, never forwarded. With `MOXIE_AUTOMARKUP=0` it falls
  back to one mood mark plus at most one gesture.
- `turn_key` + `chunk_index` give per-chunk stability (§1.5 S3).
- `filler.py`'s markup is hand-authored and left byte-identical: `test_brain_latency.py` pins its spoken line
  as one contiguous run, which a floor pass would break with a `<break>`.

### 1.3 Vocabularies we may emit

Closed sets; nothing outside [`vocab.py`](../../../mqtt/moxie_sdk/vocab.py) reaches the wire.

| Slot | Values | Source |
|---|---|---|
| Mark grammar | `<mark name="cmd:VERB,data:{…}"/>`, JSON with `+` for `"` | `behavior-markup.md` §Shape |
| Verbs | `vocab.VERBS` (25); the floor uses `playback-mood`, `behaviour-tree`, and (gated) `icons-v2` / `playaudio` | `behavior-markup.md` §command verbs |
| Mood | `ePlaybackMood` 0–10: Neutral, Happy, Sad, Angry, Shy, Surprised, Afraid, Concerned, Confused, Curious, Embarrassed; intensity 0–2 | `behavior-markup.md` §Data schemas |
| Gestures | 12 `Gesture_*` (None, Talk, Think, Think_Subtle, Question, Point, Point_Right, Self, Higher, Lower, Large, Celebrate) | `behavior-markup.md` §Gestures |
| Trees | `vocab.TREES` (50): the named `Bht_*` from the engine page plus the app-hardcoded set | `behavior-tree-engine.md` §named trees |
| Spurts | 52 vocal gestures | `behavior-markup.md` §Vocal gestures |
| Voice | `<usel variant genre>` (5 genres, variant pinned to `0`), `<break>`, `<prosody>`, `<emphasis>`, `<say-as>` (10 values) | `behavior-markup.md` §Speech markup |
| Icons | `icons-v2`, 4 confirmed values: `School`, `Birthday`, `Medical`, `Learning_About_Family_03_Heart_Family` | `behavior-markup.md` §Data schemas |
| SFX | `playaudio`, 2 confirmed ids: `sfx_twinkly_upbeat_stinger_1`, `moxie_mu_cast_zarcona_theme_loop_v2` | `behavior-markup.md` |
| Gaze | **no verb exists**; reachable only through 4 look-bearing trees (`vocab.GAZE_TREES`) | [`gaze-and-attention.md`](../../reverse-engineering/runtime/gaze-and-attention.md) |
| Dialog acts / emotions / signals | 22 `RemoteDialog.DialogAct`, 7 `EmotionState`, 9 `RemoteSignals.Signal` | `remote-chat-protocol.md` §Taxonomies |

OpenMoxie's `automarkup/markup_types/markup_mood.py` carries the same mood ids 0–10 in the same order —
independent corroboration of the enum.

### 1.4 OpenMoxie prior art — which behaviors we ported

OpenMoxie's `site/hive/automarkup/` (~2,150 LOC; entry `automarkup.process`, called from
`site/hive/mqtt/moxie_remote_chat.py::RemoteChat.make_markup`). Described, not copied.

| Their file | Behavior | Ported? |
|---|---|---|
| `markup_types/markup_mood.py` | ~30 emotion labels → 11 mood ids with an intensity ladder | yes — mood per line, intensity as int 0–2 |
| `markup_types/markup_behavior.py` | gesture change every sentence and every 3–7 words; word classes (self / you / question / high words); end on a "none" gesture; 80 % probability | the rules, made deterministic and remapped onto our 12 ids. Their `AUTO_GESTURE_ME/YOU`, `Gesture_We`, `Gesture_Small`, `Gesture_Discard` are not in our catalog and are never emitted |
| `markup_types/markup_pauses.py` | `<break>` after a sentence — never after the last word (it would delay turn hand-back) | yes, as a rule |
| `markup_types/markup_voice.py` | `<usel genre>`: `question` on `?`, `excited` on `!` | genre only; variant pinned to `0` |
| `markup.py::check_span_conflicts` | prune badly nested spans | the invariant only — our output is well-formed by construction, and a test parses it |
| `ml/mlrules.py` + a 170 KB data table | learned word → tag rules | **no** — unauditable; the P2 question (§2.7) |

**Why reimplement rather than vendor:** their engine pulls `unidecode` and a 170 KB table; it is random by
design (no goldens, no chunk stability); it emits ids we cannot justify from our own evidence; and our
generator must share a signature with the planner.

### 1.5 Design rules

**D1 · Deterministic.** No `random`, clock, network or model call. Where OpenMoxie rolls dice we take a
`blake2b(turn_key, sentence_index, sentence_text)` digest (never Python's salted `hash()`).

**D2 · Pipeline per line.** Segment with [`segment.py`](../../../mqtt/moxie_sdk/segment.py) (so the floor
and the streamer agree on sentence ends) and sub-split clauses on `, ; : —`; score the mood (hint, else the
first matching cue: sorry → Sad, "Oh!" → Surprised, "Oops" → Shy, thinking → Curious, puzzlement → Confused,
praise/`!` → Happy, else Neutral; intensity = `min(2, exclamations + emphatic words)`); wrap `?`/`!`
sentences in `<usel variant="0">`; place gestures; pick at most one whole-body tree (thinking →
`Bht_Active_Thinking`, greeting → `Bht_Gesture_Greet`, sign-off → `Bht_Sign_off`); add
`<break time="0.35s"/>` at internal boundaries and after a leading interjection; validate every id.

**D3 · Anti-twitch limits** (`automarkup.py` constants): a talking gesture every `TALK_EVERY = 5` words,
never within the last `TALK_TAIL = 2` words of a sentence; sentences under `TALK_MIN_WORDS = 6` get no talking
gesture; at most `MAX_GESTURES_PER_SENTENCE = 3` and `MAX_GESTURES_PER_LINE = 6`; a sentence that plays a
whole-body tree gets no arm gesture on top; one tree per line; always a terminal `Gesture_None`; never a
`<break>` after the final word.

**S1 · Idempotence.** Input that already carries `<mark` or `<usel` is returned unchanged.

**S2 · The words never change.** `strip_markup(annotate(t)) == strip_markup(t)` for every input. This is
what makes the floor safe to enable globally.

**S3 · Per-chunk stability.** The mood mark is emitted on `chunk_index == 0` and on **no later chunk** (the
strict form — a pure function cannot know the previously emitted mood). Every chunk ends with its own
`Gesture_None`, since the robot may pause between chunks; gesture spacing restarts per chunk.

**S4 · Budget.** Stdlib only, no I/O on the hot path; cost is tested relative to one pass over the line.

### 1.6 Goldens

[`sim/tests/goldens/annotate.json`](../../../sim/tests/goldens/annotate.json) pins eight lines byte-exact
(regenerate only when the rules change on purpose). Shorthand: `[mood N i]` = a `playback-mood` mark,
`[gest X]` = a `behaviour-tree` mark with `eventName` X, `[tree B]` = the same with `behaviour` B,
`[usel g]…[/]` = a `<usel>` span, `[break t]` = `<break>`.

| # | Input | Markup | Why |
|--:|---|---|---|
| G1 | `Hi! I am Moxie.` | `[mood 1 1][usel excited]Hi![/][break 0.35s][gest Gesture_Self] I am Moxie.[gest Gesture_None]` | `!` → Happy; "I" → self |
| G2 | `What do you want to play today?` | `[mood 9 1][usel question]…?[/][gest Gesture_Question][gest Gesture_None]` | open question → Curious; no break after the final word |
| G3 | `Hmm, let me think about that.` | `[mood 9 1]Hmm,[break 0.35s] let me think about that.[tree Bht_Active_Thinking][gest Gesture_None]` | interjection break; thinking tree, so no arm gesture |
| G4 | `That is amazing! You did it!` | two `excited` spans, `Gesture_Higher`, `Gesture_Celebrate`, `[mood 1 2]` | two `!` → intensity 2; the inter-sentence space is kept (S2) |
| G5 | `Oh! I did not know that.` | `[mood 5 1]…` | Surprised is shipped content's value for "Oh!" (14×) |
| G6 | `I am sorry that happened.` | `[mood 2 1][gest Gesture_Self]…[gest Gesture_None]` | Sad is shipped content's value for "I'm sorry" |
| G7 | `Oops.` | `[mood 4 1]Oops.[gest Gesture_None]` | Shy for "Oops."; under 6 words → no talking gesture |
| G8 | `Your birthday is on Friday.` (`icons=True`) | `[icons Birthday][mood 1 1][gest Gesture_Point]…[gest Gesture_None][icons off]` | a confirmed icon value, shown then cleared; "your" → point (no "you" gesture exists) |

G4's kept space and G8's leading (not trailing) `Gesture_Point` are the two deliberate departures from the
original hand-written spec; both are recorded in the fixture's `_readme`.

### 1.7 Tests

[`sim/tests/test_automarkup.py`](../../../sim/tests/test_automarkup.py) (hermetic): goldens byte-exact; no
unknown id over a corpus (goldens, every content module, every filler line, generated lines) with the
dropped-id counter at 0; words never change; idempotence; well-formed XML and JSON payloads; rate limits on
a long paragraph; one mood mark per streamed answer; identical bytes across `PYTHONHASHSEED`s; stdlib-only
imports; no file or socket on the hot path; every emitted id is one the SIM renders or is on an explicit
`ROBOT_ONLY` list. [`sim/test_automarkup_render.mjs`](../../../sim/test_automarkup_render.mjs) plays the
goldens through the real `bridge.js`. [`sim/tests/test_streaming.py`](../../../sim/tests/test_streaming.py)
checks S3 on a real streamed answer.

### 1.8 Limits

- **No hardware in the loop.** No physical Moxie has played our markup; robot behavior is inferred.
- **The asset namespace is bundle-defined.** The robot accepts any id its bundle defines; our lists are the
  app-hardcoded subset. The validator catches our typos, not a robot's missing asset — so we emit only
  app-hardcoded ids.
- **SFX gated off:** only two confirmed sound ids (one is a music bed). OpenMoxie's
  `doc/AssetBundleMasterManifest.csv` (data, MIT) is the cheapest way to widen this.
- **Spurts gated off:** "Hmm," in text plus a `hmm thinking` spurt might double up; unverifiable without
  hardware, and the SIM strips spurts before its external TTS.
- **Icons gated off:** the four confirmed values are calendar cues; the natural first user is a
  reminder line, not free chat.

---

## 2. The behavior planner

The floor maps **words** to tags. The planner scores the line's **job** — its `RemoteDialog.DialogAct` — and
stages a performance from it, validates every id, and lets an author rehearse it on the SIM. Its four
promises, each tested in [`test_performance.py`](../../../sim/tests/test_performance.py): it emits a
structure, not strings; a brain may suggest ids but never authorize them; it always degrades to the floor;
it adds no model call and no measurable latency.

### 2.1 What the child sees

A rule classifier assigns one of the 22 acts; an act profile (`performance.ACT_PROFILES`) stages the body.
Examples: a `factual_question` holds its gaze; `appreciation` celebrates; an `apology` gets
`Bht_Idle_Listening` (the least-searching tree — **no id lowers a gaze**); `backchannelling` ("mm-hm") gets
**no arm gesture** plus the attentive tree (**no nod id exists**). `timeout` is a turn state, reachable only
via `ctx={"timed_out": True}`.

**The words outrank the act for mood.** The floor's mood cues are what shipped content actually used
("Oops." → Shy, "Oh!" → Surprised), so the act profile only fills the silence: it supplies a face for lines
whose words score Neutral.

### 2.2 The `Performance` object

`plan()` returns a frozen `Performance`: a tuple of `Beat`s plus line-level `mood`, `mood_intensity`,
`dialog_act`, `emotion`, `signal`, and `dropped` (ids `validate()` refused). A `Beat` is one run of words
performed in one state — sentences sub-split at clause punctuation and at the talking-gesture stride — with
slots `mood`, `mood_intensity`, `gesture`, `tree`, `gaze`, `icon`, `sfx`, `spurt`, `usel`, `break_after`.

- `render()` is the **only** function that mints a mark, and every mark falls on a beat boundary. The
  terminal `Gesture_None` and the `icons-v2` clear are derived by `render()`, not stored as beats.
- `Beat.gaze` is a closed enum over `vocab.GAZE_TREES`, not a direction. Widening it needs a markup verb
  we have not found or a robot-side IPC path (`LookAtMeRequest`, see
  [`perception-pipeline.md`](../../reverse-engineering/runtime/perception-pipeline.md)).
- Budget guards: `MAX_PLAN_CHARS = 2000` (longer lines are declined to the floor), `MAX_BEATS = 96`,
  `MAX_MOOD_MARKS = 2` (§2.5).

### 2.3 Contract changes

[`ai-seam.md`](../ai-seam.md) §② already specified the destination: `RemoteChatOutput` carries `markup`,
`mood`, `mood_intensity`, `dialog_act`, `emotion`, `signals`. The wire needed nothing new; our side did:

| # | Change | Where |
|--:|---|---|
| C1 | `Reply` has `mood_intensity`, `emotion`, `signal`, `gesture`, `gaze`, `icon`, `sfx`, `performance` beside `mood`/`dialog_act` | [`moxie_sdk/types.py`](../../../mqtt/moxie_sdk/types.py) |
| C2 | `ReplyChunk` has `mood`, `dialog_act`, `mood_intensity`, `emotion`, `signal`, `performance`, so a streamed answer can carry scored output | same |
| C3 | `build_chat_response` emits all five scored fields | [`moxie_sdk/wire.py`](../../../mqtt/moxie_sdk/wire.py) |
| C4 | streamed chunks pass their scored fields through | `moxie_runtime/turns.py` |
| C5 | every published line is scored: `_stage` feeds the app's own fields to the planner as hints and fills what the app left `None`. An app's scored fields still pass `validate()` — they cannot authorize an uncatalogued id | `moxie_runtime/turns.py` |
| C6 | `markup` is derived, never authored: `render(validate(plan(text)))` | the seam |
| C7 | the preview hook (§2.4) | supervisor + console |

**Known gap — C6 does not hold on the model path.** `_stage` speaks an app's authored markup verbatim (S1),
and `LLMApp` authors the floor's `annotate` markup on every reply and chunk. So on the brain a real
deployment runs, `MOXIE_EXPRESSIVE=planner` changes the scored fields but not the performance. C6 holds for
apps that author no markup (echo, content extensions, the preview hook). This is pinned, not fixed:
`test_the_model_path_performs_the_floors_markup` in
[`test_sil_performance_e2e.py`](../../../sim/tests/test_sil_performance_e2e.py) goes red the day it
changes. Closing it means deciding whether an expressive `LLMApp` should stop authoring markup when the
planner is on. Corollary: because only `LLMApp` streams, no published streamed chunk carries planner markup
today.

### 2.4 The SIM as the preview client

There is no SIM-specific API. The console's 🎬 Rehearsal card ([`server/static/js/perform.js`](../../../server/static/js/perform.js))
posts `{text, speak, icons, sfx}` to `POST /local/robots/{device_id}/preview`
([`server/moxie_server/routes/console.py`](../../../server/moxie_server/routes/console.py)), which proxies to
the supervisor's `POST /preview` ([`status_http.py`](../../../mqtt/supervisor/moxie_runtime/status_http.py))
→ `MoxieRuntime.preview` ([`fleet.py`](../../../mqtt/supervisor/moxie_runtime/fleet.py)).

- The line is planned, validated and published as an ordinary `commands/remote_chat` — whatever is
  subscribed as that device performs it (browser SIM, `virtual_moxie.py`, a paired robot).
- No brain call, no history, no memory, no turn record. The output-side safety check still runs.
- `speak` defaults to false, so a preview spends no voice call unless asked.
- The response is the staged `Performance` plus `dropped`; the console draws it per beat with refused ids
  flagged.

### 2.5 How it is tested

| Layer | Test |
|---|---|
| Planner goldens | [`sim/tests/goldens/performance.json`](../../../sim/tests/goldens/performance.json): one line per dialog act (22), as a JSON `Performance` **and** the markup it renders to. Written by [`sim/tools/build_performance_goldens.py`](../../../sim/tools/build_performance_goldens.py); tests read the committed file |
| Validator | property test with mutated performances: no non-catalog id gets through; drops (and counts) on the hot path, raises only in strict mode |
| Renderer | the floor's invariants (words unchanged, well-formed, rate limits) apply unchanged |
| Streaming | scored fields on every chunk; at most **one mood transition per line** (`MAX_MOOD_MARKS = 2`); a chunk after the first plans no mood |
| Degradation | fault injection at `plan`, `validate`, `render`, plus the budget breaker — each lands on the floor |
| SIM | [`sim/test_performance_render.mjs`](../../../sim/test_performance_render.mjs) plays all 22 goldens through the real `bridge.js` (no browser) and writes `sim/artifacts/performance-contact-sheet.html` |
| End to end | [`test_sil_performance_e2e.py`](../../../sim/tests/test_sil_performance_e2e.py) on a real broker: scored fields on single and streamed turns, the preview path, the C6 pin; `sim/run_smoke.sh --expect-scored` in the stack smoke |
| Mutation | [`sim/tools/performance_mutation_check.py`](../../../sim/tools/performance_mutation_check.py): 39 mutations, each must turn a test red (run by hand) |

### 2.6 Degradation — always down to the floor

`plan()` returns a `Performance`, returns `None`, raises, or blows its budget. In every case but the first
the seam answers with `annotate()` and an identical wire shape. `PLAN_BUDGET_MS = 8.0` per line; after
`PLAN_BUDGET_STRIKES = 3` over-budget lines in a row the seam latches to the floor for the process. A planner
must never add a model call to the hot path.

### 2.7 Phases

**P0 · the floor** and **P1 · the planner** are shipped (§1, §2.1–§2.6). P1's acceptance, as met:

- (a) 22 dialog-act goldens, as JSON and as markup.
- (b) 0 unknown ids over a ~300-line corpus (goldens, content modules, filler, generated lines).
- (c) scored fields on every published turn, streamed included, asserted through the real runtime.
- (d) all 22 acts render on the SIM through the real `bridge.js` and perform differently
  (`test_performance_render.mjs`).
- (e) fault injection at each stage, each landing on the floor.
- (f) **no first-audio regression.** A bench of the seam put the planner at p95 ≈ 0.25–0.56 ms against
  the floor's 0.15–0.29 ms. The wire experiment ([`sim/tools/first_audio_ab.py`](../../../sim/tools/first_audio_ab.py),
  real broker, one supervisor boot per arm) found planner and floor within ~2 ms of each other on a
  controlled stub brain — inside a single arm's own spread. Live-gateway runs vary by ~800 ms within one arm,
  so they can only bound the cost. Audio there is the local tone synth, not a gateway voice.
- (g) `ai-seam.md` §② carries the `Performance` → scored-output mapping.

**P2 · learned / model-assisted (proposal, not scheduled).**

- *Problem:* the act classifier is a rule engine over cue phrases and sentence shape. It cannot read
  context or sarcasm, and it calls an unfamiliar declarative `statement_non_opinion`. Icons, SFX and spurts
  are never populated; `auto_tags[]`, `sentiment` and `perplexity` stay empty on the wire.
- *Approach:* either the brain returns the performance itself (the expressive JSON grows `dialog_act`,
  `gesture`, `gaze`, `icon`, `sfx`), or a small **local** classifier scores the line. Same validator, same
  renderer, same budget; the floor still answers when the budget blows.
- *Acceptance:* beats P1 on a blind human "feels alive" score in a live A/B; 0 unknown ids over ≥ 500
  **live** lines (P1's 0 was over a generated corpus, a different bar); first-audio unchanged; every
  model-chosen id passes the same `validate()`.
- *Open questions:* how to close the C6 gap (§2.3) on the model path; whether a hardware capture can settle
  spurt doubling and unknown-id behavior before SFX/spurts are enabled.

**Out of scope.** Barge-in and STT partials (a different seam). Visemes / `TTSMark[]` are the TTS side of
expressiveness — see [`visemes.md`](visemes.md).

---
📖 [Docs index](../../README.md) · [Backlog briefs](README.md) · [OpenMoxie feature audit](../openmoxie-feature-audit.md) · [AI seam](../ai-seam.md) · [Behavior markup (RE)](../../reverse-engineering/runtime/behavior-markup.md)
