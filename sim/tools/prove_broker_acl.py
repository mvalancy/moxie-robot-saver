#!/usr/bin/env python3
"""Assert a REAL mosquitto enforces the P0 broker ACL — security-broker-auth.md §2.

Driven by `sim/run_acl_proof.sh`, which starts a throwaway broker from the repo's own
`mqtt/broker/{compose-mosquitto.conf,acl,acl-robot}` and passes the ports and the
scratch credential in the environment.

**Every check is delivery-based.** MQTT 3.1.1 PUBACKs a publish the broker then drops
for ACL reasons, and SUBACKs a subscription it will never deliver on, so a proof written
against acks would pass on a broker with no ACL at all. Each check therefore publishes
from one client and asserts what a *second* client did or did not receive.
"""
from __future__ import annotations

import os
import sys
import time

import paho.mqtt.client as mqtt

HOST = "127.0.0.1"
PLAIN = int(os.environ.get("MOXIE_ACL_PORT_PLAIN", "2095"))
WS = int(os.environ.get("MOXIE_ACL_PORT_WS", "2096"))
ROBOT = int(os.environ.get("MOXIE_ACL_PORT_ROBOT", "2097"))
USER = os.environ.get("MOXIE_ACL_USER", "supervisor")
SECRET = os.environ.get("MOXIE_ACL_SECRET", "")

results: list[tuple[str, bool, str]] = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("   ✅ " if ok else "   ❌ ") + name + (f"   [{detail}]" if detail else ""))


def make(client_id, *, user=None, password=None, ws=False):
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id,
                    transport="websockets" if ws else "tcp")
    if user is not None:
        c.username_pw_set(user, password)
    c.rc = None
    c.msgs = []
    c.on_connect = lambda cl, u, f, rc, props=None: setattr(cl, "rc", rc)
    c.on_message = lambda cl, u, m: cl.msgs.append((m.topic, m.payload.decode()))
    return c


def connect(c, port, timeout=5.0):
    try:
        c.connect(HOST, port, 10)
    except Exception as e:                       # a refused CONNECT can raise here
        return f"refused: {e}"
    c.loop_start()
    deadline = time.time() + timeout
    while c.rc is None and time.time() < deadline:
        time.sleep(0.05)
    return str(c.rc)


def deliver(subscriber, topic, publisher, publish_topic, payload, wait=1.5):
    """What `subscriber` receives on `topic` when `publisher` sends to `publish_topic`."""
    subscriber.subscribe(topic)
    time.sleep(0.4)
    subscriber.msgs.clear()
    publisher.publish(publish_topic, payload, qos=1)
    time.sleep(wait)
    return list(subscriber.msgs)


def churn(tag):
    """Make the broker log a connect + disconnect, so $SYS/broker/log has traffic."""
    c = make(f"churn-{tag}")
    connect(c, PLAIN)
    time.sleep(0.3)
    try:
        c.loop_stop()
        c.disconnect()
    except Exception:
        pass
    time.sleep(0.7)


def main() -> int:
    if not SECRET:
        print("MOXIE_ACL_SECRET is unset — run this through sim/run_acl_proof.sh")
        return 2

    print("\n── the supervisor credential (§2.2) ──")
    sup = make("supervisor", user=USER, password=SECRET)
    check("the supervisor connects with the minted credential",
          connect(sup, PLAIN) == "Success")

    bad = make("supervisor", user=USER, password="wrong-password")
    rc = connect(bad, PLAIN)
    check("a WRONG password is refused at CONNECT", "Success" not in rc, rc)

    naked = make("evil", user=USER, password=None)
    rc = connect(naked, PLAIN)
    check("claiming username=supervisor with NO password is refused",
          "Success" not in rc, rc)

    print("\n── what the supervisor may reach ──")
    got = deliver(sup, "/devices/#", sup, "/devices/d_aaa/config", "cfg")
    check("the supervisor reads and writes /devices/#", bool(got), str(got))
    sup.subscribe("$SYS/broker/log/#")
    time.sleep(0.5)
    sup.msgs.clear()
    churn("a")
    sys_lines = [t for t, _ in sup.msgs if t.startswith("$SYS")]
    check("the supervisor reads $SYS/broker/log/# (the robot connect watch)",
          bool(sys_lines), f"{len(sys_lines)} log lines")

    print("\n── an anonymous robot on the TLS listener (§2.1) ──")
    aaa = make("d_aaa")
    check("an anonymous robot still connects — P0 refuses nothing",
          connect(aaa, ROBOT) == "Success")
    check("d_aaa reads its OWN config",
          bool(deliver(aaa, "/devices/d_aaa/config", sup, "/devices/d_aaa/config", "own")))
    check("d_aaa CANNOT read another robot's config",
          deliver(aaa, "/devices/d_bbb/config", sup, "/devices/d_bbb/config", "other") == [])
    check("d_aaa's `/devices/+/config` wildcard yields nothing for another robot",
          deliver(aaa, "/devices/+/config", sup, "/devices/d_bbb/config", "other") == [])
    check("d_aaa writes its OWN state",
          bool(deliver(sup, "/devices/#", aaa, "/devices/d_aaa/state", "mine")))
    check("d_aaa CANNOT write another robot's state",
          deliver(sup, "/devices/#", aaa, "/devices/d_bbb/state", "spoof") == [])

    aaa.subscribe("$SYS/broker/log/#")
    sup.msgs.clear()
    time.sleep(0.5)
    aaa.msgs.clear()
    churn("b")
    check("d_aaa CANNOT read $SYS/broker/log/# — fleet enumeration closes",
          not any(t.startswith("$SYS") for t, _ in aaa.msgs)
          and any(t.startswith("$SYS") for t, _ in sup.msgs),
          f"robot={len(aaa.msgs)} supervisor="
          f"{len([t for t, _ in sup.msgs if t.startswith('$SYS')])}")

    liar = make("liar", user=USER, password=None)
    rc = connect(liar, ROBOT)
    stolen = (deliver(sup, "/devices/#", liar, "/devices/d_bbb/state", "pwn")
              if "Success" in rc else [])
    check("on the robot listener, claiming username=supervisor buys NOTHING",
          stolen == [], f"connect={rc}")

    print("\n── the browser SIM over websockets (§2.5) ──")
    ws = make("mqttjs_proof", ws=True)
    check("the browser SIM connects anonymously over WS", connect(ws, WS) == "Success")
    check("it still sees a real robot's reply (the observer read is preserved)",
          bool(deliver(ws, "/devices/+/commands/remote_chat", sup,
                       "/devices/d_real/commands/remote_chat", "reply")))
    check("it still publishes as d_sim",
          bool(deliver(sup, "/devices/#", ws, "/devices/d_sim/events/remote-chat", "hi")))
    check("it CANNOT make a real robot speak",
          deliver(sup, "/devices/#", ws, "/devices/d_real/commands/remote_chat", "pup") == [])
    ws.subscribe("$SYS/broker/log/#")
    sup.msgs.clear()
    time.sleep(0.5)
    ws.msgs.clear()
    churn("c")
    check("it CANNOT read $SYS/broker/log/#",
          not any(t.startswith("$SYS") for t, _ in ws.msgs)
          and any(t.startswith("$SYS") for t, _ in sup.msgs),
          f"ws={len(ws.msgs)}")

    for c in (sup, aaa, ws):
        try:
            c.loop_stop()
            c.disconnect()
        except Exception:
            pass

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n   {len(results) - len(failed)}/{len(results)} checks passed")
    for name in failed:
        print(f"   FAILED: {name}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
