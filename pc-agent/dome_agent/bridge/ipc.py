"""Protected local IPC between ``dome-native-host`` and the agent.

Windows (named pipe, identity rule from ``bridge.schema.json``):
    both processes derive ``\\\\.\\pipe\\DoMe.Agent.<sha256(sid|session)[:16]>`` from the SID of
    their OWN process token (``GetTokenInformation(TokenUser)``) and
    ``ProcessIdToSessionId(GetCurrentProcessId())``. The agent creates the pipe with
    ``FILE_FLAG_FIRST_PIPE_INSTANCE | PIPE_REJECT_REMOTE_CLIENTS`` and a DACL granting only that SID,
    and on every connection verifies via ``GetNamedPipeClientProcessId`` that the client's session id
    and token SID equal its own before any frame is exchanged. A mismatch is closed silently and
    reported as a local security event. The client opens the pipe at ``SECURITY_IDENTIFICATION`` level,
    so whoever serves the name can identify it but never impersonate it, and checks the same way
    (``GetNamedPipeServerProcessId``) that the server is its own SID and session before it writes a frame:
    a process that took the name first is refused with ConnectionRefusedError.

    Both ends are OVERLAPPED handles. Each side reads in one thread while other threads write and close;
    on a synchronous handle Windows runs one operation at a time, so a WriteFile (or CloseHandle) waits
    behind the ReadFile pending in the reader thread, which deadlocked the very first ``bridge_hello``.
    The I/O uses the stdlib ``_winapi`` module the way :mod:`multiprocessing.connection` does; pywin32 is
    only used for the DACL'd ``CreateNamedPipe`` and the peer check. Every failure on these paths is an
    :class:`OSError` (``pywintypes.error`` is not one).

Elsewhere (Unix domain socket): ``<state dir>/bridge.sock`` with mode 0600; the peer's uid
(``SO_PEERCRED``) must equal ours.

All connections are synchronous :class:`FrameConnection` objects (the host is a plain blocking
program; the agent's bridge server drives them from worker threads). A read pending in one thread
never holds up a write from another, and ``close()`` makes that read return EOF.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import os
import socket
import struct
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..logsetup import get_logger
from .framing import make_exact_reader, read_frame

log = get_logger(__name__)

PIPE_PREFIXES = {"bridge": r"\\.\pipe\DoMe.Agent.", "control": r"\\.\pipe\DoMe.Control."}
PIPE_PREFIX = PIPE_PREFIXES["bridge"]
SOCKET_NAMES = {"bridge": "bridge.sock", "control": "control.sock"}
PIPE_BUFFER = 65536
EndpointKind = str  # "bridge" (native host ⇄ agent) | "control" (CLI/tray ⇄ agent)

_SECURITY_SQOS_PRESENT = 0x00100000
_SECURITY_IDENTIFICATION = 0x00010000  # SecurityIdentification << 16
_ACCEPT_POLL_MS = 250  # the accept thread notices stop() at least this often
_NAME_RETRY_SECONDS = 2.0  # retry interval while another process holds our pipe name
_HAND_OFF_RETRY_SECONDS = 1.0  # pause after a connection could not be handed off, then keep serving


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
        import win32security
        import win32ts

        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32security.TOKEN_QUERY)
        sid, _attrs = win32security.GetTokenInformation(token, win32security.TokenUser)
        session_id = win32ts.ProcessIdToSessionId(win32api.GetCurrentProcessId())  # type: ignore[no-untyped-call]
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
    if sys.platform == "win32":
        sid, session = _own_identity()
        return _connect_pipe(pipe_name_for(sid, session, kind), timeout, (sid, session))
    return _connect_unix(endpoint_address(state_dir, kind), timeout)


def _connect_unix(path: str, timeout: float) -> FrameConnection:
    if sys.platform == "win32":
        raise RuntimeError("Unix sockets are not used on Windows (named pipes are)")
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


def _connect_pipe(name: str, timeout: float, expected: tuple[str, str]) -> FrameConnection:
    """Open the agent's pipe; ConnectionRefusedError when no agent serves it (or none frees up in time),
    or when the process serving it is not ``expected`` = our own (SID, session id).

    The name is derived from our identity, but a name alone proves nothing: another process can create
    it first (``IpcServer.start`` reports that case), and a process of another user then sees every frame
    we send. So, like the agent checks its clients (:meth:`IpcServer._pipe_peer`), the client checks the
    server's token SID and session (``GetNamedPipeServerProcessId``) before a single frame is written."""
    if sys.platform != "win32":
        raise RuntimeError("named pipes are Windows-only")
    import _winapi

    deadline = time.monotonic() + timeout
    while True:
        try:
            _winapi.WaitNamedPipe(name, max(1, int((deadline - time.monotonic()) * 1000)))
            handle = _winapi.CreateFile(
                name,
                _winapi.GENERIC_READ | _winapi.GENERIC_WRITE,
                0,
                _winapi.NULL,
                _winapi.OPEN_EXISTING,
                _winapi.FILE_FLAG_OVERLAPPED | _SECURITY_SQOS_PRESENT | _SECURITY_IDENTIFICATION,
                _winapi.NULL,
            )
        except OSError as exc:
            # ERROR_PIPE_BUSY: another client took the free instance between the wait and the open.
            # ERROR_SEM_TIMEOUT: no instance freed up. No such pipe (FileNotFoundError): no agent, fail now.
            if exc.winerror in (_winapi.ERROR_PIPE_BUSY, _winapi.ERROR_SEM_TIMEOUT) and time.monotonic() < deadline:
                continue
            raise ConnectionRefusedError(f"agent pipe not available: {exc.strerror}") from exc
        server = _pipe_end_identity(handle, "server")
        if (server.identity, server.session) != expected:
            with contextlib.suppress(OSError):
                _winapi.CloseHandle(handle)
            raise ConnectionRefusedError(
                f"the pipe {name} is served by a process of another user or session (pid {server.pid}); not used"
            )
        return _overlapped_pipe_connection(handle)


def _pipe_end_identity(handle: int, end: str) -> PeerInfo:
    """Who holds the other end of a connected pipe handle: the token SID and session id of the
    ``"client"`` process (asked by the agent) or of the ``"server"`` process (asked by a client).
    ``PeerInfo(-1, "unknown", "unknown")``, which never equals a real identity, when Windows does not say."""
    if sys.platform != "win32":
        raise RuntimeError("named pipes are Windows-only")
    import win32api
    import win32con
    import win32pipe
    import win32security
    import win32ts

    try:
        if end == "server":
            pid = int(win32pipe.GetNamedPipeServerProcessId(handle))
        else:
            pid = int(win32pipe.GetNamedPipeClientProcessId(handle))
        process = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        try:
            token = win32security.OpenProcessToken(process, win32security.TOKEN_QUERY)
            sid, _attrs = win32security.GetTokenInformation(token, win32security.TokenUser)
            session = str(win32ts.ProcessIdToSessionId(pid))  # type: ignore[no-untyped-call, unused-ignore]
            return PeerInfo(pid=pid, identity=str(win32security.ConvertSidToStringSid(sid)), session=session)
        finally:
            win32api.CloseHandle(process)
    except Exception:
        return PeerInfo(pid=-1, identity="unknown", session="unknown")


def _overlapped_pipe_connection(handle: int) -> FrameConnection:
    """Frames over an OVERLAPPED pipe handle (a raw handle value this connection owns and closes).

    Reads and writes each wait on their own OVERLAPPED event, so they run at the same time. ``close()``
    cancels whatever is pending (CancelIoEx) before closing the handle: a blocked read then returns EOF
    and a blocked write raises BrokenPipeError."""
    if sys.platform != "win32":
        raise RuntimeError("named pipes are Windows-only")
    import _winapi

    lock = threading.Lock()  # no operation is ever started on a handle value close() already released
    closed = False

    def _complete(start: Callable[[], tuple[Any, int]]) -> tuple[Any, int, int] | None:  # Any: _winapi.Overlapped
        """Start one operation and wait for it: (overlapped, bytes transferred, error), or None once closed.
        Raises OSError (BrokenPipeError when the peer is gone) like ``_winapi`` does."""
        with lock:
            if closed:
                return None
            ov, err = start()
        try:
            if err == _winapi.ERROR_IO_PENDING:
                _winapi.WaitForMultipleObjects([ov.event], False, _winapi.INFINITE)
        except BaseException:
            ov.cancel()
            raise
        finally:
            transferred, err = ov.GetOverlappedResult(True)  # also waits out a cancelled operation
        return ov, transferred, err

    def _read(n: int) -> bytes:
        try:
            done = _complete(lambda: _winapi.ReadFile(handle, n, overlapped=True))
        except OSError:  # ERROR_BROKEN_PIPE / ERROR_PIPE_NOT_CONNECTED: the peer is gone
            return b""
        if done is None:
            return b""
        ov, _nread, err = done
        if err != 0:  # ERROR_OPERATION_ABORTED: close() cancelled the read
            return b""
        return bytes(ov.getbuffer() or b"")  # byte-mode pipe: ERROR_MORE_DATA cannot occur

    def _write(data: bytes) -> None:
        done = _complete(lambda: _winapi.WriteFile(handle, data, overlapped=True))
        if done is None:
            raise BrokenPipeError("the pipe is closed")
        _ov, written, err = done
        if err != 0 or written != len(data):  # a pipe write completes whole or is aborted by close()
            raise BrokenPipeError(f"pipe write did not complete (Windows error {err})")

    def _close() -> None:
        nonlocal closed
        with lock:
            if closed:
                return
            closed = True
            _cancel_io_ex()(handle, None)  # FALSE (ERROR_NOT_FOUND) when nothing is pending: fine
            _winapi.CloseHandle(handle)

    return FrameConnection(read=_read, write=_write, close=_close)


@functools.cache
def _cancel_io_ex() -> Any:
    """kernel32 ``CancelIoEx(handle, NULL)``: cancels every operation pending on a handle, from any thread
    (``_winapi`` only cancels one OVERLAPPED at a time)."""
    if sys.platform != "win32":
        raise RuntimeError("named pipes are Windows-only")
    import ctypes
    from ctypes import wintypes

    cancel = ctypes.WinDLL("kernel32", use_last_error=True).CancelIoEx
    cancel.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    cancel.restype = wintypes.BOOL
    return cancel


def _os_error(exc: Any) -> OSError:
    """``pywintypes.error`` is not an OSError: convert it (winerror kept, so Windows picks the subclass)."""
    return OSError(None, f"{exc.funcname}: {exc.strerror}", None, exc.winerror)


def _detach(handle: Any) -> int:
    """pywin32 returns a PyHANDLE that closes itself when garbage-collected: take the raw value over."""
    detach = getattr(handle, "Detach", None)
    return int(detach() if detach is not None else handle)


# ----- server (agent side) ------------------------------------------------------------------------


OnConnection = Callable[[FrameConnection, PeerInfo], None]
OnIdentityMismatch = Callable[[PeerInfo], None]


class IpcServer:
    """Accepts bridge connections in a background thread and verifies the peer identity."""

    def __init__(
        self,
        state_dir: Path,
        on_connection: OnConnection,
        on_identity_mismatch: OnIdentityMismatch,
        *,
        kind: EndpointKind = "bridge",
    ) -> None:
        self._state_dir = state_dir
        self._on_connection = on_connection
        self._on_mismatch = on_identity_mismatch
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._listener: socket.socket | None = None
        self._first_handle: int | None = None
        self.kind = kind
        self.address = endpoint_address(state_dir, kind)
        self._own_sid, self._own_session = _own_identity()
        self.unavailable_reason = ""  # set while another process holds our pipe name (Windows)

    def start(self) -> bool:
        """Start accepting in a background thread. Returns False when the endpoint is not listening yet:
        on Windows another process still holds the pipe name (an old ``dome-native-host`` keeping the
        previous agent's pipe open, or a squatter). ``unavailable_reason`` then says so and the thread keeps
        retrying ``FILE_FLAG_FIRST_PIPE_INSTANCE`` until the name is free. A Unix bind failure raises."""
        if sys.platform == "win32":
            try:
                self._first_handle = self._create_pipe_instance(first_instance=True)
            except OSError as exc:
                self.unavailable_reason = self._name_held_reason(exc)
            target = self._serve_pipe
        else:
            self._listener = self._bind_unix()
            target = self._serve_unix
        self._thread = threading.Thread(target=target, name=f"dome-{self.kind}-ipc", daemon=True)
        self._thread.start()
        return not self.unavailable_reason

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
        if self._thread is not None:
            self._thread.join(timeout=2.0)  # the pipe accept loop notices the stop within _ACCEPT_POLL_MS

    # ----- unix ---------------------------------------------------------------------------------
    def _bind_unix(self) -> socket.socket:
        if sys.platform == "win32":
            raise RuntimeError("Unix sockets are not used on Windows (named pipes are)")
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
            self._hand_off(
                functools.partial(self._unix_peer, conn),
                functools.partial(_socket_connection, conn),
                conn.close,
            )

    # ----- both ---------------------------------------------------------------------------------
    def _hand_off(
        self, identify: Callable[[], PeerInfo], wrap: Callable[[], FrameConnection], discard: Callable[[], None]
    ) -> None:
        """Pass one accepted connection to the owner, or close it and report a peer that is not us.

        Never raises: whatever fails here (the owner's callback, its event loop already closed, closing a
        handle) costs this one connection, not the endpoint, and the raw connection is closed exactly once
        unless it was wrapped (then the wrapper owns it)."""
        conn: FrameConnection | None = None
        raw_owned = True  # the raw connection is still ours to close
        try:
            peer = identify()
            if (peer.identity, peer.session) != (self._own_sid, self._own_session):
                raw_owned = False
                discard()
                self._on_mismatch(peer)
                return
            conn = wrap()
            raw_owned = False
            self._on_connection(conn, peer)
        except Exception as exc:
            log.exception(
                "IPC connection hand-off failed; endpoint still serving", kind=self.kind, error=exc.__class__.__name__
            )
            if conn is not None:
                conn.close()
            elif raw_owned:
                with contextlib.suppress(OSError):
                    discard()
            self._stop.wait(_HAND_OFF_RETRY_SECONDS)

    @staticmethod
    def _unix_peer(conn: socket.socket) -> PeerInfo:
        if sys.platform == "win32":
            raise RuntimeError("Unix sockets are not used on Windows (named pipes are)")
        try:
            creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            pid, uid, _gid = struct.unpack("3i", creds)
            return PeerInfo(pid=pid, identity=str(uid), session="")
        except (OSError, AttributeError):
            return PeerInfo(pid=-1, identity="unknown", session="")

    # ----- windows ------------------------------------------------------------------------------
    def _serve_pipe(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("named pipes are Windows-only")
        import _winapi

        handle, self._first_handle = self._first_handle, None
        owned = handle is not None  # once we hold an instance, later ones are created without FIRST_PIPE_INSTANCE
        try:
            while not self._stop.is_set():
                if handle is None:
                    try:
                        handle = self._create_pipe_instance(first_instance=not owned)
                    except OSError as exc:
                        if not owned:
                            reason = self._name_held_reason(exc)
                            if reason != self.unavailable_reason:
                                log.warning("IPC endpoint name held by another process; retrying", kind=self.kind)
                            self.unavailable_reason = reason
                        self._stop.wait(_NAME_RETRY_SECONDS)
                        continue
                    if not owned:
                        owned = True
                        self.unavailable_reason = ""
                        log.info("IPC endpoint listening after retry", kind=self.kind, address=self.address)
                # Whatever happens to this instance, the next one exists before it is closed, so the name
                # never lapses: a lapsed name can be taken by another process, and our next instance,
                # created without FILE_FLAG_FIRST_PIPE_INSTANCE, would then join that process's pipe.
                if not self._wait_for_client(handle):  # a client came and left, the wait failed, or stop()
                    failed, handle = handle, None
                    if not self._stop.is_set():
                        handle = self._next_pipe_instance()
                    with contextlib.suppress(OSError):
                        _winapi.CloseHandle(failed)
                    continue
                client, handle = handle, self._next_pipe_instance()
                self._hand_off(
                    functools.partial(self._pipe_peer, client),
                    functools.partial(_overlapped_pipe_connection, client),
                    functools.partial(_winapi.CloseHandle, client),
                )
        except Exception as exc:  # last resort: never let the endpoint vanish without a trace in the agent log
            log.exception("IPC accept loop failed; endpoint closed", kind=self.kind, error=exc.__class__.__name__)
            self.unavailable_reason = f"the {self.kind} pipe accept loop failed ({exc.__class__.__name__})"
        finally:
            if handle is not None:
                with contextlib.suppress(OSError):
                    _winapi.CloseHandle(handle)

    def _next_pipe_instance(self) -> int | None:
        """One more instance of the pipe we already own, or None (the loop then retries every 2 s)."""
        try:
            return self._create_pipe_instance(first_instance=False)
        except OSError as exc:
            log.warning("could not create the next IPC pipe instance", kind=self.kind, error=exc.__class__.__name__)
            return None

    def _wait_for_client(self, handle: int) -> bool:
        """Overlapped ConnectNamedPipe, waited for in slices so stop() is noticed. True once a client is connected."""
        if sys.platform != "win32":
            raise RuntimeError("named pipes are Windows-only")
        import _winapi

        try:
            ov = _winapi.ConnectNamedPipe(handle, overlapped=True)  # ERROR_PIPE_CONNECTED: event already set
        except OSError:  # ERROR_NO_DATA: a client connected and left before we got here
            return False
        err = -1
        try:
            while _winapi.WaitForMultipleObjects([ov.event], False, _ACCEPT_POLL_MS) == _winapi.WAIT_TIMEOUT:
                if self._stop.is_set():
                    ov.cancel()
                    break
        except BaseException:
            ov.cancel()
            raise
        finally:
            with contextlib.suppress(OSError):
                _n, err = ov.GetOverlappedResult(True)  # ERROR_OPERATION_ABORTED after cancel()
        return err == 0 and not self._stop.is_set()

    def _name_held_reason(self, exc: OSError) -> str:
        return (
            f"the {self.kind} pipe {self.address} is held by another process ({exc.strerror}); quit old "
            "DoMe / dome-native-host processes or sign out and back in"
        )

    def _create_pipe_instance(self, *, first_instance: bool) -> int:
        """A new OVERLAPPED instance of our pipe, as a raw handle value the caller owns. OSError on failure."""
        if sys.platform != "win32":
            raise RuntimeError("named pipes are Windows-only")
        import _winapi

        import ntsecuritycon
        import pywintypes
        import win32pipe
        import win32security

        try:
            sid = win32security.ConvertStringSidToSid(self._own_sid)
            dacl = win32security.ACL()
            dacl.AddAccessAllowedAce(win32security.ACL_REVISION, ntsecuritycon.GENERIC_ALL, sid)
            descriptor = win32security.SECURITY_DESCRIPTOR()
            descriptor.SetSecurityDescriptorOwner(sid, False)
            descriptor.SetSecurityDescriptorDacl(True, dacl, False)
            attributes = win32security.SECURITY_ATTRIBUTES()
            attributes.SECURITY_DESCRIPTOR = descriptor
            attributes.bInheritHandle = False
            open_mode = win32pipe.PIPE_ACCESS_DUPLEX | _winapi.FILE_FLAG_OVERLAPPED
            if first_instance:
                open_mode |= win32pipe.FILE_FLAG_FIRST_PIPE_INSTANCE
            pipe_mode = (
                win32pipe.PIPE_TYPE_BYTE
                | win32pipe.PIPE_READMODE_BYTE
                | win32pipe.PIPE_WAIT
                | win32pipe.PIPE_REJECT_REMOTE_CLIENTS
            )
            pipe = win32pipe.CreateNamedPipe(
                self.address,
                open_mode,
                pipe_mode,
                win32pipe.PIPE_UNLIMITED_INSTANCES,
                PIPE_BUFFER,
                PIPE_BUFFER,
                0,
                attributes,
            )
        except pywintypes.error as exc:
            raise _os_error(exc) from exc
        return _detach(pipe)

    @staticmethod
    def _pipe_peer(handle: int) -> PeerInfo:
        return _pipe_end_identity(handle, "client")
