"""Support tickets (spec section 11A): create → reference, list newest first, get own only, redaction of the
diagnostics bundle and the message, per-account hourly budget, CSRF, response_expectation only when configured."""

from __future__ import annotations

import json

from dome_api.logging import redact_diagnostics, redact_text
from dome_api.routes.support import new_reference, ticket_body
from dome_api.settings import Settings
from tests.conftest import Browser, Env, sql

TOKEN = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcdefg"  # 43 base64url chars: a PC access token / kid shape
JWT = "eyJhbGciOiJFUzI1NiJ9.eyJzdWIiOiJhbGljZSJ9.c2lnbmF0dXJlc2lnbmF0dXJl"


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
    stored = json.loads(diagnostics)
    assert stored["access_token"] == "[redacted]" and stored["media"]["title"] == "[redacted]"
    assert stored["nested"][0]["code_hash"] == "[redacted]"
    assert stored["connection"] == {"phone": "online", "relay": "connected", "pc": "reconnecting"}  # useful data kept
    assert stored["last_error"]["code"] == "[redacted]"  # `code` is a redacted key (pairing codes) — see DECISIONS
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
