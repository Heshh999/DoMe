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
health, security headers/CSP, static PWA serving, structlog JSON with redaction. Stripe endpoints
(Phase C) are **not** implemented; their tables exist in migration 0001 so the schema is complete.

Protocol 1.1 (spec §10A, §11A): the signed manual-input stream (`input_batch` routing, `input_ack` /
`input_session` delivery), `grant_update` from the PC owner, pairing with the `pointer` / `keyboard`
capabilities, and support tickets (migration 0002). See "Manual input (protocol 1.1)" and "Support
tickets" below. Evidence tags follow spec §17: everything here is **integration-tested** against a real
PostgreSQL and simulated agents/phones; nothing in this component is Windows-device-tested or
iPhone-tested, and the relay cannot verify that a batch it forwarded was injected on the PC.

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
| `DOME_PUBLIC_ORIGIN` | — (required) | Exact origin the PWA is served from. Used for Origin checks, cookies, QR/link URLs and as the OIDC redirect base. |
| `DOME_EXTRA_ORIGINS` | `` | Comma-separated additional exact origins (e.g. the Vite dev server). |
| `DOME_API_BIND` | `127.0.0.1:8000` | Listen address. |
| `DOME_TRUSTED_PROXIES` | `` (development/test) | Comma-separated IP addresses or CIDR networks of the reverse proxy whose `X-Forwarded-For` / `X-Forwarded-Proto` are honoured, or `none` when clients reach the process directly. **Required in `staging` and `production`**; `*` is refused. See "Client addresses behind a proxy". |
| `DOME_DATABASE_URL` | — (required) | `postgresql+psycopg://user@host:port/db` (unix socket: `postgresql+psycopg://dome@/dome_dev?host=/tmp&port=54329`). |
| `DOME_SESSION_SECRET` | — (required, ≥16 chars) | HMAC key for session-cookie ids and IP hashes. Rotating it signs everyone out. |
| `DOME_SESSION_IDLE_DAYS` / `DOME_SESSION_ABSOLUTE_DAYS` | `30` / `90` | Sliding idle expiry and absolute cap of web sessions. |
| `DOME_OIDC_ISSUER` | — (required) | OIDC issuer URL (discovery at `/.well-known/openid-configuration`). Auth0, Keycloak or `tools/dev-idp`. |
| `DOME_OIDC_CLIENT_ID` / `DOME_OIDC_CLIENT_SECRET` | — (required) | Relying-party credentials. |
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
| `DOME_STRIPE_*`, `DOME_AI_*` | blank / false | Declared for Phase C/E; billing endpoints are not implemented yet (`GET /v1/plans` reports `billing_enabled: false`). |

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
- behind an ingress proxy (ADR-0001 D2 names Fly.io): set the address range the proxy connects *from*
  as seen by this process (on Fly.io the proxy reaches the VM over the private 6PN network; verify the
  range with `fly ssh console` + `ss -tn` before trusting it). Example: `DOME_TRUSTED_PROXIES=fdaa::/16`.
- a process reached directly over TLS: `DOME_TRUSTED_PROXIES=none`.

Staging and production refuse to start without an explicit value.

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
uv run ruff check . && uv run ruff format --check .
uv run mypy dome_api tests
```

Current counts (2026-10-09, protocol 1.1 build after the review fixes): **117 passed** (89 from the 1.0 build,
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
  `payload`, `sig`, `title`, `email`, `text` (typed keyboard content) and anything ending in `_token/_secret/_code/_credential/_key`
  are redacted; URLs are logged as paths only. Input batches are never logged (not even on rejection
  beyond the error code), so typed text exists only in transit through this process.
- Migrations: `0001_initial`, `0002_support_tickets`. `dome-api` applies them at start-up.
- Request log lines carry the matched route template and a masked path: a device-link `user_code`
  never appears (`/v1/agent-link/{user_code}/approve`); query strings are never logged.
- Security headers: restrictive CSP (`form-action` allows the OIDC issuer), `Referrer-Policy: same-origin`,
  `X-Content-Type-Options: nosniff`, `Permissions-Policy: camera=(self)`, HSTS in production,
  `Cache-Control: no-store` on `/v1`, `/ws` and `/healthz`.
