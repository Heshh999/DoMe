"""Exact ``Origin`` checks. There is no wildcard and no prefix matching anywhere."""

from __future__ import annotations

from collections.abc import Mapping

from dome_api.errors import ApiError


def origin_allowed(origin: str | None, allowed: frozenset[str]) -> bool:
    return origin is not None and origin.lower() in allowed


def require_allowed_origin(headers: Mapping[str, str], allowed: frozenset[str]) -> str:
    origin = headers.get("origin")
    if not origin_allowed(origin, allowed):
        raise ApiError(403, "FORBIDDEN", "Origin not allowed")
    assert origin is not None
    return origin.lower()


def require_origin_absent(headers: Mapping[str, str]) -> None:
    """Agent endpoints are called by a native process, never by a browser page."""
    if "origin" in headers:
        raise ApiError(403, "FORBIDDEN", "Browser origins may not call agent endpoints")
