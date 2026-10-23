# DoMe billing, entitlements and customer lifecycle

Status (2026-10-20): **built in cloud-api and tested against an in-memory stand-in for Stripe; never run
against Stripe itself.** Checkout, the Customer Portal, AI top-ups, the webhook, the subscription state
machine, grace, the downgrade selection, the reconciler and the account-deletion cancellation function exist
in `cloud-api/dome_api/billing/` and are integration-tested on Linux with real PostgreSQL and the fake provider
(`cloud-api/dome_api/billing/fake_provider.py`). The Stripe adapter is unit-tested without the network, and since
2026-10-23 every request it sends (with the billing service's own arguments) and every response field it reads is
checked against stripe-mock v0.206.0, Stripe's official mock API, in CI (§12). stripe-mock checks parameter names,
types, enums and the API version, not an account's objects or rules, so nothing has been sandbox-provider-tested yet:
the founder's first Stripe test-mode run on staging is still the proof (`docs/LAUNCH_SETUP.md` "Stripe (payments)").
Production has no Stripe values, so billing there is **off**:
nothing can be bought and everyone stays on Free. On staging and production the values come from the GitHub
Environment through the Deploy button, which checks them before touching Fly (all seven or none, key and price-id
shapes, no live key on staging), stores the two secrets in Fly, passes the five price ids, removes Stripe secrets
GitHub no longer has, and reports "Stripe: off", "Stripe: TEST MODE — no real charges" or "Stripe: LIVE — real
charges" (`deploy/README.md` "Billing (Stripe) through the Deploy button"; unit-tested against a fake `flyctl`, never
run against Fly).

Evidence tags used below: `unit-tested`, `integration-tested (fake provider)` (Linux, real PostgreSQL, real
OIDC through `tools/dev-idp`, simulated agents/phones, `FakeStripe` signing its deliveries exactly the way
Stripe does), `stripe-mock-validated` (requests and response fields checked by stripe-mock v0.206.0, Stripe's
official mock API, started in CI with `-strict-version-check`: parameter names, types, enums and the API version the
pinned SDK sends; it knows no account, so a price's existence and type, the account name Checkout needs, the
`expires_at` window and the Portal configuration are not checked), `sandbox-provider-tested` (none yet),
`not implemented`, `not yet verified`.

## 1. What exists now

| Area | What it does | Evidence |
| --- | --- | --- |
| Plan catalogue | `shared/protocol/plans.json` (protocol 1.3): Free, Pro, Pro Plus; `pricing_defaults` is the only place display prices live; `GET /v1/plans` returns plans, `pricing`, `billing_enabled`, `billing_mode` and `ai_enabled` (whether the assistant can be used, so the website labels AI allowances honestly) | integration-tested (`test_entitlement_and_misc.py`, `test_billing_settings.py::test_plans_endpoint_reports_billing_mode`) |
| Configuration | Seven values switch billing on: `DOME_STRIPE_SECRET_KEY`, `DOME_STRIPE_WEBHOOK_SECRET`, `DOME_STRIPE_PRICE_PRO_MONTHLY`, `_PRO_ANNUAL`, `_PRO_PLUS_MONTHLY`, `_PRO_PLUS_ANNUAL`, `_TOPUP`. All or none: a partial set refuses start-up and names the missing variables (never values); a live key refuses start-up outside production | unit-tested (`test_billing_settings.py`) |
| Deploy button | Carries the seven values from a GitHub Environment (two secrets on stdin, five price-id variables with `-e`), applies the same rules before any Fly call, removes leftover Stripe secrets when GitHub has none, checks `GET /v1/plans` after the deploy and reports off / test / live (`deploy/README.md`) | unit-tested (`deploy/launch/tests/test_deploy_billing.py`, fake `flyctl`); never run against Fly |
| Checkout | `POST /v1/billing/checkout {plan, interval}` → a Stripe-hosted page; one open page per account is reused | integration-tested (fake provider) |
| Customer Portal | `POST /v1/billing/portal {}` → Stripe's portal for card, interval, plan changes, invoices, cancellation | integration-tested (fake provider) |
| AI top-up | `POST /v1/billing/topup {}` → a one-off hosted page for 100 AI requests; credited once from a verified paid Checkout | integration-tested (fake provider) |
| Webhook | `POST /v1/billing/webhook`: signature over the raw body, deduplication, synchronous processing, retries | integration-tested (fake provider) |
| State machine | `billing/state.py::apply()` is the only writer of `accounts.plan` | integration-tested (fake provider) |
| Grace and downgrade selection | 7-day grace after a failed payment; `GET/PUT /v1/account/plan-selection` within 14 days of a downgrade | integration-tested (fake provider) |
| Reconciler | Hourly pass in the API process; `dome-api --billing-reconcile` runs one pass by hand | integration-tested (fake provider) |
| Account deletion | `BillingService.cancel_for_account_deletion()` with a `pending_deletions` retry | integration-tested (fake provider); the `DELETE /v1/account` route is **not implemented** |
| Stripe adapter | `billing/stripe_provider.py` on the official `stripe` SDK 16.x (`*_async` methods, SDK retries off, 10 s timeout) | unit-tested against the SDK's own signature helper, object models and Stripe-shaped error answers (`test_billing_stripe_adapter.py`); stripe-mock-validated: every request with the service's own arguments, and the fields read back (`test_stripe_provider_mock.py`, CI); **sandbox-provider-tested: not yet** |
| Disputes, refunds, trials | Recorded as `ignored` with a `billing.needs_operator` warning (disputes, refunds); no trial is configured | **not implemented** |
| PWA | Billing page states, pricing page "Available now" when `billing_enabled` (mobile-app, built separately) | unit-tested (component tests in `mobile-app`) |

## 2. Plan contract

From `plans.json` (design assumptions, editable without code changes):

| | DoMe Free | DoMe Pro | DoMe Pro Plus |
| --- | --- | --- | --- |
| Enabled PCs / paired phones | 1 / 2 | 5 / 5 | 5 / 5 |
| Custom layouts, routines | no | yes (routines ≤ 10 steps, ≤ 60 s) | yes (same) |
| AI requests | one-time taste of 10, never renews | 30 per 5-hour window, 120 per weekly window | 90 per 5-hour window, 300 per weekly window |
| AI top-ups | no | yes | yes |
| AI cost backstop (internal) | 100 ¢ per weekly window | 500 ¢ per weekly window | 1000 ¢ per weekly window |
| Price (`pricing_defaults`) | $0 | $9.99 / month or $99.99 / year | $24.99 / month or $249.99 / year |
| AI top-up | — | 100 requests for $2.99, one-off | same |

Prices are in US dollars. The Stripe prices the founder created are **tax-inclusive** (founder decision,
2026-10-20): any sales tax is part of the amount, never added on top, and Stripe Tax is off
(`DOME_STRIPE_AUTOMATIC_TAX=false`). The amounts charged are those of the Stripe price objects; the founder keeps
them equal to `pricing_defaults`.

Policy values also in `plans.json`: `downgrade_policy.selection_window_days = 14`, `default_selection =
most_recently_seen`, `billing_grace.past_due_grace_days = 7`, `billing_grace.agent_offline_assertion_grace_hours =
72`, `entitlement_assertion.lifetime_seconds = 3600`.

**The PC sees "pro" for every paid plan.** The entitlement assertion's `plan` claim and
`agent_entitlement_response.plan` are `pro` for Pro and Pro Plus alike (`Plan.entitlement_plan_id`): the
installed agents validate against a schema that knows only `free` and `pro`, and Pro Plus has exactly Pro's
PC-facing limits. The real plan id is in `GET /v1/session` and `GET /v1/account/billing`.

Rules that hold on every plan: authentication, TLS, revocation, emergency stop, security events, session
management and permission controls are never gated. A plan limit is never a revocation.

## 3. Records

Columns are those of `cloud-api/dome_api/db/models.py` (migration `0004_billing.py`). No billing column holds an
email, card data or a raw webhook payload: provider ids, states, counts and timestamps only.

- `subscriptions` — the account's live row (partial unique index `uq_subscriptions_live_account`: one row per
  account whose `state <> 'canceled'`) plus older canceled rows. Provider ids, `price_id`, `plan`, `interval`,
  `state` (checked against the eight states of §4.1), period, `cancel_at_period_end`, `grace_until`,
  `provider_checkout_session_id` (unique), `checkout_idempotency_key`, `checkout_expires_at`, `provider_status`,
  `provider_synced_at`.
- `billing_events` — the webhook receipt: `provider_event_id` (unique, deduplication), `event_type`, `kind`
  (`subscription` / `checkout` / `other`), `provider_object_id`, `account_id` (null until attributed), `status`
  (`received` → `processed` | `ignored` | `failed`), `attempts`, `error` (a short code), `payload_digest`
  (SHA-256 of the raw body). The raw payload is never stored.
- `usage_windows`, `ai_balances`, `topup_purchases` — AI allowance accounting (§10).
- `accounts.plan_changed_at`, `pcs.plan_disabled_at`, `controllers.plan_selected_at` — the downgrade selection
  (§8).
- `pending_deletions` — a provider cancellation that has not succeeded yet (§11).
- `usage_periods` — the calendar-month table of the first AI build; kept, **no longer written**.
- `accounts.plan` — the single derived value the rest of the system reads (`plans.py` → relay snapshot,
  entitlement issuance, limit checks). It is written only by `billing/state.py::apply()`.

## 4. Subscription → entitlement state machine

### 4.1 Subscription states (`subscriptions.state`)

| State | Meaning | From Stripe's subscription status |
| --- | --- | --- |
| `checkout_pending` | DoMe created a hosted page; nothing is unlocked | (our own row) |
| `incomplete` | Subscription created, first payment not through (for example 3-D Secure pending) | `incomplete` |
| `trialing` | Only if a trial is ever configured (none is) | `trialing` |
| `active` | Paid for the current period | `active` |
| `cancel_scheduled` | Active until `current_period_end`, then ends | `active` + `cancel_at_period_end` |
| `past_due` | A payment failed; Pro or Pro Plus kept until `grace_until` | `past_due` |
| `unpaid` | Grace ended (DoMe's sweep) or Stripe stopped retrying | `unpaid`, `paused`; or `past_due` after `grace_until` |
| `canceled` | Ended | `canceled`, `incomplete_expired`, anything unknown |

### 4.2 Plan and the label the phone shows

| Subscription state | `accounts.plan` | `entitlement_state` (session and Billing page) |
| --- | --- | --- |
| no row | `free` | `free` |
| `checkout_pending` with a hosted page that can still be paid | `free` | `pending` (a page that was never created or has expired: `free`) |
| `incomplete` | `free` | `pending` |
| `active`, `trialing` | the paid plan | `active` |
| `cancel_scheduled` | the paid plan | `canceling` |
| `past_due` before `grace_until` | the paid plan | `past_due` |
| `unpaid`, `canceled` (row updated within 30 days) | `free` | `expired` (older: `free`) |

Evidence: integration-tested (fake provider), `test_billing_lifecycle.py::test_entitlement_state_mapping_in_session_document`.

A price id that `DOME_STRIPE_PRICE_*` does not name (a subscription made by hand in the dashboard, a Portal switch to
another product, any other product on the same Stripe account) **grants no paid plan**: the row follows the
subscription's state with the default plan, so the account stays Free and shows no DoMe subscription, the Portal stays
reachable, and the warning `billing.unknown_price` carries the account id only, for the operator to fix the
configuration (`test_unknown_price_grants_no_paid_plan_and_logs_the_account_only`). A paid plan is granted only for a
configured price.

### 4.3 How a change is applied

**A webhook never sets a state from its payload.** Every handled event leads to a fetch of the object as Stripe
has it now (the subscription, or the Checkout Session), and `state.apply()` maps that onto §4.1/§4.2. Duplicate,
delayed and out-of-order deliveries therefore converge (`test_out_of_order_deliveries_converge`, all 24 orders of
created / completed / paid / deleted).

**Fetch and apply are serialised per account.** The subscription is fetched inside the same transaction that applies
it, after a per-account advisory lock (`pg_advisory_xact_lock`), so two handlers (two webhooks, or a webhook and the
reconciler) apply in the order they fetched and an older snapshot can never land after a newer one
(`test_fetch_and_apply_are_serialised_per_account`). In addition, `canceled` is final for a subscription id: a
snapshot of the same subscription that says anything else is ignored, because Stripe never reactivates a canceled
subscription (`test_a_stale_active_snapshot_never_reactivates_a_canceled_subscription`).

`apply()` runs in one transaction with the lock order accounts → subscriptions → PCs/controllers →
security events. It writes the row and `accounts.plan`; when the plan changed it appends a `plan_changed` security
event (actor `system`, detail `{from, to, reason, subscription_state}`) and the log line `billing.transition`.
When the limits shrink it sets `accounts.plan_changed_at`, disables the PCs beyond `max_enabled_pcs` (the most
recently seen stay; `plan_disabled_at` set) and leaves controllers to the relay's dynamic selection. When they
grow it re-enables plan-disabled PCs up to the limit (never a PC the owner disabled by hand) and clears
`plan_changed_at`. After the commit, every online PC of the account gets a fresh `grants_snapshot` with its new
assertion (`test_checkout_completed_activates_pro_and_pushes_snapshot_with_assertion`).

An event about an **ended** subscription of the account (Stripe redelivering late) never touches the plan while a
newer subscription is live, and a second live subscription paid outside DoMe's own pages is logged
(`billing.second_subscription`) and left to the operator
(`test_late_events_of_an_ended_subscription_never_touch_the_new_one`).

`accounts.plan` is reached only from: verified webhook processing, the reconciler's provider fetch, a provider fetch
of a pending page that turned out to be paid (on `GET /v1/account/billing` or a second checkout, §5), the grace sweep,
and account-deletion cancellation. Never from a request body or a Checkout return URL
(`test_return_url_unlocks_nothing`).

**Grace.** The first failed payment DoMe learns of sets `grace_until = now + 7 days` once; later failure notices do
not move it (`test_failed_payment_enters_grace_keeps_pro_then_unpaid_free_after_grace`). The anchor is the notice,
not `current_period_end`, because at a failed renewal Stripe has already moved the period to the new, unpaid one.
After the deadline the hourly sweep sets `unpaid` → Free, and a later fetch that still says `past_due` keeps it
`unpaid`. A payment inside the grace returns the row to `active` and clears the deadline
(`test_payment_recovery_in_grace_returns_active_and_clears_grace`).

## 5. Checkout, Portal and top-up

All three are PWA routes: session cookie, `X-DoMe-CSRF`, exact `Origin`; at most 10 page creations per account per
hour (`DOME_RATE_BILLING_REQUESTS_PER_HOUR`); every response `Cache-Control: no-store`. Without the Stripe values
each answers 503 `BILLING_NOT_AVAILABLE`.

- `POST /v1/billing/checkout {plan: pro|pro_plus, interval: monthly|annual}`. The price comes from the server's
  allowlist; the body carries no price, amount or currency (unknown fields are 400). A Stripe Customer is created
  once per account (idempotency key `customer:<account_id>`; the email goes to Stripe for receipts and is never
  logged). The `checkout_pending` row, its idempotency key and the page's expiry are committed before Stripe is
  called. A second call while the page is open returns **the same page**; a different choice gets its own page, and
  the old page is closed at Stripe first, so two pages can never both be paid. A request that arrives while another
  is still creating the page (a double tap, two tabs; no session id yet, within 30 seconds) replays the same
  idempotency key and expiry, so Stripe answers with the same page; a different choice in that moment gets 409
  `CHECKOUT_PROCESSING` (`test_concurrent_checkouts_create_one_page`). A stored page that Stripe reports
  **complete** (paid, the webhook not processed yet) is never followed by a second page: its subscription is fetched
  and applied at once and the call answers 409 `ALREADY_SUBSCRIBED`, or `CHECKOUT_PROCESSING` while Stripe has not
  created the subscription yet (`test_a_paid_page_whose_webhook_is_late_is_never_sold_twice`). An account with a
  live subscription gets 409 `ALREADY_SUBSCRIBED` (detail `{portal_available}`). A Stripe failure is 502
  `BILLING_PROVIDER_ERROR` (the phone gets `{reason}` only) and nothing shows as pending; the server logs one
  `billing.provider_error` line with the operation, the reason, Stripe's HTTP status and request id, and Stripe's own
  short machine strings `stripe_error` (its error code, such as `resource_missing`) and `stripe_param` (the parameter
  it names, such as `line_items[0][price]`), never Stripe's message, which can quote customer data
  (`test_a_provider_refusal_logs_stripes_code_and_parameter_and_answers_the_reason_only`). For example
  `stripe_error=resource_missing stripe_param=line_items[0][price]` is a price id that Stripe does not know under that
  secret key (a price from another account or sandbox).
- **A customer id from a replaced key.** The customer id is stored before the page is made, so an attempt under one
  Stripe account leaves an id that another account, sandbox or mode does not know. When Stripe refuses it
  (`resource_missing` on `customer`), Checkout forgets that id on the account's rows, creates the customer under the
  current key and makes the page once more with a new idempotency key and expiry, under the account lock
  (`billing.customer_unknown`; `test_a_customer_id_from_a_replaced_secret_key_is_replaced_too`). Before 2026-10-23
  every later attempt sent the old id and failed after the key was corrected. The Portal and top-ups are not retried:
  both need a live subscription, which the new key cannot see either.
- **Page expiry.** Pages expire after `DOME_BILLING_CHECKOUT_PENDING_HOURS` (1 to 23, default 23), the same value the
  reconciler waits before asking about a page. Stripe accepts an `expires_at` from 30 minutes to 24 hours after it
  creates the page, by its own clock; DoMe sends the configured hours in whole seconds (the stored
  `checkout_expires_at` is exactly the value sent), so the value is at least 30 minutes inside both ends even when the
  server's clock is off from Stripe's. Until 2026-10-23 the default was 24 hours, which is past Stripe's limit as soon
  as the server's clock runs ahead of Stripe's by more than the request takes. A replay (above) sends the stored
  expiry again only while it is still 15 minutes inside Stripe's window; otherwise the page creation starts over with
  a new idempotency key and a new expiry (`test_a_stored_expiry_stripe_would_refuse_is_not_replayed`,
  `test_a_server_clock_off_from_stripes_still_gets_a_page`). The fake provider refuses an `expires_at` outside the
  window as Stripe does.
- **Pro Plus only while the assistant is on.** Pro Plus differs from Pro only in AI requests, so, as with top-ups,
  `checkout(plan=pro_plus)` is refused with 403 `PLAN_NOT_AVAILABLE` (detail `{plan, reason: "ai_off"}`) while
  `DOME_AI_ENABLED` is off; Pro is still sold. `billing_status_response.checkout_plans` lists what can be bought
  now, and the Billing page shows only those (`test_pro_plus_is_not_sold_while_the_assistant_is_off`).
- Return URLs: `/app/billing?checkout=return&session_id={CHECKOUT_SESSION_ID}` and `?checkout=cancel`; top-ups
  `?topup=return…` / `?topup=cancel`; the Portal returns to `/app/billing`. The query string is informational:
  the server never reads it, the phone polls `GET /v1/session` and `GET /v1/account/billing`.
- `POST /v1/billing/portal {}` — needs a Stripe customer (404 `BILLING_NO_CUSTOMER` otherwise).
- `POST /v1/billing/topup {}` — allowed only when the plan has `ai_topup_allowed`, the subscription is `active`,
  `trialing`, `cancel_scheduled` or `past_due` inside the grace, **and the assistant is switched on**
  (`DOME_AI_ENABLED`), so nobody buys requests they cannot use (403 `TOPUP_NOT_ALLOWED`). Refused with 409
  `TOPUP_BALANCE_LIMIT` when the balance plus pending pages would pass 1000 requests. Never automatic.
- `GET /v1/account/billing` — everything the Billing page shows (`billing_status_response`); works with billing
  off (`subscription: null`, nothing available). While the account waits on a page that can still be paid, it asks
  Stripe about that page once; a page that was paid is applied at once and `checkout_available` is false. An open
  page does not hide the offer, so a customer who left the page can come back to it.

Card data never touches DoMe; only Stripe-hosted pages. Trials: not configured.

Evidence: integration-tested (fake provider), `test_billing_checkout.py`, `test_billing_topup.py`.

## 6. Webhook intake, deduplication and reconciliation

1. `POST /v1/billing/webhook` refuses any `Origin` header (403; a browser page can never call it), needs no
   session, refuses a body over 64 KiB (413): a declared `Content-Length` is checked first and a chunked body is
   counted while it streams, so nothing past the cap is buffered (`test_a_chunked_body_over_the_cap_is_refused_while_it_streams`); it verifies `Stripe-Signature` over the raw
   bytes with `DOME_STRIPE_WEBHOOK_SECRET` and a 300-second tolerance (400 `BILLING_WEBHOOK_INVALID` otherwise).
   Without the secret it answers 503.
2. A `billing_events` row (`received`) is committed. A second delivery of the same event id is acknowledged as
   `{received: true, duplicate: true, status: "duplicate"}` and does nothing — unless the earlier attempt
   `failed`, in which case the redelivery is processed again (one claimant wins).
3. Processing is **synchronous inside the request** (a deliberate simplification of the earlier worker design):
   - `checkout.session.completed` / `checkout.session.async_payment_succeeded` → fetch the session; subscription
     mode → fetch the subscription and `apply()`; a paid top-up → credit once.
   - `checkout.session.expired` → drop the pending row / mark the top-up purchase expired.
   - `customer.subscription.created|updated|deleted`, `invoice.paid`, `invoice.payment_failed`,
     `invoice.payment_action_required` → fetch the subscription and `apply()`.
   - `charge.dispute.*`, `charge.refunded` → `ignored`, `error = operator_action`, warning `billing.needs_operator`.
   - anything else → `ignored`, `unhandled_type`. An object DoMe cannot attribute to a live account (another
     Stripe account, a dashboard experiment, a deleted account) → `ignored`, `unattributed`.
   Attribution uses DoMe's own metadata (`dome_account_id`) or the customer/subscription ids of rows DoMe wrote.
4. A processing error marks the row `failed`, `attempts + 1`, and answers 500 so Stripe redelivers.
5. The reconciler (hourly; `DOME_BILLING_RECONCILE_INTERVAL_SECONDS`) in one pass: asks Stripe about every
   `checkout_pending` row and pending top-up older than `DOME_BILLING_CHECKOUT_PENDING_HOURS` before touching it. A
   page that was paid while every webhook failed (for example a wrong `DOME_STRIPE_WEBHOOK_SECRET`, which answers each
   delivery 400, so no receipt row exists to retry) is applied, or its top-up credited, and logged as
   `billing.webhook_missed` (account id only); a page that is expired, or open past its expiry, is dropped; a page
   still payable, or one Stripe cannot be asked about right now, is kept for the next pass
   (`test_reconciler_recovers_a_paid_checkout_whose_webhooks_never_arrived`,
   `test_reconciler_keeps_a_page_it_cannot_ask_about_and_one_still_payable`,
   `test_reconciler_credits_a_paid_topup_whose_webhooks_never_arrived`). It also ends exhausted grace; retries `failed` rows with fewer
   than 5 attempts and `received` rows older than 10 minutes; retries pending deletions; and re-fetches up to 200
   live subscriptions, logging `billing.entitlement_mismatch` when `accounts.plan` disagreed. It runs in the single
   API process (ADR-0001); a second process would run its own pass, which is safe because every step is
   idempotent.

Logs carry event types, codes, account ids, plan names and counts, and on a failed Stripe call Stripe's error code
and parameter name (`billing.provider_error`, §5); never an email, a body, a signature, a Stripe message or a Stripe
object id (`test_webhook_logs_carry_no_email_or_body` also searches every table). The stripe SDK logs Stripe's
message and every request URL at INFO and the request and response bodies at DEBUG through its own logger, and httpx
logs every URL at INFO; both loggers are held at WARNING whatever `DOME_LOG_LEVEL` says (`logging.SDK_LOGGERS`,
`test_the_stripe_sdks_own_log_never_carries_its_message_or_the_email`).

Evidence: integration-tested (fake provider), `test_billing_webhook.py`.

## 7. Proration policy (proposed, founder decision)

| Change | Effect | Stripe setting |
| --- | --- | --- |
| Monthly → annual, Pro → Pro Plus | Immediately; unused time is credited | Portal, `proration_behavior = create_prorations` |
| Annual → monthly, Pro Plus → Pro | At the end of the paid period (no mid-term refund) | Portal "at period end" |
| Cancel | At period end; access continues until then; no partial refund | Portal cancellation, `cancel_at_period_end` |

Plan and interval changes happen only in the Portal; the app has no in-app switch. Whatever the Portal does, the
next `customer.subscription.updated` is fetched and applied like any other change.

## 8. Grace, cancellation and downgrade

**Payment failure.** Seven days of the paid plan from the first failed payment (§4.3); the Billing page shows the
date.

**Provider outage.** The backend keeps issuing assertions from its own `accounts.plan`; a Stripe outage changes
nothing until a webhook or the reconciler says otherwise. On the PC, a verified paid assertion is honoured for at
most 72 hours after `exp` only when the refresh fails with a network error or 5xx (unit-tested in pc-agent).

**Cancellation.** Pro or Pro Plus stays on until `current_period_end`; then the account returns to Free: 1 PC and
2 phones stay enabled, layouts and routines are kept but cannot run, and re-subscribing is possible at any time.
Nothing is deleted.

**Downgrade selection.** When a change lowers the limits, `accounts.plan_changed_at` is set and for 14 days:

- `GET /v1/account/plan-selection` lists every PC and phone with `selected`, `last_seen`, the plan's limits, `open`
  and `deadline`.
- `PUT /v1/account/plan-selection {pc_ids, controller_ids}` (session + CSRF) checks sizes against the plan and
  ownership (400 `PLAN_SELECTION_INVALID`), enables/disables PCs, marks the chosen phones, records
  `plan_selection_applied`, and pushes snapshots. After the window: 409 `PLAN_SELECTION_CLOSED`; changes are made
  under Devices (`PATCH /v1/pcs/{id}` already enforces the limit).
- Until the owner chooses, the most recently seen devices stay enabled. Revocation and emergency stop work for
  every device regardless of plan.
- Re-upgrading re-enables plan-disabled PCs up to the new limit and closes the window.

Evidence: integration-tested (fake provider),
`test_cancel_at_period_end_then_deleted_downgrades_opens_selection_and_defaults_apply` (3 PCs, 4 phones, Pro →
Free), `test_reupgrade_restores_plan_disabled_pcs_and_clears_window`.

## 9. Refunds and disputes (not implemented; proposed policy, needs founder review)

- Refunds are issued by the founder in the Stripe dashboard. `charge.refunded` is recorded and logged for the
  operator; it does not change the plan. If the subscription is canceled at the same time, the subscription
  events do that.
- Disputes are recorded and logged (`billing.needs_operator`); access is not paused automatically. A `disputed`
  state is not built.
- Refund wording per jurisdiction (for example EU withdrawal rights) needs founder/legal input.

## 10. AI windows and top-ups

Replaces the calendar-month usage period of the first AI build. One AI request is one **charged** assistant turn
(`answered`); failed, declined and invalid turns are never charged. Everyday commands the phone understands by
itself never count. Whether AI is offered at all is a separate server setting (`DOME_AI_ENABLED`, off in
production).

- **Windows start at the first request.** When none is open, a request opens a 5-hour window `[now, now + 5 h)`
  and a weekly window `[now, now + 7 d)`, independently. A new one opens at the next request after the previous
  ended. Nothing resets on a calendar. A request needs room in both.
- **Order.** Taste (Free: 10, never renews) — or window and week (paid plans) — else the top-up balance (paid plans
  only), else refused with `AI_ALLOWANCE_EXHAUSTED {reason: taste|window|week|cost_cap|failures, resets_at}`.
  When the window and the week are both full (on Pro, four full windows of 30 use up the week's 120), the reason
  named is the bucket that clears later, so `resets_at` is when a request really works again, never the window's
  earlier end (fixture cases "window and week both full", "both full, the week ends before the window").
  `charged_from` in the turn response says which paid for it.
- **Backstops per weekly window.** The internal cost cap (`ai_cost_cap_cents_per_week`) and the failed-turn limit
  (`DOME_AI_FAILED_TURNS_PER_PERIOD`, the period now being the weekly window) apply to every turn, including
  top-up turns. The operator's spending ceiling stays per calendar month.
- **Top-ups.** 100 requests for $2.99. The balance never expires and is kept across a downgrade, but is spendable
  only on a plan with `ai_topup_allowed`; on Free it waits until the next paid plan
  (`test_topup_not_spendable_after_downgrade_but_kept`). Credited exactly once per paid Checkout, whether Stripe
  sends `completed`, `async_payment_succeeded` or both (`test_topup_checkout_completed_credits_exactly_once`).
- **Accounting.** Reserve before the provider call, settle after, in one transaction per step: `ai_balances` (one
  row per account: taste used/reserved, top-up remaining/reserved) is locked `FOR UPDATE` by every reservation,
  settlement and credit of that account, so an account's AI accounting is serialised while other accounts are
  unaffected. `usage_windows` (`kind` window/week, start, end, requests used/reserved, cost used/reserved, failed
  turns). `topup_purchases` (Checkout session id unique, requests, amount, currency, `pending` / `credited` /
  `expired` / `failed`). `assistant_turns.source` and `window_start` record where each turn was charged.

Evidence: integration-tested, `cloud-api/tests/test_assistant_metering.py` (window opening, each exhaustion
reason, ordering, concurrency, the shared fixture `shared/protocol/fixtures/ai-window-cases.json`),
`test_assistant_routes.py`, `test_billing_topup.py`.

## 11. Account deletion (function only)

`BillingService.cancel_for_account_deletion(db, account_id)` cancels the Stripe subscription at once and applies
`canceled` → Free. If Stripe fails, a `pending_deletions` row records the attempt (`attempts`, `last_error`) and
the result is `pending`, never a false "done"; the reconciler retries until it succeeds. Webhooks about a deleted
account change nothing. Evidence: integration-tested (fake provider),
`test_account_deletion_cancels_or_records_pending_then_retries`.

**Not implemented:** the `DELETE /v1/account` route with OIDC re-authentication, session/credential revocation and
the 30-day hard delete. Until it exists, deletion is a support request.

## 12. Verification

| Check | Method | Status |
| --- | --- | --- |
| Checkout → active, Pro Plus, renewal, failed payment → grace → unpaid, recovery, cancel at period end → Free, re-upgrade | `cloud-api/tests/test_billing_checkout.py`, `test_billing_lifecycle.py` with the fake provider and an injected clock | integration-tested (fake provider) |
| Duplicate, out-of-order and failed webhooks; serialised fetch-and-apply; a paid page never sold twice; missed webhooks recovered by the reconciler; signature, size (declared and chunked), Origin; no email or body in logs or tables | `test_billing_webhook.py` | integration-tested (fake provider) |
| Top-ups: who may buy, credit once, balance cap, spending order, kept on Free | `test_billing_topup.py` | integration-tested (fake provider) |
| AI windows and the shared fixture | `test_assistant_metering.py`, shared/python and shared/ts contract tests | integration-tested / unit-tested |
| Start-up refusals (partial set, live key outside production, renamed variables, fake outside dev/test) | `test_billing_settings.py` | unit-tested |
| Stripe adapter: SDK-made signatures, tampered or stale bodies, id and period normalisation, Checkout parameters, errors with Stripe's code and parameter but never its message or the key | `test_billing_stripe_adapter.py` | unit-tested |
| Every request the adapter sends, with the billing service's own arguments (recorded through a whole customer life: two Checkout pages, payment, webhooks, Portal, top-up, account deletion), plus the Checkout, status and Portal routes with the adapter in place of the fake; the fields read back (session and customer ids, status words, the expiry echoed, the period on the subscription item, an invoice's subscription under `parent.subscription_details`); webhooks carrying stripe-mock's own objects; a deliberately wrong parameter is refused | `test_stripe_provider_mock.py` against stripe-mock v0.206.0 (`-strict-version-check`), run in CI (`.github/workflows/ci.yml`, job "Python"); skipped without `DOME_TEST_STRIPE_MOCK_URL` | stripe-mock-validated; nothing refused (2026-10-23) |
| Page expiry inside Stripe's window with the server's clock 20 minutes ahead or behind; a stored expiry Stripe would refuse is never replayed | `test_billing_checkout.py` (fake provider, which enforces Stripe's window) | integration-tested (fake provider) |
| A customer id Stripe does not know (stored under a key since replaced) is replaced once on Checkout; the Stripe SDK's and httpx's own loggers never carry Stripe's message, a body or the email | `test_billing_checkout.py` (fake provider, which refuses an unknown customer as Stripe does), `test_billing_stripe_adapter.py` | integration-tested (fake provider) / unit-tested |
| Migration 0004 down and up | `test_billing_migration.py`, `alembic check` | integration-tested |
| The same lifecycle against Stripe test mode (card 4242…, test clocks for renewal and failed payment, Stripe CLI or dashboard redeliveries) | founder on staging, `docs/LAUNCH_SETUP.md` "Stripe (payments)" | **not yet verified** |
| Live charge | founder-enabled live keys, separate checklist | not applicable yet |

## 13. Open founder decisions

- **Tax:** prices are tax-inclusive and Stripe Tax is off (decided 2026-10-20). Turning Stripe Tax on later means
  tax comes out of the charge; revisit before enabling.
- **Portal features:** which changes the Portal offers (§7 proposes them).
- **Top-up expiry:** built as "never expires"; the founder may choose otherwise.
- **Refund and withdrawal wording** per jurisdiction; Terms update.
- **Pro Plus positioning:** same device limits as Pro; three times Pro's AI requests per 5-hour window (90 vs 30)
  and 2.5 times per week (300 vs 120). Not sold while the assistant is off (§5).
- Business details on invoices; whether a trial is ever offered.

Generated copy does not prove legal compliance.
