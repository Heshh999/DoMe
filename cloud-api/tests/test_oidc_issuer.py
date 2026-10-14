"""The relying party against an Auth0-shaped issuer, without a database or a running provider.

Auth0 publishes its issuer WITH a trailing slash (``https://<tenant>.us.auth0.com/``) and puts exactly that
string in every ID token's ``iss``; ``DOME_OIDC_ISSUER`` is stored without the slash. The ID token must be
checked against the discovery document's exact issuer (OpenID Connect Core 3.1.3.7), and the token endpoint
must be called with ``client_secret_post`` (client id and secret in the form body, no Basic header).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import httpx2
import pytest
from authlib.integrations.httpx_client import AsyncOAuth2Client
from joserfc import jwt
from joserfc.jwk import KeySet, RSAKey

from dome_api.auth import oidc as oidc_module
from dome_api.auth.oidc import OIDCClient
from dome_api.errors import ApiError
from dome_api.settings import Settings

CLIENT_ID = "dome-auth0-client"
NONCE = "n-0123456789abcdef0123456789abcdef"


def _settings(issuer: str) -> Settings:
    return Settings(
        env="test",
        public_origin="https://staging.example.test",
        database_url="postgresql+psycopg://dome@/unused",
        session_secret="unit-test-session-secret-0123456789",
        oidc_issuer=issuer,
        oidc_client_id=CLIENT_ID,
        oidc_client_secret="unit-test-client-secret",
        entitlement_signing_key_pem_path=Path("/nonexistent/entitlement.pem"),
    )


class _Provider:
    """Serves a discovery document and a JWKS; signs ID tokens with a fresh RS256 key."""

    def __init__(self, discovery_issuer: str) -> None:
        self.key = RSAKey.generate_key(2048, parameters={"kid": "k1", "use": "sig", "alg": "RS256"})
        base = discovery_issuer.rstrip("/")
        self.discovery = {
            "issuer": discovery_issuer,
            "authorization_endpoint": base + "/authorize",
            "token_endpoint": base + "/oauth/token",
            "jwks_uri": base + "/.well-known/jwks.json",
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json=self.discovery)
        if request.url.path.endswith("/.well-known/jwks.json"):
            return httpx.Response(200, json=KeySet([self.key]).as_dict(private=False))
        return httpx.Response(404)

    def id_token(self, iss: str, **extra: Any) -> str:
        now = int(time.time())
        claims = {"iss": iss, "sub": "auth0|abc", "aud": CLIENT_ID, "iat": now, "exp": now + 300, "nonce": NONCE}
        claims.update(extra)
        return jwt.encode({"alg": "RS256", "kid": "k1"}, claims, self.key)


def _client(settings: Settings, provider: _Provider) -> OIDCClient:
    client = OIDCClient(settings)
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(provider.handler))
    return client


def test_configured_issuer_is_stored_without_the_trailing_slash() -> None:
    assert _settings("https://tenant.us.auth0.com/").oidc_issuer == "https://tenant.us.auth0.com"


async def test_auth0_style_issuer_with_trailing_slash_is_accepted() -> None:
    provider = _Provider("https://tenant.us.auth0.com/")
    client = _client(_settings("https://tenant.us.auth0.com/"), provider)
    try:
        claims = await client._verify_id_token(provider.id_token("https://tenant.us.auth0.com/"), NONCE)
        assert claims["sub"] == "auth0|abc"
    finally:
        await client.aclose()


async def test_token_issuer_must_match_discovery_exactly() -> None:
    provider = _Provider("https://tenant.us.auth0.com/")
    client = _client(_settings("https://tenant.us.auth0.com"), provider)
    try:
        with pytest.raises(ApiError) as caught:
            await client._verify_id_token(provider.id_token("https://tenant.us.auth0.com"), NONCE)
        assert caught.value.status == 400
        with pytest.raises(ApiError):
            await client._verify_id_token(provider.id_token("https://other.us.auth0.com/"), NONCE)
    finally:
        await client.aclose()


async def test_issuer_without_slash_keeps_working() -> None:
    """Keycloak and tools/dev-idp publish the issuer without a trailing slash."""
    provider = _Provider("https://idp.example.test/realms/dome")
    client = _client(_settings("https://idp.example.test/realms/dome"), provider)
    try:
        claims = await client._verify_id_token(provider.id_token("https://idp.example.test/realms/dome"), NONCE)
        assert claims["iss"] == "https://idp.example.test/realms/dome"
    finally:
        await client.aclose()


async def test_discovery_for_another_issuer_is_still_refused() -> None:
    provider = _Provider("https://evil.example.test/")
    client = _client(_settings("https://tenant.us.auth0.com"), provider)
    try:
        with pytest.raises(RuntimeError, match="issuer mismatch"):
            await client.discovery()
    finally:
        await client.aclose()


async def test_token_request_uses_client_secret_post(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx2.Request] = []

    def token_endpoint(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"access_token": "at", "token_type": "Bearer", "id_token": "x.y.z"})

    # Authlib's client is built on httpx2; an explicit transport also keeps any proxy settings out of the test.
    class RecordingClient(AsyncOAuth2Client):  # type: ignore[misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, transport=httpx2.MockTransport(token_endpoint), **kwargs)

    monkeypatch.setattr(oidc_module, "AsyncOAuth2Client", RecordingClient)
    client = OIDCClient(_settings("https://tenant.us.auth0.com"))
    try:
        async with client._client() as oauth:
            await oauth.fetch_token(
                "https://tenant.us.auth0.com/oauth/token",
                grant_type="authorization_code",
                code="the-code",
                code_verifier="v" * 64,
                redirect_uri=client.settings.redirect_uri,
            )
    finally:
        await client.aclose()
    assert len(seen) == 1
    request = seen[0]
    assert "authorization" not in {k.lower() for k in request.headers}
    form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
    assert form["client_id"] == CLIENT_ID and form["client_secret"] == "unit-test-client-secret"
    assert form["grant_type"] == "authorization_code" and form["code"] == "the-code"
