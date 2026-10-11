"""The Windows media adapter against a stand-in ``winsdk.windows.media.control`` (the real one needs
Windows). winsdk types the session list, playback info and its controls as ``Optional``, and WinRT calls
raise while a player exits: none of that may surface as ``INTERNAL`` in a command or the state poll."""

from __future__ import annotations

import sys
import types
from collections.abc import Generator
from typing import Any

import pytest
from dome_protocol import ProtocolError

from dome_agent.platform.windows.media import WindowsMedia

PLAYING, PAUSED = 4, 5


class Op:
    """An ``IAsyncOperation`` stand-in: awaitable, completes at once with ``value``."""

    def __init__(self, value: Any) -> None:
        self.value = value

    def __await__(self) -> Generator[Any, None, Any]:
        return self.value
        yield  # a generator function, as winsdk's awaitables are


class Vector:
    """``IVectorView`` stand-in: ``size`` and ``get_at``."""

    def __init__(self, items: list[Any]) -> None:
        self.items = items

    @property
    def size(self) -> int:
        return len(self.items)

    def get_at(self, index: int) -> Any:
        return self.items[index]


def controls(**enabled: bool) -> types.SimpleNamespace:
    names = ("play", "pause", "next", "previous")
    return types.SimpleNamespace(**{f"is_{n}_enabled": enabled.get(n, False) for n in names})


class FakeSession:
    def __init__(self, app_id: Any = "Spotify.exe", status: Any = PLAYING) -> None:
        self.source_app_user_model_id = app_id
        self.info: Any = types.SimpleNamespace(playback_status=status, controls=controls(pause=True, next=True))
        self.props: Any = types.SimpleNamespace(title="A song", artist="A band")
        self.info_error: Exception | None = None
        self.control_error: Exception | None = None
        self.calls: list[str] = []

    def get_playback_info(self) -> Any:
        if self.info_error is not None:
            raise self.info_error
        return self.info

    def try_get_media_properties_async(self) -> Op:
        return Op(self.props)

    def try_pause_async(self) -> Op:
        if self.control_error is not None:
            raise self.control_error
        self.calls.append("pause")
        self.info.playback_status = PAUSED
        self.info.controls = controls(play=True, next=True)
        return Op(True)

    def try_play_async(self) -> Op:
        self.calls.append("play")
        return Op(True)

    def try_skip_next_async(self) -> Op:
        self.calls.append("next")
        return Op(True)

    def try_skip_previous_async(self) -> Op:
        self.calls.append("previous")
        return Op(True)


@pytest.fixture
def winrt(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {"sessions": Vector([]), "manager_error": None}

    class Manager:
        @staticmethod
        def request_async() -> Op:
            return Op(state["manager"])

    def get_sessions() -> Any:
        if state["manager_error"] is not None:
            raise state["manager_error"]
        return state["sessions"]

    state["manager"] = types.SimpleNamespace(get_sessions=get_sessions)
    control = types.ModuleType("winsdk.windows.media.control")
    control.GlobalSystemMediaTransportControlsSessionManager = Manager  # type: ignore[attr-defined]
    for name in ("winsdk", "winsdk.windows", "winsdk.windows.media"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "winsdk.windows.media.control", control)
    return state


def test_lists_sessions_with_status_controls_and_metadata(winrt: dict[str, Any]) -> None:
    winrt["sessions"] = Vector([FakeSession()])
    (session,) = WindowsMedia().list_sessions()
    assert (session.session_id, session.status, session.controls) == ("Spotify.exe#0", "playing", ("pause", "next"))
    assert (session.app_label, session.title, session.artist) == ("Spotify.exe", "A song", "A band")


def test_pause_round_trip(winrt: dict[str, Any]) -> None:
    player = FakeSession()
    winrt["sessions"] = Vector([player])
    after = WindowsMedia().set_paused("Spotify.exe#0", True)
    assert player.calls == ["pause"] and after.status == "paused" and after.controls == ("play", "next")


def test_no_session_list_reads_as_no_sessions(winrt: dict[str, Any]) -> None:
    winrt["sessions"] = None
    assert WindowsMedia().list_sessions() == []
    assert WindowsMedia().get_session("Spotify.exe#0") is None


def test_null_entries_are_skipped_and_ids_keep_the_managers_index(winrt: dict[str, Any]) -> None:
    winrt["sessions"] = Vector([None, FakeSession(app_id=None)])
    (session,) = WindowsMedia().list_sessions()
    assert session.session_id == "unknown#1"


def test_missing_playback_info_reads_as_unknown_without_controls(winrt: dict[str, Any]) -> None:
    player = FakeSession()
    player.info = None
    winrt["sessions"] = Vector([player])
    (session,) = WindowsMedia().list_sessions()
    assert (session.status, session.controls) == ("unknown", ())
    with pytest.raises(ProtocolError) as exc:
        WindowsMedia().set_paused("Spotify.exe#0", True)
    assert exc.value.code == "ACTION_UNAVAILABLE"


def test_missing_controls_status_and_metadata_are_tolerated(winrt: dict[str, Any]) -> None:
    player = FakeSession(status=None)
    player.info.controls = None
    player.props = None
    winrt["sessions"] = Vector([player])
    (session,) = WindowsMedia().list_sessions()
    assert (session.status, session.controls, session.title, session.artist) == ("unknown", (), "", "")


def test_a_session_closing_while_read_is_gone_not_internal(winrt: dict[str, Any]) -> None:
    closing, staying = FakeSession(app_id="Closing.exe"), FakeSession()
    closing.info_error = OSError("RPC server unavailable")
    winrt["sessions"] = Vector([closing, staying])
    assert [s.session_id for s in WindowsMedia().list_sessions()] == ["Spotify.exe#1"]
    for call in (lambda m: m.get_session("Closing.exe#0"), lambda m: m.set_paused("Closing.exe#0", True)):
        with pytest.raises(ProtocolError) as exc:
            call(WindowsMedia())
        assert exc.value.code == "MEDIA_SESSION_GONE"


def test_a_session_whose_app_id_cannot_be_read_is_left_out(winrt: dict[str, Any]) -> None:
    class Vanished(FakeSession):
        @property  # type: ignore[override]
        def source_app_user_model_id(self) -> str:
            raise OSError("RPC server unavailable")

        @source_app_user_model_id.setter
        def source_app_user_model_id(self, _value: Any) -> None:
            pass

    winrt["sessions"] = Vector([Vanished(), FakeSession()])
    assert [s.session_id for s in WindowsMedia().list_sessions()] == ["Spotify.exe#1"]


def test_no_manager_is_os_error(winrt: dict[str, Any]) -> None:
    winrt["manager"] = None
    with pytest.raises(ProtocolError) as exc:
        WindowsMedia().list_sessions()
    assert exc.value.code == "OS_ERROR"


def test_session_list_failure_is_os_error(winrt: dict[str, Any]) -> None:
    winrt["manager_error"] = OSError("E_FAIL")
    with pytest.raises(ProtocolError) as exc:
        WindowsMedia().list_sessions()
    assert (exc.value.code, exc.value.message) == ("OS_ERROR", "Media sessions unavailable: OSError")


def test_control_call_raising_is_os_error(winrt: dict[str, Any]) -> None:
    player = FakeSession()
    player.control_error = OSError("E_FAIL")
    winrt["sessions"] = Vector([player])
    with pytest.raises(ProtocolError) as exc:
        WindowsMedia().set_paused("Spotify.exe#0", True)
    assert (exc.value.code, exc.value.message) == ("OS_ERROR", "Media control failed: OSError")


def test_winsdk_missing_is_os_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "winsdk", None)
    with pytest.raises(ProtocolError) as exc:
        WindowsMedia().list_sessions()
    assert exc.value.code == "OS_ERROR"
