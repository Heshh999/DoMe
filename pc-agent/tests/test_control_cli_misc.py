from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from dome_agent import cli
from dome_agent.control import ControlClient, ControlError
from dome_agent.diagnostics import build_bundle
from dome_agent.logsetup import configure_logging, redact_event, redact_text_line
from dome_agent.settings import Settings, load_settings

from .conftest import AgentHarness


async def test_control_roundtrip_and_errors(harness: AgentHarness) -> None:
    ctl = ControlClient(harness.settings.state_dir)
    assert (await asyncio.to_thread(ctl.call, "ping"))["pong"] is True
    status = await asyncio.to_thread(ctl.call, "status")
    assert status["connection"] == "connected" and status["identity"]["linked"] is True
    assert (await asyncio.to_thread(ctl.call, "disable"))["remote_enabled"] is False
    assert harness.agent.store.remote_enabled is False
    assert (await asyncio.to_thread(ctl.call, "enable"))["remote_enabled"] is True
    with pytest.raises(ControlError) as ei:
        await asyncio.to_thread(ctl.call, "no_such_op")
    assert ei.value.code == "UNKNOWN_OP"
    with pytest.raises(ControlError) as ei:
        await asyncio.to_thread(ctl.call, "approve_app", app_id="x", exe_path="relative.exe")
    assert ei.value.code == "INVALID_APP"
    with pytest.raises(ControlError):
        await asyncio.to_thread(ctl.call, "pair_approve", pairing_id="00000000-0000-4000-8000-000000000000")


async def test_control_rejects_malformed(harness: AgentHarness) -> None:
    from dome_agent.bridge import ipc
    from dome_agent.bridge.framing import encode_frame

    conn = await asyncio.to_thread(ipc.connect, harness.settings.state_dir, 5.0, "control")
    try:
        conn.write_frame(encode_frame({"op": "status", "args": {}, "extra": 1}))
        raw = await asyncio.to_thread(conn.read_frame)
        assert raw is not None and json.loads(raw)["error"]["code"] == "MALFORMED_MESSAGE"
        conn.write_frame(encode_frame({"op": "DROP TABLE", "args": {}}))
        raw = await asyncio.to_thread(conn.read_frame)
        assert raw is not None and json.loads(raw)["error"]["code"] == "MALFORMED_MESSAGE"
    finally:
        conn.close()


def test_control_client_without_agent(settings: Settings) -> None:
    ctl = ControlClient(settings.state_dir, timeout=0.5)
    assert not ctl.is_running()
    with pytest.raises(ControlError) as ei:
        ctl.call("status")
    assert ei.value.code == "AGENT_NOT_RUNNING"


def test_redaction() -> None:
    event: dict[str, Any] = {
        "event": "token Bearer abcdefghijklmnop1234 used",
        "access_token": "secret",
        "pc_credential": "secret",
        "challenge_text": "{...}",
        "title": "Never log me",
        "nested": {"jwk": {"x": 1}, "ok": "fine", "payload": "signed"},
        "command_id": "11111111-1111-4111-8111-111111111111",
        "kid": "A" * 43,
        "code": "UNKNOWN_KEY",
    }
    out = redact_event(None, "info", dict(event))
    assert (
        out["access_token"] == "[redacted]"
        and out["pc_credential"] == "[redacted]"
        and out["challenge_text"] == "[redacted]"
    )
    assert (
        out["title"] == "[redacted]"
        and out["nested"]["jwk"] == "[redacted]"
        and out["nested"]["payload"] == "[redacted]"
    )
    assert out["nested"]["ok"] == "fine" and out["command_id"] == event["command_id"] and out["code"] == "UNKNOWN_KEY"
    assert "Bearer [redacted]" in out["event"] and "abcdefghijklmnop1234" not in out["event"]
    assert out["kid"] == "[redacted]"  # 43-char tokens are masked wholesale
    assert "[redacted]" in redact_text_line("Authorization: Bearer " + "x" * 50)


def test_log_file_is_redacted(settings: Settings) -> None:
    configure_logging("INFO", settings.log_path)
    logging.getLogger("dome_agent.test").info("stdlib line with Bearer %s", "SECRETSTDLIBTOKEN" * 3)
    import structlog

    structlog.get_logger("dome_agent.test2").info("structured", access_token="SECRETTOKEN", action="system.ping")
    for h in logging.getLogger().handlers:
        h.flush()
    text = settings.log_path.read_text()
    assert "SECRETTOKEN" not in text and "SECRETSTDLIBTOKEN" not in text and "system.ping" in text
    logging.getLogger().handlers.clear()


async def test_diagnostics_bundle_is_redacted(harness: AgentHarness) -> None:
    status = harness.agent.status()
    bundle = build_bundle(harness.settings, status)
    dumped = json.dumps(bundle)
    assert harness.agent.identity.read_credential() not in dumped  # type: ignore[operator]
    for g in bundle["status"]["grants"]:
        assert len(g["kid"]) < 43
    assert bundle["agent_version"] and bundle["settings"]["state_dir"]
    path = await asyncio.to_thread(cli.main, ["--state-dir", str(harness.settings.state_dir), "diagnostics"])
    assert path == 0
    files = list((harness.settings.state_dir / "diagnostics").glob("*.json"))
    assert files


def test_cli_version_and_status_without_agent(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["version"]) == 0
    assert "protocol 1.0" in capsys.readouterr().out
    assert cli.main(["--state-dir", str(settings.state_dir), "status"]) == 0
    out = capsys.readouterr().out
    assert "not running" in out and "linked: False" in out
    assert cli.main(["--state-dir", str(settings.state_dir), "disable"]) == 0
    assert cli.main(["--state-dir", str(settings.state_dir), "status", "--json"]) == 0
    data = json.loads(capsys.readouterr().out.split("Remote control DISABLED on this PC.\n")[-1])
    assert data["store"]["remote_enabled"] is False


def test_cli_approve_app_without_agent(settings: Settings, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exe = tmp_path / "bin" / "tool.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    assert (
        cli.main(["--state-dir", str(settings.state_dir), "approve-app", "tool", str(exe)]) == 1
    )  # under the pytest tmp dir → temp folder
    assert "temporary" in capsys.readouterr().err
    assert cli.main(["--state-dir", str(settings.state_dir), "approve-app", "tool", "tool.exe --arg"]) == 1


def test_settings_rejects_unknown_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOME_AGENT_PLATFORM", "windows")
    with pytest.raises(ValueError):
        load_settings()
    monkeypatch.setenv("DOME_AGENT_PLATFORM", "fake")
    assert load_settings().use_fake_platform


def test_secret_files_are_0600(settings: Settings) -> None:
    from dome_agent.identity import Identity

    identity = Identity(settings.state_dir)
    identity.ensure_key()
    key_file = settings.secrets_dir / "pc_key.bin"
    assert key_file.exists() and (key_file.stat().st_mode & 0o777) == 0o600
    assert identity.kid and len(identity.kid) == 43
    assert Identity(settings.state_dir).kid == identity.kid  # stable across loads


def test_state_frame_matches_schema(settings: Settings) -> None:
    from dome_protocol import load_schemas

    from dome_agent.store import Store
    from dome_agent.testing.fake_platform import FakeState, build_fake_platform

    st = FakeState()
    st.add_session("Spotify#0", title="A song " * 50)
    store = Store(settings.db_path)
    try:
        from dome_agent.bridge.server import BridgeServer
        from dome_agent.state import StateAggregator

        agg = StateAggregator(store, build_fake_platform(st), BridgeServer(settings.state_dir))
        snap = asyncio.run(agg.snapshot())
    finally:
        store.close()
    load_schemas().validate_def("relay-frames", "pc_state", snap)
    assert snap["platform"] == "development" and snap["media_sessions"][0]["title"].__len__() <= 200
