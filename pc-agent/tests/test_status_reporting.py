"""What ``agent.status()`` (and with it `dome-agent status`, the tray Diagnostics text and the diagnostics
bundle) reports about local problems that otherwise reach only agent.log: an IPC endpoint that is not
listening (its pipe name held by a leftover process) and platform reads that keep failing."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
import structlog.testing
from dome_protocol import ProtocolError

from dome_agent import cli
from dome_agent.control import ControlServer
from dome_agent.diagnostics import build_bundle

from .conftest import AgentHarness

HELD = r"the bridge pipe \\.\pipe\DoMe.Agent.0123456789abcdef is held by another process (Access is denied.)"


class Failing:
    """An adapter read that raises ``error`` while it is set, else returns ``value``."""

    def __init__(self, value: Any) -> None:
        self.value = value
        self.error: BaseException | None = None

    def __call__(self) -> Any:
        if self.error is not None:
            raise self.error
        return self.value


async def test_a_healthy_agent_reports_no_local_problem(harness: AgentHarness) -> None:
    status = harness.agent.status()
    assert status["bridge_unavailable_reason"] == ""
    assert status["control_unavailable_reason"] == ""
    assert status["read_failures"] == {}


def test_an_endpoint_that_is_not_started_says_so(tmp_path: Path) -> None:
    control = ControlServer(tmp_path, {}, lambda _kind, _detail: None)
    assert control.unavailable_reason == "the control channel is not started"


async def test_failing_reads_are_reported_until_they_work_again(
    harness: AgentHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = harness.agent
    volume = Failing(agent.platform.volume.get())
    monkeypatch.setattr(agent.platform.volume, "get", volume)
    volume.error = ProtocolError("OS_ERROR", "Audio endpoint error: COMError (0x80070005)")
    with structlog.testing.capture_logs() as logs:
        await agent.state.snapshot()
        first = agent.status()["read_failures"]["volume"]
        await agent.state.snapshot()
    again = agent.status()["read_failures"]["volume"]
    assert first["code"] == "OS_ERROR" and first["message"] == "Audio endpoint error: COMError (0x80070005)"
    assert first["failures"] == 1 and again["failures"] >= 2 and again["since"] == first["since"]
    # the rate-limited agent.log line is still written, once
    assert [e["source"] for e in logs if e["event"].startswith("state read failed")] == ["volume"]

    volume.error = None
    await agent.state.snapshot()
    assert agent.status()["read_failures"] == {}


async def test_an_unexpected_read_error_is_reported_by_class_never_by_its_text(
    harness: AgentHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = harness.agent
    media = Failing([])
    monkeypatch.setattr(agent.platform.media, "list_sessions", media)
    media.error = PermissionError(13, "Secret Song Title - Private Playlist")
    await agent.state.snapshot()
    failure = agent.status()["read_failures"]["media"]
    assert failure["error"] == "PermissionError" and failure["os_error"] == 13
    assert "Secret Song Title" not in json.dumps(agent.status())


async def test_the_reasons_reach_the_cli_and_the_diagnostics_bundle(
    harness: AgentHarness, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    agent = harness.agent
    assert agent.bridge._ipc is not None
    monkeypatch.setattr(agent.bridge._ipc, "unavailable_reason", HELD)  # what IpcServer reports on Windows
    volume = Failing(None)
    volume.error = ProtocolError("OS_ERROR", "No audio output device is enabled on this PC")
    monkeypatch.setattr(agent.platform.volume, "get", volume)
    await agent.state.snapshot()

    status = agent.status()
    assert status["bridge_unavailable_reason"] == HELD
    bundle = build_bundle(harness.settings, status)
    assert bundle["status"]["bridge_unavailable_reason"] == HELD
    assert bundle["status"]["read_failures"]["volume"]["message"] == "No audio output device is enabled on this PC"

    assert await asyncio.to_thread(cli.main, ["--state-dir", str(harness.settings.state_dir), "status"]) == 0
    out = capsys.readouterr().out
    assert f"BROWSER BRIDGE NOT LISTENING: {HELD}" in out
    assert "volume read failing since" in out and "OS_ERROR: No audio output device is enabled on this PC" in out
