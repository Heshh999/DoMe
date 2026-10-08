# DoMe

**Your phone is a simple, secure remote for your PC.**

DoMe lets a customer control their own Windows PC from an iPhone-first mobile web app — pause or
skip the YouTube video playing in a background tab, set the system volume, open an approved app,
lock the PC — through a managed relay, with no port forwarding, VPN, or scripting.

> Status: **pre-release engineering build.** Nothing here is publicly available, no live
> billing exists, and Windows/iPhone behaviour is implemented against real APIs but can only be
> verified on real devices. See `docs/ACCEPTANCE.md` for what has actually been tested.

## Repository

| Path | What it is |
| --- | --- |
| `cloud-api/` | FastAPI backend + relay: accounts (OIDC), PCs, controllers, pairing, command routing, billing, entitlements. PostgreSQL. |
| `pc-agent/` | Python Windows user agent: outbound WSS, local authorization, action executors, SQLite journal, Native Messaging host, tray. |
| `browser-extension/` | Chrome/Edge MV3 extension: YouTube player adapter, narrow host permission, Native Messaging to the agent. |
| `mobile-app/` | React + TypeScript + Vite + Tailwind PWA (iPhone-first). Controller keys live in non-extractable WebCrypto storage. |
| `shared/protocol/` | **Source of truth**: action registry, plans, error codes, JSON Schemas, cross-language fixtures. |
| `shared/python/`, `shared/ts/` | One tested implementation per language of strict JSON, ES256 envelopes, registry/frame validation. |
| `tools/dev-idp/` | Development-only OpenID Connect issuer so local dev and tests never need a production authentication bypass. |
| `tests/` | Cross-component integration tests (relay + agent + signed controller). |
| `deploy/` | Container, Fly.io config, CI. |
| `docs/` | Architecture, security/threat model, protocol, billing, cost model, install guides, acceptance evidence, progress, handoff. |

Start with `docs/adr/0001-foundational-decisions.md`, then `docs/ARCHITECTURE.md`.

## First run (development)

Prerequisites: Python 3.12 + [`uv`](https://docs.astral.sh/uv/), Node 22 + `pnpm`, PostgreSQL 16 binaries.

```bash
make setup            # installs every component
make db-start db-create
cp .env.example .env  # defaults point at the local Postgres and the dev identity provider
make dev-idp          # terminal 1: development OIDC issuer on :8081
make api              # terminal 2: API + relay on :8000 (runs migrations)
make pwa              # terminal 3: PWA on :5173 (proxies /v1 and /ws to :8000)
make agent            # terminal 4: PC agent in development mode
```

Run everything that can run without a Windows PC or iPhone: `make test`.

Component-specific instructions: `cloud-api/README.md`, `pc-agent/README.md`,
`browser-extension/README.md`, `mobile-app/README.md`, `docs/WINDOWS_INSTALL.md`,
`docs/IPHONE_SETUP.md`.

## Principles (short form)

Free is useful forever. Buttons work without an LLM. "Delivered" is not "executed". The PC
never listens on the internet. No hidden remote access, shell execution, or login bypass.
Honest status everywhere. Full text: the product principles section of `docs/PRODUCT_AND_PLANS.md`.
