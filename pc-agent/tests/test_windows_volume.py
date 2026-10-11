"""The Windows volume adapter against stand-ins for comtypes/pycaw (the real ones need Windows).

Every stand-in COM pointer logs its release, so the tests can check the rule that matters on Windows:
all pointers are released before the thread's ``CoUninitialize``, on the error paths as well.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from dome_protocol import ProtocolError

from dome_agent.platform.windows.volume import WindowsVolume

E_NOTFOUND = -2147023728  # 0x80070490 as the signed value COM reports
E_ACCESSDENIED = -2147024891  # 0x80070005
E_OUTOFMEMORY = -2147024882  # 0x8007000E
RPC_E_CHANGED_MODE = -2147417850  # 0x80010106


class COMError(Exception):
    def __init__(self, hresult: int, text: str = "", details: Any = None) -> None:
        super().__init__(hresult, text, details)
        self.hresult = hresult


class WinOSError(OSError):
    """OSError as ctypes raises it on Windows for a failed HRESULT (``winerror`` is Windows-only)."""

    def __init__(self, winerror: int) -> None:
        super().__init__(f"[WinError {winerror}]")
        self.winerror = winerror


class Pointer:
    """A stand-in COM pointer: logs ``released <name>`` when the last reference goes away."""

    def __init__(self, com: dict[str, Any], name: str) -> None:
        self.com = com
        self.name = name

    def __del__(self) -> None:
        self.com["log"].append(f"released {self.name}")


class FakeEndpoint(Pointer):
    def __init__(self, com: dict[str, Any]) -> None:
        super().__init__(com, "endpoint")
        self.scalar = 0.5
        self.muted = 0

    def GetMasterVolumeLevelScalar(self) -> float:  # noqa: N802 - COM method name
        return self.scalar

    def SetMasterVolumeLevelScalar(self, value: float, _ctx: Any) -> None:  # noqa: N802
        if self.com["set_error"] is not None:
            raise self.com["set_error"]
        self.scalar = value

    def GetMute(self) -> int:  # noqa: N802
        return self.muted

    def SetMute(self, value: int, _ctx: Any) -> None:  # noqa: N802
        self.muted = value


class FakeUnknown(Pointer):
    def QueryInterface(self, interface: Any) -> Any:  # noqa: N802
        assert interface is self.com["IAudioEndpointVolume"]
        return FakeEndpoint(self.com)


class FakeDevice(Pointer):
    def Activate(self, iid: Any, clsctx: int, params: Any) -> Any:  # noqa: N802
        assert (iid, clsctx, params) == ("{IAudioEndpointVolume}", 7, None)
        return FakeUnknown(self.com, "unknown")


class FakeEnumerator(Pointer):
    def GetDefaultAudioEndpoint(self, flow: int, role: int) -> Any:  # noqa: N802
        assert (flow, role) == (0, 1)  # eRender, eMultimedia
        if self.com["device_error"] is not None:
            raise self.com["device_error"]
        return self.com["device"]()


ALL_RELEASED = ["released endpoint", "released unknown", "released device", "released enumerator"]


@pytest.fixture
def com(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Fake comtypes + pycaw exposing the Core Audio interfaces the adapter uses."""
    state: dict[str, Any] = {"log": [], "init_error": None, "device_error": None, "set_error": None}
    state["device"] = lambda: FakeDevice(state, "device")

    def co_initialize_ex(flags: int) -> None:
        assert flags == 2  # COINIT_APARTMENTTHREADED
        if state["init_error"] is not None:
            raise state["init_error"]
        state["log"].append("init")

    def co_create_instance(clsid: Any, interface: Any, clsctx: int) -> Any:
        assert clsid == "{BCDE0395-E52F-467C-8E3D-C4579291692E}"
        assert interface is state["IMMDeviceEnumerator"] and clsctx == 1
        return FakeEnumerator(state, "enumerator")

    comtypes = types.ModuleType("comtypes")
    comtypes.CLSCTX_ALL = 7  # type: ignore[attr-defined]
    comtypes.CLSCTX_INPROC_SERVER = 1  # type: ignore[attr-defined]
    comtypes.COINIT_APARTMENTTHREADED = 2  # type: ignore[attr-defined]
    comtypes.COMError = COMError  # type: ignore[attr-defined]
    comtypes.GUID = str  # type: ignore[attr-defined]
    comtypes.CoInitializeEx = co_initialize_ex  # type: ignore[attr-defined]
    comtypes.CoUninitialize = lambda: state["log"].append("uninit")  # type: ignore[attr-defined]
    comtypes.CoCreateInstance = co_create_instance  # type: ignore[attr-defined]

    state["IAudioEndpointVolume"] = types.SimpleNamespace(_iid_="{IAudioEndpointVolume}")
    state["IMMDeviceEnumerator"] = types.SimpleNamespace(_iid_="{IMMDeviceEnumerator}")
    pycaw = types.ModuleType("pycaw")
    pycaw_pycaw = types.ModuleType("pycaw.pycaw")
    pycaw_pycaw.IAudioEndpointVolume = state["IAudioEndpointVolume"]  # type: ignore[attr-defined]
    pycaw_pycaw.IMMDeviceEnumerator = state["IMMDeviceEnumerator"]  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "comtypes", comtypes)
    monkeypatch.setitem(sys.modules, "pycaw", pycaw)
    monkeypatch.setitem(sys.modules, "pycaw.pycaw", pycaw_pycaw)
    return state


def released_then_uninit(log: list[str], released: list[str]) -> bool:
    """``init``, then exactly ``released`` (in any order), then ``uninit`` last."""
    return log[0] == "init" and sorted(log[1:-1]) == sorted(released) and log[-1] == "uninit"


def test_set_volume_goes_through_the_default_render_endpoint(com: dict[str, Any]) -> None:
    state = WindowsVolume().set_volume(35)
    assert (state.value, state.muted) == (35, False)
    assert released_then_uninit(com["log"], ALL_RELEASED)


def test_mute_round_trips(com: dict[str, Any]) -> None:
    assert WindowsVolume().set_muted(True).muted is True
    assert released_then_uninit(com["log"], ALL_RELEASED)


def test_no_output_device_is_a_clear_error(com: dict[str, Any]) -> None:
    com["device_error"] = COMError(E_NOTFOUND, "Element not found.")
    with pytest.raises(ProtocolError) as exc:
        WindowsVolume().get()
    assert (exc.value.code, exc.value.message) == ("OS_ERROR", "No audio output device is enabled on this PC")
    # the enumerator is released before CoUninitialize although the exception (and its traceback) is alive
    assert released_then_uninit(com["log"], ["released enumerator"])


def test_a_null_device_is_the_same_clear_error(com: dict[str, Any]) -> None:
    com["device"] = lambda: None
    with pytest.raises(ProtocolError) as exc:
        WindowsVolume().get()
    assert exc.value.message == "No audio output device is enabled on this PC"
    assert released_then_uninit(com["log"], ["released enumerator"])


def test_com_failure_on_set_releases_every_pointer_before_uninit(com: dict[str, Any]) -> None:
    com["set_error"] = COMError(E_ACCESSDENIED, "Access is denied.")
    with pytest.raises(ProtocolError) as exc:
        WindowsVolume().set_volume(10)
    assert (exc.value.code, exc.value.message) == ("OS_ERROR", "Audio endpoint error: COMError (0x80070005)")
    assert isinstance(exc.value.__cause__, COMError)  # the cause is kept for the log, its frames are not
    assert released_then_uninit(com["log"], ALL_RELEASED)


def test_unexpected_pycaw_shape_becomes_os_error(com: dict[str, Any]) -> None:
    class NoActivate(Pointer):
        pass

    com["device"] = lambda: NoActivate(com, "device")
    with pytest.raises(ProtocolError) as exc:
        WindowsVolume().get()
    assert (exc.value.code, exc.value.message) == ("OS_ERROR", "Audio endpoint error: AttributeError")
    assert released_then_uninit(com["log"], ["released device", "released enumerator"])


def test_thread_already_multithreaded_uses_com_without_uninitialising(com: dict[str, Any]) -> None:
    com["init_error"] = WinOSError(RPC_E_CHANGED_MODE)
    assert WindowsVolume().set_volume(20).value == 20
    assert sorted(com["log"]) == sorted(ALL_RELEASED)  # no init of ours, so no uninit either


def test_other_com_initialisation_failure_is_os_error(com: dict[str, Any]) -> None:
    com["init_error"] = WinOSError(E_OUTOFMEMORY)
    with pytest.raises(ProtocolError) as exc:
        WindowsVolume().get()
    assert (exc.value.code, exc.value.message) == (
        "OS_ERROR",
        "Windows audio (COM) is unavailable: WinOSError (0x8007000E)",
    )
    assert com["log"] == []


def test_comtypes_import_failure_is_os_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "comtypes", None)  # makes ``import comtypes`` raise ImportError
    with pytest.raises(ProtocolError) as exc:
        WindowsVolume().get()
    assert exc.value.code == "OS_ERROR" and "Windows audio (COM) is unavailable" in exc.value.message


def test_out_of_range_volume_is_rejected_before_com(com: dict[str, Any]) -> None:
    with pytest.raises(ProtocolError) as exc:
        WindowsVolume().set_volume(101)
    assert exc.value.code == "INVALID_PARAMETERS" and com["log"] == []
