"""Screen view (protocol 1.4): the PC-signed answer, the strict SDP helper and the data-channel vocabulary.

The phone's offer travels inside the signed ``screen.session_start`` command (an ordinary command, verified by
``verify_and_parse_command``). The PC answers with ``screen_answer_payload`` signed by its identity key; the phone
verifies it against the PC key pinned at pairing (``rules.pc_key_pinning``) with ``verify_screen_answer_payload``.
Nothing here touches media: SDP text is validated, hashed and compared, never logged or stored by this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from .digest import sha256_b64url
from .errors import ProtocolError
from .keys import kid_from_jwk
from .registry import Registry, load_registry
from .schemas import Schemas, load_schemas
from .signing import Envelope, verify_envelope
from .strict_json import loads_strict
from .timeutil import check_command_window, format_rfc3339, now_utc
from .versions import protocol_compatible

__all__ = [
    "SCREEN_ANSWER_TYPE",
    "SCREEN_DATA_CHANNEL_LABEL",
    "SCREEN_SDP_PATTERN",
    "ScreenDcDirection",
    "VerifiedScreenAnswer",
    "build_screen_answer_payload",
    "offer_digest",
    "parse_screen_dc_message",
    "screen_sdp_acceptable",
    "sdp_fingerprints",
    "verify_screen_answer_payload",
]

SCREEN_ANSWER_TYPE = "screen_answer"
SCREEN_DATA_CHANNEL_LABEL = "dome-screen-v1"
# actions.json screen.session_start.params.offer_sdp and screen_answer_payload.answer_sdp share this pattern.
SCREEN_SDP_PATTERN = r"^[\x20-\x7E\r\n]*$"
_SDP_RE = re.compile(SCREEN_SDP_PATTERN, re.ASCII)
_FINGERPRINT_RE = re.compile(r"^a=fingerprint:sha-256[ \t]+([0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){31})[ \t]*$", re.ASCII)

ScreenDcDirection = Literal["phone_to_pc", "pc_to_phone"]


def screen_sdp_acceptable(text: object, *, max_bytes: int | None = None) -> bool:
    """The strict SDP shape both devices demand before parsing anything (rules.screen_signaling (4)).

    Non-empty, at most ``screen_sdp_max_bytes`` bytes, printable ASCII plus CRLF only, and every line break a real
    CRLF pair: a bare LF, a bare CR, a tab, a NUL or a non-ASCII character fails. This is the shared half of the
    check; the agent's m-line and fingerprint rules live in its ``sdp_check``.
    """
    if not isinstance(text, str) or not text:
        return False
    limit = max_bytes if max_bytes is not None else int(load_registry().limits["screen_sdp_max_bytes"])
    if len(text.encode("utf-8", errors="surrogatepass")) > limit:
        return False
    if _SDP_RE.search(text) is None:
        return False
    # CRLF only: each "\r" is followed by "\n" and each "\n" preceded by "\r".
    return text.count("\r\n") == text.count("\r") == text.count("\n")


def sdp_fingerprints(sdp: str) -> list[str]:
    """Every ``a=fingerprint:sha-256`` value in the SDP, upper-cased, in order (the phone requires them all equal)."""
    out: list[str] = []
    for line in sdp.split("\r\n"):
        m = _FINGERPRINT_RE.match(line)
        if m:
            out.append(m.group(1).upper())
    return out


def offer_digest(offer_sdp: str) -> str:
    """base64url SHA-256 of the exact offer bytes, as the answer names them."""
    return sha256_b64url(offer_sdp.encode("utf-8"))


def build_screen_answer_payload(
    *,
    account_id: str,
    pc_id: str,
    controller_id: str,
    command_id: str,
    screen_session_id: str,
    offer_sdp: str,
    answer_sdp: str,
    now: datetime | None = None,
    registry: Registry | None = None,
) -> dict[str, Any]:
    """The payload the PC signs with its identity key to answer one ``screen.session_start``
    (``rules.screen_signaling`` (5)). Lifetime = ``screen_answer_lifetime_seconds``; the SDP must already have passed
    ``screen_sdp_acceptable``. The caller signs ``dumps_compact(payload)`` with the PC key and sends it in a
    ``screen_answer`` frame; it never goes into a result or a journal."""
    registry = registry or load_registry()
    if not screen_sdp_acceptable(answer_sdp, max_bytes=registry.limits["screen_sdp_max_bytes"]):
        raise ProtocolError("MALFORMED_MESSAGE", "answer_sdp is not an acceptable SDP")
    issued = now or now_utc()
    return {
        "type": SCREEN_ANSWER_TYPE,
        "protocol_version": registry.protocol_version,
        "account_id": account_id,
        "pc_id": pc_id,
        "controller_id": controller_id,
        "command_id": command_id,
        "screen_session_id": screen_session_id,
        "offer_digest": offer_digest(offer_sdp),
        "answer_sdp": answer_sdp,
        "issued_at": format_rfc3339(issued),
        "expires_at": format_rfc3339(issued + timedelta(seconds=registry.limits["screen_answer_lifetime_seconds"])),
    }


@dataclass(frozen=True, slots=True)
class VerifiedScreenAnswer:
    """A screen answer whose signature, pin, binding, digest, window and fingerprints all checked out."""

    envelope: Envelope
    payload: dict[str, Any]
    pc_kid: str
    fingerprint: str

    @property
    def answer_sdp(self) -> str:
        return str(self.payload["answer_sdp"])

    @property
    def screen_session_id(self) -> str:
        return str(self.payload["screen_session_id"])


def _invalid(reason: str, cause: str | None = None) -> ProtocolError:
    detail: dict[str, Any] = {"reason": reason}
    if cause:
        detail["cause"] = cause
    return ProtocolError("SCREEN_ANSWER_INVALID", f"screen answer rejected: {reason}", detail=detail)


def verify_screen_answer_payload(
    raw_envelope: Any,
    *,
    pinned_jwk: dict[str, str],
    account_id: str,
    controller_id: str,
    pc_id: str,
    command_id: str,
    offer_sdp: str,
    now: datetime | None = None,
    registry: Registry | None = None,
    schemas: Schemas | None = None,
) -> VerifiedScreenAnswer:
    """The phone's acceptance test for a ``screen_answer`` frame (``rules.screen_signaling`` (6)).

    Accepts only if the envelope's kid is the PC key pinned at pairing, the signature verifies with that key, the
    payload names this account, controller, PC and command, ``offer_digest`` matches the offer the phone sent, the
    window is fresh (lifetime <= screen_answer_lifetime_seconds, skew max_clock_skew_seconds) and every
    ``a=fingerprint:sha-256`` in the answer is identical. Everything else is ``SCREEN_ANSWER_INVALID``
    (``detail.reason`` says which check, never the SDP), except a stamp this library does not speak, which is
    ``PROTOCOL_INCOMPATIBLE`` so the phone can say "update". No "continue anyway" exists.
    """
    registry = registry or load_registry()
    schemas = schemas or load_schemas()
    try:
        pinned_kid = kid_from_jwk(pinned_jwk)
    except ProtocolError as exc:
        raise _invalid("pinned key unusable", exc.code) from None
    try:
        envelope, payload = verify_envelope(
            raw_envelope,
            lambda kid: pinned_jwk if kid == pinned_kid else None,
            max_payload_bytes=registry.limits["max_payload_bytes"],
            max_depth=registry.limits["max_json_depth"],
        )
    except ProtocolError as exc:
        # UNKNOWN_KEY here means the answer was signed by a key other than the pinned one: the hard stop.
        raise _invalid("kid is not the pinned PC key" if exc.code == "UNKNOWN_KEY" else "envelope or signature", exc.code) from None
    stamp = payload.get("protocol_version")
    if isinstance(stamp, str) and not protocol_compatible(stamp, (registry.protocol_version,)):
        raise ProtocolError(
            "PROTOCOL_INCOMPATIBLE", "unsupported protocol version", detail={"peer": stamp, "supported": [registry.protocol_version]}
        )
    try:
        schemas.validate_screen_answer_payload(payload)
    except ProtocolError as exc:
        raise _invalid("payload shape", exc.code) from None
    expected = {"account_id": account_id, "controller_id": controller_id, "pc_id": pc_id, "command_id": command_id}
    for field, value in expected.items():
        if payload[field] != value:
            raise _invalid(f"{field} is not ours")
    if payload["offer_digest"] != offer_digest(offer_sdp):
        raise _invalid("offer_digest does not match the offer we sent")
    skew = registry.limits["max_clock_skew_seconds"]
    try:
        check_command_window(
            payload["issued_at"],
            payload["expires_at"],
            now=now,
            max_lifetime_seconds=registry.limits["screen_answer_lifetime_seconds"] + skew,
            max_skew_seconds=skew,
        )
    except ProtocolError as exc:
        raise _invalid("window", exc.code) from None
    answer_sdp = payload["answer_sdp"]
    if not screen_sdp_acceptable(answer_sdp, max_bytes=registry.limits["screen_sdp_max_bytes"]):
        raise _invalid("answer_sdp is not an acceptable SDP")
    prints = sdp_fingerprints(answer_sdp)
    if not prints or any(fp != prints[0] for fp in prints):
        raise _invalid("fingerprints missing or not identical")
    return VerifiedScreenAnswer(envelope=envelope, payload=payload, pc_kid=pinned_kid, fingerprint=prints[0])


def parse_screen_dc_message(
    text: str | bytes,
    direction: ScreenDcDirection,
    *,
    registry: Registry | None = None,
    schemas: Schemas | None = None,
) -> dict[str, Any]:
    """Strict-parse one ``dome-screen-v1`` data-channel message and validate it for the receiving side
    (``rules.screen_content``): at most ``screen_dc_message_max_bytes``, a JSON object, one of the closed vocabulary
    for ``direction`` (``phone_to_pc`` on the PC, ``pc_to_phone`` on the phone). Anything else raises and the receiver
    drops it; no message here can inject input or run anything."""
    registry = registry or load_registry()
    schemas = schemas or load_schemas()
    if isinstance(text, bytes | bytearray):
        try:
            text = bytes(text).decode("utf-8")
        except UnicodeDecodeError:
            raise ProtocolError("MALFORMED_MESSAGE", "data-channel message is not UTF-8") from None
    parsed = loads_strict(
        text,
        max_bytes=registry.limits["screen_dc_message_max_bytes"],
        max_depth=registry.limits["max_json_depth"],
        require_object=True,
    )
    schemas.validate_screen_dc_message(direction, parsed)
    return parsed
