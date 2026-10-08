from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest

from dome_agent.agent import Agent
from dome_agent.settings import Settings, load_settings
from dome_agent.store import Store
from dome_agent.testing.fake_extension import FakeExtension, FakeTab
from dome_agent.testing.fake_platform import FakeState, build_fake_platform
from dome_agent.testing.fake_relay import FakeApi, FakeRelay

from .helpers import Controller, link_identity


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in (
        "DOME_AGENT_PLATFORM",
        "DOME_AGENT_API_URL",
        "DOME_AGENT_RELAY_URL",
        "DOME_AGENT_HEADLESS",
        "DOME_AGENT_DEV_EXTENSION_ID",
    ):
        monkeypatch.delenv(var, raising=False)
    # Unix socket paths are limited to ~104 bytes: keep the state dir short.
    monkeypatch.setenv("DOME_AGENT_STATE_DIR", str(tmp_path / "s"))


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = load_settings(state_dir=tmp_path / "s")
    s.ensure_dirs()
    return s


@pytest.fixture
def store(settings: Settings) -> Iterator[Store]:
    st = Store(settings.db_path)
    yield st
    st.close()


@pytest.fixture
def fake_state() -> FakeState:
    return FakeState()


@pytest.fixture
async def fake_api() -> AsyncIterator[FakeApi]:
    api = FakeApi()
    await api.start()
    yield api
    await api.stop()


@pytest.fixture
async def fake_relay(fake_api: FakeApi) -> AsyncIterator[FakeRelay]:
    relay = FakeRelay(fake_api)
    await relay.start()
    yield relay
    await relay.stop()


@pytest.fixture
def controller(fake_api: FakeApi) -> Controller:
    return Controller(fake_api.account_id, fake_api.pc_id)


class AgentHarness:
    """Agent + fake relay/api/platform wired like production, but every seam observable."""

    def __init__(self, agent: Agent, relay: FakeRelay, api: FakeApi, state: FakeState, settings: Settings) -> None:
        self.agent = agent
        self.relay = relay
        self.api = api
        self.fake = state
        self.settings = settings
        self.extension: FakeExtension | None = None

    async def connect_extension(self, *tabs: FakeTab) -> FakeExtension:
        ext = FakeExtension(self.settings.state_dir)
        for t in tabs:
            ext.add_tab(t)
        await asyncio.to_thread(ext.connect)
        ext.publish_tabs()
        for _ in range(100):
            if self.agent.bridge.connected and len(self.agent.bridge.all_tabs()) >= len(tabs):
                break
            await asyncio.sleep(0.02)
        self.extension = ext
        return ext

    async def send_command(self, envelope: dict[str, Any]) -> None:
        from .helpers import relay_command_frame

        await self.relay.send(
            relay_command_frame(envelope, self.agent.relay.connection_id if self.agent.relay else None)
        )

    async def send_confirmation(self, envelope: dict[str, Any]) -> None:
        from .helpers import relay_confirmation_frame

        await self.relay.send(relay_confirmation_frame(envelope))

    async def result(self, command_id: str, timeout: float = 10.0) -> dict[str, Any]:
        return await self.relay.expect("result", command_id=command_id, timeout=timeout)

    async def ack(self, command_id: str, state: str | None = None, timeout: float = 10.0) -> dict[str, Any]:
        return await self.relay.expect("ack", command_id=command_id, state=state, timeout=timeout)

    async def wait_snapshot_applied(self, timeout: float = 10.0) -> None:
        await self.relay.expect("state", timeout=timeout)
        for _ in range(200):
            if self.agent._snapshot_received:  # noqa: SLF001
                return
            await asyncio.sleep(0.01)


@pytest.fixture
async def harness(
    settings: Settings, fake_api: FakeApi, fake_relay: FakeRelay, fake_state: FakeState, controller: Controller
) -> AsyncIterator[AgentHarness]:
    credential = fake_api.issue_credential()
    link_identity(
        settings.state_dir,
        fake_api.pc_id,
        fake_api.account_id,
        credential=credential,
        api_url=fake_api.url,
        relay_url=fake_relay.url,
    )
    store = Store(settings.db_path)
    store.set_remote_enabled(True)
    controller.grant_locally(store)
    store.close()
    fake_relay.controllers.append(controller.snapshot_entry())
    agent = Agent(settings, platform=build_fake_platform(fake_state))
    await agent.start()
    h = AgentHarness(agent, fake_relay, fake_api, fake_state, settings)
    await fake_relay.wait_connected()
    await h.wait_snapshot_applied()
    try:
        yield h
    finally:
        if h.extension is not None:
            h.extension.close()
        await agent.stop()


@pytest.fixture
def new_uuid() -> str:
    return str(uuid.uuid4())
