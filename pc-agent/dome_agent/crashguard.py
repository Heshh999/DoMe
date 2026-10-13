"""Last-resort error handling for the frozen programs (``DoMe.exe``, ``dome-native-host.exe``).

Both are windowed PyInstaller programs. An exception that escapes Python there makes the PyInstaller
bootloader show a modal error box and wait for someone to click it. Nobody can when Setup or its
uninstaller runs ``DoMe.exe quit`` / ``install-native-host`` (Setup would wait for ever), or when the
browser starts the native host in the background. :func:`run_guarded` turns every such exception
into an entry in ``<state dir>/logs/crash.log`` (and on stderr when there is one) and the exit code
:data:`EXIT_CRASH`. Only ``DoMe.exe run`` started by a person may show a short plain-English message
box (the caller decides). This module uses nothing but the standard library, so that it still works
when a DoMe module cannot be imported.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path

EXIT_CRASH = 70  # EX_SOFTWARE
CRASH_LOG_NAME = "crash.log"
CRASH_LOG_MAX_BYTES = 1_000_000

CRASH_MESSAGE = (
    "DoMe could not start because of an unexpected problem.\n\n"
    "Details were saved in this file:\n{path}\n\n"
    "Please send that file to the DoMe team."
)


def crash_log_path(argv: Sequence[str] = ()) -> Path:
    """``<state dir>/logs/crash.log``: ``--state-dir`` from the command line, else ``DOME_AGENT_STATE_DIR``,
    else ``%LOCALAPPDATA%\\DoMe`` (``~/.local/state/dome`` off Windows), like the settings."""
    args = list(argv)
    state: Path | None = None
    for i, arg in enumerate(args):
        if arg == "--state-dir" and i + 1 < len(args):
            state = Path(args[i + 1]).expanduser()
            break
        if arg.startswith("--state-dir="):
            state = Path(arg.split("=", 1)[1]).expanduser()
            break
    if state is None:
        env = os.environ.get("DOME_AGENT_STATE_DIR", "").strip()
        if env:
            state = Path(env).expanduser()
        elif sys.platform == "win32":
            base = os.environ.get("LOCALAPPDATA")
            state = (Path(base) if base else Path.home() / "AppData" / "Local") / "DoMe"
        else:
            xdg = os.environ.get("XDG_STATE_HOME")
            state = (Path(xdg) if xdg else Path.home() / ".local" / "state") / "dome"
    return state / "logs" / CRASH_LOG_NAME


def record_crash(program: str, argv: Sequence[str], exc: BaseException) -> Path | None:
    """Append the traceback to crash.log (started afresh once it passes 1 MB). Returns the file, or
    None when it could not be written. Never raises."""
    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    entry = f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {program} {' '.join(argv)}\n{text}\n"
    with contextlib.suppress(Exception):
        if sys.stderr is not None:
            sys.stderr.write(entry)
            sys.stderr.flush()
    try:
        path = crash_log_path(argv)
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "w" if path.exists() and path.stat().st_size > CRASH_LOG_MAX_BYTES else "a"
        with path.open(mode, encoding="utf-8", errors="replace") as fh:
            fh.write(entry)
        return path
    except Exception:  # noqa: BLE001 - nowhere left to report it
        return None


def show_crash_message(path: Path | None) -> None:
    """A plain-English message box (Windows only; it never blocks anything but this process)."""
    if sys.platform != "win32":
        return
    with contextlib.suppress(Exception):
        import ctypes

        text = CRASH_MESSAGE.format(path=path or "(the file could not be written)")
        ctypes.windll.user32.MessageBoxW(None, text, "DoMe", 0x10 | 0x10000)  # MB_ICONERROR | MB_SETFOREGROUND


def run_guarded(
    main: Callable[[], int],
    *,
    program: str,
    argv: Sequence[str] = (),
    show_message: bool = False,
    on_message: Callable[[Path | None], None] = show_crash_message,
) -> int:
    """Run ``main`` and return its exit code. ``SystemExit`` passes through unchanged (argparse uses it),
    Ctrl+C returns 130, and any other exception is recorded and returns :data:`EXIT_CRASH`; with
    ``show_message`` the person who started DoMe also sees a short message box."""
    try:
        return main()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        return 130
    except BaseException as exc:  # noqa: BLE001 - the whole point: nothing may reach the bootloader
        path = record_crash(program, argv, exc)
        if show_message:
            on_message(path)
        return EXIT_CRASH


def wants_message(argv: Sequence[str]) -> bool:
    """Only the tray (``DoMe.exe`` alone, or ``run`` without ``--headless``) shows the crash message box:
    a person started it. Every other command is run by Setup, the uninstaller, a script or the test
    kit, which read the exit code instead."""
    args = list(argv)
    command = next((a for a in args if not a.startswith("-") and a not in _option_values(args)), "run")
    headless = "--headless" in args or os.environ.get("DOME_AGENT_HEADLESS", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    return command == "run" and not headless


def _option_values(args: list[str]) -> set[str]:
    """Values that follow a top-level option taking one (``--state-dir X``), so they are not mistaken for
    the command."""
    return {args[i + 1] for i, a in enumerate(args[:-1]) if a == "--state-dir"}
