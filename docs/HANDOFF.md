# Handoff

Last updated 2026-10-09 (UTC), branch `claude/stoic-heisenberg-txq6ja`. Read this first if you are
continuing the work. `docs/PROGRESS.md` is the dated log; `docs/ACCEPTANCE.md` is the evidence matrix;
each component keeps its own `README.md`, `DECISIONS.md`, `KNOWN_ISSUES.md` and `CONTRACT_ISSUES.md`.

Evidence tags used everywhere in this repository: **unit-tested**, **integration-tested**,
**load-tested**, **Windows-device-tested**, **iPhone-tested**, **not yet verified**. Nothing in this
repository has been Windows-device-tested or iPhone-tested yet.

## 1. Where things stand

| Area | What exists | Evidence |
| --- | --- | --- |
| `shared/protocol` (protocol 1.1, registry 1.1) | Action registry (31 actions, 8 capabilities incl. Free `pointer`/`keyboard`), plans, error codes, JSON Schemas for envelopes, commands, confirmations, relay/bridge frames, the signed manual-input stream (`input_batch`, `input_ack`, `input_session`, `grant_update`), per-action results, entitlement claims, REST bodies incl. support tickets; normative rules in `version.json → rules` (incl. `input_sessions`, `grant_update`, `ai_eligibility`); cross-language fixtures. Changes since 1.0 are additive; 1.0 peers stay compatible and never receive input frames. | unit-tested: `shared/python` 135, `shared/ts` 86; each language verifies the other's signed fixtures |
| `cloud-api` | FastAPI + PostgreSQL: OIDC Authorization Code + PKCE sign-in, sessions, device-code PC linking, controllers, grants, PC-side pairing, relay (`/ws/agent`, `/ws/controller` with signed hello proof), command lifecycle, plan limits, entitlement assertions, security events, support tickets (Alembic `0002`), and the 1.1 input path: signed `input_batch` routing with per-event-type grant coverage and a per-controller budget, `input_ack`/`input_session` delivery, `grant_update`, agent input rejections routed to the batch's phone. Billing/usage/layout/routine/operator tables exist without routes. | integration-tested against real PostgreSQL + `tools/dev-idp`: 120 tests; ruff, mypy (source and tests) clean |
| `pc-agent` | The Windows user agent: link, identity, outbound relay client, local authorization, coalescing queue, confirmations, SQLite journal, every registry action, Windows adapters (import-gated), Native Messaging host, tray, control channel, diagnostics, PyInstaller specs; for 1.1: the manual-input session manager (one owner per PC, agent-issued id, PC-side lease watchdog, seq/age/capability checks with a skew-tolerant age estimate, ordered dispatch, held-input release on every end reason, content-free crash recovery), a `SendInput` Windows input adapter, local `pointer`/`keyboard` grants with `grant_update`, single instance per Windows session with `repair`, power confirmation copy about losing remote access. | unit-tested on Linux with the fake platform: 257 tests (+1 skipped as root); ruff + mypy clean; the Windows adapters (SendInput, mutex, secure-desktop detection) are **not yet verified** on a device |
| `browser-extension` | Chrome/Edge MV3: service worker with native port + reconnect alarms, content-script YouTube player adapter (transition-observed Next/Previous, `tab_token` and `expected_video_id` guards, context flags), build-time precompiled validators (MV3 CSP forbids Ajv's `new Function`), popup. | unit-tested with hand-written DOM fixtures: 90 tests; Vite build succeeds; **never loaded in a real browser against youtube.com** |
| `mobile-app` | React 19 + Vite + Tailwind PWA: sign-in, PC link approval, pairing (QR/code, verification digits), dashboard with Now Playing, remote, command page, apps, devices with touchpad/keyboard permissions, settings, confirmation modal with power-access copy, billing status, routines preview; for 1.1: Touchpad (gesture state machine, explicit drag, clicks, Stop Input), Keyboard (commit-once composition, Compose and Send, shortcut palette, never deletes text it did not send), the input session client, Health screen with layered states and one next action each, support ticket submission and status, release notes, brand icons. | unit-tested in jsdom: 291 tests; typecheck, lint and production build clean; **not iPhone-tested** |
| `tests/` | Cross-component suite: the real `dome-agent` process (fake platform, headless) + the real relay + real PostgreSQL + real OIDC login + an ES256-signing controller simulator + a fake extension; now also the manual-input scenarios (grant_update, ordering, replay/stale rejections reaching the phone, lease expiry releasing a held button, takeover, revocation, keyboard-only grant, no typed text on disk). | integration-tested (Linux): 23 passed in 227 s (incl. 3 manual-input tests and the load smoke: 400 commands in 10.18 s = 39 cmd/s, p50 986 ms, p95 1199 ms, RSS 208 → 212 MiB) |
| `tools/dev-idp` | Development-only OpenID Connect issuer (RS256, PKCE S256). Never deployed; cloud-api has no auth bypass. Optional passphrase gate (`DEV_IDP_PASSPHRASE`) for the test kit: the right passphrase always works, wrong ones never lock anyone out; readable refusal page for browsers. | unit-tested: 14 tests (run in CI) |
| `testkit/` | Try DoMe on a Windows PC and an iPhone without development tools: `1 Start test server.cmd` (whole stack in Docker, two Cloudflare quick tunnels or your own stable HTTPS addresses, passphrase-gated sign-in, QR code), `2 Start DoMe on this PC.cmd` (uv-managed agent from source, extension built in Docker with a pinned ID, native host, link; re-links automatically when the test account changed), `3 Stop test server.cmd`; `testkit/README.md` is the tester's guide. | 28 pwsh checks (parse, ASCII, helpers; run in CI). Rehearsed end to end on Linux with pwsh 7 and an HTTPS stand-in in stable-address mode: sign-in, link, wss relay, pairing, a phone command executed. **Quick tunnels, Windows PowerShell 5.1 and a real iPhone not yet verified** |
| `brand/` | Original DoMe icon and wordmark as editable SVG (light, dark, mono), a dependency-free export script producing PWA icons, favicons, Windows tray PNGs and `.ico` files, and `BRAND.md` (colour tokens, type, sizes, usage). The PWA uses the exports; the tray and installer wiring is pending. | unit-tested: 29 tests and an export drift check; renders not compared against a browser rasterizer |
| `deploy/`, `.github/workflows/ci.yml` | Multi-stage Dockerfile (PWA + API, non-root, healthcheck), entrypoint that refuses to invent a signing key outside development, `fly.toml` tuned for long-lived WebSockets, Compose file for machines with Docker, deploy README; CI with Python/Node/integration/container jobs and pinned actions. | written against the real commands; the image was built and run by the test kit rehearsal (Linux, the sandbox proxy certificate added to the two build stages only); **not yet verified**: deployed, or run on GitHub Actions (no Fly account here) |
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
cd shared/python && uv run pytest -q           # 135
cd shared/ts && pnpm test                       # 86
cd cloud-api && uv run pytest -q                # 120 (needs the local PostgreSQL and tools/dev-idp venv)
cd pc-agent && uv run pytest -q                 # 257 (+1 skipped as root)
cd pc-agent && uv run mypy --config-file mypy-windows.ini  # Windows code paths vs. pywin32 stubs
cd browser-extension && pnpm test               # 90
cd mobile-app && pnpm test                      # 291
cd tools/dev-idp && uv run --extra dev pytest -q  # 14
pwsh -NoProfile -File testkit/tests/kit.tests.ps1  # 28 test-kit script checks
cd brand && pnpm check                          # 29 tests + export drift check
cd tests && uv run pytest -q                    # 23, ~4 min; `-k load` for the load smoke alone
make lint typecheck                             # ruff / mypy / eslint / tsc across the repo
```

If `initdb` refuses to run as root, run the cluster as an unprivileged user (this is what the build
environment did); the `make db-*` targets accept `PG_DIR`/`PG_PORT`. Every directory above the data
directory must stay traversable by that user: in the build environment a data directory under a
root-owned `0700` parent made the checkpointer PANIC with `Permission denied` and the server shut
itself down mid-test-run (seen twice); a data directory directly under `/tmp` was stable.

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
- Hello proof of possession: a same-account session presenting a paired phone's kid stays unbound;
  forged, replayed and wrong-account proofs are refused with `UNKNOWN_KEY` + 4003 and logged.
- Honest post-forward outcomes: a forwarded command whose PC connection drops (acked or not) ends
  `outcome_unknown`; the PC's re-sent result corrects it exactly once.
- Manual input end to end (**integration-tested**, real agent process with the fake input adapter): the PC
  owner's local grant reaches the relay through `grant_update` and comes back in the snapshot; an
  existing phone cannot start a session before that; batches are applied in order; a replayed `seq`
  and a stale batch are refused and the reason reaches the phone with the session it named; the
  PC-side lease expires on its own and releases a held button; retired sessions are refused; one
  owner per PC with explicit takeover; local revocation ends the session; a keyboard-only grant
  cannot move the pointer; typed text never appears in the relay's command rows or in any file the
  agent wrote (`tests/test_e2e_input.py`).
- Unit level (**unit-tested**): every component's own suite, listed in §1.

## 4. What has NOT been verified

- **The test kit on its real target**: never run on Windows (Windows PowerShell 5.1, Docker Desktop,
  the tray, the extension in Chrome/Edge) or with an iPhone, except for the first Windows run below.
  Quick tunnels cannot be created in the build environment (its network policy blocks
  `api.trycloudflare.com`); the rehearsal used stable-address mode behind a local HTTPS stand-in.
- **First Windows run (2026-10-09, the owner's PC, Docker Desktop)**: step 1 found uv, prepared the
  agent, cleared test data and **created both quick-tunnel addresses**; the image build then failed
  with "failed to get console: The handle is invalid" (Docker's animated progress needs the console,
  and the kit pipes program output through PowerShell). Fixed by plain progress for every compose call
  and the extension build. On the re-run **step 1 passed**; step 2 hit two pywin32 API mistakes in
  `bridge/ipc.py` (wrong modules for `ProcessIdToSessionId` and `FILE_FLAG_FIRST_PIPE_INSTANCE`), now
  fixed and guarded by `mypy --config-file mypy-windows.ini` in CI. pycaw, comtypes, winsdk and the
  ctypes calls have no stubs, so the Windows adapters can still fail at runtime the first time they run.
- **Windows**: none of `pc-agent/dome_agent/platform/windows/*` (volume via pycaw, media sessions via
  winsdk, app launching, lock, power, start-at-login, native-host registry entries) has run on a
  Windows machine. The PyInstaller specs have not been built. Follow `docs/WINDOWS_INSTALL.md` and the
  manual checklist at the end of `pc-agent/README.md`.
- **Manual input on real devices**: `SendInput` injection, multi-monitor and mixed-DPI motion, pointer
  acceleration, UAC/lock/elevated-window refusal, the named mutex and repair have not run on Windows;
  the touchpad gestures (pointer capture order, two-finger tap), the phone keyboard (IME, autocorrect,
  dictation, emoji, `beforeinput` order) and backgrounding have not run on an iPhone. The checklists
  are in `pc-agent/README.md`, `mobile-app/README.md` and `docs/INPUT_CONTROL.md`; spec §10A F lists
  the required observations. The Free-feature claim must wait for them (spec §10A).
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
- A test run that suddenly reports `connection to server on socket "/tmp/.s.PGSQL.54329" failed` for
  every test means PostgreSQL died, not that the code broke: check `pg.log` for the checkpointer
  PANIC above, restart the cluster, re-run.

- Tailwind 4 puts utilities in a cascade layer, so any **unlayered** rule beats every utility: an
  unlayered `button, a, input { font: inherit }` silently dropped all text/font/min-height utilities on
  controls. Element defaults live in `@layer base` (`mobile-app/test/styles-layering.test.ts`).
- DoMe keys an account by OIDC issuer + subject. In the test kit a new quick-tunnel address is a new
  issuer, hence a new empty account; the PC must re-link with a **new key** (`dome-agent unlink
  --new-key`), because the service never moves a PC key between accounts.
- Tests that assert right after a fixed number of event-loop ticks race real WebCrypto signing (it
  finishes on a worker thread) and the relay sharing the test's event loop; wait for the work itself
  (`InputSessionClient.whenSent()`, `waitFor`, a ping/pong round trip, relay-side counts).
- Docker here uses the `vfs` storage driver (a full copy per layer): repeated image builds fill the
  session's disk allowance; `docker builder prune -af` and `docker image prune -a` recover it.
- After a container restart, the test PostgreSQL cluster (`/tmp/pgdata`, owned by `nobody`) must be
  started again as its owner.

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

0. **Run the test kit for real** (`testkit/README.md`): Windows 11 + Docker Desktop + an iPhone. It is
   the fastest route to items 1 and 2 below; record what breaks in `docs/PROGRESS.md`.
1. **Windows device pass**: install Python 3.12 on a Windows 11 machine, `uv sync` in `pc-agent`,
   run `dome-agent link`, register the native host, load the extension unpacked, and work through the
   manual checklists in `pc-agent/README.md` and `browser-extension/README.md` (now including the
   touchpad/keyboard injection, single-instance and repair rows). Fix what breaks;
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
8. Re-run the **cross-component security review** after the Windows and iPhone passes (the first one,
   2026-10-09, confirmed the core guarantees and produced the hello proof and the honest-outcome
   fixes; see `docs/PROGRESS.md`).

## 8. Security review summary (2026-10-09)

A cross-component reviewer traced every seam and reported: signing key ↔ grant binding, snapshot
narrowing, relay non-forgeability, confirmation single use, replay/expiry, tenancy, pairing, hidden
bypasses, secrets in logs, fake success, native messaging/IPC identity and the PWA's key handling
all **fine**; two **major** seam gaps (controller socket bound by a bare kid; `failed` reported for
commands already forwarded) and one **minor** (CSP `connect-src`) — all three fixed the same day
with contract updates, code, tests and docs. Residual items are the ones in each `KNOWN_ISSUES.md`
and §6 above.

## 9. How this was built

Every component was produced from its design document in `docs/design/` by a builder, reviewed by an
independent skeptical reviewer who re-ran the tests, and fixed; a second review/fix round ran before
the documentation pass. Builders could not change the contract; they recorded gaps in
`CONTRACT_ISSUES.md`. Reviews and fixes are summarised in `docs/PROGRESS.md`. The documents under
`docs/` were written from the code after the builds, with the honesty rules above.
