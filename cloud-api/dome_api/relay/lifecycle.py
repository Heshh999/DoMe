"""Background sweeper applying the in-flight deadline rule every few seconds."""

from __future__ import annotations

import asyncio
import contextlib

from dome_api.logging import get_logger
from dome_api.relay.manager import ConnectionManager

log = get_logger("dome_api.relay.sweeper")


class Sweeper:
    def __init__(self, manager: ConnectionManager, interval_seconds: float) -> None:
        self.manager = manager
        self.interval = interval_seconds
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        swept = await self.manager.startup_sweep()
        if swept:
            log.info("startup_sweep", rows=swept)
        self._task = asyncio.create_task(self._run(), name="relay-sweeper")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            try:
                n = await self.manager.sweep_deadlines()
                if n:
                    log.info("deadline_sweep", commands=n)
            except Exception:  # noqa: BLE001 - keep sweeping
                log.exception("deadline_sweep.failed")
