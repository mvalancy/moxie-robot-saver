# 📁 wifiapp

The folder name is not the package: all five files declare [`embodied.unity`](../../../proto-catalog.md#embodiedunity), as the firmware does, so full names read `embodied.unity.QRCommand` and so on.
They belong to the setup app, `bo-wifi`.
`QRCommands.proto` holds the `QRCommand` it forwards to the bus and the pairing, Wi-Fi and VPN payloads.
The other four files are its status, silent-boot, shutdown and bricked messages.
[QR commands](../../../qr-commands.md) explains the grammar.

| File | Defines |
|---|---|
| [`QRCommands.proto`](QRCommands.proto) | `QRCommand`, `QRResponse`, `QRDiagnosticData`, `StartPairingQR`, `WifiNetworkUpdate`, `QRMultiDecoder`, `QRVPNConfig`; enums `WifiBandSelect`, `VPNCommand` |
| [`WifiAppBricked.proto`](WifiAppBricked.proto) | `WifiAppBricked` |
| [`WifiAppShutdown.proto`](WifiAppShutdown.proto) | `WifiAppShutdown` |
| [`WifiAppSilentBoot.proto`](WifiAppSilentBoot.proto) | `WifiAppSilentBoot` |
| [`WifiAppStatus.proto`](WifiAppStatus.proto) | `WifiAppStatus` |

---
📖 [embodied](../README.md) · [Docs index](../../../../../../docs/README.md) · [Back to top](../../../../../../README.md)
