"""Protocol version arithmetic: the MINOR compatibility rule, negotiation and per-action stamping.

Kept apart from ``commands`` so the registry can check each action's ``since`` while loading.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

_VERSION_RE = re.compile(r"\A([0-9]+)\.([0-9]+)\Z", re.ASCII)

# rules.command_version_stamping: input batches were introduced in 1.1 and are stamped with it unless they
# carry an event a later MINOR added (see ``input_batch_protocol_version``).
INPUT_BATCH_PROTOCOL_VERSION = "1.1"

# Screen view (protocol 1.4) added three input events; a batch that carries one needs a 1.4 peer.
SCREEN_INPUT_PROTOCOL_VERSION = "1.4"
_SCREEN_INPUT_EVENT_TYPES = frozenset({"pointer_move_to", "pointer_wheel"})
_SCREEN_INPUT_KEYS = frozenset({"f11"})


def input_event_since(event: Mapping[str, Any]) -> str:
    """The protocol version that defined this ``input_event`` (1.1 for the touchpad events, 1.4 for the Screen
    view additions ``pointer_move_to``, ``pointer_wheel`` and ``key: f11``). Unknown shapes answer the base
    version: schema validation, not this function, rejects them."""
    kind = event.get("type")
    if kind in _SCREEN_INPUT_EVENT_TYPES or (kind == "key" and event.get("key") in _SCREEN_INPUT_KEYS):
        return SCREEN_INPUT_PROTOCOL_VERSION
    return INPUT_BATCH_PROTOCOL_VERSION


def input_batch_protocol_version(events: Iterable[Mapping[str, Any]]) -> str:
    """rules.command_version_stamping: the lowest protocol version that defines every event in the batch.

    A phone stamps its batches with this so a 1.1 PC keeps accepting plain touchpad batches, and verifiers refuse a
    batch stamped below what its events need (PROTOCOL_INCOMPATIBLE, ``required`` = this value)."""
    best = INPUT_BATCH_PROTOCOL_VERSION
    for ev in events:
        since = input_event_since(ev)
        if version_below(best, since):
            best = since
    return best


def parse_version(version: object) -> tuple[int, int] | None:
    """``(major, minor)`` for a strict ``MAJOR.MINOR`` string (ASCII digits only), else ``None``."""
    if not isinstance(version, str):
        return None
    m = _VERSION_RE.match(version)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def protocol_compatible(peer_version: str, supported: tuple[str, ...]) -> bool:
    parsed = parse_version(peer_version)
    if parsed is None:
        return False
    major, minor = parsed
    best_minor = -1
    for v in supported:
        sv = parse_version(v)
        if sv is not None and sv[0] == major:
            best_minor = max(best_minor, sv[1])
    return best_minor >= 0 and minor <= best_minor


def version_below(version: str, minimum: str) -> bool:
    """True when ``version`` is a lower MINOR of the same MAJOR, or a lower MAJOR, than ``minimum``.

    Both must be valid version strings (callers validate the payload schema first)."""
    v, m = parse_version(version), parse_version(minimum)
    if v is None or m is None:
        raise ValueError(f"not a protocol version: {version!r} / {minimum!r}")
    return v < m


def negotiate_version(peer_versions: Iterable[str], supported: Iterable[str]) -> str | None:
    """The highest version both sides speak (rules.bridge_negotiation).

    Within the highest MAJOR both lists name, that is the lower of the two sides' highest MINORs: under the
    compatibility rule a side that supports a MINOR also accepts every lower one. ``None`` when no MAJOR is
    shared (invalid strings are ignored)."""

    def best_minor_by_major(versions: Iterable[str]) -> dict[int, int]:
        out: dict[int, int] = {}
        for v in versions:
            parsed = parse_version(v)
            if parsed is not None:
                out[parsed[0]] = max(out.get(parsed[0], -1), parsed[1])
        return out

    peer, ours = best_minor_by_major(peer_versions), best_minor_by_major(supported)
    common = set(peer) & set(ours)
    if not common:
        return None
    major = max(common)
    return f"{major}.{min(peer[major], ours[major])}"


__all__ = [
    "INPUT_BATCH_PROTOCOL_VERSION",
    "SCREEN_INPUT_PROTOCOL_VERSION",
    "input_batch_protocol_version",
    "input_event_since",
    "negotiate_version",
    "parse_version",
    "protocol_compatible",
    "version_below",
]
