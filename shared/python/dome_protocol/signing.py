"""Detached ES256 envelopes.

Signer: ``sign_payload(private_key, payload_text)``.
Verifier: ``verify_envelope(envelope, resolve_jwk)`` — verifies the signature over the exact
UTF-8 bytes of ``payload`` and only then parses the payload exactly once with the strict parser.
Nothing from the payload is used before the signature is verified.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature

from .errors import ProtocolError
from .keys import b64url_decode, b64url_encode, jwk_from_public_key, kid_from_jwk, public_key_from_jwk
from .strict_json import DEFAULT_MAX_BYTES, DEFAULT_MAX_DEPTH, loads_strict

ENVELOPE_FIELDS = {"v", "alg", "kid", "payload", "sig"}
SUPPORTED_ALGS = {"ES256"}


@dataclass(slots=True, frozen=True)
class Envelope:
    v: int
    alg: str
    kid: str
    payload: str
    sig: str

    def to_dict(self) -> dict[str, Any]:
        return {"v": self.v, "alg": self.alg, "kid": self.kid, "payload": self.payload, "sig": self.sig}

    @classmethod
    def from_dict(cls, raw: Any) -> "Envelope":
        if not isinstance(raw, dict):
            raise ProtocolError("MALFORMED_MESSAGE", "envelope must be an object")
        if set(raw) != ENVELOPE_FIELDS:
            raise ProtocolError("MALFORMED_MESSAGE", "envelope must have exactly v, alg, kid, payload, sig")
        if raw["v"] != 1 or isinstance(raw["v"], bool):
            raise ProtocolError("PROTOCOL_INCOMPATIBLE", "unsupported envelope version")
        alg = raw["alg"]
        if not isinstance(alg, str) or alg not in SUPPORTED_ALGS:
            raise ProtocolError("SIGNATURE_INVALID", "unsupported signature algorithm")
        kid, payload, sig = raw["kid"], raw["payload"], raw["sig"]
        if not isinstance(kid, str) or len(kid) != 43:
            raise ProtocolError("MALFORMED_MESSAGE", "invalid kid")
        if not isinstance(payload, str) or len(payload) < 2:
            raise ProtocolError("MALFORMED_MESSAGE", "invalid payload")
        if not isinstance(sig, str) or len(sig) != 86:
            raise ProtocolError("SIGNATURE_INVALID", "invalid signature encoding")
        return cls(v=1, alg=alg, kid=kid, payload=payload, sig=sig)


def sign_payload(private_key: ec.EllipticCurvePrivateKey, payload_text: str) -> Envelope:
    if not isinstance(private_key.curve, ec.SECP256R1):
        raise ProtocolError("MALFORMED_MESSAGE", "signing key must be P-256")
    der = private_key.sign(payload_text.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    kid = kid_from_jwk(jwk_from_public_key(private_key.public_key()))
    return Envelope(v=1, alg="ES256", kid=kid, payload=payload_text, sig=b64url_encode(raw))


def verify_signature(public_key: ec.EllipticCurvePublicKey, payload_text: str, sig_b64url: str) -> None:
    raw = b64url_decode(sig_b64url, expected_len=64)
    r = int.from_bytes(raw[:32], "big")
    s = int.from_bytes(raw[32:], "big")
    if r == 0 or s == 0:
        raise ProtocolError("SIGNATURE_INVALID", "signature verification failed")
    try:
        public_key.verify(encode_dss_signature(r, s), payload_text.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError) as exc:
        raise ProtocolError("SIGNATURE_INVALID", "signature verification failed") from exc


def verify_envelope(
    raw_envelope: Any,
    resolve_jwk: Callable[[str], dict[str, str] | None],
    *,
    max_payload_bytes: int = DEFAULT_MAX_BYTES,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> tuple[Envelope, dict[str, Any]]:
    """Verify then parse. Returns (envelope, parsed_payload).

    ``resolve_jwk(kid)`` must return the public JWK for a *currently paired, non-revoked*
    controller or ``None``. The kid is additionally re-derived from the returned JWK so a
    mis-keyed store cannot make a signature from key A validate under kid B.
    """
    env = Envelope.from_dict(raw_envelope)
    if len(env.payload.encode("utf-8", errors="surrogatepass")) > max_payload_bytes:
        raise ProtocolError("PAYLOAD_TOO_LARGE", "payload too large")
    jwk = resolve_jwk(env.kid)
    if jwk is None:
        raise ProtocolError("UNKNOWN_KEY", "no paired key for kid")
    if kid_from_jwk(jwk) != env.kid:
        raise ProtocolError("UNKNOWN_KEY", "key id does not match stored key")
    public_key = public_key_from_jwk(jwk)
    verify_signature(public_key, env.payload, env.sig)
    parsed = loads_strict(env.payload, max_bytes=max_payload_bytes, max_depth=max_depth, require_object=True)
    return env, parsed
