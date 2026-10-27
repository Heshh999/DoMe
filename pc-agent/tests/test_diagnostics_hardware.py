"""The diagnostics bundle's hardware inventory (capture report 2026-10-26 §7), with the fake platform on Linux.

The first Screen view bundle carried no monitor list, no adapter, no remote-session flag and no Windows build, so the
cause of `capture_failed` could not be told. The bundle now has a ``hardware`` section: the agent's own monitors from
the platform's screen adapter, and (Windows only) DXGI adapters and outputs, the remote-session flag, the build, the GPU
preference and the HDR state. Every item is collected on its own and redacted; the Windows probes are stood in for by
fakes here. Nothing touches DXGI: **not yet verified on a device**.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from dome_agent import diagnostics
from dome_agent.platform.protocol import Monitor
from dome_agent.settings import Settings
from dome_agent.testing.fake_platform import FakeState, build_fake_platform

from .conftest import AgentHarness


def test_collect_hardware_keeps_every_item_on_its_own() -> None:
    def boom() -> Any:
        raise OSError(r"CreateDXGIFactory1 failed at C:\Users\Jane")

    collected = diagnostics.collect_hardware(
        {"a": lambda: {"x": 1}, "broken": boom, "c": lambda: [1, 2], "none": lambda: None}
    )
    assert collected == {"a": {"x": 1}, "broken": {"error": "OSError"}, "c": [1, 2], "none": None}
    assert "Jane" not in json.dumps(collected)  # a probe's error text never travels, only its class


def test_the_bundle_names_the_agents_monitors_and_redacts_the_inventory(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = FakeState()
    state.monitors = [Monitor(0, 0, 0, 2560, 1440, True), Monitor(1, 2560, 0, 1920, 1080, False, 90)]
    platform = build_fake_platform(state)
    bundle = diagnostics.build_bundle(settings, {"screen": {"session": None}}, platform)
    assert bundle["hardware"]["monitors"] == [
        {"index": 0, "left": 0, "top": 0, "width": 2560, "height": 1440, "primary": True, "rotation": 0},
        {"index": 1, "left": 2560, "top": 0, "width": 1920, "height": 1080, "primary": False, "rotation": 90},
    ]
    assert "dxgi_adapters" not in bundle["hardware"]  # Windows only
    assert diagnostics.build_bundle(settings, {})["hardware"] == {}  # no platform (the offline CLI path)
    # the Windows items, stood in for: each on its own, every value through the redaction
    windows = {
        "windows_build": lambda: {"major": 10, "minor": 0, "build": 26100, "version": "10.0.26100"},
        "remote_session": lambda: False,
        "dxgi_adapters": lambda: [
            {
                "index": 0,
                "description": "Intel(R) UHD Graphics",
                "vendor_id": "0x8086",
                "software": False,
                "outputs": [],
            }
        ],
        "gpu_preference": lambda: "high_performance",
        "advanced_colour": lambda: [{"device_name": r"\\.\DISPLAY1", "advanced_colour_enabled": True}],
        "secret_token": lambda: "Bearer " + "A" * 48,
        "broken": lambda: 1 / 0,
    }
    monkeypatch.setattr(diagnostics, "hardware_probes", lambda _platform: windows)
    hardware = diagnostics.build_bundle(settings, {}, platform)["hardware"]
    assert hardware["windows_build"]["build"] == 26100 and hardware["remote_session"] is False
    assert hardware["dxgi_adapters"][0]["description"] == "Intel(R) UHD Graphics"
    assert hardware["gpu_preference"] == "high_performance" and hardware["broken"] == {"error": "ZeroDivisionError"}
    assert hardware["secret_token"] == "[redacted]" and "AAAA" not in json.dumps(hardware)
    state.screen_fail = "PLATFORM_UNSUPPORTED"
    monkeypatch.undo()
    assert diagnostics.build_bundle(settings, {}, platform)["hardware"]["monitors"] == {"error": "ProtocolError"}


def test_hardware_probes_off_windows_are_the_monitors_only() -> None:
    probes = diagnostics.hardware_probes(build_fake_platform(FakeState()))
    assert list(probes) == ["monitors"] and probes["monitors"]()[0]["primary"] is True
    assert diagnostics.hardware_probes(None) == {}


async def test_the_agents_diagnostics_bundle_carries_the_hardware_section(harness: AgentHarness) -> None:
    """`DoMe.exe diagnostics` reaches the running agent over the control channel, which writes the bundle with the
    agent's platform: the monitor list is in it, and so are the input session's latency counters."""
    import asyncio

    from dome_agent import cli

    assert await asyncio.to_thread(cli.main, ["--state-dir", str(harness.settings.state_dir), "diagnostics"]) == 0
    [path] = list((harness.settings.state_dir / "diagnostics").glob("*.json"))
    bundle = json.loads(path.read_text(encoding="utf-8"))
    assert bundle["hardware"]["monitors"] == [
        {"index": 0, "left": 0, "top": 0, "width": 1920, "height": 1080, "primary": True, "rotation": 0}
    ]
    assert bundle["status"]["input"]["last_session"] is None and "age_budget_ms" in bundle["status"]["input"]
