"""ES256 key handling: JWK <-> cryptography objects, RFC 7638 thumbprints."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .errors import ProtocolError


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str, *, expected_len: int | None = None) -> bytes:
    if not isinstance(text, str) or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in text):
        raise ProtocolError("MALFORMED_MESSAGE", "invalid base64url")
    pad = "=" * (-len(text) % 4)
    try:
        out = base64.urlsafe_b64decode(text + pad)
    except (ValueError, TypeError) as exc:
        raise ProtocolError("MALFORMED_MESSAGE", "invalid base64url") from exc
    if expected_len is not None and len(out) != expected_len:
        raise ProtocolError("MALFORMED_MESSAGE", f"expected {expected_len} bytes")
    return out


def generate_private_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def private_key_to_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def private_key_from_pem(pem: bytes) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ProtocolError("MALFORMED_MESSAGE", "key is not P-256")
    return key


def jwk_from_public_key(key: ec.EllipticCurvePublicKey) -> dict[str, str]:
    if not isinstance(key.curve, ec.SECP256R1):
        raise ProtocolError("MALFORMED_MESSAGE", "key is not P-256")
    nums = key.public_numbers()
    return {
        "kty": "EC",
        "crv": "P-256",
        "x": b64url_encode(nums.x.to_bytes(32, "big")),
        "y": b64url_encode(nums.y.to_bytes(32, "big")),
    }


def public_key_from_jwk(jwk: Any) -> ec.EllipticCurvePublicKey:
    if not isinstance(jwk, dict):
        raise ProtocolError("MALFORMED_MESSAGE", "JWK must be an object")
    allowed = {"kty", "crv", "x", "y"}
    if set(jwk) != allowed:
        raise ProtocolError("MALFORMED_MESSAGE", "JWK must have exactly kty, crv, x, y")
    if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise ProtocolError("MALFORMED_MESSAGE", "JWK must be EC P-256")
    x = int.from_bytes(b64url_decode(jwk["x"], expected_len=32), "big")
    y = int.from_bytes(b64url_decode(jwk["y"], expected_len=32), "big")
    try:
        return ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1()).public_key()
    except ValueError as exc:  # point not on curve
        raise ProtocolError("MALFORMED_MESSAGE", "JWK point is not on the curve") from exc


def kid_from_jwk(jwk: dict[str, str]) -> str:
    """RFC 7638 JWK thumbprint (SHA-256) of the public members, base64url."""
    public_key_from_jwk(jwk)  # validates shape and curve point
    canonical = json.dumps(
        {"crv": jwk["crv"], "kty": jwk["kty"], "x": jwk["x"], "y": jwk["y"]},
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=True,
    )
    return b64url_encode(hashlib.sha256(canonical.encode("ascii")).digest())
