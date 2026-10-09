"""The Windows SendInput adapter's OS-independent parts: exact Win32 structure layouts, UTF-16
chunking that never splits a surrogate pair, newline/tab rendering, key tables, INPUT builders and
the CTRL-always-released shortcut sequence. Runs on Linux; the real ``SendInput`` call is only
exercised on a Windows device (README → verification checklist)."""

from __future__ import annotations

import ctypes

import pytest

from dome_agent.platform.protocol import NAMED_KEYS, SHORTCUTS, ForegroundApp
from dome_agent.platform.windows import input as w


def test_structure_layouts_match_win32() -> None:
    assert ctypes.sizeof(w.INPUT) == w.expected_input_size()
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(w.MOUSEINPUT) == 32 and ctypes.sizeof(w.KEYBDINPUT) == 24 and ctypes.sizeof(w.INPUT) == 40
    else:
        assert ctypes.sizeof(w.MOUSEINPUT) == 24 and ctypes.sizeof(w.KEYBDINPUT) == 16 and ctypes.sizeof(w.INPUT) == 28
    assert ctypes.sizeof(w.HARDWAREINPUT) == 8
    assert w.INPUT.u.offset == (8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 4)  # union aligned to ULONG_PTR
    assert w.MOUSEINPUT.dwExtraInfo.offset == 24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 20
    assert w.KEYBDINPUT.wScan.offset == 2 and w.KEYBDINPUT.dwFlags.offset == 4


def test_mouse_builders() -> None:
    (move,) = w.move_inputs(-5, 7)
    assert move.type == w.INPUT_MOUSE and move.mi.dx == -5 and move.mi.dy == 7 and move.mi.dwFlags == w.MOUSEEVENTF_MOVE
    flags = [i.mi.dwFlags for i in w.button_inputs("left", "double_click")]
    assert flags == [w.MOUSEEVENTF_LEFTDOWN, w.MOUSEEVENTF_LEFTUP] * 2
    assert [i.mi.dwFlags for i in w.button_inputs("right", "click")] == [w.MOUSEEVENTF_RIGHTDOWN, w.MOUSEEVENTF_RIGHTUP]
    assert [i.mi.dwFlags for i in w.button_inputs("middle", "down")] == [w.MOUSEEVENTF_MIDDLEDOWN]
    assert [i.mi.dwFlags for i in w.button_inputs("middle", "up")] == [w.MOUSEEVENTF_MIDDLEUP]
    wheel = w.scroll_inputs(2, -3)
    assert [(i.mi.dwFlags, i.mi.mouseData) for i in wheel] == [
        (w.MOUSEEVENTF_WHEEL, (-3 * w.WHEEL_DELTA) & 0xFFFFFFFF),
        (w.MOUSEEVENTF_HWHEEL, 2 * w.WHEEL_DELTA),
    ]
    assert ctypes.c_int32(wheel[0].mi.mouseData).value == -360  # two's complement survives the DWORD field
    assert w.scroll_inputs(0, 0) == []
    with pytest.raises(Exception, match="unknown pointer"):
        w.button_inputs("left", "triple")


def test_utf16_chunks_keep_surrogate_pairs_together() -> None:
    assert w.utf16_chunks("ab") == [[97, 98]]
    emoji = "😀"
    units = w.utf16_chunks(emoji)[0]
    assert len(units) == 2 and 0xD800 <= units[0] <= 0xDBFF and 0xDC00 <= units[1] <= 0xDFFF
    chunks = w.utf16_chunks("a" + emoji + "b", max_units=2)
    assert chunks == [[97], units, [98]]  # the pair is moved whole into the next chunk, never split
    chunks = w.utf16_chunks("xyz" + emoji, max_units=3)
    assert chunks == [[120, 121, 122], units]
    assert w.utf16_chunks("é") == [[0xE9]] and w.utf16_chunks("") == []
    inputs = w.unicode_inputs(units)
    assert [i.ki.dwFlags for i in inputs] == [w.KEYEVENTF_UNICODE, w.KEYEVENTF_UNICODE | w.KEYEVENTF_KEYUP] * 2
    assert [i.ki.wScan for i in inputs] == [units[0], units[0], units[1], units[1]] and all(
        i.ki.wVk == 0 for i in inputs
    )


def test_text_segments_render_newlines_as_enter_and_tabs_as_tab() -> None:
    assert w.text_segments("hi\r\nthere\tx\n") == [
        ("text", "hi"),
        ("key", "enter"),
        ("text", "there"),
        ("key", "tab"),
        ("text", "x"),
        ("key", "enter"),
    ]
    assert w.text_segments("plain") == [("text", "plain")]


def test_key_and_shortcut_tables_cover_the_contract() -> None:
    assert set(w.VK_FOR_KEY) == set(NAMED_KEYS) and set(w.SHORTCUT_KEY) == set(SHORTCUTS)
    down, up = w.key_inputs("arrow_left", scan_for=lambda vk: 0x4B)
    assert down.ki.wVk == w.VK_LEFT and down.ki.dwFlags == w.KEYEVENTF_EXTENDEDKEY and down.ki.wScan == 0x4B
    assert up.ki.dwFlags == w.KEYEVENTF_EXTENDEDKEY | w.KEYEVENTF_KEYUP
    down, up = w.key_inputs("enter")
    assert down.ki.wVk == w.VK_RETURN and down.ki.dwFlags == 0 and up.ki.dwFlags == w.KEYEVENTF_KEYUP
    seq = w.shortcut_inputs("ctrl_l")
    assert [(i.ki.wVk, i.ki.dwFlags) for i in seq] == [
        (w.VK_CONTROL, 0),
        (ord("L"), 0),
        (ord("L"), w.KEYEVENTF_KEYUP),
        (w.VK_CONTROL, w.KEYEVENTF_KEYUP),
    ]  # the modifier is always released last, in the same SendInput call
    with pytest.raises(Exception, match="unknown key"):
        w.key_inputs("f13")
    with pytest.raises(Exception, match="unknown shortcut"):
        w.shortcut_inputs("ctrl_w")


class _ScriptedSend:
    """Visible test double for the ONE OS call (``WindowsInput._send``): records every INPUT list and
    raises for the scripted call numbers, so the recovery logic runs on Linux without user32."""

    def __init__(self, fail_calls: set[int]) -> None:
        self.fail_calls = fail_calls
        self.calls: list[list[tuple[int, int]]] = []

    def __call__(self, inputs: list[w.INPUT], what: str) -> None:
        from dome_protocol import ProtocolError

        self.calls.append([(i.ki.wVk, i.ki.dwFlags) for i in inputs])
        if len(self.calls) in self.fail_calls:
            raise ProtocolError("INPUT_INJECTION_FAILED", f"scripted failure for {what}")


def _adapter_with(send: _ScriptedSend) -> w.WindowsInput:
    adapter = w.WindowsInput()
    adapter._send = send  # type: ignore[method-assign]  # noqa: SLF001
    adapter._scan = lambda vk: 0  # type: ignore[method-assign]  # noqa: SLF001
    return adapter


def test_failed_shortcut_releases_the_letter_and_ctrl() -> None:
    from dome_protocol import ProtocolError

    from dome_agent.platform.protocol import InputHoldError

    send = _ScriptedSend({1})
    with pytest.raises(ProtocolError) as ei:
        _adapter_with(send).shortcut("ctrl_c")
    assert not isinstance(ei.value, InputHoldError) and ei.value.code == "INPUT_INJECTION_FAILED"
    assert send.calls[1] == [(ord("C"), w.KEYEVENTF_KEYUP), (w.VK_CONTROL, w.KEYEVENTF_KEYUP)]
    # the recovery release fails too: a distinguishable error naming what may still be down
    send = _ScriptedSend({1, 2})
    with pytest.raises(InputHoldError) as hold:
        _adapter_with(send).shortcut("ctrl_v")
    assert hold.value.code == "INPUT_INJECTION_FAILED" and hold.value.stuck_keys == {"ctrl", "ctrl_v"}
    # ...and the end-of-session release knows how to lift a shortcut's letter key
    send = _ScriptedSend(set())
    assert _adapter_with(send).release(set(), {"ctrl", "ctrl_v"}) == 2
    assert sorted(send.calls[0]) == sorted([(w.VK_CONTROL, w.KEYEVENTF_KEYUP), (ord("V"), w.KEYEVENTF_KEYUP)])


def test_browser_detection_and_foreground_result_shape() -> None:
    assert w.browser_for_process("Chrome.EXE") == "chrome" and w.browser_for_process("msedge.exe") == "edge"
    assert w.browser_for_process("firefox.exe") == "other" and w.browser_for_process("notepad.exe") is None
    fg = ForegroundApp("notepad.exe", "t" * 300, None, None, "12", 3)
    assert fg.as_result() == {"process_name": "notepad.exe", "window_title": "t" * 200}  # unknown elevation omitted
    assert ForegroundApp("chrome.exe", "", "chrome", True).as_result() == {
        "process_name": "chrome.exe",
        "browser": "chrome",
        "elevated": True,
    }
    from dome_protocol import load_schemas

    load_schemas().validate_def("relay-frames", "foreground_app", fg.as_result())


def test_windows_adapter_is_not_constructible_off_windows_without_dlls() -> None:
    adapter = w.WindowsInput()
    with pytest.raises((AttributeError, OSError)):
        adapter.move(1, 1)  # ctypes.WinDLL does not exist here: the real call is Windows-only


def test_unsupported_platform_input_is_explicit() -> None:
    from dome_protocol import ProtocolError

    from dome_agent.platform.unsupported import build_unsupported_platform

    adapter = build_unsupported_platform().input
    for call in (lambda: adapter.move(1, 1), lambda: adapter.text("x"), lambda: adapter.shortcut("ctrl_a")):
        with pytest.raises(ProtocolError) as ei:
            call()
        assert ei.value.code == "PLATFORM_UNSUPPORTED"
    assert adapter.foreground() is None and adapter.input_restricted() is False
    assert adapter.secure_desktop_active() is False
    assert adapter.release(set(), set()) == 0
