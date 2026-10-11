"""`dome-agent` for test_e2e_windows.py: the real CLI on the fake platform (DOME_AGENT_PLATFORM=fake), except
that an app started with app.launch also shows one window and comes to the front, as a real program does. The
fake window manager in pc-agent's dome_agent/testing/fake_platform.py then answers app.maximize / app.restore /
app.set_fullscreen for it exactly as the agent's own tests drive it. Nothing else differs from
`dome-agent run`; test_e2e_windows.py starts this file instead of the `dome-agent` script.

A browser's window is titled from DOME_TEST_BROWSER_TITLE: after a YouTube search a real browser window's
title is "<query> - YouTube - Microsoft Edge", which must never leave the PC (rules.search_queries)."""

from __future__ import annotations

import os
import sys
from pathlib import PureWindowsPath

from dome_agent.cli import main
from dome_agent.platform.protocol import ForegroundApp, browser_for_process
from dome_agent.testing import fake_platform

WINDOW_ID_OFFSET = 70000  # the window of the process with pid N is window_id str(70000 + N)
BROWSER_TITLE_ENV = "DOME_TEST_BROWSER_TITLE"


def install() -> None:
    launch = fake_platform.FakeApps.launch

    def launch_with_window(self: fake_platform.FakeApps, exe_path: str) -> int:
        pid = launch(self, exe_path)
        name = PureWindowsPath(exe_path).name
        browser = browser_for_process(name)
        title = os.environ.get(BROWSER_TITLE_ENV, "New tab - Microsoft Edge") if browser else "Untitled - Notepad"
        window_id = str(WINDOW_ID_OFFSET + pid)
        self.st.add_window(pid, window_id, title)
        self.st.foreground_app = ForegroundApp(name, title, browser, False, window_id, pid)
        return pid

    fake_platform.FakeApps.launch = launch_with_window  # type: ignore[method-assign]


if __name__ == "__main__":
    install()
    sys.exit(main())
