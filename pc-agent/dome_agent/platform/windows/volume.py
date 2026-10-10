"""Windows master volume via Core Audio (``pycaw`` → ``IAudioEndpointVolume``).

Each call initialises COM on the calling thread (the adapters run in ``asyncio.to_thread`` worker
threads) and reads the state back after setting it (verification strategy ``read_back``).
"""

from __future__ import annotations

from ctypes import POINTER, cast
from typing import Any

from dome_protocol import ProtocolError

from ..protocol import VolumeState


def _endpoint() -> Any:
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    device = AudioUtilities.GetSpeakers()
    if device is None:
        raise ProtocolError("OS_ERROR", "No default audio output device")
    # Current pycaw (the locked 20260927 included) returns an AudioDevice wrapper that exposes the
    # endpoint as a property and has no Activate; older releases returned the raw IMMDevice.
    endpoint = getattr(device, "EndpointVolume", None)
    if endpoint is not None:
        return endpoint
    interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


def _read(endpoint: Any) -> VolumeState:
    scalar = float(endpoint.GetMasterVolumeLevelScalar())
    value = max(0, min(100, round(scalar * 100)))
    return VolumeState(value=value, muted=bool(endpoint.GetMute()))


class WindowsVolume:
    def _run(self, fn: Any) -> VolumeState:
        import comtypes

        comtypes.CoInitialize()
        try:
            endpoint: Any = None
            try:
                endpoint = _endpoint()
                return fn(endpoint)
            except ProtocolError:
                raise
            except Exception as exc:  # COM errors
                raise ProtocolError("OS_ERROR", f"Audio endpoint error: {exc.__class__.__name__}") from exc
            finally:
                # Release the COM pointer while COM is still initialised on this thread: comtypes
                # releasing it after CoUninitialize can crash the process.
                del endpoint
        finally:
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
