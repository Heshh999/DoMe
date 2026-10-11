"""Agent-side bridge server: tracks connected browser instances and their YouTube tabs, and runs
request/response exchanges with the extension through the native host.

Every inbound frame is strict-parsed and schema-validated (``extension_to_agent``) before use;
every outbound frame is validated (``agent_to_extension``) before it is written. Tab titles and
video ids are untrusted display data and are never logged.

``bridge_hello_ack.protocol_version`` is NEGOTIATED (:func:`negotiate_protocol_version`), never the
agent's own newest version: the extension refuses an ack it does not speak and reconnects only slowly.
An op added in a later MINOR (:data:`OP_SINCE`) is sent only to an extension whose negotiated version
includes it; any other extension gets ``EXTENSION_UPDATE_REQUIRED`` before a frame is written.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dome_protocol import ProtocolError, negotiate_version, protocol_compatible

from .. import SUPPORTED_PROTOCOL_VERSIONS, __version__
from ..logsetup import get_logger
from . import ipc
from .framing import decode_frame, encode_frame, validate_outgoing

log = get_logger(__name__)

DEFAULT_REQUEST_TIMEOUT_MS = 8000
# bridge.schema.json ops added after 1.0 → the MINOR that added them (version.json rules.bridge_negotiation).
# Every op not listed here is a 1.0 op. tests/test_bridge.py checks this against the schema and actions.json.
OP_SINCE: dict[str, str] = {"search": "1.2", "open_result": "1.2"}


def negotiate_protocol_version(
    peer_versions: Iterable[str], supported: Iterable[str] = SUPPORTED_PROTOCOL_VERSIONS
) -> str | None:
    """The version a ``bridge_hello_ack`` announces (version.json ``rules.bridge_negotiation``, the shared
    :func:`dome_protocol.negotiate_version`): the highest MINOR both sides support within their highest
    common MAJOR — under the compatibility rule a side that supports a MINOR also accepts every lower
    one. None when no MAJOR is shared: the hello is refused as PROTOCOL_INCOMPATIBLE.

    A 1.0-only extension must get "1.0" from a 1.2 agent: it refuses any ack its own list does not reach
    (``protocolCompatible`` in browser-extension/src/shared/version.ts)."""
    return negotiate_version(peer_versions, supported)


def op_supported(op: str, negotiated: str) -> bool:
    """Whether an extension that negotiated ``negotiated`` speaks bridge op ``op`` (:data:`OP_SINCE`)."""
    return protocol_compatible(OP_SINCE.get(op, "1.0"), (negotiated,))


def scrub_tab(tab: dict[str, Any]) -> dict[str, Any]:
    """The tab as the agent keeps and republishes it: no title unless the page is a video page.

    A YouTube results page's title is the search query and other non-video pages are unrelated browsing
    history (rules.search_queries; the 1.2 extension already omits them). Dropping it here as well keeps an
    older or misbehaving extension from putting it into state frames, the relay's state cache and results."""
    if tab.get("context") == "other" and "title" in tab:
        tab = {k: v for k, v in tab.items() if k != "title"}
    return tab


@dataclass(slots=True)
class BrowserInstance:
    browser_instance_id: str
    browser: str
    extension_version: str
    profile_label: str
    protocol_version: str  # negotiated in bridge_hello_ack; ops newer than it must not be sent
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
        if self._ipc.start():
            log.info("bridge IPC listening", address=self._ipc.address)
            return
        # Windows: another process holds the pipe name. The IPC thread keeps retrying; until then no
        # browser can connect, so say so loudly instead of logging "listening".
        log.error(
            "bridge IPC NOT listening: the browser extension cannot connect until this is resolved",
            address=self._ipc.address,
            reason=self._ipc.unavailable_reason,
        )
        self._on_security_event(
            "bridge_ipc_unavailable", {"address": self._ipc.address, "reason": self._ipc.unavailable_reason}
        )

    @property
    def unavailable_reason(self) -> str:
        """Why no browser can connect right now (the IPC endpoint is not listening); "" when it is."""
        return self._ipc.unavailable_reason if self._ipc else "the browser bridge is not started"

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
        threading.Thread(
            target=self._reader, args=(connection,), name=f"dome-bridge-reader-{peer.pid}", daemon=True
        ).start()

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
        asyncio.run_coroutine_threadsafe(coro, self._loop).add_done_callback(_log_handler_failure)

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
        negotiated = negotiate_protocol_version(peer_versions)
        if negotiated is None:
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
            protocol_version=negotiated,
            conn=connection.conn,
            tabs=dict(previous.tabs) if previous is not None else {},
        )
        connection.instance = instance
        self._instances[instance_id] = instance
        ack = {"type": "bridge_hello_ack", "protocol_version": negotiated, "agent_version": __version__}
        if await self._send(connection, ack):
            log.info(
                "browser instance connected",
                browser_instance_id=instance_id,
                browser=instance.browser,
                extension_version=instance.extension_version,
                protocol_version=negotiated,
            )
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
                instance.tabs = {
                    int(t["tab_id"]): scrub_tab(t)
                    for t in tabs
                    if t.get("browser_instance_id") == instance.browser_instance_id
                }
        elif frame["event"] == "player_state":
            tab = frame.get("tab")
            if tab is not None and tab.get("browser_instance_id") == instance.browser_instance_id:
                instance.tabs[int(tab["tab_id"])] = scrub_tab(tab)
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
                    fut.set_exception(
                        ProtocolError("EXTENSION_DISCONNECTED", "The browser extension disconnected", retryable=True)
                    )
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

    def note_tab(self, tab: dict[str, Any], browser_instance_id: str) -> None:
        """Record a tab the extension reported in a ``bridge_response`` to a request sent to
        ``browser_instance_id``.

        The extension announces tab changes through a DEBOUNCED ``tabs_changed``, so the answer to an op that
        replaced the page (``search``: a new tab_token, maybe a new tab) can arrive first; without this the
        phone's next command for the tab it was just given would fail target resolution as TARGET_CHANGED.
        Like :meth:`_event`, a tab naming another browser instance is ignored: one connection's answer never
        rewrites another browser's tab records (their tab_token feeds target resolution)."""
        if tab.get("browser_instance_id") != browser_instance_id:
            return
        inst = self._instances.get(browser_instance_id)
        if inst is None or not isinstance(tab.get("tab_id"), int):
            return
        inst.tabs[tab["tab_id"]] = scrub_tab(tab)
        self._on_change()

    async def request(
        self,
        browser_instance_id: str,
        op: str,
        args: dict[str, Any],
        timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS,
        *,
        non_idempotent: bool = False,
    ) -> dict[str, Any]:
        """Send one ``bridge_request`` and await its ``bridge_response``.

        Before the frame has been written, every failure is ``EXTENSION_DISCONNECTED`` (retryable: the
        op never reached the browser), except an op the extension's negotiated version does not include
        (:data:`OP_SINCE`): ``EXTENSION_UPDATE_REQUIRED``, and nothing is written. Once the frame is on the
        wire, a lost answer (timeout, native host gone) is ``EXTENSION_DISCONNECTED`` for idempotent ops
        but ``OUTCOME_UNKNOWN`` (non-retryable) when ``non_idempotent`` is set — the browser may well
        have executed it."""
        instance = self._instances.get(browser_instance_id)
        if instance is None:
            raise ProtocolError("EXTENSION_DISCONNECTED", "That browser is not connected", retryable=True)
        if not op_supported(op, instance.protocol_version):
            log.info(
                "bridge op needs a newer extension",
                op=op,
                negotiated=instance.protocol_version,
                required=OP_SINCE.get(op, "1.0"),
            )
            raise ProtocolError(
                "EXTENSION_UPDATE_REQUIRED",
                "Update the DoMe browser extension on the PC to use this control.",
                detail={"negotiated": instance.protocol_version, "required": OP_SINCE.get(op, "1.0")},
            )
        assert self._loop is not None
        request_id = str(uuid.uuid4())
        timeout_ms = max(100, min(60000, int(timeout_ms)))
        frame = {
            "type": "bridge_request",
            "request_id": request_id,
            "op": op,
            "args": dict(args),
            "timeout_ms": timeout_ms,
        }
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
            if non_idempotent:
                raise ProtocolError(
                    "OUTCOME_UNKNOWN",
                    "The browser did not confirm the action in time; it may have executed.",
                    retryable=False,
                ) from None
            raise ProtocolError(
                "EXTENSION_DISCONNECTED", "The browser extension did not respond in time", retryable=True
            ) from None
        except ProtocolError as exc:
            if non_idempotent and exc.code == "EXTENSION_DISCONNECTED":
                raise ProtocolError(
                    "OUTCOME_UNKNOWN",
                    "The browser extension disconnected after the action was sent; it may have executed.",
                    retryable=False,
                ) from exc
            raise


def _log_handler_failure(fut: Any) -> None:
    """Frame handlers run as fire-and-forget futures on the loop: never lose their exceptions silently."""
    if fut.cancelled():
        return
    exc = fut.exception()
    if exc is not None:
        log.error("bridge frame handler failed", error=repr(exc)[:300])
