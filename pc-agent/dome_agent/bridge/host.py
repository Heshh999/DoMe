"""``dome-native-host``: the Chrome/Edge Native Messaging host process.

Chrome starts one host process per ``chrome.runtime.connectNative`` and talks to it on stdio with
4-byte-length-prefixed JSON. The host validates every frame against the bridge schema in both
directions (``extension_to_agent`` from stdin, ``agent_to_extension`` from the agent) and forwards
it verbatim over the protected IPC endpoint to the running tray agent. It holds no state, executes
nothing, and exits when either side closes.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from typing import IO, Any

from dome_protocol import ProtocolError

from .. import __version__
from ..logsetup import configure_logging, get_logger
from ..settings import load_settings
from . import ipc
from .framing import decode_frame, encode_frame, make_exact_reader, read_frame

log = get_logger(__name__)

ERR_AGENT_NOT_RUNNING = "AGENT_NOT_RUNNING"
ERR_AGENT_DISCONNECTED = "AGENT_DISCONNECTED"


def _binary_stdio() -> tuple[IO[bytes], IO[bytes]]:
    if sys.platform == "win32":
        import msvcrt

        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    return sys.stdin.buffer, sys.stdout.buffer


class NativeHost:
    def __init__(self, state_dir: Path, stdin: IO[bytes], stdout: IO[bytes]) -> None:
        self._state_dir = state_dir
        self._stdin = stdin
        self._stdout = stdout
        self._stdout_lock = threading.Lock()
        self._conn: ipc.FrameConnection | None = None
        self._done = threading.Event()

    # ----- stdout ---------------------------------------------------------------------------------
    def _write_extension(self, frame: dict[str, Any]) -> None:
        data = encode_frame(frame)
        with self._stdout_lock:
            self._stdout.write(data)
            self._stdout.flush()

    def _write_error(self, code: str, message: str, ref_request_id: str | None = None, **detail: Any) -> None:
        frame: dict[str, Any] = {"type": "bridge_error", "error": {"code": code, "message": message[:512]}}
        if detail:
            frame["error"]["detail"] = detail
        if ref_request_id:
            frame["ref_request_id"] = ref_request_id
        try:
            self._write_extension(frame)
        except (OSError, ValueError, ProtocolError):
            pass

    # ----- main -----------------------------------------------------------------------------------
    def run(self) -> int:
        try:
            self._conn = ipc.connect(self._state_dir)
        except (OSError, ConnectionRefusedError) as exc:
            log.warning("agent IPC endpoint unavailable", error=exc.__class__.__name__)
            self._write_error(ERR_AGENT_NOT_RUNNING, "The DoMe agent is not running on this PC.")
            return 1
        agent_thread = threading.Thread(target=self._pump_agent_to_extension, name="agent->ext", daemon=True)
        agent_thread.start()
        self._pump_extension_to_agent()
        self._done.set()
        if self._conn is not None:
            self._conn.close()
        agent_thread.join(timeout=1.0)
        return 0

    def _pump_extension_to_agent(self) -> None:
        assert self._conn is not None
        read_exact = make_exact_reader(self._stdin.read)
        while not self._done.is_set():
            try:
                raw = read_frame(read_exact)
            except ProtocolError as exc:
                log.warning("invalid frame from extension", code=exc.code)
                self._write_error(exc.code, exc.message)
                if exc.code == "PAYLOAD_TOO_LARGE":
                    break  # stream position is unrecoverable after an oversize header
                continue
            except OSError:
                break
            if raw is None:
                break  # extension port closed
            try:
                frame = decode_frame(raw, "extension_to_agent")
            except ProtocolError as exc:
                log.warning("extension frame rejected by schema", code=exc.code)
                self._write_error(exc.code, exc.message)
                continue
            try:
                self._conn.write_frame(encode_frame(frame))
            except (OSError, ProtocolError):
                self._write_error(ERR_AGENT_DISCONNECTED, "The DoMe agent disconnected.")
                break
        self._done.set()

    def _pump_agent_to_extension(self) -> None:
        assert self._conn is not None
        while not self._done.is_set():
            try:
                raw = self._conn.read_frame()
            except (ProtocolError, OSError) as exc:
                log.warning("invalid frame from agent", error=exc.__class__.__name__)
                break
            if raw is None:
                break
            try:
                frame = decode_frame(raw, "agent_to_extension")
            except ProtocolError as exc:
                log.error("agent frame rejected by schema; dropped", code=exc.code)
                continue
            try:
                self._write_extension(frame)
            except (OSError, ValueError):
                break
        if not self._done.is_set():
            self._write_error(ERR_AGENT_DISCONNECTED, "The DoMe agent disconnected.")
        self._done.set()
        try:
            self._stdin.close()
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    settings = load_settings()
    settings.ensure_dirs()
    configure_logging(settings.log_level, settings.log_dir / "native-host.log")
    logging.getLogger().handlers = [
        h
        for h in logging.getLogger().handlers
        if not isinstance(h, logging.StreamHandler) or isinstance(h, logging.FileHandler)
    ]
    origin = next((a for a in argv if a.startswith("chrome-extension://")), "")
    log.info("native host started", version=__version__, origin=origin)
    stdin, stdout = _binary_stdio()
    return NativeHost(settings.state_dir, stdin, stdout).run()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
