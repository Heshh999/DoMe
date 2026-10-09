"""structlog JSON logging with redaction.

Keys that could carry secrets, pairing material, challenge text or media titles are replaced by
``"[redacted]"`` wherever they appear in an event (nested dicts and lists included). URLs are
logged as paths only by the request-logging middleware; this module never receives a query string.
"""

from __future__ import annotations

import logging
import re
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
        return {
            str(k): (REDACTED if _should_redact(str(k)) else redact(v, _depth=_depth + 1)) for k, v in value.items()
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [redact(v, _depth=_depth + 1) for v in value]
    return value


# Token-shaped substrings inside free text (support diagnostics, customer messages): JWT-like triples,
# bearer/basic authorization values, Stripe-style prefixed keys, and long base64url/hex runs such as
# access tokens, PC credentials, pairing code hashes and controller kids. Ordinary prose never matches.
_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9_.=+/-]{16,}"),
    re.compile(r"\b[sr]k_(live|test)_[A-Za-z0-9]{8,}\b"),
    re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{32,}(?![A-Za-z0-9_-])"),
)


def redact_text(text: str) -> str:
    """Mask token-shaped substrings in free text (the structural :func:`redact` only knows key names)."""
    out = text
    for pattern in _TEXT_PATTERNS:
        out = pattern.sub(REDACTED, out)
    return out


def _redact_strings(value: Any, *, _depth: int = 0) -> Any:
    if _depth > 12:
        return REDACTED
    if isinstance(value, Mapping):
        return {str(k): _redact_strings(v, _depth=_depth + 1) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact_strings(v, _depth=_depth + 1) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_diagnostics(text: str) -> str:
    """Redact a customer-submitted diagnostics bundle before it is stored.

    JSON text gets the structural key redaction (``token``, ``code_hash``, ``title``, ...) *and* the
    token-pattern pass over every remaining string; anything that is not JSON gets the pattern pass only.
    The output is text again (compact JSON when the input was JSON)."""
    import json

    try:
        parsed = json.loads(text)
    except ValueError:
        return redact_text(text)
    if not isinstance(parsed, dict | list):
        return redact_text(text)
    cleaned = _redact_strings(redact(parsed))
    return json.dumps(cleaned, separators=(",", ":"), ensure_ascii=False, sort_keys=False)


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
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.EventRenamer("message"),
            renderer,
        ],
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
