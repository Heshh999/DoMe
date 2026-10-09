"""Support tickets (spec section 11A, smallest practical issue submission and status flow).

Account-scoped: a customer creates a ticket, receives a reference after successful receipt and can read
their own tickets back. The diagnostics text the customer reviewed is passed through the log redactor
again before storage. No route here (or anywhere) lets support staff execute a command on a PC, and
operators have no route yet at all (``KNOWN_ISSUES.md``).
"""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from dome_api.auth.deps import DB, Auth, Svc
from dome_api.db.models import SupportTicket
from dome_api.errors import ApiError
from dome_api.logging import redact_diagnostics, redact_text
from dome_api.security import events
from dome_api.settings import Settings
from dome_api.util import parse_uuid, rest_response, strict_body, ts_required, utcnow

router = APIRouter(tags=["support"])

# Crockford base32 (rest.schema.json support_ticket.reference: ^DM-[0-9A-HJKMNP-TV-Z]{8}$): no I, L, O, U.
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
# A ticket body may carry a 32 KiB diagnostics string plus a 2000-char message; the generic REST limit is 16 KiB.
SUPPORT_BODY_MAX_BYTES = 48 * 1024


def new_reference() -> str:
    return "DM-" + "".join(secrets.choice(_CROCKFORD) for _ in range(8))


def ticket_body(t: SupportTicket, settings: Settings) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ticket_id": str(t.id),
        "reference": t.reference,
        "status": t.status,
        "category": t.category,
        "created_at": ts_required(t.created_at),
        "updated_at": ts_required(t.updated_at),
    }
    if t.error_code:
        out["error_code"] = t.error_code
    if t.answer:
        out["answer"] = t.answer[:4000]
    # Present only when the founder configured one; never a default promise (spec section 11A).
    if settings.support_response_expectation:
        out["response_expectation"] = settings.support_response_expectation[:200]
    return out


@router.post("/support/tickets", status_code=201)
async def create_ticket(request: Request, auth: Auth, db: DB, svc: Svc) -> Any:
    account_id = auth.account.id  # read once: nothing below may touch a possibly expired ORM attribute
    budget_key = str(account_id)
    # Reserve the budget before the first await (check and record are one synchronous step on the event loop),
    # so concurrent submissions cannot all pass the check; the reservation is given back if nothing is stored.
    if not svc.support_ticket_limiter.allow(budget_key):
        raise ApiError(429, "RATE_LIMITED", "Too many support requests this hour. Try again later.")
    try:
        body = await strict_body(request, "support_ticket_request", max_bytes=SUPPORT_BODY_MAX_BYTES)
        now = utcnow()
        diagnostics = body.get("diagnostics")
        # the customer's own words still get the token/pairing-code pass: a pasted secret must not land in the table
        message = redact_text(str(body["message"]))[:2000]
        diagnostics_redacted = redact_diagnostics(str(diagnostics))[:32768] if isinstance(diagnostics, str) else None
        ticket: SupportTicket | None = None
        for _attempt in range(5):  # the reference is random; a collision is retried, never reported as success
            candidate = SupportTicket(
                account_id=account_id,
                reference=new_reference(),
                category=str(body["category"]),
                error_code=body.get("error_code"),
                message=message,
                diagnostics_redacted=diagnostics_redacted,
                app_version=body.get("app_version"),
                status="received",
                created_at=now,
                updated_at=now,
            )
            try:
                async with db.begin_nested():  # SAVEPOINT: a collision rolls back this insert only
                    db.add(candidate)
                    await db.flush()
            except IntegrityError:
                continue
            ticket = candidate
            break
        if ticket is None:
            raise ApiError(503, "SERVICE_UNAVAILABLE", "Could not record the support request. Nothing was received.")
        events.record(
            db,
            account_id=account_id,
            kind="support_ticket_created",
            severity="info",
            actor="account",
            subject_id=ticket.id,
            detail={
                "category": ticket.category,
                "reference": ticket.reference,
                "has_diagnostics": diagnostics is not None,
            },
        )
        await db.commit()
    except BaseException:
        svc.support_ticket_limiter.release(budget_key)
        raise
    return rest_response(
        svc.settings.validate_rest_responses, "support_ticket_response", ticket_body(ticket, svc.settings), status=201
    )


@router.get("/support/tickets")
async def list_tickets(auth: Auth, db: DB, svc: Svc) -> Any:
    rows = (
        await db.execute(
            select(SupportTicket)
            .where(SupportTicket.account_id == auth.account.id)
            .order_by(SupportTicket.created_at.desc(), SupportTicket.id.desc())
            .limit(50)
        )
    ).scalars()
    return rest_response(
        svc.settings.validate_rest_responses,
        "support_tickets_response",
        {"tickets": [ticket_body(t, svc.settings) for t in rows]},
    )


@router.get("/support/tickets/{ticket_id}")
async def get_ticket(ticket_id: str, auth: Auth, db: DB, svc: Svc) -> Any:
    tid = parse_uuid(ticket_id)
    row = await db.scalar(
        select(SupportTicket).where(SupportTicket.id == tid, SupportTicket.account_id == auth.account.id)
    )
    if row is None:
        raise ApiError(404, "NOT_FOUND")
    return rest_response(
        svc.settings.validate_rest_responses, "support_ticket_response", ticket_body(row, svc.settings)
    )
