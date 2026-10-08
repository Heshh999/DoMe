"""``media.*`` handlers (Windows GlobalSystemMediaTransportControls through the media adapter).

Verification ``observe_session_state``: the session's playback status is re-read after the action;
``set_paused`` polls briefly because players report the new status asynchronously."""

from __future__ import annotations

import asyncio
from typing import Any

from dome_protocol import ProtocolError

from ..platform.protocol import MediaSession
from . import handler
from .context import ActionFailed, ExecutionContext

_READ_BACK_ATTEMPTS = 6
_READ_BACK_INTERVAL = 0.25


def _session_id(ctx: ExecutionContext) -> str:
    assert ctx.target is not None
    return str(ctx.target["session_id"])


async def _get(ctx: ExecutionContext, session_id: str) -> MediaSession:
    session = await asyncio.to_thread(ctx.services.platform.media.get_session, session_id)
    if session is None:
        raise ProtocolError("MEDIA_SESSION_GONE", "That media player is no longer available")
    return session


@handler("media.get_sessions")
async def get_sessions(ctx: ExecutionContext) -> dict[str, Any]:
    sessions = await asyncio.to_thread(ctx.services.platform.media.list_sessions)
    return {"sessions": [s.as_result() for s in sessions[:16]]}


@handler("media.set_paused")
async def set_paused(ctx: ExecutionContext) -> dict[str, Any]:
    session_id = _session_id(ctx)
    paused = bool(ctx.params["paused"])
    wanted = ("paused", "stopped") if paused else ("playing",)
    current = await _get(ctx, session_id)
    if current.status in wanted:
        return {"session": current.as_result()}
    control = "pause" if paused else "play"
    if control not in current.controls:
        raise ActionFailed("ACTION_UNAVAILABLE", f"This media player does not offer {control}", result={"session": current.as_result()})
    session = await asyncio.to_thread(ctx.services.platform.media.set_paused, session_id, paused)
    for _ in range(_READ_BACK_ATTEMPTS):
        if session.status in wanted:
            break
        await asyncio.sleep(_READ_BACK_INTERVAL)
        session = await _get(ctx, session_id)
    ctx.services.state.request_update()
    if session.status not in wanted:
        raise ActionFailed("ACTION_UNAVAILABLE", "The media player did not change its playback state", result={"session": session.as_result()})
    return {"session": session.as_result()}


async def _skip(ctx: ExecutionContext, control: str) -> dict[str, Any]:
    session_id = _session_id(ctx)
    current = await _get(ctx, session_id)
    if control not in current.controls:
        raise ActionFailed("ACTION_UNAVAILABLE", f"This media player does not offer {control}", result={"session": current.as_result()})
    ctx.mark_side_effect()
    fn = ctx.services.platform.media.next if control == "next" else ctx.services.platform.media.previous
    session = await asyncio.to_thread(fn, session_id)
    ctx.services.state.request_update()
    return {"session": session.as_result()}


@handler("media.next")
async def next_track(ctx: ExecutionContext) -> dict[str, Any]:
    return await _skip(ctx, "next")


@handler("media.previous")
async def previous_track(ctx: ExecutionContext) -> dict[str, Any]:
    return await _skip(ctx, "previous")
