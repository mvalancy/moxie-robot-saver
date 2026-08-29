# 📷 FCC filings — board-level hardware map (rev1 vs rev2)

> **What this is.** Facts extracted from Moxie's **public FCC equipment-authorization exhibits** —
> the independent, outside confirmation of the hardware our firmware analysis
> (**v3.6.4-Zephyr / OTA v24.10.803**) inferred from software. Written per the
> [self-sufficiency doctrine](external-sources.md#self-sufficiency-doctrine--assume-every-link-dies-tomorrow):
> the **facts live here**, so this document stands alone if the filings ever become unreachable.
> Photos are **described, not re-hosted** (see the [provenance policy](external-sources.md#provenance--the-law-can-we-use-this)).
>
> **Sources:** FCC ID **`2AV9N-EMBODIEDMOXIEA`** (rev1) and **`2AV9N-EMBMOXIEVTWO`** (rev2/V2),
> grantee **Embodied, Inc.** (grantee code `2AV9N`), internal/external photo + test-report exhibits.

## The two filings at a glance

| | **rev1 — `EMBODIEDMOXIEA`** | **rev2 — `EMBMOXIEVTWO`** |
|---|---|---|
| Equipment class | Embodied Moxie | Embodied Moxie **V2** |
| Application date | **2020-07-14** | **2023-11-19** |
| Test firm | Intertek Testing Services Hong Kong Ltd. | Intertek Testing Services Hong Kong Ltd. |
| Test report no. | `20031489HKG-002` / `-003` | `HK23051041` |
| Frequency range (grant) | **5180–5240 MHz** (5 GHz U-NII-1) | **5180–5240 MHz** (5 GHz U-NII-1) |
| Application purpose | Original Equipment | Original Equipment |
| Modular equipment | Does not apply (integrated radio) | Does not apply |
| Confidentiality | — | Short-term confidentiality to **2024-05-18**; photos public after |

**Key band fact:** both revisions are granted on **5180–5240 MHz** — the **5 GHz U-NII-1** block
(802.11a/n/ac channels 36–48). This independently corroborates a **dual-band** radio, consistent with
the **BCM4339** (1×1 802.11ac) identified in [`device-tree.md`](device-tree.md) — a 2.4 GHz-only part
could not hold this grant. It also confirms the practical Wi-Fi guidance in
[`qr-commands.md`](qr-commands.md#wi-fi-provisioning-support-what-networks-work): a 5 GHz network works,
but **only the lower U-NII-1 channels are certified** — put the robot on **channels 36–48** (or 2.4 GHz)
rather than upper-band 5 GHz (149+), which this grant does not cover.

## rev1 — the main compute board

Silkscreen **`100438`**, branded **`embodied`**, dated **`EB001+004+01 12-12-2019`** on a sibling board.
What the internal photos show:

- **A large BGA at board centre, covered in white thermal compound** (hence a heat-spreader/heatsink) —
  position, size, and the surrounding DDR3 packages are consistent with the **Rockchip RK3288**
  established from firmware. ⚠️ **Honest limit:** the thermal paste **obscures the part marking**, so
  the photo *corroborates* (BGA + 2× DRAM + eMMC topology) rather than *reads* the SoC number. The
  authoritative RK3288 identification remains the firmware/DTB
  ([`firmware-803-reference.md`](firmware-803-reference.md), [`device-tree.md`](device-tree.md)).
- **Two DDR3 SDRAM packages** flanking the SoC, plus additional memory packages (eMMC/DRAM) to the right.
- **XMOS audio DSP — read directly off the silicon:** a large QFP marked **`XMOS VSM02C` / `GT170802`**
  on the sibling board (`100438` area). This is the **first outside confirmation** of the XMOS DSP that
  [`perception-pipeline.md`](perception-pipeline.md) derived from `xmosdfu`/firmware alone.
- **Wi-Fi:** a **shielded RF module** with a **u.FL/IPEX antenna connector silkscreened `WIFI` (`J3`)**,
  near `U16`/`TP17`. Discrete module + external antenna pigtail. The DTB names it **`ap6335`**, i.e. an
  **AmPak AP6335** (Broadcom BCM4339 inside) — so "AMPAK" community notes are **correct**; the FCC photo
  can't read the shield marking, so it corroborates a discrete BCM4339 module without refuting AMPAK.
- **Silkscreened connectors:** **`LIZARD`** (the MCU board), **`LED`**, **`SPK`**, **`MICS`** (FFC),
  **`PROJECTOR`** (FFC, `J4`), **`CAMERA`**, plus power.

### Other rev1 boards (and the Lizard is in both generations)

rev1's internal photos also include **its own `"THE LIZARD"` board** (silk `EB001+003+02 12-02-2019`)
**[R1‑INT p.30]** — so the STM32-based motion board is present in **both** the 2020 and 2023 hardware,
not a V2 addition. rev1's Lizard silk exposes extra connector labels beyond the motor DOFs:
**`HRT LED`** (the chest/"heart" light), **`SPACE MOUSE`** (a 6‑DOF reference — the IMU/handling
connector; the same `SPACE MOUSE` silk also appears on the mainboard **[R1‑INT p.6]**), **`RIGHT ARM`**,
**`LEFT ARM`**, **`HEAD UP/DN`**, **`BODY L/R`**, **`ON/OFF SWITCH`**, **`16V IN`**. Other pages show
the camera module **[R1‑INT p.9, p.12]**, the DLP projector engine **[R1‑INT p.15]**, sub-boards
**[R1‑INT p.18–39]**, and the **Wi‑Fi antenna** (flex PIFA, silk `2014.07.22 RoHS`) **[R1‑INT p.42]**.

> **Honest limit of the photo pass:** the **RK808 PMIC**, **RT5640 codec**, and **DLPC3430** are not
> marking-legible in any rev1 page (they sit in the dense mainboard area around the paste-covered SoC),
> so they remain **DTB/firmware-sourced** (#7, #9, #10 above) rather than photo-confirmed. The camera
> sensor markings (OV2710/GC2053) are likewise not legible on the module close-ups.

### 🔑 `RESET` · `LOAD` · `POWER` — on-board buttons (major bench finding)

The rev1 mainboard carries **three tactile buttons, silkscreened `RESET`, `LOAD` (S1), and `POWER` (S2)**,
clustered at the board edge beside the `MICS` FFC.

**`LOAD` is the Rockchip download-mode button.** On RK-family boards, a `LOAD`/`RECOVERY` key held at
power-on drives the SoC into **maskrom / rockusb download mode** — exactly the unsigned-flash entry
described in [`hardware-access.md`](hardware-access.md#the-rockchip-boot--download-modes-rk3288) and the
one that bypasses the signed-OTA gate blocking pre-801 revival. This resolves part of a long-standing
open question: **a dedicated download-mode button physically exists on the mainboard** — no test-point
soldering or shorting required *once the shell is open*.

> ⚠️ Two things this does **not** settle, both still bench items: (a) whether `LOAD` is reachable
> **without disassembly** (the FCC photos are of a stripped board, so they say nothing about shell
> access), and (b) whether the external **Macro button** maps to this function via the SARADC key path.
> The `LOAD` button is the *known-good* download-mode entry; the no-open route remains unproven.

## rev2 — "THE LIZARD" MCU board (the best find)

rev2's internal photos include the **motor-control board itself**, silkscreened **`"THE LIZARD"`** with a
lizard illustration, **`#101557`**, `d6b`, `Embodied` — confirming the "Lizard" name our firmware RE
recovered from `liblizardJNI`/`lizzerface` was the **literal board name**, not a codename.

- **MCU read directly off the chip: `STM32F071VBT6`** (ST, LQFP-100, ARM Cortex-M0; marking
  `STM32F071 VBT6 / AA027 98 / TWN AA 105`). This **confirms and sharpens** the "STM32 Cortex-M" in
  [`hardware-map.md`](hardware-map.md) to an exact part — a **STM32F071**, 128 KB flash, which matches
  the Intel-HEX-at-`0x08000000` DFU image in the GOBY bootloader path.
- **🔑 `ISP & DEBUG` header** — a **6-pin header** silkscreened `ISP & DEBUG`, adjacent to a `SWITCH`
  header and an MCU-local **`RESET`** button (`SW1`). Nearby test points are labelled **`SWDIO`,
  `SWCLK`, `NRST`, `3.3V`, `GND`, `RX`, `TX`** (`TP1`, `TP5`, `TP7`, `TP10`, `TP11`, `TP12`, `TP23`).
  That is a complete **SWD debug + UART** surface for the motor MCU: it means the Lizard firmware can be
  **read, debugged, or reflashed directly**, independent of the Android side and independent of the
  GOBY/UART3 DFU path in [`hardware-map.md`](hardware-map.md#lizard-mcu-firmware-update-bootloader-goby).
- **Motor connectors, individually silkscreened and colour-coded** — the authoritative DOF list,
  read straight off the board: **`BODY L/R`**, **`HEAD UP/DN`**, **`L ARM UP/DN`**, **`L ARM IN/OUT`**,
  **`R ARM UP/DN`**, **`R ARM IN/OUT`**, **`BODY F/B`**.
- Other labelled headers: **`BACK`**, **`TUMMY`** (touch zones), **`SWITCH`**, **`I2C/3G`**,
  **`BATTERY`**, **`ON/OFF SWITCH`**, **`12V IN`/`12V OUT`**.
- A second board is silkscreened **`Penguin Board`** (`#101505`, `EB002+003+23C`) — an Embodied
  animal-codename convention alongside "Lizard".

### Cross-check: the motor list vs. our two index spaces

The seven silkscreened motor connectors match the **7-entry `libmotionlib` index** documented in
[`hardware-map.md`](hardware-map.md#native-motion-api-factory-libmotionlib--liblizardjni) — including
its divergence from the `Motor` proto enum:

| Board silkscreen | `libmotionlib` idx | Notes |
|---|--:|---|
| `L ARM UP/DN` | 0 (`laud`) | matches proto `L_ARM_UP_DN`=0 |
| `L ARM IN/OUT` | 1 (`laio`) | matches proto =1 |
| `R ARM UP/DN` | 2 (`raud`) | matches proto =2 |
| `R ARM IN/OUT` | 3 (`raio`) | matches proto =3 |
| `HEAD UP/DN` | 4 (`head`) | proto 4 = `HEAD_UP_DN` ✅ |
| `BODY L/R` | 5 (`base`) | ⚠️ proto 5 = `HEAD_L_R` — **index spaces differ** |
| `BODY F/B` | 6 (`body`) | ⚠️ proto 6 = `HEAD_TILT` |

**Physical confirmation of the foot-gun:** the board has **one** head motor connector (`HEAD UP/DN`) and
**two** body/base ones (`L/R`, `F/B`), which is exactly why `libmotionlib`'s 5/6 are base/body while the
proto enum's 5/6 are head yaw/tilt. The hardware corroborates that these are genuinely different index
spaces — use the one matching your API.

## Complete chip inventory (with provenance)

Every component identified, with **how we know** it. "Read" = the marking is legible in an FCC photo
(cited); "DTB/FW" = established from the firmware/device-tree and *located* (not marking-read) in a
photo. Citation tags are defined in [References](#references) — e.g. **[R1‑INT p.6]** = rev1 Internal
Photos exhibit, page 6.

| # | Chip / part | Function | Exact marking | Provenance |
|--:|---|---|---|---|
| 1 | **Rockchip RK3288** | Main SoC (ARMv7 Cortex‑A17, Android 9) | *obscured by thermal compound* | **DTB/FW** authoritative ([firmware-803-reference](firmware-803-reference.md)); BGA **located** centre of compute board `100438` **[R1‑INT p.3]** |
| 2 | **Samsung DDR3 SDRAM** ×2 | Main memory | `SEC …` (Samsung; full P/N not legible) | **Read (partial)** — two packages flanking the SoC **[R1‑INT p.3]** |
| 3 | **eMMC (Samsung)** | Flash storage (`by-name` GPT) | `SEC …` (P/N not legible) | **Read (partial)** **[R1‑INT p.3]**; partitioning from [firmware-image](firmware-image.md) |
| 4 | **XMOS VSM02C** | Audio DSP (wake/VAD/DFU) | **`XMOS VSM02C` / `GT170802` / `PBPS13‑AM`** | **Read** QFP on `100438` **[R1‑INT p.6]**; role from [perception-pipeline](perception-pipeline.md) |
| 5 | **Broadcom BCM4339** (**AmPak AP6335** module) | Wi‑Fi 802.11ac + BT | *module shield, P/N not legible* | **DTB/FW** (`wifi_chip_type="ap6335"`, [device-tree](device-tree.md)); **module + u.FL `WIFI J3` located** **[R1‑INT p.6]**; 5 GHz band **[R1‑TR]** |
| 6 | **ST STM32F071VBT6** | "Lizard" motor/sensor MCU (Cortex‑M0, 128 KB) | **`STM32F071VBT6` / `AA027 98` / `TWN AA 105`** | **Read** LQFP‑100 on `"THE LIZARD"` `#101557` **[R2‑INT p.5]**; role from [hardware-map](hardware-map.md) |
| 7 | **TI DLPC3430** | DLP projector controller (the face) | *not marking‑read* | **DTB/FW** (i2c5 @0x1b, [device-tree](device-tree.md)); DLP **engine located** **[R1‑INT p.17]** |
| 8 | **TI DLPA‑class PMIC + DMD** | Projector power + micromirror | *not marking‑read* | **DTB/FW**; projector assembly **[R1‑INT p.17]** |
| 9 | **Rockchip RK808** | Main PMIC | *not marking‑read* | **DTB/FW** ([device-tree](device-tree.md)) |
| 10 | **Realtek RT5640** | Audio codec | *not marking‑read* | **DTB/FW** ([device-tree](device-tree.md)) |
| 11 | **OV2710 + GC2053** | Cameras | *not marking‑read* | **DTB/FW**; camera module **located** **[R1‑INT p.12]** |
| 12 | **PCA9635** | LED driver (status ring) | *not marking‑read* | **DTB/FW**; `LED` connector + ring **[R1‑INT p.6]** |
| 13 | Projector‑interface board **"JETTA"** | LVDS/projector + temp sense | silk `JETTA`, `100747`, `Proj`, `TEMP`, `Android` | **Read (silk)** **[R1‑INT p.22]** |
| 14 | **"Penguin Board"** | secondary board (sensor/IO) | silk `Penguin Board`, `#101505`, `EB002+003+23C` | **Read (silk)** **[R2‑INT p.5]** |

> **Two silicon confirmations we owe entirely to the FCC photos:** the **XMOS DSP** (#4) and the exact
> **STM32F071VBT6** (#6) part numbers — both previously inferred from firmware, now read off the die.
> The RK3288 (#1) and BCM4339 (#5) remain firmware‑authoritative because thermal compound / a shield
> hide their markings.

## Programming & debug toolchain — per chip

What you actually need to read/flash/debug each programmable device. Ports in **bold** are physically
present on the boards per the FCC photos (cited).

### RK3288 — the main SoC (Android)
- **Entry:** **`LOAD` button** on the mainboard **[R1‑INT p.6]** → hold at power‑on → **maskrom /
  rockusb** download mode (or AVB‑fail auto‑drop, see [hardware-access](hardware-access.md)).
- **Programmer / tool:** **`rkdeveloptool`** (Linux) or **Rockchip `upgrade_tool`** / **AndroidTool +
  DriverAssistant** (Windows) over the **USB data lines** — `rkdeveloptool db <loader>` then `wl`/`ul`.
- **Toolchain / "IDE":** Rockchip RK3288 SDK + **AOSP 9** build for `system`/`vendor`/`boot`;
  **`avbtool`** to make a `--disable-verification` `vbmeta` (or re‑sign the hashtrees); `fastboot`
  fallback. Full procedure: [flashing-runbook](flashing-runbook.md).
- **Debug console:** kernel `console=ttyFIQ0` (UART) — **SoC UART pads not located** in the FCC photos
  (open bench item). No exposed SWD/JTAG for the SoC identified.

### STM32F071VBT6 — the "Lizard" motor MCU
- **Ports (present on board):** **`ISP & DEBUG` 6‑pin header** with **`SWDIO`, `SWCLK`, `NRST`, `3V3`,
  `GND`** and a separate **`RX`/`TX`** pair, plus a local **`RESET`** button **[R2‑INT p.5]**.
- **Programmer (SWD):** **ST‑LINK/V2** (or ST‑LINK‑V3) on the SWDIO/SWCLK/NRST pins — the standard,
  fastest path; also works with a Black Magic Probe or a Pi/FT2232 via OpenOCD.
- **Programmer (UART bootloader):** the STM32 **system bootloader ROM** (no external programmer needed)
  over `RX`/`TX` with **`STM32CubeProgrammer`** / **`stm32flash`** — hold BOOT0 high at reset (check the
  `SWITCH`/BOOT strap near the header).
- **Programmer (in‑app):** Embodied's own **GOBY** DFU over UART3 / `bo-firmwareUpdate`
  (Intel‑HEX @`0x08000000`), from [hardware-map](hardware-map.md#lizard-mcu-firmware-update-bootloader-goby).
- **IDE / toolchain:** **STM32CubeIDE** (free, Eclipse‑based, GCC `arm-none-eabi`) with STM32CubeMX;
  or bare `arm-none-eabi-gcc` + OpenOCD + the STM32F0 HAL/LL. Debug via **GDB over ST‑LINK**.
  Reference manual **RM0091**, datasheet for STM32F071xB.

### XMOS VSM02C — the audio DSP
- **Ports:** XMOS **xSYS/JTAG** debug link (test pads, not broken out to a labelled header in the photos)
  **and** **USB DFU** (the shipped `xmosdfu`/`bo_xmosupdate` path, [perception-pipeline](perception-pipeline.md)).
- **Programmer:** **XMOS xTAG‑4** debug adapter (xSYS) for flash/JTAG; or the USB‑DFU route with the
  on‑device `xmosdfu` tool (loads `/vendor/etc/firmware/xmosdfu.bin`).
- **IDE / toolchain:** **XMOS XTC Tools** (formerly **xTIMEcomposer**) — `xcc` compiler, `xrun`/`xflash`,
  `xgdb`; language is **XC/C** for xCORE. Firmware is the `wk` wake‑word app.

### TI DLPC3430 — the projector controller
- **Ports:** **I²C** (bus i2c5 @`0x1b`, [device-tree](device-tree.md)) + the DMD/flash interface on the
  projector engine **[R1‑INT p.17]**.
- **Programmer / tool:** TI **DLP Composer** (flash the controller's config/splash over I²C) and the
  DLPC3430 flash‑loader; a USB‑to‑I²C adapter (e.g. TI Cheetah/Aardvark) for bench work.
- **IDE / toolchain:** TI DLP Composer GUI + the DLPC3430/DLPC3435 programmer's guide; no general‑purpose
  code — it's a fixed‑function controller configured via registers/flash.

### Wi‑Fi/BT (BCM4339 / AP6335) — no separate programmer
- Firmware is **host‑loaded at runtime** from `/vendor/etc/firmware/` over SDIO (Wi‑Fi) and UART0 (BT) —
  you don't flash the module; you change the files the RK3288 loads ([device-tree](device-tree.md)). So
  "reprogramming Wi‑Fi" = editing `/vendor` on the SoC.
- **Exact blobs a custom build must ship (verified on `vendor.img`, v24.10.803):** the BCM4339 Wi‑Fi
  firmware is the **`_ag`** (a/g‑band) family — `fw_bcm4339a0_ag.bin` (STA), `fw_bcm4339a0_ag_apsta.bin`
  (STA+SoftAP), `fw_bcm4339a0_ag_p2p.bin` (Wi‑Fi Direct) — paired with **`nvram_AP6335.txt`** (the AP6335
  module's calibration/NVRAM), and BT patchram **`bcm4339a0.hcd`**. The DTB selects them via
  `wifi_chip_type="ap6335"`. (`/vendor/etc/firmware/` also carries the full stock Rockchip BSP blob set
  for dozens of other BCM/RTL/SSV parts — dormant; only the `4339a0`/`AP6335` files above are loaded.)

### Not host‑programmable
RK808 PMIC, RT5640 codec, PCA9635 LED driver, OV2710/GC2053 cameras — configured over I²C by the SoC at
boot; no standalone firmware to flash.

## Bench shopping list (what the toolchain implies)
- **USB‑A→USB cable** + a Linux box with **`rkdeveloptool`** — RK3288 flashing (needs the `LOAD` button/USB).
- **ST‑LINK/V2** (~$3 clone) + **STM32CubeIDE/Programmer** — Lizard MCU via the `ISP & DEBUG` header.
- **3.3 V USB‑TTL adapter** — the MCU `RX`/`TX` console, and (once found) the SoC `ttyFIQ0` UART.
- **XMOS xTAG‑4** + **XTC Tools** — only if reflashing the audio DSP beyond the USB‑DFU path.
- **USB‑I²C adapter** + **TI DLP Composer** — only for projector‑controller work.

## rev1 vs rev2 — what changed

| Aspect | rev1 (2020) | rev2 / V2 (2023) | Reading |
|---|---|---|---|
| Radio grant | 5180–5240 MHz | **unchanged** | Same Wi-Fi class/band plan across generations — a re-homing server needs no per-generation radio handling |
| Test lab | Intertek HK | **unchanged** | Same compliance path |
| MCU | (Lizard board not in rev1's public set) | **`STM32F071VBT6`** on `"THE LIZARD"` `#101557` | rev2 exposes the MCU part number |
| Boards shown | mainboard `100438`, projector/`JETTA` iface, camera, DLP engine | Lizard `#101557`, `Penguin Board` `#101505` | The two filings are **complementary**: rev1 documents compute/optics, rev2 documents motion control |
| Confidentiality | photos public | short-term to 2024-05-18, **then public**; **Block Diagram, Schematics, and Operational Description withheld ("metadata only")** | Matches the [expected pattern](external-sources.md#provenance--the-law-can-we-use-this) — photos public, schematics not |

> **Touch sensors across generations.** Community teardowns report *older* Moxie **lack touch sensors**
> ([external-sources](external-sources.md#teardowns)). rev2's Lizard board silkscreens dedicated
> **`BACK`** and **`TUMMY`** headers, consistent with touch being present/standardised by the V2 board.
> We have **not** seen a rev1 Lizard board to compare directly — so treat "touch added/expanded in later
> hardware" as **corroborated but not proven** from these filings alone.

## What this resolves (and what it doesn't)

**Resolved by these filings:**
- ✅ Dual-band 5 GHz (U-NII-1) radio — corroborates the **AmPak AP6335 / BCM4339** module (AP6335 *is* an
  AMPAK part, per the DTB `wifi_chip_type="ap6335"`); the shield P/N isn't legible, so the photo confirms
  a discrete BCM4339 module without reading the marking.
- ✅ **XMOS DSP** confirmed by direct chip marking (`XMOS VSM02C`).
- ✅ **Lizard MCU = `STM32F071VBT6`**, exact part.
- ✅ **`LOAD` (download-mode) button exists on the mainboard**; `RESET`/`POWER` alongside.
- ✅ **`ISP & DEBUG` (SWD) + `RX`/`TX` UART surface on the Lizard board** — MCU is directly flashable.
- ✅ Motor DOF list confirmed physically; the dual index-space caveat is real.
- ✅ "Lizard" is the literal board name; boards use animal codenames (`Penguin Board`).

**Still open (bench work — see [COVERAGE](COVERAGE.md)):**
- ❌ **SoC part marking unread** (thermal paste) — RK3288 stands on firmware evidence.
- ❌ **Whether `LOAD`/USB is reachable without opening the shell** — FCC photos are of stripped boards.
- ❌ **RK3288-side UART console pads** — not identified in these photos (the `RX`/`TX` found are the
  **MCU's**, not the SoC's).
- ❌ Macro-button → ADC-level → boot-mode mapping.

## References

All exhibits are public FCC equipment-authorization records, grantee **Embodied, Inc.** (`2AV9N`).
Citations above use these tags; page numbers are the page of that exhibit PDF as published on
`fccid.io`. Facts extracted per the [provenance policy](external-sources.md#provenance--the-law-can-we-use-this);
images described, not re-hosted.

| Tag | Exhibit | Report No. | FCC ID | URL |
|---|---|---|---|---|
| **[R1‑INT]** | rev1 Internal Photos | `20031489HKG‑002/‑003` (Intertek HK) | `2AV9N‑EMBODIEDMOXIEA` | https://fccid.io/2AV9NEMBODIEDMOXIEA |
| **[R1‑EXT]** | rev1 External Photos | — | `2AV9N‑EMBODIEDMOXIEA` | https://fccid.io/2AV9NEMBODIEDMOXIEA |
| **[R1‑TR]** | rev1 Test Report (exhibit `4817095`) | `20031489HKG‑002/‑003` | `2AV9N‑EMBODIEDMOXIEA` | https://fccid.io/2AV9NEMBODIEDMOXIEA/Test-Report/Test-report-4817095 |
| **[R2‑INT]** | rev2 Internal Photos | `HK23051041` (Intertek HK) | `2AV9N‑EMBMOXIEVTWO` | https://fccid.io/2AV9NEMBMOXIEVTWO |
| **[R2‑EXT]** | rev2 External Photos | `HK23051041` | `2AV9N‑EMBMOXIEVTWO` | https://fccid.io/2AV9NEMBMOXIEVTWO |

**Page index of the images cited** (rev1 Internal Photos "Equipment Photographs (Internal)"):
- **p.3** — robot disassembled (fresnel face, arms, base speaker) + **main compute board `100438`**
  (SoC under thermal compound, 2× Samsung DDR3, eMMC).
- **p.6** — mainboard with **XMOS `VSM02C`** QFP, **Wi‑Fi module + u.FL `WIFI J3`**, and the
  **`RESET` / `LOAD` / `POWER`** buttons; connectors `LIZARD`/`LED`/`SPK`/`MICS`/`PROJECTOR`.
- **p.9, p.12** — camera module (OV2710) + bracket (two views).
- **p.15/p.17** — **DLP projector engine** (metal‑cased, heatsink, lens) + button/sensor daughterboard.
- **p.22** — projector‑interface **"JETTA"** board (`100747`, `Proj`, `TEMP`, `Android`).
- **p.30** — rev1 **"THE LIZARD"** motion board (`EB001+003+02 12‑02‑2019`); connectors `HRT LED`, `SPACE MOUSE`, `RIGHT/LEFT ARM`, motor DOFs, `ON/OFF SWITCH`, `16V IN`.
- **p.42** — **Wi‑Fi antenna** (flex PIFA, silk `2014.07.22 RoHS`).

**rev2 Internal Photos:**
- **p.4** — **"THE LIZARD"** motor board `#101557` (top: labelled motor connectors; bottom: MCU side).
- **p.5** — **STM32F071VBT6** close‑up with **`ISP & DEBUG`** header, `SWDIO`/`SWCLK`/`NRST`/`RX`/`TX`
  test points, `RESET` button; and the **"Penguin Board"** `#101505`.

> **Confidentiality note:** rev2's **Block Diagram, Schematics, and Operational Description** are filed
> **"metadata only"** (permanently withheld under 47 CFR §0.457/§0.459); internal/external photos and
> the test/SAR reports are public (short‑term confidentiality lapsed 2024‑05‑18). See the
> [law section](external-sources.md#provenance--the-law-can-we-use-this).

---
📖 [External sources map](external-sources.md) · [Hardware map](hardware-map.md) · [Hardware access](hardware-access.md) · [Device tree](device-tree.md) · [Coverage](COVERAGE.md) · [RE index](README.md)
