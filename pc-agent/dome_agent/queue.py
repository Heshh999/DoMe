"""Per-PC serialized executor with coalescing, timeouts and lifecycle emission (design → "Execution").

One asyncio worker, queue depth 16. Coalescing group = (``coalesce`` key, canonical target JSON or
``""``) across all controllers: enqueuing a new command in a group terminates earlier *queued, not yet
executing* commands in that group as ``canceled`` / ``COMMAND_SUPERSEDED``. Each command:
``ack{executing}`` + durable journal state ``executing`` → handler under ``asyncio.wait_for(timeout_ms)``
→ ``result``. A timeout after a non-idempotent side effect was issued is ``outcome_unknown``; the
agent never retries anything. Results are validated against the action's result schema before they
are emitted.

:class:`Emitter` is the single path for acks/results: journal first (durable), then send; a result
that could not be written to the socket stays ``sent = 0`` and is re-sent on reconnect
(``rules.late_results``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from dome_protocol import ProtocolError, load_registry
from dome_protocol.commands import VerifiedCommand

from .actions import ActionFailed, Deferred, handler_for
from .actions.context import AgentServices, ExecutionContext
from .frames import ack_frame, result_frame
from .logsetup import get_logger
from .store import Store

log = get_logger(__name__)

QUEUE_DEPTH = 16
Sender = Callable[[dict[str, Any]], Awaitable[bool]]
Precheck = Callable[[VerifiedCommand], Awaitable[ProtocolError | None]]


class Emitter:
    """Journal-then-send for acks and results."""

    def __init__(self, store: Store, send: Sender) -> None:
        self._store = store
        self._send = send

    async def send_raw(self, frame: dict[str, Any]) -> bool:
        return await self._send(frame)

    async def ack(self, command_id: str, state: str, *, journal: bool = True) -> None:
        frame = ack_frame(command_id, state)
        if journal:
            self._store.journal_set_state(command_id, state, frame=frame)
        await self._send(frame)

    async def result(
        self,
        command_id: str,
        state: str,
        *,
        result: dict[str, Any] | None = None,
        error: ProtocolError | None = None,
        warning: str | None = None,
        duration_ms: int = 0,
        journal: bool = True,
        send: bool = True,
    ) -> dict[str, Any]:
        frame = result_frame(command_id, state, result=result, error=error, warning=warning, duration_ms=duration_ms)
        if journal:
            self._store.journal_set_state(command_id, state, frame=frame, error_code=error.code if error else None, sent=not send)
        if send:
            ok = await self._send(frame)
            if journal and ok:
                self._store.journal_mark_sent(command_id, True)
        return frame


@dataclass(slots=True)
class _Entry:
    command: VerifiedCommand
    enqueued_at: float
    group: tuple[str, str] | None
    canceled: bool = False


@dataclass(slots=True)
class _Running:
    command: VerifiedCommand
    ctx: ExecutionContext
    task: asyncio.Task[Any] | None = None
    started: float = field(default_factory=time.monotonic)


def coalescing_group(vc: VerifiedCommand) -> tuple[str, str] | None:
    if vc.spec.coalesce is None:
        return None
    target_text = "" if vc.target is None else json.dumps(vc.target, sort_keys=True, separators=(",", ":"))
    return (vc.spec.coalesce, target_text)


class Executor:
    def __init__(self, services: AgentServices, emitter: Emitter, *, precheck: Precheck | None = None, depth: int = QUEUE_DEPTH) -> None:
        self._s = services
        self._emit = emitter
        self._precheck = precheck
        self._depth = depth
        self._queue: list[_Entry] = []
        self._wake = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._running: _Running | None = None
        self._deferred: dict[str, VerifiedCommand] = {}  # command_id → command whose result arrives via complete()
        self._registry = load_registry()

    # ----- lifecycle --------------------------------------------------------------------------------------
    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.get_running_loop().create_task(self._run(), name="dome-executor")

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
            self._worker = None
        if self._running is not None and self._running.task is not None and not self._running.task.done():
            self._running.task.cancel()

    # ----- queueing ---------------------------------------------------------------------------------------
    @property
    def queued_ids(self) -> list[str]:
        return [e.command.command_id for e in self._queue]

    @property
    def running_id(self) -> str | None:
        return self._running.command.command_id if self._running else None

    @property
    def deferred_ids(self) -> list[str]:
        return list(self._deferred)

    def in_flight_ids(self) -> list[str]:
        ids = self.queued_ids + list(self._deferred)
        if self._running is not None:
            ids.append(self._running.command.command_id)
        return ids

    async def enqueue(self, vc: VerifiedCommand) -> None:
        """Queue an authorized command. Raises QUEUE_FULL (caller journals the rejection)."""
        group = coalescing_group(vc)
        if group is not None:
            superseded = [e for e in self._queue if e.group == group]
            for entry in superseded:
                self._queue.remove(entry)
                await self._emit.result(
                    entry.command.command_id,
                    "canceled",
                    error=self._registry.make_error("COMMAND_SUPERSEDED"),
                    duration_ms=_ms_since(entry.enqueued_at),
                )
        if len(self._queue) >= self._depth:
            raise self._registry.make_error("QUEUE_FULL")
        self._queue.append(_Entry(vc, time.monotonic(), group))
        self._wake.set()

    async def cancel_queued(self, command_id: str, error: ProtocolError | None = None) -> bool:
        for entry in list(self._queue):
            if entry.command.command_id == command_id:
                self._queue.remove(entry)
                await self._emit.result(command_id, "canceled", error=error, duration_ms=_ms_since(entry.enqueued_at))
                return True
        return False

    async def cancel_for_controller(self, controller_id: str, error: ProtocolError) -> list[str]:
        """Revocation: terminate queued and running commands of one controller."""
        canceled: list[str] = []
        for entry in list(self._queue):
            if entry.command.controller_id == controller_id:
                self._queue.remove(entry)
                await self._emit.result(entry.command.command_id, "canceled", error=error, duration_ms=_ms_since(entry.enqueued_at))
                canceled.append(entry.command.command_id)
        running = self._running
        if running is not None and running.command.controller_id == controller_id and running.task is not None and not running.task.done():
            running.task.cancel()
            canceled.append(running.command.command_id)
        for command_id, vc in list(self._deferred.items()):
            if vc.controller_id == controller_id:
                await self._s.power.cancel(reason="canceled because the phone's access was revoked")
                canceled.append(command_id)
        return canceled

    async def fail_queued_offline(self) -> list[str]:
        """Socket dropped: a stale queue is never replayed. The relay already told the controller."""
        failed: list[str] = []
        error = self._registry.make_error("PC_OFFLINE", "The PC lost its connection before running this.")
        for entry in list(self._queue):
            self._queue.remove(entry)
            await self._emit.result(entry.command.command_id, "failed", error=error, send=False)
            failed.append(entry.command.command_id)
        return failed

    # ----- deferred completion (power countdowns) -----------------------------------------------------
    async def complete(self, command_id: str, state: str, result: dict[str, Any] | None, error: ProtocolError | None) -> None:
        vc = self._deferred.pop(command_id, None)
        if vc is None:
            log.warning("completion for a command that is not deferred; dropped", command_id=command_id, state=state)
            return
        row = self._s.store.journal_get(command_id)
        started = row.created_at if row else None
        duration = 0
        if started:
            from dome_protocol import now_utc, parse_rfc3339

            duration = max(0, int((now_utc() - parse_rfc3339(started)).total_seconds() * 1000))
        if state == "succeeded" and result is not None:
            try:
                result = self._registry.validate_result(vc.spec.name, result)
            except ProtocolError as exc:
                log.error("deferred result failed schema validation", action=vc.spec.name, message=exc.message)
                state, result, error = "failed", None, self._registry.make_error("INTERNAL", "The PC produced an invalid result.")
        await self._emit.result(command_id, state, result=result, error=error, duration_ms=duration)

    # ----- worker ------------------------------------------------------------------------------------------
    async def _run(self) -> None:
        while True:
            if not self._queue:
                self._wake.clear()
                await self._wake.wait()
                continue
            entry = self._queue.pop(0)
            await self._execute(entry)

    async def _execute(self, entry: _Entry) -> None:
        vc = entry.command
        ctx = ExecutionContext(self._s, vc)
        running = _Running(vc, ctx)
        self._running = running
        try:
            if self._precheck is not None:
                blocker = await self._precheck(vc)
                if blocker is not None:
                    await self._emit.result(vc.command_id, "failed", error=blocker, duration_ms=_ms_since(entry.enqueued_at))
                    return
            await self._emit.ack(vc.command_id, "executing")  # durable before the OS call
            handler = handler_for(vc.spec.name)
            task: asyncio.Task[Any] = asyncio.get_running_loop().create_task(handler(ctx), name=f"dome-action-{vc.spec.name}")
            running.task = task
            try:
                outcome = await asyncio.wait_for(asyncio.shield(task), vc.spec.timeout_ms / 1000)
            except TimeoutError:
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
                await self._timeout(vc, ctx, entry)
                return
            except asyncio.CancelledError:
                if not (task.cancelled() or task.done()):
                    task.cancel()  # the worker itself is being stopped
                    raise
                # the handler task was canceled (controller revoked / agent stopping)
                if ctx.side_effect_issued and not vc.spec.idempotent:
                    await self._emit.result(
                        vc.command_id,
                        "outcome_unknown",
                        error=self._registry.make_error("OUTCOME_UNKNOWN"),
                        warning="The action was interrupted after it was issued; check the PC's state before retrying.",
                        duration_ms=_ms_since(entry.enqueued_at),
                    )
                else:
                    await self._emit.result(vc.command_id, "canceled", error=self._registry.make_error("CONTROLLER_REVOKED"), duration_ms=_ms_since(entry.enqueued_at))
                return
            if isinstance(outcome, Deferred):
                self._deferred[vc.command_id] = vc
                return
            try:
                validated = self._registry.validate_result(vc.spec.name, outcome)
            except ProtocolError as exc:
                log.error("handler result failed schema validation; reported as INTERNAL", action=vc.spec.name, message=exc.message)
                await self._emit.result(vc.command_id, "failed", error=self._registry.make_error("INTERNAL", "The PC produced an invalid result."), duration_ms=_ms_since(entry.enqueued_at))
                return
            await self._emit.result(vc.command_id, "succeeded", result=validated, duration_ms=_ms_since(entry.enqueued_at))
        except ActionFailed as exc:
            await self._emit.result(vc.command_id, "failed", result=_safe_result(self._registry, vc, exc.result), error=exc, duration_ms=_ms_since(entry.enqueued_at))
        except ProtocolError as exc:
            await self._emit.result(vc.command_id, "failed", error=exc, duration_ms=_ms_since(entry.enqueued_at))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # a bug in a handler must still terminate the command
            log.exception("handler crashed", action=vc.spec.name, error=exc.__class__.__name__)
            await self._emit.result(vc.command_id, "failed", error=self._registry.make_error("INTERNAL", "The PC hit an internal error running this."), duration_ms=_ms_since(entry.enqueued_at))
        finally:
            if self._running is running:
                self._running = None

    async def _timeout(self, vc: VerifiedCommand, ctx: ExecutionContext, entry: _Entry) -> None:
        seconds = vc.spec.timeout_ms / 1000
        if ctx.side_effect_issued and not vc.spec.idempotent:
            await self._emit.result(
                vc.command_id,
                "outcome_unknown",
                error=self._registry.make_error("OUTCOME_UNKNOWN", f"The PC issued the action but could not confirm the outcome within {seconds:g} s."),
                warning="The action may have executed. Check the PC's current state before retrying.",
                duration_ms=_ms_since(entry.enqueued_at),
            )
            return
        error = ctx.best_known_error or self._registry.make_error("ACTION_UNAVAILABLE", f"The action did not complete within {seconds:g} s.", retryable=vc.spec.idempotent)
        await self._emit.result(vc.command_id, "failed", error=error, duration_ms=_ms_since(entry.enqueued_at))


def _safe_result(registry: Any, vc: VerifiedCommand, result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    try:
        return dict(registry.validate_result(vc.spec.name, result))
    except ProtocolError:
        return None


def _ms_since(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))
