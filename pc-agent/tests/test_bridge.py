from __future__ import annotations

import asyncio
import struct
from typing import Any

import pytest
from dome_protocol import ProtocolError

from dome_agent.bridge.framing import MAX_FRAME_BYTES, decode_frame, encode_frame, make_exact_reader, read_frame
from dome_agent.bridge.manifest import allowed_origins, build_manifest
from dome_agent.bridge.server import BridgeServer
from dome_agent.settings import Settings
from dome_agent.testing.fake_extension import FakeExtension, FakeTab


def reader_for(data: bytes) -> Any:
    buf = bytearray(data)

    def read(n: int) -> bytes:
        out = bytes(buf[:n])
        del buf[:n]
        return out

    return make_exact_reader(read)


def test_encode_and_read_roundtrip() -> None:
    frame = {
        "type": "bridge_hello",
        "browser_instance_id": "bi_test0001",
        "browser": "chrome",
        "extension_version": "1.0.0",
        "protocol_versions": ["1.0"],
    }
    data = encode_frame(frame)
    assert struct.unpack("<I", data[:4])[0] == len(data) - 4
    body = read_frame(reader_for(data))
    assert body is not None and decode_frame(body, "extension_to_agent") == frame


def test_oversize_frame_rejected_both_ways() -> None:
    header = struct.pack("<I", MAX_FRAME_BYTES + 1)
    with pytest.raises(ProtocolError) as ei:
        read_frame(reader_for(header + b"x"))
    assert ei.value.code == "PAYLOAD_TOO_LARGE"
    big = {
        "type": "bridge_event",
        "event": "tabs_changed",
        "at": "t",
        "tabs": [
            {
                "browser_instance_id": "b",
                "tab_id": 1,
                "script_attached": True,
                "context": "watch",
                "ad_showing": False,
                "is_live": False,
                "in_playlist": False,
                "title": "x" * 200,
            }
        ]
        * 32,
    }
    big["extra"] = "y" * (MAX_FRAME_BYTES)
    with pytest.raises(ProtocolError) as ei:
        encode_frame(big)
    assert ei.value.code == "PAYLOAD_TOO_LARGE"


def test_truncated_and_empty_frames() -> None:
    assert read_frame(reader_for(b"")) is None
    with pytest.raises(ProtocolError):
        read_frame(reader_for(b"\x01\x00"))
    with pytest.raises(ProtocolError):
        read_frame(reader_for(struct.pack("<I", 10) + b"abc"))
    with pytest.raises(ProtocolError):
        read_frame(reader_for(struct.pack("<I", 0)))


@pytest.mark.parametrize(
    "frame",
    [
        {
            "type": "bridge_hello",
            "browser_instance_id": "short",
            "browser": "chrome",
            "extension_version": "1",
            "protocol_versions": ["1.0"],
        },
        {
            "type": "bridge_hello",
            "browser_instance_id": "bi_test0001",
            "browser": "safari",
            "extension_version": "1",
            "protocol_versions": ["1.0"],
        },
        {
            "type": "bridge_hello",
            "browser_instance_id": "bi_test0001",
            "browser": "chrome",
            "extension_version": "1",
            "protocol_versions": ["1.0"],
            "evil": 1,
        },
        {"type": "bridge_request", "request_id": "x", "op": "list_tabs", "args": {}},  # wrong direction
        {"type": "bridge_response", "request_id": "r", "ok": True, "result": {"bogus": 1}},
        {"type": "nope"},
    ],
)
def test_schema_rejection(frame: dict[str, Any]) -> None:
    with pytest.raises(ProtocolError):
        decode_frame(encode_frame(frame)[4:], "extension_to_agent")


def test_duplicate_keys_rejected() -> None:
    raw = b'{"type":"bridge_hello","type":"bridge_hello"}'
    with pytest.raises(ProtocolError) as ei:
        decode_frame(raw, "extension_to_agent")
    assert ei.value.code == "MALFORMED_MESSAGE"


def test_manifest_origins() -> None:
    with pytest.raises(ValueError):
        allowed_origins("")
    assert allowed_origins("a" * 32) == ["chrome-extension://" + "a" * 32 + "/"]
    with pytest.raises(ValueError):
        allowed_origins("not-an-id")
    m = build_manifest(__import__("pathlib").Path("/x/dome-native-host.exe"), allowed_origins("b" * 32))
    assert m["name"] == "com.dome.agent" and m["type"] == "stdio"


@pytest.fixture
async def bridge(settings: Settings):
    events: list[tuple[str, dict[str, Any]]] = []
    changes: list[int] = []
    server = BridgeServer(
        settings.state_dir, on_change=lambda: changes.append(1), on_security_event=lambda k, d: events.append((k, d))
    )
    await server.start()
    yield server, events, changes
    await server.stop()


async def connect(settings: Settings, **kw: Any) -> FakeExtension:
    ext = FakeExtension(settings.state_dir, **kw)
    await asyncio.to_thread(ext.connect)
    return ext


async def test_hello_tabs_and_request(settings: Settings, bridge: Any) -> None:
    server, _events, changes = bridge
    ext = await connect(settings)
    try:
        assert ext.hello_ack is not None and ext.hello_ack["protocol_version"] == "1.0"
        assert server.connected and server.instances()[0].browser == "chrome"
        tab = ext.add_tab(FakeTab(tab_id=4))
        ext.publish_tabs()
        for _ in range(100):
            if server.find_tab(ext.browser_instance_id, 4):
                break
            await asyncio.sleep(0.01)
        found = server.find_tab(ext.browser_instance_id, 4)
        assert found is not None and found["tab_token"] == tab.tab_token
        result = await server.request(
            ext.browser_instance_id,
            "set_paused",
            {"tab_id": 4, "tab_token": tab.tab_token, "paused": True},
            timeout_ms=2000,
        )
        assert result["tab"]["paused"] is True
        with pytest.raises(ProtocolError) as ei:
            await server.request(
                ext.browser_instance_id,
                "set_paused",
                {"tab_id": 4, "tab_token": "Z" * 22, "paused": True},
                timeout_ms=2000,
            )
        assert ei.value.code == "TARGET_CHANGED"
        assert changes
    finally:
        ext.close()
    for _ in range(100):
        if not server.connected:
            break
        await asyncio.sleep(0.02)
    assert not server.connected  # instance dropped when the host disconnects


async def test_incompatible_extension_is_refused(settings: Settings, bridge: Any) -> None:
    server, _events, _changes = bridge
    ext = FakeExtension(settings.state_dir, protocol_versions=("2.0",))
    with pytest.raises(TimeoutError):
        await asyncio.to_thread(ext.connect, 1.0)
    for _ in range(50):
        if ext.errors:
            break
        await asyncio.sleep(0.02)
    assert ext.errors and ext.errors[0]["error"]["code"] == "PROTOCOL_INCOMPATIBLE"
    assert ext.errors[0]["error"]["detail"]["supported"] == ["1.0"]
    assert not server.connected
    ext.close()


async def test_invalid_frames_get_bridge_error_and_do_not_crash(settings: Settings, bridge: Any) -> None:
    server, _events, _changes = bridge
    ext = await connect(settings)
    try:
        ext.send_raw({"type": "bridge_event", "event": "tabs_changed"})  # missing `at`
        for _ in range(100):
            if ext.errors:
                break
            await asyncio.sleep(0.02)
        assert ext.errors[0]["error"]["code"] == "MALFORMED_MESSAGE"
        assert server.connected
        ext.send_raw_bytes(struct.pack("<I", MAX_FRAME_BYTES + 5) + b"x")  # oversize header: connection ends
        assert await asyncio.to_thread(ext.wait_closed, 5)
    finally:
        ext.close()


async def test_request_timeout_is_extension_disconnected(settings: Settings, bridge: Any) -> None:
    server, _events, _changes = bridge
    ext = await connect(settings, op_delay=1.0)
    try:
        ext.add_tab(FakeTab(tab_id=1))
        with pytest.raises(ProtocolError) as ei:
            await server.request(ext.browser_instance_id, "list_tabs", {}, timeout_ms=200)
        assert ei.value.code == "EXTENSION_DISCONNECTED"
        with pytest.raises(ProtocolError):
            await server.request("bi_unknown01", "list_tabs", {}, timeout_ms=200)
    finally:
        ext.close()


async def test_second_hello_for_same_instance_replaces_connection(settings: Settings, bridge: Any) -> None:
    server, _events, _changes = bridge
    first = await connect(settings, browser_instance_id="bi_same00001")
    second = await connect(settings, browser_instance_id="bi_same00001")  # service worker restarted
    try:
        await asyncio.sleep(0.1)
        assert len(server.instances()) == 1
        assert await asyncio.to_thread(first.wait_closed, 5)
    finally:
        first.close()
        second.close()
