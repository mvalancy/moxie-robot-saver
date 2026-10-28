> [HTML view](https://moxie.mattvalancy.com/setup.html) · [Markdown index](https://moxie.mattvalancy.com/llms.txt). Generated from the same published source. Interactive controls and 3D scenes are available in the HTML view.

Moxie Setuprevive your robot [← Hub](https://moxie.mattvalancy.com/)

# Bring your Moxie back online

Two codes, held up to Moxie's camera, re-home it from the shut-down cloud to a server you control. This page builds both codes on your phone — nothing to install, nothing sent anywhere. Works on firmware `24.10.803`.

Step 1 · Wi-Fi

## Get Moxie onto Wi-Fi

Network name (SSID) Wi-Fi password (blank = open network)

Band

Hidden SSID

Put Moxie on its pairing screen, then hold this up to its camera. It joins the network, then waits for the **second** code below.

Open / WPA2 / hidden work. **WPA3-only, enterprise (802.1X) and captive-portal** networks don't — use a normal WPA2 network or a phone hotspot.

Step 2 · Server

## Point Moxie at your server

Where should Moxie phone home?

After Moxie is on Wi-Fi it waits for this second, different-looking code, which tells it **which server to phone home to** .

**Wait — is OPEN\_MOXIE the dead Embodied cloud?** No. That cloud is a different value ( EMBODIED\_PRODUCTION ) and it's gone. Both of these two are the **revival** endpoints, baked into the shipped 803 firmware precisely so a stock robot can leave the dead cloud:

-   **OPEN\_MOXIE** — the **open-source community** endpoint (added for the [OpenMoxie](https://github.com/jbeghtol/openmoxie)\-style revival). “Open” as in open‑source, not open‑to‑the‑world.
-   **EMBODIED\_LOCAL** — a **local-network** endpoint (resolves to client-service-api.local ).

Neither one magically knows _your_ machine — the robot resolves the name to a host, so you point that host at your server (real domain + DNS, or a LAN name). Then it connects to the backend you run below.

Needs your MQTT broker reachable with a valid TLS cert (the robot checks the CA store, with no pinning — a real domain + Let's Encrypt works). Stand up the backend: **docker compose -f sim/docker-compose.yml up** .

Created by [Matthew Valancy](https://mattvalancy.com/) · [Source on GitHub](https://github.com/mvalancy/moxie-robot-saver)

[← All experiences](https://moxie.mattvalancy.com/) · [Simulator](https://moxie.mattvalancy.com/sim.html) · [Cloud console](https://moxie.mattvalancy.com/cloud.html) · [Docs](https://moxie.mattvalancy.com/docs.html)

Unofficial fan project — not affiliated with Embodied, Inc. These codes are byte-identical to the [moxie-toolkit](https://github.com/mvalancy/moxie-robot-saver) CLI.
