# 🔓 libsecrets extractor

Recovers the factory secrets from `libsecrets.so` (the native lib behind
`me.embodied.productiontesting.Secrets`) by **emulating the real getter code** under Unicorn with a
mocked JNIEnv + libc — no Android device needed.

## The algorithm (reverse-engineered)

Each JNI getter (`getDBUsername/Password`, `getEmbodiedPSK`, `getEmbodiedStaffPSK`,
`getFTPUsername/Password`) holds an obfuscated byte blob and calls
`getOriginalKey(blob, len, packageName, JNIEnv*)`, which does a **repeating-key XOR**:

```
key      = GetStringUTFChars(packageName)      # "me.embodied.productiontesting"
out[i]   = blob[i] XOR key[i % len(key)]
return NewStringUTF(out)
```

So the "encryption" is just **XOR with the package-name string**. (Verified from the `getOriginalKey`
Thumb disassembly: `ldrb blob[i]` → `strlen(key)` → `i % keylen` → `eor` → `strb out[i]`.)

## Run

```sh
pip install unicorn capstone pyelftools protobuf
python emulate_secrets.py /path/to/libsecrets.so
```

It maps the ELF, applies relocations, resolves internal calls (SHA256/getOriginalKey) to their real
addresses, stubs libc (`memcpy`, `memclr`, `strlen`, `sprintf`, `uidivmod`) and the four JNIEnv string
ops, then runs each `Java_..._get*` export and prints `secret,value`.

> **Values are not committed.** They decrypt live-ish factory credentials (for now-defunct infra);
> run the tool locally to get them. The long PSK getters recover clean printable strings; the short
> DB/FTP getters need a blob-length validation pass (tracked in PLAN.md).
