# 12 — MQTT / "Talking" Layer Spec (the Robot Cloud half)

**Scope.** This is the *robot cloud* half of the Moxie saver: the MQTT/IoT service that
makes a paired Moxie actually **wake, listen, think, and talk**. The REST parent-app half
(this repo's `server/`) is already built and is covered in maps 01–04. This spec is a
clean, implementation-ready description of what OpenMoxie
(`github.com/jbeghtol/openmoxie`, MIT) does, verified first-hand against a clone of the
source, plus a concrete plan to build our own **fully local** equivalent on a single
**NVIDIA Jetson Orin AGX** appliance (no internet, local LLM/STT, on-device TTS).

> **Verification note.** Everything below was read directly from the OpenMoxie source
> (commit cloned 2026-08-26). File paths are relative to the OpenMoxie repo root unless
> noted. Where a fact came from prose docs rather than code it is marked *(doc)*.

---

## 0. TL;DR of the mechanism

1. Moxie firmware has two "wifi-app" QR stages. **QR #1** = Wi-Fi + pairing (our `server/`
   already generates this — the `"PA"+protobuf` `StartPairingQR`). **QR #2** = the
   **endpoint-relocation QR** that tells the robot *which MQTTS host to talk to*. This spec
   owns QR #2 and everything after it.
2. QR #2 payload is **plain JSON** (no prefix):
   `{"debug": {"command": "om", "param": "<base64(ServiceConfiguration2 protobuf)>"}}`.
3. The robot connects to your **mosquitto** broker on **8883 (MQTTS)**, anonymous, and
   starts publishing/subscribing Google-Cloud-IoT-style topics `"/devices/{d_uuid}/..."`.
4. Your service acts as the **supervisor**: it subscribes to every device's events/state,
   pushes each robot a **config JSON** on connect, answers **schedule / mentor-behavior /
   license** queries, transcribes audio (**STT**), runs the conversation (**LLM**), renders
   text→**markup**, and sends `remote_chat` responses back.
5. **TTS is on the robot.** Moxie synthesizes its own voice on-device from `text` + `markup`.
   The cloud never sends audio to the robot. (See §5.3 — this materially changes the
   "local TTS" requirement.)

---

## 1. The endpoint-config / "migration" QR (QR #2)

### 1.1 Where it is built and served

- **Builder:** `MoxieServer.get_endpoint_qr_data()` — `site/hive/mqtt/moxie_server.py`
  (lines ~379-391).
- **View:** `endpoint_qr(request)` — `site/hive/views.py:137`, wired at
  `site/hive/urls.py:15` as `path('endpoint/', views.endpoint_qr, name='endpoint_qr')`.
  The view is literally `qrcode.make(get_instance().get_endpoint_qr_data())` returned as a
  PNG. So the URL is **`/hive/endpoint/`**.

Exact builder code:

```python
def get_endpoint_qr_data(self):
    hiveconfig = HiveConfiguration.objects.filter(name="default").first()
    scfg = ServiceConfiguration2()
    scfg.gcp_project   = self._mqtt_project_id     # "openmoxie" (see §1.5 for "o")
    scfg.mqtt_host     = hiveconfig.external_host or self._mqtt_endpoint
    scfg.override_port = self._port                # 8883
    scfg.disable_verify = not self._cert_required  # True for self-signed
    scfg_base64 = base64.b64encode(scfg.SerializeToString()).decode('utf-8')
    qr = {"debug": {"command": "om", "param": scfg_base64}}
    return json.dumps(qr)
```

### 1.2 Wire format (exact)

Top-level QR string = **UTF-8 JSON, no prefix** (contrast QR #1 which is `"PA"+base64`):

```json
{"debug": {"command": "om", "param": "<base64>"}}
```

The robot parses this JSON as an `embodied.unity.QRMultiDecoder` message
(`site/hive/mqtt/protos/embodied/wifiapp/QRCommands_pb2.py`):

```proto
message QRMultiDecoder {
  QRCommand debug        = 1;   // <-- the "debug" key
  bytes     encoded_proto = 2;
}
message QRCommand {           // embodied.unity.QRCommand
  uint64      timestamp = 1;
  string      code      = 2;
  string      param     = 3;   // NOTE: builder uses "param" but sends it under command? see below
  IOTEndpoint endpoint  = 4;   // enum, embodied.logging.IOTEndpoint
  string      command   = 5;   // <-- "om"
  string      software_version = 100;
  string      module_name      = 101;
}
```

- `debug.command = "om"` selects the OpenMoxie relocation handler on the robot.
- `debug.param` = base64 of a serialized `ServiceConfiguration2`.
- The robot side (`RightPoint::on_QRCommand`, closed firmware): JSON→QRMultiDecoder →
  reads `debug.command=="om"` → base64-decodes `debug.param` → parses
  `ServiceConfiguration2` → rewrites its persistent endpoint config → reboots the wifi-app
  into "connect to this MQTTS host" mode.

### 1.3 `ServiceConfiguration2` schema (the payload) — EXACT field numbers/types

From `embodied.logging.Cloud2` (`site/hive/mqtt/protos/embodied/logging/Cloud2_pb2.py`):

```proto
message ServiceConfiguration2 {           // embodied.logging.ServiceConfiguration2
  string        gcp_project        = 1;   // MQTT client-id prefix / JWT audience
  string        webservice_root    = 2;   // base URL for OTA/http-token web service (optional)
  string        webservice_pin     = 3;
  bool          disable_sync       = 4;
  bool          disable_log_upload = 5;
  string        endpoint           = 6;
  uint64        timestamp          = 7;
  string        mqtt_host          = 8;   // <-- broker hostname/IP
  ConnectionType connection_type   = 9;   // enum below
  IOTEndpoint   endpoint_id        = 10;  // enum, embodied.logging.IOTEndpoint
  uint32        override_port      = 11;  // <-- 8883
  bool          disable_verify     = 12;  // <-- true = accept self-signed TLS
  string        software_version   = 100;
  string        module_name        = 101;

  enum ConnectionType { GOOGLE_IOT = 0; EMBODIED_IOT = 1; EMBODIED_LOCAL = 2; }
}
```

**OpenMoxie only sets 4 fields**: `gcp_project`, `mqtt_host`, `override_port`,
`disable_verify`. (For the OTA path it also sets `webservice_root` — see §2.3.)
`connection_type`/`endpoint_id` default to 0 and are not needed.

### 1.4 Concrete example (computed from the real proto — reproduce this ourselves)

For `gcp_project="o"`, `mqtt_host="192.168.1.50"`, `override_port=8883`,
`disable_verify=true`:

```
ServiceConfiguration2 wire bytes (22 bytes), hex:
  0a 01 6f                                  field1 (gcp_project) len1 = "o"
  42 0c 31 39 32 2e 31 36 38 2e 31 2e 35 30 field8 (mqtt_host) len12 = "192.168.1.50"
  58 b3 45                                  field11 (override_port) varint = 8883
  60 01                                     field12 (disable_verify) varint = true

base64(param) = CgFvQgwxOTIuMTY4LjEuNTBYs0VgAQ==

Final QR string (73 chars):
  {"debug": {"command": "om", "param": "CgFvQgwxOTIuMTY4LjEuNTBYs0VgAQ=="}}
```

Field-tag reminder (protobuf `(field<<3)|wiretype`): `0x0A`=f1/len, `0x42`=f8/len,
`0x58`=f11/varint, `0x60`=f12/varint. We can generate QR #2 with a hand-rolled encoder
(same approach as our existing `tools/pairing/moxie_qr.py`) — **no protobuf runtime
needed**; four fields, all trivial wire types.

### 1.5 QR-density constraint — `gcp_project` shortened to `"o"` *(doc + code)*

Moxie's camera "can struggle with dense QR codes" (`doc/RemoteModuleAPI.md`). The default
project id is the 8-char `"openmoxie"`; the maintainer shortens `gcp_project` to the single
char **`"o"`** to reduce QR density. `gcp_project` is only used as (a) the MQTT client-id
prefix and (b) the JWT `aud` — and since the broker is anonymous, its value is cosmetic, so
`"o"` is safe. **Recommendation: we default `gcp_project="o"` and keep `mqtt_host` short
(a LAN IP, not a long hostname).** Our 73-char example above already fits an easily-scanned
low-density QR.

---

## 2. Firmware gate — 801 vs 803

### 2.1 What changed

- **24.10.801** — the *first* firmware that supports the custom endpoint/relocation QR at
  all ("enabled a custom cloud services endpoint configuration to relocate Moxie to
  alternate … MQTTS services", `doc/MoxieOverview.md`). Pre-801 robots **cannot be
  relocated** by QR #2 and are out of scope (they'd need an OTA first, which itself needs a
  reachable trusted endpoint — hard).
- **24.10.803** — **honors `disable_verify=true`**, i.e. it will accept a **self-signed**
  broker certificate. This is the sweet spot OpenMoxie targets, and why the default
  `mqtt.Dockerfile` ships a self-signed CA and it "just works."
- **801 practical gate:** 801 still effectively wants a **publicly/peer-verified** TLS cert
  on the broker (`disable_verify` not fully honored). The OpenMoxie maintainer ran a
  **Let's Encrypt**-fronted mosquitto to serve 801 bots, and used that to push an **OTA
  801→803** so that self-signed then worked (`doc/RemoteModuleAPI.md`, "OTA Update" §; see
  §2.3).

### 2.2 The `IOTEndpoint` enum (has `OPEN_MOXIE`)

From `embodied.logging.enums` (`.../logging/enums_pb2.py`) — the QR `endpoint` field type:

```proto
enum IOTEndpoint {
  IOT_DEFAULT = 0;  GOOGLE_DEVELOP = 1;  GOOGLE_STAGING = 2;  GOOGLE_PRODUCTION = 3;
  EMBODIED_DEVELOP = 4;  EMBODIED_STAGING = 5;  EMBODIED_PRODUCTION = 6;
  EMBODIED_HIPAA = 7;  EMBODIED_LOCAL = 8;  EMBODIED_CHINA = 9;  EMBODIED_HK = 10;
  OPEN_MOXIE = 11;   // <-- the community endpoint slot
}
```

(OpenMoxie leaves `endpoint_id` at default 0 in the relocation QR; the enum matters mostly
for the **pairing** QR `StartPairingQR.endpoint` in QR #1 and matches our map-03 finding
that the pairing QR carries a single `iot_endpoint` byte. `OPEN_MOXIE = 11` = `0x0B`.)

### 2.3 Checking a robot's firmware + the OTA lever

- **How to read the version:** the robot self-reports `software_version` in several places:
  the `QRDiagnosticData` proto (`robot_uuid`, `rsa_pub`, `cloud_connected`, `cloud_project`,
  `software_version=100`), the device **state** JSON it publishes to `/devices/{id}/state`,
  and its device-log lines. Physically it's also on the robot's own screens / via `adb`.
  Our appliance can log the version the first time a bot connects (from state) and surface
  it in the UI.
- **OTA 801→803** *(doc)*: put `"ota_update": {"id":"rls","version":"…-v24.10.803-rls-robot"}`
  in the robot config; when the version differs the robot requests an HTTP token, then GETs
  `{webservice_root}/api/ota_updates/{id}/url?access_token=…&robot_id=…`, expecting JSON
  `{"url": "<signed OTA image URL>"}`. Requires (a) `webservice_root` set in QR #2, (b)
  `_PROVIDE_HTTP_TOKENS=True` (server returns a bogus `"notoken"`), (c) a real signed OTA
  image (the maintainer hosted the genuine 803 image). **For us this is an optional
  advanced path**; primary target is bots already on **803**.

### 2.4 What our appliance must present

- **Target (803):** mosquitto on **8883** with a **self-signed CA**; QR #2 carries
  `disable_verify=true`. No public DNS, no internet. This is the default and what we build.
- **801 owners:** need either a Let's-Encrypt-fronted broker (needs a domain + port 8883
  reachable) to connect, or a one-time OTA to 803. Document as a caveat; not the happy path.

---

## 3. MQTT broker setup

### 3.1 mosquitto config (verbatim from `site/data/openmoxie.conf`)

```conf
listener 8883
cafile   /mosquitto/config/keys/ca.crt
certfile /mosquitto/config/keys/mosquitto.crt
keyfile  /mosquitto/config/keys/mosquitto.key
allow_anonymous true
log_dest file /mosquitto/log/mosquitto.log
log_dest topic          # <-- publishes broker log lines to $SYS/broker/log/#
```

- Keys live in the repo `keys/` (`ca.crt`, `mosquitto.crt`, `mosquitto.key`, plus `.csr`).
  These are **self-signed** and shipped for convenience — we should generate our own CA per
  appliance.
- `mqtt.Dockerfile` = `eclipse-mosquitto:latest` + copy keys + copy conf. Exposes 8883.
- **`log_dest topic` is the connect/disconnect trick:** mosquitto mirrors its log to
  `$SYS/broker/log/#`. The supervisor subscribes to `$SYS/broker/log/#` and `$SYS/broker/
  clients/#` and regex-scans the **N** (notice) log lines for connects/disconnects (§3.4).

### 3.2 Topic structure (Google-Cloud-IoT shaped)

`{d_uuid}` = the robot's device id, always prefixed `d_` (e.g. `d_<uuid>`).

Supervisor **subscribes** (in `on_connect`, `moxie_server.py`):

```
/devices/+/events/#          all robot-published events
/devices/+/state             robot state snapshots
$SYS/broker/clients/#        client-count metrics
$SYS/broker/log/#            connect/disconnect detection
```

Supervisor **publishes** to a specific robot:

```
/devices/{d_uuid}/config              config JSON (pushed on connect / on change)
/devices/{d_uuid}/commands/{name}     JSON commands (see §3.5)
/devices/{d_uuid}/commands/zmq        binary ZMQ-over-MQTT (STT subscribe, replies)
```

### 3.3 Event names (robot → cloud, `/devices/{id}/events/{name}`)

Handled in `MoxieServer.on_device_event`:

| event name | purpose |
|---|---|
| `remote-chat` (and `remote-chat-staging`) | RemoteChatRequest — the conversation channel. Two backends: `backend:"data"` + `query:"modules"` → return remote module list; `backend:"router"` → a conversational turn (see §4). |
| `client-service-activity-log` | multiplexed via `subtopic`: `query:"schedule"`, `query:"mentor_behaviors"`, `query:"license"` (robot asks for e.g. `google_speech` key), `mentor_behavior` reports, and `subtopic:"telehealth"` puppet state. |
| `zmq` | ZMQ bridge: payload = `"{proto.full_name}:" + protobuf_bytes`. Used for `embodied.perception.audio.zmqSTTRequest` (mic audio). |
| `device-logs` | per-robot log records (`tag`, `message`). |
| `client-service-http-token` | robot requests an HTTP access token (only answered if `_PROVIDE_HTTP_TOKENS=True`, returns `"notoken"`). |

### 3.4 Connect/disconnect detection (regex on broker log)

```python
connect_pattern    = r"connected from (.*) as (d_[a-f0-9-]+)"
disconnect_pattern = r"Client (d_[a-f0-9-]+) (closed its connection|disconnected)"
```
On connect → `on_device_connect`: load DB records, **sleep 1.0s** (let client settle),
push config, then send a ZMQ `ProtoSubscribe` telling the robot to stream STT audio
(subscribe to `embodied.perception.audio.zmqSTTRequest`). There's also a fallback
`check_device_connect` that fires on the first event/state if the log line was missed.

### 3.5 Command names (cloud → robot, `/devices/{id}/commands/{name}`)

| command | payload shape / purpose |
|---|---|
| `config` | (actually the `/config` topic, not `/commands/config`) full robot config JSON, see §3.6 |
| `remote_chat` | RemoteChatResponse — the turn's `output.text` + `output.markup` + `response_actions` |
| `query_result` | answers to `schedule`, `mentor_behaviors`, `license` queries |
| `http_token` | `{"command":"http_token","http_token":"notoken"}` (OTA/http; optional) |
| `telehealth` | puppet-mode `PLAY_OUTPUT` / `INTERRUPT` / `START_SESSION` etc. |
| `wakeup` | `{"command":"wakeup"}` — wake a `wake_button_enabled` robot from screen-off |
| `zmq` | binary; e.g. `ProtoSubscribe` (STT enable) and `zmqSTTResponse` (transcripts) |

### 3.6 The robot config JSON pushed on connect

Built by `RobotData.build_config` = **common config/settings** (from `HiveConfiguration`)
deep-merged with per-device overrides. Defaults (`site/hive/mqtt/robot_data.py`):

```jsonc
// config
{
  "pairing_status": "paired",      // MUST stay "paired" or robot won't run
  "audio_volume": "0.6",
  "screen_brightness": "1.0",
  "audio_wake_set": "off",
  "timezone_id": "America/Los_Angeles",
  "child_pii": { "nickname": "Pat", "input_speed": 0.0 },
  "settings": {
    "props": {
      "touch_wake":"1","wake_alarms":"1","wake_button":"1","doa_range":"80",
      "target_all":"1","gcp_upload_disable":"1",
      "local_stt":"on",             // on-device ASR for wake phrases
      "max_enroll":"2","audio_wake":"1","cloud_schedule_reset_threshold":"5",
      "debug_whiteboard":"0","brain_entrances_available":"1",
      "mqtt_files":"0","file_sync_wait":"0","default_loglevel":"warning"
    }
  }
}
```
Important extra keys *(doc, `MoxieOverview.md`)*: `settings.props.stt` = **`"4"`** streams
audio to us over ZMQ (our STT path); `"0"` uses an on-device Google service account instead.
`wake_button_enabled`/`touch_wake_enabled` keep the robot network-connected. `child_pii`
is the decrypted child record (nickname, birthday ISO8601, `volume_preference`,
`face_options`, `input_speed`). **This is where our `server/` child profile feeds in.**

---

## 3b. Robot identity / auth

- **Robot side (Google-IoT JWT):** each robot has
  `/sdcard/EmbodiedStaticData/PERSISTENT_DATA/uuid.txt` (its uuid) and
  `.../rightpoint/RS256.key` (an RSA private key). It connects to MQTT with
  username=anything and password = an **RS256 JWT** signed by that key, claims
  `{aud: gcp_project, iat, exp:+1h}` (classic GCP-IoT auth). Client-id = `d_{uuid}`.
- **Broker side:** `allow_anonymous true` → **the JWT is never actually verified.** In the
  LAN model auth is effectively open; any device on the LAN can join. The JWT machinery is
  vestigial but harmless.
- **Supervisor side:** `RobotCredentials(fake_monitor=True)`
  (`site/hive/mqtt/robot_credentials.py`) short-circuits everything: `device_uuid =
  "supervisor"`, `create_jwt()` returns the literal string `"supervisor"`. The server
  connects `username="unknown", password="supervisor"` and subscribes to all devices. The
  non-fake path (pulling `uuid.txt`/`RS256.key` off a robot via `adb`, or from
  `MOXIE_CREDENTIALS` env) exists only for impersonating a *specific* robot in tests.
- **Our takeaway:** we replicate the anonymous LAN model. We don't need real JWT
  verification for v1. If we later want per-robot ACLs we can turn on mosquitto password/ACL
  and verify the JWT against each robot's public key (robot exposes `rsa_pub` in
  `QRDiagnosticData`).

---

## 4. Conversation flow (end to end)

### 4.1 The turn objects

- **RemoteChatRequest (RCR)** — robot→cloud JSON on `events/remote-chat`. Key fields:
  `event_id`, `command` (`prompt` | `continue`/`reprompt` | `notify`), `backend`
  (`router` for convo, `data` for module list), `module_id`, `content_id`, `speech`
  (recognized user text), `extra_lines[]` (each `{context_type, text}`; `context_type=="input"`
  = user utterance), `recommend.exits[]` (what to launch next), `input_vars` (e.g.
  `$eb_qr_value` for scanned launch cards).
- **RemoteChatResponse** — cloud→robot on `commands/remote_chat`. Built by
  `Volley.create_response` (`site/hive/mqtt/volley.py`): `command:"remote_chat"`, `result`,
  `backend`, `event_id`, **`output:{text, markup}`**, `response_actions:[{output_type,
  action, module_id, content_id, …}]` (+ legacy singular `response_action`), `fallback`.
- **`Volley`** = the per-turn object wrapping request+response+session/robot data. Helpers:
  `set_output(text, markup, output_type)`, `add_response_action(...)`,
  `add_launch_or_exit()`, `add_execution_action(fname, args)` (calls robot functions like
  `eb_enable_qr`), `update_subscriptions([...])`, and **`ingest_action_tags()`** which turns
  inline `<launch:MOD:CID>` / `<exit>` / `<sleep>` tags in the LLM text into structured
  `response_actions` (and strips them from the spoken text).

### 4.2 "Notify" context tracking

Moxie is authoritative about what it actually *said*. After every utterance it sends
`command:"notify"` RCRs. `ChatSession.ingest_notify` (`conversations.py`) rebuilds true
history: `extra_lines[].text` where `context_type=="input"` → `user` turns; `speech` (minus
`animation:`/`silent:` lines) → `assistant` turns. This keeps LLM context correct even when
the child speaks across multiple VAD windows before Moxie replies.

### 4.3 STT audio over ZMQ-over-MQTT

- Robot streams mic audio as `embodied.perception.audio.zmqSTTRequest` protos on
  `events/zmq`, payload = `b"embodied.perception.audio.zmqSTTRequest:" + bytes`.
- `zmqSTTRequest` fields: `timestamp` (u64), `vad` (enum `UNKNOWN/START_OF_SPEECH/SPEECH/
  END_OF_SPEECH`), **`audio_content`** (bytes, raw **16 kHz PCM16 mono**), `uuid` (session id).
- `STTHandler.handle_zmq` (`zmq_stt_handler.py`) keys sessions by `(device_id, uuid)`,
  concatenates `audio_content` until `vad==END_OF_SPEECH`, then transcribes in a worker.
- Reply = `zmqSTTResponse` (`type=FINAL`, `speech`, `confidence`, `start/end_timestamp`,
  `alternatives[]`, `error_code/message`) sent back on `commands/zmq`.

### 4.4 Full turn, end to end

```
child speaks → robot VAD → zmqSTTRequest frames (events/zmq)
   → STTHandler accumulates → [STT engine] → zmqSTTResponse (commands/zmq) back to robot
robot forms RemoteChatRequest{speech:"…", backend:"router"} (events/remote-chat)
   → RemoteChat.handle_request → get/make ChatSession for module_id/content_id
   → check GlobalResponses (regex "brain entrances") ; else:
   → SingleContextChatSession.next_response(): [LLM] chat.completions(system prompt + history)
   → Volley.set_output(text) ; automarkup(text) → markup ; ingest_action_tags()
   → send RemoteChatResponse (commands/remote_chat) {output:{text,markup}, response_actions}
robot renders markup → [on-device TTS + animation] → speaks
robot sends notify RCRs of what it said → history updated
```

### 4.5 The automarkup engine (why it matters)

`site/hive/automarkup/` is a vendored copy of **Embodied's text→behavior markup engine**.
`process(text, rules, mood_and_intensity)` → an XML-ish string mixing SSML voice tags with
Embodied `<mark name="cmd:…">` behavior/gesture/sound/screen commands. It uses ML rules
(`automarkup/ml/…`) to auto-insert body language, mood, pauses, and prosody so Moxie's
delivery is **expressive** rather than a flat TTS read-out. `RemoteChat.make_markup` runs it
on every LLM response that lacks markup. **This is the single biggest driver of "Moxie feels
alive."** We should keep this module as-is (it's MIT-vendored, pure Python) — it's a big win
we get for free. Markup reference: `doc/Markup.md`, asset labels in
`doc/AssetBundleMasterManifest.csv`.

---

## 5. Local AI integration points (the part we change)

Only **three** seams matter, and they're small.

### 5.1 LLM — `ai_factory.py` + `conversations.py`

Current (`site/hive/mqtt/ai_factory.py`, 14 lines):

```python
from openai import OpenAI
_OPENAPI_KEY=None
def set_openai_key(key): ...
def create_openai(): return OpenAI(api_key=_OPENAPI_KEY)
```

Consumed in `conversations.py` as:

```python
client = create_openai()
resp = client.chat.completions.create(
    model=self._model, messages=context+history,
    max_tokens=self._max_tokens, temperature=self._temperature
).choices[0].message.content
```

**The swap is one line: add `base_url`.** The whole app already speaks the OpenAI
chat-completions dialect, so *any* OpenAI-compatible gateway works unchanged:

```python
def create_openai():
    return OpenAI(base_url=OPENAI_BASE_URL,        # e.g. http://127.0.0.1:4000/v1  (LiteLLM)
                  api_key=OPENAI_API_KEY or "sk-local")
```

- Point `base_url` at a **local LiteLLM gateway / vLLM / Ollama (`/v1`) / LM Studio**.
  `model` names live per-conversation in the DB (`SinglePromptChat.model`, default
  `gpt-3.5-turbo`; some content uses `gpt-4o`) — we remap these to local model ids (a
  LiteLLM `model_list` alias is the cleanest: keep the DB strings, map them in LiteLLM).
- Optional cloud fallback: LiteLLM router with a local primary + OpenAI fallback (LLM only).
- **Never hard-wire OpenAI.** Make base_url + key config on `HiveConfiguration` (we already
  have `openai_api_key`; add `openai_base_url` and per-role model overrides).

### 5.2 STT — `zmq_stt_handler.py`

Current `STTSession.perform()` wraps the accumulated PCM into a WAV (`soundfile`, 16 kHz,
PCM_16) and calls `client.audio.transcriptions.create(file=…, model="whisper-1",
response_format="verbose_json", timestamp_granularities=["word"])`, then fills
`zmqSTTResponse.speech` + word-timestamped start/end.

**Interface we must satisfy:** given `int16` PCM @16 kHz mono, return `{text, words:[{start,
end}]}` (word times optional but used for `start_timestamp`/`end_timestamp`; we can
approximate). Two clean local options:

- **faster-whisper** on the Orin GPU (CTranslate2, CUDA) — feed the numpy `int16→float32`
  buffer directly (no WAV needed), `segments, info = model.transcribe(...)`; concatenate
  segment text, use segment/word timestamps. Fastest, recommended.
- Or a local **whisper OpenAI-compatible** endpoint (e.g. `whisper.cpp` server, faster-
  whisper-server) — then STT can reuse the same `base_url` trick as the LLM.

Keep robot setting `stt:"4"` so audio streams to us; leave `local_stt:"on"` (that's just the
on-device wake-word ASR, unrelated).

### 5.3 TTS — **there is none server-side; Moxie synthesizes on-device**

Confirmed: no TTS/synth code anywhere in OpenMoxie (grep clean). The cloud sends only
`output.text` + `output.markup`; **Moxie's Unity stack renders the voice locally** using its
built-in synth (an SSML-subset voice engine baked into firmware). Implications:

- **We do NOT need to build or run a TTS server.** The "local TTS" requirement is satisfied
  by the robot itself, for free, and it's the *correct* Moxie voice (kids recognize it).
- **The voice Moxie "expects" is its own** — you cannot swap it from the cloud; you can only
  shape delivery via **markup/SSML** (`<prosody>`, mood/intensity, behavior marks). So our
  "TTS" investment is really **markup quality** (§4.5), not audio synthesis.
- (If someone ever wanted a *different* voice, that'd require sending pre-rendered audio via
  the telehealth/asset path — out of scope, and it loses Moxie's expressive animation sync.)

### 5.4 Net change to go fully local

| Seam | File | Change |
|---|---|---|
| LLM | `ai_factory.py` | add `base_url` (LiteLLM/vLLM/Ollama/LM Studio); map DB model names |
| STT | `zmq_stt_handler.py` | replace `client.audio.transcriptions` with faster-whisper (or local OpenAI-compat STT) |
| TTS | — | **nothing** (on-robot) |
| Config | `models.py`/`HiveConfiguration` | add `openai_base_url`, STT engine/model, model overrides |

Everything else (markup, volley, scheduler, content) is model-agnostic and stays.

---

## 6. Content modules

- **Conversation modules** are DB rows (`SinglePromptChat`: `module_id`, `content_id`
  [pipe-separated for multiple], `prompt` [a Django template], `opener`, `model`,
  `max_tokens`, `temperature`, `max_history`, `max_volleys`, and optional `code` = Python
  `pre_process`/`post_process`/`complete_handler`/`notify_handler` filters). Seeded from
  `content_modules/*.json` (MoxieGo, MemoryChat, MoxieTimers, MoxieTime) and
  `site/data/default_conversations.json` via `manage.py init_data`.
- **Native/on-robot modules** (chatscript baked into firmware) are just *scheduled* by
  module_id — the ~23 in `content/data.py` `RECOMMENDABLE_MODULES`: AFFIRM, AB,
  ANIMALEXERCISE, BODYSCAN, RDL, BREATHINGSHAPES, COMPOSING, FACES, FF, GUIDEDVIS, JOKE,
  JUKEBOX, MENTORSAYS, NONSENSE, DANCE, DRAW, STORYTELLING, PASSWORDGAME, READ,
  SCAVENGERHUNT, STORY, AUDMED, WHIMSY, plus **DM** (Daily Missions, with mission→content-id
  sets in `DM_MISSION_CONTENT_IDS`). These run entirely on the robot; the cloud only launches
  them and provides schedule/mentor-behavior data.
- **Schedules** (`MoxieSchedule`, `doc/MoxieOverview.md`): `provided_schedule` list +
  `generate` block (auto-extends the day) + `hub_config` (MOXIE_GO hub) + `chat_request` +
  `wake_module` + `alarm_module`. Defaults: `default`, `only_chat`, `no_onboarding`.
- **Launch QR codes** — pre-generated in `site/data/qr/launch_*.png`, generator
  `site/data/qr/extract.py`. Format is dead simple **plain text**:
  `GO<launch:MODULE_ID>` (e.g. `GO<launch:DM>`). Robot scans it during a MOXIE_GO/QR-enabled
  module (`eb_enable_qr` + `eb-qr-event` subscription) and launches that module. We can
  generate these ourselves trivially.
- **Missing content** *(README)*: newer modules **Ocean Explorer, Animal Faces, Story
  Maker** aren't supported. Some face customization assets crash Unity and are excluded.
  Global "brain entrance" launch phrases live in `GlobalResponse` rows / `global_responses.py`.

---

## 7. Integration architecture for our repo

### 7.1 How the two halves sit on one Orin box

```
                        NVIDIA Jetson Orin AGX (no internet)
 ┌───────────────────────────────────────────────────────────────────────┐
 │  server/   (EXISTS, FastAPI)          mqtt/   (NEW, this spec)          │
 │  REST/HTTPS parent-app                MQTT supervisor + AI              │
 │  • account/child/wifi                 • endpoint QR #2 (/endpoint)      │
 │  • pairing QR #1 ("PA"+proto)         • robot config push              │
 │  • recovery-key crypto                • schedule / MBH / license        │
 │  • web client (phone)                 • conversation (RCR/volley)       │
 │        │  shared config + child       • STT handler (ZMQ)              │
 │        │  profile + robot registry    • automarkup                     │
 │        ▼                                     │                          │
 │  ┌───────────────┐                           ▼                          │
 │  │ shared store  │◄──────────────► ┌──────────────────┐                │
 │  │ (sqlite/pg)   │                 │ AI runtime        │               │
 │  └───────────────┘                 │ LiteLLM/vLLM/     │               │
 │                                     │ Ollama + whisper  │ (localhost)   │
 │  mosquitto :8883 TLS (self-signed CA) ◄──── Moxie (LAN, fw 803) ───────│
 └───────────────────────────────────────────────────────────────────────┘
   phone ──HTTPS──► server/ (QR #1)     Moxie ──MQTTS──► mosquitto ──► mqtt/
```

- **Two processes, one appliance, one config source.** Our `server/` already owns identity,
  child profiles, Wi-Fi, and QR #1. The `mqtt/` component owns QR #2 and the live
  conversation. They **share**: (a) the child profile → feeds `child_pii` in the robot
  config; (b) the robot registry / device binding (map `d_uuid` ↔ our account/child); (c)
  one config file for hosts/ports/keys/AI endpoints.
- **Handshake between halves:** `server/` issues Wi-Fi/pairing QR #1 (Moxie joins Wi-Fi +
  binds to child). `mqtt/` issues endpoint QR #2 (Moxie relocates to our broker). On first
  MQTT connect, `mqtt/` looks up the child profile from the shared store to build the config
  it pushes. Insights/activity data (mentor behaviors, state) flow back into the shared store
  for `server/`'s UI (roadmap item in README).
- **Broker:** run mosquitto natively on the Orin (or a container) on 8883 with a
  per-appliance self-signed CA we generate at first boot. `disable_verify=true` in QR #2.
- **AI runtime:** LiteLLM gateway on `127.0.0.1` fronting a local model (vLLM/Ollama) +
  faster-whisper in-process (or as a sidecar). All localhost; nothing leaves the box.

### 7.2 What we take vs rebuild from OpenMoxie

- **Take mostly as-is (MIT):** `automarkup/` (huge value), the proto definitions
  (`protos/embodied/**`), `volley.py`, `conversations.py`, `scheduler.py`,
  `global_responses.py`, `robot_data.py`, the mosquitto conf, the content JSON + default
  schedules, launch-QR generator.
- **Rewrite/adapt:** `ai_factory.py` (base_url), `zmq_stt_handler.py` (faster-whisper),
  `moxie_server.py` (keep the MQTT logic; wire `get_endpoint_qr_data` and config-build to
  our shared store instead of Django `HiveConfiguration`), and swap Django-ORM data access
  for our store if we don't want to carry Django. **Simplest v1: keep OpenMoxie's Django
  `hive` app intact as our `mqtt/` service and just (a) change the 2 AI seams and (b) point
  its `HiveConfiguration`/child data at values our `server/` writes.** Lower risk than a
  from-scratch rewrite.

### 7.3 Recommended phased build order

1. **Broker up.** mosquitto 8883 + self-signed CA on the Orin; confirm a robot (fw 803) can
   TLS-connect anonymously. (Generate our own CA.)
2. **Supervisor + relocation.** Stand up the MQTT supervisor (subscribe all, `$SYS` log
   connect-detect). Serve **QR #2** (`/endpoint`, `gcp_project="o"`, our LAN IP,
   `disable_verify=true`). Get a robot to relocate and show as "connected."
3. **Config + schedule.** Push robot config (with real `child_pii` from `server/`) on
   connect; answer `schedule` / `mentor_behaviors` / `license` queries. Robot should wake
   into a session and run **native** modules (DM/READ/etc.) with no AI yet.
4. **Local LLM.** Wire `ai_factory` → LiteLLM/local model; get `OPENMOXIE_CHAT`
   conversations working end to end (text+automarkup). Verify markup expressiveness.
5. **Local STT.** Replace Whisper API with faster-whisper on the Orin GPU; verify the full
   speak→STT→LLM→markup→speak loop with `stt:"4"`.
6. **Content + QR launch cards.** Import content modules, generate `GO<launch:…>` cards,
   author a couple of custom conversations. Optional: telehealth/puppet mode.
7. **Glue to `server/`.** Shared config + device registry + push activity/insights back for
   the parent-app UI. Optional advanced: 801→803 OTA lever for older bots.

---

## Appendix A — File map (OpenMoxie, for reference)

| Concern | File |
|---|---|
| MQTT supervisor, topics, endpoint QR | `site/hive/mqtt/moxie_server.py` |
| Endpoint QR view / URL | `site/hive/views.py:137`, `site/hive/urls.py:15` (`/hive/endpoint/`) |
| ServiceConfiguration2 / IOTEndpoint protos | `site/hive/mqtt/protos/embodied/logging/{Cloud2,enums}_pb2.py` |
| QRCommand / StartPairingQR / QRMultiDecoder protos | `.../protos/embodied/wifiapp/QRCommands_pb2.py` |
| STT proto | `.../protos/embodied/perception/audio/zmqSTT_pb2.py` |
| LLM factory (swap point) | `site/hive/mqtt/ai_factory.py` |
| STT handler (swap point) | `site/hive/mqtt/zmq_stt_handler.py` |
| Conversation / LLM calls | `site/hive/mqtt/conversations.py`, `moxie_remote_chat.py` |
| Turn object | `site/hive/mqtt/volley.py` |
| Robot config/schedule/state store | `site/hive/mqtt/robot_data.py`, `scheduler.py` |
| Credentials / JWT | `site/hive/mqtt/robot_credentials.py` |
| Text→behavior markup | `site/hive/automarkup/**` |
| mosquitto config / keys | `site/data/openmoxie.conf`, `keys/`, `mqtt.Dockerfile` |
| Content / modules / launch QRs | `content_modules/*.json`, `site/hive/content/data.py`, `site/data/qr/*` |
| Docs | `doc/MoxieOverview.md`, `doc/Markup.md`, `doc/RemoteModuleAPI.md`, `doc/ContentModules.md` |

## Appendix B — Endpoint QR #2 generator (pseudocode, no protobuf runtime needed)

```python
def build_endpoint_qr(mqtt_host, port=8883, gcp_project="o", disable_verify=True):
    def tag(field, wire): return bytes([(field << 3) | wire])
    def s(field, val):    b = val.encode(); return tag(field,2)+varint(len(b))+b
    def v(field, val):    return tag(field,0)+varint(val)
    scfg  = s(1, gcp_project) + s(8, mqtt_host) + v(11, port)
    scfg += v(12, 1 if disable_verify else 0)
    param = base64.b64encode(scfg).decode()
    return json.dumps({"debug": {"command": "om", "param": param}})
# build_endpoint_qr("192.168.1.50") ==
#   {"debug": {"command": "om", "param": "CgFvQgwxOTIuMTY4LjEuNTBYs0VgAQ=="}}
```
