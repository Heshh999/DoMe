"""input_batch: the signed manual-input stream (rules.input_sessions)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from dome_protocol import (
    KeyRecord,
    ProtocolError,
    build_input_batch_payload,
    dumps_compact,
    generate_private_key,
    input_event_capabilities,
    jwk_from_public_key,
    kid_from_jwk,
    load_schemas,
    now_utc,
    sign_payload,
    verify_and_parse_input_batch,
)

ACCOUNT = "11111111-1111-4111-8111-111111111111"
CONTROLLER = "22222222-2222-4222-8222-222222222222"
PC = "33333333-3333-4333-8333-333333333333"
SESSION = "AAAAAAAAAAAAAAAAAAAAAA"


def _controller() -> tuple[object, str, KeyRecord]:
    key = generate_private_key()
    jwk = jwk_from_public_key(key.public_key())
    return key, kid_from_jwk(jwk), KeyRecord(controller_id=CONTROLLER, account_id=ACCOUNT, jwk=jwk)


def _batch(events: list[dict], **kw) -> dict:
    return build_input_batch_payload(
        account_id=ACCOUNT, controller_id=CONTROLLER, target_pc_id=PC, input_session_id=SESSION, seq=kw.pop("seq", 1), events=events, **kw
    )


def test_round_trip_and_capabilities() -> None:
    key, kid, record = _controller()
    events = [
        {"type": "pointer_move", "dx": 12, "dy": -3},
        {"type": "pointer_button", "button": "left", "action": "click"},
        {"type": "pointer_scroll", "dx": 0, "dy": -2},
        {"type": "text", "text": "héllo 👋"},
        {"type": "key", "key": "enter"},
        {"type": "shortcut", "name": "ctrl_l"},
    ]
    payload = _batch(events, seq=7)
    load_schemas().validate_input_batch_payload(payload)
    env = sign_payload(key, dumps_compact(payload))  # type: ignore[arg-type]
    vb = verify_and_parse_input_batch(env.to_dict(), lambda k: record if k == kid else None)
    assert vb.seq == 7 and vb.input_session_id == SESSION and vb.target_pc_id == PC
    assert vb.required_capabilities == {"pointer", "keyboard"} and 0 <= vb.age_seconds() < 5
    assert input_event_capabilities(events[:3]) == {"pointer"} and input_event_capabilities(events[3:]) == {"keyboard"}
    assert input_event_capabilities([]) == set()  # lease renewal needs nothing beyond the session


def test_schema_bounds() -> None:
    schemas = load_schemas()
    bad = [
        [{"type": "pointer_move", "dx": 5000, "dy": 0}],
        [{"type": "text", "text": ""}],
        [{"type": "text", "text": "x" * 257}],
        [{"type": "key", "key": "f12"}],
        [{"type": "shortcut", "name": "alt_f4"}],
        [{"type": "pointer_button", "button": "left", "action": "click", "extra": 1}],
        [{"type": "pointer_move", "dx": 1, "dy": 1}] * 65,
    ]
    for events in bad:
        with pytest.raises(ProtocolError):
            schemas.validate_input_batch_payload(_batch(events))
    with pytest.raises(ProtocolError):
        schemas.validate_input_batch_payload({**_batch([]), "seq": 0})


def test_binding_window_and_tampering() -> None:
    key, _kid, record = _controller()
    foreign = KeyRecord(controller_id="44444444-4444-4444-8444-444444444444", account_id=ACCOUNT, jwk=record.jwk)
    env = sign_payload(key, dumps_compact(_batch([])))  # type: ignore[arg-type]
    with pytest.raises(ProtocolError) as exc:
        verify_and_parse_input_batch(env.to_dict(), lambda k: foreign)
    assert exc.value.code == "CONTROLLER_MISMATCH"
    stale = _batch([], now=now_utc() - timedelta(seconds=30))
    env = sign_payload(key, dumps_compact(stale))  # type: ignore[arg-type]
    with pytest.raises(ProtocolError) as exc:
        verify_and_parse_input_batch(env.to_dict(), lambda k: record)
    assert exc.value.code == "COMMAND_EXPIRED"
    env = sign_payload(key, dumps_compact(_batch([{"type": "text", "text": "a"}])))  # type: ignore[arg-type]
    raw = env.to_dict()
    raw["payload"] = raw["payload"].replace('"text":"a"', '"text":"b"')
    with pytest.raises(ProtocolError) as exc:
        verify_and_parse_input_batch(raw, lambda k: record)
    assert exc.value.code == "SIGNATURE_INVALID"
