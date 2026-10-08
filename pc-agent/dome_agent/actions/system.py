"""``system.*`` handlers."""

from __future__ import annotations

from typing import Any

from dome_protocol import load_registry

from .. import __version__
from ..frames import now_text
from . import handler
from .context import ExecutionContext


@handler("system.ping")
async def ping(ctx: ExecutionContext) -> dict[str, Any]:
    return {
        "agent_time": now_text(),
        "agent_version": __version__,
        "protocol_version": load_registry().protocol_version,
    }


@handler("system.get_status")
async def get_status(ctx: ExecutionContext) -> dict[str, Any]:
    return await ctx.services.state.snapshot()
