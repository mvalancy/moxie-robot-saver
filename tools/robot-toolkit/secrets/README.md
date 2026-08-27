# 🔓 libsecrets extractor

Recovers the factory secrets from `libsecrets.so` (the native lib behind
`me.embodied.productiontesting.Secrets`) by **emulating the real getter code** under Unicorn with a
mocked JNIEnv + libc — no Android device needed.

## The algorithm (reverse-engineered)

Each JNI getter (`getDBUsername/Password`, `getEmbodiedPSK`, `getEmbodiedStaffPSK`,
`getFTPUsername/Password`) holds an obfuscated byte blob and calls
`getOriginalKey(blob, len, packageName, JNIEnv*)`, which does a **repeating-key XOR**:

```
key      = ASCII( hex( sha256(packageName) ) )   # 64 hex chars of sha256("me.embodied.productiontesting")
out[i]   = blob[i] XOR key[i % 64]
return NewStringUTF(out)
```

So the "encryption" is a **repeating-XOR with the hex-SHA256 of the package name**. (Verified from the
`getOriginalKey` Thumb disasm: build `keybuf = hex(sha256(pkg))` → `ldrb blob[i]` → `i % 64` → `eor`
→ `strb out[i]`.) Note: the lib's own SHA256 miscomputes under Unicorn, so the harness captures the
blob by emulation but derives the key with Python's `hashlib` — yielding clean plaintext.

## Run

```sh
pip install unicorn capstone pyelftools protobuf
python emulate_secrets.py /path/to/libsecrets.so
```

It maps the ELF, applies relocations, resolves internal calls (SHA256/getOriginalKey) to their real
addresses, stubs libc (`memcpy`, `memclr`, `strlen`, `sprintf`, `uidivmod`) and the four JNIEnv string
ops, then runs each `Java_..._get*` export and prints `secret,value`.

> **Values are not committed.** They decrypt live-ish factory credentials (for now-defunct infra);
> run the tool locally to get them. All six getters now recover clean values. Run locally to see them.
