# Moxie Parent App — Cryptography & Key Management (subsystem map)

> **📖 About this document.** This is a *clean-room* description of the **original** Moxie parent app
> (`com.embo.embodied.parent` v2.2.2), written by reverse-engineering it. **The decompiled app is NOT
> included in this repository, and you do not need it.** Any file or class names below (e.g.
> `api/Config.java`, `pair_moxie/…`, or paths shown as `<decompiled>/…`) are references into the
> *original app's own internal structure* — they document *where a behavior lived in the app so the
> protocol is reproducible*, and are **not** files in this repo. Our actual implementation lives in
> [`server/`](../../server/), [`tools/`](../../tools/), and [`mqtt/`](../../mqtt/).

Source root: `<decompiled>/com/embo/embodied/parent`
Native: `lib/arm64-v8a/libsodiumjni.so` — **libsodium 1.0.16** (version string confirmed in `.so`), loaded via
`org.libsodium.jni.NaCl` (`System.loadLibrary("sodiumjni")`, then `Sodium.sodium_init()` on every call).

**One-sentence summary:** everything in this app hangs off a single 32-byte value — the Argon2id hash of an
8-word EFF-short-wordlist diceware passphrase. That one value is simultaneously the Ed25519 signing seed, the
X25519 box seed, and the XSalsa20-Poly1305 symmetric key, and it is what the pairing QR code hands to the robot.

---

## 0. The master secret: one seed to rule them all

`api/crypto/CryptoHelper.java:142-149`

```java
private void generateEncryptionKeyPair(byte[] bArr) {
    SigningKey signingKey = new SigningKey(bArr);       // Ed25519 from seed
    this.signingKey = signingKey;
    this.cryptoSecretBox = new SecretBox(signingKey.toBytes());   // toBytes() == the SEED
    KeyPair keyPair = new KeyPair(bArr);                // X25519 from the same seed
    this.encryptionKeyPair = keyPair;
    Config.storeClientPublicKey(Encoder.encodeAsString(keyPair.getPublicKey().toBytes()));
}
```

`bArr` is a 32-byte seed produced by `deriveSeedFromPassphrase()`. From it the app derives, deterministically:

| Artifact | Algorithm | libsodium call | Size |
|---|---|---|---|
| `signingKey` (Ed25519) | Ed25519 | `crypto_sign_ed25519_seed_keypair(pk, sk, seed)` | pk 32 B, sk 64 B, seed 32 B |
| `encryptionKeyPair` (X25519) | Curve25519 | `crypto_box_curve25519xsalsa20poly1305_seed_keypair(pk, sk, seed)` | pk 32 B, sk 32 B |
| `cryptoSecretBox` key | XSalsa20-Poly1305 | `crypto_secretbox_easy` | key = **the seed itself**, 32 B |

Note the critical consequence: **the symmetric key that protects all child PII IS the seed IS the Ed25519 seed
that gets printed into the pairing QR code.** Anyone who photographs the QR code recovers the full key material.

---

## 1. `getSigningKey()` — what it is and how it's made

`api/crypto/CryptoHelper.java:32-34`

```java
public SigningKey getSigningKey() { return this.signingKey; }
```

Type is `org.libsodium.jni.keys.SigningKey` (the deprecated libsodium-jni wrapper, decompiled at
`<decompiled>/org/libsodium/jni/keys/SigningKey.java`):

```java
public class SigningKey {
    private final byte[] secretKey;   // 64 bytes
    private final byte[] seed;        // 32 bytes
    private VerifyKey verifyKey;      // 32-byte Ed25519 public key

    public SigningKey(byte[] bArr) {
        Util.checkLength(bArr, 32);
        this.seed = bArr;
        byte[] zeros = Util.zeros(64);   this.secretKey = zeros;
        byte[] zeros2 = Util.zeros(32);
        NaCl.sodium();
        Util.isValid(Sodium.crypto_sign_ed25519_seed_keypair(zeros2, zeros, bArr),
                     "Failed to generate a key pair");
        this.verifyKey = new VerifyKey(zeros2);
    }

    public byte[] sign(byte[] bArr) {                        // crypto_sign (combined mode), returns
        byte[] prependZeros = Util.prependZeros(64, bArr);   // the first 64 bytes = detached signature
        NaCl.sodium();
        Sodium.crypto_sign_ed25519(prependZeros, new int[1], bArr, bArr.length, this.secretKey);
        return Util.slice(prependZeros, 0, 64);
    }

    public byte[] toBytes() { return this.seed; }            // <-- THE SEED, NOT the 64-byte sk
    public String toString() { return Encoder.HEX.encode(this.seed); }
}
```

**Answers:**
- **Kind of keypair:** Ed25519 (libsodium `crypto_sign_ed25519`), used for detached 64-byte signatures.
  Verification counterpart is `VerifyKey.verify(msg, sig64)` → `crypto_sign_ed25519_open`.
- **Generated from:** a 32-byte seed, *never* randomly in this app. The default `SigningKey()` ctor
  (`new Random().randomBytes(32)`) is dead code — the app only ever calls `new SigningKey(seed)`.
- **Deterministic:** yes, fully. Same diceware passphrase → same seed → same Ed25519 key, forever, on any device.
- **Seed source:** Argon2id over the diceware recovery passphrase (see §4).
- **Storage:** the key itself is **never persisted**. It lives only in the `CryptoHelper` singleton in RAM and is
  re-derived on every app start from the persisted passphrase *code*:
  - `ppcrk` → the 32-char diceware code (8 words × 4 dice digits), in `EncryptedSharedPreferences`
    ("EmbodiedApp", AES256_SIV keys / AES256_GCM values, Android Keystore master key — `api/SecureSharedPreference.java`).
  - `client_public_key` → base64 of the derived **X25519** public key (`Config.storeClientPublicKey`), used only
    as a "did the key change?" tripwire (`CryptoHelper.isClientKeyChanged()`).
  - Re-derivation entry points: `MainActivity.java:47` → `CryptoHelper.restoreSymmetricKey()`;
    `Config.storePassPhraseCode()` (`api/Config.java:424-431`) calls `restoreSymmetricKey()` right after writing.

---

## 2. `ProtoPairing.toQRString()` — exactly what bytes go in the QR

Constructed at `pair_moxie/PairMoxieQrCodeFragment.java:355`:

```java
jsonString = new ProtoPairing(PairMoxieWifiFragment.getWifiNetworkInfo(),
                              CryptoHelper.getInstance().getSigningKey().toBytes())
             .toQRString(z2, iotEndpointIndex);
```

`getSigningKey().toBytes()` returns **`this.seed`** →

> **The QR carries the raw 32-byte Argon2id seed.** Not the public key, not the 64-byte Ed25519 secret key —
> the seed. From it the robot can regenerate the Ed25519 signing key, the X25519 box key, and the
> XSalsa20-Poly1305 symmetric key on its own.

### Wire format (`pair_moxie/ProtoPairing.java`)

Hand-rolled protobuf, base64'd, prefixed with the literal `"PA"`:

```java
private static final String PROTO_PAIR_HEADER = "PA";
...
return PROTO_PAIR_HEADER + Base64.encodeToString(bArr, 0);   // flag 0 == Base64.DEFAULT (wrapped, trailing \n)
```

Field-by-field, in the exact emission order of `toQRString(boolean wifiOnly, int iotEndpoint)`:

| Bytes emitted | Protobuf tag | Field | Payload |
|---|---|---|---|
| `0x0A` + varint len + bytes | field 1, LEN | `ssid` | UTF-8 SSID |
| `0x12` (`Ascii.DC2`) + varint len + bytes | field 2, LEN | `password` | UTF-8 Wi-Fi password |
| `0x18` (`Ascii.CAN`) + `0x01` | field 3, VARINT | dev/staging flag | emitted **only when `Config.getBuildMode() != PRODUCTION`** |
| `0x28` + `0x01` | field 5, VARINT | `wifi_only` | emitted when `wifiOnly == true` (PairingMode.WIFI_ONLY) |
| `0x22` + varint len + bytes | field 4, LEN | **`secret_key`** | the 32-byte seed — emitted when `wifiOnly == false` |
| `0x30` + `0x01` | field 6, VARINT | hidden SSID | only if `wifi.isHidden()` |
| `0x38` + `0x01` \| `0x02` | field 7, VARINT | band | `1` = 5 GHz only, `2` = 2.4 GHz only, omitted for ANY |
| `0x40` (`SignedBytes.MAX_POWER_OF_TWO`) + 1 byte | field 8, VARINT | `iot_endpoint` | `User.attributes["iot-endpoint"]`, `0` if absent |

Fields 4 and 5 are mutually exclusive: a WIFI_ONLY QR carries no key at all. `encodeVarInt()` is a standard
LEB128 base-128 varint. Buffer is `ByteBuffer.allocate(1024)`.

Legacy alternative (`pair_moxie/JSONPairing.java`, mode `PairQRMode.PAIR_JSON_TOKEN`) embeds the OAuth
access token as JSON instead. **`Config.getPairQRMode()` (`api/Config.java:376-381`) unconditionally returns
`PAIR_PROTO_KEY`** — both branches return the same value, so the JSON mode is dead.

---

## 3. `serectHashFromKey()` and the pairing handshake

`pair_moxie/ProtoPairing.java:36-45`

```java
public static String serectHashFromKey(byte[] bArr) {
    MessageDigest messageDigest = MessageDigest.getInstance("SHA-256");
    messageDigest.update(bArr);
    return bytesToHexString(messageDigest.digest());   // lowercase hex, 64 chars
}
```

`bytesToHexString` is zero-padded lowercase hex. So the registered id is `hex(SHA256(seed))`.

Call site — `pair_moxie/PairMoxieWifiFragment.java:441-449`:

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

`api/RequestManager.java:580-596`:

```java
public final void registerForPairing(String id, boolean userWantsToRestoreFromBackup,
                                     String user_id, String child_id, ResponseCallback cb) {
    TokenResponseModel authDataModel = Config.getAuthDataModel();
    HashMap hashMap = new HashMap();
    hashMap.put("id", id);                                       // hex SHA-256 of the seed
    hashMap.put("restore", String.valueOf(userWantsToRestoreFromBackup));
    hashMap.put("user-id", user_id);
    hashMap.put("child-id", child_id);
    apiService2.pairingInfo(authDataModel.getAuth(), hashMap) ... // POST, params as QUERY string
}
```

`api/APIService.java:151-152` + `api/Config.java:86`:

```java
@POST(Config.API_PAIRING_INFO)     // "pairing-info"
Call<ResponseBody> pairingInfo(@Header("Authorization") String authHeader,
                               @QueryMap Map<String, String> paramsMap);
```

So: **`POST {base}/api/pairing-info?id=<hex sha256>&restore=<true|false>&user-id=<uuid>&child-id=<uuid>`**
with `Authorization: Bearer <access_token>`.

### Full handshake map (what a replacement server must implement)

```
                      PARENT APP                          SERVER                         MOXIE ROBOT
 (a) recovery passphrase --Argon2id--> seed(32B)
 (b) Ed25519/X25519/secretbox derived from seed
 (c) PUT secret-key-collection  ------------------> stores { b64(pubkey) : b64(sealed seed) }
                                                     for user pubkey AND robot pubkey
 (d) POST pairing-info?id=hex(SHA256(seed))  -----> server records: this pairing-token-hash
     &restore&user-id&child-id                       belongs to user-id / child-id, restore flag
 (e) QR = "PA" + b64(proto{ssid,pwd,secret_key=seed,...})
                                                                     <---- robot camera scans QR
                                                                          robot joins Wi-Fi
                                                                          robot has seed(32B)
 (f)                                               <---- robot presents/proves knowledge of seed
                                                        (server looks up SHA256(seed) from step d,
                                                         finds the pending pairing record,
                                                         binds robot -> user/child, issues robot creds)
 (g) robot generates/holds its own X25519 keypair; its `public-key` appears on the robot record
 (h) app re-runs CryptoManager.updateKeysIfNeeded(): now the robot pubkey exists, so the seed is
     sealed to the robot's pubkey too and PUT to secret-key-collection -> robot can fetch and
     crypto_box_seal_open it, obtaining the symmetric key for all `*-encrypted` child fields.
```

Key properties for reimplementation:

- The server never sees the seed in the clear. It sees `hex(SHA256(seed))` in step (d) and
  *sealed-box ciphertexts* of the seed in step (c). Both are one-way from the server's perspective.
- `hex(SHA256(seed))` is the **pairing rendezvous token**. The robot, having scanned the seed, can compute the
  same hash and present it; the server matches it against pending `pairing-info` registrations to learn which
  user/child the robot belongs to. (The robot-side code is not in this APK; this is the only mechanism the
  registered `id` can serve, since it is the sole value shared between the two halves of the flow.)
- Step (d) is issued *before* the QR is displayed — `PairMoxieWifiFragment.goToPairing()` gates
  `switchToPairingInstructionsPage()` on the `registerForPairing` success callback, and retries on failure
  ("register_pairing_error_title" / retry dialog).
- Step (d) is skipped entirely when `getPairingMode() == PairingMode.WIFI_ONLY` (no key in the QR either).
- `restore=true` (`Config.userWantsToRestoreFromBackup`) tells the server the robot should pull the child's
  backup after pairing; there is a parallel `POST robots/{id}/restores` with
  `RestoreStatus.initiated|declined` (`RequestManager.createRestore`).

### `secret-key-collection` (step c/h)

`api/CryptoManager.java:159-188`:

```java
public final JsonObject encryptSymmetricKeyToPublicKeys() {
    CryptoHelper cryptoHelper = CryptoHelper.getInstance();
    String encodeAsString = Encoder.encodeAsString(cryptoHelper.getKeyPair().getPublicKey().toBytes());
    // bail out if our derived X25519 pubkey != the server's user["public-key"]
    if (!Intrinsics.areEqual(encodeAsString, data.getAttributes().getPublicKey())) return null;
    List<PublicKey> gatherPublicKeys = gatherPublicKeys();          // [user pubkey, robot pubkey], base64
    byte[] bytes = cryptoHelper.getSigningKey().toBytes();          // the 32-byte SEED again
    JsonObject jsonObject = new JsonObject();
    for (PublicKey pk : gatherPublicKeys) {
        byte[] decodeAsBytes = Encoder.decodeAsBytes(pk.getEncrypted());     // raw 32-byte X25519 pk
        byte[] encryptSeal   = cryptoHelper.encryptSeal(bytes, decodeAsBytes);
        jsonObject.addProperty(Encoder.encodeAsString(decodeAsBytes),        // b64 pubkey  ->
                               Encoder.encodeAsString(encryptSeal));        // b64 sealed seed
    }
    return jsonObject;
}
```

- `gatherPublicKeys()` reads `User.data.attributes["public-key"]` and `Robot.data.attributes["public-key"]`
  (both `@SerializedName("public-key")`, base64). Returns `null` (and skips the update) if either is missing.
- `encryptSeal` → `api/crypto/SealBox.encrypt` → `crypto_box_seal(out, msg, msglen, recipient_pk)`.
  Anonymous sealed box: ephemeral X25519 pk (32 B) + crypto_box, `crypto_box_SEALBYTES = 48`, so
  ciphertext = 48 + 32 = **80 bytes**, base64 → 108 chars.
- Wire body (`api/models/user/UpdateKeysModel.java`, `SecretKeysIndexedByPublic.java`,
  `api/APIService.java:210-211`, `api/Config.java:97`):

```
PUT {base}/api/secret-key-collection      Authorization: Bearer <token>
{
  "secret_key_collection": {
    "secret-keys-indexed-by-public-keys": {
      "<base64 user X25519 pubkey>":  "<base64 sealed 32-byte seed>",
      "<base64 robot X25519 pubkey>": "<base64 sealed 32-byte seed>"
    }
  }
}
```

- Driven by `CryptoManager.updateKeysIfNeeded()` (observer on the user LiveData,
  `setupObservers` / `m34setupObservers$lambda0`) and explicitly from
  `PairMoxieQrCodeFragment.updateKeysInServer()`. Local status codes: `114` = key-update error,
  `115` = already up to date (`api/Config.java:132-134`).
- `PublicKey.getEncrypted()` is a misnomer — the field holds the **plain base64 public key** string
  (`api/crypto/PublicKey.java`, ctor param named `encrypted`).

---

## 4. Recovery key: diceware → Argon2id → seed

### 4a. Wordlist and passphrase generation

Asset: `assets/eff_short_wordlist_1.txt` (extracted at
`<decompiled>/resources/assets/eff_short_wordlist_1.txt`) —
**1296 lines**, TAB-separated, `<4-digit dice code>\t<word>`, e.g. `1111\tacid`, `1112\tacorn`.
This is the standard EFF "short wordlist #1" (1296 = 6^4 entries, ≤5-char words).

`api/crypto/diceware/Passphrase.java`:

```java
private HashMap<String, String> wordsMap = new HashMap<>();   // "1111" -> "acid"
private int keyPhraseLength = 8;                              // 8 words

// init(): reads asset, split("\t"), wordsMap.put(split[0], split[split.length-1])

public PhrasePair generateRecoveryKey() {
    ArrayList arrayList = new ArrayList(this.wordsMap.keySet());
    SecureRandom secureRandom = new SecureRandom();
    for (int i = 0; i < this.keyPhraseLength; i++) {
        String str  = (String) arrayList.get(secureRandom.nextInt(arrayList.size()));  // dice code
        String str2 = this.wordsMap.get(str);                                          // word
        sb.append(str);                       // code:  concatenated 4-digit codes  (32 chars)
        if (i != 0) sb2.append("-");
        sb2.append(str2);                     // word:  "acid-acorn-...-zoom"       (8 words, 7 dashes)
    }
    return new PhrasePair(sb.toString(), sb2.toString());
}

public String getPhraseFromCode(String str) {     // 32-char code -> dashed 8-word phrase
    if (str.length() % 4 != 0 || str.length() / 4 != this.keyPhraseLength) return null;
    ... substring(i, i+4) -> wordsMap.get(...) ... joined with "-"
}

public String getCodeFromPassphrase(String str) { // dashed phrase -> 32-char code (reverse map lookup)
    String[] split = str.split("-");
    for (String str2 : split) sb.append((String) Utils.getKeyByValue(this.wordsMap, str2));
    return sb.toString();
}
```

Entropy: 8 words × log2(1296) ≈ **82.7 bits**. Selection uses `java.security.SecureRandom.nextInt(1296)`
(iteration order of a `HashMap` keySet — irrelevant to security, uniform either way).

Note: **the KDF input is the dashed *word* string, not the dice code.** `Config` persists the code (`ppcrk`);
the code is converted back to the phrase before hashing.

### 4b. The KDF — `api/crypto/RecoveryKey.java` (verbatim)

```java
byte[] deriveSeed() {
    NaCl.sodium();
    return hash(Sodium.crypto_box_seedbytes());          // 32
}

private byte[] hash(int i) throws Exception {
    byte[] bArr = new byte[i];                                        // out, 32 bytes
    NaCl.sodium();
    byte[] bArr2 = new byte[Sodium.crypto_pwhash_saltbytes()];        // SALT = 16 ZERO BYTES  <-- !!
    NaCl.sodium();
    byte[] bytes  = this.rawValue.getBytes();                         // passphrase, platform charset (UTF-8)
    int    length = this.rawValue.getBytes().length;
    NaCl.sodium();
    int crypto_pwhash_opslimit_interactive = Sodium.crypto_pwhash_opslimit_interactive();
    NaCl.sodium();
    int crypto_pwhash_memlimit_interactive = Sodium.crypto_pwhash_memlimit_interactive();
    NaCl.sodium();
    int crypto_pwhash = Sodium.crypto_pwhash(bArr, i, bytes, length, bArr2,
                                             crypto_pwhash_opslimit_interactive,
                                             crypto_pwhash_memlimit_interactive,
                                             Sodium.crypto_pwhash_alg_default());
    Log.d(TAG, "hash: isHashSucceeded = " + crypto_pwhash);
    return bArr;                                        // NOTE: returned even on failure (non-zero rc)
}
```

`rawValue` is `str.trim()` of the dashed phrase (`init()`).

**Concrete parameters** (libsodium 1.0.16 constants):

| Parameter | Value |
|---|---|
| Algorithm | `crypto_pwhash_ALG_DEFAULT` = `crypto_pwhash_ALG_ARGON2ID13` = **2** (Argon2id v1.3) — 1.0.16 confirmed, `crypto_pwhash_argon2id_*` symbols present in the `.so` |
| Password | UTF-8 bytes of `"word1-word2-...-word8"`, trimmed |
| Salt | **16 bytes of 0x00** (`crypto_pwhash_SALTBYTES` = 16, buffer never written) |
| opslimit | `crypto_pwhash_OPSLIMIT_INTERACTIVE` = **2** |
| memlimit | `crypto_pwhash_MEMLIMIT_INTERACTIVE` = **67108864** (64 MiB) |
| Parallelism | 1 (libsodium fixes lanes/threads = 1) |
| Output | **32 bytes** (`crypto_box_SEEDBYTES`) |

Reference implementation for a replacement server / tooling:

```python
from nacl.pwhash.argon2id import kdf as argon2id_kdf   # PyNaCl
seed = argon2id_kdf(32, phrase.strip().encode("utf-8"), b"\x00"*16,
                    opslimit=2, memlimit=67108864)
```
```c
crypto_pwhash(seed, 32, phrase, strlen(phrase), zero_salt16, 2, 67108864,
              crypto_pwhash_ALG_ARGON2ID13);
```

### 4c. Restore / export flows

`recovery_key/ExportRecoveryKeyFragment.java:114-145` — first-time setup:

```java
PhrasePair generateRecoveryKey = Passphrase.getInstance().generateRecoveryKey();
Config.storePassPhraseCode(generateRecoveryKey.getCode());              // persists "ppcrk"
this.mBinding.recoveryKeyText.setText(generateRecoveryKey.getWord());   // shown to the user
String encodeAsString = Encoder.encodeAsString(
        CryptoHelper.getInstance().generateKeyPair(generateRecoveryKey.getWord())
                    .getPublicKey().toBytes());                         // X25519 pubkey, base64
UserAttributes userAttributes = new UserAttributes();
userAttributes.setPublicKey(encodeAsString);
... updateUserRequest(userAttributes, ...)                              // PUT users/me {"public-key": ...}
```

`recovery_key/EnterRecoveryKeyFragment.java:122-131` — user types their phrase back in:

```java
String trim = this.mBinding.enterRecoveryKeyEdit.getText().trim();
KeyPair generateKeyPair = CryptoHelper.getInstance().generateKeyPair(trim);
if (User.INSTANCE.getData() != null
    && !Encoder.encodeAsString(generateKeyPair.getPublicKey().toBytes())
              .equals(User.INSTANCE.getData().getAttributes().getPublicKey())) {
    this.mBinding.enterRecoveryKeyEdit.showErrorText(true);   // wrong phrase
    return;
}
Config.storePassPhraseCode(Passphrase.getInstance().getCodeFromPassphrase(trim));
```

> **Validation oracle:** correctness of a passphrase is checked purely client-side, by comparing the derived
> X25519 public key against the server-stored `users/me → attributes["public-key"]`. A replacement server must
> serve that field verbatim (base64 of the 32-byte X25519 public key) or the app will reject every phrase.

Silent re-derivation on app start (`CryptoHelper.java:69-76`):

```java
public boolean restoreSymmetricKey() {
    byte[] deriveSeedFromPassphrase;
    if (Config.getPassPhraseCode() == null
        || (deriveSeedFromPassphrase = deriveSeedFromPassphrase(
              Passphrase.getInstance().getPhraseFromCode(Config.getPassPhraseCode()))) == null) return false;
    generateEncryptionKeyPair(deriveSeedFromPassphrase);
    return true;
}
```

`restoreKeyPair(String expectedPubKeyB64)` (`CryptoHelper.java:50-67`) does the same then checks the derived
X25519 pubkey against the argument, nulling both keys on mismatch. Driver:
`BaseActivity.setupRecoveryKeyController()` (`BaseActivity.java:1754-1772`) — no `public-key` on the user
record ⇒ clear `ppcrk` and go to Export; `public-key` present but `restoreKeyPair` fails ⇒ go to Enter.

---

## 5. Symmetric encryption of child PII, and AUID

### 5a. `SecretBox` — `api/crypto/SecretBox.java`

```java
public SecretBox(byte[] bArr) { ... this.key = bArr; }          // key = the 32-byte seed

public byte[] encrypt(byte[] bArr) {
    Util.checkLength(this.key, this.SECRETBOX_KEYBYTES);        // 32
    byte[] bArr2 = new byte[bArr.length + this.SECRETBOX_MACBYTES];   // 16
    byte[] nonce = NonceGenerator.nonce(this.SECRETBOX_NONCEBYTES);   // 24, randombytes_buf
    Util.isValid(Sodium.crypto_secretbox_easy(bArr2, bArr, bArr.length, nonce, this.key), "Encryption failed");
    return Utils.concatBytes(nonce, bArr2);                     // nonce || (MAC||ct)
}
```

Ciphertext layout: **`nonce(24) || mac(16) || ciphertext(n)`**, then base64 `NO_WRAP`
(`api/crypto/Encoder.java` uses `Base64.encodeToString(bArr, 2)` / `Base64.decode(str, 2)` throughout).
`decrypt()` splits at 24 and calls `crypto_secretbox_open_easy`. Cipher is XSalsa20-Poly1305
(`crypto_secretbox_xsalsa20poly1305_*` for the size constants).

### 5b. Field-level encryption — `api/models/Child.java:177-196` / `asDecryptedData`

Every JSON key on `ChildrenModel` ending in `-encrypted` is transparently run through
`CryptoHelper.encrypt()/decrypt()`:

```java
if (StringsKt.endsWith$default(key, "-encrypted", false, 2, (Object) null)) {
    if (!key.equals("likes-imaginative-play-encrypted") && !key.equals("self-regulation-tools-preferences-encrypted")
        && !key.equals("therapy-needs-encrypted")       && !key.equals("volume-preference-encrypted")
        && !key.equals("calendar-events-encrypted")) {
        string = StringHelper.addQuotes(string);       // scalars get wrapped in literal double quotes first
    }
    jSONObject.put(key, CryptoHelper.getInstance().encrypt(string));
}
```

Decrypt is the mirror image with `StringHelper.removeQuotes(...)`. The five listed keys hold JSON
arrays/objects and are *not* quote-wrapped. Encrypted fields on `ChildrenModel` include:
`auid-encrypted`, `birthday-encrypted`, `calendar-events-encrypted`, `first-name-encrypted`,
`gender-encrypted`, `likes-imaginative-play-encrypted`, `self-regulation-tools-preferences-encrypted`,
`therapy-needs-encrypted`, `volume-preference-encrypted`.

**A replacement server stores these as opaque base64 blobs.** It cannot read them and does not need to;
only the app and (via the sealed `secret-key-collection`) the robot hold the key.

### 5c. AUID

AUID = the child's analytics/anonymous user id.

- **At rest / in the child record:** `ChildrenModel.auid` is `@SerializedName("auid-encrypted")`
  (`api/models/user/ChildrenModel.java:22`) — so it is a SecretBox blob under the seed, decrypted by the
  generic `-encrypted` sweep above. `RequestManager.auid(selectedChild, AUIDCallback)`
  (`api/RequestManager.java:814-830`) just reads the already-decrypted field off the cached child and hands
  back the plaintext (returns `"foo"` in the insights-demo build).
- **In use:** the *plaintext* AUID is sent as an ordinary query parameter to the analytics endpoints —
  `hashMap.put("auid", auid)` in `analytics()` (`GET analytics/pages/{id}`) and `detailsAnalytics()`
  (`GET analytics/pages/details`). So AUID is not encrypted in transit beyond TLS.
- **`analytics/auid-encrypted`** (`Config.API_ANALYTICS_AUID_ENCRYPTED`) is declared as
  `@GET(...) Call<ResponseBody> auidEncrypted(@Header("Authorization") String)` at `api/APIService.java:51-52`
  but has **no caller anywhere in the app** — a dead/legacy endpoint that presumably returned the same
  SecretBox blob standalone. A replacement server can stub or omit it.
- **`help/share-auid`:** `GET help` returns `encrypted_auids` (`api/models/help/GetHelpModel.java:16`), a list
  of SecretBox blobs. `HelpFragment.m274xbeb5b785` (`main/account/help/HelpFragment.java:282-292`) decrypts
  each with `CryptoHelper.getInstance().decrypt(...)` and POSTs the **plaintext** list to `help/share-auid` as
  `{"auids":[...], "mode": temporary|permanent|revoke|revoke_all|none}` — i.e. an explicit, user-consented
  de-anonymisation so support can look the child up.
- `CLIENT_PUBLIC_KEY` / `"client_public_key"` is **not** part of AUID encryption. It is only a local
  `EncryptedSharedPreferences` cache of the base64 X25519 public key, written by `generateEncryptionKeyPair()`
  and read by `CryptoHelper.isClientKeyChanged()` to detect "the user reset their key on another device".

---

## 6. Hardcoded keys, salts and constants

| Constant | Value | Where |
|---|---|---|
| **Argon2id salt** | **16 × `0x00`** (never initialised) | `api/crypto/RecoveryKey.java:33` |
| Argon2id opslimit / memlimit / alg | 2 / 67108864 / ARGON2ID13 | `RecoveryKey.hash()` via libsodium 1.0.16 defaults |
| Diceware word count | `keyPhraseLength = 8` | `diceware/Passphrase.java:19` |
| Wordlist | `eff_short_wordlist_1.txt`, 1296 entries | `assets/`, loaded in `Passphrase.init()` |
| Passphrase separator | `"-"` | `Passphrase.generateRecoveryKey/getPhraseFromCode/getCodeFromPassphrase` |
| Test recovery key | `"test-recovery-key"` | `RecoveryKey.test()` (debug helper, unused in prod paths) |
| QR header | `"PA"` | `ProtoPairing.PROTO_PAIR_HEADER` |
| Prefs key: passphrase code | `"ppcrk"` | `api/Config.java:124` |
| Prefs key: client pubkey | `"client_public_key"` | `api/Config.java:109` |
| Curve25519 base point | `"0900000000000000000000000000000000000000000000000000000000000000"` | `org/libsodium/jni/crypto/Point.STANDARD_GROUP_ELEMENT` (only used on the `publicKey == null` fallback path, which never triggers here) |
| Keystore master key alias | `MasterKey.DEFAULT_MASTER_KEY_ALIAS` ("_androidx_security_master_key_"), AES-256-GCM, no padding | `api/SecureSharedPreference.getMasterKey` |
| Prefs file name | `"EmbodiedApp"` | `EmbodiedApplication.java:42` |
| OAuth client id — STAGING | `GjnNt7QqHoiRMkciyDoTEAWug6vhpyV6LtaHn2m7hJyxNaXCduAc9Yk9CoMpKZLv` | `RequestManager.getClientId()` |
| OAuth client id — **PRODUCTION** | `1tjzBncMMwsTl0K-ORtwUXcYV5GH-LZh7YGvQNsDAD4` | idem |
| OAuth client id — DEVELOP | `DeJ8ykK4pM8G6qVe3gFLJzrpH6QfbRW3CKjdCT499maesa8r8vNAgFWzkDcTeXGT` | idem |
| OAuth client id — CHINA/HK | `AqHSIQcR_Mg0zL_L7VAdUMCXznaXCpRQT18szfGCp4w` | idem |
| OAuth client secret — default | `OKJMOFpcI16R7Mv1GTcyC9rTsuUomd_quZhsLQLGsd4` | `RequestManager.getClientSecret()` |
| OAuth client secret — CHINA/HK | `qL_EeFcK6s2de6qcalegLMBmr0zKV1qZ2UgLAmJOjkw` | idem |
| Base URL — PRODUCTION | `https://client-service-api.embodied.com/` (+ `api/`) | `Config.getBaseUrl()` |
| Local status codes | 112 AUID_NOT_FOUND, 113 NO_DATA, 114 KEY_UPDATE_ERROR, 115 KEY_UPDATED, 116 NO_NETWORK, 117 TOKEN_FAILED | `api/Config.java:127-140` |

Also present but **unused by this subsystem**: Google Tink's Ed25519 implementation
(`com/google/crypto/tink/subtle/Ed25519*.java`) is shipped as a transitive dependency (Tink is what backs
`androidx.security.crypto.EncryptedSharedPreferences`); none of the app's own crypto goes through Tink.

---

## 7. Security observations relevant to a reimplementation

1. **Zero salt.** Argon2id with a constant all-zero salt makes the seed a pure function of the passphrase —
   fully precomputable/rainbow-tableable across all users. It is also what makes reimplementation easy: given a
   passphrase you can reproduce every key offline with no server interaction.
2. **Seed reuse across three primitives.** The same 32 bytes are the Ed25519 seed, the X25519 seed and the
   XSalsa20-Poly1305 key. Cross-primitive key reuse; also means the QR code is a total-compromise artifact.
3. **The QR contains the raw seed in a plain base64 protobuf** with no expiry, no binding to a robot, and no
   signature. Anyone who photographs the pairing screen gets the Wi-Fi password *and* the key to all of the
   child's encrypted data.
4. **`RecoveryKey.hash()` ignores the return code** of `crypto_pwhash` and returns the (possibly all-zero)
   buffer regardless; a memory-allocation failure would silently yield a 32-zero-byte "seed".
5. `CryptoHelper.decryptSeal()` (`CryptoHelper.java:120-132`) calls `SealBox.decrypt(...)` and then
   `return null` unconditionally — the result is discarded. Dead/broken; nothing depends on it.
6. `Config.getPairQRMode()` returns `PAIR_PROTO_KEY` on both branches — the JSON/access-token QR path is
   unreachable, so a replacement robot only needs the `"PA"` protobuf format.
7. The server is a **zero-knowledge store** for the child PII: it holds only SecretBox blobs, sealed copies of
   the seed, and `hex(SHA256(seed))`. A reimplemented server needs to persist and echo those opaque values
   faithfully; it never needs to (and cannot) decrypt them.

---

## 8. File index

| Path (relative to `.../com/embo/embodied/parent`) | Role |
|---|---|
| `api/crypto/CryptoHelper.java` | Singleton; owns signingKey / encryptionKeyPair / cryptoSecretBox; derive, restore, encrypt, decrypt, seal |
| `api/crypto/RecoveryKey.java` | Argon2id KDF (zero salt), passphrase → 32-byte seed |
| `api/crypto/diceware/Passphrase.java` | EFF wordlist loader, 8-word generation, code↔phrase conversion |
| `api/crypto/diceware/PhrasePair.java` | `(code, word)` data class |
| `api/crypto/SecretBox.java` | XSalsa20-Poly1305, `nonce‖mac‖ct` framing |
| `api/crypto/SealBox.java` | `crypto_box_seal` / `crypto_box_seal_open` |
| `api/crypto/KeyPair.java` | X25519 keypair from seed |
| `api/crypto/PublicKey.java` | DTO holding a base64 pubkey (field misnamed `encrypted`) |
| `api/crypto/NonceGenerator.java` | `randombytes_buf` |
| `api/crypto/Encoder.java` | base64 `NO_WRAP` helpers |
| `api/crypto/StringHelper.java` | add/remove literal quotes around scalars before/after encryption |
| `api/CryptoManager.java` | Builds & PUTs the `secret-key-collection` sealed-seed map |
| `api/Config.java` | All endpoint paths, `ppcrk` / `client_public_key` prefs, base URLs |
| `api/SecureSharedPreference.java` | `EncryptedSharedPreferences` (AES256-SIV / AES256-GCM, Keystore) |
| `api/APIService.java` | Retrofit interface (`pairing-info`, `secret-key-collection`, `analytics/auid-encrypted`) |
| `api/RequestManager.java` | `registerForPairing`, `updateKeys`, `auid`, `shareAUIDs`, OAuth client ids |
| `api/models/user/UpdateKeysModel.java` + `SecretKeysIndexedByPublic.java` | `secret_key_collection` body |
| `api/models/user/ChildrenModel.java` | `*-encrypted` field names incl. `auid-encrypted` |
| `api/models/Child.java` | Generic `-encrypted` encrypt/decrypt sweep |
| `pair_moxie/ProtoPairing.java` | QR protobuf encoder + `serectHashFromKey` |
| `pair_moxie/PairMoxieWifiFragment.java` | Calls `registerForPairing` before showing the QR |
| `pair_moxie/PairMoxieQrCodeFragment.java` | Renders the QR, calls `CryptoManager.updateKeys` |
| `recovery_key/ExportRecoveryKeyFragment.java` | Generates phrase, publishes `public-key` to `users/me` |
| `recovery_key/EnterRecoveryKeyFragment.java` | Restores from phrase, validates against `public-key` |
| `BaseActivity.java:1754` | `setupRecoveryKeyController()` — export vs. enter decision |
| `MainActivity.java:47` | `restoreSymmetricKey()` on cold start |
| `org/libsodium/jni/**` | libsodium-jni wrappers (`SigningKey`, `VerifyKey`, `KeyPair`, `Sodium`, `NaCl`) |
