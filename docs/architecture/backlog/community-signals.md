# 📡 Community signals — what owners actually hit, ranked by evidence

> **Scan 1 · run 2026-09-03.** The [OpenMoxie feature audit](../openmoxie-feature-audit.md) ranks what to
> build by reading *our own* code and specs. Four of its ten items shipped on 2026-09-02, and every one of
> them came from that inward read. This page is the other direction: **what people holding a real Moxie
> say is broken**, cited to a public URL and a date, weighted by how many independent reports there are,
> and turned into either a build, a verification, or an honest "we cannot settle this without a robot."
>
> **The ranking rule is evidence, not appetite.** A single unconfirmed report that would change an
> architectural decision outranks five upvoted wishes for a feature nobody is blocked on. Two of the eight
> findings below are single-source and say so in their own row.
>
> **Privacy rule.** Every quotation is technical content only. No names, no locations, no handles beyond
> what a URL already contains. Where a report is a person describing their child's robot, only the
> failure is reproduced.

---

## 1. What was searched, and when

Run on **2026-09-03**. Venues, method, and what came back empty — stated so the next scan can tell
*coverage* from *silence*.

| Venue | Method | Result |
|---|---|---|
| `jbeghtol/openmoxie` **issues** | `gh api`, **all 60 issues, open *and* closed**, bodies + every comment on the 11 with owner traffic | The richest source. Six findings below come from here; **two comments postdate the audit's evidence base** (2026-08-29) |
| `jbeghtol/openmoxie` **discussions** | `gh api graphql`, all 11 discussions, bodies + comments | One finding the audit does not carry (**C1**), from a comment dated 2026-02-21 |
| `Noonster77/openmoxie` **issues** | `gh api`, state=all | **Empty — no issue surface at all** |
| `vapors/openmoxie-ollama` **issues** | `gh api`, state=all | **Empty — no issue surface at all** |
| `robotsaroundthehouse.com` | forum index for the Moxie board + 6 threads fetched in full | Two findings the audit does not carry (**C2**, **C5**'s reflash half) |
| **r/MoxieRobot** | `www.reddit.com` and `old.reddit.com` direct fetch; 4 search-engine queries | ❌ **Could not read it.** See §4 — this is the scan's largest gap |
| `moxierobot.com/pages/closing-faqs` | direct fetch | ❌ **HTTP 403** |
| New revival projects | web + GitHub search for any Moxie server project outside the three we track | **Nothing new** since the 2026-08 landscape snapshot |
| Firmware newer than `24.10.803` | forum version-history thread + press | **None exists**, as far as any public source shows — see **C2** |

**Two forks have no issue tracker traffic at all.** Every owner report in this ecosystem lands in exactly
two places: upstream's tracker and `robotsaroundthehouse.com`. That is worth knowing before the next scan
goes looking anywhere else.

---

## 2. Findings, ranked by evidence strength

### C1 — Our empty `license_values: []` may be the reason a real robot never gets its voice up

**Rank 1 · two independent reports, five months apart, plus our own recovered enum · ⛔ needs a physical robot**

**What they hit.** A comment on upstream's markup discussion, **2026-02-21**, asks:

> *"Is there any way to get Moxie past the CereProc license check? `cloud_tts` settings get delivered but
> the robot still crashes at CereProc init before `cloud_tts` can kick in."*
> — [jbeghtol/openmoxie discussion #35](https://github.com/jbeghtol/openmoxie/discussions/35), comment
> 2026-02-21, no replies.

That is the **second** report of the same subsystem. The first is already in the audit:
[issue #60](https://github.com/jbeghtol/openmoxie/issues/60) (2026-05-19, firmware **24.10.801**) — dropping
the `license` answer entirely crash-loops `bo-wifi.apk`, and the reporter's workaround is that a
*syntactically valid but fake* credential suffices "because the robot only checks that a `query_result`
carrying a `license_values` **entry** comes back."

**Why this is more than two anecdotes: our own corpus names the subsystem.**

- [`Cloud.proto`](../../reverse-engineering/protocol/recovered-proto/embodied/logging/Cloud.proto):311 —
  `enum LicenseID { LICENSE_UNKNOWN = 0; cereproc = 1; google_speech = 2; }`
- [`cloud-protocol.md`](../../reverse-engineering/protocol/cloud-protocol.md):226 describes the `license`
  query's answer as *"the **TTS/STT license blobs** the robot needs to run CereVoice / Google Speech."*
- [`wire.py`](../../../mqtt/moxie_sdk/wire.py):116 maps `"license" → ("license_values", [])`. We answer,
  honestly, with **nothing in it**.

So the robot asks the cloud for a CereProc licence, our own protocol doc says that is what the field is
for, and two owners five months apart report the on-robot CereVoice path failing.

**What it means for us — a new question, not a contradicted decision.** The audit's §4.4 already carries
*"does an empty `license_values: []` satisfy the robot or crash-loop its Wi-Fi App?"* as blocked. This
report adds a **second, sharper** question underneath it: *does the robot's on-device CereVoice engine
initialise at all without a licence record* — and if it does not, the failure lands **before** anything
cloud-side runs. Our entire hosted-voice design (`build_cloud_tts_response` in
[`tts.py`](../../../mqtt/moxie_sdk/tts.py):369 hands the robot **finished PCM** in an
`AudioBuffer{buffer, channels, sample_rate}`) plausibly bypasses CereVoice entirely — but the 2026-02-21
reporter's specific claim is that the crash happens *before* `cloud_tts` takes effect, which is exactly
the ordering that would break that assumption. **Stated as a risk, not a conclusion: we do not know, and
neither does anyone posting.**

**What we would build or verify.** Nothing to build. Two things owed:
1. **Record the risk where the code is** — `wire.py`'s docstring and `cloud-protocol.md` should say the
   empty answer is a *known-risk* choice with two field reports against it, not merely an honest one.
2. **An owner decision, not an agent's.** Whether to emit a shaped-but-empty `LicenseRecord{id: cereproc}`
   instead of an empty list is a change that hands a robot a fabricated credential. The audit already
   refuses to slip that into a merge, and this finding does not change that — it raises the price of
   *not* deciding.

**Settling it needs a physical robot on 801 or 803.** No test of ours reaches it.

---

### C2 — Moxie died twice. Our landscape doc only knows about the first one

**Rank 2 · multiple independent sources, one of them a dated announcement · 📄 documentation + positioning**

**What happened.** On **2025-12-06** the founder of FamPay acquired the rights to Moxie from Embodied and
relaunched it as **Moxie Robots, Inc.**, reactivating robots through a new app and a paid subscription
([robotsaroundthehouse thread 1432](https://robotsaroundthehouse.com/threads/fampay-founder-acquires-moxie-from-embodied-relaunches-under-moxie-robots-inc.1432/),
2025-12-08). On **2026-05-31** that company announced its servers would close on **2026-06-30**, citing
*"hardware limitations"*
([robotsaroundthehouse thread 1565](https://robotsaroundthehouse.com/threads/second-sunset-for-moxie-a-reminder-why-subscription-based-robots-eventually-fail.1565/),
2026-05-31, 6 replies). Owners in that thread describe paying a monthly subscription in the **$29–$40**
range and being left, in their words, with *"a brick."*

Upstream's own tracker shows the hope and then the silence: on **2025-09-02** the maintainer told an owner
*"there are teasers of a company relaunch, so you might be able to get an update in the future without
resorting to manual flashing"*
([discussion #55](https://github.com/jbeghtol/openmoxie/discussions/55)). That window opened in December
2025 and closed in June 2026.

**What it means for us.** Three things, in descending order of usefulness:

1. **[`community-research.md`](../../community-research.md) is stale at the top.** It is dated 2026-08 and
   describes a single 2024 shutdown with Embodied's services "DEAD". It does not mention the acquisition,
   the relaunch, the subscription, or the second closure. A reader currently gets the wrong shape of the
   world from our own landing doc.
2. **A checkable risk, checked, and it came back clean.** If the relaunched service pushed firmware, our
   entire `v24.10.803`-stamped corpus would be stale for that cohort. **It did not, as far as any public
   source shows:** the forum's version-history thread
   ([thread 255](https://robotsaroundthehouse.com/threads/moxie-software-updates-thread.255/)) ends at
   **24.10.803 (2025-01-01)**, and no source I reached names a later build. Worth saying out loud, because
   it was a real risk and the answer is load-bearing for every RE page.
3. **It is the strongest available evidence for our own thesis, and we should stop making the argument
   from first principles.** [`vision.md`](../vision.md) argues that a cloud-dependent appliance is a
   borrowed appliance. Moxie has now proven that twice, under two different owners, in eighteen months.
   That is a citation, not an opinion — and a cohort of owners lost service in **June 2026** and is looking
   for an off-ramp now.

**What we would build.** Nothing. Update `community-research.md`'s landscape section and let `vision.md`
cite the second sunset. **No robot needed.**

---

### C3 — The appliance's address is baked into the robot at pairing, and DHCP moves it

**Rank 3 · four independent reports, three venues, eleven months · 🟢 half already solved, half a real gap**

**What they hit**, in order:

| Date | Source | The report |
|---|---|---|
| 2025-02-17 | [issue #41](https://github.com/jbeghtol/openmoxie/issues/41) | Maintainer: *"This is typically the part people struggle with… many routers, like mine, offer local DNS service, so computer names work, but it seems far more don't and so people have had to put in the LAN IP address into the hostname field."* |
| 2025-04-05 | [discussion #51](https://github.com/jbeghtol/openmoxie/discussions/51) | Maintainer, on a robot that scans and stalls: *"If you see that and still can't connect, it's most often the hostname needs to be the right LAN IP."* |
| 2025-11-14 | [thread 827](https://robotsaroundthehouse.com/threads/setting-up-openmoxie-for-your-moxie-robot-a-detailed-step-by-step-guide.827/) | An owner's connection dies; cause is that the host computer's IP changed. Fix: edit *External host*, re-show the Migration QR |
| 2026-01-09 | same forum board, a **dedicated how-to thread** | *"Moxie not connecting to Open Moxie? How to update your computer's IP address"* — the pain got its own permanent thread |

**What it means for us — be precise about which half is ours.**

- **The first-run half we already solved.** `server/moxie_server/main.py`:470 returns
  `"default_host": _lan_ip()` on the endpoint-QR route: our console offers a **detected LAN IP**, not a
  hostname the robot cannot resolve. The single most common first-run failure in this ecosystem is a
  failure our stack does not have. **That is a documentation and marketing fact, not an engineering
  one** — nobody knows it because we have never said it.
- **The after-the-fact half is a genuine gap on no list of ours.** A robot repointed at `192.168.1.9` and
  later handed a different lease is silently dead, and **nothing in our stack notices or says so.** The
  robot cannot be told a new address over MQTT — it has to be shown a new QR — so the appliance is the only
  component that can detect the drift.

**What we would build.** Small, and testable with no hardware because the detection is pure:
1. Persist the address that went into the last issued endpoint QR; compare it to the current `_lan_ip()`
   on every console load and on supervisor start. On a mismatch, say so in one sentence and offer a
   one-click regenerated QR.
2. Name a **DHCP reservation** as a step, not a footnote, in
   [`revive-your-moxie.md`](../../guides/revive-your-moxie.md) and
   [`first-time-setup.md`](../../guides/first-time-setup.md).

**No robot needed to build or to test.** This is the highest-evidence *engineering* finding in the scan.

---

### C4 — "Crossed ears" is the most-reported visible failure, and one variant has a diagnosed cause

**Rank 4 · four independent reports over 18 months, one with a root-cause diagnosis · 🟡 one verification + one small build**

**What they hit.**

| Date | Source | The report |
|---|---|---|
| 2025-01-30 | [#26](https://github.com/jbeghtol/openmoxie/issues/26) | *"there are crossed out ear icons on each side of Moxie's screen… Moxie is not receiving an audio response from us."* Self-resolved after a sleep/wake |
| 2025-02-25 | [#44](https://github.com/jbeghtol/openmoxie/issues/44) | *"showed the 'not hearing' icon on her ears… can't receive voice input"* |
| 2026-05-03 | [#59](https://github.com/jbeghtol/openmoxie/pull/59) | **Diagnosed.** Mosquitto 2.x no longer publishes connect/disconnect notices to `$SYS/broker/log/N` by default; upstream relied on them to detect reconnection and re-send the ZMQ STT subscribe, so *"the subscribe is never re-sent after a sleep/wake cycle, and Moxie's audio data goes unprocessed."* Fix: re-send on every event from a known device, because *"the subscribe is idempotent"* |
| 2026-07-10 | [#62](https://github.com/jbeghtol/openmoxie/issues/62) | A **different**, robot-side cause (`BoVision` / `RecognizeFace`), already in the audit's §2.4 |

**What it means for us.** Two separate things, and conflating them would be the mistake:

1. **The root cause is upstream-specific; the behavioural requirement is not.** Scraping `$SYS` is
   upstream's design, and a repo-wide search for `SYS/broker` finds only broker config and ACL files —
   no supervisor hit. But the *fact the report establishes about the robot* is ours too: **the ZMQ STT
   subscribe must be re-sent after a sleep/wake, not only on first connect.** That is a real-hardware fact
   we could not have derived from our own code, and it is adjacent to — but not the same as — the
   reconnection work that shipped as §4.4 #3. **Owed: a check that our runtime re-subscribes on wake, and
   a named test for it.** *Not verified in this run:* `mqtt/supervisor/moxie_runtime.py` was reserved by
   another agent, so this row states the requirement and leaves the check owed rather than guessing.
2. **A recovery affordance we can ship almost for free.** The maintainer's fast unstick, from #44
   (2025-02-26): *"putting it into Puppet Mode, then taking it out of puppet mode — that restarts a number
   of things but not Unity so it recovers in seconds instead of minutes."* We **already shipped**
   puppet/telehealth ([`telehealth.md`](telehealth.md), PR #43), so the mechanism exists; what is missing
   is a console control that names the symptom the owner sees ("Moxie can't hear me") and performs the
   round-trip. An **S**.

**Building it needs no robot. Proving it works does.**

---

### C5 — The pre-801 door has closed, and our own doc still points at it

**Rank 5 · a dated correction from the one person who could give it, plus independent confirmation of our own hardware finding · ⛔ robot-bound by definition**

**What changed, on 2026-08-29** — two maintainer comments on
[issue #57](https://github.com/jbeghtol/openmoxie/issues/57), both after the audit's evidence base:

> *"there is no means to update older units over the air except from the owner of the domain, who I
> believe shut down their servers. So the only means left to recover an old Moxie is to flash it
> yourself."*

> *"open moxie can be used to provide an OTA image, but i was asked by the property owner not to
> distribute it."*

**What it means for us.** [`live-hardware-debug.md`](../../debugging/live-hardware-debug.md) currently
records the recovery path for a sub-801 unit as *"jbeghtol's 801→803 OTA (issue #57, needs the bot already
on 801) or a paid reflash service."* **The first half is now withdrawn** — the image will not be
distributed, and the offer in that thread from 2025-09-29 no longer stands. Our doc points at a closed
door, and it is our own robot that is behind it.

**Independent confirmation of our own finding, which is worth more than the correction.** In the same
thread an owner with a secondhand unit reports: *"On scanning the Migration QR… it shows the magnifying
glass, returns to the scan screen, and the broker never sees a single connection attempt from it — zero
TCP, ever,"* and reasons that an 801 unit rejecting a self-signed cert would still log a TLS failure, so
total silence means pre-801. The maintainer agreed. **That is byte-for-byte the symptom our own
`live-hardware-debug.md` records on our own robot** — so "zero TCP ⇒ pre-801" now has **n = 2** and a
maintainer's concurrence, where before it had one unit and one inference.

**A named alternative exists.**
[Thread 1205](https://robotsaroundthehouse.com/threads/upgrade-moxie-service.1205/) (opened 2025-07-22, still
live) advertises a reflash service with a ~2-day turnaround that migrates a robot to OpenMoxie
compatibility *"even if the robot wasn't previously unpaired"*, with an optional battery upgrade (six
2500 mAh cells → 3000 mAh, roughly 1.5 h → 3 h of runtime in puppet mode — itself a useful hardware fact
for [`telehealth.md`](telehealth.md), which has never had a session-length ceiling written down).

**Why this ranks here rather than lower.** The audit says, at every refresh: *"A real Moxie on our broker
for an hour would settle more of this page than a week of building."* Six of the production-hardening
brief's assumptions, all four of broker-auth's A1–A4, every OTA claim, C1 above and the `license_values`
question are **all** blocked on the same missing thing. This finding is the closest anyone has come to
naming a price for unblocking them. **It is an owner's decision, not an agent's** — recorded here so it is
a decision that gets made rather than one that stays implicit.

**What we would do.** Correct `live-hardware-debug.md`'s recovery line with a date. Nothing else, without
the owner.

---

### C6 — There are impostors now, and from the outside we look like one

**Rank 6 · a warning added by upstream itself, mirrored on the forum · 📄 documentation + positioning, with a safety edge**

**What happened.** Upstream's README now opens with a banner, added around **2026-01-15**
([issue #58](https://github.com/jbeghtol/openmoxie/issues/58), *"Added warning about alt openmoxie"*):

> *"⚠️ OFFICIAL SOURCE WARNING: This is the original and official repository for OpenMoxie. Please be aware
> that openmoxie.org and 'OpenMoxie 2.0' are unaffiliated third-party projects. We cannot verify the safety
> or functionality of code downloaded from those sources."*

The forum carries a matching thread, *"OpenMoxie Website Warning"* (2026-01-16), on its Moxie board.

**What it means for us — this one is uncomfortable and should be.** A parent looking for a way to save
their child's robot is now navigating a field that contains unverifiable lookalikes. **We are, from the
outside, shaped exactly like one:** a third-party site under a personal domain, Moxie-branded, offering
software for a robot whose vendor is gone, published by someone the community has never heard of. Nothing
about our intent is visible from the landing page.

**What we would build.** Nothing in the runtime; a paragraph on the public site, above the fold, that says
plainly what we are and are not — **not** affiliated with Embodied or Moxie Robots, Inc.; **not** OpenMoxie
and not a successor to it; no account, no payment, no data leaves the house; source in the open; and a
link to [OpenMoxie](https://github.com/jbeghtol/openmoxie) as the fastest way for an owner to get a robot
talking tonight, which the audit already says in its credit section and the site does not.

**No robot needed.** Touches the static site and `README.md`, nothing else.

---

### C7 — Setup failures cluster into three environmental causes, two of them pre-emptable

**Rank 7 · several reports, but each individual cause is thinly sourced · 🟠 low engineering value, real support value**

| Cause | Evidence | Ours to fix? |
|---|---|---|
| **Wi-Fi band split** — server and robot on the same SSID but different bands, so they never see each other | [thread 852](https://robotsaroundthehouse.com/threads/moxie-needs-help-comatose-state.852/) (2025-02-13/14): *"the same router name but on different frequencies (2.4GHz and 5GHz)"*; resolved by changing the SSID. Echoed on the "constantly losing Wi-Fi" thread | Partly — a **pre-flight line** in our guides, and our own `live-hardware-debug.md` already recommends a dedicated 2.4 GHz AP for exactly this reason |
| **Windows Docker Desktop** | [#54](https://github.com/jbeghtol/openmoxie/issues/54) (2025-08-27) — a Docker Engine `500` on the named pipe, three days lost; [#28](https://github.com/jbeghtol/openmoxie/issues/28) (2025-02-01) — an outdated `docker-compose` binary; the forum guide adds a Docker firewall "private network" step | Not ours to fix, but our [`one-command-stack.md`](../../guides/one-command-stack.md) can name the two known traps |
| **Charge state** | [thread 244](https://robotsaroundthehouse.com/threads/moxie-won%E2%80%99t-connect-to-wi-fi.244/) — a robot refusing the Wi-Fi QR with "error code 2" worked after five minutes on power | One anecdote. Worth one line, not a feature |

**One report each on the second and third rows.** Listed together because the *cluster* is well-evidenced
even where each cause is not, and because the fix for all three is the same artefact: a short pre-flight
in the setup guide. **No robot needed.**

---

### C8 — A first-run expectation nobody has written down: the first boot is slow

**Rank 8 · single source, but the best-placed single source there is · 📄 one line in a guide**

The upstream maintainer, on a robot apparently stuck on the boot animation
([#43](https://github.com/jbeghtol/openmoxie/issues/43), 2025-02-24):

> *"the 'spinning e'… is a transition animation used generally before the main unity application that
> drives the faces is ready. The first boot into openmoxie is long — I think 10 minutes is not unheard of
> to stay in this state the first time, latter boots are more in the 5 min range."*

**What it means for us.** An owner who power-cycles at minute three concludes the software is broken and
starts changing things — which is how #43 and several forum threads read. **Our revival guide sets no
expectation at all.** One sentence in [`revive-your-moxie.md`](../../guides/revive-your-moxie.md) prevents
a class of self-inflicted failure. Single-source, and the source is the person who built the thing.

*Also from #43, and worth recording as corroboration rather than as news:* an owner's log at 2025-08-03
shows `Fatal signal 6 (SIGABRT)` in `servicelauncher` followed by *"Wifi App dead. Restarting."*, with the
maintainer replying that ServiceLauncher *"shouldn't crash really. Ever."* That is a **second, independent**
sighting of the Wi-Fi-App restart loop that [#60](https://github.com/jbeghtol/openmoxie/issues/60) attributes
to the dropped `license` answer — but this owner had no such trigger, so it corroborates the **symptom
class**, not #60's cause. Recorded here so a future scan does not double-count it as evidence for **C1**.

---

### C9 — Tell a pre-801 robot from a broken server by *looking at its screen*

**Rank 5= · scan 2, 2026-09-04 · maintainer-stated, in the same thread as C5 · 📄 a triage rule, no code**

**What it is.** Deeper in [issue #57](https://github.com/jbeghtol/openmoxie/issues/57) the maintainer
gives a positive identification for firmware generation that needs **no broker, no logs and no network at
all**: *both* 24.10.801 and 24.10.803 render the string **`OpenMoxie`** on the robot's QR-scan screen, so
**its absence means the unit is pre-801** — for which, per C5, there is no OTA path and only a flash.

**Why this is worth a section of its own rather than a line under C5.** C5's rule is a *negative* one —
"the broker never sees a TCP connection" — and it is diagnostic only once you own a broker, have pointed
the robot at it, and have ruled out your own misconfiguration. A first-time owner cannot tell *"this robot
is too old"* from *"I set the server up wrong"*, and those two produce identical evidence: silence. This
rule separates them **before** anything is set up, by reading a word off a screen. The two together are a
proper triage pair:

| What you see | What it means |
|---|---|
| No `OpenMoxie` string on the QR-scan screen | **Pre-801.** No OTA path exists. Stop; flashing is the only route (C5) |
| `OpenMoxie` present, magnifying glass, back to the scan screen, **zero TCP at the broker** | An 801/803 unit that never reached you — network, DNS or QR payload |
| `OpenMoxie` present, TCP connects, **TLS fails** | An 801/803 unit that reached you and rejected the certificate |

**Evidence weight.** Maintainer-stated, in a closed thread, corroborated by the reporter's own observation
in the same exchange. That puts it above [#60](https://github.com/jbeghtol/openmoxie/issues/60)'s lone
anecdote and beside C5's `n = 2`.

**What we would do.** One row in [`revive-your-moxie.md`](../../guides/revive-your-moxie.md)'s
troubleshooting and one in [`live-hardware-debug.md`](../../debugging/live-hardware-debug.md), beside the
zero-TCP line that is already there. **No ranked item moves** — nobody has to build anything, which is
exactly why it is filed here rather than on the audit's §4.4.

**Honest limit:** we have not seen this screen. Our own unit's behaviour is recorded in
`live-hardware-debug.md` and the *string* is not among what was recorded, so this is upstream's
observation carried faithfully, not ours confirmed.

---

## 3. Where the community's problem is one we have already solved

The brief asked for this to be said plainly, because it is a **marketing and documentation** finding
rather than an engineering one — and every row here is a thing we built that nobody outside this repo
knows exists.

| What the community asked for | Where | What we already have |
|---|---|---|
| *"I would like to see OpenMoxie use locally hosted AI and speech services"* — answered by the maintainer with *"any local compute cloud should really be a separate project that can be integrated with openmoxie"* | [discussion #23](https://github.com/jbeghtol/openmoxie/discussions/23), 2025-01-23 | **That project is this one.** [`ai-seam.md`](../ai-seam.md) + local Piper/whisper as first-class engines. Both forks then built it independently — the strongest possible confirmation the seam was right |
| A DeepSeek integration; alternative TTS voices | [#40](https://github.com/jbeghtol/openmoxie/issues/40) 2025-02-17, [#38](https://github.com/jbeghtol/openmoxie/issues/38) 2025-02-11 | One config value each. [`voice-picker.md`](voice-picker.md) makes the voice half a dropdown |
| Eye and face colour customisation | [discussion #21](https://github.com/jbeghtol/openmoxie/discussions/21), 2025-01-20 | Shipped — face catalog + customizer (PR #36/#47) |
| *"how would you remove moxie from a OpenMoxie install… to join a different instance?"* — upstream has **no** unpairing logic; the hack is to write `"pairing_status": "unpairing"` into the robot config to return it to the QR screen, then back to `"paired"`. The maintainer: *"would be better as a toggle button somewhere"* | [discussion #20](https://github.com/jbeghtol/openmoxie/discussions/20) 2025-01-20 and [#27](https://github.com/jbeghtol/openmoxie/discussions/27) 2025-02-01 — **two independent asks** | ⚠️ **Half.** We carry `pairing_status` in [`cloud_config.py`](../../../mqtt/moxie_sdk/cloud_config.py) and in [`config-and-telemetry-contract.md`](../config-and-telemetry-contract.md), so the *mechanism* is there and upstream's is not. The **toggle upstream wished for is not built.** This is the one row here that is a real, small, build-ready gap |
| *"can you tell if your moxie has the build required for open moxie?"* — the badge test | [discussion #51](https://github.com/jbeghtol/openmoxie/discussions/51) 2025-04-05, [#43](https://github.com/jbeghtol/openmoxie/issues/43) 2025-04-18 | Already ported into [`live-hardware-debug.md`](../../debugging/live-hardware-debug.md), credited to that comment |
| The LAN-IP-not-hostname trap | **C3** above | Already the default: `main.py`:470 offers a detected `_lan_ip()` |

---

## 4. Verified nothing-new, and honest gaps

**Verified nothing-new** — stated so a thin result reads as a measurement rather than a missing section:

- **No new revival project.** Searched for any Moxie server project outside the three the audit tracks;
  found only `nhertanto/Embodied-Moxie` (content reference) and `andrsvlz/openmoxie-espanol`, both already
  in [`community-research.md`](../../community-research.md). Nothing has appeared since the 2026-08 snapshot.
- **No firmware past `24.10.803`**, including through the entire Moxie Robots, Inc. period. Our corpus
  stamp holds (**C2**).
- **Neither fork has an issue tracker with anything in it.** Both returned empty for `state=all`.
- **Scan 2, 2026-09-04 — nothing new anywhere on GitHub.** Upstream, both forks and the whole **40-fork**
  network were re-read at their live heads; **zero forks pushed after 2026-09-03**. The newest activity of
  any kind in upstream's tracker — issue, PR or comment — is **2026-08-29**, which is *before* scan 1, and
  the newest discussion update is #35 on 2026-02-23. **C9 above is the only thing this scan added, and it
  came from re-reading a thread scan 1 had already cited rather than from anything new being posted.**
  The venue gaps below are unchanged and unattempted.

**Gaps, named:**

- ❌ **r/MoxieRobot could not be read.** `www.reddit.com` and `old.reddit.com` both refuse this
  environment's fetcher, and four search-engine queries returned no subreddit content. It is named as the
  **de-facto hub** by both the upstream maintainer and our own `community-research.md`. **Everything above
  therefore over-weights GitHub and one forum**, and the ranking should be re-read once someone with a
  browser has looked. Specifically worth hunting there: owners stranded by the **June 2026** closure,
  outcomes from the reflash services, and anyone who has watched the CereProc licence path actually work.
- ❌ **`moxierobot.com/pages/closing-faqs` returned HTTP 403.** What Moxie Robots, Inc. told owners on the
  way out — refunds, data, any offline mode — is unread.
- ⏭️ The Facebook group named in `community-research.md` was not attempted; it is login-walled.
- ⚠️ **"Independent reports" counts posts, not people.** Where this page says two reports are independent,
  it means two threads with different authors as far as a public URL shows. It cannot mean more than that.
- ⚠️ **C4's verification is owed, not done.** `mqtt/supervisor/moxie_runtime.py` was reserved by another
  agent during this run, so whether our runtime re-subscribes STT after a robot sleep/wake is **unchecked**
  rather than confirmed either way.

---

## 5. What this changes on the audit's ranking

Nothing is re-ranked here — [§4.4](../openmoxie-feature-audit.md#44-the-open-backlog-re-ranked-2026-09-05)
remains the one place to look for *"what should I build next."* What this scan hands it:

| Finding | Where it lands |
|---|---|
| **C1** | Strengthens §4.4's blocked `license_values` row from *one unconfirmed report* to **two independent reports plus our own recovered enum**, and adds a second question under it (CereVoice init, not just the pull) |
| **C3** | A **new build-ready item** on no list of ours: IP-drift detection + a regenerated QR. Small |
| **C4** | A **verification** owed on the runtime (STT re-subscribe on wake) and a small console control (the puppet-mode unstick) |
| **C2, C5, C6, C8** | Documentation corrections with dates — `community-research.md`, `live-hardware-debug.md`, `revive-your-moxie.md`, the public site |
| **§3's `pairing_status` row** | A small build-ready gap, wished for by upstream and half-owned by us already |

---
📖 [Backlog index](README.md) · [OpenMoxie feature audit](../openmoxie-feature-audit.md) · [Community landscape](../../community-research.md) · [Live hardware debugging](../../debugging/live-hardware-debug.md) · [Vision](../vision.md)
