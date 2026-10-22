"""Protocol 1.4, Screen view (docs/PROTOCOL.md §20; docs/design/screen-view.md §11): the registry additions, every new
frame, def, result and REST shape (accept the example, refuse unknown fields and out-of-range values), the content-based
input-batch stamp and the 1.4 check order, the PC-signed answer, PC key pinning and the data-channel vocabulary."""

from __future__ import annotations

import copy
import json
from datetime import timedelta
from pathlib import Path

import pytest

from dome_protocol import (
    INPUT_BATCH_PROTOCOL_VERSION,
    SCREEN_DATA_CHANNEL_LABEL,
    SCREEN_INPUT_PROTOCOL_VERSION,
    KeyRecord,
    ProtocolError,
    build_input_batch_payload,
    build_screen_answer_payload,
    dumps_compact,
    format_rfc3339,
    generate_private_key,
    input_batch_protocol_version,
    input_event_capabilities,
    input_event_since,
    jwk_from_public_key,
    kid_from_jwk,
    load_registry,
    load_schemas,
    now_utc,
    offer_digest,
    parse_rfc3339,
    parse_screen_dc_message,
    pc_key_mac,
    screen_sdp_acceptable,
    sdp_fingerprints,
    sign_payload,
    verify_and_parse_command,
    verify_and_parse_input_batch,
    verify_pc_key_mac,
    verify_screen_answer_payload,
)
from dome_protocol.registry import _load_json

ACCOUNT = "11111111-1111-4111-8111-111111111111"
CONTROLLER = "22222222-2222-4222-8222-222222222222"
PC = "33333333-3333-4333-8333-333333333333"
COMMAND = "9a1c2d3e-4a5b-4c6d-8e7f-901234567890"
OTHER = "44444444-4444-4444-8444-444444444444"
SID = "U2NyZWVuU2Vzc2lvbjAxQQ"
TS = "2026-10-21T12:00:00.000Z"
FIXTURE = json.loads((Path(__file__).resolve().parents[2] / "protocol" / "fixtures" / "es256-python.json").read_text(encoding="utf-8"))
OFFER = FIXTURE["screen"]["offer_sdp"]
ANSWER = FIXTURE["screen"]["answer_sdp"]
ENVELOPE = {"v": 1, "alg": "ES256", "kid": "A" * 43, "payload": "{}", "sig": "B" * 86}
ALLOWANCE = {"unlimited": False, "remaining_seconds": 540, "resets_at": TS}
UNLIMITED = {"unlimited": True, "remaining_seconds": None, "resets_at": None}

NEW_LIMITS = {
    "screen_sdp_max_bytes": 12288,
    "screen_start_command_lifetime_seconds": 60,
    "screen_answer_lifetime_seconds": 60,
    "screen_heartbeat_seconds": 2,
    "screen_lease_seconds": 8,
    "screen_connect_timeout_seconds": 20,
    "screen_allowance_chunk_seconds": 60,
    "screen_allowance_renew_before_seconds": 20,
    "screen_session_max_seconds": 14400,
    "screen_consent_timeout_seconds": 30,
    "screen_pc_stop_cooldown_default_seconds": 600,
    "screen_secure_desktop_pause_max_seconds": 120,
    "screen_dc_message_max_bytes": 1024,
    "screen_geometry_generations_kept": 4,
    "input_wheel_max": 1200,
}
NEW_RULES = ("screen_sessions", "screen_signaling", "pc_key_pinning", "screen_metering", "screen_content", "local_permission_ui")
NEW_ERRORS = {
    "SCREEN_SESSION_OWNED": False,
    "SCREEN_SESSION_REQUIRED": False,
    "SCREEN_ALLOWANCE_EXHAUSTED": False,
    "SCREEN_OFFER_INVALID": False,
    "SCREEN_CAPTURE_UNAVAILABLE": False,
    "SCREEN_ENCODER_UNAVAILABLE": False,
    "SCREEN_CONNECTION_FAILED": True,
    "SCREEN_CONSENT_DECLINED": False,
    "SCREEN_CONSENT_TIMEOUT": False,
    "SCREEN_GEOMETRY_CHANGED": True,
    "SCREEN_VIEW_DISABLED": False,
    "INPUT_PAUSED_ON_PC": True,
    "INPUT_TARGET_RESERVED": False,
    "SCREEN_PC_UNVERIFIED": False,
    "SCREEN_ANSWER_INVALID": False,
}
END_REASONS = {
    "stopped",
    "stopped_on_pc",
    "replaced",
    "viewer_gone",
    "takeover",
    "controller_revoked",
    "controller_offline",
    "grant_removed",
    "plan_disabled",
    "remote_disabled",
    "session_locked",
    "secure_desktop",
    "allowance_exhausted",
    "allowance_unconfirmed",
    "max_duration",
    "capture_failed",
    "connection_failed",
    "agent_restart",
}


def _controller() -> tuple[object, str, KeyRecord]:
    key = generate_private_key()
    jwk = jwk_from_public_key(key.public_key())
    return key, kid_from_jwk(jwk), KeyRecord(controller_id=CONTROLLER, account_id=ACCOUNT, jwk=jwk, capabilities=("screen", "pointer"))


def _frame_ok(direction: str, frame: dict) -> None:
    load_schemas().validate_frame(direction, frame)


def _frame_bad(direction: str, frame: dict) -> None:
    with pytest.raises(ProtocolError) as ei:
        load_schemas().validate_frame(direction, frame)
    assert ei.value.code == "MALFORMED_MESSAGE"


def _with(base: dict, path: tuple, value: object) -> dict:
    out = copy.deepcopy(base)
    node = out
    for key in path[:-1]:
        node = node[key]
    if value is ...:
        del node[path[-1]]
    else:
        node[path[-1]] = value
    return out


# ----- registry: version, capabilities, actions, limits, rules, errors, plans ----------------------------------------


def test_registry_is_1_4_with_the_screen_capabilities_and_actions() -> None:
    reg = load_registry()
    assert reg.protocol_version == "1.4" and reg.registry_version == "1.4"
    assert {"screen", "screen_audio"} <= set(reg.capabilities)
    assert "DoMe window on the PC" in reg.capabilities["screen"] and "never given to an AI" in reg.capabilities["screen"]
    assert "Only together with `screen`" in reg.capabilities["screen_audio"]
    start, stop = reg.get("screen.session_start"), reg.get("screen.session_stop")
    for spec in (start, stop):
        assert spec.since == "1.4" == reg.min_version(spec.name)
        assert spec.capability == "screen" and spec.accepted_capabilities == ("screen",)
        assert not spec.ai_eligible and not spec.routine_allowed and spec.confirmation == "none" and spec.target_name is None
        assert spec.verification == "response" and spec.coalesce is None
    assert start.risk == "moderate" and start.timeout_ms == 45000 and not start.idempotent
    assert set(start.availability) == {"windows", "session_unlocked"} and start.result_name == "screen_session_result"
    assert stop.risk == "low" and stop.timeout_ms == 5000 and stop.idempotent and stop.availability == ()
    assert stop.result_name == "screen_session_stop_result"
    # the strict SDP pattern and size live in the params schema, so every verifier applies them
    offer = start.params_schema["properties"]["offer_sdp"]
    assert offer["maxLength"] == reg.limits["screen_sdp_max_bytes"] == 12288 and offer["pattern"] == "^[\\x20-\\x7E\\r\\n]*$"
    # the capability enums of every schema carry both new capabilities
    frames = load_schemas().document("relay-frames")["$defs"]
    enums = [
        frames["grants_snapshot"]["properties"]["controllers"]["items"]["properties"]["capabilities"]["items"]["enum"],
        frames["pairing_request"]["properties"]["requested_capabilities"]["items"]["enum"],
        frames["pairing_decision"]["properties"]["granted_capabilities"]["items"]["enum"],
        frames["agent_grant_update"]["properties"]["capabilities"]["items"]["enum"],
        load_schemas().document("rest")["$defs"]["capability"]["enum"],
    ]
    for enum in enums:
        assert set(enum) == set(reg.capabilities)


def test_params_defaults_and_bounds() -> None:
    reg = load_registry()
    params = reg.validate_params("screen.session_start", {"offer_sdp": OFFER})
    assert params == {"offer_sdp": OFFER, "surface": "primary", "audio": True, "takeover": False}
    assert reg.validate_params("screen.session_start", {"offer_sdp": OFFER, "surface": 3, "audio": False, "takeover": True})["surface"] == 3
    for bad in (
        {},
        {"offer_sdp": ""},
        {"offer_sdp": "v=0\r\ns=ü\r\n"},  # non-ASCII
        {"offer_sdp": "v=0\r\ns=\x00\r\n"},  # NUL
        {"offer_sdp": "v=0\r\n" + "a=" + "x" * 12290},  # over screen_sdp_max_bytes
        {"offer_sdp": OFFER, "surface": 8},
        {"offer_sdp": OFFER, "surface": "secondary"},
        {"offer_sdp": OFFER, "audio": "yes"},
        {"offer_sdp": OFFER, "extra": 1},
    ):
        with pytest.raises(ProtocolError) as ei:
            reg.validate_params("screen.session_start", bad)
        assert ei.value.code == "INVALID_PARAMETERS", bad
    assert reg.validate_params("screen.session_stop", {"screen_session_id": SID}) == {"screen_session_id": SID}
    with pytest.raises(ProtocolError):
        reg.validate_params("screen.session_stop", {"screen_session_id": "short"})
    with pytest.raises(ProtocolError):
        reg.validate_target("screen.session_start", {"app_id": "x"})


def test_limits_rules_errors_and_plans() -> None:
    reg = load_registry()
    for name, value in NEW_LIMITS.items():
        assert reg.limits[name] == value, name
    assert reg.limits["screen_start_command_lifetime_seconds"] <= reg.limits["max_command_lifetime_seconds"]
    rules = _load_json("version.json")["rules"]
    for name in NEW_RULES:
        assert name in rules and len(rules[name]) > 200, name
    assert "pointer_move_to, pointer_wheel or key f11" in rules["command_version_stamping"]
    assert "BEFORE schema validation" in rules["command_version_stamping"]
    assert "MALFORMED_MESSAGE" in rules["command_version_stamping"]  # why the relay shields older agents
    assert "pc_key_mac" in rules["pairing_secret"] and "screen or screen_audio" in rules["grant_update"]
    assert "screen.* action" in rules["ai_eligibility"] and "pointer_move_to" in rules["input_sessions"]
    assert "relay's own clock" in rules["screen_metering"] or "relay's clock" in rules["screen_metering"]
    assert "continue anyway" in rules["pc_key_pinning"] and "Nothing is recorded" in rules["screen_content"]
    for code, retryable in NEW_ERRORS.items():
        assert reg.errors[code]["retryable"] is retryable, code
        message = reg.errors[code]["user_message"]
        assert message.endswith(".") and len(message) > 10, code
    for plan_id in ("free", "pro", "pro_plus"):
        plan = reg.plan(plan_id)
        assert plan["screen_view_window_hours"] == 5
        assert plan["screen_view_seconds_per_window"] == (600 if plan_id == "free" else None), plan_id
        assert plan["screen_ice_rate_limit"] == {"per_minute": 6, "burst": 6, "account_per_hour": 60}
        assert plan["screen_start_rate_limit"] == {"per_minute": 6, "burst": 6}
    # the entitlement assertion is unchanged: Screen view limits never travel in it
    claims = load_schemas().document("entitlement")["properties"]["limits"]["properties"]
    assert not any(k.startswith("screen") for k in claims)


# ----- frames ----------------------------------------------------------------------------------------------------------


def test_screen_ice_request_and_servers() -> None:
    _frame_ok("controller_to_relay", {"type": "screen_ice_request", "pc_id": PC})
    _frame_bad("controller_to_relay", {"type": "screen_ice_request"})
    _frame_bad("controller_to_relay", {"type": "screen_ice_request", "pc_id": PC, "turn": True})
    _frame_bad("agent_to_relay", {"type": "screen_ice_request", "pc_id": PC})  # a PC never asks for ICE servers
    servers = {
        "type": "screen_ice_servers",
        "pc_id": PC,
        "ice_servers": [
            {"urls": ["stun:stun.example.net:3478"]},
            {"urls": ["turn:turn.example.net:3478?transport=udp", "turns:turn.example.net:443?transport=tcp"], "username": "1760000000:acct", "credential": "c" * 44},
        ],
        "expires_at": TS,
        "allowance": ALLOWANCE,
        "turn": "available",
    }
    _frame_ok("relay_to_controller", servers)
    _frame_ok("relay_to_controller", {**servers, "ice_servers": [{"urls": ["stun:stun.example.net:3478"]}], "expires_at": None, "turn": "unavailable", "allowance": UNLIMITED})
    _frame_ok("relay_to_controller", {**servers, "ice_servers": [], "expires_at": None, "turn": "disabled"})
    for bad in (
        _with(servers, ("turn",), "maybe"),
        _with(servers, ("ice_servers", 0, "urls", 0), "http://stun.example.net"),
        _with(servers, ("ice_servers", 0, "urls"), []),
        _with(servers, ("ice_servers", 1, "secret"), "x"),
        _with(servers, ("allowance", "remaining_seconds"), -1),
        _with(servers, ("allowance", "unlimited"), ...),
        _with(servers, ("expires_at",), ...),
        _with(servers, ("pc_name",), "Desk"),
    ):
        _frame_bad("relay_to_controller", bad)
    _frame_bad("relay_to_agent", servers)  # the PC never receives ICE servers: STUN urls ride in relay.screen


def test_screen_answer_frames_in_both_directions() -> None:
    _frame_ok("agent_to_relay", {"type": "screen_answer", "envelope": ENVELOPE, "ref_controller_id": CONTROLLER})
    _frame_bad("agent_to_relay", {"type": "screen_answer", "envelope": ENVELOPE})  # the relay must know whom to route to
    _frame_bad("agent_to_relay", {"type": "screen_answer", "envelope": ENVELOPE, "ref_controller_id": CONTROLLER, "pc_id": PC})
    _frame_ok("relay_to_controller", {"type": "screen_answer", "pc_id": PC, "envelope": ENVELOPE})
    _frame_bad("relay_to_controller", {"type": "screen_answer", "pc_id": PC, "envelope": ENVELOPE, "ref_controller_id": CONTROLLER})  # stripped
    _frame_bad("relay_to_controller", {"type": "screen_answer", "envelope": ENVELOPE})
    _frame_bad("relay_to_agent", {"type": "screen_answer", "pc_id": PC, "envelope": ENVELOPE})  # never back to a PC
    _frame_bad("relay_to_controller", {"type": "screen_answer", "pc_id": PC, "envelope": {**ENVELOPE, "payload": ANSWER}, "sdp": ANSWER})


def test_screen_session_frames() -> None:
    base = {"type": "screen_session", "pc_id": PC, "screen_session_id": SID, "controller_id": CONTROLLER, "event": "started", "reason": "started", "path": "direct", "live_seconds": 0, "at": TS}
    for direction in ("agent_to_relay", "relay_to_controller"):
        _frame_ok(direction, base)
        _frame_ok(direction, {**base, "event": "paused", "reason": "secure_desktop", "live_seconds": 42})
        _frame_ok(direction, {**base, "event": "resumed", "reason": "indicator", "path": "relayed", "live_seconds": 50})
        for reason in END_REASONS:
            _frame_ok(direction, {**base, "event": "ended", "reason": reason, "live_seconds": 192})
    schema = load_schemas().document("relay-frames")["$defs"]["agent_screen_session"]["properties"]["reason"]["enum"]
    assert END_REASONS <= set(schema) and {"started", "indicator"} <= set(schema)
    for bad in (
        _with(base, ("reason",), "lease_expired"),  # an input_session reason, not a screen one
        _with(base, ("path",), "turn"),
        _with(base, ("live_seconds",), -1),
        _with(base, ("live_seconds",), ...),
        _with(base, ("phone_name",), "Maya's iPhone"),  # no names
        _with(base, ("sdp",), "v=0"),  # no SDP
        _with(base, ("event",), "suspended"),
    ):
        _frame_bad("agent_to_relay", bad)
    _frame_bad("relay_to_agent", base)


def test_screen_usage_and_allowance_tickets() -> None:
    usage = {"type": "screen_usage", "pc_id": PC, "screen_session_id": SID, "live_seconds": 40, "relayed": True, "relayed_bytes": 2_000_000}
    _frame_ok("agent_to_relay", usage)
    _frame_bad("agent_to_relay", _with(usage, ("relayed_bytes",), -1))
    _frame_bad("agent_to_relay", _with(usage, ("relayed",), ...))
    _frame_bad("agent_to_relay", _with(usage, ("granted_until",), TS))  # the PC never proposes its own ticket
    _frame_bad("relay_to_controller", usage)
    ticket = {"type": "screen_allowance", "screen_session_id": SID, "granted_until": TS, "unlimited": False, "final": False}
    _frame_ok("relay_to_agent", ticket)
    _frame_ok("relay_to_agent", {**ticket, "granted_until": None, "final": True, "reason": "controller_offline"})
    _frame_ok("relay_to_agent", {**ticket, "granted_until": None, "final": True, "reason": "allowance_exhausted", "resets_at": TS})
    _frame_ok("relay_to_agent", {**ticket, "unlimited": True, "reason": "plan_disabled", "final": True})
    _frame_bad("relay_to_agent", _with(ticket, ("reason",), "viewer_gone"))
    _frame_bad("relay_to_agent", _with(ticket, ("final",), ...))
    _frame_bad("relay_to_agent", _with(ticket, ("live_seconds",), 3))
    _frame_bad("relay_to_controller", ticket)  # tickets go to the PC only


def test_relay_screen_rides_only_on_commands() -> None:
    relay = {"received_at": TS, "connection_id": OTHER}
    screen = {"granted_until": TS, "unlimited": False, "stun_urls": ["stun:stun.example.net:3478"]}
    _frame_ok("relay_to_agent", {"type": "command", "envelope": ENVELOPE, "relay": relay})
    _frame_ok("relay_to_agent", {"type": "command", "envelope": ENVELOPE, "relay": {**relay, "screen": screen}})
    _frame_ok("relay_to_agent", {"type": "command", "envelope": ENVELOPE, "relay": {**relay, "screen": {**screen, "granted_until": None, "unlimited": True, "relayed_max_kbps": 500}}})
    for bad_screen in (
        {**screen, "stun_urls": ["turn:turn.example.net:3478"]},  # the PC is STUN only
        {**screen, "turn_credential": "x"},
        {**screen, "relayed_max_kbps": 10},
        {k: v for k, v in screen.items() if k != "unlimited"},
    ):
        _frame_bad("relay_to_agent", {"type": "command", "envelope": ENVELOPE, "relay": {**relay, "screen": bad_screen}})
    _frame_bad("relay_to_agent", {"type": "input_batch", "envelope": ENVELOPE, "relay": {**relay, "screen": screen}})


def test_pairing_decision_carries_the_pc_key_mac_and_the_status_returns_pc_key() -> None:
    decision = {"type": "pairing_decision", "pairing_id": OTHER, "decision": "approve", "kid": "A" * 43, "granted_capabilities": ["status", "media"]}
    _frame_ok("agent_to_relay", decision)  # a pre-1.4 agent sends none
    _frame_ok("agent_to_relay", {**decision, "pc_key_mac": "C" * 43})
    _frame_ok("agent_to_relay", {**decision, "granted_capabilities": ["status", "screen", "screen_audio"]})
    _frame_bad("agent_to_relay", {**decision, "pc_key_mac": "C" * 42})
    _frame_bad("agent_to_relay", {**decision, "pc_public_jwk": FIXTURE["screen"]["pc_public_jwk"]})  # the JWK comes from the PC row
    schemas = load_schemas()
    status = {"pairing_id": OTHER, "state": "approved", "pc_id": PC, "pc_name": "Desk PC", "expires_at": TS, "controller_id": CONTROLLER, "granted_capabilities": ["status", "screen"]}
    schemas.validate_rest("pairing_status_response", status)
    schemas.validate_rest("pairing_status_response", {**status, "pc_key": {"public_jwk": FIXTURE["screen"]["pc_public_jwk"], "mac": "C" * 43}})
    for bad in (
        {**status, "pc_key": {"public_jwk": FIXTURE["screen"]["pc_public_jwk"]}},
        {**status, "pc_key": {"mac": "C" * 43}},
        {**status, "pc_key": {"public_jwk": FIXTURE["screen"]["pc_public_jwk"], "mac": "C" * 43, "kid": "A" * 43}},
        {**status, "pc_key": None},
        {**status, "granted_capabilities": ["shell"]},
    ):
        with pytest.raises(ProtocolError):
            schemas.validate_rest("pairing_status_response", bad)


def test_result_shapes() -> None:
    reg = load_registry()
    result = {
        "screen_session_id": SID,
        "surfaces": [{"index": 0, "width": 2560, "height": 1440, "primary": True}, {"index": 1, "width": 1920, "height": 1080, "primary": False}],
        "surface_index": 0,
        "gen": 1,
        "audio": False,
        "lease_seconds": 8,
        "allowance": ALLOWANCE,
        "consent": "not_required",
    }
    reg.validate_result("screen.session_start", result)
    reg.validate_result("screen.session_start", {**result, "allowance": UNLIMITED, "consent": "granted", "encoder": "vp8"})
    for bad in (
        _with(result, ("consent",), "pending"),  # pending is never a result: the ack is
        _with(result, ("encoder",), "h265"),
        _with(result, ("surfaces",), []),
        _with(result, ("surfaces", 0, "name"), "DELL U2720Q"),
        _with(result, ("answer_sdp",), ANSWER),
        _with(result, ("lease_seconds",), 0),
        _with(result, ("allowance",), ...),
    ):
        with pytest.raises(ProtocolError):
            reg.validate_result("screen.session_start", bad)
    reg.validate_result("screen.session_stop", {"stopped": True, "live_seconds": 192})
    reg.validate_result("screen.session_stop", {"stopped": False, "live_seconds": 0})
    with pytest.raises(ProtocolError):
        reg.validate_result("screen.session_stop", {"stopped": True})


# ----- data channel ----------------------------------------------------------------------------------------------------


def test_data_channel_vocabulary_is_closed_per_direction() -> None:
    assert SCREEN_DATA_CHANNEL_LABEL == "dome-screen-v1"
    for text in ('{"type":"hb"}', '{"type":"keyframe"}', '{"type":"bye"}', '{"type":"audio","on":false}'):
        assert parse_screen_dc_message(text, "phone_to_pc")["type"] == json.loads(text)["type"]
        assert parse_screen_dc_message(text.encode(), "phone_to_pc")["type"] == json.loads(text)["type"]
    geometry = {"type": "geometry", "gen": 2, "surface_index": 0, "width": 2560, "height": 1440, "rotation": 0, "reserved": [{"x": 0, "y": 0, "w": 65535, "h": 1200}]}
    for msg in (
        geometry,
        {"type": "state", "state": "sending", "encoder": "h264", "data_saver": False},
        {"type": "state", "state": "paused", "reason": "secure_desktop"},
        {"type": "state", "state": "paused", "reason": "indicator"},
        {"type": "allowance", "unlimited": False, "remaining_seconds": 300, "resets_at": TS},
        {"type": "restricted", "restricted": True, "reason": "elevated"},
        {"type": "restricted", "restricted": False},
    ):
        assert parse_screen_dc_message(dumps_compact(msg), "pc_to_phone") == msg
    # direction matters: a PC never accepts its own vocabulary, a phone never accepts input-like messages
    for text, direction in (
        (dumps_compact(geometry), "phone_to_pc"),
        ('{"type":"hb"}', "pc_to_phone"),
        ('{"type":"pointer_button","button":"left","action":"click"}', "phone_to_pc"),
        ('{"type":"text","text":"rm -rf"}', "phone_to_pc"),
        ('{"type":"roi","gen_seen":1,"rect":{"x":0,"y":0,"w":1,"h":1}}', "phone_to_pc"),  # Phase 3
        ('{"type":"cursor_pos","x":1,"y":1}', "pc_to_phone"),  # Phase 2
        ('{"type":"hb","extra":1}', "phone_to_pc"),
        ('{"type":"geometry","gen":0,"surface_index":0,"width":1,"height":1,"rotation":0,"reserved":[]}', "pc_to_phone"),
        ('{"type":"geometry","gen":1,"surface_index":0,"width":1,"height":1,"rotation":45,"reserved":[]}', "pc_to_phone"),
        ('{"type":"state","state":"recording"}', "pc_to_phone"),
        ("[]", "phone_to_pc"),
        ('{"type":"hb","type":"bye"}', "phone_to_pc"),
    ):
        with pytest.raises(ProtocolError) as ei:
            parse_screen_dc_message(text, direction)
        assert ei.value.code == "MALFORMED_MESSAGE", text
    with pytest.raises(ProtocolError) as ei:
        parse_screen_dc_message('{"type":"hb","pad":"' + "x" * 1100 + '"}', "phone_to_pc")
    assert ei.value.code == "PAYLOAD_TOO_LARGE"
    with pytest.raises(ProtocolError):
        parse_screen_dc_message(b"\xff\xfe", "phone_to_pc")
    with pytest.raises(ValueError):
        load_schemas().validate_screen_dc_message("sideways", {"type": "hb"})


# ----- SDP helper -------------------------------------------------------------------------------------------------------


def test_sdp_helper_and_fingerprints() -> None:
    assert screen_sdp_acceptable(OFFER) and screen_sdp_acceptable(ANSWER)
    assert not screen_sdp_acceptable(OFFER.replace("\r\n", "\n"))
    assert not screen_sdp_acceptable(None) and not screen_sdp_acceptable(b"v=0\r\n")  # type: ignore[arg-type]
    assert screen_sdp_acceptable("v=0\r\n", max_bytes=5) and not screen_sdp_acceptable("v=0\r\n", max_bytes=4)
    assert sdp_fingerprints(ANSWER) == [FIXTURE["screen"]["fingerprint"]] * 3
    assert sdp_fingerprints(OFFER) == [":".join(f"{b:02X}" for b in bytes(range(1, 33)))]
    lower = ANSWER.replace(FIXTURE["screen"]["fingerprint"], FIXTURE["screen"]["fingerprint"].lower())
    assert sdp_fingerprints(lower) == sdp_fingerprints(ANSWER)  # case-insensitive hex, normalised upper
    assert sdp_fingerprints("a=fingerprint:sha-1 00:11\r\na=fingerprint:sha-256 zz\r\n") == []
    assert offer_digest(OFFER) == FIXTURE["screen"]["offer_digest"]


# ----- the PC-signed answer --------------------------------------------------------------------------------------------


def _pc() -> tuple[object, dict[str, str], str]:
    key = generate_private_key()
    jwk = jwk_from_public_key(key.public_key())
    return key, jwk, kid_from_jwk(jwk)


def _answer(pc_key, **overrides) -> dict:
    payload = build_screen_answer_payload(
        account_id=ACCOUNT, pc_id=PC, controller_id=CONTROLLER, command_id=COMMAND, screen_session_id=SID, offer_sdp=OFFER, answer_sdp=ANSWER, now=now_utc()
    )
    payload.update(overrides)
    return sign_payload(pc_key, dumps_compact(payload)).to_dict()  # type: ignore[arg-type]


def _verify(raw, pinned_jwk, **kw):
    args = {"pinned_jwk": pinned_jwk, "account_id": ACCOUNT, "controller_id": CONTROLLER, "pc_id": PC, "command_id": COMMAND, "offer_sdp": OFFER}
    args.update(kw)
    return verify_screen_answer_payload(raw, **args)


def test_answer_round_trip() -> None:
    pc_key, pc_jwk, pc_kid = _pc()
    payload = build_screen_answer_payload(
        account_id=ACCOUNT, pc_id=PC, controller_id=CONTROLLER, command_id=COMMAND, screen_session_id=SID, offer_sdp=OFFER, answer_sdp=ANSWER
    )
    load_schemas().validate_screen_answer_payload(payload)
    assert payload["type"] == "screen_answer" and payload["protocol_version"] == "1.4" and payload["offer_digest"] == offer_digest(OFFER)
    assert (parse_rfc3339(payload["expires_at"]) - parse_rfc3339(payload["issued_at"])) == timedelta(seconds=60)
    verified = _verify(_answer(pc_key), pc_jwk)
    assert verified.pc_kid == pc_kid and verified.answer_sdp == ANSWER and verified.screen_session_id == SID
    assert verified.fingerprint == FIXTURE["screen"]["fingerprint"]
    with pytest.raises(ProtocolError):  # the builder refuses an SDP that would fail the strict check anyway
        build_screen_answer_payload(
            account_id=ACCOUNT, pc_id=PC, controller_id=CONTROLLER, command_id=COMMAND, screen_session_id=SID, offer_sdp=OFFER, answer_sdp=ANSWER.replace("\r\n", "\n")
        )


def test_answer_rejections_are_a_hard_stop() -> None:
    pc_key, pc_jwk, _pc_kid = _pc()
    other_key, other_jwk, _other_kid = _pc()
    good = _answer(pc_key)
    cases = [
        (good, {"pinned_jwk": other_jwk}, "kid is not the pinned PC key", "UNKNOWN_KEY"),  # signed by a key that is not the pin
        (_answer(other_key), {}, "kid is not the pinned PC key", "UNKNOWN_KEY"),
        ({**good, "payload": good["payload"].replace("a=setup:active", "a=setup:passive")}, {}, "envelope or signature", "SIGNATURE_INVALID"),
        ({**good, "alg": "none"}, {}, "envelope or signature", "SIGNATURE_INVALID"),
        (good, {"account_id": OTHER}, "account_id is not ours", None),
        (good, {"controller_id": OTHER}, "controller_id is not ours", None),
        (good, {"pc_id": OTHER}, "pc_id is not ours", None),
        (good, {"command_id": OTHER}, "command_id is not ours", None),
        (good, {"offer_sdp": OFFER + "a=x\r\n"}, "offer_digest does not match the offer we sent", None),
        (good, {"now": now_utc() + timedelta(seconds=120)}, "window", "COMMAND_EXPIRED"),
        (good, {"now": now_utc() - timedelta(seconds=120)}, "window", "CLOCK_SKEW"),
        (_answer(pc_key, expires_at=format_rfc3339(now_utc() + timedelta(seconds=600))), {}, "window", "MALFORMED_MESSAGE"),
        (_answer(pc_key, answer_sdp=ANSWER + "a=fingerprint:sha-256 " + ":".join(["00"] * 32) + "\r\n"), {}, "fingerprints missing or not identical", None),
        (_answer(pc_key, answer_sdp="v=0\r\na=sendonly\r\n"), {}, "fingerprints missing or not identical", None),
        (_answer(pc_key, type="answer"), {}, "payload shape", "MALFORMED_MESSAGE"),
        (_answer(pc_key, extra=1), {}, "payload shape", "MALFORMED_MESSAGE"),
        (_answer(pc_key, screen_session_id="short"), {}, "payload shape", "MALFORMED_MESSAGE"),
    ]
    for raw, kw, reason, cause in cases:
        kw = dict(kw)
        with pytest.raises(ProtocolError) as ei:
            _verify(raw, kw.pop("pinned_jwk", pc_jwk), **kw)
        assert ei.value.code == "SCREEN_ANSWER_INVALID", reason
        assert ei.value.detail["reason"] == reason, ei.value.detail
        if cause:
            assert ei.value.detail["cause"] == cause, ei.value.detail
        assert ANSWER[:40] not in str(ei.value) and ":".join(["00"] * 32) not in str(ei.value)  # never echo SDP
    # the one honest exception: a newer MINOR is "update", not "not your PC"
    with pytest.raises(ProtocolError) as ei:
        _verify(_answer(pc_key, protocol_version="1.5"), pc_jwk)
    assert ei.value.code == "PROTOCOL_INCOMPATIBLE" and ei.value.detail == {"peer": "1.5", "supported": ["1.4"]}
    with pytest.raises(ProtocolError) as ei:
        _verify(good, {"kty": "EC", "crv": "P-256", "x": "A" * 43, "y": "A" * 43})
    assert ei.value.code == "SCREEN_ANSWER_INVALID" and ei.value.detail["reason"] == "pinned key unusable"


# ----- PC key pinning -------------------------------------------------------------------------------------------------


def test_pc_key_mac_vector_and_constant_time_verify() -> None:
    d, screen = FIXTURE["digests"], FIXTURE["screen"]
    mac = pc_key_mac(d["pairing_code"], d["pairing_id"], d["pc_id"], FIXTURE["kid"], screen["pc_kid"])
    assert mac == screen["pc_key_mac"] and len(mac) == 43
    assert pc_key_mac(d["pairing_code"].lower().replace("-", " "), d["pairing_id"], d["pc_id"], FIXTURE["kid"], screen["pc_kid"]) == mac  # normalised code
    assert verify_pc_key_mac(d["pairing_code"], d["pairing_id"], d["pc_id"], FIXTURE["kid"], screen["pc_kid"], mac)
    _other_key, other_jwk, other_kid = _pc()
    assert not verify_pc_key_mac(d["pairing_code"], d["pairing_id"], d["pc_id"], FIXTURE["kid"], other_kid, mac)  # substituted PC key
    assert not verify_pc_key_mac(d["pairing_code"], d["pairing_id"], d["pc_id"], other_kid, screen["pc_kid"], mac)  # another controller
    assert not verify_pc_key_mac(d["pairing_code"], OTHER, d["pc_id"], FIXTURE["kid"], screen["pc_kid"], mac)
    assert not verify_pc_key_mac(d["pairing_code"], d["pairing_id"], d["pc_id"], FIXTURE["kid"], screen["pc_kid"], mac[:-1] + "A")
    assert not verify_pc_key_mac(d["pairing_code"], d["pairing_id"], d["pc_id"], FIXTURE["kid"], screen["pc_kid"], None)
    assert pc_key_mac("ABCDEFGHJKMNPQRSTVWX", d["pairing_id"], d["pc_id"], FIXTURE["kid"], screen["pc_kid"]) != mac  # another code
    with pytest.raises(ProtocolError):
        pc_key_mac("not a code", d["pairing_id"], d["pc_id"], FIXTURE["kid"], screen["pc_kid"])
    assert kid_from_jwk(other_jwk) == other_kid


# ----- input events: capabilities, stamping, check order ---------------------------------------------------------------


def test_new_input_events_capabilities_and_stamp() -> None:
    move_to = {"type": "pointer_move_to", "x": 32768, "y": 100, "gen": 7}
    wheel = {"type": "pointer_wheel", "dx": 0, "dy": -240}
    f11 = {"type": "key", "key": "f11"}
    enter = {"type": "key", "key": "enter"}
    assert input_event_capabilities([move_to, wheel]) == {"pointer"} and input_event_capabilities([f11]) == {"keyboard"}
    assert input_event_since(move_to) == input_event_since(wheel) == input_event_since(f11) == SCREEN_INPUT_PROTOCOL_VERSION == "1.4"
    assert input_event_since(enter) == input_event_since({"type": "pointer_move", "dx": 1, "dy": 1}) == INPUT_BATCH_PROTOCOL_VERSION == "1.1"
    assert input_batch_protocol_version([]) == "1.1" and input_batch_protocol_version([enter]) == "1.1"
    for events in ([move_to], [wheel], [f11], [enter, move_to], [{"type": "pointer_move", "dx": 1, "dy": 1}, wheel]):
        assert input_batch_protocol_version(events) == "1.4", events
    assert build_input_batch_payload(account_id=ACCOUNT, controller_id=CONTROLLER, target_pc_id=PC, input_session_id=SID, seq=1, events=[enter])["protocol_version"] == "1.1"
    assert build_input_batch_payload(account_id=ACCOUNT, controller_id=CONTROLLER, target_pc_id=PC, input_session_id=SID, seq=1, events=[move_to])["protocol_version"] == "1.4"
    schemas = load_schemas()
    good = build_input_batch_payload(account_id=ACCOUNT, controller_id=CONTROLLER, target_pc_id=PC, input_session_id=SID, seq=1, events=[move_to, wheel, f11])
    schemas.validate_input_batch_payload(good)
    for bad in (
        {"type": "pointer_move_to", "x": 65536, "y": 0, "gen": 1},
        {"type": "pointer_move_to", "x": -1, "y": 0, "gen": 1},
        {"type": "pointer_move_to", "x": 0, "y": 0, "gen": 0},
        {"type": "pointer_move_to", "x": 0, "y": 0},
        {"type": "pointer_wheel", "dx": 1201, "dy": 0},
        {"type": "pointer_wheel", "dx": 0, "dy": -1201},
        {"type": "key", "key": "f12"},
    ):
        with pytest.raises(ProtocolError):
            schemas.validate_input_batch_payload({**good, "events": [bad]})


def test_batch_with_a_screen_event_must_be_stamped_1_4() -> None:
    """rules.command_version_stamping (1.4): a pointer_move_to stamped 1.1 is PROTOCOL_INCOMPATIBLE with required 1.4."""
    key, kid, record = _controller()
    resolve = lambda k: record if k == kid else None
    move_to = {"type": "pointer_move_to", "x": 1, "y": 2, "gen": 1}
    good = build_input_batch_payload(account_id=ACCOUNT, controller_id=CONTROLLER, target_pc_id=PC, input_session_id=SID, seq=1, events=[move_to])
    vb = verify_and_parse_input_batch(sign_payload(key, dumps_compact(good)).to_dict(), resolve)  # type: ignore[arg-type]
    assert vb.required_protocol_version == "1.4" and vb.required_capabilities == {"pointer"}
    for stamp in ("1.1", "1.2", "1.3"):
        with pytest.raises(ProtocolError) as ei:
            verify_and_parse_input_batch(sign_payload(key, dumps_compact({**good, "protocol_version": stamp})).to_dict(), resolve)  # type: ignore[arg-type]
        assert ei.value.code == "PROTOCOL_INCOMPATIBLE" and ei.value.detail == {"peer": stamp, "required": "1.4"}, stamp
    # a plain touchpad batch may still carry any stamp from 1.1 up to this library's version
    plain = build_input_batch_payload(account_id=ACCOUNT, controller_id=CONTROLLER, target_pc_id=PC, input_session_id=SID, seq=2, events=[{"type": "key", "key": "enter"}])
    for stamp in ("1.1", "1.2", "1.3", "1.4"):
        assert verify_and_parse_input_batch(sign_payload(key, dumps_compact({**plain, "protocol_version": stamp})).to_dict(), resolve).required_protocol_version == "1.1"  # type: ignore[arg-type]


def test_check_order_version_before_schema() -> None:
    """A payload from a newer MINOR with events or fields we do not know is PROTOCOL_INCOMPATIBLE, never MALFORMED_MESSAGE;
    an unparseable stamp still falls through to the schema."""
    key, kid, record = _controller()
    resolve = lambda k: record if k == kid else None
    future_event = {"type": "pointer_tilt", "angle": 3}  # does not exist in 1.4
    batch = build_input_batch_payload(account_id=ACCOUNT, controller_id=CONTROLLER, target_pc_id=PC, input_session_id=SID, seq=1, events=[future_event])
    with pytest.raises(ProtocolError) as ei:
        verify_and_parse_input_batch(sign_payload(key, dumps_compact({**batch, "protocol_version": "1.5"})).to_dict(), resolve)  # type: ignore[arg-type]
    assert ei.value.code == "PROTOCOL_INCOMPATIBLE" and ei.value.detail == {"peer": "1.5", "supported": ["1.4"]}
    with pytest.raises(ProtocolError) as ei:  # the same unknown event stamped at our version: that IS malformed
        verify_and_parse_input_batch(sign_payload(key, dumps_compact({**batch, "protocol_version": "1.4"})).to_dict(), resolve)  # type: ignore[arg-type]
    assert ei.value.code == "MALFORMED_MESSAGE"
    with pytest.raises(ProtocolError) as ei:  # a stamp below the 1.1 floor is refused before the schema too
        verify_and_parse_input_batch(sign_payload(key, dumps_compact({**batch, "protocol_version": "1.0"})).to_dict(), resolve)  # type: ignore[arg-type]
    assert ei.value.code == "PROTOCOL_INCOMPATIBLE" and ei.value.detail == {"peer": "1.0", "required": "1.1"}
    for junk in ("1", "v1.4", "1.4\n", 14, None):
        with pytest.raises(ProtocolError) as ei:
            verify_and_parse_input_batch(sign_payload(key, dumps_compact({**batch, "protocol_version": junk})).to_dict(), resolve)  # type: ignore[arg-type]
        assert ei.value.code == "MALFORMED_MESSAGE", junk
    # commands: a 1.5 command with a field this library does not know
    now = now_utc()
    command = {
        "type": "command",
        "protocol_version": "1.5",
        "command_id": COMMAND,
        "account_id": ACCOUNT,
        "controller_id": CONTROLLER,
        "target_pc_id": PC,
        "action": "screen.session_start",
        "params": {"offer_sdp": OFFER, "surface": "primary", "audio": True, "takeover": False, "hdr": True},
        "target": None,
        "issued_at": format_rfc3339(now),
        "expires_at": format_rfc3339(now + timedelta(seconds=60)),
        "nonce": "AAAAAAAAAAAAAAAAAAAAAA",
        "priority": "high",
    }
    with pytest.raises(ProtocolError) as ei:
        verify_and_parse_command(sign_payload(key, dumps_compact(command)).to_dict(), resolve)  # type: ignore[arg-type]
    assert ei.value.code == "PROTOCOL_INCOMPATIBLE" and ei.value.detail == {"peer": "1.5", "supported": ["1.4"]}
    # a known action stamped below its since is refused before the schema as well (an older phone can never send it)
    with pytest.raises(ProtocolError) as ei:
        verify_and_parse_command(sign_payload(key, dumps_compact({**command, "protocol_version": "1.3"})).to_dict(), resolve)  # type: ignore[arg-type]
    assert ei.value.code == "PROTOCOL_INCOMPATIBLE" and ei.value.detail == {"peer": "1.3", "required": "1.4"}
    # and the honest 1.4 command, without the unknown fields, verifies with the defaults applied
    del command["priority"]
    del command["params"]["hdr"]
    verified = verify_and_parse_command(sign_payload(key, dumps_compact({**command, "protocol_version": "1.4"})).to_dict(), resolve)  # type: ignore[arg-type]
    assert verified.spec.name == "screen.session_start" and verified.params["surface"] == "primary" and verified.params["offer_sdp"] == OFFER
    stop = {**command, "protocol_version": "1.4", "action": "screen.session_stop", "params": {"screen_session_id": SID}, "expires_at": format_rfc3339(now + timedelta(seconds=30))}
    assert verify_and_parse_command(sign_payload(key, dumps_compact(stop)).to_dict(), resolve).spec.idempotent  # type: ignore[arg-type]


def test_validation_messages_never_echo_sdp() -> None:
    reg = load_registry()
    sentinel = "a=candidate:1 1 udp 2113937151 203.0.113.9 51234 typ host\r\n"
    with pytest.raises(ProtocolError) as ei:
        reg.validate_params("screen.session_start", {"offer_sdp": OFFER + sentinel + "\x00"})
    assert "203.0.113.9" not in str(ei.value) and "candidate" not in str(ei.value)
