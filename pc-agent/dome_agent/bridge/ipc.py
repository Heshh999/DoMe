"""Protected local IPC between ``dome-native-host`` and the agent.

Windows (named pipe, identity rule from ``bridge.schema.json``):
    both processes derive ``\\\\.\\pipe\\DoMe.Agent.<sha256(sid|session)[:16]>`` from the SID of
    their OWN process token (``GetTokenInformation(TokenUser)``) and
    ``ProcessIdToSessionId(GetCurrentProcessId())``. The agent creates the pipe with
    ``FILE_FLAG_FIRST_PIPE_INSTANCE | PIPE_REJECT_REMOTE_CLIENTS`` and a DACL granting only that SID,
    and on every connection verifies via ``GetNamedPipeClientProcessId`` that the client's session id
    and token SID equal its own before any frame is exchanged. A mismatch is closed silently and
    reported as a local security event.

Elsewhere (Unix domain socket): ``<state dir>/bridge.sock`` with mode 0600; the peer's uid
(``SO_PEERCRED``) must equal ours.

All connections are synchronous :class:`FrameConnection` objects (the host is a plain blocking
program; the agent's bridge server drives them from worker threads).
"""

from __future__ import annotations

import hashlib
import os
import socket
import struct
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .framing import make_exact_reader, read_frame

PIPE_PREFIXES = {"bridge": r"\\.\pipe\DoMe.Agent.", "control": r"\\.\pipe\DoMe.Control."}
PIPE_PREFIX = PIPE_PREFIXES["bridge"]
SOCKET_NAMES = {"bridge": "bridge.sock", "control": "control.sock"}
PIPE_BUFFER = 65536
EndpointKind = str  # "bridge" (native host ⇄ agent) | "control" (CLI/tray ⇄ agent)


@dataclass(frozen=True, slots=True)
class PeerInfo:
    pid: int
    identity: str  # SID (Windows) or uid (Unix)
    session: str  # session id (Windows) or "" (Unix)


class IpcIdentityError(Exception):
    """The peer is not the same Windows user/session (or Unix uid). Logged as a security event."""


class FrameConnection:
    """Length-prefixed frame stream over a socket or pipe handle. Thread-safe writes."""

    def __init__(self, read: Callable[[int], bytes], write: Callable[[bytes], None], close: Callable[[], None]) -> None:
        self._read_exact = make_exact_reader(read)
        self._write = write
        self._close = close
        self._write_lock = threading.Lock()
        self._closed = False

    def read_frame(self) -> bytes | None:
        return read_frame(self._read_exact)

    def write_frame(self, data: bytes) -> None:
        with self._write_lock:
            self._write(data)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._close()
        except OSError:
            pass

    @property
    def closed(self) -> bool:
        return self._closed


# ----- endpoint naming ----------------------------------------------------------------------------


def _own_identity() -> tuple[str, str]:
    """(SID, session id) on Windows; (uid, "") elsewhere. Always from our OWN token/process."""
    if sys.platform == "win32":
        import win32api
        import win32process
        import win32security

        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
        sid, _attrs = win32security.GetTokenInformation(token, win32security.TokenUser)
        session_id = win32process.ProcessIdToSessionId(win32api.GetCurrentProcessId())
        return str(win32security.ConvertSidToStringSid(sid)), str(session_id)
    return str(os.getuid()), ""


def pipe_name_for(sid: str, session: str, kind: EndpointKind = "bridge") -> str:
    digest = hashlib.sha256(f"{sid}|{session}".encode()).hexdigest()[:16]
    return f"{PIPE_PREFIXES[kind]}{digest}"


def endpoint_address(state_dir: Path, kind: EndpointKind = "bridge") -> str:
    if sys.platform == "win32":
        sid, session = _own_identity()
        return pipe_name_for(sid, session, kind)
    return str(state_dir / SOCKET_NAMES[kind])


# ----- client (used by dome-native-host) ----------------------------------------------------------


def connect(state_dir: Path, timeout: float = 5.0, kind: EndpointKind = "bridge") -> FrameConnection:
    address = endpoint_address(state_dir, kind)
    if sys.platform == "win32":
        return _connect_pipe(address, timeout)
    return _connect_unix(address, timeout)


def _connect_unix(path: str, timeout: float) -> FrameConnection:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(path)
    sock.settimeout(None)
    return _socket_connection(sock)


def _socket_connection(sock: socket.socket) -> FrameConnection:
    def _close() -> None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()

    return FrameConnection(read=sock.recv, write=sock.sendall, close=_close)


def _connect_pipe(name: str, timeout: float) -> FrameConnection:
    import pywintypes
    import win32file
    import win32pipe

    deadline_ms = int(timeout * 1000)
    try:
        win32pipe.WaitNamedPipe(name, deadline_ms)
    except pywintypes.error as exc:
        raise ConnectionRefusedError(f"agent pipe not available: {exc.strerror}") from exc
    handle = win32file.CreateFile(
        name,
        win32file.GENERIC_READ | win32file.GENERIC_WRITE,
        0,
        None,
        win32file.OPEN_EXISTING,
        0,
        None,
    )
    return _pipe_connection(handle)


def _pipe_connection(handle: Any) -> FrameConnection:
    import pywintypes
    import win32file

    def _read(n: int) -> bytes:
        try:
            _rc, data = win32file.ReadFile(handle, n)
        except pywintypes.error:
            return b""
        return bytes(data)

    def _write(data: bytes) -> None:
        win32file.WriteFile(handle, data)

    def _close() -> None:
        try:
            win32file.CloseHandle(handle)
        except pywintypes.error:
            pass

    return FrameConnection(read=_read, write=_write, close=_close)


# ----- server (agent side) ------------------------------------------------------------------------


OnConnection = Callable[[FrameConnection, PeerInfo], None]
OnIdentityMismatch = Callable[[PeerInfo], None]


class IpcServer:
    """Accepts bridge connections in a background thread and verifies the peer identity."""

    def __init__(self, state_dir: Path, on_connection: OnConnection, on_identity_mismatch: OnIdentityMismatch, *, kind: EndpointKind = "bridge") -> None:
        self._state_dir = state_dir
        self._on_connection = on_connection
        self._on_mismatch = on_identity_mismatch
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._listener: socket.socket | None = None
        self.kind = kind
        self.address = endpoint_address(state_dir, kind)
        self._own_sid, self._own_session = _own_identity()

    def start(self) -> None:
        if sys.platform == "win32":
            target = self._serve_pipe
        else:
            self._listener = self._bind_unix()
            target = self._serve_unix
        self._thread = threading.Thread(target=target, name="dome-bridge-ipc", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
            try:
                os.unlink(self.address)
            except OSError:
                pass
        if sys.platform == "win32":
            # Unblock ConnectNamedPipe by connecting once ourselves.
            try:
                _connect_pipe(self.address, 0.5).close()
            except Exception:  # noqa: S110
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    # ----- unix ---------------------------------------------------------------------------------
    def _bind_unix(self) -> socket.socket:
        path = self.address
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old_umask = os.umask(0o177)
        try:
            sock.bind(path)
        finally:
            os.umask(old_umask)
        os.chmod(path, 0o600)
        sock.listen(8)
        sock.settimeout(0.5)
        return sock

    def _serve_unix(self) -> None:
        assert self._listener is not None
        while not self._stop.is_set():
            try:
                conn, _addr = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            peer = self._unix_peer(conn)
            if peer.identity != self._own_sid:
                conn.close()
                self._on_mismatch(peer)
                continue
            self._on_connection(_socket_connection(conn), peer)

    @staticmethod
    def _unix_peer(conn: socket.socket) -> PeerInfo:
        try:
            creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            pid, uid, _gid = struct.unpack("3i", creds)
            return PeerInfo(pid=pid, identity=str(uid), session="")
        except (OSError, AttributeError):
            return PeerInfo(pid=-1, identity="unknown", session="")

    # ----- windows ------------------------------------------------------------------------------
    def _serve_pipe(self) -> None:
        import pywintypes
        import win32file
        import win32pipe

        first = True
        while not self._stop.is_set():
            try:
                handle = self._create_pipe_instance(first_instance=first)
            except pywintypes.error:
                if first:
                    raise  # someone else owns the name: refuse to start rather than talk to a squatter
                self._stop.wait(1.0)
                continue
            first = False
            try:
                win32pipe.ConnectNamedPipe(handle, None)
            except pywintypes.error:
                win32file.CloseHandle(handle)
                continue
            if self._stop.is_set():
                win32file.CloseHandle(handle)
                break
            peer = self._pipe_peer(handle)
            if peer.identity != self._own_sid or peer.session != self._own_session:
                win32file.CloseHandle(handle)
                self._on_mismatch(peer)
                continue
            self._on_connection(_pipe_connection(handle), peer)

    def _create_pipe_instance(self, *, first_instance: bool) -> Any:
        import ntsecuritycon
        import win32con
        import win32pipe
        import win32security

        sid = win32security.ConvertStringSidToSid(self._own_sid)
        dacl = win32security.ACL()
        dacl.AddAccessAllowedAce(win32security.ACL_REVISION, ntsecuritycon.GENERIC_ALL, sid)
        descriptor = win32security.SECURITY_DESCRIPTOR()
        descriptor.SetSecurityDescriptorOwner(sid, False)
        descriptor.SetSecurityDescriptorDacl(True, dacl, False)
        attributes = win32security.SECURITY_ATTRIBUTES()
        attributes.SECURITY_DESCRIPTOR = descriptor
        attributes.bInheritHandle = False
        open_mode = win32pipe.PIPE_ACCESS_DUPLEX
        if first_instance:
            open_mode |= win32con.FILE_FLAG_FIRST_PIPE_INSTANCE
        pipe_mode = win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT | win32pipe.PIPE_REJECT_REMOTE_CLIENTS
        return win32pipe.CreateNamedPipe(
            self.address,
            open_mode,
            pipe_mode,
            win32pipe.PIPE_UNLIMITED_INSTANCES,
            PIPE_BUFFER,
            PIPE_BUFFER,
            0,
            attributes,
        )

    @staticmethod
    def _pipe_peer(handle: Any) -> PeerInfo:
        import win32api
        import win32con
        import win32pipe
        import win32process
        import win32security

        try:
            pid = int(win32pipe.GetNamedPipeClientProcessId(handle))
            process = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            try:
                token = win32security.OpenProcessToken(process, win32security.TOKEN_QUERY)
                sid, _attrs = win32security.GetTokenInformation(token, win32security.TokenUser)
                session = str(win32process.ProcessIdToSessionId(pid))
                return PeerInfo(pid=pid, identity=str(win32security.ConvertSidToStringSid(sid)), session=session)
            finally:
                win32api.CloseHandle(process)
        except Exception:
            return PeerInfo(pid=-1, identity="unknown", session="unknown")
