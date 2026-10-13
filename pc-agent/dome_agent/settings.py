"""Process settings: ``DOME_AGENT_*`` environment variables and the per-user state directory.

Nothing here is secret. Secrets (PC private key, PC credential, access token) live in
:mod:`dome_agent.secrets` / :mod:`dome_agent.identity` and are never part of settings.

The installed ``DoMe.exe`` also has :func:`installed_api_url`: the DoMe server address that Setup
writes next to it (``dome-server.json``). Only ``link`` reads it, as the last fallback after
``--api-url`` and ``DOME_AGENT_API_URL``: once linked, the agent uses the addresses the service
returned at link time (stored in ``identity.json``), and an environment variable would override those
for every later run, which is why Setup does not set one.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ENV_PREFIX = "DOME_AGENT_"
FAKE_PLATFORM_VALUE = "fake"
INSTALLED_SERVER_FILENAME = "dome-server.json"
# An https origin, nothing else: no path, query, user info or spaces (Setup checks the same before writing it).
_HTTPS_ORIGIN_RE = re.compile(r"\Ahttps://[A-Za-z0-9.-]+(?::[0-9]{1,5})?\Z")


def default_state_dir() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "DoMe"
        return Path.home() / "AppData" / "Local" / "DoMe"
    xdg = os.environ.get("XDG_STATE_HOME")
    base_path = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base_path / "dome"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(ENV_PREFIX + name, default).strip()


def _env_flag(name: str) -> bool:
    return _env(name).lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True, slots=True)
class Settings:
    state_dir: Path
    relay_url: str
    api_url: str
    log_level: str
    headless: bool
    platform_override: str  # "" or "fake" (test double, never set by the installer)
    dev_extension_id: str
    log_max_bytes: int = 2_000_000
    log_backups: int = 3

    @property
    def use_fake_platform(self) -> bool:
        return self.platform_override == FAKE_PLATFORM_VALUE

    @property
    def db_path(self) -> Path:
        return self.state_dir / "state.sqlite3"

    @property
    def log_dir(self) -> Path:
        return self.state_dir / "logs"

    @property
    def log_path(self) -> Path:
        return self.log_dir / "agent.log"

    @property
    def secrets_dir(self) -> Path:
        return self.state_dir / "secrets"

    @property
    def bridge_socket_path(self) -> Path:
        return self.state_dir / "bridge.sock"

    def ensure_dirs(self) -> None:
        for d in (self.state_dir, self.log_dir, self.secrets_dir):
            d.mkdir(parents=True, exist_ok=True)
        if sys.platform != "win32":
            try:
                os.chmod(self.state_dir, 0o700)
                os.chmod(self.secrets_dir, 0o700)
            except OSError:
                pass


def load_settings(*, state_dir: Path | None = None) -> Settings:
    """Read settings from the environment. ``state_dir`` overrides ``DOME_AGENT_STATE_DIR``."""
    env_state = _env("STATE_DIR")
    resolved_state = state_dir or (Path(env_state).expanduser() if env_state else default_state_dir())
    platform_override = _env("PLATFORM").lower()
    if platform_override not in ("", FAKE_PLATFORM_VALUE):
        raise ValueError(f"{ENV_PREFIX}PLATFORM may only be unset or '{FAKE_PLATFORM_VALUE}'")
    return Settings(
        state_dir=resolved_state,
        relay_url=_env("RELAY_URL"),
        api_url=_env("API_URL").rstrip("/"),
        log_level=_env("LOG_LEVEL", "INFO").upper(),
        headless=_env_flag("HEADLESS"),
        platform_override=platform_override,
        dev_extension_id=_env("DEV_EXTENSION_ID"),
    )


def installed_server_file() -> Path | None:
    """``dome-server.json`` next to the installed ``DoMe.exe`` (frozen builds only; None from source)."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).with_name(INSTALLED_SERVER_FILENAME)


def installed_api_url(path: Path | None = None) -> str:
    """The server address Setup wrote (``{"api_url": "https://..."}``), or ``""`` when there is no such
    file or it does not hold a plain https origin. ``path`` defaults to :func:`installed_server_file`."""
    target = path if path is not None else installed_server_file()
    if target is None:
        return ""
    try:
        data = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return ""
    value = data.get("api_url") if isinstance(data, dict) else None
    if not isinstance(value, str):
        return ""
    value = value.strip().rstrip("/")
    return value if _HTTPS_ORIGIN_RE.match(value) else ""
