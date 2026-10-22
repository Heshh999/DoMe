"""The strict offer check (design §4.2 step 5, ``rules.screen_signaling`` (4)).

Before anything parses the phone's offer, the agent requires: at most ``screen_sdp_max_bytes``, printable ASCII
plus CRLF only (the shared ``screen_sdp_acceptable``), ``v=0`` first, exactly one ``a=fingerprint:sha-256`` value
(every such line carries the same digest: the certificate the phone signed into the command), a DTLS ``setup``
attribute, ICE credentials, exactly three media sections (``video`` recvonly, ``audio`` recvonly and one
``application`` data-channel section) and H.264 or VP8 offered for the video. Anything else is
``SCREEN_OFFER_INVALID`` with ``detail.reason`` naming the check, never the SDP. The worker then hands exactly the
checked string to aiortc, which refuses what it cannot parse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dome_protocol import ProtocolError, load_registry, screen_sdp_acceptable, sdp_fingerprints

_M_LINE_RE = re.compile(r"^m=(?P<kind>[a-z]+) (?P<port>\d+) (?P<proto>[A-Za-z0-9/]+)(?P<fmts>( [^ ]+)*)$")
_RTPMAP_RE = re.compile(r"^a=rtpmap:(?P<pt>\d+) (?P<codec>[^/]+)/")
_DIRECTIONS = frozenset({"sendrecv", "sendonly", "recvonly", "inactive"})
_SETUP_RE = re.compile(r"^a=setup:(actpass|active|passive)$")
VIDEO_CODECS = frozenset({"h264", "vp8"})


@dataclass(frozen=True, slots=True)
class OfferSummary:
    """What the check learned: the one DTLS fingerprint (upper-case hex pairs) and the codecs offered."""

    fingerprint: str
    h264: bool
    vp8: bool
    byte_size: int


def _invalid(reason: str) -> ProtocolError:
    return load_registry().make_error("SCREEN_OFFER_INVALID", reason=reason)


def check_offer(offer_sdp: object) -> OfferSummary:
    """Apply every rule above; return the summary or raise ``SCREEN_OFFER_INVALID``."""
    limit = int(load_registry().limits["screen_sdp_max_bytes"])
    if not screen_sdp_acceptable(offer_sdp, max_bytes=limit):
        raise _invalid("shape")
    assert isinstance(offer_sdp, str)
    lines = offer_sdp.split("\r\n")
    if lines[-1] == "":
        lines.pop()
    if not lines or lines[0] != "v=0":
        raise _invalid("version")
    prints = sdp_fingerprints(offer_sdp)
    if not prints or any(fp != prints[0] for fp in prints):
        raise _invalid("fingerprint")
    if not any(_SETUP_RE.match(line) for line in lines):
        raise _invalid("setup")
    if not any(line.startswith("a=ice-ufrag:") for line in lines) or not any(
        line.startswith("a=ice-pwd:") for line in lines
    ):
        raise _invalid("ice")
    sections: list[tuple[str, str, str, set[str]]] = []  # kind, proto, direction, codec names (lower case)
    direction = "sendrecv"
    codecs: set[str] = set()
    current: tuple[str, str] | None = None
    for line in lines[1:]:
        m = _M_LINE_RE.match(line)
        if m is not None:
            if current is not None:
                sections.append((current[0], current[1], direction, codecs))
            current = (m.group("kind"), m.group("proto"))
            direction, codecs = "sendrecv", set()
            continue
        if current is None:
            continue  # session-level attributes
        if line.startswith("a=") and line[2:] in _DIRECTIONS:
            direction = line[2:]
            continue
        r = _RTPMAP_RE.match(line)
        if r is not None:
            codecs.add(r.group("codec").lower())
    if current is not None:
        sections.append((current[0], current[1], direction, codecs))
    if len(sections) != 3:
        raise _invalid("media")
    video = [s for s in sections if s[0] == "video"]
    audio = [s for s in sections if s[0] == "audio"]
    data = [s for s in sections if s[0] == "application"]
    if len(video) != 1 or len(audio) != 1 or len(data) != 1:
        raise _invalid("media")
    if video[0][2] != "recvonly" or audio[0][2] != "recvonly":
        raise _invalid("direction")
    if "SCTP" not in data[0][1].upper():
        raise _invalid("media")
    offered = video[0][3] & VIDEO_CODECS
    if not offered:
        raise _invalid("codec")
    return OfferSummary(
        fingerprint=prints[0],
        h264="h264" in offered,
        vp8="vp8" in offered,
        byte_size=len(offer_sdp.encode("utf-8")),
    )


__all__ = ["VIDEO_CODECS", "OfferSummary", "check_offer"]
