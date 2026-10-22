# cloud-api — known issues

Items the review surfaced that are deliberately deferred, with the reason and the intended fix.

1. **Agent sockets have no per-socket frame budget.** The controller socket is frame-limited before any
   database work (DECISIONS #22); `/ws/agent` relies on the PC credential (issued only after an account
   owner approved the link) and on per-frame schema validation. A compromised or buggy agent could still
   write `agent_connected`/`pairing_*` events or `state` broadcasts at wire speed. Intended fix: the same
   `TokenBucketLimiter` keyed by `pc_id` in `agent_ws._loop`, closing with 4000 after `burst` refusals.
2. **Rate limiters are in-memory per process.** Correct for ADR-0001 D1 (one relay process); a second
   process would double every budget. If the deployment ever runs more than one API process, the sliding
   windows and buckets move to PostgreSQL (`security_events`-style rows with a TTL) or the proxy layer.
3. **`ACCOUNT_MISMATCH` for a command to the account's own, just-unlinked PC.** The routing order treats a
   soft-deleted PC exactly like a PC that never belonged to the account (design step 5). Subscribers are
   now told the PC is gone (DECISIONS #24), so a phone only sees this code if it ignores that
   `pc_status`. A dedicated code would be a contract change (`errors.json` is frozen for this component).
4. **Proof of possession at link start** is a contract gap (`CONTRACT_ISSUES.md` #10); the backend-side
   mitigation (no takeover of a linked PC) is in place, the cryptographic fix needs the MINOR bump.
5. **The Fly.io client-address settings are verified by a self-check, not yet on a live machine.**
   `deploy/fly.toml` trusts the private IPv4 ranges and reads `Fly-Client-IP` (`DOME_CLIENT_IP_HEADER`),
   based on Fly's documentation (the proxy connects over a private IPv4 address; the right-most
   `X-Forwarded-For` hop is the app's own address). `/healthz` → `forwarded_client` reports whether the
   resolved address equals `Fly-Client-IP`; the Deploy workflow refuses a deploy where it is not `ok`, and
   production requires `ok` on staging. Nothing outside a deploy run watches it until an external monitor
   exists. Staging and production still refuse to start without an explicit `DOME_TRUSTED_PROXIES`.
6. **Operators have no support route.** Tickets are written and read by the owning account only; the
   `status` / `answer` columns exist (`in_review`, `answered`, `closed`) but nothing sets them yet. The
   operator interface (spec §15: strong operator authentication, audit, no command execution) is the
   intended home; until then an operator answers out of band. `DOME_SUPPORT_RESPONSE_EXPECTATION` stays unset
   until the founder decides on a realistic expectation.
7. **Per-batch database work on the input path.** `route_input_batch` performs about five queries per
   accepted batch (controller record, account/plan, plan-enabled controllers, PC, grant). The suite forwards
   well above the 40 batches/s budget on one socket, but at many concurrent touchpads this is the first thing
   to optimise: a one-second per-socket authorisation cache invalidated by `grant_update`, revocation and
   `pc_status` changes. Not done yet because the agent re-verifies every batch against its local grant, so a
   stale relay check cannot grant anything.
8. **The relay does not tell the agent that a controller's socket closed.** `rules.input_sessions` lists
   "the relay losing the controller's socket" as a session-ending trigger with reason
   `controller_disconnected`, but `relay_to_agent` has no frame for it (`CONTRACT_ISSUES.md` #12); the agent's
   3 s lease ends the session instead.
9. ~~**`PC_RECONNECTING` for input is not exercised by a test.**~~ Resolved. The earlier note claimed the window
   between `register_agent` and `snapshot_sent = True` was a single awaited send; it actually spans a database
   transaction (`db.get(PC)`, `build_snapshot` with its queries) plus the send, so a batch can land in it.
   `tests/test_input_hardening.py::test_input_batch_before_first_snapshot_is_pc_reconnecting` holds the snapshot
   build on an event, asserts `PC_RECONNECTING`, releases it and asserts the next batch is forwarded.
10. **`.env.example` is a root file** and was not extended with `DOME_RATE_SUPPORT_TICKETS_PER_HOUR` /
    `DOME_SUPPORT_RESPONSE_EXPECTATION`; both default sensibly and are documented in this README.
11. **An input flood still costs a parse and a schema validation per frame until the socket closes.** The
    pre-database input bucket runs after `loads_strict` + `validate_frame` (the frame type is only known then).
    DECISIONS #37 bounds the cost to about the refusal budget (400 frames plus 80/s) per socket before the 4000
    close; a reconnect needs a fresh hello proof (signature check + database). Peeking at the frame type before the
    strict parse would save work but duplicates the parser's trust boundary; not done.
12. **Pairing-code redaction is heuristic.** A code typed with mixed separators (e.g. `K7Q2-M9XD 4HPR`) or split
    across lines is not recognised; a 20-symbol code with no digit in lower case is not masked (probability
    ~0.06 % for a random code). Diagnostics bundles come from the PWA, which never includes pairing material
    (mobile-app), so this is the server's second line, not the first.
13. **Support budget release is approximate under concurrency.** `SlidingWindowLimiter.release` drops the most
    recent timestamp for the key, which may belong to a concurrent request of the same account; the count stays
    exact, the window edge moves by the gap between the two requests (milliseconds).
14. **Timing-based relay tests can flake on a loaded machine.** The input flood tests and the 1.0-build
    `test_queue_depth_and_rate_limit` compare token-bucket outcomes with wall-clock send rates. While these fixes
    were made, one full run out of about ten failed once without a captured cause (an earlier failure of
    `test_queue_depth_and_rate_limit` followed a crashed flood test that left the server draining a backlog; that
    test bug is fixed). If it recurs, swap the limiters for smaller ones in the test, as
    `test_controller_frame_flood_is_throttled_without_growing_security_events` does.

## AI assistant, step 1

15. **Not live-tested.** There is no Anthropic key in the build environment. The adapter is tested against an
    `httpx2.MockTransport` stub with the real `anthropic` 1.13 SDK (request body, headers, event parsing, error and
    retry mapping), the service with a scripted fake provider. Whether the API accepts the output schema (35 `anyOf`
    variants, parameterless variants with `"params": {"type": "object", "additionalProperties": false,
    "properties": {}}`), what `usage.iterations` entries really carry, what a streamed mid-output fallback really looks
    like (the adapter joins the declined partial and its continuation, docs/design/assistant.md §2.10), and what a
    streamed message's request id is come from the bundled API reference and must be confirmed by the owner's first
    live call
    (`tests/test_assistant_live.py`, docs/design/assistant.md §8.6). If the first call fails with
    `reason=bad_request`, look at the parameterless `params` note of §2.4 first.
16. **A Cancel through the Cloudflare tunnel may never reach uvicorn.** The disconnect watcher cancels the provider
    call and frees the account only when uvicorn sees the client go; through a tunnel the turn may simply complete
    (and be charged), and the phone's bounded `AI_BUSY` resend covers the gap (§4.3).
17. **The price table is hand-kept.** DoMe's spend is its own estimate; reconcile it monthly with the Anthropic
    Console's usage and cost report and re-check `assistant/pricing.py` against Anthropic's pricing page before any
    paid launch (docs/OPERATIONS.md). Cache writes are priced at the configured TTL's rate, not from the
    `cache_creation` breakdown.
18. **Single process.** `in_flight`, the per-account turn buckets and the sweeper are in memory per process
    (ADR-0001 D1). The reservations themselves are in PostgreSQL and stay exact under any number of processes.
19. **AI windows are rolling, not tied to the subscription** (protocol 1.3): a 5-hour and a weekly window open at
    the account's first request while none is open, on every plan and billing interval. A plan change mid-window
    keeps the open windows and applies the new plan's limits to them from the next request. The operator ceiling
    stays per calendar month in UTC. `usage_periods` (the calendar-month table) is no longer written.

## Billing (Phase C)

20. **Never run against Stripe.** Everything is integration-tested with `FakeStripe`, which signs exactly as Stripe
    does (`tests/test_billing_stripe_adapter.py` cross-checks it with the SDK's own helper). The real request shapes
    (Checkout parameters, the invoice's subscription under `parent.subscription_details`, the period on the
    subscription item) follow the pinned `stripe` 16.0 SDK's models and must be confirmed by the founder's first
    test-mode run on staging (`docs/LAUNCH_SETUP.md` 11A).
21. **Disputes and refunds are not handled.** `charge.dispute.*` and `charge.refunded` are recorded as `ignored` with a
    `billing.needs_operator` warning; access is not paused and nothing is canceled automatically. Trials are not
    configured.
22. **No `DELETE /v1/account` route.** Only `BillingService.cancel_for_account_deletion()` and its
    `pending_deletions` retry exist; account deletion stays a support request.
23. **Single-process reconciler.** The hourly pass runs in the API process (ADR-0001 D1). Every step is idempotent
    (deduplicated events, fetch-then-apply, conditional updates), so a second process would only repeat work.
24. **The Deploy button does not carry the Stripe values yet.** `deploy/` and `.github/workflows/deploy.yml` were
    outside this build; until they pass the seven `DOME_STRIPE_*` values to Fly, staging and production run with
    billing off whatever is pasted into GitHub.
25. **The phone learns the plan by polling.** After Checkout the PWA polls `GET /v1/session` and
    `GET /v1/account/billing`; there is no push to the phone, so activation shows within the poll interval of the
    webhook, not instantly.
26. **A second subscription made outside DoMe is left to the operator.** Only reachable by paying a page DoMe did not
    create (for example one made in the Stripe dashboard): it is logged as `billing.second_subscription` and gives no
    entitlement; refund or cancel it in Stripe.

## Screen view (Phase 1 beta, protocol 1.4)

27. **Never run against Cloudflare.** The TURN adapter (`screen/turn.py::CloudflareTurn`) follows the request and
    response shapes the design's researchers read in the vendor documentation [research, verify]: `POST
    .../turn/keys/{key_id}/credentials/generate-ice-servers {ttl, customIdentifier}` → `{"iceServers": {urls, username,
    credential}}`, `POST .../credentials/{username}/revoke`, and the GraphQL `callsTurnUsageAdaptiveGroups` query with
    `egressBytes` grouped by `customIdentifier`, `username` and `date`, read with the analytics token after discovering
    the account tag from `GET /client/v4/accounts`. Whether the revoke endpoint ends a running allocation, what the
    maximum TTL is, whether a payment method is required and how fresh the analytics figures are is **not yet
    verified**; until it is, the provisional bound (every ticketed second counted as relayed at 0.5 GB/h plus the TTL of
    every credential without a session) carries the caps and the guard on its own. Everything here is **unit- and
    integration-tested with `FakeTurn`** and an httpx mock transport for the adapter; the first staging run with real
    keys is the real test (`docs/design/screen-view.md` §21 question 3).
28. **`GET /v1/plans` does not carry the switch** (`CONTRACT_ISSUES.md` #19); `/healthz` does.
29. **A start lost with the agent socket between forward and result is bound by a fallback** (review fix 2026-10-22).
    The pending start lives on the agent connection, and the window between the forward and the result includes the
    consent prompt (≤ 30 s) and DTLS (≤ 20 s), not just a second: a PC socket drop or a relay restart (every Fly
    deploy) in that window loses it. The relay now binds the PC's newest session-less reservation of the last
    `PENDING_START_SECONDS` (the frame's controller's, when the frame names one) to the `started` frame or the first
    `screen_usage`, so the tickets keep coming. A `screen_usage` for a session the relay never reserved gets no answer
    at all (the agent ends at its ticket's deadline with the honest `allowance_unconfirmed`, never "Free time is used
    up"); one for a session the relay already settled still gets `final`. The fallback binds by PC and recency, not by
    command id (the agent's frames carry none): two forgotten starts of one PC within the same 105 s would be told
    apart only by controller.
30. **The sweeps are global and in memory per process.** The ledger sweeper, the TURN sweeper and the owner-presence
    check run in the API process every `DOME_RELAY_SWEEP_INTERVAL_SECONDS` over every account (ADR-0001 D1); the
    `screen_usage` budget and the ICE/start buckets are in-memory limiters (#2 above). Rows are in PostgreSQL and
    settlement is idempotent, so a second process would only repeat work.
31. **Resolved (2026-10-22): the pause discount is capped per window and only for pairs.** Only a `paused -> resumed`
    secure-desktop pair the relay saw during a started session is discounted, at most
    `screen_secure_desktop_pause_max_seconds` (120 s) per WINDOW across all its sessions (`screen_view_windows.
    pause_discount_seconds`); a pause still open at the end is charged in full. A session spanning two windows can
    still get up to 120 s in each window (5 hours apart).
32. **Owner presence after a relay restart.** `controller_last_bound_at` is in memory; a fresh process treats every
    owner as "last seen at start-up", so a phone that does not reconnect within the 60 s grace gets `controller_offline`
    through the dead-man ticket. Phones reconnect with a short backoff, so this ends only truly absent owners.
33. **The deploy checks on the TURN values are shape guesses.** `dome_deploy.read_screen_view` requires a key id of
    8–128 `[A-Za-z0-9_-]` characters and tokens of at least 16 importable characters; the vendor's real formats were not
    checked. A real value the check refuses is a one-line fix.
34. **Nothing is device-verified.** The relay half is integration-tested against real PostgreSQL with simulated agents
    and phones; capture, playback, NAT behaviour and the indicator belong to the agent and the phone and to the device
    round (`docs/design/screen-view.md` §18).
35. **The monthly guard's figure depends on reconciliation being fresh** (review fix 2026-10-22): while the latest pass
    (`turn_reconcile_passes`) is under 3 h old the guard is the provider's figures plus the provisional bound only for
    activity from an hour before that pass; otherwise (no analytics token, provider failures, a stalled process) it falls
    back to the whole month's provisional bound, which counts every ticketed second as relayed and can put Free on
    Data saver early (COST_MODEL §7B). `/healthz` `turn_month_gb` and `turn_month_basis` show which applies. The
    session-less term (`turn_mint_days`) counts a credential's whole TTL until it is bound to a session or its
    revocation is confirmed; credentials that simply expire unrevoked keep counting for the month.
36. **An unconfirmed TURN revocation is retried, not guaranteed** (review fix 2026-10-22): a provider error leaves the
    username in `turn_pending_revocations`, retried by the sweeper every 30 s until the provider confirms or the
    credential expires (Free ≤ 3600 s, unlimited ≤ 14400 s). Until then the credential can still relay; the security
    events say `turn_revoke_failed`, not `turn_credential_revoked`. Never run against the real provider (#27).
