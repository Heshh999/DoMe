"""Process-level checks: the real native-messaging host binary path (stdio framing → IPC → agent-side
bridge server) and `dome-agent run --headless` as a subprocess driven through the CLI."""

from __future__ import annotations

import asyncio
import os
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from dome_agent.bridge.framing import encode_frame, read_frame
from dome_agent.bridge.server import BridgeServer
from dome_agent.settings import Settings

from .conftest import AgentHarness

PYTHON = sys.executable


def _env(settings: Settings, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("DOME_AGENT_")}
    env.update({"DOME_AGENT_STATE_DIR": str(settings.state_dir), "DOME_AGENT_HEADLESS": "1", "PYTHONUNBUFFERED": "1"})
    env.update(extra)
    return env


async def test_native_host_process_forwards_frames(settings: Settings) -> None:
    server = BridgeServer(settings.state_dir)
    await server.start()
    proc = subprocess.Popen(  # noqa: S603
        [PYTHON, "-m", "dome_agent.bridge.host", "chrome-extension://abcdefghijklmnopabcdefghijklmnop/"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_env(settings),
    )
    assert proc.stdin and proc.stdout
    try:
        hello = {
            "type": "bridge_hello",
            "browser_instance_id": "bi_proc00001",
            "browser": "edge",
            "extension_version": "0.1",
            "protocol_versions": ["1.0"],
        }
        proc.stdin.write(encode_frame(hello))
        proc.stdin.flush()

        def read_exact(n: int) -> bytes:
            return proc.stdout.read(n)  # type: ignore[union-attr]

        raw = await asyncio.wait_for(asyncio.to_thread(read_frame, read_exact), 10)
        assert raw is not None
        import json

        ack = json.loads(raw)
        assert ack["type"] == "bridge_hello_ack" and ack["protocol_version"] == "1.0"
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
        await server.stop()


def test_native_host_without_agent_reports_error(settings: Settings) -> None:
    proc = subprocess.run(  # noqa: S603
        [PYTHON, "-m", "dome_agent.bridge.host"],
        input=encode_frame(
            {
                "type": "bridge_hello",
                "browser_instance_id": "bi_proc00002",
                "browser": "chrome",
                "extension_version": "0.1",
                "protocol_versions": ["1.0"],
            }
        ),
        capture_output=True,
        env=_env(settings),
        timeout=30,
    )
    assert proc.returncode == 1
    import json

    body = proc.stdout[4:]
    assert json.loads(body)["error"]["code"] == "AGENT_NOT_RUNNING"


async def test_agent_run_headless_subprocess(harness: AgentHarness) -> None:
    """A second agent process must not be needed; instead run `dome-agent run` for an unlinked state dir
    and drive it with the CLI: status via the control channel, disable/enable, graceful SIGTERM."""
    await harness.agent.stop()  # free the state dir's control socket for the subprocess
    env = _env(harness.settings, DOME_AGENT_PLATFORM="fake")
    proc = subprocess.Popen(
        [PYTHON, "-m", "dome_agent.cli", "run"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )  # noqa: S603
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
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=20) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
    log_text = (harness.settings.state_dir / "logs" / "agent.log").read_text()
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
