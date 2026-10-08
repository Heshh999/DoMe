"""Application factory: ``create_app()`` wires settings, database, OIDC, entitlement signer, the relay
connection manager, routers, WebSocket endpoints, security middleware and optional static serving."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from dome_protocol import ProtocolError, load_registry, load_schemas
from fastapi import FastAPI, WebSocket
from fastapi.exceptions import RequestValidationError
from sqlalchemy import text
from starlette.exceptions import HTTPException as StarletteHTTPException

from dome_api import __version__
from dome_api.auth.oidc import OIDCClient
from dome_api.db.engine import make_engine, make_session_factory
from dome_api.db.migrate import head_revision
from dome_api.entitlements import EntitlementSigner
from dome_api.errors import (
    ApiError,
    api_error_handler,
    http_exception_handler,
    protocol_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from dome_api.logging import configure_logging, get_logger
from dome_api.relay.agent_ws import agent_endpoint
from dome_api.relay.controller_ws import controller_endpoint
from dome_api.relay.lifecycle import Sweeper
from dome_api.relay.manager import ConnectionManager
from dome_api.relay.router import RateLimiters
from dome_api.routes import account, agent, agent_link, auth, commands, controllers, grants, health, pairing, pcs, plans
from dome_api.security.headers import RequestLogMiddleware, SecurityHeadersMiddleware, build_csp
from dome_api.security.proxy import TrustedProxyMiddleware
from dome_api.security.ratelimit import SlidingWindowLimiter
from dome_api.settings import Settings, get_settings
from dome_api.state import Services
from dome_api.static import mount_static

log = get_logger("dome_api.main")


async def _check_migrations(services: Services) -> None:
    """Fail fast when the database is not at the newest Alembic revision (``dome-api`` runs
    ``upgrade head`` before serving; this guards deployments that start uvicorn directly)."""
    expected = head_revision()
    async with services.engine.connect() as conn:
        try:
            current = (await conn.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()
        except Exception as exc:  # noqa: BLE001 - table missing
            raise RuntimeError(
                "database has no alembic_version table; run `uv run dome-api` (migrations) first"
            ) from exc
    if current != expected:
        raise RuntimeError(f"database revision {current!r} is not the expected head {expected!r}; run migrations")


def _build_services(settings: Settings) -> Services:
    engine = make_engine(settings.database_url)
    db = make_session_factory(engine)
    signer = EntitlementSigner.from_path(settings.entitlement_signing_key_pem_path, settings.effective_api_url)
    relay = ConnectionManager(settings=settings, db=db, signer=signer)
    return Services(
        settings=settings,
        engine=engine,
        db=db,
        oidc=OIDCClient(settings),
        signer=signer,
        registry=load_registry(),
        schemas=load_schemas(),
        relay=relay,
        limiters=RateLimiters(settings),
        link_start_limiter=SlidingWindowLimiter(settings.rate_link_start_per_hour, 3600),
        pairing_claim_account_limiter=SlidingWindowLimiter(settings.rate_pairing_claim_per_account, 900),
        pairing_claim_ip_limiter=SlidingWindowLimiter(settings.rate_pairing_claim_per_ip, 900),
        login_limiter=SlidingWindowLimiter(settings.rate_login_per_minute, 60),
        agent_token_limiter=SlidingWindowLimiter(settings.rate_agent_token_per_minute, 60),
        link_code_account_limiter=SlidingWindowLimiter(settings.rate_link_code_failures_per_account, 900),
        link_code_ip_limiter=SlidingWindowLimiter(settings.rate_link_code_failures_per_ip, 900),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    if settings.env != "test":
        configure_logging(settings.log_level, json_output=settings.env != "development")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        services = _build_services(settings)
        await _check_migrations(services)
        sweeper = Sweeper(services.relay, settings.relay_sweep_interval_seconds)
        app.state.services = services
        await sweeper.start()
        log.info("startup", version=__version__, env=settings.env, origin=settings.public_origin)
        try:
            yield
        finally:
            await sweeper.stop()
            await services.oidc.aclose()
            await services.engine.dispose()
            log.info("shutdown")

    app = FastAPI(
        title="DoMe API", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.settings = settings

    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(ProtocolError, protocol_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    for router in (
        auth.router,
        account.router,
        agent_link.router,
        agent.router,
        pairing.router,
        pcs.router,
        controllers.router,
        grants.router,
        commands.router,
        plans.router,
    ):
        app.include_router(router, prefix="/v1")
    app.include_router(health.router)
    app.include_router(agent.wellknown)

    @app.websocket("/ws/agent")
    async def ws_agent(ws: WebSocket) -> None:
        await agent_endpoint(ws, ws.app.state.services)

    @app.websocket("/ws/controller")
    async def ws_controller(ws: WebSocket) -> None:
        svc: Services = ws.app.state.services
        await controller_endpoint(ws, svc, svc.limiters)

    mount_static(app, settings)

    # Pure-ASGI middleware wraps everything (static files and WebSocket upgrades included).
    app.add_middleware(
        SecurityHeadersMiddleware, csp=build_csp(settings.issuer_origin), hsts=settings.env == "production"
    )
    app.add_middleware(RequestLogMiddleware)
    # Outermost: the client address every limiter and ip_hash sees is resolved exactly once, and only
    # from headers a configured proxy sent (DOME_TRUSTED_PROXIES; empty = the TCP peer is the client).
    app.add_middleware(TrustedProxyMiddleware, trusted=settings.trusted_proxy_list)
    return app
