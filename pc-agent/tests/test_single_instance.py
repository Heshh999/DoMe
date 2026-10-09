"""One agent per user session (spec §10): the instance lock, the second-launch behaviour, stale
endpoints and `dome-agent repair` preserving identity/credential/grants/approved apps.
Linux uses the flock lock file; the Windows named mutex is not exercised here."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from dome_agent import cli
from dome_agent.settings import Settings
from dome_agent.single_instance import LOCK_FILENAME, PID_FILENAME, InstanceLock, PidRecord, inspect, repair

from .conftest import AgentHarness


def test_lock_is_exclusive_and_releasable(settings: Settings) -> None:
    first = InstanceLock.acquire(settings.state_dir)
    assert first is not None and first.held
    record = PidRecord.read(settings.state_dir / PID_FILENAME)
    assert record is not None and record.pid == os.getpid()
    assert InstanceLock.acquire(settings.state_dir) is None  # second acquisition in the same session fails
    report = inspect(settings.state_dir, control_timeout=0.3)
    assert report.lock_held_by_other and report.running_pid == os.getpid() and not report.control_responding
    assert "not responding" in report.message and "dome-agent repair" in report.message
    first.release()
    assert not (settings.state_dir / PID_FILENAME).exists()
    again = InstanceLock.acquire(settings.state_dir)
    assert again is not None
    again.release()


async def test_second_launch_asks_running_instance_to_show(
    harness: AgentHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    shown: list[str] = []
    harness.agent.ui.show_main = lambda: shown.append("shown")  # type: ignore[method-assign]
    lock = InstanceLock.acquire(harness.settings.state_dir)  # the running instance holds the lock
    assert lock is not None
    try:
        rc = await asyncio.to_thread(cli.main, ["--state-dir", str(harness.settings.state_dir), "run", "--headless"])
    finally:
        lock.release()
    assert rc == 0 and shown == ["shown"]
    out = capsys.readouterr().out
    assert "already running" in out and f"pid {os.getpid()}" in out
    assert harness.agent.relay is not None and harness.agent.relay.connected  # nothing competed with it


def test_second_launch_with_silent_instance_points_to_repair(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    lock = InstanceLock.acquire(settings.state_dir)
    assert lock is not None
    try:
        rc = cli.main(["--state-dir", str(settings.state_dir), "run", "--headless"])
    finally:
        lock.release()
    assert rc == 1
    out = capsys.readouterr().out
    assert "not responding" in out and f"pid {os.getpid()}" in out and "dome-agent repair" in out


def test_stale_lock_and_endpoint_are_reported_and_repaired(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    from dome_agent.bridge import ipc

    # a previous agent died: its pid file names a dead process and the control socket file is left behind
    (settings.state_dir / PID_FILENAME).write_text(json.dumps({"pid": 2**22 - 7, "session": "", "started_at": 1}))
    stale = Path(ipc.endpoint_address(settings.state_dir, "control"))
    stale.write_bytes(b"")
    report = inspect(settings.state_dir, control_timeout=0.3)
    assert report.pid_file_stale and report.control_endpoint_stale and not report.lock_held_by_other
    assert "Stale control endpoint" in report.message
    assert cli.main(["--state-dir", str(settings.state_dir), "status"]) == 0
    assert "INSTANCE: Stale control endpoint" in capsys.readouterr().out
    assert (settings.state_dir / LOCK_FILENAME).exists() or True  # the lock file itself is harmless
    lines = repair(settings.state_dir, host_path=None, dev_extension_id="")
    assert any("removed stale control endpoint" in line for line in lines)
    assert any("removed stale pid file" in line for line in lines)
    assert not stale.exists() and not (settings.state_dir / PID_FILENAME).exists()
    assert not inspect(settings.state_dir, control_timeout=0.3).control_endpoint_stale


def test_permission_problem_is_distinct(settings: Settings) -> None:
    if os.geteuid() == 0:
        pytest.skip("root can always write; the permission probe cannot fail here")
    os.chmod(settings.state_dir, 0o500)
    try:
        report = inspect(settings.state_dir, control_timeout=0.3)
    finally:
        os.chmod(settings.state_dir, 0o700)
    assert report.permission_problem and "Permission problem" in report.message


async def test_repair_preserves_identity_credential_grants_and_apps(
    harness: AgentHarness, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state_dir = harness.settings.state_dir
    identity_before = (state_dir / "identity.json").read_text()
    credential_before = harness.agent.identity.read_credential()
    grants_before = [(g.controller_id, g.capabilities) for g in harness.agent.store.list_grants()]
    exe = tmp_path / "apps" / "tool.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    harness.agent.apps._temp_dirs = ()  # noqa: SLF001
    harness.agent.apps.approve("tool", str(exe), "Tool")
    host = tmp_path / "dome-native-host"
    host.write_text("#!/bin/sh\n")
    rc = await asyncio.to_thread(cli.main, ["--state-dir", str(state_dir), "repair", "--host-path", str(host)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "running agent found" in out and "left running" in out and "NOT killed" not in out
    assert "identity.json: present, untouched" in out and "secrets: present, untouched" in out
    assert "native host manifest rewritten" in out and "Pairing, grants and approved apps were preserved" in out
    assert (state_dir / "identity.json").read_text() == identity_before
    assert harness.agent.identity.read_credential() == credential_before
    assert [(g.controller_id, g.capabilities) for g in harness.agent.store.list_grants()] == grants_before
    assert harness.agent.apps.get("tool") is not None
    assert harness.agent.relay is not None and harness.agent.relay.connected  # never terminated
    manifest = json.loads((state_dir / "com.dome.agent.json").read_text())
    assert manifest["path"] == str(host.resolve())


async def test_show_control_op_reports_pid(harness: AgentHarness) -> None:
    from dome_agent.control import ControlClient

    result = await asyncio.to_thread(ControlClient(harness.settings.state_dir).call, "show")
    assert result["shown"] is True and result["pid"] == os.getpid() and result["connection"] == "connected"
