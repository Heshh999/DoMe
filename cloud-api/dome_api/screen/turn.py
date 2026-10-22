"""TURN credentials for the PHONE only (docs/design/screen-view.md §13; rules.screen_signaling (1), screen_metering
(4)), and the spending guard that is fed by the provider's own figures, never by the agent.

* :class:`TurnProvider` is the seam: ``cloudflare`` (Cloudflare Realtime TURN, httpx, 3 s timeout), ``none`` (STUN
  only: Screen view works where a direct path exists) and ``fake`` (tests). A provider mints, revokes and reports
  usage; it never sees a database or a plan decision, and its errors carry a reason only (never a token or a body).
* :class:`TurnService` owns ``turn_credentials``: at most one outstanding credential per controller (the row's primary
  key), minting a new one first revokes the previous, and every path of §13 revokes (no start within 60 s, a refused
  start, the end of the session, the sweeper, controller revocation, grant narrowing, logout, plan change). A
  revocation that belongs to ONE start (its failure, a lost forward, a reservation swept without a session) revokes
  only that start's credential (:meth:`TurnService.revoke_for_start`), never a newer one the same phone minted for its
  next start. A confirmed revocation is a ``turn_credential_revoked{reason}`` security event; one the provider refused
  or timed out is ``turn_revoke_failed{reason}``, its username waits in ``turn_pending_revocations`` (the next mint
  cannot erase it) and the sweeper retries it until the provider confirms or the credential expires.
* Caps: a Free account's daily relayed GB checks the larger of the reconciled figure (``turn_usage_days``, filled
  hourly from the provider) and a **provisional upper bound computed here**: every ticketed second of every session
  counted as relayed at the relayed cap (0.5 GB/h) plus, at cap, the TTL of every credential that is not bound to a
  session and not confirmed revoked (``turn_mint_days``). The graduated monthly guard (Data saver past the soft cap,
  STUN only for Free past the hard cap) uses the reconciled figure for the period the provider has covered plus the
  provisional bound only for activity after it, while the latest successful pass (``turn_reconcile_passes``) is under
  3 h old; otherwise, or without an analytics token, the whole month's provisional bound. ``/healthz`` reports the
  figure the guard uses.

Honest label: the Cloudflare request and response shapes below come from the vendor's documentation as read by the
design researchers [research, verify]; nothing here has been run against the real service (KNOWN_ISSUES).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, Protocol

import httpx
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from dome_api.db.models import (
    Account,
    ScreenReservation,
    TurnCredential,
    TurnMintDay,
    TurnPendingRevocation,
    TurnReconcilePass,
    TurnUsageDay,
)
from dome_api.logging import get_logger
from dome_api.screen.metering import lock_account
from dome_api.security import events
from dome_api.settings import Settings

log = get_logger("dome_api.screen.turn")

TurnStatus = Literal["available", "unavailable", "disabled"]
ProviderErrorReason = Literal["timeout", "provider_error", "auth", "bad_request", "unconfigured"]
GB = 1_000_000_000
# the relayed bitrate cap (1.0 Mbps video + audio + overhead ≈ 0.5 GB/h, design §14) as bytes per second
RELAYED_CAP_BYTES_PER_SECOND = 500 * GB // 1000 // 3600
DATA_SAVER_KBPS = 500
CLOUDFLARE_TURN_API = "https://rtc.live.cloudflare.com/v1/turn/keys/{key_id}"
CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"
HTTP_TIMEOUT_SECONDS = 3.0
# consecutive provider failures before minting is paused (STUN only) for BREAKER_COOLDOWN_SECONDS
BREAKER_FAILURES = 3
BREAKER_COOLDOWN_SECONDS = 300.0
# the reconciled figures are trusted for the guard while the latest pass is younger than this
RECONCILE_STALE_SECONDS = 3 * 3600
# the provider's figures for the last stretch before a pass may still be landing: the guard adds the provisional
# bound from this long before the pass (a small, conservative overlap)
RECONCILE_LAG_SECONDS = 3600
PASS_KEEP_DAYS = 62  # usage rows, mint counters and pass records older than this are forgotten
# a revocation the provider did not confirm is retried at most this often by the sweeper
REVOKE_RETRY_SECONDS = 30.0


class TurnProviderError(Exception):
    """A provider call that did not produce its object. The text is the reason only: never a token, never a body."""

    def __init__(self, reason: ProviderErrorReason, *, status: int | None = None) -> None:
        super().__init__(reason)
        self.reason: ProviderErrorReason = reason
        self.status = status


@dataclass(frozen=True, slots=True)
class MintedCredential:
    username: str
    credential: str  # handed to the phone once; never stored, never logged
    urls: list[str]
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class UsageRow:
    custom_identifier: str  # the account id the relay passed when minting
    username: str
    day: date
    egress_bytes: int


class TurnProvider(Protocol):
    name: str

    async def mint(self, *, ttl_seconds: int, custom_identifier: str, now: datetime) -> MintedCredential: ...

    async def revoke(self, username: str) -> None: ...

    async def usage(self, *, since: datetime, until: datetime) -> list[UsageRow]: ...

    async def aclose(self) -> None: ...


class NoTurn:
    """No TURN key: the phone gets STUN only and ``turn: unavailable`` (an honest reason, never a fake credential)."""

    name = "none"

    async def mint(self, *, ttl_seconds: int, custom_identifier: str, now: datetime) -> MintedCredential:
        raise TurnProviderError("unconfigured")

    async def revoke(self, username: str) -> None:
        return None

    async def usage(self, *, since: datetime, until: datetime) -> list[UsageRow]:
        return []

    async def aclose(self) -> None:
        return None


@dataclass
class FakeTurn:
    """In-memory provider for tests: records every mint and revocation, serves scripted usage rows, fails on demand.
    Credentials carry a sentinel so tests can assert they never reach a log or a table."""

    name: str = "fake"
    urls: list[str] = field(
        default_factory=lambda: ["turn:turn.example.net:3478?transport=udp", "turns:turn.example.net:443?transport=tcp"]
    )
    minted: list[dict[str, Any]] = field(default_factory=list)
    revoked: list[str] = field(default_factory=list)
    usage_rows: list[UsageRow] = field(default_factory=list)
    fail_mint: ProviderErrorReason | None = None
    fail_revoke: bool = False
    fail_usage: bool = False
    counter: int = 0
    closed: bool = False

    async def mint(self, *, ttl_seconds: int, custom_identifier: str, now: datetime) -> MintedCredential:
        if self.fail_mint is not None:
            raise TurnProviderError(self.fail_mint)
        self.counter += 1
        expires = now + timedelta(seconds=ttl_seconds)
        username = f"{int(expires.timestamp())}:{custom_identifier[:8]}:{self.counter}"
        self.minted.append({"ttl": ttl_seconds, "custom_identifier": custom_identifier, "username": username})
        return MintedCredential(
            username=username,
            credential=f"FAKE-TURN-CREDENTIAL-{self.counter}-NEVER-LOGGED",
            urls=list(self.urls),
            expires_at=expires,
        )

    async def revoke(self, username: str) -> None:
        if self.fail_revoke:
            raise TurnProviderError("provider_error")
        self.revoked.append(username)

    # (minute, custom_identifier, username, egress bytes): served like the real analytics API, which filters by
    # ``datetimeMinute`` and groups by ``date``, so a window that starts mid-day sees only part of that day
    minute_usage: list[tuple[datetime, str, str, int]] = field(default_factory=list)
    usage_calls: list[tuple[datetime, datetime]] = field(default_factory=list)

    async def usage(self, *, since: datetime, until: datetime) -> list[UsageRow]:
        if self.fail_usage:
            raise TurnProviderError("provider_error")
        self.usage_calls.append((since, until))
        grouped: dict[tuple[str, str, date], int] = {}
        for at, cid, username, egress in self.minute_usage:
            if since <= at < until:
                key = (cid, username, at.astimezone(UTC).date())
                grouped[key] = grouped.get(key, 0) + egress
        return list(self.usage_rows) + [UsageRow(c, u, d, b) for (c, u, d), b in grouped.items()]

    async def aclose(self) -> None:
        self.closed = True


class CloudflareTurn:
    """Cloudflare Realtime TURN over httpx. Request shapes per the vendor documentation as read by the design's
    researchers [research, verify]: ``POST .../credentials/generate-ice-servers {ttl, customIdentifier}`` answers
    ``{"iceServers": {"urls": [...], "username", "credential"}}``; ``POST .../credentials/{username}/revoke`` ends a
    credential; usage comes from the GraphQL Analytics API (``callsTurnUsageAdaptiveGroups``, ``egressBytes`` grouped
    by ``customIdentifier`` and ``username``) read with the separate analytics token. Never logs a response body."""

    name = "cloudflare"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._base = CLOUDFLARE_TURN_API.format(key_id=settings.turn_key_id)
        self._token = settings.turn_api_token.get_secret_value().strip()
        self._analytics_token = settings.turn_analytics_token.get_secret_value().strip()
        self._client = httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS, transport=transport)
        self._account_tag: str | None = None

    @staticmethod
    def _failure(exc: Exception) -> TurnProviderError:
        if isinstance(exc, httpx.TimeoutException):
            return TurnProviderError("timeout")
        return TurnProviderError("provider_error")

    async def _post(self, url: str, body: dict[str, Any] | None, token: str) -> Any:
        try:
            response = await self._client.post(url, json=body, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            raise self._failure(exc) from None
        if response.status_code in (401, 403):
            raise TurnProviderError("auth", status=response.status_code)
        if response.status_code == 400:
            raise TurnProviderError("bad_request", status=400)
        if response.status_code >= 300:
            raise TurnProviderError("provider_error", status=response.status_code)
        if not response.content:
            return None  # 204 on revoke
        try:
            return response.json()
        except ValueError:
            raise TurnProviderError("provider_error", status=response.status_code) from None

    async def mint(self, *, ttl_seconds: int, custom_identifier: str, now: datetime) -> MintedCredential:
        body = await self._post(
            f"{self._base}/credentials/generate-ice-servers",
            {"ttl": int(ttl_seconds), "customIdentifier": custom_identifier},
            self._token,
        )
        servers = body.get("iceServers") if isinstance(body, dict) else None
        if isinstance(servers, dict):
            servers = [servers]
        if not isinstance(servers, list):
            raise TurnProviderError("provider_error")
        for entry in servers:
            if not isinstance(entry, dict):
                continue
            urls = entry.get("urls")
            if isinstance(urls, str):
                urls = [urls]
            username, credential = entry.get("username"), entry.get("credential")
            if isinstance(urls, list) and isinstance(username, str) and isinstance(credential, str):
                turn_urls = [u for u in urls if isinstance(u, str) and u.startswith(("turn:", "turns:"))]
                if turn_urls:
                    return MintedCredential(
                        username=username,
                        credential=credential,
                        urls=turn_urls[:8],
                        expires_at=now + timedelta(seconds=ttl_seconds),
                    )
        raise TurnProviderError("provider_error")

    async def revoke(self, username: str) -> None:
        await self._post(f"{self._base}/credentials/{username}/revoke", None, self._token)

    async def _account_tag_for_analytics(self) -> str:
        """The Cloudflare account the analytics token belongs to (needed as the GraphQL ``accountTag``)."""
        if self._account_tag is not None:
            return self._account_tag
        try:
            response = await self._client.get(
                f"{CLOUDFLARE_API}/accounts",
                params={"per_page": 1},
                headers={"Authorization": f"Bearer {self._analytics_token}"},
            )
        except httpx.HTTPError as exc:
            raise self._failure(exc) from None
        if response.status_code in (401, 403):
            raise TurnProviderError("auth", status=response.status_code)
        try:
            result = response.json().get("result")
        except (ValueError, AttributeError):
            raise TurnProviderError("provider_error", status=response.status_code) from None
        if not isinstance(result, list) or not result or not isinstance(result[0], dict):
            raise TurnProviderError("provider_error", status=response.status_code)
        tag = result[0].get("id")
        if not isinstance(tag, str) or not tag:
            raise TurnProviderError("provider_error", status=response.status_code)
        self._account_tag = tag
        return tag

    async def usage(self, *, since: datetime, until: datetime) -> list[UsageRow]:
        if not self._analytics_token:
            raise TurnProviderError("unconfigured")
        tag = await self._account_tag_for_analytics()
        query = (
            "query($accountTag: string!, $since: Time!, $until: Time!) { viewer { accounts(filter: {accountTag: "
            "$accountTag}) { callsTurnUsageAdaptiveGroups(limit: 10000, filter: {datetimeMinute_geq: $since, "
            "datetimeMinute_lt: $until}) { dimensions { customIdentifier username date } sum { egressBytes } } } } }"
        )
        variables = {
            "accountTag": tag,
            "since": since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "until": until.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        body = await self._post(
            f"{CLOUDFLARE_API}/graphql", {"query": query, "variables": variables}, self._analytics_token
        )
        return parse_usage_response(body)

    async def aclose(self) -> None:
        await self._client.aclose()


def parse_usage_response(body: Any) -> list[UsageRow]:
    """``UsageRow`` list from a GraphQL answer; unknown shapes raise, malformed entries are skipped."""
    try:
        accounts = body["data"]["viewer"]["accounts"]
    except (KeyError, TypeError):
        raise TurnProviderError("provider_error") from None
    rows: list[UsageRow] = []
    for account in accounts if isinstance(accounts, list) else []:
        groups = account.get("callsTurnUsageAdaptiveGroups") if isinstance(account, dict) else None
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, dict):
                continue
            dims, sums = group.get("dimensions"), group.get("sum")
            if not isinstance(dims, dict) or not isinstance(sums, dict):
                continue
            cid, username, day, egress = (
                dims.get("customIdentifier"),
                dims.get("username"),
                dims.get("date"),
                sums.get("egressBytes"),
            )
            if not isinstance(cid, str) or not isinstance(username, str) or not isinstance(day, str):
                continue
            if not isinstance(egress, int | float) or isinstance(egress, bool):
                continue
            try:
                parsed_day = date.fromisoformat(day[:10])
            except ValueError:
                continue
            rows.append(UsageRow(cid, username, parsed_day, max(0, int(egress))))
    return rows


def build_turn_provider(settings: Settings) -> TurnProvider:
    """The configured provider, created once in the lifespan."""
    provider = settings.effective_turn_provider
    if provider == "fake":
        if settings.env not in ("development", "test"):  # settings refuse it too; never reachable in production
            raise RuntimeError("the fake TURN provider is for development and tests only")
        return FakeTurn()
    if provider == "cloudflare":
        return CloudflareTurn(settings)
    return NoTurn()


@dataclass(frozen=True, slots=True)
class TurnConfig:
    configured: bool
    reconciliation_configured: bool
    soft_cap_gb: int
    hard_cap_gb: int
    free_daily_gb: int
    mints_per_account_month: int
    ttl_unlimited_seconds: int
    start_lifetime_seconds: int  # a credential with no start within this is revoked (screen_start_command_lifetime)
    free_ttl_extra_seconds: int = 60
    free_ttl_max_seconds: int = 3600
    reconcile_interval_seconds: float = 3600.0

    @classmethod
    def from_settings(cls, settings: Settings, *, start_lifetime_seconds: int) -> TurnConfig:
        return cls(
            configured=settings.turn_configured,
            reconciliation_configured=settings.turn_reconciliation_configured,
            soft_cap_gb=settings.screen_turn_soft_cap_gb,
            hard_cap_gb=settings.screen_turn_hard_cap_gb,
            free_daily_gb=settings.screen_turn_free_daily_gb,
            mints_per_account_month=settings.screen_turn_mints_per_account_month,
            ttl_unlimited_seconds=settings.screen_turn_ttl_unlimited_seconds,
            start_lifetime_seconds=start_lifetime_seconds,
            reconcile_interval_seconds=settings.screen_turn_reconcile_interval_seconds,
        )


@dataclass(frozen=True, slots=True)
class MintOutcome:
    turn: TurnStatus
    ice_server: dict[str, Any] | None  # relay-frames#/$defs/screen_ice_server with the credential, or None
    expires_at: datetime | None
    reason: str  # ok | unconfigured | provider_error | breaker_open | cap_daily | cap_monthly_mints | guard_hard


@dataclass(frozen=True, slots=True)
class MonthSummary:
    reconciled_bytes: int  # the provider's figures for the month so far
    provisional_bytes: int  # the whole month's provisional bound
    reconciled_at: datetime | None  # the latest successful pass at or before now
    guard_bytes: int  # what the monthly guard (and /healthz) uses
    basis: Literal["reconciled", "provisional"]

    @property
    def bound_bytes(self) -> int:
        """The figure the monthly guard compares with its caps (``guard_bytes``)."""
        return self.guard_bytes


def month_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def day_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


class TurnService:
    """The relay's TURN bookkeeping. Every method runs in the caller's transaction; the account row is locked where an
    account's counters change (same lock position as the ledger, so mint and reserve cannot deadlock)."""

    def __init__(self, config: TurnConfig, provider: TurnProvider) -> None:
        self.config = config
        self.provider = provider
        self.failures = 0
        self.breaker_open_until: datetime | None = None
        self.last_reconciled_at: datetime | None = None
        self.last_reconcile_error: str | None = None

    # ----- state --------------------------------------------------------------------------------------------------
    @property
    def breaker_open(self) -> bool:
        return self.breaker_open_until is not None

    def status(self, now: datetime) -> Literal["ok", "unconfigured", "error"]:
        if not self.config.configured:
            return "unconfigured"
        if self.breaker_open_until is not None and self.breaker_open_until > now:
            return "error"
        return "ok"

    def _record_failure(self, now: datetime, reason: str) -> None:
        self.failures += 1
        if self.failures >= BREAKER_FAILURES:
            self.breaker_open_until = now + timedelta(seconds=BREAKER_COOLDOWN_SECONDS)
            log.warning("turn.breaker_open", reason=reason, failures=self.failures)

    def _record_success(self) -> None:
        self.failures = 0
        self.breaker_open_until = None

    # ----- figures ------------------------------------------------------------------------------------------------
    async def provisional_bytes(self, db: AsyncSession, since: datetime, account_id: uuid.UUID | None) -> int:
        """Every ticketed second of every session active since ``since`` (created then, or still running or ended
        after it) counted as relayed at the cap, plus at the cap the TTL of every credential minted since that day
        that is neither bound to a session nor confirmed revoked (``turn_mint_days``: the per-controller credential
        row would forget all but the latest)."""
        r, m = ScreenReservation, TurnMintDay
        seconds_stmt = select(func.coalesce(func.sum(r.outstanding_seconds + r.charged_seconds), 0)).where(
            or_(r.created_at >= since, func.coalesce(r.ended_at, r.reserved_until) > since)
        )
        ttl_stmt = select(func.coalesce(func.sum(m.minted_ttl_seconds - m.released_ttl_seconds), 0)).where(
            m.day >= since.astimezone(UTC).date()
        )
        if account_id is not None:
            seconds_stmt = seconds_stmt.where(r.account_id == account_id)
            ttl_stmt = ttl_stmt.where(m.account_id == account_id)
        ttl = max(0, int(await db.scalar(ttl_stmt) or 0))
        seconds = int(await db.scalar(seconds_stmt) or 0) + ttl
        return seconds * RELAYED_CAP_BYTES_PER_SECOND

    async def reconciled_bytes(self, db: AsyncSession, since: datetime, account_id: uuid.UUID | None) -> int:
        u = TurnUsageDay
        stmt = select(func.coalesce(func.sum(u.egress_bytes), 0)).where(u.day >= since.astimezone(UTC).date())
        if account_id is not None:
            stmt = stmt.where(u.account_id == account_id)
        return int(await db.scalar(stmt) or 0)

    async def latest_pass(self, db: AsyncSession, now: datetime) -> datetime | None:
        """The latest successful reconciliation pass at or before ``now`` (persisted: survives a restart)."""
        p = TurnReconcilePass
        at: datetime | None = await db.scalar(
            select(func.max(p.reconciled_at)).where(
                p.reconciled_at <= now, p.reconciled_at > now - timedelta(days=PASS_KEEP_DAYS)
            )
        )
        return at

    async def month_summary(self, db: AsyncSession, now: datetime) -> MonthSummary:
        """The month's figures and the one the guard uses: the provider's figures for the stretch it has covered plus
        the provisional bound only for what came after (from an hour before the latest pass), while that pass is
        under 3 h old; else the larger of the month's reconciled figure and its whole provisional bound."""
        since = month_start(now)
        reconciled = await self.reconciled_bytes(db, since, None)
        provisional = await self.provisional_bytes(db, since, None)
        last = await self.latest_pass(db, now) if self.config.reconciliation_configured else None
        if last is not None and (now - last).total_seconds() <= RECONCILE_STALE_SECONDS:
            after = max(since, last - timedelta(seconds=RECONCILE_LAG_SECONDS))
            guard = reconciled + await self.provisional_bytes(db, after, None)
            return MonthSummary(reconciled, provisional, last, guard, "reconciled")
        return MonthSummary(reconciled, provisional, last, max(reconciled, provisional), "provisional")

    async def data_saver(self, db: AsyncSession, now: datetime, *, free_plan: bool) -> bool:
        """Past the soft cap, relayed Free sessions get Data saver (``relay.screen.relayed_max_kbps``)."""
        if not free_plan or not self.config.configured:
            return False
        summary = await self.month_summary(db, now)
        return summary.bound_bytes >= self.config.soft_cap_gb * GB

    # ----- minting --------------------------------------------------------------------------------------------------
    def ttl_for(self, *, free_plan: bool, remaining_seconds: int | None) -> int:
        if not free_plan or remaining_seconds is None:
            return self.config.ttl_unlimited_seconds
        return max(1, min(self.config.free_ttl_max_seconds, remaining_seconds + self.config.free_ttl_extra_seconds))

    async def mint(
        self,
        db: AsyncSession,
        *,
        account_id: uuid.UUID,
        controller_id: uuid.UUID,
        free_plan: bool,
        remaining_seconds: int | None,
        now: datetime,
    ) -> MintOutcome:
        """One credential for this controller (the previous one is revoked first), unless a cap or the guard refuses
        (``disabled``) or no provider can mint (``unavailable``). Caps check the larger of the reconciled and the
        provisional figure. The account row is locked for the mint counter."""
        if not self.config.configured:
            return MintOutcome("unavailable", None, None, "unconfigured")
        if self.breaker_open_until is not None:
            if self.breaker_open_until > now:
                return MintOutcome("unavailable", None, None, "breaker_open")
            self.breaker_open_until = None
        account = await lock_account(db, account_id, now)
        month = month_start(now)
        if account.turn_mints_month_start != month:
            account.turn_mints_month_start = month
            account.turn_mints_this_month = 0
        cap: str | None = None
        if account.turn_mints_this_month >= self.config.mints_per_account_month:
            cap = "monthly_mints"
        elif free_plan:
            today = day_start(now)
            daily = max(
                await self.reconciled_bytes(db, today, account_id), await self.provisional_bytes(db, today, account_id)
            )
            if daily >= self.config.free_daily_gb * GB:
                cap = "daily"
            elif (await self.month_summary(db, now)).bound_bytes >= self.config.hard_cap_gb * GB:
                cap = "hard"
        if cap is not None:
            await self.revoke_account(db, account_id, "cap_reached", now)
            events.record(
                db,
                account_id=account_id,
                kind="turn_cap_reached",
                severity="warning",
                actor="system",
                subject_id=controller_id,
                detail={"cap": cap},
            )
            log.warning("turn.cap_reached", cap=cap, account_id=str(account_id))
            return MintOutcome("disabled", None, None, f"cap_{cap}" if cap != "hard" else "guard_hard")
        # one outstanding credential per controller: the previous one goes first
        await self.revoke_controller(db, controller_id, "replaced", now)
        ttl = self.ttl_for(free_plan=free_plan, remaining_seconds=remaining_seconds)
        try:
            minted = await self.provider.mint(ttl_seconds=ttl, custom_identifier=str(account_id), now=now)
        except TurnProviderError as exc:
            self._record_failure(now, exc.reason)
            log.warning("turn.mint_failed", reason=exc.reason, status=exc.status)
            return MintOutcome("unavailable", None, None, "provider_error")
        self._record_success()
        account.turn_mints_this_month += 1
        account.updated_at = now
        await db.execute(
            pg_insert(TurnCredential)
            .values(
                controller_id=controller_id,
                account_id=account_id,
                username=minted.username[:256],
                minted_at=now,
                expires_at=minted.expires_at,
                ttl_seconds=ttl,
                command_id=None,
                screen_session_id=None,
                state="outstanding",
                revoked_at=None,
                revoke_reason=None,
            )
            .on_conflict_do_update(
                index_elements=[TurnCredential.controller_id],
                set_={
                    "account_id": account_id,
                    "username": minted.username[:256],
                    "minted_at": now,
                    "expires_at": minted.expires_at,
                    "ttl_seconds": ttl,
                    "command_id": None,
                    "screen_session_id": None,
                    "state": "outstanding",
                    "revoked_at": None,
                    "revoke_reason": None,
                },
            )
        )
        await self._count_ttl(db, account_id, now, minted=ttl)
        log.info("turn.minted", account_id=str(account_id), controller_id=str(controller_id), ttl=ttl)
        ice_server = {"urls": minted.urls, "username": minted.username, "credential": minted.credential}
        return MintOutcome("available", ice_server, minted.expires_at, "ok")

    async def bind_start(self, db: AsyncSession, controller_id: uuid.UUID, command_id: uuid.UUID) -> None:
        """A ``screen.session_start`` from this controller was forwarded: the no-start revocation no longer applies."""
        c = TurnCredential
        await db.execute(
            update(c)
            .where(c.controller_id == controller_id, c.state == "outstanding")
            .values(command_id=command_id, state="bound")
            .execution_options(synchronize_session=False)
        )

    async def bind_session(self, db: AsyncSession, controller_id: uuid.UUID, screen_session_id: str) -> None:
        """The start became a session: its credential is the session's from now on, and its TTL leaves the guard's
        session-less term (the session's ticketed seconds count instead). Once per credential."""
        c = TurnCredential
        bound = (
            await db.execute(
                update(c)
                .where(
                    c.controller_id == controller_id,
                    c.state.in_(("outstanding", "bound")),
                    c.screen_session_id.is_(None),
                )
                .values(screen_session_id=screen_session_id, state="bound")
                .returning(c.account_id, c.minted_at, c.ttl_seconds)
                .execution_options(synchronize_session=False)
            )
        ).first()
        if bound is not None:
            await self._count_ttl(db, bound.account_id, bound.minted_at, released=int(bound.ttl_seconds))

    async def _count_ttl(
        self, db: AsyncSession, account_id: uuid.UUID, minted_at: datetime, *, minted: int = 0, released: int = 0
    ) -> None:
        """Add to the account's counters for the credential's UTC mint day (never subtracted)."""
        day = minted_at.astimezone(UTC).date()
        await db.execute(
            pg_insert(TurnMintDay)
            .values(account_id=account_id, day=day, minted_ttl_seconds=minted, released_ttl_seconds=released)
            .on_conflict_do_update(
                index_elements=[TurnMintDay.account_id, TurnMintDay.day],
                set_={
                    "minted_ttl_seconds": TurnMintDay.minted_ttl_seconds + minted,
                    "released_ttl_seconds": TurnMintDay.released_ttl_seconds + released,
                },
            )
        )

    # ----- revocation -----------------------------------------------------------------------------------------------
    async def _revoke_row(self, db: AsyncSession, row: TurnCredential, reason: str, now: datetime) -> None:
        """Revoke at the provider. Confirmed: the row is ``revoked``, the unused rest of a session-less credential's
        TTL leaves the guard, and ``turn_credential_revoked`` is written. Refused or timed out: the row is
        ``revoke_pending``, the username goes to ``turn_pending_revocations`` (the controller's next mint replaces the
        row, never that) for the sweeper to retry, and ``turn_revoke_failed`` is written: the security events never
        claim a revocation the provider did not confirm."""
        account_id, controller_id, username = row.account_id, row.controller_id, row.username
        expires_at, minted_at, ttl = row.expires_at, row.minted_at, int(row.ttl_seconds)
        session_less = row.screen_session_id is None
        error: str | None = None
        try:
            await self.provider.revoke(username)
        except TurnProviderError as exc:
            error = exc.reason
            log.warning("turn.revoke_failed", reason=exc.reason, controller_id=str(controller_id))
        # a Core UPDATE, not an ORM change: the session does not autoflush, and ``mint`` upserts the same row right
        # after this, so an ORM change would be flushed over the fresh credential at commit
        c = TurnCredential
        await db.execute(
            update(c)
            .where(c.controller_id == controller_id)
            .values(state="revoked" if error is None else "revoke_pending", revoked_at=now, revoke_reason=reason[:32])
            .execution_options(synchronize_session=False)
        )
        db.expunge(row)  # later reads in this session load the row afresh
        if error is not None:
            await self._queue_revocation(db, username, account_id, controller_id, reason, expires_at, error, now)
            events.record(
                db,
                account_id=account_id,
                kind="turn_revoke_failed",
                severity="warning",
                actor="system",
                subject_id=controller_id,
                detail={"reason": reason, "error": error},
            )
            return
        if session_less:
            unused = max(0, ttl - int((now - minted_at).total_seconds()))
            await self._count_ttl(db, account_id, minted_at, released=unused)
        events.record(
            db,
            account_id=account_id,
            kind="turn_credential_revoked",
            severity="info",
            actor="system",
            subject_id=controller_id,
            detail={"reason": reason},
        )
        log.info("turn.revoked", reason=reason, controller_id=str(controller_id))

    async def _queue_revocation(
        self,
        db: AsyncSession,
        username: str,
        account_id: uuid.UUID,
        controller_id: uuid.UUID,
        reason: str,
        expires_at: datetime,
        error: str,
        now: datetime,
    ) -> None:
        q = TurnPendingRevocation
        await db.execute(
            pg_insert(q)
            .values(
                username=username,
                account_id=account_id,
                controller_id=controller_id,
                reason=reason[:32],
                expires_at=expires_at,
                attempts=1,
                last_error=error[:32],
                created_at=now,
                last_attempt_at=now,
            )
            .on_conflict_do_update(
                index_elements=[q.username],
                set_={"attempts": q.attempts + 1, "last_error": error[:32], "last_attempt_at": now},
            )
        )

    async def retry_revocations(self, db: AsyncSession, now: datetime) -> dict[str, int]:
        """The sweeper's pass over unconfirmed revocations: expired credentials need no call and are dropped; the others
        are retried (at most every 30 s each) until the provider confirms."""
        q = TurnPendingRevocation
        expired = await db.execute(delete(q).where(q.expires_at <= now))
        rows = (
            await db.scalars(
                select(q)
                .where(q.expires_at > now, q.last_attempt_at <= now - timedelta(seconds=REVOKE_RETRY_SECONDS))
                .order_by(q.last_attempt_at)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        ).all()
        confirmed = 0
        for row in rows:
            try:
                await self.provider.revoke(row.username)
            except TurnProviderError as exc:
                row.attempts += 1
                row.last_error = exc.reason[:32]
                row.last_attempt_at = now
                log.warning("turn.revoke_retry_failed", reason=exc.reason, attempts=row.attempts)
                continue
            confirmed += 1
            account_id, controller_id, reason, username = row.account_id, row.controller_id, row.reason, row.username
            await db.delete(row)
            c = TurnCredential
            await db.execute(
                update(c)
                .where(c.controller_id == controller_id, c.username == username, c.state == "revoke_pending")
                .values(state="revoked")
                .execution_options(synchronize_session=False)
            )
            events.record(
                db,
                account_id=account_id,
                kind="turn_credential_revoked",
                severity="info",
                actor="system",
                subject_id=controller_id,
                detail={"reason": reason, "retried": True},
            )
            log.info("turn.revoked", reason=reason, controller_id=str(controller_id), retried=True)
        return {"confirmed": confirmed, "expired": int(getattr(expired, "rowcount", 0) or 0)}

    async def revoke_controller(self, db: AsyncSession, controller_id: uuid.UUID, reason: str, now: datetime) -> bool:
        """Revoke the controller's outstanding or bound credential, if any. Returns whether one was revoked."""
        c = TurnCredential
        row = await db.scalar(
            select(c).where(c.controller_id == controller_id, c.state.in_(("outstanding", "bound"))).with_for_update()
        )
        if row is None:
            return False
        await self._revoke_row(db, row, reason, now)
        return True

    async def revoke_for_start(
        self,
        db: AsyncSession,
        controller_id: uuid.UUID,
        command_id: uuid.UUID,
        forwarded_at: datetime,
        reason: str,
        now: datetime,
    ) -> bool:
        """Revoke the credential of ONE start (its failure, a lost forward, its reservation swept without a session):
        the row bound to that start's command, or one still outstanding that was minted before that start was
        forwarded. A credential the phone minted since for its next start is left alone (the per-controller row may
        already hold it)."""
        c = TurnCredential
        row = await db.scalar(
            select(c)
            .where(
                c.controller_id == controller_id,
                c.state.in_(("outstanding", "bound")),
                or_(
                    c.command_id == command_id,
                    and_(c.state == "outstanding", c.command_id.is_(None), c.minted_at <= forwarded_at),
                ),
            )
            .with_for_update()
        )
        if row is None:
            return False
        await self._revoke_row(db, row, reason, now)
        return True

    async def revoke_account(self, db: AsyncSession, account_id: uuid.UUID, reason: str, now: datetime) -> int:
        c = TurnCredential
        rows = (
            await db.scalars(
                select(c).where(c.account_id == account_id, c.state.in_(("outstanding", "bound"))).with_for_update()
            )
        ).all()
        for row in rows:
            await self._revoke_row(db, row, reason, now)
        return len(rows)

    async def revoke_session(self, db: AsyncSession, screen_session_id: str, reason: str, now: datetime) -> bool:
        c = TurnCredential
        row = await db.scalar(
            select(c)
            .where(c.screen_session_id == screen_session_id, c.state.in_(("outstanding", "bound")))
            .with_for_update()
        )
        if row is None:
            return False
        await self._revoke_row(db, row, reason, now)
        return True

    async def sweep(self, db: AsyncSession, now: datetime) -> int:
        """Revoke credentials that got no ``screen.session_start`` within the start lifetime, mark expired ones, and
        retry the revocations the provider has not confirmed."""
        c = TurnCredential
        cutoff = now - timedelta(seconds=self.config.start_lifetime_seconds)
        stale = (
            await db.scalars(
                select(c).where(c.state == "outstanding", c.minted_at < cutoff).order_by(c.minted_at).with_for_update()
            )
        ).all()
        for row in stale:
            await self._revoke_row(db, row, "no_start", now)
        await db.execute(
            update(c)
            .where(c.state.in_(("outstanding", "bound", "revoke_pending")), c.expires_at <= now)
            .values(state="expired")
            .execution_options(synchronize_session=False)
        )
        await self.retry_revocations(db, now)
        return len(stale)

    # ----- reconciliation -------------------------------------------------------------------------------------------
    async def reconcile(self, db: AsyncSession, now: datetime) -> int:
        """Pull the provider's per-account egress for yesterday and today (UTC, whole days from midnight: the analytics
        filter is by minute and the answer is grouped by date, so a window starting mid-day would replace a finished
        day's total with part of it) and write it per account and day. Rows naming an account this relay does not
        know are skipped (counted in the log). A successful pass is recorded. Returns the rows written."""
        if not self.config.reconciliation_configured:
            return 0
        since = day_start(now) - timedelta(days=1)
        try:
            rows = await self.provider.usage(since=since, until=now)
        except TurnProviderError as exc:
            self.last_reconcile_error = exc.reason
            log.warning("turn.reconcile_failed", reason=exc.reason, status=exc.status)
            return 0
        totals: dict[tuple[uuid.UUID, date], int] = {}
        unknown = 0
        for row in rows:
            try:
                account_id = uuid.UUID(row.custom_identifier)
            except ValueError:
                unknown += 1
                continue
            totals[(account_id, row.day)] = totals.get((account_id, row.day), 0) + row.egress_bytes
        written = 0
        known = set((await db.scalars(select(Account.id).where(Account.id.in_([a for a, _ in totals])))).all())
        for (account_id, day), egress in totals.items():
            if account_id not in known:
                unknown += 1
                continue
            await db.execute(
                pg_insert(TurnUsageDay)
                .values(account_id=account_id, day=day, egress_bytes=egress, reconciled_at=now)
                .on_conflict_do_update(
                    index_elements=[TurnUsageDay.account_id, TurnUsageDay.day],
                    set_={"egress_bytes": egress, "reconciled_at": now},
                )
            )
            written += 1
        await db.execute(
            pg_insert(TurnReconcilePass)
            .values(reconciled_at=now, rows=written)
            .on_conflict_do_update(index_elements=[TurnReconcilePass.reconciled_at], set_={"rows": written})
        )
        self.last_reconciled_at = now
        self.last_reconcile_error = None
        log.info("turn.reconciled", rows=written, unknown=unknown)
        return written

    async def forget_expired_usage(self, db: AsyncSession, now: datetime, keep_days: int = PASS_KEEP_DAYS) -> int:
        """Usage rows, mint counters and pass records older than two months are no longer needed by any cap or guard."""
        cutoff = (now - timedelta(days=keep_days)).astimezone(UTC).date()
        result = await db.execute(delete(TurnUsageDay).where(TurnUsageDay.day < cutoff))
        await db.execute(delete(TurnMintDay).where(TurnMintDay.day < cutoff))
        await db.execute(
            delete(TurnReconcilePass).where(TurnReconcilePass.reconciled_at < now - timedelta(days=keep_days))
        )
        return int(getattr(result, "rowcount", 0) or 0)


__all__ = [
    "BREAKER_COOLDOWN_SECONDS",
    "BREAKER_FAILURES",
    "RECONCILE_STALE_SECONDS",
    "DATA_SAVER_KBPS",
    "GB",
    "RELAYED_CAP_BYTES_PER_SECOND",
    "CloudflareTurn",
    "FakeTurn",
    "MintOutcome",
    "MintedCredential",
    "MonthSummary",
    "NoTurn",
    "TurnConfig",
    "TurnProvider",
    "TurnProviderError",
    "TurnService",
    "UsageRow",
    "build_turn_provider",
    "day_start",
    "month_start",
    "parse_usage_response",
]
