"""Windows media sessions via ``winsdk`` (``Windows.Media.Control``,
``GlobalSystemMediaTransportControlsSessionManager``).

Session id = ``<SourceAppUserModelId>#<index>`` as listed by the manager; only controls the session
advertises (``is_play_enabled`` …) are offered. WinRT async operations are awaited on a private
event loop inside the worker thread (``asyncio.run``), which is the documented way to consume
``IAsyncOperation`` from Python with winsdk.
"""

from __future__ import annotations

import asyncio
from typing import Any

from dome_protocol import ProtocolError

from ..protocol import MediaControl, MediaSession, MediaStatus

_STATUS_BY_VALUE: dict[int, MediaStatus] = {
    0: "closed",
    1: "opened",
    2: "changing",
    3: "stopped",
    4: "playing",
    5: "paused",
}
_OP_TIMEOUT = 4.0


def _wait(op: Any, timeout: float = _OP_TIMEOUT) -> Any:
    async def _await() -> Any:
        return await asyncio.wait_for(op, timeout)

    return asyncio.run(_await())


class WindowsMedia:
    def _manager(self) -> Any:
        from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager

        try:
            return _wait(Manager.request_async())
        except Exception as exc:
            raise ProtocolError("OS_ERROR", f"Media session manager unavailable: {exc.__class__.__name__}") from exc

    def _sessions_raw(self) -> list[tuple[str, Any]]:
        manager = self._manager()
        out: list[tuple[str, Any]] = []
        sessions = manager.get_sessions()
        for index in range(sessions.size):
            session = sessions.get_at(index)
            app_id = str(session.source_app_user_model_id or "unknown")
            out.append((f"{app_id}#{index}", session))
        return out

    def _describe(self, session_id: str, session: Any) -> MediaSession:
        info = session.get_playback_info()
        status = _STATUS_BY_VALUE.get(int(info.playback_status), "unknown")
        controls: list[MediaControl] = []
        c = info.controls
        if c.is_play_enabled:
            controls.append("play")
        if c.is_pause_enabled:
            controls.append("pause")
        if c.is_next_enabled:
            controls.append("next")
        if c.is_previous_enabled:
            controls.append("previous")
        title = artist = ""
        try:
            props = _wait(session.try_get_media_properties_async(), timeout=2.0)
            title = str(props.title or "")
            artist = str(props.artist or "")
        except Exception:  # noqa: S110 - metadata is optional display data
            pass
        app_label = session_id.split("#", 1)[0].split("!")[0][:64]
        return MediaSession(
            session_id=session_id,
            status=status,
            controls=tuple(controls),
            app_label=app_label,
            title=title,
            artist=artist,
        )

    def list_sessions(self) -> list[MediaSession]:
        return [self._describe(sid, s) for sid, s in self._sessions_raw()]

    def _find(self, session_id: str) -> Any:
        for sid, session in self._sessions_raw():
            if sid == session_id:
                return session
        raise ProtocolError("MEDIA_SESSION_GONE", "That media player is no longer available")

    def get_session(self, session_id: str) -> MediaSession | None:
        for sid, session in self._sessions_raw():
            if sid == session_id:
                return self._describe(sid, session)
        return None

    def _invoke(self, session_id: str, control: MediaControl) -> MediaSession:
        session = self._find(session_id)
        before = self._describe(session_id, session)
        if control not in before.controls:
            raise ProtocolError("ACTION_UNAVAILABLE", f"This media player does not offer {control}")
        op = {
            "play": session.try_play_async,
            "pause": session.try_pause_async,
            "next": session.try_skip_next_async,
            "previous": session.try_skip_previous_async,
        }[control]()
        try:
            ok = bool(_wait(op))
        except Exception as exc:
            raise ProtocolError("OS_ERROR", f"Media control failed: {exc.__class__.__name__}") from exc
        if not ok:
            raise ProtocolError("ACTION_UNAVAILABLE", f"The media player refused {control}")
        return self._describe(session_id, self._find(session_id))

    def set_paused(self, session_id: str, paused: bool) -> MediaSession:
        current = self.get_session(session_id)
        if current is None:
            raise ProtocolError("MEDIA_SESSION_GONE", "That media player is no longer available")
        if paused and current.status == "paused":
            return current
        if not paused and current.status == "playing":
            return current
        return self._invoke(session_id, "pause" if paused else "play")

    def next(self, session_id: str) -> MediaSession:
        return self._invoke(session_id, "next")

    def previous(self, session_id: str) -> MediaSession:
        return self._invoke(session_id, "previous")
