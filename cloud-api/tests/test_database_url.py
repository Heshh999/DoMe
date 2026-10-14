"""Neon-style database addresses (staging's database, docs/LAUNCH_SETUP.md section 6).

The Deploy button stores Neon's DIRECT connection string as the Fly secret DATABASE_URL, in the form
``postgresql://<role>:<password>@ep-....us-east-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require``.
These tests follow that string through the image:

* deploy/entrypoint.sh turns it into DOME_DATABASE_URL (``postgresql+psycopg://``) without touching the query;
* settings accept the result, and SQLAlchemy hands ``sslmode`` and ``channel_binding`` to psycopg unchanged;
* libpq enforces both (against a stand-in server: one without TLS is refused, and one that authenticates the client
  without channel binding is refused; with both relaxed the same stand-in is accepted, so the refusals are theirs);
* the pool pre-pings, so a pooled connection that Neon closed while its compute slept is replaced instead of failing
  the first request after a nap (shown against the real test PostgreSQL by ending the backend from outside).

All values are fake; nothing here reaches Neon.
"""

from __future__ import annotations

import base64
import os
import socket
import struct
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from sqlalchemy import exc, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from dome_api.db.engine import make_engine
from dome_api.settings import Settings
from tests.conftest import sql

REPO = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO / "deploy" / "entrypoint.sh"
NEON_HOST = "ep-quiet-sky-a1b2c3d4.us-east-1.aws.neon.tech"
FAKE_PASSWORD = "npg_" + "FakePassw0rd123"  # assembled so no line looks like a real credential to a scanner
NEON_REST = f"neondb_owner:{FAKE_PASSWORD}@{NEON_HOST}/neondb?sslmode=require&channel_binding=require"
NEON_DIRECT = "postgresql://" + NEON_REST
CONVERTED = "postgresql+psycopg://" + NEON_REST
SSL_REQUEST, GSSENC_REQUEST = 80877103, 80877104


def entrypoint_database_url(tmp_path: Path, **env: str) -> str:
    """Run deploy/entrypoint.sh as the image does, with ``printenv DOME_DATABASE_URL`` as the command."""
    clean = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "DOME_ENV": "staging",
        # staging refuses to start without a signing key; any bytes do for this test
        "DOME_ENTITLEMENT_SIGNING_KEY_PEM_B64": base64.b64encode(b"not a real key").decode(),
        "DOME_ENTITLEMENT_SIGNING_KEY_PEM_PATH": str(tmp_path / "entitlement.pem"),
        **env,
    }
    done = subprocess.run(
        ["sh", str(ENTRYPOINT), "printenv", "DOME_DATABASE_URL"],
        env=clean,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return done.stdout.strip()


# ----- conversion and hand-over --------------------------------------------------------------------------------


@pytest.mark.parametrize("scheme", ["postgresql://", "postgres://"])
def test_entrypoint_keeps_neons_query_parameters(tmp_path: Path, scheme: str) -> None:
    assert entrypoint_database_url(tmp_path, DATABASE_URL=scheme + NEON_REST) == CONVERTED


def test_entrypoint_leaves_an_explicit_dome_database_url_alone(tmp_path: Path) -> None:
    explicit = "postgresql+psycopg://other:pw@db.example/x?sslmode=verify-full"
    assert entrypoint_database_url(tmp_path, DATABASE_URL=NEON_DIRECT, DOME_DATABASE_URL=explicit) == explicit


def test_settings_accept_the_converted_address(tmp_path: Path) -> None:
    settings = Settings(
        env="staging",
        public_origin="https://staging.domeremote.com",
        trusted_proxies="10.0.0.0/8",
        database_url=CONVERTED,
        session_secret="a-staging-session-secret-0123456789",
        oidc_issuer="https://domeremote-staging.us.auth0.com",
        oidc_client_id="client-id",
        oidc_client_secret="client-secret",
        entitlement_signing_key_pem_path=tmp_path / "entitlement.pem",
    )
    assert settings.database_url == CONVERTED


def psycopg_kwargs(database_url: str) -> dict[str, Any]:
    """What SQLAlchemy's psycopg dialect passes to ``psycopg.connect`` for this URL (the async engine uses the same
    dialect code)."""
    url = make_url(database_url)
    args, kwargs = url.get_dialect()().create_connect_args(url)
    assert list(args) == []
    return dict(kwargs)


def test_sqlalchemy_hands_sslmode_and_channel_binding_to_psycopg() -> None:
    kwargs = psycopg_kwargs(CONVERTED)
    assert kwargs["sslmode"] == "require" and kwargs["channel_binding"] == "require"
    assert (kwargs["host"], kwargs["dbname"], kwargs["user"]) == (NEON_HOST, "neondb", "neondb_owner")
    conninfo = psycopg.conninfo.conninfo_to_dict(psycopg.conninfo.make_conninfo(**kwargs))
    assert conninfo["sslmode"] == "require" and conninfo["channel_binding"] == "require"
    # channel_binding needs libpq 13 or newer; psycopg[binary] ships its own libpq
    assert psycopg.pq.version() >= 130000


# ----- libpq enforces both parameters ---------------------------------------------------------------------------


def _send_ready_session(conn: socket.socket) -> None:
    """AuthenticationOk (no password asked, so no channel binding), a few parameters and ReadyForQuery."""
    reply = b"R" + struct.pack("!ii", 8, 0)
    for key, value in ((b"server_version", b"16.4"), (b"client_encoding", b"UTF8"), (b"integer_datetimes", b"on")):
        body = key + b"\0" + value + b"\0"
        reply += b"S" + struct.pack("!i", 4 + len(body)) + body
    reply += b"K" + struct.pack("!iii", 12, 1, 2) + b"Z" + struct.pack("!i", 5) + b"I"
    conn.sendall(reply)


def _serve_once(server: socket.socket, seen: list[str]) -> None:
    """A PostgreSQL stand-in without TLS: answers 'N' to TLS (and GSS) requests, then accepts any startup."""
    conn, _ = server.accept()
    with conn:
        conn.settimeout(5)
        while True:
            head = conn.recv(8, socket.MSG_WAITALL)
            if len(head) < 8:
                seen.append("closed")
                return
            length, code = struct.unpack("!ii", head)
            if code in (SSL_REQUEST, GSSENC_REQUEST):
                seen.append("tls request" if code == SSL_REQUEST else "gss request")
                conn.sendall(b"N")
                continue
            conn.recv(length - 8, socket.MSG_WAITALL)
            seen.append("startup")
            _send_ready_session(conn)
            try:
                conn.recv(64)  # wait for the client's Terminate or for it to hang up
            except OSError:
                pass
            return


@pytest.fixture
def stand_in() -> Iterator[tuple[int, list[str]]]:
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    seen: list[str] = []
    thread = threading.Thread(target=_serve_once, args=(server, seen), daemon=True)
    thread.start()
    yield server.getsockname()[1], seen
    thread.join(5)
    server.close()


def connect_to_stand_in(port: int, **overrides: str) -> None:
    kwargs = {**psycopg_kwargs(CONVERTED), "host": "127.0.0.1", "port": port, "connect_timeout": 5, **overrides}
    with psycopg.connect(**kwargs):
        pass


def test_sslmode_require_refuses_a_server_without_tls(stand_in: tuple[int, list[str]]) -> None:
    port, seen = stand_in
    with pytest.raises(psycopg.OperationalError, match="SSL was required"):
        connect_to_stand_in(port)
    assert "startup" not in seen  # nothing, not even the user name, went over the unencrypted connection


def test_channel_binding_require_refuses_a_server_that_skips_it(stand_in: tuple[int, list[str]]) -> None:
    port, _ = stand_in
    with pytest.raises(psycopg.OperationalError, match="channel binding required"):
        connect_to_stand_in(port, sslmode="prefer")  # TLS relaxed, only to reach authentication


def test_the_stand_in_accepts_when_both_are_relaxed(stand_in: tuple[int, list[str]]) -> None:
    port, seen = stand_in
    connect_to_stand_in(port, sslmode="prefer", channel_binding="disable")
    assert seen == ["tls request", "startup"] or seen == ["gss request", "tls request", "startup"]


# ----- pre-ping: the first request after Neon's compute slept --------------------------------------------------


async def test_the_engine_pre_pings_and_recycles() -> None:
    engine = make_engine(CONVERTED)  # creating the engine opens no connection
    try:
        pool = engine.sync_engine.pool
        assert pool._pre_ping is True
        assert pool._recycle == 600
    finally:
        await engine.dispose()


def _wait_until_gone(database_url: str, pid: int) -> None:
    deadline = time.monotonic() + 10
    while sql(database_url, "SELECT count(*) FROM pg_stat_activity WHERE pid = %s", (pid,))[0][0]:
        assert time.monotonic() < deadline, "the backend did not end"
        time.sleep(0.05)


async def _pid(engine: Any) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(text("SELECT pg_backend_pid()"))).scalar_one())


async def test_a_pooled_connection_closed_by_the_server_is_replaced(database_url: str) -> None:
    engine = make_engine(database_url, pool_size=1, max_overflow=0)
    try:
        first = await _pid(engine)  # back in the pool, idle, as between two requests
        sql(database_url, "SELECT pg_terminate_backend(%s)", (first,))  # what a suspending compute does to it
        _wait_until_gone(database_url, first)
        second = await _pid(engine)
        assert second != first
    finally:
        await engine.dispose()


async def test_without_pre_ping_the_same_request_would_fail(database_url: str) -> None:
    """The control for the test above: it is pre-ping, not luck, that hides the dropped connection."""
    engine = create_async_engine(database_url, pool_size=1, max_overflow=0, pool_pre_ping=False)
    try:
        first = await _pid(engine)
        sql(database_url, "SELECT pg_terminate_backend(%s)", (first,))
        _wait_until_gone(database_url, first)
        with pytest.raises(exc.DBAPIError):
            await _pid(engine)
    finally:
        await engine.dispose()
