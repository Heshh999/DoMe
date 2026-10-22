"""rules.screen_content on the PC (design §4.4): IPv4 / IPv6 addresses with an optional port are masked in free text
(redact_text_line), in structured values (redact_value) and in crash.log (crashguard.redact), loopback included; the
Screen view keys are sensitive; the aiortc / aioice loggers are pinned to WARNING whatever LOG_LEVEL says."""

from __future__ import annotations

import logging

import pytest

from dome_agent import crashguard
from dome_agent.logsetup import (
    MEDIA_LOGGERS,
    configure_logging,
    is_sensitive_key,
    mask_addresses,
    pin_media_loggers,
    redact_text_line,
    redact_value,
)

ADDRESSES = [
    "127.0.0.1",
    "127.0.0.1:5000",
    "192.168.1.20:60000",
    "10.0.0.1",
    "255.255.255.255:1",
    "::1",
    "[::1]:4000",
    "fe80::1%eth0",
    "2001:db8::1",
    "2001:0db8:85a3:0000:0000:8a2e:0370:7334",
    "[2001:db8::8a2e:370:7334]:443",
]
LEFT_ALONE = [
    "2026-10-21T12:00:01.000Z",
    "protocol 1.4 and 0.1.0 and 1.2.3",
    "std::vector<int> and Foo::bar",
    "12:00:01",
    "00:11:22:33:44:55",  # six groups and no :: is not an IPv6 address
    "kid 8zM4Gv3WguduiZm3",  # a short base64url token (40+ characters are masked by the older token rule)
    "data=mid-level and alpha=1 and gamma=video",  # not SDP attributes
    "version 1.2.3.4.5 and build v1.2.3.4",  # a fifth dotted part, or a letter glued on, is a version string
]
# An address at the end of a sentence or an exception message ("could not reach 192.168.1.5.") is the normal shape
# of an OSError, an asyncio transport error or a library warning: the trailing period must not save it.
SENTENCES = [
    ("could not reach 192.168.1.5.", "192.168.1.5", "could not reach [redacted]."),
    ("peer 10.0.0.25. retrying", "10.0.0.25", "peer [redacted]. retrying"),
    ("connect to 10.0.0.25:5000. retrying", "10.0.0.25", "connect to [redacted]. retrying"),
    ("no route to fe80::1.", "fe80::1", "no route to [redacted]."),
    ("host 2001:db8::1. gone", "2001:db8::1", "host [redacted]. gone"),
    ("ip_192.168.1.5 seen", "192.168.1.5", "ip_[redacted] seen"),
]
SDP_LINES = [
    "a=fingerprint:sha-256 01:02:03:04:05:06:07:08:09:0A:0B:0C:0D:0E:0F:10:11:12:13:14:15:16:17:18:19:1A:1B:1C:1D:1E:1F:20",
    "a=ice-pwd:phonephonephonephonephon",
    "a=ice-ufrag:phone",
    "a=candidate:1 1 udp 2130706431 host.local 54321 typ host",
    "c=IN IP4 203.0.113.9",
    "m=video 9 UDP/TLS/RTP/SAVPF 96 97",
]


@pytest.mark.parametrize("address", ADDRESSES)
def test_addresses_are_masked_everywhere(address: str) -> None:
    line = f"Check (host {address} -> peer) resolved"
    for fn in (redact_text_line, mask_addresses, crashguard.redact):
        out = fn(line)
        assert address not in out and "[redacted]" in out, (fn.__name__, out)
    assert address not in str(redact_value({"event": line, "list": [line]}))


@pytest.mark.parametrize(("line", "address", "expected"), SENTENCES)
def test_an_address_ending_a_sentence_is_masked(line: str, address: str, expected: str) -> None:
    for fn in (redact_text_line, mask_addresses, crashguard.redact):
        assert fn(line) == expected, (fn.__name__, fn(line))
    out = str(redact_value({"event": line, "error": [line]}))
    assert address not in out and expected in out


@pytest.mark.parametrize("sdp_line", SDP_LINES)
def test_sdp_attributes_are_masked_from_their_name_to_the_end_of_the_line(sdp_line: str) -> None:
    """A library line that quotes SDP (a fingerprint, an ICE credential, a candidate, a connection address) loses the
    whole attribute; the value never reaches a log, the crash log or the diagnostics bundle (rules.screen_content)."""
    value = sdp_line.split(" ", 1)[1] if " " in sdp_line else sdp_line.split(":", 1)[1]
    line = f"aioice says: {sdp_line} (end)"
    for fn in (redact_text_line, mask_addresses, crashguard.redact):
        out = fn(line)
        assert value not in out and sdp_line.split(":")[0] not in out and out.startswith("aioice says: [redacted]"), (
            fn.__name__,
            out,
        )
    assert value not in str(redact_value({"event": line}))


@pytest.mark.parametrize("text", LEFT_ALONE)
def test_other_text_is_left_alone(text: str) -> None:
    assert redact_text_line(text) == text == crashguard.redact(text)


def test_screen_keys_are_sensitive() -> None:
    for key in ("offer_sdp", "answer_sdp", "sdp", "candidate", "ice_servers", "credential", "username"):
        assert is_sensitive_key(key), key
    assert redact_value({"offer_sdp": "v=0", "gen": 1}) == {"offer_sdp": "[redacted]", "gen": 1}


def test_media_loggers_are_pinned_to_warning_whatever_the_level(tmp_path) -> None:  # type: ignore[no-untyped-def]
    configure_logging("DEBUG", tmp_path / "agent.log")
    for name in MEDIA_LOGGERS:
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING, name
    logging.getLogger("aioice.ice").setLevel(logging.DEBUG)  # a library resetting its logger
    pin_media_loggers(logging.DEBUG)
    assert logging.getLogger("aioice.ice").level == logging.WARNING
    configure_logging("ERROR", None)
    assert logging.getLogger("aiortc").level == logging.ERROR  # never below the configured level either
    configure_logging("INFO", None)
