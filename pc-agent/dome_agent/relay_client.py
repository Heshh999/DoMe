"""Outbound WebSocket client to the managed relay (``/ws/agent``).

Sequence per connection: fetch/refresh the PC access token (``POST /v1/agent/token``, renewed whenever
less than 5 minutes remain) → open ``wss://…/ws/agent`` with ``Authorization: Bearer`` (no Origin, no
query-string token) → ``hello`` → ``hello_ack`` (``pc_id`` must equal the stored identity, else hard
stop + re-link prompt) → hand every further validated frame to the :class:`RelayHandler` (the agent
applies ``grants_snapshot`` before accepting commands). Application-level ``ping`` every 25 s; a
socket silent for two ping periods is closed and re-opened.

Reconnect: exponential backoff 1 s → 60 s with full jitter. Close code 4001 (superseded by another
agent instance) stops automatic reconnects until the user asks (tray "Reconnect"); 4003 and a
rejected credential (``POST /v1/agent/token`` → 401/403) stop reconnecting and require re-linking; an
invalid relay URL stops with ``configuration_error`` (credential kept); 4008 refreshes the token first.
Every outbound frame is validated against ``agent_to_relay`` before it is written; every inbound
frame is strict-parsed and validated against ``relay_to_agent`` before it is used.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import websockets
from dome_protocol import ProtocolError, load_schemas, loads_strict
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, InvalidURI

from . import __version__
from .api import ApiClient, ApiError
from .frames import error_frame, hello_frame, ping_frame, pong_frame
from .logsetup import get_logger

log = get_logger(__name__)

MAX_FRAME_BYTES = 65536
PING_INTERVAL = 25.0
SILENCE_LIMIT = PING_INTERVAL * 2 + 5
HELLO_TIMEOUT = 15.0
TOKEN_MIN_REMAINING = 300.0
BACKOFF_BASE = 1.0
BACKOFF_CAP = 60.0

CLOSE_FRAME_TOO_LARGE = 1009
CLOSE_PROTOCOL_ERROR = 4000
CLOSE_SUPERSEDED = 4001
CLOSE_REVOKED = 4003
CLOSE_AUTH_REQUIRED = 4008

ConnectionState = Literal["offline", "connecting", "connected", "reconnecting", "superseded", "stopped"]
StopReason = Literal[
    "identity_mismatch",
    "credential_rejected",  # POST /v1/agent/token answered 401/403: the service no longer knows this PC
    "revoked",
    "unauthorized",
    "superseded",
    "configuration_error",  # the relay URL itself is invalid: a local problem, the credential stays
    "requested",
]


class RelayHandler(Protocol):
    async def on_connected(self, hello_ack: dict[str, Any]) -> None: ...

    async def on_frame(self, frame: dict[str, Any]) -> None: ...

    async def on_disconnected(self, reason: str) -> None: ...

    async def on_stopped(self, reason: StopReason) -> None:
        """Automatic reconnection ended (identity mismatch, credential rejected, 4001/4003)."""
        ...

    def on_state_change(self, state: ConnectionState) -> None: ...


@dataclass(slots=True)
class _Token:
    value: str
    expires_at: float  # monotonic


class TokenManager:
    def __init__(self, api: ApiClient, credential_provider: Callable[[], str | None]) -> None:
        self._api = api
        self._credential = credential_provider
        self._token: _Token | None = None

    def invalidate(self) -> None:
        self._token = None

    async def get(self, *, min_remaining: float = TOKEN_MIN_REMAINING) -> str:
        tok = self._token
        if tok is not None and tok.expires_at - time.monotonic() > min_remaining:
            return tok.value
        credential = self._credential()
        if credential is None:
            raise ApiError(401, "UNAUTHORIZED", "this PC is not linked")
        fresh = await self._api.token(credential)
        self._token = _Token(fresh.access_token, time.monotonic() + fresh.expires_in)
        return fresh.access_token


Connector = Callable[[str, dict[str, str]], Awaitable[ClientConnection]]


async def _default_connect(url: str, headers: dict[str, str]) -> ClientConnection:
    return await connect(
        url,
        additional_headers=headers,
        user_agent_header=f"DoMe-agent/{__version__}",
        max_size=MAX_FRAME_BYTES,
        ping_interval=None,
        open_timeout=HELLO_TIMEOUT,
        close_timeout=5,
        compression=None,
    )


class RelayClient:
    def __init__(
        self,
        relay_url: str,
        tokens: TokenManager,
        handler: RelayHandler,
        *,
        expected_pc_id: Callable[[], str | None],
        connector: Connector | None = None,
        backoff_base: float = BACKOFF_BASE,
        backoff_cap: float = BACKOFF_CAP,
        ping_interval: float = PING_INTERVAL,
        rng: random.Random | None = None,
    ) -> None:
        self._url = relay_url
        self._tokens = tokens
        self._handler = handler
        self._expected_pc_id = expected_pc_id
        self._connect: Connector = connector or _default_connect
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._ping_interval = ping_interval
        self._rng = rng or random.SystemRandom()
        self._schemas = load_schemas()
        self._ws: ClientConnection | None = None
        self._state: ConnectionState = "offline"
        self._stop = asyncio.Event()
        self._manual_reconnect = asyncio.Event()
        self._attempt = 0
        self._last_rx = 0.0
        self._task: asyncio.Task[None] | None = None
        self._send_lock = asyncio.Lock()
        self.connection_id: str | None = None
        self.last_close_code: int | None = None

    # ----- public ------------------------------------------------------------------------------------------
    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def connected(self) -> bool:
        return self._state == "connected" and self._ws is not None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stop.clear()
            self._task = asyncio.get_running_loop().create_task(self.run(), name="dome-relay")

    async def stop(self) -> None:
        self._stop.set()
        self._manual_reconnect.set()
        await self._close_socket(1000, "agent stopping")
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._set_state("stopped")

    def request_reconnect(self) -> None:
        """Tray "Reconnect" after 4001, or after a re-link."""
        self._attempt = 0
        self._manual_reconnect.set()

    async def reconnect_now(self, reason: str = "requested") -> None:
        """Drop the current socket; the normal backoff applies (used for the mismatch storm rule)."""
        await self._close_socket(CLOSE_PROTOCOL_ERROR, reason)

    async def send(self, frame: dict[str, Any]) -> bool:
        """Validate and write one frame. False when not connected or the write failed."""
        self._schemas.validate_frame("agent_to_relay", frame)
        ws = self._ws
        if ws is None or self._state != "connected":
            return False
        from dome_protocol import dumps_compact

        data = dumps_compact(frame)
        if len(data.encode("utf-8")) > MAX_FRAME_BYTES:
            raise ProtocolError("PAYLOAD_TOO_LARGE", "frame exceeds 64 KiB")
        try:
            async with self._send_lock:
                await ws.send(data)
            return True
        except (ConnectionClosed, OSError):
            return False

    # ----- main loop ---------------------------------------------------------------------------------------
    async def run(self) -> None:
        while not self._stop.is_set():
            self._set_state("connecting" if self._attempt == 0 else "reconnecting")
            outcome = await self._session()
            if self._stop.is_set():
                break
            if outcome in (
                "identity_mismatch",
                "credential_rejected",
                "revoked",
                "unauthorized",
                "configuration_error",
            ):
                self._set_state("stopped")
                await self._handler.on_stopped(outcome)
                return
            if outcome == "superseded":
                self._set_state("superseded")
                await self._handler.on_stopped("superseded")
                self._manual_reconnect.clear()
                await self._manual_reconnect.wait()
                self._manual_reconnect.clear()
                if self._stop.is_set():
                    break
                continue
            delay = self._next_delay()
            self._set_state("reconnecting")
            log.info("relay reconnect scheduled", delay_seconds=round(delay, 1), attempt=self._attempt)
            self._manual_reconnect.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._manual_reconnect.wait(), delay)
            self._manual_reconnect.clear()
        self._set_state("stopped")

    def _next_delay(self) -> float:
        cap = min(self._backoff_cap, self._backoff_base * (2**self._attempt))
        self._attempt = min(self._attempt + 1, 16)
        return self._rng.uniform(min(0.5, cap), cap)

    async def _session(self) -> StopReason | str:
        """One connection attempt → one of the StopReason strings, or 'retry'."""
        try:
            token = await self._tokens.get()
        except ApiError as exc:
            if exc.status in (401, 403):
                log.error("PC credential rejected by the service; re-link required", code=exc.code)
                return "credential_rejected"
            log.warning("could not obtain an access token", code=exc.code, status=exc.status)
            return "retry"
        try:
            ws = await self._connect(self._url, {"Authorization": f"Bearer {token}"})
        except InvalidStatus as exc:
            status = exc.response.status_code
            if status in (401, 403):
                self._tokens.invalidate()
                log.warning("relay refused the access token at upgrade", status=status)
                try:
                    await self._tokens.get(min_remaining=float("inf"))  # force a fresh token for the next attempt
                except ApiError as token_exc:
                    if token_exc.status in (401, 403):
                        return "credential_rejected"
            else:
                log.warning("relay upgrade failed", status=status)
            return "retry"
        except (InvalidURI, ValueError) as exc:
            # A malformed relay URL (override or identity.json) is a local configuration error, never a
            # verdict on the credential: nothing is discarded, the user is told to fix the URL.
            log.error("relay URL is invalid; not connecting", error=exc.__class__.__name__)
            return "configuration_error"
        except (OSError, websockets.exceptions.WebSocketException, TimeoutError) as exc:
            log.warning("relay connection failed", error=exc.__class__.__name__)
            return "retry"
        self._ws = ws
        self._last_rx = time.monotonic()
        try:
            hello_ack = await self._handshake(ws)
            if hello_ack is None:
                await self._close_socket(CLOSE_PROTOCOL_ERROR, "bad hello_ack")
                return "retry"
            expected = self._expected_pc_id()
            if expected is None or hello_ack.get("pc_id") != expected:
                log.error("hello_ack pc_id does not match the stored identity; stopping (re-link required)")
                await self._close_socket(CLOSE_PROTOCOL_ERROR, "identity mismatch")
                return "identity_mismatch"
            self.connection_id = str(hello_ack["connection_id"])
            self._attempt = 0
            self._set_state("connected")
            await self._handler.on_connected(hello_ack)
            pinger = asyncio.get_running_loop().create_task(self._pinger(ws), name="dome-relay-ping")
            try:
                return await self._receive_loop(ws)
            finally:
                pinger.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pinger
        finally:
            code = ws.close_code
            self.last_close_code = code
            self._ws = None
            self.connection_id = None
            if self._state == "connected":
                self._set_state("reconnecting")
                await self._handler.on_disconnected(f"close_code={code}")
            with contextlib.suppress(Exception):
                await ws.close()

    async def _handshake(self, ws: ClientConnection) -> dict[str, Any] | None:
        hello = hello_frame()
        self._schemas.validate_frame("agent_to_relay", hello)
        from dome_protocol import dumps_compact

        try:
            await ws.send(dumps_compact(hello))
            raw = await asyncio.wait_for(ws.recv(), HELLO_TIMEOUT)
        except (ConnectionClosed, OSError, TimeoutError) as exc:
            log.warning("handshake failed", error=exc.__class__.__name__)
            return None
        frame = self._decode(raw)
        if frame is None:
            return None
        if frame["type"] == "error":
            log.error("relay rejected hello", code=frame["error"]["code"], message=frame["error"]["message"])
            return None
        if frame["type"] != "hello_ack":
            log.error("first frame from relay was not hello_ack", type=frame["type"])
            return None
        self._last_rx = time.monotonic()
        return frame

    def _decode(self, raw: str | bytes) -> dict[str, Any] | None:
        try:
            frame = loads_strict(raw, max_bytes=MAX_FRAME_BYTES, require_object=True)
            self._schemas.validate_frame("relay_to_agent", frame)
        except ProtocolError as exc:
            log.warning("relay frame rejected", code=exc.code, message=exc.message)
            return None
        assert isinstance(frame, dict)
        return frame

    async def _receive_loop(self, ws: ClientConnection) -> StopReason | str:
        while True:
            try:
                raw = await ws.recv()
            except ConnectionClosed as exc:
                code = exc.rcvd.code if exc.rcvd else None
                log.info("relay connection closed", close_code=code)
                if code == CLOSE_SUPERSEDED:
                    return "superseded"
                if code == CLOSE_REVOKED:
                    return "unauthorized"
                if code == CLOSE_AUTH_REQUIRED:
                    self._tokens.invalidate()
                return "retry"
            except OSError:
                return "retry"
            self._last_rx = time.monotonic()
            frame = self._decode(raw)
            if frame is None:
                await self.send(error_frame(("MALFORMED_MESSAGE", "frame rejected by the agent")))
                continue
            kind = frame["type"]
            if kind == "ping":
                await self.send(pong_frame(frame.get("t")))
                continue
            if kind == "pong":
                continue
            if kind == "revoked":
                await self._handler.on_frame(frame)
                return "revoked"
            try:
                await self._handler.on_frame(frame)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # the handler must never take the connection down silently
                log.exception("frame handler failed", type=kind, error=exc.__class__.__name__)
            if self._ws is None:  # the handler closed the socket on purpose (identity mismatch / mismatch storm)
                return "retry"

    async def _pinger(self, ws: ClientConnection) -> None:
        while True:
            await asyncio.sleep(self._ping_interval)
            if time.monotonic() - self._last_rx > SILENCE_LIMIT:
                log.warning("relay silent; closing for reconnect")
                await self._close_socket(CLOSE_PROTOCOL_ERROR, "ping timeout")
                return
            if not await self.send(ping_frame()):
                return

    async def _close_socket(self, code: int, reason: str) -> None:
        ws = self._ws
        if ws is None:
            return
        with contextlib.suppress(Exception):
            await ws.close(code=code, reason=reason[:120])

    def _set_state(self, state: ConnectionState) -> None:
        if state != self._state:
            self._state = state
            try:
                self._handler.on_state_change(state)
            except Exception:  # noqa: BLE001 - UI callbacks must not break the client
                log.debug("state change callback failed")
