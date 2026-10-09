"""PC state aggregation → ``state`` frames (``relay-frames.schema.json#/$defs/pc_state``).

Emitted right after a ``grants_snapshot`` is applied, on change (debounced 500 ms) and every 30 s
(``rules.state_cache``). Media/tab titles inside the state are untrusted display data.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from dome_protocol import ProtocolError, load_schemas

from .logsetup import get_logger

if TYPE_CHECKING:
    from .bridge.server import BridgeServer
    from .input_session import InputSessionManager
    from .platform.protocol import PlatformSet
    from .store import Store

log = get_logger(__name__)

DEBOUNCE_SECONDS = 0.5
PERIODIC_SECONDS = 30.0

Emitter = Callable[[dict[str, Any]], Awaitable[bool]]


class StateAggregator:
    def __init__(
        self,
        store: Store,
        platform: PlatformSet,
        bridge: BridgeServer,
        *,
        debounce: float = DEBOUNCE_SECONDS,
        periodic: float = PERIODIC_SECONDS,
    ) -> None:
        self._store = store
        self._platform = platform
        self._bridge = bridge
        self._debounce = debounce
        self._periodic = periodic
        self._emit: Emitter | None = None
        self._debounce_task: asyncio.Task[None] | None = None
        self._periodic_task: asyncio.Task[None] | None = None
        self._last: dict[str, Any] | None = None
        self.session_locked_override: bool | None = None  # tests / fake platform
        self.emitted = 0
        self.input: InputSessionManager | None = None  # bound by the agent: foreground_app / input_session / input_restricted

    # ----- lifecycle --------------------------------------------------------------------------------
    def start(self, emit: Emitter) -> None:
        self._emit = emit
        if self._periodic_task is None or self._periodic_task.done():
            self._periodic_task = asyncio.get_running_loop().create_task(
                self._periodic_loop(), name="dome-state-periodic"
            )

    async def stop(self) -> None:
        for task in (self._debounce_task, self._periodic_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._debounce_task = None
        self._periodic_task = None

    # ----- state --------------------------------------------------------------------------------------
    async def session_locked(self) -> bool:
        if self.session_locked_override is not None:
            return self.session_locked_override
        try:
            return bool(await asyncio.to_thread(self._platform.session.is_locked))
        except Exception:
            return False

    async def snapshot(self) -> dict[str, Any]:
        state: dict[str, Any] = {
            "remote_enabled": self._store.remote_enabled,
            "session_locked": await self.session_locked(),
            "media_while_locked": self._store.media_while_locked,
            "extension_connected": self._bridge.connected,
            "platform": self._platform.reported_platform,
        }
        if self._platform.supports_windows_actions:
            try:
                vol = await asyncio.to_thread(self._platform.volume.get)
                state["volume"] = vol.as_result()
            except ProtocolError:
                pass
            except Exception as exc:
                log.debug("volume read failed", error=exc.__class__.__name__)
            try:
                sessions = await asyncio.to_thread(self._platform.media.list_sessions)
                state["media_sessions"] = [s.as_result() for s in sessions[:16]]
            except ProtocolError:
                pass
            except Exception as exc:
                log.debug("media sessions read failed", error=exc.__class__.__name__)
        instances = self._bridge.instances()
        state["browser_instances"] = [i.summary() for i in instances[:8]]
        state["youtube_tabs"] = self._bridge.all_tabs()
        pending = self._store.get_pending_power()
        state["pending_power_action"] = (
            {"action": pending.action, "command_id": pending.command_id, "fires_at": pending.fires_at}
            if pending
            else None
        )
        if self.input is not None:
            try:
                state.update(await self.input.state_fields())
            except Exception as exc:  # noqa: BLE001 - never lose a state frame over a foreground probe
                log.debug("input state fields failed", error=exc.__class__.__name__)
        load_schemas().validate_def("relay-frames", "pc_state", state)
        self._last = state
        return state

    @property
    def last(self) -> dict[str, Any] | None:
        return self._last

    # ----- emission -----------------------------------------------------------------------------------
    async def emit_now(self) -> bool:
        if self._emit is None:
            return False
        try:
            state = await self.snapshot()
        except ProtocolError as exc:
            log.error("state frame failed schema validation; not sent", message=exc.message)
            return False
        ok = await self._emit(state)
        if ok:
            self.emitted += 1
        return ok

    def request_update(self) -> None:
        """Schedule a debounced state frame (safe to call often)."""
        if self._emit is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._debounce_task is not None and not self._debounce_task.done():
            return
        self._debounce_task = loop.create_task(self._debounced(), name="dome-state-debounce")

    async def _debounced(self) -> None:
        await asyncio.sleep(self._debounce)
        await self.emit_now()

    async def _periodic_loop(self) -> None:
        while True:
            await asyncio.sleep(self._periodic)
            await self.emit_now()
