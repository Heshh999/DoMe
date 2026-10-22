"""The send-only aiortc peer (design §3, §4.4, §5.1, §5.3, §7; ADR-0002 D1 ``MediaTransport`` seam).

* **Strict offer use**: exactly the string the agent checked goes into ``setRemoteDescription``; aiortc refuses what
  it cannot parse. The PC is STUN-only (``relay.screen.stun_urls``): no TURN credential ever reaches this process.
* **Send-only**: one video track is added; the phone's audio section is answered ``inactive`` in Phase 1 and no
  decoder runs on network input.
* **PLI → IDR**: aiortc 1.15.0 never surfaces PLI/FIR on the pre-encoded path; ``_handle_rtcp_packet`` calls
  ``RTCRtpSender._send_keyframe``, which only sets a flag the pre-encoded branch never reads. The peer replaces
  ``sender._send_keyframe`` on the instance with a function that asks the track for an IDR, which the track grants at
  most twice a second (for VP8 the track calls the original when the IDR is due, because aiortc's own encoder reads
  the flag). If the hook point is missing the peer falls back to a periodic IDR every 2 s and reports
  ``pli_hook: missing``.
* **Capture starts only after ``connectionState == "connected"``**: the track's ``capture_allowed`` event is set there.
* **Path detection**: the nominated ICE pair (aioice ``_nominated``); a ``relay`` candidate on either side means
  ``relayed`` and the relayed bitrate cap (1.0 Mbps, or ``relayed_max_kbps`` for Data saver).
* **Bitrate steps** from ``RTCRtpSender.getStats()`` (``remote-inbound-rtp`` ``fractionLost``): loss above 10 % for
  5 s steps 1500 → 800 → 500 kbps with an IDR. No REMB wiring in Phase 1.
* **Data channel** ``dome-screen-v1``: inbound messages validated with ``parse_screen_dc_message(text, "phone_to_pc")``
  (unknown or malformed dropped and counted), outbound ones with ``"pc_to_phone"`` before sending.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
from collections.abc import Callable
from typing import Any

from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.rtcrtpsender import RTCRtpSender
from dome_protocol import ProtocolError, dumps_compact, parse_screen_dc_message

from ..sdp_check import check_offer
from .track import ScreenTrack

Emit = Callable[[dict[str, Any]], None]
BITRATE_STEPS_KBPS = (1500, 800, 500)
LOSS_THRESHOLD = 0.10
LOSS_SUSTAIN_SECONDS = 5.0
STATS_INTERVAL_SECONDS = 2.0
PERIODIC_IDR_SECONDS = 2.0  # the fallback when the PLI hook point is missing


def pli_hook_available() -> bool:
    """``RTCRtpSender._send_keyframe`` exists and the RTCP dispatch still calls it for PLI/FIR (guard for the
    pinned aiortc; ``--self-test`` and a unit test check it)."""
    if not callable(getattr(RTCRtpSender, "_send_keyframe", None)):
        return False
    try:
        source = inspect.getsource(RTCRtpSender._handle_rtcp_packet)
    except (OSError, TypeError):
        return False
    return "RTCP_PSFB_PLI" in source and "self._send_keyframe()" in source


class ScreenPeer:
    def __init__(
        self,
        *,
        offer_sdp: str,
        stun_urls: list[str],
        track: ScreenTrack,
        capture_allowed: asyncio.Event,
        emit: Emit,
        bitrate_kbps: dict[str, int],
        relayed_max_kbps: int | None = None,
    ) -> None:
        self._offer_sdp = offer_sdp
        self._stun_urls = [u for u in stun_urls if isinstance(u, str) and u.startswith(("stun:", "stuns:"))]
        self.track = track
        self._capture_allowed = capture_allowed
        self._emit = emit
        self._bitrate = dict(bitrate_kbps)
        self._relayed_max_kbps = relayed_max_kbps
        self.pc: RTCPeerConnection | None = None
        self.sender: RTCRtpSender | None = None
        self.channel: Any | None = None
        self.path: str | None = None
        self.pli_hook = False
        self.pli_count = 0
        self.dc_dropped = 0
        self.dc_received = 0
        self.data_saver = False
        self._step = 0
        self._lossy_since: float | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._closed = False
        self._connected_reported = False

    # ----- signaling -----
    async def answer(self) -> str:
        check_offer(self._offer_sdp)  # the worker never trusts that the agent checked (it did): SCREEN_OFFER_INVALID
        config = RTCConfiguration(iceServers=[RTCIceServer(urls=self._stun_urls)] if self._stun_urls else [])
        pc = RTCPeerConnection(config)
        self.pc = pc
        pc.on("connectionstatechange", self._on_connection_state)
        pc.on("datachannel", self._on_datachannel)
        # The send-only video transceiver exists BEFORE the offer is applied: aiortc negotiates each m-line's codecs
        # inside setRemoteDescription from the transceiver's preferences at that moment, and associates the offer's
        # video section with this transceiver by kind.
        transceiver = pc.addTransceiver(self.track, direction="sendonly")
        transceiver.setCodecPreferences(self._preferred_codecs())
        sender = transceiver.sender
        self.sender = sender
        try:
            await pc.setRemoteDescription(RTCSessionDescription(sdp=self._offer_sdp, type="offer"))
        except Exception as exc:  # noqa: BLE001 - aiortc refused the offer the strict check let through
            raise ProtocolError(
                "SCREEN_OFFER_INVALID", f"offer refused by the media stack ({exc.__class__.__name__})"
            ) from None
        self._install_pli_hook(sender)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        local = pc.localDescription
        assert local is not None
        if not self.pli_hook:
            self._tasks.append(asyncio.get_running_loop().create_task(self._periodic_idr(), name="dome-screen-idr"))
        return str(local.sdp)

    def _preferred_codecs(self) -> list[Any]:
        """Only the codec this worker encodes (plus RTX). Without this aiortc negotiates the offerer's first video
        codec, which for an aiortc or Safari offer may be VP8, and would then packetize the worker's pre-encoded
        H.264 access units under the VP8 payload type: the phone would decode nothing (tested on loopback)."""
        wanted = "video/h264" if self.track.choice.kind == "h264" else "video/vp8"
        return [
            codec
            for codec in RTCRtpSender.getCapabilities("video").codecs
            if codec.mimeType.lower() in (wanted, "video/rtx")
        ]

    def _install_pli_hook(self, sender: RTCRtpSender) -> None:
        if not pli_hook_available():
            self.pli_hook = False
            return
        original = sender._send_keyframe
        if self.track.choice.kind == "vp8":
            self.track.vp8_keyframe = original  # aiortc's own encoder consumes the flag, when the track's slot comes

        def hooked() -> None:
            self.pli_count += 1
            self.track.force_keyframe()  # rate-limited to 2/s by the track

        sender._send_keyframe = hooked  # type: ignore[method-assign]
        self.pli_hook = True

    # ----- connection -----
    async def _on_connection_state(self) -> None:
        pc = self.pc
        if pc is None:
            return
        state = pc.connectionState
        if state == "connected" and not self._connected_reported:
            self._connected_reported = True
            self.path = self._detect_path()
            self._apply_initial_bitrate()
            self._capture_allowed.set()  # capture may start now: DTLS connected to the certificate the phone signed
            self._emit({"ev": "connected", "path": self.path})
            self._tasks.append(asyncio.get_running_loop().create_task(self._stats_loop(), name="dome-screen-stats"))
        elif state in ("failed", "closed") or (state == "disconnected" and self._connected_reported):
            if not self._closed:
                self._emit({"ev": "disconnected", "reason": state})

    def _detect_path(self) -> str:
        """``relayed`` when the nominated pair has a relay candidate on either side (aioice internals, guarded)."""
        try:
            assert self.sender is not None
            connection = self.sender.transport.transport._connection  # RTCDtlsTransport → RTCIceTransport → aioice
            nominated = getattr(connection, "_nominated", {})
            for pair in nominated.values():
                for candidate in (pair.local_candidate, pair.remote_candidate):
                    if getattr(candidate, "type", "") == "relay":
                        return "relayed"
        except Exception:  # noqa: BLE001 - unknown pair: report direct (the agent's figures are informational)
            return "direct"
        return "direct"

    def _apply_initial_bitrate(self) -> None:
        if self.path == "relayed":
            kbps = int(self._bitrate.get("relayed", 1000))
            if self._relayed_max_kbps is not None:
                kbps = min(kbps, int(self._relayed_max_kbps))
                self.data_saver = kbps <= int(self._bitrate.get("data_saver", 500))
        else:
            kbps = int(self._bitrate.get("direct", 1500))
        self.track.bitrate_kbps = kbps

    # ----- data channel -----
    def _on_datachannel(self, channel: Any) -> None:
        self.channel = channel

        def on_message(message: Any) -> None:
            self.dc_received += 1
            try:
                parsed = parse_screen_dc_message(message if isinstance(message, str | bytes) else b"", "phone_to_pc")
            except (ProtocolError, Exception):  # noqa: BLE001 - the closed vocabulary: anything else is dropped
                self.dc_dropped += 1
                return
            kind = parsed["type"]
            if kind == "hb":
                self._emit({"ev": "hb"})
            elif kind == "keyframe":
                self.track.force_keyframe()
                self._emit({"ev": "keyframe"})
            elif kind == "bye":
                self._emit({"ev": "bye"})
            elif kind == "audio":
                self._emit({"ev": "audio", "on": bool(parsed.get("on"))})

        channel.on("message", on_message)

    def send_dc(self, message: dict[str, Any]) -> bool:
        """Validated against the PC → phone vocabulary before it leaves; False when the channel is not open."""
        text = dumps_compact(message)
        parse_screen_dc_message(text, "pc_to_phone")
        channel = self.channel
        if channel is None or channel.readyState != "open":
            return False
        channel.send(text)
        return True

    # ----- stats and bitrate steps -----
    async def _stats_loop(self) -> None:
        bytes_sent = 0
        while not self._closed:
            await asyncio.sleep(STATS_INTERVAL_SECONDS)
            sender = self.sender
            if sender is None:
                return
            report = await self._stats(sender)
            if report is None:
                continue
            loss: float | None = None
            for stat in report.values():
                if stat.type == "remote-inbound-rtp":
                    loss = float(getattr(stat, "fractionLost", 0) or 0) / 256.0
                elif stat.type == "outbound-rtp":
                    bytes_sent = int(getattr(stat, "bytesSent", 0) or 0)
            now = time.monotonic()
            if loss is not None and loss > LOSS_THRESHOLD:
                if self._lossy_since is None:
                    self._lossy_since = now
                elif now - self._lossy_since >= LOSS_SUSTAIN_SECONDS:
                    self._lossy_since = None
                    self._step_down()
            else:
                self._lossy_since = None
            self._emit(
                {
                    "ev": "stats",
                    "relayed_bytes": bytes_sent if self.path == "relayed" else 0,
                    "bytes_sent": bytes_sent,
                    "bitrate_kbps": self.track.bitrate_kbps,
                    "loss": round(loss, 3) if loss is not None else None,
                }
            )

    @staticmethod
    async def _stats(sender: RTCRtpSender) -> Any | None:
        try:
            return await sender.getStats()
        except Exception:  # noqa: BLE001 - a transport mid-teardown: no stats this round
            return None

    def _step_down(self) -> None:
        lower = [k for k in BITRATE_STEPS_KBPS if k < self.track.bitrate_kbps]
        if not lower:
            return
        self.track.set_bitrate(lower[0])
        self._emit({"ev": "state", "state": "sending", "encoder": self.track.choice.kind, "bitrate_kbps": lower[0]})

    async def _periodic_idr(self) -> None:
        while not self._closed:
            await asyncio.sleep(PERIODIC_IDR_SECONDS)
            self.track.force_keyframe()

    # ----- close -----
    async def close(self) -> None:
        self._closed = True
        for task in self._tasks:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        await self.track.aclose()
        if self.pc is not None:
            with contextlib.suppress(Exception):
                await self.pc.close()
            self.pc = None


__all__ = ["BITRATE_STEPS_KBPS", "PERIODIC_IDR_SECONDS", "ScreenPeer", "pli_hook_available"]
