# Moxie Parent App — Cryptography & Key Management

**Summary.** All of the app's cryptography hangs off one 32-byte **seed**. The seed is the Argon2id hash
(with an all-zero salt) of an 8-word EFF diceware recovery passphrase. That one seed serves three roles at
once: the Ed25519 signing seed, the X25519 box seed, and the XSalsa20-Poly1305 key that encrypts every
`*-encrypted` child field. It is also exactly what the pairing QR hands to the robot. The server only ever sees
`hex(SHA-256(seed))` and sealed-box copies of the seed, so a replacement server stores opaque blobs and
never decrypts anything. Our implementation is [`server/moxie_server/crypto.py`](../../../server/moxie_server/crypto.py).

> Clean-room notes on the original app (`com.embo.embodied.parent` v2.2.2). Paths such as
> `api/crypto/CryptoHelper.java` are locations *inside that app* (under `…/com/embo/embodied/parent`), not
> files in this repo.

Native code: `lib/arm64-v8a/libsodiumjni.so` contains **libsodium 1.0.16** (the version string is confirmed in the
`.so`). The app loads it through `org.libsodium.jni.NaCl` (`System.loadLibrary("sodiumjni")`) and calls
`Sodium.sodium_init()` on every call.

---

## 0. The master secret: one seed, three keys

`api/crypto/CryptoHelper.java:142-149`:

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

| Artifact | Algorithm | libsodium call | Size |
|---|---|---|---|
| `signingKey` | Ed25519 | `crypto_sign_ed25519_seed_keypair(pk, sk, seed)` | pk 32 B, sk 64 B, seed 32 B |
| `encryptionKeyPair` | X25519 | `crypto_box_curve25519xsalsa20poly1305_seed_keypair(pk, sk, seed)` | pk 32 B, sk 32 B |
| `cryptoSecretBox` key | XSalsa20-Poly1305 | `crypto_secretbox_easy` | key = **the seed itself**, 32 B |

---

## 1. `getSigningKey()`

`CryptoHelper.getSigningKey()` returns an `org.libsodium.jni.keys.SigningKey`, from the deprecated
libsodium-jni wrapper:

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
    public byte[] sign(byte[] bArr) {                        // crypto_sign (combined), returns
        byte[] prependZeros = Util.prependZeros(64, bArr);   // the first 64 bytes = detached signature
        NaCl.sodium();
        Sodium.crypto_sign_ed25519(prependZeros, new int[1], bArr, bArr.length, this.secretKey);
        return Util.slice(prependZeros, 0, 64);
    }
    public byte[] toBytes() { return this.seed; }            // <-- THE SEED, NOT the 64-byte sk
    public String toString() { return Encoder.HEX.encode(this.seed); }
}
```

- **Key type:** Ed25519, used for detached 64-byte signatures. It is verified with `VerifyKey.verify(msg, sig64)`, which
  calls `crypto_sign_ed25519_open`.
- **Never random.** The default `SigningKey()` constructor (`new Random().randomBytes(32)`) is dead code. The app
  only ever calls `new SigningKey(seed)`. The same passphrase gives the same key on any device.
- **Never persisted.** The key lives only in the `CryptoHelper` singleton, in RAM. It is re-derived from the
  stored passphrase *code*:
  - `ppcrk` holds the 32-character diceware code (8 words × 4 dice digits) in `EncryptedSharedPreferences`
    (file `EmbodiedApp`, AES256-SIV keys, AES256-GCM values, Android Keystore master key;
    `api/SecureSharedPreference.java`).
  - `client_public_key` holds the base64 X25519 public key (`Config.storeClientPublicKey`). It is only a
    "did the key change?" tripwire (`CryptoHelper.isClientKeyChanged()`, used to detect "the user reset
    their key on another device"). It plays no part in AUID encryption.
  - Re-derivation entry points: `MainActivity.java:47` calls `CryptoHelper.restoreSymmetricKey()`, and
    `Config.storePassPhraseCode()` (`api/Config.java:424-431`) calls `restoreSymmetricKey()` right after
    it writes.

The pairing QR embeds `getSigningKey().toBytes()`, which is **the raw 32-byte seed**. From the seed the robot
can regenerate all three keys. See [`qr-format.md`](qr-format.md) for the byte layout.

---

## 2. `serectHashFromKey()`: the pairing rendezvous id

`pair_moxie/ProtoPairing.java:36-45` (`serect` is a typo in the original):

```java
public static String serectHashFromKey(byte[] bArr) {
    MessageDigest messageDigest = MessageDigest.getInstance("SHA-256");
    messageDigest.update(bArr);
    return bytesToHexString(messageDigest.digest());   // zero-padded lowercase hex, 64 chars
}
```

The phone sends `hex(SHA-256(seed))` as `id` on
`POST pairing-info?id=…&restore=…&user-id=…&child-id=…`. The call site and exact request are in
[`pairing-and-robot.md` §3](pairing-and-robot.md#3-the-pairing-sequence). The robot scans the seed, so it can
compute the same hash. The hash is the only value the two halves of the flow share, so it must be what
the cloud uses to match the robot to the pending user/child record. (On the robot side,
`UserPairingRequest` is bound by the QR's `secret_key`, and the robot also registers its RSA device
key. See [`cloud-protocol.md`](../protocol/cloud-protocol.md#robot-authentication-device-identity).)

Key flow as the server sees it:

1. The phone derives the seed and its keys from the passphrase.
2. `PUT secret-key-collection` stores `{ b64(pubkey): b64(sealed seed) }`, sealed to the user's public key
   and, once it exists, the robot's (§3).
3. `POST pairing-info` records `hex(SHA-256(seed))` against user-id/child-id and the restore flag.
4. The QR hands the robot the raw seed. The robot proves knowledge of it, and the cloud binds the robot to the user/child.
5. The robot publishes its own X25519 `public-key` on its robot record. `CryptoManager.updateKeysIfNeeded()`
   runs again, seals the seed to the robot's key too, and PUTs it. The robot can then `crypto_box_seal_open`
   its copy.

---

## 3. `secret-key-collection`

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

- On a public-key mismatch it logs *"derived public key does not match current user's public key, which is
  usually because the user's public key was reset on a different device. User will need to
  re-authenticate."*
- `gatherPublicKeys()` reads `users/me` `attributes["public-key"]` and `robots/{id}` `attributes["public-key"]`
  (both base64). If either is missing it returns `null` and the update is skipped.
- `PublicKey.getEncrypted()` is misnamed. The field holds the **plain base64 public key**; the constructor
  parameter is simply called `encrypted`.
- `encryptSeal` calls `SealBox.encrypt`, which is `crypto_box_seal(out, msg, msglen, recipient_pk)`: an anonymous sealed box
  with an ephemeral X25519 public key and `crypto_box_SEALBYTES = 48`. The ciphertext is 48 + 32 = **80 bytes**, which is 108 base64 characters.
- The drivers are `CryptoManager.updateKeysIfNeeded()`, an observer on the user LiveData
  (`setupObservers`), and an explicit call from `PairMoxieQrCodeFragment.updateKeysInServer()` on the
  legacy JSON-mode path. Local status codes: `114` means key-update error and `115` means already up to date.
- Wire (`api/models/user/UpdateKeysModel.java`, `SecretKeysIndexedByPublic.java`):

```
PUT {base}/api/secret-key-collection      Authorization: Bearer <token>
{ "secret_key_collection": {
    "secret-keys-indexed-by-public-keys": {
      "<base64 user X25519 pubkey>":  "<base64 sealed 32-byte seed>",
      "<base64 robot X25519 pubkey>": "<base64 sealed 32-byte seed>" } } }
```

---

## 4. Recovery key: diceware → Argon2id → seed

### 4a. Wordlist and passphrase generation

The asset `assets/eff_short_wordlist_1.txt` is the standard EFF short wordlist #1: **1296 lines** (6^4), words of
5 characters or fewer, and TAB-separated `<4-digit dice code>\t<word>` pairs (for example `1111\tacid`, `1112\tacorn`).

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
        sb.append(str);                       // code: concatenated 4-digit codes (32 chars)
        if (i != 0) sb2.append("-");
        sb2.append(str2);                     // word: "acid-acorn-...-zoom" (8 words, 7 dashes)
    }
    return new PhrasePair(sb.toString(), sb2.toString());
}
public String getPhraseFromCode(String str) {     // 32-char code -> dashed 8-word phrase
    if (str.length() % 4 != 0 || str.length() / 4 != this.keyPhraseLength) return null;
    ... substring(i, i+4) -> wordsMap.get(...) ... joined with "-"
}
public String getCodeFromPassphrase(String str) { // dashed phrase -> 32-char code (reverse lookup)
    for (String str2 : str.split("-")) sb.append((String) Utils.getKeyByValue(this.wordsMap, str2));
    return sb.toString();
}
```

Entropy is 8 × log2(1296) ≈ **82.7 bits** (`SecureRandom.nextInt(1296)`). **The KDF input is the dashed
*word* string, not the dice code.** The app stores the code (`ppcrk`) and converts it back to the phrase
before hashing. `RecoveryKey.test()` uses the literal `"test-recovery-key"`, a debug helper that the production
paths never call.

### 4b. The KDF — `api/crypto/RecoveryKey.java`

```java
byte[] deriveSeed() { NaCl.sodium(); return hash(Sodium.crypto_box_seedbytes()); }   // 32

private byte[] hash(int i) throws Exception {
    byte[] bArr = new byte[i];                                        // out, 32 bytes
    byte[] bArr2 = new byte[Sodium.crypto_pwhash_saltbytes()];        // SALT = 16 ZERO BYTES  <-- !!
    byte[] bytes  = this.rawValue.getBytes();                         // passphrase (UTF-8)
    int crypto_pwhash = Sodium.crypto_pwhash(bArr, i, bytes, bytes.length, bArr2,
                                             Sodium.crypto_pwhash_opslimit_interactive(),
                                             Sodium.crypto_pwhash_memlimit_interactive(),
                                             Sodium.crypto_pwhash_alg_default());
    Log.d(TAG, "hash: isHashSucceeded = " + crypto_pwhash);
    return bArr;                                        // returned even on failure (non-zero rc)
}
```

(`NaCl.sodium()` precedes each `Sodium.*` call. `rawValue` is `str.trim()` of the dashed phrase.)

| Parameter | Value (libsodium 1.0.16) |
|---|---|
| Algorithm | `crypto_pwhash_ALG_DEFAULT` = `ALG_ARGON2ID13` = **2** (Argon2id v1.3; the `crypto_pwhash_argon2id_*` symbols are present in the `.so`) |
| Password | UTF-8 bytes of `"word1-word2-...-word8"`, trimmed |
| Salt | **16 × `0x00`** (`crypto_pwhash_SALTBYTES` = 16; the buffer is never written, `RecoveryKey.java:33`) |
| opslimit | `OPSLIMIT_INTERACTIVE` = **2** |
| memlimit | `MEMLIMIT_INTERACTIVE` = **67108864** (64 MiB) |
| Parallelism | 1 (fixed by libsodium) |
| Output | **32 bytes** (`crypto_box_SEEDBYTES`) |

```python
from nacl.pwhash.argon2id import kdf as argon2id_kdf   # PyNaCl
seed = argon2id_kdf(32, phrase.strip().encode("utf-8"), b"\x00"*16, opslimit=2, memlimit=67108864)
```
```c
crypto_pwhash(seed, 32, phrase, strlen(phrase), zero_salt16, 2, 67108864, crypto_pwhash_ALG_ARGON2ID13);
```

### 4c. Export, enter, and silent restore

**Export** (first-time setup, `recovery_key/ExportRecoveryKeyFragment.java:114-145`) generates a phrase, stores
its code in `ppcrk`, shows the words, and publishes the X25519 public key:

```java
PhrasePair generateRecoveryKey = Passphrase.getInstance().generateRecoveryKey();
Config.storePassPhraseCode(generateRecoveryKey.getCode());
this.mBinding.recoveryKeyText.setText(generateRecoveryKey.getWord());
String encodeAsString = Encoder.encodeAsString(
        CryptoHelper.getInstance().generateKeyPair(generateRecoveryKey.getWord()).getPublicKey().toBytes());
userAttributes.setPublicKey(encodeAsString);
... updateUserRequest(userAttributes, ...)          // PUT users/me {"public-key": ...}
```

**Enter** (`recovery_key/EnterRecoveryKeyFragment.java:122-131`, `onSubmitClicked`) is where the user types the phrase back in:

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
getBaseActivity().runActionAfterSetupRecoveryKey();
```

> **Validation happens only on the phone.** The app checks a phrase by comparing the derived X25519
> public key with `users/me` `attributes["public-key"]`. No network round trip is involved. A replacement server must return that
> field verbatim (base64 of the 32-byte key), or the app rejects every phrase. When the phrase matches, the phone
> holds the same seed as before, so the next QR produces the same SHA-256 the cloud already knows.

If the user picks *"Continue without recovery key"*, the app calls `Config.storePassPhraseCode(null)` and forces the user
through Export. That generates a **new** phrase, so the key changes, which means re-pairing and re-uploading
`secret-key-collection`.

**Silent restore** on app start (`CryptoHelper.java:69-76`):

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

`restoreKeyPair(String expectedPubKeyB64)` (`CryptoHelper.java:50-67`) does the same derivation, then checks the derived
public key against its argument and nulls both keys on a mismatch. `UserInfoViewModel.fetchUserInfo` (line 48)
calls it lazily on every `users/me` fetch when the user record has a `public-key`, the app holds no keypair yet, and
`ppcrk` is set. `BaseActivity.setupRecoveryKeyController()` (`BaseActivity.java:1754-1772`) decides which screen
comes next. If the user record has no `public-key`, it clears `ppcrk` and goes to Export. If `restoreKeyPair` fails,
it goes to Enter. `LaunchActivity` also blocks entry on `RestorationValidator.checkRecoveryKey()`.

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

The ciphertext layout is **`nonce(24) || mac(16) || ciphertext(n)`**, encoded as base64 `NO_WRAP` (`api/crypto/Encoder.java` uses
flag 2 throughout). `decrypt()` splits at byte 24 and calls `crypto_secretbox_open_easy`. The size constants come from
`crypto_secretbox_xsalsa20poly1305_*`.

### 5b. Field-level encryption — `api/models/Child.java:177-196` / `asDecryptedData`

Every `ChildrenModel` key that ends in `-encrypted` goes through `CryptoHelper.encrypt()/decrypt()`
automatically:

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

Decryption mirrors this with `StringHelper.removeQuotes`. The five keys listed above hold JSON arrays or objects, so they are not
wrapped in quotes. The full list of `-encrypted` keys is in [`rest-api.md` §3.3](rest-api.md#33-children).
**A replacement server stores these as opaque base64 blobs.** Only the app holds the key, plus the robot through
the sealed `secret-key-collection`.

**Robot-side mirror (`embodied.logging.Cloud.proto`).** The robot receives the same scheme as two protos.
**`ChildEncrypted`** has `bytes *_encrypted` for `first_name`, `last_name`, `nickname`, `birthday`,
`therapy_needs`, `self_regulation_tools_preferences`, `likes_imaginative_play`, `volume_preference`, and
`calendar_events`, plus a `checksum`, alongside *clear* metadata (`id`, `starbits`, `face_options`,
`content_preferences`, `family`, `input_speed`, `grl_connect_enabled`, `holiday_events`, `unlimited_time`).
**`ChildDecrypted`** has the same fields in plaintext, plus `birthday_ts`. So child PII is end-to-end encrypted
from app to cloud to robot. The robot decrypts with the seed-derived key, and only after that is `nickname` available for
prompts (`child_pii.nickname`). `therapy_needs` and `self_regulation_tools_preferences` are health-adjacent,
which fits the `EMBODIED_HIPAA` endpoint ([`qr-commands.md`](../protocol/qr-commands.md)).

### 5c. AUID (the child's anonymous analytics id)

- **At rest:** `ChildrenModel.auid` is `@SerializedName("auid-encrypted")` (`ChildrenModel.java:22`), a
  SecretBox blob that the `-encrypted` sweep decrypts. `RequestManager.auid(selectedChild, AUIDCallback)`
  (`RequestManager.java:814-830`) returns the already-decrypted value, or `"foo"` in the insights-demo build.
- **In use:** the **plaintext** AUID is sent as the `auid` query parameter on `GET analytics/pages/{id}` and
  `GET analytics/pages/details`. Only TLS protects it in transit.
- **`analytics/auid-encrypted`** (`API_ANALYTICS_AUID_ENCRYPTED`, `APIService.java:51-52`) is declared, but
  nothing calls it. A server can stub it or leave it out.
- **`help/share-auid`:** `GET help` returns `encrypted_auids` (`GetHelpModel.java:16`), a list of SecretBox
  blobs. `HelpFragment` (`main/account/help/HelpFragment.java:282-292`) decrypts each one and POSTs the **plaintext**
  list as `{"auids":[...], "mode": temporary|permanent|revoke|revoke_all|none}`. This is a de-anonymization
  the user explicitly consents to, so support can look the child up.

---

## 6. Hardcoded keys, salts, and constants

OAuth client ids/secrets, base URLs, and the status-code table are in [`rest-api.md` §1](rest-api.md#1-transport-base-urls-headers-client-credentials).

| Constant | Value | Where |
|---|---|---|
| **Argon2id salt** | **16 × `0x00`** | `api/crypto/RecoveryKey.java:33` |
| Argon2id ops / mem / alg | 2 / 67108864 / ARGON2ID13 | `RecoveryKey.hash()` |
| Diceware word count | `keyPhraseLength = 8` | `diceware/Passphrase.java:19` |
| Wordlist | `eff_short_wordlist_1.txt`, 1296 entries | `assets/`, `Passphrase.init()` |
| Passphrase separator | `"-"` | `Passphrase` |
| Test recovery key | `"test-recovery-key"` | `RecoveryKey.test()` (debug only) |
| QR header | `"PA"` | `ProtoPairing.PROTO_PAIR_HEADER` |
| Prefs key: passphrase code | `"ppcrk"` | `api/Config.java:124` |
| Prefs key: client pubkey | `"client_public_key"` | `api/Config.java:109` |
| Curve25519 base point | `0900000000000000000000000000000000000000000000000000000000000000` | `org/libsodium/jni/crypto/Point.STANDARD_GROUP_ELEMENT` (used only on a `publicKey == null` fallback that never triggers) |
| Keystore master key | `MasterKey.DEFAULT_MASTER_KEY_ALIAS` (`"_androidx_security_master_key_"`), AES-256-GCM, no padding | `SecureSharedPreference.getMasterKey` |
| Prefs file | `"EmbodiedApp"` | `EmbodiedApplication.java:42` |
| Local status codes | 112 AUID_NOT_FOUND, 113 NO_DATA, 114 KEY_UPDATE_ERROR, 115 KEY_UPDATED, 116 NO_NETWORK, 117 TOKEN_FAILED | `api/Config.java:127-140` |

Google Tink's Ed25519 (`com/google/crypto/tink/subtle/Ed25519*.java`) also ships, as a transitive dependency of
`androidx.security.crypto`. None of the app's own crypto uses it.

---

## 7. Security observations relevant to a reimplementation

1. **Zero salt.** The seed is a pure function of the passphrase, so it can be precomputed across all users. That also makes
   reimplementation easy: any passphrase reproduces every key offline.
2. **One seed, three primitives.** The same 32 bytes serve as the Ed25519 seed, the X25519 seed, and the secretbox key.
3. **The QR contains the raw seed** in a plain base64 protobuf, with no expiry, no binding to a robot, and no signature. A photo
   of the pairing screen yields the Wi-Fi password *and* the key to all of the child's encrypted data.
4. **`RecoveryKey.hash()` ignores the `crypto_pwhash` return code.** An allocation failure would silently produce an
   all-zero "seed".
5. `CryptoHelper.decryptSeal()` (`CryptoHelper.java:120-132`) calls `SealBox.decrypt` and then returns `null`
   unconditionally. It is dead or broken, and nothing depends on it.
6. `getPairQRMode()` always returns `PAIR_PROTO_KEY`, so a replacement robot only needs the `"PA"` format.
7. The server is a **zero-knowledge store**: SecretBox blobs, sealed copies of the seed, and `hex(SHA-256(seed))`. It
   must persist and return them faithfully. It never needs to decrypt them, and it cannot.

---

## 8. File index (inside the original app)

| Path (under `…/com/embo/embodied/parent`) | Role |
|---|---|
| `api/crypto/CryptoHelper.java` | Singleton that owns the signing key, X25519 keypair, and secretbox. Handles derive, restore, encrypt/decrypt, and seal. |
| `api/crypto/RecoveryKey.java` | Argon2id KDF (zero salt) |
| `api/crypto/diceware/Passphrase.java`, `PhrasePair.java` | Wordlist loader, 8-word generation, code↔phrase conversion, `(code, word)` pair |
| `api/crypto/SecretBox.java`, `SealBox.java`, `KeyPair.java` | `nonce‖mac‖ct` secretbox; `crypto_box_seal`/`_open`; X25519 from seed |
| `api/crypto/PublicKey.java`, `NonceGenerator.java`, `Encoder.java`, `StringHelper.java` | base64 pubkey DTO; `randombytes_buf`; base64 `NO_WRAP`; quote add/remove |
| `api/CryptoManager.java` | Builds and PUTs `secret-key-collection` |
| `api/Config.java`, `api/SecureSharedPreference.java` | prefs keys, endpoints; `EncryptedSharedPreferences` |
| `api/RequestManager.java`, `api/APIService.java` | `registerForPairing`, `updateKeys`, `auid`, `shareAUIDs` |
| `api/models/user/{UpdateKeysModel,SecretKeysIndexedByPublic,ChildrenModel}.java`, `api/models/Child.java` | key-collection body; `*-encrypted` fields and sweep |
| `pair_moxie/{ProtoPairing,PairMoxieWifiFragment,PairMoxieQrCodeFragment}.java` | QR encoder + hash; `registerForPairing` before the QR; `updateKeysInServer` |
| `recovery_key/{Export,Enter}RecoveryKeyFragment.java` | publish `public-key`; restore from phrase |
| `BaseActivity.java:1754`, `MainActivity.java:47` | Export-vs-Enter decision; cold-start `restoreSymmetricKey()` |
| `org/libsodium/jni/**` | libsodium-jni wrappers (`SigningKey`, `VerifyKey`, `KeyPair`, `Sodium`, `NaCl`) |

---
📖 [Phone-side index](README.md) · [QR format](qr-format.md) · [Pairing](pairing-and-robot.md) · [Reverse-engineering index](../README.md)
