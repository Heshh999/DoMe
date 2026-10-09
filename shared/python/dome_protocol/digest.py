"""Digests and pairing helpers. Every function here has a byte-identical TypeScript twin and
is covered by the cross-language fixtures."""

from __future__ import annotations

import hashlib
import hmac
import secrets

from .errors import ProtocolError
from .keys import b64url_encode

# Crockford base32: no I, L, O, U; case-insensitive on input.
PAIRING_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
PAIRING_CODE_SYMBOLS = 20  # 100 bits of entropy; the code is a MAC key, not just a lookup handle
_CROCKFORD_ALIASES = {"I": "1", "L": "1", "O": "0"}


def sha256_b64url(data: bytes) -> str:
    return b64url_encode(hashlib.sha256(data).digest())


def challenge_digest(challenge_text: str) -> str:
    """Digest of the challenge_text exactly as emitted by the PC and received by the controller."""
    return sha256_b64url(challenge_text.encode("utf-8"))


def command_digest(payload_text: str) -> str:
    """Digest of the exact signed payload bytes.

    The durable journal keys on command_id; a later submission with the same id is a duplicate
    only when this digest matches (i.e. the very same signed bytes were retransmitted).
    """
    return sha256_b64url(payload_text.encode("utf-8"))


# ----- pairing ---------------------------------------------------------------------------------


def generate_pairing_code() -> str:
    """20 Crockford symbols generated on the PC. The backend never sees this value."""
    return "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(PAIRING_CODE_SYMBOLS))


def normalize_pairing_code(text: str) -> str:
    """Uppercase, drop separators/whitespace, map I/L→1 and O→0, validate length and alphabet."""
    if not isinstance(text, str):
        raise ProtocolError("PAIRING_CODE_INVALID", "pairing code must be text")
    cleaned = "".join(ch for ch in text.upper() if ch not in " -_\t\r\n.")
    cleaned = "".join(_CROCKFORD_ALIASES.get(ch, ch) for ch in cleaned)
    if len(cleaned) != PAIRING_CODE_SYMBOLS or any(ch not in PAIRING_ALPHABET for ch in cleaned):
        raise ProtocolError("PAIRING_CODE_INVALID", "pairing code is not valid")
    return cleaned


def format_pairing_code(code: str) -> str:
    code = normalize_pairing_code(code)
    return "-".join(code[i : i + 5] for i in range(0, len(code), 5))


def pairing_code_handle(code: str) -> str:
    """Lookup handle sent to the backend by both devices: SHA-256 of the normalised code."""
    code = normalize_pairing_code(code)
    return sha256_b64url(f"dome-pair-handle-v1|{code}".encode())


def pairing_verification_code(code: str, pairing_id: str, pc_id: str, kid: str) -> str:
    """6-digit code shown on both devices during pairing.

    HMAC-SHA256 keyed by the out-of-band pairing code over (pairing_id, pc_id, controller kid).
    The backend never holds the key, so it cannot grind a substitute controller key whose code
    matches what the phone displays.
    """
    key = normalize_pairing_code(code).encode("ascii")
    msg = f"dome-pair-verify-v1|{pairing_id}|{pc_id}|{kid}".encode()
    digest = hmac.new(key, msg, hashlib.sha256).digest()
    number = int.from_bytes(digest[:8], "big") % 1_000_000
    return f"{number:06d}"


__all__ = [
    "PAIRING_ALPHABET",
    "PAIRING_CODE_SYMBOLS",
    "challenge_digest",
    "command_digest",
    "format_pairing_code",
    "generate_pairing_code",
    "normalize_pairing_code",
    "pairing_code_handle",
    "pairing_verification_code",
    "sha256_b64url",
]
