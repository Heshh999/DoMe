# cloud-api design

Package `dome_api` (Python 3.12, FastAPI, SQLAlchemy 2 async + psycopg 3, Alembic, Pydantic v2,
Authlib, joserfc, structlog). One process serves REST (`/v1`), both WebSocket endpoints (`/ws`)
and, when `DOME_STATIC_DIR` is set, the built PWA. PostgreSQL is the only datastore.

Everything here follows ADR-0001 and the contract in `shared/protocol`. The shared library
`dome-protocol` (path dependency `../shared/python`) does all signature verification, strict
parsing, registry and frame validation; the API never re-implements those.

## Modules

```
dome_api/
  main.py            create_app(), lifespan (migrations check, JWKS, connection manager)
  settings.py        pydantic-settings; every DOME_* variable from .env.example; fail fast
  db/                engine/session, models (SQLAlchemy 2.0 typed), alembic/
  auth/              oidc.py (Authlib RP: discovery, PKCE, state/nonce in auth_flows table),
                     sessions.py (server-side sessions, cookie, CSRF), deps.py (current_account)
  security/          origin.py (exact Origin checks), ratelimit.py (token buckets), headers.py (CSP etc),
                     events.py (security_events writer), tokens.py (opaque token hashing)
  plans.py           loads shared/protocol/plans.json; the ONLY place plan limits are read
  entitlements.py    plan state per account -> entitlement; EdDSA assertion issuance; JWKS
  routes/            auth.py, account.py, pcs.py, agent_link.py, agent.py, pairing.py,
                     controllers.py, grants.py, commands.py, plans.py, health.py
  relay/             manager.py (bounded ConnectionManager), agent_ws.py, controller_ws.py,
                     router.py (command routing rules), lifecycle.py (command table updates, sweeper)
  billing/           (Phase C) stripe_client.py, webhooks.py, state_machine.py, reconcile.py
  static.py          serves PWA dist with CSP; SPA fallback; never caches /v1 or /ws
```

## Data model (Alembic migration 0001)

All account-owned tables carry `account_id` and every query is scoped by it; there is no
"admin fetch by id" helper without an account filter.

- `accounts(id uuid pk, issuer, subject, email, display_name, plan text default 'free', created_at, deleted_at)` unique(issuer, subject)
- `sessions(id uuid pk, account_id fk, token_hash bytea unique, csrf_token, created_at, last_seen_at, expires_at, user_agent_hash, revoked_at)`
- `auth_flows(id uuid pk, state text unique, nonce, code_verifier, return_to, created_at, expires_at)`
- `pcs(id uuid pk, account_id, name, public_jwk jsonb, kid text unique, enabled bool, remote_enabled_reported bool, platform, agent_version, created_at, last_seen_at, last_power_request jsonb, deleted_at)`
- `pc_credentials(id uuid pk, pc_id, credential_hash bytea unique, created_at, last_used_at, revoked_at)`
- `pc_access_tokens(id uuid pk, pc_id, token_hash bytea unique, expires_at, revoked_at)`
- `device_link_codes(id uuid pk, device_code_hash unique, user_code text unique, pc_public_jwk jsonb, kid, agent_version, platform, state enum(pending, approved, denied, consumed, expired), account_id null, pc_name, created_at, expires_at, consumed_at, requester_ip_hash)`
- `controllers(id uuid pk, account_id, kid text, public_jwk jsonb, display_name, created_at, last_seen_at, revoked_at)` unique(account_id, kid)
- `grants(id uuid pk, account_id, controller_id, pc_id, capabilities text[], created_at, revoked_at)` unique(controller_id, pc_id) where revoked_at is null
- `pairing_sessions(id uuid pk, account_id, pc_id, code_hash bytea unique, state enum(open, claimed, approved, declined, expired), controller_kid, controller_public_jwk, controller_display_name, requested_capabilities text[], controller_id null, grant_id null, attempts int, created_at, expires_at, decided_at)`
- `commands(id uuid pk, account_id, controller_id, pc_id, action, digest text, state lifecycle_state, error_code text null, created_at, acked_at, finished_at, duration_ms)` — **no params, no results, no titles** are stored.
- `security_events(id bigserial pk, account_id null, kind text, severity, actor enum(account, pc, controller, system), subject_id uuid null, detail jsonb (redacted, no secrets/titles), ip_hash, created_at)`
- Phase C: `subscriptions`, `billing_events(provider_event_id unique, received_at, processed_at, status, error)`, `usage_periods`, `layouts`, `routines`, `support_diagnostics`, `pending_deletions`, `operator_users`, `operator_audit`.

## Authentication

- `GET /v1/auth/login?return_to=/path` → creates `auth_flows` row (state, nonce, PKCE verifier),
  redirects to the issuer's authorization endpoint (Authlib `AsyncOAuth2Client`, discovery
  cached). `return_to` must be a relative path.
- `GET /v1/auth/callback?code&state` → exchanges code with PKCE, validates the ID token
  (issuer, audience, nonce, exp, signature via JWKS, `authlib.jose`), upserts account by
  (issuer, subject), creates a session, sets cookie `dome_session` (`HttpOnly; Secure` unless
  `DOME_ENV=development` and origin is http; `SameSite=Lax; Path=/`), redirects to `return_to`.
- `GET /v1/session` → `{account:{id,email,display_name,plan}, csrf_token, limits}`; 401 when none.
- `POST /v1/auth/logout` → revokes the session, closes its controller sockets, clears cookie.
- CSRF: every non-GET `/v1` request with a session must send `X-DoMe-CSRF` equal to the session's
  token **and** an `Origin` header exactly equal to `DOME_PUBLIC_ORIGIN` (or `DOME_EXTRA_ORIGINS`).
  Agent endpoints authenticate with bearer tokens and are exempt from CSRF but check `Origin` is absent.
- Session ids are random 32 bytes; only the SHA-256 is stored. Sliding idle expiry
  `DOME_SESSION_IDLE_DAYS`, absolute max 90 days.

## PC linking (device-code shape)

- `POST /v1/agent-link/start` body `{pc_public_jwk, agent_version, platform}` (no auth,
  rate-limited per IP: 10/hour) → `{device_code (43 chars urlsafe), user_code ("ABCD-EFGH",
  Crockford base32 without I,L,O,U), verification_uri_complete:"<origin>/link?user_code=…",
  expires_in: 600, interval: 5}`. Only `SHA-256(device_code)` is stored.
- `GET /v1/agent-link/{user_code}` (session) → `{agent_version, platform, kid, expires_at}` for the approval page.
- `POST /v1/agent-link/{user_code}/approve` (session + CSRF) body `{pc_name, remote_enabled: true}`
  → in ONE transaction: `SELECT … FOR UPDATE` the account row, count enabled PCs, compare to
  `plans.max_enabled_pcs` → if exceeded create the PC with `enabled=false` and return
  `{pc_id, enabled:false, reason:"DEVICE_LIMIT_REACHED"}`; otherwise `enabled=true`. Marks the code approved.
- `POST /v1/agent-link/{user_code}/deny` (session + CSRF).
- `POST /v1/agent-link/poll` body `{device_code}` → 428 `{status:"authorization_pending"}` /
  200 `{pc_id, account_id, pc_credential, relay_url, api_url}` exactly once (state→consumed) /
  410 denied/expired. `pc_credential` is random 32 bytes; only its hash is stored.
- `POST /v1/agent/token` body `{pc_credential}` → `{access_token, expires_in: 3600, pc_id, account_id}`.
  Revoked/unlinked credential → 401 and the agent must re-link.
- `POST /v1/agent/entitlement` (PC bearer) → `{assertion}` (Pro) or `{assertion:null, plan:"free"}`.

## Pairing (contract: `version.json` rules `pairing_secret`, `pairing_offline`)

- `POST /v1/pairing/start` (PC bearer) body `{code_hash}` → `{pairing_id, expires_at}`. The **PC**
  generated the 20-symbol code and shows it/QR (`<origin>/pair#code=…`); the backend stores only
  `code_hash` (unique while open) and never sees the code. One open session per PC (a new start
  expires the old one). Expiry 5 min.
- `POST /v1/pairing/claim` (session + CSRF, rate-limited 5 attempts / 15 min per account and per
  IP; every failure writes a `pairing_failed` security event) body `{code_hash, public_jwk,
  display_name, requested_capabilities}` → finds the open session by `code_hash` **within the same
  account** (anything else is `PAIRING_CODE_INVALID`, indistinguishable from a wrong code), derives
  `kid`, enforces `plans.max_controllers` transactionally (new kid only), stores the claim
  (state `claimed`) and returns **202** `pairing_status_response`. If the agent is online the relay
  sends `pairing_request{pairing_id, code_hash, display_name, public_jwk, kid, requested_capabilities,
  expires_at}` now; otherwise it is delivered after `grants_snapshot` on the agent's next connect
  (the relay re-sends every unexpired claimed session for that PC on each connect). The phone
  computes the verification code locally with the code it holds and polls.
- Agent replies `pairing_decision`. On approve with matching `kid`: upsert `controllers` (account,
  kid), insert `grants` with `granted_capabilities ∩ requested_capabilities`, state `approved`,
  security event `controller_paired`, push a fresh `grants_snapshot` to the agent.
- `GET /v1/pairing/{pairing_id}` (session) → `pairing_status_response`.

## Inventory and revocation

- `GET /v1/pcs`, `PATCH /v1/pcs/{id}` `{name?, enabled?}` (enable is limit-checked
  transactionally), `DELETE /v1/pcs/{id}` → revoke credentials/tokens, revoke grants, close
  socket, soft-delete.
- `GET /v1/controllers`, `PATCH /v1/controllers/{id}` `{display_name}`, `DELETE /v1/controllers/{id}`
  → revoke all its grants, send `revoked` to its sockets, close them, push snapshots to affected agents.
- `GET /v1/pcs/{id}/grants`, `DELETE /v1/grants/{id}` → same, scoped to one PC.
- `GET /v1/account/security-events?limit=` and `GET /v1/account/sessions`, `DELETE /v1/account/sessions/{id}`.
- `GET /v1/commands?pc_id&limit=20` → lifecycle rows (no content).

## Relay

`ConnectionManager` (single process): `agents: dict[pc_id, AgentConn]`, `controllers:
dict[conn_id, ControllerConn]`, `subs: dict[pc_id, set[conn_id]]`, global cap
`DOME_RELAY_MAX_CONNECTIONS` (503 on upgrade when full). One agent socket per PC; a new one
supersedes the old (close code 4001). Frames > `max_frame_bytes` close the socket (1009).
Every inbound frame is validated with `schemas.validate_frame(direction, …)` before anything else.

`/ws/agent` — `Authorization: Bearer <pc access token>` (header; query-string tokens rejected),
`Origin` must be absent. Sequence: `hello` → `hello_ack{pc_id}` → `grants_snapshot` → mark PC
online, broadcast `pc_status(online)` to subscribers in the same account.

`/ws/controller` — cookie session + exact `Origin`. Sequence: `hello{kid}` → the relay resolves
(session account, kid) → controller row; if found and not revoked the socket is **bound** to it and
`hello_ack{controller_id}` says so; otherwise `hello_ack` without `controller_id` and the socket
may not subscribe/command/confirm/cancel (`error GRANT_MISSING`). Every envelope on the socket must
carry the socket's kid (else `UNKNOWN_KEY`, close 4003). `subscribe{pc_ids}` is idempotent and
replaces the socket's set; each id must be a PC of the account on which **this controller** holds a
live grant, else `error{GRANT_MISSING, ref_pc_id}`. For each accepted id the relay sends `pc_status`
and then the cached last `state` frame of that PC (unchanged, original `at`) when one exists.

Command routing (`relay/router.py`), in this order; each failure is answered with
`result{origin:"relay", state:"failed", error}` (never an `error` frame — every command_id ends
with exactly one `result`) and recorded as a `command_rejected` security event:
1. frame schema; 2. envelope `kid == socket kid`, controller not revoked and `status == active`
   (else `UNKNOWN_KEY` / `CONTROLLER_REVOKED` / `CONTROLLER_PLAN_DISABLED`);
3. `verify_and_parse_command` with a `KeyRecord` resolver (signature, strict parse, schema,
   controller/account binding, window, registry); 4. `payload.target_pc_id == frame.pc_id`;
5. PC exists in account, not deleted, `enabled` (else `PC_PLAN_DISABLED`); 6. grant (controller, pc)
   exists and includes `spec.capability`; 7. token bucket per controller — `manual_command_rate_limit`
   for ordinary actions, `coalescable_command_rate_limit` for actions with `coalesce`;
8. agent online else `PC_OFFLINE` (**never queued**); 9. in-flight count for the PC
   (`created|accepted|executing|awaiting_confirmation`) < `per_pc_queue_depth` else `QUEUE_FULL`;
10. insert `commands` row (state `created`, deadline per `rules.in_flight`), forward
    `{type:"command", envelope, relay:{received_at, connection_id}}` to the agent.
Agent frames `ack` / `confirmation_required` / `result` are matched by `command_id` to the
originating controller connection (fallback: all sockets bound to that controller), update the
`commands` row, and are forwarded verbatim — `challenge_text` is validated with
`schemas.validate_challenge_text` on a copy and the original string is forwarded untouched.
`state` frames go to all subscribers and refresh the per-PC cache. `confirmation` and `cancel`
frames are accepted only from the socket bound to the command's controller. On agent disconnect:
PC offline → `pc_status`; every in-flight command becomes `result{origin:relay, outcome_unknown}`
(if an `executing` ack was seen) or `result{origin:relay, failed, PC_OFFLINE}`. A sweeper every 5 s
applies the `rules.in_flight` deadline. A late agent `result` for a command the relay closed as
`outcome_unknown` is forwarded once as a correction (`rules.late_results`); anything else
post-terminal is dropped and logged. `revoke_controller` from an agent revokes the grant exactly as
the REST path does.

Nothing in the relay executes an action, and nothing stores a payload body, result or title.

## Entitlements

`plan` on the account (Phase A/B: `free`, settable only via the Phase C billing state machine).
`entitlements.assertion(account, pc)` → compact JWS per `schemas/entitlement.schema.json`
(`alg=EdDSA`, header `kid`, `typ=dome-entitlement+jwt`, 1 h). Public key at
`GET /.well-known/dome-jwks.json`. Returned by `POST /v1/agent/entitlement` (`agent_entitlement_response`)
and included in every `grants_snapshot` for Pro; a fresh snapshot is pushed on plan change.
`grants_snapshot.pc_enabled` and per-controller `status` carry the plan/account device state.

## Security headers and static serving

`Content-Security-Policy: default-src 'self'; connect-src 'self' wss: https:; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self' <issuer>`,
`Referrer-Policy: same-origin`, `X-Content-Type-Options: nosniff`, `Permissions-Policy: camera=(self)`
(QR scanning), HSTS in production. `/v1` and `/ws` responses carry `Cache-Control: no-store`.

## Logging

structlog JSON; a processor redacts keys named `token, secret, code, device_code, pc_credential,
cookie, authorization, payload, sig, title, challenge`; URLs logged as path only.

## Tests (pytest, real PostgreSQL)

`DOME_TEST_DATABASE_URL` (default `postgresql+psycopg://dome@/dome_test?host=/tmp&port=54329`).
Session fixture: create a fresh database `dome_test_<random>`, run `alembic upgrade head`, drop
at the end. App runs under uvicorn in-process on a free port; HTTP via httpx, sockets via
`websockets`; `tools/dev-idp` runs on another free port for real OIDC login
(`dev_user=` non-interactive path). Required coverage: login/session/CSRF/origin; device link
happy path, denial, expiry, limit; pairing happy path with a Python-signed controller key,
wrong account, expired, reuse, rate limit; grants snapshot content; command routing acceptance
and each rejection rule; two accounts cannot subscribe to or command each other's PC; revocation
closes sockets and updates snapshots; agent disconnect → `outcome_unknown` / `PC_OFFLINE`;
queue depth; in-flight deadline; frame size; entitlement assertion verifies against the JWKS; every REST response validates against `rest.schema.json`; pairing claim while the PC is offline is delivered on reconnect; hello without a known kid cannot subscribe.
