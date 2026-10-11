"""The native host's exit path when the agent goes away (in-process; the real process is exercised in
test_processes.py). The host ends with ``os._exit``, which stops every thread wherever it is: no frame
may be partway through stdout at that moment, or the browser reports a native-host error instead of the
clean AGENT_DISCONNECTED + disconnect the extension reconnects on."""

from __future__ import annotations

import io
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from dome_agent.bridge import host as host_module
from dome_agent.bridge.framing import encode_frame, make_exact_reader, read_frame
from dome_agent.bridge.host import NativeHost

OTHER_FRAME: dict[str, Any] = {"type": "bridge_hello_ack", "protocol_version": "1.1", "agent_version": "0.0.0"}


class RecordingStdout(io.RawIOBase):
    def __init__(self) -> None:
        super().__init__()
        self.data = bytearray()
        self.flushes = 0

    def writable(self) -> bool:
        return True

    def write(self, b: Any) -> int:
        self.data += bytes(b)
        return len(b)

    def flush(self) -> None:
        self.flushes += 1


def _frames(data: bytes) -> list[dict[str, Any]]:
    stream = io.BytesIO(data)
    read_exact = make_exact_reader(stream.read)
    out: list[dict[str, Any]] = []
    while (raw := read_frame(read_exact)) is not None:
        out.append(json.loads(raw))
    return out


@pytest.fixture
def host(tmp_path: Path) -> tuple[NativeHost, RecordingStdout]:
    stdout = RecordingStdout()
    return NativeHost(tmp_path, io.BytesIO(), stdout), stdout  # type: ignore[arg-type]


def test_no_frame_can_start_between_agent_disconnected_and_the_exit(
    host: tuple[NativeHost, RecordingStdout], monkeypatch: pytest.MonkeyPatch
) -> None:
    native, stdout = host
    blocked: list[bool] = []
    exits: list[int] = []

    def fake_exit(code: int) -> None:
        # os._exit would end the process here. Another thread (the agent->extension relay, or the main
        # thread answering a bad frame) that starts a frame now would be cut off mid-frame.
        writer = threading.Thread(target=native._write_extension, args=(OTHER_FRAME,), daemon=True)
        writer.start()
        writer.join(0.3)
        blocked.append(writer.is_alive())
        exits.append(code)

    monkeypatch.setattr(host_module, "_exit_process", fake_exit)
    native._agent_gone()
    try:
        assert exits == [0]
        assert blocked == [True]  # the other writer waits on the stdout lock, which the exit path keeps
        assert [f["error"]["code"] for f in _frames(bytes(stdout.data))] == ["AGENT_DISCONNECTED"]
        assert stdout.flushes >= 1
        native._agent_gone()  # once only: a second caller neither writes nor exits again
        assert exits == [0]
    finally:
        native._stdout_lock.release()  # in a real host the process is gone; here, let the writer finish


def test_a_frame_already_being_written_completes_before_the_disconnect_frame(
    host: tuple[NativeHost, RecordingStdout], monkeypatch: pytest.MonkeyPatch
) -> None:
    native, stdout = host
    exits: list[int] = []
    monkeypatch.setattr(host_module, "_exit_process", exits.append)
    in_write, release = threading.Event(), threading.Event()
    real_write = stdout.write

    def slow_write(b: Any) -> int:  # a browser reading stdout slowly: the header is out, the body follows
        data = bytes(b)
        real_write(data[:4])
        in_write.set()
        release.wait(5)
        return real_write(data[4:]) + 4

    monkeypatch.setattr(stdout, "write", slow_write)
    writer = threading.Thread(target=native._write_extension, args=(OTHER_FRAME,), daemon=True)
    writer.start()
    assert in_write.wait(5)
    monkeypatch.setattr(stdout, "write", real_write)
    gone = threading.Thread(target=native._agent_gone, daemon=True)
    gone.start()
    time.sleep(0.2)
    assert exits == []  # the exit waits for the frame in flight
    release.set()
    gone.join(5)
    writer.join(5)
    assert exits == [0]
    assert [f["type"] for f in _frames(bytes(stdout.data))] == ["bridge_hello_ack", "bridge_error"]


def test_a_stuck_stdout_does_not_keep_the_host_alive(
    host: tuple[NativeHost, RecordingStdout], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A writer stuck on a browser that stopped reading must not keep the process (and the port) alive:
    after the timeout the host exits without the frame, rather than cutting into the stuck one."""
    native, stdout = host
    exits: list[int] = []
    monkeypatch.setattr(host_module, "_exit_process", exits.append)
    monkeypatch.setattr(host_module, "_EXIT_STDOUT_TIMEOUT", 0.2)
    native._stdout_lock.acquire()  # held by a writer that never returns
    try:
        gone = threading.Thread(target=native._agent_gone, daemon=True)
        started = time.monotonic()
        gone.start()
        gone.join(3)
        assert not gone.is_alive(), "the exit path waited forever on the stuck writer"
        assert exits == [0] and time.monotonic() - started < 2
        assert stdout.data == b""
    finally:
        native._stdout_lock.release()


def test_frames_are_whole_on_stdout(host: tuple[NativeHost, RecordingStdout]) -> None:
    native, stdout = host
    native._write_extension(OTHER_FRAME)
    assert bytes(stdout.data) == encode_frame(OTHER_FRAME)
