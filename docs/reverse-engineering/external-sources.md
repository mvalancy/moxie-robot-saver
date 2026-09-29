# 🌐 External & community research map

Other people's work on Moxie — FCC filings, teardowns, community revival projects, press — with the facts
each source contributes, cross-checked against this repo's own analysis of firmware **v3.6.4-Zephyr /
OTA v24.10.803** (which wins where they disagree). Key takeaways: the SoC is a **Rockchip RK3288**, not
the often-assumed Qualcomm part; the FCC photos independently confirm the board map
([fcc-teardown](hardware/fcc-teardown.md)); OpenMoxie cross-validates our protocol with zero diffs; and
nobody has published a root/UART/flashing map for the RK3288 board, so that tier is original work here.

## Contents
- [Provenance & the law](#provenance-the-law-can-we-use-this) — FCC public records, copyright, fair use
- [SoC adjudication](#soc-adjudication-rk3288-vs-the-qualcomm-assumption) — RK3288 vs "Open-Q"
- [Regulatory / official](#regulatory-official-sources) — FCC filings, Lantronix
- [Teardowns](#teardowns) · [Community projects](#community-revival-projects) · [Press](#press-context)
- [What's resolved vs still open](#what-external-work-resolves-vs-what-still-needs-our-bench)

---

## Provenance & the law (can we use this?)

Three legal layers, treated differently:

1. **Facts are free.** Copyright protects expression, not facts or ideas (U.S. law, *Feist v. Rural
   Telephone*: facts and "sweat of the brow" compilations are not copyrightable). "The board is an
   RK3288", "the Wi-Fi module is a BCM4339", "the battery is behind the lower shell" are facts; we record
   them freely and cite where we learned them. This is the bulk of the value.
2. **The media usually stays copyrighted.** FCC photo JPEGs, test-report PDFs, teardown videos, forum
   photos and prose carry their authors' copyright (test lab, Embodied, videographer, poster). Public
   accessibility is not public domain: FCC publication makes records accessible without waiving rights.
   So we **link and cite media; we do not re-host it**. A single low-resolution thumbnail for
   identification/commentary is a fair-use *argument* (17 U.S.C. §107), not a guarantee, so we default to linking.
3. **Some exhibits are withheld.** An applicant may request confidentiality under 47 CFR §0.457/§0.459.
   Short-term confidentiality (typically 180 days) often hides internal photos, test-setup photos and the
   user manual until launch; permanent confidentiality is routinely granted for **schematics, block
   diagrams and operational descriptions**. Never assume a filing contains a schematic.

**FCC records.** Equipment-authorization exhibits are U.S. federal public records, published through the
FCC **Equipment Authorization Search (EAS)** once the grant issues. [`fccid.io`](https://fccid.io) and
[`fcc.report`](https://fcc.report) are third-party mirrors (EAS is the primary source). Reading and
downloading them for analysis is fine; **redistributing the files** is the copyright question above.

> **Policy:** cite every source; record *facts* with attribution; do not mirror copyrighted media; never
> assume a confidential exhibit exists. *(The practical shape of the law as it applies here, not legal advice.)*

### Self-sufficiency doctrine — assume every link dies tomorrow

The repo must be able to bring a Moxie back to life with **zero external links reachable**. A URL is
provenance, not content:

- **Distill the substance into this repo, in our own words.** A teardown video's value is the facts it
  shows (board, connectors, battery location); transcribe those here. The link only lets a reader verify.
- **Facts and our descriptions are ours to keep** — facts aren't copyrightable, and our written
  description is our own expression.
- **A bare link is an unfinished job.** Any entry whose substance still lives only on the far side of a
  link is a TODO to extract into detailed, cited `.md`.

---

## SoC adjudication: RK3288 vs the "Qualcomm" assumption

Moxie is often described as a "Qualcomm-based (Lantronix/Intrinsyc Open-Q class)" device. That is wrong,
and it is the most common Moxie hardware mis-statement.

- **Where the assumption comes from.** Moxie's OS engineering was done by **Intrinsyc**, acquired by
  **Lantronix** in 2020 ([Lantronix case study](https://www.lantronix.com/resources/case-studies/moxie/)).
  Intrinsyc's best-known product line, **Open-Q**, is Snapdragon-based, so "Intrinsyc ⇒ Open-Q ⇒
  Qualcomm" is a natural but unsupported inference. Intrinsyc/Lantronix also does custom boards and
  OS/security services on non-Qualcomm silicon, and the case study names **no SoC** — only services.
- **The authoritative fact.** U-Boot, the kernel cmdline, the device tree (`rk3288-robot-gen1p5`), the
  Rockchip vendor HALs and the Rockchip boot/flash chain all identify a **Rockchip RK3288** (ARMv7
  Cortex-A17, Android 9). See [`firmware-803-reference.md`](firmware/firmware-803-reference.md),
  [`device-tree.md`](hardware/device-tree.md), [`firmware-image.md`](firmware/firmware-image.md).
- **Independent public confirmation.** The FCC internal photos locate the SoC BGA at the centre of the
  compute board with DDR3 around it, consistent with RK3288; the part marking is hidden under thermal
  compound, so the firmware evidence remains authoritative ([fcc-teardown](hardware/fcc-teardown.md)).
- **What the Lantronix case study does confirm.** Its three deliverables map exactly onto our RE: Secure
  Boot + AVB → [`firmware-image.md`](firmware/firmware-image.md) (AVB 1.1, verity enforcing, ATX
  attestation); a camera auto-exposure library → the OV2710 imaging path in
  [`perception-pipeline.md`](runtime/perception-pipeline.md). It corroborates the security model, not the silicon.

---

## Regulatory / official sources

### FCC ID `2AV9N-EMBODIEDMOXIEA` (grantee Embodied, Inc.)
The original Moxie's filing ([fccid.io](https://fccid.io/2AV9NEMBODIEDMOXIEA)); grantee code **`2AV9N`**
= Embodied, Inc. A public user/quick-start guide PDF is visible
([fcc.report mirror](https://fcc.report/FCC-ID/2AV9NEMBODIEDMOXIEA/4808018.pdf)). The internal/external
photos and RF test report of both revisions have been analysed and their facts extracted into
**[`fcc-teardown.md`](hardware/fcc-teardown.md)**: full chip inventory, the `LOAD`/`RESET`/`POWER`
buttons, the `STM32F071VBT6` MCU + `ISP & DEBUG` SWD/UART header, XMOS `VSM02C`, motor connectors, the
5 GHz U-NII-1 grant (corroborating the BCM4339 / AmPak AP6335 module), and a per-chip programmer/IDE map,
all cited to exhibit pages. (The mirrors block automated fetch behind Cloudflare; exhibits were pulled
manually and analysed locally.)

### FCC ID `2AV9N-EMBMOXIEVTWO` (Moxie V2)
A second hardware revision ([fccid.io](https://fccid.io/2AV9NEMBMOXIEVTWO)). It matters because the
firmware already shows a generation split (pre-801 Google IoT vs 801/803) and teardowns report older
units lacking touch sensors. The rev1-vs-rev2 comparison is in [`fcc-teardown.md`](hardware/fcc-teardown.md).

### Lantronix "Moxie" case study
[lantronix.com/resources/case-studies/moxie](https://www.lantronix.com/resources/case-studies/moxie/).
Lantronix Engineering Services delivered **Secure Boot** ("only authorized initial software can run"),
**Android Verified Boot** ("kernel and filesystems authenticated cryptographically"), and a **camera
auto-exposure library** ("adaptively adjust to scene changes"). Names no SoC/module — see the
[SoC adjudication](#soc-adjudication-rk3288-vs-the-qualcomm-assumption).

## Teardowns
*(Media — we describe observations and link; we do not re-host footage.)*

- **"Moxie Robot Teardown"** — YouTube [`aRK9Al7RGtc`](https://www.youtube.com/watch?v=aRK9Al7RGtc).
  The shell coming off; the compute board; the **projector beaming onto the fresnel-lens faceplate** (the
  [DLP face](hardware/hardware-map.md) / [DLPC3430 in the DTB](hardware/device-tree.md)); arm/torso motion
  with the shell removed (the DOF in [`hardware-map.md`](hardware/hardware-map.md)).
- **"Moxie Teardown (contd) & Battery Replacement"** — YouTube [`tQyRjc678rk`](https://www.youtube.com/watch?v=tQyRjc678rk).
  The lower body/base and battery; the **battery is hard to reach**. Informs the teardown sequencing in
  [`hardware-access.md`](hardware/hardware-access.md).
- **robotsaroundthehouse "Moxie tear down"** — [forum thread](https://robotsaroundthehouse.com/threads/moxie-tear-down.419/).
  Discussion of the videos; the notable fact is that **older Moxie lack touch sensors**, independently
  corroborating the hardware-generation variance seen in firmware.

No text source states SoC UART pad or maskrom test-point locations; the video frames are the remaining
public place to look (a frame-by-frame pass, cited by timestamp, is still to do).

## Community revival projects

- **`jbeghtol/openmoxie`** — [GitHub](https://github.com/jbeghtol/openmoxie). The de-facto community
  **robot-facing server**: a local MQTT hub (Dockerized, self-signed-cert broker) that a re-homed robot
  connects to after a QR endpoint change. Our [`cloud-protocol.md`](protocol/cloud-protocol.md),
  [`network-trust.md`](protocol/network-trust.md) and the 120 recovered `.proto` files were
  **cross-validated against OpenMoxie with zero diffs**. Its server surface is captured in
  [`cloud-protocol.md`](protocol/cloud-protocol.md): the MQTT topic set (`/devices/+/events/#`,
  `/devices/+/state`, `$SYS/broker/clients/#` + `$SYS/broker/log/#` for presence, and
  `/devices/{id}/config|commands/{cmd}|commands/zmq`) and its RS256-JWT / `RS256.key` device-auth flow.
  Source files: `site/hive/mqtt/moxie_server.py`, `robot_credentials.py`, `site/openmoxie/urls.py`. Our
  [`mqtt/`](../../mqtt/) + [`server/`](../../server/) aim to match and extend it.
- **`nhertanto/Embodied-Moxie`** — [GitHub](https://github.com/nhertanto/Embodied-Moxie). **ChatScript +
  Jinja2** files from a former Embodied contributor, used in-house to author activities — a rare primary
  source for the content-authoring format. Captured in
  [`content-and-conversation.md`](runtime/content-and-conversation.md#chatscript-authoring-the-real-format-pipeline):
  the three-layer pipeline (Python node classes → Jinja2 templates → generated `.top`), the ChatScript
  `.top` syntax/operators, and the named global-command set (ChatScript runs server-side, so this is what
  a revival server authors).
- **robotsaroundthehouse OpenMoxie setup guide** — [thread](https://robotsaroundthehouse.com/threads/setting-up-openmoxie-for-your-moxie-robot-a-detailed-step-by-step-guide.827/).
  An owner walkthrough of the QR re-home + OpenMoxie stand-up — the real-world instance of the
  [FIELD-GUIDE](FIELD-GUIDE.md) ① path.

> **Name collision:** `atgreen/moxie-cores`, `moxiedev-*` and similar are the GNU/GCC **"moxie" CPU
> architecture** (a toy binutils/GCC target), unrelated to the robot.

## Press / context
- **PIRG** — "How open source kept (some) AI companion robots online" ([pirg.org](https://pirg.org/articles/moxie-robot-open-source/))
- **Fight to Repair** — the shutdown / right-to-repair framing ([substack](https://fighttorepair.substack.com/p/end-of-emotional-support-800-smart))
- **Mozilla *Privacy Not Included*** — Moxie's data/security profile ([mozillafoundation.org](https://www.mozillafoundation.org/en/privacynotincluded/moxie-robot/))
- **TechCrunch (2020)** — launch context; built by iRobot's former CTO ([techcrunch.com](https://techcrunch.com/2020/05/04/moxie-is-a-technically-impressive-childhood-robot-from-irobots-former-cto))

---

## What external work resolves vs what still needs our bench

| Open item ([exploration map](EXPLORATION-MAP.md#open-items-need-a-bench-unit-or-an-external-artifact)) | External help? | Where it points |
|---|---|---|
| SoC / Wi-Fi module independent confirmation | ✅ partly — FCC photos locate both; markings hidden (thermal compound / shield) | [fcc-teardown](hardware/fcc-teardown.md); firmware stays authoritative |
| Cross-generation hardware diff | ✅ rev1 vs rev2 FCC filings compared | [fcc-teardown](hardware/fcc-teardown.md) |
| SoC UART pad map · maskrom test point | ❌ not in FCC photos or text sources (the `RX`/`TX` found are the MCU's) | teardown video frames, else our own teardown |
| USB-port external reachability | ❌ unaddressed anywhere (FCC photos are of stripped boards) | our bench |
| Macro-button → mode + ADC thresholds | ❌ unaddressed | our bench (serial console) |
| Genuine signed 803 `update.zip` | ⏳ possible community path | via the OpenMoxie author (unverified) |

---
📖 [Reverse-engineering index](README.md) · [Field guide](FIELD-GUIDE.md) · [Exploration map](EXPLORATION-MAP.md) · [Hardware access](hardware/hardware-access.md) · [Docs index](../README.md)
