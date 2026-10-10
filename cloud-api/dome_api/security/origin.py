"""Exact ``Origin`` checks. There is no wildcard and no prefix matching anywhere."""

from __future__ import annotations

from collections.abc import Mapping

from dome_api.errors import ApiError


def origin_allowed(origin: str | None, allowed: frozenset[str]) -> bool:
    return origin is not None and origin.lower() in allowed


def require_allowed_origin(headers: Mapping[str, str], allowed: frozenset[str]) -> None:
    origin = headers.get("origin")
    if origin_allowed(origin, allowed):
        return
    # Under a "no-referrer" policy, Safari and Firefox send "Origin: null" on a page's own same-origin
    # requests (Fetch standard, "serialize a request origin"); the PWA used that policy, and an installed
    # copy can keep it until it updates. Sec-Fetch-Site is set by the browser and cannot be written by a
    # page, so a null or missing Origin passes only when the browser itself marks the request same-origin.
    # A cross-site page (or a sandboxed frame) always gets a real origin or "cross-site" here.
    if origin in (None, "null") and headers.get("sec-fetch-site") == "same-origin":
        return
    raise ApiError(403, "FORBIDDEN", "Origin not allowed")


def require_origin_absent(headers: Mapping[str, str]) -> None:
    """Agent endpoints are called by a native process, never by a browser page."""
    if "origin" in headers:
        raise ApiError(403, "FORBIDDEN", "Browser origins may not call agent endpoints")
