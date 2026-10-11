"""The Windows session adapter against a stand-in ``ctypes.WinDLL``: lock-state detection from
``WTSINFOEX_LEVEL1_W.SessionFlags`` (an unknown value falls back to the input-desktop probe instead of
counting as unlocked) and fully declared ctypes signatures (64-bit handles)."""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from ctypes import wintypes
from typing import Any

import pytest
from dome_protocol import ProtocolError

from dome_agent.platform.windows import session as w

BIG_HANDLE = 0x7FF6_1234_5678  # above 32 bits: survives only with declared HANDLE argtypes


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
    """WTS + user32 stand-ins; ``flags`` is the SessionFlags value the WTS query reports (None = it fails)
    and ``desktop`` the handle OpenInputDesktop returns (0 = refused, as on the secure desktop)."""
    state: dict[str, Any] = {"flags": w.WTS_SESSIONSTATE_UNLOCK, "desktop": BIG_HANDLE, "lock_ok": True}
    info = w._WtsInfoEx()
    state["info"] = info  # keeps the buffer the fake WTS query hands out alive

    def query(server: Any, session: int, info_class: int, buffer: Any, size: Any) -> bool:
        assert (server, session, info_class) == (None, w.WTS_CURRENT_SESSION, w.WTS_SESSION_INFO_EX)
        if state["flags"] is None:
            return False
        info.Level = 1
        info.Data.WTSInfoExLevel1.SessionFlags = state["flags"]
        buffer._obj.value = ctypes.addressof(info)  # what WTSQuerySessionInformationW writes to *ppBuffer
        size._obj.value = ctypes.sizeof(info)
        return True

    state["dlls"] = {
        "wtsapi32": FakeDLL(WTSQuerySessionInformationW=query, WTSFreeMemory=lambda buffer: None),
        "user32": FakeDLL(
            OpenInputDesktop=lambda flags, inherit, access: state["desktop"],
            CloseDesktop=lambda handle: True,
            LockWorkStation=lambda: state["lock_ok"],
        ),
    }
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error=False: state["dlls"][name], raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    return state


def test_wts_lock_and_unlock_flags_decide_without_the_probe(win: dict[str, Any]) -> None:
    win["flags"] = w.WTS_SESSIONSTATE_LOCK
    assert w.WindowsSession().is_locked() is True
    win["flags"] = w.WTS_SESSIONSTATE_UNLOCK
    assert w.WindowsSession().is_locked() is False
    assert win["dlls"]["user32"].OpenInputDesktop.calls == []
    free = win["dlls"]["wtsapi32"].WTSFreeMemory
    assert len(free.calls) == 2 and free.argtypes == [ctypes.c_void_p] and free.restype is None


@pytest.mark.parametrize("desktop", [0, BIG_HANDLE])
def test_unknown_session_flags_fall_back_to_the_input_desktop(win: dict[str, Any], desktop: int) -> None:
    win["flags"] = -1  # WTS_SESSIONSTATE_UNKNOWN (0xFFFFFFFF read through a LONG)
    win["desktop"] = desktop
    assert w.WindowsSession().is_locked() is (desktop == 0)
    assert len(win["dlls"]["user32"].OpenInputDesktop.calls) == 1


def test_failed_wts_query_falls_back_to_the_input_desktop(win: dict[str, Any]) -> None:
    win["flags"] = None
    win["desktop"] = 0
    assert w.WindowsSession().is_locked() is True


def test_input_desktop_probe_declares_handle_types_and_closes_the_handle(win: dict[str, Any]) -> None:
    assert w.WindowsSession()._input_desktop_unavailable() is False
    user32 = win["dlls"]["user32"]
    assert user32.OpenInputDesktop.argtypes == [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    assert user32.OpenInputDesktop.restype is wintypes.HANDLE
    assert user32.CloseDesktop.argtypes == [wintypes.HANDLE] and user32.CloseDesktop.restype is wintypes.BOOL
    assert user32.OpenInputDesktop.calls == [(0, False, w.DESKTOP_READOBJECTS)]
    assert user32.CloseDesktop.calls == [(BIG_HANDLE,)]


def test_lock_declares_its_signature_and_reports_failure(win: dict[str, Any]) -> None:
    w.WindowsSession().lock()
    fn = win["dlls"]["user32"].LockWorkStation
    assert fn.argtypes == [] and fn.restype is wintypes.BOOL
    win["lock_ok"] = False
    with pytest.raises(ProtocolError) as exc:
        w.WindowsSession().lock()
    assert (exc.value.code, exc.value.message) == ("OS_ERROR", "LockWorkStation failed (error 5)")
