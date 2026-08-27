# 03 — Pairing, WiFi Provisioning, QR Generation & Robot Control

> **📖 About this document.** This is a *clean-room* description of the **original** Moxie parent app
> (`com.embo.embodied.parent` v2.2.2), written by reverse-engineering it. **The decompiled app is NOT
> included in this repository, and you do not need it.** Any file or class names below (e.g.
> `api/Config.java`, `pair_moxie/…`, or paths shown as `<decompiled>/…`) are references into the
> *original app's own internal structure* — they document *where a behavior lived in the app so the
> protocol is reproducible*, and are **not** files in this repo. Our actual implementation lives in
> [`server/`](../../server/), [`tools/`](../../tools/), and [`mqtt/`](../../mqtt/).

Reverse-engineered from the decompiled Embodied Parent App (`EmbodiedParentApp/v2.2.2`, jadx output at
`work/jadx-out/sources/com/embo/embodied/parent`). Everything below is quoted or derived from real
decompiled code. Line numbers refer to the jadx output files.

---

## 0. Executive summary (what a replacement server must do)

The Moxie robot is provisioned **entirely out-of-band via a QR code shown on the phone screen**. There is
no BLE, no SoftAP, no local socket. The phone never talks to the robot. The flow is:

1. Phone registers a pairing intent with the cloud: `POST /api/pairing-info?id=<sha256hex>&restore=…&user-id=…&child-id=…`
2. Phone renders a QR containing **WiFi credentials + the raw 32-byte libsodium signing key** (proto mode)
   or **WiFi credentials + the OAuth access token** (legacy JSON mode).
3. Robot's camera scans the QR, joins WiFi, and calls home to Embodied's cloud, presenting the same
   secret (or its SHA-256) so the backend can bind robot ⇄ user account.
4. Phone polls `GET /api/users/me?include=…robots…` every 2 s (max 60 tries) until a `robots` relationship
   appears, then `GET /api/robots/{id}?include=restore,robot-setting`.

So the replacement server needs, at minimum: OAuth token endpoint, `users/me`, `pairing-info`,
`robots/{id}`, and whatever endpoint the *robot firmware* hits after joining WiFi (not visible in this
app — see §9 "Gaps").

**Base URL (production):** `https://client-service-api.embodied.com/api/`
(`Config.getBaseUrl(true)`, `Config.java:337-343`).

| BuildMode | Base URL |
|---|---|
| STAGING | `https://client-service-staging-api.embodied.com/` |
| PRODUCTION | `https://client-service-api.embodied.com/` |
| DEVELOP | `https://client-service-develop-api.embodied.com/` |
| CHINA | `https://client-service-cn-api.embodied.com/` |
| HONG_KONG | `https://client-service-hk-api.embodied.com/` |

`getBaseUrl(true)` appends `api/`; `getBaseUrl(false)` does not (used only for the Privo web view).

OAuth client IDs (`RequestManager.getClientId()`, lines 178-196):

```
STAGING     GjnNt7QqHoiRMkciyDoTEAWug6vhpyV6LtaHn2m7hJyxNaXCduAc9Yk9CoMpKZLv
PRODUCTION  1tjzBncMMwsTl0K-ORtwUXcYV5GH-LZh7YGvQNsDAD4
DEVELOP     DeJ8ykK4pM8G6qVe3gFLJzrpH6QfbRW3CKjdCT499maesa8r8vNAgFWzkDcTeXGT
CHINA / HK  AqHSIQcR_Mg0zL_L7VAdUMCXznaXCpRQT18szfGCp4w
```
Client secret: `OKJMOFpcI16R7Mv1GTcyC9rTsuUomd_quZhsLQLGsd4` (CN/HK: `qL_EeFcK6s2de6qcalegLMBmr0zKV1qZ2UgLAmJOjkw`).

All authenticated requests send `Authorization: <token_type> <access_token>` (defaults to `Bearer`,
`TokenResponseModel.getAuth()` line 154) and `User-Agent: EmbodiedParentApp/v2.2.2 android/<os>`
(`RequestManager.initRetrofit`, line 157).

---

## 1. QR mode selection: PAIR_JSON_TOKEN vs PAIR_PROTO_KEY

### 1.1 The decisive finding: **JSON mode is dead code in this build**

`Config.getPairQRMode()` (`api/Config.java:376-381`) is:

```java
public static PairQRMode getPairQRMode() {
    if (PairQRMode.PAIR_PROTO_KEY.ordinal() == ((EmbodiedApplication) EmbodiedApplication.getContext())
            .getPrefs().getInt(SharedKeys.PAIRING_QR_MODE, PairQRMode.PAIR_PROTO_KEY.ordinal())) {
        return PairQRMode.PAIR_PROTO_KEY;
    }
    return PairQRMode.PAIR_PROTO_KEY;
}
```

**Both branches return `PAIR_PROTO_KEY`.** The stored preference `pairing_qr_mode`
(`SharedKeys.PAIRING_QR_MODE = "pairing_qr_mode"`) is read but its value is discarded. There is a setter
`Config.setPairQRMode()` but no caller anywhere in the tree writes a value that can change the outcome.

Conclusion: **v2.2.2 of the app always emits the `PA`+protobuf QR.** The `JSONPairing` / `PairingModel` /
`PairingInfo{user_token}` path is legacy and unreachable. It is almost certainly still accepted by older
robot firmware, and is worth keeping as a fallback in a replacement tool because it is far simpler
(no crypto, just an OAuth token).

### 1.2 Where the branch is taken

`PairMoxieQrCodeFragment.initViews` (line 102):

```java
showPairingView(view, Config.getPairQRMode().equals(Config.PairQRMode.PAIR_PROTO_KEY));
```

`showPairingView(view, isProtoActive)` → `generateQrCode(view, isProtoActive)` (lines 348-379):

```java
String accessToken = Config.getAuthDataModel() != null ? Config.getAuthDataModel().getAccessToken() : "";
boolean z2 = PairMoxieWifiFragment.getPairingMode() == PairingMode.WIFI_ONLY;   // "hidePair"
if (z) {   // proto
    jsonString = new ProtoPairing(PairMoxieWifiFragment.getWifiNetworkInfo(),
                                  CryptoHelper.getInstance().getSigningKey().toBytes())
                 .toQRString(z2,
                     (User.INSTANCE.getData() == null
                      || User.INSTANCE.getData().getAttributes().getIotEndpoint() == null)
                     ? 0 : User.INSTANCE.getData().getAttributes().getIotEndpoint().intValue());
} else {   // json
    jsonString = new JSONPairing(PairMoxieWifiFragment.getWifiNetworkInfo(),
                                 z2 ? null : new PairingInfo(accessToken)).toJsonString();
}
```

Note the second orthogonal switch: **`PairingMode`** (`pair_moxie/PairingMode.java`):

```java
public enum PairingMode { WIFI_ONLY, WIFI_AND_PAIRING }
```

* `WIFI_AND_PAIRING` — full pairing (new robot / re-pair). Passed as intent extra `pairing_mode`.
* `WIFI_ONLY` — "Edit WiFi" for an already-paired robot. The QR then contains **only** WiFi credentials
  and the `hide_pair` flag; no token, no secret key.

Entry points (`BaseActivity.switchToPairMoxieActivity(PairingMode, boolean checkRestoreFromBackup)`,
line 441):

| Caller | Mode | checkBackup |
|---|---|---|
| `MoxieFragment:392` ("Pair Moxie" main button) | WIFI_AND_PAIRING | `true` |
| `MoxieFragment:493` / `:531` ("Edit WiFi" / restore-cancel) | WIFI_ONLY / WIFI_AND_PAIRING | `false` |
| `MoxieFragment:514` (restore "try again" after `createRestore`) | WIFI_AND_PAIRING | `false` |
| `TroubleshootDialog:53`, `BaseActivity:872` | WIFI_ONLY | `false` |

### 1.3 `iotEndpoint` — what it indexes

`api/models/user/UserAttributes.java:37-38`:

```java
@SerializedName("iot-endpoint")
private Integer iotEndpoint;
```

It is a **server-assigned small integer on the *user* record**, returned by `GET users/me`. The app never
interprets it; it copies it verbatim into protobuf field 8 of the QR (and defaults to `0` when the user
record has no value). It is the **only** occurrence of the string `iot` anywhere in the app —
grep for `mqtt|websocket|graphql|greengrass|amazonaws|broker|iot-` across the whole tree returns exactly
one hit (this field).

Interpretation: it is an **index into a table of IoT/MQTT broker endpoints that lives in the robot
firmware** (a shard/region selector — e.g. `0` = default US AWS IoT endpoint, `1..n` = alternates,
plausibly aligning with the CN/HK regional deployments). The robot resolves index → hostname locally; the
cloud only tells the app which number to stamp into the QR. This matters for a replacement server: you
cannot change the broker hostname through this channel, only pick among endpoints the firmware already
knows. **Send `0` unless you have firmware evidence otherwise.**

Also note the byte encoding bug/limit: `allocate.put((byte) i)` — a **raw byte, not a varint**. Values
> 127 would serialize as a negative byte and break protobuf decoding. Practical range: 0–127.

---

## 2. QR payload formats — byte-exact

### 2.1 JSON format (`PairQRMode.PAIR_JSON_TOKEN`, legacy)

`pair_moxie/JSONPairing.java`:

```java
public String toJsonString() {
    return new String(RequestManager.INSTANCE.getGson()
        .toJson(new PairingModel(this.wifi, this.pair)).getBytes(StandardCharsets.UTF_8));
}
```

Gson is a plain `GsonBuilder()` with only a `byte[]`↔Base64 adapter (`api/models/GsonHelper.java`), so:
default field naming from `@SerializedName`, **nulls omitted**, enums serialized by `name()`.

Serialized shape:

```json
{
  "wifi": {
    "ssid": "MyNetwork",
    "password": "hunter2hunter2",
    "is_hidden": false,
    "band_select": "ONLY_24G"
  },
  "pair": { "user_token": "<oauth access_token>" }
}
```

Field sources (`api/models/wifi/`):

| JSON key | Java field | Notes |
|---|---|---|
| `wifi.ssid` | `WifiNetworkInfo.ssid` | trimmed |
| `wifi.password` | `WifiNetworkInfo.password` (`@SerializedName(Config.GRANT_TYPE_PASSWORD)` == `"password"`) | trimmed |
| `wifi.is_hidden` | `WifiNetworkInfo.isHidden` | boolean |
| `wifi.band_select` | `WifiNetworkInfo.band` (`WifiBand` enum) | `"ANY"` / `"ONLY_50G"` / `"ONLY_24G"`; **omitted entirely when null** |
| `pair.user_token` | `PairingInfo.userToken` | raw `access_token`, **no `Bearer ` prefix** |
| — | `incorrectSSID`, `incorrectPassword` | `transient`, never serialized |

`"pair"` is **omitted** in `WIFI_ONLY` mode (`new JSONPairing(wifi, z2 ? null : new PairingInfo(accessToken))`).

### 2.2 Proto format (`PairQRMode.PAIR_PROTO_KEY`, current)

`pair_moxie/ProtoPairing.java` builds the protobuf **by hand** into a `ByteBuffer.allocate(1024)`:

```java
private static final String PROTO_PAIR_HEADER = "PA";

public String toQRString(boolean z /*hidePair*/, int i /*iotEndpoint*/) {
    ByteBuffer allocate = ByteBuffer.allocate(1024);
    allocate.put((byte) 10);                                  // 0x0A  field 1, wiretype 2
    byte[] bytes = this.wifi.getSsid().getBytes(StandardCharsets.UTF_8);
    encodeVarInt(bytes.length, allocate);
    allocate.put(bytes);
    allocate.put(Ascii.DC2);                                  // 0x12  field 2, wiretype 2
    byte[] bytes2 = this.wifi.getPassword().getBytes(StandardCharsets.UTF_8);
    encodeVarInt(bytes2.length, allocate);
    allocate.put(bytes2);
    if (!Config.getBuildMode().equals(Config.BuildMode.PRODUCTION)) {
        allocate.put(Ascii.CAN);                              // 0x18  field 3, varint
        allocate.put((byte) 1);
    }
    if (z) {
        allocate.put((byte) 40);                              // 0x28  field 5, varint
        allocate.put((byte) 1);
    } else {
        allocate.put((byte) 34);                              // 0x22  field 4, wiretype 2 (bytes)
        encodeVarInt(this.secret_key.length, allocate);
        allocate.put(this.secret_key);
    }
    if (this.wifi.isHidden()) {
        allocate.put((byte) 48); allocate.put((byte) 1);      // 0x30  field 6, varint = 1
    }
    if (this.wifi.getBand() != null) {
        if (WifiBand.ONLY_50G.equals(this.wifi.getBand())) {
            allocate.put((byte) 56); allocate.put((byte) 1);  // 0x38  field 7, varint = 1
        } else if (WifiBand.ONLY_24G.equals(this.wifi.getBand())) {
            allocate.put((byte) 56); allocate.put((byte) 2);  // 0x38  field 7, varint = 2
        }
    }
    allocate.put(SignedBytes.MAX_POWER_OF_TWO);               // 0x40  field 8, varint
    allocate.put((byte) i);
    byte[] bArr = new byte[allocate.position()];
    allocate.rewind(); allocate.get(bArr);
    return PROTO_PAIR_HEADER + Base64.encodeToString(bArr, 0);
}
```

Constants decoded: `Ascii.DC2` = 0x12, `Ascii.CAN` = 0x18, `SignedBytes.MAX_POWER_OF_TWO` = 0x40 (64).

**Reconstructed `.proto`:**

```proto
message MoxiePairing {
  string ssid          = 1;  // 0x0A
  string password      = 2;  // 0x12
  bool   dev_mode      = 3;  // 0x18 — emitted ONLY when BuildMode != PRODUCTION, always value 1
  bytes  secret_key    = 4;  // 0x22 — 32-byte libsodium Ed25519 signing key (seed); mutually exclusive with 5
  bool   hide_pair     = 5;  // 0x28 — value 1 when PairingMode == WIFI_ONLY; mutually exclusive with 4
  bool   is_hidden     = 6;  // 0x30 — emitted only when true
  uint32 band_select   = 7;  // 0x38 — emitted only when band != null && band != ANY; 1 = 5GHz-only, 2 = 2.4GHz-only
  uint32 iot_endpoint  = 8;  // 0x40 — always emitted, single raw byte (0..127)
}
```

Field order is always ascending, so a strict decoder is fine.

**Base64 gotcha (important for a reimplementation):**
`Base64.encodeToString(bArr, 0)` uses Android's `Base64.DEFAULT` — i.e. **padded, line-wrapped at 76
chars with `\n`, and a trailing `\n`**. Because a typical payload (SSID + password + 32-byte key + flags,
~60–90 bytes → ~80–120 base64 chars) exceeds 76 characters, the emitted QR string will normally
**contain embedded newlines**. Your generator must reproduce this if the firmware's parser is
whitespace-sensitive; safest is to reproduce it exactly (wrap at 76, `\n` separators, trailing `\n`).

Full QR string = `"PA"` + that base64 blob.

**QR rendering parameters** (`generateQrCode`, lines 361-366):

```java
BarcodeEncoder barcodeEncoder = new BarcodeEncoder();
int displayWidth = Utils.getDisplayWidth(getContext());
HashMap hashMap = new HashMap();
hashMap.put(EncodeHintType.ERROR_CORRECTION, ErrorCorrectionLevel.L);
hashMap.put(EncodeHintType.MARGIN, 0);
Bitmap encodeBitmap = barcodeEncoder.encodeBitmap(str, BarcodeFormat.QR_CODE, displayWidth, displayWidth, hashMap);
```

ZXing, format QR_CODE, **error correction level L**, **margin 0**, square, sized to display width, then
padded with a 4 dp white border in the layout. Screen is kept awake (`window.addFlags(128)` =
`FLAG_KEEP_SCREEN_ON`). `getDefaultBrightnessForQrPercent()` returns `30` but is not wired up in this
build (the UI hint `tips_tricks_3_desc` tells the user to raise brightness manually).

### 2.3 The `secret_key` — how it is derived

`CryptoHelper.getInstance().getSigningKey().toBytes()` — an `org.libsodium.jni.keys.SigningKey`.
Derivation chain (`api/crypto/CryptoHelper.java`, `api/crypto/RecoveryKey.java`):

```java
// CryptoHelper.generateEncryptionKeyPair(byte[] seed)
SigningKey signingKey = new SigningKey(seed);       // seed = 32 bytes
this.cryptoSecretBox = new SecretBox(signingKey.toBytes());
this.encryptionKeyPair = new KeyPair(seed);          // crypto_box_curve25519xsalsa20poly1305_seed_keypair
Config.storeClientPublicKey(Encoder.encodeAsString(keyPair.getPublicKey().toBytes()));
```

and the seed itself (`RecoveryKey.hash`):

```java
byte[] bArr  = new byte[crypto_box_seedbytes()];          // 32
byte[] bArr2 = new byte[crypto_pwhash_saltbytes()];       // 16 — ALL ZEROS, never filled!
Sodium.crypto_pwhash(bArr, 32, passphraseBytes, len, bArr2,
                     crypto_pwhash_opslimit_interactive(),
                     crypto_pwhash_memlimit_interactive(),
                     crypto_pwhash_alg_default());
```

So: **seed = Argon2id(passphrase, salt = 16 zero bytes, ops = OPSLIMIT_INTERACTIVE, mem = MEMLIMIT_INTERACTIVE, out = 32 bytes)**.
The salt is a zero-filled array that is never populated — the derivation is fully **deterministic from
the recovery passphrase alone**. This is reproducible offline with any libsodium binding.

The passphrase is an 8-word EFF-diceware phrase (`api/crypto/diceware/Passphrase.java`,
`keyPhraseLength = 8`, wordlist asset `eff_short_wordlist_1.txt`). The app stores a compact *code* form
of it in SharedPreferences under key `ppcrk` (`Config.PASS_PHRASE_CODE`), not the phrase itself.

`ProtoPairing.serectHashFromKey()` [sic, typo in original] is the SHA-256 of those same key bytes,
lowercase hex:

```java
public static String serectHashFromKey(byte[] bArr) {
    MessageDigest messageDigest = MessageDigest.getInstance("SHA-256");
    messageDigest.update(bArr);
    return bytesToHexString(messageDigest.digest());   // lowercase hex, zero-padded per byte
}
```

**The robot gets the raw key in the QR; the cloud only ever gets its SHA-256.** That is the binding
proof: robot presents key (or a proof of it) → cloud matches against the registered hash → robot is
attached to that user + child.

---

## 3. Full pairing sequence

### 3.1 Sequence diagram

```
 PHONE (app)                       CLOUD (client-service-api)                ROBOT
 -----------                       --------------------------                -----
 [MoxieFragment] "Pair Moxie"
      |
      v
 PairMoxieActivity
   intent extras: pairing_mode = WIFI_AND_PAIRING
                  check_restore_from_backup = true
      |
      | if user.attributes["has-backups"] == true && checkBackup
      v
 RestoreMoxieFragment  (Yes/No: "restore from backup?")
   -> Config.userWantsToRestoreFromBackup = true|false
      |
      v
 PairMoxieWifiFragment
   user enters SSID / password / hidden / band
   setupWifiNetworkInfo() -> static WifiNetworkInfo
      |
      | goToPairing():  PROTO mode && pairingMode != WIFI_ONLY
      v
   registerForPairing()
   POST /api/pairing-info ------------------> creates pending pairing record
        ?id=<sha256hex(signing_key)>          keyed by the key hash
        &restore=true|false
        &user-id=<user id>
        &child-id=<active child id>
        Authorization: Bearer <tok>
                       <---------------------- 200/201
      | (on failure: dialog "Problem connecting to Embodied" / Retry|Skip)
      v
 PairInstructionFragment  (step 2/5 — "look for QR box on Moxie's face")
      |
      v
 PairMoxieQrCodeFragment  (step 3/5)
   generateQrCode() -> "PA"+base64(proto{ssid,password,secret_key,...,iot_endpoint})
   render QR, FLAG_KEEP_SCREEN_ON
   start AttemptCounter(max=60, delay=2000ms)
      |                                                          camera scans QR
      |                                                                |
      |                                                     joins WiFi (SSID/pw/band/hidden)
      |                                                                |
      |                                       <--- robot registers with cloud, presenting
      |                                            the secret key -> cloud matches sha256
      |                                            against the pending pairing-info record
      |                                            -> robot attached to user+child
      |
      |--- every 2 s: GET /api/users/me?include=mobile-devices,robots.restore,
      |                    robots.robot-setting,child,identity-verification
      |                                        <---- once bound, response contains a
      |                                              relationships.robots[] entry
      v
   (WIFI_AND_PAIRING + PROTO)
   GET /api/robots/{id}?include=restore,robot-setting
      |                                        <---- 200 robot object
      v
   pairingState = success
   -> RobotInfoViewModel.getOtaStatus()  GET /api/robots/{id}/ota_status
        if OTA_IN_PROGRESS -> MoxieOtaStatusFragment
        else               -> SetupSpaceFragment / MoxieConnectedFragment
```

### 3.2 Step-by-step, with code

**Step A — RestoreMoxieFragment (optional gate).** `PairMoxieActivity.initViews` (lines 46-67):

```java
if (PairingMode.WIFI_AND_PAIRING.equals(this.pairingMode)
        && User.INSTANCE.getData() != null
        && User.INSTANCE.getData().getAttributes().getHasBackups().booleanValue()
        && this.checkBackup) {
    RestoreMoxieFragment restoreMoxieFragment = new RestoreMoxieFragment();
    ... onSkip()    -> Config.userWantsToRestoreFromBackup = false; showPairingWifiPage();
    ... onRestore() -> Config.userWantsToRestoreFromBackup = true;  showPairingWifiPage();
}
showPairingWifiPage();
```

**Step B — WiFi entry.** `PairMoxieWifiFragment.setupWifiNetworkInfo()` (lines 256-267) builds the static
`WifiNetworkInfo`. Validation in `m553x92f1b962` (lines 183-201):
* empty password → confirmation dialog *"Are you sure your network has no password?"* (`network_no_password_title`), then proceed;
* 1–7 chars → error *"The password should be at least 8 characters or longer."* (`MINIMUM_PASSWORD_LENGTH = 8`);
* ≥8 chars → proceed.
SSID auto-fill uses `Utils.getConnectedWifiSSID()` and requires `ACCESS_FINE_LOCATION` + network location
provider on (see `checkLocationSettings`, `onRequestPermissionsResult`).

**Step C — registerForPairing.** `PairMoxieWifiFragment.goToPairing()` (lines 270-303):

```java
if (Config.getPairQRMode().equals(Config.PairQRMode.PAIR_PROTO_KEY) && getPairingMode() != PairingMode.WIFI_ONLY) {
    Log.i(TAG, "First time proto pairing registration");
    registerPairing(...)      // -> switchToPairingInstructionsPage() on success
}
switchToPairingInstructionsPage();
```

```java
private void registerPairing(ResponseCallback responseCallback) {
    RequestManager.INSTANCE.registerForPairing(
        ProtoPairing.serectHashFromKey(CryptoHelper.getInstance().getSigningKey().toBytes()),
        Config.userWantsToRestoreFromBackup,
        User.INSTANCE.getData().getId(),
        User.INSTANCE.getData().getRelationships().getChild().getData().getId(),
        responseCallback);
}
```

`RequestManager.registerForPairing` (lines 580-596):

```java
HashMap hashMap = new HashMap();
hashMap.put("id", id);                                       // sha256 hex of signing key
hashMap.put("restore", String.valueOf(userWantsToRestoreFromBackup));  // "true"/"false"
hashMap.put("user-id", user_id);
hashMap.put("child-id", child_id);
apiService.pairingInfo(authDataModel.getAuth(), hashMap)
```

`APIService`: `@POST(Config.API_PAIRING_INFO) pairingInfo(@Header("Authorization") String, @QueryMap Map<String,String>)`,
`API_PAIRING_INFO = "pairing-info"`. **`@QueryMap` ⇒ these are query-string parameters, and the body is
empty.** Effective request:

```
POST /api/pairing-info?id=<64 hex chars>&restore=false&user-id=<uuid>&child-id=<uuid> HTTP/1.1
Authorization: Bearer <access_token>
User-Agent: EmbodiedParentApp/v2.2.2 android/<ver>
Content-Length: 0
```

Success = HTTP 200/201/204 (`ResponseManager.onResponseCallback`). Failure surfaces the dialog
`register_pairing_error_title` = *"Problem connecting to Embodied"* /
`register_pairing_error_desc` = *"It's possible that your network is down, or Embodied's servers may be having trouble."*
with Retry / Skip. **Skip still lets the user proceed to the QR**, which strongly implies the pairing-info
POST is a convenience/pre-registration and the robot's own callback carries enough information on its own.

**Step D — QR shown, polling begins.** `showPairingView` (lines 193-213):

```java
AttemptCounter attemptCounter = new AttemptCounter(new AttemptCounter.Callback() {
    public void update() {
        if (PairMoxieQrCodeFragment.this.pairingState == PairingState.pairing) return;
        PairMoxieQrCodeFragment.this.checkUserInfoForRobotPairing(z);
    }
    public void fail() {
        pairingState = PairingState.unknown;
        showIncorrectPairingDialog();
    }
});
this.attemptCounter = attemptCounter;
attemptCounter.setAttemptMaxCount(60);
attemptCounter.attempt();
```

`AttemptCounter` (`utils/AttemptCounter.java`): `DEFAULT_DELAY_MSEC = 2000`, count set to 60 here →
**poll every 2 s for up to ~120 s**, then `fail()` → `PairingErrorDialog`.
Also: `TROUBLE_DELAY_MSEC = 10000` — after 10 s a "Having trouble?" button appears.
Polling is suspended in `onPause()` and restarted in `onResume()`.

**Step E — the poll itself.** `checkUserInfoForRobotPairing` → `getViewModel().fetchUserInfo(...)` →
`RequestManager.fetchUser`:

```
GET /api/users/me?include=mobile-devices,robots.restore,robots.robot-setting,child,identity-verification
```
(`RequestManager.USER_INCLUDE`, line 76.)

**Step F — success detection.** `PairMoxieQrCodeFragment$3.onSuccess` (lines 224-307). Two distinct
success criteria:

*WIFI_ONLY* (lines 270-286):

```java
String wifiSSID = Robot.INSTANCE.getData().getAttributes().getWifiSSID();
if (Robot.INSTANCE.getData() != null && wifiSSID != null
        && wifiSSID.equals(PairMoxieWifiFragment.getWifiNetworkInfo().getSsid())) {
    if (System.currentTimeMillis()
            - MyDate.formatDate(Robot.INSTANCE.getData().getAttributes().getLastSeen(), true).timestamp
        <= 300000) {                                  // WIFI_VALIDITY_TIMEOUT = 5 minutes
        Log.i(TAG, "success on edit WIFI");
        pairingState = PairingState.pairing; onConnectedToMoxie();
        if (!isProtoActive) { updateKeysInServer(responseCallback); return; }
        pairingState = PairingState.success; finish(); return;
    }
}
```

i.e. **the robot's `wifi-ssid` attribute must equal the SSID the user typed AND `last-seen-at` must be
within 5 minutes of now.** So `robots/{id}.attributes["wifi-ssid"]` and `["last-seen-at"]` are the
server-side liveness signals your replacement backend must maintain.

*WIFI_AND_PAIRING* (lines 287-301):

```java
} else if (robots != null && robots.getData() != null && !robots.getData().isEmpty()) {
    Log.i(TAG, "success on pairing Moxie");
    pairingState = PairingState.pairing; onConnectedToMoxie();
    if (this.val$isProtoActive) {
        Log.i(TAG, "Requesting Robot to complete Pairing in PROTO mode.");
        getRobot(robots.getData().get(0).getId());          // GET robots/{id}
        return;
    } else {
        Log.i(TAG, "Updating keys in server in JSON mode.");
        updateKeysInServer(responseCallback);               // PUT secret-key-collection
        return;
    }
}
pairingState = PairingState.unknown;
attemptCounter.attempt();                                    // keep polling
```

i.e. **success = `users/me` now returns a non-empty `relationships.robots.data[]`.**

The trailing log string *"Requesting Robot to complete Pairing in PROTO mode."* is telling: in proto mode
the app's `GET robots/{id}` is what signals the backend that the phone has observed the pairing, letting
the backend complete/commit it.

**Step G — JSON-mode-only tail.** In JSON mode the flow goes through `updateKeysInServer` →
`CryptoManager.updateKeys` → `PUT /api/secret-key-collection`; then in `WIFI_AND_PAIRING` it does
`POST /api/robots/{id}/restores` (`createRestore`) followed by `GET /api/robots/{id}`. In PROTO mode
neither the key upload nor `createRestore` happens here — the restore intent was already conveyed as the
`restore=` query param on `pairing-info`, and the key was conveyed inside the QR.

**Step H — post-success.** `finish()` → `showFinalSetupPagesIfNeeded()`:

```java
RobotInfoViewModel.INSTANCE.getOtaStatus(new OtaStatusCallback() {
    public void onSuccess(OtaStatusModel m) {
        Robot.INSTANCE.setOtaStatus(m);
        if (Robot.INSTANCE.needToDisplayOtaStatus()) switchToOTAStatusPage();
        else runnable.run();
    }
    public void onFail() { runnable.run(); }
});
runnable.run();     // NOTE: bug — always runs, so the callback path can double-fire
```

Then `SetupSpaceFragment` (WIFI_AND_PAIRING success) or the `PairingCallback.onFinish(success)` /
`switchToMainActivity()`.

### 3.3 Error / recovery UI

`PairingErrorDialog` options → `editWifi()` (pop 2 fragments back to WiFi entry), `instruction()` (pop 1),
`finishSetup()`, `tryAgain()` (reset the AttemptCounter and resume polling), `needHelp()` (opens
`Config.URL_TROUBLESHOOTING_QR_PAIRING`).

`PairingHavingTroubleDialog` sets `WifiNetworkInfo.incorrectSSID` / `incorrectPassword` (transient flags,
used only to show inline red error text on the WiFi form) — these are never sent to the server.

---

## 4. WiFi provisioning — exactly what the robot needs

Model: `api/models/wifi/WifiNetworkInfo.java`.

| Field | Type | JSON key | Proto field | Constraint |
|---|---|---|---|---|
| SSID | String | `ssid` | 1 (`0x0A`, UTF-8 string) | Required, non-empty (Next button disabled when empty). Trimmed. Case sensitive (UI note `wifi_ssid_note`: *"Note: SSID/Wi-Fi Name is Case Sensitive"*). |
| Password | String | `password` | 2 (`0x12`, UTF-8 string) | Trimmed. Empty allowed after an "are you sure" dialog. Otherwise **≥ 8 characters** (`MINIMUM_PASSWORD_LENGTH = 8`). WPA-PSK assumed; no WPA-Enterprise / captive-portal support anywhere. |
| Hidden | boolean | `is_hidden` | 6 (`0x30`, varint 1) | Only emitted in proto when `true`. |
| Band | enum | `band_select` | 7 (`0x38`, varint) | `ANY` (default, **omitted from proto**), `ONLY_50G` → **1**, `ONLY_24G` → **2**. |

`WifiBand` (`pair_moxie/WifiBand.java`): `ANY, ONLY_50G, ONLY_24G`. UI strings:
`band_any` = "Any", `band_only_50g` = "Only 5.0GHz", `band_only_24g` = "Only 2.4GHz", surfaced through
`R.array.wireless_frequency_selector` under a collapsed "Advanced Wi-Fi Settings" section
(`advanced_wifi_settings`), default selection index 0 (= Any).

**Band constraints:** the app imposes **none**. There is no code anywhere refusing 5 GHz, and the default
is `ANY`. The band selector is purely a *hint to the robot* about which band to prefer/restrict when the
SSID exists on both. So the robot hardware clearly supports both 2.4 and 5 GHz; the option exists to work
around dual-band routers that share one SSID. Note the mapping is **1 = 5 GHz, 2 = 2.4 GHz** (not the
enum's declaration order, which is ANY=0, ONLY_50G=1, ONLY_24G=2 — coincidentally the same numbers for
the two non-ANY cases).

There is **no** field for: security type, EAP identity, static IP, DNS, proxy, or country code. The robot
must infer all of that.

---

## 5. Robot control API

All under base `https://client-service-api.embodied.com/api/`, all with
`Authorization: <token_type> <access_token>`. Definitions in `api/APIService.java` + `api/Config.java`.

### 5.1 Endpoint table

| Method | Path | APIService method | Body / params | Notes |
|---|---|---|---|---|
| GET | `robots/{id}?include=restore,robot-setting` | `getRobot` | — | `ROBOT_INCLUDE = "restore,robot-setting"` |
| PUT | `robots/{id}` | `updateRobot` | `UpdateRobotModel { "robot": <RobotAttributes> }` | **PUT, not PATCH** |
| PUT | `robots/{id}` | `updateRobotSettings` | `UpdateRobotSettingsModel { "robot-settings": <RobotSettingsAttributes> }` | same path, different wrapper key |
| DELETE | `robots/{id}` | `unpairRobot` | — | plain unpair |
| DELETE | `robots/{id}?rfs=1` | `unpairRobotWithRestoreFactory` | — | `API_DELETE_ROBOT_RESTORE`; unpair **+ factory reset** |
| POST | `robots/{id}/wakeup` | `wakeupMoxie` | — | `API_WAKE_UP_MOXIE` |
| POST | `robots/{id}/reboot` | `rebootRobot` | — | `API_REBOOT_ROBOT` |
| GET | `robots/{id}/ota_status` | `getOtaStatus` | — | `API_OTA_STATUS` |
| POST | `robots/{id}/set-language` | `robotSetLanguage` | `RobotSetLanguageModel` | `API_ROBOT_SET_LANGUAGE` |
| POST | `robots/{id}/restores` | `createRestore` | `RestoreRobotModel { "restore": { "status": "..." } }` | `API_CREATE_RESTORE_ROBOT` |
| POST | `pairing-info` | `pairingInfo` | query: `id`, `restore`, `user-id`, `child-id` | see §3 |
| PUT | `secret-key-collection` | `updateKeys` | `UpdateKeysModel` | see §7 |
| GET | `network-tests` | `getNetworkTests` | — | returns the test plan |
| POST | `network-tests` | `setNetworkTests` | `TestResult { "result": SetNetworkTestModel }` | uploads results |
| GET | `language-support` | via `helpApi("language-support")` | — | actually `GET help/{path}` |
| GET | `users/me?include=…` | `fetchUser` | — | the pairing poll |
| POST | `oauth/token` | `token` / `updateToken` | form-urlencoded `client_id`, `grant_type`, `username`/`password` or `refresh_token` | |

### 5.2 `GET robots/{id}` response (JSON:API shaped)

`RobotDataModel { data: IncludedRobot, included: [IncludedModel] }`.

`IncludedRobot { id, type ("robots"), attributes: RobotAttributes, relationships: { "robot-setting": {...}, "restore": {...} } }`

`RobotAttributes` (`api/models/robot/RobotAttributes.java`):

```
"android-version"          String
"battery-level"            Float   (default 0.0)
"device-settings"          { "props": { "app-language-support", "audio-wake", "debug",
                                        "playzone", "rewards-support", "schedule-sensitive",
                                        "touch-wake", "wake-alarms", "wake-button" } }   // all String
"embodied-robot-id"        String
"is-online"                boolean
"last-backup-at"           String (timestamp)
"last-seen-at"             String (timestamp)   <-- pairing liveness check
"last-updated-at"          String
"mode"                     enum { idle, active, sleep }
"ota-required"             Boolean (default false)
"ota-status"               enum { idle, pending, uploading, downloading, flashing, finalizing, complete }
"public-key"               String (base64, libsodium curve25519 public key)
"robot-firmware-version"   String
"robot-version"            String
"serial-number"            String
"telehealth-supported"     Boolean (default false)
"wifi-ssid"                String                <-- pairing liveness check
```

`included[]` entries of `type == "robot-settings"` carry `RobotSettingsAttributes`:

```
"audio-volume"                Float
"audio-wake-set"              enum { off, low, high }
"screen-brightness"           Float
"privacy-mode-enabled"        Boolean
"touch-wake-enabled"          Boolean
"wake-button-enabled"         Boolean
"alarms"                      { "enabled": Boolean, "wakes": [ { "enabled": Boolean,
                                                                 "days": [int], "time": "HH:mm" } ] }
"weekday-bedtime-enabled"     Boolean
"weekday-bedtime-starts-at"   "HH:mm"
"weekday-bedtime-ends-at"     "HH:mm"
"weekend-bedtime-enabled"     Boolean
"weekend-bedtime-starts-at"   "HH:mm"
"weekend-bedtime-ends-at"     "HH:mm"
```

and `type == "restores"` carry `RestoreAttributes { "status": String, "created-at": String, "restore-type": enum }`.

### 5.3 `PUT robots/{id}` (update)

Two variants share the path; the *wrapper key* distinguishes them.

```json
// updateRobot  — UpdateRobotModel
{ "robot": { "<any RobotAttributes field>": value } }

// updateRobotSettings — UpdateRobotSettingsModel  (@SerializedName(Robot.SETTINGS_TYPE) == "robot-settings")
{ "robot-settings": { "audio-volume": 0.7, "screen-brightness": 0.5, "privacy-mode-enabled": false, ... } }
```

Response is the same `RobotDataModel` shape and is fed back through `RobotInfoViewModel.updateData()`.

### 5.4 `POST robots/{id}/wakeup`

Response `WakeupMoxieResponseModel`:

```json
{ "code": "...", "title": "...", "body": "...", "error": "..." }
```

Consumed by `RobotInfoViewModel.Companion.wakeupRobotRequest(WakeUpMoxieCallback)`; the app shows
`title`/`body` in a dialog. `error` non-null indicates failure even on HTTP 200. No request body.

### 5.5 `POST robots/{id}/reboot`

Response `RebootMoxieResponseModel`:

```json
{ "code": "...", "title": "...", "body": "..." }
```

No request body.

### 5.6 `GET robots/{id}/ota_status`

Response `OtaStatusModel`:

```json
{
  "status":    "idle|pending|uploading|downloading|flashing|finalizing|complete",
  "percent":   0-100,
  "remaining": "…human readable…",
  "code":      "...",
  "timestamp": 1690000000
}
```

`Robot.setOtaStatus()` composes a UI string; `Robot.needToDisplayOtaStatus()` returns true only when
`eStatus == OTA_IN_PROGRESS`, which requires `attributes["ota-required"] == true` **and**
`ota-status ∉ {idle, complete}` (`Robot.getMoxieStatus()`, lines 229-256).

### 5.7 `POST robots/{id}/set-language`

Body `RobotSetLanguageModel` (note: **snake_case here, unlike the dashed JSON:API attributes**):

```json
{ "input_language_id": "...", "output_language_id": "...", "output_voice_id": "..." }
```

The candidate IDs come from `GET help/language-support` → `LanguageSupportModel`
(`InputLanguage` / `OutputLanguage` / `VoiceItem`). The robot id is taken from `Robot.INSTANCE.getData().getId()`.

### 5.8 `network-tests`

`GET network-tests` → `GetNetworkTestModel`:

```json
{
  "access_tests":    [ { "name": "...", "address": "host", "port": 443, "cycles": 3 } ],
  "bandwidth_tests": [ { "name": "...", "download_from": "https://…", "download_cycles": 3,
                         "upload_to": "https://…", "upload_cycles": 3, "upload_size": 1048576 } ]
}
```

`POST network-tests` body `TestResult`:

```json
{ "result": {
    "Access_results":    [ { "name": "...", "ping_success": 1, "ping_time": 12.3 } ],
    "bandwidth_results": [ { "name": "...", "downstream": 24.5, "upstream": 3.1 } ],
    "environment":       { "wifi_ssid": "...", "wifi_band": "...", "bearer": "..." }
} }
```

(Note the capital `A` in `"Access_results"` — that's literal, `@SerializedName("Access_results")`.)

Response `TestResultResponseModel { code, id, message, title }`.

The *phone* runs these tests (`main/account/help/NetworkTest.java` — raw `Socket` connect for access tests,
Retrofit `@GET @Url` / `@POST @Url` for bandwidth). **The host/port list is entirely server-supplied**, so
the actual Embodied infrastructure hostnames the robot needs are *not recoverable from the APK*. A
replacement server can return an empty list or its own hosts. UI warning string:
`network_test_connect_wifi_warning` = *"Your phone is currently not connected to Wi-Fi. Please make sure to use the same network as Moxie."*

---

## 6. Restore flow

There are **two distinct things both called "restore"** in this codebase. Do not conflate them.

### 6.1 Data restore (`RestoreMoxieFragment` — cloud backup of the child's data)

`pair_moxie/RestoreMoxieFragment.java` is only 75 lines and contains **no crypto and no network calls**.
It is a plain Yes/No screen with `confirmButton` → `RestoreCallback.onRestore()` and `declineButton` →
`onSkip()`. Its entire effect is setting one global flag:

```java
public void onSkip()    { Config.userWantsToRestoreFromBackup = false; ... showPairingWifiPage(); }
public void onRestore() { Config.userWantsToRestoreFromBackup = true;  ... showPairingWifiPage(); }
```

Gate to show it (`PairMoxieActivity:46`): `pairingMode == WIFI_AND_PAIRING && user.attributes["has-backups"] == true && checkBackup`.

That flag then travels down two different paths:

* **PROTO mode**: as the `restore=true|false` **query parameter on `POST pairing-info`**
  (`PairMoxieWifiFragment.registerPairing`). No separate restore record is created by the app.
* **JSON mode**: after the key upload succeeds, `RequestManager.createRestore(robotId, userWantsToRestoreFromBackup, cb)`:

```java
Robot.RestoreStatus restoreStatus = userWantsToRestoreFromBackup ? Robot.RestoreStatus.initiated
                                                                 : Robot.RestoreStatus.declined;
apiService.createRestore(auth, id, new RestoreRobotModel(new RestoreRobotStatus(restoreStatus.name())));
```

⇒ `POST /api/robots/{id}/restores` with body `{"restore":{"status":"initiated"}}` or `{"restore":{"status":"declined"}}`.

`Robot.RestoreStatus = { initiated, declined, failed, succeeded }`,
`Robot.RestoreType = { switch_child, new_child, restore, pairing }`.

Restore progress is then observed through the `restores` include on `users/me` / `robots/{id}`:
`Robot.getRestoreStatus()` maps `status == "failed"` → `MoxieStatus.RESTORE_FAILED`,
`status == "initiated"` → `MoxieStatus.RESTORE_IN_PROGRESS`.

Retry path (`MoxieFragment.onRestoreTryAgainClicked`) → `BaseActivity.createRestoreRequest()` → same
`POST robots/{id}/restores` → `fetchUserInfo()` → re-enter `PairMoxieActivity(WIFI_AND_PAIRING, false)`.

### 6.2 Key restore (`recovery_key/EnterRecoveryKeyFragment` — regenerate the signing key)

**This** is the "recovery passphrase → regenerate signing key" flow, and it lives outside `pair_moxie/`.
`recovery_key/EnterRecoveryKeyFragment.onSubmitClicked()`:

```java
String trim = this.mBinding.enterRecoveryKeyEdit.getText().trim();
KeyPair generateKeyPair = CryptoHelper.getInstance().generateKeyPair(trim);
if (User.INSTANCE.getData() != null
        && !Encoder.encodeAsString(generateKeyPair.getPublicKey().toBytes())
                .equals(User.INSTANCE.getData().getAttributes().getPublicKey())) {
    this.mBinding.enterRecoveryKeyEdit.showErrorText(true);   // wrong passphrase
    return;
}
Config.storePassPhraseCode(Passphrase.getInstance().getCodeFromPassphrase(trim));
getBaseActivity().runActionAfterSetupRecoveryKey();
```

Verification is **entirely local**: derive seed (Argon2id, zero salt) → curve25519 keypair → base64 the
public key → compare against `users/me.attributes["public-key"]`. No network round trip. If it matches,
the phone now holds the same 32-byte signing key it had before, so the QR it generates will produce the
same SHA-256 the cloud already knows.

Alternative: *"Continue without recovery key"* → `Config.storePassPhraseCode(null)` → forced through
`ExportRecoveryKeyFragment` (generate & display a **new** 8-word phrase), which changes the signing key
and therefore requires re-pairing / re-uploading `secret-key-collection`.

The keypair is lazily restored on every `users/me` fetch (`UserInfoViewModel.fetchUserInfo`, line 48):

```java
if (!TextUtils.isEmpty(publicKey) && CryptoHelper.getInstance().getKeyPair() == null
        && Config.getPassPhraseCode() != null) {
    CryptoHelper.getInstance().restoreKeyPair(publicKey);
}
```

### 6.3 How restoring differs from first pairing — summary

| | First pairing | Restore-from-backup | Edit WiFi (WIFI_ONLY) |
|---|---|---|---|
| `PairingMode` | WIFI_AND_PAIRING | WIFI_AND_PAIRING | WIFI_ONLY |
| `RestoreMoxieFragment` shown | no (unless has-backups) | **yes** | no |
| `Config.userWantsToRestoreFromBackup` | false | **true** | n/a |
| `pairing-info?restore=` | `false` | **`true`** | *not called at all* |
| QR contents | ssid+pw+**secret_key**+iot | same | ssid+pw+**hide_pair=1** (no key, no token) |
| Success criterion | `users/me` gains a `robots[]` | same | robot `wifi-ssid` matches **and** `last-seen-at` within 5 min |
| Signing key | freshly generated or restored from passphrase | usually restored from passphrase (so hash matches prior registration) | unchanged |

### 6.4 Unpair

`BaseActivity.unpairMoxie()` offers two buttons: **Unpair** → `DELETE robots/{id}`;
**Restore factory settings** (`restore_factory_settings`, red) → `DELETE robots/{id}?rfs=1`.
`RobotInfoViewModel.unpairRobotRequest()` then clears local `Robot.INSTANCE`.

---

## 7. Crypto & `secret-key-collection` (JSON-mode key upload)

`CryptoManager.encryptSymmetricKeyToPublicKeys()` (`api/CryptoManager.java`):

1. Sanity check: local derived public key must equal `users/me.attributes["public-key"]`; otherwise abort
   with the log *"derived public key does not match current user's public key, which is usually because
   the user's public key was reset on a different device. User will need to re-authenticate."*
2. `gatherPublicKeys()` collects **two** base64 public keys: `users/me.attributes["public-key"]` and
   `robots/{id}.attributes["public-key"]`.
3. For each, `crypto_box_seal(signingKeyBytes, thatPublicKey)` (libsodium sealed box, `SealBox.encrypt`).
4. Build a `JsonObject` mapping `base64(publicKey) → base64(sealedSigningKey)`.

Uploaded as `PUT /api/secret-key-collection` with body (`UpdateKeysModel` / `SecretKeysIndexedByPublic`):

```json
{ "secret_key_collection": {
    "secret-keys-indexed-by-public-keys": {
      "<base64 user public key>":  "<base64 crypto_box_seal(signing_key)>",
      "<base64 robot public key>": "<base64 crypto_box_seal(signing_key)>"
    }
} }
```

Purpose: the robot fetches its sealed copy from the cloud and opens it with its own secret key, obtaining
the same 32-byte symmetric/signing key the phone has — the shared secret used for the encrypted
transcript/insight payloads. **In PROTO mode this is unnecessary because the key is handed to the robot
directly in the QR** — which is exactly why the app skips `updateKeysInServer` on the proto path.

Symmetric envelope used elsewhere (`SecretBox`): XSalsa20-Poly1305, output = `nonce || crypto_secretbox_easy(...)`,
key = the 32-byte signing key bytes, base64 via `Encoder` (`Base64.NO_WRAP`, flag 2).

App-level status codes (`Config`): `111` TOKEN_UPDATED (triggers automatic retry), `114` KEY_UPDATE_ERROR,
`115` KEY_UPDATED (already up to date), `113` NO_DATA, `116` NO_NETWORK, `117` TOKEN_FAILED.

---

## 8. What the robot needs from the cloud after WiFi connects

Evidence from this APK is indirect — **the parent app never speaks to the robot** — but it constrains the
answer sharply:

1. **The robot self-registers.** The only thing the QR gives it is WiFi credentials + a 32-byte secret +
   an `iot_endpoint` integer. Everything else (its account binding, child profile, settings, OTA) must be
   pulled from the cloud after it joins. The cloud must accept a robot-initiated registration that
   presents the secret (or a proof of it) and match it against the `sha256(secret)` recorded by
   `POST pairing-info?id=…`.
2. **Server-side state the app depends on and therefore the robot must be able to set:**
   `robots/{id}.attributes["is-online"]`, `["last-seen-at"]`, `["wifi-ssid"]`, `["battery-level"]`,
   `["mode"]`, `["ota-status"]`, `["ota-required"]`, `["public-key"]`, `["serial-number"]`,
   `["robot-firmware-version"]`, `["android-version"]`, `["embodied-robot-id"]`, `["device-settings"]["props"]`.
   The `props` map is a **feature-flag dictionary** (`app-language-support`, `audio-wake`, `debug`,
   `playzone`, `rewards-support`, `schedule-sensitive`, `touch-wake`, `wake-alarms`, `wake-button`) that
   gates whole sections of the app UI.
3. **The robot must have a curve25519 public key** and publish it as `robots/{id}.attributes["public-key"]`
   (base64), or JSON-mode pairing and the `secret-key-collection` mechanism cannot work.
4. **`iot_endpoint` implies an MQTT/IoT control plane.** `wakeup` and `reboot` are fire-and-forget HTTP
   POSTs from the phone that return a `{code,title,body}` acknowledgement — they cannot possibly reach a
   robot behind NAT over HTTP, so the cloud must push them over a persistent robot-initiated channel.
   Combined with the field name `iot-endpoint` and the `WakeupMoxieResponseModel.error` field (an error
   returned even on HTTP 200 — i.e. "the device didn't ack"), the architecture is almost certainly
   **AWS IoT Core MQTT** (or an MQTT-compatible broker) with the robot subscribing to a per-device topic.
   *No MQTT/WebSocket/GraphQL client library or URL exists in the parent app* — grep for
   `mqtt|websocket|graphql|greengrass|amazonaws|broker|pubsub` across the tree returns nothing. The parent
   app is 100 % Retrofit/OkHttp REST + Firebase (FCM push, Crashlytics, Analytics) + ExoPlayer.
5. **OTA**: `robots/{id}/ota_status` is polled by the app; the robot itself must have some separate OTA
   fetch channel not described here.
6. **Language/voice**: `POST robots/{id}/set-language` writes `input_language_id` / `output_language_id` /
   `output_voice_id` server-side; the robot must read those back.

### Minimum replacement-server surface for pairing to *appear* to succeed in the app

```
POST /api/oauth/token                          -> {access_token, refresh_token, token_type, expires_in, ...}
GET  /api/users/me?include=…                   -> user object; must include attributes["public-key"],
                                                  ["iot-endpoint"], ["has-backups"], relationships.child,
                                                  and relationships.robots[] once the robot registers
POST /api/pairing-info?id=&restore=&user-id=&child-id=   -> 200/201 (may be a no-op)
GET  /api/robots/{id}?include=restore,robot-setting      -> robot object (see §5.2)
GET  /api/robots/{id}/ota_status               -> {"status":"idle","percent":100,...}
POST /api/robots/{id}/restores                 -> 201
PUT  /api/secret-key-collection                -> 200
```

Everything else (`home`, `notifications`, `analytics/*`, `content-preferences`, `moxie-commands`,
`tips-for-success`, `help/*`, `network-tests`, teletherapy, GRL) is app-side polish and can be stubbed.

---

## 9. Gaps / things this APK cannot tell you

* **The robot-side protocol.** Nothing about the endpoint the robot POSTs to after joining WiFi, its
  authentication, or the MQTT topic scheme. That has to come from the robot firmware image.
* **The `iot_endpoint` → hostname table.** Firmware-side.
* **Actual `network-tests` hosts** — server-supplied at runtime, not baked into the APK.
* **Whether the robot accepts the legacy JSON QR.** The app can no longer produce one, but the format is
  fully documented above and is trivially worth trying first on a device with old firmware, since it
  needs no key material at all — just a valid OAuth access token from your own server.
* **Exactly what the robot sends as proof of the secret key** (the raw key? a signature? an HMAC?). Only
  the SHA-256 is registered with the cloud, so the robot most likely sends the key itself over TLS, or a
  signature verifiable from it.

---

## 10. File map (this subsystem)

```
pair_moxie/
  PairMoxieActivity.java          entry, reads intent extras pairing_mode / check_restore_from_backup
  RestoreMoxieFragment.java       Yes/No backup-restore prompt -> Config.userWantsToRestoreFromBackup
  PairMoxieWifiFragment.java      WiFi form, band spinner, registerForPairing(), 8-char pw rule
  PairInstructionFragment.java    step 2/5 instructional screen
  PairMoxieQrCodeFragment.java    QR generation + 2 s x60 polling loop + success detection  <-- core
  MoxieConnectedFragment.java     post-success "Moxie connected" UI only (no logic)
  MoxieConnectedProFragment.java  clinician variant
  JSONPairing.java                legacy QR payload builder (unreachable in v2.2.2)
  ProtoPairing.java               current QR payload builder + serectHashFromKey()          <-- core
  PairingMode.java                WIFI_ONLY | WIFI_AND_PAIRING
  WifiBand.java                   ANY | ONLY_50G | ONLY_24G
  PairingCallback.java            onFinish(boolean success)
  PairingErrorDialog.java         editWifi/instruction/finishSetup/tryAgain/needHelp
  PairingHavingTroubleDialog.java sets transient incorrectSSID/incorrectPassword flags
  ConnectingTipsAndTricksDialog.java

api/
  Config.java                     ALL endpoint path constants, base URLs, PairQRMode, BuildMode
  APIService.java                 Retrofit interface — the authoritative endpoint list
  RequestManager.java             registerForPairing / createRestore / getRobot / wakeup / reboot / …
  ResponseManager.java            401 refresh, error mapping to Config.STATUS_CODE_*
  CryptoManager.java              secret-key-collection payload construction
  crypto/CryptoHelper.java        signing key + curve25519 keypair from passphrase
  crypto/RecoveryKey.java         Argon2id, zero salt, 32-byte seed
  crypto/SealBox.java             crypto_box_seal
  crypto/SecretBox.java           XSalsa20-Poly1305, nonce||ct
  crypto/diceware/Passphrase.java 8-word EFF short wordlist
  models/wifi/{WifiNetworkInfo,PairingInfo,PairingModel}.java
  models/robot/*.java             RobotAttributes, OtaStatusModel, Wakeup/RebootMoxieResponseModel, …
  models/network_tests/*.java
  models/Robot.java               enums + MoxieStatus derivation (is-online / last-seen-at logic)

recovery_key/
  EnterRecoveryKeyFragment.java   passphrase -> regenerate signing key (LOCAL verification only)
  ExportRecoveryKeyFragment.java  generate & display new 8-word phrase

viewmodel/RobotInfoViewModel.java wakeup / ota_status / settings updates
viewmodel/UserInfoViewModel.java  fetchUserInfo (the pairing poll)
utils/AttemptCounter.java         the 2000 ms x N polling primitive
```
