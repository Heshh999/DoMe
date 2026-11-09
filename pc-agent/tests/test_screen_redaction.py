"""rules.screen_content on the PC (design §4.4): IPv4 / IPv6 addresses with an optional port are masked in free text
(redact_text_line), in structured values (redact_value) and in crash.log (crashguard.redact), loopback included; the
Screen view keys are sensitive (since 1.5 the cursor bitmap's ``png`` too, never the cursor counters); the new worker
log lines carry no position, handle or bitmap; the aiortc / aioice loggers are pinned to WARNING whatever LOG_LEVEL
says."""

from __future__ import annotations

import base64
import contextlib
import logging
from collections.abc import Iterator, MutableMapping
from typing import Any

import pytest
import structlog

from dome_agent import crashguard
from dome_agent.logsetup import (
    MEDIA_LOGGERS,
    configure_logging,
    is_sensitive_key,
    mask_addresses,
    pin_media_loggers,
    redact_event,
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


@contextlib.contextmanager
def debug_lines() -> Iterator[list[MutableMapping[str, Any]]]:
    """structlog's events at DEBUG too (the shape lines are debug lines), the configuration restored afterwards:
    nothing here touches the stdlib handlers other tests read."""
    previous = structlog.get_config()
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG))
    try:
        with structlog.testing.capture_logs() as logs:
            yield logs
    finally:
        structlog.configure(**previous)


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


def test_a_cursor_bitmap_is_redacted_and_the_cursor_counters_are_not() -> None:
    """rules.screen_cursor (4), design 2a §3.10: ``png`` is a sensitive key part (substring rule), so the bitmap of a
    ``cursor_shape`` never reaches a log line or the bundle; every counter and log key of the cursor path says
    ``bitmap`` instead and survives."""
    from dome_agent.screen.worker.main import CURSOR_COUNTERS

    bitmap = base64.b64encode(b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 4).decode()
    for key in ("png", "cursor_png", "PNG", "png_chars"):
        assert is_sensitive_key(key), key
    shape = {"type": "cursor_shape", "kind": "custom", "width": 24, "height": 24, "png": bitmap}
    assert redact_value({"message": shape}) == {"message": {**shape, "png": "[redacted]"}}
    event = redact_event(None, "info", {"event": "x", "png": bitmap, "kind": "custom"})
    assert event == {"event": "x", "png": "[redacted]", "kind": "custom"}
    counters = [
        *CURSOR_COUNTERS,
        "cursor_pos_unreliable",
        "cursor_bitmap_too_large",
        "cursor_poll_errors",
        "cursor_last_kind",
        "dropped_1_4",
        "encoder_hw",
        "bitmap_chars",
        "kind",
        "w",
        "h",
        "gated",
        "parameter_sets",
        "frames_in",
        "mft",
    ]
    for key in counters:
        assert "png" not in key and not is_sensitive_key(key), key
    stats = dict.fromkeys(counters, 7)
    assert redact_value(stats) == stats


def test_the_new_worker_log_lines_carry_no_position_handle_or_bitmap() -> None:
    """``cursor reporting stopped``, ``screen cursor shape`` and ``screen encoder probed`` as the worker really logs
    them: no coordinate pair, no cursor handle, no base64 run."""
    from dome_agent.screen.worker import cursor, fake

    class Failing(fake.FakeCursorReader):
        failing = False

        def read(self) -> tuple[int, int, int, int] | None:
            if self.failing:
                raise OSError("GetCursorInfo failed for 0x10001 at 1234,567")
            return super().read()

    shapes = {"c": {"custom": {"w": 24, "h": 24, "hx": 3, "hy": 2}}}
    reader = Failing({"samples": [[1, "c", 1234, 567]], "shapes": shapes})
    sent: list[dict[str, object]] = []
    poller = cursor.CursorPoller(reader, on_pos=sent.append, on_shape=sent.append)
    poller.set_surface((0, 0, 1920, 1080), 1)
    logs = _worker_lines(poller, reader)
    png = next(str(m["png"]) for m in sent if m.get("kind") == "custom")
    position = next(m for m in sent if m.get("type") == "cursor_pos")
    handle = str(0x10000)
    lines = [
        e for e in logs if e["event"] in ("cursor reporting stopped", "screen cursor shape", "screen encoder probed")
    ]
    assert sorted(e["event"] for e in lines) == [
        "cursor reporting stopped",
        "screen cursor shape",
        "screen encoder probed",
    ]
    for entry in lines:
        text = " ".join(f"{k}={v}" for k, v in entry.items())
        for needle in (png[:24], "1234", "567", handle, hex(0x10000), str(position["x"]), str(position["y"])):
            assert needle not in text, (needle, text)
        assert not any("png" in key for key in entry), entry


def _worker_lines(poller: object, reader: object) -> list[dict[str, object]]:
    """One custom shape, ten failed reads and one probe row, logged as the worker logs them."""
    from dome_agent.screen.worker import cursor, encode

    assert isinstance(poller, cursor.CursorPoller)
    with debug_lines() as logs:  # "screen cursor shape" is a debug line
        poller.tick(0.0)
        reader.failing = True  # type: ignore[attr-defined]
        for i in range(cursor.CURSOR_POLL_ERRORS_MAX):
            poller.tick(1.0 + i)
        failures: list[encode.ProbeFailure] = []
        results: list[encode.ProbeResult] = []
        encode.probe_encoders([encode.EncoderChoice("vp8", "")], 64, 36, failures, results)
        structlog.get_logger("dome_agent.screen.worker.main").info("screen encoder probed", **results[0].as_dict())
    return [dict(e) for e in logs]


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
