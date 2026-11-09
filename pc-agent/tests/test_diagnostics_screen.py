"""What the diagnostics bundle says about Screen view round 2a (protocol 1.5, design 2a §3.10): the pointer's counters
(`status.screen.session.media.cursor`: counts and the kind word, never a position or a bitmap, no key says png), the
encoder probe rows (`media.encoders`, with `gated`), `media.encoder_hw`, which screen is watched (`session.surface`),
where the consent came from (`session.consent`), the confinement counter (`status.input.session.confined_moves`) and
`settings.screen_h264_hw`. A `png` key anywhere is redacted by its name (logsetup.SENSITIVE_KEY_PARTS)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from dome_agent.diagnostics import build_bundle
from dome_agent.platform.protocol import Monitor
from dome_agent.settings import Settings

from .conftest import AgentHarness
from .helpers import Controller
from .test_screen_session import live, send_batch, start_input, wait_for

PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABXvMqGgAAAABJRU5ErkJggg=="
CURSOR_KEYS = {
    "mode",
    "pos_sent",
    "pos_unreliable",
    "shape_sent",
    "custom_sent",
    "bitmap_max_chars",
    "bitmap_too_large",
    "dropped_gen",
    "poll_errors",
    "last_kind",
}


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {str(k) for k in value} | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


async def test_the_bundle_carries_the_round_2a_facts_and_never_a_bitmap(
    harness: AgentHarness, controller: Controller
) -> None:
    h = harness
    h.fake.monitors = [Monitor(0, 0, 0, 1920, 1080, True), Monitor(1, 1920, 0, 1280, 1024, False)]
    sid = await start_input(h, controller)
    _res, worker = await live(h, controller, surface=1, cursor="overlay")
    await worker.emit(
        {
            "ev": "stats",
            "relayed_bytes": 0,
            "encoder_hw": False,
            "cursor_pos_sent": 12,
            "cursor_pos_unreliable": 12,
            "cursor_shape_sent": 2,
            "cursor_custom_sent": 1,
            "cursor_bitmap_max_chars": len(PNG_B64),
            "cursor_bitmap_too_large": 0,
            "cursor_dropped_gen": 0,
            "cursor_poll_errors": 0,
            "cursor_last_kind": "custom",
            "png": PNG_B64,  # a worker that misbehaved: never kept
            "x": 812,
            "y": 300,
        }
    )
    await worker.emit(
        {
            "ev": "encoders",
            "probed": [
                {
                    "encoder": "h264_mf hw",
                    "ok": False,
                    "gated": True,
                    "reason": "packet without SPS/PPS",
                    "frames_in": 3,
                    "open_ms": 20,
                    "mft": "NVIDIA H.264 Encoder MFT",
                    "parameter_sets": "",
                },
                {
                    "encoder": "h264_mf",
                    "ok": True,
                    "gated": False,
                    "reason": "",
                    "frames_in": 1,
                    "open_ms": 9,
                    "mft": "H264 Encoder MFT",
                    "parameter_sets": "extradata",
                },
            ],
        }
    )
    h.fake.cursor = (3199, 500)
    await send_batch(h, controller, sid, 1, [{"type": "pointer_move", "dx": 400, "dy": 0}])
    await wait_for(lambda: h.agent.input.summary()["session"]["confined_moves"] == 1)
    bundle = build_bundle(h.settings, h.agent.status(), h.agent.platform)
    session = bundle["status"]["screen"]["session"]
    media = session["media"]
    assert set(media["cursor"]) == CURSOR_KEYS
    assert media["cursor"]["mode"] == "overlay" and media["cursor"]["bitmap_max_chars"] == len(PNG_B64)
    assert media["encoder_hw"] is False
    assert [row["gated"] for row in media["encoders"]] == [True, False]
    assert (
        media["encoders"][0]["reason"] == "packet without SPS/PPS" and media["encoders"][1]["mft"] == "H264 Encoder MFT"
    )
    assert session["surface"] == {"index": 1, "switched_from": None}
    assert session["consent"]["value"] == "not_required" and session["consent"]["inherited"] is False
    assert isinstance(session["consent"]["chain_seconds"], int)
    assert session["cursor"] == {"requested": "overlay", "mode": "overlay"}
    assert bundle["status"]["input"]["session"]["confined_moves"] == 1
    assert bundle["settings"]["screen_h264_hw"] is True
    text = json.dumps(bundle)
    assert PNG_B64[:24] not in text  # the bitmap is screen content
    assert not {k for k in _keys(bundle["status"]) if "png" in k.lower()}
    assert not {"x", "y", "png"} & set(media["cursor"])


def test_a_planted_png_key_is_redacted_by_its_name(settings: Settings) -> None:
    status = {"screen": {"session": {"media": {"cursor": {"pos_sent": 3, "png": PNG_B64, "last_png": "x"}}}}}
    cursor = build_bundle(settings, status)["status"]["screen"]["session"]["media"]["cursor"]
    assert cursor == {"pos_sent": 3, "png": "[redacted]", "last_png": "[redacted]"}


@pytest.mark.parametrize(("value", "expected"), [(None, True), ("1", True), ("0", False)])
def test_settings_say_whether_the_hardware_encoder_may_be_used(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, value: str | None, expected: bool
) -> None:
    if value is None:
        monkeypatch.delenv("DOME_SCREEN_H264_HW", raising=False)
    else:
        monkeypatch.setenv("DOME_SCREEN_H264_HW", value)
    assert build_bundle(settings, {})["settings"]["screen_h264_hw"] is expected
