"""
MoxieBus — talk to the robot's on-device ZeroMQ message bus.

The brain runs a dispatch proxy (embodied::dispatch ZMQEventBroadcaster):
    XSUB  tcp://127.0.0.1:5678   <- modules PUBLISH here
    XPUB  tcp://127.0.0.1:6789   -> modules SUBSCRIBE here
Each message is TWO frames:  [ descriptor FullName (utf-8) ] [ serialized protobuf ]
Subscription topic == the descriptor FullName string (ZMQ prefix match).

So a custom program on the robot (or tunnelled in via `adb forward tcp:5678 tcp:5678` etc.) can
drive the face/motors/LEDs/audio by publishing embodied.* protobufs, and observe sensors by
subscribing. This is the lever for running custom software INTO a Moxie without replacing the
whole firmware. Requires `pip install pyzmq protobuf`.

Source of truth: docs/reverse-engineering/robot-ipc-protocol.md
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/moxie_toolkit")

PUB_ENDPOINT = "tcp://127.0.0.1:5678"   # we PUBLISH here (broker's XSUB)
SUB_ENDPOINT = "tcp://127.0.0.1:6789"   # we SUBSCRIBE here (broker's XPUB)

def full_name(msg_or_cls):
    d = msg_or_cls.DESCRIPTOR
    return d.full_name

class MoxieBus:
    def __init__(self, pub=PUB_ENDPOINT, sub=SUB_ENDPOINT):
        import zmq
        self._zmq = zmq
        self.ctx = zmq.Context.instance()
        self.pub = self.ctx.socket(zmq.PUB); self.pub.connect(pub)
        self.sub = self.ctx.socket(zmq.SUB); self.sub.connect(sub)
        self._registry = {}  # full_name -> parser class

    def send(self, message):
        """Publish a protobuf message on the bus (framed [FullName][bytes])."""
        self.pub.send_multipart([full_name(message).encode(), message.SerializeToString()])

    def subscribe(self, *message_classes):
        """Subscribe to one or more protobuf message types (by descriptor full name)."""
        for cls in message_classes:
            fn = full_name(cls)
            self._registry[fn] = cls
            self.sub.setsockopt(self._zmq.SUBSCRIBE, fn.encode())

    def subscribe_all(self):
        self.sub.setsockopt(self._zmq.SUBSCRIBE, b"")

    def recv(self, timeout_ms=None):
        """Return (full_name, parsed_message_or_raw_bytes). Blocks unless timeout given."""
        if timeout_ms is not None:
            if not self.sub.poll(timeout_ms):
                return None
        frames = self.sub.recv_multipart()
        fn = frames[0].decode(errors="replace")
        body = frames[1] if len(frames) > 1 else b""
        cls = self._registry.get(fn)
        if cls is not None:
            m = cls(); m.ParseFromString(body); return (fn, m)
        return (fn, body)

    def close(self):
        self.pub.close(0); self.sub.close(0)


# ---- convenience builders for the most useful robot-control messages ----
def led(pattern, inloop=False):
    """embodied.lizzerface.SetLedrEventPB — set the status LED face pattern."""
    from embodied.lizzerface import lizzerfaceinput_pb2 as L
    return L.SetLedrEventPB(ledr=pattern, inloop=inloop)

def motor(motor_id, pos):
    """embodied.lizzerface.MotorSetPosEventPB — drive a motor to a position."""
    from embodied.lizzerface import lizzerfaceinput_pb2 as L
    return L.MotorSetPosEventPB(motor=motor_id, pos=pos)

def power(rail, enable=True):
    from embodied.lizzerface import lizzerfaceinput_pb2 as L
    return (L.PowerEnableEventPB if enable else L.PowerDisableEventPB)(rail=rail)


# ---- read side: the fused world-model of people (embodied.perception.fusion) ----
def fused_people_classes():
    """The perception-fusion message classes — the tracked world-model of people.
    Subscribe to them to receive parsed events instead of raw bytes:
        bus.subscribe(*fused_people_classes())
        fn, msg = bus.recv()
    Returns the roster snapshot (FusedPeoplePB) + the person-level event messages
    (added/removed/moved, started/stopped speaking, saying/said, smiled, engaged/
    disengaged). See docs/reverse-engineering/perception-fusion.md."""
    from embodied.perception.fusion import FusedPeople_pb2 as F
    return [
        F.FusedPeoplePB,
        F.FusedPersonAddedPB, F.FusedPersonRemovedPB, F.FusedPersonMovedPB,
        F.FusedPersonStartedSpeakingPB, F.FusedPersonStoppedSpeakingPB,
        F.FusedPersonSayingPB, F.FusedPersonSayingTimeoutPB, F.FusedPersonSaidPB,
        F.FusedPersonSmiledPB, F.FusedPersonEngagedPB, F.FusedPersonDisengagedPB,
    ]


# ---- time, timezone & wake alarms (embodied.sys / TimeEvents) ----
# See docs/reverse-engineering/power-and-system-events.md (Time, timezone & alarms)
def user_alarm(alarm_expires, *, timer_id=None, alarm_repeats=0):
    """Build a UserAlarmRequest to arm a wake/timer. timer_id defaults to
    TIMER_ID_USER_WAKE (the child's wake alarm); use TIMER_ID_PARENT_APP or a
    TIMER_ID_CUSTOM+n for others. alarm_expires = fire time, alarm_repeats = repeat
    interval (0 = one-shot). See power-and-system-events.md."""
    from embodied.system import TimeEvents_pb2 as T
    tid = T.UserAlarmRequest.TIMER_ID_USER_WAKE if timer_id is None else timer_id
    return T.UserAlarmRequest(timer_id=tid, alarm_expires=alarm_expires, alarm_repeats=alarm_repeats)

def time_zone_info(olson_id, midnight_in_timezone=""):
    """Build a TimeZoneInfo (the robot's local timezone as an IANA/Olson id)."""
    from embodied.system import TimeEvents_pb2 as T
    return T.TimeZoneInfo(olson_id=olson_id, midnight_in_timezone=midnight_in_timezone)


# ---- CLI: monitor the bus, or send a control message (needs pyzmq + a reachable robot) ----
def _main(argv=None):
    import argparse
    from embodied.lizzerface import enums_pb2 as E
    ap = argparse.ArgumentParser(prog="moxie-bus",
        description="Talk to a Moxie's on-device ZMQ bus. Tunnel first, e.g.:\n"
                    "  adb forward tcp:5678 tcp:5678 && adb forward tcp:6789 tcp:6789")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("monitor", help="subscribe to everything and print (full_name, bytes)")
    lp = sub.add_parser("led", help="set LED face pattern"); lp.add_argument("pattern")
    mp = sub.add_parser("motor", help="drive a motor"); mp.add_argument("motor"); mp.add_argument("pos", type=int)
    a = ap.parse_args(argv)
    b = MoxieBus()
    if a.cmd == "monitor":
        b.subscribe_all(); print("monitoring bus (Ctrl-C to stop)…")
        while True:
            fn, body = b.recv()
            print(fn, body if isinstance(body, bytes) else "\n"+str(body))
    elif a.cmd == "led":
        b.send(led(E.LedrPattern.Value(a.pattern))); print("sent LED", a.pattern)
    elif a.cmd == "motor":
        b.send(motor(E.Motor.Value(a.motor), a.pos)); print("sent motor", a.motor, a.pos)

if __name__ == "__main__":
    _main()
