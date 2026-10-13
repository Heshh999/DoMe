"""Native-messaging host manifest (``com.dome.agent``).

``allowed_origins`` = the production extension id(s) baked into the build
(:data:`PRODUCTION_EXTENSION_IDS`) plus, only when ``DOME_AGENT_DEV_EXTENSION_ID`` is set, one
development id. The agent never downloads this list; a build without a production id can only be
registered for a development extension and ``install-native-host`` says so.

:func:`resolve_origins` is what ``install-native-host`` and ``repair`` use: when neither a production
id nor ``DOME_AGENT_DEV_EXTENSION_ID`` is available, they keep the origins of the manifest that is
already registered on this PC (``<state dir>/com.dome.agent.json``, written once by Setup's
``/ExtensionId=`` or by the test kit). So an upgrade (Setup unregisters the host while it replaces the
files, then registers it again) and the tray's Repair keep a development extension working. The
manifest is the user's own file, so reading it back adds no trust; a build with production ids
never looks at it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

HOST_NAME = "com.dome.agent"
HOST_DESCRIPTION = "DoMe browser bridge (YouTube control for the DoMe agent)"
EXTENSION_ID_RE = re.compile(r"\A[a-p]{32}\Z")
ORIGIN_RE = re.compile(r"\Achrome-extension://[a-p]{32}/\Z")

# Filled by the release build once the store listing's extension key is fixed. Empty in source.
PRODUCTION_EXTENSION_IDS: tuple[str, ...] = ()


def allowed_origins(
    dev_extension_id: str = "", production_ids: tuple[str, ...] = PRODUCTION_EXTENSION_IDS
) -> list[str]:
    ids: list[str] = [i for i in production_ids if EXTENSION_ID_RE.match(i)]
    if dev_extension_id:
        if not EXTENSION_ID_RE.match(dev_extension_id):
            raise ValueError("DOME_AGENT_DEV_EXTENSION_ID must be a 32-character Chrome extension id (a-p)")
        if dev_extension_id not in ids:
            ids.append(dev_extension_id)
    if not ids:
        raise ValueError(
            "no extension id available: this build has no production extension id and DOME_AGENT_DEV_EXTENSION_ID is not set"
        )
    return [f"chrome-extension://{i}/" for i in ids]


def build_manifest(host_executable: Path, origins: list[str]) -> dict[str, Any]:
    return {
        "name": HOST_NAME,
        "description": HOST_DESCRIPTION,
        "path": str(host_executable),
        "type": "stdio",
        "allowed_origins": list(origins),
    }


def write_manifest(path: Path, host_executable: Path, origins: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build_manifest(host_executable, origins), indent=2) + "\n", encoding="utf-8")
    return path


def registered_origins(manifest_path: Path) -> list[str]:
    """The ``allowed_origins`` of an existing ``com.dome.agent`` manifest, de-duplicated in order; ``[]``
    when the file is missing or unreadable, is not this host's manifest, or lists anything that is not a
    ``chrome-extension://<32 a-p>/`` origin (then none of it is trusted)."""
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or data.get("name") != HOST_NAME:
        return []
    origins = data.get("allowed_origins")
    if not isinstance(origins, list) or not origins:
        return []
    if not all(isinstance(o, str) and ORIGIN_RE.match(o) for o in origins):
        return []
    return list(dict.fromkeys(origins))


def resolve_origins(
    manifest_path: Path, dev_extension_id: str = "", production_ids: tuple[str, ...] = PRODUCTION_EXTENSION_IDS
) -> list[str]:
    """Origins for a (re-)registration: the production ids plus an explicit development id when one is
    given (an invalid one raises ``ValueError``); otherwise the production ids alone; otherwise the
    origins of the manifest already registered at ``manifest_path``. Raises ``ValueError`` (no extension
    id available) when there is none of these."""
    if dev_extension_id:
        return allowed_origins(dev_extension_id, production_ids)
    try:
        return allowed_origins("", production_ids)
    except ValueError:
        kept = registered_origins(manifest_path)
        if kept:
            return kept
        raise
