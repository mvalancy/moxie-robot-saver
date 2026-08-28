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
