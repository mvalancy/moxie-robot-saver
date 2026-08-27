# Guide: first-time setup (Phase 1)

Set up a Moxie from scratch using the local parent-app server. This covers the **Wi-Fi pairing**
step, which is working today. (Making Moxie *talk* is Phases 2–3 — see [`../../ROADMAP.md`](../../ROADMAP.md).)

## 1. Start the server
On any machine on your network (Linux/macOS/Windows):
```bash
pip install -r server/requirements.txt
python server/run.py            # listens on 0.0.0.0:8080
```
Find that machine's IP (e.g. `192.168.1.9`, or a Tailscale IP for remote access).

## 2. Open the web app from your phone
Browse to `http://<that-ip>:8080`. If you can't reach it, check your firewall allows the port on the
interface your phone uses (LAN or Tailscale).

## 3. Set up the child + Wi-Fi
1. Enter any email → **Start** (local account name only — no real login, no email sent).
2. Enter your child's first name.
3. Enter your **Wi-Fi SSID and password**. Leave the band on **2.4 GHz** — Moxie prefers it.
4. Tap **Generate pairing QR**.

You'll get a QR code plus a **recovery phrase** — write the phrase down; it restores your child's
encrypted data later.

## 4. Pair Moxie
1. Put Moxie in **pairing / QR-scan mode** (if it was previously paired to Embodied, this may need a
   factory reset first — see [`factory-reset-a-paired-moxie.md`](factory-reset-a-paired-moxie.md)).
2. Hold the phone's QR up to Moxie's camera.
3. Moxie acknowledges the scan and joins your Wi-Fi.

To confirm it connected, find it on the network: [`find-moxie-on-lan.md`](find-moxie-on-lan.md).

## No robot handy?
The web app's **"Simulate robot scan"** button completes the whole pairing flow with no hardware, so
you can verify the server end to end.

## What happens next
A firmware-801/803 Moxie will then wait for a **second, different-looking QR** — the endpoint code
that tells it to use your server for conversations. That's Phase 2 (`mqtt/`). Until then, Moxie is on
your Wi-Fi and paired in the app, but won't talk yet.
