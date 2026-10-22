"""The agent ⇄ worker pipe protocol (design §5.1): length-prefixed JSON over the worker's stdin / stdout, an
anonymous pipe pair, never a listening socket.

Framing: a 4-byte big-endian length followed by that many bytes of compact UTF-8 JSON (one object), at most
``MAX_MESSAGE_BYTES``. Messages, agent → worker (``op``):

* ``start``   ``{offer_sdp, stun_urls, source{kind, surface_index, left, top, width, height, rotation, fps, ...},
  reserved[], lease_seconds, tick_timeout_seconds, connect_timeout_seconds, encoder_preference[], bitrate_kbps
  {direct, relayed, data_saver}, relayed_max_kbps?, audio}``: everything the worker needs; the SDP crosses the pipe
  and nothing else (argv is visible to every process of the user).
* ``tick``    liveness (every 3 s; the worker exits after ``tick_timeout_seconds`` without one, design §7).
* ``pause {reason}`` / ``resume``   capture off / on (secure desktop, a missing bar).
* ``allowance {unlimited, remaining_seconds, resets_at}``   forwarded to the phone's data channel.
* ``restricted {restricted, reason?}``   forwarded to the phone's data channel.
* ``reserved {reserved[], surface_index?}``   DoMe's own rectangles changed: a new geometry generation.
* ``rebuild {source{surface_index, left, top, width, height, rotation}, reserved[]}``   the viewed monitor changed
  size, rotation or place: a new source at the new size, the encoder reopened with an IDR, a new generation.
* ``keyframe``   force an IDR.
* ``stop``    close the connection and exit.

Worker → agent (``ev``): ``answer {answer_sdp, encoder, geometry}`` or ``error {code, detail?, message}`` in reply to
``start``; ``connected {path}``, ``first_frame``, ``hb``, ``bye``, ``keyframe``, ``audio {on}``, ``geometry {...}``,
``state {state, reason?}``, ``stats {relayed_bytes, bitrate_kbps, ...}``, ``disconnected {reason}``, ``exiting {reason}``;
``geometry`` answers ``reserved`` and ``rebuild`` (``error`` when a rebuild cannot open its source).
No message is ever logged whole: SDP crosses here.
"""

from __future__ import annotations

import asyncio
import struct
from typing import IO, Any

from dome_protocol import ProtocolError, dumps_compact, loads_strict

MAX_MESSAGE_BYTES = 65536
_HEADER = struct.Struct(">I")


def encode_message(message: dict[str, Any]) -> bytes:
    data = dumps_compact(message).encode("utf-8")
    if len(data) > MAX_MESSAGE_BYTES:
        raise ProtocolError("PAYLOAD_TOO_LARGE", "pipe message exceeds 64 KiB")
    return _HEADER.pack(len(data)) + data


def _decode(data: bytes) -> dict[str, Any]:
    parsed = loads_strict(data, max_bytes=MAX_MESSAGE_BYTES, require_object=True)
    assert isinstance(parsed, dict)
    return parsed


def read_message_sync(stream: IO[bytes]) -> dict[str, Any] | None:
    """Blocking read of one message; None at EOF (the other side closed the pipe)."""
    header = _read_exact(stream, _HEADER.size)
    if header is None:
        return None
    (length,) = _HEADER.unpack(header)
    if length > MAX_MESSAGE_BYTES:
        raise ProtocolError("PAYLOAD_TOO_LARGE", "pipe message exceeds 64 KiB")
    body = _read_exact(stream, length)
    if body is None:
        return None
    return _decode(body)


def _read_exact(stream: IO[bytes], size: int) -> bytes | None:
    buf = b""
    while len(buf) < size:
        chunk = stream.read(size - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def write_message_sync(stream: IO[bytes], message: dict[str, Any]) -> None:
    stream.write(encode_message(message))
    stream.flush()


async def read_message(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    """Asyncio read of one message; None at EOF."""
    try:
        header = await reader.readexactly(_HEADER.size)
        (length,) = _HEADER.unpack(header)
        if length > MAX_MESSAGE_BYTES:
            raise ProtocolError("PAYLOAD_TOO_LARGE", "pipe message exceeds 64 KiB")
        body = await reader.readexactly(length)
    except asyncio.IncompleteReadError:
        return None
    return _decode(body)


__all__ = ["MAX_MESSAGE_BYTES", "encode_message", "read_message", "read_message_sync", "write_message_sync"]
