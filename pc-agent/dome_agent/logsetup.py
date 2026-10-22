"""Structured, redacted logging.

Rules (spec §15): never log raw credentials, tokens, pairing codes, confirmation challenge text,
signed payloads, full URLs with secrets, or media/window titles. The redaction processor removes
or masks such keys from every event *before* it reaches any sink, so a mistaken ``log.info(..., token=t)``
somewhere cannot leak it. Values that look like bearer tokens or signed envelopes are masked as well.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
from collections.abc import MutableMapping
from pathlib import Path
from typing import Any

import structlog

# Keys whose values are never written. Matching is case-insensitive on the key name and also
# matches suffixes such as ``pc_credential`` or ``access_token``.
SENSITIVE_KEY_PARTS: tuple[str, ...] = (
    "token",
    "credential",
    "secret",
    "password",
    "pairing_code",
    "device_code",
    "user_code",
    "code_hash",
    "verification_code",
    "challenge_text",
    "challenge",
    "payload",
    "envelope",
    "sig",
    "private",
    "pem",
    "jwk",
    "title",
    "artist",
    "authorization",
    "cookie",
    "assertion",
    "query",  # youtube.search text is customer-typed content (rules.search_queries)
    "search",
    # Screen view (rules.screen_content): SDP, ICE candidates and TURN credentials never reach a log line.
    "offer_sdp",
    "answer_sdp",
    "sdp",
    "candidate",
    "ice_servers",
    "username",
)
_ALLOWED_EXACT_KEYS = frozenset({"challenge_id", "code", "error_code", "close_code", "status_code"})
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}")
_LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_-]{40,}\b")
# IPv4 / IPv6 addresses with an optional :port, loopback included (rules.screen_content, design §4.4): aioice logs
# candidate pairs with both peers' ip:port at INFO, and even at WARNING a library line may carry an address. The
# same two patterns are copied into crashguard.py (it imports nothing of DoMe). An IPv6 match needs either all
# eight groups or a "::" so that clock times such as 12:00:01 are left alone. An IPv4 address may end a sentence
# ("could not reach 192.168.1.5.") or follow an underscore ("ip_10.0.0.1"); a fifth dotted part ("1.2.3.4.5", a
# version string) or a letter or digit glued to either end is not an address.
_IPV4_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_IPV4_RE = re.compile(rf"(?<![A-Za-z0-9.])(?:{_IPV4_OCTET}\.){{3}}{_IPV4_OCTET}(?::\d{{1,5}})?(?!\w|\.\d)")
_HEX4 = r"[0-9A-Fa-f]{1,4}"
_IPV6_BODY = (
    rf"(?:(?:{_HEX4}:){{7}}{_HEX4}"
    rf"|(?:{_HEX4}:){{1,6}}:(?:{_HEX4}(?::{_HEX4}){{0,5}})?"
    rf"|::(?:{_HEX4}(?::{_HEX4}){{0,6}})?)"
)
_IPV6_RE = re.compile(
    rf"(?<![\w:.])(?:\[{_IPV6_BODY}(?:%[\w.-]+)?\](?::\d{{1,5}})?|{_IPV6_BODY}(?:%[\w.-]+)?)(?![\w:])"
)
# SDP attributes in free text (rules.screen_content): the strict check names only a reason, the structured path never
# logs SDP, and the worker's stderr is redacted here; should a library line still quote a fingerprint, ICE credential,
# candidate or connection address, the attribute is masked from its name to the end of the line. Copied into
# crashguard.py too.
_SDP_RE = re.compile(
    r"(?<![\w-])(?:a=(?:fingerprint|ice-pwd|ice-ufrag|candidate|setup|rtpmap|fmtp|mid|msid|ssrc|group)|c=IN|"
    r"m=(?:video|audio|application)|o=-)[^\r\n]*"
)
REDACTED = "[redacted]"
# Loggers of the media libraries, pinned to WARNING whatever LOG_LEVEL says (design §4.4): at INFO aioice prints
# every candidate pair with both peers' ip:port, and DEBUG prints SDP.
MEDIA_LOGGERS: tuple[str, ...] = (
    "aioice",
    "aioice.ice",
    "aioice.turn",
    "aioice.stun",
    "aiortc",
    "aiortc.rtcdtlstransport",
    "aiortc.rtcicetransport",
    "aiortc.rtcpeerconnection",
    "aiortc.rtcrtpsender",
    "aiortc.rtcrtpreceiver",
    "aiortc.rtcsctptransport",
)


def mask_addresses(text: str) -> str:
    """Mask every IPv4 / IPv6 address (with an optional port) and every SDP attribute line in free text."""
    return _SDP_RE.sub(REDACTED, _IPV6_RE.sub(REDACTED, _IPV4_RE.sub(REDACTED, text)))


def pin_media_loggers(level: int = logging.WARNING) -> None:
    """Pin the aiortc / aioice loggers to at least WARNING (rules.screen_content). Called by configure_logging and
    again by any process that imports the media libraries (the screen worker), so a library's INFO lines with
    addresses never reach a sink whatever the configured level."""
    for name in MEDIA_LOGGERS:
        logging.getLogger(name).setLevel(max(level, logging.WARNING))


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in _ALLOWED_EXACT_KEYS:
        return False
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        value = _BEARER_RE.sub("Bearer " + REDACTED, value)
        value = _LONG_TOKEN_RE.sub(REDACTED, value)
        return mask_addresses(value)
    if isinstance(value, dict):
        return {k: (REDACTED if is_sensitive_key(str(k)) else redact_value(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact_value(v) for v in value]
    return value


def redact_event(_logger: Any, _method: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key in list(event_dict.keys()):
        if key == "event":
            event_dict[key] = redact_value(event_dict[key])
        elif is_sensitive_key(key):
            event_dict[key] = REDACTED
        else:
            event_dict[key] = redact_value(event_dict[key])
    return event_dict


def redact_text_line(line: str) -> str:
    """Best-effort redaction for free text (the diagnostics bundle on raw log lines, the screen worker's stderr):
    bearer tokens, long token-like strings and IPv4/IPv6 addresses with an optional port."""
    line = _BEARER_RE.sub("Bearer " + REDACTED, line)
    return mask_addresses(_LONG_TOKEN_RE.sub(REDACTED, line))


def configure_logging(level: str, log_path: Path | None, *, max_bytes: int = 2_000_000, backups: int = 3) -> None:
    """Route structlog through stdlib logging to stderr and a rotating JSON-lines file."""
    numeric = logging.getLevelName(level.upper())
    if not isinstance(numeric, int):
        numeric = logging.INFO
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_event,
    ]
    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(numeric),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(numeric)

    console = logging.StreamHandler()
    console.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared_processors,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(colors=False),
            ],
        )
    )
    root.addHandler(console)

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
        )
        file_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(
                foreign_pre_chain=shared_processors,
                processors=[
                    structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                    structlog.processors.JSONRenderer(sort_keys=True),
                ],
            )
        )
        root.addHandler(file_handler)
    # third-party chatter
    logging.getLogger("websockets").setLevel(max(numeric, logging.WARNING))
    logging.getLogger("httpx").setLevel(max(numeric, logging.WARNING))
    pin_media_loggers(numeric)


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
