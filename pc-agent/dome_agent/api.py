"""REST client for the cloud API endpoints the agent uses (device link, PC token, entitlement,
pairing start). Every response body is validated against ``rest.schema.json`` before use; error
bodies are mapped to :class:`ApiError` with the server's stable code when it sent one.

Secrets (``device_code``, ``pc_credential``, access tokens) travel only in request bodies or the
``Authorization`` header, never in URLs, and are never logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from dome_protocol import ProtocolError, load_schemas, loads_strict

from . import __version__
from .logsetup import get_logger

log = get_logger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
MAX_BODY_BYTES = 65536


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable

    @property
    def is_network_or_server_error(self) -> bool:
        """True for the failures that qualify for entitlement grace (network error / 5xx)."""
        return self.status == 0 or self.status >= 500


@dataclass(frozen=True, slots=True)
class LinkStart:
    device_code: str
    user_code: str
    verification_uri_complete: str
    expires_in: int
    interval: int


@dataclass(frozen=True, slots=True)
class LinkResult:
    pc_id: str
    account_id: str
    pc_credential: str
    relay_url: str
    api_url: str
    pc_name: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class AccessToken:
    access_token: str
    expires_in: int
    pc_id: str
    account_id: str


@dataclass(frozen=True, slots=True)
class EntitlementResponse:
    plan: str
    assertion: str | None
    pc_enabled: bool


class ApiClient:
    def __init__(self, api_url: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._base = api_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers={"User-Agent": f"DoMe-agent/{__version__}"}, follow_redirects=False)
        self._owned = client is None
        self._schemas = load_schemas()

    @property
    def base_url(self) -> str:
        return self._base

    async def close(self) -> None:
        if self._owned:
            await self._client.aclose()

    # ----- plumbing ---------------------------------------------------------------------------------------
    async def _request(self, method: str, path: str, *, json_body: dict[str, Any] | None = None, bearer: str | None = None, expect: tuple[int, ...] = (200,)) -> tuple[int, dict[str, Any]]:
        headers: dict[str, str] = {"Accept": "application/json"}
        if bearer is not None:
            headers["Authorization"] = f"Bearer {bearer}"
        try:
            response = await self._client.request(method, self._base + path, json=json_body, headers=headers)
        except httpx.HTTPError as exc:
            raise ApiError(0, "NETWORK", f"{exc.__class__.__name__} talking to the DoMe service", retryable=True) from exc
        if len(response.content) > MAX_BODY_BYTES:
            raise ApiError(response.status_code, "PAYLOAD_TOO_LARGE", "response too large")
        body: dict[str, Any] = {}
        if response.content:
            try:
                parsed = loads_strict(response.content, max_bytes=MAX_BODY_BYTES, require_object=True)
                body = parsed if isinstance(parsed, dict) else {}
            except ProtocolError as exc:
                raise ApiError(response.status_code, "MALFORMED_MESSAGE", f"invalid JSON from the service ({exc.message})") from None
        if response.status_code in expect:
            return response.status_code, body
        code, message = "HTTP_ERROR", f"unexpected status {response.status_code}"
        try:
            self._schemas.validate_rest("error_body", body)
            code, message = str(body["error"]["code"]), str(body["error"]["message"])
            retryable = bool(body["error"].get("retryable", False))
        except ProtocolError:
            retryable = response.status_code >= 500
        raise ApiError(response.status_code, code, message, retryable=retryable or response.status_code >= 500)

    def _validated(self, name: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            self._schemas.validate_rest(name, body)
        except ProtocolError as exc:
            raise ApiError(200, "MALFORMED_MESSAGE", f"{name}: {exc.message}") from None
        return body

    # ----- device link ------------------------------------------------------------------------------------
    async def link_start(self, pc_public_jwk: dict[str, str], platform: str, pc_name_hint: str | None = None) -> LinkStart:
        body: dict[str, Any] = {"pc_public_jwk": pc_public_jwk, "agent_version": __version__, "platform": platform}
        if pc_name_hint:
            body["pc_name_hint"] = pc_name_hint[:64]
        self._schemas.validate_rest("agent_link_start_request", body)
        _status, out = await self._request("POST", "/v1/agent-link/start", json_body=body)
        out = self._validated("agent_link_start_response", out)
        return LinkStart(out["device_code"], out["user_code"], out["verification_uri_complete"], int(out["expires_in"]), int(out["interval"]))

    async def link_poll(self, device_code: str) -> LinkResult | str:
        """Returns a LinkResult on 200, or the pending status string ('authorization_pending' | 'slow_down') on 428."""
        status, out = await self._request("POST", "/v1/agent-link/poll", json_body={"device_code": device_code}, expect=(200, 428))
        if status == 428:
            out = self._validated("agent_link_poll_pending", out)
            return str(out["status"])
        out = self._validated("agent_link_poll_response", out)
        return LinkResult(out["pc_id"], out["account_id"], out["pc_credential"], out["relay_url"], out["api_url"], out["pc_name"], bool(out["enabled"]))

    # ----- PC bearer --------------------------------------------------------------------------------------
    async def token(self, pc_credential: str) -> AccessToken:
        _status, out = await self._request("POST", "/v1/agent/token", json_body={"pc_credential": pc_credential})
        out = self._validated("agent_token_response", out)
        return AccessToken(out["access_token"], int(out["expires_in"]), out["pc_id"], out["account_id"])

    async def entitlement(self, access_token: str) -> EntitlementResponse:
        _status, out = await self._request("POST", "/v1/agent/entitlement", json_body={}, bearer=access_token)
        out = self._validated("agent_entitlement_response", out)
        return EntitlementResponse(str(out["plan"]), out["assertion"], bool(out["pc_enabled"]))

    async def jwks(self) -> dict[str, Any]:
        _status, out = await self._request("GET", "/.well-known/dome-jwks.json")
        keys = out.get("keys")
        if not isinstance(keys, list):
            raise ApiError(200, "MALFORMED_MESSAGE", "JWKS document has no keys")
        return out

    async def pairing_start(self, access_token: str, code_hash: str) -> tuple[str, str]:
        """→ (pairing_id, expires_at)."""
        body = {"code_hash": code_hash}
        self._schemas.validate_rest("pairing_start_request", body)
        _status, out = await self._request("POST", "/v1/pairing/start", json_body=body, bearer=access_token, expect=(200, 201))
        out = self._validated("pairing_start_response", out)
        return str(out["pairing_id"]), str(out["expires_at"])
