"""OpenID Connect relying party (Authorization Code + PKCE) on Authlib.

Discovery and JWKS documents are fetched from the configured issuer and cached. ``state``,
``nonce`` and the PKCE verifier live in the ``auth_flows`` table for one login attempt (10 minutes,
single use). The ID token is verified with ``joserfc`` (RS256/ES256 only, issuer's JWKS) and its
``iss``, ``aud``/``azp``, ``exp``, ``iat`` and ``nonce`` claims are checked explicitly.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from joserfc import jwt
from joserfc.jwk import KeySet
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from dome_api.db.models import AuthFlow
from dome_api.errors import ApiError
from dome_api.logging import get_logger
from dome_api.settings import Settings
from dome_api.util import utcnow

log = get_logger("dome_api.oidc")
FLOW_LIFETIME = timedelta(minutes=10)
ALLOWED_ID_TOKEN_ALGS = ("RS256", "ES256")
_DISCOVERY_TTL = 3600.0
_JWKS_TTL = 3600.0


@dataclass(frozen=True, slots=True)
class Identity:
    issuer: str
    subject: str
    email: str
    display_name: str


class OIDCClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._discovery: dict[str, Any] | None = None
        self._discovery_at = 0.0
        self._jwks: KeySet | None = None
        self._jwks_at = 0.0
        self._lock = asyncio.Lock()
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(10.0))

    async def aclose(self) -> None:
        await self._http.aclose()

    # ----- metadata --------------------------------------------------------------------------
    async def discovery(self) -> dict[str, Any]:
        async with self._lock:
            if self._discovery is None or time.monotonic() - self._discovery_at > _DISCOVERY_TTL:
                url = self.settings.oidc_issuer + "/.well-known/openid-configuration"
                try:
                    resp = await self._http.get(url)
                    resp.raise_for_status()
                    doc = resp.json()
                except (httpx.HTTPError, ValueError) as exc:
                    log.warning("oidc.discovery_failed", error=type(exc).__name__)
                    raise ApiError(
                        503, "SERVICE_UNAVAILABLE", "Sign-in is temporarily unavailable. Try again shortly."
                    ) from None
                if not isinstance(doc, dict) or doc.get("issuer", "").rstrip("/") != self.settings.oidc_issuer:
                    raise RuntimeError("OIDC discovery document issuer mismatch")
                for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
                    if not isinstance(doc.get(key), str) or not doc[key].startswith(("https://", "http://")):
                        raise RuntimeError(f"OIDC discovery document missing {key}")
                self._discovery = doc
                self._discovery_at = time.monotonic()
            return self._discovery

    async def jwks(self, *, force: bool = False) -> KeySet:
        disc = await self.discovery()
        async with self._lock:
            if force or self._jwks is None or time.monotonic() - self._jwks_at > _JWKS_TTL:
                try:
                    resp = await self._http.get(disc["jwks_uri"])
                    resp.raise_for_status()
                    self._jwks = KeySet.import_key_set(resp.json())
                except (httpx.HTTPError, ValueError) as exc:
                    log.warning("oidc.jwks_failed", error=type(exc).__name__)
                    raise ApiError(
                        503, "SERVICE_UNAVAILABLE", "Sign-in is temporarily unavailable. Try again shortly."
                    ) from None
                self._jwks_at = time.monotonic()
            return self._jwks

    def _client(self) -> AsyncOAuth2Client:
        return AsyncOAuth2Client(
            client_id=self.settings.oidc_client_id,
            client_secret=self.settings.oidc_client_secret.get_secret_value(),
            scope=self.settings.oidc_scopes,
            redirect_uri=self.settings.redirect_uri,
            code_challenge_method="S256",
            timeout=10.0,
        )

    # ----- flow ------------------------------------------------------------------------------
    async def begin(self, db: AsyncSession, return_to: str) -> str:
        disc = await self.discovery()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)  # 86 chars, within RFC 7636's 43..128
        now = utcnow()
        await db.execute(delete(AuthFlow).where(AuthFlow.expires_at < now))
        db.add(
            AuthFlow(
                state=state,
                nonce=nonce,
                code_verifier=verifier,
                return_to=return_to,
                created_at=now,
                expires_at=now + FLOW_LIFETIME,
            )
        )
        async with self._client() as client:
            url, _ = client.create_authorization_url(
                disc["authorization_endpoint"], state=state, nonce=nonce, code_verifier=verifier
            )
        return str(url)

    async def complete(self, db: AsyncSession, code: str, state: str) -> tuple[Identity, str]:
        """Exchange the code, verify the ID token, return (identity, return_to). Consumes the flow."""
        if not (16 <= len(state) <= 128) or not (1 <= len(code) <= 2048):
            raise ApiError(400, "MALFORMED_MESSAGE", "Invalid login response")
        flow = await db.scalar(select(AuthFlow).where(AuthFlow.state == state))
        if flow is None:
            raise ApiError(400, "MALFORMED_MESSAGE", "Unknown or already used login state")
        await db.delete(flow)  # single use, success or not
        await db.flush()
        if flow.expires_at < utcnow():
            raise ApiError(400, "MALFORMED_MESSAGE", "Login attempt expired; start again")
        disc = await self.discovery()
        try:
            async with self._client() as client:
                token = await client.fetch_token(
                    disc["token_endpoint"],
                    grant_type="authorization_code",
                    code=code,
                    code_verifier=flow.code_verifier,
                    redirect_uri=self.settings.redirect_uri,
                )
        except Exception as exc:  # noqa: BLE001 - provider errors are reported generically
            log.warning("oidc.token_exchange_failed", error=type(exc).__name__)
            raise ApiError(502, "INTERNAL", "Sign-in could not be completed. Try again.") from None
        id_token = token.get("id_token") if isinstance(token, dict) else None
        if not isinstance(id_token, str):
            raise ApiError(502, "INTERNAL", "The identity provider returned no ID token")
        claims = await self._verify_id_token(id_token, flow.nonce)
        subject = str(claims["sub"])
        email = str(claims.get("email") or "")[:254]
        name = str(claims.get("name") or claims.get("preferred_username") or email.split("@")[0] or "DoMe user")[:128]
        return Identity(
            issuer=self.settings.oidc_issuer, subject=subject, email=email, display_name=name
        ), flow.return_to

    async def _verify_id_token(self, id_token: str, nonce: str) -> dict[str, Any]:
        keyset = await self.jwks()
        try:
            try:
                tok = jwt.decode(id_token, keyset, algorithms=list(ALLOWED_ID_TOKEN_ALGS))
            except ValueError:
                tok = jwt.decode(id_token, await self.jwks(force=True), algorithms=list(ALLOWED_ID_TOKEN_ALGS))
            registry = jwt.JWTClaimsRegistry(
                leeway=60,
                iss={"essential": True, "value": self.settings.oidc_issuer},
                sub={"essential": True},
                exp={"essential": True},
                iat={"essential": True},
                nonce={"essential": True, "value": nonce},
            )
            registry.validate(tok.claims)
            aud = tok.claims.get("aud")
            audiences = aud if isinstance(aud, list) else [aud]
            if self.settings.oidc_client_id not in audiences:
                raise ValueError("aud")
            if len(audiences) > 1 and tok.claims.get("azp") != self.settings.oidc_client_id:
                raise ValueError("azp")
            if tok.header.get("alg") not in ALLOWED_ID_TOKEN_ALGS:
                raise ValueError("alg")
        except Exception as exc:  # noqa: BLE001
            log.warning("oidc.id_token_rejected", reason=type(exc).__name__)
            raise ApiError(400, "MALFORMED_MESSAGE", "The identity token was rejected") from None
        claims: dict[str, Any] = dict(tok.claims)
        return claims
