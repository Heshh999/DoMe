"""``app.*`` handlers. The only input from the wire is ``app_id`` (and an optional ``window_id``);
everything executable comes from the local approval list (:mod:`dome_agent.approved_apps`).

The window actions (``app.maximize``, ``app.restore``, ``app.set_fullscreen``; protocol 1.2) never start
the app (``APP_NOT_RUNNING``; the phone offers *Open*). A process that has no window yet gets up to
:data:`FIRST_WINDOW_WAIT` seconds for one. Every failure carries the best-known
``app_window_state_result`` (``rules.window_actions``). The adapter may press F11 in any of them (maximise
and restore leave full screen first), so each gets the deadline after which it must not (the worker thread
outlives a timed-out command), whether keys are held from the phone, and the approval's *Full screen with
F11* choice (``fullscreen_f11``: F11 may be pressed in an app off the built-in F11 list). When F11 was not
pressed because that choice is off, the phone is told where to turn it on, by the app's approved name
(:func:`~dome_agent.platform.protocol.f11_not_enabled_message`).

``app.list`` names a browser's windows (:func:`~dome_agent.platform.protocol.browser_window_label`) instead
of sending their titles — a browser window's title is its tab's page title, and a YouTube results page's is
the search query (rules.search_queries) — and the journal keeps no window title at all (:func:`journal_view`)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from pathlib import PureWindowsPath
from typing import Any

from dome_protocol import ProtocolError

from ..platform.protocol import (
    AppWindow,
    F11NotEnabledError,
    WindowState,
    WindowStateError,
    browser_window_label,
    f11_not_enabled_message,
)
from ..store import ApprovedAppRow
from . import handler, journal_view
from .context import ActionFailed, ExecutionContext

_POLL_INTERVAL = 0.25
FIRST_WINDOW_WAIT = 3.0  # a just-started app may not have shown its window yet
F11_DEADLINE_MARGIN = 1.0  # F11 is never pressed later than this before the command's timeout
_MAX_PREFER_READS = 8  # windows whose state is read to find the preferred one


def _approved(ctx: ExecutionContext, app_id: str) -> ApprovedAppRow:
    row = ctx.services.apps.get(app_id)
    if row is None:
        raise ProtocolError(
            "APP_NOT_APPROVED", "That app is not approved for remote control. Approve it on the PC first."
        )
    return row


async def _windows_for(ctx: ExecutionContext, row: ApprovedAppRow) -> tuple[list[int], list[AppWindow]]:
    pids = await asyncio.to_thread(ctx.services.platform.apps.running_pids, row.exe_path)
    if not pids:
        return [], []
    return pids, await asyncio.to_thread(ctx.services.platform.apps.list_windows, pids)


async def _select_window(
    ctx: ExecutionContext,
    wait_for_window: float = 0.0,
    prefer: Callable[[WindowState], bool] | None = None,
) -> tuple[ApprovedAppRow, AppWindow]:
    """The targeted window: the explicit ``window_id`` (it must be one of the app's windows), else — with
    ``prefer`` and several windows — the first in Z-order whose state read back satisfies it, else the
    first non-minimised window in Z-order, else the first one. With ``wait_for_window`` a running app
    that has no window yet is polled that long for its first one."""
    assert ctx.target is not None
    row = _approved(ctx, ctx.target["app_id"])
    pids, windows = await _windows_for(ctx, row)
    if not pids:
        raise ProtocolError("APP_NOT_RUNNING", "That app is not running")
    if not windows and wait_for_window > 0:
        deadline = asyncio.get_running_loop().time() + wait_for_window
        while not windows and pids and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(_POLL_INTERVAL)
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
    if prefer is not None and len(windows) > 1:
        for w in windows[:_MAX_PREFER_READS]:
            try:
                state = await asyncio.to_thread(ctx.services.platform.apps.window_state, w.window_id)
            except ProtocolError:
                continue
            if prefer(state):
                return row, w
    for w in windows:
        if not w.minimized:
            return row, w
    return row, windows[0]


def _window_result(window: AppWindow, label: str | None) -> dict[str, object]:
    out = window.as_result()
    if label is not None:
        out["title"] = label
    return out


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
        label = browser_window_label(PureWindowsPath(row.exe_path).name)
        apps.append(
            {
                "app_id": row.app_id,
                "display_name": row.display_name[:64],
                "running": bool(pids),
                "windows": [_window_result(w, label) for w in windows[:32]],
            }
        )
    return {"apps": apps}


@journal_view("app.list")
def _list_without_titles(result: dict[str, Any]) -> dict[str, Any]:
    """Window titles (document names, page titles) reach the phone once and are never journaled, like the
    foreground window's title in ``input.session_start``; a duplicate or late re-send shows no title."""
    return {
        **result,
        "apps": [{**app, "windows": [{**w, "title": ""} for w in app["windows"]]} for app in result["apps"]],
    }


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
        raise ActionFailed(
            "APP_LAUNCH_FAILED", exc.message, result={"app_id": app_id, "launched": False, "running": False}
        ) from exc
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
        raise ActionFailed(
            "APP_LAUNCH_FAILED",
            "The app was started but no matching process was observed",
            result={"app_id": app_id, "launched": True, "running": False},
        )
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


async def _window_action(
    ctx: ExecutionContext,
    call: Callable[..., WindowState],
    verified: Callable[[WindowState], bool],
    unverified_code: str,
    *,
    holds_check: bool = False,
    prefer: Callable[[WindowState], bool] | None = None,
) -> dict[str, Any]:
    """Shared body of the window actions: select (waiting for a first window), run the adapter call in a
    worker thread, report the state read back — or fail with the best-known state.

    The adapter gets ``deadline``: F11 is not pressed later than :data:`F11_DEADLINE_MARGIN` before the
    executor gives up on the command, so a worker thread still running after the timeout (asyncio cannot
    cancel it) never presses F11 once the phone was answered. It also gets ``keys_held``: maximise and
    restore of a full-screen window press F11 too. (``mark_side_effect`` matters only for non-idempotent
    actions: maximise and restore are idempotent, and their F11 only ever leaves full screen.) And it gets
    ``fullscreen_f11``, read from the approval for this command, never from the wire."""
    deadline = time.monotonic() + ctx.command.spec.timeout_ms / 1000 - F11_DEADLINE_MARGIN
    row, window = await _select_window(ctx, wait_for_window=FIRST_WINDOW_WAIT, prefer=prefer)
    session = ctx.services.input.current
    keys_held = session is not None and bool(session.held_keys)
    if holds_check:
        if keys_held:
            # F11 on top of a key the phone holds down would be a different shortcut (Ctrl+F11, ...)
            raise ActionFailed(
                "ACTION_UNAVAILABLE",
                "Release the keys held from the phone first.",
                result=await _best_known_state(ctx, row, window),
            )
        ctx.mark_side_effect()  # F11 is a toggle: a timeout after this point is outcome_unknown, never retried
    try:
        state = await asyncio.to_thread(
            call, window.window_id, deadline=deadline, keys_held=keys_held, fullscreen_f11=row.fullscreen_f11
        )
    except WindowStateError as exc:
        ctx.services.state.request_update()
        result = (
            exc.state.as_result(row.app_id, window.window_id)
            if exc.state is not None
            else await _best_known_state(ctx, row, window)
        )
        message = exc.message
        if isinstance(exc, F11NotEnabledError):  # nothing was pressed: say where the PC owner can turn it on
            message = f11_not_enabled_message(row.display_name, entering=exc.entering)
        raise ActionFailed(exc.code, message, result=result) from exc
    except ProtocolError as exc:
        if exc.code in ("PLATFORM_UNSUPPORTED", "TARGET_GONE"):
            raise
        ctx.services.state.request_update()
        raise ActionFailed(exc.code, exc.message, result=await _best_known_state(ctx, row, window)) from exc
    ctx.services.state.request_update()
    result = state.as_result(row.app_id, window.window_id)
    if not verified(state):  # the adapter must never report a placement it did not read back
        raise ActionFailed(unverified_code, "Windows did not change that window.", result=result)
    return result


async def _best_known_state(ctx: ExecutionContext, row: ApprovedAppRow, window: AppWindow) -> dict[str, Any] | None:
    try:
        state = await asyncio.to_thread(ctx.services.platform.apps.window_state, window.window_id)
    except ProtocolError:
        return None
    return state.as_result(row.app_id, window.window_id)


# Maximised / normal both mean "not in full screen" (the contract: full screen is left first).


@handler("app.maximize")
async def maximize(ctx: ExecutionContext) -> dict[str, Any]:
    return await _window_action(
        ctx,
        ctx.services.platform.apps.maximize,
        lambda s: s.state_name() == "maximized" and not s.fullscreen,
        "WINDOW_STATE_UNCHANGED",
    )


@handler("app.restore")
async def restore(ctx: ExecutionContext) -> dict[str, Any]:
    return await _window_action(
        ctx,
        ctx.services.platform.apps.restore,
        lambda s: s.state_name() == "normal" and not s.fullscreen,
        "WINDOW_STATE_UNCHANGED",
    )


@handler("app.set_fullscreen")
async def set_fullscreen(ctx: ExecutionContext) -> dict[str, Any]:
    enabled = bool(ctx.params["enabled"])
    apps = ctx.services.platform.apps

    def call(window_id: str, *, deadline: float | None, keys_held: bool, fullscreen_f11: bool) -> WindowState:
        return apps.set_fullscreen(
            window_id, enabled, deadline=deadline, keys_held=keys_held, fullscreen_f11=fullscreen_f11
        )

    return await _window_action(
        ctx,
        call,
        lambda s: s.fullscreen == enabled,
        "FULLSCREEN_UNSUPPORTED" if enabled else "WINDOW_STATE_UNCHANGED",
        holds_check=True,
        # Leaving full screen without a window_id means the app's full-screen window, which need not be the
        # front-most one (two Edge windows, an Edge app window). Entering keeps the front-most window: when
        # it is already full screen, the app is.
        prefer=None if enabled else (lambda s: s.fullscreen),
    )


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
