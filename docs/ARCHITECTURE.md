# DoMe architecture

Status: pre-release engineering build, protocol 1.0. This document describes the system as it exists in
this repository today. Every claim about behaviour is backed by code under `cloud-api/`, `pc-agent/`,
`browser-extension/`, `mobile-app/` and the contract under `shared/protocol/`; every claim about
verification carries one of the evidence tags from `docs/spec/MASTER_PROMPT.md` §17
(unit-tested, integration-tested, sandbox-provider-tested, Windows-device-tested, iPhone-tested,
load-tested, not yet verified). Nothing in DoMe has been Windows-device-tested or iPhone-tested yet.

Foundational decisions and their rationale are in `docs/adr/0001-foundational-decisions.md`
(ADR-0001); per-component designs are in `docs/design/`. This document explains how the pieces fit,
where the trust and data boundaries are, how a command travels, what the relay can and cannot see,
and what the hosting target imposes.

## 1. One-paragraph summary

A customer's phone runs the DoMe PWA. It holds a non-extractable ECDSA P-256 key per installation
and signs every command. The customer's Windows PC runs the DoMe agent, which opens one outbound
WebSocket to the managed relay and never listens on the internet. The relay (one FastAPI process
with PostgreSQL) authenticates both ends, checks ownership and grants, forwards signed envelopes
verbatim and records only lifecycle rows. The PC re-verifies the signature against a key it approved
locally during pairing, checks the grant, replay journal, target and (for disruptive actions) a
signed confirmation, then executes the action through a Windows API or, for YouTube, through a
browser extension reached over Chrome Native Messaging. The PC reports the observed result; the
phone shows it. "Delivered" and "executed" are separate states everywhere.

```
 iPhone PWA                      cloud-api (relay)                       Windows PC
 ──────────                      ─────────────────                       ──────────
 React/TS, WebCrypto key   HTTPS  FastAPI + PostgreSQL        WSS (outbound)  dome_agent (Python, user session)
 /app/* remote  ──────────────►  /v1 REST, /ws/controller  ◄──────────────  /ws/agent client
 signs commands            WSS   /ws/agent, static PWA                       local authz, SQLite journal
                 ◄──────────────►                                            │
                                                                             │ Native Messaging (stdio)
                                                                             ▼
                                                                    dome-native-host ──IPC──► agent
                                                                             ▲
                                                                             │ chrome.runtime.connectNative
                                                                    DoMe for YouTube (MV3 extension)
                                                                             │ content script
                                                                             ▼
                                                                    youtube.com tab (<video>, buttons)
```

## 2. Components

| Component | Path | Runtime | Role | Persistent state it owns |
| --- | --- | --- | --- | --- |
| Shared protocol contract | `shared/protocol/` | JSON Schema + JSON | Single source of truth: action registry (30 actions, 6 capabilities), plans, error codes, envelope/command/confirmation/frame/bridge/result/entitlement/REST schemas, normative rules (`version.json → rules`), cross-language signing fixtures | none |
| Shared libraries | `shared/python` (`dome-protocol`), `shared/ts` (`@dome/protocol`) | Python 3.12, TypeScript 5.9 | One tested implementation per language of strict JSON parsing, key handling, ES256 signing/verification, registry and frame validation, command/confirmation build + verify, pairing derivations | none |
| cloud-api | `cloud-api/` (`dome_api`) | FastAPI, SQLAlchemy 2 async, Alembic, Authlib, joserfc, uvicorn | Accounts (OIDC relying party), sessions, PC linking, pairing, controller/grant inventory and revocation, command relay and lifecycle rows, entitlement assertions, static PWA serving | PostgreSQL (migration 0001) |
| pc-agent | `pc-agent/` (`dome_agent`) | Python 3.12 in the logged-in user's session; pywin32, pycaw, winsdk, psutil, pystray, tkinter on Windows | Outbound relay client, local authorization, serialized executor, action handlers, confirmation challenges, bridge server for the extension, tray, CLI, native-messaging host entry point | SQLite `state.sqlite3` (settings, local grants, bounded journal, challenges, approved apps, pending power, pending revocations); DPAPI-protected key and credential files |
| browser-extension | `browser-extension/` | Chrome/Edge Manifest V3, TypeScript | YouTube player adapter: background service worker ↔ native host; content script drives `<video>` and YouTube's own buttons | `chrome.storage.local`: `browser_instance_id`, `profile_label`, last connection state |
| mobile-app | `mobile-app/` | React 19, Vite 7, Tailwind 4, vite-plugin-pwa | Public website and the installable remote; controller identity, pairing UI, command lifecycle UI, confirmation modal, deterministic text commands | IndexedDB: non-extractable `CryptoKey`, controller id, selected PC; service-worker precache of the built shell only |
| tools/dev-idp | `tools/dev-idp/` | standalone Python process | Development-only OpenID Connect issuer so local runs and tests use a real OIDC login instead of a bypass | none (in-memory) |
| tests | `tests/` | pytest | Cross-component integration: real cloud-api + real agent process + Python-signed controller + fake extension on loopback | none |

Not present in this build (documented, not simulated): Stripe billing and the subscription state
machine, usage periods, custom layouts, routines, the operator interface, account-deletion jobs
(Phase C; their tables exist in migration 0001 and are empty), optional AI/voice (Phase E), signed
Windows installer/updater, Chrome Web Store listing, `deploy/` container/Fly.io/CI configuration
(`deploy/README.md` says "build in progress").

## 3. Trust and data boundaries

### 3.1 Trust zones

| Zone | Trusts | Does not trust | Enforced by |
| --- | --- | --- | --- |
| Phone (PWA on the API origin) | The API origin over TLS; its own IndexedDB key; the registry labels it ships with | Any string that originated on the PC (`challenge.display.detail`, media/window titles — rendered as plain text); any frame until validated against `relay-frames.schema.json` | `mobile-app/src/lib/relay.ts`, `confirmations.ts`, `api.ts` (`validateRest`, `validateFrame`, `validateResult`, `validateChallengeText`) |
| Relay (cloud-api) | Its database; the OIDC issuer's signed ID tokens; its own session rows | Anything in a frame or body until schema-validated; `account_id`/`controller_id`/`target_pc_id` in a payload until bound to the signing key record; the client-supplied IP unless the TCP peer is a configured trusted proxy | `dome_api/relay/router.py`, `auth/deps.py`, `security/proxy.py`, `dome_protocol.commands.verify_and_parse_command` |
| PC agent | Its own SQLite store (written only by local approval or by snapshot intersection); its DPAPI-protected identity; the JWKS at the API origin for entitlements | The relay (checks `hello_ack.pc_id`, snapshot identity, every envelope signature, `target_pc_id`, duplicates); the extension and every page-derived string (titles, video ids are display data) | `dome_agent/authz.py`, `agent.py`, `confirmations.py`, `bridge/server.py` |
| Browser extension | The native host it connected to (identity scoped by the OS: per-user named pipe with SID/session check on Windows, 0600 Unix socket elsewhere) | Web pages (no `externally_connectable`; worker accepts messages only from its own content scripts in top frames on `https://www.youtube.com/`) | `src/background/service.ts` (sender checks), `public/manifest.json` |
| Local Windows user | — | DoMe does not defend the PC against the user who is logged in. Whoever can edit `state.sqlite3` can edit the approved-app allowlist (documented boundary, `pc-agent/DECISIONS.md` #5) | — |

### 3.2 Identities and secrets

| Identity / secret | Created where | Stored where | Leaves its origin? |
| --- | --- | --- | --- |
| Account | OIDC issuer (Auth0/Keycloak/dev-idp) | `accounts(issuer, subject)` | Only as a signed ID token to the relay; DoMe never sees a password |
| Web session id (32 random bytes) | relay | Cookie `HttpOnly; SameSite=Lax; Secure` (Secure iff the origin is https); only SHA-256 in `sessions.token_hash` | Cookie only; never in URLs or `localStorage` |
| CSRF token | relay per session | `sessions.csrf_token`; read by the PWA from `GET /v1/session` | Sent back in `X-DoMe-CSRF` |
| PC identity key (ES256) | agent at first run | DPAPI-protected file on Windows (`%LOCALAPPDATA%\DoMe\secrets\pc_key.bin`); 0600 file elsewhere with a logged warning | Public JWK only (`POST /v1/agent-link/start`) |
| `device_code` / `user_code` | relay at link start | `device_link_codes` (device code hashed; user code plain, 8 Crockford symbols) | `device_code` to the PC only; `user_code` shown to the user and typed/opened in the browser |
| PC credential (32 random bytes) | relay at the first successful poll | Hash in `pc_credentials`; DPAPI-protected file on the PC | Only to `POST /v1/agent/token` |
| PC access token (1 h) | relay | Hash in `pc_access_tokens`; memory on the PC | `Authorization: Bearer` header on the `/ws/agent` upgrade only (query-string tokens are refused) |
| Controller key (ECDSA P-256, non-extractable) | PWA via WebCrypto | IndexedDB `CryptoKey` | Public JWK at pairing; `kid` (RFC 7638 thumbprint) in `hello` and every envelope |
| Pairing code (20 Crockford symbols, 100 bits) | agent | Memory on the PC and in the phone's component state during pairing; never written to disk, logs or the backend | To the phone out of band (QR fragment or typing). The backend receives only `code_hash = SHA-256("dome-pair-handle-v1\|" + code)` |
| Entitlement signing key (Ed25519) | operator | PEM at `DOME_ENTITLEMENT_SIGNING_KEY_PEM_PATH` | Public JWK at `/.well-known/dome-jwks.json` |
| `browser_instance_id`, `tab_token` | extension | `chrome.storage.local` / content-script memory | In bridge frames and `state` frames; identify a browser profile and one content-script attachment; not secrets |

### 3.3 What the relay can and cannot see

The V1 boundary (ADR-0001 D10; spec §5) is TLS in transit plus a trusted relay. Signing
authenticates commands; it does not hide them from the relay. Nothing in this repository is
end-to-end encrypted or zero-knowledge, and nothing should describe it that way.

The relay **can read**, in memory while routing:

- every signed command and confirmation payload (action, parameters, target such as tab id and
  expected video id);
- every `state` frame from a PC, including YouTube tab titles, video ids, media-session titles and
  artists, approved-app names and window titles — it keeps the latest `state` per online PC in
  memory to replay it to new subscribers (`version.json → rules.state_cache`);
- every `challenge_text` (it strict-parses a copy to validate it and forwards the original string);
- every `result` payload.

The relay **cannot**:

- forge or alter a command, confirmation or challenge without detection — envelopes are verified
  on the PC against a key the PC approved locally, and the phone hashes `challenge_text` verbatim
  into the signed confirmation;
- enroll a controller — the PC's local store is the only source of controller public keys, and
  `grants_snapshot` can only remove or narrow (`rules.grants_snapshot`);
- make the two pairing displays agree on a substituted key — the 6-digit verification code is an
  HMAC keyed by the pairing code the backend never receives (`rules.pairing_secret`);
- execute anything itself — `dome_api/relay` contains no action handlers;
- read a password — authentication is delegated to the OIDC issuer.

What the relay **persists** (`cloud-api/dome_api/db/models.py`, migration 0001): accounts, hashed
sessions, PC and controller records with public keys, grants, pairing sessions (hash only),
`commands` rows with `action`, `state`, `error_code`, timestamps and a payload digest — **no
params, no results, no titles** — and `security_events` whose `detail` passes through the log
redaction rules before storage. Logs are structured JSON with keys such as `token`, `pc_credential`,
`code_hash`, `challenge_text`, `payload`, `sig`, `title`, `email` and anything ending in
`_token/_secret/_code/_credential/_key` redacted; URLs are logged as route templates with
`user_code` masked and query strings dropped.

Retention as implemented: `sessions` expire (30-day sliding idle, 90-day absolute) but no purge job
deletes expired rows, `commands` and `security_events` rows are not pruned by any job yet, and the
PC's journal is bounded to 5,000 rows. A written retention table with purge jobs is still to be
produced (`docs/DATA_RETENTION.md` is listed as a required deliverable and does not exist yet).

### 3.4 Data the PC keeps

`state.sqlite3` (WAL): `settings` (`remote_enabled` default false, `media_while_locked`,
`start_at_login`, `pc_name`), `grants` (controller id, kid, public JWK, capabilities, display name,
revoked_at, snapshot id), `journal` (command id, digest, action, state, result JSON, error code,
timestamps; pruned to the newest 5,000 rows; rows found `executing` at start-up become
`outcome_unknown`), `challenges` (exact `challenge_text`, pinned kid, expiry, consumed_at),
`approved_apps` (app id, display name, absolute `.exe` path, pinned SHA-256), `pending_power`,
`pending_revocations`, persisted entitlement claims (never the token). Logs:
`%LOCALAPPDATA%\DoMe\logs\agent.log`, redacted. Diagnostics bundles are customer-initiated and
redacted (`dome_agent/diagnostics.py`).

## 4. Request and command flows

Frame and body names below are the `$defs` of `shared/protocol/schemas/*.json`; `docs/PROTOCOL.md`
gives the exact shapes. "Relay" means cloud-api.

### 4.1 Sign-in (phone or desktop browser)

1. PWA → `GET /v1/auth/login?return_to=/app` (relative paths only). Relay creates an `auth_flows`
   row (state, nonce, PKCE verifier) and redirects to the issuer's authorization endpoint.
2. Issuer authenticates the customer and redirects to `GET /v1/auth/callback?code&state`.
3. Relay exchanges the code with PKCE, validates the ID token (issuer, audience, nonce, expiry,
   signature via the issuer's JWKS), upserts the account by (issuer, subject), creates a session
   row, sets the `dome_session` cookie and redirects to `return_to`.
4. PWA → `GET /v1/session` → `session_response` (account, `csrf_token`, plan, limits,
   `protocol_version`). Every later state-changing request carries `X-DoMe-CSRF` and an exact
   `Origin`; both are checked in `current_account`.

Evidence: integration-tested (cloud-api `tests/test_auth_and_sessions.py` against `tools/dev-idp`).
Not tested against Auth0 or Keycloak; a production issuer must be registered by the founder.

### 4.2 Linking a PC to an account (device-code shape, ADR-0001 D4)

1. Agent generates its ES256 identity key (DPAPI-protected) and calls
   `POST /v1/agent-link/start {pc_public_jwk, agent_version, platform}` (no auth; 10/hour per IP).
   Relay stores SHA-256(`device_code`) and `user_code`, answers
   `{device_code, user_code, verification_uri_complete, expires_in: 600, interval: 5}`.
2. Agent opens the system browser at `<origin>/link?user_code=…` and polls
   `POST /v1/agent-link/poll {device_code}` (428 while pending).
3. The signed-in customer sees `GET /v1/agent-link/{user_code}` (agent version, platform, kid) and
   calls `POST /v1/agent-link/{user_code}/approve {pc_name, remote_enabled: true}` (session +
   CSRF). In one transaction the relay locks the account row, counts enabled PCs against
   `plans.max_enabled_pcs` and creates the PC enabled or, over the limit, disabled with
   `reason: DEVICE_LIMIT_REACHED`. A key that belongs to a PC that is still linked is refused with
   409 `PC_ALREADY_LINKED` (cloud-api `DECISIONS.md` #2).
4. The next poll returns, exactly once, `{pc_id, account_id, pc_credential, relay_url, api_url,
   pc_name, enabled}` (code → `consumed`). The agent stores the credential under DPAPI.
5. Agent → `POST /v1/agent/token {pc_credential}` → `{access_token, expires_in: 3600, pc_id,
   account_id}`; `pc_id`/`account_id` are fixed from now on (`rules.agent_identity`).

Known gap: the start request carries no proof of possession of the PC key (cloud-api
`CONTRACT_ISSUES.md` #10); mitigated server-side, cryptographic fix proposed for the next MINOR.
Evidence: integration-tested (cloud-api `tests/test_agent_link.py`; `tests/` drives
`dome-agent link --no-browser`).

### 4.3 Agent connection

1. Agent opens `wss://…/ws/agent` with `Authorization: Bearer <access token>` and **no**
   `Origin` header (agent endpoints refuse any Origin). Upgrade refusals carry real HTTP statuses
   (401 bad token, 403 Origin present, 503 relay full). Fewer than 5 minutes of token lifetime
   left → the agent fetches a new token first (`rules.token_and_entitlement_refresh`).
2. Agent → `hello{component: agent, protocol_versions, registry_version}`; relay → `hello_ack{pc_id}`.
   If `pc_id` differs from the stored identity the agent closes, stops reconnecting and shows a
   re-link prompt. A second socket for the same PC supersedes the first (close 4001; the superseded
   agent does not auto-reconnect; tray offers Reconnect).
3. Relay → `grants_snapshot{pc_id, account_id, snapshot_id, pc_enabled, controllers[], entitlement_assertion?}`.
   The agent applies it as an intersection over its local store **before** accepting any command:
   locally approved controllers missing from the snapshot are marked revoked; listed controllers it
   never approved are ignored and logged as a security event; capabilities = local ∩ snapshot;
   in-flight commands from revoked controllers are canceled. A command arriving earlier is answered
   `result{failed, PC_RECONNECTING}` and not journaled.
4. Agent → `state` immediately, then on change (debounced 500 ms) and every 30 s. Relay marks the PC
   online, caches the latest `state`, broadcasts `pc_status{online}` to subscribed controllers of
   the same account, then re-delivers every unexpired claimed `pairing_request` for this PC.
5. Agent refreshes its entitlement assertion (`POST /v1/agent/entitlement`) on every connect and at
   80 % of the assertion lifetime; pings every 25 s; reconnects with exponential backoff
   1 s → 60 s with full jitter. Journaled results of commands that were executing when the socket
   dropped are re-sent once after the next snapshot (`rules.late_results`).

Evidence: integration-tested (cloud-api `tests/test_relay_lifecycle.py`; pc-agent
`tests/test_relay_client.py` against `testing/fake_relay.py`; `tests/` with the real agent).

### 4.4 Controller connection

1. PWA opens `wss://<origin>/ws/controller` with the session cookie; the relay checks the cookie
   session and an exact `Origin`.
2. PWA → `hello{component: controller, kid, …}`. The relay resolves (session account, kid) to a
   controller row. Found and not revoked → the socket is **bound** and `hello_ack{controller_id}`
   says so; otherwise `hello_ack` without `controller_id` and the socket may not
   subscribe/command/confirm/cancel (`error GRANT_MISSING`). Every envelope on the socket must carry
   the socket's kid (else `UNKNOWN_KEY`, close 4003) — `rules.controller_socket_identity`.
3. PWA → `subscribe{pc_ids}` (idempotent, replaces the set). Each id must be a PC of the account on
   which **this controller** holds a live grant, else `error{GRANT_MISSING, ref_pc_id}`. For each
   accepted id the relay sends `pc_status` then the cached `state` (unchanged, original `at`).
4. On `visibilitychange`/`online` the PWA re-subscribes, pings, treats state as stale until a fresh
   `pc_status`/`state` arrives, and reconnects if nothing arrives within 5 s (iOS suspends sockets
   that still report open; mobile-app `DECISIONS.md` #3). State older than 75 s disables
   consequential controls.

Evidence: integration-tested (cloud-api `tests/test_relay_routing.py`, cross-account cases in
`tests/test_security_fixes.py`); unit-tested (mobile-app `test/relay.test.ts` with a fake socket).
iPhone resume behaviour: not yet verified.

### 4.5 Pairing a phone with a PC (ADR-0001 D5)

The pairing code is a secret the backend never sees.

1. PC (tray "Pair a phone" or `dome-agent pair`) generates a 20-symbol Crockford base32 code,
   calls `POST /v1/pairing/start {code_hash}` (PC bearer) → `{pairing_id, expires_at}` (5 min, one
   open session per PC) and shows the formatted code and a QR of `<origin>/pair#code=…` (fragment:
   never sent in Referer, scrubbed from the address bar by the PWA before rendering).
2. Phone (signed in to the **same account**) scans or types the code, derives `code_hash`, and
   calls `POST /v1/pairing/claim {code_hash, public_jwk, display_name, requested_capabilities}`
   (session + CSRF; 5 attempts per 15 min per account and per IP; every failure is a
   `pairing_failed` security event). The relay finds the open session by hash within the account
   (anything else is `PAIRING_CODE_INVALID`, indistinguishable from a wrong code), derives `kid`,
   enforces `plans.max_controllers` transactionally and answers 202 `pairing_status_response{state: claimed}`.
3. Relay → agent `pairing_request{pairing_id, code_hash, controller_display_name, public_jwk, kid,
   requested_capabilities, expires_at}` now, or after `grants_snapshot` on the agent's next connect
   (`rules.pairing_offline`).
4. Agent ignores a request whose `code_hash` is not its current session's, recomputes
   `kid_from_jwk(public_jwk)` and refuses on mismatch, then computes
   `HMAC-SHA256(key = code, msg = "dome-pair-verify-v1|" + pairing_id + "|" + pc_id + "|" + kid)`
   → first 8 bytes big-endian mod 10⁶, zero-padded (test vector `412098` in
   `shared/protocol/fixtures/es256-*.json`). Both devices show the 6 digits and the requested
   capabilities; the phone's display name is untrusted text.
5. The customer approves **on the PC**. The agent stores the local grant (under a provisional
   `pending-<kid>` id until the snapshot names the real `controller_id`; pc-agent
   `CONTRACT_ISSUES.md` #1) and sends `pairing_decision{approve, kid, granted_capabilities}`.
6. Relay upserts `controllers(account, kid)`, inserts the grant with granted ∩ requested
   capabilities, writes `controller_paired`, pushes a fresh `grants_snapshot`. The phone, polling
   `GET /v1/pairing/{pairing_id}`, sees `approved` with `controller_id`, reopens its socket so
   `hello` binds, and subscribes.

Evidence: unit-tested derivations in both languages against shared fixtures; integration-tested
(cloud-api `tests/test_pairing.py`; `tests/test_e2e_security.py` with the real agent, including
claim-while-offline delivery and "no grant before `pairing_decision`"). QR scanning on a real
iPhone camera: not yet verified.

### 4.6 An ordinary command: "Next" on a background YouTube tab

```
Phone                      Relay                          Agent                    Extension
  │ build+sign command       │                               │                         │
  │──command{pc_id,env}─────►│ 1-10 routing checks           │                         │
  │                          │──command{env, relay{…}}──────►│ authz steps 1-10        │
  │◄─ack{accepted}───────────│◄─ack{accepted}────────────────│ enqueue                 │
  │◄─ack{executing}──────────│◄─ack{executing}───────────────│ journal row = executing │
  │                          │                               │──bridge_request{next}──►│ click .ytp-next-button,
  │                          │                               │◄─bridge_response{ok}────│ observe video_id change
  │◄─result{succeeded}───────│◄─result{origin:agent}─────────│ validate result, journal│
  │ validate, render         │ update commands row           │                         │
```

Phone side (`mobile-app/src/lib/commands.ts`): choose the target by the spec's order (explicit
selection while valid, else the single controllable tab, else the single playing tab, else ask),
including `browser_instance_id`, `tab_id`, `tab_token` and `expected_video_id`; build the payload
(`command_id`, `nonce`, `issued_at`, `expires_at` = +30 s), serialise once, sign the exact bytes
with the installation key, send `{type: "command", pc_id, envelope}`.

Relay routing order (`dome_api/relay/router.py`; each failure is answered with
`result{origin: relay, state: failed, error}` and a `command_rejected` security event; a command
never gets an `error` frame):

1. frame schema (`controller_to_relay`), after the per-socket frame budget (600/min, burst 120);
2. envelope `kid` equals the socket's kid; controller not revoked and `status == active`
   (`UNKNOWN_KEY` / `CONTROLLER_REVOKED` / `CONTROLLER_PLAN_DISABLED`);
3. duplicate check within the account: identical bytes → re-emit the previous result or current
   ack; different bytes under the same id → `COMMAND_ID_REUSED` (cloud-api `DECISIONS.md` #6, #25);
4. `verify_and_parse_command` with a key-record resolver: signature over the exact payload bytes,
   strict parse once (≤ 16 KiB, depth ≤ 8, no duplicate keys, no NaN/Infinity, no unsafe integers),
   schema, `controller_id`/`account_id` bound to the key record (`CONTROLLER_MISMATCH` /
   `ACCOUNT_MISMATCH`), time window with 5 s skew, action known to the registry;
5. `payload.target_pc_id == frame.pc_id`; PC exists in the account, not deleted, `enabled`
   (`PC_PLAN_DISABLED`);
6. grant (controller, pc) exists and includes the action's capability (`GRANT_MISSING`);
7. per-controller token bucket: `manual_command_rate_limit` (120/min, burst 30) or, for actions
   that declare `coalesce`, `coalescable_command_rate_limit` (360/min, burst 60);
8. agent online, else `PC_OFFLINE` — commands are **never queued** for an offline PC;
9. in-flight commands for the PC (`created|accepted|executing|awaiting_confirmation`) below 16,
   else `QUEUE_FULL`;
10. insert the `commands` row (`created`, deadline per `rules.in_flight`) and forward
    `{type: "command", envelope, relay: {received_at, connection_id}}` unchanged.

Agent authorization order (`dome_agent/authz.py`, applied to every inbound command):

1. frame schema, envelope verified and parsed through the same shared library with a resolver that
   returns a key record **only** for a locally approved, unrevoked controller present in the latest
   snapshot (`UNKNOWN_KEY` / `CONTROLLER_REVOKED`; library enforces `CONTROLLER_MISMATCH` /
   `ACCOUNT_MISMATCH`);
2. `target_pc_id` equals the stored identity (`TARGET_PC_MISMATCH`); mismatches are not journaled,
   are logged locally, and three within 60 s close the socket (`rules.mismatch_handling`); a command
   before the connection's first snapshot → `PC_RECONNECTING`;
3. `remote_enabled` (local switch; re-checked right before execution) → `PC_REMOTE_DISABLED`;
   `pc_enabled` → `PC_PLAN_DISABLED`; controller `status` → `CONTROLLER_PLAN_DISABLED`;
4. capability in local ∩ snapshot → `GRANT_MISSING`;
5. journal: same id + same `command_digest` (SHA-256 of the exact signed bytes) → re-send the stored
   result (or current ack); same id + different digest → `COMMAND_ID_REUSED`;
6. availability conditions (`windows`, `extension_connected`, `session_unlocked`,
   `session_media_allowed`) → `PLATFORM_UNSUPPORTED` / `EXTENSION_DISCONNECTED` /
   `PC_SESSION_LOCKED` / `PC_SESSION_LOCKED_MEDIA_ONLY`;
7. target resolution: browser instance and tab must exist with `script_attached`
   (`TARGET_GONE` / `TAB_NOT_CONTROLLABLE`), `tab_token` must match the current attachment and
   `expected_video_id` the current video (`TARGET_CHANGED`); apps: `app_id` approved
   (`APP_NOT_APPROVED`);
8. routine steps (`origin.kind == routine`): action `routine_allowed` and a verified Pro entitlement
   assertion, else `ENTITLEMENT_REQUIRED`;
9. disruptive actions: issue a challenge and answer `confirmation_required` instead of queuing
   (see 4.7);
10. results are validated against the action's result schema before emission.

Then `ack{accepted}`, enqueue. The executor (`dome_agent/queue.py`) runs one worker per PC with
depth 16; a coalescing group `(coalesce key, canonical target)` cancels earlier queued commands with
`result{canceled, COMMAND_SUPERSEDED}`; each command gets `ack{executing}`, a durable `executing`
journal row before the OS/browser call, `asyncio.wait_for(timeout_ms)`, and never a retry of a
non-idempotent action. Grant and plan state are re-checked at every later transition
(pc-agent `DECISIONS.md` #16).

Extension side (`browser-extension/src`): the agent maps `youtube.next` to `bridge_request{op:
next, args{tab_id, tab_token, expected_video_id}, timeout_ms}` over the native host; the worker
checks the tab still exists on YouTube and the token matches, the content script guards
`expected_video_id`, clicks `.ytp-next-button` (must exist and not be `aria-disabled`), and
succeeds only when a new `video_id` is observed before the deadline, returning
`youtube_state_result{tab, previous_video_id}`. A click without an observed transition is
`OUTCOME_UNKNOWN`, never retried (browser-extension `DECISIONS.md` D4). The agent re-checks the
transition before reporting success.

Evidence: unit-tested in every component; integration-tested end to end with the real agent
process and a fake extension on Linux (`tests/test_e2e_youtube.py`). Real Chrome/Edge DOM, native
messaging handshake and Windows background-focus behaviour: not yet verified.

### 4.7 A confirmed command: `power.sleep` (ADR-0001 D7)

1. Phone signs `power.sleep{countdown_seconds: 10}` with a 90 s lifetime (default for disruptive
   actions) and sends it. Relay routes as in 4.6 and extends the deadline by the 60 s challenge
   lifetime.
2. Agent authorizes through step 8, then issues a challenge: `dumps_compact({challenge_id,
   command_id, controller_id, pc_id, action, params, target, target_state_digest, issued_at,
   expires_at (+60 s), display{pc_name, action_label, detail}})`, stores the **exact string** with
   the command's envelope kid, journals the command as `awaiting_confirmation` and answers
   `confirmation_required{command_id, challenge_text}`. `target_state_digest` is SHA-256 of the
   observable target state (window title + handle for `app.close`; pending-power state for `power.*`).
3. Relay validates a strictly parsed **copy** against the `challenge` schema, forwards the original
   string untouched, sets `commands.state = awaiting_confirmation` and writes `pcs.last_power_request`.
4. Phone validates `challenge_text`, renders the primary line from `action`/`params`/`target` with
   its own labels and the PC name from its own inventory (`display.*` is never used for the primary
   line; `detail` is secondary text "Reported by the PC"), offers Approve only when the challenge
   matches a command this phone sent, and on Approve signs
   `confirmation{command_id, challenge_id, challenge_digest = SHA-256(challenge_text), decision:
   approve, …}` with the same key.
5. Relay accepts `confirmation` only from the socket bound to the command's controller and forwards
   it. Rejections at the relay are `error{code, ref_pc_id}` frames because the PC still owns the
   command (cloud-api `CONTRACT_ISSUES.md` #3).
6. Agent verifies the confirmation with the same resolver; the envelope kid must equal the kid
   pinned on the challenge; the challenge must exist, be unconsumed and unexpired; `command_id`,
   `controller_id` and `challenge_digest` must match; `target_state_digest` is recomputed
   (`TARGET_CHANGED` on drift); then in one SQLite transaction the challenge is consumed and the
   command moves to `accepted`. An unverifiable confirmation (unknown kid, bad signature) gets an
   `error` frame and leaves the challenge pending (pc-agent `DECISIONS.md` #8). `decline` →
   `canceled/CONFIRMATION_DECLINED`; expiry → `expired/CONFIRMATION_EXPIRED`.
7. Execution: `ack{executing}`, the countdown is visible as `pending_power_action` in `state`
   frames and can be canceled by `power.cancel`, a relay `cancel`, local disable or agent shutdown
   (`canceled/POWER_CANCELED`); when the OS call is issued the result reports what Windows
   accepted (`power_result{accepted, countdown_seconds, fires_at}`) or `POWER_DENIED`/`OS_ERROR`.
   `SetSuspendState` blocks until resume, so sleep is reported accepted when it has not failed
   within 2 s. Completion of sleep/restart/shutdown is not observable and is never claimed.

Evidence: unit-tested (pc-agent `tests/test_confirmations.py`, `test_executor.py`; mobile-app
`confirmations.test.ts`, `ConfirmationModal`); integration-tested with the fake platform
(`tests/test_e2e_reliability.py`: challenge → approve → `fires_at`, cancel, decline). Real power
transitions on Windows: not yet verified and must be tested on a disposable machine.

### 4.8 Revocation paths

| Trigger | What happens |
| --- | --- |
| Phone: `DELETE /v1/controllers/{id}` | Relay revokes the controller's grants, sends `revoked{reason}` to every socket bound to it and closes them (4003), sends a best-effort `cancel` to affected PCs for in-flight commands, pushes fresh `grants_snapshot`s. The PC marks the controller revoked on applying the snapshot and cancels its queued/awaiting commands. |
| Phone: `DELETE /v1/grants/{id}` | Same for one (controller, PC) pair. |
| Phone: `DELETE /v1/pcs/{id}` (unlink) | Credentials and tokens revoked, grants revoked, agent receives `revoked{pc_unlinked}` and the socket closes; the agent stops reconnecting, discards its credential, keeps local grants for inspection and shows a re-link prompt. Subscribed phones get `pc_status{offline, enabled: false}`. |
| Phone: `POST /v1/auth/logout` or `DELETE /v1/account/sessions/{id}` | Session revoked; controller sockets of that session closed (4008 → the PWA shows sign-in). |
| PC tray/CLI: revoke a controller | Local grant revoked at once (commands from that kid fail `CONTROLLER_REVOKED` immediately); `revoke_controller{controller_id, kid, reason}` sent to the relay, which revokes the grant exactly as the REST path and answers with a fresh snapshot; if the socket is down the revocation is journaled in `pending_revocations` and re-sent after every snapshot until acknowledged. |
| PC tray: Disable remote control | Local flag; every command fails `PC_REMOTE_DISABLED`, armed countdowns are canceled; no remote frame can undo it. |
| Reconnect | A reconnecting PC applies the new `grants_snapshot` before processing any command, so revocations made while it was offline take effect before the first command. |

Plan downgrades are **not** revocations: `pc_enabled: false` or `status: plan_disabled` keep local
grants, are refused by relay and agent with `PC_PLAN_DISABLED` / `CONTROLLER_PLAN_DISABLED`, and
revocation and emergency stop keep working (`rules.plan_state`). Today the plan-disabled selection
after a downgrade is automatic (`downgrade_policy.default_selection: most_recently_seen`); the
customer-facing selection UI is Phase C.

Evidence: integration-tested (cloud-api `tests/test_relay_lifecycle.py`, `tests/test_security_fixes.py`;
`tests/test_e2e_security.py`: REST revocation mid-session, local disable/enable); unit-tested
(pc-agent `tests/test_store.py`, `test_agent_e2e.py`).

### 4.9 Disconnects, deadlines and truthful outcomes

- Agent socket drops after `ack{executing}` was seen → relay emits
  `result{origin: relay, state: outcome_unknown}`; before that → `result{origin: relay, failed,
  PC_OFFLINE}`. A sweeper (every 5 s) applies the `rules.in_flight` deadline
  `max(expires_at, received_at + timeout_ms) [+ 60 s for challenge actions] + 10 s`.
- Agent crash between the OS call and the result: the journal row was set `executing` before the
  call, so start-up rewrites it to `outcome_unknown`; nothing is re-executed; the journaled result
  is re-sent once after the next snapshot and the relay forwards it once as a correction
  (`rules.late_results`); the PWA replaces the earlier outcome.
- Agent restart or relay restart: queued commands fail `PC_OFFLINE` in the journal; the relay's
  start-up sweep marks in-flight rows `outcome_unknown`/`expired`; nothing is replayed.
- Lost connectivity is never presented as a completed power action. The PWA says "requested …;
  DoMe cannot tell whether it ran" unless it saw an `executing` ack or an agent result
  (mobile-app `DECISIONS.md` #14).

Evidence: integration-tested (`tests/test_e2e_reliability.py`: kill the agent mid-execution,
restart, exactly one recorded effect; offline PC never queues; cloud-api sweeper and disconnect tests).

### 4.10 Entitlements (ADR-0001 D8)

Plan state lives on `accounts.plan` and is read only through `dome_api/plans.py` from
`shared/protocol/plans.json`. For Pro the relay issues a compact JWS (`alg: EdDSA`, `typ:
dome-entitlement+jwt`, 1 h, claims per `entitlement.schema.json`, bound to `sub = account_id` and
`pc = pc_id`) on `POST /v1/agent/entitlement` and inside `grants_snapshot`; the public key is served
at `/.well-known/dome-jwks.json`. The agent verifies with joserfc, refreshes on connect and at 80 %
of lifetime, persists only the claims, and honours the last verified Pro assertion for at most 72 h
after expiry **only** when the refresh fails with a network error or 5xx; `200 {assertion: null}`
means Free immediately. Entitlement never grants a capability and a grant never grants
entitlement. Since Phase C is not built, `plan` changes only through SQL in tests and
`GET /v1/plans` reports `billing_enabled: false`.

Evidence: integration-tested (cloud-api `tests/test_entitlement_and_misc.py`: assertion verifies
against the JWKS; `tests/test_e2e_security.py`: client state cannot unlock Pro); unit-tested
(pc-agent `tests/test_entitlement.py`: refresh, grace, null).

## 5. Hosting constraints

ADR-0001 D1/D2: one modular process, PostgreSQL as the only datastore, Fly.io as the single-region
target with Render/Railway as equivalents. Nothing in the code is Fly-specific, and `deploy/`
contains only a placeholder README, so the following are the constraints the code imposes on any
host, not a description of a running deployment (**not yet verified on any hosting provider**):

| Constraint | Source |
| --- | --- |
| Persistent WebSockets must be supported; the agent pings every 25 s and the PWA re-subscribes on resume, so proxy idle timeouts above ~30 s are tolerated. | `dome_agent/relay_client.py`, `mobile-app/src/lib/relay.ts` |
| Exactly **one** API/relay process per deployment. The connection manager, controller `state` cache, rate limiters and frame budgets are in-memory; a second replica would double every budget and could not route to agents connected to the other replica. Scaling out requires the documented Redis pub/sub seam (ADR-0001 D1, reversible). | `dome_api/relay/manager.py`, `security/ratelimit.py`, cloud-api `KNOWN_ISSUES.md` #2 |
| Global socket cap `DOME_RELAY_MAX_CONNECTIONS` (default 5000); upgrades beyond it get HTTP 503. Sockets that have not sent `hello` within 10 s are dropped and count toward the cap until then. | `dome_api/settings.py`, `relay/manager.py` |
| Frames above 64 KiB close the socket (1009); payloads above 16 KiB are rejected. | `version.json → limits` |
| Deploys must drain: on process exit every in-flight command is terminated truthfully (`outcome_unknown` if executing, else `PC_OFFLINE`/`expired`) and agents reconnect with backoff 1–60 s. Fly's `kill_timeout` should cover the sweep. | `dome_api/relay/lifecycle.py`, `main.py` lifespan |
| `DOME_TRUSTED_PROXIES` **must** name the ingress proxy's source range in staging/production (`*` is refused, the process refuses to start without a value); otherwise every per-IP limit and `ip_hash` is spoofable. uvicorn is started with its own proxy handling disabled. On Fly the range must be checked with `fly ssh console` + `ss -tn` before trusting it. | cloud-api `README.md` "Client addresses behind a proxy", `security/proxy.py` |
| Production requires https origins, a real `DOME_SESSION_SECRET`, a configured OIDC issuer with the redirect URI registered, and the Ed25519 entitlement key provisioned from a secret store; HSTS is enabled. | `dome_api/settings.py` |
| `alembic upgrade head` runs before serving and the lifespan refuses to start on a schema that is not at head. | `cloud-api/README.md` |
| The PWA is served from the same origin as the API (`/`, `/v1`, `/ws`, `/.well-known`); no CORS configuration exists. CSP is `script-src 'self'` with no `unsafe-eval`, which is why both the PWA and the extension ship precompiled schema validators. | `dome_api/static.py`, `security/headers.py`, mobile-app `DECISIONS.md` #1 |
| Managed PostgreSQL with encryption at rest and backups is an operator requirement; the application does not encrypt rows itself (secrets are hashed, not encrypted). Backup/restore and rollback procedures are not yet written or rehearsed. | spec §15 |

Operational targets in the spec (median button-to-observed-result < 500 ms, p95 < 1.5 s, bounded
idle memory) are **targets to measure, not achieved claims**: `tests/test_load_smoke.py` prints
throughput, latency and RSS as numbers for 40 simulated agents and controllers but no results have
been recorded in this build, so DoMe is **not load-tested**.

## 6. Seams left for later phases

- Transport: the agent's relay client and the PWA's socket client are the only places that know the
  URL scheme; a private-network mode would be a second transport, not a replacement for onboarding.
- Action registry: layouts, routines and AI proposals must all produce registry actions; the PC
  already refuses routine steps that are not `routine_allowed` and checks the entitlement assertion
  (`command.origin`).
- Billing: `billing/` tables and `DOME_STRIPE_*` settings are declared; endpoints are absent.
- Analytics: `activation_events` table exists; nothing emits to it yet.
- End-to-end payload encryption, LAN/offline mode, Wake-on-LAN, native apps, screen viewing,
  trackpad/keyboard: not designed in V1 (ADR-0001 D10).

## 7. Evidence summary for this document

| Claim group | Evidence tag |
| --- | --- |
| Contract parsing/signing/derivations identical across Python and TypeScript | unit-tested (`shared/python` 128 tests, `shared/ts` 82 tests, fixtures verified in both directions) |
| Relay REST and routing behaviour, isolation, revocation, deadlines | integration-tested (cloud-api, 76 tests against PostgreSQL 16 + `tools/dev-idp` + uvicorn in-process) |
| Agent authorization, executor, confirmations, bridge framing, relay client | unit-tested on Linux with explicit fakes (pc-agent, 194 tests) |
| Extension worker, player adapter, frames | unit-tested in jsdom with a `chrome` stub (90 tests); built bundle smoke-tested without `eval` |
| PWA libraries, stores, components | unit-tested in jsdom (205 tests) |
| Phone-sign → relay → real agent process → fake extension, including security and reliability scenarios | integration-tested (Linux, fake platform, fake extension; `tests/`) — see `docs/ACCEPTANCE.md` once written for the run record |
| Any Windows API path, DPAPI, tray, native host registration, real YouTube DOM, iPhone Safari key persistence/camera/resume, hosting provider behaviour, load | not yet verified |
