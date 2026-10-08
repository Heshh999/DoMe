"""Customer-initiated, redacted diagnostics bundle.

Contents: agent/protocol versions, platform, non-secret settings, the status summary, the local
security events and the last 200 log lines — every line passed through the redaction filter again.
Never included: keys, credentials, tokens, pairing material, challenge texts, signed payloads or
media/window titles (the structured log already drops them; the bundle applies the text filter as a
second layer). Written as JSON to ``<state dir>/diagnostics/dome-diagnostics-<timestamp>.json``.
"""

from __future__ import annotations

import json
import platform
import sys
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dome_protocol import load_registry

from . import __version__
from .logsetup import is_sensitive_key, redact_text_line, redact_value
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


def build_bundle(settings: Settings, status: dict[str, Any]) -> dict[str, Any]:
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
        "os": f"{platform.system()} {platform.release()}",
        "settings": {
            "state_dir": str(settings.state_dir),
            "headless": settings.headless,
            "platform_override": settings.platform_override,
            "log_level": settings.log_level,
            "api_url_configured": bool(settings.api_url),
            "relay_url_configured": bool(settings.relay_url),
        },
        "status": status,
        "log_tail": log_lines,
    }


def write_bundle(settings: Settings, status: dict[str, Any], out_dir: Path | None = None) -> Path:
    bundle = build_bundle(settings, status)
    target_dir = out_dir or (settings.state_dir / "diagnostics")
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = target_dir / f"dome-diagnostics-{stamp}.json"
    path.write_text(json.dumps(bundle, indent=2, default=str), encoding="utf-8")
    return path
