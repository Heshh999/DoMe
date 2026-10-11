"""The IPC endpoint's connection handling, run on any OS.

* The Windows accept loop and client are driven with stand-ins for ``_winapi`` and the pywin32 calls
  (the real overlapped pipe is covered by test_ipc_duplex.py on windows-latest): the pipe name never
  lapses when an instance fails, a client refuses a pipe served by another user or session, and one
  connection that cannot be handed off never ends the endpoint or leaks its handle.
* The same hand-off rule over a real Unix socket.
"""

from __future__ import annotations

import queue
import sys
import threading
import types
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from dome_agent.bridge import ipc
from dome_agent.bridge.framing import encode_frame
from dome_agent.settings import Settings

OWN = ("S-1-5-21-1000", "1")
DEADLINE = 5.0


class FakeWinapi(types.SimpleNamespace):
    """The parts of ``_winapi`` the client and the accept loop touch; handles are plain ints."""

    NULL = 0
    GENERIC_READ = 0x80000000
    GENERIC_WRITE = 0x40000000
    OPEN_EXISTING = 3
    FILE_FLAG_OVERLAPPED = 0x40000000
    ERROR_PIPE_BUSY = 231
    ERROR_SEM_TIMEOUT = 121

    def __init__(self, events: list[tuple[Any, ...]]) -> None:
        super().__init__()
        self.events = events
        self.next_client_handle = 77

    def CloseHandle(self, handle: int) -> None:  # noqa: N802 - the _winapi name
        self.events.append(("close", handle))

    def WaitNamedPipe(self, name: str, timeout_ms: int) -> None:  # noqa: N802
        self.events.append(("wait_named_pipe", name))

    def CreateFile(self, name: str, *_args: Any) -> int:  # noqa: N802
        self.events.append(("create_file", name))
        return self.next_client_handle


class FakeConn(ipc.FrameConnection):
    def __init__(self, handle: int, events: list[tuple[Any, ...]]) -> None:
        super().__init__(
            read=lambda _n: b"", write=lambda _d: None, close=lambda: events.append(("conn_close", handle))
        )
        self.handle = handle


@pytest.fixture
def events() -> list[tuple[Any, ...]]:
    return []


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch, events: list[tuple[Any, ...]]) -> FakeWinapi:
    """``ipc`` takes the Windows paths (only its own ``sys.platform`` check is changed) with fake I/O."""
    fake = FakeWinapi(events)
    monkeypatch.setitem(sys.modules, "_winapi", fake)
    monkeypatch.setattr(ipc, "sys", types.SimpleNamespace(platform="win32"))
    monkeypatch.setattr(ipc, "_overlapped_pipe_connection", lambda handle: FakeConn(handle, events))
    monkeypatch.setattr(ipc, "_HAND_OFF_RETRY_SECONDS", 0.0)
    return fake


class PipeLoop:
    """An IpcServer whose pipe instances, client waits and peer checks are scripted.

    ``waits`` lists what each ConnectNamedPipe wait reports (True: a client connected; False: the client
    came and left, or the wait failed); after the script the wait blocks until stop()."""

    def __init__(
        self,
        state_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        events: list[tuple[Any, ...]],
        *,
        waits: list[bool],
        peers: dict[int, ipc.PeerInfo | Exception] | None = None,
        on_connection: Callable[[ipc.FrameConnection, ipc.PeerInfo], None] | None = None,
        on_mismatch: Callable[[ipc.PeerInfo], None] | None = None,
    ) -> None:
        self.events = events
        self.delivered: queue.Queue[int] = queue.Queue()
        self.mismatches: list[ipc.PeerInfo] = []
        self.idle = threading.Event()  # the script is used up: the loop now waits on its last instance
        self._waits = list(waits)
        self._peers = peers or {}
        self._next_handle = 1

        def deliver(conn: ipc.FrameConnection, peer: ipc.PeerInfo) -> None:
            assert isinstance(conn, FakeConn)
            self.events.append(("deliver", conn.handle))
            self.delivered.put(conn.handle)

        monkeypatch.setattr(ipc, "_own_identity", lambda: OWN)
        self.server = ipc.IpcServer(
            state_dir, on_connection or deliver, on_mismatch or self.mismatches.append, kind="bridge"
        )
        self.server._own_sid, self.server._own_session = OWN
        monkeypatch.setattr(self.server, "_create_pipe_instance", self._create)
        monkeypatch.setattr(self.server, "_wait_for_client", self._wait)
        monkeypatch.setattr(self.server, "_pipe_peer", self._peer)
        self.server._first_handle = self._create(first_instance=True)
        self.thread = threading.Thread(target=self.server._serve_pipe, name="fake-pipe-accept", daemon=True)

    def _create(self, *, first_instance: bool) -> int:
        handle, self._next_handle = self._next_handle, self._next_handle + 1
        self.events.append(("create", handle, first_instance))
        return handle

    def _wait(self, handle: int) -> bool:
        self.events.append(("wait", handle))
        if self._waits:
            return self._waits.pop(0)
        self.idle.set()
        self.server._stop.wait(DEADLINE)
        return False

    def _peer(self, handle: int) -> ipc.PeerInfo:
        peer = self._peers.get(handle, ipc.PeerInfo(pid=1000 + handle, identity=OWN[0], session=OWN[1]))
        if isinstance(peer, Exception):
            raise peer
        return peer

    def run_script(self) -> None:
        self.thread.start()
        assert self.idle.wait(DEADLINE), f"the accept loop stopped early: {self.events}"
        assert self.thread.is_alive()

    def stop(self) -> None:
        self.server._stop.set()
        self.thread.join(DEADLINE)
        assert not self.thread.is_alive()

    def closes(self, handle: int) -> int:
        return self.events.count(("close", handle))


@pytest.fixture
def pipe_loop(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, events: list[tuple[Any, ...]], windows: FakeWinapi
) -> Iterator[Callable[..., PipeLoop]]:
    loops: list[PipeLoop] = []

    def make(**kw: Any) -> PipeLoop:
        loop = PipeLoop(settings.state_dir, monkeypatch, events, **kw)
        loops.append(loop)
        return loop

    yield make
    for loop in loops:
        if loop.thread.is_alive():
            loop.stop()


# ----- the pipe name never lapses (review R3) ------------------------------------------------------------


def test_a_failed_instance_is_replaced_before_it_is_closed(pipe_loop: Callable[..., PipeLoop]) -> None:
    """ERROR_NO_DATA (a client came and left) on the only listening instance: closing it first would
    let the name lapse, and the replacement (created without FILE_FLAG_FIRST_PIPE_INSTANCE) could then
    join a pipe another process created in between."""
    loop = pipe_loop(waits=[False])
    loop.run_script()
    events = loop.events
    assert events.index(("create", 2, False)) < events.index(("close", 1))
    assert events[-1] == ("wait", 2)  # and the loop listens on the replacement
    loop.stop()
    assert loop.closes(1) == 1 and loop.closes(2) == 1
    assert loop.server.unavailable_reason == ""


def test_stop_during_a_wait_creates_no_replacement(pipe_loop: Callable[..., PipeLoop]) -> None:
    loop = pipe_loop(waits=[])
    loop.run_script()
    loop.stop()
    assert [e for e in loop.events if e[0] == "create"] == [("create", 1, True)]
    assert loop.closes(1) == 1


def test_the_next_instance_exists_before_a_connected_client_is_handed_off(pipe_loop: Callable[..., PipeLoop]) -> None:
    loop = pipe_loop(waits=[True])
    loop.run_script()
    assert loop.delivered.get(timeout=DEADLINE) == 1
    assert loop.events.index(("create", 2, False)) < loop.events.index(("deliver", 1))
    loop.stop()
    assert loop.closes(1) == 0  # the delivered connection owns handle 1 now


# ----- one bad connection never ends the endpoint (review R4) ------------------------------------------------


def test_an_owner_callback_that_raises_costs_one_connection_not_the_endpoint(
    pipe_loop: Callable[..., PipeLoop],
) -> None:
    delivered: list[int] = []

    def on_connection(conn: ipc.FrameConnection, _peer: ipc.PeerInfo) -> None:
        assert isinstance(conn, FakeConn)
        if not delivered:
            delivered.append(-conn.handle)
            raise RuntimeError("can't start new thread")
        delivered.append(conn.handle)

    loop = pipe_loop(waits=[True, True], on_connection=on_connection)
    loop.run_script()
    assert delivered == [-1, 2]  # the second client is served
    assert ("conn_close", 1) in loop.events  # the connection that could not be handed off is closed
    assert loop.closes(1) == 0  # ... by its wrapper only, never twice
    loop.stop()
    assert loop.server.unavailable_reason == ""


def test_a_client_that_fails_before_it_is_wrapped_is_closed_and_serving_goes_on(
    pipe_loop: Callable[..., PipeLoop],
) -> None:
    loop = pipe_loop(waits=[True, True], peers={1: OSError(6, "The handle is invalid")})
    loop.run_script()
    assert loop.closes(1) == 1  # not leaked: raw handles are not closed by garbage collection
    assert loop.delivered.get(timeout=DEADLINE) == 2
    loop.stop()


def test_a_foreign_client_is_closed_once_even_when_reporting_it_fails(pipe_loop: Callable[..., PipeLoop]) -> None:
    def on_mismatch(_peer: ipc.PeerInfo) -> None:
        raise RuntimeError("Event loop is closed")  # loop.call_soon_threadsafe while the agent shuts down

    stranger = ipc.PeerInfo(pid=4242, identity="S-1-5-21-2000", session="1")
    loop = pipe_loop(waits=[True, True], peers={1: stranger}, on_mismatch=on_mismatch)
    loop.run_script()
    assert loop.closes(1) == 1
    assert ("conn_close", 1) not in loop.events  # never handed to the owner
    assert loop.delivered.get(timeout=DEADLINE) == 2
    loop.stop()


def test_a_client_of_another_session_is_refused(pipe_loop: Callable[..., PipeLoop]) -> None:
    other_session = ipc.PeerInfo(pid=4242, identity=OWN[0], session="2")
    loop = pipe_loop(waits=[True], peers={1: other_session})
    loop.run_script()
    assert loop.mismatches == [other_session] and loop.closes(1) == 1
    assert loop.delivered.empty()
    loop.stop()


# ----- the client checks who serves the name (review R3) ----------------------------------------------------


@pytest.mark.parametrize(
    "server",
    [
        ipc.PeerInfo(pid=4242, identity="S-1-5-21-2000", session=OWN[1]),  # another user took the name first
        ipc.PeerInfo(pid=4242, identity=OWN[0], session="2"),  # our own account, another Windows session
        ipc.PeerInfo(pid=-1, identity="unknown", session="unknown"),  # Windows would not say who it is
    ],
)
def test_a_pipe_served_by_someone_else_is_refused(
    windows: FakeWinapi, monkeypatch: pytest.MonkeyPatch, events: list[tuple[Any, ...]], server: ipc.PeerInfo
) -> None:
    asked: list[tuple[int, str]] = []

    def identity(handle: int, end: str) -> ipc.PeerInfo:
        asked.append((handle, end))
        return server

    monkeypatch.setattr(ipc, "_pipe_end_identity", identity)
    with pytest.raises(ConnectionRefusedError, match="another user or session"):
        ipc._connect_pipe(r"\\.\pipe\DoMe.Agent.0123456789abcdef", 1.0, OWN)
    assert asked == [(77, "server")]
    assert events.count(("close", 77)) == 1  # closed before a single frame was written


def test_a_pipe_served_by_ourselves_is_used(
    windows: FakeWinapi, monkeypatch: pytest.MonkeyPatch, events: list[tuple[Any, ...]]
) -> None:
    monkeypatch.setattr(
        ipc, "_pipe_end_identity", lambda _h, _end: ipc.PeerInfo(pid=4242, identity=OWN[0], session=OWN[1])
    )
    conn = ipc._connect_pipe(r"\\.\pipe\DoMe.Agent.0123456789abcdef", 1.0, OWN)
    assert isinstance(conn, FakeConn) and conn.handle == 77
    assert ("close", 77) not in events


def test_connect_checks_the_server_against_our_own_identity(
    windows: FakeWinapi, monkeypatch: pytest.MonkeyPatch, events: list[tuple[Any, ...]], settings: Settings
) -> None:
    monkeypatch.setattr(ipc, "_own_identity", lambda: OWN)
    monkeypatch.setattr(
        ipc, "_pipe_end_identity", lambda _h, _end: ipc.PeerInfo(pid=9, identity="S-1-5-21-2000", session="1")
    )
    with pytest.raises(ConnectionRefusedError):
        ipc.connect(settings.state_dir, timeout=1.0, kind="control")
    assert ("create_file", ipc.pipe_name_for(*OWN, kind="control")) in events


# ----- the same hand-off rule on a Unix socket -----------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="the Unix-socket endpoint")
def test_unix_endpoint_keeps_serving_after_a_failed_hand_off(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ipc, "_HAND_OFF_RETRY_SECONDS", 0.0)
    accepted: queue.Queue[ipc.FrameConnection] = queue.Queue()
    failures: list[int] = []

    def on_connection(conn: ipc.FrameConnection, _peer: ipc.PeerInfo) -> None:
        if not failures:
            failures.append(1)
            raise RuntimeError("can't start new thread")
        accepted.put(conn)

    server = ipc.IpcServer(settings.state_dir, on_connection, lambda _peer: None)
    assert server.start()
    ends: list[ipc.FrameConnection] = []
    try:
        first = ipc.connect(settings.state_dir, timeout=DEADLINE)
        ends.append(first)
        eof: queue.Queue[bytes | None] = queue.Queue()
        threading.Thread(target=lambda: eof.put(first.read_frame()), daemon=True).start()
        assert eof.get(timeout=DEADLINE) is None  # the connection that failed is closed by the server
        second = ipc.connect(settings.state_dir, timeout=DEADLINE)
        ends.append(second)
        agent_end = accepted.get(timeout=DEADLINE)  # ... and the next one is served
        ends.append(agent_end)
        second.write_frame(encode_frame({"ok": True}))
        assert agent_end.read_frame() == b'{"ok":true}'
        assert server._thread is not None and server._thread.is_alive()
    finally:
        for end in ends:
            end.close()
        server.stop()
