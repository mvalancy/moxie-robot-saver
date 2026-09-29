# Moxie Pairing QR Code Format

**Summary.** During setup the parent app shows a QR on the phone screen and the robot's camera reads it.
The QR carries Wi-Fi credentials plus a pairing secret. The shipped app (v2.2.2) always emits
**`"PA"` + Base64(hand-rolled protobuf)**, and that protobuf carries the raw 32-byte key seed. An older JSON
format, which carried the OAuth access token instead, is still in the code but can no longer be reached.
This page is the canonical byte-level spec. Our codec is
[`tools/pairing/moxie_qr.py`](../../../tools/pairing/moxie_qr.py) (round-trip tested) and the CLI is
[`tools/pairing/moxie_pair.py`](../../../tools/pairing/moxie_pair.py).

> Clean-room notes on the original app (`com.embo.embodied.parent` v2.2.2). Class names such as
> `ProtoPairing` and `pair_moxie/…` point to places *inside that app*. They are not files in this repo. The
> robot-side parser (`StartPairingQR`, firmware `v24.10.803`) is documented in
> [`qr-commands.md`](../protocol/qr-commands.md#pairing-qr-pa-startpairingqr).

Sources in the app: `pair_moxie/ProtoPairing`, `JSONPairing`, `PairMoxieQrCodeFragment`, `PairingMode`,
`WifiBand`, and `api/models/wifi/{WifiNetworkInfo,PairingInfo,PairingModel}`.

## Mode selection

| Mode | Enum (`Config.PairQRMode`) | Prefix | Secret carried |
|------|------|--------|----------------|
| Protobuf (shipped) | `PAIR_PROTO_KEY` | `PA` + Base64 | the 32-byte signing-key seed |
| JSON (legacy, dead) | `PAIR_JSON_TOKEN` | none (raw JSON) | the OAuth `access_token` as `user_token` |

In v2.2.2, JSON mode is dead code. `Config.getPairQRMode()` reads prefs key `pairing_qr_mode`
(`SharedKeys.PAIRING_QR_MODE`) and then returns `PAIR_PROTO_KEY` from **both** branches:

```java
public static PairQRMode getPairQRMode() {
    if (PairQRMode.PAIR_PROTO_KEY.ordinal() == prefs.getInt(SharedKeys.PAIRING_QR_MODE, PairQRMode.PAIR_PROTO_KEY.ordinal()))
        return PairQRMode.PAIR_PROTO_KEY;
    return PairQRMode.PAIR_PROTO_KEY;
}
```

`Config.setPairQRMode()` exists, but no caller can change the result. Older robot firmware probably
still accepts the JSON form, which needs no crypto, so our tools keep it as a fallback.

There is a second, independent switch: **`PairingMode { WIFI_ONLY, WIFI_AND_PAIRING }`**, passed as the
intent extra `pairing_mode`. `WIFI_ONLY` is "Edit Wi-Fi" for a robot that is already paired. Its QR
carries only the Wi-Fi fields plus `hide_pair`: no key and no token.

The branch lives in `PairMoxieQrCodeFragment.generateQrCode` (`initViews` calls
`showPairingView(view, getPairQRMode()==PAIR_PROTO_KEY)`):

```java
String accessToken = Config.getAuthDataModel() != null ? Config.getAuthDataModel().getAccessToken() : "";
boolean z2 = PairMoxieWifiFragment.getPairingMode() == PairingMode.WIFI_ONLY;   // "hidePair"
if (z) {   // proto
    jsonString = new ProtoPairing(PairMoxieWifiFragment.getWifiNetworkInfo(),
                                  CryptoHelper.getInstance().getSigningKey().toBytes())
                 .toQRString(z2, (user == null || iotEndpoint == null) ? 0 : iotEndpoint.intValue());
} else {   // json
    jsonString = new JSONPairing(PairMoxieWifiFragment.getWifiNetworkInfo(),
                                 z2 ? null : new PairingInfo(accessToken)).toJsonString();
}
```

## Protobuf mode (`PA` + Base64)

`ProtoPairing.toQRString(boolean hidePair, int iotEndpoint)` writes bytes directly into a
`ByteBuffer.allocate(1024)`. No `.proto` file is involved. It Base64-encodes the result with Android
`Base64.encodeToString(bArr, 0)` and prefixes the ASCII string `PA` (`PROTO_PAIR_HEADER`).

### Wire format

Fields are emitted in this exact order. Tag byte = `(field << 3) | wire_type`.

| Order | Tag | Field | Wire | Meaning | Emitted when |
|------:|-----|------:|------|---------|--------------|
| 1 | `0x0A` | 1 | LEN | `ssid` (UTF-8) | always |
| 2 | `0x12` (`Ascii.DC2`) | 2 | LEN | `password` (UTF-8) | always |
| 3 | `0x18` (`Ascii.CAN`) | 3 | VARINT | dev flag `= 1` | only when `Config.getBuildMode() != PRODUCTION` |
| 4a | `0x22` | 4 | LEN | `secret_key`: the 32-byte seed | when **not** hide_pair |
| 4b | `0x28` | 5 | VARINT | `hide_pair = 1` | when hide_pair (`WIFI_ONLY`), instead of 4a |
| 5 | `0x30` | 6 | VARINT | `is_hidden = 1` | only when the SSID is hidden |
| 6 | `0x38` | 7 | VARINT | band: `1` = 5 GHz only, `2` = 2.4 GHz only | only when band is set and not `ANY` |
| 7 | `0x40` (`SignedBytes.MAX_POWER_OF_TWO`) | 8 | VARINT | `iot_endpoint` | always (`0` if the user record has none) |

Reconstructed `.proto`. The robot's own names for these fields are in `StartPairingQR`: `is_staging`, `wifi_only`,
`band_select`, and `endpoint` (an `IOTEndpoint` enum).

```proto
message MoxiePairing {
  string ssid         = 1;
  string password     = 2;
  bool   dev_mode     = 3;  // non-PRODUCTION builds only, always 1
  bytes  secret_key   = 4;  // 32-byte Ed25519 seed; mutually exclusive with 5
  bool   hide_pair    = 5;  // 1 when PairingMode == WIFI_ONLY
  bool   is_hidden    = 6;  // emitted only when true
  uint32 band_select  = 7;  // 1 = 5 GHz only, 2 = 2.4 GHz only; omitted for ANY
  uint32 iot_endpoint = 8;  // single raw byte
}
```

Gotchas reproduced from the app:

- Fields always appear in ascending order, so a strict decoder works.
- **`secret_key` and `hide_pair` are mutually exclusive.**
- Length prefixes use standard LEB128 varints (`encodeVarInt`).
- `iot_endpoint` is written with `put((byte) i)`, a **single raw byte**, not a varint. The two agree for
  0–127. A value above 127 would break decoding. The value selects a robot-side
  [`IOTEndpoint`](../protocol/proto-catalog.md) (for example,
  `OPEN_MOXIE = 11`).
- Band numbering: `WifiBand` is declared `ANY, ONLY_50G, ONLY_24G` (ordinals 0/1/2). The wire value is
  1 = 5 GHz and 2 = 2.4 GHz, which happens to match those ordinals.
- **Base64:** `Base64.DEFAULT` (flag 0) pads, wraps at 76 columns with `\n`, and adds a trailing `\n`. A
  typical payload is 60–90 bytes, or about 80–120 Base64 characters, so the real QR string usually
  **contains embedded newlines**. Decoders must ignore whitespace. `moxie_qr.py` emits unwrapped Base64 by
  default, and `android_default=True` reproduces the app's exact wrapped bytes.

### The secret key

`secret_key` is `CryptoHelper.getSigningKey().toBytes()`, which returns the **32-byte Argon2id seed**. It
is not the public key and not the 64-byte Ed25519 secret key. Before showing the QR, the app registers
`hex(SHA-256(seed))` with `POST pairing-info`. For how the seed is derived and why the QR is a
total-compromise artifact, see [`crypto-and-keys.md`](crypto-and-keys.md). For the handshake, see
[`pairing-and-robot.md`](pairing-and-robot.md).

## JSON mode (legacy)

`JSONPairing.toJsonString()` serializes a `PairingModel` with the app's plain Gson (`GsonBuilder()` plus
a `byte[]`↔Base64 adapter). As a result, keys come from `@SerializedName`, **nulls are omitted**, and
enums are written by `name()`. The output is UTF-8 with no prefix.

```json
{
  "wifi": { "ssid": "HomeNet", "password": "s3cr3t!", "is_hidden": false, "band_select": "ONLY_24G" },
  "pair": { "user_token": "<oauth access_token>" }
}
```

| JSON key | Java field | Notes |
|---|---|---|
| `wifi.ssid` | `WifiNetworkInfo.ssid` | trimmed |
| `wifi.password` | `WifiNetworkInfo.password` (`@SerializedName(Config.GRANT_TYPE_PASSWORD)` = `"password"`) | trimmed |
| `wifi.is_hidden` | `WifiNetworkInfo.isHidden` | boolean |
| `wifi.band_select` | `WifiNetworkInfo.band` (`WifiBand`) | `"ANY"` / `"ONLY_50G"` / `"ONLY_24G"`; omitted when null |
| `pair.user_token` | `PairingInfo.userToken` | the raw `access_token` from `login/finish`, **no `Bearer ` prefix** |
| — | `incorrectSSID`, `incorrectPassword` | `transient`, never serialized (inline UI error flags) |

`"pair"` is omitted in `WIFI_ONLY` mode.

## Rendering

`generateQrCode` renders with ZXing `BarcodeEncoder`, `BarcodeFormat.QR_CODE`, **error correction L**, and
**margin 0**. The code is a square sized to the display width, and the layout adds a 4 dp white border. The screen
stays awake (`FLAG_KEEP_SCREEN_ON`, `window.addFlags(128)`). `getDefaultBrightnessForQrPercent()`
returns `30`, but nothing calls it. The UI hint `tips_tricks_3_desc` tells the user to raise the brightness
manually instead.

## Worked example

```
$ ./tools/pairing/moxie_pair.py --ssid HomeNet --password 's3cr3t!' \
      --band 24g --mode proto --iot-endpoint 0 \
      --secret-key-hex 000102...1f --out qr.png
QR payload (proto): PACgdIb21lTmV0EgdzM2NyM3QhIiAAAQ...OAJAAA=
```

Decoding it gives `ssid=HomeNet, password=s3cr3t!, band=ONLY_24G, secret_key=00..1f, iot_endpoint=0`.

---
📖 [Phone-side index](README.md) · [Robot-side QR grammar](../protocol/qr-commands.md) · [Reverse-engineering index](../README.md)
