# Moxie Pairing QR Code Format

> Analyzed build: **v3.6.4-Zephyr / OTA v24.10.803** (RK3288, Android 9) — see [`firmware-803-reference.md`](../firmware/firmware-803-reference.md).

> **📖 About this document.** This is a *clean-room* description of the **original** Moxie parent app
> (`com.embo.embodied.parent` v2.2.2), written by reverse-engineering it. **The decompiled app is NOT
> included in this repository, and you do not need it.** Any file or class names below (e.g.
> `api/Config.java`, `pair_moxie/…`, or paths shown as `<decompiled>/…`) are references into the
> *original app's own internal structure* — they document *where a behavior lived in the app so the
> protocol is reproducible*, and are **not** files in this repo. Our actual implementation lives in
> [`server/`](../../../server/), [`tools/`](../../../tools/), and [`mqtt/`](../../../mqtt/).

> Clean-room reconstruction from the decompiled **Moxie Robot parent app v2.2.2**
> (`com.embo.embodied.parent`), classes `ProtoPairing`, `JSONPairing`,
> `PairingModel`, `WifiNetworkInfo`, `PairingInfo`, `PairMoxieQrCodeFragment`.
> A working, round-trip-tested encoder/decoder lives in
> [`tools/pairing/moxie_qr.py`](../../../tools/pairing/moxie_qr.py).

## What the QR is for

During setup, the parent app displays a QR code on the phone screen. You hold it up
to **Moxie's camera**. The QR hands the robot two things:

1. **WiFi credentials** so the robot can join your network.
2. A **pairing secret** that binds the robot to a cloud account — either an
   Ed25519 signing key (proto mode) or a cloud `user_token` (json mode).

The app supports two encodings, chosen by `Config.PairQRMode`:

| Mode | Enum | Prefix | Secret carried |
|------|------|--------|----------------|
| Protobuf | `PAIR_PROTO_KEY` | `PA` + Base64 | Ed25519 signing key bytes |
| JSON | `PAIR_JSON_TOKEN` | none (raw JSON) | cloud `user_token` string |

`PairMoxieQrCodeFragment` picks proto mode when it also passes the account's
`iot-endpoint` index; json mode embeds an `accessToken` as `user_token`.

## JSON mode

A plain UTF-8 JSON string (no prefix), serialized by Gson from `PairingModel`:

```json
{
  "wifi": {
    "ssid": "HomeNet",
    "password": "s3cr3t!",
    "is_hidden": false,
    "band_select": "ANY"        // ANY | ONLY_50G | ONLY_24G  (WifiBand enum)
  },
  "pair": { "user_token": "<cloud token>" }   // null when "hide pair" is set
}
```

Field names come from `@SerializedName` annotations:
`ssid`, `password`, `is_hidden`, `band_select`, `pair`, `user_token`.

## Protobuf mode (`PA` + Base64)

`ProtoPairing.toQRString(hidePair, iotEndpoint)` builds a **hand-rolled protobuf**
message (not from a `.proto` — bytes are emitted directly), Base64-encodes it with
Android `Base64.DEFAULT`, and prefixes the ASCII string `PA`.

### Wire format

Emitted in this exact order (tag byte = `(field_number << 3) | wire_type`):

| Order | Tag | Field | Wire type | Meaning | Emitted when |
|------:|-----|------:|-----------|---------|--------------|
| 1 | `0x0A` | 1 | LEN | `ssid` (UTF-8) | always |
| 2 | `0x12` | 2 | LEN | `password` (UTF-8) | always |
| 3 | `0x18` | 3 | VARINT | dev flag `= 1` | **non-PRODUCTION builds only** |
| 4a | `0x22` | 4 | LEN | `secret_key` (Ed25519 key bytes) | when **not** hide_pair |
| 4b | `0x28` | 5 | VARINT | `hide_pair = 1` | when hide_pair (instead of 4a) |
| 5 | `0x30` | 6 | VARINT | `is_hidden = 1` | when SSID hidden |
| 6 | `0x38` | 7 | VARINT | `band` (`1`=5GHz only, `2`=2.4GHz only) | when band ≠ ANY |
| 7 | `0x40` | 8 | VARINT | `iot_endpoint` (int index) | always |

Notes / gotchas faithfully reproduced from the app:

- **`secret_key` and `hide_pair` are mutually exclusive** — a proto QR carries the
  key *or* the `hide_pair` flag, never both.
- Field 3 (dev flag) is **only** present in staging/develop builds
  (`Config.getBuildMode() != PRODUCTION`). A production QR skips it.
- `iot_endpoint` is written with `ByteBuffer.put((byte) i)` — a **single raw byte**,
  not a real varint. Identical to varint for values `0–127` (the only ones used).
- Varints elsewhere use the standard LEB128 (`encodeVarInt`).
- `Base64.DEFAULT` (flag `0`) wraps at 76 columns with `\n` and adds a trailing
  `\n`. Any decoder must ignore whitespace. Our encoder emits unwrapped Base64 by
  default and can reproduce the wrapped form with `android_default=True`.

### The signing key

In proto mode the app embeds
`CryptoHelper.getInstance().getSigningKey().toBytes()`. It also registers
`SHA-256(those bytes)` with the backend via `registerForPairing(...)`
(`ProtoPairing.serectHashFromKey`). See [`crypto-and-pairing.md`](crypto-and-keys.md)
for the exact key type, derivation, and the server-side handshake.

## Worked example

```
$ ./tools/pairing/moxie_pair.py --ssid HomeNet --password 's3cr3t!' \
      --band 24g --mode proto --iot-endpoint 0 \
      --secret-key-hex 000102...1f --out qr.png
QR payload (proto): PACgdIb21lTmV0EgdzM2NyM3QhIiAAAQ...OAJAAA=
```

Decoding that payload yields:
`ssid=HomeNet, password=s3cr3t!, band=ONLY_24G, secret_key=00..1f, iot_endpoint=0`.
