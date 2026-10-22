"""Video encoding for the worker (design §5.3) and the Annex B helpers its tests assert on.

* Output size fits **H.264 level 3.1** (:func:`fit_level_31`: long edge ≤ 1280, short edge ≤ 720, even).
* Encoder order (:func:`probe_encoders`): on Windows ``h264_mf`` with ``hw_encoding=1`` (Media Foundation reaches
  NVIDIA, Intel and AMD hardware: no vendor SDK encoders), then ``h264_mf`` in software, then VP8 through aiortc's
  own libvpx encoder ("Basic picture mode"). Off Windows there is no ``h264_mf``: with ``dev_h264`` the worker may
  use ``libx264`` from the stock PyPI PyAV wheel, which is **development and test only and never ships** (the
  licence gate refuses it; ``KNOWN_ISSUES.md``). Every probe opens a context and encodes one black frame.
* Packets go through aiortc's pre-encoded path, so the worker must itself make sure **every IDR access unit begins
  with SPS and PPS**: :meth:`H264Encoder.encode` prepends the context's ``extradata`` (converted from avcC to
  Annex B when needed) when an IDR arrives without them. ``h264_mf`` cannot change its bitrate mid-stream, so a
  bitrate step reopens the context (:meth:`H264Encoder.reopen`) and forces an IDR.
"""

from __future__ import annotations

import fractions
import sys
from dataclasses import dataclass, field
from typing import Any

from dome_protocol import ProtocolError

NAL_SPS = 7
NAL_PPS = 8
NAL_IDR = 5
NAL_SEI = 6
START_CODE = b"\x00\x00\x00\x01"
VIDEO_TIME_BASE = fractions.Fraction(1, 90000)
LEVEL_31_LONG = 1280
LEVEL_31_SHORT = 720
DEV_H264_CODECS = ("libx264",)  # development only: the stock PyAV wheel's GPL encoder, never in a shipping build


def fit_level_31(width: int, height: int) -> tuple[int, int]:
    """Scale down (never up) so the long edge is ≤ 1280 and the short edge ≤ 720, keeping the aspect ratio; both
    dimensions even (16:9 → 1280×720, 16:10 → 1152×720, 4:3 → 960×720, 21:9 → 1280×548)."""
    width, height = max(2, int(width)), max(2, int(height))
    long_edge, short_edge = max(width, height), min(width, height)
    scale = min(1.0, LEVEL_31_LONG / long_edge, LEVEL_31_SHORT / short_edge)
    w = max(2, int(width * scale) // 2 * 2)
    h = max(2, int(height * scale) // 2 * 2)
    return w, h


def nal_types(annexb: bytes) -> list[int]:
    """The NAL unit types in an Annex B byte string, in order (start codes 00 00 01 or 00 00 00 01)."""
    out: list[int] = []
    i = 0
    while True:
        i = annexb.find(b"\x00\x00\x01", i)
        if i == -1:
            return out
        i += 3
        if i < len(annexb):
            out.append(annexb[i] & 0x1F)


def extradata_to_annexb(extradata: bytes) -> bytes:
    """SPS/PPS from ``codec_context.extradata``: already Annex B (starts with a start code) or avcC
    (``AVCDecoderConfigurationRecord``, first byte 0x01), converted to Annex B. Empty when neither."""
    if not extradata:
        return b""
    if extradata.startswith(b"\x00\x00\x01") or extradata.startswith(b"\x00\x00\x00\x01"):
        return bytes(extradata)
    if extradata[0] != 1 or len(extradata) < 7:
        return b""
    out = bytearray()
    pos = 5
    count = extradata[pos] & 0x1F
    pos += 1
    for _ in range(count):
        if pos + 2 > len(extradata):
            return b""
        size = int.from_bytes(extradata[pos : pos + 2], "big")
        pos += 2
        out += START_CODE + extradata[pos : pos + size]
        pos += size
    if pos >= len(extradata):
        return bytes(out)
    count = extradata[pos]
    pos += 1
    for _ in range(count):
        if pos + 2 > len(extradata):
            return b""
        size = int.from_bytes(extradata[pos : pos + 2], "big")
        pos += 2
        out += START_CODE + extradata[pos : pos + size]
        pos += size
    return bytes(out)


def ensure_parameter_sets(access_unit: bytes, parameter_sets: bytes) -> bytes:
    """An IDR access unit that lacks SPS and PPS gets them prepended (design §5.3 worker requirement)."""
    types = nal_types(access_unit)
    if NAL_IDR in types and not (NAL_SPS in types and NAL_PPS in types) and parameter_sets:
        return parameter_sets + access_unit
    return access_unit


@dataclass(frozen=True, slots=True)
class EncoderChoice:
    kind: str  # h264 | vp8
    codec_name: str  # the PyAV codec, "" for vp8 (aiortc encodes)
    hardware: bool = False
    options: dict[str, str] = field(default_factory=dict)
    development_only: bool = False

    @property
    def label(self) -> str:
        if self.kind == "vp8":
            return "vp8 (aiortc libvpx)"
        return f"{self.codec_name}{' hw' if self.hardware else ''}{' DEV ONLY' if self.development_only else ''}"


def encoder_candidates(preference: list[str], *, dev_h264: bool, platform: str = sys.platform) -> list[EncoderChoice]:
    """The ordered candidates for this platform; ``preference`` is what the phone offered (h264 and/or vp8)."""
    out: list[EncoderChoice] = []
    if "h264" in preference:
        if platform == "win32":
            mf = {"scenario": "display_remoting", "rate_control": "cbr", "max_b_frames": "0"}
            out.append(EncoderChoice("h264", "h264_mf", hardware=True, options={**mf, "hw_encoding": "1"}))
            out.append(EncoderChoice("h264", "h264_mf", hardware=False, options={**mf, "hw_encoding": "0"}))
        elif dev_h264:
            for name in DEV_H264_CODECS:
                out.append(
                    EncoderChoice(
                        "h264",
                        name,
                        options={
                            "preset": "ultrafast",
                            "tune": "zerolatency",
                            "profile": "baseline",
                            "level": "3.1",
                            "forced-idr": "1",
                            "x264-params": "repeat-headers=0:bframes=0:keyint=600:annexb=1",
                        },
                        development_only=True,
                    )
                )
    if "vp8" in preference:
        out.append(EncoderChoice("vp8", ""))
    return out


class H264Encoder:
    """A PyAV codec context producing Annex B access units with SPS/PPS before every IDR."""

    def __init__(self, choice: EncoderChoice, width: int, height: int, fps: int, bitrate_kbps: int) -> None:
        self.choice = choice
        self.width, self.height = width, height
        self.fps = max(1, int(fps))
        self.bitrate_kbps = int(bitrate_kbps)
        self._ctx: Any = None
        self._parameter_sets = b""
        self.frames_encoded = 0
        self.idr_count = 0
        self.prepended = 0
        self.open()

    def open(self) -> None:
        import av
        from av.codec.context import Flags

        ctx: Any = av.CodecContext.create(self.choice.codec_name, "w")  # a VideoCodecContext at runtime
        ctx.width, ctx.height = self.width, self.height
        ctx.pix_fmt = "yuv420p"
        ctx.time_base = VIDEO_TIME_BASE
        ctx.framerate = fractions.Fraction(self.fps, 1)
        ctx.bit_rate = self.bitrate_kbps * 1000
        ctx.flags |= Flags.global_header  # parameter sets land in extradata; the worker prepends them itself
        ctx.options = dict(self.choice.options)
        ctx.open()
        self._ctx = ctx
        self._parameter_sets = extradata_to_annexb(bytes(ctx.extradata or b""))

    def close(self) -> None:
        if self._ctx is not None:
            try:
                self._ctx.close(strict=False)
            except Exception:  # noqa: BLE001, S110 - closing a hardware context may raise once the device is gone
                pass
            self._ctx = None

    @property
    def parameter_sets(self) -> bytes:
        return self._parameter_sets

    def reopen(self, bitrate_kbps: int) -> None:
        """A bitrate step: ``h264_mf`` cannot change mid-stream, so the context is reopened (≈ 100 ms) and the next
        frame is an IDR (the caller forces it)."""
        self.close()
        self.bitrate_kbps = int(bitrate_kbps)
        self.open()

    def encode(self, frame: Any, *, force_idr: bool) -> list[Any]:
        """Encode one ``av.VideoFrame`` (yuv420p, pts in the 90 kHz time base) into ``av.Packet``s whose IDR access
        units begin with SPS and PPS. Frames from ``lavfi`` arrive flagged I: the flag is reset unless an IDR is
        wanted, or every frame would be a keyframe."""
        import av
        from av.video.frame import PictureType

        frame.pict_type = PictureType.I if force_idr else PictureType.NONE
        out: list[Any] = []
        for packet in self._ctx.encode(frame):
            data = bytes(packet)
            types = nal_types(data)
            if NAL_IDR in types:
                self.idr_count += 1
            fixed = ensure_parameter_sets(data, self._parameter_sets)
            if fixed is not data:
                self.prepended += 1
            new = av.Packet(fixed)
            new.pts = packet.pts
            new.dts = packet.dts
            new.time_base = packet.time_base if packet.time_base else VIDEO_TIME_BASE
            out.append(new)
        self.frames_encoded += 1
        return out


def probe_encoders(candidates: list[EncoderChoice], width: int = 64, height: int = 64) -> EncoderChoice:
    """Open each candidate and encode one black frame; the first that works wins. ``SCREEN_ENCODER_UNAVAILABLE``
    when none does."""
    import av

    for choice in candidates:
        if choice.kind == "vp8":
            if "libvpx" in av.codecs_available:
                return choice
            continue
        if choice.codec_name not in av.codecs_available:
            continue
        if _encodes_one_frame(choice, width, height):
            return choice
    raise ProtocolError("SCREEN_ENCODER_UNAVAILABLE", "Your PC has no video encoder DoMe can use for Screen view.")


def _encodes_one_frame(choice: EncoderChoice, width: int, height: int) -> bool:
    """Open the candidate and encode one black frame; an encoder that cannot is simply not available here."""
    import av

    try:
        enc = H264Encoder(choice, width, height, 30, 500)
    except Exception:  # noqa: BLE001 - cannot open (no hardware, no Media Feature Pack, unknown option)
        return False
    try:
        frame = av.VideoFrame(width, height, "yuv420p")
        for plane in frame.planes:
            plane.update(bytes(plane.buffer_size))
        frame.pts = 0
        frame.time_base = VIDEO_TIME_BASE
        enc.encode(frame, force_idr=True)
    except Exception:  # noqa: BLE001 - opened but cannot encode
        return False
    finally:
        enc.close()
    return True


def list_encoders(*, dev_h264: bool) -> list[dict[str, Any]]:
    """``--list-encoders``: every candidate and whether it works here (never any frame content)."""
    rows: list[dict[str, Any]] = []
    for choice in encoder_candidates(["h264", "vp8"], dev_h264=dev_h264):
        try:
            probe_encoders([choice])
            ok, why = True, ""
        except ProtocolError as exc:
            ok, why = False, exc.code
        rows.append({"encoder": choice.label, "kind": choice.kind, "available": ok, "note": why})
    return rows


__all__ = [
    "DEV_H264_CODECS",
    "NAL_IDR",
    "NAL_PPS",
    "NAL_SPS",
    "VIDEO_TIME_BASE",
    "EncoderChoice",
    "H264Encoder",
    "encoder_candidates",
    "ensure_parameter_sets",
    "extradata_to_annexb",
    "fit_level_31",
    "list_encoders",
    "nal_types",
    "probe_encoders",
]
