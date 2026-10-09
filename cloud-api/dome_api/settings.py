"""Process settings. Every ``DOME_*`` variable from ``.env.example`` is declared here and
validated once at start-up so a misconfigured deployment fails fast instead of at first use."""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Env = Literal["development", "staging", "production", "test"]


def _origin(value: str) -> str:
    parts = urlsplit(value.strip())
    if (
        parts.scheme not in ("http", "https")
        or not parts.netloc
        or parts.path not in ("", "/")
        or parts.query
        or parts.fragment
    ):
        raise ValueError(f"not an origin: {value!r} (expected scheme://host[:port])")
    return f"{parts.scheme}://{parts.netloc}".lower()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DOME_", extra="ignore", frozen=True)

    env: Env = "development"
    public_origin: str = Field(description="Exact origin the PWA is served from (Origin checks, cookies, QR links)")
    extra_origins: str = Field(default="", description="Comma-separated additional exact origins allowed for CSRF/WS")
    api_bind: str = "127.0.0.1:8000"
    # Reverse proxies whose X-Forwarded-For / X-Forwarded-Proto headers are honoured: comma-separated IP
    # addresses or CIDR networks, or the literal "none" when the process is reached directly. Empty means
    # "nothing is trusted" (the TCP peer is the client); "*" is refused because it makes every per-IP abuse
    # limit and every recorded ip_hash attacker-chosen. Staging and production must set it explicitly.
    trusted_proxies: str = ""
    database_url: str
    session_secret: SecretStr = Field(min_length=16)
    session_idle_days: int = Field(default=30, ge=1, le=90)
    session_absolute_days: int = Field(default=90, ge=1, le=365)

    oidc_issuer: str
    oidc_client_id: str
    oidc_client_secret: SecretStr
    oidc_redirect_path: str = "/v1/auth/callback"
    oidc_scopes: str = "openid email profile"

    entitlement_signing_key_pem_path: Path

    relay_max_connections: int = Field(default=5000, ge=1)
    relay_max_frame_bytes: int = Field(default=65536, ge=1024)
    relay_per_pc_queue_depth: int = Field(default=16, ge=1, le=64)
    relay_sweep_interval_seconds: float = Field(default=5.0, gt=0)
    relay_hello_timeout_seconds: float = Field(default=10.0, gt=0)
    # Per controller socket: sustained inbound frames per minute and burst, counted before any database
    # work. Sized above the plan command limits (plans.json: 120/min manual + 360/min coalescable per
    # controller) so legitimate sockets hit the per-command plan limiter first; floods are cut here.
    relay_controller_frames_per_minute: int = Field(default=600, ge=1)
    relay_controller_frame_burst: int = Field(default=120, ge=1)
    # Security-event rows a single controller socket may write per minute (rejections, refused subscribes).
    relay_security_events_per_connection_per_minute: int = Field(default=20, ge=1)
    # Public URLs handed to the agent at link time. Default: derived from public_origin.
    relay_url: str | None = None
    api_url: str | None = None

    pc_access_token_seconds: int = Field(default=3600, ge=60)
    # Abuse limits (design: link start 10/hour per IP; pairing claim 5 per 15 min per account and per IP).
    rate_link_start_per_hour: int = Field(default=10, ge=1)
    rate_pairing_claim_per_account: int = Field(default=5, ge=1, description="per 15 minutes")
    rate_pairing_claim_per_ip: int = Field(default=5, ge=1, description="per 15 minutes")
    rate_login_per_minute: int = Field(default=60, ge=1)
    rate_agent_token_per_minute: int = Field(default=30, ge=1)
    # Failed user_code lookups (unknown / expired code) per 15 minutes before 429 (RFC 8628 section 5.1).
    rate_link_code_failures_per_account: int = Field(default=10, ge=1, description="per 15 minutes")
    rate_link_code_failures_per_ip: int = Field(default=10, ge=1, description="per 15 minutes")
    # Support tickets (spec section 11A): per-account creation budget per hour, and the response expectation
    # shown to customers ONLY when the founder configured one (never a default promise).
    rate_support_tickets_per_hour: int = Field(default=10, ge=1)
    support_response_expectation: str = Field(default="", max_length=200)
    static_dir: Path | None = None
    log_level: str = "INFO"

    # Phase C (disabled when blank); declared so the schema is complete and validated.
    stripe_secret_key: SecretStr = SecretStr("")
    stripe_webhook_secret: SecretStr = SecretStr("")
    stripe_price_monthly: str = ""
    stripe_price_annual: str = ""
    ai_enabled: bool = False
    ai_provider: str = ""
    ai_api_key: SecretStr = SecretStr("")

    @field_validator("public_origin")
    @classmethod
    def _v_origin(cls, v: str) -> str:
        return _origin(v)

    @field_validator("oidc_issuer")
    @classmethod
    def _v_issuer(cls, v: str) -> str:
        parts = urlsplit(v.strip())
        if parts.scheme not in ("http", "https") or not parts.netloc or parts.query or parts.fragment:
            raise ValueError("DOME_OIDC_ISSUER must be an absolute http(s) URL without query/fragment")
        return v.strip().rstrip("/")

    @field_validator("oidc_redirect_path")
    @classmethod
    def _v_redirect(cls, v: str) -> str:
        if not v.startswith("/") or "?" in v or "#" in v:
            raise ValueError("DOME_OIDC_REDIRECT_PATH must be an absolute path without query/fragment")
        return v

    @field_validator("trusted_proxies")
    @classmethod
    def _v_trusted_proxies(cls, v: str) -> str:
        raw = v.strip()
        if raw.lower() == "none":
            return "none"
        entries = [e.strip() for e in raw.split(",") if e.strip()]
        for entry in entries:
            if entry == "*":
                raise ValueError(
                    "DOME_TRUSTED_PROXIES must list proxy addresses or CIDR networks; '*' would let any client "
                    "spoof X-Forwarded-For"
                )
            try:
                ipaddress.ip_network(entry, strict=False)
            except ValueError:
                raise ValueError(f"DOME_TRUSTED_PROXIES entry {entry!r} is not an IP address or CIDR network") from None
        return ",".join(entries)

    @field_validator("database_url")
    @classmethod
    def _v_db(cls, v: str) -> str:
        if not v.startswith("postgresql+psycopg://"):
            raise ValueError("DOME_DATABASE_URL must use the postgresql+psycopg:// dialect")
        return v

    @model_validator(mode="after")
    def _v_production(self) -> Settings:
        if self.env == "production":
            if not self.public_origin.startswith("https://"):
                raise ValueError("production requires an https DOME_PUBLIC_ORIGIN")
            if not self.oidc_issuer.startswith("https://"):
                raise ValueError("production requires an https DOME_OIDC_ISSUER")
            if self.session_secret.get_secret_value().startswith("change-me"):
                raise ValueError("production requires a real DOME_SESSION_SECRET")
        if self.env in ("staging", "production") and not self.trusted_proxies:
            raise ValueError(
                f"{self.env} requires DOME_TRUSTED_PROXIES: the addresses/CIDRs of the ingress proxy whose "
                "X-Forwarded-For is trusted, or 'none' when clients reach this process directly"
            )
        if self.static_dir is not None and not self.static_dir.is_dir():
            raise ValueError(f"DOME_STATIC_DIR {self.static_dir} is not a directory")
        return self

    # ----- derived -----------------------------------------------------------------------------
    @property
    def allowed_origins(self) -> frozenset[str]:
        extras = {_origin(o) for o in self.extra_origins.split(",") if o.strip()}
        return frozenset({self.public_origin, *extras})

    @property
    def trusted_proxy_list(self) -> list[str]:
        """Proxy addresses/networks whose forwarded headers are honoured; empty = none."""
        if not self.trusted_proxies or self.trusted_proxies == "none":
            return []
        return self.trusted_proxies.split(",")

    @property
    def cookie_secure(self) -> bool:
        return self.public_origin.startswith("https://")

    @property
    def redirect_uri(self) -> str:
        return self.public_origin + self.oidc_redirect_path

    @property
    def effective_api_url(self) -> str:
        return self.api_url or self.public_origin

    @property
    def effective_relay_url(self) -> str:
        if self.relay_url:
            return self.relay_url
        scheme = "wss" if self.public_origin.startswith("https://") else "ws"
        return scheme + "://" + self.public_origin.split("://", 1)[1] + "/ws/agent"

    @property
    def bind_host_port(self) -> tuple[str, int]:
        host, _, port = self.api_bind.rpartition(":")
        return host or "127.0.0.1", int(port)

    @property
    def issuer_origin(self) -> str:
        parts = urlsplit(self.oidc_issuer)
        return _origin(f"{parts.scheme}://{parts.netloc}")

    @property
    def validate_rest_responses(self) -> bool:
        """Outside production every REST response body is checked against rest.schema.json before it
        leaves the process (a contract violation becomes a loud 500 in development and tests)."""
        return self.env != "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # populated from the environment
