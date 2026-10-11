"""'Start at login' registers a windowless command: the frozen DoMe executable, or the venv's
``pythonw.exe``, never the console ``python.exe`` (Windows would open a console window at sign-in, and
closing it would stop DoMe). Turning it off only removes the entry, so it never needs that command."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from dome_agent.settings import Settings
from dome_agent.store import Store
from dome_agent.tray import TrayUI, startup_command


def venv_scripts(tmp_path: Path, *names: str) -> Path:
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    for name in names:
        (scripts / name).write_bytes(b"MZ")
    return scripts


def test_venv_registers_the_windowless_interpreter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scripts = venv_scripts(tmp_path, "python.exe", "pythonw.exe")
    monkeypatch.setattr(sys, "executable", str(scripts / "python.exe"))
    assert startup_command() == f'"{scripts / "pythonw.exe"}" -m dome_agent.cli run'


def test_agent_already_running_windowless_keeps_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scripts = venv_scripts(tmp_path, "python.exe", "pythonw.exe")
    monkeypatch.setattr(sys, "executable", str(scripts / "pythonw.exe"))
    assert startup_command() == f'"{scripts / "pythonw.exe"}" -m dome_agent.cli run'


def test_without_pythonw_windows_refuses_instead_of_registering_a_console(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts = venv_scripts(tmp_path, "python.exe")
    monkeypatch.setattr(sys, "executable", str(scripts / "python.exe"))
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(RuntimeError, match="pythonw.exe"):
        startup_command()  # the tray reports "Could not change start at login"


def test_other_hosts_without_pythonw_use_the_interpreter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    interpreter = tmp_path / "bin" / "python3"
    monkeypatch.setattr(sys, "executable", str(interpreter))
    monkeypatch.setattr(sys, "platform", "linux")
    assert startup_command() == f'"{interpreter}" -m dome_agent.cli run'


def test_frozen_build_registers_its_own_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exe = tmp_path / "DoMe" / "DoMe.exe"
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert startup_command() == f'"{exe}" run'


class RecordingStartup:
    """The Run-key adapter's interface; remembers each change and the command it was given."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.calls: list[tuple[bool, str]] = []

    def get_start_at_login(self) -> bool:
        return self.enabled

    def set_start_at_login(self, enabled: bool, command: str) -> None:
        self.calls.append((enabled, command))
        self.enabled = enabled


def click_start_at_login(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, store: Store, startup: RecordingStartup
) -> list[str]:
    """Clicks the tray's 'Start at login' item and returns the notifications it showed."""
    monkeypatch.setenv("PYSTRAY_BACKEND", "dummy")  # only Menu/MenuItem are used; no display needed
    pytest.importorskip("pystray")
    tray = TrayUI(settings)
    tray.agent = SimpleNamespace(platform=SimpleNamespace(startup=startup), store=store)
    notices: list[str] = []
    monkeypatch.setattr(tray, "notify", lambda title, message: notices.append(message))
    item = next(i for i in tray._menu() if i.text == "Start at login")  # noqa: SLF001
    item(None)
    return notices


def test_turning_it_off_works_without_pythonw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: Settings, store: Store
) -> None:
    # A Run entry written earlier (for example by an older build, with console python.exe) must stay
    # removable from the tray even though no windowless command can be built here any more.
    startup = RecordingStartup(enabled=True)
    store.set_bool("start_at_login", True)
    scripts = venv_scripts(tmp_path, "python.exe")
    monkeypatch.setattr(sys, "executable", str(scripts / "python.exe"))
    monkeypatch.setattr(sys, "platform", "win32")
    notices = click_start_at_login(monkeypatch, settings, store, startup)
    assert notices == []
    assert startup.calls == [(False, "")]
    assert store.get_bool("start_at_login", True) is False


def test_turning_it_on_without_pythonw_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: Settings, store: Store
) -> None:
    startup = RecordingStartup(enabled=False)
    scripts = venv_scripts(tmp_path, "python.exe")
    monkeypatch.setattr(sys, "executable", str(scripts / "python.exe"))
    monkeypatch.setattr(sys, "platform", "win32")
    notices = click_start_at_login(monkeypatch, settings, store, startup)
    assert notices == ["Could not change start at login: RuntimeError"]
    assert startup.calls == []
    assert store.get_bool("start_at_login", False) is False


def test_turning_it_on_registers_pythonw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, settings: Settings, store: Store
) -> None:
    startup = RecordingStartup(enabled=False)
    scripts = venv_scripts(tmp_path, "python.exe", "pythonw.exe")
    monkeypatch.setattr(sys, "executable", str(scripts / "python.exe"))
    notices = click_start_at_login(monkeypatch, settings, store, startup)
    assert notices == []
    assert startup.calls == [(True, f'"{scripts / "pythonw.exe"}" -m dome_agent.cli run')]
    assert store.get_bool("start_at_login", False) is True
