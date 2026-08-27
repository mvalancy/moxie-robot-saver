# 13 — Camera & Vision: can we pipe Moxie's camera into a local CV/VLM stack?

**Scope.** Can the Moxie robot's **camera** be tapped and fed into a self-hosted computer-vision
stack (OpenCV + a fast detector + a local VLM) as part of the "Moxie saver" project? This map
separates **CONFIRMED** (read first-hand from the decompiled parent app, the OpenMoxie source, or
authoritative vendor/patent docs) from **SPECULATIVE** (inference, community lore, or
patent-language that may not match the shipping robot).

> **Verification provenance.**
> - Parent app: grepped `work/jadx-out/sources` first-hand (2026-08-26).
> - OpenMoxie protocol: read from `github.com/jbeghtol/openmoxie` `main` (file tree + key sources
>   fetched 2026-08-26), esp. `doc/RemoteModuleAPI.md` and `site/hive/mqtt/`.
> - Hardware/OS lockdown: Lantronix Moxie case study, USPTO patents, Mozilla PNI, teardown threads.
> - "*(doc)*" = from prose docs, not code. "*(SPEC)*" = speculative/unverified.

---

## 0. TL;DR / Verdict

- **Stock firmware does NOT stream raw camera frames off-device. Confirmed by absence:** the entire
  captured Moxie protocol has exactly **one** perception stream — **audio STT** — and **zero**
  image/video/frame protos. There is no "camera equivalent" of the audio path.
- **What the robot WILL emit off-device is on-device *vision events*** — `eb-found-face`,
  `eb-lost-target`, plus QR / ArUco / book-recognition events — as **plain event strings with no
  bounding boxes, no coordinates, no pixels.** Binary/semantic only.
- **Getting real frames requires defeating a deliberately locked-down Android device** (Secure Boot
  + Android Verified Boot + "secure external interfaces" — Lantronix, confirmed). ADB, sideloading a
  custom APK, and root are all **blocked or unverified-at-best**. A hardware camera-tap works but is
  teardown-invasive.
- **Most promising NON-invasive path today:** consume the **face/QR/ArUco vision events over
  MQTT** (they ride the same `/devices/{id}/...` topics OpenMoxie already brokers) and drive
  Moxie's *reactions* from those — but understand this gives you **"a face is present / gone,"
  not a picture.** A true OpenCV/VLM "Moxie sees the room" experience needs an **external camera**
  (add-on cam near Moxie, or ADB/custom-app if the device can be opened up), not the stock sensor.

---

## 1. What the camera does on stock Moxie (CONFIRMED on-device perception)

The robot runs its **vision on-device**. The cloud never receives pixels; it receives *events* when
the on-device stack recognizes something. Source: `openmoxie/doc/RemoteModuleAPI.md` — the API a
conversation "module" uses. Vision results are delivered to a module **as if they were speech
input**: instead of a transcript, the module receives a special event string.

### 1.1 Face search (the head-tracking / "look at me" system) — CONFIRMED *(doc)*

- **`eb_start_binned_face_search`** — "start searching for any face." Typically triggered by content
  like *"Moxie, look at me."* Kicks the on-device face stack into targeting mode.
- **`eb_custom_face_search`** — **EXPERIMENTAL**. "start searching for a custom-sized face to
  target." Params: `(min_width, min_height, unused_float, unused_bool, unused_bool)`. Only
  `min_width`/`min_height` function; both are **floats as a proportion of the image view**
  (e.g. `["0.15","0","0","true","true"]` = only fire when a face is ≥15% of frame width, i.e.
  "someone is close enough"). This is the closest thing to a size/proximity signal.
- Events emitted:
  - **`eb-found-face`** — a face matching the search params was found.
  - **`eb-lost-target`** (a.k.a. `eb-lost-face`) — the target face left view for an extended period.

**Granularity — the critical limitation:** these events carry **found/lost only**. **No bounding
box, no (x,y) position, no distance, no face embedding/identity is delivered to the module/cloud.**
The only tunable is the *threshold* (`eb_custom_face_search` min size) that decides *whether* to
fire. The robot's actual head-aiming (it physically turns to center a face) happens **entirely
on-device** and its per-frame target coordinates are **not** exposed in the module API. *(SPEC:
lower-level head-pose/gaze telemetry may exist in state/telemetry topics, but it is not in the
captured protocol and I could not confirm it.)*

### 1.2 Marker / object recognition — CONFIRMED *(doc)*

Also delivered as event strings with a single semantic payload variable (no image):
- **`eb-qr-event`** — a QR code was read; string in `input_vars['$eb_qr_value']`. (This is the same
  on-robot QR reader that scans **QR #1 pairing** and **QR #2 endpoint** — see maps 00 & 12.)
- **`eb-dr-event`** — an **ArUco** fiducial marker was detected; id in `$eb_dr_value`.
- **`eb-br-event`** — a **Moxie book** was visually recognized; name in `$eb_br_value`.

So the stock vision stack = **face detect/track + QR + ArUco + book/image recognition**, all
on-device, all surfaced as low-bandwidth semantic events.

### 1.3 Parent app is NOT involved in robot vision — CONFIRMED (first-hand grep)

`com.embo.embodied.parent` (v2.2.2) contains **no** robot-camera code. Its only "camera" content is
the **ZXing / journeyapps barcode scanner** used *on the phone* (`com/journeyapps/barcodescanner/*`,
`com/google/zxing/*`) — unrelated to the robot's sensor. Grep for
`eb_start_binned_face_search`, `RemoteModuleAPI`, `perception`, `zmqSTT`,
`embodied.perception`, `face_search` in the decompiled sources returns **zero** hits. The robot's
vision lives in **robot firmware**, not this app. (The 2,647 "face" hits are `interface`/`surface`/
`preference`; the "vision" hits are `com.google.zxing` and `DeviceProperties`.)

---

## 2. Does the robot expose raw camera frames off-device? — NO (CONFIRMED by absence)

The audio path is the proof-of-shape. Moxie tunnels **high-bandwidth binary over ZMQ-over-MQTT**:

- **Transport (CONFIRMED, read from `moxie_server.py` / `zmq_stt_handler.py`):**
  - Payloads are wrapped as `b"<protoname>:<binary_protobuf>"` (split on the first `:`).
  - Robot → server rides the device **events** topics the server subscribes to
    (`/devices/+/events/#`, `/devices/+/state`); server → robot rides
    **`/devices/{device_id}/commands/zmq`**.
  - The one perception request is **`embodied.perception.audio.zmqSTTRequest`**:
    `audio_content` (raw PCM **16-bit / 16 kHz**), `uuid`, `timestamp`, `vad`
    (`VADState.END_OF_SPEECH`). Reply `zmqSTTResponse`: `uuid`, `type` (`FINAL`), timestamps,
    `speech` (transcript), error fields.

- **The proto set (CONFIRMED, full tree fetched).** Everything under
  `site/hive/mqtt/protos/embodied/` is:
  - `perception/audio/zmqSTT_pb2.py`  ← **the only perception stream**
  - `logging/{Cloud2,Log,enums}_pb2.py`
  - `wifiapp/QRCommands_pb2.py`
  - **There is no `perception/video`, `perception/image`, `perception/camera`, `.../frame`, or any
    face/vision proto.** No `zmqImageRequest`, no snapshot command, nothing.

**Conclusion:** the transport *could* carry camera frames (it already carries 16 kHz audio the same
way), but **stock firmware defines no message to make the robot send an image or video.** The robot
keeps pixels on-device by design and emits only the semantic events of §1. **If it's not in the
firmware, no amount of server work conjures a frame stream** — the server can only *receive* what the
robot chooses to publish.

---

## 3. Paths to get camera frames into OpenCV/VLM — honest feasibility

### 3a. Via MQTT/ZMQ perception events (NON-INVASIVE) — WORKS, but it's events, not frames

- **Feasible today, zero teardown.** Your OpenMoxie-style broker already sees
  `/devices/+/events/#`. You can subscribe to the §1 vision events and react: on `eb-found-face`
  start/continue engagement, on `eb-lost-target` wind down, branch on `$eb_qr_value` /
  `$eb_dr_value` / `$eb_br_value`. You can also *drive* searches via `eb_start_binned_face_search` /
  `eb_custom_face_search` (min-size threshold) inside `remote_chat` responses.
- **What you do NOT get:** pixels, bounding boxes, positions, identities. **OpenCV/YOLO/VLM cannot
  run on this** — there is nothing to run them *on*. This path powers *presence-aware behavior*
  ("someone's here / close / gone; they showed me a QR/book"), **not** scene understanding.
- **Verdict:** the best non-invasive lever, but it is a **semantic event bus, not a camera feed.**

### 3b. Via ADB (Android) — BLOCKED / unverified, and non-trivial even if a port exists

- Moxie **is Android** and was hardened by **Lantronix**: **Secure Boot**, **Android Verified Boot**
  (kernel + filesystems cryptographically authenticated), **"secure external interfaces, ensuring no
  ports are susceptible to backdoor intrusion,"** and **signed secure updates** (all CONFIRMED,
  Lantronix case study).
- Implication: **ADB is expected to be disabled** on production units (or the USB port not exposing
  an ADB interface), and there is **no public report of an ADB shell on a retail Moxie.** Even *if*
  a debug ADB were reachable on some unit:
  - As **non-root, unprivileged shell** you cannot open the camera HAL directly (camera access is
    permission- + SELinux-gated to system/privileged apps; there's no `adb`-level `/dev/video*`
    grab on modern Android). `screenrecord`/`screencap` capture **the face-display UI, not the
    camera** — useless for vision. `dumpsys media.camera` at most enumerates devices.
  - The only sanctioned camera capture is via an app holding `CAMERA` permission (→ see 3c).
- **Blockers:** locked bootloader + AVB + SELinux + no confirmed ADB. **Verdict: not a reliable
  path; treat as blocked pending hardware-level proof one specific unit exposes ADB.**

### 3c. Custom on-device APK (sideload a "camera-publisher" app) — BLOCKED by design

- The *ideal* solution: a small APK that opens the camera (Camera2/CameraX), JPEG/H.264-encodes
  frames, and publishes them over the **same ZMQ-over-MQTT rail** the audio path proves works — then
  your broker feeds OpenCV/VLM. Architecturally clean.
- **Why it's blocked (CONFIRMED lockdown):**
  - **Android Verified Boot / dm-verity**: the system partition is integrity-checked; you can't add a
    privileged/system app, and modifying partitions breaks the verified-boot chain.
  - **No confirmed ADB** ⇒ no `adb install` vector to sideload even a normal user APK.
  - **App/OTA signing**: updates are signed; you can't slip an app in via OTA.
  - A user-space `CAMERA`-permission app would also need to be *installed and granted* — and on a
    kiosk-locked child's device there's no launcher/consent flow to do so.
- **Verdict: not achievable without first defeating verified boot / gaining root** (bootloader
  unlock, an exploit, or an unlocked/engineering unit). No such method is publicly documented for
  Moxie. This is the highest-value path **if** someone ever roots the device; today it's blocked.

### 3d. Hardware tap (last resort, DE-EMPHASIZED — we avoid teardown)

- Options, all invasive: (i) intercept the **camera module's ribbon/CSI cable** with a splitter/CSI
  bridge into a Pi/FPGA capture; (ii) desolder/replace the camera with one wired to external capture;
  (iii) place a **separate external camera** next to Moxie aimed at the same scene — technically not a
  "tap" and **not invasive** (see §6).
- CSI interception is fiddly (MIPI-CSI is high-speed differential; not a casual solder job) and voids
  any repair we want to preserve. **Noted for completeness; not recommended.**

---

## 4. If we get frames: the local vision pipeline (recommended stack)

*(Applies to any real frame source: external camera per §6, or ADB/custom-app if the device is ever
opened.)* Target box: a home **GPU PC** or **Jetson Orin (AGX/NX)** — the same appliance map 12
already proposes for local STT/LLM/TTS.

- **Capture / preprocess — OpenCV.** `cv2.VideoCapture` (USB/CSI/RTSP). Resize, color-convert, drop
  to a working FPS, motion-gate (only wake the heavy models when the scene changes).
- **Real-time detector — a YOLO-class model.** Ultralytics **YOLOv8/YOLO11n/s** (or **RT-DETR**) on
  GPU/TensorRT for persons/objects/pose at 30–60 FPS. Cheap enough to run continuously; produces the
  bounding boxes the stock robot never gives you. Optional face ID via **InsightFace** if you want
  "recognize the child."
- **Scene understanding — a local VLM behind an OpenAI-compatible endpoint.** Run **Qwen2.5-VL-7B**,
  **LLaVA-1.6**, **MiniCPM-V**, or **Moondream** via **Ollama / vLLM / llama.cpp** exposing
  `/v1/chat/completions` with image input. Call it **event-driven, not per-frame** (on a YOLO
  trigger, or when Moxie is asked "what do you see?") to keep it affordable.
- **Feedback into Moxie's conversation.** Your supervisor (the map-12 service) already forms
  `remote_chat` responses. Wire the vision layer in as a **tool/context provider**: VLM caption +
  YOLO labels → injected into the LLM prompt → Moxie speaks about what "it" sees ("I see you brought a
  red book!"). Presence gating from §3a face events decides *when* it's worth looking.
- **Latency / bandwidth realities.**
  - YOLO: single-digit-to-~30 ms/frame on a decent GPU/Orin → real-time.
  - VLM: **hundreds of ms to a few seconds** for a 7B model on one image → strictly an occasional,
    triggered "describe the scene" call, never in the tight conversational loop.
  - Frame transport (if it ever comes off the robot): 720p JPEG ≈ 50–150 KB/frame; even 5–10 FPS is
    trivial on LAN — bandwidth is **not** the constraint. The constraint is **getting the frames at
    all** (§2/§3), then **VLM inference time**.
  - Keep the audio conversational loop (map 12) independent of the vision loop so a slow VLM never
    stalls speech.

---

## 5. Camera hardware: resolution / format / FPS / location

- **Location — CONFIRMED (multiple sources):** a single **HD camera in the head, top/front** (above
  the display, "forehead" area), used for **face recognition, real-time face tracking**, and marker
  scanning. (Vendor/press: "an HD camera is on top of the head for face recognition"; Embodied
  marketing: "real-time visual tracking.")
- **Resolution / format — SPECULATIVE:** described only as **"HD"** (implies ~720p, possibly 1080p);
  no official pixel spec, sensor model, or FPS was published, and I found no reliable teardown
  BOM naming the module. The Lantronix case study confirms a custom **auto-exposure library** for
  dim-light adaptation, implying a standard rolling-shutter RGB sensor, but not its model.
- **Stereo? — SPECULATIVE / likely NO on retail:** a USPTO Embodied patent describes **stereo pairs**
  of optical sensors (baseline 5–15 cm, ~10 cm) with wide HFOV and a mic array between the upper
  pair. **Patent language ≠ shipping hardware**; community/press consistently describe a **single**
  head camera. Treat "stereo depth" as unconfirmed for the retail unit.
- **SoC — UNCONFIRMED:** runs Android (AVB), but no source names the SoC (Qualcomm/MediaTek/Rockchip
  all plausible for an Android AVB kiosk device). Not needed for the non-invasive plan; would matter
  only for a future root/ADB effort.
- *(To close these gaps, the authoritative untapped sources are the **FCC internal photos** under
  FCC-ID **2AV9NEMBODIEDMOXIEA** and the **YouTube Moxie teardown** videos — not yet mined here.)*

---

## 6. Verdict — feasibility table

| Path | What you get | Invasive? | Feasible today? | Blocker |
|---|---|---|---|---|
| **A. MQTT vision events** (`eb-found-face`, `eb-lost-target`, QR/ArUco/book) | Presence/proximity + marker semantics. **No pixels, no bbox.** | **No** | **YES** | None — already on the event bus |
| **B. Drive face search** (`eb_start_binned_face_search`, `eb_custom_face_search` size-gate) | Control *when* face events fire | **No** | **YES** | Only min-size tunable exposed |
| **C. Stock camera frames over ZMQ/MQTT** | Raw frames for OpenCV/VLM | No | **NO** | **No such proto exists** in firmware (§2) |
| **D. ADB pull of camera** | Frames | No (cable) | **NO (unconfirmed)** | No confirmed ADB; SELinux/HAL gating; screenrecord ≠ camera |
| **E. Custom on-device APK publishing frames** | Frames over existing rail — *ideal* | No (if installable) | **NO** | Verified Boot + signing + no sideload vector |
| **F. Root / bootloader unlock → then E** | Frames | No (software) | **NO (no known method)** | Secure Boot + AVB; no public Moxie root |
| **G. Hardware CSI tap** | Raw frames | **YES (teardown)** | Possible | Invasive; MIPI-CSI difficulty; we avoid |
| **H. External add-on camera beside Moxie** | Full frames for OpenCV/VLM | **No** (nothing opened) | **YES** | Not *Moxie's* eye; needs its own cam + fusion |

**Bottom line.**
- **Non-invasively today:** you can make Moxie **presence- and marker-aware** via its vision *events*
  (A/B) — but you **cannot** get its camera pixels into OpenCV/VLM. The stock robot simply does not
  emit them (§2, confirmed by the proto set).
- **For a real "Moxie sees the room" VLM experience without teardown:** pair the event bus (A/B) with
  an **external camera** (H) feeding the §4 stack, and let the fused result drive `remote_chat`. This
  is the pragmatic, honest recommendation.
- **Needs ADB:** nothing reliably — ADB itself is unconfirmed/blocked (D).
- **Needs a custom app:** the clean "publish frames over the existing ZMQ rail" solution (E) — gated
  behind **root/verified-boot defeat (F)**, which has **no public method** for Moxie.
- **Blocked:** stock frame streaming (C), custom APK (E), root (F) — all by the deliberate
  Secure-Boot / Android-Verified-Boot / secure-interfaces lockdown Lantronix built.

---

### Sources
- OpenMoxie `doc/RemoteModuleAPI.md`; `site/hive/mqtt/` (`moxie_server.py`, `zmq_stt_handler.py`,
  `moxie_zmq_handler.py`, `protos/embodied/…`) — github.com/jbeghtol/openmoxie (main, 2026-08-26).
- Decompiled parent app: `work/jadx-out/sources` (first-hand grep, 2026-08-26).
- Lantronix Moxie case study (Secure Boot, Android Verified Boot, secure interfaces, auto-exposure).
- USPTO patent US-11433546 (stereo optical-sensor layout); Mozilla *Privacy Not Included* (Moxie).
- Press/spec: Fast Company, TechCrunch, reviews (HD head camera, real-time tracking).
- Untapped (recommended next): FCC-ID 2AV9NEMBODIEDMOXIEA internal photos; YouTube Moxie teardowns.
