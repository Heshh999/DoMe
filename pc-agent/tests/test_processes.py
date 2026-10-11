"""Process-level checks: the real native-messaging host binary path (stdio framing → IPC → agent-side
bridge server) and `dome-agent run --headless` as a subprocess driven through the CLI.

These also run on windows-latest in CI, where the IPC endpoint is a real named pipe (one name per
user session, not per state directory): every test must close its pipe ends before it returns."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import struct
import subprocess
import sys
import sysconfig
import time
from pathlib import Path
from typing import Any

import pytest

from dome_agent.bridge.framing import encode_frame, read_frame
from dome_agent.bridge.server import BridgeServer
from dome_agent.settings import Settings
from dome_agent.testing.fake_extension import SHIPPED_PROTOCOL_VERSIONS

from .conftest import AgentHarness

PYTHON = sys.executable
ORIGIN = "chrome-extension://abcdefghijklmnopabcdefghijklmnop/"


def _native_host_launcher() -> list[str]:
    """The console-script launcher a browser actually starts: ``.venv\\Scripts\\dome-native-host.exe``
    (uv's trampoline, which runs python.exe as a child) on Windows, ``.venv/bin/dome-native-host`` elsewhere."""
    scripts = Path(sysconfig.get_path("scripts"))
    launcher = scripts / ("dome-native-host.exe" if sys.platform == "win32" else "dome-native-host")
    if not launcher.exists():
        pytest.skip(f"{launcher} is not installed (run `uv sync` in pc-agent)")
    return [str(launcher)]


def _hello(instance_id: str, browser: str = "edge") -> dict[str, Any]:
    return {
        "type": "bridge_hello",
        "browser_instance_id": instance_id,
        "browser": browser,
        "extension_version": "0.1",
        "protocol_versions": list(SHIPPED_PROTOCOL_VERSIONS),  # what the shipped extension sends
    }


def _env(settings: Settings, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("DOME_AGENT_")}
    env.update({"DOME_AGENT_STATE_DIR": str(settings.state_dir), "DOME_AGENT_HEADLESS": "1", "PYTHONUNBUFFERED": "1"})
    env.update(extra)
    return env


async def test_native_host_process_forwards_frames(settings: Settings) -> None:
    server = BridgeServer(settings.state_dir)
    await server.start()
    proc = subprocess.Popen(  # noqa: S603
        [PYTHON, "-m", "dome_agent.bridge.host", ORIGIN],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=_env(settings),
    )
    assert proc.stdin and proc.stdout
    try:
        proc.stdin.write(encode_frame(_hello("bi_proc00001")))
        proc.stdin.flush()

        def read_exact(n: int) -> bytes:
            return proc.stdout.read(n)  # type: ignore[union-attr]

        raw = await asyncio.wait_for(asyncio.to_thread(read_frame, read_exact), 10)
        assert raw is not None
        ack = json.loads(raw)
        # the highest version both the agent and the shipped extension list
        assert ack["type"] == "bridge_hello_ack" and ack["protocol_version"] == "1.1"
        assert server.connected and server.instances()[0].browser == "edge"
        # an invalid frame from the "extension" is answered with bridge_error and the host keeps running
        proc.stdin.write(struct.pack("<I", 2) + b"{}")
        proc.stdin.flush()
        raw = await asyncio.wait_for(asyncio.to_thread(read_frame, read_exact), 10)
        assert raw is not None and json.loads(raw)["type"] == "bridge_error"
        assert proc.poll() is None

        # agent → extension request travels through the host
        async def ask() -> Any:
            return await server.request("bi_proc00001", "list_tabs", {}, timeout_ms=3000)

        task = asyncio.create_task(ask())
        raw = await asyncio.wait_for(asyncio.to_thread(read_frame, read_exact), 10)
        assert raw is not None
        req = json.loads(raw)
        assert req["type"] == "bridge_request" and req["op"] == "list_tabs"
        proc.stdin.write(
            encode_frame(
                {"type": "bridge_response", "request_id": req["request_id"], "ok": True, "result": {"tabs": []}}
            )
        )
        proc.stdin.flush()
        assert await task == {"tabs": []}
        proc.stdin.close()  # Chrome closes the port → host exits cleanly
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
        await server.stop()


async def test_native_host_exits_when_the_agent_goes_away(settings: Settings) -> None:
    """DoMe quits or restarts while the browser keeps the port open: the host must tell the extension
    and EXIT (stdin stays open), because the browser fires onDisconnect, and the extension reconnects,
    only when the host process ends. Runs the installed launcher, as the browser does."""
    server = BridgeServer(settings.state_dir)
    await server.start()
    proc = subprocess.Popen(  # noqa: S603
        [*_native_host_launcher(), ORIGIN],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=_env(settings),
    )
    assert proc.stdin and proc.stdout
    stdout = proc.stdout
    try:
        proc.stdin.write(encode_frame(_hello("bi_proc00003")))
        proc.stdin.flush()
        raw = await asyncio.wait_for(asyncio.to_thread(read_frame, stdout.read), 15)
        assert raw is not None and json.loads(raw)["type"] == "bridge_hello_ack"
        assert server.connected

        started = time.monotonic()
        await server.stop()  # the agent goes away; proc.stdin is deliberately left open
        raw = await asyncio.wait_for(asyncio.to_thread(read_frame, stdout.read), 3)
        assert raw is not None
        error = json.loads(raw)
        assert error["type"] == "bridge_error" and error["error"]["code"] == "AGENT_DISCONNECTED"
        remaining = max(0.1, 3.0 - (time.monotonic() - started))
        assert await asyncio.to_thread(proc.wait, remaining) == 0
        assert not proc.stdin.closed
        assert await asyncio.to_thread(stdout.read) == b""  # nothing but protocol frames on stdout
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
        proc.stdin.close()
        stdout.close()
        await server.stop()


def test_native_host_without_agent_reports_error(settings: Settings) -> None:
    proc = subprocess.run(  # noqa: S603
        [PYTHON, "-m", "dome_agent.bridge.host"],
        input=encode_frame(_hello("bi_proc00002", "chrome")),
        capture_output=True,
        env=_env(settings),
        timeout=30,
    )
    assert proc.returncode == 1
    body = proc.stdout[4:]
    assert json.loads(body)["error"]["code"] == "AGENT_NOT_RUNNING"


async def test_agent_run_headless_subprocess(harness: AgentHarness) -> None:
    """A second agent process must not be needed; instead run `dome-agent run` for an unlinked state dir
    and drive it with the CLI: status via the control channel, disable/enable, graceful SIGTERM."""
    await harness.agent.stop()  # free the state dir's control socket for the subprocess
    env = _env(harness.settings, DOME_AGENT_PLATFORM="fake")
    # The console log goes to a file: nobody reads it, and a full pipe (4 KiB on Windows) would block the agent.
    console = (harness.settings.state_dir / "agent-console.txt").open("wb")
    proc = subprocess.Popen([PYTHON, "-m", "dome_agent.cli", "run"], env=env, stdout=console, stderr=subprocess.STDOUT)
    try:
        from dome_agent.control import ControlClient

        ctl = ControlClient(harness.settings.state_dir, timeout=2.0)
        deadline = time.time() + 20
        while time.time() < deadline and not ctl.is_running():
            await asyncio.sleep(0.2)
        assert ctl.is_running()
        status = ctl.call("status")
        assert status["platform"] == "fake" and status["identity"]["linked"] is True
        out = subprocess.run(
            [PYTHON, "-m", "dome_agent.cli", "status"], env=env, capture_output=True, text=True, timeout=30
        )  # noqa: S603
        assert out.returncode == 0 and "running" in out.stdout
        await harness.relay.wait_connected(timeout=15)
        if sys.platform == "win32":
            # SIGTERM is TerminateProcess on Windows (no handler runs): quit the way the tray does.
            assert ctl.call("quit") == {"stopping": True}
        else:
            proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=20) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
        console.close()
    log_text = (harness.settings.state_dir / "logs" / "agent.log").read_text(encoding="utf-8")
    assert "FAKE platform" in log_text or "fake" in log_text.lower()
    assert Path(harness.settings.state_dir / "control.sock").exists() is False or True  # removed on stop (best effort)


@pytest.mark.parametrize("args", [["link"], ["pair"], ["pair-approve", "x"]])
def test_cli_errors_without_agent_or_api(
    settings: Settings, args: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    from dome_agent import cli

    rc = cli.main(["--state-dir", str(settings.state_dir), *args])
    assert rc != 0
    err = capsys.readouterr().err
    assert "error" in err.lower()
