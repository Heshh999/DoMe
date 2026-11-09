"""The phone's half of Screen view (protocol 1.4, and the 1.5 additions) in Python, for test_e2e_screen.py and
test_e2e_versions.py: what mobile-app's `lib/screen.ts` does, driving a REAL aiortc ``RTCPeerConnection`` as the viewer
on this host.

* :class:`FrameLog` reads the controller socket in the background and validates every frame against
  ``relay_to_controller``, so the PC-signed answer, the command result, the session frames and the error frames can be
  awaited in any order (``ControllerSim.recv_type`` drops what it is not waiting for).
* :class:`Viewer` is the receive-only peer: video and audio ``recvonly`` plus the ``dome-screen-v1`` data channel,
  vanilla ICE (every candidate is gathered before the offer is signed), heartbeats every
  ``screen_heartbeat_seconds``, decoded frames counted (never kept), the PC's data-channel messages parsed against the
  closed vocabulary, and an RTP tap that timestamps every H.264 packet carrying an IDR or SPS, so "a PLI is answered
  by a keyframe within 500 ms" is measured on the wire as the phone sees it. A viewer for a start that carries
  ``cursor`` (protocol 1.5) also opens the unordered ``dome-screen-cursor-v1`` channel (``maxRetransmits: 0``) in the
  same offer, as the app does; a viewer for a 1.4 start opens only the first channel, because the shipped 1.4 worker
  sends on whichever channel the phone announced last (design 2a Part 0). Every message from either channel is kept
  in memory with its channel's label, its length on the wire and when it arrived; nothing is written anywhere.
* :class:`ScreenClient` is the session logic: ``screen_ice_request``, the offer inside the signed 60 s
  ``screen.session_start`` (``surface`` "primary" or a monitor index; ``cursor`` only when asked, so the start is
  stamped 1.4 without it and 1.5 with it: the harness stamps by content, rules.command_version_stamping), the
  PC-signed answer verified against the key PINNED AT PAIRING (``verify_screen_answer_payload``; anything else is
  SCREEN_ANSWER_INVALID and nothing is applied), then exactly the signed ``answer_sdp`` applied. A start while a
  session is live is the app's "Next screen": no ``bye`` and no stop, the PC replaces the session.

Only the STUN entries of ``screen_ice_servers`` are used: the fake TURN provider's URLs name no real server, so media
here always takes the direct path between this host's interfaces. Nothing here writes a frame anywhere.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aioice import stun
from dome_protocol import (
    SCREEN_CURSOR_CHANNEL_LABEL,
    SCREEN_DATA_CHANNEL_LABEL,
    VerifiedScreenAnswer,
    loads_strict,
    parse_screen_dc_message,
    verify_screen_answer_payload,
)
from websockets.exceptions import ConnectionClosed

from conftest import REGISTRY, recv_frame

HEARTBEAT_SECONDS = float(REGISTRY.limits["screen_heartbeat_seconds"])
START_TIMEOUT = REGISTRY.get("screen.session_start").timeout_ms / 1000 + 5


# ----- the controller socket ------------------------------------------------------------------------------------------
class FrameLog:
    """Every frame the relay sends this phone, schema-checked, each one handed out once."""

    def __init__(self, ctrl: Any) -> None:
        self.ctrl = ctrl
        self.frames: list[dict[str, Any]] = []
        self.close_code: int | None = None
        self.closed = False
        self.error: BaseException | None = None  # a frame that failed the schema stops the log (and fails the wait)
        self._taken: set[int] = set()
        self._changed = asyncio.Event()
        self._task = asyncio.create_task(self._read(), name="phone-frames")

    async def _read(self) -> None:
        try:
            while True:
                frame = await recv_frame(self.ctrl.ws, "relay_to_controller", timeout=24 * 3600)
                self.frames.append(frame)
                self._changed.set()
        except ConnectionClosed as exc:
            self.close_code = exc.rcvd.code if exc.rcvd else None
        except Exception as exc:  # noqa: BLE001 - reported by the next wait()
            self.error = exc
        finally:
            self.closed = True
            self._changed.set()

    def _take(self, kind: str, where: Callable[[dict[str, Any]], bool] | None, match: dict[str, Any]) -> Any:
        for i, frame in enumerate(self.frames):
            if i in self._taken or frame["type"] != kind:
                continue
            if all(frame.get(k) == v for k, v in match.items()) and (where is None or where(frame)):
                self._taken.add(i)
                return frame
        return None

    async def wait(
        self, kind: str, timeout: float = 10, where: Callable[[dict[str, Any]], bool] | None = None, **match: Any
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            frame = self._take(kind, where, match)
            if frame is not None:
                return frame  # type: ignore[no-any-return]
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self.closed:
                seen = [f["type"] for i, f in enumerate(self.frames) if i not in self._taken]
                raise TimeoutError(
                    f"no {kind} frame matching {match} (closed={self.closed}, error={self.error!r}); unread: {seen}"
                )
            self._changed.clear()  # single-threaded: nothing is appended between the scan above and this clear
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._changed.wait(), remaining)

    def unread(self, kind: str, **match: Any) -> list[dict[str, Any]]:
        return [
            f
            for i, f in enumerate(self.frames)
            if i not in self._taken and f["type"] == kind and all(f.get(k) == v for k, v in match.items())
        ]

    async def command(self, pc_id: str, action: str, params: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
        cid = await self.ctrl.command(pc_id, action, params, None, **kw)
        return await self.wait("result", timeout=START_TIMEOUT, command_id=cid)

    async def aclose(self) -> None:
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._task


# ----- a STUN server on loopback (the relay's stun_urls in these tests) -----------------------------------------------
class StunServer(asyncio.DatagramProtocol):
    """Answers BINDING requests with the sender's address, so both peers gather a server-reflexive candidate at once
    instead of waiting out a public STUN server this sandbox cannot reach. Counts the requests it answered."""

    def __init__(self) -> None:
        self.transport: asyncio.DatagramTransport | None = None
        self.requests = 0

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        assert isinstance(transport, asyncio.DatagramTransport)
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            message = stun.parse_message(data)
        except ValueError:
            return
        if message.message_method != stun.Method.BINDING or message.message_class != stun.Class.REQUEST:
            return
        self.requests += 1
        response = stun.Message(
            message_method=stun.Method.BINDING,
            message_class=stun.Class.RESPONSE,
            transaction_id=message.transaction_id,
        )
        response.attributes["XOR-MAPPED-ADDRESS"] = (addr[0], addr[1])
        assert self.transport is not None
        self.transport.sendto(bytes(response), addr)

    @property
    def url(self) -> str:
        assert self.transport is not None
        host, port = self.transport.get_extra_info("sockname")[:2]
        return f"stun:{host}:{port}"


async def start_stun_server() -> StunServer:
    loop = asyncio.get_running_loop()
    _transport, protocol = await loop.create_datagram_endpoint(StunServer, local_addr=("127.0.0.1", 0))
    return protocol


# ----- the viewer peer ------------------------------------------------------------------------------------------------
def h264_nal_types(payload: bytes) -> set[int]:
    """NAL unit types an H.264 RTP payload starts (RFC 6184): a single NAL, a STAP-A (24) or a FIRST FU-A (28)."""
    if not payload:
        return set()
    kind = payload[0] & 0x1F
    if 1 <= kind <= 23:
        return {kind}
    if kind == 24:
        out: set[int] = set()
        i = 1
        while i + 2 <= len(payload):
            size = int.from_bytes(payload[i : i + 2], "big")
            i += 2
            if size == 0 or i + size > len(payload):
                break
            out.add(payload[i] & 0x1F)
            i += size
        return out
    if kind == 28 and len(payload) > 1 and payload[1] & 0x80:
        return {payload[1] & 0x1F}
    return set()


@dataclass
class FrameInfo:
    at: float
    width: int
    height: int


@dataclass
class Viewer:
    stun_urls: list[str]
    cursor_channel: bool = False  # open dome-screen-cursor-v1 too (a start that carries `cursor`, protocol 1.5)
    pc: RTCPeerConnection = field(init=False)
    channel: Any = None
    cdc: Any = None  # the cursor channel, when opened
    frames: list[FrameInfo] = field(default_factory=list)
    dc_messages: list[dict[str, Any]] = field(default_factory=list)  # every parsed message, either channel, in order
    messages: list[tuple[str, dict[str, Any]]] = field(default_factory=list)  # (channel label, message), same order
    dc_raw_lengths: list[int] = field(default_factory=list)  # UTF-8 bytes of each message as it arrived, same order
    dc_arrivals: list[float] = field(default_factory=list)  # monotonic arrival of each message, same order
    keyframe_packets: list[float] = field(default_factory=list)  # monotonic arrival of packets with an IDR or SPS
    rtp_packets: int = 0
    media_ssrc: int | None = None
    heartbeats_sent: int = 0
    heartbeat_enabled: bool = True
    last_heartbeat_at: float = 0.0
    _h264_pt: int | None = None
    _tasks: list[asyncio.Task[None]] = field(default_factory=list)

    def __post_init__(self) -> None:
        servers = [RTCIceServer(urls=self.stun_urls)] if self.stun_urls else []
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=servers))

    @property
    def video(self) -> Any:
        return self.pc.getTransceivers()[0]

    async def offer(self) -> str:
        """lib/screen.ts (design §4.2 step 2; 2a §4.1 "Channels"): video and audio recvonly, the data channel (and the
        unordered, never retransmitted cursor channel in the same offer when ``cursor_channel``), every candidate
        gathered."""
        self.pc.addTransceiver("video", direction="recvonly")
        self.pc.addTransceiver("audio", direction="recvonly")
        self.channel = self.pc.createDataChannel(SCREEN_DATA_CHANNEL_LABEL, ordered=True)
        self._listen(self.channel)
        if self.cursor_channel:
            self.cdc = self.pc.createDataChannel(SCREEN_CURSOR_CHANNEL_LABEL, ordered=False, maxRetransmits=0)
            self._listen(self.cdc)

        receiver = self.video.receiver
        handle = receiver._handle_rtp_packet

        async def tap(packet: Any, arrival_time_ms: int) -> None:
            self._on_rtp(packet)
            await handle(packet, arrival_time_ms=arrival_time_ms)

        receiver._handle_rtp_packet = tap  # the phone's view of the wire: what arrives, before its decoder
        await self.pc.setLocalDescription(await self.pc.createOffer())
        return str(self.pc.localDescription.sdp)

    def _listen(self, channel: Any) -> None:
        label = str(channel.label)

        @channel.on("message")
        def on_message(message: Any) -> None:
            raw = len(message.encode("utf-8")) if isinstance(message, str) else len(message)
            parsed = parse_screen_dc_message(message, "pc_to_phone")  # raises on anything else
            self.dc_messages.append(parsed)
            self.messages.append((label, parsed))
            self.dc_raw_lengths.append(raw)
            self.dc_arrivals.append(time.monotonic())

    def _on_rtp(self, packet: Any) -> None:
        if self._h264_pt is None:
            self._h264_pt = next((c.payloadType for c in self.video._codecs if c.mimeType.lower() == "video/h264"), -1)
        if packet.payload_type != self._h264_pt:
            return  # RTX or another codec
        self.rtp_packets += 1
        self.media_ssrc = packet.ssrc
        if h264_nal_types(packet.payload) & {5, 7}:
            self.keyframe_packets.append(time.monotonic())

    async def accept(self, answer_sdp: str) -> None:
        await self.pc.setRemoteDescription(RTCSessionDescription(sdp=answer_sdp, type="answer"))
        self._tasks.append(asyncio.create_task(self._receive(), name="phone-video"))
        self._tasks.append(asyncio.create_task(self._heartbeats(), name="phone-hb"))

    async def _receive(self) -> None:
        track = self.video.receiver.track
        with contextlib.suppress(Exception):
            while True:
                frame = await track.recv()
                self.frames.append(FrameInfo(time.monotonic(), frame.width, frame.height))

    async def _heartbeats(self) -> None:
        while True:
            if self.heartbeat_enabled and self.channel is not None and self.channel.readyState == "open":
                self.send({"type": "hb"})
                self.heartbeats_sent += 1
                self.last_heartbeat_at = time.monotonic()
            await asyncio.sleep(HEARTBEAT_SECONDS)

    def send(self, message: dict[str, Any]) -> None:
        self.channel.send(json.dumps(message, separators=(",", ":")))

    async def send_pli(self) -> None:
        """What a decoder that lost its reference does (Safari sends PLI)."""
        assert self.media_ssrc is not None
        await self.video.receiver._send_rtcp_pli(self.media_ssrc)

    def dc_of(self, kind: str) -> list[dict[str, Any]]:
        return [m for m in self.dc_messages if m["type"] == kind]

    def labels_of(self, kind: str) -> list[str]:
        """The channel each ``kind`` message arrived on, in order."""
        return [label for label, m in self.messages if m["type"] == kind]

    def frames_since(self, at: float) -> int:
        return sum(1 for f in self.frames if f.at > at)

    async def wait(self, predicate: Callable[[], bool], timeout: float = 15, what: str = "condition") -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                raise TimeoutError(f"viewer: {what} not met within {timeout} s (state {self.pc.connectionState})")
            await asyncio.sleep(0.02)

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self.pc.close()


# ----- the session client ---------------------------------------------------------------------------------------------
@dataclass
class Started:
    command_id: str
    offer_sdp: str  # what the phone signed
    result: dict[str, Any]
    viewer: Viewer
    command_envelope: dict[str, Any] | None = None  # the signed start as it went on the wire
    answer: VerifiedScreenAnswer | None = None
    answer_envelope: dict[str, Any] | None = None

    @property
    def session_id(self) -> str:
        return str(self.result["result"]["screen_session_id"])

    @property
    def stamp(self) -> str:
        """The protocol_version the signed start carried."""
        assert self.command_envelope is not None
        return str(loads_strict(self.command_envelope["payload"])["protocol_version"])


class ScreenClient:
    """One phone's Screen view on one PC, pinned to the PC key it verified at pairing."""

    def __init__(self, ctrl: Any, log: FrameLog, pc_id: str, pinned_jwk: dict[str, str] | None) -> None:
        self.ctrl = ctrl
        self.log = log
        self.pc_id = pc_id
        self.pinned_jwk = pinned_jwk
        self.ice: dict[str, Any] | None = None
        self.viewers: list[Viewer] = []

    async def ice_servers(self) -> dict[str, Any]:
        """``screen_ice_request`` → ``screen_ice_servers``, or the relay's ``error`` frame naming this PC."""
        await self.ctrl.send({"type": "screen_ice_request", "pc_id": self.pc_id})
        deadline = time.monotonic() + 10
        while True:
            for kind, key in (("screen_ice_servers", "pc_id"), ("error", "ref_pc_id")):
                found: dict[str, Any] | None = self.log._take(kind, None, {key: self.pc_id})
                if found is not None:
                    if kind == "screen_ice_servers":
                        self.ice = found
                    return found
            if time.monotonic() > deadline:
                raise TimeoutError("no answer to screen_ice_request")
            await asyncio.sleep(0.02)

    def stun_urls(self) -> list[str]:
        assert self.ice is not None
        return [u for server in self.ice["ice_servers"] for u in server["urls"] if u.startswith(("stun:", "stuns:"))]

    async def start(
        self,
        *,
        takeover: bool = False,
        sign_offer: Callable[[str], str] | None = None,
        apply_answer: bool = True,
        surface: str | int = "primary",
        cursor: str | None = None,
    ) -> Started:
        """ICE servers, the offer, the signed start, then the result and the PC-signed answer in either order. With the
        pin the answer is verified before anything is applied; ``sign_offer`` lets a test change what gets signed.
        ``surface`` is the monitor to watch ("primary" or its index); ``cursor`` ("overlay" | "drawn") goes into the
        params only when given, and only then does the viewer open the cursor channel (2a Part 0)."""
        assert self.pinned_jwk is not None, "no pin, no picture: the phone never asks without a pinned PC key"
        ice = await self.ice_servers()
        assert ice["type"] == "screen_ice_servers", ice
        viewer = Viewer(self.stun_urls(), cursor_channel=cursor is not None)
        self.viewers.append(viewer)
        offer = await viewer.offer()
        signed = sign_offer(offer) if sign_offer is not None else offer
        params: dict[str, Any] = {"offer_sdp": signed, "surface": surface, "audio": True, "takeover": takeover}
        if cursor is not None:
            params["cursor"] = cursor
        cid, envelope = self.ctrl.envelope(self.pc_id, "screen.session_start", params, None, lifetime=60)
        await self.ctrl.send_envelope(self.pc_id, envelope)
        result = await self.log.wait("result", timeout=START_TIMEOUT, command_id=cid)
        started = Started(command_id=cid, offer_sdp=signed, result=result, viewer=viewer, command_envelope=envelope)
        if result["state"] != "succeeded":
            return started
        REGISTRY.validate_result("screen.session_start", result["result"])
        frame = await self.log.wait(
            "screen_answer",
            timeout=10,
            pc_id=self.pc_id,
            where=lambda f: loads_strict(f["envelope"]["payload"]).get("command_id") == cid,
        )
        started.answer_envelope = frame["envelope"]
        started.answer = self.verify(frame["envelope"], cid, signed)
        if apply_answer:
            await viewer.accept(started.answer.answer_sdp)  # exactly the signed string
        return started

    def verify(self, envelope: dict[str, Any], command_id: str, offer_sdp: str) -> VerifiedScreenAnswer:
        assert self.pinned_jwk is not None
        return verify_screen_answer_payload(
            envelope,
            pinned_jwk=self.pinned_jwk,
            account_id=self.ctrl.browser.account_id,
            controller_id=str(self.ctrl.controller_id),
            pc_id=self.pc_id,
            command_id=command_id,
            offer_sdp=offer_sdp,
        )

    async def close(self) -> None:
        for viewer in self.viewers:
            await viewer.close()
        self.viewers.clear()


__all__ = ["FrameLog", "ScreenClient", "Started", "StunServer", "Viewer", "h264_nal_types", "start_stun_server"]
