"""Generate cross-language signing fixtures into shared/protocol/fixtures/es256-python.json.

Run: uv run python scripts/make_fixtures.py   (from shared/python)
The private keys here (controller and PC) are TEST keys committed on purpose; they have no other use.
Since protocol 1.4 the file also carries the Screen view vectors: a PC-signed screen answer the phone must accept, two it
must refuse, one it must call PROTOCOL_INCOMPATIBLE, and the pairing pc_key_mac (rules.pc_key_pinning).
Since protocol 1.5 it also writes shared/protocol/fixtures/screen-dc-cases.json, the data-channel messages both libraries
must accept or refuse. shared/ts/scripts/make-fixtures.ts writes the same file byte for byte.
"""

from __future__ import annotations

import base64
import json
import struct
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dome_protocol import (
    build_screen_answer_payload,
    dumps_compact,
    jwk_from_public_key,
    kid_from_jwk,
    load_registry,
    private_key_from_pem,
    sign_payload,
)
from dome_protocol.digest import (
    challenge_digest,
    command_digest,
    pairing_code_handle,
    pairing_verification_code,
    pc_key_mac,
)
from dome_protocol.screen import offer_digest, sdp_fingerprints
from dome_protocol.versions import parse_version

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "protocol" / "fixtures"
TEST_KEY_PEM = (FIXTURE_DIR / "test-controller-key.pem").read_bytes()
TEST_PC_KEY_PEM = (FIXTURE_DIR / "test-pc-key.pem").read_bytes()

PAIRING_CODE = "7Q3K9-M2XVB-5HTC8-D4RNW"
PAIRING_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
PC_ID = "33333333-3333-4333-8333-333333333333"
ACCOUNT_ID = "11111111-1111-4111-8111-111111111111"
CONTROLLER_ID = "22222222-2222-4222-8222-222222222222"
SCREEN_COMMAND_ID = "9a1c2d3e-4a5b-4c6d-8e7f-901234567890"
SCREEN_SESSION_ID = "U2NyZWVuU2Vzc2lvbjAxQQ"
SCREEN_ISSUED_AT = datetime(2026, 10, 21, 12, 0, 0, tzinfo=UTC)

# Minimal but shaped like what the devices exchange: the phone's receive-only offer (video, audio, data channel) and the
# PC's send-only answer with one DTLS fingerprint. Test data only; the addresses are placeholders.
OFFER_SDP = "\r\n".join(
    [
        "v=0",
        "o=- 4611731400430051336 2 IN IP4 0.0.0.0",
        "s=-",
        "t=0 0",
        "a=group:BUNDLE 0 1 2",
        "a=msid-semantic: WMS",
        "m=video 9 UDP/TLS/RTP/SAVPF 96 97",
        "c=IN IP4 0.0.0.0",
        "a=rtcp:9 IN IP4 0.0.0.0",
        "a=ice-ufrag:ph0n",
        "a=ice-pwd:phonephonephonephonephon",
        "a=fingerprint:sha-256 " + ":".join(f"{b:02X}" for b in bytes(range(1, 33))),
        "a=setup:actpass",
        "a=mid:0",
        "a=recvonly",
        "a=rtcp-mux",
        "a=rtpmap:96 H264/90000",
        "a=fmtp:96 level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f",
        "a=rtpmap:97 VP8/90000",
        "m=audio 9 UDP/TLS/RTP/SAVPF 111",
        "c=IN IP4 0.0.0.0",
        "a=mid:1",
        "a=recvonly",
        "a=rtcp-mux",
        "a=rtpmap:111 opus/48000/2",
        "m=application 9 UDP/DTLS/SCTP webrtc-datachannel",
        "c=IN IP4 0.0.0.0",
        "a=mid:2",
        "a=sctp-port:5000",
        "",
    ]
)
PC_FINGERPRINT = ":".join(f"{b:02X}" for b in bytes(range(201, 233)))
ANSWER_SDP = "\r\n".join(
    [
        "v=0",
        "o=- 3900000000000000001 2 IN IP4 0.0.0.0",
        "s=-",
        "t=0 0",
        "a=group:BUNDLE 0 1 2",
        "a=msid-semantic:WMS *",
        "m=video 9 UDP/TLS/RTP/SAVPF 96",
        "c=IN IP4 0.0.0.0",
        "a=ice-ufrag:pcpc",
        "a=ice-pwd:pcpcpcpcpcpcpcpcpcpcpcpc",
        "a=fingerprint:sha-256 " + PC_FINGERPRINT,
        "a=setup:active",
        "a=mid:0",
        "a=sendonly",
        "a=rtcp-mux",
        "a=rtpmap:96 H264/90000",
        "a=fmtp:96 level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f",
        "m=audio 9 UDP/TLS/RTP/SAVPF 111",
        "c=IN IP4 0.0.0.0",
        "a=fingerprint:sha-256 " + PC_FINGERPRINT,
        "a=mid:1",
        "a=sendonly",
        "a=rtcp-mux",
        "a=rtpmap:111 opus/48000/2",
        "m=application 9 UDP/DTLS/SCTP webrtc-datachannel",
        "c=IN IP4 0.0.0.0",
        "a=fingerprint:sha-256 " + PC_FINGERPRINT,
        "a=mid:2",
        "a=sctp-port:5000",
        "",
    ]
)

# ----- protocol 1.5: the data-channel cases (screen-dc-cases.json) ---------------------------------------------------
# A real 1x1 RGBA PNG built with the standard library (signature, IHDR, IDAT with a stored deflate block, IEND); the TS
# generator builds the same bytes with node:zlib level 0. Both check them against this literal.
CURSOR_PNG_1X1_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAEElEQVR4AQEFAPr/AAAAAP8BBAEAeX5eMQAAAABJRU5ErkJggg=="
# The largest custom bitmap the schema allows: 8192 base64 characters = 6144 PNG bytes (the 1x1 image plus a tEXt chunk).
CURSOR_PNG_MAX_BYTES = 6144


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def cursor_png(total_bytes: int | None = None) -> bytes:
    """A 1x1 RGBA PNG (one opaque black pixel); with ``total_bytes`` a valid tEXt chunk pads it to exactly that size."""
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)  # width 1, height 1, 8-bit, colour type 6 (RGBA)
    idat = zlib.compress(b"\x00\x00\x00\x00\xff", 0)  # filter byte 0, then R G B A
    head = b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr)
    tail = _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")
    if total_bytes is None:
        return head + tail
    keyword = b"Comment\x00"
    pad = total_bytes - len(head) - len(tail) - 12 - len(keyword)
    return head + _png_chunk(b"tEXt", keyword + b"x" * pad) + tail


def _padded(text: str, total_bytes: int) -> str:
    """The same JSON object with spaces before its closing brace so it is exactly ``total_bytes`` UTF-8 bytes."""
    assert text.endswith("}") and text.isascii()
    out = text[:-1] + " " * (total_bytes - len(text)) + "}"
    assert len(out.encode("utf-8")) == total_bytes
    return out


def screen_dc_cases() -> dict[str, Any]:
    small = base64.b64encode(cursor_png()).decode("ascii")
    assert small == CURSOR_PNG_1X1_B64, small
    large = base64.b64encode(cursor_png(CURSOR_PNG_MAX_BYTES)).decode("ascii")
    assert len(large) == 8192
    custom = '{"type":"cursor_shape","kind":"custom","width":1,"height":1,"hotspot_x":0,"hotspot_y":0,"png":"%s"}'
    cursor_pos = '{"type":"cursor_pos","gen":1,"x":32768,"y":16384,"visible":true}'
    p2p, p2c = "phone_to_pc", "pc_to_phone"

    def case(name: str, direction: str, text: str) -> dict[str, str]:
        return {"name": name, "direction": direction, "text": text}

    return {
        "$comment": (
            "Protocol 1.5. Data-channel messages both libraries must accept or refuse with parse_screen_dc_message / "
            "parseScreenDcMessage (rules.screen_content, rules.screen_cursor); every refusal is MALFORMED_MESSAGE. "
            "`direction` is the receiving side's vocabulary, `text` the exact message string (padding is spaces before "
            "the closing brace). Written byte for byte by shared/python/scripts/make_fixtures.py and "
            "shared/ts/scripts/make-fixtures.ts; the PNGs are test images built there (1x1 RGBA, the large one padded "
            "with a tEXt chunk)."
        ),
        "accepted": [
            case("cursor_pos", p2c, cursor_pos),
            case("named cursor_shape ibeam", p2c, '{"type":"cursor_shape","kind":"ibeam"}'),
            case("custom cursor_shape with a real 1x1 RGBA PNG", p2c, custom % small),
            case("state sending with encoder h264 and cursor overlay", p2c, '{"type":"state","state":"sending","encoder":"h264","cursor":"overlay"}'),
            case("custom cursor_shape of 9000 bytes (8192-character png padded with a tEXt chunk)", p2c, _padded(custom % large, 9000)),
        ],
        "rejected": [
            case("custom cursor_shape without png", p2c, '{"type":"cursor_shape","kind":"custom","width":1,"height":1,"hotspot_x":0,"hotspot_y":0}'),
            case("custom cursor_shape whose png contains '-' (base64url, not standard base64)", p2c, custom % (small[:10] + "-" + small[11:])),
            case("custom cursor_shape with a 33-character png (not a valid base64 length)", p2c, custom % ("A" * 32 + "=")),
            case("custom cursor_shape with width 65", p2c, (custom % small).replace('"width":1', '"width":65')),
            case("custom cursor_shape with a visible key", p2c, (custom % small)[:-1] + ',"visible":true}'),
            case("state of 1025 bytes", p2c, _padded('{"type":"state","state":"sending"}', 1025)),
            case("custom cursor_shape of 12289 bytes", p2c, _padded(custom % large, 12289)),
            case("cursor_pos without visible", p2c, '{"type":"cursor_pos","gen":1,"x":32768,"y":16384}'),
            case("hb of 1025 bytes", p2p, _padded('{"type":"hb"}', 1025)),
            case("cursor_pos sent by the phone", p2p, cursor_pos),
        ],
    }


PAYLOADS = [
    '{"type":"command","protocol_version":"1.0","command_id":"6f1c2d3e-4a5b-4c6d-8e7f-901234567890","account_id":"11111111-1111-4111-8111-111111111111","controller_id":"22222222-2222-4222-8222-222222222222","target_pc_id":"33333333-3333-4333-8333-333333333333","action":"youtube.next","params":{},"target":{"browser_instance_id":"bi_test0001","tab_id":42,"tab_token":"dGFiLXRva2VuLTAxQUJDRE","expected_video_id":"dQw4w9WgXcQ"},"issued_at":"2026-10-08T12:00:00.000Z","expires_at":"2026-10-08T12:00:30.000Z","nonce":"AAAAAAAAAAAAAAAAAAAAAA"}',
    '{"type":"command","protocol_version":"1.0","command_id":"7f1c2d3e-4a5b-4c6d-8e7f-901234567890","account_id":"11111111-1111-4111-8111-111111111111","controller_id":"22222222-2222-4222-8222-222222222222","target_pc_id":"33333333-3333-4333-8333-333333333333","action":"windows.set_volume","params":{"value":35},"target":null,"issued_at":"2026-10-08T12:00:00.000Z","expires_at":"2026-10-08T12:00:30.000Z","nonce":"AQEBAQEBAQEBAQEBAQEBAQ"}',
    '{"unicode":"ü→😀","nested":{"a":[1,2,3]}}',
]

CHALLENGE_TEXT = (
    '{"challenge_id":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","command_id":"6f1c2d3e-4a5b-4c6d-8e7f-901234567890",'
    '"controller_id":"22222222-2222-4222-8222-222222222222","pc_id":"33333333-3333-4333-8333-333333333333",'
    '"action":"power.sleep","params":{"countdown_seconds":10},"target":null,'
    '"target_state_digest":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",'
    '"issued_at":"2026-10-08T12:00:00.000Z","expires_at":"2026-10-08T12:01:00.000Z",'
    '"display":{"pc_name":"Büro-PC","action_label":"Sleep","detail":"Sleep in 10 s — Bürö/ok"}}'
)


def main() -> None:
    key = private_key_from_pem(TEST_KEY_PEM)
    jwk = jwk_from_public_key(key.public_key())
    kid = kid_from_jwk(jwk)
    cases = []
    for text in PAYLOADS:
        env = sign_payload(key, text)
        cases.append({"payload": text, "envelope": env.to_dict(), "expect": "valid", "command_digest": command_digest(text)})
    base = cases[0]["envelope"]
    tampered = dict(base, payload=base["payload"].replace('"tab_id":42', '"tab_id":43'))
    cases.append({"payload": tampered["payload"], "envelope": tampered, "expect": "SIGNATURE_INVALID"})
    cases.append({"payload": base["payload"], "envelope": dict(base, alg="none"), "expect": "reject"})
    cases.append({"payload": base["payload"], "envelope": dict(base, extra="x"), "expect": "MALFORMED_MESSAGE"})
    # ----- protocol 1.4: the PC-signed screen answer and the pairing pc_key_mac --------------------------------
    pc_key = private_key_from_pem(TEST_PC_KEY_PEM)
    pc_jwk = jwk_from_public_key(pc_key.public_key())
    pc_kid = kid_from_jwk(pc_jwk)
    registry = load_registry()
    parsed = parse_version(registry.protocol_version)
    assert parsed is not None
    newer_stamp = f"{parsed[0]}.{parsed[1] + 1}"  # one MINOR above this library's version: the phone must refuse it
    answer = build_screen_answer_payload(
        account_id=ACCOUNT_ID,
        pc_id=PC_ID,
        controller_id=CONTROLLER_ID,
        command_id=SCREEN_COMMAND_ID,
        screen_session_id=SCREEN_SESSION_ID,
        offer_sdp=OFFER_SDP,
        answer_sdp=ANSWER_SDP,
        protocol_version=registry.protocol_version,
        now=SCREEN_ISSUED_AT,
    )
    answer_text = dumps_compact(answer)
    good = sign_payload(pc_key, answer_text).to_dict()
    tampered = dict(good, payload=answer_text.replace("a=setup:active", "a=setup:passive"))
    by_controller_key = sign_payload(key, answer_text).to_dict()  # right payload, wrong (unpinned) signer
    newer = sign_payload(pc_key, dumps_compact({**answer, "protocol_version": newer_stamp})).to_dict()
    screen = {
        "$comment": "Protocol 1.4+. The newer-MINOR case is stamped one MINOR above this library's version. The phone verifies these with the PC key pinned at pairing (pc_public_jwk) at `now`.",
        "pc_public_jwk": pc_jwk,
        "pc_kid": pc_kid,
        "account_id": ACCOUNT_ID,
        "controller_id": CONTROLLER_ID,
        "pc_id": PC_ID,
        "command_id": SCREEN_COMMAND_ID,
        "screen_session_id": SCREEN_SESSION_ID,
        "offer_sdp": OFFER_SDP,
        "offer_digest": offer_digest(OFFER_SDP),
        "answer_sdp": ANSWER_SDP,
        "fingerprint": sdp_fingerprints(ANSWER_SDP)[0],
        "now": "2026-10-21T12:00:01.000Z",
        "cases": [
            {"name": "PC-signed answer", "envelope": good, "expect": "valid"},
            {"name": "answer_sdp altered after signing", "envelope": tampered, "expect": "SCREEN_ANSWER_INVALID", "cause": "SIGNATURE_INVALID"},
            {"name": "signed by a key other than the pinned PC key", "envelope": by_controller_key, "expect": "SCREEN_ANSWER_INVALID", "cause": "UNKNOWN_KEY"},
            {"name": "stamped with a MINOR this library does not speak", "envelope": newer, "expect": "PROTOCOL_INCOMPATIBLE"},
        ],
        "pc_key_mac": pc_key_mac(PAIRING_CODE, PAIRING_ID, PC_ID, kid, pc_kid),
    }
    out = {
        "$comment": "Generated by shared/python/scripts/make_fixtures.py. TEST KEYS ONLY.",
        "public_jwk": jwk,
        "kid": kid,
        "cases": cases,
        "digests": {
            "challenge_text": CHALLENGE_TEXT,
            "challenge_digest": challenge_digest(CHALLENGE_TEXT),
            "pairing_code": PAIRING_CODE,
            "pairing_code_handle": pairing_code_handle(PAIRING_CODE),
            "pairing_id": PAIRING_ID,
            "pc_id": PC_ID,
            "pairing_verification_code": pairing_verification_code(PAIRING_CODE, PAIRING_ID, PC_ID, kid),
        },
        "screen": screen,
    }
    (FIXTURE_DIR / "es256-python.json").write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", FIXTURE_DIR / "es256-python.json", "kid", kid)
    (FIXTURE_DIR / "screen-dc-cases.json").write_text(json.dumps(screen_dc_cases(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", FIXTURE_DIR / "screen-dc-cases.json")


if __name__ == "__main__":
    main()
