import uuid
from datetime import timedelta

import pytest

from dome_protocol import (
    KeyRecord,
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
from dome_protocol.digest import (
    challenge_digest,
    command_digest,
    format_pairing_code,
    generate_pairing_code,
    normalize_pairing_code,
    pairing_code_handle,
    pairing_verification_code,
)
from dome_protocol.keys import b64url_encode

ACCOUNT = "11111111-1111-4111-8111-111111111111"
CONTROLLER = "22222222-2222-4222-8222-222222222222"
PC = "33333333-3333-4333-8333-333333333333"
TARGET = {"browser_instance_id": "abcdefgh", "tab_id": 5, "tab_token": "AAAAAAAAAAAAAAAAAAAAAA"}


def make_command(action="youtube.next", params=None, target=None, **overrides):
    now = now_utc()
    payload = {
        "type": "command",
        "protocol_version": "1.0",
        "command_id": str(uuid.uuid4()),
        "account_id": ACCOUNT,
        "controller_id": CONTROLLER,
        "target_pc_id": PC,
        "action": action,
        "params": params if params is not None else {},
        "target": target if target is not None else dict(TARGET),
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
    record = KeyRecord(controller_id=CONTROLLER, account_id=ACCOUNT, jwk=jwk, capabilities=("media", "status"))
    return key, record, (lambda k: record if k == kid else None)


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
    assert "DUPLICATE_COMMAND" not in reg.errors
    for code in ("CONTROLLER_MISMATCH", "COMMAND_SUPERSEDED", "TAB_NOT_CONTROLLABLE"):
        assert code in reg.errors


def test_schemas_load_and_direction_unions():
    schemas = load_schemas()
    schemas.validate_frame("controller_to_relay", {"type": "ping"})
    with pytest.raises(ProtocolError):
        schemas.validate_frame("controller_to_relay", {"type": "ping", "extra": 1})
    with pytest.raises(ProtocolError):
        schemas.validate_frame("controller_to_relay", {"type": "grants_snapshot"})
    schemas.validate_frame("agent_to_relay", {"type": "revoke_controller", "controller_id": CONTROLLER, "kid": "A" * 43, "reason": "local_revocation"})
    schemas.validate_frame("relay_to_agent", {"type": "revoked", "reason": "pc_unlinked"})
    schemas.validate_frame("relay_to_controller", {"type": "revoked", "reason": "controller_revoked"})
    with pytest.raises(ProtocolError):  # ack can't be terminal
        schemas.validate_frame("agent_to_relay", {"type": "ack", "command_id": PC, "state": "succeeded", "at": "2026-10-08T12:00:00.000Z"})
    schemas.validate_frame("agent_to_relay", {"type": "ack", "command_id": PC, "state": "executing", "at": "2026-10-08T12:00:00.000Z"})
    with pytest.raises(ProtocolError):  # result needs origin
        schemas.validate_frame("agent_to_relay", {"type": "result", "command_id": PC, "state": "succeeded", "at": "2026-10-08T12:00:00.000Z", "duration_ms": 1})
    schemas.validate_frame("relay_to_controller", {"type": "result", "command_id": PC, "origin": "relay", "state": "failed", "at": "2026-10-08T12:00:00.000Z", "duration_ms": 0, "error": {"code": "PC_OFFLINE", "message": "x", "retryable": True}})
    # grants snapshot carries no keys and only known capabilities
    snap = {"type": "grants_snapshot", "pc_id": PC, "account_id": ACCOUNT, "snapshot_id": PC, "pc_enabled": True, "controllers": [{"controller_id": CONTROLLER, "kid": "A" * 43, "capabilities": ["media"], "display_name": "Phone", "status": "active"}]}
    schemas.validate_frame("controller_to_relay", {"type": "hello", "component": "controller", "kid": "A" * 43, "component_version": "0.1", "protocol_versions": ["1.0"], "registry_version": "1.0"})
    schemas.validate_result("volume_result", {"value": 35, "muted": False})
    with pytest.raises(ProtocolError):
        schemas.validate_result("volume_result", {"value": 135, "muted": False})
    load_registry().validate_result("system.ping", {"agent_time": "2026-10-08T12:00:00.000Z", "agent_version": "0.1.0", "protocol_version": "1.0"})
    with pytest.raises(ProtocolError):
        load_registry().validate_result("windows.lock", {"accepted": False})
    schemas.validate_rest("agent_token_response", {"access_token": "A" * 43, "expires_in": 3600, "pc_id": PC, "account_id": ACCOUNT})
    poll = {"pc_id": PC, "account_id": ACCOUNT, "pc_credential": "A" * 43, "relay_url": "wss://relay.example/ws/agent", "api_url": "https://api.example", "pc_name": "Office", "enabled": True}
    schemas.validate_rest("agent_link_poll_response", poll)
    with pytest.raises(ProtocolError):
        schemas.validate_rest("agent_link_poll_response", dict(poll, api_url="not a url"))
    with pytest.raises(ProtocolError):
        schemas.validate_rest("agent_token_response", {"access_token": "A" * 43, "expires_in": 3600, "pc_id": PC})
    schemas.validate_entitlement_claims({"iss": "https://dome.example", "sub": ACCOUNT, "pc": PC, "plan": "pro", "limits": {"max_enabled_pcs": 5, "max_controllers": 5, "routines": True, "routine_max_steps": 10, "routine_max_seconds": 60, "custom_layouts": True}, "iat": 1, "exp": 3601, "jti": PC})
    schemas.validate_frame("relay_to_agent", snap)
    snap["controllers"][0]["public_jwk"] = {"kty": "EC", "crv": "P-256", "x": "A" * 43, "y": "A" * 43}
    with pytest.raises(ProtocolError):
        schemas.validate_frame("relay_to_agent", snap)
    snap["controllers"][0].pop("public_jwk")
    snap["controllers"][0]["capabilities"] = ["shell"]
    with pytest.raises(ProtocolError):
        schemas.validate_frame("relay_to_agent", snap)


def test_challenge_text_round_trip():
    schemas = load_schemas()
    now = now_utc()
    challenge = {
        "challenge_id": str(uuid.uuid4()),
        "command_id": str(uuid.uuid4()),
        "controller_id": CONTROLLER,
        "pc_id": PC,
        "action": "power.sleep",
        "params": {"countdown_seconds": 10},
        "target": None,
        "target_state_digest": "A" * 43,
        "issued_at": format_rfc3339(now),
        "expires_at": format_rfc3339(now + timedelta(seconds=60)),
        "display": {"pc_name": "Büro-PC", "action_label": "Sleep", "detail": "Sleep in 10 s"},
    }
    text = dumps_compact(challenge)
    frame = {"type": "confirmation_required", "command_id": challenge["command_id"], "challenge_text": text}
    schemas.validate_frame("agent_to_relay", frame)
    parsed = schemas.validate_challenge_text(text)
    assert parsed == challenge
    with pytest.raises(ProtocolError):
        schemas.validate_challenge_text(dumps_compact({**challenge, "extra": 1}))
    with pytest.raises(ProtocolError):  # object form is no longer accepted on the wire
        schemas.validate_frame("agent_to_relay", {"type": "confirmation_required", "command_id": challenge["command_id"], "challenge": challenge})


def test_verify_and_parse_command_happy(signer):
    key, record, resolve = signer
    payload = make_command()
    env = sign_payload(key, dumps_compact(payload))
    vc = verify_and_parse_command(env.to_dict(), resolve)
    assert vc.spec.name == "youtube.next"
    assert vc.target == payload["target"]
    assert vc.key is record
    assert vc.digest == command_digest(env.payload)


def test_default_params_applied(signer):
    key, record, resolve = signer
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
        (lambda p: p.update(target={"browser_instance_id": "abcdefgh", "tab_id": 1}), "INVALID_PARAMETERS"),  # tab_token required
        (lambda p: p.update(action="windows.lock", target=dict(TARGET)), "INVALID_PARAMETERS"),
        (lambda p: p.update(protocol_version="2.0"), "PROTOCOL_INCOMPATIBLE"),
        (lambda p: p.update(protocol_version="1.0\n"), "MALFORMED_MESSAGE"),
        (lambda p: p.update(extra_field=1), "MALFORMED_MESSAGE"),
        (lambda p: p.update(command_id="not-a-uuid"), "MALFORMED_MESSAGE"),
        (lambda p: p.update(command_id=str(uuid.uuid4()) + "\n"), "MALFORMED_MESSAGE"),
        (lambda p: p.update(issued_at=format_rfc3339(now_utc() - timedelta(seconds=120)), expires_at=format_rfc3339(now_utc() - timedelta(seconds=60))), "COMMAND_EXPIRED"),
        (lambda p: p.update(issued_at=format_rfc3339(now_utc() + timedelta(seconds=60)), expires_at=format_rfc3339(now_utc() + timedelta(seconds=90))), "CLOCK_SKEW"),
        (lambda p: p.update(expires_at=format_rfc3339(now_utc() + timedelta(seconds=3600))), "MALFORMED_MESSAGE"),
        (lambda p: p.update(action="app.launch", params={"app_id": "chrome; rm -rf /"}, target=None), "INVALID_PARAMETERS"),
        (lambda p: p.update(action="app.launch", params={"app_id": "../../evil"}, target=None), "INVALID_PARAMETERS"),
        (lambda p: p.update(action="app.launch", params={"app_id": "discord\n"}, target=None), "INVALID_PARAMETERS"),
        (lambda p: p.update(controller_id=str(uuid.uuid4())), "CONTROLLER_MISMATCH"),
        (lambda p: p.update(account_id=str(uuid.uuid4())), "ACCOUNT_MISMATCH"),
    ],
)
def test_verify_and_parse_command_rejections(signer, mutation, code):
    key, record, resolve = signer
    payload = make_command()
    mutation(payload)
    env = sign_payload(key, dumps_compact(payload))
    with pytest.raises(ProtocolError) as ei:
        verify_and_parse_command(env.to_dict(), resolve)
    assert ei.value.code == code


def test_cross_controller_forgery_rejected():
    """A paired controller signs a payload naming ANOTHER controller's id: must fail."""
    attacker = generate_private_key()
    a_jwk = jwk_from_public_key(attacker.public_key())
    a_kid = kid_from_jwk(a_jwk)
    a_record = KeyRecord(controller_id=str(uuid.uuid4()), account_id=ACCOUNT, jwk=a_jwk, capabilities=("media",))
    payload = make_command(action="power.shutdown", params={}, target=None)
    payload["target"] = None  # controller_id in payload is the VICTIM's id (CONTROLLER)
    env = sign_payload(attacker, dumps_compact(payload))
    with pytest.raises(ProtocolError) as ei:
        verify_and_parse_command(env.to_dict(), lambda k: a_record if k == a_kid else None)
    assert ei.value.code == "CONTROLLER_MISMATCH"


def test_minor_version_compat():
    assert protocol_compatible("1.0", ("1.2",))
    assert protocol_compatible("1.2", ("1.2",))
    assert not protocol_compatible("1.3", ("1.2",))
    assert not protocol_compatible("2.0", ("1.2",))
    assert not protocol_compatible("x", ("1.0",))
    assert not protocol_compatible("1.0\n", ("1.0",))
    assert not protocol_compatible("１.０", ("1.0",))


def test_confirmation_payload(signer):
    key, record, resolve = signer
    challenge_text = dumps_compact({"challenge_id": str(uuid.uuid4()), "command_id": str(uuid.uuid4())})
    now = now_utc()
    payload = {
        "type": "confirmation",
        "protocol_version": "1.0",
        "command_id": str(uuid.uuid4()),
        "challenge_id": str(uuid.uuid4()),
        "challenge_digest": challenge_digest(challenge_text),
        "controller_id": CONTROLLER,
        "target_pc_id": PC,
        "decision": "approve",
        "issued_at": format_rfc3339(now),
        "expires_at": format_rfc3339(now + timedelta(seconds=60)),
        "nonce": b64url_encode(uuid.uuid4().bytes),
    }
    env = sign_payload(key, dumps_compact(payload))
    vc = verify_and_parse_confirmation(env.to_dict(), resolve)
    assert vc.approved and vc.key is record
    bad = dict(payload, decision="maybe")
    with pytest.raises(ProtocolError):
        verify_and_parse_confirmation(sign_payload(key, dumps_compact(bad)).to_dict(), resolve)
    too_long = dict(payload, expires_at=format_rfc3339(now + timedelta(seconds=600)))
    with pytest.raises(ProtocolError) as ei:
        verify_and_parse_confirmation(sign_payload(key, dumps_compact(too_long)).to_dict(), resolve)
    assert ei.value.code == "MALFORMED_MESSAGE"
    other = dict(payload, controller_id=str(uuid.uuid4()))
    with pytest.raises(ProtocolError) as ei:
        verify_and_parse_confirmation(sign_payload(key, dumps_compact(other)).to_dict(), resolve)
    assert ei.value.code == "CONTROLLER_MISMATCH"
    legacy = {k: v for k, v in payload.items() if k != "expires_at"}
    with pytest.raises(ProtocolError):
        verify_and_parse_confirmation(sign_payload(key, dumps_compact(legacy)).to_dict(), resolve)


def test_command_digest_is_over_exact_bytes():
    assert command_digest('{"a":1}') != command_digest('{"a": 1}')
    assert command_digest('{"a":1}') == command_digest('{"a":1}')


def test_pairing_code_helpers():
    code = generate_pairing_code()
    assert len(code) == 20 and all(c in "0123456789ABCDEFGHJKMNPQRSTVWXYZ" for c in code)
    formatted = format_pairing_code(code)
    assert formatted.count("-") == 3
    assert normalize_pairing_code(formatted.lower()) == code
    assert normalize_pairing_code(code.replace("1", "l", 1).replace("0", "O", 1)) == code
    with pytest.raises(ProtocolError):
        normalize_pairing_code(code[:-1])
    with pytest.raises(ProtocolError):
        normalize_pairing_code(code[:-1] + "U")
    handle = pairing_code_handle(code)
    assert len(handle) == 43 and handle == pairing_code_handle(format_pairing_code(code))
    v = pairing_verification_code(code, "p", PC, "kid")
    assert len(v) == 6 and v.isdigit()
    assert pairing_verification_code(code, "p", PC, "kid2") != v
    assert pairing_verification_code(generate_pairing_code(), "p", PC, "kid") != v
