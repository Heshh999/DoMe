"""Manual pointer/keyboard injection with ``user32!SendInput`` (spec §10A-C, design §3.1).

Structures are declared with the exact Win32 layouts for both 32- and 64-bit processes
(``ULONG_PTR`` = ``c_size_t``, so ``INPUT`` is 28 bytes on x86 and 40 bytes on x64 — asserted by
:func:`expected_input_size` and a unit test that runs on every platform). Everything that does not
touch the OS (structure layout, UTF-16 chunking, VK tables, INPUT array builders) lives in module
functions so it can be unit-tested on Linux; only :class:`WindowsInput` loads ``user32``/``kernel32``/
``advapi32`` and is therefore exercised on a Windows device only (README → verification checklist).

* motion:   ``MOUSEEVENTF_MOVE`` with relative ``dx/dy`` in desktop pixels (no ``ABSOLUTE`` flag, no
            clamping: Windows moves across every monitor of the real topology; "Enhance pointer
            precision" acceleration applies to relative motion like it does to a physical mouse)
* buttons:  ``MOUSEEVENTF_{LEFT,RIGHT,MIDDLE}{DOWN,UP}``; a double click is two full clicks in ONE
            ``SendInput`` call so Windows sees them inside its double-click time
* wheel:    ``MOUSEEVENTF_WHEEL`` / ``MOUSEEVENTF_HWHEEL`` with ``mouseData = notches * WHEEL_DELTA``
* text:     ``KEYEVENTF_UNICODE`` key down + key up per UTF-16 code unit; a surrogate pair is never
            split across two ``SendInput`` calls; ``\\n`` / ``\\r\\n`` are rendered as the Enter key and
            ``\\t`` as Tab because applications do not accept U+000A/U+0009 through ``WM_CHAR`` reliably
* keys:     named keys → virtual-key codes (extended keys carry ``KEYEVENTF_EXTENDEDKEY`` and their scan
            code from ``MapVirtualKeyW`` where available)
* shortcut: ``VK_CONTROL`` down, key down/up, ``VK_CONTROL`` up in one call; if the call did not insert
            every event the modifier is released by a separate call, always
* results:  ``SendInput`` returning fewer events than requested → ``INPUT_INJECTION_FAILED``;
            ``GetLastError() == ERROR_ACCESS_DENIED`` while :meth:`WindowsInput.input_restricted` is
            true → ``INPUT_RESTRICTED``. The return value alone is never read as "blocked by UIPI".
* foreground: ``GetForegroundWindow`` → ``GetWindowThreadProcessId`` → ``GetWindowTextW`` + ``psutil``
            process name; browser from the executable name; elevation by comparing the foreground
            process's token integrity level with ours (``OpenProcess`` + ``GetTokenInformation``
            ``TokenIntegrityLevel``) — unknown (None) when the process cannot be opened
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any

from dome_protocol import ProtocolError

from ..protocol import NAMED_KEYS, POINTER_BUTTONS, SHORTCUTS, BrowserKind, ForegroundApp

# ----- Win32 constants -------------------------------------------------------------------------------

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
INPUT_HARDWARE = 2

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
WHEEL_DELTA = 120

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

VK_BACK = 0x08
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_CONTROL = 0x11
VK_ESCAPE = 0x1B
VK_SPACE = 0x20
VK_PRIOR = 0x21
VK_NEXT = 0x22
VK_END = 0x23
VK_HOME = 0x24
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_DELETE = 0x2E

ERROR_ACCESS_DENIED = 5
MAPVK_VK_TO_VSC = 0
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_INTEGRITY_LEVEL = 25
SECURITY_MANDATORY_MEDIUM_RID = 0x2000
SECURITY_MANDATORY_HIGH_RID = 0x3000
UOI_NAME = 2
DESKTOP_READOBJECTS = 0x0001
MAX_TITLE_CHARS = 256
MAX_EVENTS_PER_CALL = 64  # bounded SendInput arrays; a surrogate pair is never split across calls

VK_FOR_KEY: dict[str, int] = {
    "enter": VK_RETURN,
    "tab": VK_TAB,
    "escape": VK_ESCAPE,
    "backspace": VK_BACK,
    "delete": VK_DELETE,
    "space": VK_SPACE,
    "arrow_up": VK_UP,
    "arrow_down": VK_DOWN,
    "arrow_left": VK_LEFT,
    "arrow_right": VK_RIGHT,
    "home": VK_HOME,
    "end": VK_END,
    "page_up": VK_PRIOR,
    "page_down": VK_NEXT,
}
EXTENDED_KEYS = frozenset(
    {VK_DELETE, VK_UP, VK_DOWN, VK_LEFT, VK_RIGHT, VK_HOME, VK_END, VK_PRIOR, VK_NEXT}
)  # keys whose scan code lives on the extended (E0) page
SHORTCUT_KEY: dict[str, int] = {
    "ctrl_a": ord("A"),
    "ctrl_c": ord("C"),
    "ctrl_v": ord("V"),
    "ctrl_z": ord("Z"),
    "ctrl_l": ord("L"),
}
BUTTON_FLAGS: dict[str, tuple[int, int]] = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}
BROWSER_PROCESSES: dict[str, BrowserKind] = {
    "chrome.exe": "chrome",
    "msedge.exe": "edge",
    "firefox.exe": "other",
    "brave.exe": "other",
    "opera.exe": "other",
    "vivaldi.exe": "other",
}
assert set(VK_FOR_KEY) == set(NAMED_KEYS)
assert set(SHORTCUT_KEY) == set(SHORTCUTS)
assert set(BUTTON_FLAGS) == set(POINTER_BUTTONS)

# Fixed-width aliases: identical to the Win32 typedefs on Windows and, unlike ``wintypes.LONG``
# (= ``c_long``, 8 bytes on LP64 Linux), they reproduce the exact Win32 layout on every build host so
# the layout test can run in CI.
LONG = ctypes.c_int32
DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
ULONG_PTR = ctypes.c_size_t  # unsigned, pointer-sized on both 32- and 64-bit Windows


# ----- structures (exact Win32 layouts) ---------------------------------------------------------------


class MOUSEINPUT(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [
        ("dx", LONG),
        ("dy", LONG),
        ("mouseData", DWORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [
        ("wVk", WORD),
        ("wScan", WORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):  # noqa: N801 - Win32 name
    _anonymous_ = ("u",)
    _fields_ = [("type", DWORD), ("u", _INPUTUNION)]


def expected_input_size() -> int:
    """``sizeof(INPUT)`` per the Win32 ABI: 28 bytes in a 32-bit process, 40 bytes in a 64-bit one."""
    return 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28


def _dword(value: int) -> int:
    """Two's-complement DWORD for signed wheel deltas (``mouseData`` is unsigned in the structure)."""
    return value & 0xFFFFFFFF


# ----- INPUT builders (pure; unit-tested on every platform) -----------------------------------------


def mouse_input(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_MOUSE
    inp.mi = MOUSEINPUT(dx=int(dx), dy=int(dy), mouseData=_dword(int(data)), dwFlags=flags, time=0, dwExtraInfo=0)
    return inp


def key_input(vk: int, *, up: bool, scan: int = 0, extended: bool = False) -> INPUT:
    flags = (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if extended else 0)
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return inp


def unicode_input(code_unit: int, *, up: bool) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki = KEYBDINPUT(
        wVk=0, wScan=code_unit, dwFlags=KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0), time=0, dwExtraInfo=0
    )
    return inp


def move_inputs(dx: int, dy: int) -> list[INPUT]:
    return [mouse_input(MOUSEEVENTF_MOVE, dx=dx, dy=dy)]


def button_inputs(button: str, action: str) -> list[INPUT]:
    if button not in BUTTON_FLAGS:
        raise ProtocolError("MALFORMED_MESSAGE", f"unknown pointer button {button!r}")
    down, up = BUTTON_FLAGS[button]
    if action == "down":
        return [mouse_input(down)]
    if action == "up":
        return [mouse_input(up)]
    if action == "click":
        return [mouse_input(down), mouse_input(up)]
    if action == "double_click":
        return [mouse_input(down), mouse_input(up), mouse_input(down), mouse_input(up)]
    raise ProtocolError("MALFORMED_MESSAGE", f"unknown pointer action {action!r}")


def scroll_inputs(dx: int, dy: int) -> list[INPUT]:
    out: list[INPUT] = []
    if dy:
        out.append(mouse_input(MOUSEEVENTF_WHEEL, data=int(dy) * WHEEL_DELTA))
    if dx:
        out.append(mouse_input(MOUSEEVENTF_HWHEEL, data=int(dx) * WHEEL_DELTA))
    return out


def utf16_chunks(text: str, max_units: int = MAX_EVENTS_PER_CALL // 2) -> list[list[int]]:
    """UTF-16 code units of ``text`` in chunks of at most ``max_units`` so that a surrogate pair is
    never split between two chunks (each code unit becomes a key-down and a key-up event)."""
    raw = text.encode("utf-16-le")
    units = [int.from_bytes(raw[i : i + 2], "little") for i in range(0, len(raw), 2)]
    chunks: list[list[int]] = []
    current: list[int] = []
    i = 0
    while i < len(units):
        unit = units[i]
        pair = 0xD800 <= unit <= 0xDBFF and i + 1 < len(units) and 0xDC00 <= units[i + 1] <= 0xDFFF
        size = 2 if pair else 1
        if current and len(current) + size > max_units:
            chunks.append(current)
            current = []
        current.extend(units[i : i + size])
        i += size
    if current:
        chunks.append(current)
    return chunks


def text_segments(text: str) -> list[tuple[str, str]]:
    """Split literal text into ``("text", run)`` and ``("key", "enter"|"tab")`` segments in order."""
    segments: list[tuple[str, str]] = []
    run = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "\r\n\t":
            if run:
                segments.append(("text", run))
                run = ""
            if ch == "\r" and i + 1 < len(text) and text[i + 1] == "\n":
                i += 1
            segments.append(("key", "tab" if ch == "\t" else "enter"))
        else:
            run += ch
        i += 1
    if run:
        segments.append(("text", run))
    return segments


def unicode_inputs(units: list[int]) -> list[INPUT]:
    out: list[INPUT] = []
    for unit in units:
        out.append(unicode_input(unit, up=False))
        out.append(unicode_input(unit, up=True))
    return out


def key_inputs(key: str, scan_for: Any = None) -> list[INPUT]:
    vk = VK_FOR_KEY.get(key)
    if vk is None:
        raise ProtocolError("MALFORMED_MESSAGE", f"unknown key {key!r}")
    extended = vk in EXTENDED_KEYS
    scan = int(scan_for(vk)) if scan_for is not None else 0
    return [key_input(vk, up=False, scan=scan, extended=extended), key_input(vk, up=True, scan=scan, extended=extended)]


def shortcut_inputs(name: str, scan_for: Any = None) -> list[INPUT]:
    vk = SHORTCUT_KEY.get(name)
    if vk is None:
        raise ProtocolError("MALFORMED_MESSAGE", f"unknown shortcut {name!r}")
    ctrl_scan = int(scan_for(VK_CONTROL)) if scan_for is not None else 0
    scan = int(scan_for(vk)) if scan_for is not None else 0
    return [
        key_input(VK_CONTROL, up=False, scan=ctrl_scan),
        key_input(vk, up=False, scan=scan),
        key_input(vk, up=True, scan=scan),
        key_input(VK_CONTROL, up=True, scan=ctrl_scan),
    ]


def browser_for_process(process_name: str) -> BrowserKind | None:
    return BROWSER_PROCESSES.get(process_name.lower())


# ----- the adapter ----------------------------------------------------------------------------------


class WindowsInput:
    """Real ``SendInput`` adapter. Constructed only on ``win32``; the DLLs are loaded lazily."""

    def __init__(self) -> None:
        self._user32: Any = None
        self._kernel32: Any = None
        self._advapi32: Any = None
        self._own_integrity: int | None = None

    # -- DLL access --
    def _libs(self) -> tuple[Any, Any, Any]:
        if self._user32 is None:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
            user32.SendInput.restype = wintypes.UINT
            user32.GetForegroundWindow.restype = wintypes.HWND
            user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
            user32.GetWindowThreadProcessId.restype = wintypes.DWORD
            user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
            user32.GetWindowTextW.restype = ctypes.c_int
            user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
            user32.MapVirtualKeyW.restype = wintypes.UINT
            user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            user32.OpenInputDesktop.restype = wintypes.HANDLE
            user32.CloseDesktop.argtypes = [wintypes.HANDLE]
            user32.GetUserObjectInformationW.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
            ]
            user32.GetUserObjectInformationW.restype = wintypes.BOOL
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
            advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
            advapi32.OpenProcessToken.restype = wintypes.BOOL
            advapi32.GetTokenInformation.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD),
            ]
            advapi32.GetTokenInformation.restype = wintypes.BOOL
            advapi32.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
            advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
            advapi32.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
            advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
            self._user32, self._kernel32, self._advapi32 = user32, kernel32, advapi32
        return self._user32, self._kernel32, self._advapi32

    def _scan(self, vk: int) -> int:
        user32, _k, _a = self._libs()
        try:
            return int(user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)) & 0xFFFF
        except OSError:
            return 0

    # -- the one OS call --
    def _send(self, inputs: list[INPUT], what: str) -> None:
        if not inputs:
            return
        user32, _k, _a = self._libs()
        array = (INPUT * len(inputs))(*inputs)
        ctypes.set_last_error(0)
        inserted = int(user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT)))
        if inserted == len(inputs):
            return
        err = ctypes.get_last_error()
        if err == ERROR_ACCESS_DENIED and self.input_restricted():
            raise ProtocolError(
                "INPUT_RESTRICTED",
                f"Windows refused {what}: a protected screen (lock, sign-in or an administrator prompt) is in front.",
            )
        raise ProtocolError(
            "INPUT_INJECTION_FAILED",
            f"Windows accepted {inserted} of {len(inputs)} input events for {what} (error {err}).",
        )

    # -- InputAdapter --
    def move(self, dx: int, dy: int) -> None:
        if dx == 0 and dy == 0:
            return
        self._send(move_inputs(dx, dy), "pointer motion")

    def button(self, button: str, action: str) -> None:
        self._send(button_inputs(button, action), f"{button} button {action}")

    def scroll(self, dx: int, dy: int) -> None:
        self._send(scroll_inputs(dx, dy), "scrolling")

    def text(self, text: str) -> None:
        for kind, value in text_segments(text):
            if kind == "key":
                self.key(value)
                continue
            for chunk in utf16_chunks(value):
                self._send(unicode_inputs(chunk), "text entry")

    def key(self, key: str) -> None:
        self._send(key_inputs(key, self._scan), f"the {key} key")

    def shortcut(self, name: str) -> None:
        try:
            self._send(shortcut_inputs(name, self._scan), f"the {name} shortcut")
        except ProtocolError:
            # The modifier must never stay down: release CTRL on its own, whatever happened above.
            try:
                self._send([key_input(VK_CONTROL, up=True, scan=self._scan(VK_CONTROL))], "releasing CTRL")
            except ProtocolError:
                pass
            raise

    def release(self, buttons: set[str], keys: set[str]) -> int:
        inputs: list[INPUT] = []
        for button in sorted(buttons):
            if button in BUTTON_FLAGS:
                inputs.append(mouse_input(BUTTON_FLAGS[button][1]))
        for key in sorted(keys):
            if key == "ctrl":
                inputs.append(key_input(VK_CONTROL, up=True, scan=self._scan(VK_CONTROL)))
            elif key in VK_FOR_KEY:
                vk = VK_FOR_KEY[key]
                inputs.append(key_input(vk, up=True, scan=self._scan(vk), extended=vk in EXTENDED_KEYS))
        if not inputs:
            return 0
        self._send(inputs, "releasing held input")
        return len(inputs)

    def foreground(self) -> ForegroundApp | None:
        user32, _k, _a = self._libs()
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        buffer = ctypes.create_unicode_buffer(MAX_TITLE_CHARS)
        length = user32.GetWindowTextW(hwnd, buffer, MAX_TITLE_CHARS)
        title = buffer.value[:200] if length > 0 else ""
        process_name = self._process_name(int(pid.value))
        browser = browser_for_process(process_name)
        return ForegroundApp(
            process_name=process_name[:64],
            window_title=title,
            browser=browser,
            elevated=self._elevated(int(pid.value)),
            window_id=str(int(hwnd)),
            pid=int(pid.value),
        )

    def input_restricted(self) -> bool:
        return self._secure_desktop_active() or bool(self._foreground_elevated())

    # -- helpers --
    @staticmethod
    def _process_name(pid: int) -> str:
        if pid <= 0:
            return "unknown"
        try:
            import psutil

            return str(psutil.Process(pid).name())
        except Exception:  # noqa: BLE001 - access denied / gone: report unknown, never guess
            return "unknown"

    def _secure_desktop_active(self) -> bool:
        """The input desktop cannot be opened (secure desktop / lock screen) or is not ``Default``."""
        user32, _k, _a = self._libs()
        handle = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS)
        if not handle:
            return True
        try:
            buffer = ctypes.create_unicode_buffer(64)
            needed = wintypes.DWORD(0)
            ok = user32.GetUserObjectInformationW(handle, UOI_NAME, buffer, ctypes.sizeof(buffer), ctypes.byref(needed))
            if not ok:
                return False
            return buffer.value.lower() != "default"
        finally:
            user32.CloseDesktop(handle)

    def _foreground_elevated(self) -> bool | None:
        user32, _k, _a = self._libs()
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return self._elevated(int(pid.value))

    def _integrity_of(self, process_handle: Any) -> int | None:
        _u, kernel32, advapi32 = self._libs()
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(process_handle, TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            needed = wintypes.DWORD(0)
            advapi32.GetTokenInformation(token, TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(needed))
            if needed.value == 0:
                return None
            buffer = ctypes.create_string_buffer(needed.value)
            if not advapi32.GetTokenInformation(
                token, TOKEN_INTEGRITY_LEVEL, buffer, needed.value, ctypes.byref(needed)
            ):
                return None
            # TOKEN_MANDATORY_LABEL { SID_AND_ATTRIBUTES Label { PSID Sid; DWORD Attributes; } }
            sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p)).contents.value
            if not sid:
                return None
            count = advapi32.GetSidSubAuthorityCount(sid).contents.value
            return int(advapi32.GetSidSubAuthority(sid, count - 1).contents.value)
        finally:
            kernel32.CloseHandle(token)

    def _own_integrity_level(self) -> int | None:
        if self._own_integrity is None:
            _u, kernel32, _a = self._libs()
            self._own_integrity = self._integrity_of(kernel32.GetCurrentProcess())
        return self._own_integrity

    def _elevated(self, pid: int) -> bool | None:
        """True when the process runs at a higher integrity level than we do; None when undeterminable."""
        if pid <= 0:
            return None
        _u, kernel32, _a = self._libs()
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None  # access denied is typical for elevated processes, but it is not proof: report unknown
        try:
            theirs = self._integrity_of(handle)
        finally:
            kernel32.CloseHandle(handle)
        ours = self._own_integrity_level()
        if theirs is None or ours is None:
            return None
        return theirs > ours
