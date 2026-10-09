# First-time setup

Put a Moxie on your Wi-Fi using the local parent-app server. This is one step of
[Revive your Moxie](revive-your-moxie.md), which covers the rest; on the day a real robot is on the
bench, follow the [bench runbook](bench-runbook.md).

## 1. Start the server
If you run the [one-command stack](one-command-stack.md), the console is already on port 8080. To run
only the parent-app server, on any machine on your network:
```bash
pip install -r server/requirements.txt
python server/run.py            # listens on 0.0.0.0:8080
```
Find that machine's LAN address: your router's list of connected devices shows it.

## 2. Open the web app from your phone
Browse to `http://<that-ip>:8080`. If you can't reach it, check your firewall allows the port on the
interface your phone uses (LAN or VPN).

## 3. Set up the child + Wi-Fi
1. Enter any email → **Start** (local account name only — no real login, no email sent).
2. In the **📶 Wi-Fi** tab, enter your child's first name. **The name you type is the name Moxie
   says**: in its hello, its opener and every answer, once the robot is on your account (step
   *What happens next*). Typing it again later renames your child; it does not add a second one.
   Up to 40 letters, digits, spaces, periods, apostrophes or hyphens. Until your account has a
   name for your child, Moxie says its default name (`friend`, or `MOXIE_CHILD_NICKNAME`).
3. Enter your **Wi-Fi SSID and password**. Leave the band on **2.4 GHz** — Moxie prefers it.
4. Tap **Make the Wi-Fi code**.

**Where the name goes.** It is kept in this server's database and in the robot's settings on this
server, and it is sent to the robot. It is also in every prompt Moxie's brain gets, and in every line
Moxie says that names your child, so it goes to whatever brain and voice endpoints you configured,
which may be cloud services. Unpair and factory reset take it off the robot's settings; your child's
profile stays on your account until you delete it. This server has no real sign-in yet (an account is
just its email), so someone on your network who opens the console with your email, or reads the
supervisor's status port, can see the name. The full list:
[config contract, the child's name](../architecture/config-and-telemetry-contract.md#the-childs-name-the-parents-record-per-robot).

The code carries only your network name and password: it is the **Wi-Fi-only** code, the right
first code for a robot coming to this server. Leave **Put a pairing key in the code** (under *For the
original Moxie app or Simulate robot scan*) unticked: a pairing key sends the robot looking for the
original cloud, which is gone ([live notes](../debugging/live-hardware-debug.md)). With that box
ticked you get the original app's pairing-key code instead, plus a **recovery phrase** to write down.

## 4. Show Moxie the code
1. Put Moxie in **pairing / QR-scan mode** (if it was previously paired to Embodied, this may need a
   factory reset first — see [`factory-reset-a-paired-moxie.md`](factory-reset-a-paired-moxie.md)).
2. Hold the phone's QR up to Moxie's camera.
3. Moxie acknowledges the scan and joins your Wi-Fi.

To confirm it connected, find it on the network: [`find-moxie-on-lan.md`](find-moxie-on-lan.md).

## No robot handy?
The web app's **"Simulate robot scan"** button completes the whole pairing flow with no hardware, so
you can verify the server end to end. It needs the pairing-key code: tick **Put a pairing key in the
code** before you make it (a Wi-Fi-only code pairs nothing to simulate).

## What happens next
A firmware-801/803 Moxie then waits for a **second QR**, the endpoint code that points it at your
broker. Show it the code from the console's **🔗 Server** tab. Once the robot reaches your broker,
open **🤖 Moxie** and press **Add to my account**: the robot card (settings, insights, memory, Wake,
Unpair) appears, and the live box says **Moxie calls your child: …**, the name the robot has now. The [bench runbook](bench-runbook.md) covers every step and what each Connection
monitor line means; see also [Revive your Moxie, Path B](revive-your-moxie.md#path-b-re-home-an-801-or-803-robot-with-a-qr).
This part (the server code, then Add to my account) is built to the documented flow and not yet
done with a physical Moxie ([bench runbook](bench-runbook.md), Status).
