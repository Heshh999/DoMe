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
* **What the media path did** (device round 2: the PC was ``live`` for 217 s and nothing showed which stage failed):
  the 2 s ``stats`` event carries the sender's ``outbound-rtp`` counts, whether a receiver report from the phone has
  come (``rr``), its loss, jitter and round-trip time, the PLIs and NACKed packets (``sender._retransmit`` counted on
  the instance) and the track's counters (:meth:`ScreenTrack.stats`); one ``screen media`` line is logged every
  :data:`MEDIA_LOG_SECONDS` and a final one at close (never per frame), and one ``screen transport`` line at
  ``connected`` (candidate types, protocol, address family, the negotiated codec and RTP payload limit; never an
  address). Log keys avoid every redacted word (``sig``, ``payload``, ``sdp``, ``candidate``, ...).
* **RTP payloads of at most 1200 bytes** (:data:`RTP_PAYLOAD_MAX`): aiortc packetizes H.264 and VP8 up to its module
  global ``PACKET_MAX`` (1300), which with headers, SRTP and an IPv6 tunnel, a VPN or some cellular paths exceeds the
  path MTU; both globals are lowered before the first send.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import time
from collections.abc import Callable
from typing import Any

import structlog
from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription
from aiortc.codecs import h264 as aiortc_h264
from aiortc.codecs import vpx as aiortc_vpx
from aiortc.rtcrtpsender import RTCRtpSender
from dome_protocol import ProtocolError, dumps_compact, parse_screen_dc_message

from ..sdp_check import check_offer
from .track import ScreenTrack

log = structlog.get_logger(__name__)

Emit = Callable[[dict[str, Any]], None]
BITRATE_STEPS_KBPS = (1500, 800, 500)
LOSS_THRESHOLD = 0.10
LOSS_SUSTAIN_SECONDS = 5.0
STATS_INTERVAL_SECONDS = 2.0
MEDIA_LOG_SECONDS = 5.0  # one "screen media" line this often while connected (never per frame)
PERIODIC_IDR_SECONDS = 2.0  # the fallback when the PLI hook point is missing
RTP_PAYLOAD_MAX = 1200  # bytes of RTP payload per packet (aiortc's PACKET_MAX is 1300)
# the "screen media" line's fields, in the stats event's names (au_bytes and rtp_bytes are logged in KiB)
MEDIA_LOG_FIELDS = (
    "captured",
    "refreshed",
    "pumped",
    "encoded",
    "aus",
    "idr",
    "au_bytes",
    "pending",
    "rtp_packets",
    "rtp_bytes",
    "rr",
    "lost",
    "rtt_ms",
    "pli",
    "kf_req",
    "nack",
    "first_kf_ms",
    "stalled",
)


def limit_rtp_payloads(size: int = RTP_PAYLOAD_MAX) -> None:
    """aiortc's H.264 and VP8 packetizers read their module global ``PACKET_MAX`` at call time: lower both."""
    aiortc_h264.PACKET_MAX = int(size)
    aiortc_vpx.PACKET_MAX = int(size)


def rtp_stats(stats: Any) -> dict[str, Any]:
    """The sender's report reduced to numbers: ``outbound-rtp`` packets and bytes; from ``remote-inbound-rtp`` (the
    phone's receiver reports on our SSRC) ``rr`` (one has come), ``lost``, ``jitter_ms`` (RTCP jitter is in 90 kHz
    units for video), ``rtt_ms`` and ``loss`` (the fraction of the last report)."""
    out: dict[str, Any] = {
        "rtp_packets": 0,
        "rtp_bytes": 0,
        "rr": False,
        "lost": 0,
        "jitter_ms": None,
        "rtt_ms": None,
        "loss": None,
    }
    for stat in stats:
        kind = getattr(stat, "type", None)
        if kind == "outbound-rtp":
            out["rtp_packets"] = int(getattr(stat, "packetsSent", 0) or 0)
            out["rtp_bytes"] = int(getattr(stat, "bytesSent", 0) or 0)
        elif kind == "remote-inbound-rtp":
            out["rr"] = True
            out["lost"] = int(getattr(stat, "packetsLost", 0) or 0)
            jitter = getattr(stat, "jitter", None)
            out["jitter_ms"] = round(float(jitter) / 90.0, 1) if isinstance(jitter, int | float) else None
            rtt = getattr(stat, "roundTripTime", None)
            out["rtt_ms"] = int(float(rtt) * 1000) if isinstance(rtt, int | float) else None
            out["loss"] = round(float(getattr(stat, "fractionLost", 0) or 0) / 256.0, 3)
    return out


def media_log_fields(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The ``screen media`` line from a stats snapshot: short keys, byte counts in KiB."""
    fields: dict[str, Any] = {}
    for key in MEDIA_LOG_FIELDS:
        value = snapshot.get(key)
        if key == "au_bytes":
            fields["au_kb"] = int(value or 0) // 1024
        elif key == "rtp_bytes":
            fields["rtp_kb"] = int(value or 0) // 1024
        elif key == "stalled":
            if value:
                fields["stalled"] = value
        else:
            fields[key] = value
    return fields


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
        self.transceiver: Any | None = None
        self.channel: Any | None = None
        self.path: str | None = None
        self.pli_hook = False
        self.pli_count = 0
        self.nack_count = 0  # packets the phone NACKed (each retransmission request)
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
        limit_rtp_payloads()  # before anything is sent
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
        self.transceiver = transceiver
        sender = transceiver.sender
        self.sender = sender
        self._count_nacks(sender)
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

    def _count_nacks(self, sender: RTCRtpSender) -> None:
        """Count the packets the phone NACKs: aiortc's RTCP dispatch awaits ``self._retransmit(seq)`` for each one,
        so the instance's method is wrapped (left alone when the pinned aiortc lacks it)."""
        original = getattr(sender, "_retransmit", None)
        if not callable(original) or not inspect.iscoroutinefunction(original):
            return

        async def counted(sequence_number: int) -> None:
            self.nack_count += 1
            await original(sequence_number)

        sender._retransmit = counted  # type: ignore[method-assign]

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
            log.info("screen transport", **self.transport_facts())
            loop = asyncio.get_running_loop()
            self._tasks.append(loop.create_task(self._stats_loop(), name="dome-screen-stats"))
            self._tasks.append(loop.create_task(self._media_log_loop(), name="dome-screen-media-log"))
        elif state in ("failed", "closed") or (state == "disconnected" and self._connected_reported):
            if not self._closed:
                self._emit({"ev": "disconnected", "reason": state})

    def _nominated_pairs(self) -> list[Any]:
        """The nominated ICE pairs (aioice internals, guarded): none when they cannot be read."""
        try:
            assert self.sender is not None
            connection = self.sender.transport.transport._connection  # RTCDtlsTransport → RTCIceTransport → aioice
            return list(getattr(connection, "_nominated", {}).values())
        except Exception:  # noqa: BLE001 - unknown pair
            return []

    def _detect_path(self) -> str:
        """``relayed`` when the nominated pair has a relay candidate on either side (aioice internals, guarded)."""
        try:
            for pair in self._nominated_pairs():
                for candidate in (pair.local_candidate, pair.remote_candidate):
                    if getattr(candidate, "type", "") == "relay":
                        return "relayed"
        except Exception:  # noqa: BLE001 - unknown pair: report direct (the agent's figures are informational)
            return "direct"
        return "direct"

    def transport_facts(self) -> dict[str, Any]:
        """The ``screen transport`` line: the path, both candidates' types (host, srflx, prflx, relay), the protocol,
        the address family of the nominated pair (never an address), the negotiated codec, its payload type,
        ``profile-level-id`` and ``packetization-mode``, and the RTP payload limit."""
        facts: dict[str, Any] = {
            "path": self.path,
            "local_type": None,
            "remote_type": None,
            "protocol": None,
            "ip": None,
        }
        try:
            pairs = self._nominated_pairs()
            if pairs:
                local, remote = pairs[0].local_candidate, pairs[0].remote_candidate
                facts["local_type"] = str(getattr(local, "type", "") or "")[:8] or None
                facts["remote_type"] = str(getattr(remote, "type", "") or "")[:8] or None
                facts["protocol"] = str(getattr(local, "transport", "") or "").lower()[:4] or None
                host = str(getattr(local, "host", "") or "")
                facts["ip"] = ("v6" if ":" in host else "v4") if host else None
        except Exception:  # noqa: BLE001, S110 - aioice internals: the line still says what is known
            pass
        codecs = list(getattr(self.transceiver, "_codecs", None) or [])
        if codecs:
            codec = codecs[0]
            parameters = dict(getattr(codec, "parameters", {}) or {})
            facts["codec"] = str(getattr(codec, "mimeType", "")).split("/")[-1][:8] or None
            facts["pt"] = getattr(codec, "payloadType", None)
            profile = parameters.get("profile-level-id")
            facts["profile_level_id"] = str(profile)[:6] if profile is not None else None
            mode = parameters.get("packetization-mode")
            facts["packetization_mode"] = str(mode)[:2] if mode is not None else None
        facts["packet_max"] = aiortc_h264.PACKET_MAX if self.track.choice.kind == "h264" else aiortc_vpx.PACKET_MAX
        return facts

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
                self.track.force_keyframe(source="dc")
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
    async def media_snapshot(self) -> dict[str, Any] | None:
        """The sender's numbers (:func:`rtp_stats`), the PLI and NACK counts, the encoder's label and bitrate and the
        track's counters; None when the sender has no report (a transport mid-teardown)."""
        sender = self.sender
        if sender is None:
            return None
        report = await self._stats(sender)
        if report is None:
            return None
        snapshot = rtp_stats(report.values())
        snapshot.update(
            pli=self.pli_count,
            nack=self.nack_count,
            bitrate_kbps=self.track.bitrate_kbps,
            encoder_label=self.track.choice.label,
        )
        snapshot.update(self.track.stats())
        return snapshot

    async def _stats_loop(self) -> None:
        while not self._closed:
            await asyncio.sleep(STATS_INTERVAL_SECONDS)
            self.track.check_stall()  # also when an encode call never returns to the track's own loop
            snapshot = await self.media_snapshot()
            if snapshot is None:
                if self.sender is None:
                    return
                continue
            loss: float | None = snapshot["loss"]
            bytes_sent = int(snapshot["rtp_bytes"])
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
                    **snapshot,
                    "ev": "stats",
                    "relayed_bytes": bytes_sent if self.path == "relayed" else 0,
                    "bytes_sent": bytes_sent,
                    "bitrate_kbps": self.track.bitrate_kbps,
                    "loss": round(loss, 3) if loss is not None else None,
                }
            )

    async def _media_log_loop(self) -> None:
        while not self._closed:
            await asyncio.sleep(MEDIA_LOG_SECONDS)
            await self._log_media("screen media")

    async def _log_media(self, event: str) -> None:
        try:
            snapshot = await asyncio.wait_for(self.media_snapshot(), 1.0)
        except Exception:  # noqa: BLE001 - a transport mid-teardown: no line this time
            return
        if snapshot is not None:
            log.info(event, **media_log_fields(snapshot))

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
            self.track.force_keyframe(source="periodic")

    # ----- close -----
    async def close(self) -> None:
        if not self._closed and self._connected_reported:
            await self._log_media("screen media final")
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


__all__ = [
    "BITRATE_STEPS_KBPS",
    "MEDIA_LOG_SECONDS",
    "PERIODIC_IDR_SECONDS",
    "RTP_PAYLOAD_MAX",
    "ScreenPeer",
    "limit_rtp_payloads",
    "media_log_fields",
    "pli_hook_available",
    "rtp_stats",
]
