"""``windows.lock`` (LockWorkStation; verification ``os_accepted``)."""

from __future__ import annotations

import asyncio
from typing import Any

from . import handler
from .context import ExecutionContext


@handler("windows.lock")
async def lock(ctx: ExecutionContext) -> dict[str, Any]:
    ctx.mark_side_effect()
    await asyncio.to_thread(ctx.services.platform.session.lock)
    ctx.services.state.request_update()
    return {"accepted": True}
