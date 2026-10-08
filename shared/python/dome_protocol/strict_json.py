"""Strict JSON: the only parser allowed for anything that crossed a trust boundary.

Rejects: oversized text, nesting deeper than ``max_depth``, duplicate object keys, NaN/Infinity,
lone surrogates in strings, and non-object top-level values when ``require_object`` is set.
"""

from __future__ import annotations

import json
from typing import Any

from .errors import ProtocolError

DEFAULT_MAX_BYTES = 16384
DEFAULT_MAX_DEPTH = 8


class _DuplicateKey(Exception):
    pass


def _pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise _DuplicateKey(key)
        out[key] = value
    return out


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-finite number {name!r} is not allowed")


def _check(value: Any, depth: int, max_depth: int) -> None:
    if depth > max_depth:
        raise ProtocolError("MALFORMED_MESSAGE", "JSON nesting too deep")
    if isinstance(value, dict):
        for k, v in value.items():
            _check_str(k)
            _check(v, depth + 1, max_depth)
    elif isinstance(value, list):
        for v in value:
            _check(v, depth + 1, max_depth)
    elif isinstance(value, str):
        _check_str(value)
    elif isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ProtocolError("MALFORMED_MESSAGE", "non-finite number")


def _check_str(s: str) -> None:
    try:
        s.encode("utf-8")
    except UnicodeEncodeError as exc:  # lone surrogate
        raise ProtocolError("MALFORMED_MESSAGE", "invalid string encoding") from exc


def loads_strict(
    text: str | bytes,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_depth: int = DEFAULT_MAX_DEPTH,
    require_object: bool = True,
) -> Any:
    if isinstance(text, (bytes, bytearray)):
        raw = bytes(text)
        if len(raw) > max_bytes:
            raise ProtocolError("PAYLOAD_TOO_LARGE", f"JSON exceeds {max_bytes} bytes")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProtocolError("MALFORMED_MESSAGE", "JSON is not valid UTF-8") from exc
    else:
        if len(text.encode("utf-8", errors="surrogatepass")) > max_bytes:
            raise ProtocolError("PAYLOAD_TOO_LARGE", f"JSON exceeds {max_bytes} bytes")
    try:
        value = json.loads(text, object_pairs_hook=_pairs_hook, parse_constant=_reject_constant)
    except _DuplicateKey as exc:
        raise ProtocolError("MALFORMED_MESSAGE", f"duplicate JSON key {exc.args[0]!r}") from None
    except RecursionError:
        raise ProtocolError("MALFORMED_MESSAGE", "JSON nesting too deep") from None
    except (ValueError, TypeError) as exc:
        raise ProtocolError("MALFORMED_MESSAGE", f"invalid JSON: {exc}") from None
    if require_object and not isinstance(value, dict):
        raise ProtocolError("MALFORMED_MESSAGE", "JSON top level must be an object")
    _check(value, 0, max_depth)
    return value


def dumps_compact(value: Any) -> str:
    """Compact, deterministic-enough serialisation for payloads we sign ourselves.

    Key order is preserved (insertion order) and never re-sorted; the bytes we emit are the
    bytes we sign, so no canonicalisation is required by the verifier.
    """
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
