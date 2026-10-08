"""Chrome Native Messaging framing: 4-byte little-endian length + UTF-8 JSON.

Chrome allows 1 MiB; DoMe limits frames to 64 KiB (``version.json → limits.max_frame_bytes``) in
both directions. Decoding always goes through the strict parser and the bridge schema for the
stated direction; nothing is forwarded or acted on before that.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from typing import Any

from dome_protocol import ProtocolError, dumps_compact, load_schemas, loads_strict

MAX_FRAME_BYTES = 65536
_HEADER = struct.Struct("<I")

Direction = str  # "extension_to_agent" | "agent_to_extension"


def encode_frame(frame: dict[str, Any]) -> bytes:
    body = dumps_compact(frame).encode("utf-8")
    if len(body) > MAX_FRAME_BYTES:
        raise ProtocolError("PAYLOAD_TOO_LARGE", f"bridge frame exceeds {MAX_FRAME_BYTES} bytes")
    return _HEADER.pack(len(body)) + body


def read_frame(read_exact: Callable[[int], bytes]) -> bytes | None:
    """Read one frame body using ``read_exact(n)`` (which returns b'' at EOF). None at clean EOF."""
    header = read_exact(_HEADER.size)
    if not header:
        return None
    if len(header) != _HEADER.size:
        raise ProtocolError("MALFORMED_MESSAGE", "truncated frame header")
    (length,) = _HEADER.unpack(header)
    if length > MAX_FRAME_BYTES:
        raise ProtocolError("PAYLOAD_TOO_LARGE", f"bridge frame of {length} bytes exceeds {MAX_FRAME_BYTES}")
    if length == 0:
        raise ProtocolError("MALFORMED_MESSAGE", "empty frame")
    body = read_exact(length)
    if len(body) != length:
        raise ProtocolError("MALFORMED_MESSAGE", "truncated frame body")
    return body


def decode_frame(raw: bytes, direction: Direction) -> dict[str, Any]:
    frame = loads_strict(raw, max_bytes=MAX_FRAME_BYTES, require_object=True)
    load_schemas().validate_bridge_frame(direction, frame)
    assert isinstance(frame, dict)
    return frame


def validate_outgoing(frame: dict[str, Any], direction: Direction) -> None:
    load_schemas().validate_bridge_frame(direction, frame)


def make_exact_reader(read: Callable[[int], bytes]) -> Callable[[int], bytes]:
    """Turn a ``read(n)`` that may return short reads into ``read_exact(n)`` (b'' only at EOF before any byte)."""

    def read_exact(n: int) -> bytes:
        chunks: list[bytes] = []
        remaining = n
        while remaining > 0:
            chunk = read(remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    return read_exact
