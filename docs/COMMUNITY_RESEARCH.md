# Moxie revival — community landscape (2026-08)

Snapshot of existing work so we build the *gap*, not a duplicate.

## TL;DR for THIS project
- **Nobody has recreated the parent app.** OpenMoxie (the canonical project) replaces the
  **robot's** cloud (MQTT/IoT) and does all configuration through its **own Django web UI**.
  It has **no parent-app backend and no parent app**. That is exactly the gap we target.
- The robot's *functionality* (talking, activities, LLM chat) is delivered over **MQTT**, which
  is OpenMoxie's domain. The original **parent app** used a *separate* **REST** service
  (`client-service-api.embodied.com`) for account, pairing-QR issuance, child profiles,
  schedules, content prefs, and "insights"/analytics. That REST service is what our repo maps
  and reimplements.

## Two independent cloud channels (critical mental model)
```
            REST/HTTPS                              MQTT/TLS (8883)
Parent app  ───────────►  client-service-api        Robot ───────────► IoT broker
 (this APK)               .embodied.com  (DEAD)                         (DEAD)
                          ▲                                             ▲
                          │  we reimplement this                       │  OpenMoxie reimplements this
                        OUR SERVER                                    OpenMoxie
```
The robot chooses its MQTT broker from a compiled-in enum `embodied.logging.IOTEndpoint`
that already contains an `OPEN_MOXIE` value. The QR's `iot-endpoint` integer selects among these.

## OpenMoxie (canonical) — https://github.com/jbeghtol/openmoxie
- Author **Justin Beghtol**, ex-Embodied; CEO-sanctioned as the official off-ramp. MIT.
  Django 5.1 + sqlite + eclipse-mosquitto. Web UI on `:8001/hive`, MQTT on `:8883` (self-signed,
  anonymous). Last release **v0.8 (2025-02-28)**; functionally dormant but author still answers issues.
- Replaces the robot's cloud on the LAN. Needs an **OpenAI key** (Whisper STT + chat) by default.
- Has the **real Embodied protobufs** under `site/hive/mqtt/protos/embodied/...` (Log, Cloud2,
  zmqSTT, **QRCommands**) and porting docs under `doc/` — the authoritative protocol reference.
- Works: Daily Missions, Reading, Wild Workout, Tips&Tricks, jokes, dance, breathing, custom LLM
  chat, schedules, per-robot config, face/eye color, Puppet/Telehealth. Missing: newer modules
  (Ocean Explorer, Animal Faces, Story Maker) and **any parent-app experience**.

### How a robot is actually revived (mechanism)
- **No modified APK, no DNS trick, no parent app.** The escape hatch shipped in the final OTA:
  - **24.10.801** (Oct 2024): enables custom cloud endpoint, but requires a **publicly-signed** cert.
  - **24.10.803** (Jan 1 2025, final): honors `disable_verify`, so OpenMoxie's **self-signed** cert works. This is the target firmware.
- **Migration QR** from `GET /hive/endpoint/`: `{"debug":{"command":"om","param":"<b64 ServiceConfiguration2>"}}`
  with `ServiceConfiguration2{gcp_project="openmoxie", mqtt_host=<LAN>, override_port=8883, disable_verify=true}`.
  Robot camera scans it → permanently repointed at your broker (`RightPoint::on_QRCommand`).
- **WiFi QR** from `GET /hive/wifi_qr/`: `embodied.wifiapp.QRCommands.StartPairingQR{wifi_only, ssid,
  password, is_hidden, band_select}`, serialized, prefixed with the literal **`PA`** header —
  **identical to what the parent app's `ProtoPairing` produces** (independent confirmation of our decode).
- Pairing "completes" because OpenMoxie pushes `pairing_status:"paired"` over MQTT.

### Firmware compatibility & recovery
- 803 → plug-and-play OpenMoxie. 801 → need a publicly-signed broker cert OR contact jbeghtol
  (issue #57) for a code to pull the 803 OTA. Older than 801 → not recoverable (locked bootloader,
  signed images unavailable); paid third-party reflash services exist via r/MoxieRobot.
- Gotcha (issue #60): on 801 a blank `google_api_key` makes `bo-wifi.apk` crash-loop; paste any
  syntactically valid GCP service-account JSON to dodge it.

## Robot OS / ADB
- Robot runs **Android** with **locked bootloader + Verified Boot + SELinux** (Lantronix). Not rooted publicly.
  Face is a **Unity** app projected onto a fresnel faceplate; `bo-wifi.apk` is the on-device Wifi/pairing app;
  native cloud layer is `embodied::logging::cloud::RightPoint`.
- **ADB reads the filesystem.** Robot identity for MQTT (Google-IoT style JWT/RS256):
  `adb pull /sdcard/EmbodiedStaticData/PERSISTENT_DATA/uuid.txt` and `.../rightpoint/RS256.key`.
  Device id = `d_<uuid>`. (Only needed to *impersonate a robot client*; a normal OpenMoxie setup doesn't need ADB.)

## Other repos
| Repo | Notes |
|---|---|
| `jbeghtol/openmoxie` | canonical |
| `Noonster77/openmoxie` | most active fork; **LM Studio local LLM + local faster-whisper** (no OpenAI bill), reconnect/locking fixes, Parent Corner, transcripts, tests |
| `vapors/openmoxie-ollama` | local **Ollama** / xAI Grok + faster-whisper ("unfiltered, not for kids") |
| `nhertanto/Embodied-Moxie` | ex-Embodied designer's Jinja2/ChatScript source for original activities — content reference |
| `andrsvlz/openmoxie-espanol` | Spanish localization |
| (~40 forks) | mostly mirrors |
No firmware-image repos, no APK-mod repos, **no parent-app repo**, no independent protocol-RE repos.

## Community hubs
- **Reddit r/MoxieRobot** (de-facto hub; u/OpenMoxie ~ ex-employee support). GitHub Discussions on the repo (technical).
- **robotsaroundthehouse.com** — best written setup guides (Mac & Win11), version history, teardown.
- Facebook group `groups/873320370652494`; YouTube walkthroughs. **No Discord found.**
- Press: PIRG, Techdirt, Slashdot, AppleInsider, Fight-to-Repair (all Dec 2024 open-sourcing coverage).

## Where OUR repo fits (the gap)
1. A clean-room **spec of the parent-app REST API** (`client-service-api.embodied.com`) — does not exist anywhere.
2. Clean-room **pairing-QR tooling** independent of the dead app (done: `tools/pairing/`).
3. A **local, cross-platform web server** that recreates the *parent-app* features (account-free),
   optionally bridging to OpenMoxie's MQTT layer for robot data (insights/activity logs).
