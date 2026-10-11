"""Full-duplex frame IPC: a thread blocked in ``read_frame`` must never hold up a write from another
thread, and ``close()`` must end a pending read with EOF.

On Windows (CI job on windows-latest) this is a real OVERLAPPED named pipe: with synchronous handles
the first write waited behind the read pending in the other thread forever, which was the bridge_hello
deadlock that kept the browser extension from ever connecting. Elsewhere it is a Unix socket. Every
blocking call runs in a daemon thread with a deadline, so a regression fails instead of hanging."""

from __future__ import annotations

import json
import queue
import sys
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from dome_agent.bridge import ipc
from dome_agent.bridge.framing import encode_frame
from dome_agent.settings import Settings

DEADLINE = 2.0
Outcome = queue.Queue[tuple[bool, Any]]


def _in_thread(fn: Callable[[], Any], name: str) -> Outcome:
    """Run ``fn`` in a daemon thread; its result (or exception) lands in the returned queue."""
    out: Outcome = queue.Queue(maxsize=1)

    def run() -> None:
        try:
            out.put((True, fn()))
        except BaseException as exc:  # noqa: BLE001 - handed to the test thread
            out.put((False, exc))

    threading.Thread(target=run, name=name, daemon=True).start()
    return out


def _result(outcome: Outcome, what: str, timeout: float = DEADLINE) -> Any:
    try:
        ok, value = outcome.get(timeout=timeout)
    except queue.Empty:
        pytest.fail(f"{what} did not finish within {timeout} s")
    if not ok:
        raise value
    return value


def _read_json(conn: ipc.FrameConnection, what: str) -> Any:
    raw = _result(_in_thread(conn.read_frame, "reader"), what)
    assert raw is not None, f"{what}: unexpected EOF"
    return json.loads(raw)


def _start(settings: Settings, accepted: queue.Queue[ipc.FrameConnection]) -> ipc.IpcServer:
    server = ipc.IpcServer(settings.state_dir, lambda conn, _peer: accepted.put(conn), lambda _peer: None)
    assert server.start(), server.unavailable_reason
    return server


@pytest.fixture
def pair(settings: Settings) -> Iterator[tuple[ipc.FrameConnection, ipc.FrameConnection]]:
    """(client end, agent end) of one connection."""
    accepted: queue.Queue[ipc.FrameConnection] = queue.Queue()
    server = _start(settings, accepted)
    ends: list[ipc.FrameConnection] = []
    try:
        ends.append(ipc.connect(settings.state_dir, timeout=5.0))
        ends.append(accepted.get(timeout=5.0))
        yield ends[0], ends[1]
    finally:
        for end in ends:
            end.close()
        server.stop()


@pytest.mark.parametrize("blocked_side", ["client", "agent"])
def test_write_is_not_held_up_by_a_pending_read(
    pair: tuple[ipc.FrameConnection, ipc.FrameConnection], blocked_side: str
) -> None:
    client, agent = pair
    blocked, peer = (client, agent) if blocked_side == "client" else (agent, client)
    pending = _in_thread(blocked.read_frame, "blocked-reader")
    time.sleep(0.2)  # the reader is parked inside the read now
    assert pending.empty()

    # the end with the pending read writes from another thread, and the peer receives it
    _result(_in_thread(lambda: blocked.write_frame(encode_frame({"from": blocked_side})), "writer"), "write_frame")
    assert _read_json(peer, "the peer's read") == {"from": blocked_side}

    # the other direction completes the read that was pending all along
    _result(_in_thread(lambda: peer.write_frame(encode_frame({"reply": 1})), "peer-writer"), "peer write_frame")
    raw = _result(pending, "the pending read")
    assert raw is not None and json.loads(raw) == {"reply": 1}


def test_concurrent_writers_never_interleave(pair: tuple[ipc.FrameConnection, ipc.FrameConnection]) -> None:
    client, agent = pair
    pending = _in_thread(client.read_frame, "blocked-reader")
    writers = [
        _in_thread(lambda i=i: client.write_frame(encode_frame({"i": i, "pad": "x" * 3000})), f"writer-{i}")
        for i in range(8)
    ]
    for w in writers:
        _result(w, "a concurrent write_frame")
    assert sorted(_read_json(agent, "the agent's read")["i"] for _ in writers) == list(range(8))
    agent.write_frame(encode_frame({"done": True}))
    assert _result(pending, "the pending read") is not None


@pytest.mark.parametrize("closing_side", ["client", "agent"])
def test_close_ends_a_pending_read_with_eof(
    pair: tuple[ipc.FrameConnection, ipc.FrameConnection], closing_side: str
) -> None:
    client, agent = pair
    closing, peer = (client, agent) if closing_side == "client" else (agent, client)
    pending = _in_thread(closing.read_frame, "blocked-reader")
    peer_pending = _in_thread(peer.read_frame, "peer-reader")
    time.sleep(0.2)

    _result(_in_thread(closing.close, "closer"), "close() while a read is pending")
    assert _result(pending, "the read pending on the closed end") is None  # EOF, not an error
    assert _result(peer_pending, "the peer's pending read") is None  # the peer sees EOF as well
    with pytest.raises(OSError):
        closing.write_frame(encode_frame({"late": True}))
    with pytest.raises(OSError):
        _result(_in_thread(lambda: peer.write_frame(encode_frame({"late": True}) * 40), "late-writer"), "late write")


def test_several_clients_connect_at_once(settings: Settings) -> None:
    """A named pipe has one listening instance at a time: clients that lose the race get ERROR_PIPE_BUSY
    and must wait for the next instance instead of failing."""
    accepted: queue.Queue[ipc.FrameConnection] = queue.Queue()
    server = _start(settings, accepted)
    ends: list[ipc.FrameConnection] = []
    try:
        attempts = [_in_thread(lambda: ipc.connect(settings.state_dir, timeout=5.0), f"client-{i}") for i in range(4)]
        ends.extend(_result(a, "connect", timeout=10.0) for a in attempts)
        agents = [accepted.get(timeout=5.0) for _ in attempts]
        ends.extend(agents)
        for i, end in enumerate(ends[:4]):
            end.write_frame(encode_frame({"client": i}))
        assert sorted(_read_json(a, "the agent's read")["client"] for a in agents) == [0, 1, 2, 3]
    finally:
        for end in ends:
            end.close()
        server.stop()


def test_stop_is_prompt_and_refuses_new_clients(settings: Settings) -> None:
    accepted: queue.Queue[ipc.FrameConnection] = queue.Queue()
    server = _start(settings, accepted)
    started = time.monotonic()
    server.stop()
    assert time.monotonic() - started < 1.5
    assert server._thread is not None and not server._thread.is_alive()  # noqa: SLF001
    with pytest.raises(OSError):  # ConnectionRefusedError / FileNotFoundError: nobody serves the name
        ipc.connect(settings.state_dir, timeout=0.5).close()


@pytest.mark.skipif(
    sys.platform != "win32", reason="FILE_FLAG_FIRST_PIPE_INSTANCE is a named-pipe rule; a Unix socket path is re-bound"
)
def test_pipe_name_held_elsewhere_is_reported_then_recovered(settings: Settings) -> None:
    """An old process still holding our pipe name must be reported (not a dead accept thread after a
    'listening' log line), and the endpoint must come up by itself once the name is free."""
    first = _start(settings, queue.Queue())
    second = ipc.IpcServer(settings.state_dir, lambda conn, _peer: conn.close(), lambda _peer: None)
    try:
        assert second.start() is False
        assert "held by another process" in second.unavailable_reason
        first.stop()
        deadline = time.monotonic() + 10
        while second.unavailable_reason and time.monotonic() < deadline:
            time.sleep(0.1)
        assert second.unavailable_reason == ""
        ipc.connect(settings.state_dir, timeout=5.0).close()  # the second server serves the name now
    finally:
        first.stop()
        second.stop()
