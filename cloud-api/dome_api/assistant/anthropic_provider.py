"""The Anthropic adapter (docs/design/assistant.md §2.1, §2.7, §2.9, §3.3): one streamed Messages API call to
``claude-opus-5-5`` with structured outputs, adaptive thinking at an explicit effort, server-side refusal fallbacks
and one cached system block.

- Streamed (``beta.messages.stream`` + ``get_final_message()``): the per-attempt read timeout limits the gap between
  chunks and the service's turn deadline limits the whole call. Events are read only to record progress; the reply
  comes from the final message only (after a mid-output fallback, the declined partial plus its continuation).
- No ``thinking`` (always on for this model; omitted means adaptive), no sampling parameters, no prefill, no tools and
  no ``tool_choice`` (a forced choice is a 400 here, and the design needs no tools): the model can only answer with
  the JSON the output schema describes, and the service validates it.
- The SDK's own retries are off. This adapter retries at most ``ai_max_retries`` times, and only failures Anthropic
  does not bill: an HTTP 429 or 529 before the stream opened or, its streaming form, an overload or rate-limit
  ``error`` event before ``message_start``; or a connection that never opened. It counts its attempts itself, so no
  billed attempt goes unmetered.
- ``base_url`` is passed explicitly, so a stray ``ANTHROPIC_BASE_URL`` can never redirect the key and the words
  (start-up also refuses every ``ANTHROPIC_*`` variable while this provider is enabled). Errors are mapped from the
  exception class, status, error type and request id only; the provider's message, the body and the key are never
  logged or put in an error.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Literal

import anthropic
import httpx2
from anthropic.types.beta import (
    BetaCacheControlEphemeralParam,
    BetaMessage,
    BetaMessageParam,
    BetaOutputConfigParam,
    BetaTextBlockParam,
)
from dome_protocol import ProtocolError, loads_strict

from dome_api.assistant.provider import (
    AttemptUsage,
    ProviderError,
    ProviderErrorReason,
    ProviderProgress,
    ProviderRequest,
    ProviderResult,
)
from dome_api.assistant.validate import MAX_OUTPUT_BYTES
from dome_api.settings import Settings

BASE_URL = "https://api.anthropic.com"
# The "default" scalar form of `fallbacks` needs exactly this beta (the array form uses a different one).
FALLBACK_BETA = "server-side-fallback-2026-07-01"
CONNECT_TIMEOUT_SECONDS = 5.0
RETRY_DELAY_SECONDS = 1.0
RETRY_MIN_REMAINING_SECONDS = 10.0
# The HTTP status an error event inside an opened stream stands for (the stream itself answered 200).
_STATUS_FOR_ERROR_TYPE = {
    "invalid_request_error": 400,
    "authentication_error": 401,
    "billing_error": 402,
    "permission_error": 403,
    "not_found_error": 404,
    "request_too_large": 413,
    "rate_limit_error": 429,
    "api_error": 500,
    "timeout_error": 504,
    "overloaded_error": 529,
}


class _UnbilledError(Exception):
    """An attempt that failed before Anthropic could bill it; eligible for DoMe's single retry."""

    def __init__(self, error: ProviderError) -> None:
        super().__init__(error.reason)
        self.error = error


def _int(obj: object, name: str) -> int:
    value = getattr(obj, name, 0)  # SDK types for usage entries may lag the API: read defensively
    return int(value) if isinstance(value, int) else 0


def _status_error(exc: anthropic.APIStatusError, *, opened: bool, started: bool) -> ProviderError | _UnbilledError:
    status = exc.status_code
    error_type = exc.type
    effective = status if status >= 400 else _STATUS_FOR_ERROR_TYPE.get(error_type or "", 500)
    reason: ProviderErrorReason
    if effective in (401, 403):
        reason = "provider_auth"
    elif effective == 402 or error_type == "billing_error":
        reason = "provider_billing"
    elif effective in (429, 529) or error_type in ("rate_limit_error", "overloaded_error"):
        reason = "overloaded"
    elif 400 <= effective < 500:
        reason = "bad_request"  # an operator problem: model id, schema, beta header
    else:
        reason = "provider_error"
    # Unbilled: a 429 or 529 answer, or its streaming form (an overload or rate-limit `error` event before
    # message_start: no model ran, so nothing was billed).
    unbilled = (not opened and status in (429, 529)) or (
        opened and not started and error_type in ("rate_limit_error", "overloaded_error")
    )
    error = ProviderError(
        reason,
        status=effective,
        error_type=error_type,
        request_id=exc.request_id,
        cost_unknown=opened and not unbilled,
        error_class=type(exc).__name__,
    )
    return _UnbilledError(error) if unbilled else error


def map_error(exc: BaseException, *, opened: bool, started: bool) -> ProviderError | _UnbilledError:
    """Most specific first. ``opened``: the attempt's stream had opened, so it may have been billed. ``started``: its
    ``message_start`` had arrived."""
    name = type(exc).__name__
    if isinstance(exc, anthropic.APITimeoutError):  # a subclass of APIConnectionError
        connect_phase = not opened and isinstance(exc.__cause__, httpx2.ConnectTimeout)
        error = ProviderError("timeout", cost_unknown=not connect_phase, error_class=name)
        return _UnbilledError(error) if connect_phase else error
    if isinstance(exc, anthropic.APIStatusError):
        return _status_error(exc, opened=opened, started=started)
    if isinstance(exc, anthropic.APIConnectionError):
        # Only a connection that never opened is certainly unbilled; anything later may have reached Anthropic.
        connect_phase = not opened and isinstance(exc.__cause__, httpx2.ConnectError | httpx2.ConnectTimeout)
        error = ProviderError("provider_error", cost_unknown=not connect_phase, error_class=name)
        return _UnbilledError(error) if connect_phase else error
    if isinstance(exc, httpx2.TimeoutException):  # a read timeout inside an opened stream reaches us unwrapped
        return ProviderError("timeout", cost_unknown=True, error_class=name)
    return ProviderError("provider_error", cost_unknown=True, error_class=name)


class AnthropicProvider:
    name: Literal["anthropic", "fake"] = "anthropic"

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx2.AsyncClient | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._model = settings.ai_model
        self._max_tokens = settings.ai_max_output_tokens
        self._effort: Literal["low", "medium", "high"] = settings.ai_effort
        self._ttl = settings.ai_cache_ttl
        self._max_retries = settings.ai_max_retries
        self._deadline_seconds = float(settings.ai_turn_deadline_seconds)
        self._clock = clock
        self._sleep = sleep
        self._client = anthropic.AsyncAnthropic(
            api_key=settings.ai_anthropic_api_key.get_secret_value(),
            base_url=BASE_URL,
            timeout=anthropic.Timeout(float(settings.ai_timeout_seconds), connect=CONNECT_TIMEOUT_SECONDS),
            max_retries=0,
            http_client=http_client,
        )

    async def aclose(self) -> None:
        await self._client.close()

    async def complete(self, request: ProviderRequest, progress: ProviderProgress) -> ProviderResult:
        deadline_at = self._clock() + self._deadline_seconds
        retries = 0
        while True:
            try:
                return await self._attempt(request, progress)
            except _UnbilledError as unbilled:
                remaining = deadline_at - self._clock()
                if retries >= self._max_retries or remaining < RETRY_MIN_REMAINING_SECONDS + RETRY_DELAY_SECONDS:
                    raise unbilled.error from None
                retries += 1
                await self._sleep(RETRY_DELAY_SECONDS)

    def _system(self, request: ProviderRequest) -> list[BetaTextBlockParam]:
        """The single cached block: the frozen prompt and catalogue. Everything volatile comes after it."""
        cache_control: BetaCacheControlEphemeralParam = {"type": "ephemeral"}
        if self._ttl == "1h":
            cache_control["ttl"] = "1h"
        return [{"type": "text", "text": request.system_text, "cache_control": cache_control}]

    def _output_config(self, request: ProviderRequest) -> BetaOutputConfigParam:
        return {
            "effort": self._effort,  # always explicit: the API default for this model is medium
            "format": {"type": "json_schema", "schema": dict(request.output_schema)},
        }

    async def _attempt(self, request: ProviderRequest, progress: ProviderProgress) -> ProviderResult:
        messages: list[BetaMessageParam] = [{"role": "user", "content": request.user_json}]
        opened = started = False
        try:
            async with self._client.beta.messages.stream(
                model=self._model,
                max_tokens=self._max_tokens,
                betas=[FALLBACK_BETA],
                system=self._system(request),
                messages=messages,
                output_config=self._output_config(request),
                fallbacks="default",
            ) as stream:
                opened = True
                progress.opened_attempts += 1
                progress.last_start = None
                stopped = False
                async for event in stream:
                    if event.type == "message_start":
                        started = True
                        progress.last_start = self._start_usage(event.message)
                    elif event.type == "content_block_start" and event.content_block.type == "fallback":
                        progress.fallback_seen = True
                    elif event.type == "message_stop":
                        stopped = True
                if not stopped:  # the body ended early without an error: the attempt may still have been billed
                    raise ProviderError(
                        "provider_error",
                        cost_unknown=True,
                        request_id=stream.request_id,
                        error_class="IncompleteStream",
                    )
                message = await stream.get_final_message()
                request_id = getattr(message, "_request_id", None) or stream.request_id
        except (ProviderError, _UnbilledError):
            raise
        except Exception as exc:  # noqa: BLE001 - every failure becomes a ProviderError without its message
            mapped = map_error(exc, opened=opened, started=started)
            if opened and isinstance(mapped, _UnbilledError):
                progress.opened_attempts -= 1  # refused before message_start: not an attempt that may cost anything
            raise mapped from None
        return self._result(message, request_id)

    def _start_usage(self, message: BetaMessage) -> AttemptUsage:
        usage = message.usage
        return AttemptUsage(
            model=str(message.model),
            input_tokens=_int(usage, "input_tokens"),
            output_tokens=0,
            cache_read_tokens=_int(usage, "cache_read_input_tokens"),
            cache_write_tokens=_int(usage, "cache_creation_input_tokens"),
        )

    @staticmethod
    def _chain(message: BetaMessage) -> list[str]:
        """The models that ran this turn, in order, from the ``fallback`` blocks: one block per hop whose model ran
        and declined (``from``), naming the model that ran next (``to``). Empty without a block."""
        blocks = [block for block in message.content if block.type == "fallback"]
        if not blocks:
            return []
        hops = [getattr(blocks[0], "from_", None), *(getattr(block, "to", None) for block in blocks)]
        models = [getattr(hop, "model", None) for hop in hops]
        return [m if isinstance(m, str) and m else str(message.model) for m in models]

    def _attempts(self, message: BetaMessage) -> tuple[tuple[AttemptUsage, ...], bool]:
        """One entry per billed attempt (``usage.iterations`` is the per-attempt source of truth), each with the
        model whose rates apply: the entry's own ``model`` when present, else the model that ran that hop by the
        ``fallback`` blocks, else the message's model (no block: every attempt ran on it, a sticky turn included)."""
        usage = message.usage
        served = str(message.model)
        iterations = list(getattr(usage, "iterations", None) or [])
        if not iterations:
            return (
                (
                    AttemptUsage(
                        model=served,
                        input_tokens=_int(usage, "input_tokens"),
                        output_tokens=_int(usage, "output_tokens"),
                        cache_read_tokens=_int(usage, "cache_read_input_tokens"),
                        cache_write_tokens=_int(usage, "cache_creation_input_tokens"),
                    ),
                ),
                False,
            )
        chain = self._chain(message)
        attempts: list[AttemptUsage] = []
        fallback_used = False
        for index, entry in enumerate(iterations):
            kind = getattr(entry, "type", None)
            fallback_used = fallback_used or kind == "fallback_message"
            model = getattr(entry, "model", None)
            if not isinstance(model, str) or not model:
                model = chain[index] if index < len(chain) else served
            attempts.append(
                AttemptUsage(
                    model=model,
                    input_tokens=_int(entry, "input_tokens"),
                    output_tokens=_int(entry, "output_tokens"),
                    cache_read_tokens=_int(entry, "cache_read_input_tokens"),
                    cache_write_tokens=_int(entry, "cache_creation_input_tokens"),
                )
            )
        return tuple(attempts), fallback_used

    def _result(self, message: BetaMessage, request_id: str | None) -> ProviderResult:
        attempts, fallback_used = self._attempts(message)
        served = str(message.model)
        stop = message.stop_reason
        if stop == "refusal":  # the whole fallback chain declined; any partial content is discarded
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None)
            return ProviderResult(
                "refused", None, attempts, served, fallback_used, request_id, str(category)[:32] if category else None
            )
        if stop == "max_tokens":
            return ProviderResult("truncated", None, attempts, served, fallback_used, request_id, None)
        if stop != "end_turn":
            return ProviderResult("invalid", None, attempts, served, fallback_used, request_id, None)
        text = self._reply_text(message)
        if text is None:
            return ProviderResult("invalid", None, attempts, served, fallback_used, request_id, None)
        return ProviderResult("ok", text, attempts, served, fallback_used, request_id, None)

    @staticmethod
    def _reply_text(message: BetaMessage) -> str | None:
        """The reply's one text block (thinking and fallback blocks are skipped by type, never by position). After a
        mid-output fallback the stream keeps the declined model's partial text and the fallback model continues it
        after the ``fallback`` block: the text blocks joined in order, or the text after the last boundary on its own
        if only that parses (a fallback model that started over). Anything else is invalid."""
        texts: list[str] = []
        tail: list[str] = []
        crossed = False  # a fallback block after a text block
        for block in message.content:
            if block.type == "text":
                texts.append(block.text)
                tail.append(block.text)
            elif block.type == "fallback":
                crossed = crossed or bool(texts)
                tail = []
        if not crossed:
            return texts[0] if len(texts) == 1 else None
        for candidate in ("".join(texts), "".join(tail)):
            try:
                loads_strict(candidate, max_bytes=MAX_OUTPUT_BYTES, require_object=True)
            except ProtocolError:
                continue
            return candidate
        return None
