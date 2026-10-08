from __future__ import annotations

import base64
import hashlib
import html
import os
import secrets
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from joserfc import jwt
from joserfc.jwk import RSAKey

AUTH_CODE_TTL = 120
TOKEN_TTL = 3600


DEFAULT_ISSUER = "http://127.0.0.1:8081"
DEFAULT_CLIENT_ID = "dome-dev"
DEFAULT_CLIENT_SECRET = "dome-dev-secret"
DEFAULT_REDIRECT_URIS = ("http://localhost:5173/v1/auth/callback", "http://127.0.0.1:8000/v1/auth/callback")
DEFAULT_USERS = ("alice@example.test", "bob@example.test")


@dataclass(slots=True)
class DevIdpSettings:
    issuer: str = DEFAULT_ISSUER
    client_id: str = DEFAULT_CLIENT_ID
    client_secret: str = DEFAULT_CLIENT_SECRET
    redirect_uris: tuple[str, ...] = DEFAULT_REDIRECT_URIS
    # Users offered on the sign-in page. Any other email typed in is also accepted (dev only).
    users: tuple[str, ...] = DEFAULT_USERS

    @classmethod
    def from_env(cls) -> "DevIdpSettings":
        redirect = os.environ.get("DEV_IDP_REDIRECT_URI")
        return cls(
            issuer=os.environ.get("DEV_IDP_ISSUER", DEFAULT_ISSUER),
            client_id=os.environ.get("DEV_IDP_CLIENT_ID", DEFAULT_CLIENT_ID),
            client_secret=os.environ.get("DEV_IDP_CLIENT_SECRET", DEFAULT_CLIENT_SECRET),
            redirect_uris=tuple(u.strip() for u in redirect.split(",")) if redirect else DEFAULT_REDIRECT_URIS,
        )


@dataclass(slots=True)
class _Code:
    sub: str
    email: str
    nonce: str | None
    redirect_uri: str
    code_challenge: str | None
    code_challenge_method: str | None
    expires_at: float
    used: bool = False


@dataclass
class _State:
    key: RSAKey
    codes: dict[str, _Code] = field(default_factory=dict)
    access_tokens: dict[str, tuple[str, str, float]] = field(default_factory=dict)  # token -> (sub, email, exp)


def _sub_for(email: str) -> str:
    # Stable pseudo-random subject per email so re-login maps to the same account.
    return "dev|" + hashlib.sha256(email.lower().encode()).hexdigest()[:24]


def create_app(settings: DevIdpSettings | None = None) -> FastAPI:
    settings = settings or DevIdpSettings.from_env()
    state = _State(key=RSAKey.generate_key(2048, parameters={"kid": "dev-idp-" + uuid.uuid4().hex[:8], "use": "sig", "alg": "RS256"}))
    app = FastAPI(title="DoMe development identity provider", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings

    @app.middleware("http")
    async def _dev_only_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["X-DoMe-Dev-IdP"] = "development-only"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/.well-known/openid-configuration")
    async def discovery() -> JSONResponse:
        iss = settings.issuer.rstrip("/")
        return JSONResponse(
            {
                "issuer": iss,
                "authorization_endpoint": f"{iss}/authorize",
                "token_endpoint": f"{iss}/token",
                "userinfo_endpoint": f"{iss}/userinfo",
                "jwks_uri": f"{iss}/jwks",
                "end_session_endpoint": f"{iss}/logout",
                "response_types_supported": ["code"],
                "subject_types_supported": ["public"],
                "id_token_signing_alg_values_supported": ["RS256"],
                "scopes_supported": ["openid", "email", "profile"],
                "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
                "code_challenge_methods_supported": ["S256"],
                "claims_supported": ["sub", "email", "email_verified", "name"],
            }
        )

    @app.get("/jwks")
    async def jwks() -> JSONResponse:
        return JSONResponse({"keys": [state.key.as_dict(private=False)]})

    def _validate_authorize(params: dict[str, str]) -> None:
        if params.get("client_id") != settings.client_id:
            raise HTTPException(400, "unknown client_id")
        if params.get("response_type") != "code":
            raise HTTPException(400, "only response_type=code is supported")
        if params.get("redirect_uri") not in settings.redirect_uris:
            raise HTTPException(400, "redirect_uri not registered")
        if "openid" not in (params.get("scope") or "").split():
            raise HTTPException(400, "scope must include openid")
        if params.get("code_challenge") and params.get("code_challenge_method", "S256") != "S256":
            raise HTTPException(400, "only S256 PKCE is supported")

    def _issue_code(params: dict[str, str], email: str) -> RedirectResponse:
        code = secrets.token_urlsafe(32)
        state.codes[code] = _Code(
            sub=_sub_for(email),
            email=email,
            nonce=params.get("nonce"),
            redirect_uri=params["redirect_uri"],
            code_challenge=params.get("code_challenge"),
            code_challenge_method=params.get("code_challenge_method"),
            expires_at=time.time() + AUTH_CODE_TTL,
        )
        q = {"code": code}
        if params.get("state"):
            q["state"] = params["state"]
        return RedirectResponse(params["redirect_uri"] + "?" + urlencode(q), status_code=303)

    @app.get("/authorize")
    async def authorize(request: Request):  # type: ignore[no-untyped-def]
        params = dict(request.query_params)
        _validate_authorize(params)
        # Non-interactive path for integration tests: ?dev_user=<email>
        dev_user = params.pop("dev_user", None)
        if dev_user:
            return _issue_code(params, dev_user.strip().lower())
        hidden = "".join(
            f'<input type="hidden" name="{html.escape(k)}" value="{html.escape(v)}">' for k, v in params.items()
        )
        buttons = "".join(
            f'<button name="email" value="{html.escape(u)}">{html.escape(u)}</button>' for u in settings.users
        )
        page = f"""<!doctype html><html><head><meta charset="utf-8"><title>DoMe dev sign-in</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{font:16px system-ui;background:#0b0f14;color:#e6edf3;display:grid;place-items:center;min-height:100vh;margin:0}}
form{{background:#121821;padding:24px;border-radius:16px;width:min(420px,92vw);display:grid;gap:12px}}
button,input{{font:inherit;padding:12px 14px;border-radius:10px;border:1px solid #2a3442;background:#1a2330;color:#e6edf3}}
button{{cursor:pointer;background:#2563eb;border-color:#2563eb}} .warn{{color:#f59e0b;font-size:13px}}</style></head>
<body><form method="post" action="/authorize">{hidden}
<h1 style="margin:0;font-size:20px">Development identity provider</h1>
<p class="warn">This sign-in exists only on developer machines. It never runs in production.</p>
{buttons}
<label>Or any email<input name="email" type="email" placeholder="you@example.test"></label>
<button type="submit">Continue</button></form></body></html>"""
        return HTMLResponse(page)

    @app.post("/authorize")
    async def authorize_post(request: Request) -> RedirectResponse:
        form = await request.form()
        params = {k: str(v) for k, v in form.items() if k != "email"}
        _validate_authorize(params)
        email = str(form.get("email") or "").strip().lower()
        if not email or "@" not in email:
            raise HTTPException(400, "email required")
        return _issue_code(params, email)

    def _client_auth(request: Request, form: dict[str, str]) -> None:
        auth = request.headers.get("authorization", "")
        cid = csec = None
        if auth.lower().startswith("basic "):
            try:
                cid, csec = base64.b64decode(auth[6:]).decode().split(":", 1)
            except Exception:  # noqa: BLE001
                raise HTTPException(401, "bad client auth") from None
        else:
            cid, csec = form.get("client_id"), form.get("client_secret")
        if cid != settings.client_id or not secrets.compare_digest(csec or "", settings.client_secret):
            raise HTTPException(401, "invalid client")

    @app.post("/token")
    async def token(request: Request) -> JSONResponse:
        form = {k: str(v) for k, v in (await request.form()).items()}
        _client_auth(request, form)
        if form.get("grant_type") != "authorization_code":
            raise HTTPException(400, "unsupported grant_type")
        code = state.codes.pop(form.get("code", ""), None)
        if code is None or code.used or code.expires_at < time.time():
            raise HTTPException(400, "invalid_grant")
        code.used = True
        if form.get("redirect_uri") != code.redirect_uri:
            raise HTTPException(400, "redirect_uri mismatch")
        if code.code_challenge:
            verifier = form.get("code_verifier", "")
            digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            if not secrets.compare_digest(digest, code.code_challenge):
                raise HTTPException(400, "invalid PKCE verifier")
        now = int(time.time())
        claims = {
            "iss": settings.issuer.rstrip("/"),
            "sub": code.sub,
            "aud": settings.client_id,
            "iat": now,
            "exp": now + TOKEN_TTL,
            "email": code.email,
            "email_verified": True,
            "name": code.email.split("@")[0],
        }
        if code.nonce:
            claims["nonce"] = code.nonce
        id_token = jwt.encode({"alg": "RS256", "kid": state.key.kid}, claims, state.key)
        access = secrets.token_urlsafe(32)
        state.access_tokens[access] = (code.sub, code.email, now + TOKEN_TTL)
        return JSONResponse({"access_token": access, "token_type": "Bearer", "expires_in": TOKEN_TTL, "id_token": id_token, "scope": "openid email profile"})

    @app.get("/userinfo")
    async def userinfo(request: Request) -> JSONResponse:
        auth = request.headers.get("authorization", "")
        tok = auth[7:] if auth.lower().startswith("bearer ") else ""
        entry = state.access_tokens.get(tok)
        if not entry or entry[2] < time.time():
            raise HTTPException(401, "invalid token")
        sub, email, _ = entry
        return JSONResponse({"sub": sub, "email": email, "email_verified": True, "name": email.split("@")[0]})

    @app.get("/logout")
    async def logout(post_logout_redirect_uri: str | None = None):  # type: ignore[no-untyped-def]
        if post_logout_redirect_uri and any(post_logout_redirect_uri.startswith(u.rsplit("/", 2)[0]) for u in settings.redirect_uris):
            return RedirectResponse(post_logout_redirect_uri, status_code=303)
        return JSONResponse({"logged_out": True})

    return app
