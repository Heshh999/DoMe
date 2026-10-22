"""Screen view history (design §2.3, §15): the last 20 sessions and refused starts, metadata only.

Rows hold the phone's name, start and end, duration, and the end reason or the refusal cause; never a frame, an
SDP or an address (``rules.screen_content``). Shown in the status window ("Screen view history"), clearable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dome_protocol import format_rfc3339, now_utc

from ..store import SCREEN_HISTORY_MAX_ROWS

if TYPE_CHECKING:
    from ..store import Store

REFUSAL_REASONS = ("denied", "timeout", "backoff", "blocked")
END_REASON_TEXT = {
    "stopped": "stopped from the phone",
    "stopped_on_pc": "stopped at this PC",
    "replaced": "the phone started again",
    "viewer_gone": "the phone went away",
    "takeover": "another phone took over",
    "controller_revoked": "the phone's access was revoked",
    "controller_offline": "the phone was signed out",
    "grant_removed": "Screen view was turned off for the phone",
    "plan_disabled": "the plan does not allow it",
    "remote_disabled": "remote control was disabled",
    "session_locked": "this PC was locked",
    "secure_desktop": "a Windows permission prompt stayed open",
    "allowance_exhausted": "the Free minutes were used up",
    "allowance_unconfirmed": "the DoMe service could not be reached",
    "max_duration": "the 4-hour limit was reached",
    "capture_failed": "this PC could not keep capturing",
    "connection_failed": "the phone could not connect",
    "agent_restart": "DoMe restarted",
    "denied": "refused at this PC",
    "timeout": "nobody answered at this PC",
    "backoff": "refused without asking (too many refusals)",
    "blocked": "this phone is blocked for an hour",
}


def _duration_text(seconds: int) -> str:
    minutes, rest = divmod(max(0, int(seconds)), 60)
    return f"{minutes} min {rest} s" if minutes else f"{rest} s"


class ScreenHistory:
    def __init__(self, store: Store) -> None:
        self._store = store

    def record_session(
        self, *, controller_id: str, display_name: str, started_at: str, live_seconds: int, reason: str
    ) -> None:
        self._store.add_screen_history(
            controller_id=controller_id,
            display_name=display_name,
            kind="session",
            started_at=started_at,
            ended_at=format_rfc3339(now_utc()),
            live_seconds=live_seconds,
            reason=reason,
        )

    def record_refusal(self, *, controller_id: str, display_name: str, reason: str) -> None:
        """A declined, timed-out, backed-off or blocked start (design §6.3)."""
        if reason not in REFUSAL_REASONS:
            raise ValueError(reason)
        now = format_rfc3339(now_utc())
        self._store.add_screen_history(
            controller_id=controller_id,
            display_name=display_name,
            kind="refused",
            started_at=now,
            ended_at=now,
            live_seconds=0,
            reason=reason,
        )

    def recent(self, limit: int = SCREEN_HISTORY_MAX_ROWS) -> list[dict[str, Any]]:
        return [row.as_dict() for row in self._store.list_screen_history(limit)]

    def clear(self) -> int:
        return self._store.clear_screen_history()

    def lines(self) -> list[str]:
        """Plain-English lines for the status window, newest first."""
        out: list[str] = []
        for row in self._store.list_screen_history():
            when = row.started_at.replace("T", " ")[:16]
            why = END_REASON_TEXT.get(row.reason, row.reason)
            if row.kind == "refused":
                out.append(f"{when}  {row.display_name}: start {why}")
            else:
                out.append(f"{when}  {row.display_name}: {_duration_text(row.live_seconds)}, {why}")
        return out


__all__ = ["END_REASON_TEXT", "REFUSAL_REASONS", "ScreenHistory"]
