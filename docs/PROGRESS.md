# Progress log

Newest first. Each entry: what changed, what was actually run, evidence tag, what is next.

## 2026-10-20 — Billing (Stripe), protocol 1.3, signed-release pipeline and Screen view Phase 0 built; review fixes applied

What was built (nothing of it is released; production still sells nothing and AI stays off there):

- **Plans and billing (#51), protocol 1.3, migration 0004.** Free / Pro / Pro Plus; AI requests in a 5-hour
  and a weekly window with Free's one-time taste and $2.99 top-ups; Stripe Checkout, Customer Portal,
  webhook, hourly reconciler and account-deletion cancellation (`cloud-api/dome_api/billing/`,
  `docs/BILLING.md`); the Billing page and the pricing page's live states. Evidence: **integration-tested
  with a fake Stripe** (real PostgreSQL); the Stripe adapter **unit-tested** against the SDK; **not
  sandbox-provider-tested** (the Deploy button does not pass `DOME_STRIPE_*` to Fly yet).
- **Signed Windows release pipeline.** `.github/workflows/release.yml` (Azure Artifact Signing in the
  protected `release` environment, Ed25519-signed `latest.json`), the agent's update check, and the
  Download page's signed-release states. Evidence: **unit-tested**; `release.yml` **not yet run**.
- **Screen view Phase 0 (#52).** The WP-0 "Coming" card (**unit-tested** in jsdom, not iPhone-tested);
  `.github/workflows/ffmpeg-lgpl.yml` (**not yet run**); its licence gate (**unit-tested** on Linux);
  `screen/probe.py`, a skeleton with no capture or encode code (**not yet verified** on a device).

Review round applied the same day (29 findings; one partly declined):

- **Billing correctness.** A paid page whose webhook is late is never sold again: checkout and the Billing
  status ask Stripe about the stored page and apply its subscription (no second subscription); the
  reconciler asks Stripe before dropping a stale page or top-up, so a payment whose webhooks all failed
  (wrong signing secret) is applied (`billing.webhook_missed`); fetch-and-apply serialised per account with
  an advisory lock, and `canceled` final per subscription, so a stale snapshot can never reactivate or
  downgrade an account; concurrent checkouts replay one idempotency key and expiry (one page); the
  webhook body cap counts a chunked body while it streams; the page lifetime is
  `DOME_BILLING_CHECKOUT_PENDING_HOURS` (1 to 24); an unknown Stripe price grants no paid plan (logged with
  the account id only); Pro Plus is not sold while the assistant is off (`PLAN_NOT_AVAILABLE`). New
  REST-only codes `CHECKOUT_PROCESSING`, `PLAN_NOT_AVAILABLE` and fields `plans_response.ai_enabled`,
  `billing_status_response.checkout_plans`, inside the unreleased 1.3 MINOR.
- **Honest reset time.** When the window and the week are both full, the reason and `resets_at` are the
  later one (new fixture cases in `shared/protocol/fixtures/ai-window-cases.json`; cloud-api, shared and
  phone agree).
- **Website honesty.** Layouts and routines say "Coming" (never "at paid launch"); every AI allowance says
  "Not switched on yet" until `GET /v1/plans` says AI is on; the FAQ says prices are tax included; the
  landing page and FAQ follow `billing_enabled` (`lib/useBillingAvailability.ts`); the Download page says
  "everything else in DoMe is included" and that Smart App Control is not yet verified; the build refuses
  an unsigned `windows-release.json` (`scripts/check-release.ts`, first step of `pnpm build`).
- **Release integrity.** `release_files.py verify --setup` hashes the installer itself in both verify steps.
- **FFmpeg build blocker.** n8.1.3's configure cannot select `buffer`/`buffersink`/`abuffer`/`abuffersink`
  (always built) and only warns, so the gate's filter check failed on every run. The four flags are gone,
  the gate fails on any "did not match anything" warning (C5) and checks the graph filters in the built
  library (R10, smoke); its tests now run against captured n8.1.3 configure output; more x265, openh264,
  fdk-aac, version3 and non-free cases.
- **Docs.** `WINDOWS_INSTALL.md` / `TROUBLESHOOTING.md` no longer imply signing fixes Smart App Control;
  `LAUNCH_SETUP.md` §11A gives Stripe's costs, §17 keeps one approval rule (only the founder publishes,
  with Run workflow on the default branch; no `v*` tag rule) and an exact PowerShell folder;
  `COST_MODEL.md` models the 1.3 AI caps (at the cap a paid account costs more than it pays; per-request
  cost **not yet verified**); `PRODUCT_AND_PLANS.md`, `ACCEPTANCE.md`, `TROUBLESHOOTING.md` §8,
  `deploy/README.md` and the cloud-api README brought up to date.
- Declined in part: hiding the Subscribe buttons for the whole life of an open (unpaid) page would lock a
  customer who left Stripe's page out of buying for up to 24 hours; instead the server asks Stripe whether
  the page was paid and hides them only then.

Run (Linux, this session): cloud-api `pytest` against real PostgreSQL 458 passed, 1 skipped; pc-agent `pytest`
826 passed, 3 skipped; mobile-app `vitest` 1330 passed, 1 skipped, plus typecheck, lint and `pnpm build`
(with the release check); shared/python 303 passed; shared/ts 144 passed; browser-extension 155 passed; ruff and mypy clean in cloud-api
and pc-agent. Not run: the cross-component `tests/` suite, any GitHub workflow, Stripe test mode, a
Windows or iPhone device.

Next: founder steps in `docs/LAUNCH_SETUP.md` §11A (Stripe on staging) and §17 (signing and the first
release run); an engineer makes the Deploy button pass `DOME_STRIPE_*`; run `ffmpeg-lgpl.yml` by hand
(`docs/HANDOFF.md` items 1, 2 and 4).

## 2026-10-20 — Stripe sandbox set up by the founder; prices are tax-**inclusive** by the founder's decision

- Stripe account `domeremote` created, sandbox (test mode) in use. Prebuilt Checkout chosen as the
  payment surface, matching what cloud-api is being built against.
- Product catalogue created in the sandbox, matching `mobile-app/src/content/plans.ts` exactly:
  **DoMe Pro** $9.99/month and $99.99/year; **DoMe Pro Plus** $24.99/month and $249.99/year;
  **DoMe AI top-up** $2.99 one-off (first created as a monthly recurring price by mistake, archived and
  recreated as one-off). Five price ids held by the founder, not yet in GitHub.
- **Founder decision (2026-10-20): the prices carry `tax_behavior: inclusive`, and stay that way.** The
  alternative, `exclusive`, is the usual US convention (tax added on top at checkout). `tax_behavior` is
  immutable per price, so changing it later means new prices and migrating every subscriber. The
  consequence to carry into any future Stripe Tax decision: with inclusive pricing a $24.99 charge is the
  gross amount and any sales tax comes out of it (≈ $23.33 net at a 7 % rate), rather than being added to
  it. Stripe Tax itself is **off** and must not be enabled without revisiting this.
- The products also carry Stripe's SaaS tax code `txcd_10103001`. Harmless while Stripe Tax is off, and
  useful if it is ever enabled.
- Evidence: **founder-run in the Stripe sandbox**; no repository code is connected to these prices yet
  (the billing build is in flight). Nothing is live and no real money can move.

## 2026-10-20 — Real sign-up and password-reset emails (Auth0 + Resend), founder-run

- The founder added the sending domain `mail.domeremote.com` in Resend (DNS records written into
  Cloudflare by Resend's Cloudflare integration, domain **Verified**), created a sending-only API key
  scoped to that domain, and connected it under Auth0 **Branding > Email Provider** with From
  `DoMe Remote <no-reply@mail.domeremote.com>`. Auth0's **Send Test Email** arrived.
- Auth0's built-in test provider is no longer in the path, so verification and password-reset mail is
  real for the first time. `LAUNCH_SETUP.md` §16 records it as done, with the limits of Resend's free
  plan (3,000/month, 100/day).
- **Gap left open, deliberately:** DoMe does not refuse sign-in for an unverified address. `oidc.py`
  reads the `email` claim and never `email_verified`, so an account can be created with an address the
  person does not control. It matters little today (linking a PC still needs the PC), but it should be
  closed before real customers; the founder decides, because the change can lock out an existing
  unverified account.
- Evidence: **founder-run on the live tenant**; nothing in this repository changed except these notes.

## 2026-10-20 — Screen view (#52) designed: design brief + ADR-0002 (nothing built, nothing run)

- `docs/design/screen-view.md` and `docs/adr/0002-screen-view-transport.md` written through a
  workflow: four research reports (Windows capture/encoding and licences, transport and TURN pricing,
  iOS Safari/PWA behaviour, codebase fit), three independent designs (lean, quality, secure), a judge
  (secure design as the base with the other two grafted in), the author, then two adversarial reviewers
  (security/privacy; feasibility/cost/licensing) whose 15 findings were verified and applied: TURN
  spending bounded server-side, the Free ledger settled on the relay's clock rather than agent-reported
  seconds, PC-wide consent cooldown with back-off, `PairPage` keeping the pairing code until the PC key
  pin is verified, aioice address logging redacted, the PC indicator failing closed, logout ending a live
  stream, the `wrapped_avframe` decoder the `lavfi` device needs, PLI reaching the pre-encoded track,
  1.2 agents shielded from 1.3-stamped batches, and the rest recorded in the design's §18/§19.
- Headline decisions: WebRTC peer to peer (aiortc in a separate send-only `DoMe.exe screen-worker`;
  Safari's own `RTCPeerConnection` on the phone); media never passes through Fly; signaling rides the
  relay as phone-signed offers and PC-signed answers against a PC key pinned at pairing; our own LGPL
  FFmpeg + PyAV build with `ddagrab → h264_mf` and a VP8 fallback; `screen` and `screen_audio` granted
  only in a DoMe window on the PC; a "Screen view on" bar on every monitor; Cloudflare Realtime TURN
  minted for the phone only, with per-account caps and hourly reconciliation; Free's 10 minutes per
  5-hour window as a server ledger; protocol 1.3 (additive MINOR). Cost ≈ $0 at 100 MAU and ≈ $1–3 a
  month at 1,000 MAU; a stress case is bounded by the guard.
- Evidence: **design only**; vendor prices and several WebKit/Windows behaviours are tagged
  [research, verify] in the document; nothing is unit-, integration- or device-tested. Status of
  ADR-0002 is "proposed" until Phase 0's gate numbers exist.
- Next: the founder's eight decisions (§22) and setup steps (§20: a Cloudflare TURN key, counsel on
  H.264, code signing); WP-0 ("Screen view: Coming" card); Phase 0 spikes.

## 2026-10-16 — DoMe is live at domeremote.com; first real PC + iPhone run; installed copy names `DoMe.exe`

- **Live.** Production deploy #4 (commit 7a590ad) runs on Fly.io (Managed Postgres Basic, iad); the founder
  added the apex A/AAAA records in Cloudflare as DNS only, `www.domeremote.com` redirects to the apex with a
  Cloudflare redirect rule, and sign-in works on https://domeremote.com. DNSSEC is being turned on.
  `domeremote.app` stays parked by the founder's choice (LAUNCH_SETUP §12's domeremote.app part is for later).
- **First real-device run (owner smoke test, not the full checklists).** `DoMe-Setup-0.1.0+a686dda` installed
  on the founder's Windows PC as a standard user; `DoMe.exe link` linked it to production after `DoMe.exe
  unlink` cleared an older test-kit link; an iPhone paired; the founder reported touchpad (move, tap, two-finger
  tap, two-finger scroll, drag), live typing into Notepad and the Windows volume slider "work great". Recorded
  in `docs/ACCEPTANCE.md` Scenarios 16, 18 and 19 as owner-reported rows; Windows build, iOS version and
  Safari/home-screen mode were not recorded, so the scenarios stay open.
- **Wording fix.** The installed copy is run as `DoMe.exe`, but its messages said `dome-agent link`,
  `dome-agent unlink`, `dome-agent repair` and so on. `settings.command_name()` gives `DoMe.exe` in the
  frozen build and `dome-agent` from source; every user-facing message uses it.
  `pc-agent/tests/test_link_pairing.py::test_messages_name_the_installed_command`. `docs/WINDOWS_INSTALL.md`
  "Linking" now describes linking to the live service and the "already linked" case.
- **Screen view (#52).** The founder looked for it on the phone: it is not built (the website says
  "Coming"). Its design started: research, three competing designs, a judge and an adversarial review,
  writing `docs/design/screen-view.md` and `docs/adr/0002-screen-view-transport.md`.
- Tests run: pc-agent 635 passed, 3 skipped; ruff clean; mypy clean (default and `mypy-windows.ini`).
  **Evidence tag: unit-tested** for the wording fix; the device run is owner-reported.
- Next: Screen view design → contract → implementation; a tray "Link this PC…" item so nobody needs PowerShell.

## 2026-10-14 — Neon launch review fixes: one app per environment, no Neon in production, channel binding, a paused Neon (not run against Fly.io or Neon)

A review of the Neon change (entry below) found that one wrong repository variable could deploy staging into
the production app, that production could quietly run on staging's free Neon database, and that a month's
used-up Neon hours would also block every production deploy without the summary or the guide saying so.

- **Staging and production must be two different Fly apps.** `check_distinct_apps` stops the `gate`,
  `deploy` and `status` runs before any request or `flyctl` call when `FLY_STAGING_APP` / `FLY_PRODUCTION_APP`
  make both resolve to one app (either token reaches every app of the organization). Before, a staging run
  with `FLY_STAGING_APP=domeremote-production` overwrote production's Managed Postgres `DATABASE_URL` with the
  Neon address and deployed staging's settings and sleeping machine into production.
- **No Neon in production.** A production `DATABASE_URL` whose host is `*.neon.tech` stops the run in the
  input check, with the step to delete it from the production environment. The GitHub `DATABASE_URL` is now
  used for production only when Fly will not list the clusters or the attach sets no address; when Fly lists
  them and production's is missing, the run stops for the $41 consent even with that secret set, and names
  any other clusters ("do not tick: ask an engineer first"). Docstrings, `deploy.yml` comments and
  `deploy/README.md` now say exactly that.
- **Channel binding required.** Staging's address must carry `channel_binding=require` with `sslmode=require`
  or `verify-ca` (`sslmode=require` alone encrypts but does not check the server's certificate); only
  `sslmode=verify-full` may go without it. The "or just `?sslmode=require`" wording is gone everywhere.
- **A paused Neon database is named.** When staging's `/healthz` answers 503 or its database is not ok, the
  production gate (both jobs), `status` (staging, and a note on production's) and a failed staging smoke check
  tell the founder to look at https://console.neon.tech for used-up compute-hours; a staging `flyctl deploy`
  that fails says the same for a database the migrations could not reach. `LAUNCH_SETUP.md` section 6 no
  longer says "production is not affected": production keeps running, but no new version reaches it until
  staging works again (wait for the next month, or ask an engineer about Neon's paid plan).
- **Docs.** Neon's default branch is called `production` (with a `development` child) in new projects,
  according to Neon's docs as shown in search results (changelog 2025-04-18): the guide says to keep
  `production` in the Connect window and names it in the password reset, as do the program's repository-secret
  message and `OPERATIONS.md`. Neon's Free storage is 1 GB per project since 2026-10-02 (Neon's changelog and
  blog as shown in search results; its FAQ page still showed 0.5 GB): `LAUNCH_SETUP.md`, `COST_MODEL.md` and
  `deploy/README.md` updated, still "as of October 2026". The guide's status note says Neon's screens come from
  search results. Section 14 now has a row for every new message (the three `DATABASE_URL` messages that had
  none, channel binding, the Neon address in production, one app for both, the consent with other clusters,
  Neon not answering, a failed staging deploy) and the Neon password reset for a repository copy of
  `DATABASE_URL`; section 11 says to delete a production `DATABASE_URL`. `deploy.yml`'s status line names
  Neon. `SECURITY.md`, `OPERATIONS.md`, `DATA_RETENTION.md`, `HANDOFF.md`.
- **What ran here**: `deploy/launch` ruff, format check, mypy strict, pytest 205 passed (was 180; the new
  tests use the fake flyctl, and each new check was removed once to see its test fail); cloud-api (unchanged)
  ruff, format check, mypy strict, full suite 340 passed, 1 skipped against the local PostgreSQL 16;
  actionlint 1.7.12 with shellcheck 0.11.0 on all workflows, shellcheck on `entrypoint.sh`: clean; YAML and
  TOML parse.
- **Not run**: anything against Fly.io, Neon, Auth0 or Cloudflare. Nothing is live. neon.com could not be
  fetched from this environment (DNS); the Neon facts above are from search results.
- **Evidence**: written and statically checked; unit- and integration-tested against fakes.

## 2026-10-14 — About $50 a month: staging on Neon's free plan and asleep when idle, production unchanged (not run against Fly.io or Neon)

The founder chose a budget of about $50 a month. Production keeps Fly Managed Postgres exactly as built (Basic,
`iad`, about $41 a month plus about $4 for its always-on machine). Staging moves to a free Neon database and a
machine that sleeps when nobody uses it (about $4 a month at most). Before: about $90 a month for both.

- **Per-environment database mode** (`deploy/launch/environments.toml`, `dome_deploy.py`). Staging is
  `external`: the Deploy button never lists, creates or attaches Fly Managed Postgres for it and never shows
  the database cost consent. It requires the GitHub Environment secret `DATABASE_URL` (Neon's direct
  connection string), checks it without printing it (a `postgres(ql)://` URL with user, password, host and
  database; no `-pooler` host; `sslmode=require`/`verify-ca`/`verify-full`; no stars for a password), stops
  with a "needs you" summary naming the Neon screen and the GitHub field when it is missing or unusable, and
  stores it in Fly on every deploy, so a Neon password reset is "paste in GitHub, press Deploy". Its password
  is masked on its own. A staging app with its own `DOME_DATABASE_URL` Fly secret stops for an engineer.
  Production is `fly-managed`, unchanged, with the GitHub secret still only a fallback. The workflow's env
  name is now `DEPLOY_DATABASE_URL` (was `DOME_FALLBACK_DATABASE_URL`), and the checkbox reads "Production
  only"; `environments.toml` is validated on load (production must stay on).
- **Staging sleeps.** Each deploy writes `deploy/fly.<environment>.generated.toml`: `deploy/fly.toml` with only
  `auto_stop_machines`, `auto_start_machines` and `min_machines_running` changed (staging `"stop"`, `true`, `0`;
  production `"off"`, `false`, `1`, the base file's own values). It refuses to deploy unless the copy parses to
  exactly that, and deletes it afterwards, also on failure (git ignores it). flyctl v0.4.110's `deploy` has no
  flag for these (its `deploy.go` and `flag.go` were read). The production gate and `status` ask a sleeping
  staging up to 4 times, 10 s apart.
- **Neon-safe connections** (cloud-api; behaviour unchanged, now proven): the entrypoint keeps the URL query,
  SQLAlchemy hands `sslmode` and `channel_binding` to psycopg, and libpq 18 (psycopg-binary) enforces both
  against a stand-in server; `pool_pre_ping` (already on, with `pool_recycle=600`) replaces a pooled
  connection whose backend was ended, and a control shows the same request fails without it. The direct host
  is used, not Neon's PgBouncer `-pooler` (transaction mode; psycopg prepares statements server-side after 5
  runs; Neon's support for protocol-level prepared statements there was not confirmed). `DECISIONS.md` 68.
- **Docs**: `LAUNCH_SETUP.md` gains section 6 (Neon: account, project `domeremote-staging`, PostgreSQL 16,
  AWS `us-east-1`, Connect with pooling off, paste as staging `DATABASE_URL`, password reset) and renumbers
  the later sections; the staging run no longer ticks the database; costs are staging ≈ $4, production ≈ $45,
  total ≈ $50; troubleshooting rows for the new messages (three `DATABASE_URL` messages and the Neon
  password reset for a repository copy were missing; added by the review fixes above). `COST_MODEL.md` §2 Neon row (Free: 0.5 GB, 100
  CU-hours, scale to zero after 5 minutes, 6-hour restore window, as of October 2026, sources linked) and §2A
  rewritten. `deploy/README.md` ("Staging database (Neon)"), `OPERATIONS.md` (Neon's restore window is
  staging's only backup; rotating the Neon password; the restore rehearsal needs a temporary Managed Postgres
  cluster now; DB pool row corrected to 600 s), `SECURITY.md`, `DATA_RETENTION.md`, `HANDOFF.md`.
- **Neon facts**: neon.com could not be fetched from this environment; the Free-plan figures and console
  words come from Neon's own pages as shown in search results on 2026-10-14 and are marked "as of October
  2026" and *(per vendor docs, not yet exercised)*.
- **What ran here**: `deploy/launch` ruff, format check, mypy strict, pytest 180 passed (was 136); cloud-api
  ruff, format check, mypy strict (`dome_api tests`), `tests/test_database_url.py` (11) with the settings and
  OIDC tests (61 passed together) against the local PostgreSQL 16, and the full cloud-api suite: 340
  passed, 1 skipped; actionlint 1.7.12 with shellcheck 0.11.0 on all workflows and shellcheck on `entrypoint.sh`:
  clean; YAML and TOML parse.
- **Not run**: anything against Fly.io, Neon, Auth0 or Cloudflare. Nothing is live. Unverified until the
  first staging run: Neon's real connection string and channel binding, Fly stopping the idle machine (and
  its health checks not keeping it awake), the cold start after a nap, Neon's monthly hours in practice.
- **Evidence**: written and statically checked; unit- and integration-tested against fakes and a local
  PostgreSQL.

## 2026-10-14 — Launch review fixes: gate checks GitHub's own controls, stricter checks, founder guide (not run against Fly.io)

A review of the one-button launch found ways the founder's approval could be bypassed or misdirected,
and places where a run could be green while something was broken. Fixed:

- **Gate** (`dome_deploy.py gate`, still no secret values): refuses while `FLY_API_TOKEN`,
  `DOME_OIDC_CLIENT_SECRET` or `DATABASE_URL` exists as a repository secret. GitHub evaluates
  `secrets.X != ''` and passes names only. A repository secret would otherwise reach the deploy job
  silently, and reach any workflow run without approval. The gate also reads both GitHub Environments with
  the job's own token (`actions: read`) and refuses unless each exists, has a required reviewer, has no
  administrator bypass, and allows exactly one branch rule: the default branch.
- **Checks after deploy**: the sign-in start (`/v1/auth/login` → 303 to the provider's
  `authorization_endpoint` with this client id and callback) and a WebSocket upgrade to `/ws/agent` through
  Fly's proxy. A custom host that answers unhealthy (or, in production, with another commit) is now a red
  run. `status` is red for unhealthy, unreachable or a bad `forwarded_client`, and stays green while it
  waits for DNS or the certificate.
- **Supply chain**: flyctl v0.4.110 is downloaded from its release and checked with `sha256sum -c`
  (hash from the release's checksums file, re-fetched and matched) instead of `setup-flyctl`, which
  verified nothing. Only the binary is extracted. The program runs as `/usr/bin/python3 -I`. `flyctl
  deploy` adds `--builder-pool=false --depot=false` (read in the v0.4.110 source: otherwise a Fly-side
  flag could pick a remote builder that cannot see the runner's image). The base images in
  `deploy/Dockerfile` are pinned by digest, and there is no environment `url:` (it showed the domain
  as "deployed" before DNS existed).
- **Robustness**: child output is decoded as UTF-8 with replacement, and a failure while streaming
  kills the child process. A refused Fly token is a "needs you" stop with the fix.
- **Docs**: `LAUNCH_SETUP.md` (approve only runs you started; untick the administrator bypass; no
  repository secrets; a words box; vendor addresses; Auth0 tenants explained; Cloudflare's "@";
  $41/$45 consent; private-repository advice corrected; troubleshooting rows for every headline).
  `SECURITY.md` §6 now says the approval comes from the founder's GitHub account and authorizes
  unreviewed default-branch code, with branch protection as a follow-up (§9). `OPERATIONS.md` §7
  replaces the generated secrets in place on stdin: the old `fly secrets unset` step would have taken
  production down.
- **What ran here**: `deploy/launch` ruff, format check, mypy strict, pytest 136 passed. actionlint 1.7.12
  with shellcheck 0.11.0 on all workflows: clean (a control case shows shellcheck runs). The flyctl
  install step was run locally against the real release: hash OK; a changed hash stops it before
  extraction and before the PATH entry. Base-image digests were read from the registries (registry HEAD;
  for python and uv the index body was also hashed).
- **Not run**: the image build with the pinned digests (CI's `container` job builds it), GitHub's
  environments API (its answers in the tests follow GitHub's documented shapes), and **nothing against
  Fly.io, Auth0 or Cloudflare**. Nothing is live.
- **Evidence**: written and statically checked; unit-tested against fakes.

## 2026-10-14 — One-button launch: Deploy workflow, founder guide, hosting fixes (not run against Fly.io)

Goal: going live becomes creating accounts, pasting values into GitHub/Fly/Auth0 settings pages and
pressing one button, with no command line for the founder. Built from a researched plan and an adversarial
critique of it (every blocking and major point applied; see the commit message).

- **The button**: `.github/workflows/deploy.yml` (manual runs only; inputs environment staging|production,
  action deploy|status, a "create the database" consent checkbox). Job `gate` (no secrets, no environment)
  refuses other branches and, for production, any commit staging does not already run healthily; job
  `deploy` (the chosen GitHub Environment's secrets, founder approval on both environments) runs
  `deploy/launch/dome_deploy.py deploy`; job `status` (no secrets, never runs flyctl) reads `/healthz`, DNS
  and TLS. The program (standard library only) creates the Fly app, the free shared IPv4 and IPv6, the
  Managed Postgres cluster (Basic, `iad`, only with consent) and attaches it, generates the session secret
  and the Ed25519 entitlement key only when absent (masked, stdin only), builds the image with CI's
  `docker build` and deploys it with `flyctl deploy --image`, scales to one machine, adds the certificate
  and checks fly.dev and the custom host; the summary gives the next click with exact Cloudflare records.
  `deploy/launch/environments.toml` holds the fixed per-environment facts; `deploy/fly.toml` is now an
  environment-neutral base (region `iad`, no placeholder origin/issuer/client id, so a manual deploy fails
  closed).
- **Secret handling, from the critique**: flyctl v0.4.110's `mpg create` and `mpg attach` print the
  database password, so their output is never shown and `mpg status` is never called; all child output is
  redacted (known values, URL credentials, Fly tokens, key blocks); argv is checked for secret values;
  multi-line values are masked line by line (a test caught a PEM printed through a multi-line
  `::add-mask::`); a generated secret is "absent" only when `secrets list` succeeded and parsed; a
  production app with machines but no generated secret stops for an engineer; the run keeps its own
  80-minute budget so the summary is always written.
- **cloud-api** (`DECISIONS.md` 62–67): the ID token's `iss` is checked against the discovery document's
  exact issuer (Auth0's trailing slash rejected every sign-in before); `client_secret_post` explicitly;
  `DOME_BUILD_COMMIT` and `/healthz` → `commit`; `/healthz` → `forwarded_client` self-check;
  `DOME_CLIENT_IP_HEADER=fly-client-ip` behind private-range trust (the documented `fdaa::/16` could never
  match the IPv4-only socket, and the right-most X-Forwarded-For hop on Fly is the app's own address);
  production `X-Robots-Tag` on non-canonical hosts; pool recycle 600 s. `deploy/Dockerfile` carries the
  build commit; `ci.yml` gained the `deploy-tooling` job and a build-commit check in `container`.
- **Docs**: `docs/LAUNCH_SETUP.md` (new, the founder's guide), `deploy/README.md` (button first, stale
  lines fixed, client addresses, engineer-only `flyctl` with `fly mpg`), `docs/OPERATIONS.md`, `HANDOFF.md`
  (§0, §4, §7 item 0), `SECURITY.md` (§6 deploy pipeline: who can run what, what each token reaches; §9),
  `COST_MODEL.md` §2A (~$45 per environment, ~$90 both), cloud-api README/KNOWN_ISSUES #5, `.env.example`,
  root README.
- **What ran here**: cloud-api ruff, format check, mypy strict and pytest: 329 passed, 1 skipped (the live
  AI test; local PostgreSQL 16 + `tools/dev-idp`). `deploy/launch`: `uv lock --check`, ruff, format check,
  mypy strict, pytest 82 passed (fake `flyctl` with Fly-shaped output read from the v0.4.110 source, real
  `openssl`). actionlint 1.7.12 with shellcheck 0.11.0 on all three workflows: clean (shellcheck integration
  proven with a deliberately bad script). PyYAML/tomllib parse of every workflow and TOML file. The new
  Dockerfile ARG/LABEL/ENV lines replayed on top of the cached CI image: commit in the environment and the
  OCI label, empty without the argument, entrypoint still exits 2 in staging without a key. `git ls-remote`
  re-checked the setup-flyctl and actionlint pins. A grep of the whole change for token, key and
  credential-URL patterns finds only the tests' fake values (assembled at run time so no line looks like a
  real Fly token).
- **Not run**: the full image build (base images need the sandbox proxy; CI builds it on push), and
  **nothing against Fly.io, Auth0 or Cloudflare**: no account exists, and HTTPS to those hosts is blocked
  here. Nothing is live, and no sign-in against a real provider has happened.
- **Evidence**: written and statically checked; unit-tested against fakes.
- **Next**: the founder follows `docs/LAUNCH_SETUP.md` to the first staging run; an engineer reads that
  run's log and summary and fixes whatever Fly answers differently from the source-derived expectations
  (`mpg` with an organization token, attach before machines exist, `forwarded_client` on a live machine).

## 2026-10-14 — First Windows installer build: green

- **What ran**: `.github/workflows/windows-installer.yml` on the push of a686dda (run 38488799434,
  windows-latest). Job "pc-agent tests (Windows)": mypy on the Windows code paths and the Windows pytest
  set (`tests/windows_suite.txt`), green. Job "Build and smoke-test DoMe-Setup": the PyInstaller folder
  (`DoMe.exe`, `dome-native-host.exe`), `DoMe-Setup-0.1.0.exe` (Inno Setup 6, development build), then
  `smoke_test.py` through 13 phases (install with and without start at login and a development extension
  id, files and shortcut, uninstall entry, server address, HKCU native-host keys and manifest, the frozen
  command line and a native-messaging round trip, the tray as a process and `DoMe.exe quit`, upgrades,
  downgrade refusal, a staging address kept across an upgrade, uninstall with and without `/REMOVEDATA`,
  install and uninstall without ever starting DoMe): **all 157 checks passed**, first attempt. Artifacts:
  `DoMe-Setup-0.1.0+a686dda` (Setup, SHA256SUMS, BUILD-INFO.txt) and `installer-smoke-logs`, kept 30 days.
  `ci.yml` run 38488799569 on the same commit: green.
- **Docs**: WINDOWS_INSTALL, HANDOFF, PRODUCT_AND_PLANS and ACCEPTANCE now record this run instead of
  "has not run yet".
- **Evidence**: integration-tested on the windows-latest CI runner (Windows Server, administrator, UAC
  off). **Not Windows-device-tested**: no Windows 10/11 PC, no standard user, no UAC prompt, the tray icon
  and its menu not seen, Smart App Control / SmartScreen behaviour for the unsigned build not observed.
- **Next**: the founder installs the build on his Windows 11 PC as a standard user; production extension
  id and signing before a release build; hosting at domeremote.com (`deploy/README.md` "Domain").

## 2026-10-14 — Windows installer: review fixes

Follows the entry below; a review of the installer commit found no blocker and these issues.

- **Setup never runs DoMe.exe with administrator rights** (`pc-agent/DECISIONS.md` 57): a Setup elevated
  through UAC ("Run as administrator") installs nothing and says so in plain English; an uninstaller in
  that state runs no `DoMe.exe` command and uses its registry fallbacks. With UAC off (the CI runner)
  Setup keeps asking whether it is the right account. Each command's output file moved to Inno's private
  `{tmp}` folder.
- **A release build inherits nothing from a test install**: it always writes `https://domeremote.com`,
  deletes the old `com.dome.agent.json` before `install-native-host`, carries neutral wizard text (the
  test-version wording is compiled only into development builds), and `build_installer.py --release`
  refuses while `PRODUCTION_EXTENSION_IDS` is empty.
- **Uninstall** also removes the empty browser keys `install-native-host` created; its last message tells
  only someone who removed the settings, or will not use DoMe on this PC again, to remove the PC on the
  phone.
- **crash.log** is masked like every other log (bearer tokens, long token-like strings); the crash
  message no longer asks people to send the file (it cannot be attached and is not a reviewed bundle).
- **CI**: the installer artifact is not uploaded for a pull request from a fork; `BUILD-INFO.txt` names
  the branch commit of a pull request (and GitHub's test merge), the event and the branch. The smoke test
  fails when the process list cannot be read, asserts the exit codes of the `DoMe.exe` commands Setup
  and the uninstaller run (so their registry fallbacks cannot hide a broken command), the re-registration
  after a stopped upgrade, the reason the bridge was skipped, that the empty browser keys are gone, that
  Setup did not count as elevated, and that no `crash.log` was written.
- **Docs** made honest: the workflow has not run yet (WINDOWS_INSTALL, HANDOFF, ACCEPTANCE,
  PRODUCT_AND_PLANS); downgrade refusal is per X.Y.Z, and every test build is 0.1.0; the tray is checked
  only as a process; the founder downloads only push builds of the branch the team names; Smart App
  Control; testers re-add `/ExtensionId=` after a reinstall; DATA_RETENTION §2 now says what uninstall
  keeps by default and lists `crash.log` and `dome-server.json`; SUPPORT §6.3 and TROUBLESHOOTING §21 on
  `crash.log`; SECURITY T12; the test kit README on sharing a PC with the installed DoMe.
- **Run here**: pc-agent ruff, ruff format, mypy (both configs) and pytest (634 passed, 3 skipped); the
  installer scripts pass ruff and `mypy --strict` for Linux and Windows; actionlint clean. The spec built
  again with PyInstaller 6.22.3 on Linux, `smoke_test.py bridge` passed (10 checks) and a frozen crash
  ended in `crash.log` with exit 70. `DoMe.iss` compiled (development and release) with Inno Setup 6.7.1
  under wine and passed 80 scenario checks there with a stand-in `DoMe.exe`, including an elevated Setup
  that installs nothing and an elevated uninstall that runs no `DoMe.exe`. Under wine an administrator's
  token always reads as "elevated through UAC", so the other scenarios ran from a scratch copy of
  `DoMe.iss` that differs only in that constant.
- **Evidence**: **not yet verified** on Windows — still awaiting the first `windows-installer.yml` run.
  If its phase 1 check "Setup does not count as elevated through UAC here" fails, the runner's token is
  not what GitHub documents (UAC off), and the elevation rule needs revisiting before anything else.
- **Next**: unchanged (the first CI run and its fixes; the founder's Windows 11 install).

## 2026-10-13 — Windows installer (development build) and its CI

HANDOFF §7 item 4, pulled forward ahead of items 1–3 (which keep their order).

- **One PyInstaller folder** (`pc-agent/packaging/dome-agent.spec`; `dome-native-host.spec` deleted):
  `DoMe.exe` and `dome-native-host.exe` share `_internal\`, so the browser no longer starts a one-file
  host that unpacks itself to `%TEMP%` every few seconds while DoMe is stopped (browser-extension K5).
  Both carry `brand/exports/windows/dome.ico`. The protocol contract is bundled without `fixtures/`
  (the old spec shipped a test private key).
- **Installer** (`pc-agent/packaging/installer/DoMe.iss`, Inno Setup 6, per user, no admin): installs
  into `%LOCALAPPDATA%\Programs\DoMe` with a Start menu entry; writes the server address into
  `dome-server.json` (no environment variable: one would override the addresses stored at link time and
  could send a test-kit credential to production); registers the native host with
  `DoMe.exe install-native-host` when an extension id exists, otherwise says plainly that YouTube
  control is not set up; start at login is an unticked option; an upgrade first unregisters the host and
  runs `DoMe.exe quit`, and stops with nothing changed (exit code 7) when DoMe does not quit; it refuses
  to install over a newer version; every DoMe.exe it runs has 60 s, after which only that command is
  stopped; it warns when run as administrator and never starts DoMe elevated. The uninstaller quits
  DoMe (or stops and leaves everything in place when DoMe does not quit), removes the program, shortcut,
  host keys, manifest, start at login and `dome-server.json`, and keeps `%LOCALAPPDATA%\DoMe` unless
  the user answers Yes or passes `/REMOVEDATA`. `/ApiUrl=` and `/ExtensionId=` exist only in
  development builds.
- **pc-agent**: `quit` (the tray's Quit DoMe from the command line; never kills), `start-at-login
  on|off|status`, a stdout guard so `link`/`pair` no longer crash in a windowed `DoMe.exe` started
  without a console, `install-native-host`/`repair` keep the extension already registered here, `link`
  falls back to `dome-server.json`, the installer-facing commands build only the adapter they need and
  create no state folder, a crash guard on both frozen entry points (`logs\crash.log`, exit 70, never the
  bootloader's modal error box; only the tray shows a short message), and an exited-but-unreaped child
  counts as gone (`_pid_alive`). `pc-agent/DECISIONS.md` 51–56.
- **CI**: `.github/workflows/windows-installer.yml` (Windows tests, then build, smoke test, upload of
  `DoMe-Setup-<version>.exe` with `SHA256SUMS` and `BUILD-INFO.txt` after a passing smoke test, logs
  always; no secrets). The Windows test list moved to `pc-agent/tests/windows_suite.txt`, used by both
  workflows; `ci.yml`'s header no longer claims it never ran.
- **Run here**: pc-agent ruff, ruff format, mypy (both configs) and pytest (632 passed, 3 skipped; the
  new `tests/test_installer_commands.py` included) all pass; the packaging scripts pass ruff and
  `mypy --strict` for Linux and Windows; the spec built with PyInstaller 6.22.3 on Linux and
  `smoke_test.py bridge` passed against it (`DoMe version`, `AGENT_NOT_RUNNING` from the host alone,
  `run --headless`, a `bridge_hello` round trip, `DoMe quit`); a frozen crash ended in `crash.log` and
  exit 70. `DoMe.iss` compiled with Inno Setup 6.7.1 under wine and passed 52 scenario checks there with
  a stand-in `DoMe.exe` (install, upgrade over a running stand-in, an upgrade and an uninstall that stop
  because DoMe does not quit, a hung command stopped after its timeout, downgrade and bad-address
  refusals, keep-data and `/REMOVEDATA` uninstalls, a remembered staging address, a release build
  ignoring the development options, no empty state folder left). actionlint: both workflows clean.
- **Evidence**: **not yet verified** on Windows — awaiting the first `windows-installer.yml` run (a
  Windows Server runner as administrator with UAC off; not a Windows 10/11 PC, not a standard user).
- **Next**: the first CI run and its fixes; the founder installs it on his Windows 11 PC; a tray "Link
  this PC…" item; a store listing and the production extension id; signing in a separate protected job.

## 2026-10-13 — Design A round: domain, two polish rounds, documentation

Follows the rollout entry below (commits 0a7bf6a, b30f0c9, 6d76945 and this documentation commit).

- **Domain** (0a7bf6a): the founder registered `domeremote.com` and `domeremote.app` (Cloudflare
  Registrar) today. `https://domeremote.com` is the one origin for the website, PWA, API and WebSockets;
  `staging.domeremote.com` for staging; `.app` and `www` will only redirect, because the session cookie,
  the Origin check and the phone's signing key belong to one origin. In the code: Open Graph / Twitter
  tags with a 1200×630 preview image, `robots.txt` and `sitemap.xml` (a test keeps the sitemap equal to
  the router's public routes), `X-Robots-Tag: noindex, nofollow` outside production (cloud-api), the
  extension's `homepage_url`. The DNS, redirect, DNSSEC and identity-provider plan is in
  `deploy/README.md` "Domain"; **none of it is configured yet** (no Fly apps exist).
- **Polish round 1** (b30f0c9), from screenshot reviews of the website, the film and the app: the hero's
  "Volume set to 40%" toast only from 1280 px and never over a call to action or the caveat (a
  real-browser test measures it); copy that describes the AI exactly as the app runs it; compact pricing
  rows on phones; skip link, page titles, a focusable main, Escape closes the menu, no sideways scroll at
  320–430 px; film scene 5 on an opaque veil, the end card's real links, a readable vertical layout, the
  lit room as poster, captions silent until the visitor starts the film; in the app a PC-offline warning
  with two steps on Home and Remote, Touchpad fitting above the tab bar at 390×844 and 375×667, focus
  never hidden under the tab bar or the Ask dock, status lines instead of disabled buttons, a
  control-edge token for switches that are off, empty slider tracks and field borders.
- **Polish round 2, checkpoint** (6d76945; the round is not finished): Settings words Free's AI
  allowance as a one-time taste ("4 of 10 AI requests to try used", no renewal date); Health says "1
  item needs attention"; the vertical film draws the lamp's glow only where the lamp is and raises the
  caption band so a two-line chip fits; the preview's `?pc=offline` sends nothing at start and answers
  commands with PC_OFFLINE, as the relay does. Tests added for each.
- **Documentation** (this commit): `mobile-app/README.md` (design system and themes, the preview routes,
  the film and its export script, the plan content module, the new tests, an iPhone checklist row),
  `mobile-app/DECISIONS.md` 60–63 (Design A over B; the film as a vector animation with an export
  pipeline; plan content as static copy until metering; honesty labels), `brand/BRAND.md` §2 "As
  shipped" (the app's dark theme uses Navy as the page, not the recommended near-black), `docs/HANDOFF.md`.
- Runs (Linux, at 6d76945 plus this documentation): mobile-app `pnpm typecheck`, `pnpm lint`, `pnpm test`
  (68 files, 1218 passed, including the real-browser hero layout test at 1024/1280/1440/1920 px, which
  runs here because Playwright is installed), `pnpm build` (bundle check: no key material, no design
  preview); brand `pnpm check` (33 tests, exports and consumer copies up to date); browser-extension
  `test/manifest.test.ts` (5 passed); cloud-api `test_security_fixes.py` header test (staging noindex,
  production HSTS; 1 passed). The film was re-exported after 6d76945: 1920×1080 and 1080×1920, 30 fps,
  66.0 s, MP4 + WebM + poster (ffprobe), and a contact sheet of the six scenes was looked at.
  **Evidence tag: component-tested** (app and website), **looked at** (film frames), **not yet verified**
  on an iPhone; the domain is registered but not configured.
- **Still open**: polish round 2; the main JS chunk is 890 kB (278 kB gzip; Vite warns over 500 kB) and
  holds the website and the film, so splitting the public pages from the app is worth doing; the
  film's autoplay-when-visible is untested (jsdom has no IntersectionObserver); `plans.json` has no Pro
  Plus, still says `pricing_defaults` 599 / 4999 and a monthly AI allowance, and `docs/BILLING.md`,
  `COST_MODEL.md`, `PRODUCT_AND_PLANS.md` keep the old price (all for #51); Screen view and point-and-talk
  exist only as website copy and film scenes.
- Next: plans metering (#51), then Screen view (#52), the butler's click by name (#42) and the Windows
  installer; `docs/HANDOFF.md` §7.

## 2026-10-13 — Design A "Midnight Studio" rollout, demo film, Free / Pro / Pro Plus content

Three builders worked in parallel worktrees from `docs`-free briefs (app, public website, film);
the integrator merged them (one merge commit each, no conflicts: disjoint files) and reconciled copy.

- **App** (`mobile-app/src/styles.css`, `components/ui.tsx`, `icons.tsx`, every `pages/app/**`): the
  design system's component classes and primitives (wells, segments, list cards, key-value rows,
  chips, check rows, back links), every app screen restyled in both themes with every behaviour,
  accessible name, test id and safety confirmation kept; critic fixes (opaque tab bar/dock with a
  no-backdrop-filter fallback, three-column Ask dock, tinted icon wells so the one solid-blue
  control per screen is the primary action, drawn icons instead of glyphs); the `<dialog>` sheet now
  sits at the bottom of a phone screen (pre-existing bug). The dev-only design preview covers every
  app route plus `?offline=1` and `?confirm=1`.
- **Website** (`pages/public/**`, `app/PublicLayout.tsx`, `styles/public.css`, `content/plans.ts`,
  `content/faq.tsx`, `content/nav.ts`, `lib/pricing.ts`): Free / Pro / Pro Plus as static content
  (Free $0 "Available now"; Pro $9.99 / month or $99.99 / year and Pro Plus $24.99 / $249.99 with the
  status "Coming at paid launch"; 1 PC + 2 phones vs 5 + 5 read from the plan contract; Screen view
  10 min per 5-hour window vs unlimited, marked "Coming"; AI butler one-time taste of 10 vs 30 / 120
  vs 90 / 300 per 5-hour window / week; $2.99 top-up for 100 requests, never automatic); pricing page
  with three cards and a grouped comparison, FAQ by topic, Download / Support / Release notes / legal /
  404 restyled (legal body text byte-identical), Demo link first in the nav.
- **Film** (`components/film/**`, `styles/film.css`, `pages/public/DemoPage.tsx`, `/demo`,
  `scripts/export-film.mjs`): a 66 s SVG + CSS film on one deterministic clock (every frame a pure
  function of t) in six scenes with honest chips ("Screen view · Coming", "Point and talk · Planned",
  "Pro and Pro Plus arrive at paid launch"); controls, keyboard, captions in an aria-live region,
  autoplay only when in view, a storyboard of stills under `prefers-reduced-motion`, a vertical 9:16
  restack; the home page gets a "See it in action" section and a three-plan closing teaser
  ("Compare plans"). The export script steps the clock frame by frame through Playwright and ffmpeg
  (1920×1080 and 1080×1920 MP4 + WebM, poster); exports are never committed.
- **Integrator reconciliation**: the in-app Billing page and ProExplanation now read the paid plans'
  prices and the "coming at paid launch" status from `content/plans.ts` (they said "planned at
  $5.99"), with a new `test/components/BillingPage.test.tsx` and tightened upgrade-copy guards.
  `shared/protocol/plans.json → pricing_defaults` (599 / 4999 cents) and `docs/BILLING.md`,
  `docs/COST_MODEL.md`, `docs/PRODUCT_AND_PLANS.md` still carry the old experiment price: they belong
  to the metering task that builds the real plans.
- Runs (Linux, merged tree): mobile-app `pnpm typecheck`, `pnpm lint`, `pnpm test` (66 files,
  1198 tests passed), `pnpm build` (check-bundle clean); brand `pnpm check`. The film re-exported from
  the merged build. **Evidence tag: component-tested** for the app and website; **looked at** (phone and
  desktop screenshots, both themes, and extracted film frames); **not yet verified** on a real iPhone.
- Next: the real Free / Pro / Pro Plus metering (5-hour and weekly AI windows, top-ups, Stripe prices,
  `plans.json` limits), then Screen view.

## 2026-10-11 — Optional AI voice butler, step 1 (Phase E, behind consent and allowances)

The owner asked for a Jarvis-like butler they talk to; they chose a formal British butler, Claude
Opus 5.5, two steps (screen clicking later, always shown first), and later: no screenshots at all.

- **Design** `docs/design/assistant.md` (reviewed by security/cost, iPhone voice and integration lenses).
  Deterministic first: the phone's own parser handles what it understands (free, instant, no AI);
  only what it cannot understand goes to `POST /v1/assistant/turn` (consent checked every turn).
  The model returns the same plan representation as the Type grammar (up to 3 AI-eligible steps,
  app names as spoken) or a question/answer/decline; the phone resolves app names locally, shows the
  plan, signs every command and speaks a butler sentence built from the real results (no second
  model call). Sent to Anthropic: the words, at most six recent lines from the last 10 minutes and a
  closed set of enums/numbers; never audio, screenshots, app lists, titles, clipboard or typed text.
- **cloud-api**: provider interface (fake + Anthropic SDK 1.x adapter: `claude-opus-5-5`, effort low,
  structured outputs, server-side fallbacks, refusal -> `AI_DECLINED`, prompt caching of the frozen
  prompt + catalogue), output validation against the registry (AI-eligible only), metering ledger
  with reservations, per-account cap, operator ceiling, rate limit, one turn in flight, sweeper; no
  words in logs or tables (sentinel tests). Migration 0003.
- **Phone**: the Ask screen (push-to-talk with Safari speech recognition, keyboard-dictation and
  typed fallbacks, en-GB speech unlocked on the first tap, mute), consent and Settings (name, form of
  address, voice, "Ask before every action"), plan cards reusing the Type plan runner, honest
  fallbacks when AI is off, down or used up; `pnpm build` refuses a bundle containing key material.
- **Test kit**: the owner's key in `testkit\ai-key.txt` (git-ignored, never printed), an optional
  monthly limit file and step 4 "Apply AI settings" (rebuilds the server when its sources changed).
- Reviews: 25 findings (4 major) all fixed with tests that fail on the old code.
- Runs (Linux): 2,688 tests, 2,684 passed, 4 skipped, 0 failed (shared/python 264, dev-idp 14,
  cloud-api 316 + 1 live test skipped without a key, pc-agent 604 + 3, shared/ts 124, mobile-app 1116,
  browser-extension 154, integration 30); ruff/mypy/tsc/eslint clean; alembic check; testkit 270;
  actionlint.
- **Not yet verified**: the real Anthropic API (no key here; the owner's first call), the iPhone voice
  behaviour. Plans in `plans.json` keep AI allowances at 0 until the owner's new Free / Pro / Pro Plus
  plans (5-hour and weekly limits) are built next, after the brand and UI redesign.

## 2026-10-11 — Protocol 1.2: window controls, YouTube search, much richer Type, tray "Add an app..."

Built from the owner's critique of Type ("full screen <app>", "make the volume 100%", "bring <app> to
the front", "search ... on YouTube", "play the second video") and their request for a tray button to
approve apps. Designed by five read-only investigators, built by agents per component, then five
adversarial reviewers (compatibility, Windows, Type grammar, security/privacy, UX) and fixers.

- **Contract 1.2** (additive): `app.maximize`, `app.restore`, `app.set_fullscreen{enabled}`,
  `youtube.search{query}`, `youtube.open_result{index,video_id?}`; five new errors; per-action `since`
  with version stamping, so a phone updated by the server keeps working with older PCs (new actions get
  a clear "update the PC" error); bridge version negotiation; search text is never logged, journaled,
  persisted or echoed (also not through browser window titles, a leak the security review found).
- **PC**: reliable Bring to front (restore, SetForegroundWindow, empty mouse input, AttachThreadInput,
  polled), Maximise/Restore, Full screen by one F11 press only after re-checking the approved window is in
  front and no key is held, never a silent maximise. F11 is pressed only in apps that use it for full
  screen (browsers, Explorer, VS Code, ...) or that the owner allowed per app (**Approved apps... > Full
  screen with F11**, stored per app); otherwise the phone shows how to allow it and offers Maximise.
  Elevated apps, hung windows, cloaked windows (other desktops) and multi-window apps are handled.
- **Tray**: **Add an app...** (Windows file picker, suggested name from the program's description,
  unique id, Store/launcher warnings, errors in plain words) and an **Approved apps...** window (list,
  re-approve after an update, remove, Full screen with F11). Diagnostics shows why the browser bridge or
  a Windows read is failing.
- **Extension 0.2**: YouTube search in the chosen tab or a new tab, up to 10 primary results (ads and
  Shorts skipped), open result N only when that video is reached; results pages publish no title; play()
  refused by the autoplay policy reports `ACTIVATION_REQUIRED`.
- **Phone**: Apps page Maximise/Restore/Full screen (and "Maximise instead"), app list refreshed when an
  app is added on the PC or opened/closed; Remote "Search YouTube" card; Type understands far more
  (number words, %, max/half, relative volume, seek, theater, search, "play X on YouTube", result picks,
  fuzzy app names incl. aliases, open/switch/bring to front/minimise/maximise/full screen/restore/close),
  runs up to three steps in one sentence shown as a plan first, pauses the other playing video when
  opening a result, and is honest about what it cannot do ("click on that movie" in another app: DoMe
  cannot see the screen, use the Touchpad; captions/speed not supported yet).
- **Runs (Linux)**: shared/python 210 (ruff clean), shared/ts 116, cloud-api 126, pc-agent 604 + 3
  skipped (ruff, format, mypy, mypy-windows clean), browser-extension 154, mobile-app 715 (typecheck,
  lint, build clean; a load-dependent flake fixed and proven with slowed signatures), tests/ integration
  30 (incl. a 1.1 agent with a 1.2 phone/relay), testkit 121 checks, actionlint clean. Windows CI runs
  the pipe, native host, window control, tray and adapter tests on windows-latest.
- **Owner's devices (Windows 11 + iPhone Safari, after step 1, step 2, sign-in and pairing)**: the owner
  reports that everything on the test list works: the Remote volume slider, YouTube control in Edge and
  Remote > Search YouTube, the new Type phrases, the Apps window buttons and Lock. Not yet reported:
  sleep/restart/shut down. The owner then asked for a voice assistant that talks back and can act on what
  is on the screen ("click on that movie"); see the next entry once decided.

## 2026-10-11 — Second Windows/iPhone test round: volume, YouTube bridge, Windows adapters

Owner's report: approved apps, touchpad and keyboard work; the Remote volume slider and Type volume
phrases fail; YouTube control never works; Type is too limited (wants "full screen X", "bring X to the
front", "volume 100%", "search YouTube for ...", "play the second video").

- **Volume** (52dee5b): the locked pycaw returns an `AudioDevice` wrapper from `GetSpeakers()` with no
  `Activate()`, so every volume read/set/mute raised AttributeError (shown as a Windows error). Uses
  `EndpointVolume` now (falls back for older pycaw); COM pointers are released before `CoUninitialize`.
  The Type phrase itself parsed fine.
- **YouTube was blocked twice.** (1) The browser bridge's Windows named pipe was opened synchronous while
  one thread read and another wrote, so the native host's first `bridge_hello` waited forever behind its
  own pending read (Windows serialises I/O on a synchronous handle; Linux sockets do not, so every test
  passed). Both pipe ends now use overlapped I/O (stdlib `_winapi`, as `multiprocessing` does), cancel
  pending I/O before closing, retry a busy pipe, open at identification level and check the server's
  user/session; the accept loop survives per-connection errors and never lets the pipe name lapse.
  (2) The agent acked the extension's hello with its own version 1.1 while the extension only accepted
  1.0, so it disconnected as incompatible. The ack is now negotiated (highest version both list), the
  extension lists 1.0 and 1.1, the fake extension refuses an ack it would not accept, and a test reads
  the shipped extension's version list.
- Native host exits when the agent goes away (the browser then reconnects); the extension reconnects
  fast for 5 minutes (1, 2, 4, 8 s) and every 30 s after, Retry always reconnects, and the content script
  is injected into YouTube tabs that were already open (no reload needed). Plain popup titles.
- **Windows adapters audited against the locked wheels**: power errors on a locked PC map to
  `PC_SESSION_LOCKED`, media results are None-guarded, `session.py` ctypes signatures fixed and unknown
  session flags no longer count as unlocked, Start at login registers `pythonw.exe`, adapter read failures
  are logged (rate-limited) and shown by `dome-agent status`; CLI output never crashes on a cp1252 pipe.
- **Test kit**: step 2 rebuilds the extension automatically when its sources changed (fingerprint in
  `testkit/.state`) and asks to reload it in Edge; a failed rebuild keeps the previous build.
- **CI**: new `pc-agent (Windows)` job on windows-latest runs the pipe, native-host, mutex and Windows
  adapter tests on real Windows; CI now also runs on `claude/**` pushes.
- Runs (Linux): pc-agent 374 passed, 2 skipped, ruff/format/mypy/mypy-windows clean; browser-extension
  110 passed, typecheck/lint/build clean; tests/ integration 23 passed; testkit checks 0 failed;
  actionlint clean. **Unverified until Windows CI and the owner's PC**: the overlapped pipe itself.
- In progress: protocol 1.2 (window actions maximise/restore/full screen, YouTube search and "open
  result N", per-action version stamping), a much richer Type grammar, and a tray "Add an app..." button.

## 2026-10-09 — First test-kit run on the owner's Windows PC

- Step 1 on Windows 11 with Docker Desktop: uv found, agent prepared, test data cleared, both Cloudflare
  quick tunnels created (first real check of the tunnel path). The build then stopped with
  `failed to get console: The handle is invalid`: Docker's animated progress display needs the console
  window, and the kit passes program output through PowerShell. Fix: `--progress plain` on every
  compose call and on the extension build, plus `BUILDKIT_PROGRESS=plain`. Re-checked here: the start
  script builds with plain output and the stack comes up.
- Re-run: **step 1 passed on Windows end to end** (build, api healthy, public addresses reachable). Step 2
  stopped at the agent's status: `dome-agent status --json` raised `AttributeError: module
  'win32process' has no attribute 'ProcessIdToSessionId'` (it lives in `win32ts`). The pipe server
  also took `FILE_FLAG_FIRST_PIPE_INSTANCE` from `win32con` (it is in `win32pipe`). Both fixed. New
  guard: `types-pywin32` in the agent's dev tools and `mypy --config-file mypy-windows.ini` (CI) type-
  checks every Windows code path against the pywin32 stubs; it reports the original bug when it is put
  back. The kit now prints the agent's own error output when its status cannot be read.
- Next run: step 2 completed and the tray icon appeared. Pairing from the iPhone was refused with "This
  request was not allowed" (403 FORBIDDEN: a wrong origin or a CSRF token that no longer matches the
  session cookie, for example after signing in again in another tab). The phone app now refreshes its
  CSRF token from GET /v1/session and retries a refused state-changing request once, only for the same
  account; the api logs the reason of every 403 (`request.refused`).
- Restart from a fresh ZIP: step 1 stopped at "Making the QR code" with `OSError: [Errno 22] Invalid
  argument` writing `open-on-iphone.png`: Windows Photos still showed (and locked) the picture from the
  previous start. Each start now writes a new file name, removes old pictures when it can, and a failed
  picture only warns (the address is printed anyway).
- Pairing from the iPhone was still refused. The api's new refusal log said `reason='Origin not allowed'`
  for `POST /v1/pairing/claim`, while the same iPhone's `/ws/controller` passed the same check: under
  `Referrer-Policy: no-referrer` Safari sends `Origin: null` on a page's own same-origin requests (Fetch
  standard), which Chromium does not, so every rehearsal (Chromium) passed. Fixed: the PWA and the api use
  `Referrer-Policy: same-origin` (still nothing sent to other sites), and the Origin check accepts a
  null/missing Origin only with the browser-set `Sec-Fetch-Site: same-origin` (an installed PWA can keep
  the old policy until it updates).
- After updating: **pairing succeeded on the owner's iPhone in Safari** over quick tunnels (sign-in,
  link, claim, matching codes, approval on the PC). Windows media control worked.
- Touchpad locked ("no touchpad or keyboard permission"; the PC approval window leaves both unticked on
  purpose). The way to grant them later, tray -> Paired phones -> phone -> Allow touchpad, was broken: the
  submenu was built once when the icon was created, before the agent thread bound (it read "(no agent)")
  and before any phone was paired, so it never listed a phone. It is now re-read on every menu rebuild
  (each status update); `tests/test_tray_menu.py` fails on the old code. The phone also never re-read its
  grant after the PC changed it (the relay's `pc_status` after `grant_update` updated only the online
  state); the runtime now re-reads grants already shown for that PC. Runs: pc-agent 258 (+1 skipped),
  ruff, mypy and mypy-windows clean; mobile-app 293 in 32 files, typecheck, lint, build clean.

## 2026-10-09 — Test kit for a real Windows PC and iPhone; fixes found by rehearsing it

- **`testkit/`**: three double-click scripts for a non-developer on Windows with Docker Desktop.
  Step 1 runs postgres, the dev identity provider and the api (the real `deploy/Dockerfile` image)
  behind two Cloudflare quick tunnels or user-provided stable HTTPS addresses, with a per-start sign-in
  passphrase, and prints a QR code to `/app`. Step 2 prepares the agent from source with uv, builds the
  extension in Docker and pins its ID with a generated key (the derived ID matched the one Chromium
  assigned), registers the native host and links the PC, re-linking with a new key whenever the test
  account changed. Step 3 stops, optionally deleting the data. `testkit/README.md` is the tester's guide.
- **Rehearsed end to end** on Linux: the kit's own scripts under pwsh 7, a local HTTPS stand-in for the
  tunnels (stable-address mode), headless Chromium as the phone: wrong passphrase refused, sign-in,
  `dome-agent link` approved in the browser, the agent connected over wss, pairing by the QR link with
  equal verification codes, and a Mute from the Remote screen executed by the agent
  (`windows.set_muted` succeeded) with the new state shown on the phone. Stop with data deletion and a
  clean restart were exercised too. Evidence tag: integration-tested (Linux); **not** Windows- or
  iPhone-tested; quick tunnels were not created (blocked by this environment's network policy).
- **Bugs the rehearsal found and fixed** (each with a test that fails without the fix):
  - cloud-api: the sliding-window limiter pruned a new key's empty window right after creating it, so
    the first PC link more than five minutes after start answered HTTP 500.
  - mobile-app: after pairing, the app kept the previously selected PC; an unlayered global style made
    Tailwind 4 drop every text/font/min-height utility on buttons, links and inputs (bottom tab labels
    overflowed at iPhone width); "Open app" was squeezed and cut on phone-width public pages; a
    device-limit refusal now offers Manage devices.
  - dev-idp: the empty "any email" box overrode the clicked account; Enter signed in as the first
    listed account; refusals were raw JSON in the browser; the page overflowed a phone screen.
- **Independent review** (5 reviewers + 5 adversarial verifiers): confirmed and fixed — a new
  quick-tunnel address is a new account (PC re-link, data cleared per new address, `dome-agent unlink
  --new-key` because the service never moves a PC key between accounts); CRLF checkouts of
  `deploy/entrypoint.sh` on Windows (root `.gitattributes`); a global passphrase lockout anyone could
  trigger; per-IP limits shared behind cloudflared (`DOME_TRUSTED_PROXIES` for private networks);
  tunnel containers left retrying after a failure; stable-address normalisation; troubleshooting
  commands that needed the kit's variables; several README inaccuracies.
- **Flaky tests made deterministic**: cloud-api input hardening (relay-side counts, awaited security
  events, pong before refilling) and the mobile-app touchpad suite (answering the right start command,
  waiting for asynchronous signing). New CI steps: dev-idp tests and the pwsh test-kit checks.
- Runs: cloud-api 120, pc-agent 257 (+1 skipped), mobile-app 291 in 32 files (typecheck, lint, build
  clean), dev-idp 14, test-kit checks 28, cross-component `tests/` 23 passed in 243 s.
- Next: run the kit on a real Windows 11 PC with an iPhone (`docs/HANDOFF.md` §7, item 0).

## 2026-10-09 — Manual touchpad/keyboard, health, support, single instance and brand built, reviewed and fixed

- Built per component against the 1.1 contract, each reviewed by an independent skeptic who re-ran the
  tests, then fixed (two usage-limit interruptions and one model switch were absorbed by resuming the
  workflow; nothing was lost):
  - **pc-agent**: input session manager (one owner per PC, agent-issued id, PC-side lease watchdog,
    seq/age/capability checks, ordered dispatch with adjacent-motion coalescing only, backpressure
    suspension, held-input release on every end reason, content-free crash recovery, acks ≤ 4/s),
    `SendInput` Windows adapter (32/64-bit layouts, Unicode with intact surrogate pairs, CTRL shortcuts
    that always release the modifier), local `pointer`/`keyboard` grants with `grant_update`, single
    instance per Windows session with `repair`, power confirmation copy. Review fixes: a foreground
    change now blocks queued typing until the phone clicks or restarts, the age check estimates the
    phone's clock offset, only a lock or secure desktop ends a session (an elevated window marks
    `input_restricted`), hold bookkeeping and journal hygiene.
  - **cloud-api**: `input_batch` routing (signature, binding, per-event-type grant coverage,
    per-controller budget, verbatim forward, no command row), `input_ack`/`input_session` delivery,
    `grant_update`, support tickets (Alembic `0002`, redaction, references). Review fixes: bounded
    agent-originated events and state, no duplicate session frames, abusive input sockets closed,
    pre-database budget, no instance values in error messages.
  - **mobile-app**: Touchpad (gesture state machine, explicit drag, clicks, Stop Input), Keyboard
    (commit-once composition, Compose and Send, shortcut palette), input session client, INPUT_* copy,
    Health screen, support submission and status, Now Playing, power copy, permissions UX, brand icons.
    Review fixes: a **blocker** where live typing could send Backspaces that delete PC text the phone
    never typed, two-finger right click on real browsers, Health ownership label, idle indicators,
    honest support outcome copy, plus a compose draft that could leak into live typing.
  - **brand**: original icon and wordmark (SVG), dependency-free export script, `BRAND.md`.
- Cross-component gap closed by the maintainer: agent input rejections never reached the phone (the
  error frame had no reference and the relay only logged it). Additive amendment: `error_frame`
  `ref_input_session_id` / `ref_controller_id`; the agent names both, the relay routes after an account
  check and strips the controller reference, the phone ignores rejections for sessions it left; the
  age rule wording now matches the skew-tolerant implementation.
- Documents: new `docs/INPUT_CONTROL.md` and `docs/SUPPORT.md`; manual-input updates to SECURITY
  (new threats and controls), ACCEPTANCE (scenarios 18–25), TROUBLESHOOTING, WINDOWS_INSTALL,
  IPHONE_SETUP, PRODUCT_AND_PLANS, COST_MODEL, ARCHITECTURE, PROTOCOL.
- Tests: `shared/python` 135, `shared/ts` 86, `cloud-api` 118, `pc-agent` 256 (+1 skipped as root),
  `browser-extension` 90, `mobile-app` 287, `brand` 29, `tests/` 23.
  **Evidence tag: unit-tested and integration-tested (Linux, fake input adapter).** Nothing here is
  Windows-device-tested or iPhone-tested, so touchpad/keyboard are not yet advertised as working.
- Next: Windows and iPhone passes with the new checklists; then the remaining release work in
  `docs/HANDOFF.md` §7.

## 2026-10-09 — Consolidated spec (8 October) adopted; protocol 1.1 contract for manual input

- The founder's consolidated prompt replaces `docs/spec/MASTER_PROMPT.md`. New Free-beta scope: manual
  touchpad/keyboard input with explicit per-controller permissions and a bounded input-session
  protocol (§10A), connection health and guided recovery (§11A), one agent per Windows user session
  (§10), media-target clarity (§9), power-confirmation copy about losing remote access (§10),
  support submission/status and download/help flows (§11A), the upgrade-experience rules (§12),
  brand assets (§11), scenarios 18–25 (§17) and the documents INPUT_CONTROL.md and SUPPORT.md (§18).
- Contract (protocol 1.1, registry 1.1, additive; 1.0 peers stay compatible): capabilities `pointer`
  and `keyboard`; actions `input.session_start` (either capability via `alternate_capabilities`) and
  `input.session_stop`; `ai_eligible` on every action (false for the human-only input actions); the
  signed `input_batch` stream (≤ 64 events, strictly increasing `seq`, 5 s window) with
  `input_ack` (Windows acceptance only, ≤ 4/s) and `input_session` lifecycle frames;
  `grant_update` so the PC owner can add or remove the new capabilities for an existing phone;
  `pc_state.foreground_app / input_session / input_restricted`; `INPUT_*` error codes; limits and the
  identical Free/Pro `input_rate_limit`; normative `rules.input_sessions`, `rules.grant_update`,
  `rules.ai_eligibility`; support-ticket REST bodies. Both shared libraries gained builders/verifiers
  and `satisfied_by` / `capabilitySatisfied`; types and validators regenerated.
- Tests: `shared/python` 135, `shared/ts` 86, `cloud-api` 89, `pc-agent` 193 (+1 deliberately failing
  until the input handlers exist), `mobile-app` 206, `browser-extension` 90. **Evidence tag:
  unit-tested.** `docs/design/input-control.md` is the builders' brief.
- Next: component builds (pc-agent input adapter and session manager, cloud-api routing and support
  tickets, mobile-app touchpad/keyboard/health/support, brand), reviews and fixes, integration tests,
  documents.

## 2026-10-09 — Cross-component security review and the two fixes it forced

- An independent reviewer read every seam (relay ↔ phone, relay ↔ agent, agent ↔ extension, the
  shared verifiers, the PWA) after the per-component reviews, with live probes against the real
  stack. Verdict: the core guarantee holds everywhere traced — nothing executes on the PC without a
  locally approved grant for the signing key, the relay cannot forge or alter commands or
  confirmations, tenancy and pairing are sound, replay/expiry handling matches across Python and
  TypeScript, no hidden bypass, no secret in logs or repo. Two seam-level gaps were confirmed live
  and are now fixed; one minor hardening was applied.
- **Fix 1 — controller hello proof of possession.** `/ws/controller` bound a socket to a paired
  controller by its bare `kid`, which `GET /v1/controllers` reveals to everyone signed in to the
  account; a second session could subscribe to the PC's state, receive the phone's results and
  challenges when the phone was in the background, and cancel its commands. The hello now carries
  `proof`: an ES256 envelope over `hello_proof{kid, account_id, issued_at, expires_at, nonce}`
  (contract: `relay-frames#/$defs/hello_proof`, `rules.controller_socket_identity`). The relay
  verifies it with the JWK stored at pairing, binds only then, refuses invalid proofs with
  `UNKNOWN_KEY` + 4003 + a security event, and keeps a single-use nonce cache. Implemented in
  `shared/python` (`verify_hello_proof`), `shared/ts` (`buildHelloProofPayload`, `signHelloProof`),
  cloud-api (`controller_ws.py`), the PWA (`relay.ts` signs on every connect) and the test
  simulators; new tests: shared (4 + 2), cloud-api `tests/test_hello_proof.py` (impostor stays
  unbound; forged, replayed and wrong-account proofs rejected), PWA relay test.
- **Fix 2 — honest outcome for a forwarded command that is lost.** After writing a command to the
  PC's socket the relay still reported `failed/PC_OFFLINE` or `COMMAND_EXPIRED` unless an
  `executing` ack had arrived, and the phone rendered "Nothing ran"; a PC behind a half-open socket
  (NAT/Wi-Fi drop, up to 55 s before it notices) executes its queue and writes results into the dead
  socket, which the agent counted as delivered. Now: the relay terminates every forwarded command as
  `outcome_unknown` (`rules.terminal_result`, `rules.in_flight`; start-up sweep too); the agent
  re-sends every terminal result finished after the last frame the previous connection received
  (`rules.late_results`; `relay_client.previous_last_inbound_at`, `store.journal_terminal_finished_after`),
  so the queued commands' `PC_OFFLINE` verdicts and any executed results correct the unknown
  outcome once; the `OUTCOME_UNKNOWN` copy says "may or may not have run". Tests updated in
  cloud-api (`test_relay_lifecycle.py`) and pc-agent (`test_agent_e2e.py`).
- **Minor:** CSP `connect-src` tightened from `'self' wss: https:` to `'self'`.
- Also fixed on the way: the signing fixtures' `tab_token` was 24 characters where the schema
  requires 22, so the fixture commands were not valid commands; regenerated in both languages.
- Tests run after the fixes: `shared/python` 132, `shared/ts` 84, `cloud-api` 89,
  `pc-agent` 194, `mobile-app` 206 (typecheck, lint, build), `tests/` 20 passed in 191 s (load smoke: 400 commands in 9.58 s = 42 cmd/s, p50 907 ms, p95 1290 ms).
  **Evidence tag: integration-tested (Linux); unit-tested.**
- Docs updated: `PROTOCOL.md`, `SECURITY.md` (T1, T5, new T15), `ARCHITECTURE.md`.

## 2026-10-09 — Documentation, deploy and CI written from the real state

- `docs/`: ARCHITECTURE, SECURITY, PROTOCOL, PRODUCT_AND_PLANS, BILLING, COST_MODEL,
  DATA_RETENTION, WINDOWS_INSTALL, IPHONE_SETUP, TROUBLESHOOTING, OPERATIONS, ACCEPTANCE, plus the
  maintainer's HANDOFF and this log. Every claim carries an evidence tag; implemented vs planned is
  marked per item; prices and vendor figures are dated assumptions with their sources.
- `deploy/`: multi-stage `Dockerfile` (PWA build → uv-installed API → non-root runtime with
  healthcheck), `entrypoint.sh` (writes the entitlement key from a secret, refuses to invent one
  outside development, maps `DATABASE_URL`), `fly.toml` (WebSocket-friendly: machines never
  auto-stop, connection-based concurrency, 30 s drain, release-command migrations),
  `docker-compose.dev.yml`, `README.md` (secrets by name, environment separation, founder
  prerequisites). **Not built or deployed here (no Docker daemon, no Fly account); not yet verified.**
- `.github/workflows/ci.yml`: Python (shared/python, dev-idp, cloud-api with a PostgreSQL service
  container, pc-agent), Node (shared/ts incl. generated-types drift check, mobile-app,
  browser-extension incl. build), integration (`tests/`), container build; actions pinned to
  release commits; the uv and pnpm versions match the lockfiles. **Not yet run on GitHub Actions.**
- The documentation pass found no code defects; the docs agents verified their references against
  the code (two of them were interrupted by a usage limit and re-run).

## 2026-10-08 — Cross-component integration suite (`tests/`)

- What runs: the real `dome-agent` process (`DOME_AGENT_PLATFORM=fake`, headless) linked by device
  code through a real OIDC login (`tools/dev-idp`), paired from the PC side (PC-generated code, HMAC
  verification code checked on both ends), connected to `cloud-api` under uvicorn with a fresh
  PostgreSQL database, driven by an ES256-signing controller simulator, with a `FakeExtension` on
  the agent's bridge socket. Only the Windows adapters and the browser are doubles.
- Scenarios (20 tests, spec §17): Next in a background tab with an observed transition and
  lifecycle-only relay rows; `NO_NEXT_VIDEO` / `UNSUPPORTED_CONTEXT` / `ACTIVATION_REQUIRED`;
  pause, seek, player volume vs Windows volume, two tabs need an explicit target, theater;
  `TARGET_CHANGED` / `TARGET_GONE` / `TAB_NOT_CONTROLLABLE` / extension disconnected; cross-account
  isolation including a forged `controller_id` and a foreign pairing code; pairing needs PC approval,
  single use, subset grants, `GRANT_MISSING`, expiry, decline, delivery of a claim made while the PC
  was offline; account-side and PC-side revocation; replay, `COMMAND_ID_REUSED`, `COMMAND_EXPIRED`,
  `INVALID_PARAMETERS`, `CONFIRMATION_INVALID`; Free limits (`origin: routine` →
  `ENTITLEMENT_REQUIRED`, second PC → `PC_PLAN_DISABLED`); SIGKILL between side effect and result →
  `outcome_unknown` in the relay and the journal with no re-execution; offline PC → immediate
  `PC_OFFLINE`, never queued; 20-step volume burst → only `COMMAND_SUPERSEDED` besides success and
  the final value; confirmed power countdown, `power.cancel`, decline; supersession (4001) and
  reconnect keep grants.
- Load smoke: 40 simulated agents + 40 controllers, 400 `system.ping`: 13.47 s = 30 commands/s,
  round trip p50 1314 ms / p95 1629 ms, RSS 215 → 218 MiB (loopback, every endpoint in the test
  process, shared 4-CPU machine). Numbers from one run, not a promise.
- Result: `cd tests && uv run pytest -q` → 20 passed in 214 s. No product code changed; the harness
  itself was adjusted (wait for the right `state` frame, simulate supersession instead of restarting
  the process for the offline-claim scenario, one account per pair because Free limits are real).
- **Evidence tag: integration-tested (Linux, fake platform, fake extension); load-tested (small).**
  Not Windows-device-tested, not iPhone-tested.
- Next: documentation, deploy and CI from the real state; handoff.

## 2026-10-08 — Component builds, reviews and fixes

- `cloud-api`, `pc-agent`, `browser-extension` and `mobile-app` were each built from their design
  document, reviewed by an independent skeptical reviewer (code read, tests re-run), and fixed; a
  second review/fix round ran before the documentation pass. Every blocker/major finding was fixed;
  deferred minors are in each component's `KNOWN_ISSUES.md`, contract gaps in `CONTRACT_ISSUES.md`
  (the contract stayed frozen).
- Notable outcomes of the reviews: pairing claim/approval ordering and plan-limit enforcement
  hardened in `cloud-api`; approved-app validation, crash recovery and the confirmation transaction
  tightened in `pc-agent`; the extension moved to build-time precompiled validators because MV3 CSP
  forbids Ajv's `new Function`; the PWA fixed its sign-out ordering (session store enters
  `signing_out` before the key is deleted), binds the confirmation header to the challenge's `pc_id`,
  removed a `g`-flag `lastIndex` bug in the intent parser, and closes sockets with 1000 instead of the
  protocol-error code 4000.
- Tests run after the fixes: `cloud-api` 86 (real PostgreSQL + dev-idp; ruff + mypy on source and
  tests), `pc-agent` 194 (fake platform; ruff + mypy), `browser-extension` 90 (jsdom fixtures; Vite
  build), `mobile-app` 205 (fake socket + fake IndexedDB; typecheck, lint, production
  build). **Evidence tag: unit-tested; cloud-api integration-tested against PostgreSQL.**
- Not verified: anything on a Windows device, a real browser session against youtube.com, or an
  iPhone.
- Next: end-to-end integration suite, docs, deploy, CI.

## 2026-10-08 — Contract review and hardening (before component builds)

- Four independent adversarial reviews of the contract (security/crypto, cross-language
  encoding + state machine, product fit, implementability) produced 38 findings; skeptics
  confirmed the blockers/majors against the committed state. All substantive findings were
  applied; refuted/duplicate ones were dropped. Highlights:
  - Confirmation challenge now travels as an opaque `challenge_text` string hashed verbatim
    (the nested-object form would have broken every confirmation across Python↔TypeScript).
  - `grants_snapshot` is an intersection/revocation list carrying no keys; the PC's locally
    approved store is the only key source. Plan-disabled devices are a separate state.
  - Verifiers bind `controller_id`/`account_id` to the signing key (`CONTROLLER_MISMATCH`).
  - Pairing code is generated on the PC (20 Crockford symbols); the backend sees only its
    SHA-256 handle; the 6-digit verification code is an HMAC keyed by the code, so a key-substituting
    backend cannot make both screens agree.
  - Controller `hello` carries `kid`; per-action `result` schemas; REST body schemas;
    entitlement claim schema; `tab_token` binds YouTube targets; richer `youtube_tab`;
    normative behavioural rules collected in `version.json → rules`.
  - Python `pattern` validation now uses ECMA-262 semantics (ASCII classes, true end
    anchoring); both parsers reject unsafe integers and prototype-pollution keys; a shared
    `strict-json-cases.json` fixture is run by both suites.
- Tests run: `shared/python` 128 passed; `shared/ts` 82 passed; fixtures cross-verified in both
  directions. **Evidence tag: unit-tested.**
- `tools/dev-idp` (development OIDC issuer) smoke-tested: discovery, PKCE authorize, token,
  replay rejection, userinfo.
- Next: component builds (cloud-api, pc-agent, browser-extension, mobile-app), then docs,
  then the end-to-end relay test and a security review of the implementations.

## 2026-10-08 — Phase A foundation

- Repository skeleton, ADR-0001 (auth = OIDC/PKCE via Authlib; PC linking = device-code shape;
  controller keys = non-extractable WebCrypto ECDSA P-256; envelopes = detached ES256 over exact
  bytes; hosting target = Fly.io; single FastAPI process + PostgreSQL).
- `shared/protocol/`: action registry (29 actions, 6 capabilities), plans, error codes, JSON
  Schemas for envelope / command / confirmation / relay frames / bridge frames, cross-language
  fixtures.
- `shared/python` (`dome-protocol`) and `shared/ts` (`@dome/protocol`): strict JSON, keys, signing,
  registry/frame validation, command build/verify.
- Tests run: `shared/python` 65 passed; `shared/ts` 23 passed. Each language verifies the other
  language's signed fixtures. **Evidence tag: unit-tested.**
- Next: adversarial review of the contract, then cloud-api / pc-agent / browser-extension /
  mobile-app builds and the end-to-end relay test.
