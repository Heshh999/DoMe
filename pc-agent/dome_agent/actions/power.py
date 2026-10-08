"""``power.*`` handlers and the :class:`PowerManager` countdown.

A confirmed power command is acknowledged ``executing``, armed as a cancellable countdown
(``pending_power_action`` in state frames) and completed through ``Executor.complete`` when the OS
call has been issued — the result then reports what Windows actually accepted. ``power.cancel``
(or local Disable remote control) ends the pending command as ``canceled`` / ``POWER_CANCELED``.
Sleep (``SetSuspendState``) blocks until resume, so it runs in a worker thread and is reported as
accepted if it has not failed within a short grace period.

Honesty rule for the most dangerous action: once the countdown has moved into its *issuing* phase
(the OS call is being made in a worker thread) a cancel can no longer stop it — cancelling the
awaiting coroutine would not stop the thread — so ``cancel()`` refuses (``canceled: false``) and the
countdown's own completion reports the real OS outcome. For a restart/shutdown that Windows already
accepted, ``power.cancel`` asks Windows to abort it (``AbortSystemShutdownW``) and reports
``canceled: true`` only when Windows said yes.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from dome_protocol import ProtocolError, format_rfc3339, now_utc, parse_rfc3339

from ..logsetup import get_logger
from . import handler
from .context import DEFERRED, ActionFailed, Deferred, ExecutionContext

if TYPE_CHECKING:
    from ..platform.protocol import PlatformSet
    from ..state import StateAggregator
    from ..store import Store

log = get_logger(__name__)

Completer = Callable[[str, str, dict[str, Any] | None, ProtocolError | None], Awaitable[None]]
SLEEP_GRACE_SECONDS = 2.0
ABORTABLE_ACTIONS = frozenset({"power.restart", "power.shutdown"})


@dataclass(slots=True)
class _Armed:
    command_id: str
    action: str
    countdown_seconds: int
    fires_at: str
    task: asyncio.Task[None]
    issuing: bool = False  # the OS call is being made: too late to cancel


@dataclass(slots=True)
class _Issued:
    """A restart/shutdown Windows accepted; ``power.cancel`` may still abort it through the OS."""

    command_id: str
    action: str


class PowerManager:
    def __init__(self, store: Store, platform: PlatformSet, state: StateAggregator) -> None:
        self._store = store
        self._platform = platform
        self._state = state
        self._complete: Completer | None = None
        self._armed: _Armed | None = None
        self._issued: _Issued | None = None
        self._lock = asyncio.Lock()

    def bind(self, complete: Completer) -> None:
        self._complete = complete

    @property
    def pending(self) -> _Armed | None:
        return self._armed

    async def arm(self, ctx: ExecutionContext) -> Deferred:
        async with self._lock:
            if self._armed is not None or self._store.get_pending_power() is not None:
                raise ProtocolError(
                    "ACTION_UNAVAILABLE", "Another power action is already pending on this PC. Cancel it first."
                )
            countdown = int(ctx.params.get("countdown_seconds", 10))
            fires_at = format_rfc3339(now_utc() + timedelta(seconds=countdown))
            self._store.set_pending_power(ctx.command.command_id, ctx.action, fires_at)
            self._issued = None
            task = asyncio.get_running_loop().create_task(
                self._countdown(ctx.command.command_id, ctx.action, countdown, fires_at),
                name=f"dome-power-{ctx.action}",
            )
            self._armed = _Armed(ctx.command.command_id, ctx.action, countdown, fires_at, task)
        log.info("power countdown armed", action=ctx.action, countdown_seconds=countdown)
        self._state.request_update()
        return DEFERRED

    async def _countdown(self, command_id: str, action: str, countdown: int, fires_at: str) -> None:
        try:
            delay = (parse_rfc3339(fires_at) - now_utc()).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            if not self._store.remote_enabled:
                await self._finish(
                    command_id,
                    "canceled",
                    None,
                    ProtocolError("POWER_CANCELED", "Remote control was disabled on the PC before the countdown ended"),
                )
                return
            armed = self._armed
            if armed is not None and armed.command_id == command_id:
                # From here on a cancel is too late: the flag is set and the OS call starts without any
                # intervening suspension point, so cancel() sees issuing=True or a finished countdown.
                armed.issuing = True
            try:
                await self._issue(action)
            except ProtocolError as exc:
                await self._finish(command_id, "failed", None, exc)
                return
            except Exception as exc:  # unexpected adapter failure
                log.error("power adapter raised", action=action, error=exc.__class__.__name__)
                await self._finish(command_id, "failed", None, ProtocolError("OS_ERROR", "Windows reported an error"))
                return
            if action in ABORTABLE_ACTIONS:
                self._issued = _Issued(command_id, action)
            await self._finish(
                command_id, "succeeded", {"accepted": True, "countdown_seconds": countdown, "fires_at": fires_at}, None
            )
        except asyncio.CancelledError:
            raise

    async def _issue(self, action: str) -> None:
        power = self._platform.power
        if action == "power.sleep":
            fut = asyncio.get_running_loop().run_in_executor(None, power.sleep)
            done, _pending = await asyncio.wait({fut}, timeout=SLEEP_GRACE_SECONDS)
            if done:
                fut.result()  # raises ProtocolError when Windows refused
            # still blocked after the grace period: the system is suspending → accepted
            return
        if action == "power.restart":
            await asyncio.to_thread(power.restart)
            return
        if action == "power.shutdown":
            await asyncio.to_thread(power.shutdown)
            return
        raise ProtocolError("UNKNOWN_ACTION", f"unknown power action {action}")

    async def _finish(
        self, command_id: str, state: str, result: dict[str, Any] | None, error: ProtocolError | None
    ) -> None:
        async with self._lock:
            if self._armed is not None and self._armed.command_id == command_id:
                self._armed = None
            self._store.clear_pending_power()
        self._state.request_update()
        if self._complete is not None:
            await self._complete(command_id, state, result, error)

    async def cancel(self, *, reason: str = "canceled from the phone") -> dict[str, Any]:
        """Cancel the pending countdown (if any). Returns the ``power_cancel_result``.

        * countdown still running → task canceled, command ends ``canceled``/``POWER_CANCELED``
        * OS call in progress (``issuing``) → ``{"canceled": false, action, command_id}``; the countdown's
          own completion reports what Windows did
        * restart/shutdown already accepted by Windows → ``AbortSystemShutdownW``; ``canceled`` is True
          only when Windows aborted it
        * nothing pending → ``{"canceled": false}``
        """
        armed = self._armed
        if armed is None:
            row = self._store.clear_pending_power()  # stale row without a task (should not happen after recovery)
            if row is not None:
                self._state.request_update()
                return {"canceled": True, "action": row.action, "command_id": row.command_id}
            return await self._abort_issued()
        if armed.issuing or armed.task.done():
            log.info("power cancel too late: the OS call is already being issued", action=armed.action)
            return {"canceled": False, "action": armed.action, "command_id": armed.command_id}
        armed.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await armed.task
        await self._finish(
            armed.command_id, "canceled", None, ProtocolError("POWER_CANCELED", f"The power action was {reason}")
        )
        log.info("power countdown canceled", action=armed.action)
        return {"canceled": True, "action": armed.action, "command_id": armed.command_id}

    async def _abort_issued(self) -> dict[str, Any]:
        issued = self._issued
        if issued is None:
            return {"canceled": False}
        try:
            aborted = await asyncio.to_thread(self._platform.power.abort_shutdown)
        except ProtocolError as exc:
            log.info("abort of an initiated shutdown refused", action=issued.action, code=exc.code)
            aborted = False
        except Exception as exc:  # noqa: BLE001 - adapter bug must not crash the command
            log.error("power adapter raised on abort", error=exc.__class__.__name__)
            aborted = False
        if not aborted:
            return {"canceled": False}
        self._issued = None
        log.info("initiated power action aborted by Windows", action=issued.action)
        return {"canceled": True, "action": issued.action, "command_id": issued.command_id}

    async def shutdown(self) -> None:
        """Agent is stopping: an armed countdown must not fire from a dead process."""
        armed = self._armed
        if armed is None:
            return
        outcome = await self.cancel(reason="canceled because the agent stopped")
        if not outcome["canceled"] and not armed.task.done():
            # Too late to cancel: let the OS call finish so the journal records the real outcome.
            with contextlib.suppress(BaseException):
                await armed.task


@handler("power.sleep")
@handler("power.restart")
@handler("power.shutdown")
async def power_action(ctx: ExecutionContext) -> Deferred:
    return await ctx.services.power.arm(ctx)


@handler("power.cancel")
async def power_cancel(ctx: ExecutionContext) -> dict[str, Any]:
    outcome = await ctx.services.power.cancel()
    if not outcome["canceled"] and "command_id" in outcome:
        # Something was pending but could not be stopped: the OS call is already under way.
        raise ActionFailed(
            "ACTION_UNAVAILABLE",
            "Too late to cancel: the PC is already carrying out the power action.",
            result=outcome,
        )
    return outcome
