"""Windows master volume via Core Audio: ``IMMDeviceEnumerator`` → default render ``IMMDevice`` →
``IAudioEndpointVolume`` (interface definitions from ``pycaw``).

The interfaces are used directly instead of ``pycaw.AudioUtilities.GetSpeakers()``: that helper changed
its return type (a raw ``IMMDevice`` became an ``AudioDevice`` wrapper without ``Activate``) and reads the
device's whole property store on every call, while the COM interfaces are Windows ABI. The calls are the
ones ``GetSpeakers()`` and ``AudioDevice.EndpointVolume`` make (pycaw 20260927 ``utils.py``).

Each call initialises COM on the calling thread (the adapters run in ``asyncio.to_thread`` worker
threads) and reads the state back after setting it (verification strategy ``read_back``). Every COM
pointer is released before that thread's ``CoUninitialize``, on the error paths too.
"""

from __future__ import annotations

import traceback
from typing import Any

from dome_protocol import ProtocolError

from ..protocol import VolumeState

_CLSID_MM_DEVICE_ENUMERATOR = "{BCDE0395-E52F-467C-8E3D-C4579291692E}"  # pycaw.constants.CLSID_MMDeviceEnumerator
_E_RENDER = 0  # EDataFlow.eRender
_E_MULTIMEDIA = 1  # ERole.eMultimedia, the role GetSpeakers() uses
_E_NOTFOUND = 0x80070490  # HRESULT_FROM_WIN32(ERROR_NOT_FOUND): no enabled output device
_RPC_E_CHANGED_MODE = 0x80010106  # the thread is already initialised as multithreaded (MTA)
_NO_DEVICE = "No audio output device is enabled on this PC"


def _hresult(exc: BaseException) -> int | None:
    """The HRESULT of a ``COMError`` (``hresult``) or the code of an ``OSError`` (``winerror``), unsigned."""
    value = getattr(exc, "hresult", None)
    if value is None:
        value = getattr(exc, "winerror", None)
    return value & 0xFFFFFFFF if isinstance(value, int) else None


def _describe(exc: BaseException) -> str:
    """Exception class and HRESULT only: COM error text is localised and says nothing more."""
    code = _hresult(exc)
    return exc.__class__.__name__ + (f" (0x{code:08X})" if code is not None else "")


def _release_references(exc: BaseException) -> None:
    """Drop what ``exc``, its cause and its context keep alive: the locals of every finished frame in their
    tracebacks, and the object an ``AttributeError`` was raised on (``obj``).

    Those hold COM pointers (the enumerator, the device, the endpoint, and comtypes' own ``self``). Left
    alone they travel with the exception to the event-loop thread and are released there, after this
    thread's ``CoUninitialize``: comtypes then calls ``Release`` into an apartment that is gone."""
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        traceback.clear_frames(current.__traceback__)  # skips the frame still running (``_run``)
        if isinstance(current, AttributeError):
            current.obj = None
        pending.extend(e for e in (current.__cause__, current.__context__) if e is not None)


def _interfaces() -> tuple[Any, Any]:
    """``(IAudioEndpointVolume, IMMDeviceEnumerator)``; ``pycaw.pycaw`` re-exports the split modules."""
    try:
        from pycaw.pycaw import IAudioEndpointVolume, IMMDeviceEnumerator
    except ImportError:
        from pycaw.api.endpointvolume import IAudioEndpointVolume
        from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
    return IAudioEndpointVolume, IMMDeviceEnumerator


def _endpoint() -> Any:
    """``IAudioEndpointVolume`` of the default output device for the multimedia role, as ``GetSpeakers()``."""
    import comtypes

    endpoint_volume, device_enumerator = _interfaces()
    enumerator = comtypes.CoCreateInstance(
        comtypes.GUID(_CLSID_MM_DEVICE_ENUMERATOR), device_enumerator, comtypes.CLSCTX_INPROC_SERVER
    )
    try:
        device = enumerator.GetDefaultAudioEndpoint(_E_RENDER, _E_MULTIMEDIA)
    except comtypes.COMError as exc:
        if _hresult(exc) == _E_NOTFOUND:
            raise ProtocolError("OS_ERROR", _NO_DEVICE) from exc
        raise
    if not device:  # NULL pointer without a failure HRESULT
        raise ProtocolError("OS_ERROR", _NO_DEVICE)
    return device.Activate(endpoint_volume._iid_, comtypes.CLSCTX_ALL, None).QueryInterface(endpoint_volume)


def _read(endpoint: Any) -> VolumeState:
    scalar = float(endpoint.GetMasterVolumeLevelScalar())
    value = max(0, min(100, round(scalar * 100)))
    return VolumeState(value=value, muted=bool(endpoint.GetMute()))


class WindowsVolume:
    def _run(self, fn: Any) -> VolumeState:
        try:
            import comtypes  # its first import runs CoInitializeEx on this thread, which can fail
        except Exception as exc:
            raise ProtocolError("OS_ERROR", f"Windows audio (COM) is unavailable: {_describe(exc)}") from exc
        initialised = False
        try:
            comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)  # S_FALSE (already STA) counts too
            initialised = True
        except OSError as exc:
            if _hresult(exc) != _RPC_E_CHANGED_MODE:
                raise ProtocolError("OS_ERROR", f"Windows audio (COM) is unavailable: {_describe(exc)}") from exc
            # Someone else made this thread multithreaded: COM is usable as it is (MMDeviceEnumerator is
            # registered ThreadingModel=Both), and that initialisation is not ours to undo.
        try:
            endpoint = _endpoint()
            try:
                return fn(endpoint)
            finally:
                del endpoint  # Release() while COM is still initialised on this thread
        except ProtocolError as exc:
            _release_references(exc)
            raise
        except Exception as exc:  # COMError, OSError, AttributeError from an unexpected pycaw shape
            _release_references(exc)
            raise ProtocolError("OS_ERROR", f"Audio endpoint error: {_describe(exc)}") from exc
        finally:
            if initialised:
                comtypes.CoUninitialize()

    def get(self) -> VolumeState:
        return self._run(_read)

    def set_volume(self, value: int) -> VolumeState:
        if not 0 <= int(value) <= 100:
            raise ProtocolError("INVALID_PARAMETERS", "volume must be 0..100")

        def _set(endpoint: Any) -> VolumeState:
            endpoint.SetMasterVolumeLevelScalar(int(value) / 100.0, None)
            return _read(endpoint)

        return self._run(_set)

    def set_muted(self, muted: bool) -> VolumeState:
        def _set(endpoint: Any) -> VolumeState:
            endpoint.SetMute(1 if muted else 0, None)
            return _read(endpoint)

        return self._run(_set)
