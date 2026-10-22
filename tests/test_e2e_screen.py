"""Screen view (protocol 1.4; docs/design/screen-view.md §18 WP1.8) through the real relay and the real agent process.

The PC is the real `dome-agent` on the fake platform (screen_agent.py: pc-agent's FakeScreenUI plays the person at the
PC); it spawns the real worker process, which captures the fake frame source (synthetic frames), encodes H.264 with
the development-only libx264 of the PyPI PyAV wheel and answers with aiortc, STUN only. The phone is a Python aiortc
`RTCPeerConnection` (screen_phone.py) on this host, signing with its ControllerSim key: it pins the PC key at pairing
(`pc_key_mac`, keyed by the pairing code the relay never sees), signs its offer inside `screen.session_start`, accepts
only an answer signed by the pinned key, receives the picture peer to peer and taps through 1.4 input batches. The
relay is cloud-api under uvicorn against real PostgreSQL with the operator switch on (except where a test says off),
the in-memory fake TURN provider and a movable ledger clock (`FakeClock`: the relay charges wall time it measures
itself, so tests move it instead of waiting minutes).

Evidence tag: integration-tested (Linux, fake platform, fake frame source, loopback network). NOT verified: Windows
capture (ddagrab / WGC), h264_mf encoding, the tkinter bars and windows, Safari/iOS playback, NAT traversal and TURN
relaying (the fake TURN provider's URLs name no server, so media always takes the direct path here).
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
import sys
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from dome_protocol import (
    ProtocolError,
    dumps_compact,
    generate_pairing_code,
    generate_private_key,
    jwk_from_public_key,
    kid_from_jwk,
    loads_strict,
    sdp_fingerprints,
    sign_payload,
    verify_pc_key_mac,
)
from screen_phone import FrameLog, ScreenClient, Started, StunServer, Viewer, start_stun_server

from conftest import (
    REGISTRY,
    Browser,
    ControllerSim,
    Env,
    RealAgent,
    fake_turn,
    screen_clock,
    sql,
)

HERE = Path(__file__).resolve().parent
SCREEN_AGENT = [sys.executable, str(HERE / "screen_agent.py")]
# The phone asks for Screen view at pairing too: the PC defers it (never granted at pairing, rules.local_permission_ui)
PAIR_CAPS = ("status", "media", "volume", "pointer", "keyboard", "screen")
LEASE = float(REGISTRY.limits["screen_lease_seconds"])
CHUNK = int(REGISTRY.limits["screen_allowance_chunk_seconds"])
FREE_SECONDS = 600
STREAM_SIZE = (1280, 720)  # the fake 1920x1080 primary monitor at H.264 level 3.1 (design §5.3)
PLI_BUDGET = 0.5


# ----- fixtures -------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="session")
async def stun_server() -> AsyncIterator[StunServer]:
    server = await start_stun_server()
    yield server
    assert server.transport is not None
    server.transport.close()


@pytest.fixture
async def screen_on(screen: Any, stun_server: StunServer) -> Any:
    """The `screen` fixture (switch on, fake TURN, movable clock, sweeper paused) with the loopback STUN server as the
    relay's stun_urls: what the PC gets in relay.screen and the phone in screen_ice_servers."""
    screen.config = dataclasses.replace(screen.config, stun_urls=[stun_server.url])
    return screen


async def start_pc(env: Env, browser: Browser, *, lying: bool = False) -> RealAgent:
    pc = RealAgent(env, browser, command=SCREEN_AGENT, extra_env={"DOME_TEST_LYING_AGENT": "1"} if lying else None)
    await pc.link()
    await pc.start()
    return pc


@pytest.fixture
async def agent(env: Env, alice: Browser) -> AsyncIterator[RealAgent]:
    """This module's PC: the real agent with a UI the test answers for the person at the PC (screen_agent.py)."""
    pc = await start_pc(env, alice)
    try:
        yield pc
    finally:
        await pc.stop()
        pc.cleanup()


@dataclass
class Phone:
    ctrl: ControllerSim
    log: FrameLog
    client: ScreenClient
    pairing: dict[str, Any]

    @property
    def controller_id(self) -> str:
        return str(self.ctrl.controller_id)

    async def close(self) -> None:
        await self.client.close()
        await self.log.aclose()
        await self.ctrl.close()


@pytest.fixture
async def phones() -> AsyncIterator[list[Phone]]:
    made: list[Phone] = []
    yield made
    for phone in made:
        await phone.close()


async def pair_phone(env: Env, browser: Browser, pc: RealAgent, phones: list[Phone], name: str) -> Phone:
    """Pair through the PC's code, verify `pc_key_mac` with the code the phone still holds, pin the key, connect a 1.4
    socket and subscribe. The phone asked for `screen`; the PC did not grant it."""
    ctrl = ControllerSim(env, browser, name=name)
    final, code = await pc.pair(ctrl, PAIR_CAPS)
    assert "screen" not in final["granted_capabilities"] and "pointer" in final["granted_capabilities"], final
    pin = verified_pin(pc, ctrl, final, code)
    ack = await ctrl.connect()
    assert ack["controller_id"] == ctrl.controller_id
    log = FrameLog(ctrl)
    await ctrl.subscribe(pc.pc_id)
    await log.wait("pc_status", pc_id=pc.pc_id)
    phone = Phone(ctrl, log, ScreenClient(ctrl, log, pc.pc_id, pin), final)
    phones.append(phone)
    return phone


def verified_pin(pc: RealAgent, ctrl: ControllerSim, final: dict[str, Any], code: str) -> dict[str, str]:
    """rules.pc_key_pinning: the approved pairing carries the PC's identity key and the MAC keyed by the pairing code.
    The phone pins the key only if the MAC verifies; the relay, which never held the code, could not have made it."""
    from dome_agent.identity import Identity

    pc_key = final["pc_key"]
    jwk: dict[str, str] = pc_key["public_jwk"]
    pc_kid = kid_from_jwk(jwk)
    assert pc_kid == Identity(pc.state_dir).kid  # the PC's own identity key, not one the relay picked
    assert verify_pc_key_mac(code, final["pairing_id"], pc.pc_id, ctrl.kid, pc_kid, pc_key["mac"])
    # what a relay could forge without the code fails: another code, or the same MAC over a substitute key
    assert not verify_pc_key_mac(
        generate_pairing_code(), final["pairing_id"], pc.pc_id, ctrl.kid, pc_kid, pc_key["mac"]
    )
    other_kid = kid_from_jwk(jwk_from_public_key(generate_private_key().public_key()))
    assert not verify_pc_key_mac(code, final["pairing_id"], pc.pc_id, ctrl.kid, other_kid, pc_key["mac"])
    return jwk


async def allow_screen_on_the_pc(pc: RealAgent, phone: Phone) -> None:
    """The PC owner opens "Allow Screen view…" for this phone and presses Allow in DoMe's window; the widened grant
    reaches the relay (grant_update) and comes back in the snapshot."""
    opened = await pc.control("test_open_grant_window", controller_id=phone.controller_id)
    assert opened["window_opened"] is True
    window = (await pc.control("test_ui"))["grant_windows"][-1]
    assert window["controller_id"] == phone.controller_id and window["screen"] is False
    await pc.control("test_answer_grant", allow=True)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        grants = await phone.ctrl.browser.get(f"/v1/pcs/{pc.pc_id}/grants")
        mine = [g for g in grants["grants"] if g["controller_id"] == phone.controller_id]
        local = await pc.control("grant", controller_id=phone.controller_id, add=[])
        if mine and "screen" in mine[0]["capabilities"] and "screen" in local["effective_capabilities"]:
            return
        await asyncio.sleep(0.2)
    raise AssertionError("the grant from the DoMe window never reached the relay / came back in the snapshot")


async def screen_status(pc: RealAgent) -> dict[str, Any]:
    status: dict[str, Any] = (await pc.control("status"))["screen"]
    return status


async def wait_session_gone(pc: RealAgent, timeout: float = 10) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        status = await screen_status(pc)
        if status["session"] is None:
            return status
        if time.monotonic() > deadline:
            raise TimeoutError(f"the PC still has a Screen view session: {status['session']}")
        await asyncio.sleep(0.1)


async def stream(phone: Phone, *, takeover: bool = False, frames: int = 10) -> Started:
    """A pinned start whose picture arrives: the answer verified, DTLS connected, ``frames`` decoded frames."""
    started = await phone.client.start(takeover=takeover)
    assert started.result["state"] == "succeeded", started.result
    viewer = started.viewer
    await viewer.wait(lambda: viewer.pc.connectionState == "connected", 20, "DTLS connected")
    await viewer.wait(lambda: len(viewer.frames) >= frames, 20, f"{frames} frames")
    return started


async def keyframe_latency(viewer: Viewer, request: Any) -> float:
    """Seconds from ``request()`` to the first RTP packet carrying an IDR or SPS, measured on a stream that sent no
    keyframe for the second before (the encoder's own interval is 600 frames, so the keyframe is the answer)."""
    await viewer.wait(
        lambda: bool(viewer.keyframe_packets) and time.monotonic() - viewer.keyframe_packets[-1] >= 1.0, 10, "quiet"
    )
    t0 = time.monotonic()
    await request()
    await viewer.wait(lambda: viewer.keyframe_packets[-1] > t0, 3, "a keyframe")
    return next(t for t in viewer.keyframe_packets if t > t0) - t0


def reservation(env: Env, command_id: str) -> dict[str, Any]:
    rows = sql(
        env.database_url,
        "SELECT state, screen_session_id, forwarded_at, started_at, ended_at, end_reason, charged_seconds,"
        " outstanding_seconds, reserved_until, segment_started_at FROM screen_reservations WHERE command_id = %s",
        (uuid.UUID(command_id),),
    )
    assert len(rows) == 1, rows
    keys = (
        "state",
        "screen_session_id",
        "forwarded_at",
        "started_at",
        "ended_at",
        "end_reason",
        "charged_seconds",
        "outstanding_seconds",
        "reserved_until",
        "segment_started_at",
    )
    return dict(zip(keys, rows[0], strict=True))


async def settled(env: Env, command_id: str, timeout: float = 10) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        row = reservation(env, command_id)
        if row["state"] in ("settled", "swept") or time.monotonic() > deadline:
            return row
        await asyncio.sleep(0.1)


def window(env: Env, account_id: str) -> dict[str, Any]:
    rows = sql(
        env.database_url,
        "SELECT used_seconds, reserved_seconds, zero_charged_attempts, window_end FROM screen_view_windows"
        " WHERE account_id = %s ORDER BY window_start DESC",
        (uuid.UUID(account_id),),
    )
    assert rows, "no Screen view window for the account"
    return dict(zip(("used_seconds", "reserved_seconds", "zero_charged_attempts", "window_end"), rows[0], strict=True))


def round_up(seconds: float) -> int:
    return int(-(-seconds // 10) * 10) if seconds > 0 else 0


async def ended_frame(phone: Phone, sid: str, timeout: float = 15) -> dict[str, Any]:
    return await phone.log.wait("screen_session", timeout=timeout, screen_session_id=sid, event="ended")


def sdp_secrets(*sdps: str) -> set[str]:
    """What must never leave the two devices: ICE passwords, DTLS fingerprints, candidate addresses. (The 4-character
    ICE ufrags are left out: random 4-character strings turn up by chance in a binary store.)"""
    out: set[str] = set()
    for sdp in sdps:
        for line in sdp.split("\r\n"):
            if line.startswith("a=ice-pwd:"):
                out.add(line.split(":", 1)[1])
            elif line.startswith("a=fingerprint:"):
                out.add(line.split()[-1])
            elif line.startswith("a=candidate:"):
                parts = line.split()
                out.add(f"{parts[4]} {parts[5]}")  # "address port" as in the candidate line
                if not parts[4].startswith("127."):
                    out.add(parts[4])
        out.update(sdp_fingerprints(sdp))
    return out


# ----- the scenarios --------------------------------------------------------------------------------------------------
async def test_pinned_session_streams_answers_pli_maps_taps_renews_and_is_charged_on_the_relay_clock(
    env: Env, alice: Browser, agent: RealAgent, screen_on: Any, stun_server: StunServer, phones: list[Phone]
) -> None:
    """The whole beta path once: pairing pins the PC key, the PC owner allows Screen view in DoMe's window, the phone
    gets ICE servers and a TURN credential of its own, signs its offer into the start, verifies the PC-signed answer
    against the pin, receives the picture peer to peer, gets a keyframe within 500 ms of a PLI, taps and scrolls at
    the right pixels, keeps the session alive through an allowance ticket, stops it, and is charged on the relay's
    clock; afterwards nothing of the session is left in any log, file or table."""
    phone = await pair_phone(env, alice, agent, phones, "Alice's iPhone")

    # 1. Paired is not allowed: the relay refuses ICE servers until the PC owner allows Screen view in DoMe's window.
    refused = await phone.client.ice_servers()
    assert refused["type"] == "error" and refused["error"]["code"] == "GRANT_MISSING", refused
    await allow_screen_on_the_pc(agent, phone)

    # 2. ICE servers: STUN for everyone, a TURN credential minted for THIS phone only, the Free allowance.
    stun_before = stun_server.requests
    ice = await phone.client.ice_servers()
    assert ice["type"] == "screen_ice_servers" and ice["turn"] == "available", ice
    assert ice["allowance"] == {"unlimited": False, "remaining_seconds": FREE_SECONDS, "resets_at": None}
    turn_entries = [s for s in ice["ice_servers"] if "username" in s]
    assert len(turn_entries) == 1 and len(fake_turn(screen_on).minted) == 1

    # 3. The signed start (its ICE request mints a fresh credential, revoking the first: one outstanding per phone);
    #    the PC's answer arrives in its own frame and verifies against the PINNED key.
    started = await stream(phone)
    turn = fake_turn(screen_on)
    assert len(turn.minted) == 2 and turn.revoked == [turn.minted[0]["username"]], (turn.minted, turn.revoked)
    result = started.result
    assert result["origin"] == "agent", result
    session = result["result"]
    sid = started.session_id
    assert session["surfaces"] == [{"index": 0, "width": 1920, "height": 1080, "primary": True}], session
    assert (session["surface_index"], session["gen"], session["audio"], session["consent"]) == (
        0,
        1,
        False,
        "not_required",
    )
    assert session["lease_seconds"] == int(LEASE) and session["encoder"] == "h264"
    assert "offer_sdp" not in dumps_compact(result) and "a=" not in dumps_compact(result)  # no SDP in a result
    assert started.answer is not None and started.answer_envelope is not None
    assert started.answer_envelope["kid"] == kid_from_jwk(phone.client.pinned_jwk or {})
    answer_sdp = started.answer.answer_sdp
    assert "a=sendonly" in answer_sdp and "a=inactive" in answer_sdp  # video send-only, PC sound is Phase 2
    # both peers gathered through the relay's STUN url: the phone from screen_ice_servers, the PC from relay.screen
    assert stun_server.requests >= stun_before + 2
    # the "Screen view on" bar is up on the one monitor (that it comes up BEFORE the worker is pc-agent's unit-tested
    # order); the PC's status shows the session
    ui = await agent.control("test_ui")
    assert ui["bars_shown"] is True and ui["bar_monitors"] == [1], ui
    status = await screen_status(agent)
    assert status["session"]["connected"] is True and status["session"]["path"] == "direct", status
    started_frame = await phone.log.wait("screen_session", timeout=10, screen_session_id=sid, event="started")
    assert started_frame["path"] == "direct" and started_frame["controller_id"] == phone.controller_id

    # 4. The picture: decoded frames at the level-3.1 size; the data channel's first geometry and state.
    viewer = started.viewer
    assert {(f.width, f.height) for f in viewer.frames} == {STREAM_SIZE}
    await viewer.wait(lambda: bool(viewer.dc_of("geometry")) and bool(viewer.dc_of("state")), 10, "geometry + state")
    geometry = viewer.dc_of("geometry")[0]
    assert (geometry["gen"], geometry["width"], geometry["height"], geometry["surface_index"]) == (1, *STREAM_SIZE, 0)
    assert geometry["reserved"] and geometry["reserved"][0]["y"] == 0  # the "Screen view on" bar's band
    assert viewer.dc_of("state")[0] == {"type": "state", "state": "sending", "encoder": "h264"}

    # 5. PLI → keyframe within 500 ms, three times, each from a stream that had sent no keyframe for a second; the
    #    phone's own `keyframe` request on the data channel is answered the same way.
    for _ in range(3):
        latency = await keyframe_latency(viewer, viewer.send_pli)
        assert latency < PLI_BUDGET, f"keyframe {latency * 1000:.0f} ms after the PLI"

    async def ask() -> None:
        viewer.send({"type": "keyframe"})

    latency = await keyframe_latency(viewer, ask)
    assert latency < PLI_BUDGET, f"keyframe {latency * 1000:.0f} ms after the data-channel request"

    # 6. Taps: an input session, then pointer_move_to at the generation the phone saw, mapped to physical pixels.
    res = await phone.log.command(agent.pc_id, "input.session_start", {})
    assert res["state"] == "succeeded" and res["result"]["pointer"] is True, res
    isid = res["result"]["input_session_id"]
    tap = [
        {"type": "pointer_move_to", "x": 32768, "y": 49152, "gen": 1},
        {"type": "pointer_button", "button": "left", "action": "click"},
    ]
    batch = await phone.ctrl.input_batch(agent.pc_id, isid, 1, tap)
    assert loads_strict(batch["payload"])["protocol_version"] == "1.4"
    ack = await phone.log.wait("input_ack", timeout=10, input_session_id=isid, where=lambda f: f["last_seq"] >= 1)
    assert ack["accepted_events"] == 2 and ack["dropped_events"] == 0, ack
    from dome_agent.platform.protocol import Monitor
    from dome_agent.screen.session import map_to_pixels

    expected = map_to_pixels(Monitor(0, 0, 0, 1920, 1080, True), 32768, 49152)
    injected = await agent.control("test_input")
    assert tuple(injected["cursor"]) == expected == (960, 809), injected
    assert injected["injected"][-2:] == [["move_to", "960", "809"], ["button", "left", "click"]]
    # two-finger scroll: smooth wheel units
    await phone.ctrl.input_batch(agent.pc_id, isid, 2, [{"type": "pointer_wheel", "dx": 0, "dy": -240}])
    await phone.log.wait("input_ack", timeout=10, input_session_id=isid, where=lambda f: f["last_seq"] >= 2)
    assert (await agent.control("test_input"))["injected"][-1] == ["wheel", "0", "-240"]
    # a tap aimed at an older picture is dropped whole (never mapped onto a rectangle that may have moved)
    await phone.ctrl.input_batch(agent.pc_id, isid, 3, [{**tap[0], "gen": 2}, tap[1]])
    err = await phone.log.wait("error", timeout=10, where=lambda f: f["error"]["code"] == "SCREEN_GEOMETRY_CHANGED")
    assert err["ref_pc_id"] == agent.pc_id and err.get("ref_input_session_id") == isid, err
    # a tap on DoMe's own "Screen view on" bar moves the pointer but never presses there
    await phone.ctrl.input_batch(
        agent.pc_id, isid, 4, [{"type": "pointer_move_to", "x": 32768, "y": 0, "gen": 1}, tap[1]]
    )
    err = await phone.log.wait("error", timeout=10, where=lambda f: f["error"]["code"] == "INPUT_TARGET_RESERVED")
    injected = await agent.control("test_input")
    assert injected["cursor"] == [960, 0] and injected["injected"][-1] == ["move_to", "960", "0"], injected

    # 7. Allowance tickets: about 20 s before the first chunk runs out the PC asks, the relay grants the next chunk on
    #    its own clock and the PC passes the news to the phone on the data channel (heartbeats kept the lease alive).
    await viewer.wait(lambda: bool(viewer.dc_of("allowance")), CHUNK, "an allowance ticket")
    assert viewer.dc_of("allowance")[0] == {
        "type": "allowance",
        "unlimited": False,
        "remaining_seconds": None,
        "resets_at": None,
    }
    row = reservation(env, started.command_id)
    assert row["state"] == "live" and row["screen_session_id"] == sid and row["outstanding_seconds"] == 2 * CHUNK, row
    assert viewer.heartbeats_sent >= 10 and (await screen_status(agent))["session"]["state"] == "live"

    # 8. The relay's clock runs 30 s ahead of the PC's view of it; the phone presses Stop (the signed
    #    screen.session_stop; `bye` on the data channel is test_bye_on_the_data_channel_ends_the_session); the relay
    #    charges what IT measured from the forward to the `ended` frame, whatever the PC reports.
    screen_clock(screen_on).advance(30)
    res = await phone.log.command(agent.pc_id, "screen.session_stop", {"screen_session_id": sid})
    assert res["state"] == "succeeded" and res["result"]["stopped"] is True, res
    ended = await ended_frame(phone, sid)
    assert ended["reason"] == "stopped" and abs(ended["live_seconds"] - res["result"]["live_seconds"]) <= 1, ended
    row = await settled(env, started.command_id)
    measured = (row["ended_at"] - row["forwarded_at"]).total_seconds()
    assert row["end_reason"] == "stopped" and row["charged_seconds"] == round_up(measured), row
    assert row["charged_seconds"] >= ended["live_seconds"] + 30, (row, ended)
    used = window(env, alice.account_id)
    assert used["used_seconds"] == row["charged_seconds"] and used["reserved_seconds"] == 0, used

    # 9. The end on the PC, in order: no session, the bar gone, a history row; the phone's credential revoked.
    status = await wait_session_gone(agent)
    assert status["history"][0]["reason"] == "stopped", status["history"]
    ui = await agent.control("test_ui")
    assert ui["bars_shown"] is False and ui["bars_hidden"] >= 1, ui
    assert turn.revoked == [m["username"] for m in turn.minted], (turn.minted, turn.revoked)
    frames_at_end = len(viewer.frames)
    await asyncio.sleep(1.5)
    assert len(viewer.frames) <= frames_at_end + 2, "the picture kept coming after the session ended"

    # 10. Nothing recorded, nothing leaked: no SDP secret, candidate address, loopback address:port or TURN credential
    #     in any file the agent wrote (logs at DEBUG, the store, the diagnostics bundle, crash logs), in the relay's
    #     database, or in what the relay told the phone outside the answer.
    await agent.control("diagnostics")
    secrets = sdp_secrets(started.offer_sdp, answer_sdp)
    for frame in phone.log.frames:  # only the PC-signed answer carries SDP to the phone
        if frame["type"] != "screen_answer":
            text = dumps_compact(frame)
            assert not any(secret in text for secret in secrets), frame["type"]
    secrets.add(turn_entries[0]["credential"])  # the phone's own credential: in screen_ice_servers, nowhere else
    assert_nothing_leaked(agent, env, secrets)


def assert_nothing_leaked(pc: RealAgent, env: Env, secrets: set[str]) -> None:
    """Every file under the agent's state directory (store, journal, recovery file, logs, the diagnostics bundle, any
    crash log) plus its stderr, and every row of every relay table. The loopback sentinel applies to the logs and the
    bundle: identity.json legitimately names the API origin, which is loopback in these tests."""
    secrets = {s for s in secrets if s}
    files = [pc.log_path, *(p for p in pc.state_dir.rglob("*") if p.is_file())]
    assert any(p.name == "agent.log" for p in files) and any(p.parent.name == "diagnostics" for p in files)
    loopback = re.compile(rb"127\.0\.0\.1:\d+|\[::1\]|(?<![\w:])::1(?![\w:])")
    for path in files:
        data = path.read_bytes()
        for secret in secrets:
            assert secret.encode() not in data, f"{secret!r} found in {path}"
        assert b"a=candidate" not in data and b"a=ice-pwd" not in data and b"a=fingerprint" not in data, path
        if path.suffix == ".log" or path.parent.name == "diagnostics":
            found = loopback.search(data)
            assert found is None, f"{found.group(0)!r} in {path}" if found else ""
    tables = [r[0] for r in sql(env.database_url, "SELECT tablename FROM pg_tables WHERE schemaname = 'public'")]
    for table in tables:
        dump = "\n".join(str(r[0]) for r in sql(env.database_url, f'SELECT t::text FROM "{table}" t'))  # noqa: S608
        for secret in secrets:
            assert secret not in dump, f"{secret!r} found in table {table}"
        assert "a=candidate" not in dump and "a=ice-pwd" not in dump, table


# ----- the end paths --------------------------------------------------------------------------------------------------
# An end the worker reports (`bye` on the data channel, `disconnected`, a worker `error`) arrives on the supervisor's
# pipe-reader task; the agent ends the session on a task of its own (pc-agent review fix, 2026-10-22), so these tests
# assert the whole end: the `ended` frame, the settled reservation, the history row and the bars closed.


async def ended_with_history(phone: Phone, pc: RealAgent, sid: str, history_before: int) -> dict[str, Any]:
    ended = await ended_frame(phone, sid, timeout=20)
    status = await screen_status(pc)
    assert status["session"] is None and len(status["history"]) == history_before + 1, status
    assert (await pc.control("test_ui"))["bars_shown"] is False
    return ended


async def test_bye_on_the_data_channel_ends_the_session(
    env: Env, alice: Browser, agent: RealAgent, screen_on: Any, phones: list[Phone]
) -> None:
    """rules.screen_sessions (6): `stopped` is the phone's `bye` on the data channel or its signed stop, whichever
    first. The phone leaving the page sends `bye` before the signed stop."""
    phone = await pair_phone(env, alice, agent, phones, "Alice's iPhone")
    await allow_screen_on_the_pc(agent, phone)
    started = await stream(phone)
    history_before = len((await screen_status(agent))["history"])
    started.viewer.send({"type": "bye"})
    ended = await ended_with_history(phone, agent, started.session_id, history_before)
    assert ended["reason"] == "stopped", ended
    row = await settled(env, started.command_id)
    assert row["end_reason"] == "stopped", row
    assert (await agent.control("test_ui"))["bars_shown"] is False


async def test_the_viewer_lease_ends_a_session_whose_phone_went_quiet(
    env: Env, alice: Browser, agent: RealAgent, screen_on: Any, phones: list[Phone]
) -> None:
    """rules.screen_sessions (3): heartbeats every 2 s keep the 8 s viewer lease; a phone that stops sending them (app
    killed, network gone, phone locked) loses the picture within the lease, with no message from anyone."""
    phone = await pair_phone(env, alice, agent, phones, "Alice's iPhone")
    await allow_screen_on_the_pc(agent, phone)
    started = await stream(phone)
    viewer = started.viewer
    # heartbeats keep it alive past one lease
    await asyncio.sleep(LEASE + 2)
    assert (await screen_status(agent))["session"]["state"] == "live" and viewer.heartbeats_sent >= 4
    viewer.heartbeat_enabled = False
    await asyncio.sleep(0.05)
    last_hb = viewer.last_heartbeat_at
    ended = await ended_frame(phone, started.session_id, timeout=LEASE + 6)
    gone_after = time.monotonic() - last_hb
    assert ended["reason"] == "viewer_gone", ended
    # the lease runs out 8 s after the last heartbeat; `ended` follows the end order (the worker is stopped first,
    # within its 2 s grace), and the last frame reached the phone before that
    assert LEASE - 1 <= gone_after <= LEASE + 3, f"ended {gone_after:.1f} s after the last heartbeat"
    last_frame_after = viewer.frames[-1].at - last_hb
    assert last_frame_after <= LEASE + 2.5, f"the picture kept coming {last_frame_after:.1f} s after the last heartbeat"
    row = await settled(env, started.command_id)
    assert row["end_reason"] == "viewer_gone", row
    assert fake_turn(screen_on).revoked[-1] == fake_turn(screen_on).minted[-1]["username"]
    await wait_session_gone(agent)
    assert (await agent.control("test_ui"))["bars_shown"] is False


async def test_a_second_phone_is_refused_then_takes_over(
    env: Env, alice: Browser, agent: RealAgent, screen_on: Any, phones: list[Phone]
) -> None:
    """rules.screen_sessions (1): one session per PC; another phone gets SCREEN_SESSION_OWNED unless it asks to take
    over, and then the first session ends `takeover`. Subscribers that speak 1.4 see every lifecycle frame; the
    PC-signed answer reaches only the phone that asked."""
    phone = await pair_phone(env, alice, agent, phones, "Alice's iPhone")
    tablet = await pair_phone(env, alice, agent, phones, "Alice's iPad")
    await allow_screen_on_the_pc(agent, phone)
    await allow_screen_on_the_pc(agent, tablet)
    first = await stream(phone)
    seen = await tablet.log.wait("screen_session", timeout=10, screen_session_id=first.session_id, event="started")
    assert seen["controller_id"] == phone.controller_id

    refused = await tablet.client.start()
    assert refused.result["state"] == "failed" and refused.result["error"]["code"] == "SCREEN_SESSION_OWNED", refused
    assert refused.result["origin"] == "agent"
    turn = fake_turn(screen_on)
    tablet_credential = turn.minted[-1]["username"]
    await asyncio.sleep(0.3)
    assert tablet_credential in turn.revoked  # every start refusal revokes that phone's credential
    assert (await screen_status(agent))["session"]["controller_id"] == phone.controller_id
    frames_before = len(first.viewer.frames)
    await first.viewer.wait(lambda: len(first.viewer.frames) > frames_before + 5, 10, "the first phone still watching")

    second = await stream(tablet, takeover=True)
    ended = await ended_frame(phone, first.session_id)
    assert ended["reason"] == "takeover", ended
    assert (await screen_status(agent))["session"]["controller_id"] == tablet.controller_id
    assert not phone.log.unread("screen_answer")  # the tablet's answer never reached the other phone
    row = await settled(env, first.command_id)
    assert row["end_reason"] == "takeover" and row["state"] == "settled", row
    first_credential = turn.minted[-3]["username"]  # phone's, the tablet's refused one, the tablet's current one
    assert first_credential in turn.revoked and turn.minted[-1]["username"] not in turn.revoked
    stopped_at = len(first.viewer.frames)
    await asyncio.sleep(1.5)
    assert len(first.viewer.frames) <= stopped_at + 2, "the first phone kept getting the picture"
    assert second.viewer.frames_since(time.monotonic() - 1.0) > 0
    res = await tablet.log.command(agent.pc_id, "screen.session_stop", {"screen_session_id": second.session_id})
    assert res["state"] == "succeeded" and res["result"]["stopped"] is True, res


async def test_signing_the_phone_out_ends_its_stream_within_one_ticket(
    env: Env, alice: Browser, agent: RealAgent, screen_on: Any, phones: list[Phone]
) -> None:
    """Logout (and account-session revocation) closes the phone's sockets; the relay answers the PC's ticket with
    `final, controller_offline` at once, so the picture ends within one 60 s chunk even though the media path between
    the two devices is still up (rules.screen_sessions (4); SECURITY.md T4 amendment)."""
    browser = Browser(env)
    await browser.login(alice.email)
    assert browser.account_id == alice.account_id  # the same account, a second web session (the phone's)
    try:
        phone = await pair_phone(env, browser, agent, phones, "Alice's iPhone")
        watcher = await pair_phone(env, alice, agent, phones, "Alice's iPad")
        await allow_screen_on_the_pc(agent, phone)
        started = await stream(phone)
        t0 = time.monotonic()
        await browser.request("POST", "/v1/auth/logout", expect=204)
        ended = await ended_frame(watcher, started.session_id, timeout=CHUNK)
        assert ended["reason"] == "controller_offline", ended
        assert time.monotonic() - t0 < 10, "the stream outlived the sign-out by more than a few seconds"
        await phone.log.wait("revoked", timeout=5)
        assert phone.log.closed
        row = await settled(env, started.command_id)
        assert row["end_reason"] == "controller_offline", row
        turn = fake_turn(screen_on)
        assert turn.minted[-1]["username"] in turn.revoked
        frames = len(started.viewer.frames)
        await asyncio.sleep(1.5)
        assert len(started.viewer.frames) <= frames + 2
        assert (await wait_session_gone(agent))["history"][0]["reason"] == "controller_offline"
    finally:
        await browser.aclose()


async def test_revocation_and_disable_remote_control_end_the_stream(
    env: Env, alice: Browser, agent: RealAgent, screen_on: Any, phones: list[Phone]
) -> None:
    """Revoking a phone on the account ends its stream (the relay's `controller_offline` ticket or the PC's snapshot,
    whichever lands first); "Disable remote control" on the PC ends any stream `remote_disabled` and refuses new
    ones."""
    phone = await pair_phone(env, alice, agent, phones, "Alice's iPhone")
    tablet = await pair_phone(env, alice, agent, phones, "Alice's iPad")
    await allow_screen_on_the_pc(agent, phone)
    await allow_screen_on_the_pc(agent, tablet)
    started = await stream(phone)
    t0 = time.monotonic()
    await alice.delete(f"/v1/controllers/{phone.controller_id}")
    ended = await ended_frame(tablet, started.session_id, timeout=CHUNK)
    assert ended["reason"] in ("controller_offline", "controller_revoked"), ended
    assert time.monotonic() - t0 < 10
    row = await settled(env, started.command_id)
    assert row["end_reason"] == ended["reason"], row
    await wait_session_gone(agent)

    second = await stream(tablet)
    await agent.control("disable")
    ended = await ended_frame(tablet, second.session_id, timeout=10)
    assert ended["reason"] == "remote_disabled", ended
    cid = await tablet.ctrl.command(agent.pc_id, "screen.session_start", start_params(), None, lifetime=60)
    res = await tablet.log.wait("result", timeout=20, command_id=cid)
    assert res["state"] == "failed" and res["error"]["code"] == "PC_REMOTE_DISABLED", res
    await agent.control("enable")


async def test_stop_on_the_pc_starts_a_cooldown_that_asks_the_pc_for_every_phone(
    env: Env, alice: Browser, agent: RealAgent, screen_on: Any, phones: list[Phone]
) -> None:
    """rules.screen_sessions (5): Stop pressed at the PC ends the session `stopped_on_pc` and starts a PC-wide
    cooldown: the next start from ANY phone opens a consent window at the PC. Deny reaches the phone as
    SCREEN_CONSENT_DECLINED (relay-recorded, credential revoked); right after it, every phone is refused without a
    window (the back-off, and at most one window a minute)."""
    phone = await pair_phone(env, alice, agent, phones, "Alice's iPhone")
    tablet = await pair_phone(env, alice, agent, phones, "Alice's iPad")
    await allow_screen_on_the_pc(agent, phone)
    await allow_screen_on_the_pc(agent, tablet)
    started = await stream(phone)
    assert (await agent.control("screen_stop"))["stopped"] is True  # the tray's / CLI's "Stop Screen view"
    ended = await ended_frame(phone, started.session_id)
    assert ended["reason"] == "stopped_on_pc", ended
    assert (await screen_status(agent))["consent"]["cooldown_until"] is not None

    attempt = asyncio.create_task(tablet.client.start())
    deadline = time.monotonic() + 15
    while not (await agent.control("test_ui"))["consent_windows"]:
        assert time.monotonic() < deadline and not attempt.done(), "no consent window opened at the PC"
        await asyncio.sleep(0.1)
    window_shown = (await agent.control("test_ui"))["consent_windows"][-1]
    assert window_shown == {"controller_id": tablet.controller_id, "cause": "cooldown"}, window_shown
    await agent.control("test_answer_consent", action="deny")
    declined = await attempt
    assert declined.result["state"] == "failed", declined.result
    assert declined.result["error"]["code"] == "SCREEN_CONSENT_DECLINED", declined.result
    turn = fake_turn(screen_on)
    await asyncio.sleep(0.3)
    assert turn.minted[-1]["username"] in turn.revoked
    events = sql(
        env.database_url,
        "SELECT detail FROM security_events WHERE account_id = %s AND kind = 'screen_consent_declined'",
        (uuid.UUID(alice.account_id),),
    )
    assert [e[0]["reason"] for e in events] == ["denied"], events

    again = await phone.client.start()
    assert again.result["state"] == "failed", again.result
    assert again.result["error"]["code"] == "SCREEN_CONSENT_DECLINED", again.result
    assert len((await agent.control("test_ui"))["consent_windows"]) == 1  # refused without a window


async def test_a_missing_bar_pauses_the_picture_until_it_is_back(
    env: Env, alice: Browser, agent: RealAgent, screen_on: Any, phones: list[Phone]
) -> None:
    """rules.screen_sessions (2), fail-closed: while a "Screen view on" bar is missing (a monitor change, a window
    that vanished) capture pauses and the phone is told why; once every bar is back the picture resumes."""
    phone = await pair_phone(env, alice, agent, phones, "Alice's iPhone")
    await allow_screen_on_the_pc(agent, phone)
    started = await stream(phone)
    viewer = started.viewer
    await agent.control("test_bars_present", present=False)
    paused = await phone.log.wait("screen_session", timeout=10, screen_session_id=started.session_id, event="paused")
    assert paused["reason"] == "indicator", paused
    await viewer.wait(
        lambda: {"type": "state", "state": "paused", "reason": "indicator"} in viewer.dc_messages, 5, "paused state"
    )
    await asyncio.sleep(1.0)
    still = len(viewer.frames)
    await asyncio.sleep(1.5)
    assert len(viewer.frames) <= still + 1, "frames kept coming while a bar was missing"
    await agent.control("test_bars_present", present=True)
    resumed = await phone.log.wait("screen_session", timeout=10, screen_session_id=started.session_id, event="resumed")
    assert resumed["reason"] == "indicator", resumed
    await viewer.wait(lambda: len(viewer.frames) > still + 5, 10, "the picture again")
    assert (await screen_status(agent))["session"]["paused_reason"] is None


async def test_a_pc_without_its_bar_never_starts_the_worker(
    env: Env, alice: Browser, screen_on: Any, phones: list[Phone]
) -> None:
    """The plain headless agent (`dome-agent run --headless`, no wrapper) has no window, so no "Screen view on" bar
    can exist: even with the grant (here through the fake platform's local shortcut) every start is refused
    SCREEN_CAPTURE_UNAVAILABLE{indicator}, nothing is captured and the relay forgives the failed start."""
    pc = RealAgent(env, alice)
    await pc.link()
    await pc.start()
    try:
        phone = await pair_phone(env, alice, pc, phones, "Alice's iPhone")
        res = await pc.control("grant", controller_id=phone.controller_id, add=["screen"])
        assert "screen" in res["capabilities"]
        deadline = time.monotonic() + 15
        while True:
            grants = await alice.get(f"/v1/pcs/{pc.pc_id}/grants")
            if any(
                g["controller_id"] == phone.controller_id and "screen" in g["capabilities"] for g in grants["grants"]
            ):
                break
            assert time.monotonic() < deadline, "the grant never reached the relay"
            await asyncio.sleep(0.2)
        started = await phone.client.start()
        assert started.result["state"] == "failed" and started.result["origin"] == "agent", started.result
        error = started.result["error"]
        assert error["code"] == "SCREEN_CAPTURE_UNAVAILABLE" and error["detail"] == {"detail": "indicator"}, error
        assert (await screen_status(pc))["sessions_started"] == 0
        row = await settled(env, started.command_id)
        assert row["charged_seconds"] == 0 and row["end_reason"] == "start_failed", row
        assert not phone.log.unread("screen_answer")
    finally:
        await pc.stop()
        pc.cleanup()


def start_params(offer_sdp: str | None = None) -> dict[str, Any]:
    from conftest import harness

    return {"offer_sdp": offer_sdp or harness.OFFER_SDP, "surface": "primary", "audio": True, "takeover": False}


# ----- the ledger -----------------------------------------------------------------------------------------------------
async def test_free_minutes_run_out_on_the_relay_clock(
    env: Env, alice: Browser, agent: RealAgent, screen_on: Any, phones: list[Phone]
) -> None:
    """rules.screen_metering: 600 s per 5-hour window, reserved in chunks and settled on the relay's clock. With 30 s
    left, the start reserves 30 s; the PC's next ticket request finds nothing left (`final`) and the PC ends the
    session `allowance_exhausted`; the relay charged all 30 s reserved (it measured more), and from then on it
    refuses ICE servers and starts (the PC never sees them) until the window resets."""
    phone = await pair_phone(env, alice, agent, phones, "Alice's iPhone")
    await allow_screen_on_the_pc(agent, phone)
    clock = screen_clock(screen_on)
    now = clock()
    window_end = now + timedelta(hours=4)
    sql(
        env.database_url,
        "INSERT INTO screen_view_windows (id, account_id, window_start, window_end, used_seconds, reserved_seconds,"
        " zero_charged_attempts, created_at) VALUES (%s, %s, %s, %s, %s, 0, 0, %s)",
        (uuid.uuid4(), uuid.UUID(alice.account_id), now - timedelta(hours=1), window_end, 570, now),
    )
    ice = await phone.client.ice_servers()
    assert ice["allowance"]["remaining_seconds"] == 30, ice
    started = await phone.client.start()
    assert started.result["state"] == "succeeded", started.result
    clock.advance(40)  # 40 s pass on the relay's clock before the PC asks for its next ticket
    viewer = started.viewer
    await viewer.wait(lambda: viewer.pc.connectionState == "connected", 20, "DTLS connected")
    ended = await ended_frame(phone, started.session_id, timeout=CHUNK)
    assert ended["reason"] == "allowance_exhausted", ended
    row = await settled(env, started.command_id)
    assert row["end_reason"] == "allowance_exhausted" and row["charged_seconds"] == 30, row
    used = window(env, alice.account_id)
    assert used["used_seconds"] == FREE_SECONDS and used["reserved_seconds"] == 0, used

    refused = await phone.client.ice_servers()
    assert refused["type"] == "error" and refused["error"]["code"] == "SCREEN_ALLOWANCE_EXHAUSTED", refused
    assert refused["error"]["detail"]["resets_at"] is not None
    sessions_before = (await screen_status(agent))["sessions_started"]
    cid = await phone.ctrl.command(agent.pc_id, "screen.session_start", start_params(), None, lifetime=60)
    res = await phone.log.wait("result", timeout=20, command_id=cid)
    assert res["origin"] == "relay" and res["error"]["code"] == "SCREEN_ALLOWANCE_EXHAUSTED", res
    assert (await screen_status(agent))["sessions_started"] == sessions_before  # the PC never saw it


async def test_a_lying_agent_cannot_reduce_the_charge(
    env: Env, alice: Browser, screen_on: Any, phones: list[Phone]
) -> None:
    """A patched agent (screen_agent.py DOME_TEST_LYING_AGENT=1) never reports `started`, claims `connection_failed`
    with 0 live seconds on every end and 0 in every ticket request. The relay charges the wall time IT measured from
    the forward to the end: the lie buys nothing once the session outlived the connect timeout."""
    pc = await start_pc(env, alice, lying=True)
    try:
        phone = await pair_phone(env, alice, pc, phones, "Alice's iPhone")
        await allow_screen_on_the_pc(pc, phone)
        started = await stream(phone)
        row = reservation(env, started.command_id)
        assert row["state"] == "live" and row["started_at"] is None, row  # bound by the result, never `started`
        screen_clock(screen_on).advance(45)
        res = await phone.log.command(pc.pc_id, "screen.session_stop", {"screen_session_id": started.session_id})
        assert res["state"] == "succeeded", res
        ended = await ended_frame(phone, started.session_id)
        assert (ended["reason"], ended["live_seconds"]) == ("connection_failed", 0), ended
        row = await settled(env, started.command_id)
        measured = (row["ended_at"] - row["forwarded_at"]).total_seconds()
        assert measured > 45 and row["charged_seconds"] == min(round_up(measured), CHUNK) >= 50, row
        used = window(env, alice.account_id)
        assert used["used_seconds"] == row["charged_seconds"] and used["zero_charged_attempts"] == 0, used
    finally:
        await pc.stop()
        pc.cleanup()


# ----- the end-to-end authentication of the signaling -----------------------------------------------------------------
async def test_an_answer_not_signed_by_the_pinned_pc_key_shows_nothing(
    env: Env, alice: Browser, agent: RealAgent, screen_on: Any, phones: list[Phone]
) -> None:
    """rules.screen_signaling (6): the phone applies an answer only if the PC key pinned at pairing signed it for
    this account, controller, PC, command and offer. A relay that swaps in its own answer, edits the signed one, or
    replays it for another start gets SCREEN_ANSWER_INVALID; a PC key that changed since pairing is a hard stop. The
    phone applies nothing and gets no picture."""
    phone = await pair_phone(env, alice, agent, phones, "Alice's iPhone")
    await allow_screen_on_the_pc(agent, phone)
    started = await phone.client.start(apply_answer=False)
    genuine = started.answer_envelope
    assert genuine is not None and started.answer is not None
    payload = loads_strict(genuine["payload"])

    def refused(envelope: dict[str, Any], **override: Any) -> str:
        args = {"command_id": started.command_id, "offer_sdp": started.offer_sdp, **override}
        with pytest.raises(ProtocolError) as exc:
            phone.client.verify(envelope, args["command_id"], args["offer_sdp"])
        assert exc.value.code == "SCREEN_ANSWER_INVALID", exc.value
        return str(exc.value.detail["reason"])

    relay_key = generate_private_key()
    swapped = sign_payload(relay_key, dumps_compact(payload)).to_dict()  # the relay's own answer, same words
    assert refused(swapped) == "kid is not the pinned PC key"
    edited = {
        **genuine,
        "payload": dumps_compact({**payload, "answer_sdp": payload["answer_sdp"].replace("a=", "a=x", 1)}),
    }
    assert refused(edited) == "envelope or signature"
    assert refused(genuine, command_id=str(uuid.uuid4())) == "command_id is not ours"
    assert refused(genuine, offer_sdp=started.offer_sdp + "a=x\r\n") == "offer_digest does not match the offer we sent"
    repinned = ScreenClient(
        phone.ctrl, phone.log, agent.pc_id, jwk_from_public_key(generate_private_key().public_key())
    )
    with pytest.raises(ProtocolError) as exc:
        repinned.verify(genuine, started.command_id, started.offer_sdp)
    assert exc.value.code == "SCREEN_ANSWER_INVALID"  # a re-keyed PC: no "continue anyway"

    # nothing was applied, so nothing arrives; the phone stops the session it could not verify
    await asyncio.sleep(2)
    assert started.viewer.rtp_packets == 0 and not started.viewer.frames
    assert started.viewer.pc.connectionState == "new"
    res = await phone.log.command(agent.pc_id, "screen.session_stop", {"screen_session_id": started.session_id})
    assert res["state"] == "succeeded", res
    ended = await ended_frame(phone, started.session_id)
    assert ended["reason"] == "stopped"


async def test_a_tampered_offer_fingerprint_never_gets_a_picture(
    env: Env, alice: Browser, agent: RealAgent, screen_on: Any, phones: list[Phone]
) -> None:
    """rules.screen_signaling (7): the PC captures only after DTLS connects to the certificate whose fingerprint is in
    the offer the phone signed. An offer carrying another fingerprint (what a relay would need to sit in the middle)
    verifies and is answered, but the handshake with the real phone fails: no RTP, no frame, and the session ends
    `connection_failed`, which the relay forgives (no `started`, inside the connect timeout)."""
    phone = await pair_phone(env, alice, agent, phones, "Alice's iPhone")
    await allow_screen_on_the_pc(agent, phone)
    other = ":".join(["5A"] * 32)

    def tamper(offer: str) -> str:
        assert len(sdp_fingerprints(offer)) >= 1
        return re.sub(r"(a=fingerprint:sha-256 )[0-9A-Fa-f:]+", lambda m: m.group(1) + other, offer)

    history_before = len((await screen_status(agent))["history"])
    started = await phone.client.start(sign_offer=tamper)
    assert started.result["state"] == "succeeded", started.result
    viewer = started.viewer
    await asyncio.sleep(8)  # ICE completes within a second on this host; the handshake gets every chance
    # (the phone's own DTLS side may still report "connected": it checked the PC's genuine certificate; the PC, which
    # checks the phone's against the signed offer, refused, so not one packet of media was sent)
    assert viewer.rtp_packets == 0 and not viewer.frames, "media flowed to a peer whose certificate was not signed"
    ended = await ended_with_history(phone, agent, started.session_id, history_before)
    assert ended["reason"] == "connection_failed", ended
    row = await settled(env, started.command_id)
    assert row["charged_seconds"] == 0 and window(env, alice.account_id)["zero_charged_attempts"] == 1, row


# ----- the operator switch --------------------------------------------------------------------------------------------
async def test_the_operator_switch_off_refuses_screen_view_before_the_pc(
    env: Env, alice: Browser, agent: RealAgent, phones: list[Phone]
) -> None:
    """DOME_SCREEN_VIEW_ENABLED unset (the default): every ICE request and start is refused SCREEN_VIEW_DISABLED by
    the relay, even for a phone the PC owner allowed; the PC never sees the command; /healthz says off."""
    assert env.services.screen.config.enabled is False
    health = (await env.http.get("/healthz")).json()
    assert health["screen_view"] == "off", health
    phone = await pair_phone(env, alice, agent, phones, "Alice's iPhone")
    await allow_screen_on_the_pc(agent, phone)
    refused = await phone.client.ice_servers()
    assert refused["type"] == "error" and refused["error"]["code"] == "SCREEN_VIEW_DISABLED", refused
    cid = await phone.ctrl.command(agent.pc_id, "screen.session_start", start_params(), None, lifetime=60)
    res = await phone.log.wait("result", timeout=20, command_id=cid)
    assert res["origin"] == "relay" and res["state"] == "failed", res
    assert res["error"]["code"] == "SCREEN_VIEW_DISABLED", res
    assert (await screen_status(agent))["sessions_started"] == 0
