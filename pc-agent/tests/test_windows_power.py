"""The Windows power adapter against a stand-in ``ctypes.WinDLL`` (the real DLLs need Windows): the
declared signatures, the arguments that never force apps closed, and Windows error codes mapped to
protocol errors the phone can explain (a locked PC above all)."""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from ctypes import wintypes
from typing import Any

import pytest
from dome_protocol import ProtocolError

from dome_agent.platform.windows import power as w


class FakeFunction:
    def __init__(self, impl: Callable[..., Any]) -> None:
        self.impl = impl
        self.argtypes: Any = "undeclared"
        self.restype: Any = "undeclared"
        self.calls: list[tuple[Any, ...]] = []

    def __call__(self, *args: Any) -> Any:
        self.calls.append(args)
        return self.impl(*args)


class FakeDLL:
    def __init__(self, **functions: Callable[..., Any]) -> None:
        self.functions = {name: FakeFunction(impl) for name, impl in functions.items()}

    def __getattr__(self, name: str) -> FakeFunction:
        try:
            return self.functions[name]
        except KeyError:
            raise AttributeError(name) from None


@pytest.fixture
def win(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """``ctypes.WinDLL`` / ``ctypes.get_last_error`` stand-ins; ``ok`` and ``last_error`` steer the calls."""
    state: dict[str, Any] = {"log": [], "ok": False, "last_error": 0}

    def result(name: str) -> Callable[..., Any]:
        def impl(*args: Any) -> Any:
            state["log"].append(name)
            return state["ok"]

        return impl

    state["dlls"] = {
        "powrprof": FakeDLL(SetSuspendState=result("SetSuspendState")),
        "advapi32": FakeDLL(
            InitiateSystemShutdownExW=result("InitiateSystemShutdownExW"),
            AbortSystemShutdownW=result("AbortSystemShutdownW"),
        ),
    }
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error=False: state["dlls"][name], raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: state["last_error"], raising=False)
    monkeypatch.setattr(w, "_enable_shutdown_privilege", lambda: state["log"].append("privilege"))
    return state


@pytest.mark.parametrize(
    ("err", "code"),
    [
        (w.ERROR_MACHINE_LOCKED, "PC_SESSION_LOCKED"),
        (w.ERROR_ACCESS_DENIED, "POWER_DENIED"),
        (w.ERROR_PRIVILEGE_NOT_HELD, "POWER_DENIED"),
        (w.ERROR_SHUTDOWN_USERS_LOGGED_ON, "POWER_DENIED"),
        (w.ERROR_SHUTDOWN_IN_PROGRESS, "POWER_DENIED"),
        (w.ERROR_SHUTDOWN_IS_SCHEDULED, "POWER_DENIED"),
        (w.ERROR_SERVER_SHUTDOWN_IN_PROGRESS, "POWER_DENIED"),
        (w.ERROR_NOT_SUPPORTED, "ACTION_UNAVAILABLE"),
        (31, "OS_ERROR"),
    ],
)
def test_windows_errors_map_to_protocol_errors(err: int, code: str) -> None:
    assert w._map_error(err, "restart").code == code


def test_win32_error_values_match_winerror_h() -> None:
    # pywin32 312 win32/lib/winerror.py
    assert (w.ERROR_ACCESS_DENIED, w.ERROR_NOT_SUPPORTED, w.ERROR_SHUTDOWN_IN_PROGRESS) == (5, 50, 1115)
    assert (w.ERROR_NO_SHUTDOWN_IN_PROGRESS, w.ERROR_SHUTDOWN_IS_SCHEDULED) == (1116, 1190)
    assert (w.ERROR_SHUTDOWN_USERS_LOGGED_ON, w.ERROR_SERVER_SHUTDOWN_IN_PROGRESS) == (1191, 1255)
    assert (w.ERROR_MACHINE_LOCKED, w.ERROR_PRIVILEGE_NOT_HELD) == (1271, 1314)


def test_restart_of_a_locked_pc_says_it_is_locked(win: dict[str, Any]) -> None:
    win["last_error"] = w.ERROR_MACHINE_LOCKED
    with pytest.raises(ProtocolError) as exc:
        w.WindowsPower().restart()
    assert exc.value.code == "PC_SESSION_LOCKED"
    assert "locked" in exc.value.message and "Windows error" not in exc.value.message
    fn = win["dlls"]["advapi32"].InitiateSystemShutdownExW
    assert fn.argtypes == [
        wintypes.LPWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    assert fn.restype is wintypes.BOOL
    # local machine, no OS grace period, bForceAppsClosed=FALSE, reboot, SHTDN_REASON_FLAG_PLANNED
    assert fn.calls == [(None, "DoMe: restart requested from your phone.", 0, False, True, 0x80000000)]
    assert win["log"] == ["privilege", "InitiateSystemShutdownExW"]


def test_shutdown_never_forces_apps_and_succeeds(win: dict[str, Any]) -> None:
    win["ok"] = True
    w.WindowsPower().shutdown()
    (call,) = win["dlls"]["advapi32"].InitiateSystemShutdownExW.calls
    assert call[3] is False and call[4] is False


def test_missing_shutdown_privilege_is_power_denied(win: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse() -> None:
        raise OSError("OpenProcessToken failed")

    monkeypatch.setattr(w, "_enable_shutdown_privilege", refuse)
    with pytest.raises(ProtocolError) as exc:
        w.WindowsPower().restart()
    assert exc.value.code == "POWER_DENIED" and win["log"] == []


def test_sleep_enables_the_shutdown_privilege_first(win: dict[str, Any]) -> None:
    win["ok"] = True
    w.WindowsPower().sleep()
    fn = win["dlls"]["powrprof"].SetSuspendState
    assert fn.argtypes == [wintypes.BOOLEAN, wintypes.BOOLEAN, wintypes.BOOLEAN]
    assert fn.restype is wintypes.BOOLEAN
    assert fn.calls == [(False, False, False)]  # sleep, not hibernate; no forced suspend; wake events stay on
    assert win["log"] == ["privilege", "SetSuspendState"]


def test_sleep_still_tries_when_the_privilege_cannot_be_enabled(
    win: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse() -> None:
        raise OSError("AdjustTokenPrivileges failed")

    monkeypatch.setattr(w, "_enable_shutdown_privilege", refuse)
    win["last_error"] = w.ERROR_NOT_SUPPORTED  # e.g. a Modern Standby PC refusing an app-initiated sleep
    with pytest.raises(ProtocolError) as exc:
        w.WindowsPower().sleep()
    assert exc.value.code == "ACTION_UNAVAILABLE" and win["log"] == ["SetSuspendState"]


def test_abort_without_a_pending_shutdown_is_false(win: dict[str, Any]) -> None:
    win["last_error"] = w.ERROR_NO_SHUTDOWN_IN_PROGRESS
    assert w.WindowsPower().abort_shutdown() is False
    win["last_error"] = w.ERROR_ACCESS_DENIED
    with pytest.raises(ProtocolError) as exc:
        w.WindowsPower().abort_shutdown()
    assert exc.value.code == "POWER_DENIED"
