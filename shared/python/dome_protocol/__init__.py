"""DoMe shared protocol library.

Everything that crosses a trust boundary is defined by the JSON files in ``shared/protocol``.
This package gives the backend and the Windows agent one tested implementation of:

* strict JSON parsing (size, depth, duplicate keys, NaN/Infinity, lone surrogates rejected)
* ES256 detached-signature envelopes (sign / verify-then-parse-once)
* RFC 7638 key ids and JWK conversion
* action registry loading and parameter/target validation
* relay / bridge frame validation by direction
* protocol version compatibility, negotiation and per-action stamping (``since``)
* Screen view (1.4): the PC-signed answer, the strict SDP helper, the data-channel vocabulary, PC key pinning
* small helpers (timestamps, digests, pairing verification codes)
"""

from .commands import (
    INPUT_BATCH_PROTOCOL_VERSION,
    SCREEN_INPUT_PROTOCOL_VERSION,
    KeyRecord,
    VerifiedCommand,
    VerifiedConfirmation,
    VerifiedHelloProof,
    VerifiedInputBatch,
    build_hello_proof_payload,
    build_input_batch_payload,
    input_batch_protocol_version,
    input_event_capabilities,
    input_event_since,
    negotiate_version,
    protocol_compatible,
    verify_and_parse_command,
    verify_and_parse_confirmation,
    verify_and_parse_input_batch,
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
    pc_key_mac,
    sha256_b64url,
    verify_pc_key_mac,
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
from .screen import (
    SCREEN_DATA_CHANNEL_LABEL,
    SCREEN_SDP_PATTERN,
    VerifiedScreenAnswer,
    build_screen_answer_payload,
    offer_digest,
    parse_screen_dc_message,
    screen_sdp_acceptable,
    sdp_fingerprints,
    verify_screen_answer_payload,
)
from .signing import Envelope, sign_payload, verify_envelope
from .strict_json import dumps_compact, loads_strict
from .timeutil import format_rfc3339, now_utc, parse_rfc3339

__all__ = [
    "INPUT_BATCH_PROTOCOL_VERSION",
    "SCREEN_DATA_CHANNEL_LABEL",
    "SCREEN_INPUT_PROTOCOL_VERSION",
    "SCREEN_SDP_PATTERN",
    "ActionSpec",
    "Envelope",
    "KeyRecord",
    "ProtocolError",
    "Registry",
    "Schemas",
    "VerifiedCommand",
    "VerifiedConfirmation",
    "VerifiedHelloProof",
    "VerifiedInputBatch",
    "VerifiedScreenAnswer",
    "build_hello_proof_payload",
    "build_input_batch_payload",
    "build_screen_answer_payload",
    "challenge_digest",
    "command_digest",
    "dumps_compact",
    "format_pairing_code",
    "format_rfc3339",
    "generate_pairing_code",
    "generate_private_key",
    "input_batch_protocol_version",
    "input_event_capabilities",
    "input_event_since",
    "jwk_from_public_key",
    "kid_from_jwk",
    "load_registry",
    "load_schemas",
    "loads_strict",
    "negotiate_version",
    "normalize_pairing_code",
    "now_utc",
    "offer_digest",
    "pairing_code_handle",
    "pairing_verification_code",
    "parse_rfc3339",
    "parse_screen_dc_message",
    "pc_key_mac",
    "private_key_from_pem",
    "private_key_to_pem",
    "protocol_compatible",
    "public_key_from_jwk",
    "screen_sdp_acceptable",
    "sdp_fingerprints",
    "sha256_b64url",
    "sign_payload",
    "verify_and_parse_command",
    "verify_and_parse_confirmation",
    "verify_and_parse_input_batch",
    "verify_envelope",
    "verify_hello_proof",
    "verify_pc_key_mac",
    "verify_screen_answer_payload",
]
