"""Integration harness: real PostgreSQL (fresh database per session via Alembic), the API under
uvicorn in-process on a free port, ``tools/dev-idp`` as a real OIDC issuer in a subprocess, httpx
for REST and the ``websockets`` library for sockets. Agents and controllers are simulated with real
ES256 keys from ``dome_protocol``; nothing in the API is mocked.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import subprocess
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import psycopg
import pytest
import uvicorn
import websockets
from sqlalchemy.engine import make_url
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from dome_api.db.migrate import upgrade_to_head
from dome_api.keygen import write_key
from dome_api.main import create_app
from dome_api.settings import Settings
from dome_api.state import Services
from dome_protocol import (
    dumps_compact,
    format_rfc3339,
    generate_pairing_code,
    generate_private_key,
    jwk_from_public_key,
    kid_from_jwk,
    load_registry,
    load_schemas,
    loads_strict,
    now_utc,
    pairing_code_handle,
    pairing_verification_code,
    sign_payload,
)
from dome_protocol.keys import b64url_encode

REPO = Path(__file__).resolve().parents[2]
DEV_IDP_BIN = REPO / "tools" / "dev-idp" / ".venv" / "bin" / "dome-dev-idp"
TEST_DB_URL = os.environ.get("DOME_TEST_DATABASE_URL", "postgresql+psycopg://dome@/dome_test?host=/tmp&port=54329")
RECV_TIMEOUT = 5.0


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


# ----- infrastructure fixtures -------------------------------------------------------------------


@pytest.fixture(scope="session")
def ports() -> dict[str, int]:
    return {"api": free_port(), "idp": free_port()}


@pytest.fixture(scope="session")
def database_url() -> AsyncIterator[str]:
    base = make_url(TEST_DB_URL)
    name = f"dome_test_{secrets.token_hex(4)}"
    admin = base.set(database="postgres").set(drivername="postgresql")
    with psycopg.connect(admin.render_as_string(hide_password=False), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = base.set(database=name).render_as_string(hide_password=False)
    upgrade_to_head(url)
    yield url  # type: ignore[misc]
    with psycopg.connect(admin.render_as_string(hide_password=False), autocommit=True) as conn:
        conn.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
        conn.execute(f'DROP DATABASE "{name}"')


def sql(database_url: str, query: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Direct SQL against the test database (test set-up only: expiring codes, switching plans)."""
    url = make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(url, autocommit=True) as conn:
        cur = conn.execute(query, params)
        return cur.fetchall() if cur.description else []


@pytest.fixture(scope="session")
def dev_idp(ports: dict[str, int]) -> AsyncIterator[str]:
    assert DEV_IDP_BIN.exists(), f"{DEV_IDP_BIN} missing: run `cd tools/dev-idp && uv sync`"
    issuer = f"http://127.0.0.1:{ports['idp']}"
    env = {
        **os.environ,
        "DEV_IDP_BIND": f"127.0.0.1:{ports['idp']}",
        "DEV_IDP_ISSUER": issuer,
        "DEV_IDP_REDIRECT_URI": f"http://127.0.0.1:{ports['api']}/v1/auth/callback",
    }
    proc = subprocess.Popen([str(DEV_IDP_BIN)], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 30
    while True:
        try:
            if httpx.get(issuer + "/.well-known/openid-configuration", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline or proc.poll() is not None:
            proc.kill()
            raise RuntimeError("dev-idp did not start")
        time.sleep(0.1)
    yield issuer  # type: ignore[misc]
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="session")
def static_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("static")
    (d / "index.html").write_text("<!doctype html><title>DoMe</title><div id=app></div>")
    (d / "assets").mkdir()
    (d / "assets" / "app-abc123.js").write_text("console.log('dome')")
    (d / "manifest.webmanifest").write_text('{"name":"DoMe"}')
    return d


@pytest.fixture(scope="session")
def settings(ports: dict[str, int], database_url: str, dev_idp: str, static_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> Settings:
    key_path = write_key(tmp_path_factory.mktemp("keys") / "entitlement-ed25519.pem")
    return Settings(
        env="test",
        public_origin=f"http://127.0.0.1:{ports['api']}",
        api_bind=f"127.0.0.1:{ports['api']}",
        database_url=database_url,
        session_secret="test-session-secret-not-for-production-0123456789",
        oidc_issuer=dev_idp,
        oidc_client_id="dome-dev",
        oidc_client_secret="dome-dev-secret",
        entitlement_signing_key_pem_path=key_path,
        relay_sweep_interval_seconds=0.3,
        relay_hello_timeout_seconds=5.0,
        static_dir=static_dir,
        log_level="WARNING",
    )


@dataclass
class Env:
    settings: Settings
    ports: dict[str, int]
    database_url: str
    services: Services
    http: httpx.AsyncClient

    @property
    def origin(self) -> str:
        return self.settings.public_origin

    @property
    def ws_base(self) -> str:
        return f"ws://127.0.0.1:{self.ports['api']}"


@pytest.fixture(scope="session")
async def env(settings: Settings, ports: dict[str, int], database_url: str) -> AsyncIterator[Env]:
    app = create_app(settings)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=ports["api"],
        log_config=None,
        access_log=False,
        ws_max_size=settings.relay_max_frame_bytes,
        lifespan="on",
        log_level="warning",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    deadline = time.monotonic() + 30
    while not server.started:
        if task.done():
            task.result()
        if time.monotonic() > deadline:
            raise RuntimeError("API did not start")
        await asyncio.sleep(0.05)
    async with httpx.AsyncClient(base_url=settings.public_origin, follow_redirects=False, timeout=10) as http:
        yield Env(settings=settings, ports=ports, database_url=database_url, services=app.state.services, http=http)
    server.should_exit = True
    await asyncio.wait_for(task, 30)


# ----- contract validation helpers -----------------------------------------------------------------

SCHEMAS = load_schemas()
REGISTRY = load_registry()
PENDING_STATUSES = {428: "agent_link_poll_pending"}


def check_rest(response: httpx.Response, schema: str | None) -> Any:
    """Every JSON body the API returns must be a rest.schema.json definition."""
    if response.status_code == 204:
        assert response.content == b""
        return None
    if not response.headers.get("content-type", "").startswith("application/json"):
        return None
    body = response.json()
    if response.status_code in PENDING_STATUSES:
        SCHEMAS.validate_rest(PENDING_STATUSES[response.status_code], body)
    elif response.status_code >= 400:
        SCHEMAS.validate_rest("error_body", body)
    elif schema is not None:
        SCHEMAS.validate_rest(schema, body)
    return body


def frame_from_text(text: str, direction: str) -> dict[str, Any]:
    frame = loads_strict(text, max_bytes=1 << 20, max_depth=16)
    SCHEMAS.validate_frame(direction, frame)
    assert isinstance(frame, dict)
    return frame


async def recv_frame(ws: ClientConnection, direction: str, timeout: float = RECV_TIMEOUT) -> dict[str, Any]:
    raw = await asyncio.wait_for(ws.recv(), timeout)
    assert isinstance(raw, str)
    return frame_from_text(raw, direction)


async def recv_until(ws: ClientConnection, direction: str, kind: str, timeout: float = RECV_TIMEOUT, **match: Any) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"no {kind} frame matching {match}")
        frame = await recv_frame(ws, direction, remaining)
        if frame["type"] == kind and all(frame.get(k) == v for k, v in match.items()):
            return frame


async def expect_nothing(ws: ClientConnection, seconds: float = 0.7) -> None:
    try:
        raw = await asyncio.wait_for(ws.recv(), seconds)
    except TimeoutError:
        return
    raise AssertionError(f"unexpected frame: {raw[:200]!r}")


async def close_code(ws: ClientConnection, timeout: float = RECV_TIMEOUT) -> int | None:
    """Drain until the peer closes; return the close code."""
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            await asyncio.wait_for(ws.recv(), deadline - time.monotonic())
    except ConnectionClosed as exc:
        return exc.rcvd.code if exc.rcvd else None
    except TimeoutError:
        return None
    return None


# ----- simulated clients ---------------------------------------------------------------------------


class Browser:
    """A signed-in PWA: cookie jar, CSRF header and exact Origin."""

    def __init__(self, env: Env) -> None:
        self.env = env
        self.client = httpx.AsyncClient(base_url=env.origin, follow_redirects=False, timeout=10)
        self.csrf: str | None = None
        self.account: dict[str, Any] = {}
        self.email = ""

    async def login(self, email: str) -> None:
        self.email = email
        r = await self.client.get("/v1/auth/login", params={"return_to": "/app"})
        assert r.status_code == 303, r.text
        async with httpx.AsyncClient(follow_redirects=False, timeout=10) as idp:
            r2 = await idp.get(r.headers["location"] + "&dev_user=" + quote(email))
        assert r2.status_code == 303, r2.text
        r3 = await self.client.get(r2.headers["location"])
        assert r3.status_code == 303 and r3.headers["location"] == "/app", r3.text
        session = await self.get("/v1/session", schema="session_response")
        self.csrf = session["csrf_token"]
        self.account = session["account"]

    @property
    def account_id(self) -> str:
        return str(self.account["id"])

    @property
    def session_cookie(self) -> str:
        return str(self.client.cookies.get("dome_session"))

    def headers(self, *, csrf: bool = True, origin: bool = True) -> dict[str, str]:
        h: dict[str, str] = {}
        if origin:
            h["Origin"] = self.env.origin
        if csrf and self.csrf:
            h["X-DoMe-CSRF"] = self.csrf
        return h

    async def request(self, method: str, path: str, json_body: Any = None, *, schema: str | None = None, expect: int | None = None, csrf: bool = True, origin: bool = True, headers: dict[str, str] | None = None) -> httpx.Response:
        h = {**self.headers(csrf=csrf, origin=origin), **(headers or {})}
        r = await self.client.request(method, path, json=json_body, headers=h)
        r.body_checked = check_rest(r, schema)  # type: ignore[attr-defined]
        if expect is not None:
            assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text}"
        return r

    async def get(self, path: str, *, schema: str | None = None, expect: int = 200) -> Any:
        r = await self.request("GET", path, schema=schema, expect=expect, csrf=False, origin=False)
        return r.body_checked  # type: ignore[attr-defined]

    async def post(self, path: str, json_body: Any = None, *, schema: str | None = None, expect: int = 200) -> Any:
        r = await self.request("POST", path, json_body, schema=schema, expect=expect)
        return r.body_checked  # type: ignore[attr-defined]

    async def patch(self, path: str, json_body: Any, *, schema: str | None = None, expect: int = 200) -> Any:
        r = await self.request("PATCH", path, json_body, schema=schema, expect=expect)
        return r.body_checked  # type: ignore[attr-defined]

    async def delete(self, path: str, *, expect: int = 204) -> Any:
        r = await self.request("DELETE", path, expect=expect)
        return r.body_checked  # type: ignore[attr-defined]

    async def aclose(self) -> None:
        await self.client.aclose()


@dataclass
class AgentSim:
    """A linked PC: ES256 identity key, credential, access token and the ``/ws/agent`` socket."""

    env: Env
    key: Any = field(default_factory=generate_private_key)
    pc_id: str = ""
    account_id: str = ""
    credential: str = ""
    token: str = ""
    ws: ClientConnection | None = None
    snapshot: dict[str, Any] | None = None

    @property
    def jwk(self) -> dict[str, str]:
        return jwk_from_public_key(self.key.public_key())

    @property
    def kid(self) -> str:
        return kid_from_jwk(self.jwk)

    async def rest(self, method: str, path: str, json_body: Any = None, *, bearer: bool = False, schema: str | None = None, expect: int | None = None) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.token}"} if bearer else {}
        r = await self.env.http.request(method, path, json=json_body, headers=headers)
        r.body_checked = check_rest(r, schema)  # type: ignore[attr-defined]
        if expect is not None:
            assert r.status_code == expect, f"{method} {path} -> {r.status_code} {r.text}"
        return r

    async def link(self, browser: Browser, name: str = "Desk PC") -> dict[str, Any]:
        start = await self.rest("POST", "/v1/agent-link/start", {"pc_public_jwk": self.jwk, "agent_version": "0.1.0-test", "platform": "development"}, schema="agent_link_start_response", expect=200)
        s = start.body_checked  # type: ignore[attr-defined]
        preview = await browser.get(f"/v1/agent-link/{s['user_code']}", schema="agent_link_preview_response")
        assert preview["kid"] == self.kid
        pending = await self.rest("POST", "/v1/agent-link/poll", {"device_code": s["device_code"]}, expect=428)
        assert pending.body_checked["status"] == "authorization_pending"  # type: ignore[attr-defined]
        approve = await browser.post(f"/v1/agent-link/{s['user_code']}/approve", {"pc_name": name, "remote_enabled": True}, schema="agent_link_approve_response")
        poll = await self.rest("POST", "/v1/agent-link/poll", {"device_code": s["device_code"]}, schema="agent_link_poll_response", expect=200)
        body = poll.body_checked  # type: ignore[attr-defined]
        assert body["pc_id"] == approve["pc_id"] and body["enabled"] == approve["enabled"]
        self.pc_id, self.account_id, self.credential = body["pc_id"], body["account_id"], body["pc_credential"]
        await self.fetch_token()
        return {**body, "approve": approve, "start": s}

    async def fetch_token(self) -> str:
        r = await self.rest("POST", "/v1/agent/token", {"pc_credential": self.credential}, schema="agent_token_response", expect=200)
        self.token = r.body_checked["access_token"]  # type: ignore[attr-defined]
        return self.token

    async def connect(self, *, protocol_versions: tuple[str, ...] = ("1.0",), expect_snapshot: bool = True) -> dict[str, Any]:
        self.ws = await connect(self.env.ws_base + "/ws/agent", additional_headers={"Authorization": f"Bearer {self.token}"}, max_size=1 << 20)
        await self.send({"type": "hello", "component": "agent", "component_version": "0.1.0-test", "protocol_versions": list(protocol_versions), "registry_version": REGISTRY.registry_version})
        ack = await self.recv()
        if not expect_snapshot:
            return ack
        assert ack["type"] == "hello_ack" and ack["pc_id"] == self.pc_id, ack
        self.snapshot = await self.recv_type("grants_snapshot")
        assert self.snapshot["pc_id"] == self.pc_id and self.snapshot["account_id"] == self.account_id
        return ack

    async def send(self, frame: dict[str, Any]) -> None:
        assert self.ws is not None
        SCHEMAS.validate_frame("agent_to_relay", frame)
        await self.ws.send(dumps_compact(frame))

    async def recv(self, timeout: float = RECV_TIMEOUT) -> dict[str, Any]:
        assert self.ws is not None
        return await recv_frame(self.ws, "relay_to_agent", timeout)

    async def recv_type(self, kind: str, timeout: float = RECV_TIMEOUT, **match: Any) -> dict[str, Any]:
        assert self.ws is not None
        return await recv_until(self.ws, "relay_to_agent", kind, timeout, **match)

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()
            self.ws = None

    async def abort(self) -> None:
        """Drop the TCP connection without a close handshake (crash / network loss)."""
        if self.ws is not None:
            self.ws.transport.abort()
            self.ws = None

    # --- agent behaviour ---
    async def ack(self, command_id: str, state: str) -> None:
        await self.send({"type": "ack", "command_id": command_id, "state": state, "at": format_rfc3339(now_utc())})

    async def result(self, command_id: str, state: str = "succeeded", *, result: dict[str, Any] | None = None, error: dict[str, Any] | None = None, duration_ms: int = 12) -> None:
        frame: dict[str, Any] = {"type": "result", "command_id": command_id, "origin": "agent", "state": state, "at": format_rfc3339(now_utc()), "duration_ms": duration_ms}
        if result is not None:
            frame["result"] = result
        if error is not None:
            frame["error"] = error
        await self.send(frame)

    def ping_result(self) -> dict[str, Any]:
        return {"agent_time": format_rfc3339(now_utc()), "agent_version": "0.1.0-test", "protocol_version": REGISTRY.protocol_version}

    async def serve_one(self, *, executing: bool = True) -> dict[str, Any]:
        """Receive one command, ack it and answer succeeded with a registry-valid result."""
        cmd = await self.recv_type("command")
        payload = loads_strict(cmd["envelope"]["payload"])
        cid = payload["command_id"]
        await self.ack(cid, "accepted")
        if executing:
            await self.ack(cid, "executing")
        result = self.ping_result() if payload["action"] == "system.ping" else {"value": int(payload["params"].get("value", 0)), "muted": False}
        REGISTRY.validate_result(payload["action"], result)
        await self.result(cid, "succeeded", result=result)
        return payload

    async def state(self, **overrides: Any) -> dict[str, Any]:
        frame = {"type": "state", "pc_id": self.pc_id, "at": format_rfc3339(now_utc()), "state": {"remote_enabled": True, "session_locked": False, "extension_connected": False, "platform": "development", "volume": {"value": 40, "muted": False}, **overrides}}
        await self.send(frame)
        return frame

    # --- pairing (PC side) ---
    async def start_pairing(self) -> tuple[str, dict[str, Any]]:
        code = generate_pairing_code()
        r = await self.rest("POST", "/v1/pairing/start", {"code_hash": pairing_code_handle(code)}, bearer=True, schema="pairing_start_response", expect=200)
        return code, r.body_checked  # type: ignore[attr-defined]

    async def decide_pairing(self, request: dict[str, Any], code: str, *, approve: bool = True, granted: list[str] | None = None, expected_phone_code: str | None = None) -> None:
        assert kid_from_jwk(request["public_jwk"]) == request["kid"]
        if expected_phone_code is not None:
            assert pairing_verification_code(code, request["pairing_id"], self.pc_id, request["kid"]) == expected_phone_code
        await self.send({"type": "pairing_decision", "pairing_id": request["pairing_id"], "decision": "approve" if approve else "decline", "kid": request["kid"], "granted_capabilities": granted if granted is not None else request["requested_capabilities"]})


@dataclass
class ControllerSim:
    """A phone installation: ES256 key in 'IndexedDB', the browser session, and ``/ws/controller``."""

    env: Env
    browser: Browser
    name: str = "Alice's iPhone"
    key: Any = field(default_factory=generate_private_key)
    controller_id: str | None = None
    grant_id: str | None = None
    ws: ClientConnection | None = None

    @property
    def jwk(self) -> dict[str, str]:
        return jwk_from_public_key(self.key.public_key())

    @property
    def kid(self) -> str:
        return kid_from_jwk(self.jwk)

    async def claim(self, code: str, capabilities: tuple[str, ...] = ("status", "media", "volume"), *, expect: int = 202) -> Any:
        return await self.browser.post("/v1/pairing/claim", {"code_hash": pairing_code_handle(code), "public_jwk": self.jwk, "display_name": self.name, "requested_capabilities": list(capabilities)}, schema="pairing_status_response", expect=expect)

    async def pair(self, agent: AgentSim, capabilities: tuple[str, ...] = ("status", "media", "volume"), granted: list[str] | None = None) -> dict[str, Any]:
        """Full pairing with an online agent: start on the PC, claim from the phone, approve on the PC."""
        code, started = await agent.start_pairing()
        status = await self.claim(code, capabilities)
        assert status["state"] == "claimed" and status["pairing_id"] == started["pairing_id"]
        request = await agent.recv_type("pairing_request", pairing_id=started["pairing_id"])
        assert request["kid"] == self.kid and request["code_hash"] == pairing_code_handle(code)
        phone_code = pairing_verification_code(code, request["pairing_id"], agent.pc_id, self.kid)
        await agent.decide_pairing(request, code, granted=granted, expected_phone_code=phone_code)
        agent.snapshot = await agent.recv_type("grants_snapshot")
        final = await self.browser.get(f"/v1/pairing/{started['pairing_id']}", schema="pairing_status_response")
        assert final["state"] == "approved", final
        self.controller_id = final["controller_id"]
        self.grant_id = final["grant_id"]
        return final

    async def connect(self, *, origin: str | None = None, cookie: bool = True) -> dict[str, Any]:
        headers = {"Origin": origin if origin is not None else self.env.origin}
        if cookie:
            headers["Cookie"] = f"dome_session={self.browser.session_cookie}"
        self.ws = await connect(self.env.ws_base + "/ws/controller", additional_headers=headers, max_size=1 << 20)
        await self.send({"type": "hello", "component": "controller", "kid": self.kid, "component_version": "0.1.0-test", "protocol_versions": ["1.0"], "registry_version": REGISTRY.registry_version})
        ack = await self.recv()
        assert ack["type"] == "hello_ack", ack
        return ack

    async def send(self, frame: dict[str, Any]) -> None:
        assert self.ws is not None
        SCHEMAS.validate_frame("controller_to_relay", frame)
        await self.ws.send(dumps_compact(frame))

    async def recv(self, timeout: float = RECV_TIMEOUT) -> dict[str, Any]:
        assert self.ws is not None
        return await recv_frame(self.ws, "relay_to_controller", timeout)

    async def recv_type(self, kind: str, timeout: float = RECV_TIMEOUT, **match: Any) -> dict[str, Any]:
        assert self.ws is not None
        return await recv_until(self.ws, "relay_to_controller", kind, timeout, **match)

    async def subscribe(self, *pc_ids: str) -> None:
        await self.send({"type": "subscribe", "pc_ids": list(pc_ids)})

    def envelope(self, pc_id: str, action: str, params: dict[str, Any] | None = None, target: dict[str, Any] | None = None, *, lifetime: int = 30, command_id: str | None = None, controller_id: str | None = None, account_id: str | None = None, issued_at: Any = None, key: Any = None, nonce: str | None = None) -> tuple[str, dict[str, Any]]:
        issued = issued_at or now_utc()
        from datetime import timedelta

        payload = {
            "type": "command",
            "protocol_version": REGISTRY.protocol_version,
            "command_id": command_id or str(uuid.uuid4()),
            "account_id": account_id or self.browser.account_id,
            "controller_id": controller_id or self.controller_id or str(uuid.uuid4()),
            "target_pc_id": pc_id,
            "action": action,
            "params": params or {},
            "target": target,
            "issued_at": format_rfc3339(issued),
            "expires_at": format_rfc3339(issued + timedelta(seconds=lifetime)),
            "nonce": nonce or b64url_encode(secrets.token_bytes(16)),
        }
        env = sign_payload(key or self.key, dumps_compact(payload)).to_dict()
        return payload["command_id"], env

    async def command(self, pc_id: str, action: str, params: dict[str, Any] | None = None, target: dict[str, Any] | None = None, **kw: Any) -> str:
        cid, env = self.envelope(pc_id, action, params, target, **kw)
        await self.send({"type": "command", "pc_id": pc_id, "envelope": env})
        return cid

    async def send_envelope(self, pc_id: str, env: dict[str, Any], kind: str = "command") -> None:
        await self.send({"type": kind, "pc_id": pc_id, "envelope": env})

    async def close(self) -> None:
        if self.ws is not None:
            await self.ws.close()
            self.ws = None


# ----- per-test factories --------------------------------------------------------------------------

AccountFactory = Callable[[], Awaitable[Browser]]


@pytest.fixture
async def make_account(env: Env) -> AsyncIterator[AccountFactory]:
    browsers: list[Browser] = []

    async def factory() -> Browser:
        b = Browser(env)
        await b.login(f"user-{secrets.token_hex(6)}@example.test")
        browsers.append(b)
        return b

    yield factory
    for b in browsers:
        await b.aclose()


@pytest.fixture
async def alice(make_account: AccountFactory) -> Browser:
    return await make_account()


@pytest.fixture
async def bob(make_account: AccountFactory) -> Browser:
    return await make_account()


@pytest.fixture
async def linked_agent(env: Env, alice: Browser) -> AsyncIterator[AgentSim]:
    agent = AgentSim(env)
    await agent.link(alice)
    yield agent
    await agent.close()


@pytest.fixture
async def online_agent(linked_agent: AgentSim) -> AgentSim:
    await linked_agent.connect()
    return linked_agent


@pytest.fixture
async def paired(env: Env, alice: Browser, online_agent: AgentSim) -> AsyncIterator[ControllerSim]:
    """Alice's phone paired with her online PC (status+media+volume) and connected to the relay."""
    ctrl = ControllerSim(env, alice)
    await ctrl.pair(online_agent)
    await ctrl.connect()
    yield ctrl
    await ctrl.close()


def dump(obj: Any) -> str:
    return json.dumps(obj, indent=1, default=str)
