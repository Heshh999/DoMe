"""DoMe shared protocol library.

Everything that crosses a trust boundary is defined by the JSON files in ``shared/protocol``.
This package gives the backend and the Windows agent one tested implementation of:

* strict JSON parsing (size, depth, duplicate keys, NaN/Infinity, lone surrogates rejected)
* ES256 detached-signature envelopes (sign / verify-then-parse-once)
* RFC 7638 key ids and JWK conversion
* action registry loading and parameter/target validation
* relay / bridge frame validation by direction
* small helpers (timestamps, digests, pairing verification codes)
"""

from .errors import ProtocolError
from .strict_json import dumps_compact, loads_strict
from .keys import (
    generate_private_key,
    jwk_from_public_key,
    kid_from_jwk,
    private_key_from_pem,
    private_key_to_pem,
    public_key_from_jwk,
)
from .signing import Envelope, sign_payload, verify_envelope
from .registry import ActionSpec, Registry, load_registry
from .schemas import Schemas, load_schemas
from .timeutil import format_rfc3339, now_utc, parse_rfc3339
from .digest import sha256_b64url, pairing_verification_code, challenge_digest, command_digest

__all__ = [
    "ProtocolError",
    "dumps_compact",
    "loads_strict",
    "generate_private_key",
    "jwk_from_public_key",
    "kid_from_jwk",
    "private_key_from_pem",
    "private_key_to_pem",
    "public_key_from_jwk",
    "Envelope",
    "sign_payload",
    "verify_envelope",
    "ActionSpec",
    "Registry",
    "load_registry",
    "Schemas",
    "load_schemas",
    "format_rfc3339",
    "now_utc",
    "parse_rfc3339",
    "sha256_b64url",
    "pairing_verification_code",
    "challenge_digest",
    "command_digest",
]
