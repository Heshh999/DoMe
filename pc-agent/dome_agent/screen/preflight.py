"""The Desktop Duplication pre-flight of a Screen view start (KNOWN_ISSUES #31; capture report of 2026-10-26).

The capture source opens only after DTLS (DECISIONS #82), so without a pre-flight a PC whose duplication is busy,
unsupported for the display or refused in a remote session answers the start, shows the bars, connects and then ends
``capture_failed`` ("stopped unexpectedly"). Before the bars, :func:`run_preflight` asks the platform's screen adapter
for a :class:`~dome_agent.platform.protocol.DuplicationProbe` (``platform/windows/dxgi.py``: the same device and output
``ddagrab`` would use, one ``DuplicateOutput1`` released at once, no frame) on a thread, bounded to
:data:`PREFLIGHT_TIMEOUT_SECONDS`, and :func:`preflight_detail` (pure) turns the raw HRESULT into the contract's
``SCREEN_CAPTURE_UNAVAILABLE`` detail, so the phone's text is honest.

The probe is advice, not the only path: a probe that cannot run (``hresult`` None: no DXGI, an older Windows, the
monitor on another adapter, a slow driver past the budget) lets the start proceed exactly as before, and a probe that
succeeds is followed by the real open after DTLS, whose failure is still reported as today. A probe that FAILS refuses
the start only when no capture path could serve it (:func:`preflight_refuses`: the D3D11 device itself); Desktop
Duplication's own refusals are handed to the worker (:func:`preflight_verdict`, the source spec's ``preflight`` entry),
which skips Desktop Duplication and opens Windows.Graphics.Capture at once, the HRESULT in its fallback warning.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from typing import Any

from ..logsetup import get_logger
from ..platform.protocol import DuplicationProbe, Monitor, PlatformSet

log = get_logger(__name__)

PREFLIGHT_TIMEOUT_SECONDS = 0.5  # the whole probe, D3D11 device creation included; past it the start goes on as before
_probe_lock = threading.Lock()  # one probe at a time: a driver call that hangs must not pile up a thread per start
S_OK = 0x00000000
E_ACCESSDENIED = 0x80070005
E_INVALIDARG = 0x80070057
DXGI_ERROR_NOT_FOUND = 0x887A0002
DXGI_ERROR_UNSUPPORTED = 0x887A0004
DXGI_ERROR_NOT_CURRENTLY_AVAILABLE = 0x887A0022
DXGI_ERROR_ACCESS_LOST = 0x887A0026
DXGI_ERROR_SESSION_DISCONNECTED = 0x887A0028
HRESULT_NAMES = {
    S_OK: "S_OK",
    E_ACCESSDENIED: "E_ACCESSDENIED",
    E_INVALIDARG: "E_INVALIDARG",
    DXGI_ERROR_NOT_FOUND: "DXGI_ERROR_NOT_FOUND",
    DXGI_ERROR_UNSUPPORTED: "DXGI_ERROR_UNSUPPORTED",
    DXGI_ERROR_NOT_CURRENTLY_AVAILABLE: "DXGI_ERROR_NOT_CURRENTLY_AVAILABLE",
    DXGI_ERROR_ACCESS_LOST: "DXGI_ERROR_ACCESS_LOST",
    DXGI_ERROR_SESSION_DISCONNECTED: "DXGI_ERROR_SESSION_DISCONNECTED",
}


def preflight_detail(probe: DuplicationProbe) -> str | None:
    """Pure: the ``SCREEN_CAPTURE_UNAVAILABLE`` detail a probe result justifies, or None to go ahead (S_OK, or no
    answer). The HRESULTs are the ones ``vsrc_ddagrab.c`` maps after ``DuplicateOutput1``, read here directly because
    ``ddagrab`` never prints them: ``DXGI_ERROR_NOT_CURRENTLY_AVAILABLE`` is the duplication-session limit (``busy``);
    ``DXGI_ERROR_UNSUPPORTED`` is the display (a hybrid-GPU output, 8 bpp, a remote session when Windows says so);
    ``E_ACCESSDENIED`` is the secure desktop or a remote session (``remote_session`` only when Windows says this is one,
    else ``driver``: the start path has already refused a secure desktop); ``DXGI_ERROR_SESSION_DISCONNECTED`` is a
    remote session; an output that vanished between mapping and probe is ``unsupported_display``; a failed
    ``D3D11CreateDevice`` and anything else is ``driver``."""
    if probe.hresult is None:
        return None
    hresult = int(probe.hresult) & 0xFFFFFFFF
    if hresult == S_OK:
        return None
    if probe.stage == "device":
        return "driver"
    if hresult == DXGI_ERROR_NOT_CURRENTLY_AVAILABLE:
        return "busy"
    if hresult == DXGI_ERROR_UNSUPPORTED:
        return "remote_session" if probe.remote_session else "unsupported_display"
    if hresult == DXGI_ERROR_SESSION_DISCONNECTED:
        return "remote_session"
    if hresult == E_ACCESSDENIED:
        return "remote_session" if probe.remote_session else "driver"
    if hresult == DXGI_ERROR_NOT_FOUND:
        return "unsupported_display"
    return "driver"


def preflight_refuses(probe: DuplicationProbe, detail: str | None) -> bool:
    """Whether a failed probe refuses the start outright. Only a D3D11 device that cannot be created does: both capture
    paths need one (windows-capture's ``create_d3d_device`` is the same ``D3D11CreateDevice(NULL, HARDWARE)`` call,
    ``d3d11.rs`` in 2.0.1). Desktop Duplication's own refusals (``busy``, ``unsupported_display``, ``remote_session``,
    another HRESULT after ``DuplicateOutput1``) are what the Windows.Graphics.Capture fallback exists for: the start
    goes on and the worker skips Desktop Duplication (:func:`preflight_verdict`)."""
    return detail is not None and probe.stage == "device"


def preflight_verdict(probe: DuplicationProbe, detail: str) -> dict[str, Any]:
    """What the worker is told about a Desktop Duplication the probe found failing (the ``preflight`` entry of the
    source spec, read by ``capture_dda.preflight_skip``): the detail, the HRESULT and its name, the stage."""
    fields = probe_fields(probe)
    return {
        "detail": detail,
        "hresult": fields["hresult"],
        "hresult_name": fields["hresult_name"],
        "stage": fields["stage"],
    }


def probe_fields(probe: DuplicationProbe) -> dict[str, Any]:
    """Content-free log fields: the HRESULT (hex and name), the stage, the adapter's description and output index."""
    hresult = None if probe.hresult is None else int(probe.hresult) & 0xFFFFFFFF
    return {
        "hresult": None if hresult is None else f"0x{hresult:08x}",
        "hresult_name": HRESULT_NAMES.get(hresult) if hresult is not None else None,
        "stage": probe.stage or None,
        "adapter": probe.adapter[:64] or None,
        "output_index": probe.output_index,
        "remote_session": probe.remote_session,
        "reason": probe.reason or None,
    }


async def run_preflight(
    platform: PlatformSet, monitor: Monitor, *, budget_seconds: float = PREFLIGHT_TIMEOUT_SECONDS
) -> tuple[str | None, DuplicationProbe | None]:
    """The probe on a thread of its own, bounded: ``(detail, probe)``. ``(None, None)`` when it did not finish in time,
    raised, or the previous probe has not returned yet (the start proceeds as without a pre-flight). Not the agent's
    shared executor: a driver call that hangs (a GPU reset, a driver update mid-session) would otherwise keep one of
    the pooled threads the touchpad's dispatch and the watchdog run on, once per start; here it keeps one daemon thread,
    no second probe starts behind it, and its late return is logged so the bundle shows the hang. Never raises."""
    if not _probe_lock.acquire(blocking=False):
        log.warning("capture pre-flight skipped: the previous probe has not returned; starting as before")
        return None, None
    loop = asyncio.get_running_loop()
    future: asyncio.Future[DuplicationProbe] = loop.create_future()
    started = time.monotonic()

    def run() -> None:
        outcome: DuplicationProbe | Exception
        try:
            outcome = platform.screen.duplication_probe(monitor)
        except Exception as exc:  # noqa: BLE001 - delivered to the waiter, which logs it and goes on
            outcome = exc
        finally:
            _probe_lock.release()
        elapsed_ms = round((time.monotonic() - started) * 1000)
        with contextlib.suppress(RuntimeError):  # the loop is closed: nobody waits
            loop.call_soon_threadsafe(_deliver, future, outcome, elapsed_ms)

    try:
        threading.Thread(target=run, name="dome-capture-preflight", daemon=True).start()
    except RuntimeError as exc:  # no thread could be started: nothing holds the lock
        _probe_lock.release()
        log.warning("capture pre-flight failed; starting as before", error=exc.__class__.__name__)
        return None, None
    try:
        probe = await asyncio.wait_for(future, budget_seconds)
    except TimeoutError:
        log.info(
            "capture pre-flight did not finish in time; starting as before", budget_ms=round(budget_seconds * 1000)
        )
        return None, None
    except Exception as exc:  # noqa: BLE001 - a broken probe is no reason to refuse a start
        log.warning("capture pre-flight failed; starting as before", error=exc.__class__.__name__)
        return None, None
    return preflight_detail(probe), probe


def _deliver(future: asyncio.Future[DuplicationProbe], outcome: DuplicationProbe | Exception, elapsed_ms: int) -> None:
    """Loop thread: the probe's outcome to its waiter, or a log line when the waiter gave up (the start went on)."""
    if future.cancelled():
        log.warning("capture pre-flight returned after its budget; the result was discarded", elapsed_ms=elapsed_ms)
        return
    if isinstance(outcome, Exception):
        future.set_exception(outcome)
    else:
        future.set_result(outcome)


__all__ = [
    "PREFLIGHT_TIMEOUT_SECONDS",
    "preflight_detail",
    "preflight_refuses",
    "preflight_verdict",
    "probe_fields",
    "run_preflight",
]
