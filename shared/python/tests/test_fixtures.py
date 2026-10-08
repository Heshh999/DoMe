"""Verify the committed cross-language fixtures (Python-generated and TypeScript-generated)
with the Python implementation."""

import json
from pathlib import Path

import pytest

from dome_protocol import ProtocolError, verify_envelope
from dome_protocol.digest import challenge_digest, command_digest, pairing_code_handle, pairing_verification_code

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "protocol" / "fixtures"
FILES = sorted(FIXTURE_DIR.glob("es256-*.json"))


@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_fixture_file(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    jwk, kid = data["public_jwk"], data["kid"]
    resolve = lambda k: jwk if k == kid else None  # noqa: E731
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
