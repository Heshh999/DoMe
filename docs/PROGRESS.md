# Progress log

Newest first. Each entry: what changed, what was actually run, evidence tag, what is next.

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
