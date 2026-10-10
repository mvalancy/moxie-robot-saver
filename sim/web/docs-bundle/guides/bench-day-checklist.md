# Bench-day checklist: record the first session with a real Moxie

For the person with a real Moxie on the bench and this stack running. It records the session from
the first connect, then walks every claim the code makes about a real robot that no robot has
confirmed yet, in the order a session reaches them. Pair the robot with the
[bench runbook](bench-runbook.md); use this page beside it.

Every row below is built to the recovered protocol and tested against doubles, and **none of it
has been seen on a physical Moxie**. The recording turns each row into an answer you can read,
replay and share.

## Before you start

- **Check the badge.** Under the code box on Moxie's face, "EmbodiedProduction" or "OpenMoxie"
  means firmware 801 or 803. No badge means older than 801: no code can move it, and the session
  waits on the [flash-first path](revive-your-moxie.md#path-c-flash-an-older-robot-first)
  ([live notes](../debugging/live-hardware-debug.md#the-wall-we-hit)).
- **Bring the stack up** as the [runbook](bench-runbook.md#before-you-start) says.
- **Plan about an hour.** Lifecycle comes last, and two of its steps are only for a robot you can
  set up again from scratch (see [Not on your only robot](#not-on-your-only-robot)).

## Start the recorder

Start it before the robot connects, and leave it running for the whole session:

```bash
docker compose exec supervisor python -m moxie_sdk.wire_record --out /data/wire/bench.jsonl
```

It connects to the broker as the supervisor does and subscribes to the robot topics and the
broker's log, and it never publishes. Its first lines say what the file holds: what the child
says, the child's name, and the robot's address, id and Wi-Fi name. The file is created readable
by its owner only, in the supervisor's data volume. Ctrl-C stops it; `--minutes N` stops it for
you. Raw microphone audio is kept only with `--audio`; by default each clip keeps its length and
loudness. How it works: [`wire_record.py`](../../mqtt/moxie_sdk/wire_record.py) `FILTERS`.

Note the time you start it. The timeline counts seconds from that moment, and your notes about
the face and the voice need the same clock.

## What the recording cannot see

The wire carries words, ids and timings. It cannot see the face, hear the voice or read the
screen. Watch and listen for these yourself, and write down roughly when:

- **The voice:** which voice spoke, whether a sentence was cut, whether the child's name was said.
- **The face:** moods, gestures and a changed look.
- **The screen:** what Moxie shows after a config change, an unpair or a reset.
- **What was heard:** compare what was said with the console feed. The timeline never prints
  anyone's words.

## The session, in order

Each row gives the claim and where it is made, what to do, what the timeline shows, and the
sentence to update afterwards. Read the timeline with:

```bash
docker compose exec -T supervisor cat /data/wire/bench.jsonl | python3 sim/tools/wire_timeline.py -
```

### Connect

| Claim | Do | The timeline shows | Then update |
|---|---|---|---|
| An 803 robot opens TLS on 8883 and accepts the stack's self-signed certificate through `disable_verify` ([server code](bench-runbook.md#2-the-server-code), [network trust](../reverse-engineering/protocol/network-trust.md#the-disable_verify-escape-hatch)). | Show the server code. | `New connection from [address] on port [port].`, then `New client connected from [address] as d_robot-1`. An `OpenSSL Error` line instead means the robot refused the certificate. | [Runbook §3](bench-runbook.md#3-read-the-connection-monitor): the alert text "has not yet been seen in this monitor with a real robot". |
| The supervisor answers a robot's connect line with config about one second later ([§3.4](../architecture/mqtt-and-conversation.md#34-connect-and-disconnect-detection); [`connection.py`](../../mqtt/supervisor/moxie_runtime/connection.py) `_device_connect`). | Nothing: it happens on connect. | A `config` line about 1 s after the connect line. Until you add the robot, that config holds no child data and its microphone is not asked for. | §3.4's "unverified on our hardware". |
| A robot's id is `d_<uuid>` and stays the same across reconnects (A17 in the [assumption ledger](../architecture/backlog/production-hardening.md#9-assumption-ledger-the-rows-that-still-matter); [`constants.py`](../../mqtt/supervisor/moxie_runtime/constants.py) `CONNECT_RE`). | Power-cycle the robot once. | The second connect line names the same robot: `d_robot-1` again, never `d_robot-2`. | A17. |
| The robot's MQTT username is unknown (A2 in [broker auth §6](../architecture/backlog/security-broker-auth.md#6-what-only-a-physical-robot-can-answer)). | Nothing. | Not shown: the shareable view drops it. On the appliance, find `connected from` in the raw file and read the `u'…'` part. | §6 item 2: whether it is fixed text or carries an id. Never paste the value if it identifies the robot. |

### Claim and config

| Claim | Do | The timeline shows | Then update |
|---|---|---|---|
| **Add to my account** sends the child's settings at once, the name included ([runbook §4](bench-runbook.md#4-add-it-to-your-account)). | Press **Add to my account**, then listen to the next hello. | A second `config` line right after the click, then `ears ProtoSubscribe zmqSTTRequest` when the appliance has a Listening engine. | Runbook §4: what a physical Moxie does with the name "has not been observed yet". |
| A robot accepts a config push in the middle of a session (A6). | Change the volume in **⚙️ Settings** while Moxie is awake. | Another `config` line, and whether a `/state` follows it. Listen for the volume. | A6 in the [ledger](../architecture/backlog/production-hardening.md#9-assumption-ledger-the-rows-that-still-matter). |
| A duplicate config push is harmless (A7). | Save the same settings twice. | Two `config` lines: every save pushes. Watch for a restart or a reset. | A7. |
| The robot re-reads `child_pii.nickname` from a push without reconnecting ([the child's name](../architecture/config-and-telemetry-contract.md#the-childs-name-the-parents-record-per-robot)). | Rename the child while Moxie is awake, then start a chat. | A `config` line with no connect line after it. Listen for the new name. | That section's "Unverified on a physical Moxie" note. |
| A new look reaches the face through `face_options` and the `child_pii.id` cache-buster ([appearance](../architecture/config-and-telemetry-contract.md#appearance-the-childs-chosen-face); [`faces.py`](../../mqtt/moxie_sdk/faces.py) `face_child_id`). | Change the look in the console. | A `config` line. Watch the face. | That section's two assumptions. |
| Wake alarms resolve against `timezone_id` on the robot ([wake alarms](../architecture/config-and-telemetry-contract.md#wake-alarms-scheduled-activities-the-json-we-emit)). | Set a wake alarm a few minutes ahead, then let Moxie sleep. | A `config` line. Watch whether Moxie wakes at that local time. | That section's assumptions. |

### Ears

| Claim | Do | The timeline shows | Then update |
|---|---|---|---|
| The robot streams its microphone after the `ProtoSubscribe` ask ([plug point B](../architecture/ai-seam.md#this-repos-implementation-plug-point-b); [`voice.py`](../../mqtt/supervisor/moxie_runtime/voice.py) `_subscribe_stt`). | Say a sentence. | `ears ProtoSubscribe zmqSTTRequest; first audio frame +… s`, then `utterance` lines. `no audio after it` means it never streamed. | "No physical Moxie has streamed audio to this appliance yet." |
| The honest ears' levels suit Moxie's far-field microphone ([what the ears refuse to hear](../architecture/ai-seam.md#what-the-ears-refuse-to-hear); [`stt.py`](../../mqtt/moxie_sdk/stt.py) `ROOM_TONE_RMS`; [`config.py`](../../mqtt/config.py) `STT_ROOM_TONE_RMS`). | Say "bye" and "yes" close by and from across the room, then stay silent for a minute. | Each `utterance` line's length in ms and its `rms`; `FINAL empty (heard nothing)` for a dropped clip. | "The thresholds are unverified on a robot": write the levels you measured, then set `MOXIE_STT_ROOM_TONE_RMS` and `MOXIE_STT_MIN_SPEECH_MS` from them. |
| Moxie still hears after a sleep and a wake (C4 in [community signals](../architecture/backlog/community-signals.md#2-findings)). | Put Moxie to sleep, wake it, and speak. | A new `ears ProtoSubscribe` after the wake, and audio frames after it. | C4's status. |

### Turns

| Claim | Do | The timeline shows | Then update |
|---|---|---|---|
| Chunk 0 with `REPLY_PENDING` keeps a turn open until the last chunk ([§4.5](../architecture/mqtt-and-conversation.md#45-slow-brain-a-filler-now-the-real-answer-next-reply_pending); [`turns.py`](../../mqtt/supervisor/moxie_runtime/turns.py) `_handle_stream_turn`). | Ask a question with a long answer. | `chunk 0 pending … chunk N done`. Listen for every sentence, in order. | §4.5's "Unverified on hardware" note. |
| The robot asks again after about 20 s of silence, which decides whether the 60 s `ERROR_OFFLINE` is ever heard (A4; the "A gateway hangs" row of [§4.4](../architecture/backlog/production-hardening.md#44-what-each-failure-looks-like)). | Point the brain at an endpoint that accepts and never answers for one turn, then ask something. | `no reply; the robot asked again N s later`: N is the window. A `result 4 ERROR_OFFLINE` reply shows only when no new request came first. | A4 and that row. |
| Moxie reports what it said with `notify` requests ([§4.2](../architecture/mqtt-and-conversation.md#42-notify-context-tracking)). | Nothing extra. | `N notify reports` on each turn: one per chunk, or one per answer. | §4.2: the cadence a real robot uses. |
| A real robot speaks our `text` and `markup` in its own voice; `commands/tts` audio is for the Sim ([the mechanism](../architecture/mqtt-and-conversation.md#0-the-mechanism-in-five-lines); [`voice.py`](../../mqtt/supervisor/moxie_runtime/voice.py) `set_synthesizer`). | Listen to an answer. | `tts xN` in the traffic line means audio was sent. Only your ears say which voice played. | Line 5 of the mechanism. |
| An empty licence answer does not stop the robot's voice (C1 in [community signals](../architecture/backlog/community-signals.md#2-findings)). | Nothing extra. | An `activity query license` line, and the robot still speaks after it. | C1. |
| A robot parses every reply shape we send ([the wire a robot can read](../architecture/ai-seam.md#the-wire-a-robot-can-read)). | Have an ordinary chat. | `done` on every turn, and no new request right after a reply. | "No physical Moxie has yet parsed a reply from this appliance." |
| Our markup plays: moods, gestures, a face that matches the words ([limits](../architecture/backlog/expressiveness.md#18-limits)). | Watch the face while Moxie answers. | Nothing: the wire cannot see the face. | "No physical Moxie has played our markup." |

### Actions

| Claim | Do | The timeline shows | Then update |
|---|---|---|---|
| `exit_module` ends the activity and `sleep` sends Moxie to sleep ([the ten global commands](../architecture/content-module-contract.md#the-ten-the-real-robot-listened-for); [`wire.py`](../../mqtt/moxie_sdk/wire.py) `encode_action`). | Say "bye Moxie"; later, "go to sleep". | `actions exit_module; next request … in module X`: a new module means Moxie left. After `sleep`, no request until a wake. | "No physical robot has yet been seen leaving the module or going to sleep." |
| **Wake up** on the robot card wakes a sleeping robot ([§3.5](../architecture/mqtt-and-conversation.md#35-command-names-cloud-robot-devicesidcommandsname); [`fleet.py`](../../mqtt/supervisor/moxie_runtime/fleet.py) `WAKEUP_COMMAND`). | Press **Wake up** while the screen is off. | `wakeup x1` in the cloud-to-robot traffic, then the robot's traffic resumes. | The `wakeup` row of §3.5. |

### Presence

| Claim | Do | The timeline shows | Then update |
|---|---|---|---|
| A vision event arrives as the `speech` of a request; a first sighting never greets, a return after 5 minutes does ([§4.7](../architecture/mqtt-and-conversation.md#47-vision-events-and-whether-the-cloud-may-speak-first); [`presence.py`](../../mqtt/supervisor/moxie_runtime/presence.py) `_greeting_for`). | Step out of view for more than 5 minutes, then come back. | `vision eb-lost-target …`, then `vision eb-found-face …` answered `done` (a hello) or `result 6 NOREPLY_ACK`. | [Vision §7.5](../architecture/vision.md#75-what-is-still-not-true). |
| A printed launch card starts an activity ([launch cards §7](../architecture/backlog/qr-launch-cards.md#7-risks-and-the-honest-ceiling); [`launch_cards.py`](../../mqtt/moxie_sdk/launch_cards.py) `decode_event`). | Hold a printed card up to Moxie. | `vision eb-qr-event …` answered with `actions launch`, then a request in the card's module. | Q1 to Q3 of that table. |
| The cloud never speaks first; a hello earned during a turn waits for the next request ([§4.7](../architecture/mqtt-and-conversation.md#47-vision-events-and-whether-the-cloud-may-speak-first)). | Nothing extra. | No `unasked` line: every reply answers a request the robot sent. | §4.7's "Revisit this first if a capture from a physical robot appears." |

### Other traffic

| Claim | Do | The timeline shows | Then update |
|---|---|---|---|
| The robot pulls its day plan at the start of a session and waits for the answer ([how a schedule becomes the day](../architecture/content-module-contract.md#how-a-schedules-entry-becomes-the-day-the-robot-runs)). | Nothing extra. | `activity query schedule; answered +… s`, then requests in the plan's modules. | "Unsettled without hardware" in [the routes](../architecture/content-module-contract.md#the-routes). |
| A real robot sends telemetry packets ([insights §0](../architecture/backlog/insights.md#0-the-ceiling)). | Nothing extra. | `telemetry`, `analytics` or `packet` counts in the robot's traffic line. | §0's first bullet. |
| Telehealth puts the robot in puppet mode and it speaks the operator's line ([telehealth §6](../architecture/backlog/telehealth.md#6-what-only-a-physical-robot-can-settle)). | Start a session from the console and send one line. | `telehealth` in the cloud-to-robot traffic, then `activity subtopic telehealth`. Listen for the line. | B1 to B5 there. |
| A real robot works within the broker's per-robot rules (R6 in [broker auth §6](../architecture/backlog/security-broker-auth.md#6-what-only-a-physical-robot-can-answer)). | Nothing extra. | "Topics the recovered map does not name" lists any unrecovered topic. A denied message never reaches the recorder: count `Denied` lines in `docker compose logs broker`. | §6 item 5. |

### Resilience

| Claim | Do | The timeline shows | Then update |
|---|---|---|---|
| A real Moxie reconnects on its own after a broker restart (A5). | Run `docker compose restart broker` during a chat. | The recorder's own `lost the broker` and `reconnected` lines, then the robot's connect line, a `config` line and the ears' ask again. | A5 in the [ledger](../architecture/backlog/production-hardening.md#9-assumption-ledger-the-rows-that-still-matter). |
| After a supervisor restart, config is pushed again with no robot event ([§3.4](../architecture/mqtt-and-conversation.md#34-connect-and-disconnect-detection); [`connection.py`](../../mqtt/supervisor/moxie_runtime/connection.py) `resume_roster`). | Run `docker compose restart supervisor`. | A `config` line and `ears ProtoSubscribe` with no connect line before them. | §3.4: "What a physical robot does across a broker restart is unverified." |

### Lifecycle, last

| Claim | Do | The timeline shows | Then update |
|---|---|---|---|
| Unpair sends the not-paired settings and the robot stops being served ([built here](../features/robot-lifecycle.md#built-here-unpair-and-factory-reset); [`cloud_config.py`](../../mqtt/moxie_sdk/cloud_config.py) `build_unpaired_cloud_config`). | Press **Unpair this robot**. Watch the screen. | `config pushed (pairing_status unpairing)`; any later request gets one fixed line. | [Not done yet](../features/robot-lifecycle.md#not-done-yet): what a physical Moxie shows. |

## Not on your only robot

> **Two steps can leave a robot needing a full setup again.** The factory reset code
> `{"debug":{"command":"restore_factory"}}` ([the reset code](factory-reset-a-paired-moxie.md#the-reset-code))
> and the `start-systemunpair` and `start-systemsuspend` markup
> ([the command verbs](../reverse-engineering/runtime/behavior-markup.md#the-command-verbs-24))
> are for a second robot only. Nothing in the console sends either markup. Do them last, with the
> recorder running, on a robot you can set up again from scratch.

## After the session

1. **Read the timeline** (the command above) and fill in the "Then update" sentences, with what you
   watched and heard beside each.
2. **Make the shareable copy** if anyone else needs the recording:
   `docker compose exec -T supervisor cat /data/wire/bench.jsonl | python3 sim/tools/wire_timeline.py - --share bench.share.jsonl`.
   It replaces every robot id with `d_robot-1`, `d_robot-2` and drops every address, hostname,
   username and word. It refuses, naming the kind, if anything identifying survives. Read it before
   you paste it.
3. **Replay it** against today's code: `python3 sim/tools/wire_replay.py bench.share.jsonl`. It
   feeds the robot's side of the session through a fresh supervisor with a scripted brain and
   reports every reply whose shape differs from what was recorded
   ([`wire_replay.py`](../../sim/tools/wire_replay.py) `replay`).
4. **Delete the recording:** `docker compose exec supervisor rm /data/wire/bench.jsonl`.

Only a `--share` copy may be pasted into an issue or committed, and a committed one goes in
[`sim/tests/data/wire/`](../../sim/tests/data/wire/README.md), where a test checks it holds no
identity ([`wire_timeline.py`](../../sim/tools/wire_timeline.py) `share_records`). The raw file
never leaves the machine.

## Evidence

- **Tests.** [`test_wire_record.py`](../../sim/tests/test_wire_record.py) feeds one synthetic
  session through the recorder on a fake client: it never publishes, subscribes to its two filters,
  writes each message's direction and decoded body, keeps no raw audio without `--audio`, never
  writes the broker host, and survives a full queue, its size cap and a full disk. The `--share`
  copy of that session holds none of its ids, addresses, ports, MAC, hostname, username, Wi-Fi name
  or words. The timeline of the committed copy is a golden, and the replay reproduces its reply
  shapes against a fresh runtime.
- **SIL.** The same test records the virtual robot's echo turn through a real broker and the real
  supervisor: `/state`, config, the request and the reply, in order.
- **Not yet seen.** No physical Moxie has connected to this appliance
  ([live notes](../debugging/live-hardware-debug.md#the-wall-we-hit)). Every claim in the tables is
  unverified on a robot until a recording answers it.

---
📖 [Guides index](README.md) · [Bench runbook](bench-runbook.md) · [Docs index](../README.md)
