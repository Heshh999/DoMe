# Handoff

Last updated 2026-10-08 (UTC), branch `claude/stoic-heisenberg-txq6ja`. Read this first if you are
continuing the work. `docs/PROGRESS.md` is the dated log; `docs/ACCEPTANCE.md` is the evidence matrix;
each component keeps its own `README.md`, `DECISIONS.md`, `KNOWN_ISSUES.md` and `CONTRACT_ISSUES.md`.

Evidence tags used everywhere in this repository: **unit-tested**, **integration-tested**,
**load-tested**, **Windows-device-tested**, **iPhone-tested**, **not yet verified**. Nothing in this
repository has been Windows-device-tested or iPhone-tested yet.

## 1. Where things stand

| Area | What exists | Evidence |
| --- | --- | --- |
| `shared/protocol` (protocol 1.0, registry 1) | Action registry (29 actions, 6 capabilities), plans, error codes, JSON Schemas for envelopes, commands, confirmations, relay/bridge frames, per-action results, entitlement claims, REST bodies; normative rules in `version.json → rules`; cross-language fixtures. Frozen during the component builds; additive proposals are collected in each component's `CONTRACT_ISSUES.md`. | unit-tested: `shared/python` 128, `shared/ts` 82; each language verifies the other's signed fixtures |
| `cloud-api` | FastAPI + PostgreSQL: OIDC Authorization Code + PKCE sign-in (Authlib), sessions, device-code PC linking, controllers, grants, PC-side pairing, relay (`/ws/agent`, `/ws/controller`), command lifecycle rows, plan limits, entitlement assertions (EdDSA JWS), security events, `/healthz`, static PWA serving. Alembic `0001` also creates the billing, usage, layout, routine, support, deletion and operator tables that have **no routes yet**. | integration-tested against real PostgreSQL + `tools/dev-idp`: 86 tests; ruff, mypy (source and tests) clean |
| `pc-agent` | The Windows user agent: `link`, identity/secret store, outbound relay client, local authorization against the locally approved grant store, queue with coalescing, confirmation transaction, SQLite journal with crash recovery, every registry action, Windows adapters (pywin32 / pycaw / winsdk, import-gated), Chrome Native Messaging host, tray, local control channel (`status`, `pair`, `revoke`, `reconnect`, …), diagnostics, PyInstaller specs. | unit-tested on Linux with `fake_platform`: 194 tests; ruff + mypy clean; the Windows adapters are **not yet verified** on a device |
| `browser-extension` | Chrome/Edge MV3: service worker with native port + reconnect alarms, content-script YouTube player adapter (transition-observed Next/Previous, `tab_token` and `expected_video_id` guards, context flags), build-time precompiled validators (MV3 CSP forbids Ajv's `new Function`), popup. | unit-tested with hand-written DOM fixtures: 90 tests; Vite build succeeds; **never loaded in a real browser against youtube.com** |
| `mobile-app` | React 19 + Vite + Tailwind PWA: sign-in, PC link approval, pairing by QR/code with the verification-code comparison, dashboard, remote, command page, apps, devices, settings, confirmation modal, billing status (no checkout), routines preview (nothing runs). Controller keys are non-extractable WebCrypto ECDSA P-256 keys in IndexedDB. | unit-tested with a fake socket and fake IndexedDB: 205 tests; typecheck, lint and production build clean; **not iPhone-tested** |
| `tests/` | Cross-component suite: the real `dome-agent` process (fake platform, headless) + the real relay + real PostgreSQL + real OIDC login through `tools/dev-idp` + an ES256-signing controller simulator + a fake extension on the agent's bridge socket. | integration-tested (Linux): 20 passed in 214 s, including a load smoke (see §3) |
| `tools/dev-idp` | Development-only OpenID Connect issuer (RS256, PKCE S256). Never deployed; cloud-api has no auth bypass. | unit/smoke-tested |
| `deploy/`, `.github/workflows/ci.yml` | DEPLOY_STATUS | not yet run on Fly.io or GitHub Actions |
| `docs/` | ADR-0001, design docs per component, the product spec, and the user/operator documents listed in `README.md`. | n/a |

## 2. Running everything on a fresh machine

Prerequisites: Python 3.12 + `uv`, Node 22 + `pnpm`, PostgreSQL 16 binaries (`initdb`, `pg_ctl`,
`psql`). No Docker is needed (none was available while building this).

```sh
make setup                 # every component (uv sync / pnpm install, generates TS types)
make db-start db-create    # local cluster on /tmp:54329 as user `dome`, databases dome_dev + dome_test
cp .env.example .env
make dev-idp               # terminal 1: OIDC issuer on :8081
make api                   # terminal 2: API + relay on :8000 (runs migrations)
make pwa                   # terminal 3: PWA on :5173
make agent                 # terminal 4: agent (Linux → development platform, actions report PLATFORM_UNSUPPORTED)
```

Tests, per component (each is a standalone package with its own lockfile):

```sh
cd shared/python && uv run pytest -q           # 128
cd shared/ts && pnpm test                       # 82
cd cloud-api && uv run pytest -q                # 86 (needs the local PostgreSQL and tools/dev-idp venv)
cd pc-agent && uv run pytest -q                 # 194
cd browser-extension && pnpm test               # 90
cd mobile-app && pnpm test                      # 205
cd tests && uv run pytest -q                    # 20, ~3.5 min; `-k load` for the load smoke alone
make lint typecheck                             # ruff / mypy / eslint / tsc across the repo
```

If `initdb` refuses to run as root, run the cluster as an unprivileged user (this is what the build
environment did); the `make db-*` targets accept `PG_DIR`/`PG_PORT`.

## 3. What has actually been verified

All of the following were run on Linux in this repository's environment. Tag: **integration-tested**
unless stated.

- Real OIDC login (Authorization Code + PKCE against `tools/dev-idp`), CSRF/Origin checks, session
  listing and revocation.
- PC linking by device code: `dome-agent link --no-browser` → user code approved in the account →
  credential issued → agent connects outbound over WebSocket and receives its `grants_snapshot`.
- Pairing: code generated on the PC (20 Crockford symbols), backend sees only its SHA-256 handle,
  the phone claims it, the PC approves locally, and the 6-digit HMAC verification code computed by
  the agent equals the one computed by the controller.
- The first milestone: a controller-signed `youtube.next` reaches the real agent through the relay,
  the (fake) extension observes a video transition, the result validates against the per-action
  result schema, and the relay stores lifecycle fields only.
- Honest failures: `NO_NEXT_VIDEO`, `UNSUPPORTED_CONTEXT`, `ACTIVATION_REQUIRED`, `TARGET_REQUIRED`,
  `TARGET_CHANGED`, `TARGET_GONE`, `TAB_NOT_CONTROLLABLE`, `EXTENSION_DISCONNECTED`, `PC_OFFLINE`,
  `PC_REMOTE_DISABLED`, `GRANT_MISSING`, `COMMAND_ID_REUSED`, `COMMAND_EXPIRED`,
  `INVALID_PARAMETERS`, `CONFIRMATION_INVALID`, `CONFIRMATION_DECLINED`, `POWER_CANCELED`,
  `COMMAND_SUPERSEDED`, `ENTITLEMENT_REQUIRED`, `DEVICE_LIMIT_REACHED`, `PC_PLAN_DISABLED`,
  `PAIRING_CODE_INVALID`.
- Security properties: cross-account isolation (including a forged `controller_id` and a foreign
  pairing code), single-use pairing codes, subset grants, account-side and PC-side revocation,
  replay rejection, no admin or test bypass in the backend.
- Reliability: SIGKILL of the agent between the side effect and the result → `outcome_unknown` on the
  phone and in the journal, and no re-execution after restart; commands to an offline PC fail at once
  and are never queued; a 20-step volume burst terminates every command with only
  `COMMAND_SUPERSEDED` as the non-success code and lands on the final value; confirmed power actions
  arm a countdown that `power.cancel` stops; supersession by another agent instance (close 4001) and
  manual reconnect keep the grants.
- Load smoke (**load-tested**, loopback, simulated endpoints in the test process, shared 4-CPU
  machine): 40 agents + 40 controllers, 400 `system.ping` in 13.47 s = 30 commands/s, round trip
  p50 1314 ms / p95 1629 ms, test-process RSS 215 → 218 MiB. These are numbers from one run, not a
  performance promise; the spec's device latency targets remain unmeasured.
- Unit level (**unit-tested**): every component's own suite, listed in §1.

## 4. What has NOT been verified

- **Windows**: none of `pc-agent/dome_agent/platform/windows/*` (volume via pycaw, media sessions via
  winsdk, app launching, lock, power, start-at-login, native-host registry entries) has run on a
  Windows machine. The PyInstaller specs have not been built. Follow `docs/WINDOWS_INSTALL.md` and the
  manual checklist at the end of `pc-agent/README.md`.
- **Chrome/Edge**: the extension has never been loaded unpacked against the real youtube.com DOM
  (`browser-extension/KNOWN_ISSUES.md` K1); the manual checklist is in `browser-extension/README.md`.
- **iPhone**: the PWA has not been opened in iOS Safari (standalone mode, resume behaviour, QR
  scanning, WebCrypto/IndexedDB persistence); see `docs/IPHONE_SETUP.md` and
  `mobile-app/KNOWN_ISSUES.md` #5.
- **Production identity provider**: only `tools/dev-idp` was used; Auth0/Keycloak settings exist in
  `cloud-api/README.md` but have not been exercised.
- **Hosting**: `deploy/` has not been deployed; `DOME_TRUSTED_PROXIES` for Fly.io is a deployment fact
  that must be set by hand (`cloud-api/KNOWN_ISSUES.md` #5).
- **Billing**: no Stripe code exists. `cloud-api` only reports `billing_enabled` from the presence of
  a Stripe key, and the PWA shows plan state without any checkout. `docs/BILLING.md` is a design.
- **Pro features**: routines and layouts have tables and plan limits but no API routes or execution;
  the Routines page is an explicitly labelled preview.
- **Operator tooling**: `OperatorUser`/`OperatorAudit` tables exist, nothing is served.
- **CI**: `.github/workflows/ci.yml` was written against the real commands but has not run on GitHub
  Actions yet.

## 5. Failures and quirks met during development (so you do not re-discover them)

- jsonschema (Python): registering schemas with their `$schema` keyword makes `evolve()` re-select the
  stock validator when following `$ref`, silently bypassing the ECMA-262 `pattern` semantics.
  `dome_protocol.schemas` strips `$schema` from registered resources on purpose.
- Ajv in a Manifest V3 service worker throws `EvalError` (`new Function`). The extension precompiles
  validators at build time (`browser-extension/scripts/gen-validators.ts`); the test
  `generated.test.ts` fails if they drift from the schemas.
- Unix socket paths are limited to about 104 bytes: the integration harness uses a short temporary
  state directory for the agent's bridge socket.
- The load smoke must use one account per agent/controller pair: the Free plan limits (1 PC, 2
  controllers) are real product behaviour and are not bypassed by tests.
- joserfc warns that the JWS `alg` value `EdDSA` is deprecated by RFC 9864; the contract mandates it
  for entitlement assertions, so the warning is expected (proposal: switch to `Ed25519` at the next
  protocol MINOR bump).
- `dataclass(slots=True)` class attributes are not defaults; FastAPI rejects union return annotations
  of response classes (both hit in `tools/dev-idp`).
- The first `state` frame a subscriber receives can be the cached pre-extension one; wait for the
  predicate you need (`tests/conftest.py::wait_state`).

## 6. Open contract proposals (highest value first)

The contract was frozen for the component builds; these are recorded, not applied. Apply them together
in a protocol MINOR bump (`shared/protocol/version.json`), regenerate fixtures and types
(`make fixtures`, `cd shared/ts && pnpm gen:types`) and re-run every suite.

1. `pairing_request` should carry the relay-assigned `controller_id` so the PC can store the grant
   under its final id (`pc-agent/CONTRACT_ISSUES.md` #1).
2. Proof of possession on `POST /v1/agent-link/start` (`cloud-api/CONTRACT_ISSUES.md` #10).
3. List the REST-only error codes (`UNAUTHENTICATED`, `FORBIDDEN`, `NOT_FOUND`, `LINK_EXPIRED`,
   `LINK_DENIED`, `PC_ALREADY_LINKED`, …) in `errors.json` (cloud-api #4, mobile-app #4).
4. `TRANSITION_NOT_OBSERVED` for "Next was clicked but no change was observed" and a minimum of
   1000 ms for `bridge_request.timeout_ms` (`browser-extension/CONTRACT_ISSUES.md` #2, #5).
5. `COMMAND_CANCELED` for relay-initiated cancels and an explicit rule for cancelling an executing
   command (pc-agent #3, #4).
6. `ref_command_id` on `error` frames for rejected confirmations (cloud-api #3).
7. Regenerate the signing fixtures with a 22-character `tab_token` (mobile-app #1).
8. `Ed25519` instead of `EdDSA` as the entitlement JWS algorithm identifier (cloud-api #9, pc-agent #5).
9. A `hello_ack` re-push when a connected unbound controller socket gets its controller row
   (mobile-app #2), or keep the current reopen.

## 7. Next tasks, in order

1. **Windows device pass**: install Python 3.12 on a Windows 11 machine, `uv sync` in `pc-agent`,
   run `dome-agent link`, register the native host, load the extension unpacked, and work through the
   manual checklists in `pc-agent/README.md` and `browser-extension/README.md`. Fix what breaks;
   record results in `docs/ACCEPTANCE.md` with the tag Windows-device-tested.
2. **iPhone pass**: serve the PWA over HTTPS (Fly.io staging or a tunnel), follow
   `docs/IPHONE_SETUP.md`, verify pairing by QR, Add to Home Screen, resume after lock, confirmation
   modal. Record as iPhone-tested.
3. **Staging deployment**: `deploy/README.md` — Fly.io app + PostgreSQL, Auth0 or Keycloak as the
   identity provider, `DOME_TRUSTED_PROXIES`, entitlement signing key, run `tests/` against the local
   stack before each deploy; enable the GitHub Actions workflow and fix anything it reports.
4. **Phase B beta**: invite-only Free accounts, support diagnostics intake (table exists), the
   operator read-only views (`OperatorUser`/`OperatorAudit`).
5. **Phase C billing**: implement `docs/BILLING.md` (Stripe Checkout/Portal, webhook intake with
   dedup, the subscription → entitlement state machine, grace and downgrade selection), then routines
   and layouts routes and the agent-side routine execution.
6. **Protocol MINOR bump** applying §6.
7. **Agent socket frame budget** and the other deferred items in each `KNOWN_ISSUES.md`.
8. A **cross-component adversarial security review** of the implementations (each component was
   reviewed on its own; the integration suite covers the seams, a dedicated review has not been run).

## 8. How this was built

Every component was produced from its design document in `docs/design/` by a builder, reviewed by an
independent skeptical reviewer who re-ran the tests, and fixed; a second review/fix round ran before
the documentation pass. Builders could not change the contract; they recorded gaps in
`CONTRACT_ISSUES.md`. Reviews and fixes are summarised in `docs/PROGRESS.md`. The documents under
`docs/` were written from the code after the builds, with the honesty rules above.
