# Handoff

Last updated 2026-10-13 (UTC), branch `claude/stoic-heisenberg-txq6ja`. Read this first if you are
continuing the work. `docs/PROGRESS.md` is the dated log; `docs/ACCEPTANCE.md` is the evidence matrix;
each component keeps its own `README.md`, `DECISIONS.md`, `KNOWN_ISSUES.md` and `CONTRACT_ISSUES.md`.

Evidence tags used everywhere in this repository: **unit-tested**, **integration-tested**,
**load-tested**, **Windows-device-tested**, **iPhone-tested**, **not yet verified**. No formal
Windows-device-tested or iPhone-tested pass has been recorded in `docs/ACCEPTANCE.md` yet; the owner has
reported features working on their Windows 11 PC and iPhone through the test kit (§0).

## 0. Current state (2026-10-13)

What happened since the 2026-10-09 baseline that §1–§6 describe (details in `docs/PROGRESS.md`):

- **Owner's devices** (2026-10-09 to 10-11, through `testkit/`): the iPhone (Safari) paired with the
  Windows 11 PC over quick tunnels; after the second test round's fixes the owner reports the Remote
  volume slider, YouTube control in Edge and Remote → Search YouTube, the Type phrases, the Apps window
  buttons and Lock working. Sleep, restart and shut down are not reported yet.
- **Protocol 1.2** (2026-10-11): window controls (maximise, restore, full screen), YouTube search and
  open result, a much richer Type grammar, the tray's **Add an app…**.
- **AI butler, step 1** (2026-10-11): the Ask screen (talk or type), the deterministic parser first and
  Claude only for words it does not know, every plan shown before it runs, behind consent and off until
  turned on. The real Anthropic API had not been called from here (no key); the committed `plans.json`
  keeps AI allowances at 0.
- **Brand** (2026-10-10/11): the owner's logo and the full name "DoMe Remote" recorded, the logo
  traced into vector files, new icons everywhere.
- **Design A "Midnight Studio"** (2026-10-12/13): the owner chose Design A over B
  (`mobile-app/DECISIONS.md` 60); every website and app screen restyled in light and dark; a 66 s demo
  film on the home page and `/demo`, exportable as 1080p and vertical video (DECISIONS 61); the website
  states Free / Pro / Pro Plus as static content, with Pro and Pro Plus "Coming at paid launch",
  Screen view "Coming" and point-and-talk "Planned" (DECISIONS 62–63). Two polish rounds followed; the
  second is saved as a checkpoint (6d76945) and still in progress. mobile-app: 68 files, 1218 tests pass.
- **Domain** (2026-10-13): see the next subsection.

### Domain

The founder **registered `domeremote.com` and `domeremote.app` on Cloudflare (Registrar, founder's
account) on 2026-10-13**. Commit 0a7bf6a made `https://domeremote.com` the single origin for the
website, PWA, API and WebSockets (`staging.domeremote.com` for staging; `.app` and `www` only redirect,
because the session cookie, the Origin check and the phone's signing key are per origin) and wired it
into the code: link-preview tags and image, `robots.txt`, `sitemap.xml`, `X-Robots-Tag: noindex` outside
production, the extension's homepage link. **Nothing is configured at Cloudflare yet.** The DNS records,
redirect rules, DNSSEC and identity-provider callbacks are written up in `deploy/README.md` "Domain";
carry them out once the Fly apps exist (§7 item 5). The Windows installer points `DoMe.exe link` at
`https://domeremote.com` (`dome-server.json` in the install folder, no environment variable; §7 item 4).

## 1. Where things stand

Test counts in this table are from 2026-10-09 unless dated; §2 has the latest recorded counts.

| Area | What exists | Evidence |
| --- | --- | --- |
| `shared/protocol` (protocol 1.2, registry 1.2) | Action registry (37 actions incl. 1.2 window controls and YouTube search, per-action `since` version stamping, 8 capabilities incl. Free `pointer`/`keyboard`), plans, error codes, JSON Schemas for envelopes, commands, confirmations, relay/bridge frames, the signed manual-input stream (`input_batch`, `input_ack`, `input_session`, `grant_update`), per-action results, entitlement claims, REST bodies incl. support tickets; normative rules in `version.json → rules` (incl. `input_sessions`, `grant_update`, `ai_eligibility`); cross-language fixtures. Changes since 1.0 are additive; 1.0 peers stay compatible and never receive input frames. | unit-tested: `shared/python` 210, `shared/ts` 116; each language verifies the other's signed fixtures |
| `cloud-api` | FastAPI + PostgreSQL: OIDC Authorization Code + PKCE sign-in, sessions, device-code PC linking, controllers, grants, PC-side pairing, relay (`/ws/agent`, `/ws/controller` with signed hello proof), command lifecycle, plan limits, entitlement assertions, security events, support tickets (Alembic `0002`), and the 1.1 input path: signed `input_batch` routing with per-event-type grant coverage and a per-controller budget, `input_ack`/`input_session` delivery, `grant_update`, agent input rejections routed to the batch's phone. Billing/usage/layout/routine/operator tables exist without routes. | integration-tested against real PostgreSQL + `tools/dev-idp`: 120 tests; ruff, mypy (source and tests) clean |
| `pc-agent` | The Windows user agent: link, identity, outbound relay client, local authorization, coalescing queue, confirmations, SQLite journal, every registry action, Windows adapters (import-gated), Native Messaging host, tray, control channel, diagnostics, one PyInstaller folder for both programs and a per-user Inno Setup development installer (2026-10-13, §7 item 4); for 1.1: the manual-input session manager (one owner per PC, agent-issued id, PC-side lease watchdog, seq/age/capability checks with a skew-tolerant age estimate, ordered dispatch, held-input release on every end reason, content-free crash recovery), a `SendInput` Windows input adapter, local `pointer`/`keyboard` grants with `grant_update`, single instance per Windows session with `repair`, power confirmation copy about losing remote access. | unit-tested on Linux with the fake platform: 604 tests (+3 skipped); the pipe, native-host, window-control, tray and adapter subset also passes on windows-latest CI; ruff + mypy clean; the Windows adapters (SendInput, mutex, secure-desktop detection) are **not yet verified** on a device |
| `browser-extension` | Chrome/Edge MV3: service worker with native port + reconnect alarms, content-script YouTube player adapter (transition-observed Next/Previous, `tab_token` and `expected_video_id` guards, context flags), build-time precompiled validators (MV3 CSP forbids Ajv's `new Function`), popup. | unit-tested with hand-written DOM fixtures: 154 tests; Vite build succeeds; **never loaded in a real browser against youtube.com** |
| `mobile-app` | React 19 + Vite + Tailwind PWA: sign-in, PC link approval, pairing (QR/code, verification digits), dashboard with Now Playing, remote, command page, apps, devices with touchpad/keyboard permissions, settings, confirmation modal with power-access copy, billing status, routines preview; for 1.1: Touchpad (gesture state machine, explicit drag, clicks, Stop Input), Keyboard (commit-once composition, Compose and Send, shortcut palette, never deletes text it did not send), the input session client, Health screen with layered states and one next action each, support ticket submission and status, release notes, brand icons. Since then (§0): 1.2 window controls and YouTube search, Ask (AI butler step 1), the "Midnight Studio" redesign of every app and website screen in light and dark, the demo film and `/demo`, Free / Pro / Pro Plus as website content, a development-only design preview (`/preview…`). | component-tested in jsdom: 1218 tests (2026-10-13); typecheck, lint and production build clean; the 1.2 features owner-reported on an iPhone; the redesign **not iPhone-tested** |
| `tests/` | Cross-component suite: the real `dome-agent` process (fake platform, headless) + the real relay + real PostgreSQL + real OIDC login + an ES256-signing controller simulator + a fake extension; now also the manual-input scenarios (grant_update, ordering, replay/stale rejections reaching the phone, lease expiry releasing a held button, takeover, revocation, keyboard-only grant, no typed text on disk). | integration-tested (Linux): 23 passed in 227 s (incl. 3 manual-input tests and the load smoke: 400 commands in 10.18 s = 39 cmd/s, p50 986 ms, p95 1199 ms, RSS 208 → 212 MiB) |
| `tools/dev-idp` | Development-only OpenID Connect issuer (RS256, PKCE S256). Never deployed; cloud-api has no auth bypass. Optional passphrase gate (`DEV_IDP_PASSPHRASE`) for the test kit: the right passphrase always works, wrong ones never lock anyone out; readable refusal page for browsers. | unit-tested: 14 tests (run in CI) |
| `testkit/` | Try DoMe on a Windows PC and an iPhone without development tools: `1 Start test server.cmd` (whole stack in Docker, two Cloudflare quick tunnels or your own stable HTTPS addresses, passphrase-gated sign-in, QR code), `2 Start DoMe on this PC.cmd` (uv-managed agent from source, extension built in Docker with a pinned ID, native host, link; re-links automatically when the test account changed), `3 Stop test server.cmd`; `testkit/README.md` is the tester's guide. | 28 pwsh checks (parse, ASCII, helpers; run in CI). Rehearsed end to end on Linux with pwsh 7 and an HTTPS stand-in in stable-address mode: sign-in, link, wss relay, pairing, a phone command executed. Since then run by the owner on Windows 11 with quick tunnels and an iPhone (§0, §4) |
| `brand/` | The owner's DoMe Remote logo traced into editable SVG (full and compact lockups for light and dark, mark, icon, mono), a dependency-free export script producing PWA icons, favicons, Windows tray PNGs and `.ico` files, and `BRAND.md` (palette measured from the logo, recommended and as-shipped UI tokens, type, sizes, usage). The PWA, extension, tray, both Windows programs, Setup and the Start menu shortcut use the exports (the last three since 2026-10-13, awaiting the first Windows build). | unit-tested: 33 tests plus export and consumer-copy drift checks (2026-10-13); renders not compared against a browser rasterizer |
| `deploy/`, `.github/workflows/ci.yml` | Multi-stage Dockerfile (PWA + API, non-root, healthcheck), entrypoint that refuses to invent a signing key outside development, `fly.toml` tuned for long-lived WebSockets, Compose file for machines with Docker, deploy README; CI with Python/Node/integration/container jobs and pinned actions. | CI runs on GitHub Actions since 2026-10-11 (13 runs by 2026-10-13, the latest green; the Windows job runs the pipe, native-host, tray and adapter tests); `windows-installer.yml` (2026-10-13) awaits its first run; the image was built and run by the test kit rehearsal (Linux, the sandbox proxy certificate added to the two build stages only); **not yet verified**: deployed (no Fly account here) |
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

Tests, per component (each is a standalone package with its own lockfile). Counts are the last recorded
runs: 2026-10-11 (`docs/PROGRESS.md`, AI butler step 1) except mobile-app and brand (2026-10-13):

```sh
cd shared/python && uv run pytest -q           # 264
cd shared/ts && pnpm test                       # 124
cd cloud-api && uv run pytest -q                # 316 (+1 live AI test skipped without a key; needs the local PostgreSQL and tools/dev-idp venv)
cd pc-agent && uv run pytest -q                 # 604 (+3 skipped: root, named-pipe-only, no Tk)
cd pc-agent && uv run mypy --config-file mypy-windows.ini  # Windows code paths vs. pywin32 stubs
cd browser-extension && pnpm test               # 154
cd mobile-app && pnpm test                      # 1218 (68 files); `pnpm check` = typecheck + lint + test + build
cd tools/dev-idp && uv run --extra dev pytest -q  # 14
pwsh -NoProfile -File testkit/tests/kit.tests.ps1  # 270 test-kit script checks
cd brand && pnpm check                          # 33 tests + export and consumer-copy drift checks
cd tests && uv run pytest -q                    # 30, ~5.5 min; `-k load` for the load smoke alone
make lint typecheck                             # ruff / mypy / eslint / tsc across the repo
```

The website and app design can be reviewed without a backend: `cd mobile-app && pnpm dev`, then
`/preview…` (`mobile-app/README.md` "Design preview"). The demo film is exported to video with
`node scripts/export-film.mjs` in `mobile-app/` (README "The demo film"); exports are never committed.

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
  Later runs fixed a QR-picture lock (Windows Photos) and a Safari-only pairing refusal (`Origin: null`
  under `no-referrer`, now `same-origin`); then **the iPhone (Safari) paired with the PC over quick
  tunnels**. Later rounds (2026-10-11, `docs/PROGRESS.md`) fixed volume, the YouTube bridge and other
  Windows adapter mismatches; the owner then reported volume, YouTube in Edge, YouTube search, Type,
  the Apps window buttons, touchpad, keyboard and Lock working. Sleep, restart and shut down are not
  reported, and none of it is recorded in `docs/ACCEPTANCE.md` with build and iOS versions yet.
- **Windows**: the adapters in `pc-agent/dome_agent/platform/windows/*` have run only on the owner's PC,
  through the test kit, for the features the owner reported above (volume, YouTube via Edge, apps and
  their windows, lock, touchpad, keyboard). Power (sleep, restart, shut down), start-at-login and the
  Windows media-session fallback are not reported. The PyInstaller folder has been built and run only
  on Linux; the Windows build and the installer await their first `windows-installer.yml` run. Follow
  `docs/WINDOWS_INSTALL.md` and the manual checklist at the end of `pc-agent/README.md`.
- **Manual input on real devices**: `SendInput` injection, multi-monitor and mixed-DPI motion, pointer
  acceleration, UAC/lock/elevated-window refusal, the named mutex and repair have not run on Windows;
  the touchpad gestures (pointer capture order, two-finger tap), the phone keyboard (IME, autocorrect,
  dictation, emoji, `beforeinput` order) and backgrounding have not run on an iPhone as checklists. (The
  owner reports the touchpad and keyboard working in everyday use, 2026-10-11; the edge cases in the
  checklists are not reported.) The checklists
  are in `pc-agent/README.md`, `mobile-app/README.md` and `docs/INPUT_CONTROL.md`; spec §10A F lists
  the required observations. The Free-feature claim must wait for them (spec §10A).
- **Chrome/Edge**: the extension has never been loaded unpacked against the real youtube.com DOM
  (`browser-extension/KNOWN_ISSUES.md` K1); the manual checklist is in `browser-extension/README.md`.
- **iPhone**: the owner has used the PWA in iOS Safari (pairing, the remote, touchpad, keyboard, Type);
  standalone (Home Screen) mode, resume behaviour, QR scanning and key persistence across restarts are
  not reported; see `docs/IPHONE_SETUP.md` and `mobile-app/KNOWN_ISSUES.md` #5.
- **Production identity provider**: only `tools/dev-idp` was used; Auth0/Keycloak settings exist in
  `cloud-api/README.md` but have not been exercised.
- **Hosting**: `deploy/` has not been deployed; `DOME_TRUSTED_PROXIES` for Fly.io is a deployment fact
  that must be set by hand (`cloud-api/KNOWN_ISSUES.md` #5). The domain is registered but no DNS record,
  redirect or certificate exists yet (§0 "Domain").
- **Billing and plans**: no Stripe code exists. `cloud-api` only reports `billing_enabled` from the
  presence of a Stripe key, and the PWA shows plan state without any checkout. `docs/BILLING.md` is a
  design. The website's Free / Pro / Pro Plus figures are static content (`mobile-app/src/content/plans.ts`);
  the contract (`shared/protocol/plans.json`) has no Pro Plus, a monthly AI period with allowances of 0,
  and the old $5.99 / $49.99 `pricing_defaults`.
- **The redesign and the film on an iPhone**: checked only in headless Chromium screenshots at phone
  and desktop sizes; the checklist row is in `mobile-app/README.md`.
- **Screen view and point-and-talk** do not exist in the product; they appear only on the website and
  in the film, labelled "Coming" and "Planned".
- **Pro features**: routines and layouts have tables and plan limits but no API routes or execution;
  the Routines page is an explicitly labelled preview.
- **Operator tooling**: `OperatorUser`/`OperatorAudit` tables exist, nothing is served.
- **CI**: `.github/workflows/ci.yml` runs on GitHub Actions (since 2026-10-11; 13 runs by 2026-10-13,
  12 green, the one failure fixed by afdc130). `.github/workflows/windows-installer.yml` (the installer
  build and smoke test) has not run yet.

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

## 7. Next tasks, in order (2026-10-13)

Finish polish round 2 of the redesign first (checkpoint 6d76945); then:

1. **Plans metering (#51)**: make the website's Free / Pro / Pro Plus real (the figures are in
   `mobile-app/README.md` "Plan content"). A contract MINOR: a `pro_plus` plan; AI allowances as a 5-hour
   window that starts at the first AI request plus a weekly window that refreshes 7 days after the week's
   first use, replacing the monthly period; Free's one-time taste of 10 requests; top-ups (100 requests
   for $2.99, bought only on request, never automatic); Free's Screen view minutes ready for #52.
   cloud-api: extend the existing metering ledger and reservations to the two windows and top-ups;
   Stripe prices for both paid plans, monthly and annual, and `pricing_defaults` to match. App: the
   exact refresh time ("Refreshes Saturday at 2 pm"), a warning at 80 %, the allowance line in Settings.
   Then read the figures in `mobile-app/src/content/plans.ts` from the contract (keep the "Coming at paid
   launch" labels until paid launch) and update `docs/BILLING.md`, `COST_MODEL.md` and
   `PRODUCT_AND_PLANS.md`. Everyday commands DoMe understands by itself never count, on any plan.
2. **Screen view (#52)**: the PC's screen live on the phone with tap to click, two-finger scroll, a
   floating mini view, PC sound with mute and a TV-style remote for full-screen apps, as the website and
   film describe it. Not designed yet: it needs a design document, an ADR for the video transport (the
   relay carries JSON frames today), a contract MINOR, capture on the agent, and Free's 10 minutes per
   5-hour window metered by #51. Nothing is recorded and the AI never sees the stream. When it ships,
   change `SCREEN_VIEW_STATUS` and every "in development" line together (`mobile-app/DECISIONS.md` 63).
3. **AI butler step 2: click by name (#42)**: "click that" / "click Subscribe" acts on a control found by
   its Windows accessibility name, shown on the phone and confirmed before it runs. The owner ruled out
   screenshots, so `docs/design/assistant.md` §7 (screenshot with a box) must be rewritten before
   building; it keeps the rest of §7: its own contract MINOR, a new consent version, the action
   challenge-confirmed, pointer and keyboard primitives human-only. The film's point-and-talk (a finger
   on the Screen view picture) also needs #52. The website says "Planned" until then.
4. **Windows installer — pulled forward, in progress (2026-10-13)**: written ahead of items 1–3, which
   keep their order. What exists: one PyInstaller folder for `DoMe.exe` and `dome-native-host.exe`
   (`pc-agent/packaging/dome-agent.spec`, `dome.ico` wired in), a per-user Inno Setup installer
   (`pc-agent/packaging/installer/DoMe.iss`: no admin, the server address in `dome-server.json` for
   `DoMe.exe link`, the native host under HKCU when an extension id exists, start at login opt-in, an
   upgrade that asks DoMe to quit and changes nothing when it does not, downgrade refusal, uninstall
   cleanup that keeps the settings unless asked), `DoMe.exe quit` / `start-at-login`, and
   `.github/workflows/windows-installer.yml`, which builds it and smoke-tests install, tray, upgrades and
   uninstalls on windows-latest (`docs/WINDOWS_INSTALL.md` "The installer"). Next: the first green
   `windows-installer.yml` run (fix what it reports); the founder installs it on his Windows 11 PC as a
   standard user; a tray "Link this PC…" item; a Chrome Web Store / Edge Add-ons listing, then the
   production extension id in the release build; Authenticode signing (most likely Azure Trusted
   Signing) in a separate protected job; an Inno Setup commercial licence before paid launch; an updater
   with signature verification (`docs/SECURITY.md` T12). The Download page says "not yet published" until
   signing exists.
5. **Domain and staging**: create the Fly apps, then carry out `deploy/README.md` "Domain" (DNS-only
   records for Fly, redirect rules for `.app` and `www`, DNSSEC, identity-provider callbacks) and the
   staging steps there: PostgreSQL, Auth0 or Keycloak, `DOME_TRUSTED_PROXIES`, the entitlement signing
   key. (The GitHub Actions workflows already run on every push.)
6. **Device evidence**: record the owner's Windows 11 + iPhone results in `docs/ACCEPTANCE.md` with build
   and iOS versions; still to report: sleep, restart, shut down, and the redesign and film on the iPhone
   (`mobile-app/README.md` checklist). The manual checklists in `pc-agent/README.md`,
   `browser-extension/README.md` and `docs/IPHONE_SETUP.md` remain the reference.
7. **Phase B beta**: invite-only Free accounts, support diagnostics intake (table exists), the operator
   read-only views (`OperatorUser`/`OperatorAudit`).
8. **Phase C billing, before paid launch**: implement `docs/BILLING.md` (Stripe Checkout/Portal, webhook
   intake with dedup, the subscription → entitlement state machine, grace and downgrade selection; top-up
   purchases from #51 go through it), then routines and layouts routes and the agent-side routine
   execution. Only then do the "Coming at paid launch" labels change.
9. **Protocol MINOR bump** applying §6 (it can ride with #51's MINOR); the **agent socket frame budget**
   and the other deferred items in each `KNOWN_ISSUES.md`.
10. Re-run the **cross-component security review** after the device passes and #51 (the first one,
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
