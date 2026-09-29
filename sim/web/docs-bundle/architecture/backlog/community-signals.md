# Community signals — what owners of real robots report

**Status:** research record (scans of 2026-09-03 and 2026-09-04). Most follow-ups are still open; see
the table below. C1 was folded into the [OpenMoxie feature audit](../openmoxie-feature-audit.md).

The feature audit ranks work by reading our own code. This page goes the other way: what people holding
a real Moxie say is broken, cited to public sources, weighted by how many independent reports there are,
and turned into a build, a verification, or "needs a physical robot". Quotations are technical content
only — no names or handles beyond what a URL already contains.

## 1. Where owner reports live

Every owner report in this ecosystem lands in two places: upstream's tracker
([jbeghtol/openmoxie](https://github.com/jbeghtol/openmoxie) issues and discussions) and the Moxie board
on [robotsaroundthehouse.com](https://robotsaroundthehouse.com/forums/moxie-by-moxie-robots.50/). Both
forks (`Noonster77/openmoxie`, `vapors/openmoxie-ollama`) have empty issue trackers. No new revival
project and no firmware newer than **24.10.803** was found, including through the Moxie Robots, Inc.
period — so our firmware corpus is not stale.

## 2. Findings

| # | Finding | Evidence | What it asks of us | Status (verified 2026-09-28) |
|---|---|---|---|---|
| C1 | Our empty `license_values: []` answer may stop a real robot's voice: two reports of CereProc licence failures, one saying the crash happens **before** `cloud_tts` applies | issue #60 (2026-05-19); discussion #35 comment (2026-02-21); our `Cloud.proto` `LicenseID { cereproc, google_speech }` | Record it as a known risk; emitting a shaped-but-fake `LicenseRecord` is an **owner decision** | Carried in the feature audit's blocked row. [`wire.py`](../../../mqtt/moxie_sdk/wire.py) still answers `[]` with no risk note. Needs a robot on 801/803 |
| C2 | Moxie died **twice**: relaunched by Moxie Robots, Inc. (2025-12-06, paid subscription), whose servers closed 2026-06-30 | forum threads 1432 and 1565 | Update `community-research.md`; let [`vision.md`](../vision.md) cite it | **Open** — neither doc mentions the relaunch or second closure |
| C3 | The appliance address is baked in at pairing and DHCP moves it (4 reports over 11 months) | issue #41, discussion #51, forum thread 827, a dedicated forum how-to | First-run: offer a LAN IP, not a hostname. After the fact: detect drift and offer a regenerated QR; recommend a DHCP reservation | First-run half **done** (`default_host: lan_ip()` in [`routes/pairing.py`](../../../server/moxie_server/routes/pairing.py)). Drift detection and the guide step **open** |
| C4 | "Crossed ears" (robot not hearing) after sleep/wake; upstream's cause was Mosquitto 2.x no longer publishing `$SYS` connect notices, so the STT subscribe was never re-sent | issues #26, #44; PR #59 (diagnosis) | Verify our runtime re-establishes STT after a robot sleep/wake; add a console "Moxie can't hear me" control that does the puppet-mode round-trip (the maintainer's fast unstick) | **Open.** No test covers wake-after-sleep STT; no such console control |
| C5 | The pre-801 OTA path is closed: the maintainer will not distribute the 801→803 image (2026-08-29). "Zero TCP at the broker ⇒ pre-801" now has n = 2 | issue #57 | Correct the recovery line in [`live-hardware-debug.md`](../../debugging/live-hardware-debug.md); a paid reflash service (forum thread 1205) is the remaining route — an owner call | **Open** — the doc still offers the 801→803 OTA |
| C6 | Impostor projects exist and upstream warns about them; from outside we look like one | issue #58 (~2026-01-15), forum thread 1473 | Above the fold: not affiliated with Embodied or Moxie Robots, Inc.; not OpenMoxie; no account, no payment, nothing leaves the house; link to OpenMoxie | **Partial** — site pages carry "not affiliated with Embodied, Inc."; the fuller statement is not there |
| C7 | Setup failures cluster in three environmental causes: Wi-Fi band split (same SSID on 2.4/5 GHz), Windows Docker Desktop traps, low charge | forum 852, 244; issues #54, #28 | A short pre-flight in the setup guides | **Open** (first-time-setup says to use 2.4 GHz; nothing else) |
| C8 | The first boot into OpenMoxie is slow — up to ~10 min on the "spinning e", ~5 min after | issue #43 (maintainer) | One sentence in [`revive-your-moxie.md`](../../guides/revive-your-moxie.md) | **Open** |
| C9 | Triage by screen: 801 and 803 both show the word **OpenMoxie** on the QR-scan screen, so its absence means pre-801 | issue #57 (maintainer) | A troubleshooting row in the revive guide and `live-hardware-debug.md` | **Open.** We have not seen this screen ourselves |

C9 and C5 together make a triage table for a robot that never connects:

| What you see | What it means |
|---|---|
| No `OpenMoxie` on the QR-scan screen | Pre-801. No OTA path; flashing is the only route |
| `OpenMoxie` shown, back to scan screen, **zero TCP** at the broker | 801/803 that never reached you — network, DNS or QR payload |
| `OpenMoxie` shown, TCP connects, **TLS fails** | 801/803 that reached you and rejected the certificate |

## 3. Problems we have already solved (and nobody knows)

| Community ask | What we have |
|---|---|
| Locally hosted AI and speech (discussion #23; the maintainer said it should be a separate project) | This project: [`ai-seam.md`](../ai-seam.md), local Piper/whisper as first-class engines |
| Alternative TTS voices, other LLMs (issues #38, #40) | [`voice-picker.md`](voice-picker.md), [`brain-picker.md`](brain-picker.md) |
| Eye and face colour customisation (discussion #21) | Face catalog and customizer |
| Unpairing a robot to join another server (discussions #20, #27; upstream has no unpair logic) | **Half:** `UNPAIRED_PAIRING_STATUS` / `build_unpaired_cloud_config()` in [`cloud_config.py`](../../../mqtt/moxie_sdk/cloud_config.py) are used for unpermitted robots, but there is no console unpair toggle |
| "Is my robot's build new enough?" (discussion #51) | Ported into `live-hardware-debug.md`, credited |
| The LAN-IP-not-hostname trap | C3's first-run half |

The corroborating `servicelauncher` SIGABRT / "Wifi App dead" log in issue #43 matches the **symptom
class** of #60 but had no licence trigger; do not count it as evidence for C1.

## 4. Gaps

r/MoxieRobot, named as the de-facto hub by upstream and by
[`community-research.md`](../../community-research.md), refuses this environment's fetcher and search
(a Reddit crawler policy, not a transient error), so everything here over-weights GitHub and one forum.
`moxierobot.com/pages/closing-faqs` returned HTTP 403. The Facebook group is login-walled. "Independent
reports" means different authors as far as a public URL shows.

---
📖 [Backlog index](README.md) · [OpenMoxie feature audit](../openmoxie-feature-audit.md) · [Community landscape](../../community-research.md) · [Live hardware debugging](../../debugging/live-hardware-debug.md) · [Vision](../vision.md)
