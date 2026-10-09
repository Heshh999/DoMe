"""Process-wide services created in the FastAPI lifespan and reached through ``request.app.state``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from dome_protocol import Registry, Schemas
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from dome_api.auth.oidc import OIDCClient
from dome_api.entitlements import EntitlementSigner
from dome_api.security.ratelimit import SlidingWindowLimiter
from dome_api.settings import Settings

if TYPE_CHECKING:
    from dome_api.relay.manager import ConnectionManager
    from dome_api.relay.router import RateLimiters


@dataclass(slots=True)
class Services:
    settings: Settings
    engine: AsyncEngine
    db: async_sessionmaker[AsyncSession]
    oidc: OIDCClient
    signer: EntitlementSigner
    registry: Registry
    schemas: Schemas
    relay: ConnectionManager
    limiters: RateLimiters
    # REST abuse limits (design: link start 10/hour per IP; pairing claim 5 per 15 min per account and per IP)
    link_start_limiter: SlidingWindowLimiter
    pairing_claim_account_limiter: SlidingWindowLimiter
    pairing_claim_ip_limiter: SlidingWindowLimiter
    login_limiter: SlidingWindowLimiter
    agent_token_limiter: SlidingWindowLimiter
    # failed user_code lookups (unknown/expired) per account and per IP, 15-minute windows
    link_code_account_limiter: SlidingWindowLimiter
    link_code_ip_limiter: SlidingWindowLimiter
    # support ticket creation per account, one-hour window
    support_ticket_limiter: SlidingWindowLimiter
