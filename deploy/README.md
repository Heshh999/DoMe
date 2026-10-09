# deploy — container image, Fly.io configuration, Docker Compose, CI

How DoMe's cloud side (API + relay + PWA, one process, one PostgreSQL) is built into an image and
run in **development**, **staging** and **production**, what the founder has to set up before the
first deployment, and the operational procedures the product spec requires (migrations, rollback,
backup/restore, secret rotation, incident basics). ADR-0001 D1/D2 are the decisions behind it.

> **Status (2026-10-09): nothing in this directory has been deployed.** This repository was built
> on a machine **without a Docker daemon**, so the image has never been built or run, the Compose
> stack has never been brought up, and the Fly.io configuration has never been applied. What was
> verified is listed in [What was actually verified](#what-was-actually-verified); everything else
> carries the tag **not yet verified**. The first real deployment is step 3 of `docs/HANDOFF.md`.

| File | Purpose |
| --- | --- |
| `Dockerfile` | Multi-stage image: build the PWA with pnpm, install cloud-api with `uv`, run `dome-api` (uvicorn) as a non-root user with `DOME_STATIC_DIR` pointing at the built PWA; HEALTHCHECK on `/healthz`. Build context is the **repository root**. |
| `Dockerfile.dockerignore` | Keeps `.git`, `node_modules`, `.venv`, `.env*`, keys and the components the image does not need out of the context. |
| `entrypoint.sh` | Turns the hosting platform's environment-only secrets into what the API wants: writes the Ed25519 signing key from `DOME_ENTITLEMENT_SIGNING_KEY_PEM_B64` to a 0600 file; derives `DOME_DATABASE_URL` from a `DATABASE_URL`. Generates a key **only** in `DOME_ENV=development`. |
| `fly.toml` | Fly.io app configuration (persistent-WebSocket friendly: machines never auto-stop, connection-based concurrency, 30 s drain, `/healthz` check, release command for migrations). Placeholders marked; no secrets. |
| `docker-compose.dev.yml` | PostgreSQL 16 + `tools/dev-idp` + the API image for machines that have Docker. |
| `../.github/workflows/ci.yml` | GitHub Actions: `python`, `node`, `integration` and `container` jobs with lockfile-keyed caches, SHA-pinned actions, no secrets. |

Related: `cloud-api/README.md` (every `DOME_*` variable and the proxy-trust rules),
`docs/ARCHITECTURE.md`, `docs/SECURITY.md`, `docs/DATA_RETENTION.md`, `docs/COST_MODEL.md`.
`.env.example` mentions a `docs/OPERATIONS.md` that does not exist yet; the operational procedures
live in this file until it is written.

## The image

```
pwa        node:22-bookworm-slim + pnpm 10.28.0   shared/ts + mobile-app  ->  pnpm build  ->  dist/
api-build  python:3.12-slim-bookworm + uv 0.11.32 cloud-api (+ ../shared/python, ../shared/protocol)
           uv sync --locked --no-dev            ->  /app/cloud-api/.venv
runtime    python:3.12-slim-bookworm, user dome (uid 10001), /app/cloud-api/.venv + /app/static
           ENTRYPOINT deploy/entrypoint.sh, CMD dome-api, EXPOSE 8080, HEALTHCHECK GET /healthz
```

- `uv sync --locked` and `pnpm install --frozen-lockfile` fail the build if a lockfile is out of
  date instead of resolving something new. The `uv` and `pnpm` versions in the image are the ones
  that produced the lockfiles in this repository.
- The API serves the PWA from the same origin (`/` static, `/v1` REST, `/ws` sockets), so there is
  no CORS configuration and no second deployable. API and PWA are always the same commit.
- Build arguments: `VITE_DOME_RELEASE_CHANNEL` (default `staging`), `VITE_DOME_SUPPORT_URL`,
  `VITE_DOME_API_ORIGIN` (leave empty: same origin). They are public, build-time values.
- The container does not run as root, has no shell entry other than `entrypoint.sh`, and contains
  no secret. Keys arrive at start-up through the environment and are written to `/app/run` (0600).
- `CMD dome-api` runs `alembic upgrade head` and then serves. Fly runs the migration once as a
  release command and starts the process with `--skip-migrations` (start-up still refuses to run
  unless the schema is at head; see `cloud-api/README.md` "Operational notes").

Build and run locally (needs a Docker daemon; **not yet verified** — see status above):

```sh
docker build -f deploy/Dockerfile -t dome-api:local .          # from the repository root
docker run --rm -p 8080:8080 \
  -e DOME_ENV=development -e DOME_PUBLIC_ORIGIN=http://localhost:8080 \
  -e DOME_DATABASE_URL=postgresql+psycopg://dome@host.docker.internal:54329/dome_dev \
  -e DOME_SESSION_SECRET=local-only-not-a-real-secret-0123456789 \
  -e DOME_OIDC_ISSUER=http://dev-idp:8081 -e DOME_OIDC_CLIENT_ID=dome-dev -e DOME_OIDC_CLIENT_SECRET=dome-dev-secret \
  dome-api:local
```

## Docker Compose (development)

This repository was developed without Docker; `make db-start db-create`, `make dev-idp`, `make api`
and `make pwa` (root `README.md`) are the exercised path. The Compose file is for machines that do
have Docker and was validated with `docker compose config` only.

1. Add `127.0.0.1 dev-idp` to your hosts file. The browser and the API must address the OIDC
   issuer by the same URL (`http://dev-idp:8081`); inside the Compose network that name resolves to
   the issuer container, on your machine it must resolve to the published port.
2. `docker compose -f deploy/docker-compose.dev.yml up --build`
3. Open `http://localhost:8080`, sign in as `alice@example.test` or `bob@example.test` (the
   development issuer accepts any typed email; it is the only identity provider in this stack).

The API container generates a development entitlement signing key on first start and keeps it in
the `api-run` volume; PostgreSQL is published on `localhost:54329` (the Makefile's port) so the
pytest suites can use it with
`DOME_TEST_DATABASE_URL=postgresql+psycopg://dome:dome@localhost:54329/dome_test`.
The PC agent runs on the host, not in a container (`make agent`, `DOME_AGENT_API_URL=http://localhost:8080`).

## Environments

One Fly app, one database, one OIDC client, one signing key and one origin **per environment**.
Nothing is shared between them; a secret that exists in two environments is a mistake.

| | development | staging | production |
| --- | --- | --- | --- |
| Where | developer machine (Makefile) or Compose | Fly app `dome-api-staging` (placeholder name) | Fly app `dome-api-prod` (placeholder name) |
| `DOME_ENV` | `development` | `staging` | `production` |
| Identity provider | `tools/dev-idp` (never deployed) | the real provider, a **separate** application/client ("DoMe staging") | the real provider, production client |
| Origin | `http://localhost:5173` (Vite) or `:8080` | `https://<app>.fly.dev`, later `https://staging.<domain>` | `https://<domain>` |
| TLS / HSTS | plain http | Fly-terminated TLS | Fly-terminated TLS; `production` enables HSTS |
| `DOME_TRUSTED_PROXIES` | unset (TCP peer is the client) | **required**: the Fly proxy's source range, verified on the machine | same, verified separately |
| Entitlement key | generated locally (`make`/entrypoint) | generated once, stored only in Fly secrets | generated once, stored only in Fly secrets; never the staging key |
| Stripe | none (Phase C not implemented) | test-mode keys only, when Phase C exists | live keys only after the founder's go-live decision |
| Data | throwaway | throwaway; may be reset | retained per `docs/DATA_RETENTION.md` |

Code paths that differ by `DOME_ENV` are in `cloud-api/dome_api/settings.py`: production requires
https origins and issuer and a non-placeholder session secret; staging and production require an
explicit `DOME_TRUSTED_PROXIES`; outside production every REST response is also validated against
`rest.schema.json` (a contract violation becomes a loud 500 in staging, which is intended).

## What the founder must set up (blocked on accounts, not on code)

1. **Domain.** A domain for the product origin (for example `app.<domain>` for the PWA/API and the
   same host for `wss://`), DNS at a registrar, and `fly certs add <host>` once the app exists.
   Until then the `*.fly.dev` origin works for staging.
2. **OpenID Connect provider** (ADR-0001 D3: Auth0 or Keycloak; any compliant issuer). Create one
   application per environment with: Authorization Code flow with PKCE, client secret enabled
   (the backend is a confidential client), allowed callback URL exactly
   `<DOME_PUBLIC_ORIGIN>/v1/auth/callback`, allowed logout/return URL `<DOME_PUBLIC_ORIGIN>/`,
   scopes `openid email profile`. Note the issuer URL (Auth0: `https://<tenant>.<region>.auth0.com`;
   Keycloak: `https://<host>/realms/<realm>`), client id and client secret.
   Only `tools/dev-idp` has been exercised; the first login against a real provider is **not yet
   verified** and is the first thing to test on staging.
3. **Entitlement signing key** (Ed25519, PKCS8 PEM), one per environment:
   `openssl genpkey -algorithm ed25519 -out entitlement-ed25519.pem` or
   `cd cloud-api && uv run dome-api-gen-entitlement-key entitlement-ed25519.pem`. Store it only in
   the environment's secret store (below) and delete the local file; it never enters the repository.
4. **Fly.io account**, the `fly` CLI, and a PostgreSQL for each environment. The Fly PostgreSQL
   offerings (unmanaged cluster vs. Managed Postgres) differ in backups and in how `DATABASE_URL`
   is injected; pick one, read its current backup/restore documentation, and record the choice in
   `docs/COST_MODEL.md`.
5. **Stripe** — only when Phase C is implemented. Nothing in the code calls Stripe today;
   `DOME_STRIPE_*` are declared and ignored, `GET /v1/plans` reports `billing_enabled: false`.
   When the time comes: test-mode secret key, a webhook endpoint secret, and the two price ids,
   test mode first, live mode only after a deliberate go-live.
6. **Windows code signing, Chrome Web Store, support mailbox** are release prerequisites outside
   this directory (`docs/SECURITY.md` T12, `docs/HANDOFF.md`).

## Secrets and configuration (names only — values never enter this repository)

Set with `fly secrets set --app <app> NAME=value` (secrets override `[env]` values of the same name).

| Secret | Used for | Rotation effect |
| --- | --- | --- |
| `DOME_SESSION_SECRET` | HMAC key for session-cookie ids and `ip_hash` values; at least 16 characters, use 32+ random bytes | every web session is signed out; the PWA redirects to sign-in |
| `DOME_OIDC_CLIENT_SECRET` | the relying-party credential at the identity provider | rotate at the provider first (most allow two active secrets), then here |
| `DOME_ENTITLEMENT_SIGNING_KEY_PEM_B64` | `base64 -w0 entitlement-ed25519.pem`; written to `DOME_ENTITLEMENT_SIGNING_KEY_PEM_PATH` at start | agents re-fetch the JWKS and refresh assertions on their next connect; the restart that activates the new key disconnects them, so refresh follows immediately. Pro-only gates fall back to Free until then; no Free function is affected |
| `DATABASE_URL` or `DOME_DATABASE_URL` | PostgreSQL connection (`fly postgres attach` injects `DATABASE_URL`; the entrypoint converts it to the `postgresql+psycopg://` form) | rotate the database role password, then the secret, then restart |
| `DOME_TRUSTED_PROXIES` | not secret, but environment-specific and mandatory in staging/production; may live in `[env]` once verified | none |
| `DOME_STRIPE_SECRET_KEY`, `DOME_STRIPE_WEBHOOK_SECRET` | Phase C only; unused today | — |
| `DOME_AI_API_KEY` | Phase E only; unused today | — |

Non-secret configuration lives in `fly.toml` `[env]` (`DOME_ENV`, `DOME_PUBLIC_ORIGIN`,
`DOME_OIDC_ISSUER`, `DOME_OIDC_CLIENT_ID`, relay limits, log level) and is documented variable by
variable in `cloud-api/README.md`. Logs are JSON on stderr with redaction of tokens, credentials,
challenge text, payloads, titles and emails; URLs are logged as paths only.

## Deploying to Fly.io (staging first)

Commands follow the Fly CLI as documented in the Fly configuration reference read on 2026-10-09;
**none of this has been run** — compare with the current `fly help` output while doing it.

```sh
fly auth login
fly apps create dome-api-staging                              # match `app` in deploy/fly.toml
fly postgres create --name dome-pg-staging --region ams       # or Managed Postgres; see founder step 4
fly postgres attach dome-pg-staging --app dome-api-staging    # injects DATABASE_URL as a secret

# edit deploy/fly.toml placeholders: app, primary_region, DOME_PUBLIC_ORIGIN, DOME_OIDC_ISSUER, DOME_OIDC_CLIENT_ID

fly secrets set --app dome-api-staging \
  DOME_SESSION_SECRET="$(openssl rand -base64 48)" \
  DOME_OIDC_CLIENT_SECRET="<from the identity provider>" \
  DOME_ENTITLEMENT_SIGNING_KEY_PEM_B64="$(base64 -w0 entitlement-ed25519.pem)"

# First deploy: expected to FAIL at start-up with
#   "staging requires DOME_TRUSTED_PROXIES ..."
# That is the API refusing to guess which addresses may set X-Forwarded-For.
fly deploy --config deploy/fly.toml --dockerfile deploy/Dockerfile .

# Find the address the Fly proxy connects FROM (expected: the private 6PN range, e.g. fdaa::/16):
fly ssh console --app dome-api-staging -C "ss -tn"
fly secrets set --app dome-api-staging DOME_TRUSTED_PROXIES="fdaa::/16"   # the verified value
fly deploy --config deploy/fly.toml --dockerfile deploy/Dockerfile .

curl -s https://dome-api-staging.fly.dev/healthz        # {"status":"ok","database":"ok",...}
curl -s https://dome-api-staging.fly.dev/v1/plans        # billing_enabled false
fly logs --app dome-api-staging
```

Then run the real checks the test doubles cannot: sign in through the real identity provider, link
a PC (`dome-agent link` against `DOME_AGENT_API_URL=https://dome-api-staging.fly.dev`), pair a phone
over cellular, press Next. Record the results in `docs/ACCEPTANCE.md` with the honest tag
(`Windows-device-tested`, `iPhone-tested`), not before.

Production repeats the procedure with a separate app, database, OIDC client, key and
`DOME_ENV=production` (which additionally enforces https and a real session secret). Do not point
the production app at the staging database or provider, and do not make a live Stripe charge
because staging works (`docs/spec/MASTER_PROMPT.md` §18, Phase D).

### Deployment-time facts to verify on the first machine

- `DOME_TRUSTED_PROXIES`: the proxy's source range, as above. A wrong value makes every per-IP
  abuse limit and every stored `ip_hash` attacker-chosen (`cloud-api/KNOWN_ISSUES.md` #5).
- Fly's proxy closes idle connections; the API pings every WebSocket every 20 s
  (`ws_ping_interval` in `cloud-api/dome_api/cli.py`) and the agent pings every 25 s, which the
  design assumes is enough. Confirm an idle agent stays connected for an hour.
- `kill_timeout = "30s"` must stay above uvicorn's 20 s graceful shutdown.
- Memory: 512 MB is a cost-model assumption; watch RSS under real agents.

## Operations

### Migrations

Alembic, forward-only. `fly.toml` runs `alembic -c alembic.ini upgrade head` as the release
command (once, before new machines start); the app process starts with `--skip-migrations` and
refuses to serve unless the schema is at the single head. Locally `dome-api` migrates on start.
A new migration ships in the same release as the code that needs it and must be backwards
compatible with the previous release for the duration of a rolling deploy (additive columns with
defaults first; destructive changes one release later). There is one revision today (`0001`), with
the Phase C tables already present and empty. Downgrades are not part of rollback (next section).
Before touching production: take a database backup; the release command is not transactional
across statements that PostgreSQL cannot run in a transaction.

### Rollback of a deployment

1. `fly releases --app <app>` shows the image of each release; `fly deploy --image <previous image ref>`
   redeploys it without rebuilding. (The image is also rebuildable from the git tag.)
2. Rolling back **code** is safe at any time: the process is stateless apart from the in-memory
   connection manager, agents reconnect with backoff, in-flight commands are marked
   `outcome_unknown`/`expired` by the start-up sweep (never re-executed).
3. Rolling back **a migration** is not done with `alembic downgrade` in production. If a release
   with a new migration must be rolled back, the previous code must tolerate the new schema (the
   compatibility rule above); otherwise restore the pre-deploy backup and accept the data loss
   window explicitly. Rehearse this on staging before it is ever needed (**not yet done**).

### Backup and restore

- Backups are the database provider's: Fly PostgreSQL volume snapshots or Managed Postgres
  backups, plus a scheduled logical dump (`pg_dump` through `fly proxy`) to an object store the
  founder controls. Retention follows `docs/DATA_RETENTION.md`; backups contain only what the
  database contains (hashed credentials and session ids, no payloads, no challenge text, no titles).
- Restore procedure to rehearse on staging **before** calling anything production-ready
  (spec §15): create a fresh database, `pg_restore`, point a staging app at it, run the
  integration checks, measure the time it took. The result and date belong in `docs/ACCEPTANCE.md`.
  **Not yet done.**

### Secret rotation

Follow the table above. Order: create the new secret at its source (identity provider, database
role, new key file) → `fly secrets set` (this restarts the machines) → verify `/healthz` and one
sign-in → revoke the old secret at its source. Rotating `DOME_SESSION_SECRET` signs everyone out
by design; announce it. The entitlement key's JWKS only ever serves the current key.

### Incident basics

- `fly status`, `fly logs` (JSON lines; `event` and `route` fields; no customer secrets),
  `/healthz` (`database: unavailable` → 503). The relay occupancy is in the health body.
- Account takeover or abuse: the customer revokes controllers and sessions from the PWA; the
  operator interface for suspending accounts is **not implemented** (Phase C/D), so today an
  operator acts with SQL on `sessions`/`controllers`/`grants` and records it. Operators cannot
  execute PC commands or bypass local approvals by design (`docs/SECURITY.md`).
- Compromised signing key: rotate it (agents fall back to Free gates until refreshed); nothing
  else is signed with it.
- Compromised session secret or database: rotate the session secret (signs everyone out), rotate
  the database role, review `security_events`. Stored credentials are hashes.
- Relay restart: expected and safe (above). Agents and phones show "reconnecting"; no command is
  queued or replayed for an offline PC.

### Version compatibility

The API image carries its PWA, so backend and PWA always match. PC agents and browser extensions
update independently; compatibility is negotiated by `shared/protocol/version.json` (rules in
`docs/PROTOCOL.md`): an incompatible agent is refused at `hello` with a clear code rather than
reinterpreted. Deploy a protocol MINOR bump backend-first, then release the agent; never make the
backend require a protocol version no shipped agent speaks.

### Monitoring — what exists and what does not

Exists: `/healthz`, structured JSON logs, `security_events` rows, the connection counters in the
health body. Does not exist yet: metrics export, alerting, uptime checks, error aggregation, the
operator interface, product-analytics activation events (`docs/spec/MASTER_PROMPT.md` §16). These
are Phase D work; do not describe the service as monitored until they are in place.

## CI (`.github/workflows/ci.yml`)

Four jobs, no secrets, SHA-pinned actions (tag in the trailing comment; resolved with
`git ls-remote --tags` on 2026-10-09: `actions/checkout` v7.0.1, `actions/setup-node` v7.1.0,
`astral-sh/setup-uv` v10.2.0, `pnpm/action-setup` v6.1.0):

| Job | What runs | Needs |
| --- | --- | --- |
| `python` | `shared/python` pytest; `cloud-api` ruff + format check + mypy strict + `alembic upgrade head`/`alembic check` + pytest (real PostgreSQL service container, `tools/dev-idp` started by the suite, uvicorn in-process); `pc-agent` ruff + format check + mypy + pytest (Linux, fake platform) | `postgres:16` service |
| `node` | `shared/ts` install, `gen:types` drift check, typecheck, tests (cross-language fixtures); `mobile-app` typecheck/lint/test/build; `browser-extension` typecheck/lint/test/build | pnpm store cache keyed on the three lockfiles |
| `integration` | `tests/` — the real `dome-agent` process + real relay + PostgreSQL + OIDC login + fake extension (last local run 20 passed in 214 s) | `postgres:16` service |
| `container` | `docker build -f deploy/Dockerfile .`, then checks the image refuses to invent a signing key outside development | the runner's Docker |

Python 3.12 is used everywhere because `pc-agent` requires `<3.13`; `uv sync --locked` fails if
any `uv.lock` is stale (all five were checked with `uv lock --check` here). The workflow has **not
run on GitHub Actions yet**; the first run will show whether the runner needs anything the build
machine had (for example for `pystray`/tkinter imports in the agent's headless tests).

## What was actually verified

| Item | Evidence | Tag |
| --- | --- | --- |
| `Dockerfile` syntax | parsed by the BuildKit Dockerfile parser (`dockerfile` Python binding, 48 instructions, 4 stages); `hadolint` 2.14.0 clean | not built — no Docker daemon here |
| `entrypoint.sh` | `sh -n`; all four paths exercised locally with the real `dome-api-gen-entitlement-key`: staging without key → exit 2; development without key → 0600 PEM generated; `_B64` → identical PEM written 0600 and `DATABASE_URL` → `postgresql+psycopg://…` translated; existing key + `DOME_DATABASE_URL` → untouched | unit-tested (shell, locally) |
| `fly.toml` | parses as TOML; fields checked against the Fly configuration reference (superfly/docs) read 2026-10-09 | not yet verified on Fly.io |
| `docker-compose.dev.yml` | `docker compose config --quiet` (Compose v5.6.0) | not brought up |
| `ci.yml` | `actionlint` 1.7.11 clean; PyYAML parse; every `uses:` SHA resolved from the release tag with `git ls-remote`; every `with:` input checked against the action's `action.yml` at that tag; commands copied from the component READMEs/Makefile; `uv lock --check` passes in all five projects | not yet run on GitHub |
| Pinned images `python:3.12-slim-bookworm`, `node:22-bookworm-slim`, `ghcr.io/astral-sh/uv:0.11.32`, `postgres:16` | variant directories confirmed in docker-library/python and nodejs/docker-node; uv 0.11.32 release page confirmed | pulls not attempted |

Remaining before "deployed": build the image on a machine with Docker, bring up the Compose stack
and sign in, create the staging app and follow the section above, run the restore rehearsal, enable
the workflow and fix whatever the first run reports.
