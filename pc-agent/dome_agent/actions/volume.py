"""``windows.get_volume`` / ``windows.set_volume`` / ``windows.set_muted`` (pycaw on Windows).

Verification strategy ``read_back``: the adapter returns the state it read from the OS after
setting it; the handler compares it with the request."""

from __future__ import annotations

import asyncio
from typing import Any

from . import handler
from .context import ActionFailed, ExecutionContext

READ_BACK_TOLERANCE = 1  # pycaw stores a float scalar; a 1 % rounding difference is not a failure


@handler("windows.get_volume")
async def get_volume(ctx: ExecutionContext) -> dict[str, Any]:
    state = await asyncio.to_thread(ctx.services.platform.volume.get)
    return state.as_result()


@handler("windows.set_volume")
async def set_volume(ctx: ExecutionContext) -> dict[str, Any]:
    wanted = int(ctx.params["value"])
    state = await asyncio.to_thread(ctx.services.platform.volume.set_volume, wanted)
    ctx.services.state.request_update()
    if abs(state.value - wanted) > READ_BACK_TOLERANCE:
        raise ActionFailed("OS_ERROR", f"Windows reports volume {state.value} after setting {wanted}", result=state.as_result())
    return state.as_result()


@handler("windows.set_muted")
async def set_muted(ctx: ExecutionContext) -> dict[str, Any]:
    wanted = bool(ctx.params["muted"])
    state = await asyncio.to_thread(ctx.services.platform.volume.set_muted, wanted)
    ctx.services.state.request_update()
    if state.muted != wanted:
        raise ActionFailed("OS_ERROR", "Windows did not apply the mute state", result=state.as_result())
    return state.as_result()
