"""Protocol version arithmetic: the MINOR compatibility rule, negotiation and per-action stamping.

Kept apart from ``commands`` so the registry can check each action's ``since`` while loading.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_VERSION_RE = re.compile(r"\A([0-9]+)\.([0-9]+)\Z", re.ASCII)

# rules.command_version_stamping: input batches were introduced in 1.1 and are stamped with it.
INPUT_BATCH_PROTOCOL_VERSION = "1.1"


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
    "negotiate_version",
    "parse_version",
    "protocol_compatible",
    "version_below",
]
