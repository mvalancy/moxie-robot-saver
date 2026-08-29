# 🌐 External & community research map

> **What this is.** A detailed, cited account of *other people's* work on Moxie — regulatory filings,
> teardowns, community revival projects, and press — cross-checked against this repo's own
> reverse-engineering of firmware **v3.6.4-Zephyr / OTA v24.10.803**. Not a link farm: each source is
> described, its facts extracted, and its claims adjudicated against our firmware analysis (which is
> the authoritative source where they disagree).

## Contents
- [Provenance & the law](#provenance--the-law-can-we-use-this) — FCC public records, copyright, fair use
- [SoC adjudication](#soc-adjudication-rk3288-vs-the-qualcomm-assumption) — settling RK3288 vs "Open-Q"
- [Regulatory / official](#regulatory--official-sources) — FCC filings, Lantronix
- [Teardowns](#teardowns) · [Community projects](#community-revival-projects) · [Press](#press--context)
- [What's resolved vs. still open](#what-external-work-resolves-vs-what-still-needs-our-bench)

---

## Provenance & the law (can we use this?)

The recurring question when mapping outside work is *"is this public, and can it go in our repo?"* The
honest answer separates **three legal layers**, and we treat each differently.

**1. Facts are free.** Copyright protects *expression*, not *facts or ideas* (in U.S. law, the
*Feist v. Rural Telephone* principle — facts and a "sweat of the brow" compilation are not
copyrightable). "The board is an RK3288," "the Wi-Fi module is a BCM4339," "the RF test covers 2.4 and
5 GHz," "the battery is behind the lower shell" — these are facts. We can extract and record them
freely, and we cite where we learned them so a reader can verify. **This is the bulk of the value and
the bulk of this document.**

**2. The media itself usually stays copyrighted.** An FCC photo JPEG, a test-report PDF, a YouTube
teardown video, a forum member's photos and prose — the *documents* carry their author's copyright
(the test lab, Embodied, the videographer, the poster). **Public accessibility is not public domain.**
FCC *publication* is a government act that makes the records *accessible*; it does not place the
underlying works in the public domain or waive the author's rights. So we **link and cite media, we do
not re-host it.** A single low-resolution thumbnail used for identification/commentary is a *fair-use
argument* (17 U.S.C. §107 — purpose is transformative/commentary, factual work, small portion), but
it's a defense, not a guarantee, so we default to linking.

**3. Some exhibits are withheld.** An FCC applicant may request **confidentiality** under 47 CFR
§0.457/§0.459. **Short-term confidentiality** (typically 180 days) commonly hides the internal photos,
test setup photos, and user manual until a product ships; **permanent confidentiality** is routinely
granted for **schematics, block diagrams, and operational descriptions**. So a filing may simply *not*
contain a schematic for us to read — we should never assume it does.

**FCC records specifically.** Equipment-authorization exhibits are U.S. federal public records,
published through the FCC **Equipment Authorization Search (EAS)** once the grant issues.
[`fccid.io`](https://fccid.io) and [`fcc.report`](https://fcc.report) are third-party mirrors of that
system (convenient, but the FCC EAS is the primary source of truth). Accessing and reading them — even
downloading for our own analysis — is fine; **redistributing the files** is the copyright question
above.

> **Net policy for this repo:** cite every source; record *facts* with attribution; **do not mirror
> copyrighted media**; never assume a confidential exhibit exists. *(This is the practical shape of the
> law as it applies here, not legal advice.)*

### Self-sufficiency doctrine — assume every link dies tomorrow

**The repo must be able to bring a Moxie back to life with zero external links reachable.** A URL is
*provenance*, not *content*. So for every source below, the rule is:

- **Distill the substance into this repo, in our own words.** A teardown video's value is the *facts*
  it shows (what board, what connector, where the battery sits) — transcribe those into prose here; the
  link is only so a reader can verify. If the video vanishes, we must have lost nothing we needed.
- **Facts + our description are ours to keep.** Facts aren't copyrightable, and our written description
  of what a source shows is our own copyrightable expression — both survive link-death and belong in
  the repo permanently.
- **A bare link is an unfinished job.** Any entry here that is *only* a link, with the substance still
  living on the far side of it, is a TODO — flagged for a future tick to deep-extract into detailed,
  cited `.md`. The end state is a repo that reads as a complete robot-revival manual on its own, with
  citations, not a bibliography that points elsewhere for the real information.

This is why the sections below **describe** each source rather than merely list it — and why the
highest-value remaining work (the FCC internal photos, the teardown frames, the ChatScript authoring
format) is queued for *extraction into the repo*, not just citation.

---

## SoC adjudication: RK3288 vs. the "Qualcomm" assumption

This project's own `hardware/` notes once described Moxie as a *"Qualcomm-based Android device
(Lantronix/Intrinsyc Open-Q class board)."* Mapping the external sources shows **where that came from
and why it's wrong**, which is worth spelling out because it's the single most common Moxie hardware
mis-statement.

- **The origin of the assumption.** Moxie's OS engineering was done by **Intrinsyc**, acquired by
  **Lantronix** in 2020 ([Lantronix case study](https://www.lantronix.com/resources/case-studies/moxie/)).
  Intrinsyc's best-known product line is the **Open-Q** system-on-module family — which is
  **Qualcomm Snapdragon**-based. The inference "Intrinsyc ⇒ Open-Q ⇒ Qualcomm" is natural but
  unsupported: Intrinsyc/Lantronix also does **custom board design and OS/security services on
  non-Qualcomm silicon**, and the case study itself lists their contribution as **Secure Boot, Android
  Verified Boot, and a camera auto-exposure library** — *services*, with **no SoC named**.
- **The authoritative fact.** Our firmware analysis is unambiguous and multiply-sourced: U-Boot,
  kernel cmdline, the device tree (`rk3288-robot-gen1p5`), the Rockchip vendor HALs, and the
  Rockchip-specific boot/flash chain all identify a **Rockchip RK3288** (ARMv7 Cortex-A17, Android 9).
  See [`firmware-803-reference.md`](firmware-803-reference.md), [`device-tree.md`](device-tree.md),
  [`firmware-image.md`](firmware-image.md).
- **Independent public confirmation available.** The **FCC internal photos** (below) show the actual
  production PCB and its markings — the cleanest *public* way to independently confirm the SoC and the
  Wi-Fi module without opening a robot. Extracting those facts (carefully, per the policy above) is the
  highest-value next step this map points to.
- **What Lantronix's case study *does* confirm.** Its three named deliverables map **exactly** onto
  our RE: Secure Boot + AVB → [`firmware-image.md`](firmware-image.md) (AVB 1.1, verity-enforcing,
  ATX attestation); camera auto-exposure → the OV2710 imaging path in
  [`perception-pipeline.md`](perception-pipeline.md). So the case study is a **genuine corroboration of
  the security model**, just not of the silicon.

---

## Regulatory / official sources

### FCC ID `2AV9N-EMBODIEDMOXIEA` (grantee Embodied, Inc.)
The original Moxie's equipment-authorization filing ([fccid.io](https://fccid.io/2AV9NEMBODIEDMOXIEA)).
Grantee code **`2AV9N`** = Embodied, Inc. A **public user/quick-start guide PDF** is already visible
([fcc.report mirror](https://fcc.report/FCC-ID/2AV9NEMBODIEDMOXIEA/4808018.pdf)). The high-value
exhibits for us are the **internal photos** (PCB, SoC/module markings, antenna, connectors — the
independent SoC/Wi-Fi confirmation, and possibly **UART/test-point pads visible on the board**) and
the **RF test report** (confirms bands: 2.4 GHz Wi-Fi/BT and, if present, 5 GHz — a BCM4339 is 1×1
802.11ac, so 5 GHz support in the report would corroborate [`device-tree.md`](device-tree.md)). Block
diagram/schematics may be confidentiality-withheld. **Next tick: extract those facts (facts only, no
re-hosting) into `hardware-map.md`/`device-tree.md` with citation.**

### FCC ID `2AV9N-EMBMOXIEVTWO` (Moxie **V2**)
A **second hardware revision** exists ([fccid.io](https://fccid.io/2AV9NEMBMOXIEVTWO)). This matters
because we already see a **generation split** in the firmware (pre-801 Google-IoT vs 801/803) and in
teardowns (older units lack touch sensors). Comparing the V2 filing's photos/test report against the
original is the public way to characterize **what changed between generations** without owning both
units — directly relevant to the "revive *every* Moxie" goal.

### Lantronix "Moxie" case study
[lantronix.com/resources/case-studies/moxie](https://www.lantronix.com/resources/case-studies/moxie/).
Describes Lantronix Engineering Services' role: **Secure Boot** ("only authorized initial software can
run"), **Android Verified Boot** ("kernel and filesystems authenticated cryptographically"), and a
**camera auto-exposure library** ("adaptively adjust to scene changes"). Names **no SoC/module** — see
the [SoC adjudication](#soc-adjudication-rk3288-vs-the-qualcomm-assumption) above. Corroborates our
security-model RE; does not settle silicon.

## Teardowns
*(Media — we describe observations and link; we do not re-host footage.)*

- **"Moxie Robot Teardown"** — YouTube [`aRK9Al7RGtc`](https://www.youtube.com/watch?v=aRK9Al7RGtc).
  Shows the shell coming off and the **mechatronics**: the compute board, the **projector beaming onto
  the fresnel-lens faceplate** (our [DLP face](hardware-map.md) / [DLPC3430 in the DTB](device-tree.md)),
  and arm/torso motion with the shell removed (the arm/head/base DOF in [`hardware-map.md`](hardware-map.md)).
- **"Moxie Teardown (contd) & Battery Replacement"** — YouTube [`tQyRjc678rk`](https://www.youtube.com/watch?v=tQyRjc678rk).
  Continues into the **lower body/base and battery**, noting the **battery is hard to reach**. Useful
  for the physical-access sequencing in the [`hardware-access.md`](hardware-access.md) teardown checklist.
- **robotsaroundthehouse "Moxie tear down"** — [forum thread](https://robotsaroundthehouse.com/threads/moxie-tear-down.419/).
  Community discussion of the videos; the notable *fact* is that **older Moxie lack touch sensors**,
  which **independently corroborates the hardware generation variance** we infer from firmware.

> **These are the leads most likely to hold a UART-pad / USB-port / maskrom test-point map** — the
> exact [open bench items](COVERAGE.md). No *text* source found so far states pad locations; the
> **video frames** and **FCC internal photos** are where to look. A future tick should do a careful
> frame-by-frame pass and write up any visible connectors/pads (facts) with timestamps as citations.

## Community revival projects

- **`jbeghtol/openmoxie`** — [GitHub](https://github.com/jbeghtol/openmoxie). The de-facto community
  **robot-facing server**: a local MQTT hub (Dockerized, self-signed-cert broker) that a re-homed robot
  connects to after a QR endpoint change. Our [`cloud-protocol.md`](cloud-protocol.md),
  [`network-trust.md`](network-trust.md), and the 120 recovered `.proto` files were **cross-validated
  against OpenMoxie with zero diffs**, which is strong mutual confirmation of the protocol. It's the
  reference our own [`mqtt/`](../../mqtt/) + [`server/`](../../server/) aim to match and extend.
- **`nhertanto/Embodied-Moxie`** — [GitHub](https://github.com/nhertanto/Embodied-Moxie). **ChatScript +
  Jinja2** files from a former Embodied contributor, used in-house to author Moxie **activities/content**.
  This is a rare **primary source for the ChatScript/content-authoring format** — worth a dedicated
  cross-check against [`content-and-conversation.md`](content-and-conversation.md) (we established
  ChatScript runs cloud-side, not on the robot, so this is what a revival *server* would need to speak).
- **robotsaroundthehouse OpenMoxie setup guide** — [thread](https://robotsaroundthehouse.com/threads/setting-up-openmoxie-for-your-moxie-robot-a-detailed-step-by-step-guide.827/).
  A step-by-step owner walkthrough of the QR re-home + OpenMoxie stand-up — the real-world instance of
  our [FIELD-GUIDE](FIELD-GUIDE.md) ① path.

> ⚠️ **Name-collision false positives:** `atgreen/moxie-cores`, `moxiedev-*`, and similar are the
> **GNU/GCC "moxie" *CPU architecture*** (a toy target in binutils/GCC) — **unrelated** to the robot.
> Flagged here so future searches don't waste a tick on them.

## Press / context
Non-technical but useful for history, motivation, and the data/privacy profile:
- **PIRG** — "How open source kept (some) AI companion robots online" ([pirg.org](https://pirg.org/articles/moxie-robot-open-source/))
- **Fight to Repair** — the shutdown / right-to-repair framing ([substack](https://fighttorepair.substack.com/p/end-of-emotional-support-800-smart))
- **Mozilla *Privacy Not Included*** — Moxie's data/security profile ([mozillafoundation.org](https://www.mozillafoundation.org/en/privacynotincluded/moxie-robot/))
- **TechCrunch (2020)** — launch context; built by ex-iRobot CTO ([techcrunch.com](https://techcrunch.com/2020/05/04/moxie-is-a-technically-impressive-childhood-robot-from-irobots-former-cto))

---

## What external work resolves vs. what still needs our bench

| Open bench item ([COVERAGE](COVERAGE.md)) | External help? | Where it points |
|---|---|---|
| SoC / Wi-Fi module *independent* confirmation | ⏳ available publicly | **FCC internal photos + RF test report** (facts-only extraction — next tick) |
| UART pad map · maskrom test-point | ❌ not in text sources | **Teardown video frames / FCC internal photos**, else our own teardown |
| USB-port external reachability | ❌ unaddressed anywhere | **Our bench** |
| Macro-button → mode + ADC thresholds | ❌ unaddressed | **Our bench** (serial console) |
| Genuine signed 803 `update.zip` | ⏳ possible community path | via the OpenMoxie author (unverified) |
| Cross-generation hardware diff | ⏳ available publicly | **FCC `…MOXIEVTWO` vs `…MOXIEA`** photos/reports |

**Bottom line.** The community has solved the **software re-home** (OpenMoxie) and produced **teardown
footage**; nobody has published a **root / UART / flashing** map for the RK3288 board. That invasive
tier remains original work for this project — and the **FCC internal photos** are the highest-value
*public* artifact still to mine (for facts). This document should grow a dedicated, detailed section
(or child `.md`) as each source is deeply extracted.

---
📖 [Field guide](FIELD-GUIDE.md) · [Coverage](COVERAGE.md) · [Hardware access](hardware-access.md) · [Reverse-engineering index](README.md) · [Docs index](../README.md)
