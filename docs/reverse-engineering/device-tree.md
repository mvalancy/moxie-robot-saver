# 🔧 Device tree — board-level hardware wiring

> The authoritative hardware map of **v3.6.4-Zephyr / OTA v24.10.803**, decompiled from the DTB inside
> `boot.img` (Rockchip RSCE resource). Board **`rockchip,rk3288-robot`**, model *"Rockchip rk3288
> robot board"* (`rk3288-robot-gen1p5`). Full source: [`manifests/rk3288-robot-gen1p5.dts`](manifests/rk3288-robot-gen1p5.dts)
> (4,202 lines). This is ground-truth for what's wired where — essential for custom firmware & bring-up.

## I²C buses & devices

| Bus (base) | Linux | Device @addr | Chip | Role |
|---|---|---|---|---|
| `ff650000` | i2c0 | `pmic@1b` | **RK808** (`rockchip,rk808`) | PMIC — power rails, RTC, regulators |
| `ff140000`/`ff150000` | i2c1/2 | `ov2710@36`, `gc2053@37` | **OV2710** (`ovti`) + **GC2053** (`galaxycore`) | camera sensors (two options) |
| `ff160000` | i2c3 | `pca9635@60` | **PCA9635** (`nxp`) | 16-ch LED driver → **6× RGB** (`red/green/blue_1..6`) status LEDs |
| `ff170000` | i2c4 | `hx7027@48` | **Himax HX7027** | sensor (ambient light / ADC front-end) |
| `ff660000` | i2c5 | `rt5640@1c` | **Realtek RT5640** (ALC5640) | audio codec |

> The DLP projector controller (**DLPC3430**, referenced in init as `ff170000.i2c/i2c-5/5-001b`) and
> the XMOS DSP are **not** base-DTB I²C nodes here — the projector is driven over a display
> output + a control channel, and XMOS is on **USB** ([`perception-pipeline.md`](perception-pipeline.md)).
> They may be added by an overlay (`dtbo.img`) or probed at runtime.

## UARTs

| Alias | Node | `/dev` | Use |
|---|---|---|---|
| serial0 | `ff180000` | ttyS0 | — |
| serial1 | `ff190000` | ttyS1 | — |
| serial2 | `ff690000` | ttyFIQ0 | **debug console** (`earlycon`, `console=ttyFIQ0`) — the [serial console](hardware-access.md) |
| serial3 | `ff1b0000` | **ttyS3** | **Lizard STM32 MCU** (motors/sensors — see [`hardware-map.md`](hardware-map.md)) |
| serial4 | `ff1c0000` | ttyS4 | — |

## Display & camera

- **Display path:** dual **VOP** (`vop-big`/`vop-lit`) → **2× MIPI-DSI**, **DisplayPort (`dp`)**, **HDMI**,
  MIPI-DPHY/CSI. The **DLP projector** (face) is fed by one of these outputs; PWM (`pwm@ff680000`×4)
  drives backlight/fan (`projectorfanpid`).
- **Camera path:** OV2710/GC2053 → **RKISP1**/CIF ISP (`isp@ff910000`) + IOMMU. `sys.embodied.camhw=ov2710`
  selects the sensor.
- **GPU:** ARM **Mali-T764** (`gpu@ffa30000`); **VPU**/HEVC video codecs for decode.

## Audio

- **RT5640** codec on i2c5 + **I²S** (`i2s@ff890000`) + **SPDIF** (`sound@ff8b0000`). The **XMOS DSP**
  (mic array/AEC/wake-word) sits on USB upstream of the codec ([`perception-pipeline.md`](perception-pipeline.md)).

## Power, storage, misc

- **RK808 PMIC** + power-management (`ff730000`) with power domains (`pd_vio/hevc/video/gpu`); a
  `syscon-reboot-mode` on the GPU PD node (reboot-reason signalling → recovery/loader).
- **eMMC** via `dwmmc@ff0f0000`; SD via `dwmmc@ff0c0000`; NAND controller present but unused.
- **SARADC** (`ff100000`) analog inputs; **TSADC** thermal; **secure eFuse** (`ffb40000`) holds chip
  id / leakage / performance grades; **crypto-controller** (`ff8a0000`).
- **9 GPIO banks** (`gpio0..8`); **GMAC** ethernet present (unused on the robot); **USB**: OTG
  (`ff580000`) + EHCI/OHCI hosts (XMOS + peripherals).

## For custom firmware (goal #1)

The DTS is the wiring contract: it names every regulator, clock, pinmux, and device address a custom
kernel/HAL must match. Notably the **Lizard MCU is UART3** and the **status LEDs are a PCA9635** — the
two custom-silicon touch-points beyond the stock RK3288 SDK. Reuse this DTB (or the RK3288 Android-9
SDK's `rk3288-robot` target) rather than a generic EVB.

---
📖 [Hardware map](hardware-map.md) · [Hardware access](hardware-access.md) · [Firmware reference](firmware-803-reference.md) · [Docs index](../README.md)
