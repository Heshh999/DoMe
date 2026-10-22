"""Verify the committed cross-language fixtures (Python-generated and TypeScript-generated)
with the Python implementation."""

import json
from pathlib import Path

import pytest

from dome_protocol import (
    ProtocolError,
    parse_rfc3339,
    verify_envelope,
    verify_screen_answer_payload,
)
from dome_protocol.digest import (
    challenge_digest,
    command_digest,
    pairing_code_handle,
    pairing_verification_code,
    pc_key_mac,
)
from dome_protocol.screen import offer_digest, sdp_fingerprints

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "protocol" / "fixtures"
FILES = sorted(FIXTURE_DIR.glob("es256-*.json"))


@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_fixture_file(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    jwk, kid = data["public_jwk"], data["kid"]
    resolve = lambda k: jwk if k == kid else None
    assert data["cases"], "fixture has no cases"
    for case in data["cases"]:
        if case["expect"] == "valid":
            _, parsed = verify_envelope(case["envelope"], resolve)
            assert json.dumps(parsed, separators=(",", ":"), ensure_ascii=False) == case["payload"]
            assert command_digest(case["payload"]) == case["command_digest"]
        else:
            with pytest.raises(ProtocolError) as ei:
                verify_envelope(case["envelope"], resolve)
            if case["expect"] != "reject":
                assert ei.value.code == case["expect"]
    d = data["digests"]
    assert challenge_digest(d["challenge_text"]) == d["challenge_digest"]
    assert pairing_code_handle(d["pairing_code"]) == d["pairing_code_handle"]
    assert pairing_verification_code(d["pairing_code"], d["pairing_id"], d["pc_id"], kid) == d["pairing_verification_code"]
    # protocol 1.4: the PC-signed screen answer verified against the pinned PC key, and the pairing pc_key_mac
    sc = data["screen"]
    assert sc["cases"], "fixture has no screen cases"
    expectation = {
        "pinned_jwk": sc["pc_public_jwk"],
        "account_id": sc["account_id"],
        "controller_id": sc["controller_id"],
        "pc_id": sc["pc_id"],
        "command_id": sc["command_id"],
        "offer_sdp": sc["offer_sdp"],
        "now": parse_rfc3339(sc["now"]),
    }
    for case in sc["cases"]:
        if case["expect"] == "valid":
            verified = verify_screen_answer_payload(case["envelope"], **expectation)
            assert verified.pc_kid == sc["pc_kid"] and verified.answer_sdp == sc["answer_sdp"]
            assert verified.fingerprint == sc["fingerprint"] and verified.payload["screen_session_id"] == sc["screen_session_id"]
        else:
            with pytest.raises(ProtocolError) as ei:
                verify_screen_answer_payload(case["envelope"], **expectation)
            assert ei.value.code == case["expect"], case["name"]
            if "cause" in case:
                assert ei.value.detail["cause"] == case["cause"], case["name"]
    assert offer_digest(sc["offer_sdp"]) == sc["offer_digest"]
    assert sdp_fingerprints(sc["answer_sdp"]) and set(sdp_fingerprints(sc["answer_sdp"])) == {sc["fingerprint"]}
    assert pc_key_mac(d["pairing_code"], d["pairing_id"], d["pc_id"], kid, sc["pc_kid"]) == sc["pc_key_mac"]
