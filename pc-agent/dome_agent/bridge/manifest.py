"""Native-messaging host manifest (``com.dome.agent``).

``allowed_origins`` = the production extension id(s) baked into the build
(:data:`PRODUCTION_EXTENSION_IDS`) plus, only when ``DOME_AGENT_DEV_EXTENSION_ID`` is set, one
development id. The agent never downloads this list; a build without a production id can only be
registered for a development extension and ``install-native-host`` says so.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

HOST_NAME = "com.dome.agent"
HOST_DESCRIPTION = "DoMe browser bridge (YouTube control for the DoMe agent)"
EXTENSION_ID_RE = re.compile(r"\A[a-p]{32}\Z")

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
