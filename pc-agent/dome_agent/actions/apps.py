"""``app.*`` handlers. The only input from the wire is ``app_id`` (and an optional ``window_id``);
everything executable comes from the local approval list (:mod:`dome_agent.approved_apps`)."""

from __future__ import annotations

import asyncio
from typing import Any

from dome_protocol import ProtocolError

from ..platform.protocol import AppWindow
from ..store import ApprovedAppRow
from . import handler
from .context import ActionFailed, ExecutionContext

_POLL_INTERVAL = 0.25


def _approved(ctx: ExecutionContext, app_id: str) -> ApprovedAppRow:
    row = ctx.services.apps.get(app_id)
    if row is None:
        raise ProtocolError("APP_NOT_APPROVED", "That app is not approved for remote control. Approve it on the PC first.")
    return row


async def _windows_for(ctx: ExecutionContext, row: ApprovedAppRow) -> tuple[list[int], list[AppWindow]]:
    pids = await asyncio.to_thread(ctx.services.platform.apps.running_pids, row.exe_path)
    if not pids:
        return [], []
    return pids, await asyncio.to_thread(ctx.services.platform.apps.list_windows, pids)


async def _select_window(ctx: ExecutionContext) -> tuple[ApprovedAppRow, AppWindow]:
    assert ctx.target is not None
    row = _approved(ctx, ctx.target["app_id"])
    pids, windows = await _windows_for(ctx, row)
    if not pids:
        raise ProtocolError("APP_NOT_RUNNING", "That app is not running")
    wanted = ctx.target.get("window_id")
    if wanted is not None:
        for w in windows:
            if w.window_id == wanted:
                return row, w
        raise ProtocolError("TARGET_GONE", "That window is no longer there")
    if not windows:
        raise ProtocolError("TARGET_GONE", "The app is running but has no window to control")
    for w in windows:
        if not w.minimized:
            return row, w
    return row, windows[0]


@handler("app.list")
async def list_apps(ctx: ExecutionContext) -> dict[str, Any]:
    apps: list[dict[str, Any]] = []
    for row in ctx.services.apps.list()[:64]:
        try:
            pids, windows = await _windows_for(ctx, row)
        except ProtocolError as exc:
            if exc.code == "PLATFORM_UNSUPPORTED":
                raise
            pids, windows = [], []
        apps.append(
            {
                "app_id": row.app_id,
                "display_name": row.display_name[:64],
                "running": bool(pids),
                "windows": [w.as_result() for w in windows[:32]],
            }
        )
    return {"apps": apps}


@handler("app.launch")
async def launch(ctx: ExecutionContext) -> dict[str, Any]:
    app_id = str(ctx.params["app_id"])
    row = ctx.services.apps.resolve_for_launch(app_id)  # re-checks the executable identity (hash)
    ctx.mark_side_effect()
    try:
        await asyncio.to_thread(ctx.services.platform.apps.launch, row.exe_path)
    except ProtocolError as exc:
        if exc.code in ("APP_LAUNCH_FAILED", "PLATFORM_UNSUPPORTED"):
            raise
        raise ActionFailed("APP_LAUNCH_FAILED", exc.message, result={"app_id": app_id, "launched": False, "running": False}) from exc
    deadline = asyncio.get_running_loop().time() + max(1.0, ctx.command.spec.timeout_ms / 1000 - 1.0)
    running = False
    while True:
        pids = await asyncio.to_thread(ctx.services.platform.apps.running_pids, row.exe_path)
        if pids:
            running = True
            break
        if asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(_POLL_INTERVAL)
    ctx.services.state.request_update()
    if not running:
        raise ActionFailed("APP_LAUNCH_FAILED", "The app was started but no matching process was observed", result={"app_id": app_id, "launched": True, "running": False})
    return {"app_id": app_id, "launched": True, "running": True}


@handler("app.focus")
async def focus(ctx: ExecutionContext) -> dict[str, Any]:
    row, window = await _select_window(ctx)
    focused = bool(await asyncio.to_thread(ctx.services.platform.apps.focus, window.window_id))
    result = {"app_id": row.app_id, "window_id": window.window_id, "focused": focused}
    if not focused:
        raise ActionFailed("FOCUS_DENIED", "Windows did not allow the window to come to the front", result=result)
    return result


@handler("app.minimize")
async def minimize(ctx: ExecutionContext) -> dict[str, Any]:
    row, window = await _select_window(ctx)
    minimized = bool(await asyncio.to_thread(ctx.services.platform.apps.minimize, window.window_id))
    result = {"app_id": row.app_id, "window_id": window.window_id, "minimized": minimized}
    if not minimized:
        raise ActionFailed("OS_ERROR", "Windows did not minimise the window", result=result)
    return result


@handler("app.close")
async def close(ctx: ExecutionContext) -> dict[str, Any]:
    row, window = await _select_window(ctx)
    ctx.mark_side_effect()
    await asyncio.to_thread(ctx.services.platform.apps.request_close, window.window_id)
    deadline = asyncio.get_running_loop().time() + max(1.0, ctx.command.spec.timeout_ms / 1000 - 0.5)
    while True:
        if not await asyncio.to_thread(ctx.services.platform.apps.window_exists, window.window_id):
            ctx.services.state.request_update()
            return {"app_id": row.app_id, "window_id": window.window_id, "closed": True}
        if asyncio.get_running_loop().time() >= deadline:
            break
        await asyncio.sleep(_POLL_INTERVAL)
    raise ActionFailed(
        "CLOSE_REFUSED",
        "The app did not close. It may be asking about unsaved work on the PC.",
        result={"app_id": row.app_id, "window_id": window.window_id, "closed": False},
    )
