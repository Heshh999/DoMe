"""Agent-side bridge server: tracks connected browser instances and their YouTube tabs, and runs
request/response exchanges with the extension through the native host.

Every inbound frame is strict-parsed and schema-validated (``extension_to_agent``) before use;
every outbound frame is validated (``agent_to_extension``) before it is written. Tab titles and
video ids are untrusted display data and are never logged.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dome_protocol import ProtocolError, load_registry
from dome_protocol.commands import protocol_compatible

from .. import SUPPORTED_PROTOCOL_VERSIONS, __version__
from ..logsetup import get_logger
from . import ipc
from .framing import decode_frame, encode_frame, validate_outgoing

log = get_logger(__name__)

DEFAULT_REQUEST_TIMEOUT_MS = 8000


@dataclass(slots=True)
class BrowserInstance:
    browser_instance_id: str
    browser: str
    extension_version: str
    profile_label: str
    conn: ipc.FrameConnection
    tabs: dict[int, dict[str, Any]] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {"browser_instance_id": self.browser_instance_id, "browser": self.browser}
        if self.profile_label:
            out["profile_label"] = self.profile_label[:64]
        return out


class _Connection:
    """One native-host connection; becomes a BrowserInstance after a valid bridge_hello."""

    def __init__(self, conn: ipc.FrameConnection, peer: ipc.PeerInfo) -> None:
        self.conn = conn
        self.peer = peer
        self.instance: BrowserInstance | None = None


class BridgeServer:
    def __init__(
        self,
        state_dir: Path,
        *,
        on_change: Callable[[], None] | None = None,
        on_security_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self._state_dir = state_dir
        self._on_change = on_change or (lambda: None)
        self._on_security_event = on_security_event or (lambda kind, detail: None)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ipc: ipc.IpcServer | None = None
        self._instances: dict[str, BrowserInstance] = {}
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._connections: set[_Connection] = set()
        self._lock = threading.Lock()

    # ----- lifecycle --------------------------------------------------------------------------------
    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._ipc = ipc.IpcServer(self._state_dir, self._accepted, self._identity_mismatch)
        self._ipc.start()
        log.info("bridge IPC listening", address=self._ipc.address)

    async def stop(self) -> None:
        if self._ipc is not None:
            await asyncio.to_thread(self._ipc.stop)
        for c in list(self._connections):
            c.conn.close()
        self._instances.clear()
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ProtocolError("EXTENSION_DISCONNECTED", "bridge stopped", retryable=True))
        self._pending.clear()

    @property
    def address(self) -> str:
        return self._ipc.address if self._ipc else ""

    # ----- callbacks from the IPC thread -------------------------------------------------------------
    def _identity_mismatch(self, peer: ipc.PeerInfo) -> None:
        assert self._loop is not None
        self._loop.call_soon_threadsafe(
            self._on_security_event,
            "bridge_ipc_identity_mismatch",
            {"peer_pid": peer.pid, "peer_identity": peer.identity, "peer_session": peer.session},
        )

    def _accepted(self, conn: ipc.FrameConnection, peer: ipc.PeerInfo) -> None:
        connection = _Connection(conn, peer)
        with self._lock:
            self._connections.add(connection)
        threading.Thread(target=self._reader, args=(connection,), name=f"dome-bridge-reader-{peer.pid}", daemon=True).start()

    def _reader(self, connection: _Connection) -> None:
        assert self._loop is not None
        try:
            while True:
                try:
                    raw = connection.conn.read_frame()
                except ProtocolError as exc:
                    self._dispatch(self._send_error(connection, exc.code, exc.message))
                    if exc.code == "PAYLOAD_TOO_LARGE":
                        break
                    continue
                except OSError:
                    break
                if raw is None:
                    break
                try:
                    frame = decode_frame(raw, "extension_to_agent")
                except ProtocolError as exc:
                    log.warning("bridge frame rejected", code=exc.code, message=exc.message)
                    self._dispatch(self._send_error(connection, exc.code, exc.message))
                    continue
                self._dispatch(self._handle_frame(connection, frame))
        finally:
            self._dispatch(self._disconnected(connection))

    def _dispatch(self, coro: Any) -> None:
        assert self._loop is not None
        if self._loop.is_closed():
            coro.close()
            return
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    # ----- frame handling (event loop) ---------------------------------------------------------------
    async def _send(self, connection: _Connection, frame: dict[str, Any]) -> bool:
        validate_outgoing(frame, "agent_to_extension")
        try:
            await asyncio.to_thread(connection.conn.write_frame, encode_frame(frame))
            return True
        except (OSError, ProtocolError) as exc:
            log.warning("bridge write failed", error=exc.__class__.__name__)
            return False

    async def _send_error(self, connection: _Connection, code: str, message: str, **detail: Any) -> None:
        frame: dict[str, Any] = {"type": "bridge_error", "error": {"code": code, "message": message[:512]}}
        if detail:
            frame["error"]["detail"] = detail
        await self._send(connection, frame)

    async def _handle_frame(self, connection: _Connection, frame: dict[str, Any]) -> None:
        kind = frame["type"]
        if connection.instance is None:
            if kind != "bridge_hello":
                await self._send_error(connection, "MALFORMED_MESSAGE", "bridge_hello must be the first frame")
                connection.conn.close()
                return
            await self._hello(connection, frame)
            return
        instance = connection.instance
        if kind == "bridge_hello":
            await self._hello(connection, frame)
        elif kind == "bridge_response":
            self._response(frame)
        elif kind == "bridge_event":
            self._event(instance, frame)
        elif kind == "bridge_error":
            ref = frame.get("ref_request_id")
            err = frame["error"]
            log.warning("bridge_error from extension", code=err["code"], ref_request_id=ref)
            if ref and ref in self._pending:
                fut = self._pending.pop(ref)
                if not fut.done():
                    fut.set_exception(ProtocolError(err["code"], err["message"]))

    async def _hello(self, connection: _Connection, frame: dict[str, Any]) -> None:
        peer_versions = tuple(frame["protocol_versions"])
        if not any(protocol_compatible(v, SUPPORTED_PROTOCOL_VERSIONS) for v in peer_versions):
            await self._send_error(
                connection,
                "PROTOCOL_INCOMPATIBLE",
                "The DoMe extension and agent speak incompatible protocol versions.",
                peer=list(peer_versions),
                supported=list(SUPPORTED_PROTOCOL_VERSIONS),
            )
            connection.conn.close()
            return
        instance_id = frame["browser_instance_id"]
        previous = self._instances.get(instance_id)
        if previous is not None and previous.conn is not connection.conn:
            # Service worker restarted → new native port for the same profile; the old one is stale.
            previous.conn.close()
        instance = BrowserInstance(
            browser_instance_id=instance_id,
            browser=frame["browser"],
            extension_version=frame["extension_version"],
            profile_label=frame.get("profile_label", ""),
            conn=connection.conn,
            tabs=dict(previous.tabs) if previous is not None else {},
        )
        connection.instance = instance
        self._instances[instance_id] = instance
        ack = {"type": "bridge_hello_ack", "protocol_version": load_registry().protocol_version, "agent_version": __version__}
        if await self._send(connection, ack):
            log.info("browser instance connected", browser_instance_id=instance_id, browser=instance.browser, extension_version=instance.extension_version)
            self._on_change()

    def _response(self, frame: dict[str, Any]) -> None:
        fut = self._pending.pop(frame["request_id"], None)
        if fut is None or fut.done():
            log.info("late or unknown bridge_response dropped", request_id=frame["request_id"])
            return
        if frame["ok"]:
            fut.set_result(frame.get("result", {}))
        else:
            err = frame.get("error") or {"code": "INTERNAL", "message": "extension reported failure"}
            fut.set_exception(ProtocolError(err["code"], err["message"]))

    def _event(self, instance: BrowserInstance, frame: dict[str, Any]) -> None:
        if frame["event"] == "tabs_changed":
            tabs = frame.get("tabs")
            if tabs is not None:
                instance.tabs = {int(t["tab_id"]): t for t in tabs if t.get("browser_instance_id") == instance.browser_instance_id}
        elif frame["event"] == "player_state":
            tab = frame.get("tab")
            if tab is not None and tab.get("browser_instance_id") == instance.browser_instance_id:
                instance.tabs[int(tab["tab_id"])] = tab
        self._on_change()

    async def _disconnected(self, connection: _Connection) -> None:
        with self._lock:
            self._connections.discard(connection)
        connection.conn.close()
        instance = connection.instance
        if instance is not None and self._instances.get(instance.browser_instance_id) is instance:
            del self._instances[instance.browser_instance_id]
            log.info("browser instance disconnected", browser_instance_id=instance.browser_instance_id)
            for rid, fut in list(self._pending.items()):
                if getattr(fut, "_dome_instance", None) == instance.browser_instance_id and not fut.done():
                    fut.set_exception(ProtocolError("EXTENSION_DISCONNECTED", "The browser extension disconnected", retryable=True))
                    self._pending.pop(rid, None)
            self._on_change()

    # ----- public API ---------------------------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return bool(self._instances)

    def instances(self) -> list[BrowserInstance]:
        return list(self._instances.values())

    def get_instance(self, browser_instance_id: str) -> BrowserInstance | None:
        return self._instances.get(browser_instance_id)

    def all_tabs(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for inst in self._instances.values():
            out.extend(inst.tabs.values())
        return out[:32]

    def find_tab(self, browser_instance_id: str, tab_id: int) -> dict[str, Any] | None:
        inst = self._instances.get(browser_instance_id)
        if inst is None:
            return None
        return inst.tabs.get(int(tab_id))

    async def request(self, browser_instance_id: str, op: str, args: dict[str, Any], timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS) -> dict[str, Any]:
        instance = self._instances.get(browser_instance_id)
        if instance is None:
            raise ProtocolError("EXTENSION_DISCONNECTED", "That browser is not connected", retryable=True)
        assert self._loop is not None
        request_id = str(uuid.uuid4())
        timeout_ms = max(100, min(60000, int(timeout_ms)))
        frame = {"type": "bridge_request", "request_id": request_id, "op": op, "args": dict(args), "timeout_ms": timeout_ms}
        fut: asyncio.Future[dict[str, Any]] = self._loop.create_future()
        fut._dome_instance = browser_instance_id  # type: ignore[attr-defined]
        self._pending[request_id] = fut
        connection = next((c for c in self._connections if c.instance is instance), None)
        if connection is None or not await self._send(connection, frame):
            self._pending.pop(request_id, None)
            raise ProtocolError("EXTENSION_DISCONNECTED", "The browser extension disconnected", retryable=True)
        try:
            return await asyncio.wait_for(fut, timeout_ms / 1000)
        except TimeoutError:
            self._pending.pop(request_id, None)
            raise ProtocolError("EXTENSION_DISCONNECTED", "The browser extension did not respond in time", retryable=True) from None
