"""Security headers and request logging as pure ASGI middleware (no body buffering)."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from dome_api.logging import get_logger

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

log = get_logger("dome_api.http")


def build_csp(issuer_origin: str) -> str:
    return (
        "default-src 'self'; connect-src 'self'; img-src 'self' data:; style-src 'self'; "
        "script-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'; "
        f"form-action 'self' {issuer_origin}"
    )


def _host_matches(request_host: str, canonical_host: str) -> bool:
    """``Host`` equals the canonical host, allowing an explicit default port (``:443``/``:80``)."""
    host = request_host.strip().lower()
    if host == canonical_host:
        return True
    return ":" not in canonical_host and host in (f"{canonical_host}:443", f"{canonical_host}:80")


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, csp: str, hsts: bool, noindex: bool = False, canonical_host: str = "") -> None:
        self.app = app
        self.csp = csp
        self.hsts = hsts
        # Outside production every response says "do not index", so a staging site never shows up in
        # search results next to the real one (robots.txt is the same file in every environment).
        self.noindex = noindex
        # In production, a response served under any other host name (the hosting provider's own address,
        # e.g. <app>.fly.dev) also says "do not index": it is the same site, and sign-in fails there anyway
        # because the Origin check only accepts the public origin. Empty = every host is canonical.
        self.canonical_host = canonical_host.strip().lower()

    def _noindex_for(self, scope: Scope) -> bool:
        if self.noindex:
            return True
        if not self.canonical_host:
            return False
        hosts = [value.decode("latin1") for key, value in scope.get("headers", []) if key == b"host"]
        return len(hosts) != 1 or not _host_matches(hosts[0], self.canonical_host)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        private = path.startswith("/v1") or path.startswith("/ws") or path.startswith("/healthz")
        noindex = self._noindex_for(scope)

        async def send_wrapper(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                extra = {
                    b"content-security-policy": self.csp.encode(),
                    # same-origin, not no-referrer: still nothing to other sites, but no-referrer makes
                    # Safari send "Origin: null" on the app's own POSTs, which the Origin check refuses.
                    b"referrer-policy": b"same-origin",
                    b"x-content-type-options": b"nosniff",
                    b"x-frame-options": b"DENY",
                    b"permissions-policy": b"camera=(self), microphone=(self), geolocation=()",
                    b"cross-origin-opener-policy": b"same-origin",
                    b"cross-origin-resource-policy": b"same-origin",
                }
                if self.hsts:
                    extra[b"strict-transport-security"] = b"max-age=31536000; includeSubDomains"
                if noindex:
                    extra[b"x-robots-tag"] = b"noindex, nofollow"
                if private:
                    extra[b"cache-control"] = b"no-store"
                    extra[b"pragma"] = b"no-cache"
                for k, v in extra.items():
                    if k not in present or k == b"cache-control" and private:
                        headers = [(hk, hv) for hk, hv in headers if hk.lower() != k]
                        headers.append((k, v))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


_SECRET_PATH_PREFIXES = ("/v1/agent-link/",)
_SECRET_PATH_PUBLIC_LEAVES = frozenset({"start", "poll"})


def loggable_path(path: str) -> str:
    """The request path as it may appear in a log line: never the query string (the middleware only
    sees ``scope["path"]``) and never a device-link ``user_code`` (``/v1/agent-link/ABCD-EFGH[/approve]``
    becomes ``/v1/agent-link/{user_code}[/approve]``; ``/start`` and ``/poll`` are endpoint names)."""
    for prefix in _SECRET_PATH_PREFIXES:
        if path.startswith(prefix):
            rest = path[len(prefix) :]
            head, sep, tail = rest.partition("/")
            if head and head not in _SECRET_PATH_PUBLIC_LEAVES:
                return f"{prefix}{{user_code}}{sep}{tail}"
    return path[:256]


def route_template(scope: Scope) -> str | None:
    """The matched route's path template (``/v1/pcs/{pc_id}``) when Starlette recorded one."""
    route = scope.get("route")
    template = getattr(route, "path", None)
    return template if isinstance(template, str) and template else None


class RequestLogMiddleware:
    """Logs method, route template and masked path (never the query string or a user_code), status and
    duration."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.monotonic()
        status_holder = {"status": 0}

        async def send_wrapper(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            log.info(
                "request",
                method=scope.get("method"),
                route=route_template(scope),
                path=loggable_path(str(scope.get("path", ""))),
                status=status_holder["status"],
                duration_ms=int((time.monotonic() - started) * 1000),
            )
