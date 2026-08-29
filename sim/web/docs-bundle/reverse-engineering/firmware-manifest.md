# 🗃️ Firmware manifest — every file, by the numbers

> Consolidated file inventory of **v3.6.4-Zephyr / OTA v24.10.803**, from read-only loop mounts of the
> partition images. Full `size⇥path` tables are in [`manifests/`](manifests/); this is the summary.

## Totals

| Partition | Files | Size |
|---|--:|--:|
| `system.img` | 2,250 | ~2,085 MB |
| `vendor.img` | 507 | ~1,186 MB |
| `oem.img` | 4 | ~81 MB (boot animation) |

## `/system` by top directory

| Dir | Size | Notes |
|---|--:|---|
| `priv-app` | **1,527 MB** | privileged apps — **bo-android alone is 962 MB** |
| `lib` | 177 MB | 32-bit shared libs |
| `framework` | 150 MB | AOSP framework (`.jar`/`framework-res.apk`) |
| `app` | 105 MB | regular apps (webview 49 MB, …) |
| `fonts` | 68 MB | incl. NotoSerifCJK (24 MB) |
| `usr` | 26 MB | icu, keychars, share |
| `bin` | 15 MB | 334 native binaries |
| `media` | 9 MB | audio/ui media |

## Largest files (system)

| Size | Path |
|--:|---|
| 962 MB | `priv-app/bo-android/bo-android.apk` (the brain — native ML libs dominate) |
| 144 MB | `priv-app/bo-wifi/bo-wifi.apk` (Unity setup app) |
| 133 MB | `priv-app/FabTestSoftware/FabTestSoftware.apk` |
| 49 MB | `app/webview/webview.apk` |
| 46 MB | `priv-app/Settings/Settings.apk` |
| 39 MB | `framework/framework-res.apk` |
| 28/27/27 MB | `productiontesting.{finaltest,internalassytest,lifetest}` |

## File-type breakdown (system)

| ext | count | | ext | count |
|---|--:|---|---|--:|
| `.so` | 673 | | `.vdex` | 120 |
| `.ogg` | 217 | | `.odex` | 105 |
| (none) | 209 | | `.apk` | 80 |
| `.ttf` | 196 | | `.rc` | 49 |
| `.0` (certs) | 194 | | `.jar` | 43 |

`.vdex`/`.odex` = AOT-compiled app bytecode (dexpreopt); 673 `.so` = the native surface (RK3288 HALs
+ the `bo-*` brain libs, see [`firmware-803-reference.md`](firmware-803-reference.md)); 217 `.ogg` =
system/UI sounds; 194 `.0` = the CA trust store ([`network-trust.md`](network-trust.md)).

## Embodied file hashes

SHA-256 (first 32) of the Embodied apps/binaries is in
[`manifests/embodied-sha256.tsv`](manifests/embodied-sha256.tsv) — e.g. `bo-android.apk`
`04d6aa6745a8e629728cb95819fdb3fd…`, `ledctrld` `36bb1d3e7eb38326e9084fffc8dba384…`. Partition-image
hashes are in [`firmware-803-reference.md`](firmware-803-reference.md).

---
📖 [Firmware reference](firmware-803-reference.md) · [Inventory](firmware-inventory.md) · [Reverse-engineering index](README.md) · [Docs index](../README.md)
