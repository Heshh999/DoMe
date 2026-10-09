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

from .commands import (
    KeyRecord,
    VerifiedCommand,
    VerifiedConfirmation,
    VerifiedHelloProof,
    build_hello_proof_payload,
    protocol_compatible,
    verify_and_parse_command,
    verify_and_parse_confirmation,
    verify_hello_proof,
)
from .digest import (
    challenge_digest,
    command_digest,
    format_pairing_code,
    generate_pairing_code,
    normalize_pairing_code,
    pairing_code_handle,
    pairing_verification_code,
    sha256_b64url,
)
from .errors import ProtocolError
from .keys import (
    generate_private_key,
    jwk_from_public_key,
    kid_from_jwk,
    private_key_from_pem,
    private_key_to_pem,
    public_key_from_jwk,
)
from .registry import ActionSpec, Registry, load_registry
from .schemas import Schemas, load_schemas
from .signing import Envelope, sign_payload, verify_envelope
from .strict_json import dumps_compact, loads_strict
from .timeutil import format_rfc3339, now_utc, parse_rfc3339

__all__ = [
    "ActionSpec",
    "Envelope",
    "KeyRecord",
    "ProtocolError",
    "Registry",
    "Schemas",
    "VerifiedCommand",
    "VerifiedConfirmation",
    "VerifiedHelloProof",
    "build_hello_proof_payload",
    "challenge_digest",
    "command_digest",
    "dumps_compact",
    "format_pairing_code",
    "format_rfc3339",
    "generate_pairing_code",
    "generate_private_key",
    "jwk_from_public_key",
    "kid_from_jwk",
    "load_registry",
    "load_schemas",
    "loads_strict",
    "normalize_pairing_code",
    "now_utc",
    "pairing_code_handle",
    "pairing_verification_code",
    "parse_rfc3339",
    "private_key_from_pem",
    "private_key_to_pem",
    "protocol_compatible",
    "public_key_from_jwk",
    "sha256_b64url",
    "sign_payload",
    "verify_and_parse_command",
    "verify_and_parse_confirmation",
    "verify_envelope",
    "verify_hello_proof",
]
