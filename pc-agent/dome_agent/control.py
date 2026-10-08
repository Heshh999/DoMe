"""Local control channel between the CLI / tray helpers and the running agent.

Same transport and identity rule as the browser bridge (:mod:`dome_agent.bridge.ipc`, kind
``control``): a per-user named pipe on Windows (DACL + client SID/session check) or a 0600 Unix
socket whose peer uid must be ours. Frames are length-prefixed JSON requests
``{"op": ..., "args": {...}}`` answered by ``{"ok": true, "result": ...}`` or
``{"ok": false, "error": {"code", "message"}}``. The pairing code is returned ONLY by ``pair_start``
to the local caller and is never persisted or logged.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from dome_protocol import ProtocolError, dumps_compact, loads_strict
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .bridge import ipc
from .bridge.framing import MAX_FRAME_BYTES, encode_frame
from .logsetup import get_logger

log = get_logger(__name__)

Op = Callable[[dict[str, Any]], Coroutine[Any, Any, Any]]
REQUEST_TIMEOUT = 30.0


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: str = Field(min_length=1, max_length=64, pattern=r"^[a-z_]+$")
    args: dict[str, Any] = Field(default_factory=dict)


class ControlError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ControlServer:
    def __init__(self, state_dir: Path, ops: dict[str, Op], on_security_event: Callable[[str, dict[str, Any]], None]) -> None:
        self._state_dir = state_dir
        self._ops = ops
        self._on_security_event = on_security_event
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ipc: ipc.IpcServer | None = None

    @property
    def address(self) -> str:
        return self._ipc.address if self._ipc else ""

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._ipc = ipc.IpcServer(self._state_dir, self._accepted, self._mismatch, kind="control")
        self._ipc.start()
        log.info("control channel listening", address=self._ipc.address)

    async def stop(self) -> None:
        if self._ipc is not None:
            await asyncio.to_thread(self._ipc.stop)
            self._ipc = None

    def _mismatch(self, peer: ipc.PeerInfo) -> None:
        assert self._loop is not None
        self._loop.call_soon_threadsafe(self._on_security_event, "control_ipc_identity_mismatch", {"peer_pid": peer.pid, "peer_identity": peer.identity, "peer_session": peer.session})

    def _accepted(self, conn: ipc.FrameConnection, peer: ipc.PeerInfo) -> None:
        threading.Thread(target=self._serve, args=(conn,), name=f"dome-control-{peer.pid}", daemon=True).start()

    def _serve(self, conn: ipc.FrameConnection) -> None:
        assert self._loop is not None
        try:
            while True:
                try:
                    raw = conn.read_frame()
                except (ProtocolError, OSError):
                    break
                if raw is None:
                    break
                response = self._handle(raw)
                try:
                    conn.write_frame(encode_frame(response))
                except (OSError, ProtocolError):
                    break
        finally:
            conn.close()

    def _handle(self, raw: bytes) -> dict[str, Any]:
        assert self._loop is not None
        try:
            parsed = loads_strict(raw, max_bytes=MAX_FRAME_BYTES, require_object=True)
            request = ControlRequest.model_validate(parsed)
        except (ProtocolError, ValidationError) as exc:
            return {"ok": False, "error": {"code": "MALFORMED_MESSAGE", "message": str(exc)[:300]}}
        op = self._ops.get(request.op)
        if op is None:
            return {"ok": False, "error": {"code": "UNKNOWN_OP", "message": f"unknown control op {request.op}"}}
        future: Any = asyncio.run_coroutine_threadsafe(op(request.args), self._loop)
        try:
            result = future.result(timeout=REQUEST_TIMEOUT)
        except ProtocolError as exc:
            return {"ok": False, "error": {"code": exc.code, "message": exc.message}}
        except ControlError as exc:
            return {"ok": False, "error": {"code": exc.code, "message": exc.message}}
        except TimeoutError:
            future.cancel()
            return {"ok": False, "error": {"code": "TIMEOUT", "message": "the agent did not answer in time"}}
        except Exception as exc:  # noqa: BLE001 - report, never crash the agent
            log.exception("control op failed", op=request.op, error=exc.__class__.__name__)
            return {"ok": False, "error": {"code": "INTERNAL", "message": exc.__class__.__name__}}
        return {"ok": True, "result": result}


class ControlClient:
    """Synchronous client used by the CLI."""

    def __init__(self, state_dir: Path, timeout: float = 5.0) -> None:
        self._state_dir = state_dir
        self._timeout = timeout

    def call(self, op: str, **args: Any) -> Any:
        try:
            conn = ipc.connect(self._state_dir, timeout=self._timeout, kind="control")
        except (OSError, ConnectionRefusedError) as exc:
            raise ControlError("AGENT_NOT_RUNNING", "The DoMe agent is not running (start it with `dome-agent run`).") from exc
        try:
            conn.write_frame(encode_frame({"op": op, "args": args}))
            raw = conn.read_frame()
        finally:
            conn.close()
        if raw is None:
            raise ControlError("AGENT_DISCONNECTED", "The agent closed the control connection.")
        response = loads_strict(raw, max_bytes=MAX_FRAME_BYTES, require_object=True)
        if not isinstance(response, dict):
            raise ControlError("MALFORMED_MESSAGE", "bad control response")
        if response.get("ok") is True:
            return response.get("result")
        error = response.get("error") or {}
        raise ControlError(str(error.get("code", "INTERNAL")), str(error.get("message", "unknown error")))

    def is_running(self) -> bool:
        try:
            self.call("ping")
            return True
        except ControlError:
            return False


def encode_control(frame: dict[str, Any]) -> bytes:
    return encode_frame(frame)


__all__ = ["ControlClient", "ControlError", "ControlRequest", "ControlServer", "dumps_compact"]
