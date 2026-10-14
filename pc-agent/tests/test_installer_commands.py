"""The commands Setup and its uninstaller run (pc-agent/packaging/installer/DoMe.iss): ``quit``,
``start-at-login``, ``install-native-host`` / ``uninstall-native-host`` keeping an already registered
extension, Setup's ``dome-server.json`` for ``link``, the stdout guard of a windowed DoMe.exe and the
crash guard of the frozen entry points.

Nothing here touches the real registry: every test that reaches the start-at-login or native-host
adapter replaces it with a recorder. On Windows the control pipe and the instance mutex belong to the
user SESSION, not to a state directory, so a DoMe running on this PC would answer these tests (and
``quit`` would stop it): the tests that talk to an agent skip when one is already running there."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from dome_agent import cli, crashguard
from dome_agent.bridge.manifest import (
    HOST_NAME,
    allowed_origins,
    registered_origins,
    resolve_origins,
    write_manifest,
)
from dome_agent.settings import Settings, installed_api_url, installed_server_file, load_settings
from dome_agent.single_instance import PID_FILENAME, InstanceLock, lock_held_by_other, wait_for_exit

ID_A = "abcdefghijklmnopabcdefghijklmnop"
ID_B = "ponmlkjihgfedcbaponmlkjihgfedcba"
ORIGIN_A = f"chrome-extension://{ID_A}/"
ORIGIN_B = f"chrome-extension://{ID_B}/"
TESTS_DIR = Path(__file__).resolve().parent


def _agent_already_running(state_dir: Path) -> bool:
    """True on Windows when a DoMe agent already runs in this user session (it would answer us)."""
    if sys.platform != "win32":
        return False
    from dome_agent.control import ControlClient

    return ControlClient(state_dir, timeout=1.0).is_running() or lock_held_by_other(state_dir)


def _skip_if_agent_running(state_dir: Path) -> None:
    if _agent_already_running(state_dir):
        pytest.skip("a DoMe agent is running in this Windows session; these tests would talk to it")


class StartupRecorder:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled
        self.calls: list[tuple[bool, str]] = []

    def get_start_at_login(self) -> bool:
        return self.enabled

    def set_start_at_login(self, enabled: bool, command: str) -> None:
        self.calls.append((enabled, command))
        self.enabled = enabled


class RegistrarRecorder:
    def __init__(self, fail: BaseException | None = None) -> None:
        self.fail = fail
        self.installed: list[Path] = []
        self.uninstalled = 0

    def install(self, manifest_path: Path) -> list[str]:
        if self.fail is not None:
            raise self.fail
        self.installed.append(manifest_path)
        return [r"HKCU\Software\Google\Chrome\NativeMessagingHosts\com.dome.agent"]

    def uninstall(self) -> list[str]:
        if self.fail is not None:
            raise self.fail
        self.uninstalled += 1
        return [r"HKCU\Software\Google\Chrome\NativeMessagingHosts\com.dome.agent"]


@pytest.fixture
def startup(monkeypatch: pytest.MonkeyPatch) -> StartupRecorder:
    import dome_agent.platform
    import dome_agent.tray

    recorder = StartupRecorder()
    monkeypatch.setattr(dome_agent.platform, "build_startup", lambda settings: recorder)
    monkeypatch.setattr(dome_agent.tray, "startup_command", lambda: r'"C:\Apps\DoMe\DoMe.exe" run')
    return recorder


@pytest.fixture
def registrar(monkeypatch: pytest.MonkeyPatch) -> RegistrarRecorder:
    import dome_agent.platform

    recorder = RegistrarRecorder()
    monkeypatch.setattr(dome_agent.platform, "build_native_host_registrar", lambda settings: recorder)
    return recorder


@pytest.fixture
def host_exe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import dome_agent.tray

    exe = tmp_path / "app" / "dome-native-host.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(dome_agent.tray, "host_executable_path", lambda: exe)
    return exe


# ----- quit ---------------------------------------------------------------------------------------------------------


def test_quit_when_nothing_runs(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    _skip_if_agent_running(settings.state_dir)
    assert cli.main(["--state-dir", str(settings.state_dir), "quit", "--timeout", "2"]) == 0
    assert "not running" in capsys.readouterr().out


def test_quit_stops_a_running_agent_process(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    _skip_if_agent_running(settings.state_dir)
    env = {k: v for k, v in os.environ.items() if not k.startswith("DOME_AGENT_")}
    env.update(
        {
            "DOME_AGENT_STATE_DIR": str(settings.state_dir),
            "DOME_AGENT_HEADLESS": "1",
            "DOME_AGENT_PLATFORM": "fake",
            "PYTHONUNBUFFERED": "1",
        }
    )
    console = (settings.state_dir / "agent-console.txt").open("wb")
    proc = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "dome_agent.cli", "run"], env=env, stdout=console, stderr=subprocess.STDOUT
    )
    try:
        from dome_agent.control import ControlClient

        ctl = ControlClient(settings.state_dir, timeout=2.0)
        deadline = time.time() + 30
        while time.time() < deadline and not ctl.is_running():
            time.sleep(0.2)
        assert ctl.is_running()
        assert (settings.state_dir / PID_FILENAME).exists()
        assert cli.main(["--state-dir", str(settings.state_dir), "quit", "--timeout", "30"]) == 0
        assert "DoMe has quit." in capsys.readouterr().out
        assert proc.wait(timeout=20) == 0  # it shut down cleanly: nothing was killed
        assert not (settings.state_dir / PID_FILENAME).exists()
        assert not lock_held_by_other(settings.state_dir)
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)
        console.close()


def test_quit_refuses_when_the_running_agent_does_not_answer(
    settings: Settings, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _skip_if_agent_running(settings.state_dir)
    monkeypatch.setattr(cli, "QUIT_PING_PATIENCE", 0.5)
    lock = InstanceLock.acquire(settings.state_dir)  # "an agent" holds the lock but serves no control channel
    assert lock is not None
    try:
        rc = cli.main(["--state-dir", str(settings.state_dir), "quit", "--timeout", "1"])
    finally:
        lock.release()
    assert rc == 1
    assert "did not answer, so it was not stopped" in capsys.readouterr().out


def test_wait_for_exit_gives_up_without_killing(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    import dome_agent.single_instance as single_instance

    monkeypatch.setattr(single_instance, "_pid_alive", lambda pid: True)
    started = time.monotonic()
    assert wait_for_exit(settings.state_dir, os.getpid() + 1, timeout=0.3, poll=0.05) is False
    assert time.monotonic() - started >= 0.3
    monkeypatch.setattr(single_instance, "_pid_alive", lambda pid: False)
    assert wait_for_exit(settings.state_dir, os.getpid() + 1, timeout=0.3, poll=0.05) is True
    assert wait_for_exit(settings.state_dir, None, timeout=0.3, poll=0.05) is True


# ----- start-at-login ------------------------------------------------------------------------------------------------


def test_start_at_login_on_off_status(
    settings: Settings, startup: StartupRecorder, capsys: pytest.CaptureFixture[str]
) -> None:
    base = ["--state-dir", str(settings.state_dir), "start-at-login"]
    assert cli.main([*base, "status"]) == 0
    assert capsys.readouterr().out.strip() == "off"
    assert cli.main([*base, "on"]) == 0
    assert startup.calls == [(True, r'"C:\Apps\DoMe\DoMe.exe" run')]
    assert "Start at login is on" in capsys.readouterr().out
    assert cli.main([*base, "status"]) == 0
    assert capsys.readouterr().out.strip() == "on"
    assert cli.main([*base, "off"]) == 0
    assert startup.calls[-1] == (False, "")
    assert "Start at login is off." in capsys.readouterr().out


def test_start_at_login_reports_a_registry_failure(
    settings: Settings, startup: StartupRecorder, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(enabled: bool, command: str) -> None:
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(startup, "set_start_at_login", fail)
    assert cli.main(["--state-dir", str(settings.state_dir), "start-at-login", "on"]) == 1
    assert "Access is denied" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform == "win32", reason="would use the real HKCU Run key")
def test_start_at_login_on_an_unsupported_platform(settings: Settings, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--state-dir", str(settings.state_dir), "start-at-login", "status"]) == 0
    assert capsys.readouterr().out.strip() == "off"
    assert cli.main(["--state-dir", str(settings.state_dir), "start-at-login", "on"]) == 1
    assert "error" in capsys.readouterr().err


def test_uninstaller_commands_create_no_state_folder(
    tmp_path: Path, startup: StartupRecorder, registrar: RegistrarRecorder
) -> None:
    """Uninstalling DoMe for someone who never started it must not leave an empty state folder."""
    state = tmp_path / "never-created"
    assert cli.main(["--state-dir", str(state), "uninstall-native-host"]) == 0
    assert cli.main(["--state-dir", str(state), "start-at-login", "off"]) == 0
    assert not state.exists()


# ----- native host registration --------------------------------------------------------------------------------------


def test_registered_origins_trusts_only_a_well_formed_manifest(tmp_path: Path) -> None:
    path = tmp_path / f"{HOST_NAME}.json"
    assert registered_origins(path) == []  # missing
    path.write_text("not json", encoding="utf-8")
    assert registered_origins(path) == []
    path.write_text(json.dumps(["chrome-extension://x/"]), encoding="utf-8")
    assert registered_origins(path) == []  # not an object
    path.write_text(json.dumps({"name": "other.host", "allowed_origins": [ORIGIN_A]}), encoding="utf-8")
    assert registered_origins(path) == []  # another host's manifest
    for bad in ([], "chrome-extension://x/", [ORIGIN_A, "https://evil.example/"], [ORIGIN_A[:-1]], [1]):
        path.write_text(json.dumps({"name": HOST_NAME, "allowed_origins": bad}), encoding="utf-8")
        assert registered_origins(path) == [], bad
    path.write_text(json.dumps({"name": HOST_NAME, "allowed_origins": [ORIGIN_A, ORIGIN_B, ORIGIN_A]}), "utf-8")
    assert registered_origins(path) == [ORIGIN_A, ORIGIN_B]


def test_resolve_origins_order(tmp_path: Path) -> None:
    path = tmp_path / f"{HOST_NAME}.json"
    with pytest.raises(ValueError, match="no extension id available"):
        resolve_origins(path)
    write_manifest(path, tmp_path / "host.exe", [ORIGIN_B])
    assert resolve_origins(path) == [ORIGIN_B]  # the extension already registered here is kept
    assert resolve_origins(path, ID_A) == [ORIGIN_A]  # an explicit id wins over the registered one
    with pytest.raises(ValueError, match="32-character"):
        resolve_origins(path, "not-an-id")
    assert resolve_origins(path, production_ids=(ID_A,)) == [ORIGIN_A]  # a release build ignores the file
    assert resolve_origins(path, ID_B, production_ids=(ID_A,)) == allowed_origins(ID_B, (ID_A,))


def test_install_native_host_keeps_the_registered_extension(
    settings: Settings, host_exe: Path, registrar: RegistrarRecorder, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest = settings.state_dir / f"{HOST_NAME}.json"
    write_manifest(manifest, Path("C:/old/dome-native-host.exe"), [ORIGIN_B])
    assert cli.main(["--state-dir", str(settings.state_dir), "install-native-host"]) == 0
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["allowed_origins"] == [ORIGIN_B] and data["path"] == str(host_exe)
    assert registrar.installed == [manifest]
    assert "Registered" in capsys.readouterr().out


def test_install_native_host_without_any_extension_id(
    tmp_path: Path, host_exe: Path, registrar: RegistrarRecorder, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "fresh"
    assert cli.main(["--state-dir", str(state), "install-native-host"]) == 1
    assert "no extension id available" in capsys.readouterr().err
    assert registrar.installed == [] and not state.exists()


def test_install_native_host_with_a_development_id(
    settings: Settings,
    host_exe: Path,
    registrar: RegistrarRecorder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOME_AGENT_DEV_EXTENSION_ID", ID_A)
    write_manifest(settings.state_dir / f"{HOST_NAME}.json", host_exe, [ORIGIN_B])
    assert cli.main(["--state-dir", str(settings.state_dir), "install-native-host"]) == 0
    data = json.loads((settings.state_dir / f"{HOST_NAME}.json").read_text(encoding="utf-8"))
    assert data["allowed_origins"] == [ORIGIN_A]


def test_native_host_registry_errors_are_exit_code_1(
    settings: Settings, host_exe: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import dome_agent.platform

    broken = RegistrarRecorder(fail=PermissionError(5, "Access is denied"))
    monkeypatch.setattr(dome_agent.platform, "build_native_host_registrar", lambda settings: broken)
    monkeypatch.setenv("DOME_AGENT_DEV_EXTENSION_ID", ID_A)
    assert cli.main(["--state-dir", str(settings.state_dir), "install-native-host"]) == 1
    assert cli.main(["--state-dir", str(settings.state_dir), "uninstall-native-host"]) == 1
    assert capsys.readouterr().err.count("Access is denied") == 2


def test_uninstall_native_host_removes_the_registration(
    settings: Settings, registrar: RegistrarRecorder, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--state-dir", str(settings.state_dir), "uninstall-native-host"]) == 0
    assert registrar.uninstalled == 1
    assert "Removed HKCU" in capsys.readouterr().out


def test_platform_builders_never_build_the_whole_platform(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    import dome_agent.platform as platform

    real = load_settings(state_dir=settings.state_dir)
    names = (type(platform.build_startup(real)).__name__, type(platform.build_native_host_registrar(real)).__name__)
    if sys.platform == "win32":
        assert names == ("WindowsStartup", "WindowsNativeHostRegistrar")
    else:
        assert names == ("UnsupportedStartup", "UnsupportedNativeHost")
    monkeypatch.setenv("DOME_AGENT_PLATFORM", "fake")
    fake = load_settings(state_dir=settings.state_dir)
    assert type(platform.build_startup(fake)).__name__.startswith("Fake")
    assert type(platform.build_native_host_registrar(fake)).__name__.startswith("Fake")


# ----- the server address Setup writes (dome-server.json) ------------------------------------------------------------


def test_installed_api_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert installed_server_file() is None  # running from source: there is no installed file
    assert installed_api_url() == ""
    path = tmp_path / "dome-server.json"
    assert installed_api_url(path) == ""
    good = {"api_url": "https://domeremote.com/"}
    path.write_text(json.dumps(good), encoding="utf-8")
    assert installed_api_url(path) == "https://domeremote.com"
    path.write_text("\ufeff" + json.dumps({"api_url": "https://staging.domeremote.com:8443"}), encoding="utf-8")
    assert installed_api_url(path) == "https://staging.domeremote.com:8443"
    for bad in (
        "http://domeremote.com",
        "https://domeremote.com/v1",
        "https://user@domeremote.com",
        "https://dome remote.com",
        "",
        42,
    ):
        path.write_text(json.dumps({"api_url": bad}), encoding="utf-8")
        assert installed_api_url(path) == "", bad
    path.write_text("[1]", encoding="utf-8")
    assert installed_api_url(path) == ""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "DoMe.exe"))
    assert installed_server_file() == tmp_path / "dome-server.json"
    path.write_text(json.dumps(good), encoding="utf-8")
    assert installed_api_url() == "https://domeremote.com"


def _fake_link(monkeypatch: pytest.MonkeyPatch, used: list[str]) -> None:
    import dome_agent.api
    import dome_agent.link

    class FakeApi:
        def __init__(self, api_url: str) -> None:
            used.append(api_url)

        async def close(self) -> None:
            return None

    async def fake_link_pc(api: Any, *args: Any, **kwargs: Any) -> SimpleNamespace:
        await asyncio.sleep(0)
        return SimpleNamespace(pc_name="PC", pc_id="pc_1", account_id="acc_1", enabled=True)

    monkeypatch.setattr(dome_agent.api, "ApiClient", FakeApi)
    monkeypatch.setattr(dome_agent.link, "link_pc", fake_link_pc)
    monkeypatch.setenv("DOME_AGENT_PLATFORM", "fake")


def test_link_falls_back_to_the_installed_server_address(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    used: list[str] = []
    _fake_link(monkeypatch, used)
    monkeypatch.setattr(cli, "installed_api_url", lambda: "https://domeremote.com")
    base = ["--state-dir", str(settings.state_dir), "link", "--no-browser"]
    assert cli.main(base) == 0
    assert cli.main([*base, "--api-url", "https://staging.example.test"]) == 0
    monkeypatch.setenv("DOME_AGENT_API_URL", "https://env.example.test")
    assert cli.main(base) == 0
    assert used == ["https://domeremote.com", "https://staging.example.test", "https://env.example.test"]
    assert "Linked as 'PC'" in capsys.readouterr().out


def test_link_without_any_address(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "installed_api_url", lambda: "")
    assert cli.main(["--state-dir", str(settings.state_dir), "link", "--no-browser"]) == 2
    assert "DOME_AGENT_API_URL" in capsys.readouterr().err


# ----- a windowed DoMe.exe has no stdout ---------------------------------------------------------------------------


def test_flush_stdout_without_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    cli._flush_stdout()  # noqa: SLF001 - must not raise (it did: AttributeError before the browser opened)
    print("goes nowhere")  # what print() does without a stdout


def test_link_runs_without_stdout(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    import dome_agent.api
    import dome_agent.link

    presented: list[str] = []

    class FakeApi:
        def __init__(self, api_url: str) -> None:
            pass

        async def close(self) -> None:
            return None

    async def fake_link_pc(api: Any, *args: Any, present: Any, **kwargs: Any) -> SimpleNamespace:
        present(SimpleNamespace(user_code="ABCD-EFGH", verification_uri_complete="https://x.test/l", expires_in=600))
        presented.append("shown")
        return SimpleNamespace(pc_name="PC", pc_id="pc_1", account_id="acc_1", enabled=True)

    monkeypatch.setattr(dome_agent.api, "ApiClient", FakeApi)
    monkeypatch.setattr(dome_agent.link, "link_pc", fake_link_pc)
    monkeypatch.setenv("DOME_AGENT_PLATFORM", "fake")
    monkeypatch.setattr(sys, "stdout", None)
    rc = cli.main(["--state-dir", str(settings.state_dir), "link", "--no-browser", "--api-url", "https://x.test"])
    assert rc == 0 and presented == ["shown"]


# ----- crash guard of the frozen entry points ----------------------------------------------------------------------


def test_run_guarded_turns_an_exception_into_exit_70_and_a_crash_log(tmp_path: Path) -> None:
    shown: list[Path | None] = []

    def boom() -> int:
        raise RuntimeError("something broke")

    argv = ["--state-dir", str(tmp_path / "st"), "quit"]
    code = crashguard.run_guarded(boom, program="DoMe", argv=argv, show_message=True, on_message=shown.append)
    assert code == crashguard.EXIT_CRASH == 70
    log_file = tmp_path / "st" / "logs" / "crash.log"
    text = log_file.read_text(encoding="utf-8")
    assert "RuntimeError: something broke" in text and "DoMe --state-dir" in text
    assert shown == [log_file]
    assert crashguard.run_guarded(boom, program="DoMe", argv=argv, on_message=shown.append) == 70
    assert len(shown) == 1  # no message box unless asked
    assert log_file.read_text(encoding="utf-8").count("RuntimeError: something broke") == 2


def test_crash_log_is_redacted_like_every_other_log(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    token = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"  # 40 token-like characters

    def boom() -> int:
        raise RuntimeError(f"relay refused: Authorization: Bearer {token}")

    argv = ["--state-dir", str(tmp_path / "st"), "run", f"--token={token}x"]
    assert crashguard.run_guarded(boom, program="DoMe", argv=argv) == 70
    text = (tmp_path / "st" / "logs" / "crash.log").read_text(encoding="utf-8")
    assert token not in text and "Bearer [redacted]" in text and "[redacted]" in text
    assert "RuntimeError: relay refused" in text  # the useful part stays
    assert token not in capsys.readouterr().err  # stderr gets the same masked entry
    # the same patterns as the structured logs (crashguard copies them to stay standard-library only)
    from dome_agent import logsetup

    assert crashguard._BEARER_RE.pattern == logsetup._BEARER_RE.pattern
    assert crashguard._LONG_TOKEN_RE.pattern == logsetup._LONG_TOKEN_RE.pattern
    assert crashguard.REDACTED == logsetup.REDACTED


def test_crash_message_asks_for_nothing_the_user_cannot_do() -> None:
    text = crashguard.CRASH_MESSAGE.format(path=r"C:\Users\x\AppData\Local\DoMe\logs\crash.log")
    assert "support request" in text and "after you have read them" in text
    assert "send that file" not in text.lower()


def test_run_guarded_passes_exit_codes_and_system_exit(tmp_path: Path) -> None:
    assert crashguard.run_guarded(lambda: 3, program="DoMe") == 3

    def interrupted() -> int:
        raise KeyboardInterrupt

    assert crashguard.run_guarded(interrupted, program="DoMe") == 130

    def exits() -> int:
        raise SystemExit(2)

    with pytest.raises(SystemExit) as info:
        crashguard.run_guarded(exits, program="DoMe")
    assert info.value.code == 2


def test_crash_log_location(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOME_AGENT_STATE_DIR", str(tmp_path / "env"))
    assert crashguard.crash_log_path([]) == tmp_path / "env" / "logs" / "crash.log"
    assert crashguard.crash_log_path(["--state-dir", str(tmp_path / "a")]) == tmp_path / "a" / "logs" / "crash.log"
    assert crashguard.crash_log_path([f"--state-dir={tmp_path / 'b'}"]) == tmp_path / "b" / "logs" / "crash.log"


def test_only_the_tray_shows_the_crash_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOME_AGENT_HEADLESS", raising=False)
    assert crashguard.wants_message(["run"]) is True
    assert crashguard.wants_message(["--state-dir", "run", "run"]) is True
    assert crashguard.wants_message(["run", "--headless"]) is False
    for argv in (["quit"], ["install-native-host"], ["--state-dir", "x", "status", "--json"], ["start-at-login", "on"]):
        assert crashguard.wants_message(argv) is False, argv
    monkeypatch.setenv("DOME_AGENT_HEADLESS", "1")
    assert crashguard.wants_message(["run"]) is False


# ----- the Windows test list shared by ci.yml and windows-installer.yml ----------------------------------------------


def test_windows_test_list_names_existing_test_files() -> None:
    lines = (TESTS_DIR / "windows_suite.txt").read_text(encoding="utf-8").splitlines()
    assert lines and all(line == line.strip() and line for line in lines), "one path per line, no blanks"
    assert len(lines) == len(set(lines))
    for line in lines:
        assert line.startswith("tests/test_") and line.endswith(".py"), line
        assert (TESTS_DIR.parent / line).is_file(), line
    assert "tests/test_installer_commands.py" in lines
