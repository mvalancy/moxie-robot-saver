# Bench runbook: bring a real Moxie onto your server

The order of operations for the day a real robot is on the bench, and what to do at each fork:
the Wi-Fi code, the server code, reading the Connection monitor, then **Add to my account**.
Robot details refer to firmware v3.6.4-Zephyr / OTA v24.10.803
([reference](../reverse-engineering/firmware/firmware-803-reference.md)).

> **Status.** A real Moxie has scanned this project's Wi-Fi code and joined the network
> ([live notes](../debugging/live-hardware-debug.md)). Everything after that (the server code
> landing on our broker, the Connection monitor lines for a real robot, Add to my account for a
> real robot) is built to the documented contract and tested against hermetic doubles, and against a
> real broker and the real supervisor with an MQTT client standing in for the robot. **No physical
> Moxie has completed this flow on this project yet.**

## Before you start

- **The stack is up.** `docker compose up` ([guide](one-command-stack.md)); open the console at
  `http://<this-computer's-ip>:8080` on a phone or laptop and log in.
- **The broker address is one the robot can reach.** The console's **🔗 Server** tab fills in this
  computer's LAN address. A VPN address only your laptop can reach will not work for the robot.
- **Know the firmware.** Look under the code box on Moxie's face: a badge reading
  "EmbodiedProduction" or "OpenMoxie" means firmware 801 or 803, which a code can re-home (the badge
  may only show once it has joined Wi-Fi). No badge means older than 801: no code can move it, it
  needs the [flash-first path](revive-your-moxie.md#path-c-flash-an-older-robot-first)
  ([live notes](../debugging/live-hardware-debug.md)).
- **Pick the simplest network.** A normal WPA2 network on 2.4 GHz, or **🚀 Moxie Direct** (this
  computer's own access point), which removes every router setting from the question.

## 1. The Wi-Fi code (Wi-Fi only)

Console → **📶 Wi-Fi**: the network name and password, band **2.4 GHz** → **Make the Wi-Fi code**.
Put Moxie on its code screen and hold the code up to its camera. Moxie Direct's Wi-Fi code is the
same kind, with nothing to type.

This code carries only the network name and password (`StartPairingQR.wifi_only`, and no pairing
key). Leave **Put a pairing key in the code** unticked: a pairing key sends the robot looking for
the original cloud, which is gone ([live notes](../debugging/live-hardware-debug.md),
[`moxie_qr.py`](../../tools/pairing/moxie_qr.py) `encode_wifi_only`). That option exists only for
the original app's pairing and for testing with **Simulate robot scan**.

Moxie joins the network and waits for a second code. This server cannot see that happen: the robot
does not contact it until step 2. To check, find it on the network
([find Moxie on the LAN](find-moxie-on-lan.md)).

## 2. The server code

Console → **🔗 Server**: check the broker address, then show Moxie the code. It is the `om` code
with this broker's host, port 8883 and `disable_verify`, so firmware 803 accepts the stack's
self-signed certificate ([QR commands](../reverse-engineering/protocol/qr-commands.md),
[network trust](../reverse-engineering/protocol/network-trust.md)). Keep the **🔎 Connection
monitor** below the code in view.

## 3. Read the Connection monitor

The monitor shows the broker's own log lines about connections and errors, and the supervisor's
notes, newest first.

| What you see | What it means | Next |
|---|---|---|
| **Nothing at all.** The top line stays "Broker up · app: … · waiting for Moxie…" and the log says "No activity yet". | The robot never opened a connection to the broker. On a pre-801 robot this was measured: it reads the server code, beeps, returns to the code screen, and not one packet reaches the broker. | Check the badge (no badge: older than 801, [flash first](revive-your-moxie.md#path-c-flash-an-older-robot-first)). Check the robot is on your network ([find it](find-moxie-on-lan.md)) and that the broker address in the Server tab is reachable from that network (same subnet, no guest-network client isolation). Moxie Direct rules out both. Then show the server code again. |
| **A red line that starts `OpenSSL Error`** and names an alert, such as `tlsv1 alert unknown ca`. | The robot reached the broker's port and refused its TLS certificate. | Firmware 801 needs a publicly trusted certificate (a real domain and Let's Encrypt); 803 accepts the self-signed one only through `disable_verify`, which the Server tab's code sets ([Revive your Moxie, Path B step 3](revive-your-moxie.md#path-b-re-home-an-801-or-803-robot-with-a-qr)). If you made the code yourself, rebuild it with the Server tab or `tools/pairing/moxie_endpoint_qr.py`. A pre-801 robot reaches this broker only if its traffic for `mqtt.googleapis.com` is redirected here, and then it refuses the certificate the same way; no certificate fixes that one. |
| **`🤖 robot connected: d_…`**, and at the top **✅ Moxie connected — d_…** | It worked: the robot is on your broker. Until you let it in it is **pending**: it gets a configuration with no child data, and one fixed line if it tries to talk ([permitting a robot](permitting-a-robot.md)). | Step 4. |

The red `OpenSSL` line is the broker's log as the supervisor forwards it (the broker publishes its
log, and the supervisor shows every line that mentions an error). The alert text itself was seen at
the broker during the live session; it has not yet been seen in this monitor with a real robot.

## 4. Add it to your account

Console → **🤖 Moxie**. A connected robot that is on no account is listed in two places: under
**No Moxie paired yet** and in **🔐 Robot access → Waiting for you**. Press **Add to my account**
in either one. That one click:

- puts the robot (its `d_…` id) on your account, bound to your child; a child named "Moxie Kid" is
  made if the account has none;
- lets it in, with the same permit the **Permit** button sends, so the robot gets your child's
  settings straight away.

The robot card then appears: live state, **⚙️ Settings**, **📈 Insights**, **🛡️ Safety**, **🧠 What
Moxie remembers**, **Wake up**, **Unpair this robot** and **Factory reset**. The card's *Serial* line
shows the robot's id on this server (`d_…`): the robot never sends its hardware serial here.

**Permit** on its own lets a robot in without putting it on your account, so it gets no robot card.
Nothing is ever added to an account without the click.

**What adding a robot proves, and what it does not.** This server cannot tell one Moxie from
another: nothing a robot sends carries the code it scanned. **Add to my account** is your word that
the robot is yours, the same trust as Permit, which anyone who can open this console on your network
can press. Add only the robot you just showed the codes to.

**No Add to my account button?** The page says why, where the button would be:

| What you see | Next |
|---|---|
| Beside the robot in Robot access, and on your robot card: "This account already has a robot (…): unpair it first." The 📶 Wi-Fi tab says the same. | One robot per account. An earlier test leaves exactly this (**Simulate robot scan** makes a record). Press **Unpair this robot** on the robot card; **Add to my account** then appears. |
| Under **No Moxie paired yet**: "The robot service cannot be reached right now". | The supervisor is down or not answering, so the page cannot check. Start the stack again. |

If a click is refused, nothing was changed. The page shows the server's own words, and each one
means something changed after the page last looked:

| The message | Next |
|---|---|
| "That robot is already on another account on this server" | It was added from another account first. Unpair it there. |
| "This account already has a robot (…). Unpair the current robot first" | A robot was added to this account from another page. Unpair one first. |
| "No robot with that id has connected to this server" | It left the broker: back to step 3. |
| "This server cannot reach its robot side" | The supervisor went down. Start it and try again. |

## Afterwards

- The robot applies its new configuration on its own schedule. Give it a few seconds; if it will not
  settle, power-cycle it ([permitting a robot](permitting-a-robot.md#if-something-is-not-working)).
- Unpair and factory reset are on the robot card ([what they do](../features/robot-lifecycle.md#built-here-unpair-and-factory-reset)).

## Code reference

- The Wi-Fi tab's code: `POST /local/wifi/payload`; the pairing-key code: `POST /local/pairing/prepare`
  ([`routes/pairing.py`](../../server/moxie_server/routes/pairing.py)).
- Add to my account: `POST /local/robots/{device_id}/claim` with the parent's token; `/local/state`
  lists the connected robots no account has added as `unclaimed`, and says `unclaimed_known: false`
  when the supervisor could not be asked (the list is then empty because nobody could check).
- Tests: [`test_wifi_first_qr.py`](../../sim/tests/test_wifi_first_qr.py),
  [`test_robot_claim.py`](../../sim/tests/test_robot_claim.py), the browser suite
  [`test_robot_claim.mjs`](../../sim/test_robot_claim.mjs), and
  [`test_sil_robot_claim.py`](../../sim/tests/test_sil_robot_claim.py): steps 3 and 4 against a real
  broker and the real supervisor, with an MQTT client standing in for the robot.

---
📖 [Guides index](README.md) · [Revive your Moxie](revive-your-moxie.md) · [Docs index](../README.md)
