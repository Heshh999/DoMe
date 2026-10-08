"""TEST DOUBLE: an in-process stand-in for the cloud API + relay, speaking the real contract.

``FakeApi`` is a tiny asyncio HTTP/1.1 server for the endpoints the agent calls (device link, token,
entitlement, JWKS, pairing start). ``FakeRelay`` is a ``websockets`` server for ``/ws/agent`` that
authenticates the bearer token, performs ``hello`` → ``hello_ack`` → ``grants_snapshot`` and then lets
a test inject ``relay_to_agent`` frames and observe every ``agent_to_relay`` frame. Both validate
frames/bodies against the shared schemas so a test cannot pass with a non-conforming message.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from dome_protocol import dumps_compact, format_rfc3339, load_schemas, loads_strict, now_utc
from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.http11 import Request, Response


def _b64url_token() -> str:
    return secrets.token_urlsafe(32)[:43].ljust(43, "A")


# ----- fake API ---------------------------------------------------------------------------------------


@dataclass
class LinkState:
    device_code: str
    user_code: str
    approved: dict[str, Any] | None = None
    polls: int = 0


@dataclass
class FakeApi:
    pc_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    account_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    pc_name: str = "Test PC"
    credentials: set[str] = field(default_factory=set)
    tokens: dict[str, float] = field(default_factory=dict)  # token → expiry (unix)
    token_lifetime: int = 3600
    plan: str = "free"
    pc_enabled: bool = True
    entitlement_status: int = 200  # set to 503 to simulate an outage
    entitlement_assertion: str | None = None
    token_status: int = 200  # set to 401 to simulate a revoked credential
    links: dict[str, LinkState] = field(default_factory=dict)
    pairing_starts: list[str] = field(default_factory=list)  # code hashes received
    pairing_expiry_seconds: int = 300
    requests: list[tuple[str, str]] = field(default_factory=list)
    _server: asyncio.AbstractServer | None = None
    port: int = 0
    relay_url: str = ""
    _jwk: Any = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    # -- helpers for tests --
    def issue_credential(self) -> str:
        cred = _b64url_token()
        self.credentials.add(cred)
        return cred

    def valid_token(self, token: str) -> bool:
        exp = self.tokens.get(token)
        return exp is not None and exp > time.time()

    def approve_link(self, user_code: str, *, enabled: bool = True) -> str:
        link = next(link for link in self.links.values() if link.user_code == user_code)
        credential = self.issue_credential()
        relay_url = self.relay_url or "ws://127.0.0.1:1/ws/agent"
        link.approved = {
            "pc_id": self.pc_id,
            "account_id": self.account_id,
            "pc_credential": credential,
            "relay_url": relay_url,
            "api_url": self.url,
            "pc_name": self.pc_name,
            "enabled": enabled,
        }
        return credential

    def signing_key(self) -> Any:
        if self._jwk is None:
            from joserfc.jwk import OKPKey

            self._jwk = OKPKey.generate_key("Ed25519", parameters={"use": "sig", "alg": "EdDSA"})
        return self._jwk

    def jwks(self) -> dict[str, Any]:
        key = self.signing_key()
        public = key.as_dict(private=False)
        public["kid"] = key.thumbprint()
        return {"keys": [public]}

    def make_assertion(
        self,
        *,
        plan: str = "pro",
        lifetime: int = 3600,
        pc_id: str | None = None,
        account_id: str | None = None,
        routines: bool = True,
        iat: int | None = None,
    ) -> str:
        from joserfc import jwt

        key = self.signing_key()
        now = int(time.time()) if iat is None else iat
        claims = {
            "iss": self.url,
            "sub": account_id or self.account_id,
            "pc": pc_id or self.pc_id,
            "plan": plan,
            "limits": {
                "max_enabled_pcs": 5,
                "max_controllers": 5,
                "routines": routines,
                "routine_max_steps": 10,
                "routine_max_seconds": 60,
                "custom_layouts": True,
            },
            "iat": now,
            "exp": now + lifetime,
            "jti": str(uuid.uuid4()),
        }
        return jwt.encode(
            {"alg": "EdDSA", "typ": "dome-entitlement+jwt", "kid": key.thumbprint()}, claims, key, algorithms=["EdDSA"]
        )

    # -- HTTP --
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await asyncio.wait_for(reader.readline(), 10)
            if not request_line:
                return
            method, path, _ = request_line.decode("latin-1").strip().split(" ", 2)
            headers: dict[str, str] = {}
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                name, _, value = line.decode("latin-1").partition(":")
                headers[name.strip().lower()] = value.strip()
            body = b""
            length = int(headers.get("content-length", "0") or 0)
            if length:
                body = await reader.readexactly(length)
            self.requests.append((method, path))
            status, payload = self._route(method, path, headers, body)
            data = json.dumps(payload).encode("utf-8")
            reason = {
                200: "OK",
                201: "Created",
                401: "Unauthorized",
                404: "Not Found",
                428: "Precondition Required",
                503: "Service Unavailable",
                400: "Bad Request",
                403: "Forbidden",
            }.get(status, "OK")
            writer.write(
                f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode()
                + data
            )
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, TimeoutError, ValueError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    def _error(self, status: int, code: str, message: str) -> tuple[int, dict[str, Any]]:
        return status, {"error": {"code": code, "message": message, "retryable": status >= 500}}

    def _bearer(self, headers: dict[str, str]) -> str | None:
        scheme, _, token = headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not self.valid_token(token.strip()):
            return None
        return token.strip()

    def _route(self, method: str, path: str, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, Any]]:
        schemas = load_schemas()
        data: dict[str, Any] = {}
        if body:
            parsed = loads_strict(body, require_object=True)
            assert isinstance(parsed, dict)
            data = parsed
        if method == "GET" and path == "/.well-known/dome-jwks.json":
            return 200, self.jwks()
        if method == "POST" and path == "/v1/agent-link/start":
            schemas.validate_rest("agent_link_start_request", data)
            link = LinkState(
                device_code=_b64url_token(),
                user_code=f"{secrets.choice('ABCDEFGHJKMNPQRSTVWXYZ0123456789')}BCD-EFG{secrets.choice('0123456789')}",
            )
            self.links[link.device_code] = link
            out = {
                "device_code": link.device_code,
                "user_code": link.user_code,
                "verification_uri_complete": f"{self.url}/link?user_code={link.user_code}",
                "expires_in": 600,
                "interval": 1,
            }
            schemas.validate_rest("agent_link_start_response", out)
            return 200, out
        if method == "POST" and path == "/v1/agent-link/poll":
            schemas.validate_rest("agent_link_poll_request", data)
            pending_link = self.links.get(data["device_code"])
            if pending_link is None:
                return self._error(404, "PAIRING_CODE_INVALID", "unknown device code")
            pending_link.polls += 1
            if pending_link.approved is None:
                return 428, {"status": "authorization_pending"}
            out = pending_link.approved
            del self.links[data["device_code"]]
            schemas.validate_rest("agent_link_poll_response", out)
            return 200, out
        if method == "POST" and path == "/v1/agent/token":
            if self.token_status != 200:
                return self._error(self.token_status, "UNKNOWN_KEY", "credential revoked")
            schemas.validate_rest("agent_token_request", data)
            if data["pc_credential"] not in self.credentials:
                return self._error(401, "UNKNOWN_KEY", "unknown credential")
            token = _b64url_token()
            self.tokens[token] = time.time() + self.token_lifetime
            out = {
                "access_token": token,
                "expires_in": self.token_lifetime,
                "pc_id": self.pc_id,
                "account_id": self.account_id,
            }
            schemas.validate_rest("agent_token_response", out)
            return 200, out
        if method == "POST" and path == "/v1/agent/entitlement":
            if self._bearer(headers) is None:
                return self._error(401, "UNKNOWN_KEY", "bad token")
            if self.entitlement_status != 200:
                return self._error(self.entitlement_status, "INTERNAL", "outage")
            out = {"plan": self.plan, "assertion": self.entitlement_assertion, "pc_enabled": self.pc_enabled}
            schemas.validate_rest("agent_entitlement_response", out)
            return 200, out
        if method == "POST" and path == "/v1/pairing/start":
            if self._bearer(headers) is None:
                return self._error(401, "UNKNOWN_KEY", "bad token")
            schemas.validate_rest("pairing_start_request", data)
            self.pairing_starts.append(data["code_hash"])
            from datetime import timedelta

            out = {
                "pairing_id": str(uuid.uuid4()),
                "expires_at": format_rfc3339(now_utc() + timedelta(seconds=self.pairing_expiry_seconds)),
            }
            schemas.validate_rest("pairing_start_response", out)
            return 200, out
        return self._error(404, "NOT_FOUND", path)


# ----- fake relay --------------------------------------------------------------------------------------


@dataclass
class RelayConnection:
    ws: ServerConnection
    connection_id: str
    hello: dict[str, Any] | None = None
    closed: asyncio.Event = field(default_factory=asyncio.Event)


class FakeRelay:
    def __init__(self, api: FakeApi, *, auto_snapshot: bool = True) -> None:
        self.api = api
        self.auto_snapshot = auto_snapshot
        self.schemas = load_schemas()
        self.inbound: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.connections: list[RelayConnection] = []
        self.connect_count = 0
        self.rejected_upgrades = 0
        self.controllers: list[dict[str, Any]] = []  # grants_snapshot entries
        self.pc_enabled = True
        self.entitlement_assertion: str | None = None
        self.hello_ack_pc_id: str | None = None  # override to simulate an identity mismatch
        self.snapshot_pc_id: str | None = None
        self._server: Server | None = None
        self._connected = asyncio.Event()
        self.port = 0

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws/agent"

    async def start(self) -> None:
        self._server = await serve(
            self._handler, "127.0.0.1", 0, process_request=self._process_request, max_size=65536, ping_interval=None
        )
        self.port = self._server.sockets[0].getsockname()[1]
        self.api.relay_url = self.url

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    @property
    def current(self) -> RelayConnection | None:
        for c in reversed(self.connections):
            if not c.closed.is_set():
                return c
        return None

    async def wait_connected(self, timeout: float = 10.0) -> RelayConnection:  # noqa: ASYNC109 - test helper
        await asyncio.wait_for(self._connected.wait(), timeout)
        self._connected.clear()
        conn = self.current
        assert conn is not None
        return conn

    def _process_request(self, connection: ServerConnection, request: Request) -> Response | None:
        if request.path != "/ws/agent":
            return connection.respond(404, "not found\n")
        if "Origin" in request.headers:
            self.rejected_upgrades += 1
            return connection.respond(403, "origin not allowed\n")
        scheme, _, token = request.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not self.api.valid_token(token.strip()):
            self.rejected_upgrades += 1
            return connection.respond(401, "unauthorized\n")
        return None

    def snapshot_frame(self) -> dict[str, Any]:
        frame: dict[str, Any] = {
            "type": "grants_snapshot",
            "pc_id": self.snapshot_pc_id or self.api.pc_id,
            "account_id": self.api.account_id,
            "snapshot_id": str(uuid.uuid4()),
            "pc_enabled": self.pc_enabled,
            "controllers": [dict(c) for c in self.controllers],
        }
        if self.entitlement_assertion is not None:
            frame["entitlement_assertion"] = self.entitlement_assertion
        return frame

    async def _handler(self, ws: ServerConnection) -> None:
        conn = RelayConnection(ws, str(uuid.uuid4()))
        self.connections.append(conn)
        self.connect_count += 1
        try:
            raw = await asyncio.wait_for(ws.recv(), 10)
            hello = loads_strict(raw, require_object=True)
            self.schemas.validate_frame("agent_to_relay", hello)
            assert hello["type"] == "hello", hello
            conn.hello = hello
            ack = {
                "type": "hello_ack",
                "protocol_version": "1.0",
                "server_time": format_rfc3339(now_utc()),
                "connection_id": conn.connection_id,
                "pc_id": self.hello_ack_pc_id or self.api.pc_id,
            }
            await self._send_to(conn, ack)
            if self.auto_snapshot:
                await self._send_to(conn, self.snapshot_frame())
            self._connected.set()
            async for message in ws:
                frame = loads_strict(message, max_bytes=65536, require_object=True)
                self.schemas.validate_frame("agent_to_relay", frame)
                if frame["type"] == "ping":
                    await self._send_to(conn, {"type": "pong", "t": frame.get("t", format_rfc3339(now_utc()))})
                await self.inbound.put(frame)
        except Exception:  # noqa: BLE001 - connection ended
            pass
        finally:
            conn.closed.set()

    async def _send_to(self, conn: RelayConnection, frame: dict[str, Any]) -> None:
        self.schemas.validate_frame("relay_to_agent", frame)
        await conn.ws.send(dumps_compact(frame))

    async def send(self, frame: dict[str, Any]) -> None:
        conn = self.current
        assert conn is not None, "no live agent connection"
        await self._send_to(conn, frame)

    async def send_snapshot(self) -> dict[str, Any]:
        frame = self.snapshot_frame()
        await self.send(frame)
        return frame

    async def close(self, code: int = 1000, reason: str = "") -> None:
        conn = self.current
        if conn is not None:
            await conn.ws.close(code=code, reason=reason)
            await conn.closed.wait()

    async def expect(
        self,
        frame_type: str,
        *,
        command_id: str | None = None,
        timeout: float = 10.0,  # noqa: ASYNC109 - test helper
        state: str | None = None,
    ) -> dict[str, Any]:
        """Pull inbound frames until one matches; unrelated frames are kept in ``skipped``."""
        deadline = asyncio.get_running_loop().time() + timeout
        skipped: list[dict[str, Any]] = []
        try:
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError(
                        f"no {frame_type} frame (command_id={command_id}, state={state}); saw {[f['type'] for f in skipped]}"
                    )
                frame = await asyncio.wait_for(self.inbound.get(), remaining)
                if (
                    frame["type"] != frame_type
                    or (command_id is not None and frame.get("command_id") != command_id)
                    or (state is not None and frame.get("state") != state)
                ):
                    skipped.append(frame)
                    continue
                return frame
        finally:
            for f in skipped:
                self.inbound.put_nowait(f)

    def drain(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        while not self.inbound.empty():
            out.append(self.inbound.get_nowait())
        return out
