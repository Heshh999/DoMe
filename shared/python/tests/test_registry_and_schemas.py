import copy
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
from dome_protocol.commands import (
    protocol_compatible,
    verify_and_parse_command,
    verify_and_parse_confirmation,
)
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
    assert reg.protocol_version == "1.3" and reg.registry_version == "1.3"
    assert "youtube.next" in reg.actions
    # manual input (rules.input_sessions / ai_eligibility): either capability permits the session actions,
    # and the human-only actions can never be AI tools or routine steps
    start = reg.get("input.session_start")
    assert start.satisfied_by({"keyboard"}) and start.satisfied_by({"pointer"}) and not start.satisfied_by({"media"})
    assert not start.ai_eligible and not start.routine_allowed
    assert reg.get("youtube.next").ai_eligible and reg.get("youtube.next").accepted_capabilities == ("media",)
    assert {"pointer", "keyboard"} <= set(reg.capabilities)
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
    for code in ("WINDOW_STATE_UNCHANGED", "FULLSCREEN_UNSUPPORTED", "APP_ELEVATED", "SEARCH_RESULT_UNAVAILABLE", "EXTENSION_UPDATE_REQUIRED"):
        assert reg.errors[code]["retryable"] is False and reg.errors[code]["user_message"]


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
    key, _record, resolve = signer
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
    key, _record, resolve = signer
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
    assert protocol_compatible("1.2", ("1.3",))  # a 1.2 agent keeps talking to a 1.3 relay
    assert protocol_compatible("1.3", ("1.3",))
    assert not protocol_compatible("1.3", ("1.2",))
    assert not protocol_compatible("1.4", ("1.3",))
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


# ----- protocol 1.2: window actions, YouTube search, per-action `since` -------------------------------------
NEW_IN_1_2 = ("app.maximize", "app.restore", "app.set_fullscreen", "youtube.search", "youtube.open_result")
BROWSER = {"browser_instance_id": "abcdefgh"}
WINDOW = {"app_id": "notepad"}
RESULTS_TAB = {
    "browser_instance_id": "abcdefgh",
    "tab_id": 5,
    "tab_token": "B" * 22,
    "script_attached": True,
    "context": "other",
    "ad_showing": False,
    "is_live": False,
    "in_playlist": False,
}


def test_since_marks_the_version_that_introduced_each_action():
    reg = load_registry()
    for name, spec in reg.actions.items():
        expected = "1.2" if name in NEW_IN_1_2 else "1.1" if name.startswith("input.") else "1.0"
        assert spec.since == expected == reg.min_version(name), name
    for name in ("app.maximize", "app.restore", "app.set_fullscreen"):
        spec = reg.get(name)
        assert (spec.capability, spec.target_name, spec.result_name) == ("apps", "window_target", "app_window_state_result")
        assert spec.confirmation == "none" and spec.coalesce is None and spec.ai_eligible
        assert set(spec.availability) == {"windows", "session_unlocked"}
    fullscreen = reg.get("app.set_fullscreen")
    assert fullscreen.risk == "moderate" and not fullscreen.idempotent and not fullscreen.routine_allowed
    assert fullscreen.verification == "fullscreen_observed"
    assert reg.get("app.maximize").routine_allowed and reg.get("app.restore").idempotent
    search, open_result = reg.get("youtube.search"), reg.get("youtube.open_result")
    assert search.target_name == "youtube_browser_target" and search.result_name == "youtube_search_result"
    assert open_result.target_name == "youtube_target" and open_result.result_name == "youtube_state_result"
    for spec in (search, open_result):
        assert spec.capability == "media" and not spec.idempotent and not spec.routine_allowed
        assert set(spec.availability) == {"extension_connected", "session_unlocked"}
    with pytest.raises(ProtocolError) as ei:
        reg.min_version("app.nope")
    assert ei.value.code == "UNKNOWN_ACTION"


def test_registry_refuses_an_invalid_since():
    from dome_protocol.registry import Registry, _load_json

    docs = [_load_json(n) for n in ("actions.json", "errors.json", "version.json", "plans.json")]
    Registry(*docs)
    for bad in ("1.4", "2.0", "0.9", "1.x", "1.2\n", ""):
        raw = copy.deepcopy(docs[0])
        raw["actions"]["system.ping"]["since"] = bad
        with pytest.raises(ValueError):
            Registry(raw, *docs[1:])


def test_search_limits_match_the_schemas():
    reg, schemas = load_registry(), load_schemas()
    query = reg.get("youtube.search").params_schema["properties"]["query"]
    assert query["minLength"] == 1 and query["maxLength"] == reg.limits["youtube_search_query_max_chars"] == 100
    results = schemas.document("results")["$defs"]
    assert results["youtube_search_result"]["properties"]["results"]["maxItems"] == reg.limits["youtube_search_results_max"] == 10
    open_index = reg.get("youtube.open_result").params_schema["properties"]["index"]
    assert results["youtube_search_item"]["properties"]["index"]["maximum"] == open_index["maximum"] == 10
    bridge_args = schemas.document("bridge")["$defs"]["bridge_request"]["properties"]["args"]["properties"]
    without_text = lambda s: {k: v for k, v in s.items() if k != "description"}
    assert without_text(bridge_args["query"]) == without_text(query)
    assert bridge_args["index"] == open_index


@pytest.mark.parametrize(
    "action, params, target",
    [
        ("youtube.search", {"query": "lofi beats"}, BROWSER),  # new tab in that browser
        ("youtube.search", {"query": "Ünïcode ✓ 🎵"}, TARGET),  # in the selected tab
        ("youtube.search", {"query": "x" * 100}, BROWSER),
        ("youtube.open_result", {"index": 1}, TARGET),
        ("youtube.open_result", {"index": 10, "video_id": "dQw4w9WgXcQ"}, TARGET),
        ("app.maximize", {}, WINDOW),
        ("app.restore", {}, {"app_id": "notepad", "window_id": "h1234"}),
        ("app.set_fullscreen", {"enabled": True}, WINDOW),
        ("app.set_fullscreen", {"enabled": False}, WINDOW),
    ],
)
def test_1_2_commands_verify(signer, action, params, target):
    key, _record, resolve = signer
    payload = make_command(action=action, params=params, target=dict(target), protocol_version="1.2")
    vc = verify_and_parse_command(sign_payload(key, dumps_compact(payload)).to_dict(), resolve)
    assert vc.spec.name == action and vc.params == params and vc.target == target


@pytest.mark.parametrize(
    "action, params, target, stamps",
    [
        ("system.ping", {}, None, ("1.0", "1.1", "1.2")),
        ("youtube.next", {}, TARGET, ("1.0", "1.2")),
        ("input.session_start", {}, None, ("1.1", "1.2")),
    ],
)
def test_older_actions_accept_every_stamp_from_their_since(signer, action, params, target, stamps):
    """rules.command_version_stamping: an updated phone keeps stamping old actions low, and 1.1 phones stamp 1.1."""
    key, _record, resolve = signer
    for stamp in stamps:
        payload = make_command(action=action, params=params, target=target, protocol_version=stamp)
        payload["target"] = target
        verify_and_parse_command(sign_payload(key, dumps_compact(payload)).to_dict(), resolve)


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"action": "app.maximize", "params": {}, "target": WINDOW, "protocol_version": "1.1"}, "PROTOCOL_INCOMPATIBLE"),
        ({"action": "app.maximize", "params": {}, "target": WINDOW, "protocol_version": "1.0"}, "PROTOCOL_INCOMPATIBLE"),
        ({"protocol_version": "1.1"}, "PROTOCOL_INCOMPATIBLE"),
        ({"protocol_version": "1.4"}, "PROTOCOL_INCOMPATIBLE"),
        ({"action": "input.session_start", "params": {}, "target": None, "protocol_version": "1.0"}, "PROTOCOL_INCOMPATIBLE"),
        ({"params": {}}, "INVALID_PARAMETERS"),
        ({"params": {"query": ""}}, "INVALID_PARAMETERS"),
        ({"params": {"query": "x" * 101}}, "INVALID_PARAMETERS"),
        ({"params": {"query": "lofi\n"}}, "INVALID_PARAMETERS"),
        ({"params": {"query": " lofi"}}, "INVALID_PARAMETERS"),
        ({"params": {"query": "lo\u0085fi"}}, "INVALID_PARAMETERS"),
        ({"params": {"query": 5}}, "INVALID_PARAMETERS"),
        ({"params": {"query": "lofi", "open_index": 1}}, "INVALID_PARAMETERS"),
        ({"target": None}, "TARGET_REQUIRED"),
        ({"target": {"browser_instance_id": "abcdefgh", "tab_id": 5}}, "INVALID_PARAMETERS"),
        ({"target": {"browser_instance_id": "abcdefgh", "tab_token": "A" * 22}}, "INVALID_PARAMETERS"),
        ({"target": {"tab_id": 5, "tab_token": "A" * 22}}, "INVALID_PARAMETERS"),
        ({"target": dict(BROWSER, expected_video_id="dQw4w9WgXcQ")}, "INVALID_PARAMETERS"),
        ({"action": "app.set_fullscreen", "params": {}, "target": WINDOW}, "INVALID_PARAMETERS"),
        ({"action": "app.set_fullscreen", "params": {"enabled": "yes"}, "target": WINDOW}, "INVALID_PARAMETERS"),
        ({"action": "app.maximize", "params": {"state": "maximized"}, "target": WINDOW}, "INVALID_PARAMETERS"),
        ({"action": "app.restore", "params": {}, "target": None}, "TARGET_REQUIRED"),
        ({"action": "app.restore", "params": {}, "target": {"app_id": "../notepad"}}, "INVALID_PARAMETERS"),
        ({"action": "youtube.open_result", "params": {"index": 0}, "target": TARGET}, "INVALID_PARAMETERS"),
        ({"action": "youtube.open_result", "params": {"index": 11}, "target": TARGET}, "INVALID_PARAMETERS"),
        ({"action": "youtube.open_result", "params": {"index": "1"}, "target": TARGET}, "INVALID_PARAMETERS"),
        ({"action": "youtube.open_result", "params": {"index": 1, "video_id": "../x"}, "target": TARGET}, "INVALID_PARAMETERS"),
        ({"action": "youtube.open_result", "params": {"index": 1}, "target": BROWSER}, "INVALID_PARAMETERS"),
    ],
)
def test_1_2_command_rejections(signer, overrides, code):
    key, _record, resolve = signer
    payload = make_command(action="youtube.search", params={"query": "lofi beats"}, target=dict(BROWSER), protocol_version="1.2")
    payload.update(copy.deepcopy(overrides))
    with pytest.raises(ProtocolError) as ei:
        verify_and_parse_command(sign_payload(key, dumps_compact(payload)).to_dict(), resolve)
    assert ei.value.code == code


def test_stamp_below_since_names_both_versions(signer):
    key, _record, resolve = signer
    payload = make_command(action="app.maximize", params={}, target=dict(WINDOW), protocol_version="1.1")
    with pytest.raises(ProtocolError) as ei:
        verify_and_parse_command(sign_payload(key, dumps_compact(payload)).to_dict(), resolve)
    assert ei.value.code == "PROTOCOL_INCOMPATIBLE" and ei.value.detail == {"peer": "1.1", "required": "1.2"}


def test_1_2_result_shapes():
    reg = load_registry()
    state = {"app_id": "notepad", "window_id": "h1234", "state": "maximized", "fullscreen": False, "foreground": True}
    for action in ("app.maximize", "app.restore", "app.set_fullscreen"):
        reg.validate_result(action, state)
    for bad in (
        {**state, "state": "fullscreen"},
        {k: v for k, v in state.items() if k != "foreground"},
        {k: v for k, v in state.items() if k != "window_id"},
        {**state, "title": "Untitled - Notepad"},
        {**state, "app_id": "Notepad"},
    ):
        with pytest.raises(ProtocolError):
            reg.validate_result("app.maximize", bad)
    items = [{"index": i, "video_id": f"vid{i}", "title": f"Video {i}"} for i in range(1, 11)]
    items[0].update(channel="A channel", duration_seconds=212, is_live=False)
    reg.validate_result("youtube.search", {"tab": RESULTS_TAB, "results": items})
    reg.validate_result("youtube.search", {"tab": RESULTS_TAB, "results": []})
    for bad in (
        {"tab": RESULTS_TAB, "results": [*items, {"index": 10, "video_id": "x", "title": "y"}]},  # 11 results
        {"tab": RESULTS_TAB, "results": [{"index": 0, "video_id": "x", "title": "y"}]},
        {"tab": RESULTS_TAB, "results": [{"index": 1, "video_id": "x y", "title": "y"}]},
        {"tab": RESULTS_TAB, "results": [{"index": 1, "video_id": "x"}]},
        {"tab": RESULTS_TAB, "results": [{"index": 1, "video_id": "x", "title": "y" * 201}]},
        {"tab": RESULTS_TAB, "results": [{"index": 1, "video_id": "x", "title": "y", "url": "https://x"}]},
        {"tab": RESULTS_TAB, "results": items, "query": "lofi"},  # rules.search_queries: never echoed
        {"results": items},
    ):
        with pytest.raises(ProtocolError):
            reg.validate_result("youtube.search", bad)
    reg.validate_result("youtube.open_result", {"tab": {**RESULTS_TAB, "context": "watch", "video_id": "vid2"}, "previous_video_id": "vid9"})


def test_existing_shapes_unchanged_so_1_1_phones_validate_new_agents():
    """1.2 adds new defs only: an app.list / youtube_tab from a 1.2 agent must still validate on a 1.1 phone."""
    docs = load_schemas()
    assert set(docs.document("results")["$defs"]["app_window"]["properties"]) == {"window_id", "title", "minimized", "foreground"}
    assert set(docs.document("relay-frames")["$defs"]["youtube_tab"]["properties"]) == {
        "browser_instance_id", "tab_id", "tab_token", "script_attached", "title", "video_id", "paused", "muted", "volume",
        "position_seconds", "duration_seconds", "context", "ad_showing", "is_live", "in_playlist", "theater", "fullscreen",
        "has_next", "has_previous", "active",
    }  # fmt: skip
    with pytest.raises(ProtocolError):
        load_registry().validate_result(
            "app.list",
            {"apps": [{"app_id": "notepad", "display_name": "Notepad", "running": True, "windows": [{"window_id": "h1", "title": "x", "maximized": True}]}]},
        )


def test_bridge_1_2_ops_and_results():
    schemas = load_schemas()
    rid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    req = {"type": "bridge_request", "request_id": rid, "op": "search", "args": {"query": "lofi beats"}, "timeout_ms": 15000}
    schemas.validate_bridge_frame("agent_to_extension", req)
    schemas.validate_bridge_frame("agent_to_extension", {**req, "args": {"query": "lofi", "tab_id": 5, "tab_token": "A" * 22}})
    schemas.validate_bridge_frame(
        "agent_to_extension",
        {**req, "op": "open_result", "args": {"tab_id": 5, "tab_token": "A" * 22, "index": 2, "video_id": "dQw4w9WgXcQ"}},
    )
    for args in ({"query": "x" * 101}, {"query": "a\nb"}, {"query": ""}, {"index": 11}, {"video_id": "a b"}, {"title": "x"}):
        with pytest.raises(ProtocolError):
            schemas.validate_bridge_frame("agent_to_extension", {**req, "args": args})
    with pytest.raises(ProtocolError):
        schemas.validate_bridge_frame("agent_to_extension", {**req, "op": "read_results"})  # extension-internal, never a bridge op
    found = {"tab": RESULTS_TAB, "results": [{"index": 1, "video_id": "v1", "title": "t"}]}
    schemas.validate_bridge_frame("extension_to_agent", {"type": "bridge_response", "request_id": rid, "ok": True, "result": found})
    schemas.validate_bridge_frame("extension_to_agent", {"type": "bridge_response", "request_id": rid, "ok": True, "result": {"tab": RESULTS_TAB}})
    with pytest.raises(ProtocolError):  # neither a search result nor a state result
        schemas.validate_bridge_frame("extension_to_agent", {"type": "bridge_response", "request_id": rid, "ok": True, "result": {**found, "previous_video_id": "v0"}})
    schemas.validate_bridge_frame(
        "extension_to_agent",
        {"type": "bridge_response", "request_id": rid, "ok": False, "error": {"code": "SEARCH_RESULT_UNAVAILABLE", "message": "gone"}},
    )


def test_validation_messages_never_echo_the_value(signer):
    """rules.search_queries: a rejected query must not travel back in the error message (result frames, journals, logs)."""
    reg, schemas = load_registry(), load_schemas()
    sentinel = "SENTINEL-" + "q" * 100
    for params in ({"query": sentinel}, {"query": sentinel[:20] + "\n"}, {"query": " " + sentinel[:20]}, {"query": sentinel[:20], "SENTINEL": 1}):
        with pytest.raises(ProtocolError) as ei:
            reg.validate_params("youtube.search", params)
        assert ei.value.code == "INVALID_PARAMETERS" and "SENTINEL" not in ei.value.message and "SENTINEL" not in str(ei.value)
    with pytest.raises(ProtocolError) as ei:
        reg.validate_params("youtube.search", {"query": sentinel})
    assert ei.value.message == "query: failed maxLength"
    with pytest.raises(ProtocolError) as ei:
        reg.validate_params("youtube.search", {})
    assert ei.value.message == "<root>: failed required (query)"
    key, _record, resolve = signer
    payload = make_command(action="youtube.search", params={"query": sentinel}, target=dict(BROWSER), protocol_version="1.2")
    with pytest.raises(ProtocolError) as ei:
        verify_and_parse_command(sign_payload(key, dumps_compact(payload)).to_dict(), resolve)
    assert ei.value.code == "INVALID_PARAMETERS" and "SENTINEL" not in str(ei.value)
    rid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    with pytest.raises(ProtocolError) as ei:
        schemas.validate_bridge_frame("agent_to_extension", {"type": "bridge_request", "request_id": rid, "op": "search", "args": {"query": sentinel}})
    assert "SENTINEL" not in str(ei.value)
    with pytest.raises(ProtocolError) as ei:
        reg.validate_result("youtube.search", {"tab": RESULTS_TAB, "results": [{"index": 1, "video_id": "v1", "title": sentinel * 2}]})
    assert "SENTINEL" not in str(ei.value)
