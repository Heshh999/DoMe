"""Protocol 1.4, Screen view (docs/PROTOCOL.md §20; docs/design/screen-view.md §11): the registry additions, every new
frame, def, result and REST shape (accept the example, refuse unknown fields and out-of-range values), the content-based
input-batch stamp and the 1.4 check order, the PC-signed answer, PC key pinning and the data-channel vocabulary.
Protocol 1.5 (docs/design/screen-view-2a.md §1): content-based command stamping (`param_since`), the answer carrying the
start's stamp, the pointer messages and the per-type size cap of the data channel."""

from __future__ import annotations

import copy
import json
from datetime import timedelta
from pathlib import Path

import pytest

from dome_protocol import (
    INPUT_BATCH_PROTOCOL_VERSION,
    SCREEN_CURSOR_CHANNEL_LABEL,
    SCREEN_DATA_CHANNEL_LABEL,
    SCREEN_INPUT_PROTOCOL_VERSION,
    KeyRecord,
    ProtocolError,
    build_input_batch_payload,
    build_screen_answer_payload,
    command_protocol_version,
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
DC_CASES = json.loads((Path(__file__).resolve().parents[2] / "protocol" / "fixtures" / "screen-dc-cases.json").read_text(encoding="utf-8"))
# the real 1x1 RGBA test PNG of the shared data-channel cases (1.5)
CURSOR_PNG = next(json.loads(c["text"])["png"] for c in DC_CASES["accepted"] if c["name"] == "custom cursor_shape with a real 1x1 RGBA PNG")
CURSOR_KINDS = ("arrow", "ibeam", "hand", "wait", "appstarting", "cross", "no", "sizeall", "sizens", "sizewe", "sizenesw", "sizenwse", "uparrow", "help")
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


def test_registry_is_1_5_with_the_screen_capabilities_and_actions() -> None:
    reg = load_registry()
    assert reg.protocol_version == "1.5" and reg.registry_version == "1.5"
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
    # 1.5: `cursor` only when the start carried it; the REST pc row may name the agent's protocol versions
    for mode in ("drawn", "overlay"):
        reg.validate_result("screen.session_start", {**result, "cursor": mode})
    with pytest.raises(ProtocolError):
        reg.validate_result("screen.session_start", {**result, "cursor": "video"})
    pc = {"id": PC, "name": "Office", "enabled": True, "connection": "online", "last_seen": None, "created_at": TS, "platform": "windows"}
    for row in (pc, {**pc, "protocol_versions": []}, {**pc, "protocol_versions": ["1.0", "1.1", "1.2", "1.3", "1.4", "1.5"]}):
        load_schemas().validate_rest("pc", row)
    for versions in (["1.5\n"], ["v1.5"], ["1"], [15], ["1.0"] * 9, "1.5"):
        with pytest.raises(ProtocolError) as ei:
            load_schemas().validate_rest("pc", {**pc, "protocol_versions": versions})
        assert ei.value.code == "MALFORMED_MESSAGE", versions
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
        ('{"type":"cursor_pos","x":1,"y":1}', "pc_to_phone"),  # 1.5 needs gen and visible
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
    with pytest.raises(ProtocolError) as ei:  # 1.5: an over-long message is refused before parsing, as MALFORMED_MESSAGE
        parse_screen_dc_message('{"type":"hb","pad":"' + "x" * 1100 + '"}', "phone_to_pc")
    assert ei.value.code == "MALFORMED_MESSAGE"
    with pytest.raises(ProtocolError):
        parse_screen_dc_message(b"\xff\xfe", "phone_to_pc")
    with pytest.raises(ValueError):
        load_schemas().validate_screen_dc_message("sideways", {"type": "hb"})


def test_1_5_limit_rules_and_the_cursor_channel_label() -> None:
    reg = load_registry()
    assert reg.limits["screen_dc_cursor_shape_max_bytes"] == 12288 and reg.limits["screen_dc_message_max_bytes"] == 1024
    assert SCREEN_CURSOR_CHANNEL_LABEL == "dome-screen-cursor-v1" and SCREEN_DATA_CHANNEL_LABEL == "dome-screen-v1"
    rules = _load_json("version.json")["rules"]
    assert "screen_cursor" in rules and len(rules["screen_cursor"]) > 200
    stamping = rules["command_version_stamping"]
    assert "param_since" in stamping and "command_protocol_version" in stamping and "checked after schema validation" in stamping
    assert "INVALID_PARAMETERS" in stamping and "ANY command whose stamp" in stamping
    assert "protocol_version: 1.4" not in rules["screen_signaling"] and "1.4 for a 1.4-stamped start" in rules["screen_signaling"]
    assert "dome-screen-cursor-v1" in rules["screen_signaling"] and "only in a start that carries `cursor`" in rules["screen_signaling"]
    assert "(1b) Surface switch" in rules["screen_sessions"] and "screen_session_max_seconds" in rules["screen_sessions"]
    assert "A surface switch is a new session" in rules["screen_metering"]
    content = rules["screen_content"]
    assert "cursor_pos and cursor_shape (1.5)" in content and "screen_dc_cursor_shape_max_bytes" in content and "screen_cursor (4)" in content
    cursor = rules["screen_cursor"]
    assert "no clip rectangle is ever set" in cursor and "before and after each injected relative `pointer_move`" in cursor
    assert "a 1.4 phone never receives them" in cursor and "kinds, sizes and counts only" in cursor
    assert "since 1.5, any command whose stamp" in reg.errors["PROTOCOL_INCOMPATIBLE"]["description"]


def test_the_cursor_param_has_no_default_and_raises_the_stamp() -> None:
    """rules.command_version_stamping (1.5): the stamp follows the params PRESENT, never the defaults validation fills in."""
    reg = load_registry()
    start = reg.get("screen.session_start")
    assert start.since == "1.4" and start.param_since == {"cursor": "1.5"}
    cursor = start.params_schema["properties"]["cursor"]
    assert cursor["enum"] == ["drawn", "overlay"] and "default" not in cursor
    plain = {"offer_sdp": OFFER, "surface": 1}
    assert command_protocol_version("screen.session_start", plain) == "1.4" == reg.min_version("screen.session_start", plain)
    for mode in ("overlay", "drawn"):
        assert command_protocol_version("screen.session_start", {**plain, "cursor": mode}) == "1.5"
        assert command_protocol_version("screen.session_start", {**plain, "cursor": mode}, registry=reg) == "1.5"
    assert reg.min_version("screen.session_start") == "1.4" and reg.min_version("screen.session_start", None) == "1.4"
    filled = reg.validate_params("screen.session_start", {"offer_sdp": OFFER})
    assert "cursor" not in filled and reg.min_version("screen.session_start", filled) == "1.4"  # defaults never raise it
    assert reg.validate_params("screen.session_start", {"offer_sdp": OFFER, "cursor": "overlay"})["cursor"] == "overlay"
    for bad in ("video", "", 1, None, True):
        with pytest.raises(ProtocolError) as ei:
            reg.validate_params("screen.session_start", {"offer_sdp": OFFER, "cursor": bad})
        assert ei.value.code == "INVALID_PARAMETERS", bad
    # no other action has a param_since, so a `cursor` key never moves their stamp
    for name, spec in reg.actions.items():
        if name != "screen.session_start":
            assert command_protocol_version(name, {"cursor": "overlay"}) == spec.since == reg.min_version(name, {}), name
    with pytest.raises(ProtocolError) as ei:
        command_protocol_version("app.nope", {})
    assert ei.value.code == "UNKNOWN_ACTION"


def _start_command(**overrides) -> dict:
    now = now_utc()
    command = {
        "type": "command",
        "protocol_version": "1.4",
        "command_id": COMMAND,
        "account_id": ACCOUNT,
        "controller_id": CONTROLLER,
        "target_pc_id": PC,
        "action": "screen.session_start",
        "params": {"offer_sdp": OFFER, "cursor": "overlay"},
        "target": None,
        "issued_at": format_rfc3339(now),
        "expires_at": format_rfc3339(now + timedelta(seconds=60)),
        "nonce": "AAAAAAAAAAAAAAAAAAAAAA",
    }
    command.update(overrides)
    return command


def test_a_start_is_refused_below_what_its_present_params_need() -> None:
    """A 1.4-stamped start carrying `cursor` is PROTOCOL_INCOMPATIBLE {peer, required: 1.5}, judged AFTER the schema and
    the params check on the RAW signed params; without `cursor` a 1.4 stamp passes and nothing adds the key."""
    key, kid, record = _controller()
    resolve = lambda k: record if k == kid else None

    def verify(payload):
        return verify_and_parse_command(sign_payload(key, dumps_compact(payload)).to_dict(), resolve)  # type: ignore[arg-type]

    with pytest.raises(ProtocolError) as ei:
        verify(_start_command())
    assert ei.value.code == "PROTOCOL_INCOMPATIBLE" and ei.value.detail == {"peer": "1.4", "required": "1.5"}
    with pytest.raises(ProtocolError) as ei:  # the schema runs first: an unknown top-level field stays MALFORMED_MESSAGE
        verify(_start_command(priority="high"))
    assert ei.value.code == "MALFORMED_MESSAGE"
    with pytest.raises(ProtocolError) as ei:  # and the params check runs first: a bad value is INVALID_PARAMETERS
        verify(_start_command(params={"offer_sdp": OFFER, "cursor": "video"}))
    assert ei.value.code == "INVALID_PARAMETERS"
    verified = verify(_start_command(protocol_version="1.5"))
    assert verified.params["cursor"] == "overlay" and verified.params["surface"] == "primary"
    assert verified.payload["params"] == {"offer_sdp": OFFER, "cursor": "overlay"}  # the raw object the agent reads presence from
    old = verify(_start_command(params={"offer_sdp": OFFER, "surface": 1}))
    assert old.payload["protocol_version"] == "1.4" and "cursor" not in old.params and old.payload["params"] == {"offer_sdp": OFFER, "surface": 1}
    assert verify(_start_command(protocol_version="1.5", params={"offer_sdp": OFFER})).payload["params"] == {"offer_sdp": OFFER}


def test_cursor_messages_are_pc_to_phone_only_and_bounded() -> None:
    pos = {"type": "cursor_pos", "gen": 3, "x": 0, "y": 65535, "visible": False}
    custom = {"type": "cursor_shape", "kind": "custom", "width": 64, "height": 64, "hotspot_x": 63, "hotspot_y": 0, "png": CURSOR_PNG}
    state = {"type": "state", "state": "sending", "encoder": "vp8", "data_saver": False, "cursor": "drawn"}
    for msg in (pos, custom, state, {**state, "cursor": "overlay"}, {**custom, "png": "A" * 8192}, *({"type": "cursor_shape", "kind": k} for k in CURSOR_KINDS)):
        assert parse_screen_dc_message(dumps_compact(msg), "pc_to_phone") == msg
        if msg["type"] != "state":
            with pytest.raises(ProtocolError) as ei:  # the phone never sends pointer messages
                parse_screen_dc_message(dumps_compact(msg), "phone_to_pc")
            assert ei.value.code == "MALFORMED_MESSAGE"
    for bad in (
        _with(pos, ("visible",), ...),
        _with(pos, ("gen",), 0),
        _with(pos, ("x",), 65536),
        _with(pos, ("y",), -1),
        _with(pos, ("on_surface",), True),
        {"type": "cursor_shape", "kind": "pointer"},
        {"type": "cursor_shape", "kind": "arrow", "width": 1},
        {"type": "cursor_shape", "kind": "other"},
        _with(custom, ("png",), ...),
        _with(custom, ("width",), 0),
        _with(custom, ("height",), 65),
        _with(custom, ("hotspot_x",), 64),
        _with(custom, ("visible",), True),
        _with(custom, ("png",), "A" * 28),  # shorter than any PNG
        _with(custom, ("png",), "A" * 32 + "="),  # 33 characters: not a base64 length
        _with(custom, ("png",), "A" * 8196),  # over 8192 characters
        _with(custom, ("png",), CURSOR_PNG[:10] + "-" + CURSOR_PNG[11:]),  # base64url is not standard base64
        _with(custom, ("png",), "AA==" + "A" * 32),  # padding only at the end
        _with(custom, ("png",), "A" * 32 + "\n" + "A" * 32),  # no line breaks
        _with(state, ("cursor",), "video"),
    ):
        with pytest.raises(ProtocolError) as ei:
            parse_screen_dc_message(dumps_compact(bad), "pc_to_phone")
        assert ei.value.code == "MALFORMED_MESSAGE", bad


def _padded(text: str, total_bytes: int) -> str:
    """The same JSON object with spaces before its closing brace, exactly ``total_bytes`` bytes."""
    out = text[:-1] + " " * (total_bytes - len(text.encode())) + "}"
    assert len(out.encode()) == total_bytes
    return out


def test_the_data_channel_size_cap_is_per_type() -> None:
    """§1.6: at most 1024 bytes, except a pc_to_phone cursor_shape (at most 12288); every refusal is MALFORMED_MESSAGE."""
    custom = dumps_compact({"type": "cursor_shape", "kind": "custom", "width": 1, "height": 1, "hotspot_x": 0, "hotspot_y": 0, "png": "A" * 8192})
    named = '{"type":"cursor_shape","kind":"arrow"}'
    state = '{"type":"state","state":"sending"}'
    hb = '{"type":"hb"}'
    assert parse_screen_dc_message(_padded(hb, 1024), "phone_to_pc") == {"type": "hb"}
    assert parse_screen_dc_message(_padded(state, 1024), "pc_to_phone") == {"type": "state", "state": "sending"}
    assert parse_screen_dc_message(_padded(named, 1025), "pc_to_phone") == {"type": "cursor_shape", "kind": "arrow"}  # any cursor_shape
    for size in (9000, 12288):
        assert parse_screen_dc_message(_padded(custom, size), "pc_to_phone")["kind"] == "custom", size
        assert parse_screen_dc_message(_padded(custom, size).encode(), "pc_to_phone")["kind"] == "custom", size
    for text, direction, why in (
        (_padded(hb, 1025), "phone_to_pc", "data-channel message too long"),
        (_padded(hb, 1025).encode(), "phone_to_pc", "data-channel message too long"),
        (_padded(named, 1025), "phone_to_pc", "data-channel message too long"),  # the larger cap is for the phone's side only
        (_padded(state, 1025), "pc_to_phone", "only cursor_shape may exceed screen_dc_message_max_bytes"),
        (_padded(custom, 12289), "pc_to_phone", "data-channel message too long"),
        (_padded(custom, 12289).encode(), "pc_to_phone", "data-channel message too long"),
        (_padded(hb, 20000), "phone_to_pc", "data-channel message too long"),
    ):
        with pytest.raises(ProtocolError) as ei:
            parse_screen_dc_message(text, direction)  # type: ignore[arg-type]
        assert ei.value.code == "MALFORMED_MESSAGE" and why in str(ei.value), (direction, len(text))


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
        account_id=ACCOUNT,
        pc_id=PC,
        controller_id=CONTROLLER,
        command_id=COMMAND,
        screen_session_id=SID,
        offer_sdp=OFFER,
        answer_sdp=ANSWER,
        protocol_version="1.5",
        now=now_utc(),
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
        account_id=ACCOUNT, pc_id=PC, controller_id=CONTROLLER, command_id=COMMAND, screen_session_id=SID, offer_sdp=OFFER, answer_sdp=ANSWER, protocol_version="1.5"
    )
    load_schemas().validate_screen_answer_payload(payload)
    assert payload["type"] == "screen_answer" and payload["protocol_version"] == "1.5" and payload["offer_digest"] == offer_digest(OFFER)
    assert (parse_rfc3339(payload["expires_at"]) - parse_rfc3339(payload["issued_at"])) == timedelta(seconds=60)
    verified = _verify(_answer(pc_key), pc_jwk)
    assert verified.pc_kid == pc_kid and verified.answer_sdp == ANSWER and verified.screen_session_id == SID
    assert verified.fingerprint == FIXTURE["screen"]["fingerprint"]
    with pytest.raises(ProtocolError):  # the builder refuses an SDP that would fail the strict check anyway
        build_screen_answer_payload(
            account_id=ACCOUNT,
            pc_id=PC,
            controller_id=CONTROLLER,
            command_id=COMMAND,
            screen_session_id=SID,
            offer_sdp=OFFER,
            answer_sdp=ANSWER.replace("\r\n", "\n"),
            protocol_version="1.5",
        )


def test_the_answer_carries_the_stamp_of_the_start_it_answers() -> None:
    """rules.screen_signaling (5), 1.5: a 1.4 start gets a 1.4 answer that a 1.4 phone accepts; the builder refuses a stamp
    this library does not speak, and the stamp is a required argument (no silent default to the library's version)."""
    pc_key, pc_jwk, _pc_kid = _pc()
    args = {"account_id": ACCOUNT, "pc_id": PC, "controller_id": CONTROLLER, "command_id": COMMAND, "screen_session_id": SID, "offer_sdp": OFFER, "answer_sdp": ANSWER}
    old = build_screen_answer_payload(**args, protocol_version="1.4", now=now_utc())
    assert old["protocol_version"] == "1.4"
    raw = sign_payload(pc_key, dumps_compact(old)).to_dict()  # type: ignore[arg-type]
    phone_1_4 = copy.copy(load_registry())
    phone_1_4.protocol_version = "1.4"  # a 1.4 phone (stale PWA bundle, the 1.4 Python phone)
    assert _verify(raw, pc_jwk, registry=phone_1_4).payload["protocol_version"] == "1.4"
    assert _verify(raw, pc_jwk).payload["protocol_version"] == "1.4"  # and a 1.5 phone accepts it under the compatibility rule
    with pytest.raises(ProtocolError) as ei:  # the 1.4 phone refuses a 1.5 answer: why the stamp follows the start
        _verify(_answer(pc_key), pc_jwk, registry=phone_1_4)
    assert ei.value.code == "PROTOCOL_INCOMPATIBLE" and ei.value.detail == {"peer": "1.5", "supported": ["1.4"]}
    for bad in ("1.6", "2.0", "x", ""):
        with pytest.raises(ProtocolError) as ei:
            build_screen_answer_payload(**args, protocol_version=bad)
        assert ei.value.code == "PROTOCOL_INCOMPATIBLE" and ei.value.detail == {"peer": bad, "supported": ["1.5"]}, bad
    with pytest.raises(TypeError):
        build_screen_answer_payload(**args)  # type: ignore[call-arg]


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
        _verify(_answer(pc_key, protocol_version="1.6"), pc_jwk)
    assert ei.value.code == "PROTOCOL_INCOMPATIBLE" and ei.value.detail == {"peer": "1.6", "supported": ["1.5"]}
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
    for stamp in ("1.1", "1.2", "1.3", "1.4", "1.5"):
        assert verify_and_parse_input_batch(sign_payload(key, dumps_compact({**plain, "protocol_version": stamp})).to_dict(), resolve).required_protocol_version == "1.1"  # type: ignore[arg-type]


def test_check_order_version_before_schema() -> None:
    """A payload from a newer MINOR with events or fields we do not know is PROTOCOL_INCOMPATIBLE, never MALFORMED_MESSAGE;
    an unparseable stamp still falls through to the schema."""
    key, kid, record = _controller()
    resolve = lambda k: record if k == kid else None
    future_event = {"type": "pointer_tilt", "angle": 3}  # does not exist in 1.5
    batch = build_input_batch_payload(account_id=ACCOUNT, controller_id=CONTROLLER, target_pc_id=PC, input_session_id=SID, seq=1, events=[future_event])
    with pytest.raises(ProtocolError) as ei:
        verify_and_parse_input_batch(sign_payload(key, dumps_compact({**batch, "protocol_version": "1.6"})).to_dict(), resolve)  # type: ignore[arg-type]
    assert ei.value.code == "PROTOCOL_INCOMPATIBLE" and ei.value.detail == {"peer": "1.6", "supported": ["1.5"]}
    with pytest.raises(ProtocolError) as ei:  # the same unknown event stamped at our version: that IS malformed
        verify_and_parse_input_batch(sign_payload(key, dumps_compact({**batch, "protocol_version": "1.5"})).to_dict(), resolve)  # type: ignore[arg-type]
    assert ei.value.code == "MALFORMED_MESSAGE"
    with pytest.raises(ProtocolError) as ei:  # a stamp below the 1.1 floor is refused before the schema too
        verify_and_parse_input_batch(sign_payload(key, dumps_compact({**batch, "protocol_version": "1.0"})).to_dict(), resolve)  # type: ignore[arg-type]
    assert ei.value.code == "PROTOCOL_INCOMPATIBLE" and ei.value.detail == {"peer": "1.0", "required": "1.1"}
    for junk in ("1", "v1.4", "1.4\n", 14, None):
        with pytest.raises(ProtocolError) as ei:
            verify_and_parse_input_batch(sign_payload(key, dumps_compact({**batch, "protocol_version": junk})).to_dict(), resolve)  # type: ignore[arg-type]
        assert ei.value.code == "MALFORMED_MESSAGE", junk
    # commands: a 1.6 command with a field this library does not know
    now = now_utc()
    command = {
        "type": "command",
        "protocol_version": "1.6",
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
    assert ei.value.code == "PROTOCOL_INCOMPATIBLE" and ei.value.detail == {"peer": "1.6", "supported": ["1.5"]}
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
