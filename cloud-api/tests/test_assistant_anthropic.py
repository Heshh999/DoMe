"""The Anthropic adapter against an HTTP-level stub of the Messages API (docs/design/assistant.md §3.3): no network, no
key, no database. The real SDK builds the request and parses canned event streams; an ``httpx2.MockTransport`` handler
records every request. The client uses the production settings (SDK retries off), so each error case makes exactly
the requests DoMe's own retry rule allows."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest

from dome_api.assistant.anthropic_provider import FALLBACK_BETA, AnthropicProvider
from dome_api.assistant.catalogue import OUTPUT_SCHEMA, SYSTEM_TEXT
from dome_api.assistant.pricing import cost_micro_usd
from dome_api.assistant.provider import ProviderError, ProviderProgress, ProviderRequest, ProviderResult
from dome_api.logging import SDK_LOGGERS, pin_sdk_loggers
from dome_api.settings import Settings

KEY = "sk-ant-api03-STUBKEY-" + "k" * 40
REPLY = json.dumps({"mode": "answer", "reply": "Good evening.", "choices": [], "handoff": "none", "steps": []})
USER_JSON = '{"context":{},"recent":[],"request":{"source":"speech","text":"good evening"}}'
Handler = Callable[[httpx2.Request], httpx2.Response]


def settings(**over: Any) -> Settings:
    values: dict[str, Any] = {
        "env": "test",
        "public_origin": "http://127.0.0.1:1",
        "database_url": "postgresql+psycopg://dome@/unused",
        "session_secret": "test-session-secret-0123456789",
        "oidc_issuer": "http://idp.invalid",
        "oidc_client_id": "c",
        "oidc_client_secret": "s",
        "entitlement_signing_key_pem_path": Path("/nonexistent.pem"),
        "ai_provider": "anthropic",
        "ai_anthropic_api_key": KEY,
        **over,
    }
    return Settings(**values)


# ----- canned Messages API event streams -----------------------------------------------------------------------------


def message_start(model: str = "claude-opus-5-5", *, i: int = 180, r: int = 3600, w: int = 0) -> dict[str, Any]:
    usage = {"input_tokens": i, "output_tokens": 1, "cache_read_input_tokens": r, "cache_creation_input_tokens": w}
    message: dict[str, Any] = {
        "id": "msg_stub",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": usage,
    }
    return {"type": "message_start", "message": message}


def text_block(index: int, text: str) -> list[dict[str, Any]]:
    return [
        {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": index},
    ]


def thinking_block(index: int) -> list[dict[str, Any]]:
    return [
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "thinking", "thinking": "", "signature": ""},
        },
        {"type": "content_block_delta", "index": index, "delta": {"type": "signature_delta", "signature": "c2ln"}},
        {"type": "content_block_stop", "index": index},
    ]


def fallback_block(index: int, src: str, dst: str) -> list[dict[str, Any]]:
    block = {"type": "fallback", "from": {"model": src}, "to": {"model": dst}, "trigger": {"type": "refusal"}}
    return [
        {"type": "content_block_start", "index": index, "content_block": block},
        {"type": "content_block_stop", "index": index},
    ]


def iteration(kind: str, *, i: int = 20, o: int = 30, w: int = 3600) -> dict[str, Any]:
    """A `usage.iterations` entry without a `model` field (the API documents only `type`)."""
    return {
        "type": kind,
        "input_tokens": i,
        "output_tokens": o,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": w,
    }


def message_delta(stop_reason: str = "end_turn", *, o: int = 60, **usage: Any) -> dict[str, Any]:
    delta: dict[str, Any] = {"stop_reason": stop_reason, "stop_sequence": None}
    if "stop_details" in usage:
        delta["stop_details"] = usage.pop("stop_details")
    return {"type": "message_delta", "delta": delta, "usage": {"output_tokens": o, **usage}}


def sse_bytes(events: list[dict[str, Any]]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def stream(*events: dict[str, Any], request_id: str = "req_stub_1") -> Handler:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream", "request-id": request_id},
            content=sse_bytes(list(events)),
        )

    return handler


def good(text: str = REPLY) -> Handler:
    return stream(message_start(), *thinking_block(0), *text_block(1, text), message_delta(), {"type": "message_stop"})


def status(code: int, error_type: str) -> Handler:
    def handler(_request: httpx2.Request) -> httpx2.Response:
        body = {"type": "error", "error": {"type": error_type, "message": f"stub says {KEY}"}}
        return httpx2.Response(code, json=body, headers={"request-id": f"req_err_{code}"})

    return handler


def raises(exc_type: type[Exception]) -> Handler:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise exc_type("stub transport failure", request=request)  # type: ignore[call-arg]

    return handler


def drops_after_start(exc_type: type[Exception] = httpx2.ReadTimeout) -> Handler:
    def handler(request: httpx2.Request) -> httpx2.Response:
        async def body() -> AsyncIterator[bytes]:
            yield sse_bytes([message_start()])
            raise exc_type("stream dropped", request=request)  # type: ignore[call-arg]

        return httpx2.Response(
            200, headers={"content-type": "text/event-stream", "request-id": "req_drop"}, content=body()
        )

    return handler


class Stub:
    def __init__(self, *handlers: Handler, on_request: Callable[[], None] | None = None) -> None:
        self.handlers = list(handlers)
        self.requests: list[httpx2.Request] = []
        self.on_request = on_request

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.on_request is not None:
            self.on_request()
        return self.handlers.pop(0)(request)

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests]


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def provider(stub: Stub, time: FakeTime | None = None, **over: Any) -> AnthropicProvider:
    t = time or FakeTime()
    client = anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(stub))
    return AnthropicProvider(settings(**over), http_client=client, clock=t.clock, sleep=t.sleep)


async def call(
    stub: Stub, *, time: FakeTime | None = None, user_json: str = USER_JSON, **over: Any
) -> tuple[ProviderResult | ProviderError, ProviderProgress]:
    p = provider(stub, time, **over)
    progress = ProviderProgress()
    try:
        return await p.complete(ProviderRequest(SYSTEM_TEXT, OUTPUT_SCHEMA, user_json), progress), progress
    except ProviderError as exc:
        return exc, progress
    finally:
        await p.aclose()


# ----- the request ------------------------------------------------------------------------------------------------


async def test_request_shape_is_exactly_the_design(monkeypatch: pytest.MonkeyPatch) -> None:
    # a stray ANTHROPIC_BASE_URL can never redirect the key and the words: base_url is passed explicitly
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://evil.invalid")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-wrong-key")
    stub = Stub(good())
    result, _ = await call(stub)
    assert isinstance(result, ProviderResult) and result.outcome == "ok"
    [request] = stub.requests
    assert request.method == "POST" and request.url.path == "/v1/messages"
    assert request.url.host == "api.anthropic.com" and request.url.scheme == "https"
    assert request.headers["x-api-key"] == KEY
    assert FALLBACK_BETA in request.headers["anthropic-beta"].split(",")
    assert "authorization" not in request.headers
    body = json.loads(request.content)
    assert set(body) == {"model", "max_tokens", "stream", "system", "messages", "output_config", "fallbacks"}
    assert body["model"] == "claude-opus-5-5" and body["max_tokens"] == 1024 and body["stream"] is True
    assert body["system"] == [{"type": "text", "text": SYSTEM_TEXT, "cache_control": {"type": "ephemeral"}}]
    assert body["messages"] == [{"role": "user", "content": USER_JSON}]
    assert body["output_config"] == {"effort": "low", "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}}
    assert body["fallbacks"] == "default"
    for absent in ("thinking", "temperature", "top_p", "top_k", "tools", "tool_choice", "metadata"):
        assert absent not in body


async def test_one_hour_ttl_and_effort_settings() -> None:
    stub = Stub(good())
    await call(stub, ai_cache_ttl="1h", ai_effort="medium", ai_max_output_tokens=2048)
    body = stub.bodies()[0]
    assert body["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert body["output_config"]["effort"] == "medium" and body["max_tokens"] == 2048


async def test_two_turns_send_a_byte_identical_prefix() -> None:
    stub = Stub(good(), good())
    p = provider(stub)
    for words in ("good evening", "how do I take a screenshot"):
        user_json = json.dumps({"request": {"source": "speech", "text": words}})
        await p.complete(ProviderRequest(SYSTEM_TEXT, OUTPUT_SCHEMA, user_json), ProviderProgress())
    await p.aclose()
    first, second = (r.content for r in stub.requests)
    # the SDK writes `system` after `messages`; everything from `"system":` on is the cached prefix plus `stream`
    assert first.split(b'"system":', 1)[1] == second.split(b'"system":', 1)[1]
    a, b = stub.bodies()
    assert a["system"] == b["system"] and a["output_config"] == b["output_config"]
    assert a["messages"] != b["messages"]


# ----- reading the response ------------------------------------------------------------------------------------------


async def test_thinking_then_text() -> None:
    stub = Stub(good())
    result, progress = await call(stub)
    assert isinstance(result, ProviderResult)
    assert (result.outcome, result.text, result.served_model, result.fallback_used) == (
        "ok",
        REPLY,
        "claude-opus-5-5",
        False,
    )
    assert result.request_id == "req_stub_1"
    [attempt] = result.attempts
    assert (attempt.model, attempt.input_tokens, attempt.output_tokens, attempt.cache_read_tokens) == (
        "claude-opus-5-5",
        180,
        60,
        3600,
    )
    assert progress.opened_attempts == 1 and progress.last_start is not None and not progress.fallback_seen


async def test_fallback_with_iterations_prices_each_attempt_at_its_model(capfd: pytest.CaptureFixture[str]) -> None:
    iterations = [  # the API documents only `type`; neither entry names its model here
        {
            "type": "message",
            "input_tokens": 20,
            "output_tokens": 5,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 3600,
        },
        {
            "type": "fallback_message",
            "input_tokens": 20,
            "output_tokens": 80,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 3600,
        },
    ]
    stub = Stub(
        stream(
            message_start(),
            *fallback_block(0, "claude-opus-5-5", "claude-opus-5"),
            *text_block(1, REPLY),
            message_delta(o=80, iterations=iterations),
            {"type": "message_stop"},
        )
    )
    result, progress = await call(stub)
    assert isinstance(result, ProviderResult) and result.outcome == "ok"
    assert result.fallback_used and result.served_model == "claude-opus-5" and progress.fallback_seen
    declined, served = result.attempts
    assert declined.model == "claude-opus-5-5" and served.model == "claude-opus-5"
    assert cost_micro_usd(result.attempts, "5m") == (80 + 100 + 18_000) + (100 + 2_000 + 22_500)
    assert "unknown_model_price" not in capfd.readouterr().out
    # an entry that does name its model wins
    named = [dict(iterations[0], model="claude-opus-4-8"), dict(iterations[1], model="claude-opus-4-8")]
    stub = Stub(
        stream(message_start(), *text_block(0, REPLY), message_delta(iterations=named), {"type": "message_stop"})
    )
    result, _ = await call(stub)
    assert isinstance(result, ProviderResult) and [a.model for a in result.attempts] == ["claude-opus-4-8"] * 2


@pytest.mark.parametrize("after", [REPLY[15:], REPLY], ids=["continues_the_partial", "starts_over"])
async def test_a_mid_output_fallback_keeps_the_rescued_reply(after: str) -> None:
    """A streamed decline after some output keeps the declined partial and marks the boundary with a `fallback` block;
    the fallback model continues from the partial (only its text blocks are the continuation context)."""
    stub = Stub(
        stream(
            message_start(),
            *thinking_block(0),
            *text_block(1, REPLY[:15]),
            *fallback_block(2, "claude-opus-5-5", "claude-opus-5"),
            *text_block(3, after),
            message_delta(iterations=[iteration("message"), iteration("fallback_message")]),
            {"type": "message_stop"},
        )
    )
    result, progress = await call(stub)
    assert isinstance(result, ProviderResult)
    assert (result.outcome, result.text, result.fallback_used, result.served_model) == (
        "ok",
        REPLY,
        True,
        "claude-opus-5",
    )
    assert [a.model for a in result.attempts] == ["claude-opus-5-5", "claude-opus-5"] and progress.fallback_seen


async def test_a_mid_output_fallback_that_does_not_parse_is_invalid() -> None:
    stub = Stub(
        stream(
            message_start(),
            *text_block(0, REPLY[:15]),
            *fallback_block(1, "claude-opus-5-5", "claude-opus-5"),
            *text_block(2, '"mode": "answer"}'),
            message_delta(iterations=[iteration("message"), iteration("fallback_message")]),
            {"type": "message_stop"},
        )
    )
    result, _ = await call(stub)
    assert isinstance(result, ProviderResult) and (result.outcome, result.text) == ("invalid", None)
    assert len(result.attempts) == 2  # still metered


async def test_a_refusal_by_the_whole_chain_prices_each_hop_at_its_model() -> None:
    """Both hops declined: two `message` entries without a model. The second ran on the fallback model, which bills at
    its own rates; the `fallback` blocks name the model of each hop."""
    details = {"type": "refusal", "category": "cyber"}
    entries = [iteration("message", o=0), iteration("message", o=0)]
    stub = Stub(
        stream(
            message_start("claude-opus-5"),  # a pre-output decline: message_start already names the next model
            *fallback_block(0, "claude-opus-5-5", "claude-opus-5"),
            message_delta("refusal", o=0, iterations=entries, stop_details=details),
            {"type": "message_stop"},
        )
    )
    result, _ = await call(stub)
    assert isinstance(result, ProviderResult) and (result.outcome, result.fallback_used) == ("refused", False)
    assert [a.model for a in result.attempts] == ["claude-opus-5-5", "claude-opus-5"]
    assert cost_micro_usd(result.attempts, "5m") == (80 + 18_000) + (100 + 22_500)
    # no block: no model declined before this one, so every attempt ran on the message's model (a sticky turn)
    stub = Stub(
        stream(
            message_start("claude-opus-5"),
            message_delta("refusal", o=0, iterations=entries[:1], stop_details=details),
            {"type": "message_stop"},
        )
    )
    result, _ = await call(stub)
    assert isinstance(result, ProviderResult) and [a.model for a in result.attempts] == ["claude-opus-5"]


async def test_refusal_is_reported_without_content() -> None:
    details = {"type": "refusal", "category": "cyber", "explanation": "stub explanation"}
    stub = Stub(stream(message_start(), message_delta("refusal", o=0, stop_details=details), {"type": "message_stop"}))
    result, _ = await call(stub)
    assert isinstance(result, ProviderResult)
    assert (result.outcome, result.text, result.refusal_category) == ("refused", None, "cyber")
    assert len(stub.requests) == 1


@pytest.mark.parametrize(
    ("events", "outcome"),
    [
        ([*text_block(0, REPLY[:20]), message_delta("max_tokens", o=1024)], "truncated"),
        ([*text_block(0, REPLY), *text_block(1, REPLY), message_delta()], "invalid"),
        ([*thinking_block(0), message_delta()], "invalid"),
        ([*text_block(0, REPLY), message_delta("pause_turn")], "invalid"),
        ([*text_block(0, REPLY), message_delta("tool_use")], "invalid"),
    ],
)
async def test_unusable_messages(events: list[dict[str, Any]], outcome: str) -> None:
    stub = Stub(stream(message_start(), *events, {"type": "message_stop"}))
    result, _ = await call(stub)
    assert isinstance(result, ProviderResult) and result.outcome == outcome and result.text is None
    assert result.attempts  # still metered


# ----- errors and the retry rule -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "error_type", "reason"),
    [
        (401, "authentication_error", "provider_auth"),
        (403, "permission_error", "provider_auth"),
        (402, "billing_error", "provider_billing"),
        (400, "invalid_request_error", "bad_request"),
        (404, "not_found_error", "bad_request"),
        (422, "invalid_request_error", "bad_request"),
        (500, "api_error", "provider_error"),
        (503, "api_error", "provider_error"),
    ],
)
async def test_status_errors_make_one_request(code: int, error_type: str, reason: str) -> None:
    stub = Stub(status(code, error_type), good())
    error, progress = await call(stub)
    assert isinstance(error, ProviderError)
    assert (error.reason, error.status, error.error_type, error.request_id) == (
        reason,
        code,
        error_type,
        f"req_err_{code}",
    )
    assert not error.cost_unknown and progress.opened_attempts == 0
    assert len(stub.requests) == 1
    assert KEY not in str(error) and KEY not in repr(error) and "stub says" not in str(error)


async def test_connection_errors_and_timeouts() -> None:
    stub = Stub(raises(httpx2.ConnectError), raises(httpx2.ConnectError))
    error, _ = await call(stub)
    assert isinstance(error, ProviderError) and error.reason == "provider_error" and not error.cost_unknown
    assert len(stub.requests) == 2  # never opened: unbilled, retried once
    stub = Stub(raises(httpx2.ReadTimeout), good())
    error, _ = await call(stub)
    assert isinstance(error, ProviderError) and error.reason == "timeout"
    assert error.cost_unknown  # the request was sent: Anthropic may have billed it
    assert len(stub.requests) == 1


async def test_mid_stream_failures_are_cost_unknown_and_never_retried() -> None:
    stub = Stub(drops_after_start(), good())
    error, progress = await call(stub)
    assert isinstance(error, ProviderError) and error.reason == "timeout" and error.cost_unknown
    assert progress.opened_attempts == 1 and progress.last_start is not None
    assert progress.last_start.model == "claude-opus-5-5" and progress.last_start.cache_read_tokens == 3600
    assert len(stub.requests) == 1
    stub = Stub(drops_after_start(httpx2.RemoteProtocolError), good())
    error, _ = await call(stub)
    assert isinstance(error, ProviderError) and error.reason == "provider_error" and error.cost_unknown
    assert len(stub.requests) == 1
    # an `error` event after message_start is mapped by its type
    overloaded = {"type": "error", "error": {"type": "overloaded_error", "message": f"stub {KEY}"}}
    stub = Stub(stream(message_start(), overloaded), good())
    error, _ = await call(stub)
    assert isinstance(error, ProviderError) and error.reason == "overloaded" and error.cost_unknown
    assert (error.error_type, error.status) == ("overloaded_error", 529) and len(stub.requests) == 1
    # a body that simply ends before message_stop
    stub = Stub(stream(message_start(), *text_block(0, REPLY)), good())
    error, _ = await call(stub)
    assert isinstance(error, ProviderError) and error.reason == "provider_error" and error.cost_unknown
    assert len(stub.requests) == 1


async def test_retry_policy() -> None:
    t = FakeTime()
    stub = Stub(status(529, "overloaded_error"), good())
    result, _ = await call(stub, time=t)
    assert isinstance(result, ProviderResult) and result.outcome == "ok"
    assert len(stub.requests) == 2 and t.slept == [1.0]
    stub = Stub(status(429, "rate_limit_error"), status(429, "rate_limit_error"), good())
    error, _ = await call(stub)
    assert isinstance(error, ProviderError) and error.reason == "overloaded" and len(stub.requests) == 2
    stub = Stub(raises(httpx2.ConnectError), good())
    result, _ = await call(stub)
    assert isinstance(result, ProviderResult) and len(stub.requests) == 2
    stub = Stub(raises(httpx2.ConnectTimeout), good())
    result, _ = await call(stub)
    assert isinstance(result, ProviderResult) and len(stub.requests) == 2
    for once in (raises(httpx2.ReadTimeout), status(500, "api_error"), drops_after_start()):
        stub = Stub(once, good())
        assert isinstance((await call(stub))[0], ProviderError) and len(stub.requests) == 1
    # DOME_AI_MAX_RETRIES=0: every case is one request
    for first in (status(529, "overloaded_error"), status(429, "rate_limit_error"), raises(httpx2.ConnectError)):
        stub = Stub(first, good())
        assert isinstance((await call(stub, ai_max_retries=0))[0], ProviderError) and len(stub.requests) == 1


@pytest.mark.parametrize(("error_type", "code"), [("overloaded_error", 529), ("rate_limit_error", 429)])
async def test_an_overload_event_before_message_start_is_unbilled_and_retried(error_type: str, code: int) -> None:
    """The streaming form of a 529 or 429: an `error` event as the first event of a 200 stream. No model ran, so it is
    retried like the HTTP answer and never makes the turn's cost unknown."""
    event = {"type": "error", "error": {"type": error_type, "message": f"stub {KEY}"}}
    t = FakeTime()
    stub = Stub(stream(event), good())
    result, progress = await call(stub, time=t)
    assert isinstance(result, ProviderResult) and result.outcome == "ok"
    assert len(stub.requests) == 2 and t.slept == [1.0]
    assert progress.opened_attempts == 1  # only the attempt that may cost anything: a cancel keeps the bound
    stub = Stub(stream(event), drops_after_start())
    error, progress = await call(stub)
    assert isinstance(error, ProviderError) and error.cost_unknown and len(stub.requests) == 2
    assert progress.opened_attempts == 1 and progress.last_start is not None
    stub = Stub(stream(event), good())
    error, progress = await call(stub, ai_max_retries=0)
    assert isinstance(error, ProviderError) and len(stub.requests) == 1
    assert (error.reason, error.status, error.error_type, error.cost_unknown) == ("overloaded", code, error_type, False)
    assert progress.opened_attempts == 0 and KEY not in str(error)


async def test_other_error_events_before_message_start_stay_cost_unknown() -> None:
    # it may follow a hop that ran and declined before any output: never retried, cost unknown
    api_error = {"type": "error", "error": {"type": "api_error", "message": "stub"}}
    stub = Stub(stream(api_error), good())
    error, progress = await call(stub)
    assert isinstance(error, ProviderError) and (error.reason, error.status) == ("provider_error", 500)
    assert error.cost_unknown and progress.opened_attempts == 1 and len(stub.requests) == 1


async def test_no_retry_when_less_than_ten_seconds_of_the_deadline_remain() -> None:
    t = FakeTime()

    def slow_first_attempt() -> None:
        t.now += 9.5  # deadline 20 s: 10.5 s remain, not enough for the 1 s wait plus 10 s

    stub = Stub(status(529, "overloaded_error"), good(), on_request=slow_first_attempt)
    error, _ = await call(stub, time=t)
    assert isinstance(error, ProviderError) and error.reason == "overloaded"
    assert len(stub.requests) == 1 and t.slept == []
    t = FakeTime()

    def quick_first_attempt() -> None:
        t.now += 2.0

    stub = Stub(status(529, "overloaded_error"), good(), on_request=quick_first_attempt)
    result, _ = await call(stub, time=t)
    assert isinstance(result, ProviderResult) and len(stub.requests) == 2


async def test_key_never_in_logs_or_errors(capfd: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture) -> None:
    pin_sdk_loggers()
    for name in SDK_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING
    caplog.set_level(logging.DEBUG)
    errors: list[ProviderError] = []
    for handler in (status(401, "authentication_error"), raises(httpx2.ReadTimeout), drops_after_start(), good()):
        result, _ = await call(Stub(handler))
        if isinstance(result, ProviderError):
            errors.append(result)
    captured = capfd.readouterr()
    for text in (captured.out, captured.err, caplog.text, *(str(e) for e in errors), *(repr(e) for e in errors)):
        assert KEY not in text and "STUBKEY" not in text
    assert settings().ai_anthropic_api_key.get_secret_value() == KEY
    assert KEY not in repr(settings()) and KEY not in str(settings().model_dump())
