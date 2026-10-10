"""The Windows volume adapter against stand-ins for comtypes/pycaw (the real ones need Windows)."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest
from dome_protocol import ProtocolError

from dome_agent.platform.windows.volume import WindowsVolume


class FakeEndpoint:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.scalar = 0.5
        self.muted = 0

    def GetMasterVolumeLevelScalar(self) -> float:  # noqa: N802 - COM method name
        return self.scalar

    def SetMasterVolumeLevelScalar(self, value: float, _ctx: Any) -> None:  # noqa: N802
        self.scalar = value

    def GetMute(self) -> int:  # noqa: N802
        return self.muted

    def SetMute(self, value: int, _ctx: Any) -> None:  # noqa: N802
        self.muted = value

    def __del__(self) -> None:
        self.log.append("released")


@pytest.fixture
def com(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Fake comtypes + pycaw where GetSpeakers returns the current AudioDevice wrapper (no Activate)."""
    state: dict[str, Any] = {"log": [], "speakers": None}
    # GetSpeakers builds a new wrapper (and a new COM pointer) on every call, as pycaw does.

    comtypes = types.ModuleType("comtypes")
    comtypes.CLSCTX_ALL = 23  # type: ignore[attr-defined]
    comtypes.CoInitialize = lambda: state["log"].append("init")  # type: ignore[attr-defined]
    comtypes.CoUninitialize = lambda: state["log"].append("uninit")  # type: ignore[attr-defined]

    class AudioUtilities:
        @staticmethod
        def GetSpeakers() -> Any:  # noqa: N802
            factory = state["speakers"]
            return factory() if factory is not None else None

    pycaw = types.ModuleType("pycaw")
    pycaw_pycaw = types.ModuleType("pycaw.pycaw")
    pycaw_pycaw.AudioUtilities = AudioUtilities  # type: ignore[attr-defined]
    pycaw_pycaw.IAudioEndpointVolume = object  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "comtypes", comtypes)
    monkeypatch.setitem(sys.modules, "pycaw", pycaw)
    monkeypatch.setitem(sys.modules, "pycaw.pycaw", pycaw_pycaw)
    return state


def test_set_volume_uses_the_audio_device_wrapper_and_releases_before_uninit(com: dict[str, Any]) -> None:
    # current pycaw: an AudioDevice wrapper with an EndpointVolume property and no Activate
    com["speakers"] = lambda: types.SimpleNamespace(EndpointVolume=FakeEndpoint(com["log"]))
    state = WindowsVolume().set_volume(35)
    assert (state.value, state.muted) == (35, False)
    assert com["log"] == ["init", "released", "uninit"]


def test_mute_round_trips(com: dict[str, Any]) -> None:
    com["speakers"] = lambda: types.SimpleNamespace(EndpointVolume=FakeEndpoint(com["log"]))
    assert WindowsVolume().set_muted(True).muted is True


def test_no_output_device_is_a_clear_error(com: dict[str, Any]) -> None:
    with pytest.raises(ProtocolError) as exc:
        WindowsVolume().get()
    assert exc.value.code == "OS_ERROR"
    assert com["log"] == ["init", "uninit"]


def test_unexpected_com_failure_becomes_os_error(com: dict[str, Any]) -> None:
    class Broken:
        @property
        def EndpointVolume(self) -> Any:  # noqa: N802
            raise OSError("E_FAIL")

    com["speakers"] = Broken
    with pytest.raises(ProtocolError) as exc:
        WindowsVolume().set_volume(10)
    assert exc.value.code == "OS_ERROR"
