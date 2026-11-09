"""``screen.session_start`` / ``screen.session_stop`` (design §4.2, §7; ``rules.screen_sessions``).

Only the session lifecycle travels as commands: the picture goes DTLS-SRTP from the worker to the phone, the
heartbeats ride the data channel, and taps stay on the signed ``input_batch`` path. Both actions are
``ai_eligible: false``. The result strips everything but ids, geometry, flags and the allowance: the PC-signed
answer leaves in a ``screen_answer`` frame (``ScreenSessionManager``), never in a result, because results are
journaled and SDP is never persisted (``rules.screen_content``).

Since 1.5 (``rules.screen_cursor``): the ``cursor`` param is read from the RAW signed params, so its presence is what
the phone signed (``VerifiedCommand.payload["params"]``; no default ever fills it in), and the result carries
``cursor`` only when the start carried it, because a 1.4 phone validates the result with additionalProperties:false.
The PC-signed answer is stamped with the start's own ``protocol_version`` (``rules.screen_signaling`` (5)).
"""

from __future__ import annotations

from typing import Any

from dome_protocol import ProtocolError, load_registry

from . import handler
from .context import ExecutionContext


@handler("screen.session_start")
async def session_start(ctx: ExecutionContext) -> dict[str, Any]:
    services = ctx.services
    if not services.platform.supports_windows_actions:
        raise ProtocolError("PLATFORM_UNSUPPORTED", "Screen view is only available on Windows.")
    store = services.store
    grant = store.get_grant(ctx.command.controller_id)
    if grant is None or grant.revoked:
        raise ProtocolError("CONTROLLER_REVOKED", "Access for this phone was revoked.")
    effective = set(grant.effective_capabilities(store.current_snapshot_id()))
    if "screen" not in effective:
        raise load_registry().make_error(
            "GRANT_MISSING", "This phone does not have the 'screen' permission on this PC."
        )
    manager = services.screen
    raw_params = ctx.command.payload.get("params")
    cursor = raw_params.get("cursor") if isinstance(raw_params, dict) else None
    session = await manager.start_session(
        controller_id=ctx.command.controller_id,
        kid=ctx.command.envelope.kid,
        display_name=grant.display_name,
        command_id=ctx.command.command_id,
        offer_sdp=str(ctx.params["offer_sdp"]),
        surface=ctx.params.get("surface", "primary"),
        takeover=bool(ctx.params.get("takeover", False)),
        protocol_version=str(ctx.command.payload["protocol_version"]),
        cursor=str(cursor) if cursor is not None else None,
    )
    # PC sound is Phase 2: the audio transceiver is negotiated inactive, so `audio` is false whatever was asked.
    result: dict[str, Any] = {
        "screen_session_id": session.screen_session_id,
        "surfaces": [m.as_surface() for m in session.surfaces][:8],
        "surface_index": session.monitor.index,
        "gen": session.gen,
        "audio": False,
        "lease_seconds": max(1, min(60, int(round(manager.lease_seconds)))),
        # The agent knows the ticket, not the window: `remaining_seconds` is null until the relay says more
        # (CONTRACT_ISSUES.md); the phone counts down from screen_ice_servers.allowance.
        "allowance": {"unlimited": session.unlimited, "remaining_seconds": None, "resets_at": session.resets_at},
        "consent": session.consent,
    }
    if session.encoder in ("h264", "vp8"):
        result["encoder"] = session.encoder
    if cursor is not None:
        # the mode the PC will try (the data channel's state.cursor is authoritative once capture is open)
        result["cursor"] = session.cursor_mode if session.cursor_mode in ("drawn", "overlay") else "drawn"
    return result


@handler("screen.session_stop")
async def session_stop(ctx: ExecutionContext) -> dict[str, Any]:
    stopped, live = await ctx.services.screen.stop_session(
        controller_id=ctx.command.controller_id, screen_session_id=str(ctx.params["screen_session_id"])
    )
    return {"stopped": stopped, "live_seconds": live}
