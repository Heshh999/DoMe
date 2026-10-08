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
        "default-src 'self'; connect-src 'self' wss: https:; img-src 'self' data:; style-src 'self'; "
        "script-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'; "
        f"form-action 'self' {issuer_origin}"
    )


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, csp: str, hsts: bool) -> None:
        self.app = app
        self.csp = csp
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope.get("path", "")
        private = path.startswith("/v1") or path.startswith("/ws") or path.startswith("/healthz")

        async def send_wrapper(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                extra = {
                    b"content-security-policy": self.csp.encode(),
                    b"referrer-policy": b"no-referrer",
                    b"x-content-type-options": b"nosniff",
                    b"x-frame-options": b"DENY",
                    b"permissions-policy": b"camera=(self), microphone=(self), geolocation=()",
                    b"cross-origin-opener-policy": b"same-origin",
                    b"cross-origin-resource-policy": b"same-origin",
                }
                if self.hsts:
                    extra[b"strict-transport-security"] = b"max-age=31536000; includeSubDomains"
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
