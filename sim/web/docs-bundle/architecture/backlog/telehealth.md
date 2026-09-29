# Telehealth: "Be Moxie", where a remote operator drives the body

**Status:** shipped. Implemented in `mqtt/moxie_sdk/telehealth.py` (wire + vocabulary) and `mqtt/supervisor/moxie_runtime/telehealth.py` (runtime verbs). Tested by `sim/tests/test_telehealth.py`, `test_telehealth_runtime.py`, `test_telehealth_view.py`, `test_console_telehealth.py`, `sim/test_bridge.mjs` and `sim/run_smoke.sh --telehealth`. It has never been run against a physical robot (§6).

A person somewhere else can speak through Moxie: a parent on a work trip, a grandparent, a speech
therapist. The operator types a line, picks a mood and an intensity, and the robot says it. While the
mode is on, the robot's own brain is switched off. The operator sees what the child says as text only.

The feature is item ADOPT #7 in the [OpenMoxie feature audit](../openmoxie-feature-audit.md). The
wire contract is [`mqtt-and-conversation.md` §3.9](../mqtt-and-conversation.md).

**Clean-room.** The protocol comes from our own recovered corpus: the
[telehealth protocol page](../../reverse-engineering/protocol/telehealth.md) (the
`embodied.telehealth.TeleHealth.proto` recovered from the v24.10.803 image),
[`boot-and-launcher.md`](../../reverse-engineering/firmware/boot-and-launcher.md) (the `STATE_TELEBRAIN`
launcher state) and [`proto-catalog.md`](../../reverse-engineering/protocol/proto-catalog.md).
OpenMoxie's puppet page (`views.py::puppet_api`, `templates/hive/puppet.html`) is prior art, cited by
path. We ported its behaviour (enable, disable, speak with mood and intensity, interrupt, and a state
poll) and none of its code. See [`ATTRIBUTION.md`](../../../ATTRIBUTION.md).

## 0. What the recovered protocol gives us

- **The mode.** `RobotCloudConfig.moxie_mode = 21`, an enum
  `MoxieMode { DEFAULT_MODE = 0; TELEHEALTH = 1; }`. `build_robot_cloud_config` emits it by name. The
  launcher's `STATE_TELEBRAIN` runs perception and MAINAPP **without the on-device brain**: the remote
  human is the brain.
- **The messages.**

  ```proto
  enum Action     { UNKNOWN_ACTION=0; START_SESSION=1; PLAY_OUTPUT=2; END_SESSION=3; UPDATE_STATE=4; INTERRUPT=5; }
  enum RobotState { UNKNOWN_STATE=0;  READY=1; IN_SESSION=2; EXITING=3; }
  message Output            { line_id=1; repeated line_params=2; text=3; markup=4; }
  message TelehealthMessage { timestamp=1; action=2; output=3; state=4; session_id=5;
                              software_version=100; module_name=101; }
  message TelehealthRobotCommand { command=1; message=2; }   // cloud -> robot
  message TelehealthRobotEvent   { subtopic=1; message=2; }  // robot -> cloud
  ```

- **Transport.** Cloud to robot, JSON on `/devices/{id}/commands/telehealth`. Robot to cloud, on
  `events/client-service-activity-log` with `subtopic: "telehealth"`, carrying the robot's `state`.
- **Session shape.** `START_SESSION → (PLAY_OUTPUT | INTERRUPT)* → END_SESSION`. The robot reports
  `READY → IN_SESSION → EXITING → READY`.
- **`Output.markup` is the full behaviour language.** It is the same grammar our markup seam already
  produces for the brain, which is why this feature was cheap.

**What the corpus does not establish.** Each of these is a named assumption, and the code is written to
be safe whichever way it turns out:

| # | Open question | How the design copes |
|---|---|---|
| B1 | Does `moxie_mode: "TELEHEALTH"` in `/config` actually put the robot into `STATE_TELEBRAIN`? OpenMoxie does exactly this with real robots (field-proven), but no capture shows the trigger. | Kept behind one set of constants (`MOXIE_MODE_KEY`, `TELEHEALTH_MOXIE_MODE`), so a contradicting capture is a one-line fix. |
| B2 | What `INTERRUPT` does physically: a clean cut, a fade, or nothing mid-phoneme | We send it and claim nothing about the result. |
| B3 | Whether a brain-less robot still sends `events/remote-chat` | Remote chat is ignored during a session (§2.5). |
| B4 | Whether bedtime suppresses `PLAY_OUTPUT` | The operator gets a warning and the line is sent anyway (§2.4). |
| B5 | Whether `Output.line_id` / `line_params` resolve against on-board content | Never emitted. We have no catalog of those ids. |

## 1. Where it lives

| Piece | File |
|---|---|
| Pure wire and vocabulary: `ACTIONS`, `STATES`, `build_telehealth_command`, `parse_telehealth_event`, `validate_mood`, `validate_intensity`, `moods()`, `TRANSCRIPT_MAX = 200` | [`mqtt/moxie_sdk/telehealth.py`](../../../mqtt/moxie_sdk/telehealth.py) |
| Runtime verbs and per-robot state | [`mqtt/supervisor/moxie_runtime/telehealth.py`](../../../mqtt/supervisor/moxie_runtime/telehealth.py) |
| Ingesting the robot's state report (`subtopic == "telehealth"`) and refusing the brain during a session | [`moxie_runtime/turns.py`](../../../mqtt/supervisor/moxie_runtime/turns.py) |
| The child's words into the transcript (from STT) | [`moxie_runtime/voice.py`](../../../mqtt/supervisor/moxie_runtime/voice.py) |
| `GET` / `POST /telehealth?device_id=…` on the supervisor status server | [`moxie_runtime/status_http.py`](../../../mqtt/supervisor/moxie_runtime/status_http.py) |
| Bedtime check (`in_bedtime(cfg, now_local)`) | [`moxie_sdk/cloud_config.py`](../../../mqtt/moxie_sdk/cloud_config.py) |
| Markup: the seam (planner, with the floor as fallback) and the floor's `intensity` override | [`mqtt/supervisor/markup.py`](../../../mqtt/supervisor/markup.py) `make_markup`, [`moxie_sdk/automarkup.py`](../../../mqtt/moxie_sdk/automarkup.py) `annotate(..., intensity=)` |
| Console proxy: `GET` / `POST /local/robots/{id}/telehealth`, plus `normalize_telehealth` | [`server/moxie_server/routes/console.py`](../../../server/moxie_server/routes/console.py), [`fleet/cards.py`](../../../server/moxie_server/fleet/cards.py) |
| The "Be Moxie" card | [`server/static/index.html`](../../../server/static/index.html) (`#telehealth-card`), [`server/static/js/perform.js`](../../../server/static/js/perform.js) |
| Browser SIM: a `commands/telehealth` subscription that routes through the `remote_chat` rendering path, plus the state report back | [`sim/web/bridge/index.js`](../../../sim/web/bridge/index.js), [`sim/web/bridge/activity.js`](../../../sim/web/bridge/activity.js) |
| SIL robot: `--telehealth` | [`sim/virtual_moxie.py`](../../../sim/virtual_moxie.py), [`sim/run_smoke.sh`](../../../sim/run_smoke.sh) |

## 2. How it works

### 2.1 The wire module

`build_telehealth_command(action, *, text, markup, session_id, timestamp)` returns
`{"command": "telehealth", "message": {timestamp, action[, output][, session_id]}}`. The rules:

- An action outside `ACTIONS` raises. `UNKNOWN_ACTION` also raises, because it is the proto's zero value,
  not a command.
- `output` is present **only** for `PLAY_OUTPUT`. For that action `text` is required, and `markup` is
  omitted rather than sent empty.
- `line_id` and `line_params` are never emitted (B5).
- `timestamp` is in milliseconds.

`parse_telehealth_event` accepts the wrapped event or a bare message and never raises, because it runs on
the MQTT loop. It returns `{state, session_id, at, known}`. An unknown state name, or a numeric one, is
kept verbatim with `known: False`. It is never coerced to a recovered state.

`validate_mood` accepts a name, an alias or an `ePlaybackMood` id, and **raises on an unknown label**.
The picker is a closed vocabulary with a person at the keyboard, and silently turning their choice into a
neutral face would be worse than refusing it. `validate_intensity` returns an integer in 0 to
`vocab.MAX_INTENSITY` (2) and clamps anything out of range. It is deliberately not a 0.0–1.0 float.

### 2.2 Runtime verbs

| Verb (status HTTP `action`) | Runtime method | Does |
|---|---|---|
| `enable` / `disable` | `telehealth_enable(device_id, on)` | Writes `moxie_mode` (1 or 0) into **this robot's** override layer via `update_config`, which re-pushes `/config` with `moxie_mode: "TELEHEALTH"` or `"DEFAULT_MODE"`. Disabling ends an open session first. `sanitize_config_overrides` does not whitelist `moxie_mode`, so a fleet-wide edit cannot put every robot into puppet mode. |
| `start` / `end` / `state` | `telehealth_session(device_id, action)` | Publishes `START_SESSION` (minting a `ths-…` session id; requires the mode to be on), `END_SESSION` (clears the id) or `UPDATE_STATE`. |
| `speak` | `telehealth_speak(device_id, text, *, mood, intensity, gesture)` | The hot path (§2.3). |
| `interrupt` | `telehealth_interrupt(device_id)` | Publishes `INTERRUPT` with no `output`. The mode must be on. |
| (GET) | `telehealth_view(device_id)` | Returns `{ok, enabled, online, session_id, in_session, state, state_at, in_bedtime, transcript, moods, max_intensity}`. |
| (activity log) | `ingest_telehealth_event` | Stores the reported state and timestamp. |

Every verb first refuses a device that is not on the permit list (*"This robot is waiting to be
permitted"*) and a device that is not connected. The view is the exception to the second rule: a robot
that dropped offline keeps its transcript and reads `online: false`. A device the runtime has never seen
returns the same "unknown device" shape as the other views, and the console turns that into a 404.

`state` stays empty until the robot actually reports one. The card then shows "never reported". It never
invents `READY`. A session id reported by the robot is adopted only from an `IN_SESSION` report, which
lets a restarted supervisor resume a session. A late `EXITING` report can never resurrect a session we
have already closed.

### 2.3 The speak path, in order

1. **Permit check.** A pending robot gets `{"ok": false, "error": "not permitted"}` and nothing is
   published.
2. **Mode check.** If the robot's effective config is not `TELEHEALTH`, the call is refused with
   *"Turn on Be Moxie first."* Sending `PLAY_OUTPUT` to a robot still running its own brain would put two
   voices in one mouth.
3. **Validation.** An empty line, an unknown mood or a non-numeric intensity is refused back to the
   operator.
4. **Safety.** The operator's text is checked with `_assess(text, role=MOXIE)`, the same classifier that
   guards the brain's own output. We check it even though a person wrote it, for three reasons:
   - the `MOXIE` role is defined as *text about to be spoken to a child*, whoever wrote it;
   - the operator is by design a third party;
   - the parent's safety journal must record everything that was said to their child.

   The handling differs from the brain path on purpose. **A BLOCK returns 400 with the verdict's
   categories and labels, and nothing is spoken**, so the operator can rephrase. It is never replaced
   with a redirect, the way a model's unsafe line is. A FLAG is spoken and journaled.
5. **Markup.** `make_markup(line, mood_hint, gesture_hint, intensity, turn_key=line_key, chunk_index=0)`
   goes through the runtime's markup seam ([`mqtt/supervisor/markup.py`](../../../mqtt/supervisor/markup.py)).
   That is the same path a brain reply takes: the behaviour planner by default (`MOXIE_EXPRESSIVE`),
   with the `automarkup.annotate` floor as its fallback. Both receive the operator's mood and intensity.
   On `annotate`, `intensity` is a keyword-only override that defaults to `None`, which leaves
   `sim/tests/goldens/annotate.json` unchanged. **Every line is
   its own utterance.** `line_key` is `"{session_id}#{n}"` and every line is chunk 0, for two reasons:
   the mood mark rides chunk 0 only, and the SIM voice player requires every utterance to start at
   chunk 0. Telehealth never streams.
6. **Publish** `commands/telehealth` with `action: "PLAY_OUTPUT"`, `output: {text, markup}` and the
   session id.
7. **Voice.** `_maybe_synthesize(device_id, markup, event_id=line_key, chunk_num=0)` gives the SIM a
   voice. A real robot synthesizes speech itself and ignores this.
8. **Transcript and notes.** The line is appended to the per-device transcript as `who: "operator"`, and
   the runtime notes `🎭 said '…'`.

### 2.4 How it interacts with other features

| Feature | Decision |
|---|---|
| Pairing gate | Every verb checks `is_permitted` first. A pending robot can never be puppeted, and the card says why instead of showing dead controls. |
| Safety gate | See §2.3. |
| Streaming | Not used. There is no brain and no token stream. `INTERRUPT` is the barge-in primitive instead. |
| Bedtime (B4) | `in_bedtime` is a **warning, not a gate**. The card shows *"this robot is inside its bedtime window; the line may not be delivered"* and the line is still sent. Guessing either way would be worse than telling the operator the truth. |
| Memory | Operator lines are **never** written into the child's conversation memory. Otherwise Moxie would later "recall" things it never thought. The transcript is the record. |
| Fleet config | `moxie_mode` exists only in the per-robot layer (see §2.2). |

### 2.5 The child's side: text only

While a session is open, the child's words reach the operator as text. `feed_stt` already transcribes
the robot's microphone, and during a session it also appends `{who: "child", text, at}` to a per-device
ring (`deque(maxlen=200)`). The ring is in memory only, never written through `store.py`, and it is
cleared when the runtime restarts. It also obeys the safety journal's `LoggingPolicy`: under `NO_DATA`
it keeps operator lines only. Outside a session, nothing of the child is kept.

**No audio and no video from the child reach the operator.** There are three reasons:

1. `LoggingPolicy` is the contract's line between text and media (`NO_DATA` / `NO_MEDIA` / `FULL`), and
   nothing in it authorizes piping a child's live microphone into a third party's browser.
2. The recovered protocol is text out, status back. `TelehealthMessage` has no audio field, so an audio
   path would be our own invention.
3. A transcript and a live listen-in on a child's room are different products with different consent
   stories.

The honest limit is that a real clinician wants to *hear* the child, and this does not let them. That is
a stated non-goal, not an oversight.

**No brain during a session (B3).** If a remote-chat event arrives while a session is open, the runtime
does not call the brain. It answers nothing and notes *"ignored a remote-chat during a session"*. A brain
reply racing the operator is the one failure that would look broken to a child. Once the session ends,
the brain answers again.

### 2.6 The console card and the SIM

The card holds:

- **A mode switch.** Turning it on sends `enable`, then immediately `start`, because that is what a
  person means by "let me talk through Moxie now". Turning it off sends `disable`, which ends the
  session. The card states plainly what the switch does.
- **A line box** with Send.
- **A mood picker** with the 11 recovered `ePlaybackMood` names, served by the runtime from
  `telehealth.moods()`, so the picker cannot offer a mood the robot lacks.
- **An intensity control** with three steps, 0, 1 and 2.
- **An Interrupt button.**
- **A start/end session button.**
- **The live transcript**, with child lines and operator lines styled differently.
- **The robot's reported state**, with its timestamp, or "never reported".
- **The bedtime warning.**

The card refreshes on the existing `refreshLive()` cadence, with no new poller. A safety block shows its
reason inline.

In the browser SIM, `bridge/index.js` subscribes to `commands/telehealth` and routes
`message.output.{text, markup}` through the same `handleRemoteChat` path a brain reply uses, so the
avatar performs the operator's line exactly as it would a brain reply. `bridge/activity.js` reports the
SIM's own telehealth state back on the activity log. The headless SIL robot (`virtual_moxie.py
--telehealth`) does the same over a real broker.

## 3. Tests

| File | Covers |
|---|---|
| [`sim/tests/test_telehealth.py`](../../../sim/tests/test_telehealth.py) | Pure wire: JSON shape per action; `output` only on `PLAY_OUTPUT`; `line_id` never emitted; unknown or zero action raises; every emitted key is a real field of the compiled `TeleHealth_pb2` (the recovered proto is the oracle); state parsing, including unknown and numeric states; mood and intensity validation; mode constants mirror `MoxieMode`; the fleet layer cannot carry `moxie_mode` |
| [`sim/tests/test_telehealth_runtime.py`](../../../sim/tests/test_telehealth_runtime.py) | Real `MoxieRuntime` with a fake transport: one `PLAY_OUTPUT` plus one `commands/tts` per line; each line is its own utterance carrying its mood; the mode gate, the permit gate and the not-connected refusal; safety BLOCK (400, never spoken, never rewritten) and FLAG (spoken and journaled); no brain during a session (B3); state ingest, including the `EXITING`-resurrection and restart-resume cases; transcript bounds and `NO_DATA`; the bedtime warning; the status HTTP verbs |
| [`sim/tests/test_telehealth_view.py`](../../../sim/tests/test_telehealth_view.py) | `fleet.normalize_telehealth`: "never reported", unknown states flagged, pending robot, block and receipt shapes, junk input |
| [`sim/tests/test_console_telehealth.py`](../../../sim/tests/test_console_telehealth.py) | Console ↔ real runtime: enable → speak → interrupt → disable, a safety 400 with a reason, the mode-off 400, the config re-push |
| [`sim/test_bridge.mjs`](../../../sim/test_bridge.mjs) | A `commands/telehealth` `PLAY_OUTPUT` drives the avatar the same way the equivalent `remote_chat` does |
| `sim/run_smoke.sh --telehealth` | SIL over a real broker: the virtual robot receives the operator's line, reports `IN_SESSION`, and `/telehealth` shows it |
| [`sim/tests/test_live_telehealth_voice.py`](../../../sim/tests/test_live_telehealth_voice.py) | Opt-in and live: the operator's line comes out as real gateway speech, with exactly one TTS request (skips without a gateway key) |
| `sim/tests/test_automarkup.py` + `goldens/annotate.json` | The `intensity` parameter is additive: the goldens stay byte-identical |

## 4. Known gaps

- **No owner guide** under `docs/guides/`.
- **No session-length ceiling.** A session stays open until someone ends it or disables the mode.
- **Delivery is never confirmed.** The card reports what was *sent* and what the robot *reported*,
  never that a line was heard.

## 5. Risks

| # | Risk | Mitigation |
|--:|---|---|
| R1 | B1 is wrong and the mode switch is cosmetic, so a line reaches a robot still running its brain | One set of constants to change. The mode gate means we never send to a robot we believe is unprepared. If B1 fails, the symptom is two voices, which is loud, not silent. |
| R2 | An operator line races a brain reply | No brain calls during a session (§2.5), plus the mode gate |
| R3 | A puppet channel is a **social** attack surface: anyone who reaches the console can speak to the child in Moxie's voice | Safety classifier on every line; every line journaled as `who: "operator"`; the permit gate plus the console's own auth. The mitigation is that a parent can read every line afterwards. |
| R4 | Someone guesses `line_id` values (B5) | The builder refuses to emit them |
| R5 | A bedtime line silently never plays (B4) | Warn, send, and show the robot's own reported state |
| R6 | The transcript is a new store of a child's words | In memory, bounded to 200 entries, never persisted; child lines obey `LoggingPolicy` (§2.5) |
| R7 | Changes to the SIM and SIL touch the harness that gates every PR | The new subscription is additive, and the existing bridge and SIL tests stay green |

## 6. What only a physical robot can settle

Nothing in this feature has been exercised against a real Moxie. The open questions:

1. **B1**: does `moxie_mode: "TELEHEALTH"` in `/config` enter `STATE_TELEBRAIN`?
2. **B2**: what does `INTERRUPT` do to a line already being spoken?
3. **B3**: does a brain-less robot still send `events/remote-chat`?
4. **B4**: does bedtime suppress `PLAY_OUTPUT`?
5. **B5**: does `line_id` resolve against on-board content, and what are the ids?
6. Does the robot report `IN_SESSION` on the activity-log subtopic at all, or only on a state change?
   That decides whether the state line on the card is informative or decorative.

---
📖 [Backlog index](README.md) · [OpenMoxie feature audit](../openmoxie-feature-audit.md) · [Telehealth protocol (RE)](../../reverse-engineering/protocol/telehealth.md) · [Boot & launcher](../../reverse-engineering/firmware/boot-and-launcher.md) · [MQTT & conversation](../mqtt-and-conversation.md) · [Config & telemetry contract](../config-and-telemetry-contract.md) · [Docs index](../../README.md)
