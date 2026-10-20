"""The subscription → entitlement state machine (docs/BILLING.md §4). ``apply`` is the **only** writer of
``accounts.plan``; it is reached from verified webhook processing, the reconciler's provider fetch, the grace-expiry
sweep and account-deletion cancellation, never from a request body or a Checkout return URL.

``derive_state`` and ``plan_for_state`` are pure so the mapping is unit-testable on its own; ``apply`` runs in one
transaction that locks in the order accounts → subscriptions → pcs/controllers → security_events, writes the row and
the plan, disables or re-enables PCs by the plan limits, and returns the PCs whose snapshot the caller pushes after
the commit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dome_api.billing.provider import SubscriptionSnapshot
from dome_api.db.models import PC, Account, Subscription
from dome_api.logging import get_logger
from dome_api.plans import Plan, catalog, plan_for
from dome_api.security import events

log = get_logger("dome_api.billing")

EntitlementState = Literal["free", "active", "pending", "past_due", "canceling", "expired"]
EXPIRED_VISIBLE_DAYS = 30  # an ended subscription shows as `expired` this long, then the account is simply Free


@dataclass(frozen=True, slots=True)
class Derived:
    state: str
    plan_id: str
    interval: str | None
    grace_until: datetime | None
    unknown_price: bool = False  # a price DOME_STRIPE_PRICE_* does not name: the row grants no paid plan


def derive_state(
    snapshot: SubscriptionSnapshot,
    prev: Subscription | None,
    now: datetime,
    price_map: dict[str, tuple[str, str]],
) -> Derived:
    """Map what the provider says now onto ``subscriptions.state`` (BILLING §4.1/§4.3). The grace deadline is set
    once when the row enters ``past_due`` and kept while it stays there."""
    status = snapshot.status
    if status == "active":
        state = "cancel_scheduled" if snapshot.cancel_at_period_end else "active"
    elif status == "trialing":
        state = "trialing"
    elif status == "past_due":
        state = "past_due"
    elif status in ("unpaid", "paused"):
        state = "unpaid"
    elif status == "incomplete":
        state = "incomplete"
    else:  # canceled, incomplete_expired, anything unknown: no entitlement
        state = "canceled"
    plan_id, interval = price_map.get(snapshot.price_id or "", (None, None))
    unknown_price = False
    if plan_id is None:
        if prev is not None and prev.price_id == snapshot.price_id and prev.plan in catalog().paid_plan_ids:
            plan_id, interval = prev.plan, prev.interval
        else:
            # a price DOME_STRIPE_PRICE_* does not name (a subscription made in the Stripe dashboard, a portal switch
            # to another product, another product on the same account): no DoMe plan is granted for it. The row keeps
            # the subscription's state with the default plan, so the account stays on it, and ``apply`` logs
            # billing.unknown_price for the operator to fix the configuration
            plan_id, interval, unknown_price = catalog().default_plan.id, None, True
    grace_until: datetime | None = None
    if state == "past_due":
        if prev is not None and prev.state in ("past_due", "unpaid") and prev.grace_until is not None:
            # set once per payment problem; a grace the sweep already ended stays ended while the provider still
            # retries the card (it says past_due, DoMe says unpaid until a payment succeeds)
            grace_until = prev.grace_until
            if now >= grace_until:
                state = "unpaid"
        else:
            # seven days from the first failed payment we learn of: at a failed renewal the provider has already moved
            # current_period_end to the end of the new (unpaid) period, so it is no anchor for a grace period
            grace_until = now + timedelta(days=catalog().past_due_grace_days)
    return Derived(
        state=state, plan_id=plan_id, interval=interval, grace_until=grace_until, unknown_price=unknown_price
    )


def plan_for_state(state: str, plan_id: str, grace_until: datetime | None, now: datetime) -> str:
    """The plan an account holds in a subscription state: paid while active/trialing/cancel_scheduled and during the
    past_due grace; the default plan otherwise (and always for a row of an unknown price, whose plan is the default)."""
    if state in ("active", "trialing", "cancel_scheduled"):
        return plan_id
    if state == "past_due" and grace_until is not None and now < grace_until:
        return plan_id
    return catalog().default_plan.id


def entitlement_state(row: Subscription | None, now: datetime) -> EntitlementState:
    """``session_response.entitlement_state`` / ``billing_status_response.entitlement_state`` (BILLING §4.2)."""
    if row is None:
        return "free"
    if row.state != "checkout_pending" and row.plan not in catalog().paid_plan_ids:
        return "free"  # a subscription to a price DoMe does not sell (billing.unknown_price): no DoMe plan
    if row.state == "checkout_pending":
        # pending only while a hosted page exists and can still be paid: a page the provider never created (a failed
        # call) or one past its expiry is nothing to wait for
        if not row.provider_checkout_session_id or (row.checkout_expires_at and now >= row.checkout_expires_at):
            return "free"
        return "pending"
    if row.state == "incomplete":
        return "pending"
    if row.state in ("active", "trialing"):
        return "active"
    if row.state == "cancel_scheduled":
        return "canceling"
    if row.state == "past_due":
        return "past_due" if row.grace_until is None or now < row.grace_until else "expired"
    if row.state in ("unpaid", "canceled"):
        return "expired" if now - row.updated_at < timedelta(days=EXPIRED_VISIBLE_DAYS) else "free"
    return "free"


async def live_subscription(
    db: AsyncSession, account_id: uuid.UUID, *, for_update: bool = False
) -> Subscription | None:
    """The account's one non-canceled row, or the newest canceled one (for the `expired` label)."""
    stmt = (
        select(Subscription)
        .where(Subscription.account_id == account_id)
        .order_by((Subscription.state == "canceled").asc(), Subscription.updated_at.desc())
        .limit(1)
    )
    if for_update:
        stmt = stmt.with_for_update()
    row: Subscription | None = await db.scalar(stmt)
    return row


def _pc_order() -> Any:
    return (PC.last_seen_at.desc().nulls_last(), PC.created_at.asc())


async def _fit_devices(
    db: AsyncSession, account: Account, old_plan: Plan, new_plan: Plan, now: datetime
) -> list[uuid.UUID]:
    """Disable the PCs beyond the new limit (most recently seen stay), or re-enable plan-disabled PCs up to it.
    Controllers are selected dynamically by the relay (``plan_enabled_controller_ids``). Returns the PCs whose
    snapshot must be pushed."""
    changed: list[uuid.UUID] = []
    pcs = list(
        (
            await db.execute(
                select(PC)
                .where(PC.account_id == account.id, PC.deleted_at.is_(None))
                .order_by(*_pc_order())
                .with_for_update()
            )
        ).scalars()
    )
    if new_plan.limits_shrink_from(old_plan):
        account.plan_changed_at = now
        enabled = [pc for pc in pcs if pc.enabled]
        for pc in enabled[new_plan.max_enabled_pcs :]:
            pc.enabled = False
            pc.plan_disabled_at = now
            changed.append(pc.id)
    else:
        account.plan_changed_at = None
        room = new_plan.max_enabled_pcs - sum(1 for pc in pcs if pc.enabled)
        for pc in pcs:
            if room <= 0:
                break
            if not pc.enabled and pc.plan_disabled_at is not None:
                pc.enabled = True
                pc.plan_disabled_at = None
                changed.append(pc.id)
                room -= 1
    # every PC of the account carries the plan in its snapshot (assertion, controller statuses)
    return list({*changed, *(pc.id for pc in pcs)})


async def apply(
    db: AsyncSession,
    account_id: uuid.UUID,
    snapshot: SubscriptionSnapshot | None,
    *,
    reason: str,
    now: datetime,
    price_map: dict[str, tuple[str, str]],
    forced_state: str | None = None,
) -> list[uuid.UUID]:
    """Write the subscription row and ``accounts.plan`` from ``snapshot`` (or from ``forced_state`` for the grace
    sweep and deletion, which have no provider object). Returns the PC ids whose snapshot the caller pushes after
    commit; empty when the plan did not change."""
    account = await db.scalar(select(Account).where(Account.id == account_id).with_for_update())
    if account is None:
        return []
    row = await live_subscription(db, account_id, for_update=True)
    if snapshot is not None:
        if (
            row is not None
            and row.provider_subscription_id == snapshot.id
            and row.state == "canceled"
            and snapshot.status != "canceled"
        ):
            # canceled is final at the provider (a canceled subscription is never reactivated): a snapshot that says
            # otherwise was fetched before the cancellation and arrives late
            return []
        if row is None or row.provider_subscription_id != snapshot.id:
            own = await db.scalar(
                select(Subscription)
                .where(Subscription.account_id == account_id, Subscription.provider_subscription_id == snapshot.id)
                .with_for_update()
            )
            if own is not None:
                # an ended subscription of this account while a newer row is live: canceled is final at the
                # provider, so a late or redelivered event about it never touches the plan
                return []
            if row is not None and row.state != "canceled" and row.provider_subscription_id is not None:
                # a second live subscription for one account (paid outside DoMe's own pages): the row DoMe already
                # follows stays authoritative; the operator refunds or cancels the other in the Stripe dashboard
                log.warning("billing.second_subscription", account_id=str(account_id), provider_status=snapshot.status)
                return []
            if row is not None and row.state == "canceled":
                row = None  # a new subscription after an ended one gets its own row
        derived = derive_state(snapshot, row, now, price_map)
        if derived.unknown_price:
            log.warning("billing.unknown_price", account_id=str(account_id), provider_status=snapshot.status)
        if row is None:
            row = Subscription(account_id=account_id, provider="stripe", plan=derived.plan_id, state=derived.state)
            db.add(row)
        row.provider_customer_id = snapshot.customer_id or row.provider_customer_id
        row.provider_subscription_id = snapshot.id
        row.price_id = snapshot.price_id
        row.plan = derived.plan_id
        row.interval = derived.interval
        row.state = derived.state
        row.provider_status = snapshot.status[:32]
        row.provider_synced_at = now
        row.current_period_start = snapshot.current_period_start
        row.current_period_end = snapshot.current_period_end
        row.cancel_at_period_end = snapshot.cancel_at_period_end
        row.grace_until = derived.grace_until
        row.provider_checkout_session_id = None
        row.checkout_idempotency_key = None
        row.checkout_expires_at = None
    elif forced_state is not None and row is not None:
        row.state = forced_state
    else:
        return []
    row.updated_at = now
    new_plan_id = plan_for_state(row.state, row.plan, row.grace_until, now)
    old_plan_id = account.plan
    await db.flush()
    if new_plan_id == old_plan_id:
        return []
    account.plan = new_plan_id
    old_plan, new_plan = plan_for(old_plan_id), plan_for(new_plan_id)
    touched = await _fit_devices(db, account, old_plan, new_plan, now)
    events.record(
        db,
        account_id=account_id,
        kind="plan_changed",
        severity="notice",
        actor="system",
        subject_id=row.id,
        detail={"from": old_plan_id, "to": new_plan_id, "reason": reason, "subscription_state": row.state},
    )
    log.info(
        "billing.transition",
        account_id=str(account_id),
        from_plan=old_plan_id,
        to_plan=new_plan_id,
        subscription_state=row.state,  # "state" is a redacted key (the OAuth state)
        reason=reason,
        pcs_touched=len(touched),
    )
    await db.flush()
    return touched
