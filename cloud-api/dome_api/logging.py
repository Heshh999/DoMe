"""structlog JSON logging with redaction.

Keys that could carry secrets, pairing material, challenge text or media titles are replaced by
``"[redacted]"`` wherever they appear in an event (nested dicts and lists included). URLs are
logged as paths only by the request-logging middleware; this module never receives a query string.
"""

from __future__ import annotations

import functools
import json
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
        # typed keyboard content (rules.input_sessions (5): no component logs typed text)
        "text",
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

# Pairing codes (dome_protocol.digest): 20 Crockford symbols, typed or displayed with or without separators
# (the normaliser accepts " -_." and maps I/L -> 1, O -> 0). Candidates are 20 contiguous symbols, or a run of
# groups of 5 (the PC's display form, 4 groups) or of 4 (5 groups) joined by one consistent separator; within a
# run every window of the right number of groups is checked, so "code K7Q2 M9XD 4HPR 8WTV ZC3N" masks the code
# and keeps "code".
_PAIRING_GROUP_RUNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"(?<![0-9A-Za-z_-])[0-9A-Za-z]{5}([-_. ])[0-9A-Za-z]{5}(?:\1[0-9A-Za-z]{5})+(?![0-9A-Za-z_-])"), 4),
    (re.compile(r"(?<![0-9A-Za-z_-])[0-9A-Za-z]{4}([-_. ])[0-9A-Za-z]{4}(?:\1[0-9A-Za-z]{4})+(?![0-9A-Za-z_-])"), 5),
)
_PAIRING_CONTIGUOUS = re.compile(r"(?<![0-9A-Za-z_-])[0-9A-Za-z]{20}(?![0-9A-Za-z_-])")
_CROCKFORD = frozenset("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
_CROCKFORD_ALIASES = {"I": "1", "L": "1", "O": "0"}


def _looks_like_pairing_code(groups: list[str], *, separated: bool) -> bool:
    symbols = "".join(groups)
    if len(symbols) != 20:
        return False
    if any(_CROCKFORD_ALIASES.get(ch, ch) not in _CROCKFORD for ch in symbols.upper()):
        return False
    # A random code lacks a digit with probability ~0.06 %; without that anchor, only the displayed form
    # (upper case, grouped) counts, so a run of ordinary lower-case words never matches.
    return any(ch.isdigit() for ch in symbols) or (separated and symbols.upper() == symbols)


def _mask_group_run(match: re.Match[str], window: int) -> str:
    sep = match.group(1)
    groups = match.group(0).split(sep)
    candidates = [
        i for i in range(len(groups) - window + 1) if _looks_like_pairing_code(groups[i : i + window], separated=True)
    ]
    if not candidates:
        return match.group(0)

    def uniform_case(i: int) -> bool:
        letters = "".join(groups[i : i + window])
        return letters.upper() == letters or letters.lower() == letters

    # overlapping windows: a code is shown in one case, so prefer the uniform window ("code K7Q2 ... ZC3N")
    chosen: list[int] = []
    for i in sorted(candidates, key=lambda i: (not uniform_case(i), i)):
        if all(abs(i - j) >= window for j in chosen):
            chosen.append(i)
    out: list[str] = []
    i = 0
    while i < len(groups):
        if i in chosen:
            out.append(REDACTED)
            i += window
        else:
            out.append(groups[i])
            i += 1
    return sep.join(out)


def _mask_contiguous(match: re.Match[str]) -> str:
    return REDACTED if _looks_like_pairing_code([match.group(0)], separated=False) else match.group(0)


def redact_pairing_codes(text: str) -> str:
    out = text
    for pattern, window in _PAIRING_GROUP_RUNS:
        out = pattern.sub(functools.partial(_mask_group_run, window=window), out)
    return _PAIRING_CONTIGUOUS.sub(_mask_contiguous, out)


def redact_text(text: str) -> str:
    """Mask token-shaped substrings and pairing codes in free text (the structural :func:`redact` only knows
    key names). Used for the customer's own support message and as part of :func:`redact_diagnostics`."""
    out = text
    for pattern in _TEXT_PATTERNS:
        out = pattern.sub(REDACTED, out)
    return redact_pairing_codes(out)


# ----- support diagnostics (spec section 11A) -------------------------------------------------------------
# Default diagnostics carry component versions, connection states, error codes and timing only: never typed
# keyboard content, full media URLs/titles or pairing material. On top of the log keys these are masked.
DIAGNOSTIC_REDACT_EXACT = frozenset(
    {
        "text",
        "events",
        "composer",
        "typed",
        "typed_text",
        "keys",
        "url",
        "urls",
        "href",
        "link",
        "query",
        "search",
        "pairing",
        "pairing_code",
        "clipboard",
        "video_id",
    }
)
DIAGNOSTIC_REDACT_SUFFIXES = ("_url", "_urls", "_href", "_text", "_query")
# Any absolute URL (http, https, ws, wss) is reduced to scheme + host; a bare host followed by a path
# (``www.youtube.com/watch?v=...``, ``youtu.be/...``) to the host.
_URL_RE = re.compile(r"(?i)\b((?:https?|wss?)://)([^/\s?#\"'<>]+)[^\s\"'<>]*")
_BARE_HOST_PATH_RE = re.compile(r"(?i)(?<![\w@/.-])((?:[a-z0-9-]+\.)+[a-z]{2,})/[^\s\"'<>]*")


def _reduce_urls(text: str) -> str:
    def host_only(m: re.Match[str]) -> str:
        host = m.group(2).rsplit("@", 1)[-1]  # never keep userinfo
        return f"{m.group(1)}{host}"

    out = _URL_RE.sub(host_only, text)
    return _BARE_HOST_PATH_RE.sub(lambda m: m.group(1), out)


def _diagnostic_key(key: str) -> bool:
    k = key.lower()
    return _should_redact(k) or k in DIAGNOSTIC_REDACT_EXACT or k.endswith(DIAGNOSTIC_REDACT_SUFFIXES)


def _redact_diagnostic_text(text: str) -> str:
    return redact_text(_reduce_urls(text))


def _redact_diagnostic_value(value: Any, *, _depth: int = 0) -> Any:
    if _depth > 12:
        return REDACTED
    if isinstance(value, Mapping):
        return {
            str(k): (REDACTED if _diagnostic_key(str(k)) else _redact_diagnostic_value(v, _depth=_depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [_redact_diagnostic_value(v, _depth=_depth + 1) for v in value]
    if isinstance(value, str):
        nested = _parse_json_container(value)
        if nested is not None:  # a JSON document embedded as a string gets the key rules too
            return json.dumps(
                _redact_diagnostic_value(nested, _depth=_depth + 1), separators=(",", ":"), ensure_ascii=False
            )
        return _redact_diagnostic_text(value)
    return value


def _parse_json_container(text: str) -> Any:
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        parsed = json.loads(stripped)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict | list) else None


def redact_diagnostics(text: str) -> str:
    """Redact a customer-submitted diagnostics bundle before it is stored (the server's last check before a
    durable row; spec section 11A, ``support_ticket_request.diagnostics``).

    JSON text gets the structural key rules (the log keys plus typed text, input events, URLs, search
    queries, pairing material and clipboard), then every remaining string is reduced: absolute URLs to
    scheme + host, token-shaped substrings and pairing codes masked. Anything that is not JSON gets the string
    pass only. The output is text again (compact JSON when the input was JSON)."""
    parsed = _parse_json_container(text)
    if parsed is None:
        return _redact_diagnostic_text(text)
    cleaned = _redact_diagnostic_value(parsed)
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
