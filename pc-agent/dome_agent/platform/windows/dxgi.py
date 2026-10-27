"""DXGI for Screen view: which graphics adapter drives which monitor, and whether Desktop Duplication would start.

FFmpeg's ``ddagrab`` (n8.1.3, ``libavfilter/vsrc_ddagrab.c``) creates its D3D11 device with
``D3D11CreateDevice(NULL, D3D_DRIVER_TYPE_HARDWARE, ...)`` — the DEFAULT adapter, the first one
``IDXGIFactory1::EnumAdapters1`` lists — and then duplicates ``IDXGIAdapter::EnumOutputs(output_idx)`` of THAT adapter.
``output_idx`` is therefore an index among the default adapter's outputs, not DoMe's primary-first monitor index
(``screen.py``), and a monitor driven by another adapter (a hybrid-GPU laptop's external screen) is not reachable
through ``ddagrab`` at all (no adapter option; ``lavfi`` cannot hand it a device). This module answers both questions:

* :func:`enumerate_adapters` → :class:`DxgiAdapter` rows with their :class:`DxgiOutput` rows (``CreateDXGIFactory1`` →
  ``EnumAdapters1`` → ``GetDesc1`` → ``EnumOutputs`` → ``GetDesc``); :func:`locate_output` (pure, unit-tested on Linux)
  finds the output whose ``HMONITOR`` or desktop rectangle is the streamed monitor's.
* :func:`probe_duplication` does exactly what ``ddagrab`` does, without a frame: the same device creation, the device's
  own adapter, the output matching the monitor, ``IDXGIOutput5::DuplicateOutput1`` with the BGRA format list under
  per-monitor-v2 DPI awareness (``IDXGIOutput1::DuplicateOutput`` before Windows 10 1703), and releases the duplication
  at once. The raw HRESULT is what ``ddagrab`` never prints; ``screen.preflight`` maps it to the contract's detail.
* :func:`remote_session_active` (``GetSystemMetrics(SM_REMOTESESSION)``) and :func:`gpu_preference` (the per-app GPU
  choice under ``HKCU\\Software\\Microsoft\\DirectX\\UserGpuPreferences``, which redirects the default adapter) feed the
  classification and the diagnostics bundle; :func:`inventory` renders the adapters for the bundle without handles.

COM is driven through ``ctypes`` by vtable index (``_method``); every function declares ``argtypes``/``restype`` and every
interface pointer is released. Each ctypes entry point is failure-tolerant: callers get ``None`` / an empty list or a
:class:`DuplicationProbe` with ``hresult=None`` rather than an exception. Windows only; **not yet verified on a device**.
"""

from __future__ import annotations

import contextlib
import ctypes
import sys
import uuid
from collections.abc import Callable, Iterable, Iterator
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

from ..protocol import DuplicationProbe

# Fixed-width field types: on Linux, ctypes makes the wintypes DWORD / LONG / BOOL 8 bytes and c_wchar 4 bytes, which
# would make these layouts differ from Windows' (and the Linux size tests meaningless); WCHAR arrays are UTF-16 units.
UINT = ctypes.c_uint32
LONG = ctypes.c_int32
BOOL = ctypes.c_int32
WCHAR = ctypes.c_uint16
HRESULT = ctypes.c_int32  # signed on the wire; compared as unsigned (hresult & 0xFFFFFFFF)

S_OK = 0x00000000
E_NOINTERFACE = 0x80004002
E_ACCESSDENIED = 0x80070005
E_INVALIDARG = 0x80070057
DXGI_ERROR_INVALID_CALL = 0x887A0001
DXGI_ERROR_NOT_FOUND = 0x887A0002
DXGI_ERROR_UNSUPPORTED = 0x887A0004
DXGI_ERROR_DEVICE_REMOVED = 0x887A0005
DXGI_ERROR_NOT_CURRENTLY_AVAILABLE = 0x887A0022
DXGI_ERROR_ACCESS_LOST = 0x887A0026
DXGI_ERROR_WAIT_TIMEOUT = 0x887A0027
DXGI_ERROR_SESSION_DISCONNECTED = 0x887A0028
DXGI_ADAPTER_FLAG_REMOTE = 1
DXGI_ADAPTER_FLAG_SOFTWARE = 2
DXGI_FORMAT_B8G8R8A8_UNORM = 87
D3D_DRIVER_TYPE_HARDWARE = 1
D3D11_SDK_VERSION = 7
D3D11_CREATE_DEVICE_VIDEO_SUPPORT = 0x800  # hwcontext_d3d11va.c's creationFlags
SM_REMOTESESSION = 0x1000
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
# DXGI_MODE_ROTATION → degrees (UNSPECIFIED = 0 and IDENTITY = 1 are 0°)
_ROTATION_DEGREES = {2: 90, 3: 180, 4: 270}
GPU_PREFERENCES_KEY = r"Software\Microsoft\DirectX\UserGpuPreferences"
GPU_PREFERENCE_WORDS = {"0": "auto", "1": "power_saving", "2": "high_performance"}

IID_IDXGIFactory1 = "770aae78-f26f-4dba-a829-253c83d1b387"
IID_IDXGIDevice = "54ec77fa-1377-44e6-8c32-88fd5f44c84c"
IID_IDXGIOutput1 = "00cddea8-939b-4b83-a340-a685226666cc"
IID_IDXGIOutput5 = "80a07424-ab52-42eb-833c-0c42fd282d98"
# COM vtable slots (IUnknown 0-2; IDXGIObject 3-6; then each interface appends to its base in declaration order)
VT_QUERY_INTERFACE = 0
VT_RELEASE = 2
VT_FACTORY1_ENUM_ADAPTERS1 = 12  # IDXGIFactory (7-11) + IDXGIFactory1
VT_ADAPTER_ENUM_OUTPUTS = 7
VT_ADAPTER_GET_DESC = 8
VT_ADAPTER1_GET_DESC1 = 10  # IDXGIAdapter (7-9) + IDXGIAdapter1
VT_OUTPUT_GET_DESC = 7
VT_OUTPUT1_DUPLICATE_OUTPUT = 22  # IDXGIOutput has 12 methods (7-18); IDXGIOutput1 adds 4 (19-22)
VT_OUTPUT5_DUPLICATE_OUTPUT1 = 26  # IDXGIOutput2 (23), 3 (24), 4 (25), 5
VT_DEVICE_GET_ADAPTER = 7


class GUID(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, text: str) -> GUID:
        value = uuid.UUID(text)
        guid = cls()
        guid.Data1 = value.time_low
        guid.Data2 = value.time_mid
        guid.Data3 = value.time_hi_version
        guid.Data4 = (ctypes.c_ubyte * 8)(*value.bytes[8:])
        return guid


class LUID(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [("LowPart", UINT), ("HighPart", LONG)]


class RECT(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG)]


class DXGI_ADAPTER_DESC(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [
        ("Description", WCHAR * 128),
        ("VendorId", UINT),
        ("DeviceId", UINT),
        ("SubSysId", UINT),
        ("Revision", UINT),
        ("DedicatedVideoMemory", ctypes.c_size_t),
        ("DedicatedSystemMemory", ctypes.c_size_t),
        ("SharedSystemMemory", ctypes.c_size_t),
        ("AdapterLuid", LUID),
    ]


class DXGI_ADAPTER_DESC1(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [*DXGI_ADAPTER_DESC._fields_, ("Flags", UINT)]


class DXGI_OUTPUT_DESC(ctypes.Structure):  # noqa: N801 - Win32 name
    _fields_ = [
        ("DeviceName", WCHAR * 32),
        ("DesktopCoordinates", RECT),
        ("AttachedToDesktop", BOOL),
        ("Rotation", UINT),
        ("Monitor", wintypes.HMONITOR),
    ]


def wide(field: Any) -> str:
    """A ``WCHAR[]`` array (UTF-16 code units) as text, up to its NUL."""
    return bytes(field).decode("utf-16-le", "replace").split("\x00", 1)[0]


@dataclass(frozen=True, slots=True)
class DxgiOutput:
    """One output of one adapter (``DXGI_OUTPUT_DESC``). ``index`` is its index on its adapter: ``ddagrab``'s
    ``output_idx`` when the adapter is the default one. ``monitor`` is the ``HMONITOR`` as an int, for matching only:
    it never reaches a log or the bundle (:func:`inventory` leaves it out)."""

    index: int
    device_name: str  # \\.\DISPLAY1
    left: int
    top: int
    right: int
    bottom: int
    attached: bool
    rotation: int  # degrees
    monitor: int = 0

    @property
    def rect(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.right, self.bottom)


@dataclass(frozen=True, slots=True)
class DxgiAdapter:
    """One adapter (``DXGI_ADAPTER_DESC1``), ``index`` in ``EnumAdapters1`` order: 0 is the default adapter."""

    index: int
    description: str
    vendor_id: int
    device_id: int
    software: bool  # DXGI_ADAPTER_FLAG_SOFTWARE (the Basic Render Driver)
    outputs: tuple[DxgiOutput, ...] = ()


@dataclass(frozen=True, slots=True)
class OutputLocation:
    adapter: DxgiAdapter
    output: DxgiOutput

    @property
    def on_default_adapter(self) -> bool:
        return self.adapter.index == 0


def locate_output(
    adapters: Iterable[DxgiAdapter], *, left: int, top: int, width: int, height: int, monitor: int | None = None
) -> OutputLocation | None:
    """Pure: the output that shows the monitor at ``(left, top, width, height)`` (physical pixels, virtual-desktop
    coordinates, as ``DXGI_OUTPUT_DESC.DesktopCoordinates`` and ``MONITORINFO.rcMonitor`` both are), or the one whose
    ``HMONITOR`` is ``monitor`` when that is given. Attached outputs first; None when no adapter shows it."""
    rect = (left, top, left + width, top + height)
    rows = [(adapter, output) for adapter in adapters for output in adapter.outputs]
    for attached_only in (True, False):
        for adapter, output in rows:
            if attached_only and not output.attached:
                continue
            if monitor and output.monitor == monitor:
                return OutputLocation(adapter, output)
            if output.rect == rect:
                return OutputLocation(adapter, output)
    return None


def inventory(adapters: Iterable[DxgiAdapter]) -> list[dict[str, Any]]:
    """The adapters for the diagnostics bundle: description, vendor and software flag per adapter (what the capture
    report asked for; the PCI device id stays out), device name and geometry per output; no handles, no memory sizes."""
    return [
        {
            "index": adapter.index,
            "description": adapter.description,
            "vendor_id": f"0x{adapter.vendor_id:04x}",
            "software": adapter.software,
            "outputs": [
                {
                    "index": output.index,
                    "device_name": output.device_name,
                    "attached_to_desktop": output.attached,
                    "desktop_coordinates": list(output.rect),
                    "rotation": output.rotation,
                }
                for output in adapter.outputs
            ],
        }
        for adapter in adapters
    ]


# ----- ctypes / COM (Windows only) -----------------------------------------------------------------------------------
def _unsigned(hresult: int) -> int:
    return int(hresult) & 0xFFFFFFFF


def _failed(hresult: int) -> bool:
    return bool(_unsigned(hresult) & 0x80000000)


def _method(obj: Any, index: int, restype: Any, *argtypes: Any) -> Any:
    """The COM method at vtable slot ``index`` of interface pointer ``obj`` (a ``c_void_p``), bound to it."""
    vtable = ctypes.cast(ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    prototype = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    function = prototype(vtable[index])

    def call(*args: Any) -> Any:
        return function(obj, *args)

    return call


def _release(obj: Any) -> None:
    if obj:
        with contextlib.suppress(Exception):
            _method(obj, VT_RELEASE, ctypes.c_ulong)()


def _query_interface(obj: Any, iid: str) -> Any:
    """``QueryInterface``: the new interface pointer, or None (``E_NOINTERFACE`` on an older Windows)."""
    out = ctypes.c_void_p()
    hr = _method(obj, VT_QUERY_INTERFACE, HRESULT, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
        ctypes.byref(GUID.from_string(iid)), ctypes.byref(out)
    )
    return out if not _failed(hr) and out.value else None


def _output_row(index: int, output: Any) -> DxgiOutput | None:
    desc = DXGI_OUTPUT_DESC()
    hr = _method(output, VT_OUTPUT_GET_DESC, HRESULT, ctypes.POINTER(DXGI_OUTPUT_DESC))(ctypes.byref(desc))
    if _failed(hr):
        return None
    r = desc.DesktopCoordinates
    return DxgiOutput(
        index=index,
        device_name=wide(desc.DeviceName),
        left=int(r.left),
        top=int(r.top),
        right=int(r.right),
        bottom=int(r.bottom),
        attached=bool(desc.AttachedToDesktop),
        rotation=_ROTATION_DEGREES.get(int(desc.Rotation), 0),
        monitor=int(desc.Monitor or 0),
    )


def _outputs_of(adapter: Any, visit: Callable[[int, Any, DxgiOutput], bool] | None = None) -> list[DxgiOutput]:
    """Every output of ``adapter`` (``EnumOutputs`` until ``DXGI_ERROR_NOT_FOUND``). ``visit(index, output, row)``
    may claim the interface pointer (returning True keeps it alive for the caller; it is released otherwise)."""
    rows: list[DxgiOutput] = []
    enum_outputs = _method(adapter, VT_ADAPTER_ENUM_OUTPUTS, HRESULT, UINT, ctypes.POINTER(ctypes.c_void_p))
    for index in range(64):
        output = ctypes.c_void_p()
        if _failed(enum_outputs(index, ctypes.byref(output))) or not output.value:
            break
        keep = False
        try:
            row = _output_row(index, output)
            if row is not None:
                rows.append(row)
                keep = visit is not None and visit(index, output, row)
        finally:
            if not keep:
                _release(output)
    return rows


def _dxgi() -> Any:
    dxgi = ctypes.WinDLL("dxgi", use_last_error=True)
    dxgi.CreateDXGIFactory1.argtypes = [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    dxgi.CreateDXGIFactory1.restype = HRESULT
    return dxgi


@contextlib.contextmanager
def _factory() -> Iterator[Any]:
    factory = ctypes.c_void_p()
    hr = _dxgi().CreateDXGIFactory1(ctypes.byref(GUID.from_string(IID_IDXGIFactory1)), ctypes.byref(factory))
    if _failed(hr) or not factory.value:
        raise OSError(f"CreateDXGIFactory1 failed (0x{_unsigned(hr):08x})")
    try:
        yield factory
    finally:
        _release(factory)


def enumerate_adapters() -> list[DxgiAdapter]:
    """Every adapter with its outputs, in ``EnumAdapters1`` order (index 0 is the default adapter). Raises OSError
    when DXGI itself is unavailable; callers treat that as "no inventory"."""
    if sys.platform != "win32":
        raise OSError("DXGI needs Windows")
    adapters: list[DxgiAdapter] = []
    with _factory() as factory:
        enum_adapters = _method(factory, VT_FACTORY1_ENUM_ADAPTERS1, HRESULT, UINT, ctypes.POINTER(ctypes.c_void_p))
        for index in range(32):
            adapter = ctypes.c_void_p()
            if _failed(enum_adapters(index, ctypes.byref(adapter))) or not adapter.value:
                break
            try:
                desc = DXGI_ADAPTER_DESC1()
                hr = _method(adapter, VT_ADAPTER1_GET_DESC1, HRESULT, ctypes.POINTER(DXGI_ADAPTER_DESC1))(
                    ctypes.byref(desc)
                )
                if _failed(hr):
                    continue
                adapters.append(
                    DxgiAdapter(
                        index=index,
                        description=wide(desc.Description),
                        vendor_id=int(desc.VendorId),
                        device_id=int(desc.DeviceId),
                        software=bool(int(desc.Flags) & DXGI_ADAPTER_FLAG_SOFTWARE),
                        outputs=tuple(_outputs_of(adapter)),
                    )
                )
            finally:
                _release(adapter)
    return adapters


def remote_session_active() -> bool:
    """``GetSystemMetrics(SM_REMOTESESSION) != 0``: this is a Remote Desktop session (Desktop Duplication is refused
    there). False off Windows or when the call fails."""
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int
        return bool(user32.GetSystemMetrics(SM_REMOTESESSION))
    except (OSError, AttributeError):
        return False


def parse_gpu_preference(value: str) -> str:
    """Pure: the value word of a ``UserGpuPreferences`` entry (``GpuPreference=N;``): ``auto`` / ``power_saving`` /
    ``high_performance``, ``GpuPreference=N`` for a number (at most three digits) Windows has not documented, ``unknown``
    when the entry holds no such token or its value is not such a number. Never the raw value: the bundle gets the word
    only (DECISIONS #96), whatever the registry string holds."""
    for part in str(value).split(";"):
        name, _sep, number = part.partition("=")
        if name.strip() == "GpuPreference":
            number = number.strip()
            if not (number.isascii() and number.isdigit() and len(number) <= 3):
                return "unknown"
            return GPU_PREFERENCE_WORDS.get(number, f"GpuPreference={number}")
    return "unknown"


def gpu_preference(executable: str) -> str | None:
    """The per-app GPU choice Windows keeps for ``executable`` (Settings › Display › Graphics), as
    :func:`parse_gpu_preference` reads it, or None when there is no entry. A high-performance preference moves the
    default adapter — and so ``ddagrab``'s outputs — to that GPU."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, GPU_PREFERENCES_KEY, 0, winreg.KEY_READ) as key:
            value, _kind = winreg.QueryValueEx(key, executable)
    except OSError:
        return None
    return parse_gpu_preference(str(value))


@contextlib.contextmanager
def _per_monitor_dpi() -> Iterator[None]:
    """``ddagrab`` makes the thread per-monitor DPI aware (v2) around ``DuplicateOutput1``; so does the probe."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    setter = getattr(user32, "SetThreadDpiAwarenessContext", None)
    previous = None
    if setter is not None:
        setter.argtypes = [ctypes.c_void_p]
        setter.restype = ctypes.c_void_p
        previous = setter(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    try:
        yield
    finally:
        if setter is not None and previous:
            setter(previous)


def _create_device() -> tuple[int, Any]:
    """``D3D11CreateDevice`` exactly as ``hwcontext_d3d11va.c`` creates ``ddagrab``'s device: the default adapter,
    hardware driver, video support. Returns ``(hresult, device)``."""
    d3d11 = ctypes.WinDLL("d3d11", use_last_error=True)
    d3d11.D3D11CreateDevice.argtypes = [
        ctypes.c_void_p,  # pAdapter
        ctypes.c_int,  # DriverType
        ctypes.c_void_p,  # Software
        UINT,  # Flags
        ctypes.c_void_p,  # pFeatureLevels
        UINT,  # FeatureLevels
        UINT,  # SDKVersion
        ctypes.POINTER(ctypes.c_void_p),  # ppDevice
        ctypes.c_void_p,  # pFeatureLevel
        ctypes.c_void_p,  # ppImmediateContext
    ]
    d3d11.D3D11CreateDevice.restype = HRESULT
    device = ctypes.c_void_p()
    hr = d3d11.D3D11CreateDevice(
        None,
        D3D_DRIVER_TYPE_HARDWARE,
        None,
        D3D11_CREATE_DEVICE_VIDEO_SUPPORT,
        None,
        0,
        D3D11_SDK_VERSION,
        ctypes.byref(device),
        None,
        None,
    )
    return _unsigned(hr), device


def _duplicate(output: Any, device: Any) -> int:
    """``IDXGIOutput5::DuplicateOutput1`` (BGRA only, per-monitor DPI aware, like ``ddagrab`` with its default
    ``output_fmt``), else ``IDXGIOutput1::DuplicateOutput``; the duplication is released at once. The HRESULT."""
    duplication = ctypes.c_void_p()
    output5 = _query_interface(output, IID_IDXGIOutput5)
    if output5 is not None:
        try:
            formats = (UINT * 1)(DXGI_FORMAT_B8G8R8A8_UNORM)
            duplicate1 = _method(
                output5,
                VT_OUTPUT5_DUPLICATE_OUTPUT1,
                HRESULT,
                ctypes.c_void_p,
                UINT,
                UINT,
                ctypes.POINTER(UINT),
                ctypes.POINTER(ctypes.c_void_p),
            )
            with _per_monitor_dpi():
                hr = duplicate1(device, 0, 1, formats, ctypes.byref(duplication))
        finally:
            _release(output5)
    else:
        output1 = _query_interface(output, IID_IDXGIOutput1)
        if output1 is None:
            return E_NOINTERFACE
        try:
            duplicate = _method(
                output1, VT_OUTPUT1_DUPLICATE_OUTPUT, HRESULT, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
            )
            hr = duplicate(device, ctypes.byref(duplication))
        finally:
            _release(output1)
    _release(duplication)
    return _unsigned(hr)


def probe_duplication(*, left: int, top: int, width: int, height: int, monitor: int | None = None) -> DuplicationProbe:
    """Would ``ddagrab`` be able to duplicate the monitor at this rectangle right now? The same device, the device's
    own adapter, the output that shows the monitor, one ``DuplicateOutput1`` released at once; no frame is acquired.
    ``hresult`` None with a ``reason`` when the question cannot be answered (not Windows, no D3D11, the monitor is
    not on the default adapter — ``ddagrab`` is skipped for it anyway). Never raises."""
    if sys.platform != "win32":
        return DuplicationProbe(None, reason="not Windows")
    remote = remote_session_active()
    try:
        hr, device = _create_device()
    except (OSError, AttributeError) as exc:
        return DuplicationProbe(None, remote, reason=f"D3D11 unavailable ({exc.__class__.__name__})")
    if _failed(hr) or not device.value:
        return DuplicationProbe(hr, remote, stage="device", reason="D3D11CreateDevice failed")
    dxgi_device = adapter = None
    try:
        dxgi_device = _query_interface(device, IID_IDXGIDevice)
        if dxgi_device is None:
            return DuplicationProbe(None, remote, reason="no IDXGIDevice")
        adapter_ptr = ctypes.c_void_p()
        hr = _method(dxgi_device, VT_DEVICE_GET_ADAPTER, HRESULT, ctypes.POINTER(ctypes.c_void_p))(
            ctypes.byref(adapter_ptr)
        )
        if _failed(hr) or not adapter_ptr.value:
            return DuplicationProbe(None, remote, reason="IDXGIDevice::GetAdapter failed")
        adapter = adapter_ptr
        desc = DXGI_ADAPTER_DESC()
        description = ""
        if not _failed(
            _method(adapter, VT_ADAPTER_GET_DESC, HRESULT, ctypes.POINTER(DXGI_ADAPTER_DESC))(ctypes.byref(desc))
        ):
            description = wide(desc.Description)
        rect = (left, top, left + width, top + height)
        claimed: list[tuple[int, Any]] = []

        def claim(index: int, output: Any, row: DxgiOutput) -> bool:
            if claimed or not ((monitor and row.monitor == monitor) or row.rect == rect):
                return False
            claimed.append((index, output))
            return True

        _outputs_of(adapter, claim)
        if not claimed:
            return DuplicationProbe(
                None, remote, adapter=description, reason="the monitor is not an output of the default adapter"
            )
        index, output = claimed[0]
        try:
            hr = _duplicate(output, device)
        finally:
            _release(output)
        return DuplicationProbe(hr, remote, stage="duplicate", adapter=description, output_index=index)
    except (OSError, AttributeError, ValueError) as exc:
        return DuplicationProbe(None, remote, reason=f"probe failed ({exc.__class__.__name__})")
    finally:
        _release(adapter)
        _release(dxgi_device)
        _release(device)


__all__ = [
    "DXGI_ADAPTER_DESC1",
    "DXGI_ERROR_ACCESS_LOST",
    "DXGI_ERROR_NOT_CURRENTLY_AVAILABLE",
    "DXGI_ERROR_NOT_FOUND",
    "DXGI_ERROR_SESSION_DISCONNECTED",
    "DXGI_ERROR_UNSUPPORTED",
    "DXGI_OUTPUT_DESC",
    "E_ACCESSDENIED",
    "E_INVALIDARG",
    "GUID",
    "RECT",
    "S_OK",
    "DxgiAdapter",
    "DxgiOutput",
    "OutputLocation",
    "enumerate_adapters",
    "gpu_preference",
    "parse_gpu_preference",
    "inventory",
    "locate_output",
    "probe_duplication",
    "remote_session_active",
    "wide",
]
