"""`dome-agent` for test_e2e_screen.py: the real CLI on the fake platform (DOME_AGENT_PLATFORM=fake, headless), with
the person at the PC played by the test. Nothing in the agent's Screen view path is replaced: the real session
manager, consent gate, indicator, supervisor, the real worker process (`python -m dome_agent.screen.worker`, aiortc,
the fake frame source) and the real signing. Two seams differ from `dome-agent run --headless`:

* The UI. Headless, the agent has no window, so its "Screen view on" bars cannot exist and every start is refused
  `SCREEN_CAPTURE_UNAVAILABLE{indicator}` (fail-closed, design §7). Here the agent's default UI is pc-agent's own test
  double `dome_agent.testing.fake_ui.FakeScreenUI`: bars that confirm on every monitor, and grant / consent windows
  that stay open until the test answers them.
* Extra control ops on the agent's local control channel (the same 0600 socket the tray and CLI use), so the test can
  be the person at the PC: `test_open_grant_window` is the tray's "Allow Screen view…" (`Agent.request_screen_grant`,
  the only way `screen` is widened off the fake platform's shortcut), `test_answer_grant` / `test_answer_consent`
  press the window's buttons, `test_ui` reads what the windows and bars show, `test_bars_present` makes a bar vanish
  (and come back), `test_input` reads where the fake input adapter put the cursor (the MAPPED physical pixels, design
  §8.2) and what it pressed.

DOME_TEST_LYING_AGENT=1 models a patched agent (rules.screen_metering (3)): it never reports `started`, claims
`connection_failed` with `live_seconds: 0` on every end and `live_seconds: 0` in every `screen_usage`. The relay must
charge it on its own clock anyway.

test_e2e_screen.py starts this file instead of the `dome-agent` script."""

from __future__ import annotations

import os
import sys
from typing import Any

from dome_agent import agent as agent_module
from dome_agent.cli import main
from dome_agent.screen import session as session_module
from dome_agent.testing.fake_ui import FakeScreenUI
from dome_agent.ui import ScreenGrantDecision, StatusView

LYING_ENV = "DOME_TEST_LYING_AGENT"


class PersonAtThePC(FakeScreenUI):
    """pc-agent's FakeScreenUI, keeping only the latest status (the agent runs for minutes)."""

    def update_status(self, status: StatusView) -> None:
        self.statuses[:] = [status]


def _control_ops(original: Any) -> Any:
    def control_ops(self: Any) -> dict[str, Any]:
        ops: dict[str, Any] = original(self)
        ui: PersonAtThePC = self.ui
        state = self.platform.input.st  # the fake platform's FakeState (DOME_AGENT_PLATFORM=fake only)

        async def test_ui(_: dict[str, Any]) -> dict[str, Any]:
            return {
                "bars_shown": ui.bars_shown,
                "bar_requests": len(ui.bar_requests),
                "bar_monitors": [len(r.monitors) for r in ui.bar_requests],
                "bars_hidden": ui.hidden,
                "grant_windows": [
                    {"controller_id": r.controller_id, "screen": r.screen, "display_name": r.display_name}
                    for r in ui.grant_requests
                ],
                "consent_windows": [{"controller_id": r.controller_id, "cause": r.cause} for r in ui.consent_requests],
                "closed_consents": len(ui.closed_consents),
                "notifications": [title for title, _message in ui.notifications],
            }

        async def test_open_grant_window(args: dict[str, Any]) -> Any:
            return await self.request_screen_grant(str(args["controller_id"]))

        async def test_answer_grant(args: dict[str, Any]) -> dict[str, Any]:
            decision = (
                ScreenGrantDecision(cooldown_seconds=int(args.get("cooldown_seconds", 600))) if args["allow"] else None
            )
            ui.answer_grant(decision)
            return {"answered": True}

        async def test_answer_consent(args: dict[str, Any]) -> dict[str, Any]:
            ui.answer_consent(str(args["action"]))  # type: ignore[arg-type]
            return {"answered": True}

        async def test_input(_: dict[str, Any]) -> dict[str, Any]:
            injected = [
                [r.name, *map(str, r.args)] for r in state.input_events if r.name in ("move_to", "wheel", "button")
            ]
            return {"cursor": list(state.cursor), "injected": injected[-64:]}

        async def test_bars_present(args: dict[str, Any]) -> dict[str, Any]:
            ui.bars_present = bool(args["present"])  # False: a bar window vanished (what the re-check then sees)
            return {"bars_present": ui.bars_present}

        ops.update(
            test_ui=test_ui,
            test_open_grant_window=test_open_grant_window,
            test_answer_grant=test_answer_grant,
            test_answer_consent=test_answer_consent,
            test_input=test_input,
            test_bars_present=test_bars_present,
        )
        return ops

    return control_ops


def _lie() -> None:
    manager = session_module.ScreenSessionManager
    emit = manager._emit
    usage_frame = session_module.screen_usage_frame

    async def lying_emit(self: Any, session: Any, event: str, reason: str, **kw: Any) -> None:
        if event == "started":
            return  # never tell the relay the picture is up
        await emit(
            self,
            session,
            event,
            "connection_failed" if event == "ended" else reason,
            live_seconds=0,
            path=kw.get("path"),
        )

    def lying_usage(pc_id: str, screen_session_id: str, **_: Any) -> dict[str, Any]:
        return usage_frame(pc_id, screen_session_id, live_seconds=0, relayed=False, relayed_bytes=0)

    manager._emit = lying_emit  # type: ignore[method-assign]
    session_module.screen_usage_frame = lying_usage  # type: ignore[assignment]


def install() -> None:
    agent_module.NullUI = PersonAtThePC  # type: ignore[misc]  # Agent(settings) without a ui uses this
    agent_module.Agent._control_ops = _control_ops(agent_module.Agent._control_ops)  # type: ignore[method-assign]
    if os.environ.get(LYING_ENV) == "1":
        _lie()


if __name__ == "__main__":
    install()
    sys.exit(main())
