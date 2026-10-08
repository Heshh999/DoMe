"""Opaque tokens: generation and one-way hashing. Plain values exist only in memory and in the
single response that hands them to their owner; the database stores digests."""

from __future__ import annotations

import hashlib
import hmac
import secrets

TOKEN_CHARS = 43  # base64url of 32 random bytes
USER_CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32 (no I, L, O, U)


def new_token() -> str:
    """32 random bytes, base64url without padding (43 chars)."""
    return secrets.token_urlsafe(32)


def sha256(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def hmac_sha256(secret: str, value: str) -> bytes:
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).digest()


def new_user_code() -> str:
    """Human-typed device-link code ``ABCD-EFGH`` (40 bits, Crockford base32)."""
    raw = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_user_code(text: str) -> str | None:
    cleaned = "".join(ch for ch in text.upper() if ch not in " -_")
    cleaned = cleaned.replace("I", "1").replace("L", "1").replace("O", "0")
    if len(cleaned) != 8 or any(ch not in USER_CODE_ALPHABET for ch in cleaned):
        return None
    return f"{cleaned[:4]}-{cleaned[4:]}"


def ip_hash(secret: str, ip: str | None) -> bytes | None:
    if not ip:
        return None
    return hmac_sha256(secret, "ip|" + ip)
