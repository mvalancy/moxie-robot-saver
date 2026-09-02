# 🔐 Letting a robot in — "pending" and how to permit it

Your Moxie server does **not** hand your child's details to just anything that connects to
it. A robot has to be on its list first. This guide is the whole story: what *pending*
means, where the button is, and when you will never see any of it.

---

## The one-paragraph version

Moxie talks to your server over a home-network message broker, and that broker — like the
one Embodied ran — lets any device on the network connect. So the server does its own
checking: **a robot it has not been told about is "pending".** A pending robot is sent a
stub of a configuration with *no* child information in it, is not answered by Moxie's
brain, and gets none of your settings or schedule. It sits in the console until you say
yes. One click, and it is your Moxie.

---

## Will I ever see this?

**Usually not.** Pairing through the parent console *is* you saying "this robot is mine",
so the console permits the robot as part of finishing the pairing. You see the pending
list when something arrives *without* going through that flow:

| Situation | What you see |
|---|---|
| You paired through the console's Wi-Fi/QR flow, console knew the robot's id | Nothing — already permitted |
| A robot re-homed to your server by scanning an endpoint QR | It appears as **pending**; click **Permit** |
| Your robot rejoined after a factory reset with a new id | **Pending** again — permit it |
| A second robot, a friend's robot, or a stray device on your Wi-Fi | **Pending**, and it stays that way until you permit it |

## Permitting a robot

1. Open the parent console (`http://localhost:8080` by default) and go to **Moxie**.
2. Find the **🔐 Robot access** card. A robot waiting for you is listed under
   *"Waiting for you"* with its device id (`d_` followed by a long code).
3. Click **Permit**.

That is it — no restart, no unplugging the robot. The server immediately sends that robot
its real configuration, and Moxie starts behaving normally within a few seconds.

Under *"Allowed"* you can **Revoke** any robot you no longer want served. The next
configuration it receives has your child's details stripped out of it.

## What a pending robot actually gets

Deliberately, almost nothing:

- a configuration containing only a "not paired" marker, a "do not upload anything" flag,
  and an empty settings envelope — **no nickname, no birthday, no volume, no bedtime**;
- one fixed spoken line if it tries to start a conversation: *"I'm not connected to a
  family yet. Ask a grown-up to add me in the Moxie console."* Moxie's brain is never
  asked, and nothing it says is remembered;
- an empty answer when it asks for a schedule, so it does not hang waiting;
- nothing at all for anything else — no microphone stream, no activity history.

The only thing the server does listen to from a pending robot is its "hello, I'm here"
announcement — otherwise it could never appear in the list for you to permit it.

## The "let any robot in" switch

The same card has a checkbox: **"Let any robot that connects use this server."**

> ⚠️ **Leave this off.** With it on, any device that can reach your server is paired and
> receives your child's name and birthday. It exists for two reasons: testing, and servers
> that were already running before this check was added, so they keep working while you
> permit your own robot.

Operators can set the same thing with the `MOXIE_ALLOW_UNVERIFIED_BOTS=1` environment
variable (in `.env` for the `docker compose` stack); `0` locks it shut and overrides the
checkbox. The console always shows the setting that is actually in force, so an appliance
opened by the environment variable can never *look* closed.

## Where the list is kept

`fleet/permits.json` in the server's data directory (`MOXIE_DATA_DIR`, the `/data` volume
in the compose stack), beside the house-rules config. It survives restarts and upgrades. A
damaged or missing file means "nobody is permitted" — it fails safe, never open.

## If something is not working

- **Moxie says "I'm not connected to a family yet"** → it is pending. Permit it (above).
- **The 🔐 Robot access card is missing** → the supervisor is not running; check the
  server, then reload the console.
- **The robot is not in either list** → it has not reached the broker at all. That is a
  network/pairing problem, not a permission one — see
  [`find-moxie-on-lan.md`](find-moxie-on-lan.md) and
  [`first-time-setup.md`](first-time-setup.md).
- **You permitted it and nothing happened** → give it a few seconds; the server re-sends
  the configuration immediately, but the robot applies it on its own schedule. If it still
  will not settle, power-cycle the robot.

---

For the protocol details behind this — what exactly is pushed, and the one assumption we
are carrying about the "not paired" value — see
[mqtt-and-conversation.md §3.7](../architecture/mqtt-and-conversation.md) and
[config-and-telemetry-contract.md](../architecture/config-and-telemetry-contract.md).

---
📖 [Guides index](README.md) · [Docs index](../README.md) · [Back to top](../../README.md)
