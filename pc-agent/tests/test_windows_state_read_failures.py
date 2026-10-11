"""Windows adapter reads that fail while a state frame is built (volume, media sessions, lock state)
leave their field out of the frame; the reason must reach agent.log, once per distinct error (repeated
at most hourly), without media titles or other exception text."""

from __future__ import annotations

import dataclasses
import types
from pathlib import Path
from typing import Any

import pytest
import structlog.testing
from dome_protocol import ProtocolError

from dome_agent.platform.protocol import VolumeState
from dome_agent.state import ReadFailureLog, StateAggregator
from dome_agent.store import Store
from dome_agent.testing.fake_platform import FakeState, build_fake_platform


class Flaky:
    """An adapter call that raises ``error`` while it is set, else returns ``value``."""

    def __init__(self, value: Any) -> None:
        self.value = value
        self.error: BaseException | None = None

    def __call__(self) -> Any:
        if self.error is not None:
            raise self.error
        return self.value


@pytest.fixture
def setup(tmp_path: Path) -> Any:
    volume, media, locked = Flaky(VolumeState(value=40, muted=False)), Flaky([]), Flaky(False)
    platform = dataclasses.replace(
        build_fake_platform(FakeState()),
        volume=types.SimpleNamespace(get=volume),
        media=types.SimpleNamespace(list_sessions=media),
        session=types.SimpleNamespace(is_locked=locked),
    )
    bridge = types.SimpleNamespace(connected=False, instances=lambda: [], all_tabs=lambda: [])
    store = Store(tmp_path / "state.db")
    agg = StateAggregator(store, platform, bridge)  # type: ignore[arg-type]
    yield agg, volume, media, locked
    store.close()


def failures(logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in logs if e["log_level"] == "warning" and e["event"].startswith("state read failed")]


async def test_volume_read_failure_is_logged_once_per_distinct_error(setup: Any) -> None:
    agg, volume, _media, _locked = setup
    volume.error = ProtocolError("OS_ERROR", "Audio endpoint error: COMError (0x80070005)")
    with structlog.testing.capture_logs() as logs:
        for _ in range(3):
            assert "volume" not in await agg.snapshot()
        volume.error = ProtocolError("OS_ERROR", "No audio output device is enabled on this PC")
        await agg.snapshot()
        await agg.snapshot()
    logged = failures(logs)
    assert [(e["source"], e["code"], e["message"]) for e in logged] == [
        ("volume", "OS_ERROR", "Audio endpoint error: COMError (0x80070005)"),
        ("volume", "OS_ERROR", "No audio output device is enabled on this PC"),
    ]


async def test_recovery_is_logged_and_rearms_the_warning(setup: Any) -> None:
    agg, volume, _media, _locked = setup
    volume.error = ProtocolError("OS_ERROR", "Audio endpoint error: COMError (0x80070005)")
    with structlog.testing.capture_logs() as logs:
        await agg.snapshot()
        volume.error = None
        assert (await agg.snapshot())["volume"] == {"value": 40, "muted": False}
        await agg.snapshot()  # a second success logs nothing more
        volume.error = ProtocolError("OS_ERROR", "Audio endpoint error: COMError (0x80070005)")
        await agg.snapshot()
    events = [(e["log_level"], e["event"].split(";")[0], e["source"]) for e in logs if e.get("source")]
    assert events == [
        ("warning", "state read failed", "volume"),
        ("info", "state read works again", "volume"),
        ("warning", "state read failed", "volume"),
    ]


async def test_unexpected_errors_log_the_class_and_os_code_but_never_their_text(setup: Any) -> None:
    agg, _volume, media, _locked = setup
    error = OSError("The player 'My Secret Song - Artist' stopped")
    error.errno = 5
    media.error = error
    with structlog.testing.capture_logs() as logs:
        assert "media_sessions" not in await agg.snapshot()
        await agg.snapshot()
    (entry,) = failures(logs)
    assert (entry["source"], entry["error"], entry["os_error"]) == ("media", "OSError", 5)
    assert "Secret" not in repr(logs)


async def test_session_lock_read_failure_is_logged_and_reads_as_unlocked(setup: Any) -> None:
    agg, _volume, _media, locked = setup
    locked.error = AttributeError("WinDLL")
    with structlog.testing.capture_logs() as logs:
        assert await agg.session_locked() is False
        assert (await agg.snapshot())["session_locked"] is False
    (entry,) = failures(logs)
    assert (entry["source"], entry["error"]) == ("session", "AttributeError")


def test_a_persisting_error_is_repeated_after_the_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr("dome_agent.state.time.monotonic", lambda: now[0])
    failures_log = ReadFailureLog(repeat_after=3600.0)
    error = ProtocolError("OS_ERROR", "Media sessions unavailable: OSError")
    with structlog.testing.capture_logs() as logs:
        failures_log.failed("media", error)
        now[0] += 3599.0
        failures_log.failed("media", error)
        now[0] += 2.0
        failures_log.failed("media", error)
    assert len(failures(logs)) == 2


def test_distinct_errors_per_source_are_bounded() -> None:
    failures_log = ReadFailureLog()
    with structlog.testing.capture_logs() as logs:
        for n in range(40):
            failures_log.failed("volume", ProtocolError("OS_ERROR", f"error {n}"))
    assert len(failures(logs)) == 16
