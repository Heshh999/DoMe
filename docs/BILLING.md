# DoMe billing, entitlements and customer lifecycle

Status: **design for Phase C, written against the schema that cloud-api's migration 0001 already
creates.** The parts marked *implemented* exist in the repository today and are integration-tested
on Linux; everything marked *planned* is not written yet. No Stripe call, webhook handler or
subscription state transition exists in the code. Prices and limits are design assumptions from
`shared/protocol/plans.json`, not validated pricing.

Evidence tags used below: `unit-tested`, `integration-tested` (Linux, real PostgreSQL, real OIDC
through `tools/dev-idp`, simulated agents/controllers), `sandbox-provider-tested` (none yet),
`not yet verified`.

## 1. What exists now versus what is planned

| Area | Implemented now | Evidence | Planned (Phase C) |
| --- | --- | --- | --- |
| Plan catalogue | `shared/protocol/plans.json` loaded by `cloud-api/dome_api/plans.py`, the only place limits are read; `GET /v1/plans` returns plans, display prices and `billing_enabled` (currently `false`) | integration-tested | Same file; Stripe price ids come from `DOME_STRIPE_PRICE_MONTHLY` / `DOME_STRIPE_PRICE_ANNUAL` |
| `accounts.plan` | Column exists, defaults to `free`; nothing in the code changes it (tests switch it with SQL) | integration-tested | Written only by the subscription state machine |
| Database tables | `subscriptions`, `billing_events`, `usage_periods`, `layouts`, `routines`, `support_diagnostics`, `pending_deletions`, `operator_users`, `operator_audit` are created empty by migration 0001 (`cloud-api/dome_api/db/alembic/versions/0001_initial.py`) | integration-tested (migration runs in every test session; `alembic check` clean) | Rows written by Phase C code |
| Device limits | `max_enabled_pcs` enforced transactionally at link approval and PC enable (`SELECT … FOR UPDATE` on the account row); `max_controllers` enforced transactionally at pairing claim | integration-tested | Re-evaluated on every plan change |
| Plan-disabled devices | `grants_snapshot.pc_enabled` and per-controller `status: plan_disabled` computed per `downgrade_policy.default_selection` (`most_recently_seen`); relay refuses routing with `PC_PLAN_DISABLED` / `CONTROLLER_PLAN_DISABLED`; agent refuses independently; local grants are never revoked | integration-tested (relay), unit-tested (agent) | Explicit customer selection endpoint and the 14-day window |
| Entitlement assertions | EdDSA (Ed25519) JWS, 1 h lifetime, claims per `schemas/entitlement.schema.json`, bound to `account_id` + `pc_id`, served by `POST /v1/agent/entitlement` and inside every `grants_snapshot` for plans that issue one; JWKS at `/.well-known/dome-jwks.json` | integration-tested (cloud-api), unit-tested (agent verification, 80 % refresh, 72 h grace only on network error/5xx) | Issued for `pro` once the state machine sets the plan |
| Agent-side gate | Routine steps (`origin.kind == "routine"`) require `routine_allowed` and a verified assertion with `routines: true`, else `ENTITLEMENT_REQUIRED`; "editing client state cannot unlock Pro" is tested end to end | integration-tested | Layout/routine CRUD that produces such steps |
| PWA | Billing page shows the current plan, limits and the planned price with "not available yet"; Routines page is a disabled preview | unit-tested (component tests) | Checkout button, portal link, pending-activation state, downgrade selection UI |
| Stripe | `DOME_STRIPE_SECRET_KEY`, `DOME_STRIPE_WEBHOOK_SECRET`, price-id variables are declared and validated as settings; nothing uses them | — | Checkout session, Customer Portal session, webhook endpoint, reconciliation job |
| Usage periods | Table only; AI allowances are `0` in both plans | — | Phase E, after the AI release gate |
| Operator interface | Tables only (`operator_users`, `operator_audit`) | — | Phase C/D |
| Account deletion | Not implemented (no endpoint) | — | Deletion flow with `pending_deletions` fallback |

Nothing below has been sandbox-provider-tested. The first Phase C task is to run the lifecycle
against Stripe test mode with test clocks and record the evidence in `docs/ACCEPTANCE.md`.

## 2. Plan contract

From `plans.json` (design assumptions, editable without code changes):

| | DoMe Free | DoMe Pro |
| --- | --- | --- |
| Enabled PCs | 1 | 5 |
| Paired controllers | 2 | 5 |
| Custom layouts | no | yes |
| Routines | no | yes, ≤ 10 steps, ≤ 60 s |
| AI interpretations / transcription minutes per period | 0 | 0 until the Phase E gate (cost model evaluates 250 / 30) |
| Manual command rate limit | 120/min, burst 30 | same |
| Coalescable (slider) rate limit | 360/min, burst 60 | same |
| Display price | — | US$5.99/month or US$49.99/year (`pricing_defaults`; live amounts come from the Stripe price objects) |

Policy values also in `plans.json`: `downgrade_policy.selection_window_days = 14`,
`default_selection = most_recently_seen`, `billing_grace.past_due_grace_days = 7`,
`billing_grace.agent_offline_assertion_grace_hours = 72`, `entitlement_assertion.lifetime_seconds = 3600`.

Rules that hold on every plan: authentication, TLS, revocation, emergency stop, security events,
session management and permission controls are never gated. A plan limit is never a revocation.

## 3. Records

Columns are those of `cloud-api/dome_api/db/models.py`.

- `subscriptions` — one row per account at most for `provider = stripe`: `provider_customer_id`,
  `provider_subscription_id` (unique), `price_id`, `plan`, `state`, `current_period_start/end`,
  `cancel_at_period_end`, `grace_until`, `created_at`, `updated_at`. The row is the backend's cache
  of the provider's authoritative state plus DoMe's own grace decision.
- `billing_events` — durable webhook receipt: `provider_event_id` (unique → deduplication),
  `event_type`, `account_id` (nullable: an event may arrive before it can be attributed),
  `received_at`, `processed_at`, `status` (`received` → `processed` | `ignored` | `failed`), `error`,
  `payload_digest`. The raw payload is **not** stored; the handler re-fetches the object from Stripe.
- `usage_periods` — per account and `period_start` (unique): used and reserved counters for AI
  interpretations and transcription seconds (Phase E).
- `pending_deletions` — one per account (unique): `provider_cancel_state`, `attempts`, `last_error`,
  `completed_at`. Exists so a failed provider call never makes the product claim a deletion it did not
  finish.
- `operator_audit` — every operator action with `target_account_id` and redacted `detail`.
- `accounts.plan` — the single derived value the rest of the system reads (`plans.py` → relay snapshot,
  entitlement issuance, limit checks). It is written only by the state machine in §4.

## 4. Subscription → entitlement state machine (planned)

### 4.1 Subscription states (`subscriptions.state`)

| State | Meaning | Provider status it mirrors |
| --- | --- | --- |
| `none` | No subscription row, or the row is historical | — |
| `checkout_pending` | A Checkout Session was created for this account and has not completed; nothing is unlocked | Checkout Session `open` |
| `incomplete` | Subscription created but the first payment has not succeeded | `incomplete` |
| `trialing` | Only if a trial is ever configured (none is planned at launch) | `trialing` |
| `active` | Paid for the current period | `active` |
| `cancel_scheduled` | Active, `cancel_at_period_end = true`; access continues to `current_period_end` | `active` + `cancel_at_period_end` |
| `past_due` | A renewal failed; inside `past_due_grace_days` (`grace_until` set) | `past_due` |
| `unpaid` | Grace exhausted or provider gave up retrying | `unpaid` |
| `canceled` | Ended (by customer, by dunning, by refund policy or by deletion) | `canceled` / `incomplete_expired` |
| `disputed` | A chargeback is open on the latest invoice | `active` with an open dispute |

### 4.2 Derived entitlement (`accounts.plan` plus a UI-only flag)

| Subscription state | `accounts.plan` | Entitlement assertion issued | UI label |
| --- | --- | --- | --- |
| `none`, `checkout_pending`, `incomplete`, `unpaid`, `canceled` | `free` | no (`assertion: null`) | Free (checkout_pending shows "Pending activation") |
| `trialing`, `active`, `cancel_scheduled` | `pro` | yes | Pro (cancel_scheduled shows the end date) |
| `past_due` (before `grace_until`) | `pro` | yes | Pro, "Payment problem, update your card by <date>" |
| `past_due` (after `grace_until`) | transition to `unpaid` by the reconciliation timer | — | — |
| `disputed` | `free` while open | no | "Access paused while a payment dispute is open" |

The relay and the agent read only `accounts.plan` (through `plans.py`) and the assertion. There is
no second place where the plan is decided.

### 4.3 Transitions

Every transition runs inside one database transaction that locks the account row
(`SELECT … FOR UPDATE`, the same pattern the device-limit checks use), writes `subscriptions`,
writes `accounts.plan`, appends a `security_events` row of kind `plan_changed` (actor `system`,
detail `{from, to, reason}`) and, after commit, pushes a fresh `grants_snapshot` to every connected
agent of the account (`ConnectionManager.push_grants_snapshot`, implemented) so plan-disabled
devices and entitlement assertions change within one round trip.

| From | To | Trigger | Side effects |
| --- | --- | --- | --- |
| `none` | `checkout_pending` | `POST /v1/billing/checkout` (session + CSRF) | Create/lookup Stripe Customer bound to `account_id` (idempotency key `checkout:<account_id>:<price_id>`); store `provider_customer_id`; return the hosted Checkout URL |
| `checkout_pending` | `active` | `checkout.session.completed` **and** the fetched subscription is `active` | Set `provider_subscription_id`, `price_id`, period, plan `pro`; push snapshot |
| `checkout_pending` | `incomplete` | Fetched subscription is `incomplete` | Plan stays `free`; UI "Pending activation" |
| `checkout_pending` | `none` | Checkout expired (`checkout.session.expired`) or 24 h reconciliation sweep | Row deleted |
| `incomplete` | `active` | `invoice.paid` → fetch shows `active` | Plan `pro` |
| `incomplete` | `canceled` | `incomplete_expired` | — |
| `active` | `active` | `invoice.paid` (renewal) | Update period; no plan change |
| `active` | `cancel_scheduled` | `customer.subscription.updated` with `cancel_at_period_end = true` (customer used the Portal) | UI shows end date; access unchanged |
| `cancel_scheduled` | `active` | Same event with `cancel_at_period_end = false` (customer resumed) | — |
| `active` / `cancel_scheduled` | `past_due` | `invoice.payment_failed` → fetch shows `past_due` | `grace_until = max(now, current_period_end) + past_due_grace_days`; plan stays `pro`; email "payment failed" |
| `past_due` | `active` | `invoice.paid` | Clear `grace_until` |
| `past_due` | `unpaid` | Reconciliation timer passes `grace_until`, or provider reports `unpaid` | Plan `free`; downgrade selection window opens (§8) |
| `unpaid` | `active` | Provider reports `active` again (customer paid the open invoice) | Plan `pro`; plan-disabled devices re-enabled if within limits |
| any paid state | `canceled` | `customer.subscription.deleted` (period end reached after cancel, or dunning ended) | Plan `free`; selection window opens; settings retained |
| `active` | `disputed` | `charge.dispute.created` on the latest invoice | Plan `free`; operator alert |
| `disputed` | `active` | `charge.dispute.closed` with status `won` | Plan `pro` |
| `disputed` | `canceled` | `charge.dispute.closed` with status `lost` | Subscription canceled at the provider immediately |
| any | `canceled` | Account deletion (§11) | Provider subscription canceled immediately, no proration refund by default |

Rule: **a webhook never sets a state directly.** It marks the event received and schedules a
"refresh subscription `<id>`" job that fetches the subscription and latest invoice from Stripe and
applies the table above to what the provider says *now*. Out-of-order, duplicate and delayed events
therefore converge on the same result (§6).

## 5. Checkout and portal (planned)

- `POST /v1/billing/checkout {interval: "monthly" | "annual"}` — requires a session, CSRF header and
  exact `Origin` like every state-changing route. The price id is taken from the server allowlist
  (`DOME_STRIPE_PRICE_*`); the request carries no amount, currency or price. One Checkout Session is
  created per account with an idempotency key; a second call while one is open returns the same URL.
  If the account already has an `active`/`past_due`/`cancel_scheduled` row the call returns 409
  `ALREADY_SUBSCRIBED` and the Portal URL instead, so duplicate subscriptions cannot be created.
- `POST /v1/billing/portal` — creates a Customer Portal session for the stored
  `provider_customer_id`; the Portal handles card updates, interval changes, invoices and cancellation.
  Cancelling never requires contacting support.
- The Checkout `success_url` lands on `/app/billing?checkout=return`. The page shows "Pending
  activation — we are confirming your payment" until `GET /v1/session` reports `plan: pro`. **Nothing
  is unlocked from the return URL** (spec §14).
- Card data never touches DoMe; Stripe-hosted pages only.
- Trials: not configured. If one is introduced later, `trialing` maps to Pro and the first failed
  charge after the trial goes through the `incomplete` path.

## 6. Webhook intake, deduplication and reconciliation (planned)

1. `POST /v1/billing/webhook` reads the **raw body** (≤ 64 KiB, larger bodies are refused with 413
   before parsing), verifies `Stripe-Signature` with `DOME_STRIPE_WEBHOOK_SECRET` and a 5-minute
   tolerance, and rejects anything else with 400. The route is exempt from session/CSRF checks and,
   like the agent routes, refuses any `Origin` header.
2. Inside one transaction: `INSERT INTO billing_events (provider_event_id, event_type, payload_digest,
   status='received')`. A unique-constraint conflict means a duplicate delivery: respond 200 and stop.
   The response is sent only after commit (`Depends(scope="function")`, the same fix applied to the
   REST routes in DECISIONS #18).
3. Attribution: `customer` / `subscription` ids on the event are mapped to `account_id` through
   `subscriptions`; unknown ids are stored with `account_id = NULL`, `status = ignored`,
   `error = "unattributed"` and surfaced to operators (a customer created outside the product, or a
   webhook from the wrong Stripe account).
4. Processing is asynchronous (an in-process worker in the single-process deployment, ADR-0001 D1)
   and idempotent: the worker fetches the current subscription + latest invoice (+ open disputes) and
   applies §4.3. `processed_at`/`status` are written when done; exceptions set `status = failed` with
   the redacted error and the event is retried with backoff up to 5 times.
5. Event types handled: `checkout.session.completed`, `checkout.session.expired`,
   `customer.subscription.created|updated|deleted`, `invoice.paid`, `invoice.payment_failed`,
   `charge.refunded`, `charge.dispute.created|closed`. Everything else is recorded as `ignored`.
6. Reconciliation job (hourly, and on demand from the operator interface): for every `subscriptions`
   row not in `none`/`canceled`, fetch the provider subscription and re-apply §4.3; for every
   `checkout_pending` older than 24 h, expire it; for every account with `plan = pro` and no live
   subscription row, or `plan = free` with an `active` row, write an `entitlement_mismatch` operator
   item. Discrepancies, failed events and unattributed events form the **operator failure queue**
   (`billing_events.status in (failed, ignored)` plus `operator_audit` entries). Operators can re-run an
   event or a reconciliation but cannot set a plan by hand without an audited reason.

## 7. Proration policy (proposed, founder decision)

| Change | Effect | Stripe setting |
| --- | --- | --- |
| Monthly → annual | Immediately; unused monthly time is credited against the annual invoice | `proration_behavior = create_prorations`, invoice now |
| Annual → monthly | At the end of the annual period (no mid-term refund) | Subscription schedule phase change at `current_period_end` |
| Cancel | At period end; access continues until then; no partial refund | Portal cancellation, `cancel_at_period_end` |
| Immediate cancel with refund | Only by operator action for a documented reason (refund policy §9) | `cancel_now` + refund |

The Portal is configured to offer exactly these options so the Portal and the state machine cannot
disagree.

## 8. Grace, cancellation and downgrade

**Payment failure.** `past_due_grace_days = 7`: a failed renewal keeps Pro for seven days past the
period end while Stripe retries (smart retries, then the final attempt). The customer is told the
date on the Billing page and by email. After the grace the subscription is `unpaid` → Free.

**Provider outage.** The backend keeps issuing assertions from its own `accounts.plan`; a Stripe
outage changes nothing until a webhook or reconciliation says otherwise. On the PC, a verified Pro
assertion is honoured for at most `agent_offline_assertion_grace_hours = 72` after `exp` **only** when
the refresh fails with a network error or 5xx (unit-tested); a `200 {assertion: null}` is Free
immediately.

**Cancellation.** The Billing page says, before the customer confirms in the Portal: "Pro stays on
until <current_period_end>. After that your account returns to Free: 1 PC and 2 phones stay enabled,
your layouts and routines are kept but cannot run, and you can re-subscribe at any time." Nothing is
deleted.

**Downgrade selection (14-day window, `plans.json`).** When the plan becomes `free` with more than
`max_enabled_pcs` enabled PCs or more than `max_controllers` live controllers:

- Implemented now: controllers are selected automatically by `last_seen_at` descending (ties by
  `created_at`), the rest are `plan_disabled` in every snapshot; `pcs.enabled` is a stored flag, so
  Phase C must set `enabled = false` on the PCs beyond the limit using the same ordering.
- Planned: `GET /v1/account/plan-selection` lists the candidates with the deadline
  (`plan_changed_at + selection_window_days`), `PUT /v1/account/plan-selection {pc_ids, controller_ids}`
  (session + CSRF, sizes checked against the plan, ownership checked) applies the choice
  transactionally and pushes snapshots. Until the customer chooses, and after the window closes, the
  default selection stands. Changing the selection later is allowed by disabling one device and
  enabling another (the existing `PATCH /v1/pcs/{id}` path already enforces the limit).
- Revocation (`DELETE /v1/controllers/{id}`, `DELETE /v1/grants/{id}`, `DELETE /v1/pcs/{id}`, the PC's
  local `revoke_controller` and local "Disable remote control") works for every device regardless of
  plan state (integration-tested).
- Re-upgrade re-enables devices up to the Pro limits, restoring the previous selection where it still
  fits.

## 9. Refunds and disputes (proposed policy, needs founder review)

- Refund requests within 14 days of the first charge are granted by an operator through Stripe
  (`charge.refunded` arrives) and the subscription is canceled immediately; otherwise cancellation at
  period end applies and no partial refund is made. Jurisdictions with statutory withdrawal rights
  (for example EU consumer law) may require more: **founder/legal input needed**.
- A `charge.refunded` event alone does not change the plan (operators refund for many reasons); the
  operator action that issued the refund decides whether to cancel. A full refund of the current
  period without cancellation is flagged by reconciliation as a mismatch.
- A dispute (`charge.dispute.created`) pauses Pro at once (state `disputed` → Free) because the funds
  are contested; devices beyond Free limits become plan-disabled, nothing is deleted. `won` restores
  Pro; `lost` cancels. The customer sees a plain explanation on the Billing page and an email. Stripe's
  dispute fee (US$15 in the pricing sources used by `docs/COST_MODEL.md`) is a cost of doing business
  and is in the cost model as an allowance.

## 10. Usage periods and AI allowances (Phase E, planned)

Both plans have `0` allowances today and the AI provider adapter is not built. When enabled:

- A usage period is one calendar month anchored to the subscription's start day (annual subscribers
  get 12 monthly periods; a full year's allowance is never granted at once). `usage_periods` has one
  row per `(account_id, period_start)`.
- Reserve → call provider → settle: `ai_interpretations_reserved` is incremented atomically
  (`UPDATE … WHERE used + reserved < allowance`) before the call; on success the reservation moves
  to `used`; on provider error, timeout or customer cancel the reservation is released and nothing is
  charged to the customer. Units are never deducted for requests a product error prevented from
  being submitted.
- No automatic overage. At exhaustion the UI falls back to typed deterministic commands and says so.
- An operator spending ceiling per period stops all AI calls when reached.

## 11. Account deletion (planned)

`DELETE /v1/account` (session + CSRF + re-authentication through the OIDC issuer within the last
5 minutes) runs, in order:

1. Revoke every session (closing its controller sockets), every PC credential and access token
   (closing agent sockets with `revoked{account_deleted}`), every controller and grant. This is the
   same code the existing revocation routes use.
2. Cancel the provider subscription immediately (`cancel_now`). If the call fails, insert a
   `pending_deletions` row (`provider_cancel_state = pending`, `attempts`, `last_error`), alert
   operators, and tell the customer "Your data is removed; we are still confirming the subscription
   cancellation with our payment provider" — never a false "done".
3. Soft-delete the account (`accounts.deleted_at`), which hides it from sign-in; the OIDC subject is
   kept hashed so a re-registration does not inherit anything.
4. A daily job hard-deletes rows that cascade from `accounts` after 30 days (the window exists so an
   accidental deletion can be reversed by support with the customer's confirmation), except the
   records in `docs/DATA_RETENTION.md` that are retained for an identified reason
   (`billing_events` and invoice references for tax/accounting, `operator_audit`).
5. Local data on the PC and the phone is the customer's: the agent sees `revoked` and discards its
   credential; the uninstall steps in `pc-agent/README.md` remove the rest.

## 12. Verification plan (none of it done yet)

| Check | Method | Status |
| --- | --- | --- |
| Checkout → `active`, renewal, failed payment → grace → unpaid, cancel at period end, resume | Stripe test clocks in test mode; assert `subscriptions.state`, `accounts.plan`, snapshot push | not yet verified |
| Duplicate, out-of-order and delayed webhooks | Replay recorded events in shuffled order with duplicates; final state identical | not yet verified |
| Webhook signature, oversized body, wrong account | 400/413 and `ignored` rows | not yet verified |
| Downgrade selection and defaults | Integration test: 3 PCs + 4 controllers on Pro → Free; snapshots show the right `plan_disabled` set; selection endpoint changes it | partly: the default selection path is integration-tested today |
| Client state cannot unlock Pro | Existing `tests/test_e2e_security.py` scenario (13) | integration-tested |
| Deletion with a failing provider call | Fault-injected Stripe client; `pending_deletions` row and operator alert | not yet verified |
| Live charge | Never by default; requires founder-enabled live keys and a separate checklist | not applicable yet |

## 13. Open founder decisions

Currency and tax handling (Stripe Tax or not), the legal entity and business details on invoices,
refund wording per jurisdiction, whether a trial is offered, the exact Portal features enabled, and
the live price ids. Generated copy does not prove legal compliance.
