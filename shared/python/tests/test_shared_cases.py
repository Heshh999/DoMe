"""Run the shared accept/reject cases that both language implementations must agree on."""

import json
from pathlib import Path

import pytest

from dome_protocol import ProtocolError, loads_strict, parse_rfc3339
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
