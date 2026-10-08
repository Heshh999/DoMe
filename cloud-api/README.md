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
| `DOME_RELAY_URL` / `DOME_API_URL` | derived from the public origin | URLs handed to the agent at link time (`wss://…/ws/agent`, `https://…`). |
| `DOME_PC_ACCESS_TOKEN_SECONDS` | `3600` | Lifetime of PC access tokens. |
| `DOME_RATE_LINK_START_PER_HOUR` | `10` | `POST /v1/agent-link/start` per client IP. |
| `DOME_RATE_PAIRING_CLAIM_PER_ACCOUNT` / `DOME_RATE_PAIRING_CLAIM_PER_IP` | `5` / `5` | Pairing claims per 15 minutes (each failure is a `pairing_failed` security event). |
| `DOME_RATE_LOGIN_PER_MINUTE` / `DOME_RATE_AGENT_TOKEN_PER_MINUTE` | `60` / `30` | Login starts and credential→token exchanges per client IP. |
| `DOME_STATIC_DIR` | unset | Built PWA directory to serve at `/` (SPA fallback to `index.html`). |
| `DOME_LOG_LEVEL` | `INFO` | structlog level. JSON output except in `development`. |
| `DOME_STRIPE_*`, `DOME_AI_*` | blank / false | Declared for Phase C/E; billing endpoints are not implemented yet (`GET /v1/plans` reports `billing_enabled: false`). |

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
uv run pytest -q                 # creates dome_test_<random>, runs alembic upgrade head, drops it afterwards
uv run ruff check . && uv run ruff format --check .
uv run mypy dome_api
```

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
  `payload`, `sig`, `title`, `email` and anything ending in `_token/_secret/_code/_credential/_key`
  are redacted; URLs are logged as paths only.
- Security headers: restrictive CSP (`form-action` allows the OIDC issuer), `Referrer-Policy: no-referrer`,
  `X-Content-Type-Options: nosniff`, `Permissions-Policy: camera=(self)`, HSTS in production,
  `Cache-Control: no-store` on `/v1`, `/ws` and `/healthz`.
