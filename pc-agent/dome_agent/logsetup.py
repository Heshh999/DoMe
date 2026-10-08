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
)
_ALLOWED_EXACT_KEYS = frozenset({"challenge_id", "code", "error_code", "close_code", "status_code"})
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}")
_LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_-]{40,}\b")
REDACTED = "[redacted]"


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in _ALLOWED_EXACT_KEYS:
        return False
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        value = _BEARER_RE.sub("Bearer " + REDACTED, value)
        value = _LONG_TOKEN_RE.sub(REDACTED, value)
        return value
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
    """Best-effort redaction for free text (used by the diagnostics bundle on raw log lines)."""
    line = _BEARER_RE.sub("Bearer " + REDACTED, line)
    return _LONG_TOKEN_RE.sub(REDACTED, line)


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


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
