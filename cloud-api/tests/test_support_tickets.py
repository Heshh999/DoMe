"""Support tickets (spec section 11A): create → reference, list newest first, get own only, redaction of the
diagnostics bundle and the message, per-account hourly budget, CSRF, response_expectation only when configured."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator

import pytest

from dome_api.logging import redact, redact_diagnostics, redact_text
from dome_api.routes import support as support_routes
from dome_api.routes.support import new_reference, ticket_body
from dome_api.settings import Settings
from tests.conftest import AccountFactory, Browser, Env, sql

TOKEN = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdefg"  # 43 base64url chars: a PC access token / kid shape
JWT = "eyJhbGciOiJFUzI1NiJ9.eyJzdWIiOiJhbGljZSJ9.c2lnbmF0dXJlc2lnbmF0dXJl"
TYPED = "my bank pin 4417"
WATCH_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42"
PAIRING = "K7Q2-M9XD-4HPR-8WTV-ZC3N"
PAIRING_DISPLAY = "K7Q2M-9XD4H-PR8WT-VZC3N"  # the PC's 4x5 display form of a code


def _bundle() -> str:
    return json.dumps(
        {
            "app_version": "0.3.0",
            "protocol_version": "1.1",
            "connection": {"phone": "online", "relay": "connected", "pc": "reconnecting"},
            "last_error": {"code": "PC_RECONNECTING", "at": "2026-10-09T10:00:00Z"},
            "access_token": TOKEN,  # a buggy client might include one; the key rule catches it
            "note": f"copied from console: Bearer {TOKEN} and {JWT}",
            "media": {"title": "Never Gonna Give You Up"},
            "nested": [{"code_hash": "x" * 43}, {"kid_prefix": "abcd1234"}],
            # what a buggy client might put in a bundle (spec section 11A forbids all of it)
            "input": {"text": TYPED, "events": [{"type": "text", "text": "hunter2"}]},
            "url": WATCH_URL,
            "pairing": PAIRING,
            "log": [f"opened {WATCH_URL}", f"pairing with {PAIRING_DISPLAY}", "relay connected"],
        }
    )


async def test_create_list_get_scoped_per_account_with_redaction(env: Env, alice: Browser, bob: Browser) -> None:
    created = await alice.post(
        "/v1/support/tickets",
        {
            "category": "connection",
            "message": f"My PC shows reconnecting. Console showed token {TOKEN}.",
            "error_code": "PC_RECONNECTING",
            "diagnostics": _bundle(),
            "app_version": "0.3.0",
        },
        schema="support_ticket_response",
        expect=201,
    )
    assert created["status"] == "received" and created["category"] == "connection"
    assert created["reference"].startswith("DM-") and len(created["reference"]) == 11
    assert created["error_code"] == "PC_RECONNECTING"
    assert "response_expectation" not in created  # not configured in tests: never a default promise
    assert "answer" not in created
    # what was stored: structural + token-pattern redaction applied, nothing executable, scoped to alice
    rows = sql(
        env.database_url,
        "SELECT account_id::text, message, diagnostics_redacted, status FROM support_tickets WHERE id = %s",
        (created["ticket_id"],),
    )
    account_id, message, diagnostics, status = rows[0]
    assert account_id == alice.account_id and status == "received"
    assert TOKEN not in message and "[redacted]" in message and "reconnecting" in message
    assert TOKEN not in diagnostics and JWT not in diagnostics
    assert "Never Gonna" not in diagnostics  # media titles are redacted by key
    for forbidden in (TYPED, "hunter2", "dQw4w9WgXcQ", "watch?v=", PAIRING, PAIRING_DISPLAY, "K7Q2"):
        assert forbidden not in diagnostics, forbidden  # typed text, full media URL, pairing code
    stored = json.loads(diagnostics)
    assert stored["access_token"] == "[redacted]" and stored["media"]["title"] == "[redacted]"
    assert stored["nested"][0]["code_hash"] == "[redacted]"
    assert stored["connection"] == {"phone": "online", "relay": "connected", "pc": "reconnecting"}  # useful data kept
    assert stored["last_error"]["code"] == "[redacted]"  # `code` is a redacted key (pairing codes) — see DECISIONS
    assert stored["input"] == {"text": "[redacted]", "events": "[redacted]"}
    assert stored["url"] == "[redacted]" and stored["pairing"] == "[redacted]"
    assert stored["log"] == ["opened https://www.youtube.com", "pairing with [redacted]", "relay connected"]
    # list: own, newest first
    second = await alice.post(
        "/v1/support/tickets",
        {"category": "input", "message": "Touchpad lags."},
        schema="support_ticket_response",
        expect=201,
    )
    listed = await alice.get("/v1/support/tickets", schema="support_tickets_response")
    assert [t["ticket_id"] for t in listed["tickets"]][:2] == [second["ticket_id"], created["ticket_id"]]
    # get: own only
    got = await alice.get(f"/v1/support/tickets/{created['ticket_id']}", schema="support_ticket_response")
    assert got == created
    await bob.request("GET", f"/v1/support/tickets/{created['ticket_id']}", expect=404)
    assert (await bob.get("/v1/support/tickets", schema="support_tickets_response"))["tickets"] == []
    kinds = [
        e for e in (await alice.get("/v1/account/security-events"))["events"] if e["kind"] == "support_ticket_created"
    ]
    assert kinds and kinds[0]["detail"]["reference"] == second["reference"]


async def test_create_requires_session_csrf_and_valid_body(env: Env, alice: Browser) -> None:
    r = await alice.request(
        "POST", "/v1/support/tickets", {"category": "other", "message": "x"}, csrf=False, expect=403
    )
    assert r.body_checked["error"]["code"] == "FORBIDDEN"  # type: ignore[attr-defined]
    r = await alice.request("POST", "/v1/support/tickets", {"category": "nope", "message": "x"}, expect=400)
    assert r.body_checked["error"]["code"] == "MALFORMED_MESSAGE"  # type: ignore[attr-defined]
    r = await alice.request(
        "POST", "/v1/support/tickets", {"category": "other", "message": "x", "run": "cmd"}, expect=400
    )
    assert r.body_checked["error"]["code"] == "MALFORMED_MESSAGE"  # type: ignore[attr-defined]
    r = await alice.request("POST", "/v1/support/tickets", {"category": "other", "message": "x" * 2001}, expect=400)
    assert r.status_code == 400
    # a 32 KiB diagnostics string is within the contract and accepted
    big = json.dumps({"log": ["line"] * 2000})
    assert len(big) <= 32768
    await alice.post("/v1/support/tickets", {"category": "other", "message": "big", "diagnostics": big}, expect=201)
    import httpx

    async with httpx.AsyncClient(base_url=env.origin, timeout=10) as anon:
        r2 = await anon.post(
            "/v1/support/tickets", json={"category": "other", "message": "x"}, headers={"Origin": env.origin}
        )
    assert r2.status_code == 401
    r3 = await alice.request("GET", "/v1/support/tickets/not-a-uuid", expect=404)
    assert r3.status_code == 404


async def test_ticket_rate_limit_per_account(env: Env, alice: Browser, bob: Browser) -> None:
    for _ in range(env.settings.rate_support_tickets_per_hour):
        await alice.post("/v1/support/tickets", {"category": "other", "message": "again"}, expect=201)
    r = await alice.request("POST", "/v1/support/tickets", {"category": "other", "message": "once more"}, expect=429)
    assert r.body_checked["error"]["code"] == "RATE_LIMITED"  # type: ignore[attr-defined]
    # another account is unaffected
    await bob.post("/v1/support/tickets", {"category": "other", "message": "bob"}, expect=201)
    assert len((await alice.get("/v1/support/tickets"))["tickets"]) == env.settings.rate_support_tickets_per_hour


def test_redaction_helpers_and_reference_shape(settings: Settings) -> None:
    assert redact_text(f"token {TOKEN} ok") == "token [redacted] ok"
    assert redact_text(f"auth: Bearer {TOKEN[:20]}") == "auth: [redacted]"
    assert redact_text(JWT) == "[redacted]"
    assert redact_text("The PC showed PC_RECONNECTING at 10:00 after a Wi-Fi change") == (
        "The PC showed PC_RECONNECTING at 10:00 after a Wi-Fi change"
    )
    assert redact_diagnostics("plain text with " + TOKEN) == "plain text with [redacted]"
    assert redact_diagnostics('{"token":"a","v":"1.1"}') == '{"token":"[redacted]","v":"1.1"}'
    assert redact_diagnostics("[1, 2]") == "[1,2]"
    # the reviewer's probe: typed text, input events, a full watch URL and a pairing code never survive
    probe = json.dumps(
        {
            "input": {"text": TYPED, "events": [{"type": "text", "text": "hunter2"}]},
            "url": WATCH_URL,
            "pairing": PAIRING,
            "email": "a@b.test",
        }
    )
    assert json.loads(redact_diagnostics(probe)) == {
        "input": {"text": "[redacted]", "events": "[redacted]"},
        "url": "[redacted]",
        "pairing": "[redacted]",
        "email": "[redacted]",
    }
    for key in ("composer", "typed", "href", "query", "search", "pairing_code", "clipboard", "media_url"):
        assert json.loads(redact_diagnostics(json.dumps({key: "x", "ok": "y"}))) == {key: "[redacted]", "ok": "y"}
    # strings: URLs reduced to scheme + host (userinfo dropped), bare host paths to the host, pairing codes masked
    assert redact_diagnostics(f"saw {WATCH_URL} then https://u:p@music.youtube.com/watch?v=x") == (
        "saw https://www.youtube.com then https://music.youtube.com"
    )
    assert redact_diagnostics("tab youtu.be/dQw4w9WgXcQ open") == "tab youtu.be open"
    assert redact_diagnostics("relay wss://relay.dome.example/ws/agent?x=1 ok") == "relay wss://relay.dome.example ok"
    for code in (PAIRING, PAIRING_DISPLAY, PAIRING.replace("-", ""), PAIRING.replace("-", " "), PAIRING.lower()):
        assert redact_text(f"code {code} here") == "code [redacted] here", code
    # a JSON document embedded as a string gets the key rules too
    assert json.loads(redact_diagnostics(json.dumps({"blob": json.dumps({"text": TYPED, "v": 1})}))) == {
        "blob": '{"text":"[redacted]","v":1}'
    }
    # ordinary prose, versions, dates and error codes are left alone
    for prose in (
        "have some time with this now",
        "version 0.3.0 on 2026-10-09 saw INPUT_NOT_PERMITTED then PC_RECONNECTING",
        "dome_api/relay/agent_ws.py line 12",
    ):
        assert redact_diagnostics(prose) == prose
    # the log redactor masks typed text under its natural key (rules.input_sessions (5))
    assert redact({"event": "x", "text": TYPED}) == {"event": "x", "text": "[redacted]"}
    for _ in range(50):
        ref = new_reference()
        assert len(ref) == 11 and ref.startswith("DM-") and not set(ref[3:]) & set("ILOU")
    # response_expectation appears only when configured
    from datetime import UTC, datetime
    from uuid import uuid4

    from dome_api.db.models import SupportTicket

    now = datetime.now(UTC)
    t = SupportTicket(
        id=uuid4(),
        account_id=uuid4(),
        reference=new_reference(),
        category="other",
        message="m",
        status="received",
        created_at=now,
        updated_at=now,
    )
    assert "response_expectation" not in ticket_body(t, settings)
    configured = settings.model_copy(
        update={"support_response_expectation": "We usually answer within two working days."}
    )
    body = ticket_body(t, configured)
    assert body["response_expectation"] == "We usually answer within two working days."
    from tests.conftest import SCHEMAS

    SCHEMAS.validate_rest("support_ticket", body)


async def test_ticket_budget_holds_under_concurrent_submissions(env: Env, make_account: AccountFactory) -> None:
    """The hourly budget is reserved before the first await: 40 concurrent submissions with 32 KiB diagnostics
    from one account store exactly ``rate_support_tickets_per_hour`` tickets, the rest are 429."""
    carol = await make_account()
    limit = env.settings.rate_support_tickets_per_hour
    big = json.dumps({"log": ["connection state line"] * 1300})
    assert len(big) <= 32768
    body = {"category": "connection", "message": "concurrent", "diagnostics": big}
    responses = await asyncio.gather(
        *(carol.request("POST", "/v1/support/tickets", body, expect=None) for _ in range(40))
    )
    codes = sorted(r.status_code for r in responses)
    assert codes.count(201) == limit and codes.count(429) == 40 - limit, codes
    stored = sql(env.database_url, "SELECT count(*) FROM support_tickets WHERE account_id = %s", (carol.account_id,))
    assert stored[0][0] == limit


@pytest.fixture
def scripted_references(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """References handed out by ``new_reference`` in order; falls back to random ones when exhausted."""
    queue: list[str] = []
    real = support_routes.new_reference
    monkeypatch.setattr(support_routes, "new_reference", lambda: queue.pop(0) if queue else real())
    yield queue


async def test_reference_collision_retries_in_a_savepoint(
    env: Env, make_account: AccountFactory, scripted_references: list[str]
) -> None:
    dave = await make_account()
    first = await dave.post("/v1/support/tickets", {"category": "other", "message": "one"}, expect=201)
    # the next two attempts collide with the existing reference, the third is fresh: one ticket, no error
    scripted_references.extend([first["reference"], first["reference"]])
    second = await dave.post(
        "/v1/support/tickets", {"category": "other", "message": "two"}, schema="support_ticket_response", expect=201
    )
    assert second["reference"] != first["reference"] and not scripted_references
    # five collisions: 503 and nothing stored; the reservation is given back
    scripted_references.extend([first["reference"]] * 5)
    r = await dave.request("POST", "/v1/support/tickets", {"category": "other", "message": "three"}, expect=503)
    assert r.body_checked["error"]["code"] == "SERVICE_UNAVAILABLE"  # type: ignore[attr-defined]
    rows = sql(env.database_url, "SELECT message FROM support_tickets WHERE account_id = %s", (dave.account_id,))
    assert sorted(m for (m,) in rows) == ["one", "two"]
    for _ in range(env.settings.rate_support_tickets_per_hour - 2):  # failures did not consume the budget
        await dave.post("/v1/support/tickets", {"category": "other", "message": "again"}, expect=201)
    await dave.request("POST", "/v1/support/tickets", {"category": "other", "message": "over"}, expect=429)
