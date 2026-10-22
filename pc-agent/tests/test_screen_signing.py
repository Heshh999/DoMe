"""The PC-signed screen answer (dome_agent/screen/signing.py) against the shared fixture vectors: an answer built by this
agent verifies with the phone-side verifier under the PC's own pinned key, matches the fixture's byte-exact payload when
signed by the fixture's test PC key at the fixture's time, and every tampered fixture case is still refused."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from dome_protocol import (
    ProtocolError,
    kid_from_jwk,
    loads_strict,
    parse_rfc3339,
    verify_screen_answer_payload,
)

from dome_agent.identity import Identity
from dome_agent.screen.signing import sign_screen_answer
from dome_agent.settings import Settings

ROOT = Path(__file__).resolve().parents[2] / "shared" / "protocol" / "fixtures"
FIXTURE = json.loads((ROOT / "es256-python.json").read_text(encoding="utf-8"))
SCREEN = FIXTURE["screen"]


def _identity_with_test_key(settings: Settings) -> Identity:
    """The committed test-only PC key (test-pc-key.pem), written where Identity keeps pc_key.bin (0600 off Windows)."""
    from dome_agent import secrets as secret_files

    identity = Identity(settings.state_dir)
    secret_files.write_secret_file(
        settings.state_dir / "secrets" / "pc_key.bin", (ROOT / "test-pc-key.pem").read_bytes()
    )
    assert identity.kid == SCREEN["pc_kid"]
    return identity


def test_agent_answer_matches_the_fixture_vector_byte_for_byte(settings: Settings) -> None:
    identity = _identity_with_test_key(settings)
    envelope = sign_screen_answer(
        identity,
        account_id=SCREEN["account_id"],
        pc_id=SCREEN["pc_id"],
        controller_id=SCREEN["controller_id"],
        command_id=SCREEN["command_id"],
        screen_session_id=SCREEN["screen_session_id"],
        offer_sdp=SCREEN["offer_sdp"],
        answer_sdp=SCREEN["answer_sdp"],
        now=parse_rfc3339("2026-10-21T12:00:00.000Z"),
    )
    valid = next(c for c in SCREEN["cases"] if c["expect"] == "valid")
    assert envelope["kid"] == valid["envelope"]["kid"] == SCREEN["pc_kid"]
    assert (
        envelope["payload"] == valid["envelope"]["payload"]
    )  # the same canonical bytes (ES256 signatures differ by design)
    verified = verify_screen_answer_payload(
        envelope,
        pinned_jwk=SCREEN["pc_public_jwk"],
        account_id=SCREEN["account_id"],
        controller_id=SCREEN["controller_id"],
        pc_id=SCREEN["pc_id"],
        command_id=SCREEN["command_id"],
        offer_sdp=SCREEN["offer_sdp"],
        now=parse_rfc3339(SCREEN["now"]),
    )
    assert verified.fingerprint == SCREEN["fingerprint"] and verified.pc_kid == SCREEN["pc_kid"]


def test_a_fresh_pc_key_verifies_only_when_pinned(settings: Settings) -> None:
    identity = Identity(settings.state_dir)
    identity.ensure_key()
    envelope = sign_screen_answer(
        identity,
        account_id=SCREEN["account_id"],
        pc_id=SCREEN["pc_id"],
        controller_id=SCREEN["controller_id"],
        command_id=SCREEN["command_id"],
        screen_session_id=SCREEN["screen_session_id"],
        offer_sdp=SCREEN["offer_sdp"],
        answer_sdp=SCREEN["answer_sdp"],
    )
    payload = loads_strict(envelope["payload"])
    assert payload["protocol_version"] == "1.4" and payload["type"] == "screen_answer"
    assert envelope["kid"] == kid_from_jwk(identity.public_jwk)
    kwargs = dict(
        account_id=SCREEN["account_id"],
        controller_id=SCREEN["controller_id"],
        pc_id=SCREEN["pc_id"],
        command_id=SCREEN["command_id"],
        offer_sdp=SCREEN["offer_sdp"],
    )
    assert (
        verify_screen_answer_payload(envelope, pinned_jwk=identity.public_jwk, **kwargs).answer_sdp
        == SCREEN["answer_sdp"]
    )
    # the phone pinned another PC's key (the fixture's): the hard stop, no "continue anyway"
    with pytest.raises(ProtocolError) as exc:
        verify_screen_answer_payload(envelope, pinned_jwk=SCREEN["pc_public_jwk"], **kwargs)
    assert exc.value.code == "SCREEN_ANSWER_INVALID" and exc.value.detail["reason"] == "kid is not the pinned PC key"


def test_signing_refuses_a_malformed_answer_sdp(settings: Settings) -> None:
    identity = Identity(settings.state_dir)
    with pytest.raises(ProtocolError) as exc:
        sign_screen_answer(
            identity,
            account_id=SCREEN["account_id"],
            pc_id=SCREEN["pc_id"],
            controller_id=SCREEN["controller_id"],
            command_id=SCREEN["command_id"],
            screen_session_id=SCREEN["screen_session_id"],
            offer_sdp=SCREEN["offer_sdp"],
            answer_sdp="v=0\n",  # bare LF
        )
    assert exc.value.code == "MALFORMED_MESSAGE"


@pytest.mark.parametrize("case", [c for c in SCREEN["cases"] if c["expect"] != "valid"], ids=lambda c: c["name"])
def test_tampered_fixture_cases_are_refused(case: dict[str, object]) -> None:
    with pytest.raises(ProtocolError) as exc:
        verify_screen_answer_payload(
            case["envelope"],
            pinned_jwk=SCREEN["pc_public_jwk"],
            account_id=SCREEN["account_id"],
            controller_id=SCREEN["controller_id"],
            pc_id=SCREEN["pc_id"],
            command_id=SCREEN["command_id"],
            offer_sdp=SCREEN["offer_sdp"],
            now=parse_rfc3339(SCREEN["now"]),
        )
    assert exc.value.code == case["expect"]
