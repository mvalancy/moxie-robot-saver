# 🔧 Device tree — board-level hardware wiring

Board-level wiring of **v3.6.4-Zephyr / OTA v24.10.803**, decompiled from the DTB inside `boot.img`
(Rockchip RSCE resource): board **`rockchip,rk3288-robot`**, model *"Rockchip rk3288 robot board"*
(`rk3288-robot-gen1p5`). Full source: [`manifests/rk3288-robot-gen1p5.dts`](../firmware/manifests/rk3288-robot-gen1p5.dts)
(4,202 lines). It is the wiring contract a custom kernel/HAL must match. The two custom-silicon
touch-points beyond the stock RK3288 SDK are the **Lizard MCU on UART3** and the **PCA9635 LED driver on
i2c4**. Reuse this DTB (or the SDK's `rk3288-robot` target) rather than a generic EVB.

## I²C buses & devices

Linux bus numbers are from the DT `aliases` (authoritative):

| Bus (base) | Linux | Device @addr | Chip | Role |
|---|---|---|---|---|
| `ff650000` | **i2c0** | `pmic@1b` | **RK808** (`rockchip,rk808`) | PMIC — rails, RTC, regulators (power tree below) |
| `ff140000` | **i2c1** | — | — | (camera bus) |
| `ff150000` | **i2c3** | `ov2710@36`, `gc2053@37` | **OV2710** (`ovti`) + **GC2053** (`galaxycore`) | camera sensors (two options) |
| `ff160000` | **i2c4** | `pca9635@60` | **PCA9635** (`nxp`) | 16-ch LED driver: `red_1..6`, `green_1..5`, `blue_1..5` status LEDs |
| `ff170000` | **i2c5** | `hx7027@48` **+ `@0x1b`** | **Himax HX7027** + **TI DLPC3430** | HX7027 sensor; **DLPC3430 DLP projector controller @0x1b** (runtime-probed as `5-001b`, see init `dlpc3430-bl`) |
| `ff660000` | **i2c2** | `rt5640@1c` | **Realtek RT5640** (ALC5640) | audio codec |

> The **DLPC3430** is on **i2c5 @0x1b** (probed at runtime — the base DTB lists only `hx7027@48` on
> that bus; init drives `…/5-001b/{led_out,rgb_out,brightness_alt,temperature}`). The **XMOS DSP** is on
> **USB** ([`perception-pipeline.md`](../runtime/perception-pipeline.md)). `dtbo.img` is **empty** (a 72-byte stub —
> no overlay hardware).

## UARTs

| Alias | Node | `/dev` | Use |
|---|---|---|---|
| serial0 | `ff180000` | ttyS0 | — |
| serial1 | `ff190000` | ttyS1 | — |
| serial2 | `ff690000` | ttyFIQ0 | **debug console** (`earlycon`, `console=ttyFIQ0`) — the [serial console](hardware-access.md) |
| serial3 | `ff1b0000` | **ttyS3** | **Lizard STM32 MCU** (motors/sensors — see [`hardware-map.md`](hardware-map.md)) |
| serial4 | `ff1c0000` | ttyS4 | — |

## Display & camera

- **Display path (the face):** the **DLP projector** is wired as an **RGB parallel panel** —
  `rgb-panel { compatible = "simple-panel"; status = "okay" }` driven by a **VOP** (`vop-big`/`vop-lit`);
  `edp-panel` and `lvds-panel` are **disabled**. The **DLPC3430** takes that 24-bit RGB input, is
  configured over **i2c5 @0x1b**, and its **backlight/enable is GPIO7** (`projector { backlight-en }`).
  PWM (`pwm@ff680000`×4) drives the projector fan (`everflow,projectorfan-pwm` / `projectorfanpid`).
  (MIPI-DSI/DP/HDMI blocks exist on the SoC but aren't the face path.)
- **Camera path:** OV2710/GC2053 → **RKISP1**/CIF ISP (`isp@ff910000`) + IOMMU. `sys.embodied.camhw=ov2710`
  selects the sensor.
- **GPU:** ARM **Mali-T764** (`gpu@ffa30000`); **VPU**/HEVC video codecs for decode.

## Audio

- **RT5640** codec on i2c2 + **I²S** (`i2s@ff890000`) + **SPDIF** (`sound@ff8b0000`). The **XMOS DSP**
  (mic array/AEC/wake-word) sits on USB upstream of the codec ([`perception-pipeline.md`](../runtime/perception-pipeline.md)).

## Inputs & controls

Moxie has **exactly two physical buttons** (`rockchip,key`):

| Button | Wiring | Linux code | Notes |
|---|---|---|---|
| **Power** | **GPIO0_A5** (`gpio0` pin 5, active-low) | `KEY_POWER` (0x74) | `gpio-key,wakeup` — wakes the SoC |
| **Macro** | **SARADC channel 1** (`adc_value=0x01`) | `KEY_MACRO` (0x70) | the multi-function button, read as an ADC level |

Other GPIO/analog I/O of note (banks resolved from phandles):

- **Camera power-down:** `gpio2` pin 14 (`pwdn-gpios` for OV2710 / GC2053).
- **Projector enable / backlight:** `gpio7` (pins 2/3 `enable-gpios`, `backlight-en`) + `projector-en-regulator`.
- **USB OTG + charger detect:** `dwc-control-usb` exposes `otg_id`/`otg_bvalid`/`otg_linestate` and the
  USB-phy `chgdet`/`vdatdetenb` — USB role + charger detection (the barrel-jack `DC_PLUG` is sensed on
  the MCU side, see [`hardware-map.md`](hardware-map.md)).
- **SARADC** (`ff100000`, `vref` from a regulator) also backs the macro key; remaining channels are
  available for analog sensing.

With only Power + Macro exposed, any button route into loader/recovery uses those two (bench
hypothesis; [hardware-access](hardware-access.md#boot-mode-entry-reboot-reasons-keys)). The mainboard
also has an internal `LOAD` button ([fcc-teardown](fcc-teardown.md)).

## Power, storage, misc

- **RK808 PMIC** + power-management (`ff730000`) with power domains (`pd_vio/hevc/video/gpu`); a
  `syscon-reboot-mode` on the GPU PD node (reboot-reason signalling → recovery/loader).
- **eMMC** via `dwmmc@ff0f0000`; SD via `dwmmc@ff0c0000`; NAND controller present but unused.
- **SARADC** (`ff100000`) analog inputs; **TSADC** thermal; **secure eFuse** (`ffb40000`) holds chip
  id / leakage / performance grades; **crypto-controller** (`ff8a0000`).
- **9 GPIO banks** (`gpio0..8`); **GMAC** ethernet present (unused on the robot); **USB**: OTG
  (`ff580000`) + EHCI/OHCI hosts (XMOS + peripherals).

## Connectivity (Wi-Fi / Bluetooth)

The combo module is an **AmPak AP6335** (`wireless-wlan { wifi_chip_type = "ap6335" }`) — a **Broadcom
BCM4339** (single-stream 802.11ac + BT 4.x):
- **Wi-Fi over SDIO** (`dwmmc`, `sdio-pwrseq` with a `wifi-enable-h` GPIO, `wifi-supply` regulator);
  interface `wlan0`.
- **Bluetooth over UART0** (`wireless-bluetooth`/`bluetooth-platdata`, `uart0_gpios`). `ro.rk.bt_enable=true`.

The exact firmware files loaded (`fw_bcm4339a0_ag*.bin`, `nvram_AP6335.txt`, `bcm4339a0.hcd`) and the
unused SDK blobs are listed in [hal-and-drivers](../firmware/hal-and-drivers.md#radio-bcm4339-ap6335).

## Power tree (RK808 PMIC → rails)

The RK808 (i2c0 @0x1b) regulators map to named rails (from DT `aliases`):

| Regulator | Rail | | Regulator | Rail |
|---|---|---|---|---|
| DCDC_REG1 | `vdd_cpu` | | LDO_REG5 | `vccio_sd` |
| DCDC_REG2 | `vdd_gpu` | | LDO_REG6 | `vdd10_lcd` (projector 1.0 V) |
| DCDC_REG3 | `vcc_ddr` | | LDO_REG7 | `vcc_18` |
| DCDC_REG4 | `vcc_io` | | LDO_REG8 | `vcc18_lcd` (projector 1.8 V) |
| LDO_REG1 | `vcc_tp` | | SWITCH_REG1 | `vcc_sd` |
| LDO_REG2 | `vcca_codec` | | SWITCH_REG2 | `vcc_lcd` (projector main) |
| LDO_REG3 | `vdd_10` | | | |
| LDO_REG4 | `vcc_wl` (Wi-Fi) | | | |

`rockchip-suspend` configures PMIC-driven sleep; `projector-en-regulator` gates projector power.

---
📖 [Hardware map](hardware-map.md) · [Hardware access](hardware-access.md) · [Firmware reference](../firmware/firmware-803-reference.md) · [Docs index](../../README.md)
