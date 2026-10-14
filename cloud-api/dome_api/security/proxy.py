"""Client address resolution behind a trusted reverse proxy.

Per-IP abuse limits and the ``ip_hash`` recorded with security events use ``scope["client"]``. That
value may only be rewritten from forwarded headers when the TCP peer is one of the configured
proxies (``DOME_TRUSTED_PROXIES``):

* ``X-Forwarded-For`` is walked to the right-most hop that is *not* itself a trusted proxy (the address
  the proxy appended), never to the left-most entry a client can forge. uvicorn's
  ``ProxyHeadersMiddleware`` implements exactly that walk for an explicit host/network list; this module
  applies it inside the application so the policy does not depend on how uvicorn was started. It also
  sets ``scope["scheme"]`` from ``X-Forwarded-Proto``.
* When ``DOME_CLIENT_IP_HEADER`` names a header the proxy itself sets (only ``fly-client-ip`` today),
  that header then wins over the walk. On Fly.io the right-most ``X-Forwarded-For`` hop is the app's own
  public address, so the walk alone would need those addresses in the trusted list, and they can change
  without a deploy. The header must appear exactly once and hold one IP address; anything else leaves
  the walk's answer in place. A client cannot use it: it is read only on connections from a trusted peer.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def single_header_ip(scope: Scope, name: bytes) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The one IP address in header ``name`` (lower-case bytes); None when absent, repeated or malformed.
    IPv4-mapped IPv6 addresses are returned as IPv4, so they compare equal to the plain form."""
    values = [value for key, value in scope.get("headers", []) if key == name]
    if len(values) != 1:
        return None
    try:
        ip = ipaddress.ip_address(values[0].decode("latin1").strip())
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


class TrustedProxyMiddleware:
    """Rewrite ``scope["client"]``/``scope["scheme"]`` from forwarded headers sent by a trusted proxy.

    ``trusted`` must be an explicit list of IP addresses or CIDR networks; ``"*"`` is refused here as
    well as in the settings validator. With an empty list the middleware is a pass-through and the TCP
    peer stays the client. ``client_ip_header`` (lower-case header name, or empty) is described in the
    module docstring.
    """

    def __init__(self, app: ASGIApp, *, trusted: list[str], client_ip_header: str = "") -> None:
        if any(entry.strip() == "*" for entry in trusted):
            raise ValueError("trusted proxies must be explicit addresses or networks, never '*'")
        if client_ip_header and not trusted:
            raise ValueError("a client address header is only read from trusted proxies; list them")
        self.app = app
        self.trusted = list(trusted)
        self._header = client_ip_header.strip().lower().encode("latin1")
        self._inner: ProxyHeadersMiddleware | None = None
        self._inner_with_header: ProxyHeadersMiddleware | None = None
        if self.trusted:
            self._inner = ProxyHeadersMiddleware(app, trusted_hosts=self.trusted)  # type: ignore[arg-type]
            if self._header:
                self._inner_with_header = ProxyHeadersMiddleware(
                    self._apply_client_header,  # type: ignore[arg-type]
                    trusted_hosts=self.trusted,
                )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._inner is None or scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        if self._inner_with_header is not None and self._peer_is_trusted(scope):
            await self._inner_with_header(scope, receive, send)  # type: ignore[arg-type]
            return
        await self._inner(scope, receive, send)  # type: ignore[arg-type]

    def _peer_is_trusted(self, scope: Scope) -> bool:
        peer = scope.get("client")
        if not peer or self._inner is None:
            return False
        return str(peer[0]) in self._inner.trusted_hosts

    async def _apply_client_header(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Reached only for a connection whose TCP peer is a trusted proxy (checked in __call__ before the walk).
        ip = single_header_ip(scope, self._header)
        if ip is not None:
            scope["client"] = (str(ip), 0)
        await self.app(scope, receive, send)
