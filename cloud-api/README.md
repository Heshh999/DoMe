# cloud-api — DoMe backend and relay

FastAPI service that owns REST (`/v1`), both WebSocket endpoints (`/ws/agent`, `/ws/controller`) and,
when `DOME_STATIC_DIR` is set, the built PWA. PostgreSQL is the only datastore. Everything that crosses
a trust boundary is validated with the shared `dome-protocol` library against the frozen contract in
`shared/protocol/`. Design: `docs/design/cloud-api.md`; decisions: `DECISIONS.md`; contract notes:
`CONTRACT_ISSUES.md`.

Scope implemented (Phase A/B): OIDC login (Authorization Code + PKCE via Authlib), server-side
sessions with HttpOnly cookie, CSRF header + exact Origin checks, account endpoints (security events,
session list/revoke), PC linking (device-code shape, transactional device limit), PC credential →
access token, EdDSA entitlement assertions + JWKS, pairing (PC-generated code, backend sees only the
hash, offline claim re-delivered on connect), controller/grant inventory and revocation, the relay
(hello kid binding, grants_snapshot, subscribe with cached state, command routing with every
rejection as a relay-origin result, in-flight deadline sweeper, outcome_unknown on disconnect,
late-result correction, revoke_controller, pairing_request delivery), command lifecycle rows, plans,
health, security headers/CSP, static PWA serving, structlog JSON with redaction. Plans and Stripe billing
(Phase C, protocol 1.3, migration 0004) are implemented and **integration-tested with an in-memory fake Stripe**;
every request the Stripe adapter sends is checked against stripe-mock v0.206.0 (Stripe's official mock API) in CI;
not yet run against Stripe test mode. See "Billing (Phase C)" below.

Protocol 1.1 (spec §10A, §11A): the signed manual-input stream (`input_batch` routing, `input_ack` /
`input_session` delivery), `grant_update` from the PC owner, pairing with the `pointer` / `keyboard`
capabilities, and support tickets (migration 0002). See "Manual input (protocol 1.1)" and "Support
tickets" below. Evidence tags follow spec §17: everything here is **integration-tested** against a real
PostgreSQL and simulated agents/phones; nothing in this component is Windows-device-tested or
iPhone-tested, and the relay cannot verify that a batch it forwarded was injected on the PC.

AI assistant, step 1 (spec §13, Phase E; `docs/design/assistant.md`): `GET /v1/assistant`, consent, and
`POST /v1/assistant/turn`, which turns the owner's words into at most three AI-eligible steps or a short spoken
butler reply (migration 0003). Off unless configured; see "AI assistant" below.
**Integration-tested** with a scripted fake provider and an HTTP-level stub of the Anthropic API; **not live-tested**
(no key in the build environment) and not released (`DOME_AI_ENABLED` is off in production; the plan allowances of
protocol 1.3 describe the plans, not availability).

Screen view, Phase 1 beta (protocol 1.4; `docs/design/screen-view.md` §4, §7, §10–§13, ADR 0002; migration
`0005_screen_view`): the relay half of the live PC picture. Routing of the `screen.*` commands and the four 1.4 frames,
the start hook (kill switch, plan allowance, owner presence), the screen ledger settled on the relay's clock with
pull-based 60 s allowance tickets, STUN for both devices always and the phone's relay-server credential (Cloudflare
Realtime TURN) only when configured, one outstanding credential per controller revoked on every §13 path and capped per
account and per month, `pc_key_mac` stored at pairing and the `pc_key` returned once approved, the 1.4 stamp gate for
input batches, and `DOME_SCREEN_VIEW_ENABLED` (off by default: every start is refused `SCREEN_VIEW_DISABLED`). See
"Screen view (protocol 1.4)" below. **Integration-tested** against a real PostgreSQL with simulated 1.4 agents and
phones and an in-memory fake TURN provider; the Cloudflare adapter is **unit-tested against a mock transport and has
never been run against the real service**; nothing is device-verified (no picture reaches a phone through this
component alone: the agent and the PWA halves are separate work packages).

## AI assistant (step 1)

Design: `docs/design/assistant.md`; contract: `docs/PROTOCOL.md` §19 (PWA-only bodies, no version bump). Code:
`dome_api/assistant/` and `dome_api/routes/assistant.py`.

| Route | Auth | Body | Answer |
| --- | --- | --- | --- |
| `GET /v1/assistant` | session | | `assistant_status_response` (available or the first reason it is not, consent, allowance, turn deadline) |
| `POST /v1/assistant/consent` | session + CSRF | `assistant_consent_request{version: "2026-10-11", accept: true}` | `assistant_status_response`; 403 `AI_NOT_AVAILABLE` when AI is off or not in the plan |
| `DELETE /v1/assistant/consent` | session + CSRF | | 204 (all phones, next turn; idempotent) |
| `POST /v1/assistant/turn` | session + CSRF | `assistant_turn_request` | `assistant_turn_response`, or the codes of `docs/design/assistant.md` §1.2 |

A turn, in order: configured → one turn in flight per account (`AI_BUSY`, checked first: it does no work, so the
phone's resend after a Cancel does not use up the bucket) → per-account token bucket (`plans.json
ai_turn_rate_limit`, 429 + `Retry-After`) → strict body (16 KiB) → consent (`AI_CONSENT_REQUIRED`) → effective
limits → the request's transaction is committed → reservation (allowance, cost cap, failed-turn limit, operator
ceiling; `AI_TURN_DUPLICATE`, `AI_ALLOWANCE_EXHAUSTED` with `detail{resets_at, reason}`, `AI_SPEND_LIMIT_REACHED`) →
the provider call inside the turn deadline, raced against the client disconnecting → validation of the model's
output against `actions.json` (`ai_eligible` only; a violation becomes a fixed, uncharged question) → settlement →
response → one `assistant.turn` log line. Refusals answer `AI_DECLINED` (422), provider failures `AI_UNAVAILABLE`
(502, or 504 for a timeout) with `detail.reason`. Only valid replies are charged; every turn's real token cost is
metered, and turns whose cost is unknown are charged to the account's own cost cap.

Allowances (protocol 1.3, `rules.ai_allowance`, migration 0004; `docs/BILLING.md` §10): one AI request is one charged
turn. Free has a one-time taste of 10 (`ai_taste_requests`); Pro and Pro Plus have a 5-hour window and a weekly
window (`ai_window` / `ai_week`: 30/120 and 90/300) that each open at the first request while none is open, and a
turn needs room in both; otherwise the account's top-up balance pays (paid plans only, and only while billing is on),
otherwise `AI_ALLOWANCE_EXHAUSTED {reason: taste|window|week|cost_cap|failures, resets_at}`. The cost cap
(`ai_cost_cap_cents_per_week`) and the failed-turn limit are per weekly window; the operator ceiling stays per
calendar month. `assistant_turn_response.charged_from` says which bucket paid. Reservations lock the account's
`ai_balances` row, so one account's accounting is serialised while other accounts are unaffected
(integration-tested with 10 concurrent reservations, `tests/test_assistant_metering.py`).

What reaches Anthropic: the frozen system prompt and action catalogue (one cached block) and one user message with
exactly `context`, `recent` and `request`, copied field by field from the validated request. What is stored: counts,
micro-dollars, outcome codes, the served model id and the provider request id (`assistant_turns`, `usage_windows`,
`ai_balances`, `ai_spend_months`; `usage_periods` is no longer written). Never stored or logged: the words, `recent`, the context, the reply, the choices, the steps.

The Anthropic call (`assistant/anthropic_provider.py`, SDK `anthropic` 1.x): `beta.messages.stream` with
`model=claude-opus-5-5`, `max_tokens=1024`, `output_config={effort: "low", format: json_schema}`,
`fallbacks="default"` + beta `server-side-fallback-2026-07-01`, no `thinking` (always adaptive on this model), no
tools, no sampling parameters, an explicit `base_url`, SDK retries off. The key lives only in
`DOME_AI_ANTHROPIC_API_KEY`; start-up refuses every `ANTHROPIC_*` environment variable while this provider is on, and
the `anthropic`, `httpx2` and `httpcore` loggers are pinned to WARNING.

Monthly reconciliation (spec §13): compare `ai_spend_months` and the ledger's sums for the closed month with the
Anthropic Console's usage and cost report; investigate a difference above 10 %; re-check `assistant/pricing.py`.

First live check (owner, with a key; costs a few cents, never in CI):

```sh
DOME_AI_LIVE_TESTS=1 DOME_AI_ANTHROPIC_API_KEY=sk-ant-... uv run pytest -q -s tests/test_assistant_live.py
```

## Billing (Phase C)

Design and evidence: `docs/BILLING.md`; contract: protocol 1.3 (`shared/protocol/README.md`). Code:
`dome_api/billing/` (`provider.py` interface, `stripe_provider.py` on the official `stripe` SDK 16.x,
`fake_provider.py` for development and tests, `state.py` the state machine, `service.py`), `routes/billing.py`,
and the billing and plan-selection routes in `routes/account.py`.

| Route | Auth | Body | Answer |
| --- | --- | --- | --- |
| `GET /v1/plans` | public | | `plans_response` with `pricing`, `billing_enabled`, `billing_mode` (`off` / `test` / `live`), `ai_enabled` |
| `GET /v1/account/billing` | session | | `billing_status_response` |
| `POST /v1/billing/checkout` | session + CSRF | `billing_checkout_request{plan, interval}` | `billing_checkout_response` (the same open page on a second call, or on a concurrent one); 409 `ALREADY_SUBSCRIBED` (also when the stored page was already paid), 409 `CHECKOUT_PROCESSING`, 403 `PLAN_NOT_AVAILABLE` (Pro Plus while the assistant is off), 502 `BILLING_PROVIDER_ERROR` |
| `POST /v1/billing/portal` | session + CSRF | `{}` | `billing_portal_response`; 404 `BILLING_NO_CUSTOMER` |
| `POST /v1/billing/topup` | session + CSRF | `{}` | `billing_checkout_response`; 403 `TOPUP_NOT_ALLOWED`, 409 `TOPUP_BALANCE_LIMIT` |
| `POST /v1/billing/webhook` | Stripe signature; any `Origin` refused | raw body ≤ 64 KiB | `billing_webhook_response`; 400 `BILLING_WEBHOOK_INVALID`, 413, 500 when processing failed (Stripe redelivers) |
| `GET` / `PUT /v1/account/plan-selection` | session (+ CSRF for PUT) | `plan_selection_request` | `plan_selection_response`; 409 `PLAN_SELECTION_CLOSED`, 400 `PLAN_SELECTION_INVALID` |

The page creations share a per-account budget (`DOME_RATE_BILLING_REQUESTS_PER_HOUR`, 429). Webhook URL to give
Stripe: `{DOME_PUBLIC_ORIGIN}/v1/billing/webhook` (`https://domeremote.com/v1/billing/webhook`,
`https://staging.domeremote.com/v1/billing/webhook`). On staging and production the seven Stripe values come from the
GitHub Environment through the Deploy button (`deploy/README.md` "Billing (Stripe) through the Deploy button";
founder steps in `docs/LAUNCH_SETUP.md` 11A), which applies the same all-or-none and live-key rules before Fly is
touched. `accounts.plan` is written only by `billing/state.py::apply()`,
from an object fetched from Stripe (never from an event payload or a return URL); after the commit every online PC
of the account gets a new `grants_snapshot`. The entitlement assertion's `plan` claim and
`agent_entitlement_response.plan` are `pro` for Pro Plus too (the installed agents know only free and pro). An
hourly reconciler runs in the process (`DOME_BILLING_RECONCILE_INTERVAL_SECONDS`); `dome-api --billing-reconcile`
runs one pass and exits.

Without the seven Stripe values (production today) billing is off: `billing_enabled: false`, `billing_mode: "off"`,
checkout/portal/top-up/webhook answer 503 `BILLING_NOT_AVAILABLE`, `GET /v1/account/billing` and the plan selection
still work, and everyone stays on Free. A partial set refuses start-up naming the missing variables.

Logs: one line per operation (`billing.checkout`, `billing.webhook`, `billing.transition`, `billing.reconcile`, …)
with codes, account ids, plan names and counts; never an email, a body, a signature or a Stripe key. A failed Stripe
call logs `billing.provider_error` with the operation, reason, HTTP status, Stripe request id, `stripe_error` (Stripe's
error code, e.g. `resource_missing`) and `stripe_param` (the parameter it names, e.g. `line_items[0][price]`); never
Stripe's message (the stripe SDK's and httpx's own loggers are held at WARNING, `logging.SDK_LOGGERS`). A customer id
Stripe no longer knows (stored under a secret key since replaced) is replaced once on Checkout
(`billing.customer_unknown`, DECISIONS 108). Evidence: **integration-tested (fake provider)** in `tests/test_billing_*.py`; the Stripe adapter is
**unit-tested** against the SDK's signature helper, object models and Stripe-shaped error answers, and
**stripe-mock-validated**: `tests/test_stripe_provider_mock.py` replays every provider call the billing service makes
(recorded through a whole customer life) against the adapter pointed at stripe-mock v0.206.0, runs the Checkout,
status and Portal routes with the adapter in place of the fake, and checks the response fields the adapter reads
(CI starts stripe-mock with `-strict-version-check`; locally the test is skipped unless `DOME_TEST_STRIPE_MOCK_URL`
is set, see the module docstring). stripe-mock knows no account, so a price's existence and type, the account name
Checkout needs, the `expires_at` window and the Portal configuration are **not sandbox-provider-tested** yet.

## Screen view (protocol 1.4)

Design: `docs/design/screen-view.md` (§4 security model, §7 phone↔PC flow, §10 plans, §11 "Peers", §12 relay, §13
revocation); contract: `shared/protocol/version.json → rules.screen_sessions`, `rules.screen_metering`,
`rules.screen_content`, `plans.json → screen_view_*`. Code: `dome_api/screen/` (`metering.py` the ledger, `turn.py` the
provider interface with the Cloudflare adapter, `FakeTurn` and `TurnService`, `service.py` the orchestration and the
sweeper), the screen steps of `relay/router.py` (`route_command` step 8a, `route_screen_ice_request`, the stamp gate in
`route_input_batch`), the three agent frames in `relay/agent_ws.py`, presence and the screen hooks in `relay/manager.py`,
`routes/pairing.py` (`pc_key`) and `routes/health.py`. The relay never sees media: SDP travels inside the signed
`screen.session_start` command (phone) and `screen_answer` (PC), is forwarded verbatim and never logged.

| From | Frame | What the relay does |
| --- | --- | --- |
| phone | `screen_ice_request{pc_id}` | 1.4 socket only; plan rate limit per controller and per account (`screen_ice_rate_limit`); a grant with the `screen` capability. Answers `screen_ice_servers{ice_servers, expires_at, allowance, turn}`: the STUN urls always, plus one TURN entry minted by the provider when configured (`turn: available`); otherwise `turn: unavailable` (no keys, provider failure, breaker open) or `disabled` (a cap or the monthly guard). Refusals are `error{code, ref_pc_id}` plus a `screen_ice_rejected` event. |
| phone | signed `command` `screen.session_start` | Step 8a of `route_command`: the PC must announce 1.4 (`PROTOCOL_INCOMPATIBLE` for every `screen` command otherwise), the switch (`SCREEN_VIEW_DISABLED`), `screen_start_rate_limit`, then `metering.reserve_start` in the command's transaction (`SCREEN_ALLOWANCE_EXHAUSTED` with `resets_at` and a `screen_allowance_exhausted` event; the PC never sees the command). The forwarded command carries `relay.screen{granted_until, unlimited, stun_urls, relayed_max_kbps?}` (the last only in Data saver). The PC's `result` binds its `screen_session_id` to the reservation, or settles it (`connection_failed` / `start_failed`, a `screen_consent_declined` event for `SCREEN_CONSENT_*`) and revokes the TURN credential. The PC owns the single session per PC (`SCREEN_SESSION_OWNED` comes back as its result and is settled like any refused start; a `takeover` start's `ended{takeover}` settles the first phone's reservation and revokes its credential while the new one keeps its own); the relay remembers each forwarded start for 105 s. `screen.session_stop` needs only the 1.4 agent: it passes the switch, so a phone can always stop. |
| PC | `screen_answer{envelope, ref_controller_id}` (the PC-signed `screen_answer_payload`) | Delivered verbatim, `ref_controller_id` stripped, to the controller that holds the screen grant and issued the start (its socket first, any other 1.4 socket of that controller otherwise); dropped with a log line when the grant is gone. |
| PC | `screen_session{event}` | `started` / `paused` / `resumed` / `ended` mark the ledger (`started` opens the charge, pauses the relay saw are discounted up to 120 s, `ended` settles); the `screen_session_started` / `screen_session_ended` events carry ids, the path or the reason and the relay-charged seconds, and the one log line per end a duration bucket only; delivered to the owner and the 1.4 subscribers. |
| PC | `screen_usage{screen_session_id}` | Budget 2 per minute per session. Answers `screen_allowance`: the next ticket (`granted_until` at most 60 s ahead, `remaining_seconds` / `resets_at` on Free, `unlimited: true` on paid plans, still in chunks), or `final: true` with the honest reason: `controller_offline` (no bound socket of the owner for 60 s, or logout / session revocation / controller revocation at once), `plan_disabled`, exhausted (`resets_at`), an unknown session (no reason). The agent's own figures in the frame are read by nobody (rules.screen_metering (3)). |
| PC | `pairing_decision{…, pc_key_mac}` | Stored on the pairing row; `pairing_status_response.pc_key{public_jwk, mac}` once approved, and an approved row stays readable for 300 s (`PAIRING_APPROVED_READABLE_SECONDS`, a tested invariant) so the phone can pin the key. |
| PC | `agent_error` with a `SCREEN_*` code | Delivered to the controller's 1.4 sockets only. |
| phone | `input_batch` stamped 1.4 | Stamp gate: a batch whose `protocol_version` the PC did not announce is refused `PROTOCOL_INCOMPATIBLE{peer: agent, required}` by the relay after the signature check and before forwarding (design §8.1), because a shipped 1.2 agent would answer `MALFORMED_MESSAGE`. |

**Ledger** (`metering.py`, tables `screen_view_windows`, `screen_reservations`, `screen_accounts`): one rolling window
per account (`screen_view_window_hours`, 5 h) with `used_seconds` and `reserved_seconds`; one reservation per start,
`reserved` → `live` → `settled` / `swept`. Time is settled on the relay's clock in 60 s chunks rounded up to 10 s and
capped at what was reserved; a start that never reached `started`, lasted ≤ 20 s and is one of at most three such
attempts in the window costs nothing; a window that ends mid-session rolls the segment into the next one; a session
whose ticket expired without a renewal (no `screen_usage`, PC gone) is swept 10 s after `granted_until` and charged in
full; paid plans go through the same chunks with `unlimited: true` so a revocation still lands within one ticket. The
account row is locked for every write, so parallel starts, renewals and the sweeper agree
(`tests/test_screen_metering.py` runs them from separate sessions). `DOME_SCREEN_CHARGE_FROM=started` moves the start of
the charge from the forwarded command to the PC's `started` event (default `forwarded`, rules.screen_metering).

**TURN** (`turn.py`, tables `turn_credentials`, `turn_usage_days`): the phone alone gets a relay-server credential
(the PC is on the owner's network; spec §4). `CloudflareTurn` mints short-lived credentials from the Cloudflare
Realtime TURN API with `DOME_TURN_KEY_ID` / `DOME_TURN_API_TOKEN` (httpx, 3 s timeout), revokes them, and with
`DOME_TURN_ANALYTICS_TOKEN` reads the account's daily egress through the GraphQL analytics API once an hour
(`reconcile`). One outstanding credential per controller (`controller_id` is the primary key): a new mint revokes the
previous one; TTL = remaining allowance + 60 s capped at 3600 s on Free, `DOME_SCREEN_TURN_TTL_UNLIMITED_SECONDS` on
paid plans. Revoked at the provider and marked with the reason on every §13 path: `start_refused`, `session_ended`,
`controller_offline`, `grant_narrowed` (the `screen` capability left a `grant_update`), `plan_change` (the whole
account), `cap_reached`, `swept` (ticket expired), `no_start` (minted but no start within the lifetime), `replaced`,
plus logout / session revocation / controller revocation through `ConnectionManager` (`controller_gone`). Caps use the
larger of the reconciled figure and a provisional bound (outstanding + charged seconds, and the TTL of session-less
credentials, at 0.5 GB/h): `DOME_SCREEN_TURN_FREE_DAILY_GB` per Free account and day,
`DOME_SCREEN_TURN_MINTS_PER_ACCOUNT_MONTH` per account; the monthly guard gives relayed Free sessions Data saver
(`relayed_max_kbps: 500`) past `DOME_SCREEN_TURN_SOFT_CAP_GB` and STUN only past `DOME_SCREEN_TURN_HARD_CAP_GB`
(`turn_cap_reached{cap: soft | hard | daily | monthly_mints}` events; `soft` is written with each start Data saver
applies to). Three provider failures open a 300 s breaker (STUN only, `turn: unavailable`). Without
the two values the provider is `none`: Screen view works only where a direct connection exists, and the answer says
so (`turn: unavailable`, `/healthz turn: unconfigured`). `FakeTurn` (`DOME_TURN_PROVIDER=fake`, development/test) records
mints and revocations and serves scripted usage rows. A credential never appears in a log line (`credential`,
`username`, `ice_servers` are redacted keys; addresses in free text are masked).

**Settings** (`dome_api/settings.py`, refused at start-up with the variable named): the two TURN values both or
neither, the analytics token only with them, `fake` outside development/test, `DOME_SCREEN_FREE_SECONDS_OVERRIDE` in
production, a soft cap not below the hard cap, a `turn:` url in `DOME_SCREEN_STUN_URLS`. `GET /v1/plans` is unchanged
(`screen_view_enabled` would need a contract change, CONTRACT_ISSUES #19); `/healthz` carries `screen_view` (`on` /
`off`), `turn` (`ok` / `unconfigured` / `error` while the breaker is open), `turn_month_gb` (the figure the monthly
guard uses), `turn_month_basis` (`reconciled` while the latest reconciliation pass is under 3 h old, else
`provisional`), `turn_month_gb_provisional` and `turn_reconciled_at`, cached for a minute. The sweeper and the hourly reconciliation run in the process
(`DOME_SCREEN_TURN_RECONCILE_INTERVAL_SECONDS`); there is no one-pass CLI for them yet.

Evidence: **integration-tested** in `tests/test_screen_routing.py` (every row of the table, the lying agent, the
budget, one session per PC with a refused second phone and a takeover, the start rate limit, a stop past the switch, the sweeper, the no-start revocation, logout / session revocation / controller revocation / 60 s absence /
grant narrowing ending a session within one ticket, the stamp gate, `pc_key_mac`, no SDP or credential in the logs),
`tests/test_screen_metering.py` (every ledger rule including concurrency and the window roll-over),
`tests/test_screen_turn.py` (`TurnService` with `FakeTurn`; the Cloudflare adapter against an `httpx.MockTransport`,
including the 204 revoke and the GraphQL usage shape), `tests/test_screen_settings.py` (including the address masking and the redacted keys), `tests/test_screen_migration.py`
(0005 down and up, `alembic check`). **Not verified against the real Cloudflare service** (the request and response
shapes follow its documentation and may need a fix on first contact: KNOWN_ISSUES), **not device-verified**.

## Manual input (protocol 1.1)

Rules: `shared/protocol/version.json → rules.input_sessions`, `rules.grant_update`. The relay is the
*verify-and-forward* half; the agent owns sessions, sequence, age budget and injection.

**Controller → relay `input_batch`** (`relay/router.route_input_batch`, integration-tested):

1. envelope `kid` must equal the socket's kid, else `UNKNOWN_KEY` and close 4003 (as for commands) —
   the only input rejection that closes the socket;
2. the socket must have announced protocol 1.1 in `hello` (`PROTOCOL_INCOMPATIBLE` otherwise) and be
   bound to a paired, non-revoked controller (`GRANT_MISSING` / `CONTROLLER_REVOKED`);
3. `verify_and_parse_input_batch` with the controller's key record: signature over the exact bytes,
   `input_batch_payload` schema (≤ 64 events, bounded motion/text), controller/account binding, the 5 s
   window (`INPUT_STALE` when outside it — the shared library's `COMMAND_EXPIRED` is translated, DECISIONS
   #30), over-long windows are `MALFORMED_MESSAGE`;
4. `payload.target_pc_id == frame.pc_id` (`TARGET_PC_MISMATCH`); a `seq` this socket already forwarded for
   the same session is `INPUT_SEQUENCE_INVALID` (relay-side replay guard; the agent's check stays
   authoritative);
5. controller enabled under the plan (`CONTROLLER_PLAN_DISABLED`), PC on the account (`ACCOUNT_MISMATCH`)
   and enabled (`PC_PLAN_DISABLED`);
6. a live grant whose capabilities cover `VerifiedInputBatch.required_capabilities` — `pointer_*` need
   `pointer`, `text`/`key`/`shortcut` need `keyboard`; a grant with neither refuses even an empty
   keepalive (`INPUT_NOT_PERMITTED`, `detail.missing` lists what is absent; a mixed batch is refused whole);
7. per-controller token bucket from `plans.json → input_rate_limit` (40 batches/s, burst 80; identical for
   every plan) → `RATE_LIMITED`;
8. agent online (`PC_OFFLINE`), past its first snapshot (`PC_RECONNECTING`) and announcing 1.1
   (`PROTOCOL_INCOMPATIBLE`: the PC needs an agent update);
9. forward `{type: input_batch, envelope, relay{received_at, connection_id}}` with the envelope **verbatim**.

The rows behind steps 2, 5 and 6 (the controller's key and revocation, the plan and its enabled set, the PC, the
live grant) are read at most once per `DOME_RELAY_INPUT_AUTH_TTL_SECONDS` (1 s) per socket and PC and kept on the
socket; the signature, binding, window and sequence are checked on every batch (DECISIONS #110). Every change made
through this process drops the copy at once (pairing, `grant_update`, revocations, `PATCH /v1/pcs`, unlink and
re-link, plan changes and the downgrade selection, another phone of the account binding a socket); a row edited
behind the relay's back takes effect within the TTL. A steady 40 batches/s stream costs one read a second instead
of five statements per batch (integration-tested). `input_batch` frames are schema-checked against their own branch
of `controller_to_relay` first (DECISIONS #111).

No `commands` row, no result, no security event and no log line per accepted batch; the content of
`text` events is never logged (rejections log the error code and ids only). Rejections are `error` frames
with `ref_pc_id` (no command id exists) and at most one `input_rejected` security event per minute per
controller, further bounded by the per-socket event cap. Before any database work, `input_batch` frames are
charged to their own per-socket bucket (`version.json → limits.input_batches_per_second`, burst = plan
burst) instead of the command frame bucket. Refusals are answered with `RATE_LIMITED` at most once per second
(also when the per-controller budget of step 7 refuses, e.g. for a second socket of the same phone: no frame and
no log line per refused batch). Ordinary overshoot while dragging keeps the session; a sustained flood — refusals
above twice the input rate for about five seconds (a leaky refusal budget of 80/s with 400 capacity), which no
touchpad reaches — closes the socket with 4000 and exactly one `controller_throttled` security event
(`detail.reason: input_flood`), as the command path does (integration-tested: 300 batches/s closes after
~2 s; 100 batches/s for 3 s and a 400-frame wire-speed burst keep the socket).

Commands now check the grant with `ActionSpec.satisfied_by`, so `input.session_start` /
`input.session_stop` run on a keyboard-only grant as well as a pointer-only one (integration-tested).

**Agent → relay** (`relay/agent_ws`, integration-tested):

- `input_ack` → every protocol-1.1 socket of the controller that owns the session. The owner is what the
  agent announced in `input_session` (the agent issues session ids); an ack for a session the agent never
  announced on this connection is dropped, never guessed.
- `input_session` → the owner's sockets **and** the PC's subscribers (other phones see ownership change);
  every socket gets each frame once, also an owner socket that is subscribed to the PC (integration-tested).
  `ended` forgets the owner mapping. The named controller must belong to the PC's account, and to *become* an
  owner it must be live (not revoked) and hold a live grant on this PC (`relay_frame_rejected`, reason
  `controller_not_in_account` / `no_live_grant`, otherwise). The owner map is bounded: the latest session per
  controller, at most 4 entries per agent connection (unit-tested).
- Agent-attributed `relay_frame_rejected` rows (`input_session`, `grant_update`, `confirmation_required`) are capped
  per PC at `DOME_RELAY_SECURITY_EVENTS_PER_CONNECTION_PER_MINUTE`; the first suppressed one becomes a single
  `relay_events_throttled` row (integration-tested).
- `grant_update{controller_id, kid, capabilities}` → the controller must belong to the PC's account with
  that kid and hold a live grant on this PC; the grant row's capabilities are replaced with exactly the
  given list (widen or narrow), a `grant_updated` security event records `added` / `removed`, a fresh
  `grants_snapshot` is pushed to the PC and subscribers get a `pc_status` nudge. `GET /v1/pcs/{id}/grants`
  shows the new list. `grant_update` cannot create a grant or touch another PC's grant.
- `pc_state.foreground_app`, `pc_state.input_session`, `pc_state.input_restricted` pass through unchanged to 1.1
  subscribers
  with the `state` frame (schema-validated on receipt).
- 1.0 peers never receive 1.1 frames: `input_ack` / `input_session` skip sockets that announced only 1.0, and a
  1.0 subscriber's copy of a `state` frame (live and the cached one sent on subscribe) has the three 1.1-only
  `pc_state` keys removed, because 1.0 peers reject unknown fields (integration-tested).
- `PC_RECONNECTING`: a batch that arrives after the agent's `hello` but before its first `grants_snapshot` was sent
  is refused with `PC_RECONNECTING` and forwarded once the snapshot is out (integration-tested by holding the
  snapshot build).

**Pairing**: `requested_capabilities` may include `pointer` and `keyboard`; granted = PC's list ∩ requested as
before. Existing grants never gain them implicitly (`rules.grant_update`).

**Not verifiable here** (not yet verified): that a forwarded batch is injected, the real relay latency budget
on the public path, and the phone/agent halves — see `pc-agent/` and `mobile-app/`.

## Support tickets

Spec §11A, smallest practical flow (`routes/support.py`, migration `0002_support_tickets`,
integration-tested):

| Route | Behaviour |
| --- | --- |
| `POST /v1/support/tickets` | session + CSRF + exact Origin; body `support_ticket_request` (category, message ≤ 2000, optional `error_code`, `diagnostics` ≤ 32 KiB text, `app_version`); 10 per hour per account (`DOME_RATE_SUPPORT_TICKETS_PER_HOUR`, `429 RATE_LIMITED`); `201 support_ticket_response` with `reference` `DM-XXXXXXXX` (Crockford base32) and `status: received`. |
| `GET /v1/support/tickets` | the account's own tickets, newest first, ≤ 50 (`support_tickets_response`). |
| `GET /v1/support/tickets/{id}` | own ticket or 404 (another account's id is indistinguishable from a missing one). |

Before storage the diagnostics text goes through a dedicated diagnostics redactor (`redact_diagnostics`,
DECISIONS #33, #40), the server's last check before a durable row: the log key rules (`token`, `code_hash`,
`title`, `access_token`, `email`, …) plus the keys that carry what spec §11A forbids — `text`, `events`,
`composer`, `typed`, `keys`, `url`/`href`/`link`, `query`/`search`, `pairing`/`pairing_code`, `clipboard`,
`video_id` and anything ending in `_url`/`_href`/`_text`/`_query` — then every remaining string: absolute URLs
reduced to scheme + host (userinfo dropped), bare `host/path` to the host, token-shaped substrings (JWT-like
triples, `Bearer …`, Stripe-style keys, base64url/hex runs ≥ 32 characters) and pairing codes (20 Crockford
symbols, contiguous or grouped 4×5 / 5×4 with one separator) masked; JSON embedded as a string gets the key
rules too. Non-JSON text gets the string pass only. The message gets the token and pairing-code pass (not the
URL reduction: it is the customer's own words). Integration-tested with a typed-text input event, a YouTube
watch URL and pairing codes that never reach `support_tickets.diagnostics_redacted`; over-redaction (e.g. a
shouted run of five four-letter upper-case words) is accepted. `response_expectation` is present only when
`DOME_SUPPORT_RESPONSE_EXPECTATION` is configured; there is no default promise. The hourly budget is reserved
before the request body is read (so concurrent submissions cannot overshoot it: 40 concurrent posts store exactly
10, integration-tested) and given back if nothing is stored; a reference collision is retried inside a SAVEPOINT. No support route can
execute, queue or forward anything to a PC, and operators have no route yet (`KNOWN_ISSUES.md` #6). A
failed write is a 5xx, never a reference: the client must not claim receipt without one.

## Setup

Prerequisites: Python 3.12+, `uv`, PostgreSQL 16, and `tools/dev-idp` for local sign-in.

```sh
cd cloud-api
uv venv --python 3.12
uv sync --extra dev                        # standalone project; dome-protocol is a path dependency (../shared/python)

# a local PostgreSQL (from the repository root): make db-start && make db-create
# development identity provider (separate terminal, from the repository root): make dev-idp

cp ../.env.example ../.env                 # edit DOME_* values; the API reads them from the process environment
set -a; . ../.env; set +a
./scripts/gen-entitlement-key.sh           # writes $DOME_ENTITLEMENT_SIGNING_KEY_PEM_PATH (0600)
uv run dome-api                            # alembic upgrade head, then serve on DOME_API_BIND
uv run dome-api --reload                   # development auto-reload
uv run dome-api --skip-migrations          # serve only (start-up still verifies the schema is at head)
```

Sign in at `http://localhost:5173/v1/auth/login` (through the Vite proxy) or directly on
`DOME_PUBLIC_ORIGIN`. With `tools/dev-idp` the sign-in page offers `alice@example.test` /
`bob@example.test`; there is no login path that bypasses OIDC.

## Environment variables

All variables are prefixed `DOME_` and validated once at start-up (`dome_api/settings.py`); a
misconfiguration stops the process with a plain error.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DOME_ENV` | `development` | `development` / `staging` / `production` / `test`. Production requires https origins and a real session secret and enables HSTS. |
| `DOME_PUBLIC_ORIGIN` | — (required) | Exact origin the PWA is served from (production `https://domeremote.com`, staging `https://staging.domeremote.com`; `deploy/README.md` "Domain"). Used for Origin checks, cookies, QR/link URLs and as the OIDC redirect base. Outside `production` every response also carries `X-Robots-Tag: noindex, nofollow`; in `production`, so does every response served under another `Host` (the hosting provider's own address). |
| `DOME_EXTRA_ORIGINS` | `` | Comma-separated additional exact origins (e.g. the Vite dev server). |
| `DOME_API_BIND` | `127.0.0.1:8000` | Listen address. |
| `DOME_TRUSTED_PROXIES` | `` (development/test) | Comma-separated IP addresses or CIDR networks of the reverse proxy whose `X-Forwarded-For` / `X-Forwarded-Proto` are honoured, or `none` when clients reach the process directly. **Required in `staging` and `production`**; `*` is refused. See "Client addresses behind a proxy". |
| `DOME_CLIENT_IP_HEADER` | `` | `fly-client-ip` or empty. When set, a connection from a trusted proxy takes its client address from that single header (Fly.io sets `Fly-Client-IP` on every request); requires `DOME_TRUSTED_PROXIES`. |
| `DOME_DATABASE_URL` | — (required) | `postgresql+psycopg://user@host:port/db` (unix socket: `postgresql+psycopg://dome@/dome_dev?host=/tmp&port=54329`). |
| `DOME_SESSION_SECRET` | — (required, ≥16 chars) | HMAC key for session-cookie ids and IP hashes. Rotating it signs everyone out. |
| `DOME_SESSION_IDLE_DAYS` / `DOME_SESSION_ABSOLUTE_DAYS` | `30` / `90` | Sliding idle expiry and absolute cap of web sessions. |
| `DOME_OIDC_ISSUER` | — (required) | OIDC issuer URL (discovery at `/.well-known/openid-configuration`). Auth0 (`https://<tenant>.<region>.auth0.com`, with or without the trailing slash), Keycloak or `tools/dev-idp`. Stored without a trailing slash; the ID token's `iss` is compared with the discovery document's `issuer` exactly as published (Auth0 keeps the slash). |
| `DOME_OIDC_CLIENT_ID` / `DOME_OIDC_CLIENT_SECRET` | — (required) | Relying-party credentials. The token endpoint is called with `client_secret_post` (Auth0: Credentials > Authentication Method "Client Secret (Post)"). |
| `DOME_OIDC_REDIRECT_PATH` | `/v1/auth/callback` | Registered redirect path (appended to `DOME_PUBLIC_ORIGIN`). |
| `DOME_OIDC_SCOPES` | `openid email profile` | Requested scopes. |
| `DOME_ENTITLEMENT_SIGNING_KEY_PEM_PATH` | — (required) | Ed25519 private key (PKCS8 PEM) that signs entitlement assertions; public key served at `/.well-known/dome-jwks.json`. |
| `DOME_RELAY_MAX_CONNECTIONS` | `5000` | Global socket cap; upgrades above it are refused with HTTP 503. |
| `DOME_RELAY_MAX_FRAME_BYTES` | `65536` | Frames above this close the socket with 1009. |
| `DOME_RELAY_PER_PC_QUEUE_DEPTH` | `16` | In-flight commands per PC (`QUEUE_FULL` beyond). |
| `DOME_RELAY_SWEEP_INTERVAL_SECONDS` | `5` | In-flight deadline sweeper period. |
| `DOME_RELAY_HELLO_TIMEOUT_SECONDS` | `10` | Time a socket has to send `hello`. |
| `DOME_RELAY_CONTROLLER_FRAMES_PER_MINUTE` / `DOME_RELAY_CONTROLLER_FRAME_BURST` | `600` / `120` | Inbound frames one controller socket may send (token bucket, checked before any database work). Refused frames are answered `RATE_LIMITED` from memory; after `burst` refusals the socket is closed (4000) with one `controller_throttled` security event. |
| `DOME_RELAY_SECURITY_EVENTS_PER_CONNECTION_PER_MINUTE` | `20` | Security-event rows one controller socket may write per minute (`command_rejected`, `subscribe_refused`, `input_rejected`), and `relay_frame_rejected` rows one PC may cause per minute; the first suppressed one becomes a single `relay_events_throttled` row. |
| `DOME_RELAY_INPUT_AUTH_TTL_SECONDS` | `1` | How long a controller socket routes `input_batch` frames on its cached copy of the controller, plan, PC and grant rows before reading them again (0–5; 0 reads them for every batch). Changes made through the relay apply at once regardless; this bounds only changes made behind its back (DECISIONS #110). |
| `DOME_RELAY_URL` / `DOME_API_URL` | derived from the public origin | URLs handed to the agent at link time (`wss://…/ws/agent`, `https://…`). |
| `DOME_PC_ACCESS_TOKEN_SECONDS` | `3600` | Lifetime of PC access tokens. |
| `DOME_RATE_LINK_START_PER_HOUR` | `10` | `POST /v1/agent-link/start` per client IP. |
| `DOME_RATE_PAIRING_CLAIM_PER_ACCOUNT` / `DOME_RATE_PAIRING_CLAIM_PER_IP` | `5` / `5` | Pairing claims per 15 minutes (each failure is a `pairing_failed` security event). |
| `DOME_RATE_LOGIN_PER_MINUTE` / `DOME_RATE_AGENT_TOKEN_PER_MINUTE` | `60` / `30` | Login starts and credential→token exchanges per client IP. |
| `DOME_RATE_LINK_CODE_FAILURES_PER_ACCOUNT` / `DOME_RATE_LINK_CODE_FAILURES_PER_IP` | `10` / `10` | Failed `user_code` lookups (unknown, expired or decided codes on `GET/approve/deny /v1/agent-link/{user_code}`) per 15 minutes before `429` (RFC 8628 §5.1); each counted failure is a `link_code_lookup_failed` security event. |
| `DOME_RATE_SUPPORT_TICKETS_PER_HOUR` | `10` | Support tickets one account may create per hour. |
| `DOME_SUPPORT_RESPONSE_EXPECTATION` | `` | Customer-facing response expectation (≤ 200 chars) returned with tickets **only** when set by the founder (spec §11A). Unset = the field is absent. |
| `DOME_STATIC_DIR` | unset | Built PWA directory to serve at `/` (SPA fallback to `index.html`). |
| `DOME_LOG_LEVEL` | `INFO` | structlog level. JSON output except in `development`. |
| `DOME_BUILD_COMMIT` | `` | Set by the image (`deploy/Dockerfile` build argument): the git commit, 7–40 lower-case hex digits. Reported by `/healthz` as `commit`. |
| `DOME_STRIPE_SECRET_KEY` | `` | Stripe secret (or restricted) key. Outside `production` only `sk_test_` / `rk_test_` is accepted; a live key there refuses start-up. Never logged. |
| `DOME_STRIPE_WEBHOOK_SECRET` | `` | The webhook endpoint's signing secret (`whsec_…`). |
| `DOME_STRIPE_PRICE_PRO_MONTHLY` / `_PRO_ANNUAL` / `_PRO_PLUS_MONTHLY` / `_PRO_PLUS_ANNUAL` / `_TOPUP` | `` | The five Stripe price ids (`price_…`, all different). Renamed from `DOME_STRIPE_PRICE_MONTHLY` / `_ANNUAL`: a non-empty old variable refuses start-up, an empty one only warns. Billing is on only when the key, the webhook secret and all five are set; a partial set refuses start-up naming the missing variables. |
| `DOME_BILLING_PROVIDER` | `` | Blank = `stripe` when a key is set; `fake` = the in-memory stand-in (refused in staging and production). |
| `DOME_STRIPE_AUTOMATIC_TAX` | `false` | Stripe Tax on Checkout pages. Keep off: the founder's prices are tax-inclusive. |
| `DOME_BILLING_WEBHOOK_TOLERANCE_SECONDS` | `300` | 60–900; signature timestamp tolerance. |
| `DOME_BILLING_RECONCILE_INTERVAL_SECONDS` | `3600` | Reconciler period. |
| `DOME_BILLING_CHECKOUT_PENDING_HOURS` | `23` | 1–23; how long a hosted Checkout page stays payable, and how long the reconciler waits before it asks Stripe about a page still pending. Stripe accepts 30 minutes to 24 hours after it creates the page, by its own clock; 1–23 keeps the `expires_at` sent at least 30 minutes inside both ends (`docs/BILLING.md` §5). 24, the old default, now refuses start-up. |
| `DOME_RATE_BILLING_REQUESTS_PER_HOUR` | `10` | Checkout/Portal/top-up page creations per account per hour. |
| `DOME_AI_ENABLED` | `false` | Master switch of the AI assistant. When true, start-up requires a provider, a key for `anthropic` and no `ANTHROPIC_*` variable in the environment, a ceiling above 0, a deadline ≥ timeout + 5, and for every plan with an effective allowance a cost cap of at least one reservation. |
| `DOME_AI_PROVIDER` | `` | `anthropic` or `fake` (scripted test provider; refused in staging and production). |
| `DOME_AI_ANTHROPIC_API_KEY` | `` | Server only; never logged, returned or written to disk by DoMe. (Renamed from `DOME_AI_API_KEY`: a non-empty old variable refuses start-up, an empty one only warns.) |
| `DOME_AI_MODEL` | `claude-opus-5-5` | `^claude-[a-z0-9-]{1,48}$`; change only after review (prices, schema support). |
| `DOME_AI_EFFORT` | `low` | `low`, `medium` or `high` (always sent; changing it invalidates the prompt cache). |
| `DOME_AI_MAX_OUTPUT_TOKENS` | `1024` | 512–4096; thinking counts toward it. Above about 1555 the default reservation no longer covers the worst case and start-up refuses until `DOME_AI_TURN_RESERVE_MICRO_USD` is raised. |
| `DOME_AI_TIMEOUT_SECONDS` | `15` | 5–30; with streaming, the longest gap between chunks (connect timeout fixed at 5 s). |
| `DOME_AI_MAX_RETRIES` | `1` | 0–1; DoMe's own retry of unbilled failures only (the SDK's retries are always 0). |
| `DOME_AI_TURN_DEADLINE_SECONDS` | `20` | 10–60, the whole turn including a retry; the phone aborts 5 s after it. |
| `DOME_AI_CACHE_TTL` | `5m` | `5m` or `1h` (the test kit uses `1h`). |
| `DOME_AI_MONTHLY_CEILING_CENTS` | `0` | Operator spending ceiling across all accounts per calendar month (UTC). |
| `DOME_AI_TURN_RESERVE_MICRO_USD` | `250000` | ≥ 10 000; reserved per turn before the call. With the assistant on, start-up refuses a value below the worst case of one turn for `DOME_AI_MODEL` and `DOME_AI_MAX_OUTPUT_TOKENS` (one attempt plus one fallback attempt, `pricing.worst_case_turn_micro_usd`). |
| `DOME_AI_FAILED_TURNS_PER_PERIOD` | `20` | 1–1000; timeouts, refusals and invalid outputs per account and **weekly window** (the name is kept) before `AI_ALLOWANCE_EXHAUSTED` (`failures`). |
| `DOME_AI_DEV_GRANT_INTERPRETATIONS` / `DOME_AI_DEV_GRANT_COST_CAP_CENTS` | `0` / `0` | Development/test allowance: raises the 5-hour and the weekly window and the weekly cost cap to max(plan, grant) (a Free account with a grant gets windows instead of its taste); refused outside `development` and `test`. |
| `DOME_SCREEN_VIEW_ENABLED` | `false` | Kill switch of Screen view. Off: every `screen.session_start` and `screen_ice_request` is refused `SCREEN_VIEW_DISABLED`; the ledger, the TURN tables and the pairing `pc_key` keep working. |
| `DOME_TURN_KEY_ID` / `DOME_TURN_API_TOKEN` | `` | Cloudflare Realtime TURN key id (`[A-Za-z0-9_-]`, ≤ 128) and its API token. Both or neither: a half set refuses start-up naming the missing variable. Never logged. |
| `DOME_TURN_ANALYTICS_TOKEN` | `` | Read-only analytics token for the hourly egress reconciliation; only with the two values above. Without it the caps run on the provisional bound alone. |
| `DOME_TURN_PROVIDER` | `` | Blank = `cloudflare` when the two values are set, else `none` (STUN only); `fake` = the in-memory stand-in (refused in staging and production); `cloudflare` without the values and `none` with them refuse start-up. |
| `DOME_SCREEN_TURN_SOFT_CAP_GB` / `DOME_SCREEN_TURN_HARD_CAP_GB` | `700` / `900` | Monthly guard on relayed traffic (GB, soft < hard): Data saver for relayed Free sessions past the soft cap, STUN only for Free past the hard cap. |
| `DOME_SCREEN_TURN_FREE_DAILY_GB` | `2` | Relayed GB one Free account may cause per UTC day before its credentials are revoked and no new one is minted (`turn_cap_reached`). |
| `DOME_SCREEN_TURN_MINTS_PER_ACCOUNT_MONTH` | `600` | Credentials one account may mint per calendar month. |
| `DOME_SCREEN_TURN_TTL_UNLIMITED_SECONDS` | `14400` | 60–86400; credential lifetime on paid plans (Free: remaining allowance + 60 s, at most 3600). |
| `DOME_SCREEN_TURN_RECONCILE_INTERVAL_SECONDS` | `3600` | Period of the usage reconciliation and the expired-credential sweep. |
| `DOME_SCREEN_STUN_URLS` | `stun:stun.cloudflare.com:3478` | Comma-separated `stun:` / `stuns:` urls handed to both devices (a `turn:` url is refused: TURN comes from the provider). |
| `DOME_SCREEN_FREE_SECONDS_OVERRIDE` | `0` | Development/staging only: a shorter Free allowance per window for testing; 0 = `plans.json` (600); refused in production. |
| `DOME_SCREEN_CHARGE_FROM` | `forwarded` | `forwarded` (the charge starts when the start command leaves the relay) or `started` (at the PC's `started` event). |

### Client addresses behind a proxy

Every per-IP abuse limit and every `ip_hash` stored with a security event uses the client address the
application resolved. That address is taken from `X-Forwarded-For` **only** when the TCP peer is one of
`DOME_TRUSTED_PROXIES`, and then it is the right-most hop that is not itself a trusted proxy (the entry
the proxy appended), never the left-most value a client can forge. `dome-api` starts uvicorn with its
own proxy handling disabled (`proxy_headers=False`, so `FORWARDED_ALLOW_IPS` has no effect) and applies
`dome_api.security.proxy.TrustedProxyMiddleware` itself; running uvicorn any other way must keep
`--proxy-headers` off or the policy is weakened.

Settings by deployment:

- development / tests: leave it empty — the TCP peer is the client and forwarded headers are ignored.
- Fly.io (ADR-0001 D2; set in `deploy/fly.toml`): `DOME_TRUSTED_PROXIES=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`
  and `DOME_CLIENT_IP_HEADER=fly-client-ip`. Fly's proxy reaches the machine over a private IPv4 address
  and `DOME_API_BIND=0.0.0.0:8080` is IPv4 only, so every peer is the proxy; the right-most
  `X-Forwarded-For` hop on Fly is the app's own public address, which is why the single `Fly-Client-IP`
  header the proxy sets is used instead (`deploy/README.md` "Client addresses"). The earlier example
  `fdaa::/16` (Fly's IPv6 private network) cannot match an IPv4-only socket.
- another ingress proxy: the address range it connects *from* as seen by this process, and no
  `DOME_CLIENT_IP_HEADER` unless that proxy overwrites a single-value client header on every request.
- a process reached directly over TLS: `DOME_TRUSTED_PROXIES=none`.

Staging and production refuse to start without an explicit value. `GET /healthz` → `forwarded_client`
checks the result on a live host: `ok` when the resolved address equals the request's `Fly-Client-IP`,
`not_checked` without that header, `unresolved_private` when the resolved address is not a public one
(the proxy itself was not trusted), `unresolved_other` for a different public address. The header is
used only for this self-check; a client that forges it changes nothing but its own answer.

### Entitlement signing key

`uv run dome-api-gen-entitlement-key [path] [--force]` (or `scripts/gen-entitlement-key.sh`) creates
an Ed25519 key for development. In production, provision the PEM from the deployment's secret store
and point `DOME_ENTITLEMENT_SIGNING_KEY_PEM_PATH` at it; rotate by deploying a new key (agents
re-fetch the JWKS and refresh assertions on every connect).

## Tests

The suite runs against a real PostgreSQL, a real OIDC login through `tools/dev-idp`, and the API under
uvicorn in-process on a free port. REST is exercised with httpx, sockets with the `websockets`
library, agents and controllers are simulated with real ES256 keys from `dome_protocol`.

```sh
# once: PostgreSQL on /tmp:54329 (make db-start from the root) and cd tools/dev-idp && uv sync
cd cloud-api
uv run pytest -q -p no:cacheprovider   # creates dome_test_<random>, runs alembic upgrade head, drops it afterwards
# with stripe-mock running as in CI (tests/test_stripe_provider_mock.py says how), its 6 tests run too
DOME_TEST_STRIPE_MOCK_URL=http://127.0.0.1:12111 uv run pytest -q -p no:cacheprovider
uv run ruff check . && uv run ruff format --check .
uv run mypy dome_api tests
```

Current counts (2026-10-23, billing against stripe-mock): **578 passed, 7 skipped** without stripe-mock and **584
passed, 1 skipped** with `DOME_TEST_STRIPE_MOCK_URL` set (as in CI); mypy strict clean on 105 files; ruff and ruff
format clean. New: `tests/test_stripe_provider_mock.py` (6, skipped without stripe-mock), 8 in
`tests/test_billing_stripe_adapter.py` (Stripe-shaped error answers through the SDK, the HTTP client closed, the
stripe SDK's own logger held at WARNING) and 8 in `tests/test_billing_checkout.py` (page expiry inside Stripe's window
with the clock 20 minutes off, no replay of an expiry Stripe would refuse, the provider-error log fields, a customer id
from a replaced key replaced once); `test_billing_settings.py` expects 1–23 hours, default 23.
Before that:
(2026-10-21, Screen view Phase 1 relay half, protocol 1.4): **532 passed, 1 skipped**; mypy strict
clean on 104 files; ruff and ruff format clean. New:
`tests/test_screen_routing.py` (23, the relay paths end to end with 1.4 simulators and `FakeTurn`),
`tests/test_screen_metering.py` (14, the ledger from several database sessions), `tests/test_screen_turn.py`
(15: `TurnService`, caps, breaker, revocation paths, the Cloudflare adapter against a mock transport),
`tests/test_screen_settings.py` (9), `tests/test_screen_migration.py` (1, 0005 down and up). Changed in expectation:
`test_relay_routing.py` and `test_auth_and_sessions.py` pin 1.4 where they pinned 1.3 (both simulators now announce
`("1.0", "1.1", "1.2", "1.3", "1.4")`); the harness gained `AgentSim.screen_answer` / `screen_session` /
`screen_usage` / `screen_result`, `ControllerSim.screen_ice_request` / `screen_start`, `decide_pairing(pin_pc_key=)`,
the `screen` fixture (switch on, `FakeClock`, `FakeTurn`) and `fake_turn()` / `screen_clock()`. Before that:
(2026-10-20, plans and billing, protocol 1.3): **444 passed, 1 skipped**; mypy strict clean on 95
files; ruff and ruff format clean. New: `tests/test_billing_*.py` (settings and the CLI pass, checkout and portal,
webhook, lifecycle with an injected clock, top-ups, the Stripe adapter without the network, the 0004 migration down
and up) and the rewritten `tests/test_assistant_metering.py` (windows, taste, top-up order, the shared
`ai-window-cases.json` fixture, concurrency); the skipped one is the opt-in live AI check. Before that:
(2026-10-11, AI assistant step 1): **301 passed, 1 skipped** (126 before the assistant, all unchanged
in expectation apart from `test_security_fixes.py`'s proxy test, whose staging settings now drop the suite's fake
AI provider; 175 new in `tests/test_assistant_*.py`: catalogue and golden prefix, the Anthropic adapter against an
`httpx2.MockTransport` stub, pricing, output validation with the shared plan fixture, metering races from 10
database sessions, routes end to end, settings; the skipped one is the opt-in live check). Earlier:
(2026-10-09, protocol 1.1 build after the review fixes): **117 passed** (89 from the 1.0 build,
all unchanged in expectation; 13 in `tests/test_input_routing.py`; 6 in `tests/test_support_tickets.py`, whose
bundle and redaction-helper tests gained typed-text / URL / pairing-code assertions, plus concurrent-budget and
reference-collision tests; 9 in `tests/test_input_hardening.py` for the flood close, per-socket RATE_LIMITED
throttling, single delivery to a subscribed owner, live-grant owners and the bounded owner map, the per-PC
rejection cap, 1.0 state stripping and `PC_RECONNECTING`); mypy strict clean on 61 files; ruff clean. The flood
tests are timing-based (rate-controlled senders against the real limits) and passed in repeated full runs. The harness (`tests/conftest.py`, shared with the repository-level
`tests/`) gained `ControllerSim.input_envelope` / `input_batch`, `AgentSim.input_ack` / `input_session` /
`grant_update`, `new_input_session_id()` and a `protocol_versions` parameter on both `connect()`s; both
simulators now announce `("1.0", "1.1")` by default. No existing test's expectation changed.

`DOME_TEST_DATABASE_URL` (default `postgresql+psycopg://dome@/dome_test?host=/tmp&port=54329`) names the
server and credentials; the database part is replaced by the per-session random name. Every REST
response body in the tests is validated against `shared/protocol/schemas/rest.schema.json`, every
frame against `relay-frames.schema.json`.

## Operational notes

- `dome-api` runs `alembic upgrade head` before serving; the lifespan refuses to start if the database
  is not at the newest revision. `alembic` itself is available with `uv run alembic -c alembic.ini …`
  (`DOME_DATABASE_URL` must be set).
- One process per deployment (ADR-0001 D1). The connection manager is in-memory; a restart marks
  in-flight commands `outcome_unknown` / `expired` (start-up sweep) and agents reconnect with backoff.
- Logs are JSON on stderr. Keys such as `token`, `pc_credential`, `code_hash`, `challenge_text`,
  `payload`, `sig`, `title`, `email`, `text` (typed keyboard content), the assistant's `reply`, `choices`, `query`,
  `recent`, `persona`, `context`, `steps`, `params`, `user_json`, `system_text`, the Screen view keys `offer_sdp`,
  `answer_sdp`, `sdp`, `candidate(s)`, `ice_servers`, `credential`, `username`, and anything ending in
  `_token/_secret/_code/_credential/_key` are redacted; IPv4/IPv6 addresses in free text are masked; URLs are logged as
  paths only. Input batches are never logged (not even on rejection
  beyond the error code), so typed text exists only in transit through this process.
- Migrations: `0001_initial`, `0002_support_tickets`, `0003_assistant`, `0004_billing`, `0005_screen_view`. `dome-api`
  applies them at start-up;
  on Fly.io the `release_command` applies them and the process starts with `--skip-migrations`.
- `GET /healthz` (no auth, `no-store`): `status` (`ok`/`degraded`, 200/503), `version`, `commit` (the build
  commit or null), `database`, `forwarded_client` (above), the relay counts, and the Screen view fields `screen_view`,
  `turn`, `turn_month_gb`, `turn_month_basis`, `turn_month_gb_provisional`, `turn_reconciled_at` (cached for a minute;
  never a secret). The database pool recycles
  connections after 600 s and pre-pings (Fly Managed Postgres guidance; staging's Neon database closes every
  connection when its compute suspends, and pre-ping replaces them: `tests/test_database_url.py`). A
  `DATABASE_URL` query such as Neon's `?sslmode=require&channel_binding=require` reaches libpq unchanged.
- Request log lines carry the matched route template and a masked path: a device-link `user_code`
  never appears (`/v1/agent-link/{user_code}/approve`); query strings are never logged.
- Security headers: restrictive CSP (`form-action` allows the OIDC issuer), `Referrer-Policy: same-origin`,
  `X-Content-Type-Options: nosniff`, `Permissions-Policy: camera=(self)`, HSTS in production,
  `Cache-Control: no-store` on `/v1`, `/ws` and `/healthz`.
