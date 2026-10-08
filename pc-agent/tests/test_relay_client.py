from __future__ import annotations

import asyncio
import random
from typing import Any

import pytest
from dome_protocol import ProtocolError

from dome_agent.api import ApiClient
from dome_agent.relay_client import RelayClient, TokenManager
from dome_agent.testing.fake_relay import FakeApi, FakeRelay


class RecordingHandler:
    def __init__(self) -> None:
        self.connected: list[dict[str, Any]] = []
        self.frames: list[dict[str, Any]] = []
        self.disconnects: list[str] = []
        self.stopped: list[str] = []
        self.states: list[str] = []
        self.connected_event = asyncio.Event()

    async def on_connected(self, hello_ack: dict[str, Any]) -> None:
        self.connected.append(hello_ack)
        self.connected_event.set()

    async def on_frame(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)

    async def on_disconnected(self, reason: str) -> None:
        self.disconnects.append(reason)

    async def on_stopped(self, reason: str) -> None:
        self.stopped.append(reason)

    def on_state_change(self, state: str) -> None:
        self.states.append(state)


@pytest.fixture
async def client(fake_api: FakeApi, fake_relay: FakeRelay):
    cred = fake_api.issue_credential()
    api = ApiClient(fake_api.url)
    tokens = TokenManager(api, lambda: cred)
    handler = RecordingHandler()
    rc = RelayClient(
        fake_relay.url,
        tokens,
        handler,
        expected_pc_id=lambda: fake_api.pc_id,
        backoff_base=0.05,
        backoff_cap=0.3,
        ping_interval=0.3,
        rng=random.Random(1),
    )
    yield rc, handler, tokens, api
    await rc.stop()
    await api.close()


async def test_connect_hello_ack_and_snapshot(client: Any, fake_relay: FakeRelay) -> None:
    rc, handler, _tokens, _api = client
    rc.start()
    await asyncio.wait_for(handler.connected_event.wait(), 10)
    assert rc.connected and handler.connected[0]["pc_id"] == fake_relay.api.pc_id
    await fake_relay.wait_connected()
    for _ in range(50):
        if handler.frames:
            break
        await asyncio.sleep(0.02)
    assert handler.frames[0]["type"] == "grants_snapshot"
    assert "connected" in handler.states


async def test_application_pings_and_pongs(client: Any, fake_relay: FakeRelay) -> None:
    rc, handler, _tokens, _api = client
    rc.start()
    await asyncio.wait_for(handler.connected_event.wait(), 10)
    ping = await fake_relay.expect("ping", timeout=5)
    assert "t" in ping
    # the relay's pong is consumed by the client, never handed to the handler
    await asyncio.sleep(0.1)
    assert all(f["type"] != "pong" for f in handler.frames)
    # inbound ping is answered with pong
    await fake_relay.send({"type": "ping", "t": "2026-01-01T00:00:00.000Z"})
    pong = await fake_relay.expect("pong", timeout=5)
    assert pong["t"] == "2026-01-01T00:00:00.000Z"


async def test_reconnects_with_backoff_after_close(client: Any, fake_relay: FakeRelay) -> None:
    rc, handler, _tokens, _api = client
    rc.start()
    await asyncio.wait_for(handler.connected_event.wait(), 10)
    await fake_relay.wait_connected()
    handler.connected_event.clear()
    await fake_relay.close(1012, "restart")
    await asyncio.wait_for(handler.connected_event.wait(), 10)
    assert fake_relay.connect_count == 2
    assert handler.disconnects and "reconnecting" in handler.states


async def test_backoff_grows_and_is_jittered() -> None:
    rc = RelayClient(
        "ws://127.0.0.1:1/ws/agent",
        None,
        RecordingHandler(),
        expected_pc_id=lambda: None,
        backoff_base=1.0,
        backoff_cap=60.0,
        rng=random.Random(7),
    )  # type: ignore[arg-type]
    delays = [rc._next_delay() for _ in range(8)]  # noqa: SLF001
    caps = [1, 2, 4, 8, 16, 32, 60, 60]
    for d, cap in zip(delays, caps, strict=True):
        assert 0.5 <= d <= cap
    assert len(set(delays)) > 1


async def test_token_refreshed_when_rejected_at_upgrade(client: Any, fake_api: FakeApi, fake_relay: FakeRelay) -> None:
    rc, handler, tokens, _api = client
    # pre-load a token the relay will not accept
    tokens._token = type("T", (), {"value": "A" * 43, "expires_at": 1e18})()  # noqa: SLF001
    rc.start()
    await asyncio.wait_for(handler.connected_event.wait(), 10)
    assert fake_relay.rejected_upgrades == 1 and fake_relay.connect_count == 1


async def test_credential_rejected_stops(fake_api: FakeApi, fake_relay: FakeRelay) -> None:
    fake_api.token_status = 401
    api = ApiClient(fake_api.url)
    handler = RecordingHandler()
    rc = RelayClient(
        fake_relay.url,
        TokenManager(api, lambda: "B" * 43),
        handler,
        expected_pc_id=lambda: fake_api.pc_id,
        backoff_base=0.05,
    )
    rc.start()
    for _ in range(100):
        if handler.stopped:
            break
        await asyncio.sleep(0.05)
    assert handler.stopped == ["credential_rejected"] and rc.state == "stopped"
    await rc.stop()
    await api.close()


async def test_hello_ack_identity_mismatch_stops(client: Any, fake_relay: FakeRelay) -> None:
    rc, handler, _tokens, _api = client
    fake_relay.hello_ack_pc_id = "00000000-0000-4000-8000-000000000000"
    rc.start()
    for _ in range(100):
        if handler.stopped:
            break
        await asyncio.sleep(0.05)
    assert handler.stopped == ["identity_mismatch"] and not handler.connected


async def test_token_manager_refreshes_near_expiry(fake_api: FakeApi) -> None:
    cred = fake_api.issue_credential()
    api = ApiClient(fake_api.url)
    tokens = TokenManager(api, lambda: cred)
    t1 = await tokens.get()
    t2 = await tokens.get()
    assert t1 == t2
    tokens._token.expires_at = 0  # noqa: SLF001  (less than 5 minutes left)
    t3 = await tokens.get()
    assert t3 != t1
    await api.close()


async def test_send_validates_frames(client: Any) -> None:
    rc, _handler, _tokens, _api = client
    with pytest.raises(ProtocolError):
        await rc.send({"type": "ack", "command_id": "nope"})
    assert await rc.send({"type": "ping"}) is False  # not connected yet


async def test_invalid_relay_url_is_configuration_error_not_credential_rejected(fake_api: FakeApi) -> None:
    cred = fake_api.issue_credential()
    api = ApiClient(fake_api.url)
    handler = RecordingHandler()
    rc = RelayClient(
        "http://[not-a-valid-uri/ws/agent",
        TokenManager(api, lambda: cred),
        handler,
        expected_pc_id=lambda: fake_api.pc_id,
        backoff_base=0.05,
    )
    rc.start()
    for _ in range(100):
        if handler.stopped:
            break
        await asyncio.sleep(0.05)
    assert handler.stopped == ["configuration_error"] and rc.state == "stopped"
    assert cred in fake_api.credentials  # the credential was never touched
    await rc.stop()
    await api.close()
