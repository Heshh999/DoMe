"""Run the shared accept/reject cases that both language implementations must agree on."""

import base64
import importlib.util
import json
import struct
import zlib
from pathlib import Path

import pytest

from dome_protocol import (
    ProtocolError,
    loads_strict,
    negotiate_version,
    parse_rfc3339,
    parse_screen_dc_message,
    screen_sdp_acceptable,
)
from dome_protocol.strict_regex import pattern_matches

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "protocol" / "fixtures"
CASES = json.loads((FIXTURE_DIR / "strict-json-cases.json").read_text(encoding="utf-8"))
DC_CASES_TEXT = (FIXTURE_DIR / "screen-dc-cases.json").read_text(encoding="utf-8")
DC_CASES = json.loads(DC_CASES_TEXT)


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


# ----- protocol 1.5: the data-channel cases (screen-dc-cases.json) ---------------------------------------------------


@pytest.mark.parametrize("case", DC_CASES["accepted"], ids=[c["name"] for c in DC_CASES["accepted"]])
def test_screen_dc_case_accepted(case):
    """rules.screen_content / rules.screen_cursor: both languages accept the same data-channel messages."""
    assert parse_screen_dc_message(case["text"], case["direction"]) == json.loads(case["text"])


@pytest.mark.parametrize("case", DC_CASES["rejected"], ids=[c["name"] for c in DC_CASES["rejected"]])
def test_screen_dc_case_rejected(case):
    """... and refuse the same ones, always as MALFORMED_MESSAGE (the receiver drops the message)."""
    with pytest.raises(ProtocolError) as ei:
        parse_screen_dc_message(case["text"], case["direction"])
    assert ei.value.code == "MALFORMED_MESSAGE"


def _png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    out, pos = [], 8
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind, body = data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + length]
        (crc,) = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])
        assert crc == zlib.crc32(kind + body) & 0xFFFFFFFF, kind
        out.append((kind, body))
        pos += 12 + length
    return out


def test_screen_dc_cases_carry_real_rgba_pngs():
    """The custom cursor_shape cases carry real PNGs of exactly width x height, 8-bit RGBA, as the schema describes."""
    customs = [json.loads(c["text"]) for c in DC_CASES["accepted"] if '"kind":"custom"' in c["text"]]
    assert len(customs) == 2
    for msg in customs:
        chunks = _png_chunks(base64.b64decode(msg["png"], validate=True))
        assert [k for k, _ in chunks][0] == b"IHDR" and chunks[-1] == (b"IEND", b"")
        assert struct.unpack(">IIBB", chunks[0][1][:10]) == (msg["width"], msg["height"], 8, 6)
        assert zlib.decompress(b"".join(body for kind, body in chunks if kind == b"IDAT")) == b"\x00\x00\x00\x00\xff"
    assert len(customs[1]["png"]) == 8192 and any(k == b"tEXt" for k, _ in _png_chunks(base64.b64decode(customs[1]["png"])))


def test_screen_dc_cases_file_is_what_the_generator_writes():
    """shared/python/scripts/make_fixtures.py (and, byte for byte, shared/ts/scripts/make-fixtures.ts) wrote this file."""
    spec = importlib.util.spec_from_file_location("make_fixtures", Path(__file__).resolve().parents[1] / "scripts" / "make_fixtures.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert json.dumps(module.screen_dc_cases(), indent=2, ensure_ascii=False) + "\n" == DC_CASES_TEXT
