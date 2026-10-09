"""``input.session_start`` / ``input.session_stop`` (spec §10A, ``rules.input_sessions``).

Only the session lifecycle travels as commands; the events arrive in signed ``input_batch`` frames
that bypass the command queue (``Agent._on_input_batch`` → :class:`InputSessionManager`). Both actions
are ``ai_eligible: false``: no AI catalogue, routine or layout shortcut may issue them.
"""

from __future__ import annotations

from typing import Any

from dome_protocol import ProtocolError

from . import handler
from .context import ExecutionContext


@handler("input.session_start")
async def session_start(ctx: ExecutionContext) -> dict[str, Any]:
    services = ctx.services
    if not services.platform.supports_windows_actions:
        raise ProtocolError("PLATFORM_UNSUPPORTED", "Manual touchpad/keyboard input is only available on Windows.")
    store = services.store
    grant = store.get_grant(ctx.command.controller_id)
    if grant is None or grant.revoked:
        raise ProtocolError("CONTROLLER_REVOKED", "Access for this phone was revoked.")
    effective = set(grant.effective_capabilities(store.current_snapshot_id()))
    manager = services.input
    session = await manager.start_session(
        controller_id=ctx.command.controller_id,
        kid=ctx.command.envelope.kid,
        pointer="pointer" in effective,
        keyboard="keyboard" in effective,
        takeover=bool(ctx.params.get("takeover", False)),
        controller_issued_at=ctx.command.payload.get("issued_at"),
    )
    fg = manager.foreground
    # The window title (document names, URLs) is NOT part of the result: results are journaled durably for
    # duplicate re-emission. The phone gets the title through the next pc_state frame (memory only).
    foreground = fg.as_result() if fg is not None else None
    if foreground is not None:
        foreground.pop("window_title", None)
    return {
        "input_session_id": session.input_session_id,
        "lease_seconds": max(1, min(30, int(round(manager.lease_seconds)))),
        "input_age_budget_ms": max(100, min(5000, int(manager.age_budget_ms))),
        "max_batch_events": max(1, min(256, int(manager.max_batch_events))),
        "pointer": session.pointer,
        "keyboard": session.keyboard,
        "foreground_app": foreground,
    }


@handler("input.session_stop")
async def session_stop(ctx: ExecutionContext) -> dict[str, Any]:
    stopped, released = await ctx.services.input.stop_session(
        controller_id=ctx.command.controller_id, input_session_id=str(ctx.params["input_session_id"])
    )
    return {"stopped": stopped, "released_holds": released}
