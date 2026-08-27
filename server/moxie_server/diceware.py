"""
Diceware recovery-phrase generator — mirrors the app's
api/crypto/diceware/Passphrase.java. 8 words from the EFF short wordlist
(1296 = 6^4 entries), joined with '-'. ~82.7 bits of entropy.

The phrase (not the dice code) is the Argon2id input in crypto.derive_seed().
We generate the phrase here and show it to the user as their recovery key,
exactly like the app's ExportRecoveryKey screen.
"""
from __future__ import annotations
import os, secrets
from functools import lru_cache

WORD_COUNT = 8
SEPARATOR = "-"
_WORDLIST_PATH = os.path.join(os.path.dirname(__file__), "data", "eff_short_wordlist_1.txt")


@lru_cache(maxsize=1)
def _load() -> dict[str, str]:
    m = {}
    with open(_WORDLIST_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            m[parts[0]] = parts[-1]     # "1111" -> "acid"
    return m


def generate_phrase() -> str:
    """Return a dashed 8-word phrase, e.g. 'acid-acorn-...-zoom'."""
    words = list(_load().values())
    return SEPARATOR.join(secrets.choice(words) for _ in range(WORD_COUNT))


def is_valid_phrase(phrase: str) -> bool:
    words = set(_load().values())
    parts = phrase.strip().split(SEPARATOR)
    return len(parts) == WORD_COUNT and all(w in words for w in parts)
