"""Customer-initiated, redacted diagnostics bundle.

Contents: agent/protocol versions, platform, non-secret settings, the status summary, the local
security events, a hardware inventory and the last 200 log lines — every line passed through the redaction filter
again. Never included: keys, credentials, tokens, pairing material, challenge texts, signed payloads or
media/window titles (the structured log already drops them; the bundle applies the text filter as a
second layer). Written as JSON to ``<state dir>/diagnostics/dome-diagnostics-<timestamp>.json``.

The hardware inventory (``hardware``; capture report of 2026-10-26 §7: the first Screen view bundle could not tell a
hybrid-GPU PC from a remote session) names what Screen view depends on: the agent's own monitor list, and on Windows
the DXGI adapters with their outputs (descriptions, vendor ids, the software flag, device names, desktop rectangles;
no handles, no pixels), ``GetSystemMetrics(SM_REMOTESESSION)``, the Windows build, the per-app GPU preference for this
executable and each monitor's advanced-colour (HDR) state. Each item is collected on its own (:func:`collect_hardware`):
one that fails leaves ``{"error": "<class>"}`` in its place and never spoils the bundle.
"""

from __future__ import annotations

import dataclasses
import json
import platform as platform_module
import sys
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dome_protocol import load_registry

from . import __version__
from .logsetup import is_sensitive_key, redact_text_line, redact_value
from .platform.protocol import PlatformSet
from .settings import Settings

LOG_TAIL_LINES = 200


def _tail(path: Path, lines: int) -> list[str]:
    if not path.exists():
        return []
    buf: deque[str] = deque(maxlen=lines)
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            buf.append(line.rstrip("\n"))
    return list(buf)


def _redact_structure(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[redacted]" if is_sensitive_key(str(k)) else _redact_structure(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_structure(v) for v in value]
    if isinstance(value, str):
        return redact_value(value)
    return value


def _windows_build() -> dict[str, Any]:
    version = getattr(sys, "getwindowsversion", None)
    if version is None:
        raise OSError("not Windows")
    info = version()
    return {"major": info.major, "minor": info.minor, "build": info.build, "version": platform_module.version()}


def hardware_probes(platform: PlatformSet | None) -> dict[str, Callable[[], Any]]:
    """What the bundle asks about the display hardware, by name. The agent's monitor list comes from the platform's
    screen adapter (any platform); the DXGI inventory, the remote-session flag, the Windows build, the GPU preference
    and the HDR state are Windows only (``platform/windows/dxgi.py``, ``displayconfig.py``)."""
    probes: dict[str, Callable[[], Any]] = {}
    if platform is not None:
        probes["monitors"] = lambda: [dataclasses.asdict(m) for m in platform.screen.monitors()]
    if sys.platform == "win32":
        from .platform.windows import displayconfig, dxgi

        probes["windows_build"] = _windows_build
        probes["remote_session"] = dxgi.remote_session_active
        probes["dxgi_adapters"] = lambda: dxgi.inventory(dxgi.enumerate_adapters())
        probes["gpu_preference"] = lambda: dxgi.gpu_preference(sys.executable)
        probes["advanced_colour"] = displayconfig.advanced_colour
    return probes


def collect_hardware(probes: dict[str, Callable[[], Any]]) -> dict[str, Any]:
    """Every probe on its own: its value, or ``{"error": "<exception class>"}`` (never the text) when it raised."""
    out: dict[str, Any] = {}
    for name, probe in probes.items():
        try:
            out[name] = probe()
        except Exception as exc:  # noqa: BLE001 - one failed item must not cost the bundle
            out[name] = {"error": exc.__class__.__name__}
    return out


def build_bundle(settings: Settings, status: dict[str, Any], platform: PlatformSet | None = None) -> dict[str, Any]:
    status = _redact_structure(status)
    # grants carry kids (public identifiers) — keep only a prefix so the bundle cannot be used to match keys
    for grant in status.get("grants", []) if isinstance(status.get("grants"), list) else []:
        if isinstance(grant, dict) and isinstance(grant.get("kid"), str):
            grant["kid"] = grant["kid"][:8] + "…"
    log_lines = [redact_text_line(line) for line in _tail(settings.log_path, LOG_TAIL_LINES)]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "agent_version": __version__,
        "protocol_version": load_registry().protocol_version,
        "python": sys.version.split()[0],
        "os": f"{platform_module.system()} {platform_module.release()}",
        "settings": {
            "state_dir": str(settings.state_dir),
            "headless": settings.headless,
            "platform_override": settings.platform_override,
            "log_level": settings.log_level,
            "api_url_configured": bool(settings.api_url),
            "relay_url_configured": bool(settings.relay_url),
        },
        "status": status,
        "hardware": _redact_structure(collect_hardware(hardware_probes(platform))),
        "log_tail": log_lines,
    }


def write_bundle(
    settings: Settings, status: dict[str, Any], out_dir: Path | None = None, *, platform: PlatformSet | None = None
) -> Path:
    bundle = build_bundle(settings, status, platform)
    target_dir = out_dir or (settings.state_dir / "diagnostics")
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = target_dir / f"dome-diagnostics-{stamp}.json"
    path.write_text(json.dumps(bundle, indent=2, default=str), encoding="utf-8")
    return path
