"""A scripted stand-in for the AI provider (docs/design/assistant.md §3.4). Development and tests only: settings refuse
``DOME_AI_PROVIDER=fake`` in staging and production, and the test kit never uses it.

Tests swap it in-process (``services.assistant.provider = FakeProvider([...])``) and read ``requests`` to see exactly
what would have been sent. Each call pops the next scripted outcome; an empty script answers a fixed reply.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Literal

from dome_api.assistant.provider import (
    AttemptUsage,
    ProviderError,
    ProviderErrorReason,
    ProviderProgress,
    ProviderRequest,
    ProviderResult,
)

FAKE_MODEL = "claude-opus-5-5"
DEFAULT_USAGE = AttemptUsage(
    model=FAKE_MODEL, input_tokens=180, output_tokens=60, cache_read_tokens=3600, cache_write_tokens=0
)
DEFAULT_REPLY = json.dumps(
    {"mode": "answer", "reply": "This is DoMe's test assistant.", "choices": [], "handoff": "none", "steps": []}
)


@dataclass(frozen=True, slots=True)
class FakeReply:
    json_text: str
    attempts: tuple[AttemptUsage, ...] = (DEFAULT_USAGE,)
    fallback_used: bool = False
    request_id: str | None = "req_fake"


@dataclass(frozen=True, slots=True)
class FakeRefusal:
    category: str | None = None
    attempts: tuple[AttemptUsage, ...] = (DEFAULT_USAGE,)


@dataclass(frozen=True, slots=True)
class FakeTruncated:
    attempts: tuple[AttemptUsage, ...] = (DEFAULT_USAGE,)


@dataclass(frozen=True, slots=True)
class FakeError:
    reason: ProviderErrorReason
    cost_unknown: bool = False
    status: int | None = None


@dataclass(slots=True)
class FakeHang:
    """Holds the call until ``release()`` (then answers ``then``) or until ``seconds`` pass (then raises a timeout).
    ``started`` is written into the progress first, as if one attempt's stream had opened and reported its
    ``message_start`` usage."""

    seconds: float = 60.0
    started: AttemptUsage | None = None
    then: FakeReply | None = None
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    _released: asyncio.Event = field(default_factory=asyncio.Event)
    cancelled: bool = False

    def release(self) -> None:
        self._released.set()


FakeOutcome = FakeReply | FakeRefusal | FakeTruncated | FakeError | FakeHang


class FakeProvider:
    name: Literal["anthropic", "fake"] = "fake"

    def __init__(self, script: list[FakeOutcome] | None = None) -> None:
        self.script: list[FakeOutcome] = list(script or [])
        self.requests: list[ProviderRequest] = []
        self.closed = False

    async def complete(self, request: ProviderRequest, progress: ProviderProgress) -> ProviderResult:
        self.requests.append(request)
        outcome: FakeOutcome = self.script.pop(0) if self.script else FakeReply(DEFAULT_REPLY)
        if isinstance(outcome, FakeHang):
            if outcome.started is not None:
                progress.opened_attempts += 1
                progress.last_start = outcome.started
            outcome.entered.set()
            try:
                async with asyncio.timeout(outcome.seconds):
                    await outcome._released.wait()
            except TimeoutError:
                raise ProviderError("timeout", cost_unknown=outcome.started is not None) from None
            except asyncio.CancelledError:
                outcome.cancelled = True
                raise
            outcome = outcome.then or FakeReply(DEFAULT_REPLY)
        if isinstance(outcome, FakeError):
            raise ProviderError(
                outcome.reason, status=outcome.status, cost_unknown=outcome.cost_unknown, error_class="FakeError"
            )
        if isinstance(outcome, FakeRefusal):
            return ProviderResult("refused", None, outcome.attempts, FAKE_MODEL, False, "req_fake", outcome.category)
        if isinstance(outcome, FakeTruncated):
            return ProviderResult("truncated", None, outcome.attempts, FAKE_MODEL, False, "req_fake", None)
        assert isinstance(outcome, FakeReply)
        served = outcome.attempts[-1].model if outcome.attempts else FAKE_MODEL
        return ProviderResult(
            "ok", outcome.json_text, outcome.attempts, served, outcome.fallback_used, outcome.request_id, None
        )

    async def aclose(self) -> None:
        self.closed = True
