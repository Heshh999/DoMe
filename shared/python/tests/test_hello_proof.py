"""hello.proof: the controller proves possession of its key before the relay binds the socket."""

from __future__ import annotations

from datetime import timedelta

import pytest

from dome_protocol import (
    KeyRecord,
    ProtocolError,
    build_hello_proof_payload,
    dumps_compact,
    generate_private_key,
    jwk_from_public_key,
    kid_from_jwk,
    load_schemas,
    now_utc,
    sign_payload,
    verify_hello_proof,
)

ACCOUNT = "11111111-1111-4111-8111-111111111111"
OTHER_ACCOUNT = "22222222-2222-4222-8222-222222222222"


def _controller() -> tuple[object, dict[str, str], str, KeyRecord]:
    key = generate_private_key()
    jwk = jwk_from_public_key(key.public_key())
    kid = kid_from_jwk(jwk)
    return key, jwk, kid, KeyRecord(controller_id="33333333-3333-4333-8333-333333333333", account_id=ACCOUNT, jwk=jwk)


def test_round_trip_and_schema() -> None:
    key, _jwk, kid, record = _controller()
    payload = build_hello_proof_payload(kid=kid, account_id=ACCOUNT)
    load_schemas().validate_hello_proof_payload(payload)
    env = sign_payload(key, dumps_compact(payload))  # type: ignore[arg-type]
    verified = verify_hello_proof(env.to_dict(), lambda k: record if k == kid else None, expected_kid=kid, expected_account_id=ACCOUNT)
    assert verified.nonce == payload["nonce"] and verified.key is record


def test_other_key_or_other_kid_is_unknown_key() -> None:
    key, _jwk, kid, record = _controller()
    other_key, _unused, other_kid, other_record = _controller()
    # signed by a key the relay does not know for this account
    payload = build_hello_proof_payload(kid=other_kid, account_id=ACCOUNT)
    env = sign_payload(other_key, dumps_compact(payload))  # type: ignore[arg-type]
    with pytest.raises(ProtocolError) as exc:
        verify_hello_proof(env.to_dict(), lambda k: record if k == kid else None, expected_kid=kid, expected_account_id=ACCOUNT)
    assert exc.value.code == "UNKNOWN_KEY"
    # a valid proof for a different kid presented on a socket that said hello with `kid`
    with pytest.raises(ProtocolError) as exc:
        verify_hello_proof(
            env.to_dict(), lambda k: other_record if k == other_kid else None, expected_kid=kid, expected_account_id=ACCOUNT
        )
    assert exc.value.code == "UNKNOWN_KEY"
    # payload names another kid than the envelope's
    payload = build_hello_proof_payload(kid=other_kid, account_id=ACCOUNT)
    env = sign_payload(key, dumps_compact(payload))  # type: ignore[arg-type]
    with pytest.raises(ProtocolError) as exc:
        verify_hello_proof(env.to_dict(), lambda k: record if k == kid else None, expected_kid=kid, expected_account_id=ACCOUNT)
    assert exc.value.code == "UNKNOWN_KEY"


def test_account_mismatch() -> None:
    key, _jwk, kid, record = _controller()
    payload = build_hello_proof_payload(kid=kid, account_id=OTHER_ACCOUNT)
    env = sign_payload(key, dumps_compact(payload))  # type: ignore[arg-type]
    with pytest.raises(ProtocolError) as exc:
        verify_hello_proof(env.to_dict(), lambda k: record if k == kid else None, expected_kid=kid, expected_account_id=ACCOUNT)
    assert exc.value.code == "ACCOUNT_MISMATCH"


def test_window_and_tampering() -> None:
    key, _jwk, kid, record = _controller()
    stale = build_hello_proof_payload(kid=kid, account_id=ACCOUNT, now=now_utc() - timedelta(seconds=120))
    env = sign_payload(key, dumps_compact(stale))  # type: ignore[arg-type]
    with pytest.raises(ProtocolError) as exc:
        verify_hello_proof(env.to_dict(), lambda k: record if k == kid else None, expected_kid=kid, expected_account_id=ACCOUNT)
    assert exc.value.code == "COMMAND_EXPIRED"
    fresh = build_hello_proof_payload(kid=kid, account_id=ACCOUNT)
    env = sign_payload(key, dumps_compact(fresh))  # type: ignore[arg-type]
    raw = env.to_dict()
    raw["payload"] = raw["payload"].replace(ACCOUNT, OTHER_ACCOUNT)
    with pytest.raises(ProtocolError) as exc:
        verify_hello_proof(raw, lambda k: record if k == kid else None, expected_kid=kid, expected_account_id=ACCOUNT)
    assert exc.value.code == "SIGNATURE_INVALID"
    bad = dict(fresh)
    del bad["nonce"]
    env = sign_payload(key, dumps_compact(bad))  # type: ignore[arg-type]
    with pytest.raises(ProtocolError) as exc:
        verify_hello_proof(env.to_dict(), lambda k: record if k == kid else None, expected_kid=kid, expected_account_id=ACCOUNT)
    assert exc.value.code == "MALFORMED_MESSAGE"
