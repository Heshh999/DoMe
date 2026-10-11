"""The interface between the assistant service and an AI provider (docs/design/assistant.md §3.2).

A provider gets the frozen prefix (system text and output schema) and the volatile user JSON, and returns either the
model's JSON text or an outcome code, plus what each billed attempt used. It never sees an account, a session, a
database or a key other than its own; the service does the consent, allowance and metering around it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

ProviderErrorReason = Literal[
    "timeout", "provider_error", "overloaded", "provider_auth", "provider_billing", "bad_request"
]
ProviderOutcome = Literal["ok", "refused", "truncated", "invalid"]


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    system_text: str  # SYSTEM_TEXT: the cached prefix
    output_schema: Mapping[str, Any]  # OUTPUT_SCHEMA: sent on every request unchanged
    user_json: str  # the only message, after the cache breakpoint
    # step 2 adds image blocks here (§7); step 1 never sets anything else


@dataclass(frozen=True, slots=True)
class AttemptUsage:
    """What one billed attempt used. ``model`` is the model whose rates apply to it (§2.7)."""

    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int


@dataclass(frozen=True, slots=True)
class ProviderResult:
    outcome: ProviderOutcome
    text: str | None  # the model's JSON text, only when outcome == "ok"
    attempts: tuple[AttemptUsage, ...]
    served_model: str | None
    fallback_used: bool
    request_id: str | None  # the provider's request id, for support; not secret
    refusal_category: str | None


class ProviderError(Exception):
    """A provider call that produced no message. ``cost_unknown``: an attempt failed after the request reached the
    provider (a read timeout, a dropped stream, an error event), so tokens may have been billed that nobody reported;
    the service then settles the turn by the unknown-cost rule (§3.7). The text is the reason only, never the
    provider's message, a body or a key."""

    def __init__(
        self,
        reason: ProviderErrorReason,
        *,
        status: int | None = None,
        error_type: str | None = None,
        request_id: str | None = None,
        cost_unknown: bool = False,
        error_class: str | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason: ProviderErrorReason = reason
        self.status = status
        self.error_type = error_type
        self.request_id = request_id
        self.cost_unknown = cost_unknown
        self.error_class = error_class


@dataclass(slots=True)
class ProviderProgress:
    """Written by the provider as stream events arrive; read by the service after a deadline, a cancel or a
    ``cost_unknown`` error to bound what the unfinished call may have cost."""

    # attempts whose stream opened (HTTP 200 received), less those refused before message_start (unbilled)
    opened_attempts: int = 0
    last_start: AttemptUsage | None = None  # message_start usage of the latest opened attempt
    fallback_seen: bool = False  # a `fallback` content block started


class AssistantProvider(Protocol):
    name: Literal["anthropic", "fake"]

    async def complete(self, request: ProviderRequest, progress: ProviderProgress) -> ProviderResult: ...

    async def aclose(self) -> None: ...
