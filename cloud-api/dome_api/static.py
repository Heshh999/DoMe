"""Serving the built PWA from ``DOME_STATIC_DIR`` (same origin as the API, ADR-0001 D3).

- hashed build assets under ``/assets/`` are immutable and cached for a year;
- ``index.html``, the service worker and the manifest are revalidated on every load;
- every unknown path that is not ``/v1``, ``/ws`` or ``/.well-known`` falls back to ``index.html``
  (SPA routing), so ``/link?user_code=…`` and ``/pair#code=…`` open the app;
- paths are resolved and checked to stay inside the directory (no traversal);
- nothing under ``/v1`` or ``/ws`` is ever served from disk or cached.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response

from dome_api.errors import ApiError
from dome_api.settings import Settings

IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"
RESERVED_PREFIXES = ("/v1", "/ws", "/.well-known", "/healthz")


def resolve_static_path(root: Path, url_path: str) -> Path | None:
    """Map a URL path to a file inside ``root`` or return None (→ SPA fallback)."""
    candidate = (root / url_path.lstrip("/")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if candidate.is_file():
        return candidate
    return None


def cache_header_for(path: Path, root: Path) -> str:
    rel = path.relative_to(root).as_posix()
    if rel.startswith("assets/"):
        return IMMUTABLE
    return REVALIDATE


def mount_static(app: FastAPI, settings: Settings) -> None:
    if settings.static_dir is None:
        return
    root = settings.static_dir.resolve()
    index = root / "index.html"
    if not index.is_file():
        raise RuntimeError(f"DOME_STATIC_DIR {root} has no index.html (build the PWA first)")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def static_or_index(request: Request, full_path: str) -> Response:
        url_path = "/" + full_path
        if url_path.startswith(RESERVED_PREFIXES):
            raise ApiError(404, "NOT_FOUND")
        file = resolve_static_path(root, url_path)
        if file is not None and file != index:
            return FileResponse(file, headers={"Cache-Control": cache_header_for(file, root)})
        return FileResponse(index, headers={"Cache-Control": REVALIDATE}, media_type="text/html")
