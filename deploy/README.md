# deploy — container image, Fly.io configuration, the Deploy button, Docker Compose, CI

How DoMe's cloud side (API + relay + PWA, one process, one PostgreSQL) is built into an image and
run in **development**, **staging** and **production**, how the founder deploys it with one button,
and the operational procedures the product spec requires (migrations, rollback, backup/restore,
secret rotation, incident basics). ADR-0001 D1/D2 are the decisions behind it.

> **Status (2026-10-14): nothing has been deployed to Fly.io yet.** The image is built by CI on every
> push since 2026-10-11 and was run in the test-kit rehearsal (Linux). The Deploy workflow and its
> program (`launch/dome_deploy.py`) are written and tested against a fake `flyctl`
> (`launch/tests/`), but have **never run against Fly.io, Neon, Auth0 or Cloudflare**: the founder's
> first staging run is the real test. Since 2026-10-14 (budget about $50 a month) staging's database is
> Neon's free plan and staging's machine sleeps when idle; production is unchanged. What was verified is listed in
> [What was actually verified](#what-was-actually-verified).
>
> **Founder: start with [`docs/LAUNCH_SETUP.md`](../docs/LAUNCH_SETUP.md)**, the click-by-click guide.
> You never need a command line; everything below is for engineers.

| File | Purpose |
| --- | --- |
| `Dockerfile` | Multi-stage image: build the PWA with pnpm, install cloud-api with `uv`, run `dome-api` (uvicorn) as a non-root user with `DOME_STATIC_DIR` pointing at the built PWA; HEALTHCHECK on `/healthz`. Build context is the **repository root**. |
| `Dockerfile.dockerignore` | Keeps `.git`, `node_modules`, `.venv`, `.env*`, keys and the components the image does not need out of the context. |
| `entrypoint.sh` | Turns the hosting platform's environment-only secrets into what the API wants: writes the Ed25519 signing key from `DOME_ENTITLEMENT_SIGNING_KEY_PEM_B64` to a 0600 file; derives `DOME_DATABASE_URL` from a `DATABASE_URL`. Generates a key **only** in `DOME_ENV=development`. |
| `fly.toml` | Fly.io app configuration shared by both environments (persistent-WebSocket friendly: production's machine never auto-stops, connection-based concurrency, 30 s drain, `/healthz` check, release command for migrations, the Fly client-address settings). No secrets and no per-environment values: the Deploy workflow passes those, and deploys a temporary copy, `fly.<environment>.generated.toml`, that differs only in machine sleep (staging stops when idle). |
| `launch/environments.toml` | The fixed, public facts per environment: Fly app name, region `iad`, origin, host name, release channel, database mode (staging `external` = Neon, production `fly-managed` with its cluster name, plan and size) and machine sleep. |
| `launch/dome_deploy.py` | Everything the Deploy button does (`gate`, `deploy`, `status`); standard library only. |
| `launch/tests/` | Its tests: a fake `flyctl` with Fly-shaped output, the workflow's safety rules, `fly.toml` consistency. |
| `docker-compose.dev.yml` | PostgreSQL 16 + `tools/dev-idp` + the API image for machines that have Docker. |
| `../.github/workflows/deploy.yml` | The Deploy button (manual runs only). |
| `../.github/workflows/ci.yml` | GitHub Actions CI: six jobs with lockfile-keyed caches, SHA-pinned actions, no secrets. |

Related: `docs/LAUNCH_SETUP.md` (founder guide), `cloud-api/README.md` (every `DOME_*` variable and
the proxy-trust rules), `docs/OPERATIONS.md`, `docs/ARCHITECTURE.md`, `docs/SECURITY.md`,
`docs/DATA_RETENTION.md`, `docs/COST_MODEL.md`.

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
- The three base images (`python:3.12-slim-bookworm`, `node:22-bookworm-slim`, `ghcr.io/astral-sh/uv:0.11.32`)
  are pinned by **digest** (the tag's multi-platform index, resolved 2026-10-14), so staging's and
  production's builds of one commit start from the same layers and a re-pushed tag changes nothing. Bumping
  them is a manual edit (resolve the tag, replace the digest, let CI build it); no bot does it yet.
- The API serves the PWA from the same origin (`/` static, `/v1` REST, `/ws` sockets), so there is
  no CORS configuration and no second deployable. API and PWA are always the same commit.
- Build arguments: `VITE_DOME_RELEASE_CHANNEL` (default `staging`), `VITE_DOME_SUPPORT_URL`,
  `VITE_DOME_API_ORIGIN` (leave empty: same origin), all public, build-time values; and
  `DOME_BUILD_COMMIT`, the git commit, which becomes an environment variable and the OCI
  `org.opencontainers.image.revision` label. `/healthz` reports it as `"commit"`, so a deploy can prove
  which build runs and production can require the commit staging runs.
- The container does not run as root, has no shell entry other than `entrypoint.sh`, and contains
  no secret. Keys arrive at start-up through the environment and are written to `/app/run` (0600).
- `CMD dome-api` runs `alembic upgrade head` and then serves. Fly runs the migration once as a
  release command and starts the process with `--skip-migrations` (start-up still refuses to run
  unless the schema is at head; see `cloud-api/README.md` "Operational notes").

Build and run locally (needs a Docker daemon; the same build CI runs):

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
| Where | developer machine (Makefile) or Compose | Fly app `domeremote-staging`, region `iad` | Fly app `domeremote-production`, region `iad` |
| `DOME_ENV` | `development` | `staging` | `production` |
| Identity provider | `tools/dev-idp` (never deployed) | the real provider, a **separate** application/client ("DoMe staging") | the real provider, production client |
| Origin | `http://localhost:5173` (Vite) or `:8080` | `https://staging.domeremote.com` from the first deploy (`<app>.fly.dev` serves `/healthz` until DNS exists; sign-in waits for DNS) | `https://domeremote.com` |
| TLS / HSTS | plain http | Fly-terminated TLS | Fly-terminated TLS; `production` enables HSTS |
| `DOME_TRUSTED_PROXIES` | unset (TCP peer is the client) | private IPv4 ranges + `DOME_CLIENT_IP_HEADER=fly-client-ip` (`fly.toml`); checked by `/healthz` on every deploy | same |
| Database | local PostgreSQL | Neon free plan, project `domeremote-staging`, AWS `us-east-1`, outside Fly; its direct address is the GitHub Environment secret `DATABASE_URL` ([Staging database](#staging-database-neon)) | Fly Managed Postgres Basic, `domeremote-production-db`, `iad` |
| Machine | — | one, sleeps when idle (`auto_stop_machines = "stop"`, `auto_start_machines = true`, `min_machines_running = 0`); the first request after a nap takes a few seconds | one, always on (`"off"`, `false`, `1`): PCs hold WebSockets |
| Entitlement key | generated locally (`make`/entrypoint) | generated once, stored only in Fly secrets | generated once, stored only in Fly secrets; never the staging key |
| Stripe (billing) | off, or the fake provider (`DOME_BILLING_PROVIDER=fake`) or test-mode keys in `.env` | off without values; Test mode keys only (the Deploy button refuses a live key, and so does the app) | off without values; Test mode or Live mode keys (the founder tries Test mode first; every Deploy summary says "Stripe: TEST MODE — no real charges" or "Stripe: LIVE — real charges") |
| Data | throwaway | throwaway; may be reset; Neon's 6-hour restore window is the only backup | retained per `docs/DATA_RETENTION.md` |

Code paths that differ by `DOME_ENV` are in `cloud-api/dome_api/settings.py`: production requires
https origins and issuer and a non-placeholder session secret; staging and production require an
explicit `DOME_TRUSTED_PROXIES`; outside production every REST response is also validated against
`rest.schema.json` (a contract violation becomes a loud 500 in staging, which is intended).

## What the founder sets up

Accounts and pasted values only; [`docs/LAUNCH_SETUP.md`](../docs/LAUNCH_SETUP.md) is the click-by-click
guide. In short:

1. **Fly.io** account with a card on file (the trial stops machines after 5 minutes), one organization
   token per GitHub Environment. **Neon** free account with the project `domeremote-staging` (PostgreSQL
   16, AWS `us-east-1`); its direct connection string (Connection pooling off) is the staging Environment
   secret `DATABASE_URL`.
2. **GitHub Environments** `staging` and `production` on this repository, each limited to the default
   branch with the founder as **required reviewer** and no administrator bypass (the `gate` job checks
   this), approving only runs he started himself, holding the secrets and variables in
   [Secrets and configuration](#secrets-and-configuration-names-only--values-never-enter-this-repository).
3. **Auth0**: one tenant per environment, one Regular Web Application each, callback URL exactly
   `<origin>/v1/auth/callback`, Credentials > Authentication Method **Client Secret (Post)** (what
   `cloud-api/dome_api/auth/oidc.py` sends). Keycloak or another compliant issuer also works.
4. **Cloudflare**: the DNS records each Deploy summary prints, as **DNS only**; the redirects and DNSSEC
   below.
5. **Stripe**: billing is built (`docs/BILLING.md`; integration-tested with a fake Stripe, not
   sandbox-provider-tested). The founder puts seven values in a GitHub Environment (the secrets
   `DOME_STRIPE_SECRET_KEY`, `DOME_STRIPE_WEBHOOK_SECRET`; the variables `DOME_STRIPE_PRICE_PRO_MONTHLY`,
   `_PRO_ANNUAL`, `_PRO_PLUS_MONTHLY`, `_PRO_PLUS_ANNUAL`, `_TOPUP`) and creates the webhook endpoint
   `<origin>/v1/billing/webhook` in Stripe; the Deploy button carries them to Fly
   ([Billing](#billing-stripe-through-the-deploy-button), `docs/LAUNCH_SETUP.md` section 11A). Without them
   billing stays off.
   **Anthropic key**: not now (the assistant stays off: the workflow never sets `DOME_AI_*`; the protocol 1.3
   allowances describe the plans, and the website labels them "Not switched on yet").
6. **Windows code signing, Chrome Web Store, support mailbox** are release prerequisites outside
   this directory (`docs/SECURITY.md` T12, `docs/HANDOFF.md`).

## The Deploy button (`.github/workflows/deploy.yml`)

GitHub > Actions > **Deploy** > Run workflow; inputs `environment` (staging | production), `action`
(deploy | status) and `create_database_if_missing` (production only: the founder's consent to about $41 a
month for production's database and its 10 GB; about $45 a month for the whole of production; a staging run
ignores it and says so).
The file must be on the default branch or GitHub shows no button. Three jobs:

| Job | Secrets | What it does |
| --- | --- | --- |
| `gate` | no values, no environment | refuses any branch but the default branch; refuses while `FLY_API_TOKEN`, `DOME_OIDC_CLIENT_SECRET`, `DATABASE_URL`, `DOME_STRIPE_SECRET_KEY` or `DOME_STRIPE_WEBHOOK_SECRET` exists as a **repository** secret (GitHub evaluates `secrets.X != ''` and passes only names, `REPO_LEVEL_SECRETS`); reads both GitHub Environments with the job's own token (`actions: read`) and refuses unless each exists, has a required reviewer, no administrator bypass and exactly one branch rule, the default branch; for production, refuses unless `https://<staging app>.fly.dev/healthz` reports `status ok`, `database ok`, `forwarded_client ok` and **this** commit. Runs before GitHub asks for approval, so the founder is never asked to approve a run that would be refused, and a refused gate never lets GitHub auto-create an unprotected environment. |
| `deploy` | the chosen GitHub Environment's | `dome_deploy.py deploy`, below. Needs the founder's approval (required reviewer on both environments). |
| `status` | none, no environment | `dome_deploy.py status`: reads `/healthz`, the public `GET /v1/plans` (billing off / test mode / live), DNS and TLS for one environment and prints the next click. Never runs `flyctl`; cannot change anything. |

`dome_deploy.py deploy`, in order (steps 1-5 only read; a stop there changes nothing):

1. Branch, inputs (each missing value is named with the exact GitHub page and tab), issuer normalization
   (`x.us.auth0.com`, `https://x.us.auth0.com` and `https://x.us.auth0.com/` are all accepted), and the
   Stripe values ([Billing](#billing-stripe-through-the-deploy-button)).
2. OIDC discovery from the runner: the issuer answers, names itself, and accepts `client_secret_post`.
3. Production only: the staging check of the `gate` job again.
4. `flyctl apps list`, `secrets list` (names and digests only), and for production `mpg list`. A
   generated secret counts as missing only when `secrets list` succeeded and parsed; any other answer
   stops the run. A production app that already has machines but lacks a generated secret stops for an
   engineer instead of getting a new one (that would sign everyone out or invalidate every linked PC).
5. Database decision, by `database_mode`. **Staging (`external`)**: no `mpg` command ever runs. The GitHub
   Environment secret `DATABASE_URL` is required and checked in step 1 without ever being printed (a
   `postgres(ql)://` URL with user, password, host and database; no `-pooler` host; `sslmode=require` or
   `verify-ca` with `channel_binding=require`, or `sslmode=verify-full`; no stars for a password); missing or
   unusable stops with "needs you" and the Neon steps. A staging app with its own `DOME_DATABASE_URL` Fly
   secret (it would override `DATABASE_URL`) stops for an engineer. **Production (`fly-managed`)**: already
   attached / the cluster exists / create it (only with the checkbox). The GitHub secret `DATABASE_URL` is a
   fallback only when Fly will not list the clusters (the token cannot manage Managed Postgres) or the attach
   sets no address; when Fly lists them and production's is missing, the run stops for the cost consent
   ("about $41 a month", nothing changed) even with that secret set, and names any other clusters so a
   misnamed hand-made one is not bought twice. A production `DATABASE_URL` whose host is `*.neon.tech` is
   refused in step 1 (it would put production on staging's free database).
   Before step 1's inputs, staging and production must resolve to two different Fly apps (`FLY_STAGING_APP` /
   `FLY_PRODUCTION_APP`); the `gate` and `status` jobs check this too, before any request.
6. Create the app if missing (a name owned by someone else stops with "set the repository variable
   `FLY_STAGING_APP`/`FLY_PRODUCTION_APP`"); allocate a dedicated IPv6 and the **free shared** IPv4
   (never a dedicated IPv4).
7. Production only: `flyctl mpg create` (Basic, `iad`, 10 GB, PostgreSQL 16) when consented, wait until
   `ready`, then `flyctl mpg attach` (sets the pooled `DATABASE_URL`). Both commands print the database password, so
   their output is never shown; only the exit code is.
8. Generate `DOME_SESSION_SECRET` (`secrets.token_hex(32)`) and `DOME_ENTITLEMENT_SIGNING_KEY_PEM_B64`
   (base64 of `openssl genpkey -algorithm ed25519`, in memory only) **only if absent**; mask them; stage
   them plus `DOME_OIDC_CLIENT_SECRET` (always re-copied from GitHub), for staging `DATABASE_URL`
   (always re-copied from GitHub, so a Neon password reset is: paste in GitHub, press Deploy) and, with
   billing on, `DOME_STRIPE_SECRET_KEY` and `DOME_STRIPE_WEBHOOK_SECRET` (always re-copied) with
   `flyctl secrets import --stage` on stdin. The password inside `DATABASE_URL` is masked on its own. Any
   `DOME_STRIPE_*` Fly secret that GitHub no longer supplies is removed with `flyctl secrets unset --stage`
   (below).
9. `docker build` exactly as CI does, with the channel, support URL and commit build arguments, then
   `flyctl deploy --config deploy/fly.<environment>.generated.toml --image <that image> --local-only
   --builder-pool=false --depot=false --ha=false --no-public-ips` with `-e DOME_ENV`,
   `DOME_PUBLIC_ORIGIN`, `DOME_OIDC_ISSUER`, `DOME_OIDC_CLIENT_ID` and, with billing on, the five
   `DOME_STRIPE_PRICE_*` ids. The config is a copy of `fly.toml`
   written next to it just before the deploy, with `environments.toml`'s machine sleep in
   `[http_service]`; the program refuses to deploy unless the copy parses to exactly `fly.toml` with only
   those three keys changed, and deletes it afterwards, failure or not (git ignores
   `deploy/fly.*.generated.toml`). `flyctl deploy` v0.4.110 has no flag for autostop, autostart or minimum
   machines (checked in its `internal/command/deploy/deploy.go` and `internal/flag/flag.go` on 2026-10-14),
   so a file is used. Fly's release command runs the migrations first; a failure leaves the previous version
   running.
10. Scale to exactly one machine if Fly created more (the relay is in memory).
11. `flyctl certs add <host>` if missing, `certs check`; the DNS records to add come from Fly's answer.
12. Checks on `https://<app>.fly.dev`: `/healthz` with this commit, `database ok` and
    `forwarded_client ok` (up to 3 minutes for a rolling restart); the web page with its security
    headers and `X-Robots-Tag` (the fly.dev name is never indexable); the sign-in start, `GET
    /v1/auth/login` without following the redirect: 303 to the discovery document's
    `authorization_endpoint` with `client_id` = `DOME_OIDC_CLIENT_ID` and `redirect_uri` = the callback (it
    proves the app reached the provider from inside Fly; it leaves one unfinished sign-in attempt row that
    expires); and a WebSocket upgrade to `/ws/agent` without credentials over raw TLS, which must reach the
    app (its own 401, or 101) rather than a proxy error; and `GET /v1/plans`, whose `billing_mode` (and
    `billing_enabled`) must match what this deploy configured (`off`, `test` or `live`). Then the custom host: not in DNS / proxied by
    Cloudflare / pointing elsewhere / certificate pending (green, with the next click) / unhealthy, or in
    production another commit (red: fly.dev just passed, so it is a real misconfiguration) / **live**.
    "Live" is said only when `https://<host>/healthz` answered with this commit during this run; sign-in
    is never claimed. `status` runs are red for the same states and for a `forwarded_client` that is not
    ok, green for "not deployed yet" and DNS or certificate pending. Production's staging check (gate and
    deploy) and `status` ask `/healthz` up to 4 times, 10 s apart, while the app does not answer or answers
    502-504: that is a sleeping staging machine starting and its Neon database waking.
13. The run summary (Actions > the run > Summary): what was created, what was already there, the next
    click with exact Cloudflare records, and for production the list of what is still not done.

### Billing (Stripe) through the Deploy button

The seven values of `cloud-api/dome_api/settings.py` (`_STRIPE_FIELDS`): the GitHub Environment **secrets**
`DOME_STRIPE_SECRET_KEY` and `DOME_STRIPE_WEBHOOK_SECRET` (only in the deploy step's `env`, staged into Fly on stdin,
dropped from every child process's environment, in the redaction book, never echoed; the redaction also hides any
`sk_`/`rk_` key or `whsec_` secret it was never told about) and the Environment **variables**
`DOME_STRIPE_PRICE_PRO_MONTHLY`, `_PRO_ANNUAL`, `_PRO_PLUS_MONTHLY`, `_PRO_PLUS_ANNUAL`, `_TOPUP` (price ids are not
secret; passed with `flyctl deploy -e`, like `DOME_OIDC_CLIENT_ID`). Checked in step 1, before any `flyctl` call, with
founder-readable stops that name variables, never values:

- all seven or none: none = billing off (the summary says "Stripe: off"); some = "Payments are half set up", naming
  the missing ones on their tab;
- the key starts with `sk_test_`/`rk_test_` or `sk_live_`/`rk_live_`; a publishable `pk_` key, a `whsec_` secret in
  the key's place, any other shape or a cut-off key is refused; **staging refuses a live key** (so does the app outside
  production); production accepts both, and the summary says **"Stripe: TEST MODE — no real charges"** or **"Stripe:
  LIVE — real charges"** (with a warning while production is in test mode: anyone can subscribe with a test card);
- the signing secret starts with `whsec_` (an API key there is refused);
- each price id matches `price_[A-Za-z0-9_]{4,}` (the app's `_PRICE_ID`), a `prod_` id gets its own message, and the
  five must differ; a Stripe secret pasted into a price **variable** is reported as exposed (variables are printed in
  the step's log) with the roll steps;
- a Stripe secret saved as a **variable** instead of a secret: deploy.yml hands over only its NAME
  (`STRIPE_SECRETS_AS_VARIABLES`, `vars.X != ''`), and the run stops with the roll steps;
- the gate refuses repository-level copies of the two secrets, like the other deploy secrets.

**Removal semantics.** The GitHub Environment is the only source of the Stripe values, as it is for
`DOME_OIDC_CLIENT_SECRET`, and the app refuses to start with only some of the seven. So a deploy removes every
`DOME_STRIPE_*` Fly secret that it does not stage itself: with billing off, the two secrets an earlier deploy stored
(the summary notes that billing was switched off, and that existing subscriptions keep charging at Stripe until
cancelled there); with billing on, any price id someone kept as a Fly secret by hand (the price ids come with `-e`).
`flyctl secrets unset --stage` (v0.4.110 `internal/command/secrets/unset.go` shares `--stage`, read 2026-10-20), so
nothing restarts until the deploy itself. Not passed, ever: `DOME_STRIPE_AUTOMATIC_TAX` (Stripe Tax stays off; the
founder's prices are tax-inclusive), `DOME_BILLING_PROVIDER` (blank = Stripe when a key is set; `fake` is refused in
staging and production) and the other `DOME_BILLING_*` tuning values (their defaults apply).

**Webhook.** `<origin>/v1/billing/webhook` (`routes/billing.py`, mounted under `/v1`): `https://domeremote.com/v1/billing/webhook`
for production, `https://staging.domeremote.com/v1/billing/webhook` for staging, each with its own signing secret.
Events: the nine the webhook acts on (`checkout.session.completed`, `checkout.session.async_payment_succeeded`,
`checkout.session.expired`, `customer.subscription.created|updated|deleted`, `invoice.paid`, `invoice.payment_failed`,
`invoice.payment_action_required`) plus the four it only records for the operator (`charge.refunded`,
`charge.dispute.created|updated|closed`). A billing-on summary repeats the address and the events;
`tests/test_deploy_billing.py` checks them against `billing/service.py` and the route.

Engineer notes: the program runs as `/usr/bin/python3 -I` (the runner's system Python 3.12 on
ubuntu-24.04, isolated from `PYTHON*` variables and the working directory) and installs nothing. flyctl
v0.4.110 comes from its GitHub release tarball, checked with `sha256sum -c` against the hash in the
workflow (from the release's `flyctl_0.4.110_checksums.txt`) before extraction; only the `flyctl`
binary is extracted, into `$RUNNER_TEMP/flyctl`, which is then added to `$GITHUB_PATH`. Child output is
decoded as UTF-8 with replacement, and a failure while streaming kills the child before the error is
reported. `cd deploy/launch && uv sync --extra dev && uv run pytest` runs its tests. Unverified until the
first real run, so parsed leniently with plain stops: the JSON shapes of `flyctl` 0.4.110 (read from its
source), `mpg create`/`attach` with an organization token, and attach before the app has machines.

## Domain

Registered 2026-10-13 by the founder (Cloudflare Registrar, founder's account): `domeremote.com` and
`domeremote.app`. **Nothing below is configured yet**; it is the plan for when the Fly apps exist, and
`docs/LAUNCH_SETUP.md` walks the founder through it.
Cloudflare's dashboard changes over time, so compare these steps with its current documentation.

- **One origin per environment.** Production is `https://domeremote.com`: the website, the PWA, the REST
  API and both WebSockets on that one host (ADR-0001 D3). Staging is `https://staging.domeremote.com`.
  `domeremote.app` and `www.domeremote.com` only **redirect** there; they are never served and never go in
  `DOME_EXTRA_ORIGINS`. The reason is not cosmetic: the session cookie, the Origin check and the phone's
  signing key (stored per origin in the browser) all belong to one origin, so a phone that opened the app
  on a second origin would be signed out and look like a new, unpaired phone.
- **Records for the apps** (Cloudflare → `domeremote.com` → DNS): the Deploy run adds the certificate on Fly
  and prints exactly the records Fly asks for: for the bare `domeremote.com`, an A record (the shared IPv4)
  and an AAAA record (the IPv6); for `staging`, a CNAME to Fly's target (normally
  `domeremote-staging.fly.dev`). Delete any older A/AAAA/CNAME record of the same name first, and set them
  to **DNS only** (grey cloud): Fly issues the certificate over TLS-ALPN, which Cloudflare's proxy blocks,
  and the proxy would also replace the client address Fly reports (`Fly-Client-IP` would be Cloudflare's).
  The `status` action detects a proxied record (Cloudflare's published ranges) and says so.
- **Redirects** (Cloudflare → Rules → Redirect Rules), for `domeremote.app` (both `@` and `www`) and for
  `www.domeremote.com`: a **proxied** (orange cloud) placeholder record such as `AAAA @ 100::`, then a single
  redirect for all incoming requests to the dynamic target `concat("https://domeremote.com",
  http.request.uri.path)`, status 301, query string kept. `.app` is on the browsers' HTTPS-only (HSTS
  preload) list, so the redirect is only reachable over https; the proxied record gets Cloudflare's edge
  certificate automatically.
- **DNSSEC**: Cloudflare → each domain → DNS → Settings → Enable DNSSEC (Cloudflare Registrar publishes the
  DS record itself).
- **Identity provider** callback URLs: `https://domeremote.com/v1/auth/callback` (production) and
  `https://staging.domeremote.com/v1/auth/callback` (staging). No logout URL is needed: DoMe's sign-out
  ends its own session only (no redirect to the provider).
- **Windows app**: the agent has no built-in server address. A release installer always links to
  production; linking a PC to staging needs the test kit or a development build (`/ApiUrl=` works only
  there). The installer
  (`pc-agent/packaging/installer/DoMe.iss`) writes `https://domeremote.com` into `dome-server.json` next
  to `DoMe.exe`, which only `DoMe.exe link` reads; it deliberately sets no `DOME_AGENT_API_URL`, because
  that variable would override the address stored at link time (`pc-agent/DECISIONS.md` 54). The test
  kit keeps setting its own tunnel address for its own process.
- **Search engines**: `mobile-app/public/robots.txt` and `sitemap.xml` name `https://domeremote.com`;
  outside production every response carries `X-Robots-Tag: noindex, nofollow`, so staging never appears
  in search results; in production, so does every response served under another host name (the
  `<app>.fly.dev` duplicate). Link previews use `https://domeremote.com/og-image.png` (`mobile-app/index.html`).
- The JSON Schema `$id`s under `https://dome.app/…` in `shared/protocol` are opaque identifiers that are
  never fetched (and `dome.app` is not ours); they are unrelated to the product domain and stay as they
  are, since changing them regenerates every validator.

## Secrets and configuration (names only — values never enter this repository)

Founder-pasted values are **GitHub Environment** secrets and variables (Settings > Environments >
`staging` / `production`); the Deploy workflow copies what the app needs into **Fly app secrets** through
`flyctl secrets import` on stdin. Values generated by the workflow exist only in Fly.

| Name | Kind | Set by | Used for | Rotation effect |
| --- | --- | --- | --- | --- |
| `FLY_API_TOKEN` | GitHub Environment secret (never a repository secret: the gate refuses while one exists) | founder (Fly dashboard > Tokens, organization token) | `flyctl` in the deploy job only | revoke on the Fly Tokens page, paste a new one |
| `DOME_OIDC_CLIENT_SECRET` | GitHub Environment secret → Fly secret | founder (Auth0 > Application > Settings); re-copied to Fly on every deploy | the relying-party credential | rotate in Auth0 (Credentials > Rotate), paste in GitHub, press Deploy |
| `DATABASE_URL` | Fly secret | **staging**: the GitHub Environment secret `DATABASE_URL` (Neon's direct connection string), required and copied to Fly on every deploy; **production**: `flyctl mpg attach` in the deploy job (pooled PgBouncer URL), fallback: founder pastes it as a GitHub Environment secret, or uses the cluster's Connect tab > Set secret | PostgreSQL connection; the entrypoint converts it to `postgresql+psycopg://`, keeping its query (`sslmode`, `channel_binding`) | staging: Neon > Reset password, paste the new string in GitHub, press Deploy; production: rotate the database user's password, then the secret, then deploy (`docs/OPERATIONS.md` §7) |
| `DOME_SESSION_SECRET` | Fly secret | generated in the deploy job (`secrets.token_hex(32)`) only when absent | HMAC key for session-cookie ids and `ip_hash` values | every web session is signed out; the PWA redirects to sign-in |
| `DOME_ENTITLEMENT_SIGNING_KEY_PEM_B64` | Fly secret | generated in the deploy job (base64 of an Ed25519 PKCS8 PEM) only when absent; one per environment | written to `DOME_ENTITLEMENT_SIGNING_KEY_PEM_PATH` at start | agents re-fetch the JWKS on their next connect; Pro-only gates fall back to Free until then |
| `DOME_OIDC_ISSUER`, `DOME_OIDC_CLIENT_ID` | GitHub Environment variables | founder (Auth0 Domain and Client ID) | passed with `flyctl deploy -e` | change and press Deploy (the issuer change re-keys accounts: `accounts` are keyed by issuer + subject) |
| `FLY_ORG`, `VITE_DOME_SUPPORT_URL`, `DOME_SUPPORT_RESPONSE_EXPECTATION` | GitHub Environment variables, optional | founder | Fly organization (default `personal`); support contact (public, build-time); support promise | press Deploy |
| `FLY_STAGING_APP`, `FLY_PRODUCTION_APP` | repository variables, optional | founder, only if a default app name is taken | overrides `launch/environments.toml` | a new name is a new app |
| `DOME_TRUSTED_PROXIES`, `DOME_CLIENT_IP_HEADER` | `fly.toml` `[env]` | repository | client addresses, below | none |
| `DOME_STRIPE_SECRET_KEY`, `DOME_STRIPE_WEBHOOK_SECRET` | GitHub Environment secrets → Fly secrets (never repository secrets or variables: refused) | founder (Stripe > Developers > API keys; > Webhooks > the endpoint > Signing secret); re-copied to Fly on every deploy, removed from Fly when GitHub no longer has them | billing (`docs/BILLING.md`); all seven Stripe values or none; a live key is refused on staging | roll in Stripe, paste in GitHub, press Deploy (`docs/LAUNCH_SETUP.md` 11A) |
| `DOME_STRIPE_PRICE_PRO_MONTHLY`, `_PRO_ANNUAL`, `_PRO_PLUS_MONTHLY`, `_PRO_PLUS_ANNUAL`, `_TOPUP` | GitHub Environment variables | founder (Stripe > Product catalog > the price's `price_...` id) | passed with `flyctl deploy -e` | change and press Deploy (test and live prices are different ids: switch all seven together) |
| `DOME_AI_ANTHROPIC_API_KEY` | — | nobody yet | the AI assistant stays off (`DOME_AI_ENABLED` unset); the workflow never sets any `DOME_AI_*` or `ANTHROPIC_*` variable | — |

Non-secret configuration shared by both environments lives in `fly.toml` `[env]` and is documented
variable by variable in `cloud-api/README.md`. Logs are JSON on stderr with redaction of tokens,
credentials, challenge text, payloads, titles and emails; URLs are logged as paths only.

## Client addresses (`DOME_TRUSTED_PROXIES`, `DOME_CLIENT_IP_HEADER`)

Per-IP abuse limits (10 PC link starts per hour, 5 pairing claims per 15 minutes, 60 sign-ins per
minute) and the `ip_hash` of security events need the real client address. On Fly.io:

- Fly's proxy reaches the machine "through a private IPv4 address on each VM"
  (fly.io/docs/networking/app-services), and `DOME_API_BIND=0.0.0.0:8080` listens on IPv4 only, so every
  TCP peer is Fly's proxy on a private IPv4 address. No internet client can be a peer of this socket.
  The earlier expectation `fdaa::/16` (6PN, IPv6) could never match an IPv4-only socket.
- The right-most `X-Forwarded-For` hop is the app's **own** public address (fly.io/docs/networking/
  request-headers). The X-Forwarded-For walk would need those addresses in the trusted list, and they can
  change without a deploy. Fly's docs recommend `Fly-Client-IP` instead when nothing else is in front.
- So `fly.toml` sets `DOME_TRUSTED_PROXIES=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16` and
  `DOME_CLIENT_IP_HEADER=fly-client-ip`: from a trusted peer, the single valid `Fly-Client-IP` value is the
  client (`cloud-api/dome_api/security/proxy.py`); anything else falls back to the walk.
- **Verification without SSH**: `/healthz` reports `forwarded_client`: `ok` (the resolved address equals
  `Fly-Client-IP`), `not_checked` (no header, e.g. Fly's own health checks), `unresolved_private` (the
  proxy's address was not trusted: the ranges are wrong) or `unresolved_other`. The deploy job fails on
  anything but `ok` for requests it sends through Fly's edge, and production requires `ok` on staging.
  **Not yet verified on a live machine**; the first staging deploy is that check.
- Do not put Cloudflare's proxy (orange cloud) in front of the app hosts: `Fly-Client-IP` would then be
  Cloudflare's address for everyone.

## Engineer-only: the same steps with `flyctl`

For debugging, never needed by the founder. Managed Postgres (`fly mpg`), not the legacy unmanaged
`fly postgres`, which Fly Support does not support; it is production's database only. Use an app name from
`launch/environments.toml`.

```sh
fly apps create domeremote-production --org personal
fly ips allocate-v6 -a domeremote-production && fly ips allocate-v4 --shared -a domeremote-production --yes
fly mpg create --name domeremote-production-db --org personal --region iad --plan Basic --volume-size 10
fly mpg list -o personal                         # wait for Status "ready"; note the ID
fly mpg attach <ID> -a domeremote-production     # prints the password: do not paste the output anywhere
# generated secrets, on stdin only (never on the command line or in shell history):
{ printf 'DOME_SESSION_SECRET=%s\n' "$(openssl rand -hex 32)"
  printf 'DOME_ENTITLEMENT_SIGNING_KEY_PEM_B64=%s\n' "$(openssl genpkey -algorithm ed25519 | base64 -w0)"
} | fly secrets import --stage -a domeremote-production
docker build -f deploy/Dockerfile --build-arg VITE_DOME_RELEASE_CHANNEL=production \
  --build-arg DOME_BUILD_COMMIT="$(git rev-parse HEAD)" -t dome-launch:manual .
fly deploy --config deploy/fly.toml -a domeremote-production --image dome-launch:manual --local-only \
  --builder-pool=false --depot=false --ha=false \
  -e DOME_ENV=production -e DOME_PUBLIC_ORIGIN=https://domeremote.com \
  -e DOME_OIDC_ISSUER=https://<tenant>.us.auth0.com -e DOME_OIDC_CLIENT_ID=<client id>
fly certs add domeremote.com -a domeremote-production
```

(`DOME_OIDC_CLIENT_SECRET` too must arrive on stdin via `fly secrets import`, and with billing so must
`DOME_STRIPE_SECRET_KEY` and `DOME_STRIPE_WEBHOOK_SECRET`; the five `DOME_STRIPE_PRICE_*` ids go with `-e`.) **Staging** by hand: no `mpg`
lines; put `DATABASE_URL=<Neon direct string>` on the same stdin import (read it with `read -rs`, never type
it on a command line), and deploy a copy of `fly.toml` with `auto_stop_machines = "stop"`,
`auto_start_machines = true` and `min_machines_running = 0` (`fly.toml` itself is production's shape).

### Staging database (Neon)

Staging's database is Neon's free plan (decision of 2026-10-14, `docs/COST_MODEL.md` §2A): 1 GB per project
(Neon raised it from 0.5 GB on 2026-10-02), 100 CU-hours a month, the compute sleeps after 5 idle minutes, a
6-hour restore window (as of October 2026; check neon.com/pricing).
What the code relies on, and why:

- **The direct connection string, not the pooler.** Neon's `-pooler` host is PgBouncer in transaction mode.
  psycopg 3 prepares a statement on the server after it has run 5 times on one connection
  (`prepare_threshold`), and in transaction mode the next transaction may land on another server
  connection; Neon's docs also list session state (`SET`, `LISTEN`, SQL `PREPARE`, advisory locks) as
  unsupported there. Whether Neon's PgBouncer tracks protocol-level prepared statements was not confirmed,
  so DoMe does not depend on it: the direct host needs no such analysis. DoMe opens at most 30 connections
  (pool 10 + overflow 20, `cloud-api/dome_api/db/engine.py`) plus one for migrations; Neon's smallest
  compute (0.25 CU) allows about 105 direct connections (Neon docs: 112, 7 reserved). The Deploy button
  refuses a `-pooler` host.
- **TLS and channel binding.** Neon's string ends `?sslmode=require&channel_binding=require`, and the Deploy
  button refuses one without `channel_binding=require` (`sslmode=require` alone encrypts but does not check
  the server's certificate; only `sslmode=verify-full` may go without channel binding). The entrypoint
  changes only the scheme; SQLAlchemy passes both to psycopg; the libpq bundled with psycopg-binary (18.x)
  enforces both: no TLS, refused; a server that authenticates without SCRAM channel binding, refused
  (`cloud-api/tests/test_database_url.py`, against a stand-in server; nothing here has connected to Neon).
- **Naps.** Neon closes every connection when its compute suspends. The pool pre-pings each connection
  before use (`pool_pre_ping=True`, plus `pool_recycle=600`), so the first request after a nap reconnects
  instead of failing (tested by ending the server backend under a pooled connection). Fly's health check
  reads `/healthz` (one database round trip) every 15 s while the machine runs, so the database is awake
  whenever staging's machine is; the machine stops after a few idle minutes (Fly's proxy decides), and the
  database 5 minutes later. A linked PC holds a WebSocket and keeps both awake: around the clock, that uses
  the free 100 CU-hours in about 17 days at 0.25 CU, after which Neon suspends the database until the next
  month.
- **Region.** AWS `us-east-1` (northern Virginia), Neon's nearest to Fly `iad`.

### Deployment-time facts still to confirm on the first machine

- `forwarded_client` is `ok` (above).
- Fly's proxy closes idle connections; the API pings every WebSocket every 20 s
  (`ws_ping_interval` in `cloud-api/dome_api/cli.py`) and the agent pings every 25 s, which the
  design assumes is enough. Confirm an idle agent stays connected for an hour.
- `kill_timeout = "30s"` must stay above uvicorn's 20 s graceful shutdown.
- Memory: 512 MB is a cost-model assumption; watch RSS under real agents.
- The pooled `DATABASE_URL` from `mpg attach` goes through PgBouncer in **session** mode (Fly's default),
  where psycopg's prepared statements work. Switching the pool to transaction mode would break them.
- Staging: the machine stops when idle and starts on the next request (and Fly's health checks do not keep
  it awake); the cold start (machine + Neon wake) stays within the deploy's 15 s HTTP timeout and 4 tries;
  Neon accepts psycopg's channel binding; the Neon connection string's exact shape.

## Operations

### Migrations

Alembic, forward-only. `fly.toml` runs `alembic -c alembic.ini upgrade head` as the release
command (once, before new machines start); the app process starts with `--skip-migrations` and
refuses to serve unless the schema is at the single head. Locally `dome-api` migrates on start.
A new migration ships in the same release as the code that needs it and must be backwards
compatible with the previous release for the duration of a rolling deploy (additive columns with
defaults first; destructive changes one release later). There are three revisions today
(`0001_initial`, `0002_support_tickets`, `0003_assistant`), with the Phase C tables already present and
empty. Downgrades are not part of rollback (next section).
Before touching production: take a database backup; the release command is not transactional
across statements that PostgreSQL cannot run in a transaction.

### Rollback of a deployment

1. `fly releases --app <app> --image` shows the image of each release (the Deploy workflow labels them
   `git-<12 hex>`); `fly deploy --config deploy/fly.toml --app <app> --image <previous image ref>`
   redeploys it without rebuilding. Simpler for the founder: run Deploy on the previous commit (staging
   first). A one-click rollback action is a follow-up (`docs/HANDOFF.md`).
2. Rolling back **code** is safe at any time: the process is stateless apart from the in-memory
   connection manager, agents reconnect with backoff, in-flight commands are marked
   `outcome_unknown`/`expired` by the start-up sweep (never re-executed).
3. Rolling back **a migration** is not done with `alembic downgrade` in production. If a release
   with a new migration must be rolled back, the previous code must tolerate the new schema (the
   compatibility rule above); otherwise restore the pre-deploy backup and accept the data loss
   window explicitly. Rehearse this on staging before it is ever needed (**not yet done**).

### Backup and restore

- Staging (Neon free plan) has only Neon's 6-hour restore window; its data is throwaway.
- Production backups are Fly Managed Postgres's automated backups (included in every plan; retention to be
  recorded in `docs/OPERATIONS.md`), plus, later, a scheduled logical dump (`pg_dump` through `fly mpg
  proxy`) to an object store the founder controls. Retention follows `docs/DATA_RETENTION.md`; backups contain only what the
  database contains (hashed credentials and session ids, no payloads, no challenge text, no titles).
- Restore procedure to rehearse on staging **before** calling anything production-ready
  (spec §15): create a fresh database, `pg_restore`, point a staging app at it, run the
  integration checks, measure the time it took. The result and date belong in `docs/ACCEPTANCE.md`.
  **Not yet done.**

### Secret rotation

Follow the table above. Founder-pasted secrets: create the new value at its source → paste it into the
GitHub Environment → press Deploy → check one sign-in → revoke the old value at its source. Generated
secrets have no button yet (a "rotate" action is a follow-up): an engineer replaces the value in place on
stdin, for example `printf 'DOME_SESSION_SECRET=%s\n' "$(openssl rand -hex 32)" | fly secrets import -a
<app>` (`docs/OPERATIONS.md` §7). Never `fly secrets unset` a generated secret: the machines restart
without it and the app cannot start, and the Deploy button refuses to generate a new one for a production
app that already has machines. Rotating `DOME_SESSION_SECRET` signs everyone out by design; announce it.
The entitlement key's JWKS only ever serves the current key.

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

Six jobs, no secrets, SHA-pinned actions (tag in the trailing comment). Green on GitHub Actions since
2026-10-11 (run 38488799569 on 2026-10-14 was green on the five jobs that existed then).

| Job | What runs | Needs |
| --- | --- | --- |
| `python` | `shared/python` pytest; `cloud-api` ruff + format check + mypy strict + `alembic upgrade head`/`alembic check` + pytest (real PostgreSQL service container, `tools/dev-idp` started by the suite, uvicorn in-process); `pc-agent` ruff + format check + mypy + pytest (Linux, fake platform) | `postgres:16` service |
| `pc-agent-windows` | the Windows-only pc-agent tests (named pipes, native host, mutex, adapters) | `windows-latest` |
| `node` | `shared/ts` install, `gen:types` drift check, typecheck, tests (cross-language fixtures); `mobile-app` typecheck/lint/test/build; `browser-extension` typecheck/lint/test/build | pnpm store cache keyed on the three lockfiles |
| `integration` | `tests/` — the real `dome-agent` process + real relay + PostgreSQL + OIDC login + fake extension | `postgres:16` service |
| `container` | `docker build -f deploy/Dockerfile --build-arg DOME_BUILD_COMMIT=$GITHUB_SHA .`, then checks the image refuses to invent a signing key outside development and carries the commit | the runner's Docker |
| `deploy-tooling` | `deploy/launch`: ruff, mypy strict, pytest (fake `flyctl`, real `openssl`); `actionlint` v1.7.12 (with shellcheck) over all three workflows | Go on the runner |

## What was actually verified

| Item | Evidence | Tag |
| --- | --- | --- |
| Image | built by CI's `container` job on every push since 2026-10-11; run in the test-kit rehearsal (Linux) | built, not run on Fly.io |
| `entrypoint.sh` | `sh -n`; all four paths exercised locally (staging without key → exit 2; development → generated 0600 PEM; `_B64` → identical PEM and `DATABASE_URL` translated; existing key untouched); shellcheck 0.11.0 clean; a Neon-style `DATABASE_URL` keeps `?sslmode=require&channel_binding=require` (`cloud-api/tests/test_database_url.py`, 2026-10-14) | unit-tested (shell, locally) |
| `fly.toml` | parses; `launch/tests/test_fly_config.py` (no secrets or placeholders, ports, release command, region, trusted ranges); flags and merge rules checked in the flyctl v0.4.110 source | not yet verified on Fly.io |
| Deploy workflow + `dome_deploy.py` | `launch/tests/` (258 tests: order of operations, re-runs, stops before changes, the Stripe values (all or none, shapes, live key refused on staging, removal, `/v1/plans` check, status, agreement with cloud-api's names, events and webhook path), database paths (staging never runs `mpg`; a missing or unusable Neon address stops; production unchanged), the per-environment `fly.toml` copy, production gate, the gate's repository-secret and environment-protection checks against GitHub-shaped API answers, sign-in start and WebSocket checks, DNS states, red/green exit codes, secret hygiene across scenarios); actionlint 1.7.12 + shellcheck clean; SHAs re-checked with `git ls-remote` on 2026-10-14; the flyctl install step run locally against the real release (hash OK; a changed hash stops it before extraction) | written and statically checked; **never run against Fly.io, Neon, Auth0, Cloudflare or GitHub's environments API** |
| `docker-compose.dev.yml` | `docker compose config --quiet` (Compose v5.6.0) | not brought up |
| `ci.yml` | green on GitHub Actions since 2026-10-11 | CI-tested |

Remaining before "deployed": the founder's accounts and the first staging run (`docs/LAUNCH_SETUP.md`),
then the restore rehearsal, a failed-deploy rehearsal and an external uptime monitor before production
is called ready.
