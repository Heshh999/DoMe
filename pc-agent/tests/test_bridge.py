from __future__ import annotations

import asyncio
import struct
from typing import Any

import pytest
from dome_protocol import ProtocolError

from dome_agent.bridge.framing import MAX_FRAME_BYTES, decode_frame, encode_frame, make_exact_reader, read_frame
from dome_agent.bridge.manifest import allowed_origins, build_manifest
from dome_agent.bridge.server import OP_SINCE, BridgeServer, negotiate_protocol_version, op_supported
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
        # FakeExtension lists what the shipped extension lists ("1.0", "1.1", "1.2"): the highest version
        # both sides speak is acked (a 1.0-only extension is the explicit case in the next tests)
        assert ext.hello_ack is not None and ext.hello_ack["protocol_version"] == "1.2"
        assert server.connected and server.instances()[0].browser == "chrome"
        assert server.instances()[0].protocol_version == "1.2"
        assert server.unavailable_reason == ""
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
    assert ext.errors[0]["error"]["detail"]["supported"] == ["1.0", "1.1", "1.2"]  # every MINOR the agent speaks
    assert not server.connected
    ext.close()


@pytest.mark.parametrize(
    ("peer", "agent", "expected"),
    [
        (["1.0"], ("1.0", "1.1"), "1.0"),
        (["1.0", "1.1"], ("1.0", "1.1"), "1.1"),
        (["1.1", "1.0"], ("1.0", "1.1"), "1.1"),
        (["1.0", "1.1", "1.2"], ("1.0", "1.1"), "1.1"),  # a newer extension talking to this agent
        (["1.0"], ("1.0",), "1.0"),
        (["2.0", "1.0"], ("1.0", "1.1"), "1.0"),
        (["2.0", "2.1"], ("1.0", "1.1"), None),
        (["1.2"], ("1.0", "1.1"), "1.1"),  # rules.bridge_negotiation: a 1.2 extension also accepts 1.1
        (["1.0", "1.1"], ("1.0", "1.1", "1.2"), "1.1"),  # a 1.1 extension talking to a 1.2 agent
        (["x", "1"], ("1.0",), None),
    ],
)
def test_negotiate_protocol_version(peer: list[str], agent: tuple[str, ...], expected: str | None) -> None:
    assert negotiate_protocol_version(peer, agent) == expected


@pytest.mark.parametrize(
    ("versions", "acked"),
    [
        (("1.0",), "1.0"),  # an extension the owner has not reloaded since the 1.0 build: never acked "1.1"
        (("1.0", "1.1"), "1.1"),
        (("1.0", "1.1", "1.2"), "1.2"),
        (("1.0", "1.1", "1.2", "1.3"), "1.2"),  # a newer extension talking to this agent
    ],
)
async def test_hello_ack_is_the_highest_common_version(
    settings: Settings, bridge: Any, versions: tuple[str, ...], acked: str
) -> None:
    server, _events, _changes = bridge
    ext = await connect(settings, protocol_versions=versions)
    try:
        assert ext.hello_ack is not None and ext.hello_ack["protocol_version"] == acked
        assert server.instances()[0].protocol_version == acked
    finally:
        ext.close()


async def test_fake_extension_refuses_an_ack_it_does_not_speak(
    settings: Settings, bridge: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug this guards against: the agent acked its own newest version ("1.1") and the shipped 1.0
    extension disconnected as incompatible. The fake extension must fail the same way, not accept it."""
    from dome_agent.bridge import server as server_module

    server, _events, _changes = bridge
    monkeypatch.setattr(server_module, "negotiate_protocol_version", lambda peer, ours=("1.1",): "1.1")
    ext = FakeExtension(settings.state_dir, protocol_versions=("1.0",))
    with pytest.raises(ProtocolError) as ei:
        await asyncio.to_thread(ext.connect, 5.0)
    assert ei.value.code == "PROTOCOL_INCOMPATIBLE"
    assert ext.hello_ack is None and ext.refused_ack is not None
    assert await asyncio.to_thread(ext.wait_closed, 5)
    for _ in range(100):
        if not server.connected:
            break
        await asyncio.sleep(0.02)
    assert not server.connected  # the refused connection is gone on the agent side too
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


async def test_request_timeout_non_idempotent_is_outcome_unknown(settings: Settings, bridge: Any) -> None:
    server, _events, _changes = bridge
    ext = await connect(settings, op_delay=1.0)
    try:
        ext.add_tab(FakeTab(tab_id=1))
        with pytest.raises(ProtocolError) as ei:
            await server.request(ext.browser_instance_id, "next", {"tab_id": 1}, timeout_ms=200, non_idempotent=True)
        assert ei.value.code == "OUTCOME_UNKNOWN" and ei.value.retryable is False
        # before anything was written the op cannot have run: still EXTENSION_DISCONNECTED
        with pytest.raises(ProtocolError) as ei2:
            await server.request("bi_unknown01", "next", {"tab_id": 1}, timeout_ms=200, non_idempotent=True)
        assert ei2.value.code == "EXTENSION_DISCONNECTED"
    finally:
        ext.close()


# ----- ops added in a later MINOR (version.json rules.bridge_negotiation) ------------------------------


def test_op_since_matches_the_contract() -> None:
    """OP_SINCE names only real bridge ops, and each op is as old as the action that uses it: an action an
    agent accepts (stamped >= its since) must not need an op an extension of that version lacks."""
    from dome_protocol import load_registry, load_schemas

    from dome_agent.actions.youtube import OPS

    bridge_schema = load_schemas().document("bridge")
    ops = set(bridge_schema["$defs"]["bridge_request"]["properties"]["op"]["enum"])
    assert set(OP_SINCE) <= ops
    assert set(OPS.values()) == ops  # every bridge op is reachable from exactly the YouTube actions
    registry = load_registry()
    for action, op in OPS.items():
        assert OP_SINCE.get(op, "1.0") == registry.min_version(action), action
    assert op_supported("search", "1.2") and op_supported("set_paused", "1.0")
    assert not op_supported("search", "1.1") and not op_supported("open_result", "1.0")


@pytest.mark.parametrize("versions", [("1.0",), ("1.0", "1.1")])
async def test_newer_ops_are_never_sent_to_an_older_extension(
    settings: Settings, bridge: Any, versions: tuple[str, ...]
) -> None:
    server, _events, _changes = bridge
    ext = await connect(settings, protocol_versions=versions)
    try:
        tab = ext.add_tab(FakeTab.results_page(5))
        for op, args in (
            ("search", {"query": "lofi beats"}),
            ("search", {"query": "lofi beats", "tab_id": 5, "tab_token": tab.tab_token}),
            ("open_result", {"tab_id": 5, "tab_token": tab.tab_token, "index": 1}),
        ):
            with pytest.raises(ProtocolError) as ei:
                await server.request(ext.browser_instance_id, op, args, timeout_ms=2000, non_idempotent=True)
            assert ei.value.code == "EXTENSION_UPDATE_REQUIRED" and ei.value.retryable is False
            assert ei.value.detail == {"negotiated": versions[-1], "required": "1.2"}
        # an op the extension speaks still works over the same connection
        result = await server.request(ext.browser_instance_id, "list_tabs", {}, timeout_ms=2000)
        assert [t["tab_id"] for t in result["tabs"]] == [5]
        await asyncio.sleep(0.1)
        assert [r["op"] for r in ext.requests] == ["list_tabs"]  # no frame was written for the refused ops
        assert all(f["type"] != "bridge_request" or f["op"] == "list_tabs" for f in ext.received)
    finally:
        ext.close()


async def test_a_1_2_extension_receives_search(settings: Settings, bridge: Any) -> None:
    server, _events, _changes = bridge
    ext = await connect(settings, protocol_versions=("1.0", "1.1", "1.2"))
    try:
        result = await server.request(ext.browser_instance_id, "search", {"query": "lofi"}, timeout_ms=2000)
        assert result["tab"]["context"] == "other" and len(result["results"]) == 5
        assert [r["op"] for r in ext.requests] == ["search"]
    finally:
        ext.close()


async def test_titles_of_pages_without_a_video_are_not_kept(settings: Settings, bridge: Any) -> None:
    """A results page's title is the search query (rules.search_queries): even an extension that still
    reports it (pre-1.2) does not get it into the agent's tab cache, state frames or results."""
    server, _events, _changes = bridge
    ext = await connect(settings)
    try:
        leaky = ext.add_tab(FakeTab(tab_id=1, context="other", video_id=None, title="my private query - YouTube"))
        ext.add_tab(FakeTab(tab_id=2, title="A video"))
        ext.publish_tabs()
        for _ in range(100):
            if server.find_tab(ext.browser_instance_id, 2):
                break
            await asyncio.sleep(0.01)
        cached = server.find_tab(ext.browser_instance_id, 1)
        assert cached is not None and "title" not in cached and cached["tab_token"] == leaky.tab_token
        assert server.find_tab(ext.browser_instance_id, 2)["title"] == "A video"  # type: ignore[index]
        ext.publish_player_state(1)
        await asyncio.sleep(0.1)
        assert "title" not in (server.find_tab(ext.browser_instance_id, 1) or {"title": "missing"})
        assert "my private query" not in repr(server.all_tabs())
        # a tab reported in a bridge_response is recorded the same way
        server.note_tab({**leaky.as_frame(ext.browser_instance_id), "tab_token": "N" * 22}, ext.browser_instance_id)
        cached = server.find_tab(ext.browser_instance_id, 1)
        assert cached is not None and cached["tab_token"] == "N" * 22 and "title" not in cached
        server.note_tab({**leaky.as_frame("bi_other0001"), "tab_id": 77}, ext.browser_instance_id)  # ignored
        assert server.find_tab(ext.browser_instance_id, 77) is None
    finally:
        ext.close()


async def test_an_answer_never_rewrites_another_browsers_tabs(settings: Settings, bridge: Any) -> None:
    """note_tab records a tab from the answer to a request sent to one browser: a tab naming another
    connected browser (a buggy or hostile connection) is ignored, as tabs_changed/player_state are."""
    server, _events, _changes = bridge
    ext = await connect(settings)
    other = await connect(settings, browser_instance_id="bi_fake0002")
    try:
        theirs = other.add_tab(FakeTab(tab_id=5, title="Their video"))
        other.publish_tabs()
        for _ in range(100):
            if server.find_tab(other.browser_instance_id, 5):
                break
            await asyncio.sleep(0.01)
        before = server.find_tab(other.browser_instance_id, 5)
        assert before is not None and before["tab_token"] == theirs.tab_token
        forged = {**theirs.as_frame(other.browser_instance_id), "tab_token": "F" * 22, "title": "Injected"}
        server.note_tab(forged, ext.browser_instance_id)  # the answer came from ext's connection
        assert server.find_tab(other.browser_instance_id, 5) == before
        assert server.find_tab(ext.browser_instance_id, 5) is None
        server.note_tab(forged, other.browser_instance_id)  # the same tab from its own browser is recorded
        assert server.find_tab(other.browser_instance_id, 5)["tab_token"] == "F" * 22  # type: ignore[index]
    finally:
        ext.close()
        other.close()
