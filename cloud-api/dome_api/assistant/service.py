"""The assistant service: status, consent and one interpreted turn (docs/design/assistant.md §3.5–§3.8).

A turn is: configured? → one turn in flight per account → per-account token bucket → strict body → consent →
effective limits → commit the request's transaction → reserve (allowance, cost cap, failed turns, ceiling) → the
provider call inside the turn deadline, raced against the client going away → validate → settle → respond → one log
line. Nothing the owner said, the context, the model's reply or the steps is ever logged or stored; the ledger gets
counts and micro-dollars. The response carries steps the phone checks again, resolves, shows and signs itself; this
service never builds, signs or sends a command.

Everything a test needs to vary (enabled flag, provider, dev grants, ceiling, reserve, failed-turn limit, deadline,
rate limit, clock) lives in a replaceable :class:`AssistantConfig` and the replaceable ``provider`` attribute, because
the suite's app is built once from frozen settings.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from dome_protocol import Registry
from fastapi import Request
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dome_api.assistant import metering
from dome_api.assistant.catalogue import CONSENT_VERSION, OUTPUT_SCHEMA, SYSTEM_TEXT
from dome_api.assistant.metering import Limits, Reservation, Settlement
from dome_api.assistant.pricing import CacheTtl, cost_micro_usd, unknown_cost_bound
from dome_api.assistant.provider import AssistantProvider, ProviderError, ProviderProgress, ProviderRequest
from dome_api.assistant.validate import AssistantOutcome, InvalidOutputError, clean_text, validate_output
from dome_api.db.models import Account
from dome_api.errors import ApiError
from dome_api.logging import get_logger
from dome_api.plans import Plan, RateLimit, plan_for
from dome_api.security import events
from dome_api.security.ratelimit import TokenBucketLimiter
from dome_api.settings import MICRO_USD_PER_CENT, Settings
from dome_api.util import rest_response, strict_body, ts, utcnow

log = get_logger("dome_api.assistant")

# What an invalid model output becomes: a question, never a guess, and never charged (§3.5).
INVALID_REPLY = "I'm sorry, I didn't manage that. Could you put it another way?"
CANCELLED_STATUS = 499  # nobody receives it: the client went away (nginx's "client closed request")
SWEEP_INTERVAL_SECONDS = 60.0
SWEEP_GRACE_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class AssistantConfig:
    enabled: bool
    model: str
    max_output_tokens: int
    cache_ttl: CacheTtl
    deadline_seconds: float  # enforced around the provider call
    reported_deadline_seconds: int  # what GET /v1/assistant tells the phone (it aborts 5 s after it)
    reserve_micro_usd: int
    ceiling_micro_usd: int
    failed_turns_per_period: int
    dev_grant_interpretations: int
    dev_grant_cost_cap_cents: int
    turn_rate_limit: RateLimit | None = None  # None: the plan's ai_turn_rate_limit
    clock: Callable[[], datetime] = utcnow
    disconnect_poll_seconds: float = 0.5
    sweep_interval_seconds: float = SWEEP_INTERVAL_SECONDS

    @classmethod
    def from_settings(cls, settings: Settings) -> AssistantConfig:
        return cls(
            enabled=settings.ai_enabled,
            model=settings.ai_model,
            max_output_tokens=settings.ai_max_output_tokens,
            cache_ttl=settings.ai_cache_ttl,
            deadline_seconds=float(settings.ai_turn_deadline_seconds),
            reported_deadline_seconds=settings.ai_turn_deadline_seconds,
            reserve_micro_usd=settings.ai_turn_reserve_micro_usd,
            ceiling_micro_usd=settings.ai_monthly_ceiling_cents * MICRO_USD_PER_CENT,
            failed_turns_per_period=settings.ai_failed_turns_per_period,
            dev_grant_interpretations=settings.ai_dev_grant_interpretations,
            dev_grant_cost_cap_cents=settings.ai_dev_grant_cost_cap_cents,
        )

    def limits(self, plan: Plan) -> Limits:
        """Effective per-account limits: max(plan value, development grant) (§1.5)."""
        return Limits(
            interpretations=max(plan.ai_interpretations_per_period, self.dev_grant_interpretations),
            cost_cap_micro_usd=max(plan.ai_cost_cap_cents_per_period, self.dev_grant_cost_cap_cents)
            * MICRO_USD_PER_CENT,
            failed_turns=self.failed_turns_per_period,
        )


def build_provider(settings: Settings) -> AssistantProvider | None:
    """The configured provider, or ``None`` while the assistant is off. Created once in the lifespan."""
    if not settings.ai_enabled or not settings.ai_provider:
        return None
    if settings.ai_provider == "fake":
        if settings.env not in ("development", "test"):  # settings refuse it too; never reachable in production
            raise RuntimeError("the fake AI provider is for development and tests only")
        from dome_api.assistant.fake_provider import FakeProvider

        return FakeProvider()
    from dome_api.assistant.anthropic_provider import AnthropicProvider

    return AnthropicProvider(settings)


def build_user_json(body: dict[str, Any], text: str, recent: list[dict[str, str]]) -> str:
    """The only message (§2.5), after the cache breakpoint. Copied field by field from the validated request, so
    nothing the schema did not name can ever be forwarded."""
    ctx = body["context"]
    persona = {"name": ctx["persona"]["name"]}
    if "address" in ctx["persona"]:
        persona["address"] = ctx["persona"]["address"]
    context: dict[str, Any] = {
        "persona": persona,
        "pc_connection": ctx["pc_connection"],
        "playback": {"youtube": ctx["playback"]["youtube"], "media": ctx["playback"]["media"]},
    }
    for key in ("pc_volume", "youtube_volume", "search_results", "part_of_day"):
        if key in ctx:
            context[key] = ctx[key]
    if "capabilities" in ctx:
        context["capabilities"] = sorted(ctx["capabilities"])
    document = {"context": context, "recent": recent, "request": {"source": body["source"], "text": text}}
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(slots=True)
class _TurnRecord:
    """What the one log line and the ledger may say about a turn: codes and numbers, never text."""

    outcome: str = "pending"
    mode: str | None = None
    actions: tuple[str, ...] = ()
    charged: bool = False
    settlement: Settlement | None = None
    refusal_category: str | None = None
    invalid_reason: str | None = None
    attempts: int = 0


@dataclass(slots=True)
class _Flight:
    token: object = field(default_factory=object)


class AssistantService:
    def __init__(
        self,
        *,
        config: AssistantConfig,
        provider: AssistantProvider | None,
        db: async_sessionmaker[AsyncSession],
        registry: Registry,
        validate_responses: bool = True,
    ) -> None:
        self.config = config
        self.provider = provider
        self.db = db
        self.registry = registry
        self.validate_responses = validate_responses  # outside production every body is checked against the contract
        # account id -> the turn holding it (single process, ADR-0001 D1); a cancelled turn releases only its own
        self.in_flight: dict[uuid.UUID, _Flight] = {}
        self._limiters: dict[tuple[str, int, int], TokenBucketLimiter] = {}
        self._sweeper: asyncio.Task[None] | None = None

    # ----- state ----------------------------------------------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return self.config.enabled and self.provider is not None

    @property
    def provider_name(self) -> Literal["anthropic", "fake", "none"]:
        return self.provider.name if self.configured and self.provider is not None else "none"

    def _limiter(self, plan: Plan) -> TokenBucketLimiter:
        rate = self.config.turn_rate_limit or plan.ai_turn_rate_limit
        key = (plan.id, rate.per_minute, rate.burst)
        limiter = self._limiters.get(key)
        if limiter is None:
            limiter = self._limiters[key] = TokenBucketLimiter(rate.per_minute, rate.burst)
        return limiter

    def _unknown_cost(self, progress: ProviderProgress) -> int:
        """An unfinished call's cost (§3.7): a bound when exactly one attempt opened and reported its message_start
        without a fallback, else the full reservation."""
        if progress.opened_attempts == 1 and progress.last_start is not None and not progress.fallback_seen:
            return unknown_cost_bound(progress.last_start, self.config.max_output_tokens, self.config.cache_ttl)
        return self.config.reserve_micro_usd

    async def status_body(self, db: AsyncSession, account: Account) -> dict[str, Any]:
        """rest.schema.json#/$defs/assistant_status_response"""
        now = self.config.clock()
        limits = self.config.limits(plan_for(account.plan))
        view = await metering.allowance(db, account.id, now, limits, self.config.reserve_micro_usd)
        reason: str | None = None
        if not self.configured:
            reason = "not_configured"
        elif limits.interpretations == 0:
            reason = "not_in_plan"
        elif view.exhausted is not None:
            reason = "allowance_exhausted"
        elif await metering.ceiling_reached(db, now, self.config.reserve_micro_usd, self.config.ceiling_micro_usd):
            reason = "spend_limit"
        consent: dict[str, Any] | None = None
        if account.assistant_consent_version and account.assistant_consented_at:
            consent = {"version": account.assistant_consent_version, "given_at": ts(account.assistant_consented_at)}
        return {
            "available": reason is None,
            "unavailable_reason": reason,
            "provider": self.provider_name,
            "turn_deadline_seconds": self.config.reported_deadline_seconds,
            "consent_version": CONSENT_VERSION,
            "consent": consent,
            "allowance": view.body(),
        }

    # ----- consent --------------------------------------------------------------------------------------------------
    async def give_consent(self, db: AsyncSession, account: Account) -> dict[str, Any]:
        if not self.configured or self.config.limits(plan_for(account.plan)).interpretations == 0:
            raise ApiError(403, "AI_NOT_AVAILABLE")
        account.assistant_consent_version = CONSENT_VERSION
        account.assistant_consented_at = utcnow()
        events.record(
            db,
            account_id=account.id,
            kind="assistant_consent_given",
            severity="info",
            actor="account",
            detail={"version": CONSENT_VERSION},
        )
        log.info("assistant.consent", change="given", version=CONSENT_VERSION, account_id=str(account.id))
        await db.flush()
        return await self.status_body(db, account)

    async def withdraw_consent(self, db: AsyncSession, account: Account) -> None:
        """Takes effect for the next turn of every phone on the account. Idempotent."""
        if account.assistant_consent_version is None and account.assistant_consented_at is None:
            return
        version = account.assistant_consent_version
        account.assistant_consent_version = None
        account.assistant_consented_at = None
        events.record(
            db,
            account_id=account.id,
            kind="assistant_consent_withdrawn",
            severity="info",
            actor="account",
            detail={"version": version},
        )
        log.info("assistant.consent", change="withdrawn", version=version, account_id=str(account.id))
        await db.flush()

    # ----- a turn ---------------------------------------------------------------------------------------------------
    async def turn(self, request: Request, db: AsyncSession, account: Account) -> Response:
        started = time.monotonic()
        account_id = account.id
        if not self.configured:
            raise ApiError(403, "AI_NOT_AVAILABLE")
        # Busy first: the refusal does no work and reserves nothing, so the phone's resend after a Cancel the server
        # never saw (§4.3) does not use up the account's bucket.
        if account_id in self.in_flight:
            raise ApiError(409, "AI_BUSY")
        plan = plan_for(account.plan)
        rate = self.config.turn_rate_limit or plan.ai_turn_rate_limit
        if not self._limiter(plan).allow(str(account_id)):
            raise ApiError(429, "RATE_LIMITED", headers={"Retry-After": str(math.ceil(60 / rate.per_minute))})
        flight = self.in_flight[account_id] = _Flight()
        try:
            return await self._turn(request, db, account, plan, flight, started)
        finally:
            self._release(account_id, flight)

    def _release(self, account_id: uuid.UUID, flight: _Flight) -> None:
        if self.in_flight.get(account_id) is flight:
            del self.in_flight[account_id]

    async def _turn(
        self, request: Request, db: AsyncSession, account: Account, plan: Plan, flight: _Flight, started: float
    ) -> Response:
        account_id = account.id
        body = await strict_body(request, "assistant_turn_request")
        # The schema already refused controls and the BMP's format characters; supplementary-plane ones (Unicode tag
        # characters can hide instructions) are removed here before anything reaches the model.
        text = clean_text(str(body["text"]))
        if not text:
            raise ApiError(400, "MALFORMED_MESSAGE")
        recent: list[dict[str, str]] = []
        for line in body["recent"]:
            cleaned = clean_text(str(line["text"]))
            if cleaned:
                recent.append({"text": cleaned, "who": str(line["who"])})
        if account.assistant_consent_version != CONSENT_VERSION:
            raise ApiError(403, "AI_CONSENT_REQUIRED")
        limits = self.config.limits(plan)
        if limits.interpretations == 0:
            raise ApiError(403, "AI_NOT_AVAILABLE")
        user_json = build_user_json(body, text, recent)
        client_turn_id = uuid.UUID(str(body["turn_id"]))
        # Nothing may hold a connection or a transaction while the model thinks.
        await db.commit()

        provider = self.provider
        assert provider is not None  # configured
        try:
            reservation = await metering.reserve(
                self.db,
                account_id=account_id,
                client_turn_id=client_turn_id,
                now=self.config.clock(),
                limits=limits,
                reserve_micro_usd=self.config.reserve_micro_usd,
                ceiling_micro_usd=self.config.ceiling_micro_usd,
            )
        except metering.TurnDuplicateError:
            raise ApiError(409, "AI_TURN_DUPLICATE") from None
        except metering.AllowanceExhaustedError as exc:
            raise ApiError(
                429, "AI_ALLOWANCE_EXHAUSTED", detail={"resets_at": ts(exc.resets_at), "reason": exc.reason}
            ) from None
        except metering.SpendLimitError:
            raise ApiError(503, "AI_SPEND_LIMIT_REACHED") from None

        record = _TurnRecord()
        try:
            return await self._call_and_answer(
                request, provider, user_json, reservation, record, account_id, flight, body, limits, started
            )
        finally:
            self._log_turn(account_id, client_turn_id, record, started)

    async def _call_and_answer(
        self,
        request: Request,
        provider: AssistantProvider,
        user_json: str,
        reservation: Reservation,
        record: _TurnRecord,
        account_id: uuid.UUID,
        flight: _Flight,
        body: dict[str, Any],
        limits: Limits,
        started: float,
    ) -> Response:
        progress = ProviderProgress()
        call = asyncio.create_task(provider.complete(ProviderRequest(SYSTEM_TEXT, OUTPUT_SCHEMA, user_json), progress))
        stop_watching = asyncio.Event()
        watch = asyncio.create_task(self._watch_disconnect(request, stop_watching))
        timed_out = False
        try:
            async with asyncio.timeout(self.config.deadline_seconds):
                await asyncio.wait({call, watch}, return_when=asyncio.FIRST_COMPLETED)
        except TimeoutError:
            timed_out = True
        finally:
            # Stopped by an event, not by cancellation alone: Starlette's is_disconnected() runs inside an anyio
            # cancel scope that absorbs a task cancellation arriving while it is inside, and the watcher would then
            # keep polling until the client gave up.
            stop_watching.set()
            watch.cancel()
            if not call.done():
                call.cancel()
            # let a cancelled provider close its stream before the turn is settled; our own cancellation propagates
            await asyncio.gather(call, watch, return_exceptions=True)

        if call.cancelled():
            # Unfinished: the deadline passed, or the client went away first (the owner pressed Cancel). Either way
            # the provider may have billed tokens nobody reported: settle by the unknown-cost rule.
            cost = self._unknown_cost(progress)
            if timed_out:
                await self._settle(record, reservation, Settlement("timeout", False, cost, latency_ms=_ms(started)))
                raise ApiError(504, "AI_UNAVAILABLE", detail={"reason": "timeout"})
            self._release(account_id, flight)  # at once, so a rephrased request is not answered AI_BUSY
            await self._settle(record, reservation, Settlement("cancelled", False, cost, latency_ms=_ms(started)))
            return Response(status_code=CANCELLED_STATUS, headers={"Cache-Control": "no-store"})

        error = call.exception()
        if error is not None:
            reason = error.reason if isinstance(error, ProviderError) else "provider_error"
            unknown = error.cost_unknown if isinstance(error, ProviderError) else True
            log.warning(
                "assistant.provider_error",
                reason=reason,
                status=getattr(error, "status", None),
                error_type=getattr(error, "error_type", None),
                error_class=getattr(error, "error_class", None) or type(error).__name__,
                provider_request_id=getattr(error, "request_id", None),
                cost_unknown=unknown,
            )
            status = "timeout" if reason == "timeout" else "provider_error"
            cost = self._unknown_cost(progress) if unknown else 0
            await self._settle(
                record,
                reservation,
                Settlement(
                    status,
                    False,
                    cost,
                    provider_request_id=getattr(error, "request_id", None),
                    latency_ms=_ms(started),
                ),
            )
            raise ApiError(504 if reason == "timeout" else 502, "AI_UNAVAILABLE", detail={"reason": reason})

        result = call.result()
        record.refusal_category = result.refusal_category
        record.attempts = len(result.attempts)
        cost = cost_micro_usd(result.attempts, self.config.cache_ttl)
        usage = {
            "input_tokens": sum(a.input_tokens for a in result.attempts),
            "output_tokens": sum(a.output_tokens for a in result.attempts),
            "cache_read_tokens": sum(a.cache_read_tokens for a in result.attempts),
            "cache_write_tokens": sum(a.cache_write_tokens for a in result.attempts),
        }
        outcome: AssistantOutcome | None = None
        if result.outcome == "ok" and result.text is not None:
            try:
                outcome = validate_output(result.text, self.registry)
            except InvalidOutputError as exc:
                record.invalid_reason = exc.reason
        elif result.outcome != "refused":
            record.invalid_reason = result.outcome  # truncated (max_tokens) or invalid (stop reason, text blocks)
        status = "answered" if outcome else ("refused" if result.outcome == "refused" else "invalid_output")
        await self._settle(
            record,
            reservation,
            Settlement(
                status,
                outcome is not None,
                cost,
                model_served=result.served_model,
                fallback_used=result.fallback_used,
                provider_request_id=result.request_id,
                latency_ms=_ms(started),
                **usage,
            ),
        )
        if status == "refused":
            raise ApiError(422, "AI_DECLINED")
        if outcome is None:
            outcome = AssistantOutcome(mode="ask", reply=INVALID_REPLY, choices=(), handoff="none", steps=())
        record.mode = outcome.mode
        record.actions = outcome.actions
        async with self.db() as db:
            view = await metering.allowance(db, account_id, self.config.clock(), limits, self.config.reserve_micro_usd)
        response = {
            "turn_id": str(body["turn_id"]),
            "mode": outcome.mode,
            "reply": outcome.reply,
            "choices": list(outcome.choices),
            "handoff": outcome.handoff,
            "steps": [dict(step) for step in outcome.steps],
            "charged": status == "answered",
            "allowance": view.body(),
        }
        return rest_response(self.validate_responses, "assistant_turn_response", response)  # type: ignore[no-any-return]

    async def _settle(self, record: _TurnRecord, reservation: Reservation, settlement: Settlement) -> None:
        await metering.settle(self.db, reservation, settlement, self.config.clock())
        # only after the write: if it fails, the line says pending, as the ledger does until the sweeper repairs it
        record.outcome = settlement.status
        record.charged = settlement.charged
        record.settlement = settlement

    async def _watch_disconnect(self, request: Request, stop: asyncio.Event) -> bool:
        """True as soon as the client has gone away; False once ``stop`` is set. Through the Cloudflare tunnel a
        disconnect may never reach uvicorn; then the turn simply completes (the phone's AI_BUSY handling covers it)."""
        while not stop.is_set():
            try:
                if await request.is_disconnected():
                    return True
            except Exception:  # noqa: BLE001 - an unreadable connection state is not a disconnect; stop watching
                await stop.wait()
                return False
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(self.config.disconnect_poll_seconds):
                    await stop.wait()
        return False

    def _log_turn(self, account_id: uuid.UUID, turn_id: uuid.UUID, record: _TurnRecord, started: float) -> None:
        """One line per turn (§3.10): codes, action names and numbers; never the words, recent lines, context,
        reply, choices, app names, parameters or queries."""
        s = record.settlement
        log.info(
            "assistant.turn",
            account_id=str(account_id),
            turn_id=str(turn_id),
            outcome=record.outcome,
            mode=record.mode,
            actions=list(record.actions),
            charged=record.charged,
            input_tokens=s.input_tokens if s else 0,
            output_tokens=s.output_tokens if s else 0,
            cache_read_tokens=s.cache_read_tokens if s else 0,
            cache_write_tokens=s.cache_write_tokens if s else 0,
            cost_micro_usd=s.cost_micro_usd if s else 0,
            model_served=s.model_served if s else None,
            fallback_used=s.fallback_used if s else False,
            latency_ms=s.latency_ms if s else int((time.monotonic() - started) * 1000),
            provider_request_id=s.provider_request_id if s else None,
            refusal_category=record.refusal_category,
            invalid_reason=record.invalid_reason,
            attempts=record.attempts,
        )

    # ----- sweeper and lifecycle ------------------------------------------------------------------------------------
    async def sweep(self) -> int:
        swept = await metering.sweep(
            self.db, self.config.clock(), self.config.reported_deadline_seconds + SWEEP_GRACE_SECONDS
        )
        if swept:
            log.info("assistant.sweep", count=swept)
        return swept

    async def _sweep_loop(self) -> None:
        while True:
            try:
                await self.sweep()
            except Exception as exc:  # noqa: BLE001 - the next pass retries; never let the loop die
                log.warning("assistant.sweep_failed", error_class=type(exc).__name__)
            await asyncio.sleep(self.config.sweep_interval_seconds)

    async def start(self) -> None:
        self._sweeper = asyncio.create_task(self._sweep_loop(), name="assistant-sweeper")

    async def stop(self) -> None:
        if self._sweeper is not None:
            self._sweeper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sweeper
            self._sweeper = None

    async def aclose(self) -> None:
        if self.provider is not None:
            await self.provider.aclose()


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)
