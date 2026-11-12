"""OpenID Connect relying party (Authorization Code + PKCE) on Authlib.

Discovery and JWKS documents are fetched from the configured issuer and cached. ``state``,
``nonce`` and the PKCE verifier live in the ``auth_flows`` table for one login attempt (10 minutes,
single use). The ID token is verified with ``joserfc`` (RS256/ES256 only, issuer's JWKS) and its
``iss``, ``aud``/``azp``, ``exp``, ``iat`` and ``nonce`` claims are checked explicitly. A sign-in is refused
(``EmailNotVerifiedError``) unless ``email_verified`` is exactly ``true``: absent, ``false`` or a string such as
``"true"`` all mean the provider has not confirmed that the person owns the address (DECISIONS #118). A callback
whose login attempt is unknown, already used or older than those 10 minutes raises ``StartAgainError``: the one remedy
is a fresh sign-in, and the callback sends the browser to a plain notice for it (DECISIONS #120).

``iss`` is compared with the discovery document's ``issuer`` exactly as the provider wrote it (OpenID
Connect Core 3.1.3.7): Auth0 publishes ``https://<tenant>.auth0.com/`` with a trailing slash, while
``DOME_OIDC_ISSUER`` is stored without one. The configured issuer only has to match the discovery
document up to that slash. The token endpoint is called with ``client_secret_post`` (client id and secret
in the form body), which is what a new Auth0 Regular Web Application expects by default; Keycloak and
``tools/dev-idp`` accept it as well.
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client
from joserfc import jwt
from joserfc.jwk import KeySet
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from dome_api.db.models import AuthFlow
from dome_api.errors import ApiError
from dome_api.logging import get_logger
from dome_api.settings import Settings
from dome_api.util import utcnow

log = get_logger("dome_api.oidc")
FLOW_LIFETIME = timedelta(minutes=10)
ALLOWED_ID_TOKEN_ALGS = ("RS256", "ES256")
TOKEN_ENDPOINT_AUTH_METHOD = "client_secret_post"  # noqa: S105 - a method name, not a secret
_DISCOVERY_TTL = 3600.0
_JWKS_TTL = 3600.0


@dataclass(frozen=True, slots=True)
class Identity:
    issuer: str
    subject: str
    email: str
    display_name: str


class EmailNotVerifiedError(Exception):
    """The ID token is valid but its ``email_verified`` claim is not exactly ``true``.

    Raised before any account is looked up, created or updated; the callback turns it into a redirect to the
    PWA's sign-in notice, without a session or a session cookie (its one cookie is the re-prompt marker,
    DECISIONS #119).
    """


StartAgainReason = Literal["unknown_state", "expired"]


class StartAgainError(Exception):
    """The login attempt this callback names cannot be finished, and starting sign-in again is the whole remedy.

    ``reason`` is one of two fixed words, chosen here and never taken from the request: ``unknown_state`` (no such
    attempt: it was already used by an earlier callback, never started here, or expired and was then removed by a
    later login's sweep in ``begin``) or ``expired`` (older than ``FLOW_LIFETIME`` and still in the table). Raised
    before the provider is contacted and before any account is looked up; the callback turns it into a redirect to
    the PWA's "Sign in again" notice, without a session or any cookie (DECISIONS #120).

    Reading the log: every login start removes all expired attempts, so a slow sign-in is logged ``expired`` only when
    it comes back before anyone has started another one. ``expired`` is a lower bound on slow sign-ins, and
    ``unknown_state`` covers the rest of them, so it does not by itself mean that a callback address was replayed.
    """

    def __init__(self, reason: StartAgainReason) -> None:
        super().__init__(reason)
        self.reason: StartAgainReason = reason


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
                if (
                    not isinstance(doc, dict)
                    or not isinstance(doc.get("issuer"), str)
                    or doc["issuer"].rstrip("/") != self.settings.oidc_issuer
                ):
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
            # Explicit, so the method never depends on the library default (Authlib: client_secret_basic).
            token_endpoint_auth_method=TOKEN_ENDPOINT_AUTH_METHOD,
            timeout=10.0,
        )

    # ----- flow ------------------------------------------------------------------------------
    async def begin(self, db: AsyncSession, return_to: str, *, prompt: Literal["login"] | None = None) -> str:
        """Start one login attempt; returns the provider's authorization URL. ``prompt="login"`` adds OpenID Connect
        Core 3.1.2.1 ``prompt=login``, so the provider shows its login page even while it still remembers the person
        (sign-ins after a refusal, DECISIONS #119); without it the URL has no ``prompt`` at all."""
        disc = await self.discovery()
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)  # 86 chars, within RFC 7636's 43..128
        now = utcnow()
        # Every start sweeps all expired attempts, anyone's. A slow sign-in that comes back after this finds no row
        # and is logged ``unknown_state``, not ``expired`` (see ``StartAgainError``).
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
        # Authlib puts extra keyword arguments into the query (after state and nonce, before the PKCE challenge).
        extra = {"prompt": prompt} if prompt is not None else {}
        async with self._client() as client:
            url, _ = client.create_authorization_url(
                disc["authorization_endpoint"], state=state, nonce=nonce, code_verifier=verifier, **extra
            )
        return str(url)

    async def complete(self, db: AsyncSession, code: str, state: str) -> tuple[Identity, str]:
        """Exchange the code, verify the ID token, return (identity, return_to). Consumes the flow.

        Raises ``StartAgainError`` for an unknown, already used or expired attempt and ``EmailNotVerifiedError`` for
        an unconfirmed address, which the callback turns into redirects; every other failure stays an error answer.
        """
        # Not a start-again case: ``begin`` only ever issues a 43-character state and a provider's code is never
        # empty, so a browser coming back from a sign-in started here cannot fail this check. Only a hand-made or
        # cut-off address does, and it keeps the plain 400 (DECISIONS #120).
        if not (16 <= len(state) <= 128) or not (1 <= len(code) <= 2048):
            raise ApiError(400, "MALFORMED_MESSAGE", "Invalid login response")
        # Single use: the attempt is taken out of the table in the one statement that reads it. Two callbacks with the
        # same state cannot both get it: the second waits for the first, and finds nothing once the first has
        # committed. (Reading the row and deleting it afterwards let both through to the provider, because a delete
        # that matches no row is only a warning.) The delete is part of the request's transaction: committed when the
        # callback returns (a sign-in, the unconfirmed-email refusal, the start-again redirect) and rolled back with
        # everything else when it answers an error (a failed token exchange, a rejected ID token, a deleted account).
        taken = await db.execute(
            delete(AuthFlow)
            .where(AuthFlow.state == state)
            .returning(AuthFlow.nonce, AuthFlow.code_verifier, AuthFlow.return_to, AuthFlow.expires_at)
        )
        flow = taken.tuples().one_or_none()
        if flow is None:
            raise StartAgainError("unknown_state")
        nonce, code_verifier, return_to, expires_at = flow
        if expires_at < utcnow():
            raise StartAgainError("expired")
        disc = await self.discovery()
        try:
            async with self._client() as client:
                token = await client.fetch_token(
                    disc["token_endpoint"],
                    grant_type="authorization_code",
                    code=code,
                    code_verifier=code_verifier,
                    redirect_uri=self.settings.redirect_uri,
                )
        except Exception as exc:  # noqa: BLE001 - provider errors are reported generically
            log.warning("oidc.token_exchange_failed", error=type(exc).__name__)
            raise ApiError(502, "INTERNAL", "Sign-in could not be completed. Try again.") from None
        id_token = token.get("id_token") if isinstance(token, dict) else None
        if not isinstance(id_token, str):
            raise ApiError(502, "INTERNAL", "The identity provider returned no ID token")
        claims = await self._verify_id_token(id_token, nonce)
        verified = claims.get("email_verified")
        if verified is not True:
            # No email, no subject: the account is not known yet, and an address someone may not own is not logged.
            claim = "absent" if verified is None else ("false" if verified is False else "not_boolean")
            log.info("oidc.email_unverified", claim=claim)
            raise EmailNotVerifiedError
        subject = str(claims["sub"])
        email = str(claims.get("email") or "")[:254]
        name = str(claims.get("name") or claims.get("preferred_username") or email.split("@")[0] or "DoMe user")[:128]
        return Identity(issuer=self.settings.oidc_issuer, subject=subject, email=email, display_name=name), return_to

    async def _verify_id_token(self, id_token: str, nonce: str) -> dict[str, Any]:
        # The exact issuer string from discovery (already checked against DOME_OIDC_ISSUER up to a trailing
        # slash); the stored identity keeps the configured, slash-free form so account keys never change.
        expected_iss = str((await self.discovery())["issuer"])
        keyset = await self.jwks()
        try:
            try:
                tok = jwt.decode(id_token, keyset, algorithms=list(ALLOWED_ID_TOKEN_ALGS))
            except ValueError:
                tok = jwt.decode(id_token, await self.jwks(force=True), algorithms=list(ALLOWED_ID_TOKEN_ALGS))
            registry = jwt.JWTClaimsRegistry(
                leeway=60,
                iss={"essential": True, "value": expected_iss},
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
