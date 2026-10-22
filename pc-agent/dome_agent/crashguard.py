"""Last-resort error handling for the frozen programs (``DoMe.exe``, ``dome-native-host.exe``).

Both are windowed PyInstaller programs. An exception that escapes Python there makes the PyInstaller
bootloader show a modal error box and wait for someone to click it. Nobody can when Setup or its
uninstaller runs ``DoMe.exe quit`` / ``install-native-host`` (Setup would wait for ever), or when the
browser starts the native host in the background. :func:`run_guarded` turns every such exception
into an entry in ``<state dir>/logs/crash.log`` (and on stderr when there is one) and the exit code
:data:`EXIT_CRASH`. Only ``DoMe.exe run`` started by a person may show a short plain-English message
box (the caller decides). This module uses nothing but the standard library, so that it still works
when a DoMe module cannot be imported.

crash.log gets the same masking as every other DoMe log (``logsetup``: bearer tokens and long
token-like strings). It is still not a reviewed support bundle: an exception message can carry paths,
addresses or other text, so the user reads it before sharing any of it (``docs/SUPPORT.md``).
"""

from __future__ import annotations

import contextlib
import os
import re
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
    "To get help, write what you were doing in a support request. Keep that file: the DoMe team may ask "
    "you to open it and copy its last lines, after you have read them."
)

# The same patterns as logsetup (_BEARER_RE, _LONG_TOKEN_RE, _IPV4_RE, _IPV6_RE), copied: this module imports
# nothing of DoMe. The address patterns exist for Screen view (design §4.4): the worker's crash log is written
# only through here and must carry no peer address, loopback included.
_BEARER_RE = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}")
_LONG_TOKEN_RE = re.compile(r"\b[A-Za-z0-9_-]{40,}\b")
_IPV4_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_IPV4_RE = re.compile(rf"(?<![A-Za-z0-9.])(?:{_IPV4_OCTET}\.){{3}}{_IPV4_OCTET}(?::\d{{1,5}})?(?!\w|\.\d)")
_HEX4 = r"[0-9A-Fa-f]{1,4}"
_IPV6_BODY = (
    rf"(?:(?:{_HEX4}:){{7}}{_HEX4}"
    rf"|(?:{_HEX4}:){{1,6}}:(?:{_HEX4}(?::{_HEX4}){{0,5}})?"
    rf"|::(?:{_HEX4}(?::{_HEX4}){{0,6}})?)"
)
_IPV6_RE = re.compile(
    rf"(?<![\w:.])(?:\[{_IPV6_BODY}(?:%[\w.-]+)?\](?::\d{{1,5}})?|{_IPV6_BODY}(?:%[\w.-]+)?)(?![\w:])"
)
_SDP_RE = re.compile(
    r"(?<![\w-])(?:a=(?:fingerprint|ice-pwd|ice-ufrag|candidate|setup|rtpmap|fmtp|mid|msid|ssrc|group)|c=IN|"
    r"m=(?:video|audio|application)|o=-)[^\r\n]*"
)
REDACTED = "[redacted]"


def redact(text: str) -> str:
    """Mask bearer tokens, long token-like strings, IPv4/IPv6 addresses (with a port) and SDP attribute lines, like
    every other DoMe log."""
    text = _LONG_TOKEN_RE.sub(REDACTED, _BEARER_RE.sub("Bearer " + REDACTED, text))
    return _SDP_RE.sub(REDACTED, _IPV6_RE.sub(REDACTED, _IPV4_RE.sub(REDACTED, text)))


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
    """Append the traceback to crash.log (started afresh once it passes 1 MB), masked like every other
    DoMe log. Returns the file, or None when it could not be written. Never raises."""
    try:
        text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        entry = redact(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {program} {' '.join(argv)}\n{text}\n")
    except Exception:  # noqa: BLE001 - an exception whose str() fails must still end in a crash entry
        entry = f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {program}\n{type(exc).__name__} (details unavailable)\n\n"
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
