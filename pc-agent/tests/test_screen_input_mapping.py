"""Screen view's absolute pointer on the PC side (design §8.1, §8.2): the normalised → physical-pixel mapping on
layouts with negative origins and mixed DPI, the Windows INPUT builders for pointer_wheel and f11, monitor ordering,
and the Windows adapters' new methods exercised through their pure parts (SetCursorPos itself is not device-tested)."""

from __future__ import annotations

import pytest

from dome_agent.platform.protocol import NAMED_KEYS, Monitor
from dome_agent.platform.windows import input as w
from dome_agent.platform.windows.screen import order_monitors
from dome_agent.screen.session import map_to_pixels


@pytest.mark.parametrize(
    ("monitor", "x", "y", "expected"),
    [
        (Monitor(0, 0, 0, 1920, 1080, True), 0, 0, (0, 0)),
        (Monitor(0, 0, 0, 1920, 1080, True), 65535, 65535, (1919, 1079)),
        (Monitor(0, 0, 0, 1920, 1080, True), 32768, 32768, (960, 540)),
        (Monitor(1, -2560, -300, 2560, 1440, False), 0, 0, (-2560, -300)),  # a monitor left of and above the primary
        (Monitor(1, -2560, -300, 2560, 1440, False), 65535, 65535, (-1, 1139)),
        (
            Monitor(2, 1920, 0, 3840, 2160, False),
            65535,
            32768,
            (5759, 1080),
        ),  # a 4K (200 % scale) monitor: physical pixels
        (Monitor(0, 0, 0, 2, 2, True), 65535, 0, (1, 0)),
        (Monitor(0, 0, 0, 1920, 1080, True), -5, 70000, (0, 1079)),  # clamped, never off the monitor
    ],
)
def test_normalised_positions_map_to_physical_pixels(
    monitor: Monitor, x: int, y: int, expected: tuple[int, int]
) -> None:
    assert map_to_pixels(monitor, x, y) == expected


def test_order_monitors_puts_the_primary_first_and_numbers_stably() -> None:
    found = [
        ((1920, 0, 4480, 1440), False, 0),
        ((-1080, -200, 0, 1720), False, 90),
        ((0, 0, 1920, 1080), True, 0),
        ((5, 5, 5, 5), False, 0),  # degenerate: dropped
    ]
    monitors = order_monitors(found)
    assert [(m.index, m.left, m.top, m.width, m.height, m.primary, m.rotation) for m in monitors] == [
        (0, 0, 0, 1920, 1080, True, 0),
        (1, -1080, -200, 1080, 1920, False, 90),
        (2, 1920, 0, 2560, 1440, False, 0),
    ]
    assert monitors[0].as_surface() == {"index": 0, "width": 1920, "height": 1080, "primary": True}


def test_wheel_builders_send_raw_units_and_f11_is_a_named_key() -> None:
    assert "f11" in NAMED_KEYS and w.VK_FOR_KEY["f11"] == 0x7A and 0x7A not in w.EXTENDED_KEYS
    both = w.wheel_inputs(-240, 120)
    assert len(both) == 2
    assert both[0].mi.dwFlags == w.MOUSEEVENTF_WHEEL and both[0].mi.mouseData == 120  # raw, not × WHEEL_DELTA
    assert both[1].mi.dwFlags == w.MOUSEEVENTF_HWHEEL and both[1].mi.mouseData == (-240) & 0xFFFFFFFF
    assert w.wheel_inputs(0, 0) == []
    down, up = w.key_inputs("f11")
    assert down.ki.wVk == 0x7A and not down.ki.dwFlags & w.KEYEVENTF_KEYUP and up.ki.dwFlags & w.KEYEVENTF_KEYUP


def test_windows_input_move_to_uses_set_cursor_pos_inside_the_dpi_context() -> None:
    """A scripted user32 double: SetCursorPos is called with the exact pixels while the thread is per-monitor DPI
    aware (v2), the position is read back, and a refusal is an honest error."""
    calls: list[tuple[str, object]] = []

    class Point:
        def __init__(self) -> None:
            self.x, self.y = 0, 0

    class User32:
        def SetThreadDpiAwarenessContext(self, ctx: int) -> int:  # noqa: N802 - Win32 name
            calls.append(("dpi", ctx))
            return 7

        def SetCursorPos(self, x: int, y: int) -> int:  # noqa: N802
            calls.append(("set", (x, y)))
            self._pos = (x, y)
            return 1 if x >= 0 else 0

        def GetCursorPos(self, point: object) -> int:  # noqa: N802
            calls.append(("get", None))
            target = point._obj  # ctypes.byref(POINT)
            target.x, target.y = self._pos
            return 1

    adapter = w.WindowsInput()
    adapter._user32 = User32()  # noqa: SLF001
    adapter.move_to(640, 360)
    kinds = [c[0] for c in calls]
    assert kinds == ["dpi", "set", "get", "dpi"]  # context set, call, read back, context restored
    assert calls[0] == ("dpi", w.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) and calls[1] == ("set", (640, 360))
    assert adapter.cursor_position() == (640, 360)
    from dome_protocol import ProtocolError

    adapter.input_restricted = lambda: False  # type: ignore[method-assign]
    with pytest.raises(ProtocolError) as exc:
        adapter.move_to(-1, 0)
    assert exc.value.code == "INPUT_INJECTION_FAILED"
