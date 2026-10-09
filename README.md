# DoMe

**Your phone is a simple, secure remote for your PC.**

DoMe lets a customer control their own Windows PC from an iPhone-first mobile web app — pause or
skip the YouTube video playing in a background tab, set the system volume, open an approved app,
lock the PC, put it to sleep after confirming on the phone, or use the phone as a touchpad and
keyboard for the PC — through a managed relay, with no port forwarding, VPN, or scripting. Free is
useful forever; Pro (routines, layouts, more devices) comes at paid launch.

> Status: **pre-release engineering build, not yet device-tested.** Nothing here is publicly
> available and no billing exists. The whole path phone → relay → PC agent → browser extension is
> implemented and proven end to end on Linux with the operating-system adapters and the browser
> replaced by explicit test doubles. **Nothing has run on a Windows PC, in a real Chrome against
> youtube.com, or on an iPhone yet.** `docs/ACCEPTANCE.md` lists what was actually tested and how;
> `docs/HANDOFF.md` says what to do next.

## What is here

| Path | What it is | Tests (this environment) |
| --- | --- | --- |
| `shared/protocol/` | **Source of truth** (protocol 1.1): action registry (31 actions, 8 capabilities incl. Free touchpad/keyboard), the signed manual-input stream, plans, error codes, JSON Schemas, normative rules, cross-language fixtures. | — |
| `shared/python/`, `shared/ts/` | One tested implementation per language of strict JSON, ES256 envelopes over exact bytes, registry/frame/result validation, input-batch builders/verifiers. | 135 · 86 (unit) |
| `cloud-api/` | FastAPI backend + relay: OIDC sign-in (PKCE), PC linking, controllers, grants, pairing, command routing, manual-input routing, plan limits, entitlement assertions, support tickets. PostgreSQL + Alembic. | 120 (against real PostgreSQL and the dev identity provider) |
| `pc-agent/` | Python Windows user agent: outbound WSS only, local authorization, coalescing queue, confirmation transaction, SQLite journal, action handlers, manual-input session manager and SendInput adapter, single instance, Windows adapters, Chrome Native Messaging host, tray. | 257 (+1 skipped as root) (unit, Linux, fake platform) |
| `browser-extension/` | Chrome/Edge MV3 extension: YouTube player adapter with transition-observed Next, narrow host permission, Native Messaging to the agent, no eval. | 90 (unit, DOM fixtures) |
| `mobile-app/` | React + TypeScript + Vite + Tailwind PWA (iPhone-first): remote, touchpad and keyboard, Health screen, support, Now Playing. Non-extractable WebCrypto controller keys, strict validation of every frame, honest status everywhere. | 291 (unit, jsdom) |
| `tests/` | Cross-component suite: real agent process + real relay + real PostgreSQL + real OIDC login + fake extension and fake input adapter. | 23 (integration, incl. manual input and a load smoke) |
| `tools/dev-idp/` | Development-only OpenID Connect issuer, so local runs and tests never need an authentication bypass; optional passphrase gate for the test kit. | 14 (unit) |
| `testkit/` | **Try DoMe on your own Windows PC and iPhone**: three double-click scripts run the whole service in Docker behind temporary HTTPS addresses, start the PC program from source and build the extension. Start with `testkit/README.md`. | 28 script checks (pwsh); full rehearsal on Linux with an HTTPS stand-in |
| `brand/` | Original DoMe icon and wordmark (editable SVG), export script, `BRAND.md`. | 29 (unit) + export drift check |
| `deploy/` | Container image, Fly.io configuration, Docker Compose for development, deployment notes. | image built and run by the test kit rehearsal (Linux); not deployed |
| `.github/workflows/` | CI for every suite above. | not run on GitHub yet |
| `docs/` | Everything written for people: see the list below. | — |

Evidence tags used throughout: **unit-tested**, **integration-tested**, **load-tested**,
**Windows-device-tested**, **iPhone-tested**, **not yet verified**. Every claim of verification in
this repository carries one of them.

## Documents

Start with `docs/adr/0001-foundational-decisions.md`, then `docs/ARCHITECTURE.md`.

| Document | Content |
| --- | --- |
| `docs/ARCHITECTURE.md` | Components, trust boundaries, flows, what the relay can and cannot see |
| `docs/SECURITY.md` | Threat model, controls and where each is tested, pairing/revocation, operator boundaries |
| `docs/PROTOCOL.md` | The wire contract explained: envelopes, frames, states, error codes, versioning |
| `docs/PRODUCT_AND_PLANS.md` | Product principles, Free vs Pro, limits, rate budgets |
| `docs/BILLING.md` | Subscription → entitlement design for paid launch (not implemented) |
| `docs/COST_MODEL.md` | Hosting/vendor cost assumptions with dated sources |
| `docs/DATA_RETENTION.md` | What is stored, for how long, and why |
| `docs/WINDOWS_INSTALL.md`, `docs/IPHONE_SETUP.md` | Customer setup, as implemented today |
| `docs/INPUT_CONTROL.md` | Touchpad/keyboard permissions, gestures, input-session protocol, recovery, tested compatibility |
| `docs/SUPPORT.md` | Support submission and status, redaction rules, known-issues upkeep |
| `docs/TROUBLESHOOTING.md` | One section per recovery scenario |
| `docs/ACCEPTANCE.md` | Scenario matrix with the evidence tag for each |
| `docs/PROGRESS.md`, `docs/HANDOFF.md` | Dated log; exact state, commands, failures and next tasks |
| `docs/design/*.md`, `docs/spec/MASTER_PROMPT.md` | Per-component design docs and the product specification |

## First run (development)

**Just want to try it on your PC and iPhone?** Use `testkit/README.md` (needs Docker Desktop on
Windows; the scripts set up the rest).

Prerequisites for development: Python 3.12 + [`uv`](https://docs.astral.sh/uv/), Node 22 + `pnpm`,
PostgreSQL 16 binaries. Docker is optional (`deploy/docker-compose.dev.yml`).

```bash
make setup            # installs every component
make db-start db-create
cp .env.example .env  # defaults point at the local Postgres and the dev identity provider
make dev-idp          # terminal 1: development OIDC issuer on :8081
make api              # terminal 2: API + relay on :8000 (runs migrations)
make pwa              # terminal 3: PWA on :5173 (proxies /v1 and /ws to :8000)
make agent            # terminal 4: PC agent in development mode
```

Run everything that can run without a Windows PC or iPhone: `make test` (unit suites plus the
integration suite, which needs the local PostgreSQL), `make lint typecheck`.

Component-specific instructions: `cloud-api/README.md`, `pc-agent/README.md`,
`browser-extension/README.md`, `mobile-app/README.md`, `tests/README.md`,
`docs/WINDOWS_INSTALL.md`, `docs/IPHONE_SETUP.md`.

## Principles (short form)

Free is useful forever. Buttons work without an LLM. "Delivered" is not "executed". The PC never
listens on the internet and never accepts a command it has not authorised locally. No hidden remote
access, shell execution, admin backdoor or login bypass. Honest status everywhere. Full text:
`docs/PRODUCT_AND_PLANS.md`.
