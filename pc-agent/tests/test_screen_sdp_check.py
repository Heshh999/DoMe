"""The strict offer check (dome_agent/screen/sdp_check.py; design §4.2 step 5, rules.screen_signaling (4)): table tests on
the fixture offer, a real aiortc recvonly offer and every way an offer may be wrong. SCREEN_OFFER_INVALID names the
check in detail.reason, never the SDP."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from dome_protocol import ProtocolError

from dome_agent.screen.sdp_check import check_offer

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[2] / "shared" / "protocol" / "fixtures" / "es256-python.json").read_text(
        encoding="utf-8"
    )
)
OFFER: str = FIXTURE["screen"]["offer_sdp"]
FP = "a=fingerprint:sha-256 01:02:03:04:05:06:07:08:09:0A:0B:0C:0D:0E:0F:10:11:12:13:14:15:16:17:18:19:1A:1B:1C:1D:1E:1F:20"


def test_the_fixture_offer_passes_and_is_summarised() -> None:
    summary = check_offer(OFFER)
    assert summary.fingerprint.startswith("01:02:03") and summary.h264 and summary.vp8
    assert summary.byte_size == len(OFFER.encode())


def test_a_real_aiortc_recvonly_offer_passes() -> None:
    aiortc = pytest.importorskip("aiortc")
    import asyncio

    async def offer() -> str:
        pc = aiortc.RTCPeerConnection(aiortc.RTCConfiguration(iceServers=[]))
        pc.addTransceiver("video", direction="recvonly")
        pc.addTransceiver("audio", direction="recvonly")
        pc.createDataChannel("dome-screen-v1", ordered=True)
        await pc.setLocalDescription(await pc.createOffer())
        sdp = pc.localDescription.sdp
        await pc.close()
        return sdp

    sdp = asyncio.run(offer())
    summary = check_offer(sdp)
    assert summary.h264 and summary.vp8  # aiortc offers both; sha-384/sha-512 fingerprints alongside are fine


def _reason(sdp: object) -> str:
    with pytest.raises(ProtocolError) as exc:
        check_offer(sdp)
    assert exc.value.code == "SCREEN_OFFER_INVALID"
    assert exc.value.message == "The phone's connection request was not valid. Try again."
    reason = exc.value.detail["reason"]
    if isinstance(sdp, str) and sdp:
        assert sdp[:40] not in exc.value.message  # the SDP never travels in the error
    return str(reason)


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda s: "", "shape"),
        (lambda s: s.replace("\r\n", "\n"), "shape"),  # bare LF
        (lambda s: s + "x" * 13000, "shape"),  # over screen_sdp_max_bytes
        (lambda s: s.replace("v=0\r\n", "v=1\r\n"), "version"),
        (lambda s: s.replace(FP + "\r\n", ""), "fingerprint"),  # no fingerprint at all
        (
            lambda s: s.replace("a=mid:1\r\n", "a=mid:1\r\n" + FP.replace("20", "21") + "\r\n"),
            "fingerprint",
        ),  # two values
        (lambda s: s.replace("a=setup:actpass\r\n", ""), "setup"),
        (lambda s: s.replace("a=ice-pwd:phonephonephonephonephon\r\n", ""), "ice"),
        (
            lambda s: s.replace(
                "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n",
                "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n",
            ),
            "media",
        ),
        (lambda s: s[: s.index("m=application")], "media"),  # no data channel section
        (lambda s: s.replace("a=mid:0\r\na=recvonly", "a=mid:0\r\na=sendrecv"), "direction"),  # the phone must not send
        (lambda s: s.replace("a=mid:1\r\na=recvonly", "a=mid:1\r\na=sendonly"), "direction"),
        (
            lambda s: s.replace("a=rtpmap:96 H264/90000\r\n", "").replace(
                "a=rtpmap:97 VP8/90000\r\n", "a=rtpmap:97 AV1/90000\r\n"
            ),
            "codec",
        ),
        (
            lambda s: s.replace(
                "m=application 9 UDP/DTLS/SCTP webrtc-datachannel", "m=application 9 UDP/TLS/RTP/SAVPF 100"
            ),
            "media",
        ),
    ],
)
def test_every_rule_names_its_reason(mutation: object, reason: str) -> None:
    sdp = mutation(OFFER)  # type: ignore[operator]
    assert _reason(sdp) == reason


def test_non_text_is_shape() -> None:
    assert _reason(None) == "shape"
    assert _reason(123) == "shape"
