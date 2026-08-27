"""
Clean-room reimplementation of the Moxie parent app's crypto (see
docs/CRYPTO_AND_PAIRING.md). Everything hangs off ONE 32-byte seed:

    seed = Argon2id(passphrase, salt=16*0x00, ops=2, mem=64MiB, out=32)

From that seed the app derives (deterministically):
  * an Ed25519 signing key   (seed is the Ed25519 *seed*)
  * an X25519 box keypair     (same seed)
  * an XSalsa20-Poly1305 key  (the seed itself)

The pairing QR carries the raw seed. The server only ever sees
hex(SHA256(seed)) and sealed-box copies of the seed — it is zero-knowledge.

This module lets our LOCAL server play BOTH roles: the account backend AND
the "app" that derives keys and builds the pairing QR (because our UI is a
thin browser client, the crypto lives here on the trusted local machine).
"""
from __future__ import annotations
import hashlib
from dataclasses import dataclass

from nacl.pwhash.argon2id import kdf as _argon2id_kdf
from nacl.signing import SigningKey
from nacl.public import PrivateKey, PublicKey, SealedBox
from nacl.secret import SecretBox
from nacl import utils as nacl_utils

# --- exact libsodium 1.0.16 "interactive" params used by RecoveryKey.hash() ---
ARGON2_OPSLIMIT = 2
ARGON2_MEMLIMIT = 67108864          # 64 MiB
ARGON2_SALT = b"\x00" * 16          # RecoveryKey.java: salt buffer never written
SEED_BYTES = 32


def derive_seed(passphrase: str) -> bytes:
    """passphrase (dashed 8-word diceware phrase, trimmed) -> 32-byte seed.
    Matches CryptoHelper/RecoveryKey exactly, so a phrase reproduces the same
    key material as the original app, offline, on any machine."""
    raw = passphrase.strip().encode("utf-8")
    return _argon2id_kdf(SEED_BYTES, raw, ARGON2_SALT,
                         opslimit=ARGON2_OPSLIMIT, memlimit=ARGON2_MEMLIMIT)


@dataclass
class MoxieKeys:
    seed: bytes
    ed25519_public: bytes      # 32B Ed25519 verify key
    x25519_public: bytes       # 32B Curve25519 public key
    _x25519_private: PrivateKey

    @property
    def secret_hash_hex(self) -> str:
        """hex(SHA256(seed)) — the pairing rendezvous id sent to POST pairing-info."""
        return hashlib.sha256(self.seed).hexdigest()

    def secretbox_encrypt(self, plaintext: bytes) -> bytes:
        """XSalsa20-Poly1305 with key=seed, framed nonce(24)||mac(16)||ct (as SecretBox.java)."""
        box = SecretBox(self.seed)
        nonce = nacl_utils.random(SecretBox.NONCE_SIZE)
        enc = box.encrypt(plaintext, nonce)     # returns nonce||ct; layout matches app
        return bytes(enc)

    def secretbox_decrypt(self, blob: bytes) -> bytes:
        return SecretBox(self.seed).decrypt(blob)

    def seal_seed_to(self, recipient_x25519_pub: bytes) -> bytes:
        """crypto_box_seal(seed, recipient_pub) — the value stored per-pubkey in
        secret-key-collection so the robot can recover the symmetric key."""
        return SealedBox(PublicKey(recipient_x25519_pub)).encrypt(self.seed)


def keys_from_seed(seed: bytes) -> MoxieKeys:
    if len(seed) != SEED_BYTES:
        raise ValueError("seed must be 32 bytes")
    ed_pub = bytes(SigningKey(seed).verify_key)
    x_priv = PrivateKey(seed)                    # X25519 seeded identically
    x_pub = bytes(x_priv.public_key)
    return MoxieKeys(seed=seed, ed25519_public=ed_pub,
                     x25519_public=x_pub, _x25519_private=x_priv)


def keys_from_passphrase(passphrase: str) -> MoxieKeys:
    return keys_from_seed(derive_seed(passphrase))
