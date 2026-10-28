> [HTML view](https://moxie.mattvalancy.com/sim.html) · [Markdown index](https://moxie.mattvalancy.com/llms.txt). Generated from the same published source. Interactive controls and 3D scenes are available in the HTML view.

## MOXIE · SIL needs JavaScript

This page is a live WebGL simulation of the Moxie robot: the 3‑D model, her voice and the comms log are all rendered in your browser, so with scripting turned off there is genuinely nothing here to show.

These parts of the project do not need it:

-   [The landing hub](https://moxie.mattvalancy.com/) — plain HTML, and it lists every experience.
-   [The docs in the repository](https://github.com/mvalancy/moxie-robot-saver/tree/main/docs) — the reverse‑engineering write‑ups as Markdown. (The in‑browser [docs explorer](https://moxie.mattvalancy.com/docs.html) needs JavaScript too.)

Fan project. Not affiliated with Embodied, Inc.; “Moxie” is their trademark.

[Hub](https://moxie.mattvalancy.com/ "Back to the hub")

# MOXIE

v24.10.803 SOFTWARE-IN-THE-LOOP

LINK IDLE

FAN PROJECT

**Not affiliated with Embodied, Inc.** “Moxie” is their trademark.

Created by [Matthew Valancy](https://mattvalancy.com/) [GitHub ↗](https://github.com/mvalancy/moxie-robot-saver)

MOXIE · TTS OUT

Motion servos · scene

### Motors 0–32767 · 16384 REST

### Scene lighting · liveness

LIGHT 0.85 liveness (imaginary life — same as the ALIVE button) show axes

Expression face · heart

### Face expression cluster

### Heart LED chest indicator

power

Voice tts · audio

### Phrases tap one · shipped audio

### Audio pre-cached voice · sfx

sound

Tap phrases above play shipped audio (no server). Free text uses your browser's voice, or a local Piper service if you run one.

optional local Piper service

### Mic optional local STT

Link bus · presence · session

### Live bus MQTT/WS :9001

not connected

### Presence robot vision events

no face events yet

Publishes `eb-found-face` / `eb-lost-target` the way the robot delivers a subscribed vision event — as the `speech` of a RemoteChatRequest. The stock camera never leaves the robot: these carry presence, not pixels.

### Session record / replay

load 

### Revive a robot endpoint QR

hold the code up to Moxie's camera

[full setup page →](https://moxie.mattvalancy.com/setup.html) · Wi‑Fi + server, phone‑friendly

[parent cloud console →](https://moxie.mattvalancy.com/cloud.html) · example dashboard

[← all experiences](https://moxie.mattvalancy.com/) · the landing hub

[docs explorer →](https://moxie.mattvalancy.com/docs.html) · RE docs + mermaid

**Talk to Moxie**  
Ask anything, or tap Listen and say hello.

Tap Listen and talk — I'll know when you're done.
