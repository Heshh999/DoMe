# Progress log

Newest first. Each entry: what changed, what was actually run, evidence tag, what is next.

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
