# DoMe operations

Status: written on 2026-10-09 from the code in this repository. **No DoMe deployment exists yet.**
`deploy/README.md` is a placeholder ("Build in progress"), `.github/workflows/` is empty, and no
`Dockerfile` or `fly.toml` is checked in; the `README.md` rows that describe them name intended
content. Everything below is therefore one of two kinds, and each section says which:

- **as implemented** — behaviour the service and agent code has today, with the test that shows it
  (tag: unit-tested / integration-tested on Linux);
- **procedure, not yet run** — the steps an operator will follow, derived from ADR-0001 D1/D2 and the
  constraints the code imposes (`docs/ARCHITECTURE.md` §5). None of these has been executed against
  Fly.io or any other host (**not yet verified**). Spec §15 requires a restore and a failed-deployment
  recovery to be *tested* before a release is called production-ready; neither has been.

Related: `cloud-api/README.md` (every setting), `docs/SECURITY.md` §6 (operator boundaries),
`docs/DATA_RETENTION.md` (what the database holds), `docs/TROUBLESHOOTING.md` (customer-facing
recovery).

## 0. What runs where

One deployable unit: the `dome-api` process (FastAPI under uvicorn, Python 3.12) serving REST at
`/v1`, both WebSocket endpoints (`/ws/agent`, `/ws/controller`), `/healthz`,
`/.well-known/dome-jwks.json` and, with `DOME_STATIC_DIR`, the built PWA at `/` from the **same
origin** (no CORS exists). PostgreSQL 16 is the only datastore. The OpenID Connect provider is
external (Auth0 or Keycloak per ADR-0001 D3; `tools/dev-idp` is development-only and must never be
deployed — the backend has no other login path). Customer devices (Windows agent, iPhone PWA, browser
extension) are not operated by DoMe.

**Exactly one API process per deployment** (ADR-0001 D1). The connection manager, the cached
`state` per PC, every rate limiter and frame budget live in process memory; a second replica would
double every budget and could not route to agents connected to the other one
(`cloud-api/KNOWN_ISSUES.md` #2). Scaling out is a Redis pub/sub seam, not a configuration change.

## 1. Configuration (as implemented)

All settings are `DOME_*` environment variables validated once at start-up by
`cloud-api/dome_api/settings.py`; a bad value stops the process with a plain message (exit code 2 from
`dome-api`). The full table is in `cloud-api/README.md`; the production-relevant rules:

| Rule | Enforced by |
| --- | --- |
| `DOME_ENV=production` requires an `https://` `DOME_PUBLIC_ORIGIN` and `DOME_OIDC_ISSUER`, and a `DOME_SESSION_SECRET` not starting with `change-me` (minimum 16 characters) | `Settings._v_production` |
| `staging` and `production` require `DOME_TRUSTED_PROXIES` (CIDRs of the ingress proxy, or `none`); `*` is refused | `Settings._v_trusted_proxies`; **integration-tested** `test_security_fixes.py::test_settings_refuse_wildcard_and_require_proxies_outside_development` |
| `DOME_DATABASE_URL` must be `postgresql+psycopg://…` | `Settings._v_db` |
| `DOME_ENTITLEMENT_SIGNING_KEY_PEM_PATH` must point at an Ed25519 PKCS8 PEM (0600) | `EntitlementSigner.from_path` at start-up |
| `DOME_STATIC_DIR`, if set, must be a directory | `Settings._v_production` |
| HSTS is sent only in `production`; the session cookie is `Secure` iff the origin is https | `headers.py`, `DECISIONS.md` #14 |
| Outside production every REST response body is validated against `rest.schema.json` before it leaves the process (a contract violation is a loud 500) | `Settings.validate_rest_responses` |

Secrets to provision from the host's secret store (never in the image or repository):
`DOME_SESSION_SECRET`, `DOME_OIDC_CLIENT_SECRET`, `DOME_DATABASE_URL` (contains the password), the
entitlement PEM (mounted as a file), and, in Phase C, `DOME_STRIPE_*`. Generate the entitlement key
with `uv run dome-api-gen-entitlement-key <path>` (refuses to overwrite without `--force`).

Client address resolution: `dome-api` starts uvicorn with `proxy_headers=False` and applies its own
`TrustedProxyMiddleware`, taking the client IP from the right-most `X-Forwarded-For` hop that is not
itself a trusted proxy. Running uvicorn by hand with `--proxy-headers` weakens this. Every per-IP
limit and every stored `ip_hash` depends on it (**integration-tested**:
`test_security_fixes.py::test_forwarded_for_from_an_untrusted_peer_cannot_evade_per_ip_limits`,
`::test_trusted_proxy_middleware_uses_the_hop_the_proxy_appended`).

## 2. Build (procedure, not yet run)

There is no container definition yet. One must do, in order:

1. `cd shared/ts && pnpm install && pnpm gen:types`, then `cd mobile-app && pnpm install && pnpm build`
   → `mobile-app/dist/`.
2. `cd cloud-api && uv sync --no-dev` (installs `dome-protocol` from `../shared/python` as a path
   dependency, so the build context must contain `shared/`).
3. Image contents: Python 3.12, the `cloud-api` virtual environment, `shared/python`,
   `mobile-app/dist` at the path given by `DOME_STATIC_DIR`. Entry point: `dome-api` (runs
   `alembic upgrade head`, then serves on `DOME_API_BIND`, which must be `0.0.0.0:<port>` inside a
   container). Do not bake `.env` in; do not include `tools/dev-idp`.
4. Tag the image with the git commit and the `dome_api.__version__` string (`0.1.0` today) so
   `/healthz` → `version` identifies the running build.

Before building any image, the suites in `docs/ACCEPTANCE.md` §1 must pass on the commit.

## 3. Deployment to Fly.io (procedure, not yet run)

ADR-0001 D2 chose Fly.io for long-lived WebSockets on ordinary VMs plus managed Postgres; nothing in
the code is Fly-specific and Render/Railway are equivalents. The steps that follow are what the code
requires; the Fly-specific values are to be confirmed against Fly's current documentation at the time
of the first deployment.

1. **Database.** Create a managed PostgreSQL 16 instance in the same region. Confirm on the vendor
   page that volumes and backups are encrypted at rest and note the backup retention
   (`docs/DATA_RETENTION.md` §1, **not yet verified**). Create a database and a role for the API with
   ownership of that database only.
2. **App.** Create the app with **one** machine, no autoscaling (`min_machines = max_machines = 1`),
   and a health check on `GET /healthz` expecting 200. The process must receive `SIGTERM` and be given
   at least **25 s** to stop (`kill_timeout`): uvicorn's graceful shutdown is 20 s
   (`timeout_graceful_shutdown=20` in `dome_api/cli.py`) and the lifespan then stops the sweeper,
   closes the OIDC client and disposes the engine.
3. **Secrets.** Set the variables from §1 as Fly secrets; mount or write the entitlement PEM to the
   path in `DOME_ENTITLEMENT_SIGNING_KEY_PEM_PATH` (a Fly secret written to a file by the entry point
   is acceptable; the file must be 0600).
4. **Identity provider.** Register `https://<DOME_PUBLIC_ORIGIN>/v1/auth/callback` as the redirect
   URI at Auth0/Keycloak; set `DOME_OIDC_ISSUER`, `DOME_OIDC_CLIENT_ID`, `DOME_OIDC_CLIENT_SECRET`.
   `tools/dev-idp` is never used here. Only `tools/dev-idp` has been exercised so far
   (`docs/HANDOFF.md` §4).
5. **Trusted proxies.** Before the first start with `DOME_ENV=staging|production`, find the address
   range the Fly proxy connects *from* as seen inside the VM: `fly ssh console`, then `ss -tn` while
   a request is in flight. Set `DOME_TRUSTED_PROXIES` to that range (the README example is
   `fdaa::/16`, the 6PN private network, **to be verified**, `cloud-api/KNOWN_ISSUES.md` #5). The
   process refuses to start without a value.
6. **Deploy.** `fly deploy` with the image from §2. `dome-api` runs migrations at start-up (§4). Watch
   the log for `migrations.start`, `migrations.done`, `startup_sweep` (rows closed from the previous
   process), `startup` with `env` and `origin`.
7. **Verify.** `GET /healthz` → `{"status":"ok","database":"ok","relay":{...}}`; `GET /` serves the
   PWA; `GET /v1/plans` is public and reports `billing_enabled:false`; sign in from a phone; link a
   development agent with `DOME_AGENT_API_URL=https://<origin>` (the relay URL is handed to the agent at
   link time, `wss://<origin>/ws/agent`); confirm `agent_connected` appears in security activity and
   `/healthz` → `relay.agents` is 1. Then run Scenario 4 of `docs/ACCEPTANCE.md`.
8. **Environments.** Staging and production are separate Fly apps with separate databases, separate
   OIDC clients and separate entitlement keys (spec §18 D). Never point a staging agent at production.

Idle WebSockets: the agent sends an application `ping` every 25 s and closes a socket silent for
55 s; the PWA pings every 25 s with a 75 s idle rule; uvicorn sends protocol pings every 20 s with a
20 s timeout. Any proxy idle timeout above ~30 s is therefore tolerated; a shorter one would show up
as agents cycling `reconnecting` every few seconds in the logs.

## 4. Database migrations (as implemented)

- Alembic, scripts in `cloud-api/dome_api/db/alembic/versions/`. There is exactly one revision,
  `0001` (initial schema including the empty Phase C tables); `downgrade()` exists and drops
  everything, so a downgrade of `0001` is a wipe, never a rollback.
- `dome-api` runs `alembic upgrade head` **before** binding the port (`dome_api/cli.py`). The
  application lifespan then re-checks `alembic_version` and refuses to start if the database is not
  at the single head (`main.py::_check_migrations`; it also refuses if there is more than one head).
  `dome-api --skip-migrations` serves without upgrading but still performs the check.
- Manual use: `cd cloud-api && DOME_DATABASE_URL=… uv run alembic -c alembic.ini current | upgrade head | downgrade <rev>`.
- `alembic check` (run during the cloud-api review) confirms the SQLAlchemy models and `0001` are
  in sync; make it part of CI when CI exists.

Procedure for a release that adds a migration (not yet exercised):

1. Write only **additive, backward-compatible** migrations (new tables/columns with defaults, new
   indexes `CONCURRENTLY` where large) so that the previous image keeps working against the new
   schema; removals go in a later release after the code that needed the old shape is gone
   (expand/contract). This is what makes §6 rollback possible.
2. Take a backup (§5) and record its id in the release notes.
3. Deploy. Because the single machine upgrades the schema and then serves, the window between
   `migrations.start` and `startup` is downtime for REST and every socket; agents reconnect with
   backoff and phones re-subscribe. Keep migrations short; anything long-running belongs in a
   separate step run before the deploy with the old code still serving.
4. If `migrations.*` fails the process exits non-zero and the previous machine state is whatever the
   platform does on a failed health check; see §6.

## 5. Backup and restore (procedure, not yet run)

What is in the database and what is not (`docs/DATA_RETENTION.md` §1): accounts, sessions (hashes),
PCs and their public keys, PC credential and token **hashes**, controllers (public keys), grants,
pairing sessions (code hashes), command lifecycle rows (no content), security and activation events,
and the empty Phase C tables. No private keys, no payloads, no titles. Loss of the database therefore
loses *who is linked and paired to what*; it cannot leak command content or let anyone sign commands.

**Backups.**

1. Managed snapshots from the provider (retention per plan — confirm and record it).
2. In addition, a nightly logical dump to object storage in another provider or region:
   `pg_dump --format=custom --no-owner --no-privileges "$DOME_DATABASE_URL_SYNC" > dome-$(date -u +%F).dump`,
   encrypted at rest, retained 30 days (proposal; `docs/DATA_RETENTION.md` server-log and backup
   rows are marked *not yet verified*).
3. The entitlement private key, `DOME_SESSION_SECRET` and the OIDC client secret are **not** in the
   database; keep them in the secret store with its own backup. Without the entitlement key, Pro
   assertions cannot be issued until a new key is deployed (§7); without the session secret everyone
   is signed out.

**Restore procedure** (to be rehearsed on staging before any release, spec §15):

1. Stop the API machine (or scale to 0) so no writes race the restore. Customers see *Offline* on the
   phone; agents retry with backoff; nothing is queued anywhere (`rules.terminal_result`,
   **integration-tested**).
2. Create a fresh database; `pg_restore --no-owner --no-privileges -d <new db> dome-<date>.dump`
   (or restore the provider snapshot to a new instance).
3. Point `DOME_DATABASE_URL` at it and start the API. `alembic upgrade head` is a no-op if the dump
   is at head, otherwise it upgrades; the lifespan's head check must pass.
4. Expected consequences, which are correct behaviour, not bugs: every command row that was in flight
   at backup time is swept to `outcome_unknown`/`expired` by `startup_sweep`; sessions created after
   the backup are gone (customers sign in again; paired devices are unaffected if their rows were in
   the backup); PCs linked after the backup receive 401 at `/v1/agent/token` and the tray asks for a
   re-link; phones paired after the backup see *Not paired* and must pair again. Commands never
   replay because nothing is stored to replay.
5. Verify: `/healthz` ok; a known account signs in; `GET /v1/pcs` lists its PCs; an agent reconnects
   (`agent_connected` event) and a command round-trips.
6. Record the rehearsal (date, dump size, wall-clock minutes to serve) in `docs/ACCEPTANCE.md`.

Point-in-time recovery depends on the provider; the application has no requirements beyond "one
consistent snapshot".

## 6. Deployment rollback (procedure, not yet run)

- Rolling back the **code** is redeploying the previous image (`fly releases` / `fly deploy
  --image <previous>`). It is safe only if every migration applied since is additive (§4 rule 1): the
  lifespan head check compares the database to the *image's* newest revision, so an older image
  started against a newer schema **refuses to start** (`database revision '0002' is not the expected
  head '0001'`). Therefore: a rollback across a migration boundary requires either the old image to
  include the new (additive) migration file, or `alembic downgrade` first — which, for anything
  destructive, means a restore (§5). Keep migrations and the code that needs them in separate
  releases when in doubt.
- A failed deployment leaves agents reconnecting and phones *Offline*; nothing is lost but
  availability. Expect `startup_sweep` to close in-flight rows and a burst of reconnects when the
  working image returns.
- Spec §15 asks for a failed-deployment recovery to be tested before release: on staging, deploy an
  image with a deliberately broken setting (for example a missing `DOME_TRUSTED_PROXIES`), observe the
  exit-code-2 configuration error, roll back, and time the recovery. Not done yet.

## 7. Secret rotation (as implemented, procedures not yet run)

| Secret | How to rotate | Effect on customers |
| --- | --- | --- |
| `DOME_SESSION_SECRET` | Set the new value and restart | Every web session is invalid: all phones and browsers sign in again (`UNAUTHENTICATED` / close 4008); controller keys and pairings are unaffected. Stored `ip_hash` values no longer match new ones (they are keyed by this secret), which only affects correlating old security events by IP. |
| Entitlement signing key (Ed25519) | Generate a new PEM, deploy it, restart. The JWKS at `/.well-known/dome-jwks.json` serves the current public key; agents fetch the JWKS and refresh their assertion on every connect and at 80 % of the 1 h lifetime (`rules.token_and_entitlement_refresh`, **unit-tested** `pc-agent/tests/test_entitlement.py::test_refresh_against_fake_api`) | Free accounts: none (their assertion is null). Pro (Phase C): an assertion signed by the old key fails verification at the next refresh and the agent treats the account as Free until it obtains a new one — within an hour, immediately on reconnect. The 72 h grace applies only to network errors/5xx, never to a verification failure, so there is no window in which a revoked key keeps Pro alive. Serve both keys during a transition is **not** implemented (single key, single `kid`). |
| OIDC client secret | Rotate at the provider, set `DOME_OIDC_CLIENT_SECRET`, restart | Logins fail between the two steps (`oidc.token_exchange_failed`, HTTP 503 to the customer); existing sessions keep working. |
| Database password | Rotate at the provider, set `DOME_DATABASE_URL`, restart | Brief downtime; `pool_pre_ping` discards dead connections. |
| PC credential (per PC) | Customer: Devices → Unlink, then `DoMe.exe link` again; or operator sets `pc_credentials.revoked_at` | The agent gets 401 on `/v1/agent/token`, stops and asks for a re-link; pairings are not inherited (`cloud-api/DECISIONS.md` #2). |
| PC access tokens | Automatic: 1 h lifetime (`DOME_PC_ACCESS_TOKEN_SECONDS`), refreshed by the agent before reconnecting when < 5 min remain; open sockets are never closed for token expiry | none |
| Controller key (per phone) | Customer: revoke under Devices and pair again; or "Forget this installation" on the phone | That phone must pair again. |
| `DOME_STRIPE_*` (Phase C) | Not applicable yet | — |

There is no key-rotation automation and no dual-key JWKS; both are reasonable Phase D additions.

## 8. Monitoring signals (as implemented)

There is **no metrics endpoint and no alerting**; the signals below are what the code emits today.

**`GET /healthz`** (no auth, `Cache-Control: no-store`; **integration-tested**
`test_entitlement_and_misc.py::test_health`):

```json
{"status":"ok","version":"0.1.0","database":"ok",
 "relay":{"agents":12,"controllers":7,"max_connections":5000}}
```

`status: degraded` with HTTP 503 when `SELECT 1` fails. Poll it from an external monitor every
minute; alert on non-200, and on `relay.agents` dropping to 0 while it was > 0 (mass disconnect) or
approaching `max_connections`.

**Structured logs** (structlog JSON on stderr, redacted; request lines carry the route template, a
masked path and no query string). Event names worth counting or alerting on:

| Event | Meaning |
| --- | --- |
| `startup`, `shutdown`, `migrations.start`, `migrations.done` | Process lifecycle; a `startup` without a matching planned deploy is a crash-restart |
| `startup_sweep` (`rows`) | Commands closed as `outcome_unknown`/`expired` from a previous process; non-zero after every restart |
| `deadline_sweep` | In-flight deadlines fired by the 5 s sweeper; a steady rate means agents that acknowledge and never answer |
| `agent.superseded` | A second agent socket for the same PC (normal after a PC restart; a storm means two installs of one PC) |
| `agent.inflight_failed`, `agent.error_frame` | Agent disconnects with work in flight; agent-side protocol errors |
| `command.rejected` | Relay-side rejection before forwarding (the error code is in the line; a per-code histogram is the abuse dashboard) |
| `command.forwarded` | Dispatch rate; with `/healthz` relay counts this is the capacity view |
| `controller.throttled_close`, `security_event.suppressed` | Frame floods and security-event throttling (`DECISIONS.md` #22) |
| `result.late_correction`, `result.post_terminal_dropped` | Late results after `outcome_unknown` (one correction allowed) and anything else dropped |
| `confirmation_required.invalid`, `confirmation_required.unknown_command`, `ack.unknown_command`, `cancel.ignored`, `revoke_controller.ignored`, `pairing_decision.*` | Agent frames that did not match relay state; a rising count indicates an agent bug or a hostile agent |
| `oidc.discovery_failed`, `oidc.jwks_failed`, `oidc.token_exchange_failed`, `oidc.id_token_rejected`, `oidc.callback_error` | Identity-provider outage or misconfiguration (customers get 503 at sign-in); `id_token_rejected` with clock errors points at the service clock |
| `security_event` (`kind`, `severity`) | Every security event is also logged as it is written (below) |
| Unhandled exceptions | `unhandled_error_handler` logs the exception with the request id and returns `INTERNAL`; alert on any |

**Security events** (`security_events` table, account-scoped, redacted `detail`; readable by the
account through `GET /v1/account/security-events`). Kinds written today:

`account_created`, `logout`, `session_revoked`, `pc_linked`, `pc_link_denied`, `pc_credential_issued`,
`pc_enabled`, `pc_unlinked`, `link_code_lookup_failed`, `agent_connected`, `agent_disconnected`,
`agent_token_refused`, `pairing_started`, `pairing_claimed`, `pairing_declined`, `pairing_failed`,
`pairing_rate_limited`, `controller_paired`, `controller_limit_reached`, `controller_revoked`,
`grant_revoked`, `command_rejected`, `confirmation_rejected`, `subscribe_refused`,
`relay_frame_rejected`, `controller_throttled`, `relay_events_throttled`.

Useful aggregates (SQL over the table, or counts of the `security_event` log line by `kind`):
`pairing_failed` and `link_code_lookup_failed` per hour (guessing), `command_rejected` per account
per hour (a client out of sync or hostile), `agent_token_refused` (unlinked PCs still running),
`controller_throttled` (floods). Note `docs/DATA_RETENTION.md` §4: no purge job exists, so these tables
grow until it is added; watch table size.

**Agent side.** Customers produce redacted diagnostics on request (tray → Diagnostics…); there is no
telemetry from agents to the service. The `activation_events` table exists but nothing emits to it
(spec §16 instrumentation is Phase D).

Suggested first alerts, all unimplemented: `/healthz` non-200 for 2 minutes; `startup` more than
twice in 10 minutes; any unhandled exception; `oidc.*` failures above 5 per minute;
`relay.agents + relay.controllers > 0.8 × max_connections`; PostgreSQL connections above
`pool_size + max_overflow = 30` per process (the pool is 10 + 20 overflow with `pool_pre_ping` and a
30-minute recycle, `db/engine.py`).

## 9. Connection limits, idle timeouts and draining (as implemented)

| Parameter | Value | Where | Evidence |
| --- | --- | --- | --- |
| Global socket cap | `DOME_RELAY_MAX_CONNECTIONS` = 5000; upgrades above it get HTTP 503; sockets that have not completed `hello` count toward it | `relay/manager.py`, `ws_http.py` | integration-tested `test_relay_connection_cap_returns_503`, `test_pre_hello_sockets_count_toward_the_connection_cap` |
| Hello timeout | 10 s to send `hello` (`DOME_RELAY_HELLO_TIMEOUT_SECONDS`); agent gives up waiting for `hello_ack` after 15 s; PWA after 10 s | `controller_ws.py`, `agent_ws.py`; `relay_client.py HELLO_TIMEOUT`; `relay.ts HELLO_TIMEOUT_MS` | integration-tested `test_hello_is_required_and_validated`; unit-tested both clients |
| Frame size | 64 KiB (`DOME_RELAY_MAX_FRAME_BYTES`, also uvicorn `ws_max_size`) → close 1009; payload 16 KiB | `version.json → limits` | integration-tested `test_oversized_frame_closes_1009_and_malformed_frames_get_errors` |
| Protocol pings | uvicorn `ws_ping_interval=20`, `ws_ping_timeout=20` | `dome_api/cli.py` | code |
| Application pings | agent every 25 s, closes after 55 s of silence (`SILENCE_LIMIT`); PWA every 25 s, idle limit 75 s, 5 s nudge deadline on resume; the relay answers `ping` with `pong` | `relay_client.py`, `relay.ts`, `agent_ws.py`, `controller_ws.py` | unit-tested `test_relay_client.py::test_application_pings_and_pongs`, `relay.test.ts` "hello timeout and idle timeout…" |
| Reconnect backoff | agent 1 s → 60 s full jitter, indefinitely, except after 4001 (manual), 4003 and credential rejection (re-link); PWA 1 s → 30 s ±30 %; extension 1 s → 60 s with `chrome.alarms` | same | unit-tested |
| Per-PC in-flight depth | 16 (`DOME_RELAY_PER_PC_QUEUE_DEPTH`; the agent has the same `QUEUE_DEPTH`) → `QUEUE_FULL` | `router.py`, `queue.py` | integration-tested `test_queue_depth_and_rate_limit` |
| Per-controller command rate | plan limits from `plans.json`: 120/min burst 30 manual, 360/min burst 60 coalescable → `RATE_LIMITED` | `router.py RateLimiters` | integration-tested |
| Per-socket frame budget (controller) | 600/min, burst 120, checked before any DB work; after 120 refusals close 4000 + one `controller_throttled` event | `controller_ws.py` | integration-tested `test_controller_frame_flood_is_throttled_without_growing_security_events` |
| Security-event writes per controller socket | 20/min, then one `relay_events_throttled` row | `controller_ws.py` | same |
| Agent socket frame budget | **none** (`cloud-api/KNOWN_ISSUES.md` #1) | — | — |
| REST abuse limits | link start 10/h per IP; pairing claims 5 per 15 min per account and per IP; login 60/min per IP; agent token 30/min per IP; failed link-code look-ups 10 per 15 min per account and per IP | `settings.py`, `main.py` | integration-tested |
| Sweeper | every 5 s (`DOME_RELAY_SWEEP_INTERVAL_SECONDS`); deadline = `max(expires_at, received_at + timeout_ms) [+ 60 s for challenge actions] + 10 s`; armed power countdowns exempt until `fires_at + timeout_ms` | `lifecycle.py`, `manager.py` | integration-tested `test_in_flight_deadline_sweeper`, `test_power_countdown_is_exempt_from_deadline_until_fires_at` |
| Graceful shutdown | uvicorn `timeout_graceful_shutdown=20`; lifespan stops the sweeper, closes the OIDC client, disposes the pool | `cli.py`, `main.py` | code; shutdown under load **not yet verified** |
| Start-up sweep | rows in `created/accepted/executing/awaiting_confirmation` from a previous process → `outcome_unknown` (executing) or `expired`; logged as `startup_sweep` | `manager.py::startup_sweep` | integration-tested `test_startup_sweep_closes_rows_from_a_previous_process` |
| DB pool | 10 + 20 overflow, pre-ping, recycle 1800 s | `db/engine.py` | code |

**What a restart or deploy means for customers.** Every socket drops. Commands that had an
`executing` ack are reported to the phone as `outcome_unknown` (by the dying process if it gets to
run `agent.inflight_failed`, otherwise by the next process's start-up sweep when the phone asks);
earlier ones as `PC_OFFLINE`/`expired`. Nothing is queued or replayed. Agents are back within their
backoff (a fleet of N agents reconnects spread over up to 60 s), re-send any journaled result that
finished after their last acknowledged frame, and the relay forwards one correction per
`outcome_unknown` command (`rules.late_results`). Phones re-subscribe on reconnect and receive the
cached state again. Plan a deploy for a quiet hour anyway: a customer mid-countdown on a power action
will see the countdown continue on the PC (the agent owns it) but the phone's `power.cancel` cannot be
delivered until both are reconnected.

**Draining is not graceful beyond uvicorn's 20 s.** The process does not stop accepting new sockets
early, does not send a close code announcing a restart, and does not wait for in-flight commands to
finish. Those are candidate Phase D improvements.

## 10. Incident response outline (procedure, not yet run)

No status page, on-call rota or runbook automation exists; `docs/SECURITY.md` §6 records that the
only operator access today is direct database/host access by the founder, which should be treated as
the highest-privilege path and audited by the provider's own access logs. The outline below is the
starting point.

**Severities.** S1: service down or any suspected cross-account access/secret leak. S2: a feature
down for everyone (sign-in, pairing, relay routing). S3: degraded (elevated rejections, slow DB).
S4: single customer.

**First ten minutes (any severity).** `GET /healthz`; last `startup` time in the logs (crash loop?);
`oidc.*` and unhandled-exception counts; `relay.agents`/`controllers` trend; provider status for
Fly and the database; whether a deploy or secret change happened in the last hour. Write down the
timeline as you go.

**Specific situations.**

| Situation | Signals | Actions |
| --- | --- | --- |
| Database unavailable | `/healthz` 503 `database: unavailable`; every REST call fails `INTERNAL`; sockets may stay up but anything touching rows (commands, pairing) fails | Provider incident or credentials. Fix the database; restart the API afterwards if the pool does not recover (`pool_pre_ping` should). Customers see *Not delivered*/`INTERNAL`; nothing executed in the gap. |
| Identity provider down | `oidc.discovery_failed`/`token_exchange_failed`; customers get HTTP 503 at sign-in | Existing sessions (30 d idle) keep working, agents are unaffected. Wait or fail over the IdP; no DoMe change. |
| Crash loop after deploy | repeated `startup` or a configuration error on stderr (exit 2) | §6 rollback. If the error is the migration head check, see §4/§6. |
| Mass agent reconnect after restart | `startup_sweep rows > 0`, `agent_connected` burst, `agent.superseded` burst if customers also restarted agents | Expected; watch that `relay.agents` returns to its previous level within ~2 minutes. |
| Suspected account takeover (customer report or odd `pairing_*`/`controller_paired` events) | security events for the account | With the customer's consent: revoke their sessions (`UPDATE sessions SET revoked_at = now() WHERE account_id = …` — there is no operator UI; the customer can do it from More → Settings), revoke suspicious controllers (`controllers.revoked_at`, then the relay must be told: today only the REST path `DELETE /v1/controllers/{id}` pushes the snapshot, so prefer having the customer do it from Devices). Pairing requires the PC's local approval, so an attacker with the account alone cannot gain control (`docs/SECURITY.md` §2). |
| Leaked `DOME_SESSION_SECRET` | — | Rotate (§7): signs everyone out. Review `security_events` for sessions created since the suspected leak. |
| Leaked entitlement private key | — | Rotate (§7). Impact is limited to Pro gating on PCs (Phase C), never to command authority. |
| Leaked database dump | — | Contains public keys and hashes only; no command content, no private keys, no plaintext secrets (`docs/DATA_RETENTION.md`). Rotate the database password and the session secret; notify per the privacy notice draft; email addresses and PC names are the personal data at stake. |
| Compromised relay host | — | S1. The relay can read routed payloads in transit (not end-to-end encrypted, ADR-0001 D10) but cannot sign commands, enrol controllers or satisfy confirmations (`docs/SECURITY.md` §6). Rebuild the host from a clean image, rotate every secret in §7, review customer `security_events`, and tell customers that commands may have been observed in transit (titles are not persisted but were visible). |
| Abusive client / flood | `controller.throttled_close`, `command_rejected` per account, 429s | The in-process limiters hold for one process. For a sustained flood, block at the Fly proxy/firewall by IP (the limiters cannot ban). If it is an authenticated account, revoke its sessions and controllers as above. |
| Agent-side bug reported by many customers | `confirmation_required.invalid`, `ack.unknown_command`, `result.post_terminal_dropped` rising | Ask for diagnostics bundles (redacted). The agent version is in `pcs.agent_version` and `GET /v1/pcs`. There is no updater; a fix is a new download (Phase D). |

**After.** Blameless write-up within a week: timeline, customer impact (which error codes they saw
and for how long), root cause, the test that would have caught it, follow-ups with owners. Add the
scenario to this table.

## 11. Version compatibility (as implemented)

- Protocol `1.0`, registry `1` (`shared/protocol/version.json`). Every `hello` lists supported
  `MAJOR.MINOR` versions; a peer accepts a message iff its MAJOR is supported and its MINOR is ≤ the
  highest supported MINOR for that MAJOR; unknown fields are **rejected**; incompatible peers receive
  `PROTOCOL_INCOMPATIBLE` with both lists (**integration-tested** `test_hello_is_required_and_validated`,
  `test_minor_version_compat`, extension/agent both ways).
- Rollout order for an additive MINOR: relay first (it must understand both the old and the new
  frames), then the PWA (served by the relay, so it updates with it), then agents and the extension
  as customers update them. Agents cannot be force-updated (no updater yet, `docs/WINDOWS_INSTALL.md`
  §9); an agent with a lower MINOR keeps working by definition of the rule.
- A MAJOR bump is a coordinated migration with both MAJORs served for a long window; nothing like
  that is planned.
- `pcs.agent_version` and the `agent_version` in `GET /v1/pcs` show the fleet's versions; there is no
  equivalent for the extension (the agent could report it in `state` in a MINOR).
- At the time of writing a MINOR-level change is **in progress in the working tree**
  (`version.json` rules `controller_socket_identity` with a signed `hello_proof`, a stricter
  `late_results` re-send rule, `relay-frames.schema.json`, and a reworded `OUTCOME_UNKNOWN`
  message): the shared libraries, cloud-api, pc-agent and mobile-app have not been updated for it and
  the recorded test runs predate it. Do not deploy from a tree where `shared/protocol` and the
  components disagree; `shared/python` and `shared/ts` fixtures (`make fixtures`) plus every suite
  must pass first.

## 12. Pre-production checklist (spec §15/§18 D) — none of it done

- [ ] Container image and `fly.toml` written, built and deployed to **staging** (§2–§3); `DOME_TRUSTED_PROXIES` verified with `ss -tn`
- [ ] Production OIDC application registered; sign-in tested from an iPhone over the real origin
- [ ] Encryption at rest and backup retention confirmed on the database vendor's page and recorded in `docs/DATA_RETENTION.md`
- [ ] Nightly logical dump to a second location configured; **restore rehearsed** on staging and timed (§5)
- [ ] **Failed-deployment recovery rehearsed** on staging (§6)
- [ ] Secret rotation rehearsed for the session secret and the entitlement key (§7)
- [ ] External `/healthz` monitor and the first alerts from §8 configured; log shipping with a retention of 30 days or the documented alternative
- [ ] Retention purge job implemented (`docs/DATA_RETENTION.md` §4) or table growth accepted and monitored
- [ ] Load smoke run against staging from a separate machine; sustained idle/reconnect test (24 h) recorded in `docs/ACCEPTANCE.md` Scenario 17
- [ ] Agent socket frame budget (`cloud-api/KNOWN_ISSUES.md` #1) implemented or explicitly accepted
- [ ] CI workflow exists and runs every suite on every push (currently none)
- [ ] Staging and production separated: apps, databases, OIDC clients, entitlement keys, agents never cross
- [ ] `docs/SECURITY.md` §6 operator boundaries re-read; the operator interface (Phase C/D) or a written manual-access procedure with audit in place
- [ ] Incident contacts and the write-up template agreed (§10)

## Evidence summary for this document

| Claim group | Tag |
| --- | --- |
| Settings validation, start-up migration and head check, start-up sweep, `/healthz`, limits in §9, log event names and security-event kinds | code read; integration-tested where a test is named |
| Reconnect/ping/idle behaviour of the agent, PWA and extension | unit-tested against fakes |
| Everything about Fly.io, images, backups, restore, rollback, rotation procedures, incident handling | not yet verified (procedure, never run) |
| Behaviour of the hosting provider's proxy, idle timeouts, `kill_timeout` semantics, encryption at rest | not yet verified; confirm against the vendor's current documentation at first deployment |
