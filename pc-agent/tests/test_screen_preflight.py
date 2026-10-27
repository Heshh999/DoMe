"""The Desktop Duplication pre-flight and the DXGI helpers, on Linux (KNOWN_ISSUES #31, capture report 2026-10-26).

* ``platform.windows.dxgi``: the pure monitor → output mapping over fake adapter rows (``HMONITOR`` first, then the
  desktop rectangle, attached outputs first), the bundle inventory (no handles), ``GUID.from_string`` and the
  ``DXGI_*`` / ``DISPLAYCONFIG_*`` structure sizes against the SDK's (a wrong layout would read garbage on a device).
* ``screen.preflight``: the pure HRESULT → detail mapping, which failures refuse the start and which are handed to the
  worker (the WGC fallback serves them), the log fields, and ``run_preflight`` bounded on the fake platform (a slow
  probe on its own thread, one at a time, its late return logged; a raising probe; no probe).

Nothing here calls DXGI or D3D11: **not yet verified on a device**.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import threading
import uuid
from typing import Any

import pytest
import structlog

from dome_agent.platform.protocol import DuplicationProbe, Monitor
from dome_agent.platform.windows import displayconfig, dxgi
from dome_agent.screen import preflight
from dome_agent.testing.fake_platform import FakeState, build_fake_platform

# ----- dxgi: pure parts ------------------------------------------------------------------------------------------------
INTEL = dxgi.DxgiAdapter(
    0,
    "Intel(R) UHD Graphics",
    0x8086,
    0x9A49,
    False,
    (
        dxgi.DxgiOutput(0, r"\\.\DISPLAY1", 0, 0, 2560, 1440, True, 0, monitor=0x10001),
        dxgi.DxgiOutput(1, r"\\.\DISPLAY4", 0, 0, 1024, 768, False, 0, monitor=0x10004),  # detached: not on the desktop
    ),
)
NVIDIA = dxgi.DxgiAdapter(
    1,
    "NVIDIA GeForce RTX 3060 Laptop GPU",
    0x10DE,
    0x2520,
    False,
    (
        dxgi.DxgiOutput(0, r"\\.\DISPLAY2", 2560, 0, 4480, 1080, True, 0, monitor=0x10002),
        dxgi.DxgiOutput(1, r"\\.\DISPLAY3", -1080, 0, 0, 1920, True, 90, monitor=0x10003),
    ),
)
BASIC = dxgi.DxgiAdapter(2, "Microsoft Basic Render Driver", 0x1414, 0x8C, True)
ADAPTERS = [INTEL, NVIDIA, BASIC]


def test_locate_output_by_rectangle_prefers_attached_outputs_and_knows_the_default_adapter() -> None:
    found = dxgi.locate_output(ADAPTERS, left=0, top=0, width=2560, height=1440)
    assert found is not None and found.adapter is INTEL and found.output.index == 0 and found.on_default_adapter
    other = dxgi.locate_output(ADAPTERS, left=2560, top=0, width=1920, height=1080)
    assert other is not None and other.adapter is NVIDIA and other.output.index == 0 and not other.on_default_adapter
    rotated = dxgi.locate_output(ADAPTERS, left=-1080, top=0, width=1080, height=1920)  # portrait: desktop coordinates
    assert rotated is not None and rotated.output.device_name == r"\\.\DISPLAY3" and rotated.output.rotation == 90
    assert dxgi.locate_output(ADAPTERS, left=0, top=0, width=1920, height=1080) is None  # resized meanwhile
    assert dxgi.locate_output([], left=0, top=0, width=2560, height=1440) is None
    # a detached output's rectangle is a last resort: an attached one with the same rectangle wins
    detached = dxgi.locate_output(ADAPTERS, left=0, top=0, width=1024, height=768)
    assert detached is not None and detached.output.index == 1 and not detached.output.attached


def test_locate_output_by_hmonitor_wins_over_the_rectangle() -> None:
    by_handle = dxgi.locate_output(ADAPTERS, left=0, top=0, width=1, height=1, monitor=0x10002)
    assert by_handle is not None and by_handle.adapter is NVIDIA and by_handle.output.index == 0
    assert dxgi.locate_output(ADAPTERS, left=0, top=0, width=1, height=1, monitor=0x99999) is None
    assert dxgi.locate_output(ADAPTERS, left=0, top=0, width=2560, height=1440, monitor=0) is not None  # 0: no handle


def test_inventory_names_the_hardware_and_carries_no_handles() -> None:
    rows = dxgi.inventory(ADAPTERS)
    assert [r["description"] for r in rows] == [INTEL.description, NVIDIA.description, BASIC.description]
    assert rows[0]["vendor_id"] == "0x8086" and rows[2]["software"] is True and rows[2]["outputs"] == []
    assert rows[1]["outputs"][1] == {
        "index": 1,
        "device_name": r"\\.\DISPLAY3",
        "attached_to_desktop": True,
        "desktop_coordinates": [-1080, 0, 0, 1920],
        "rotation": 90,
    }
    dumped = json.dumps(rows)
    assert "monitor" not in dumped and "0x10001" not in dumped and str(0x10001) not in dumped
    assert (
        "device_id" not in dumped and "0x2520" not in dumped
    )  # the PCI device id: more identity than the bundle needs


def test_parse_gpu_preference_gives_the_value_word_only() -> None:
    assert dxgi.parse_gpu_preference("GpuPreference=0;") == "auto"
    assert dxgi.parse_gpu_preference("GpuPreference=1;") == "power_saving"
    assert dxgi.parse_gpu_preference(" GpuPreference = 2 ;") == "high_performance"
    assert dxgi.parse_gpu_preference("GpuPreference=7;") == "GpuPreference=7"  # a number Windows has not documented
    assert dxgi.parse_gpu_preference("SwapEffectUpgradeEnable=1;") == "unknown"  # no such token: never the raw value
    assert dxgi.parse_gpu_preference("") == "unknown"


def test_guid_from_string_lays_the_bytes_out_as_windows_does() -> None:
    guid = dxgi.GUID.from_string(dxgi.IID_IDXGIFactory1)  # {770aae78-f26f-4dba-a829-253c83d1b387}
    assert (guid.Data1, guid.Data2, guid.Data3) == (0x770AAE78, 0xF26F, 0x4DBA)
    assert bytes(guid.Data4) == bytes.fromhex("a829253c83d1b387")
    assert ctypes.sizeof(dxgi.GUID) == 16
    assert bytes(guid) == uuid.UUID(dxgi.IID_IDXGIFactory1).bytes_le  # the in-memory GUID layout


def test_dxgi_and_displayconfig_structures_have_the_sdk_sizes() -> None:
    assert ctypes.sizeof(dxgi.DXGI_ADAPTER_DESC) == 304  # dxgi.h, x64
    assert ctypes.sizeof(dxgi.DXGI_ADAPTER_DESC1) == 312
    assert ctypes.sizeof(dxgi.DXGI_OUTPUT_DESC) == 96
    assert ctypes.sizeof(displayconfig.DISPLAYCONFIG_PATH_INFO) == 72  # wingdi.h
    assert ctypes.sizeof(displayconfig.DISPLAYCONFIG_MODE_INFO) == 64
    assert ctypes.sizeof(displayconfig.DISPLAYCONFIG_DEVICE_INFO_HEADER) == 20
    assert ctypes.sizeof(displayconfig.DISPLAYCONFIG_GET_ADVANCED_COLOR_INFO) == 32
    assert ctypes.sizeof(displayconfig.DISPLAYCONFIG_SOURCE_DEVICE_NAME) == 84
    # vtable slots: IUnknown (3) + IDXGIObject (4) + each interface's methods in declaration order
    assert (dxgi.VT_FACTORY1_ENUM_ADAPTERS1, dxgi.VT_ADAPTER_ENUM_OUTPUTS, dxgi.VT_ADAPTER1_GET_DESC1) == (12, 7, 10)
    assert (dxgi.VT_OUTPUT_GET_DESC, dxgi.VT_OUTPUT1_DUPLICATE_OUTPUT, dxgi.VT_OUTPUT5_DUPLICATE_OUTPUT1) == (7, 22, 26)


def test_windows_only_entry_points_answer_honestly_off_windows() -> None:
    assert dxgi.remote_session_active() is False and dxgi.gpu_preference("DoMe.exe") is None
    assert displayconfig.advanced_colour() == []
    probe = dxgi.probe_duplication(left=0, top=0, width=2560, height=1440)
    assert probe.hresult is None and probe.reason == "not Windows"
    with pytest.raises(OSError):
        dxgi.enumerate_adapters()


# ----- preflight: HRESULT -> detail ------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("probe", "detail"),
    [
        (DuplicationProbe(None, reason="no DXGI"), None),
        (DuplicationProbe(preflight.S_OK, stage="duplicate", output_index=0), None),
        (DuplicationProbe(preflight.DXGI_ERROR_NOT_CURRENTLY_AVAILABLE, stage="duplicate"), "busy"),
        (DuplicationProbe(preflight.DXGI_ERROR_UNSUPPORTED, stage="duplicate"), "unsupported_display"),
        (DuplicationProbe(preflight.DXGI_ERROR_UNSUPPORTED, remote_session=True, stage="duplicate"), "remote_session"),
        (DuplicationProbe(preflight.DXGI_ERROR_SESSION_DISCONNECTED, stage="duplicate"), "remote_session"),
        (DuplicationProbe(preflight.E_ACCESSDENIED, remote_session=True, stage="duplicate"), "remote_session"),
        (DuplicationProbe(preflight.E_ACCESSDENIED, stage="duplicate"), "driver"),
        (DuplicationProbe(preflight.DXGI_ERROR_NOT_FOUND, stage="duplicate"), "unsupported_display"),
        (DuplicationProbe(preflight.E_INVALIDARG, stage="duplicate"), "driver"),
        (DuplicationProbe(0x887A0005, stage="duplicate"), "driver"),  # DXGI_ERROR_DEVICE_REMOVED: anything else
        (DuplicationProbe(0x887A0004 - 0x100000000, stage="duplicate"), "unsupported_display"),  # a signed HRESULT
        (DuplicationProbe(0x887A0022, stage="device"), "driver"),  # D3D11CreateDevice failed: never "busy"
    ],
)
def test_preflight_detail_maps_ddagrabs_hresults(probe: DuplicationProbe, detail: str | None) -> None:
    assert preflight.preflight_detail(probe) == detail


def test_only_a_missing_d3d11_device_refuses_the_start_and_the_rest_is_handed_to_the_worker() -> None:
    """Desktop Duplication's own refusals are what the Windows.Graphics.Capture fallback exists for (DECISIONS #88,
    #95): the start goes on and the worker skips Desktop Duplication on the pre-flight's verdict."""
    device = DuplicationProbe(0x887A0005, stage="device", adapter="GPU A")
    assert preflight.preflight_refuses(device, preflight.preflight_detail(device)) is True
    for probe in (
        DuplicationProbe(preflight.DXGI_ERROR_NOT_CURRENTLY_AVAILABLE, stage="duplicate", output_index=1),
        DuplicationProbe(preflight.DXGI_ERROR_UNSUPPORTED, stage="duplicate"),
        DuplicationProbe(preflight.E_ACCESSDENIED, remote_session=True, stage="duplicate"),
        DuplicationProbe(preflight.DXGI_ERROR_SESSION_DISCONNECTED, stage="duplicate"),
        DuplicationProbe(0x887A0005, stage="duplicate"),
        DuplicationProbe(preflight.DXGI_ERROR_NOT_FOUND, stage="output"),
    ):
        detail = preflight.preflight_detail(probe)
        assert detail is not None and preflight.preflight_refuses(probe, detail) is False
    assert preflight.preflight_refuses(DuplicationProbe(preflight.S_OK, stage="duplicate"), None) is False
    assert preflight.preflight_refuses(DuplicationProbe(None, reason="no DXGI"), None) is False
    busy = DuplicationProbe(preflight.DXGI_ERROR_NOT_CURRENTLY_AVAILABLE, stage="duplicate", output_index=1)
    assert preflight.preflight_verdict(busy, "busy") == {
        "detail": "busy",
        "hresult": "0x887a0022",
        "hresult_name": "DXGI_ERROR_NOT_CURRENTLY_AVAILABLE",
        "stage": "duplicate",
    }
    assert "adapter" not in preflight.preflight_verdict(busy, "busy")  # the worker needs the verdict, not the inventory


def test_probe_fields_are_content_free_and_name_the_hresult() -> None:
    probe = DuplicationProbe(
        preflight.DXGI_ERROR_UNSUPPORTED, stage="duplicate", adapter="Intel(R) UHD Graphics", output_index=1
    )
    assert preflight.probe_fields(probe) == {
        "hresult": "0x887a0004",
        "hresult_name": "DXGI_ERROR_UNSUPPORTED",
        "stage": "duplicate",
        "adapter": "Intel(R) UHD Graphics",
        "output_index": 1,
        "remote_session": False,
        "reason": None,
    }
    assert preflight.probe_fields(DuplicationProbe(None, reason="not Windows"))["hresult"] is None
    assert preflight.probe_fields(DuplicationProbe(0x80070005))["hresult_name"] == "E_ACCESSDENIED"


async def test_run_preflight_is_bounded_and_never_raises() -> None:
    state = FakeState()
    platform = build_fake_platform(state)
    monitor = Monitor(0, 0, 0, 1920, 1080, True)
    assert await preflight.run_preflight(platform, monitor) == (
        None,
        DuplicationProbe(None, reason="fake platform has no DXGI"),
    )
    state.duplication_probe = DuplicationProbe(preflight.DXGI_ERROR_NOT_CURRENTLY_AVAILABLE, stage="duplicate")
    detail, probe = await preflight.run_preflight(platform, monitor)
    assert detail == "busy" and probe is state.duplication_probe
    state.probe_delay_seconds = 1.0
    with structlog.testing.capture_logs() as logs:
        t0 = asyncio.get_running_loop().time()
        assert await preflight.run_preflight(platform, monitor, budget_seconds=0.1) == (None, None)
        assert asyncio.get_running_loop().time() - t0 < 0.8
        # the probe is still inside the "driver": a second start does not pile another thread onto it
        probes = state.count("duplication_probe")
        assert await preflight.run_preflight(platform, monitor, budget_seconds=0.1) == (None, None)
        assert state.count("duplication_probe") == probes
    assert [e["event"] for e in logs] == [
        "capture pre-flight did not finish in time; starting as before",
        "capture pre-flight skipped: the previous probe has not returned; starting as before",
    ]
    assert logs[0]["budget_ms"] == 100
    assert any(t.name == "dome-capture-preflight" for t in threading.enumerate())  # its own thread, not the executor
    with structlog.testing.capture_logs() as logs:
        await asyncio.sleep(1.1)  # the late probe returns: logged (the bundle shows the hang), the result discarded
    assert [e["event"] for e in logs] == ["capture pre-flight returned after its budget; the result was discarded"]
    assert logs[0]["elapsed_ms"] >= 900
    state.probe_delay_seconds = 0.0
    detail, probe = await preflight.run_preflight(platform, monitor)  # the lock is free again
    assert detail == "busy" and probe is state.duplication_probe

    def broken(_monitor: Monitor) -> Any:
        raise RuntimeError("ctypes")

    platform.screen.duplication_probe = broken  # type: ignore[method-assign]
    with structlog.testing.capture_logs() as logs:
        assert await preflight.run_preflight(platform, monitor) == (None, None)
    assert logs[0]["event"] == "capture pre-flight failed; starting as before" and logs[0]["error"] == "RuntimeError"
    assert await preflight.run_preflight(platform, monitor) == (None, None)  # a raising probe released the lock too
