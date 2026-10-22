"""Run the shared accept/reject cases that both language implementations must agree on."""

import json
from pathlib import Path

import pytest

from dome_protocol import (
    ProtocolError,
    loads_strict,
    negotiate_version,
    parse_rfc3339,
    screen_sdp_acceptable,
)
from dome_protocol.strict_regex import pattern_matches

CASES = json.loads((Path(__file__).resolve().parents[2] / "protocol" / "fixtures" / "strict-json-cases.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES["parser_cases"], ids=[c["name"] for c in CASES["parser_cases"]])
def test_parser_case(case):
    if case["expect"] == "accept":
        loads_strict(case["text"])
    else:
        with pytest.raises(ProtocolError) as ei:
            loads_strict(case["text"])
        assert ei.value.code == case["expect"]


@pytest.mark.parametrize("case", CASES["pattern_cases"], ids=[f"{c['pattern']}|{c['value']!r}" for c in CASES["pattern_cases"]])
def test_pattern_case(case):
    assert pattern_matches(case["pattern"], case["value"]) is case["expect"]


@pytest.mark.parametrize("case", CASES["timestamp_cases"], ids=[repr(c["value"]) for c in CASES["timestamp_cases"]])
def test_timestamp_case(case):
    if case["expect"] == "accept":
        parse_rfc3339(case["value"])
    else:
        with pytest.raises(ProtocolError) as ei:
            parse_rfc3339(case["value"])
        assert ei.value.code == case["expect"]


@pytest.mark.parametrize("case", CASES["negotiation_cases"], ids=[f"{c['peer']}|{c['supported']}" for c in CASES["negotiation_cases"]])
def test_negotiation_case(case):
    """rules.bridge_negotiation: both languages pick the same version for the same two lists."""
    assert negotiate_version(case["peer"], case["supported"]) == case["expect"]


def _padded(case):
    value = case["value"]
    if "pad_to_bytes" in case:
        # one extra attribute line so the text is exactly pad_to_bytes bytes (the case file stays readable)
        value += "a=" + "x" * (case["pad_to_bytes"] - len(value.encode()) - 4) + "\r\n"
        assert len(value.encode()) == case["pad_to_bytes"]
    return value


@pytest.mark.parametrize("case", CASES["sdp_cases"], ids=[c["name"] for c in CASES["sdp_cases"]])
def test_sdp_case(case):
    """rules.screen_signaling (4): both languages accept and refuse the same SDP shapes (protocol 1.4)."""
    assert screen_sdp_acceptable(_padded(case)) is case["expect"]
