"""
Cloud-transport helpers — build/parse the MQTT topics + envelope a Moxie backend uses.

Google IoT-Core topic convention (kept post-migration); {device_id} = robot UUID/serial:
  robot -> cloud:  /devices/{id}/events/{eventname}   (JSON)   ·   /devices/{id}/state
  cloud -> robot:  /devices/{id}/config               (JSON)
                   /devices/{id}/commands/{command}   (JSON)
                   /devices/{id}/commands/zmq         (binary: "{proto_full_name}:" + serialized)

The zmq command injects any embodied.* protobuf straight onto the robot's on-device ZMQ bus
(same messages as MoxieBus in bus.py). See docs/reverse-engineering/cloud-protocol.md.
"""
import json
# NOTE: QRCommand et al. live in the module `embodied.wifiapp.QRCommands_pb2` (file path),
# though their protobuf package/full_name is `embodied.unity.*`. Use the full_name for the bus.

# ---- topic builders ----
def events_topic(device_id, eventname):      return f"/devices/{device_id}/events/{eventname}"
def state_topic(device_id):                  return f"/devices/{device_id}/state"
def config_topic(device_id):                 return f"/devices/{device_id}/config"
def command_topic(device_id, command):       return f"/devices/{device_id}/commands/{command}"
def zmq_command_topic(device_id):            return f"/devices/{device_id}/commands/zmq"

# server-side wildcard subscriptions
SUBSCRIBE_EVENTS = "/devices/+/events/#"
SUBSCRIBE_STATE  = "/devices/+/state"

# ---- /commands/zmq binary framing: "{full_name}:" + protobuf bytes ----
def encode_zmq_command(message):
    """message: a protobuf message. Returns bytes to publish on /commands/zmq."""
    return (message.DESCRIPTOR.full_name + ":").encode("utf-8") + message.SerializeToString()

def decode_zmq_command(payload, registry=None):
    """Split a /commands/zmq payload into (full_name, body_bytes). If `registry` maps
    full_name -> message class, also returns the parsed message as the 3rd element."""
    i = payload.index(b":")
    full_name = payload[:i].decode("utf-8"); body = payload[i+1:]
    if registry and full_name in registry:
        m = registry[full_name](); m.ParseFromString(body); return (full_name, body, m)
    return (full_name, body, None)

# ---- JSON command / event envelope helpers ----
def command_json(command, **fields):
    """Build a cloud->robot JSON command payload (published to /commands/{command})."""
    return json.dumps({"command": command, **fields})

def parse_event(topic, payload):
    """Parse an incoming robot->cloud message. Returns dict with device_id, basetype,
    eventname (for events), and the decoded JSON payload (or raw bytes)."""
    parts = topic.split("/")
    out = {"device_id": parts[2] if len(parts) > 2 else None,
           "basetype":  parts[3] if len(parts) > 3 else None,
           "eventname": parts[4] if len(parts) > 4 else None}
    try:    out["payload"] = json.loads(payload)
    except Exception: out["payload"] = payload
    return out

# common event names (robot -> cloud)
EVENT_REMOTE_CHAT   = "remote-chat"
EVENT_ACTIVITY_LOG  = "client-service-activity-log"   # multiplexed by payload["subtopic"]
EVENT_HTTP_TOKEN    = "client-service-http-token"


# ---- higher-level builders for goal-#2 server implementers ----
def telehealth_play_output(text, markup="", *, session_id="", line_id="", line_params=None):
    """Build a telehealth PLAY_OUTPUT TelehealthMessage — make Moxie speak `text` and perform the
    `<mark cmd:...>` `markup` live (remote-puppet). Publish to /devices/{id}/commands/telehealth
    (JSON command wrapping TelehealthRobotCommand) or over the activity-log subtopic=telehealth.
    See docs/reverse-engineering/content-and-conversation.md#telehealth--remote-puppet-mode."""
    from embodied.telehealth import TeleHealth_pb2 as TH
    out = TH.Output(text=text, markup=markup, line_id=line_id)
    if line_params:
        out.line_params.extend(line_params)
    return TH.TelehealthMessage(action=TH.PLAY_OUTPUT, output=out, session_id=session_id)

def telehealth_session(action, *, session_id=""):
    """START_SESSION / END_SESSION / INTERRUPT (pass the TeleHealth Action enum)."""
    from embodied.telehealth import TeleHealth_pb2 as TH
    return TH.TelehealthMessage(action=action, session_id=session_id)

def service_configuration(*, mqtt_host=None, webservice_root=None, override_port=None,
                          connection_type=None, endpoint_id=None, disable_verify=None,
                          disable_sync=None, gcp_project=None, webservice_pin=None):
    """Build an embodied.logging.ServiceConfiguration to repoint a robot at your backend.
    Push over MQTT config. e.g. service_configuration(mqtt_host='my.example.com', override_port=8883,
    connection_type=EMBODIED_LOCAL, disable_verify=True). Only set what you need.
    See docs/reverse-engineering/cloud-protocol.md (Service configuration)."""
    from embodied.logging import Cloud_pb2 as C
    cfg = C.ServiceConfiguration()
    if mqtt_host is not None: cfg.mqtt_host = mqtt_host
    if webservice_root is not None: cfg.webservice_root = webservice_root
    if override_port is not None: cfg.override_port = override_port
    if connection_type is not None: cfg.connection_type = connection_type
    if endpoint_id is not None: cfg.endpoint_id = endpoint_id
    if disable_verify is not None: cfg.disable_verify = disable_verify
    if disable_sync is not None: cfg.disable_sync = disable_sync
    if gcp_project is not None: cfg.gcp_project = gcp_project
    if webservice_pin is not None: cfg.webservice_pin = webservice_pin
    return cfg
