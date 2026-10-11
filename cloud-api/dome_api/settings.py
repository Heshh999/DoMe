"""Process settings. Every ``DOME_*`` variable from ``.env.example`` is declared here and
validated once at start-up so a misconfigured deployment fails fast instead of at first use."""

from __future__ import annotations

import ipaddress
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Env = Literal["development", "staging", "production", "test"]
AiProvider = Literal["", "anthropic", "fake"]
# The Anthropic SDK reads these from the environment (base URL, logging, extra headers, credentials): a stray one
# could redirect the key and the owner's words, log request bodies or add headers (docs/design/assistant.md §3.11).
_ANTHROPIC_ENV_PREFIX = "ANTHROPIC_"
MICRO_USD_PER_CENT = 10_000


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
    # hide_input_in_errors: a failed start-up check must never print the raw values (keys, secrets, database URL)
    model_config = SettingsConfigDict(env_prefix="DOME_", extra="ignore", frozen=True, hide_input_in_errors=True)

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

    # AI assistant, step 1 (docs/design/assistant.md §3.11). Off unless DOME_AI_ENABLED; the key lives only here.
    ai_enabled: bool = False
    ai_provider: AiProvider = ""
    ai_anthropic_api_key: SecretStr = SecretStr("")
    ai_model: str = Field(default="claude-opus-5-5", pattern=r"^claude-[a-z0-9-]{1,48}$")
    ai_effort: Literal["low", "medium", "high"] = "low"
    ai_max_output_tokens: int = Field(default=1024, ge=512, le=4096)
    # with streaming: the longest allowed gap between chunks of one attempt (the connect timeout is fixed at 5 s)
    ai_timeout_seconds: int = Field(default=15, ge=5, le=30)
    # DoMe's own retry of failures Anthropic does not bill (the SDK's retries are always off)
    ai_max_retries: int = Field(default=1, ge=0, le=1)
    ai_turn_deadline_seconds: int = Field(default=20, ge=10, le=60)
    ai_cache_ttl: Literal["5m", "1h"] = "5m"
    # operator spending ceiling across all accounts per calendar month (UTC); 0 refuses start-up with AI on
    ai_monthly_ceiling_cents: int = Field(default=0, ge=0)
    ai_turn_reserve_micro_usd: int = Field(default=250_000, ge=10_000)
    ai_failed_turns_per_period: int = Field(default=20, ge=1, le=1000)
    # development/test allowance; refused anywhere else
    ai_dev_grant_interpretations: int = Field(default=0, ge=0)
    ai_dev_grant_cost_cap_cents: int = Field(default=0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _v_renamed_ai_key(cls, data: Any) -> Any:
        # Settings ignore unknown variables, so the renamed key is looked up directly. Every developer .env copied
        # from an older .env.example has an empty DOME_AI_API_KEY=; only a value refuses start-up.
        if os.environ.get("DOME_AI_API_KEY", "").strip():
            raise ValueError(
                "DOME_AI_API_KEY was renamed to DOME_AI_ANTHROPIC_API_KEY; move the key and remove the old variable"
            )
        if "DOME_AI_API_KEY" in os.environ:
            from dome_api.logging import get_logger

            get_logger("dome_api.settings").warning("settings.renamed_variable", variable="DOME_AI_API_KEY")
        return data

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
        self._v_ai()
        return self

    def _v_ai(self) -> None:
        """The assistant's start-up checks (docs/design/assistant.md §3.11). Messages name variables, never values."""
        if self.ai_provider == "fake" and self.env in ("staging", "production"):
            raise ValueError(f"DOME_AI_PROVIDER=fake is for development and tests only, not {self.env}")
        if self.env not in ("development", "test") and (
            self.ai_dev_grant_interpretations or self.ai_dev_grant_cost_cap_cents
        ):
            raise ValueError(
                f"DOME_AI_DEV_GRANT_INTERPRETATIONS and DOME_AI_DEV_GRANT_COST_CAP_CENTS must be 0 in {self.env}"
            )
        if not self.ai_enabled:
            return
        if not self.ai_provider:
            raise ValueError("DOME_AI_ENABLED=true requires DOME_AI_PROVIDER (anthropic or fake)")
        if self.ai_provider == "anthropic":
            if not self.ai_anthropic_api_key.get_secret_value().strip():
                raise ValueError("DOME_AI_PROVIDER=anthropic requires DOME_AI_ANTHROPIC_API_KEY")
            stray = sorted(k for k in os.environ if k.upper().startswith(_ANTHROPIC_ENV_PREFIX))
            if stray:
                raise ValueError(
                    f"{stray[0]} is set in the environment; remove every ANTHROPIC_* variable while the Anthropic "
                    "provider is enabled (the SDK would read it)"
                )
        if self.ai_monthly_ceiling_cents <= 0:
            raise ValueError("DOME_AI_ENABLED=true requires DOME_AI_MONTHLY_CEILING_CENTS above 0")
        if self.ai_turn_deadline_seconds < self.ai_timeout_seconds + 5:
            raise ValueError("DOME_AI_TURN_DEADLINE_SECONDS must be at least DOME_AI_TIMEOUT_SECONDS + 5")
        from dome_api.assistant.pricing import worst_case_turn_micro_usd

        if self.ai_turn_reserve_micro_usd < worst_case_turn_micro_usd(self.ai_model, self.ai_max_output_tokens):
            raise ValueError(
                "DOME_AI_TURN_RESERVE_MICRO_USD is below what one turn can cost at most with DOME_AI_MODEL and "
                "DOME_AI_MAX_OUTPUT_TOKENS (one attempt plus one fallback attempt, docs/design/assistant.md §3.7); "
                "raise DOME_AI_TURN_RESERVE_MICRO_USD or lower DOME_AI_MAX_OUTPUT_TOKENS"
            )
        from dome_api.plans import catalog

        for plan in catalog().plans.values():
            limit = max(plan.ai_interpretations_per_period, self.ai_dev_grant_interpretations)
            cap = max(plan.ai_cost_cap_cents_per_period, self.ai_dev_grant_cost_cap_cents) * MICRO_USD_PER_CENT
            if limit > 0 and cap < self.ai_turn_reserve_micro_usd:
                raise ValueError(
                    f"plan {plan.id!r}: an AI allowance needs a cost cap of at least one turn reservation "
                    "(ai_cost_cap_cents_per_period / DOME_AI_DEV_GRANT_COST_CAP_CENTS x 10000 >= "
                    "DOME_AI_TURN_RESERVE_MICRO_USD); every turn would fail otherwise"
                )

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
