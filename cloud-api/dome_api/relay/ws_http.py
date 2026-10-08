"""Refusing a WebSocket upgrade with a real HTTP status.

Starlette's ``WebSocket.close()`` before ``accept()`` always yields HTTP 403. uvicorn exposes the
``websocket.http.response`` ASGI extension, which lets the handshake be denied with the status the
design specifies (401 authentication required, 403 origin refused, 503 relay full). When the server
does not offer the extension we fall back to the plain close."""

from __future__ import annotations

from dome_protocol import dumps_compact
from starlette.websockets import WebSocket

from dome_api.errors import ApiError

_REASONS = {401: "UNAUTHENTICATED", 403: "FORBIDDEN", 503: "SERVICE_UNAVAILABLE"}


async def deny_upgrade(ws: WebSocket, status: int, message: str | None = None) -> None:
    extensions = ws.scope.get("extensions") or {}
    if "websocket.http.response" in extensions:
        body = dumps_compact(ApiError(status, _REASONS.get(status, "FORBIDDEN"), message).body()).encode("utf-8")
        await ws.send(
            {
                "type": "websocket.http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"cache-control", b"no-store"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await ws.send({"type": "websocket.http.response.body", "body": body})
        return
    await ws.close(code=1008)
