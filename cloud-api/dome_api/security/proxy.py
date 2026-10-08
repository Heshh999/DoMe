"""Client address resolution behind a trusted reverse proxy.

Per-IP abuse limits and the ``ip_hash`` recorded with security events use ``scope["client"]``. That
value may only be rewritten from ``X-Forwarded-For`` when the TCP peer is one of the configured
proxies (``DOME_TRUSTED_PROXIES``), and then to the right-most hop that is *not* itself a trusted proxy
(the address the proxy appended), never to the left-most entry a client can forge. uvicorn's
``ProxyHeadersMiddleware`` implements exactly that walk for an explicit host/network list; this module
applies it inside the application so the policy does not depend on how uvicorn was started.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class TrustedProxyMiddleware:
    """Rewrite ``scope["client"]``/``scope["scheme"]`` from forwarded headers sent by a trusted proxy.

    ``trusted`` must be an explicit list of IP addresses or CIDR networks; ``"*"`` is refused here as
    well as in the settings validator. With an empty list the middleware is a pass-through and the TCP
    peer stays the client.
    """

    def __init__(self, app: ASGIApp, *, trusted: list[str]) -> None:
        if any(entry.strip() == "*" for entry in trusted):
            raise ValueError("trusted proxies must be explicit addresses or networks, never '*'")
        self.app = app
        self.trusted = list(trusted)
        self._inner: ProxyHeadersMiddleware | None = (
            ProxyHeadersMiddleware(app, trusted_hosts=self.trusted) if self.trusted else None  # type: ignore[arg-type]
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._inner is None or scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        await self._inner(scope, receive, send)  # type: ignore[arg-type]
