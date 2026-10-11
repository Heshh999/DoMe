"""Approving apps on the PC: ``Agent.approve_app_local`` / ``remove_app_local`` serve both the control channel
(``dome-agent approve-app``) and the tray's Approved apps window, hash off the event loop, refresh the phone's
state, and never silently point an ``app_id`` at another program."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest
from dome_protocol import load_schemas

from dome_agent import cli
from dome_agent.approved_apps import AppIdTakenError, ApprovalError, sha256_file, suggest_app_id
from dome_agent.control import ControlClient, ControlError
from dome_agent.settings import Settings

from .conftest import AgentHarness
from .helpers import Controller, payload_of


def _program(root: Path, folder: str, name: str, content: bytes = b"MZ") -> Path:
    exe = root / "Programs" / folder / name
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_bytes(content)
    return exe


@pytest.fixture
def programs(harness: AgentHarness, tmp_path: Path) -> Path:
    # Outside every temp folder the agent refuses (pytest's tmp_path lives under the system temp dir).
    harness.agent.apps._temp_dirs = (tmp_path / "Temp",)  # noqa: SLF001
    return tmp_path


async def _app_list(h: AgentHarness, controller: Controller) -> list[dict[str, Any]]:
    env = controller.command("app.list", {}, None)
    cid = payload_of(env)["command_id"]
    await h.send_command(env)
    res = await h.result(cid)
    load_schemas().validate_frame("agent_to_relay", res)
    assert res["state"] == "succeeded"
    apps: list[dict[str, Any]] = res["result"]["apps"]
    return apps


async def test_add_collide_and_remove_through_the_shared_local_path(
    harness: AgentHarness, controller: Controller, programs: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = harness.agent
    loop_thread = threading.current_thread()
    hashed_on: list[threading.Thread] = []
    updates: list[bool] = []

    def recording_hash(path: Path) -> str:
        hashed_on.append(threading.current_thread())
        return sha256_file(path)

    def recording_update() -> None:
        asyncio.get_running_loop()  # StateAggregator.request_update is a no-op off the loop
        updates.append(True)

    monkeypatch.setattr("dome_agent.approved_apps.sha256_file", recording_hash)
    monkeypatch.setattr(agent.state, "request_update", recording_update)

    spotify = _program(programs, "Spotify", "spotify.exe", b"MZ spotify")
    app_id = suggest_app_id("spotify", (r.app_id for r in agent.apps.list()))
    row = await agent.approve_app_local(app_id, str(spotify), "Spotify")
    assert (row.app_id, row.display_name, row.exe_path) == ("spotify", "Spotify", str(spotify.resolve()))
    assert hashed_on and all(t is not loop_thread for t in hashed_on)  # never on the event loop
    assert updates == [True]

    # A second spotify.exe gets the next free id; asking for the taken id is refused, nothing overwritten.
    other = _program(programs, "Other", "spotify.exe", b"MZ another spotify")
    with pytest.raises(AppIdTakenError):
        await agent.approve_app_local("spotify", str(other), "Spotify (other)")
    assert agent.apps.get("spotify") == row
    second_id = suggest_app_id("spotify", (r.app_id for r in agent.apps.list()))
    assert second_id == "spotify-2"
    await agent.approve_app_local(second_id, str(other), "Spotify 2")
    listed = await _app_list(harness, controller)
    assert {(a["app_id"], a["display_name"]) for a in listed} == {("spotify", "Spotify"), ("spotify-2", "Spotify 2")}

    # Re-approve after an update keeps the id; "Find it again" moves it explicitly.
    spotify.write_bytes(b"MZ spotify, updated")
    assert agent.apps.statuses()["spotify"] == "changed"
    await agent.approve_app_local("spotify", str(spotify), "Spotify")
    assert agent.apps.statuses()["spotify"] == "ok"
    moved = _program(programs, "Spotify-new", "spotify.exe", b"MZ spotify moved")
    assert (await agent.approve_app_local("spotify", str(moved), "Spotify", replace=True)).exe_path == str(
        moved.resolve()
    )

    assert await agent.remove_app_local("spotify-2") is True
    assert await agent.remove_app_local("spotify-2") is False
    assert [a["app_id"] for a in await _app_list(harness, controller)] == ["spotify"]
    assert len(updates) == 6  # every add, re-approve, move and remove asked for a state update


async def test_temp_folder_programs_are_refused_through_the_local_path(harness: AgentHarness, programs: Path) -> None:
    dropper = programs / "Temp" / "setup.exe"
    dropper.parent.mkdir()
    dropper.write_bytes(b"MZ")
    with pytest.raises(ApprovalError) as ei:
        await harness.agent.approve_app_local("setup", str(dropper))
    assert ei.value.code == "TEMP_DIR" and harness.agent.apps.list() == []


async def test_control_channel_uses_the_same_rules(harness: AgentHarness, programs: Path) -> None:
    ctl = ControlClient(harness.settings.state_dir)
    tool = _program(programs, "Tool", "tool.exe")
    other = _program(programs, "Tool2", "tool.exe", b"MZ other")
    row = await asyncio.to_thread(ctl.call, "approve_app", app_id="tool", exe_path=str(tool), display_name="Tool")
    assert row["exe_path"] == str(tool.resolve())
    with pytest.raises(ControlError) as ei:
        await asyncio.to_thread(ctl.call, "approve_app", app_id="tool", exe_path=str(other))
    assert ei.value.code == "INVALID_APP" and "--replace" in ei.value.message
    row = await asyncio.to_thread(ctl.call, "approve_app", app_id="tool", exe_path=str(other), replace=True)
    assert row["exe_path"] == str(other.resolve())
    with pytest.raises(ControlError) as ei:
        await asyncio.to_thread(ctl.call, "remove_app", app_id="Not An Id")
    assert ei.value.code == "INVALID_APP"  # was an INTERNAL error before
    assert (await asyncio.to_thread(ctl.call, "remove_app", app_id="tool"))["removed"] is True


def test_cli_replace_flag_without_agent(
    settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("dome_agent.approved_apps.default_temp_dirs", lambda: (tmp_path / "Temp",))
    first = _program(tmp_path, "A", "tool.exe")
    second = _program(tmp_path, "B", "tool.exe", b"MZ b")
    base = ["--state-dir", str(settings.state_dir), "approve-app", "tool"]
    assert cli.main([*base, str(first)]) == 0
    capsys.readouterr()
    assert cli.main([*base, str(second)]) == 1
    assert "--replace" in capsys.readouterr().err
    assert cli.main([*base, str(second), "--replace"]) == 0
    assert str(second.resolve()) in capsys.readouterr().out
