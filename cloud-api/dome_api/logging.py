"""structlog JSON logging with redaction.

Keys that could carry secrets, pairing material, challenge text or media titles are replaced by
``"[redacted]"`` wherever they appear in an event (nested dicts and lists included). URLs are
logged as paths only by the request-logging middleware; this module never receives a query string.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

REDACTED = "[redacted]"
# Exact key names (case-insensitive) that are always redacted.
REDACT_EXACT = frozenset(
    {
        "token",
        "secret",
        "code",
        "device_code",
        "user_code",
        "pc_credential",
        "cookie",
        "cookies",
        "authorization",
        "payload",
        "sig",
        "title",
        "artist",
        "challenge",
        "challenge_text",
        "challenge_digest",
        "code_hash",
        "code_verifier",
        "nonce",
        "state",
        "id_token",
        "access_token",
        "refresh_token",
        "csrf_token",
        "password",
        "email",
        "envelope",
        "assertion",
        "entitlement_assertion",
        "public_jwk",
        "jwk",
        "set-cookie",
        "x-dome-csrf",
    }
)
REDACT_SUFFIXES = ("_token", "_secret", "_code", "_credential", "_key", "_pem", "_title")


def _should_redact(key: str) -> bool:
    k = key.lower()
    return k in REDACT_EXACT or k.endswith(REDACT_SUFFIXES)


def redact(value: Any, *, _depth: int = 0) -> Any:
    """Return a copy of ``value`` with sensitive keys replaced. Never mutates the input."""
    if _depth > 12:
        return REDACTED
    if isinstance(value, Mapping):
        return {str(k): (REDACTED if _should_redact(str(k)) else redact(v, _depth=_depth + 1)) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [redact(v, _depth=_depth + 1) for v in value]
    return value


def redaction_processor(_logger: Any, _method: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    out = redact(dict(event_dict))
    assert isinstance(out, dict)
    return out


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    lvl = logging.getLevelName(level.upper())
    if not isinstance(lvl, int):
        lvl = logging.INFO
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redaction_processor,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: Any = structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[*shared, structlog.processors.EventRenamer("message"), renderer],
        wrapper_class=structlog.make_filtering_bound_logger(lvl),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )
    # Route stdlib logging (uvicorn, sqlalchemy, alembic) through the same redacting pipeline.
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, structlog.processors.EventRenamer("message"), renderer],
        foreign_pre_chain=shared,
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(lvl)
    # uvicorn's access log would print full URLs (query strings included); our middleware logs paths.
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error", "sqlalchemy.engine", "alembic", "httpx", "httpcore"):
        logging.getLogger(name).handlers[:] = []
        logging.getLogger(name).propagate = True


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
