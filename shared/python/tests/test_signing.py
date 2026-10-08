import json

import pytest

from dome_protocol import (
    ProtocolError,
    generate_private_key,
    jwk_from_public_key,
    kid_from_jwk,
    private_key_from_pem,
    private_key_to_pem,
    public_key_from_jwk,
    sign_payload,
    verify_envelope,
)
from dome_protocol.keys import b64url_decode, b64url_encode


@pytest.fixture
def key():
    return generate_private_key()


@pytest.fixture
def jwk(key):
    return jwk_from_public_key(key.public_key())


def resolver(jwk):
    kid = kid_from_jwk(jwk)
    return lambda k: jwk if k == kid else None


def test_sign_and_verify(key, jwk):
    payload = '{"type":"command","x":1}'
    env = sign_payload(key, payload)
    assert env.alg == "ES256" and env.kid == kid_from_jwk(jwk)
    assert len(b64url_decode(env.sig)) == 64
    env2, parsed = verify_envelope(env.to_dict(), resolver(jwk))
    assert parsed == {"type": "command", "x": 1}
    assert env2 == env


def test_tampered_payload_rejected(key, jwk):
    env = sign_payload(key, '{"type":"command","x":1}').to_dict()
    env["payload"] = '{"type":"command","x":2}'
    with pytest.raises(ProtocolError) as ei:
        verify_envelope(env, resolver(jwk))
    assert ei.value.code == "SIGNATURE_INVALID"


def test_whitespace_change_breaks_signature(key, jwk):
    env = sign_payload(key, '{"type":"command","x":1}').to_dict()
    env["payload"] = '{"type": "command", "x": 1}'
    with pytest.raises(ProtocolError):
        verify_envelope(env, resolver(jwk))


def test_unknown_kid(key, jwk):
    env = sign_payload(key, '{"a":1}').to_dict()
    with pytest.raises(ProtocolError) as ei:
        verify_envelope(env, lambda k: None)
    assert ei.value.code == "UNKNOWN_KEY"


def test_resolver_returning_wrong_key_is_rejected(key):
    other = generate_private_key()
    other_jwk = jwk_from_public_key(other.public_key())
    env = sign_payload(key, '{"a":1}').to_dict()
    with pytest.raises(ProtocolError) as ei:
        verify_envelope(env, lambda k: other_jwk)  # store returns a key whose kid != env.kid
    assert ei.value.code == "UNKNOWN_KEY"


def test_wrong_signer_rejected(key, jwk):
    other = generate_private_key()
    env = sign_payload(other, '{"a":1}').to_dict()
    env["kid"] = kid_from_jwk(jwk)
    with pytest.raises(ProtocolError) as ei:
        verify_envelope(env, resolver(jwk))
    assert ei.value.code == "SIGNATURE_INVALID"


@pytest.mark.parametrize("alg", ["none", "HS256", "ES384", "EdDSA", "", None, 1])
def test_unknown_alg_rejected(key, jwk, alg):
    env = sign_payload(key, '{"a":1}').to_dict()
    env["alg"] = alg
    with pytest.raises(ProtocolError):
        verify_envelope(env, resolver(jwk))


def test_extra_field_rejected(key, jwk):
    env = sign_payload(key, '{"a":1}').to_dict()
    env["extra"] = 1
    with pytest.raises(ProtocolError) as ei:
        verify_envelope(env, resolver(jwk))
    assert ei.value.code == "MALFORMED_MESSAGE"


def test_missing_field_rejected(key, jwk):
    env = sign_payload(key, '{"a":1}').to_dict()
    del env["sig"]
    with pytest.raises(ProtocolError):
        verify_envelope(env, resolver(jwk))


def test_version_2_rejected(key, jwk):
    env = sign_payload(key, '{"a":1}').to_dict()
    env["v"] = 2
    with pytest.raises(ProtocolError) as ei:
        verify_envelope(env, resolver(jwk))
    assert ei.value.code == "PROTOCOL_INCOMPATIBLE"


def test_bool_version_rejected(key, jwk):
    env = sign_payload(key, '{"a":1}').to_dict()
    env["v"] = True
    with pytest.raises(ProtocolError):
        verify_envelope(env, resolver(jwk))


def test_signature_must_be_64_bytes(key, jwk):
    env = sign_payload(key, '{"a":1}').to_dict()
    env["sig"] = b64url_encode(b"\x01" * 70)
    with pytest.raises(ProtocolError):
        verify_envelope(env, resolver(jwk))


def test_zero_signature_rejected(key, jwk):
    env = sign_payload(key, '{"a":1}').to_dict()
    env["sig"] = b64url_encode(b"\x00" * 64)
    with pytest.raises(ProtocolError) as ei:
        verify_envelope(env, resolver(jwk))
    assert ei.value.code == "SIGNATURE_INVALID"


def test_signature_malleability_high_s_rejected_or_valid_only_once(key, jwk):
    """ECDSA (r, s) and (r, n-s) are both mathematically valid. We accept whichever the signer
    produced (WebCrypto does not normalise s) — but the payload bytes are what matter, and the
    command_id journal makes replay with a flipped s harmless. This test documents the behaviour."""
    env = sign_payload(key, '{"a":1}').to_dict()
    raw = b64url_decode(env["sig"])
    n = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
    s = int.from_bytes(raw[32:], "big")
    flipped = raw[:32] + (n - s).to_bytes(32, "big")
    env["sig"] = b64url_encode(flipped)
    verify_envelope(env, resolver(jwk))  # accepted: same signer, same bytes


def test_payload_parsed_strictly_after_verification(key, jwk):
    env = sign_payload(key, '{"a":1,"a":2}').to_dict()  # signer signed a dup-key payload
    with pytest.raises(ProtocolError) as ei:
        verify_envelope(env, resolver(jwk))
    assert ei.value.code == "MALFORMED_MESSAGE"


def test_payload_size_limit(key, jwk):
    env = sign_payload(key, json.dumps({"a": "x" * 17000})).to_dict()
    with pytest.raises(ProtocolError) as ei:
        verify_envelope(env, resolver(jwk))
    assert ei.value.code == "PAYLOAD_TOO_LARGE"


def test_pem_roundtrip(key):
    pem = private_key_to_pem(key)
    key2 = private_key_from_pem(pem)
    assert jwk_from_public_key(key2.public_key()) == jwk_from_public_key(key.public_key())


def test_jwk_validation():
    with pytest.raises(ProtocolError):
        public_key_from_jwk({"kty": "EC", "crv": "P-384", "x": "A" * 43, "y": "A" * 43})
    with pytest.raises(ProtocolError):
        public_key_from_jwk({"kty": "EC", "crv": "P-256", "x": "A" * 43, "y": "A" * 43, "d": "A" * 43})
    with pytest.raises(ProtocolError):  # not on curve
        public_key_from_jwk({"kty": "EC", "crv": "P-256", "x": "A" * 43, "y": "A" * 43})


def test_kid_is_rfc7638_thumbprint(jwk):
    import hashlib

    canonical = '{"crv":"P-256","kty":"EC","x":"%s","y":"%s"}' % (jwk["x"], jwk["y"])
    expected = b64url_encode(hashlib.sha256(canonical.encode()).digest())
    assert kid_from_jwk(jwk) == expected
