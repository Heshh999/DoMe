import uuid
from datetime import timedelta

import pytest

from dome_protocol import (
    ProtocolError,
    dumps_compact,
    format_rfc3339,
    generate_private_key,
    jwk_from_public_key,
    kid_from_jwk,
    load_registry,
    load_schemas,
    now_utc,
    sign_payload,
)
from dome_protocol.commands import protocol_compatible, verify_and_parse_command, verify_and_parse_confirmation
from dome_protocol.digest import challenge_digest, command_digest, pairing_verification_code
from dome_protocol.keys import b64url_encode


def make_command(action="youtube.next", params=None, target=None, **overrides):
    now = now_utc()
    payload = {
        "type": "command",
        "protocol_version": "1.0",
        "command_id": str(uuid.uuid4()),
        "account_id": str(uuid.uuid4()),
        "controller_id": str(uuid.uuid4()),
        "target_pc_id": str(uuid.uuid4()),
        "action": action,
        "params": params if params is not None else {},
        "target": target if target is not None else {"browser_instance_id": "abcdefgh", "tab_id": 5},
        "issued_at": format_rfc3339(now),
        "expires_at": format_rfc3339(now + timedelta(seconds=30)),
        "nonce": b64url_encode(uuid.uuid4().bytes),
    }
    payload.update(overrides)
    return payload


@pytest.fixture(scope="module")
def signer():
    key = generate_private_key()
    jwk = jwk_from_public_key(key.public_key())
    kid = kid_from_jwk(jwk)
    return key, jwk, (lambda k: jwk if k == kid else None)


def test_registry_loads_and_is_consistent():
    reg = load_registry()
    assert reg.protocol_version == "1.0"
    assert "youtube.next" in reg.actions
    for spec in reg.actions.values():
        assert spec.params_schema.get("additionalProperties") is False
        assert spec.capability in reg.capabilities
        assert spec.verification in reg.verification_strategies
        for cond in spec.availability:
            assert cond in reg.availability_conditions
        if spec.risk == "disruptive":
            assert spec.requires_confirmation and not spec.routine_allowed
    for code in reg.errors:
        assert code.isupper()


def test_schemas_load():
    schemas = load_schemas()
    schemas.validate_frame("controller_to_relay", {"type": "ping"})
    with pytest.raises(ProtocolError):
        schemas.validate_frame("controller_to_relay", {"type": "ping", "extra": 1})
    with pytest.raises(ProtocolError):
        schemas.validate_frame("controller_to_relay", {"type": "grants_snapshot"})


def test_verify_and_parse_command_happy(signer):
    key, jwk, resolve = signer
    payload = make_command()
    env = sign_payload(key, dumps_compact(payload))
    vc = verify_and_parse_command(env.to_dict(), resolve)
    assert vc.spec.name == "youtube.next"
    assert vc.target == payload["target"]
    assert vc.digest == command_digest(payload)


def test_default_params_applied(signer):
    key, jwk, resolve = signer
    payload = make_command(action="power.sleep", params={}, target=None)
    payload["target"] = None
    env = sign_payload(key, dumps_compact(payload))
    vc = verify_and_parse_command(env.to_dict(), resolve)
    assert vc.params == {"countdown_seconds": 10}
    assert vc.spec.requires_confirmation


@pytest.mark.parametrize(
    "mutation, code",
    [
        (lambda p: p.update(action="shell.exec"), "UNKNOWN_ACTION"),
        (lambda p: p.update(action="youtube.set_volume", params={"value": 101}), "INVALID_PARAMETERS"),
        (lambda p: p.update(action="youtube.seek_relative", params={"seconds": 0}), "INVALID_PARAMETERS"),
        (lambda p: p.update(params={"unexpected": 1}), "INVALID_PARAMETERS"),
        (lambda p: p.update(target=None), "TARGET_REQUIRED"),
        (lambda p: p.update(target={"browser_instance_id": "abcdefgh"}), "INVALID_PARAMETERS"),
        (lambda p: p.update(action="windows.lock", target={"browser_instance_id": "abcdefgh", "tab_id": 1}), "INVALID_PARAMETERS"),
        (lambda p: p.update(protocol_version="2.0"), "PROTOCOL_INCOMPATIBLE"),
        (lambda p: p.update(extra_field=1), "MALFORMED_MESSAGE"),
        (lambda p: p.update(command_id="not-a-uuid"), "MALFORMED_MESSAGE"),
        (lambda p: p.update(issued_at=format_rfc3339(now_utc() - timedelta(seconds=120)), expires_at=format_rfc3339(now_utc() - timedelta(seconds=60))), "COMMAND_EXPIRED"),
        (lambda p: p.update(issued_at=format_rfc3339(now_utc() + timedelta(seconds=60)), expires_at=format_rfc3339(now_utc() + timedelta(seconds=90))), "CLOCK_SKEW"),
        (lambda p: p.update(expires_at=format_rfc3339(now_utc() + timedelta(seconds=3600))), "MALFORMED_MESSAGE"),
        (lambda p: p.update(action="app.launch", params={"app_id": "chrome; rm -rf /"}, target=None), "INVALID_PARAMETERS"),
        (lambda p: p.update(action="app.launch", params={"app_id": "../../evil"}, target=None), "INVALID_PARAMETERS"),
    ],
)
def test_verify_and_parse_command_rejections(signer, mutation, code):
    key, jwk, resolve = signer
    payload = make_command()
    mutation(payload)
    env = sign_payload(key, dumps_compact(payload))
    with pytest.raises(ProtocolError) as ei:
        verify_and_parse_command(env.to_dict(), resolve)
    assert ei.value.code == code


def test_minor_version_compat():
    assert protocol_compatible("1.0", ("1.2",))
    assert protocol_compatible("1.2", ("1.2",))
    assert not protocol_compatible("1.3", ("1.2",))
    assert not protocol_compatible("2.0", ("1.2",))
    assert not protocol_compatible("x", ("1.0",))


def test_confirmation_payload(signer):
    key, jwk, resolve = signer
    challenge_text = dumps_compact({"challenge_id": str(uuid.uuid4()), "command_id": str(uuid.uuid4())})
    payload = {
        "type": "confirmation",
        "protocol_version": "1.0",
        "command_id": str(uuid.uuid4()),
        "challenge_id": str(uuid.uuid4()),
        "challenge_digest": challenge_digest(challenge_text),
        "controller_id": str(uuid.uuid4()),
        "target_pc_id": str(uuid.uuid4()),
        "decision": "approve",
        "issued_at": format_rfc3339(now_utc()),
        "nonce": b64url_encode(uuid.uuid4().bytes),
    }
    env = sign_payload(key, dumps_compact(payload))
    _, parsed = verify_and_parse_confirmation(env.to_dict(), resolve)
    assert parsed["decision"] == "approve"
    payload["decision"] = "maybe"
    env = sign_payload(key, dumps_compact(payload))
    with pytest.raises(ProtocolError):
        verify_and_parse_confirmation(env.to_dict(), resolve)


def test_command_digest_sensitivity():
    p = make_command()
    d1 = command_digest(p)
    p2 = dict(p, params={"x": 1})
    assert command_digest(p2) != d1
    p3 = dict(p, nonce="different")  # nonce not part of digest: same execution fields
    assert command_digest(p3) == d1


def test_pairing_verification_code_deterministic():
    code = pairing_verification_code("p", "pc", "kid")
    assert len(code) == 6 and code.isdigit()
    assert pairing_verification_code("p", "pc", "kid2") != code
