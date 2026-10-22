"""The PC-signed screen answer (design §4.3, ``rules.screen_signaling`` (5)).

The worker produces a send-only answer with a fresh per-session DTLS certificate; the AGENT signs
``screen_answer_payload`` with the identity key it already has (``identity.py``, ``pc_key.bin``), so the phone can
verify it against the PC key it pinned at pairing (``rules.pc_key_pinning``). The envelope travels in a
``screen_answer`` frame, never in a result: results are journaled and SDP is never persisted (``rules.screen_content``).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from dome_protocol import build_screen_answer_payload, dumps_compact, sign_payload

if TYPE_CHECKING:
    from ..identity import Identity


def sign_screen_answer(
    identity: Identity,
    *,
    account_id: str,
    pc_id: str,
    controller_id: str,
    command_id: str,
    screen_session_id: str,
    offer_sdp: str,
    answer_sdp: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build and sign the answer payload; returns the envelope dict (``kid`` = the PC's ``pc_kid``)."""
    payload = build_screen_answer_payload(
        account_id=account_id,
        pc_id=pc_id,
        controller_id=controller_id,
        command_id=command_id,
        screen_session_id=screen_session_id,
        offer_sdp=offer_sdp,
        answer_sdp=answer_sdp,
        now=now,
    )
    envelope = sign_payload(identity.ensure_key(), dumps_compact(payload))
    assert envelope.kid == identity.kid
    return envelope.to_dict()


__all__ = ["sign_screen_answer"]
