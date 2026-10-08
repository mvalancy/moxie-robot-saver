# MQTT and conversation contract (the robot cloud)

> **Spec version 1 · robot side stamped to firmware v3.6.4-Zephyr / OTA v24.10.803.**
> One of the six [build contracts](README.md); reads standalone.

This contract covers the half of the backend a **robot** talks to: the endpoint QR that moves a
robot onto our broker, the broker itself, the MQTT topics, and the conversation flow. The phone-side
half is the [REST services contract](rest-api-contract.md). The exact STT, brain and TTS message
shapes are in the [AI seam](ai-seam.md); the `/config` and `/state` documents are in the
[config and telemetry contract](config-and-telemetry-contract.md).

Provenance: OpenMoxie ([jbeghtol/openmoxie](https://github.com/jbeghtol/openmoxie), MIT) was read
first-hand; paths like `site/hive/...` below are relative to that repo. Our implementation is our own
code in [`mqtt/`](../../mqtt/). Facts taken from OpenMoxie's prose docs rather than its code are
marked *(doc)*.

## 0. The mechanism in five lines

1. A robot has two setup QR stages. **QR #1** is Wi-Fi + pairing (`"PA"` + protobuf, served by
   `server/`). **QR #2** is the endpoint QR that tells the robot which MQTTS host to use. This
   contract owns QR #2 and everything after it.
2. QR #2 is plain JSON: `{"debug": {"command": "om", "param": "<base64(ServiceConfiguration2)>"}}`.
3. The robot connects to a mosquitto broker on **8883 (MQTTS)** and uses Google-Cloud-IoT-style
   topics `/devices/{d_uuid}/...`.
4. The **supervisor** subscribes to every robot, pushes each one a config JSON on connect, answers
   schedule / mentor-behavior / license queries, transcribes audio (STT), runs the conversation
   (brain), adds behavior markup, and publishes `remote_chat` responses.
5. The robot can voice a line itself (on-device CereVoice from `text` + `markup`) or play audio the
   cloud renders (`commands/tts`, a `CloudTTSResponse`). See [AI seam §③](ai-seam.md).

## 1. The endpoint QR (QR #2)

### 1.1 Where it is built

- **Ours:** [`tools/pairing/moxie_endpoint_qr.py`](../../tools/pairing/moxie_endpoint_qr.py) and the
  parent web app's *Server pairing* tab.
- **OpenMoxie:** `MoxieServer.get_endpoint_qr_data()` in `site/hive/mqtt/moxie_server.py`, served as
  a PNG at `/hive/endpoint/` (`site/hive/views.py:137`, `site/hive/urls.py:15`):

```python
def get_endpoint_qr_data(self):
    hiveconfig = HiveConfiguration.objects.filter(name="default").first()
    scfg = ServiceConfiguration2()
    scfg.gcp_project   = self._mqtt_project_id     # "openmoxie" (see §1.5 for "o")
    scfg.mqtt_host     = hiveconfig.external_host or self._mqtt_endpoint
    scfg.override_port = self._port                # 8883
    scfg.disable_verify = not self._cert_required  # True for self-signed
    scfg_base64 = base64.b64encode(scfg.SerializeToString()).decode('utf-8')
    return json.dumps({"debug": {"command": "om", "param": scfg_base64}})
```

### 1.2 Wire format

The QR string is UTF-8 JSON with no prefix (QR #1, by contrast, is `"PA"` + base64):

```json
{"debug": {"command": "om", "param": "<base64>"}}
```

The robot parses it as an `embodied.unity.QRMultiDecoder`
(`site/hive/mqtt/protos/embodied/wifiapp/QRCommands_pb2.py`):

```proto
message QRMultiDecoder {
  QRCommand debug        = 1;   // the "debug" key
  bytes     encoded_proto = 2;
}
message QRCommand {             // embodied.unity.QRCommand
  uint64      timestamp = 1;
  string      code      = 2;
  string      param     = 3;
  IOTEndpoint endpoint  = 4;    // embodied.logging.IOTEndpoint
  string      command   = 5;    // "om"
  string      software_version = 100;
  string      module_name      = 101;
}
```

`debug.command = "om"` selects the relocation handler; `debug.param` is a base64 serialized
`ServiceConfiguration2`. On the robot (`RightPoint::on_QRCommand`, closed firmware) the handler
decodes the param, rewrites the persistent endpoint config, and restarts the wifi-app in "connect to
this MQTTS host" mode. The full QR grammar is in
[QR commands](../reverse-engineering/protocol/qr-commands.md).

### 1.3 `ServiceConfiguration2` schema

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
  string        mqtt_host          = 8;   // broker hostname/IP
  ConnectionType connection_type   = 9;
  IOTEndpoint   endpoint_id        = 10;
  uint32        override_port      = 11;  // 8883
  bool          disable_verify     = 12;  // true = accept a self-signed TLS cert
  string        software_version   = 100;
  string        module_name        = 101;

  enum ConnectionType { GOOGLE_IOT = 0; EMBODIED_IOT = 1; EMBODIED_LOCAL = 2; }
}
```

Only four fields are needed: `gcp_project`, `mqtt_host`, `override_port`, `disable_verify`
(plus `webservice_root` for the OTA path, §2.3). `connection_type` and `endpoint_id` stay 0.

### 1.4 Worked example

For `gcp_project="o"`, `mqtt_host="192.168.1.50"`, `override_port=8883`, `disable_verify=true`:

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

Field tags are `(field<<3)|wiretype`: `0x0A` = f1/len, `0x42` = f8/len, `0x58` = f11/varint,
`0x60` = f12/varint. No protobuf runtime is needed; see Appendix B.

### 1.5 Keep the QR sparse: `gcp_project = "o"`

Moxie's camera "can struggle with dense QR codes" *(doc, `doc/RemoteModuleAPI.md`)*.
`gcp_project` is only the MQTT client-id prefix and the JWT `aud`, and the broker does not verify
the JWT, so its value is cosmetic. Default it to `"o"` and prefer a LAN IP over a long hostname for
`mqtt_host`.

## 2. Firmware gate: 801 vs 803

### 2.1 What changed

- **Before 24.10.801:** no endpoint QR at all. These robots cannot be relocated by QR #2 (see
  [revival path](revival-path.md)).
- **24.10.801:** the first firmware with the relocation QR *(doc, `doc/MoxieOverview.md`)*, but it
  still effectively wants a publicly verifiable TLS certificate on the broker.
- **24.10.803:** honors `disable_verify=true`, so a self-signed broker certificate works. This is
  the target.

OpenMoxie's maintainer served 801 robots through a Let's-Encrypt-fronted broker and used it to push
an OTA to 803 *(doc)*.

### 2.2 The `IOTEndpoint` enum

From `embodied.logging.enums`:

```proto
enum IOTEndpoint {
  IOT_DEFAULT = 0;  GOOGLE_DEVELOP = 1;  GOOGLE_STAGING = 2;  GOOGLE_PRODUCTION = 3;
  EMBODIED_DEVELOP = 4;  EMBODIED_STAGING = 5;  EMBODIED_PRODUCTION = 6;
  EMBODIED_HIPAA = 7;  EMBODIED_LOCAL = 8;  EMBODIED_CHINA = 9;  EMBODIED_HK = 10;
  OPEN_MOXIE = 11;   // the community endpoint slot
}
```

The relocation QR leaves `endpoint_id` at 0. The enum matters for QR #1, whose
`StartPairingQR.endpoint` is a single `iot_endpoint` byte (`OPEN_MOXIE = 0x0B`).

### 2.3 Reading the firmware version, and the OTA lever

- **Version:** the robot reports `software_version` in `QRDiagnosticData` (field 100, alongside
  `robot_uuid`, `rsa_pub`, `cloud_connected`, `cloud_project`), in its `/devices/{id}/state` JSON, and
  in device logs.
- **OTA 801 → 803** *(doc)*: put `"ota_update": {"id":"rls","version":"…-v24.10.803-rls-robot"}` in
  the robot config. When the version differs, the robot requests an HTTP token, then GETs
  `{webservice_root}/api/ota_updates/{id}/url?access_token=…&robot_id=…` and expects
  `{"url": "<signed OTA image URL>"}`. This needs `webservice_root` in QR #2, a token reply (OpenMoxie
  answers `"notoken"`), and a genuine signed image. We specify but do not build this
  ([OTA push brief](backlog/ota-push.md)).

### 2.4 What the appliance presents

- **803 (default):** mosquitto on 8883 with a per-appliance self-signed CA; QR #2 carries
  `disable_verify=true`. No public DNS or internet needed.
- **801:** needs a publicly trusted certificate (a domain and a reachable port 8883) or a one-time
  OTA to 803. A caveat, not the happy path.

## 3. MQTT broker

### 3.1 Broker configuration

Ours is [`mqtt/broker/compose-mosquitto.conf`](../../mqtt/broker/compose-mosquitto.conf) plus two ACL
files ([`acl`](../../mqtt/broker/acl), [`acl-robot`](../../mqtt/broker/acl-robot)), designed in
[broker authentication](backlog/security-broker-auth.md). OpenMoxie's broker is one anonymous TLS
listener (`site/data/openmoxie.conf`).

**The limit first: this is containment, not authentication.** Nothing checks that a client calling
itself `d_1234…` is that robot. A spoofed id still connects; the permit list (§3.7) decides whether
it is served anything real.

```conf
per_listener_settings true

listener 8883                                     # the robot, TLS
cafile   /mosquitto/config/keys/ca.crt
certfile /mosquitto/config/keys/mosquitto.crt
keyfile  /mosquitto/config/keys/mosquitto.key
tls_version tlsv1.2
allow_anonymous true                              # the robot presents a JWT; see §3b
acl_file /mosquitto/config/acl-robot

listener 1883                                     # supervisor · SIM · tests
allow_anonymous true
password_file /mosquitto/config/keys/passwd
acl_file /mosquitto/config/acl

listener 9001                                     # the browser UI, MQTT over WebSockets
protocol websockets
allow_anonymous true
password_file /mosquitto/config/keys/passwd
acl_file /mosquitto/config/acl

log_dest stdout
log_dest topic          # publishes broker log lines to $SYS/broker/log/#
log_type all
```

**Per-device confinement.** mosquitto substitutes the client id for `%c` in a `pattern` line, and
every client has a client id, so both ACL files share this floor and grant nothing else globally:

```conf
pattern write /devices/%c/events/#
pattern write /devices/%c/state
pattern read  /devices/%c/config
pattern read  /devices/%c/commands/#
```

This closes fleet enumeration (`$SYS/broker/log`, which announces every `d_<uuid>`, is
supervisor-only) and cross-device reads and writes (no subscribing to another child's `/config`). It
does not provide identity.

**Why security is per listener.** A robot's MQTT password is an RS256 JWT, so a `password_file` on
8883 would refuse it. On a listener with no password file, mosquitto accepts any username and then
matches it against `user` blocks, so a `user supervisor` block there would hand the fleet to anyone
who typed the word. The supervisor's identity therefore lives only in `acl`, which is loaded only
where `password_file` is. `sim/run_acl_proof.sh` proves this against a real broker.

| Listener | Who | `password_file` | `acl_file` | May read `$SYS` |
|---|---|---|---|---|
| `8883` TLS | a real robot | no (it presents a JWT) | `acl-robot` (the `%c` floor only) | no |
| `1883` plain | supervisor, SIM, tests | yes | `acl` | supervisor only |
| `9001` WebSockets | the browser UI | yes | `acl` | supervisor only |

**The browser SIM** renders whichever robot is talking, so it needs `/devices/+/…` reads. A browser
page cannot hold a secret, so `acl` grants that read anonymously and read-only, plus writes as the
fixed SIM id `d_sim`. It cannot publish into a real robot's `commands/` or read `$SYS`.

**The supervisor credential** is minted per appliance by the `certs` one-shot
([`gen-passwd.sh`](../../mqtt/broker/gen-passwd.sh)): 32 random bytes, hashed into `passwd`, with the
plaintext in `supervisor.pass` (mode 0600) on the shared volume. The supervisor reads it via
`MOXIE_MQTT_PASSWORD_FILE`, never from a compose `environment:` literal.

**Ports.** `1883` binds `127.0.0.1` by default (`MOXIE_BIND_HOST_PLAIN`); `8883` and `9001` use
`MOXIE_BIND_HOST`, since robots and phones are on the LAN. Keys live in `mqtt/broker/keys/`, generated
per appliance ([`gen-certs.sh`](../../mqtt/broker/gen-certs.sh)) and never committed. The broker is
upstream `eclipse-mosquitto:2.0.20` plus config.

### 3.2 Topic structure

`{d_uuid}` is the robot's device id, always prefixed `d_`.

The supervisor **subscribes** to:

```
/devices/+/events/#          all robot-published events
/devices/+/state             robot state snapshots
$SYS/broker/clients/#        client-count metrics
$SYS/broker/log/#            connect/disconnect detection
```

and **publishes** to one robot:

```
/devices/{d_uuid}/config              config JSON (on connect and on change)
/devices/{d_uuid}/commands/{name}     JSON commands (§3.5)
/devices/{d_uuid}/commands/zmq        binary ZMQ-over-MQTT (STT subscribe, transcripts)
```

### 3.3 Event names (robot → cloud, `/devices/{id}/events/{name}`)

| Event | Purpose |
|---|---|
| `remote-chat` (and `remote-chat-staging`) | `RemoteChatRequest`, the conversation channel. `backend:"data"` + `query:"modules"` asks for the remote module list; `backend:"router"` is a conversational turn (§4). |
| `client-service-activity-log` | Multiplexed by `subtopic`: `query:"schedule"`, `query:"mentor_behaviors"`, `query:"license"` (e.g. the `google_speech` key), `mentor_behavior` reports, and `subtopic:"telehealth"` puppet state. |
| `zmq` | ZMQ bridge: payload `"{proto.full_name}:" + protobuf_bytes`, e.g. `embodied.perception.audio.zmqSTTRequest` (mic audio). |
| `device-logs` | Per-robot log records (`tag`, `message`). |
| `client-service-http-token` | The robot asks for an HTTP access token (OTA path; OpenMoxie answers `"notoken"`). |

### 3.4 Connect and disconnect detection

The supervisor regex-scans broker log lines on `$SYS/broker/log/#`
([`constants.py`](../../mqtt/supervisor/moxie_runtime/constants.py)):

```python
CONNECT_RE    = r"connected from (.*) as (d_[a-f0-9-]+)"
DISCONNECT_RE = (r"Client (d_[a-f0-9-]+) (?:closed its connection|disconnected|been disconnected|"
                 r"has exceeded timeout|already connected, closing old connection)"
                 r"|(?:Bad socket read/write on|Socket error on) client (d_[a-f0-9-]+)")
```

The disconnect spellings are the ones in the `mosquitto` binary of the pinned `eclipse-mosquitto:2.0.20`
image, checked against a live capture of its `$SYS/broker/log` on 2026-10-08: a clean `DISCONNECT`
packet logs `disconnected.`, a socket that just went away (a TCP reset included) `closed its connection.`,
a keepalive expiry `has exceeded timeout, disconnecting.`, and a robot whose new socket displaces its old
session `already connected, closing old connection.` (that one at level `E`, which is why the
subscription is `log/#`); plus mosquitto 1.6's `Socket error on client …, disconnecting.` for a
distro-packaged broker. OpenMoxie matches only the first two, and only on level-`N` lines
(`moxie_server.py:80-81`, `:149-158`), and initialises a robot once per entry in its online map
(`robot_data.py:94-99`). Read from that code, a plausible C4 mechanism is a robot that left with a line
it does not match (or one logged at level `E`) and so was never released: its return got no config and
no subscribe. Upstream's own diagnosis (PR #59) blamed mosquitto 2.x no longer publishing the `$SYS`
connect notices it read; the two readings are not exclusive, and neither is verified here.

On connect: register the robot, wait about 1 s, push config, then send a ZMQ `ProtoSubscribe`
asking the robot to stream STT audio (`embodied.perception.audio.zmqSTTRequest`), the order OpenMoxie
uses (`on_device_connect`, `moxie_server.py:254-266`). The connect line is also evidence of a **new
session**: a second one for a robot already onboarded, with no disconnect line in between (Wi-Fi dropped
and came back inside the keepalive, or a line no pattern knows), forgets what the supervisor believed
about that robot and onboards it again, config and subscribe included. A `/state` or an event is not
such evidence; it repeats. The subscribe is also re-sent on `wakeup`, on Permit, when the Listening
picker installs an engine (to every permitted robot the supervisor knows of that is not yet asked,
ghosts included: a robot that sat connected through our socket blip never announces itself again),
after a broker outage in whichever order the supervisor and the robot come back (the latch is dropped
with the socket, and an ask that goes out while the robot is still away, from the roster resume, the
picker, a wake or a Permit, is not recorded as its session, so its own connect line is still answered
with config and the ask), and by the roster resume after a supervisor restart; `/status` shows
`stt_subscribed_at` per robot, set only for a robot confirmed on this connection. Nothing withdraws the
subscription: a revoke or Listening `off` leaves the robot streaming to the LAN broker, where the permit
gate or the missing engine drops the audio (the recovered `Log.proto` has no unsubscribe message).
Built to this contract and OpenMoxie's field-proven behaviour; **unverified on our hardware**.
[`sim/tests/test_stt_wire.py`](../../sim/tests/test_stt_wire.py) covers every trigger, both
broker-restart orders and every leave line.

**The log is live-only.** mosquitto does not replay log lines on re-subscribe, so a supervisor that
restarts while a robot stays connected never sees its connect line, and the robot has no reason to
re-publish. The fallback is therefore essential: the first `/devices/{id}/state` **or**
`/devices/{id}/events/{name}` from an unknown device registers it (config push, `app.on_connect`,
presence, `/status`). Registration is not admission; the permit gate (§3.7) still applies.
`sim/tests/test_connection_resilience.py` covers this. What a physical robot does across a broker
restart is unverified.

### 3.5 Command names (cloud → robot, `/devices/{id}/commands/{name}`)

| Command | Payload / purpose |
|---|---|
| `config` | Actually the `/config` topic, not `/commands/config`: the full robot config JSON (§3.6). |
| `remote_chat` | `RemoteChatResponse`: `output.text` + `output.markup` + `response_actions`. |
| `tts` | `CloudTTSResponse`: rendered audio and marks for a line ([AI seam §③](ai-seam.md)). |
| `query_result` | Answers to `schedule`, `mentor_behaviors` and `license` queries. |
| `http_token` | `{"command":"http_token","http_token":"notoken"}` (OTA path, optional). |
| `telehealth` | Puppet mode: a `TelehealthRobotCommand` (§3.9). |
| `wakeup` | `{"command":"wakeup"}`: wake a `wake_button_enabled` robot from screen-off. |
| `zmq` | Binary, e.g. `ProtoSubscribe` (enable STT) and `zmqSTTResponse` (transcripts). |

There is no remote reboot command in any source we have, so we do not offer one.

### 3.6 The robot config pushed on connect

OpenMoxie builds it as common settings deep-merged with per-device overrides
(`site/hive/mqtt/robot_data.py`); ours is
[`cloud_config.py`](../../mqtt/moxie_sdk/cloud_config.py)`::merge_config_layers`. The full field
list is in the [config contract](config-and-telemetry-contract.md). Defaults:

```jsonc
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
      "mqtt_files":"0","file_sync_wait":"0","default_loglevel":"warning",
      "stt":"4"                     // stream mic audio to the cloud over ZMQ
    }
  }
}
```

- `settings.props.stt = "4"` streams audio to us; `"0"` uses an on-device Google service account
  *(doc)*. `local_stt:"on"` is only the on-device wake-word ASR.
- `wake_button_enabled` / `touch_wake_enabled` keep the robot network-connected.
- `child_pii` is the decrypted child record (nickname, birthday ISO 8601, `volume_preference`,
  `face_options`, `input_speed`), fed from the parent app's child profile.

### 3.7 The pairing gate

The broker accepts anonymous connections, so reaching the port must not mean "is my child's robot".
Otherwise any device that announces itself would be pushed `pairing_status:"paired"` and the
child's `child_pii`. The appliance keeps a **permit list, closed by default**:

| | Permitted (or `allow_unverified_bots`) | Not permitted (*pending*) |
|---|---|---|
| `/config` push | full config: `pairing_status:"paired"`, `child_pii`, the parent's settings | `build_unpaired_cloud_config()`: `pairing_status:"unpairing"`, `data_sharing:"NO_DATA"`, the bare `settings` envelope; no `child_pii`, no `stt` prop |
| `events/remote-chat` | the brain answers; history is kept | one fixed line ("I'm not connected to a family yet…"); no brain call, nothing stored |
| activity-log queries | the real `schedule` / `mentor_behaviors` | a `CloudQueryResponse` with an empty value, so the robot's pull resolves |
| reports, telemetry, `zmq` audio | ingested | dropped |
| `/devices/{id}/state` | ingested | ingested (so a pending robot shows up in the console) |

The gate sits at the transport boundary (the runtime's `_on_message`), so there is one place a
device is refused.

**`pairing_status`.** `"paired"` is the operating value. The not-paired value we push is
`"unpairing"`, taken from OpenMoxie (`models.py::MoxieDevice.is_paired`); it is field-proven there,
not captured from the original cloud, and no physical robot has been observed receiving it. See the
[config contract](config-and-telemetry-contract.md).

**Where it lives.** `fleet/permits.json` beside `fleet/config.json`
(`{"allow_unverified_bots": bool, "devices": {device_id: {permitted_at, label}}}`); `GET`/`POST
/permits` on the supervisor's status server; the console's Robot access card (`GET /local/fleet` →
`pending`, `POST /local/robots/{id}/permit`). Pairing through the console
(`POST /local/simulate-robot-scan`) permits the device automatically. Owner guide:
[permitting a robot](../guides/permitting-a-robot.md).

### 3.8 The `schedule` query

The robot pulls its day at the start of every session (`client-service-activity-log`,
`subtopic:"query"`, `query:"schedule"`), and the cloud answers on `commands/query_result` with a
`CloudQueryResponse` whose field 6 is a `ContentSchedule`. The planner that fills it scores the
robot's `mentor_behaviors`, the parent's `schedule_preferences`, bedtime windows and the clock; the
rules are in [content-module contract: how a `schedules[]` entry becomes the day the robot
runs](content-module-contract.md).

Each served day's reasons are kept for the parent (never sent on the wire) in
`robots/<device_id>/schedule_explain.json`, readable from the supervisor's localhost status server:

| Endpoint | Answer |
|---|---|
| `GET /schedule?device_id=…` | `{ok, device_id, day, planned_at, served, schedule, explanations[], inputs}`: the exact `ContentSchedule` sent, one `{module_id, slot, at, reason_codes[], line, score, factors}` per entry in plan order, and what the planner knew (`bucket`, `bedtime`, `slots`, `parent_requests`, `ftue_skips`, `telemetry`, `history`, `planned`) |
| `GET /schedule?device_id=…&refresh=1` | re-plans now instead of reading the stored answer (`served:false`) |

Unknown devices get 404. Example `line`s:

```
 07:38  DRAW           Sam finished Drawing once — scheduling it in the morning slot.
 07:58  SCAVENGERHUNT  Requested by a parent for 8:01 am — Scavenger hunt is pinned to that slot.
```

### 3.9 `commands/telehealth`: an operator speaks through Moxie

In puppet ("Be Moxie") mode a remote adult types a line and Moxie says it in a chosen mood, with the
robot's own dialog engine off. The protocol is recovered in
[Telehealth (RE)](../reverse-engineering/protocol/telehealth.md); the build brief and the open
hardware questions are in [telehealth](backlog/telehealth.md).

**Cloud → robot** on `/devices/{id}/commands/telehealth`, a `TelehealthRobotCommand` as JSON:

```jsonc
{"command": "telehealth",
 "message": {"timestamp": 1788360800925,          // ms
             "action": "PLAY_OUTPUT",             // START_SESSION | PLAY_OUTPUT |
                                                  //   END_SESSION | UPDATE_STATE | INTERRUPT
             "output": {"text": "Hello Sam.",     // PLAY_OUTPUT only
                        "markup": "<mark .../>"}, //   the same markup grammar as §4
             "session_id": "ths-4e91e52b3f"}}
```

`output` appears only on `PLAY_OUTPUT`. `Output.line_id` / `line_params` exist in the proto but refer
to pre-authored lines we have no catalog for, so we never emit them. Builders and parser:
[`telehealth.py`](../../mqtt/moxie_sdk/telehealth.py), whose keys CI checks against the recovered
`.proto`.

**Robot → cloud:** the `client-service-activity-log` event with `subtopic:"telehealth"` carries a
`TelehealthRobotEvent` with `RobotState` `READY` / `IN_SESSION` / `EXITING`. It is stored verbatim; an
unknown name is kept and flagged; before any report the console says "never reported".

**Turning it on** is a config write: `moxie_mode` (`RobotCloudConfig` field 21) is set to
`"TELEHEALTH"` in that robot's override layer and the config is re-pushed. That this enters
`STATE_TELEBRAIN` is an assumption taken from OpenMoxie, behind one constant
(`TELEHEALTH_MOXIE_MODE`). `sanitize_config_overrides` does not accept `moxie_mode`, so fleet-wide
settings cannot put every robot into puppet mode.

**Rules:**

- A session is `START_SESSION → (PLAY_OUTPUT | INTERRUPT)* → END_SESSION`. While it is open, the
  brain is not called for that robot; a `remote-chat` event mid-session is dropped.
- Every operator line goes through the same `InputSafety` classifier as Moxie's own speech (as
  `role=MOXIE`) and into the parent's safety journal with `who: "operator"`. A blocked line is
  returned to the operator with its reason (HTTP 400) and nothing is spoken.
- The operator sees a per-robot, in-memory, 200-entry text transcript (`{who, text, at}`): their own
  lines and the child's transcribed speech. **No audio or video of the child reaches the operator.**
  Under `LoggingPolicy.NO_DATA` only operator lines are kept.
- Inside the robot's bedtime window the console warns but still sends (`cloud_config.in_bedtime`),
  since we cannot know whether the robot suppresses the line.

**Where it lives:** the runtime's telehealth verbs and `GET`/`POST /telehealth` on the supervisor's
status server; the console's Be Moxie card (`GET`/`POST /local/robots/{id}/telehealth`); and in
the simulator, `sim/web/bridge/` + `sim/virtual_moxie.py --telehealth` (`sim/run_smoke.sh
--telehealth`).

> **Risk:** whoever can reach the console can speak to the child in Moxie's voice. Mitigations: a
> pending robot cannot be puppeted, every line passes the safety classifier, and every line is
> journalled for the parent to read back.

## 3b. Robot identity and auth

- **Robot:** each robot has `/sdcard/EmbodiedStaticData/PERSISTENT_DATA/uuid.txt` and
  `.../rightpoint/RS256.key` (an RSA private key). It connects with any username and a password that
  is an **RS256 JWT** signed by that key, claims `{aud: gcp_project, iat, exp:+1h}`. Client id =
  `d_{uuid}`.
- **Broker:** `allow_anonymous true` on 8883, so the JWT is never verified.
- **OpenMoxie's supervisor** (`site/hive/mqtt/robot_credentials.py`, `fake_monitor=True`) connects as
  `username="unknown", password="supervisor"`.
- **Ours:** the supervisor authenticates with the per-appliance credential (§3.1); every anonymous
  client is confined to its own `/devices/<client id>/…`.

**Not enforced: identity.** A spoofed `d_<uuid>` connects and is served as that robot, and because
MQTT evicts an existing session on a client-id collision, it can knock the real robot off. Fixing
that means verifying the JWT against the robot's public key (`rsa_pub` in `QRDiagnosticData`, or via
`adb`), which is blocked on a physical robot ([broker authentication](backlog/security-broker-auth.md)).
Meanwhile the permit list (§3.7) stops an unpermitted device from being served anything.

## 4. Conversation flow

### 4.1 The turn objects

- **`RemoteChatRequest`** (robot → cloud, `events/remote-chat`). Key fields: `event_id`, `command`
  (`prompt` | `continue`/`reprompt` | `notify`), `backend` (`router` for conversation, `data` for the
  module list), `module_id`, `content_id`, `speech` (recognized text), `extra_lines[]` (each
  `{context_type, text}`; `context_type=="input"` is a user utterance), `recommend.exits[]` (what to
  launch next), `input_vars` (e.g. `$eb_qr_value` from a scanned card).
- **`RemoteChatResponse`** (cloud → robot, `commands/remote_chat`): `command:"remote_chat"`,
  `result`, `backend`, `event_id`, **`output:{text, markup}`**,
  `response_actions:[{output_type, action, module_id, content_id, …}]` (plus the legacy singular
  `response_action`, which mirrors `response_actions[0]`; a client must act on one or the other,
  never both, or it launches twice), `fallback`. Full field list: [AI seam §②](ai-seam.md).
- **Action tags.** The brain may write `<launch:MOD:CID>`, `<exit>` or `<sleep>` inline; they become
  structured `response_actions` and are stripped from the spoken text
  ([`actions.py`](../../mqtt/moxie_sdk/actions.py); OpenMoxie's `volley.py::ingest_action_tags`).

### 4.2 "Notify" context tracking

Moxie is authoritative about what it actually said. After each utterance it sends a
`command:"notify"` request. History is rebuilt from it: `extra_lines[].text` with
`context_type=="input"` become user turns; `speech` (minus `animation:` / `silent:` lines) becomes an
assistant turn. This keeps the brain's context right even when the child speaks across several VAD
windows.

### 4.3 STT audio over ZMQ-over-MQTT

- The robot streams `embodied.perception.audio.zmqSTTRequest` on `events/zmq`, payload
  `b"embodied.perception.audio.zmqSTTRequest:" + bytes`.
- Fields: `timestamp` (u64), `vad` (`UNKNOWN / START_OF_SPEECH / SPEECH / END_OF_SPEECH`),
  **`audio_content`** (raw 16 kHz PCM16 mono), `uuid` (session id).
- Sessions are keyed by `(device_id, uuid)`; audio is concatenated until `END_OF_SPEECH`, then
  transcribed.
- The reply is a `zmqSTTResponse` (`type=FINAL`, `speech`, `confidence`, `start/end_timestamp`,
  `alternatives[]`, `error_code/message`) on `commands/zmq`, in the same framing:
  `b"embodied.perception.audio.zmqSTTResponse:" + bytes`. The robot injects that payload straight onto
  its bus, so JSON there is a frame it cannot route. Ours carries `timestamp`, `type=FINAL`, `speech`,
  `confidence` and `uuid` ([`stt.py`](../../mqtt/moxie_sdk/stt.py)`::encode_zmq_stt_response`); an
  empty transcript is still a `FINAL`. Field reference: OpenMoxie `zmq_stt_handler.py:52-76` (a protobuf
  `zmqSTTResponse` with `uuid`, `type=FINAL`, `timestamp`, `speech`, sent through `send_zmq_to_bot`).
  Unverified on our hardware. Engine choices: [AI seam §①](ai-seam.md).

### 4.4 A full turn

```
child speaks → robot VAD → zmqSTTRequest frames (events/zmq)
   → STT → zmqSTTResponse (commands/zmq) back to the robot
robot sends RemoteChatRequest{speech:"…", backend:"router"} (events/remote-chat)
   → input safety check → brain (or a matched global "brain entrance")
   → markup added, action tags lifted into response_actions
   → RemoteChatResponse (commands/remote_chat) {output:{text,markup}, response_actions}
   → optionally CloudTTSResponse (commands/tts) with rendered audio
robot speaks and animates
robot sends notify requests for what it said → history updated
```

### 4.5 Slow brain: a filler now, the real answer next (`REPLY_PENDING`)

If the cloud stays silent the robot re-prompts after roughly 20 s, and a hosted LLM turn can take
longer. So one `event_id` may be answered by **more than one response**. The contract carries this:
`RemoteChatResponse.result = REPLY_PENDING` (ResultCode **9**) means more chunks follow, `chunk_num`
(field 22) orders them, and `consistency_control`
(`RemoteConsistencyControl{prefix, is_completed}`, field 18) marks the last one
([remote-chat protocol](../reverse-engineering/protocol/remote-chat-protocol.md#the-response-remotechatresponse);
[`RemoteChat.proto`](../reverse-engineering/protocol/recovered-proto/embodied/robotbrain/RemoteChat.proto):201-205, :317, :336-340).

Our runtime ([`turns.py`](../../mqtt/supervisor/moxie_runtime/turns.py), budget
`MOXIE_BRAIN_BUDGET_S`, default 6 s):

```
t=0.0   events/remote-chat {event_id: E, speech: "why does the moon change shape?"}
t=6.0   commands/remote_chat {result: REPLY_PENDING, chunk_num: 0,     ← a filler, spoken now
                              consistency_control:{is_completed:false},
                              output:{text:"Hmm, let me think about that one.", markup:…}}
        commands/tts         {event_id: E, chunk_num: 0}
t=17.9  commands/remote_chat {result: SUCCESS, chunk_num: 1,            ← the real line
                              consistency_control:{is_completed:true}, output:{…}}
        commands/tts         {event_id: E, chunk_num: 1}
```

- **Under budget nothing changes:** one `SUCCESS`, no `chunk_num` on the wire (chunk 0 is the proto
  default), so a client that ignores chunking is unaffected.
- **Chunks are ordered:** a client queues one `event_id`'s chunks by `chunk_num`
  ([SIM as a client](sim-as-a-client.md)); the server publishes chunk 0 before chunk 1.
- **Stale answers are dropped:** if a newer turn starts for the same robot, the old result is never
  published.
- Fillers rotate (never the same line twice running) and carry thinking markup
  ([`filler.py`](../../mqtt/moxie_sdk/filler.py)). They are our own lines and are not safety-checked.

> **Unverified on hardware.** The fields are recovered, but no capture shows a real Moxie speaking
> chunk 0 and keeping the turn open for chunk 1. The SIM does. If a robot disagrees, the fallback is
> to answer the current request with the filler and deliver the answer on the robot's next
> (re)prompt. `MOXIE_STREAMING=0` returns to the one-reply wire.

#### Streaming: one chunk per sentence (`MOXIE_STREAMING`, default on)

When the app can answer incrementally (`MoxieApp.respond_stream(turn) -> Iterator[ReplyChunk]`), the
runtime publishes each finished sentence as its own chunk as soon as the model writes it, so the
first words arrive at first-token latency:

```
t=0.00  events/remote-chat {event_id: E, speech: "why does the moon change shape?"}
t=1.52  commands/remote_chat {result: REPLY_PENDING, chunk_num: 0,
                              consistency_control:{is_completed:false},
                              output:{text:"The moon looks different because of how the
                                            sun lights it up.", markup:…}}
t=2.22  commands/remote_chat {result: REPLY_PENDING, chunk_num: 1, …}
t=2.86  commands/remote_chat {result: REPLY_PENDING, chunk_num: 2, …}
t=4.38  commands/remote_chat {result: SUCCESS,       chunk_num: 3,      ← closes the turn
                              consistency_control:{is_completed:true}, output:{…}}
```

- **Sentence boundaries** ([`segment.py`](../../mqtt/moxie_sdk/segment.py)): `. ! ?` (plus any
  closing quote) followed by whitespace **and more real text**. No split on decimals, known
  abbreviations or initials ("Dr.", "8 p.m.", "J. R. R."), ellipses, or sentences under about 24
  characters. Requiring more text guarantees the last sentence is still buffered when the stream
  ends, so a chunk always remains to carry `is_completed`.
- **Action tags** are parsed on each chunk and land on the chunk where they appear; an action in a
  wordless chunk carries to the next chunk.
- **Markup** is generated per chunk locally (§4.6); the closing chunk also receives the model's own
  mood/gesture hints. The mood mark is emitted on the first chunk only, so a multi-sentence reply
  holds one face.
- **Fillers re-arm** after each chunk, up to `MAX_FILLERS_PER_TURN = 2` per turn; fillers take the
  next `chunk_num`.
- **A newer turn cancels the stream** and closes the generator; nothing more is published for the
  abandoned `event_id`.
- **Fallbacks:** a one-sentence answer, a non-streaming app, or `MOXIE_STREAMING=0` publishes a single
  `SUCCESS` with no `chunk_num` or `consistency_control`. If the stream fails before any word, the
  runtime falls back to `respond`; if it dies mid-answer the sequence is closed rather than re-asked.

#### Safety on the wire: `InputSafety` (`input.safety`)

A streamed sentence is published before the rest exists, so the runtime checks both ends of a turn:
the child's speech before the brain is called, and every chunk before it is published (policy:
[AI seam, input safety](ai-seam.md); for parents: [child safety](../guides/child-safety.md)).

The verdict travels in `RemoteChatResponse.input` (**field 17**, `RemoteChatInput`), whose
**field 12** is `InputSafety`: `is_unsafe` (1, bool), `blocked_by` (2, repeated string), `intents` (3,
repeated string), `phrase_id` (4, int32)
([`RemoteChat.proto`](../reverse-engineering/protocol/recovered-proto/embodied/robotbrain/RemoteChat.proto):180-186, :198, :335;
[remote-chat protocol](../reverse-engineering/protocol/remote-chat-protocol.md#remotechatinput-the-brains-read-of-the-child)).
`RemoteChatResponse.input_intents` (**field 10**) carries the same intents flat.

```
t=0.00  events/remote-chat {event_id: E, speech: "how do I make a bomb to hurt my brother?"}
        ↳ assessed BEFORE the brain: blocked. No model sees it.
t=0.02  commands/remote_chat {result: SUCCESS,
                              output:{text:"That one's not for me. If it's important, a
                                            grown-up you trust is the best person to ask.",
                                      markup:…},
                              input:{safety:{is_unsafe:true, blocked_by:["violence"],
                                             intents:["violence_instructions","threat"],
                                             phrase_id:404}},
                              input_intents:["violence_instructions","threat"]}
```

- **An ordinary turn is unchanged:** no verdict, no `input`, no `input_intents`.
- **`is_unsafe` means blocked.** A merely flagged utterance goes to the brain and to the parent's
  review queue; `blocked_by` is empty exactly when `is_unsafe` is false.
- **Only the child's side is reported.** A block on Moxie's own output has no field in the contract:
  the blocked chunk is not published, a short safe line closes the sequence (`SUCCESS` +
  `is_completed`), the stream is cancelled, and the event goes to the parent queue. Earlier chunks
  stay spoken.

### 4.6 Behavior markup

How a server makes Moxie move and emote is through markup. The one generator is
[`automarkup.py`](../../mqtt/moxie_sdk/automarkup.py): a pure, deterministic, stdlib-only
`annotate(text, …) -> markup`, used by the runtime, `LLMApp.build_markup`, and the content app's
authored-markup path. Per line it emits:

| Slot | Rule | Vocabulary |
|---|---|---|
| **Mood** | one per line: apology → Sad, "Oh!" → Surprised, "Oops" → Shy, thinking/a question → Curious, puzzlement → Confused, praise or `!` → Happy, else Neutral. Intensity `min(2, max(1, exclamations + emphatic words))` | `ePlaybackMood` 0–10 ([behavior markup](../reverse-engineering/runtime/behavior-markup.md):107-133) |
| **Voice** | a `?` sentence in `<usel genre="question">`, a `!` sentence in `genre="excited"`; `variant` pinned to `0` | the 5 CereVoice genres (:37) |
| **Gesture** | one per clause on the first word that carries the thought, then `Gesture_Talk` every 5 words; never in the last two words of a sentence | the 12 hardcoded `Gesture_*` (:191-198) |
| **Tree** | at most one whole-body animation per line, for thinking, greeting and sign-off lines only; no arm gesture stacked on it | `Bht_Active_Thinking` / `Bht_Gesture_Greet` / `Bht_Sign_off` ([behavior tree engine](../reverse-engineering/runtime/behavior-tree-engine.md):103-115) |
| **Pause** | `<break time="0.35s"/>` at internal sentence boundaries and after a leading interjection comma; never after the final word | (:38) |
| **Rest** | every chunk ends on `Gesture_None` | |

Invariants:

- **The words never change:** `strip_markup(annotate(t)) == strip_markup(t)`.
- **Only recovered ids:** every mood, `eventName`, `behaviour`, icon value and `SoundToPlay` is checked
  against the catalog in [`vocab.py`](../../mqtt/moxie_sdk/vocab.py). An id suggested by the brain
  that is not in the catalog is dropped and counted.
- **Deterministic:** randomness comes from a `blake2b` digest of `(turn_key, chunk_index, sentence,
  word)`, never Python's salted `hash()`, so golden tests can pin output.
- **Cheap:** no model call or I/O; well under 1 ms per line.

Rollback: `MOXIE_AUTOMARKUP=0` restores passthrough.

**The behavior planner** ([`performance.py`](../../mqtt/moxie_sdk/performance.py)) sits on top.
`plan()` classifies what the line is doing (one of the 22 `RemoteDialog.DialogAct`s) and returns a
frozen `Performance` of `Beat`s plus the line's dialog act, emotion, signal and mood; `validate()`
checks every id against `vocab.py`; one `render()` produces the markup. Examples: a
`factual_question` tilts and holds gaze; an `apology` goes Sad and stops gesturing; `appreciation`
celebrates at intensity 2; `backchannelling` moves nothing but the rest pose. The scored fields
(`mood_intensity`, `emotion`, `signals`) go on every published turn, including chunks, fillers and
greetings; an app's own `Reply.mood` / `dialog_act` wins field by field. Goldens:
[`sim/tests/goldens/performance.json`](../../sim/tests/goldens/performance.json).

- `MOXIE_EXPRESSIVE=planner|floor|off` (see [`markup.py`](../../mqtt/supervisor/markup.py)). A plan
  that fails or exceeds an 8 ms budget three times in a row falls back to the floor with the same
  wire shape.
- **Rehearsal:** `POST /local/robots/{id}/preview` (→ the supervisor's `POST /preview`) publishes a
  staged line as an ordinary `remote_chat`, with no brain and no history.

**Prior art.** The behaviors are ported from OpenMoxie's `site/hive/automarkup/` (MIT) and credited
in the module docstring; no code or data table was copied. We did not vendor it because it adds
dependencies and a large data table, is non-deterministic, and uses several gesture ids
(`AUTO_GESTURE_ME`, `Gesture_We`, `Gesture_Small`) that are not in our recovered catalog.

**Limits:**

- No hardware has played our markup. The browser SIM is the only renderer we can test against
  ([`sim/test_automarkup_render.mjs`](../../sim/test_automarkup_render.mjs)).
- The catalog catches our typos but cannot prove a given robot's asset bundle has an id, or what a
  robot does with an unknown one.
- Icons, sound effects and speech "spurts" are off: the confirmed icon values are calendar cues, only
  one usable sound id is confirmed, and spurt behavior needs a hardware capture.
- Gaze is set on-device; the cloud can only choose a look-bearing tree. There is no id for lowering
  the gaze or nodding, so the planner does not fake one.
- The dialog-act classifier is rule-based and cannot read context or sarcasm.

### 4.7 Vision events, and whether the cloud may speak first

A subscribed perception event (`eb-found-face`, `eb-lost-target`, `eb-qr-event`, `eb-dr-event`,
`eb-br-event`) arrives as the **`speech` of an ordinary `RemoteChatRequest`** *(doc,
`doc/RemoteModuleAPI.md` §Event Handling; same shape as
[content and conversation](../reverse-engineering/runtime/content-and-conversation.md))*. Events are
discarded unless the active module subscribed: the brain opts in with
`RemoteChatAction.EventSubscription{clear, active[]}` on any response. The runtime
(`turns.py::_on_remote_chat`) recognizes an event in the `speech` slot and diverts it before any brain
call; one reply per `(device, module_id)` carries the subscription. Design: [vision](vision.md) §7.

**May the cloud publish a `remote_chat` nobody asked for?** Not established, so we do not. The
contract is request/response (`event_id` is echoed), and no source says what a robot does with an
unmatched `event_id`. Therefore:

- **A vision event is itself a request**, and it requires a response. An unprompted hello goes out as
  an ordinary `SUCCESS` on that event's `event_id`; with nothing to say the runtime answers
  `NOREPLY_ACK` (ResultCode 6, acknowledge only), which is terminal.
- **With no request to answer** (e.g. a hello earned mid-turn), the line is queued and delivered as
  chunk 0 / `REPLY_PENDING` of the next turn, exactly the §4.5 shape.

Revisit this first if a capture from a physical robot appears.

## 5. Where the AI plugs in

The three seams (speech-to-text, the brain, text-to-speech) and their engines are specified in the
[AI seam](ai-seam.md). In brief, for this repo:

| Seam | Ours | Configure with |
|---|---|---|
| Brain | any OpenAI-compatible chat endpoint; no default is built in | `MOXIE_APP`, `MOXIE_LLM_BASE_URL` (see [`mqtt/config.py`](../../mqtt/config.py)) |
| STT | local faster-whisper or a gateway `/audio/transcriptions` | `MOXIE_STT` |
| TTS | gateway voice, local Piper, or a tone; or none, letting the robot voice the markup itself | `MOXIE_TTS` |

For reference, OpenMoxie's swap points are `site/hive/mqtt/ai_factory.py` (an `OpenAI()` client
without `base_url`) and `site/hive/mqtt/zmq_stt_handler.py` (wraps PCM into a WAV and calls
`audio.transcriptions` with `whisper-1`). Keep `settings.props.stt:"4"` so audio streams to the
cloud.

## 6. Content modules

What OpenMoxie ships, for reference; our format is the
[content-module contract](content-module-contract.md).

- **Conversation modules** are DB rows (`SinglePromptChat`: `module_id`, `content_id`
  [pipe-separated], `prompt` [a Django template], `opener`, `model`, `max_tokens`, `temperature`,
  `max_history`, `max_volleys`, optional `code` hooks `pre_process` / `post_process` /
  `complete_handler` / `notify_handler`). Seeded from `content_modules/*.json` (MoxieGo, MemoryChat,
  MoxieTimers, MoxieTime) and `site/data/default_conversations.json`.
- **Native on-robot modules** (ChatScript in firmware) are only *scheduled* by id. The ~23 in
  `content/data.py` `RECOMMENDABLE_MODULES`: AFFIRM, AB, ANIMALEXERCISE, BODYSCAN, RDL,
  BREATHINGSHAPES, COMPOSING, FACES, FF, GUIDEDVIS, JOKE, JUKEBOX, MENTORSAYS, NONSENSE, DANCE, DRAW,
  STORYTELLING, PASSWORDGAME, READ, SCAVENGERHUNT, STORY, AUDMED, WHIMSY, plus **DM** (Daily Missions,
  with content ids in `DM_MISSION_CONTENT_IDS`). They run entirely on the robot.
- **Schedules** (`MoxieSchedule`) *(doc, `doc/MoxieOverview.md`)*: a `provided_schedule` list, a
  `generate` block that extends the day, `hub_config`, `chat_request`, `wake_module`,
  `alarm_module`. Defaults: `default`, `only_chat`, `no_onboarding`.
- **Launch QR codes** are plain text `GO<launch:MODULE_ID>` (e.g. `GO<launch:DM>`), scanned during a
  QR-enabled module (`eb_enable_qr` + an `eb-qr-event` subscription). Ours:
  [`launch_cards.py`](../../mqtt/moxie_sdk/launch_cards.py) and
  [`launch_sheet.py`](../../mqtt/moxie_sdk/launch_sheet.py).
- **Missing content** *(doc, README)*: the newer modules Ocean Explorer, Animal Faces and Story Maker
  are not supported. Some face customization assets crash Unity and are excluded.
- Global "brain entrance" launch phrases live in `GlobalResponse` rows / `global_responses.py`.

## 7. Our implementation map

| Concern | Where |
|---|---|
| Broker config and ACLs | [`mqtt/broker/`](../../mqtt/broker/) |
| Endpoint QR | [`tools/pairing/moxie_endpoint_qr.py`](../../tools/pairing/moxie_endpoint_qr.py) |
| Connect detection, config push, permit gate | [`supervisor/moxie_runtime/connection.py`](../../mqtt/supervisor/moxie_runtime/connection.py), [`fleet.py`](../../mqtt/supervisor/moxie_runtime/fleet.py) |
| Turns, fillers, streaming, vision events | [`supervisor/moxie_runtime/turns.py`](../../mqtt/supervisor/moxie_runtime/turns.py) |
| Cloud TTS publish | [`supervisor/moxie_runtime/voice.py`](../../mqtt/supervisor/moxie_runtime/voice.py) |
| Schedule query | [`supervisor/moxie_runtime/schedule.py`](../../mqtt/supervisor/moxie_runtime/schedule.py), [`moxie_sdk/schedule/`](../../mqtt/moxie_sdk/schedule/) |
| Telehealth | [`supervisor/moxie_runtime/telehealth.py`](../../mqtt/supervisor/moxie_runtime/telehealth.py), [`moxie_sdk/telehealth.py`](../../mqtt/moxie_sdk/telehealth.py) |
| Status HTTP (`/status`, `/permits`, `/schedule`, `/telehealth`, `/preview`) | [`supervisor/moxie_runtime/status_http.py`](../../mqtt/supervisor/moxie_runtime/status_http.py) |
| Wire builders | [`moxie_sdk/wire.py`](../../mqtt/moxie_sdk/wire.py), [`actions.py`](../../mqtt/moxie_sdk/actions.py) |
| Markup and planner | [`moxie_sdk/automarkup.py`](../../mqtt/moxie_sdk/automarkup.py), [`performance.py`](../../mqtt/moxie_sdk/performance.py), [`supervisor/markup.py`](../../mqtt/supervisor/markup.py) |

## Appendix A: OpenMoxie file map

| Concern | File |
|---|---|
| MQTT supervisor, topics, endpoint QR | `site/hive/mqtt/moxie_server.py` |
| Endpoint QR view / URL | `site/hive/views.py:137`, `site/hive/urls.py:15` (`/hive/endpoint/`) |
| ServiceConfiguration2 / IOTEndpoint protos | `site/hive/mqtt/protos/embodied/logging/{Cloud2,enums}_pb2.py` |
| QRCommand / StartPairingQR / QRMultiDecoder protos | `.../protos/embodied/wifiapp/QRCommands_pb2.py` |
| STT proto | `.../protos/embodied/perception/audio/zmqSTT_pb2.py` |
| LLM factory | `site/hive/mqtt/ai_factory.py` |
| STT handler | `site/hive/mqtt/zmq_stt_handler.py` |
| Conversation / LLM calls | `site/hive/mqtt/conversations.py`, `moxie_remote_chat.py` |
| Turn object | `site/hive/mqtt/volley.py` |
| Robot config / schedule / state store | `site/hive/mqtt/robot_data.py`, `scheduler.py` |
| Credentials / JWT | `site/hive/mqtt/robot_credentials.py` |
| Text → behavior markup | `site/hive/automarkup/**` |
| mosquitto config / keys | `site/data/openmoxie.conf`, `keys/`, `mqtt.Dockerfile` |
| Content / modules / launch QRs | `content_modules/*.json`, `site/hive/content/data.py`, `site/data/qr/*` |
| Docs | `doc/MoxieOverview.md`, `doc/Markup.md`, `doc/RemoteModuleAPI.md`, `doc/ContentModules.md` |

## Appendix B: endpoint QR generator (no protobuf runtime)

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
