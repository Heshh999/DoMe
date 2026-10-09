# DoMe security: threat model, pairing and revocation design, boundaries

Status: pre-release engineering build. This document connects each threat in
`docs/spec/MASTER_PROMPT.md` §15 to the control that exists in the code and to where that control is
tested, using the evidence tags of §17. Nothing in DoMe has been Windows-device-tested,
iPhone-tested, load-tested beyond a small loopback smoke, or reviewed by an external party. No security certification exists or is
claimed. Where a control is designed but not implemented, this document says so.

Companion documents: `docs/ARCHITECTURE.md` (trust and data boundaries, flows),
`docs/PROTOCOL.md` (the wire contract), `docs/adr/0001-foundational-decisions.md`,
`cloud-api/KNOWN_ISSUES.md`, `pc-agent/KNOWN_ISSUES.md`, `browser-extension/KNOWN_ISSUES.md`,
`mobile-app/KNOWN_ISSUES.md` and each component's `CONTRACT_ISSUES.md`.

## 1. The V1 security boundary in one paragraph

Transport is TLS (HTTPS/WSS). The relay is a **trusted** service: it can read everything it routes
(see "What is not protected"). What the relay cannot do is authorise control: every command and
confirmation is signed by a controller key that lives non-extractably in the phone's browser and
that the customer approved **on the PC** during pairing; the PC verifies the signature against its
own local copy of that key, checks the grant it stored locally, and refuses anything the relay
could have invented. Account authentication is delegated to an OpenID Connect issuer; DoMe never
sees a password. The PC never listens on the network; it opens one outbound connection. A local
switch on the PC disables remote control and no remote message can undo it. This is not
end-to-end encryption and is not described as such anywhere in the product.

## 2. Threat model

Columns: threat → control implemented in this repository (where) → evidence tag and test location,
or "not yet verified". Multiple controls are listed where they stack.

| # | Threat | Implemented control | Where tested |
| --- | --- | --- | --- |
| T1 | **Account takeover** (stolen password, phished session, session fixation, CSRF) | Authentication delegated to an OIDC issuer with Authorization Code + PKCE; `state`/`nonce`/verifier kept server-side in `auth_flows`; ID token validated (issuer, audience, nonce, expiry, signature) — `dome_api/auth/oidc.py`. Server-side sessions: 32 random bytes, only SHA-256 stored, cookie `HttpOnly; SameSite=Lax; Path=/`, `Secure` on https origins, 30-day sliding idle and 90-day absolute expiry, listed and revocable at `GET/DELETE /v1/account/sessions` — `auth/sessions.py`. Every state-changing request needs `X-DoMe-CSRF` equal to the session's token **and** an exact `Origin` match — `auth/deps.py`. Login starts limited to 60/min per IP. A valid account session still cannot control, observe or interfere with a PC: **authentication and pairing are separate** — a session can only pair a *new* controller key through a code displayed on the PC and approved on the PC (§3), and a controller socket is bound to a paired phone only after a signed `hello_proof` shows the caller holds that phone's key (a bare `kid`, which `GET /v1/controllers` reveals to the whole account, is not enough to subscribe to PC state, receive the phone's results/challenges or cancel its commands — `relay/controller_ws.py`, `rules.controller_socket_identity`). Account recovery therefore never re-enables a lost phone. | integration-tested: cloud-api `tests/test_auth_and_sessions.py` (login via `tools/dev-idp`, CSRF, Origin, logout closes sockets, session revocation), `tests/test_hello_proof.py` (same-account session with a paired phone's kid stays unbound; forged, replayed and wrong-account proofs → `UNKNOWN_KEY` + 4003 + security event); `tests/test_e2e_security.py` (a second account cannot act on the first's PC). Real issuer (Auth0/Keycloak) configuration: not yet verified. |
| T2 | **Malicious websites** (on the phone or the PC browser) | Phone: PWA served from the API origin with CSP `default-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'none'` (no `unsafe-eval`, which is why validators are precompiled), `Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff`, HSTS in production — `dome_api/security/headers.py`. Cookies are `HttpOnly` and `SameSite=Lax`; WebSocket upgrades check exact `Origin`; cross-origin pages cannot read the CSRF token (same-origin `GET /v1/session`) and cannot open a bound controller socket. The controller key is non-extractable (`extractable: false`) so a script injection could sign while the page is open but could not copy the key — stated honestly below as a residual risk. PC: the extension has `host_permissions` for `https://www.youtube.com/*` only, no `tabs`/`history`/`cookies`/`debugger`/`<all_urls>`, no `externally_connectable`; the worker accepts messages only from its own content script in a top frame on youtube.com; page strings are sanitized and treated as display data — `browser-extension/src/background/service.ts`, `src/shared/sanitize.ts`, `public/manifest.json`. Pairing codes travel in a URL fragment only, are scrubbed from the address bar before React renders, and never enter storage — `mobile-app/src/app/navigation.ts`. | unit-tested: browser-extension `test/manifest.test.ts`, `test/background.test.ts` (sender checks, sanitization); mobile-app `test/components` (SignInRedirect fragment scrubbing), `api.test.ts`; integration-tested: cloud-api Origin/CSRF refusals, 403 on WebSocket upgrade with a wrong Origin. Real-browser CSP behaviour: not yet verified. |
| T3 | **Malicious or compromised browser extension on the PC** | The native host manifest lists only the production extension id (empty in source until the store listing exists) plus an explicit `DOME_AGENT_DEV_EXTENSION_ID` — `dome_agent/bridge/manifest.py`. The bridge IPC endpoint is scoped to the same Windows user **and** session (named pipe named from the process token SID + session id, DACL for that SID, `PIPE_REJECT_REMOTE_CLIENTS`, client SID/session verified per connection; Unix socket 0600 with peer uid check elsewhere) — `dome_agent/bridge/ipc.py`. Every bridge frame is validated in both directions against `bridge.schema.json`; the extension can only answer ops the agent asked for and emit state events — it cannot originate commands, change settings, or reach the relay. Titles and video ids from the extension are untrusted display data; the agent never derives an action from them. | unit-tested: pc-agent `tests/test_bridge.py` (framing, oversize, schema rejection, a process-level native-host test), browser-extension `test/frames.test.ts`. Windows pipe DACL and SID/session verification: not yet verified (manual checklist in `pc-agent/README.md`). |
| T4 | **Stolen or lost phone** | What is on the phone: a session cookie and the installation's key. The owner revokes from any signed-in browser: `DELETE /v1/controllers/{id}` (closes the controller's sockets with `revoked` + 4003, revokes grants, pushes new snapshots) and `DELETE /v1/account/sessions/{id}` / logout — `dome_api/routes/controllers.py`, `routes/account.py`. The PC owner can also revoke locally from the tray/CLI (`revoke_controller`) without any cloud access, and "Disable remote control" stops everything at once. Disruptive actions still require an on-phone confirmation signed by the same key, and `app.close`/`power.*` never run without it, so a thief with an unlocked phone has the same powers as the owner until revoked — no more. Device lock on the phone is outside DoMe's control. | integration-tested: cloud-api `tests/test_relay_lifecycle.py` (revocation mid-flight: cancel to PC, `revoked` + 4003 to phone, re-snapshot), `tests/test_e2e_security.py` (REST revocation stops a live controller; local revocation). |
| T5 | **Controller-key theft / cloning** | Key generated with WebCrypto `extractable: false` and stored as a `CryptoKey` in IndexedDB; the public JWK and `kid` are derived on demand — `mobile-app/src/lib/controllerKey.ts`. The browser does not let a page export the private key. A copied IndexedDB cannot be imported elsewhere (non-extractable keys are not serialisable). Clearing storage loses the key and requires re-pairing on the PC; there is no recovery shortcut. The relay binds a socket to `(session account, kid)` only after verifying a `hello_proof` signed by that key: a key alone, without a session for the same account, cannot open a bound socket, and a session without the key can neither bind a socket nor sign. Verifiers bind `controller_id`/`account_id` in the payload to the key record (`CONTROLLER_MISMATCH`/`ACCOUNT_MISMATCH`), so a key can never sign its way into another controller's grant — `shared/python/dome_protocol/commands.py`, `shared/ts/src/commands.ts`. | unit-tested: mobile-app `test/controllerKey.test.ts` (fake-indexeddb, non-extractable); shared libraries (mismatch cases); integration-tested: cloud-api `tests/test_relay_routing.py` (forged `controller_id`/`account_id`, kid mismatch → 4003, bad signature). iOS Safari key persistence across restarts: not yet verified. |
| T6 | **Pairing theft** (shoulder-surfed/intercepted code, replayed code, relay substituting a key, administrator enrolling a controller) | Code: 20 Crockford symbols (100 bits) generated **on the PC**, 5-minute single-use session, one open session per PC; the backend stores only `SHA-256("dome-pair-handle-v1\|" + code)` — `dome_agent/pairing.py`, `dome_api/routes/pairing.py`. A claim must come from a session of the **same account** (any other account sees `PAIRING_CODE_INVALID`), 5 attempts per 15 minutes per account and per IP, each failure a `pairing_failed` security event. The 6-digit verification code is `HMAC-SHA256(code, "dome-pair-verify-v1\|" + pairing_id + "\|" + pc_id + "\|" + kid)`: because the backend never has `code`, a backend that substitutes a controller key cannot make the PC's and the phone's digits agree; the PC recomputes `kid` from the JWK it received. Approval happens **on the PC**; the backend creates rows only after `pairing_decision{approve}`. A listed controller the PC never approved locally is ignored and logged — `dome_agent/store.py::apply_snapshot`. Pairing material is redacted from logs (`code`, `*_code`, `code_hash`) and never in query strings or Referer (fragment). | unit-tested: handle and verification code against shared fixtures in both languages (`shared/python/tests`, `shared/ts/test`, mobile-app `pairing.test.ts`, pc-agent `test_link_pairing.py`); integration-tested: cloud-api `tests/test_pairing.py` (wrong account, expired, reuse, rate limit), `tests/test_e2e_security.py` (expired/used code rejected, no grant before the PC decides, offline claim delivered on reconnect). Real QR scanning: not yet verified. |
| T7 | **Cross-account object access** (IDOR on REST, subscribing to or commanding another customer's PC by changing an id) | Every account-owned query is scoped by `account_id` with no unscoped helper — `dome_api/routes/*`; `commands` lookups are account-scoped too (`DECISIONS.md` #25). WebSocket subscriptions are scoped exactly like REST: `subscribe` requires a PC of the session's account on which **this bound controller** holds a live grant (`GRANT_MISSING` with `ref_pc_id`); commands require `payload.target_pc_id == frame.pc_id`, PC in the account, grant with the capability. The agent independently refuses `account_id`/`target_pc_id` that differ from its link-time identity (`ACCOUNT_MISMATCH`/`TARGET_PC_MISMATCH`), does not journal them, and closes the socket after three in 60 s. Device and controller limits are enforced in one transaction with `SELECT … FOR UPDATE` on the account row. | integration-tested: cloud-api `tests/test_security_fixes.py`, `tests/test_relay_routing.py` (cross-account subscribe/command/REST), `tests/test_agent_link.py`, `tests/test_pairing.py` (transactional limits); `tests/test_e2e_security.py` scenario 3 (account B cannot list, subscribe to, pair with or command account A's PC). |
| T8 | **Replay and duplicate execution** | Payloads carry `command_id`, `nonce`, `issued_at`, `expires_at` (default 30 s, max 300 s, 5 s skew). The relay checks the window and keeps lifecycle rows; the PC keeps a durable journal keyed by `command_id` with `command_digest = SHA-256(exact signed bytes)`: identical bytes re-emit the stored result, different bytes → `COMMAND_ID_REUSED` — `dome_agent/authz.py`, `store.py`. Confirmations are single-use: the challenge is consumed atomically in SQLite before execution; a confirmation for a consumed or expired challenge fails; `challenge_digest` must equal SHA-256 of the exact `challenge_text`. Non-idempotent actions (`youtube.next`, seek, `app.close`, `power.*`) are never auto-retried; an ambiguous outcome is reported `outcome_unknown` with a warning rather than repeated. `target_state_digest` and `tab_token`/`expected_video_id` make a replayed confirmation or command fail when the target changed. | integration-tested: cloud-api `tests/test_relay_routing.py` (identical duplicate re-emits, different bytes rejected), `tests/test_e2e_security.py` scenario 6 (replay, reuse, expiry, confirmation digest mismatch, target changed), `tests/test_e2e_reliability.py` (kill mid-execution → exactly one effect); unit-tested: pc-agent `test_store.py`, `test_confirmations.py`. |
| T9 | **Relay compromise** (malicious operator, breached host, rogue code in cloud-api) | Cannot forge commands or confirmations (signature verified on the PC against a locally approved key; `challenge_text` hashed verbatim by the phone). Cannot enroll a controller or widen a grant (`grants_snapshot` is an intersection list carrying no keys; local store written only at local approval). Cannot make pairing displays agree on a substituted key (HMAC keyed by the code it never sees). Cannot reach a PC that has remote control disabled locally. Can: read routed payloads and state, drop or delay traffic, revoke/disable devices, see the account/device inventory. Secrets at rest are hashes (session ids, device codes, credentials, tokens, pairing codes). | integration-tested for the cryptographic properties (relay-side tests prove routing cannot bypass the PC checks; pc-agent tests prove a snapshot cannot add a controller: `test_store.py`, `test_agent_e2e.py`). The residual visibility is a design fact (ADR-0001 D10), not something a test can remove. |
| T10 | **Abuse of powerful actions** (close an app with unsaved work, power off, launch arbitrary programs, run commands while the session is locked, flood the PC) | Capabilities are granted per controller per PC at pairing (`status`, `media`, `volume`, `apps`, `lock`, `power`) and checked by relay and PC. `app.close` and `power.*` are `risk: disruptive` with a signed two-message confirmation bound to command, controller, PC, action, exact params, target and a digest of the target state, 60 s expiry, consumed once; a caller-supplied `confirm=true` does not exist. Routines (future) exclude disruptive actions by registry (`routine_allowed: false`). Apps: only locally approved `app_id`s; absolute, existing `.exe` outside temp folders, SHA-256 pinned, re-approval when the binary changes; `Popen([exe_path])` with **no shell and no arguments**; `WM_CLOSE` only, never `TerminateProcess`; no path, argument, script or shell text crosses the wire (schema `additionalProperties: false` everywhere). Locked session: non-media actions refused (`PC_SESSION_LOCKED`), media only with the local "media while locked" opt-in. Power: cancellable countdown (0–60 s), `bForceAppsClosed = FALSE`, no `EWX_FORCE`. Flooding: per-controller token buckets from `plans.json`, per-socket frame budget, per-PC in-flight depth 16, 64 KiB frames, 16 KiB payloads, security-event write caps. Emergency stop: local "Disable remote control". | unit-tested: pc-agent `test_approved_apps.py` (non-absolute, non-exe, temp dir, argument-like input rejected), `test_authz.py` (every rejection code in order), `test_confirmations.py`, `test_handlers.py`; integration-tested: cloud-api rate limit / queue depth / frame size / 503 cap, `tests/test_e2e_reliability.py` (confirmed power on the fake platform, cancel, decline). Real Windows behaviour of `SetSuspendState`, `InitiateSystemShutdownExW`, `SetForegroundWindow`, lock detection: not yet verified. |
| T11 | **Prompt injection (future AI) and injection through deterministic text** | AI is not built (Phase E). The only text path is the PWA's deterministic rule table (`mobile-app/src/lib/intents.ts`): normalised input matched against anchored rules; anything else is `unknown`; a rule never matches inside a sentence containing another verb; "ignore", "instructions", URLs and similar never parse; disruptive intents still go through the signed confirmation. Media titles, window titles and `challenge.display.detail` are rendered as plain text and never used to pick an action or label the primary confirmation line. When AI arrives it must emit registry actions that the paired controller still signs, and the PC's authorization path does not change. | unit-tested: mobile-app `test/intents.test.ts` (spec table, rejections, injection strings, multi-verb guard and its statelessness); `ConfirmationModal` tests (primary line from registry labels only). Provider-side controls: not applicable yet. |
| T12 | **Compromised updates / supply chain** | Not implemented: there is no installer, no signed update manifest, no Chrome Web Store listing and no CI in `deploy/`. What exists: PyInstaller specs (`pc-agent/packaging/`), pinned Python/Node dependencies via `uv.lock`/`pnpm-lock.yaml`, no remote code or remotely loaded configuration in the extension or the agent, `installer never sets DOME_AGENT_PLATFORM=fake`, native host registration under HKCU only (no elevation). Required before public release: Authenticode signing, a maintained updater with signature verification and downgrade rejection, store review. | not yet verified — nothing to test yet. |
| T13 | **Spoofed client address / abuse-limit bypass** (added during review) | `X-Forwarded-For` is honoured only when the TCP peer is in `DOME_TRUSTED_PROXIES` (explicit CIDRs; `*` refused; required in staging/production), resolving to the right-most untrusted hop; uvicorn's own proxy handling is disabled so `FORWARDED_ALLOW_IPS` cannot widen trust — `dome_api/security/proxy.py`. | unit-tested with simulated peers/headers (cloud-api `tests/test_security_fixes.py`). The actual Fly.io proxy source range: not yet verified. |
| T14 | **Resource exhaustion of the relay/database** (added during review) | Global socket cap (503), 10 s hello timeout, per-socket inbound frame budget (600/min, burst 120; close 4000 after `burst` refusals with one `controller_throttled` event), security-event rows per socket capped per minute, bounded per-PC in-flight count, sweeper for stale rows, strict parser limits before any database work. Open: agent sockets have no per-socket frame budget (`cloud-api/KNOWN_ISSUES.md` #1). | integration-tested: cloud-api relay tests for queue depth, frame size, cap and throttling. Sustained load: not load-tested. |
| T15 | **Half-open PC connection turning an executed action into "nothing ran"** (added by the cross-component review) | Once the relay has written a command to the PC's socket it terminates a lost connection or a passed deadline as `outcome_unknown`, never `failed/PC_OFFLINE` or `COMMAND_EXPIRED` — `relay/manager.py::_terminate_unknown`, `rules.terminal_result`/`in_flight`. The PC treats a socket write as unconfirmed delivery: on reconnect it re-sends every result finished after the last frame the previous connection received, and the relay forwards one correction per `outcome_unknown` — `dome_agent/agent.py::_resend_late_results`, `relay_client.py` (`previous_last_inbound_at`), `rules.late_results`. The phone's `OUTCOME_UNKNOWN` copy says "may or may not have run" and never "nothing ran". | integration-tested: cloud-api `tests/test_relay_lifecycle.py` (forwarded-but-unacked command → `outcome_unknown`; late result corrects it once); unit-tested: pc-agent `tests/test_agent_e2e.py::test_late_result_resent_after_reconnect` (queued command's `PC_OFFLINE` verdict re-sent after reconnect). |

## 3. Pairing design

Goal: enrol a controller key on a PC so that **neither** a password-only attacker **nor** a backend
administrator can do it, and so that a relay that substitutes a key is detected.

1. **Code generation on the PC.** `generate_pairing_code()` draws 20 symbols from the Crockford
   base32 alphabet (100 bits); display format `XXXXX-XXXXX-XXXXX-XXXXX`; manual entry is
   case-insensitive with `I/L → 1`, `O → 0`. The code lives in the running agent's memory and is
   handed to the local CLI only over the user-scoped control channel (`pc-agent/DECISIONS.md` #7);
   it is never written to disk or logs.
2. **Registration by hash.** `POST /v1/pairing/start {code_hash}` with the PC's bearer token;
   `code_hash = base64url(SHA-256("dome-pair-handle-v1|" + code))`; 5-minute expiry shared by the
   session, the request and the code; a new start expires the previous open session for that PC.
3. **Transfer out of band.** QR of `https://<origin>/pair#code=…` (fragment → never in Referer or
   server logs) or typing. The PWA reads the fragment once, replaces the history entry, keeps the
   code in component state only, and does not forward it through the sign-in redirect (the customer
   re-scans after signing in).
4. **Claim by the same account.** `POST /v1/pairing/claim {code_hash, public_jwk, display_name,
   requested_capabilities}` (session + CSRF). The relay looks the hash up **within the account**;
   wrong account, expired, already claimed or unknown are all `PAIRING_CODE_INVALID`. Limits: 5 per
   15 minutes per account and per IP; each failure is a security event. `kid` is derived from the
   JWK (RFC 7638); `max_controllers` is enforced transactionally for a new kid. 202 `claimed`.
5. **Request to the PC.** `pairing_request` (now, or after the next `grants_snapshot` if the PC is
   offline; every unexpired claimed session is re-delivered on each connect). The agent ignores a
   request whose `code_hash` is not its current session's and refuses one whose `kid` does not
   equal `kid_from_jwk(public_jwk)`.
6. **Verification code.** Both devices compute
   `HMAC-SHA256(key = code, msg = "dome-pair-verify-v1|" + pairing_id + "|" + pc_id + "|" + kid)`,
   take the first 8 bytes as a big-endian integer mod 10⁶, zero-padded. Normative vectors in
   `shared/protocol/fixtures/es256-*.json` (`412098`). The backend, lacking `code`, cannot produce a
   substitute key whose code matches the phone's display. The 6 digits are a comparison aid, not a
   credential; the credential is the key pair.
7. **Approval on the PC.** The PC shows the phone's name (untrusted text), the digits and the
   requested capabilities. Approve → local grant row (provisional id until the snapshot names the
   real `controller_id`) → `pairing_decision{approve, kid, granted_capabilities}`. Only then does the
   relay create `controllers`/`grants` rows (granted ∩ requested) and push a new snapshot. Decline
   or expiry → `declined`/`expired`; nothing is stored on the PC.
8. **Re-pairing.** A lost key (cleared storage, new phone) is a new pairing; there is no recovery
   that bypasses the PC. Account recovery at the identity provider does not touch grants.

What pairing does **not** defend against: someone who can read the PC's screen *and* is signed in to
the same account while the code is valid (they are the account owner or have the owner's session,
covered by T1); a compromised PC (it holds the key store).

## 4. Revocation design

Principle: the PC's local store is the only source of controller keys, and the backend's
`grants_snapshot` can only remove or narrow. Every path converges on the same two effects: the
relay stops routing for the controller/PC, and the PC refuses the kid locally.

| Path | Immediate effect | Synchronisation |
| --- | --- | --- |
| Account revokes a controller (`DELETE /v1/controllers/{id}`) | Grants revoked in the database **before** sockets are touched (commit first); `revoked{controller_revoked}` to every socket bound to that controller, close 4003; best-effort `cancel` to PCs for its in-flight commands (the PC's own result or the sweeper terminates them truthfully); fresh `grants_snapshot` to affected agents. | A PC that is offline receives the snapshot on its next connect before any command is processed; in-flight and queued commands from the revoked kid are canceled on apply. |
| Account revokes one grant (`DELETE /v1/grants/{id}`) | Same, scoped to one (controller, PC). | Same. |
| Account unlinks a PC (`DELETE /v1/pcs/{id}`) | Credentials, tokens and grants revoked; `revoked{pc_unlinked}` to the agent, socket closed; subscribers told `pc_status{offline, enabled: false}`. | The agent discards its credential, stops reconnecting, keeps local grants for inspection, shows a re-link prompt; a later `POST /v1/agent/token` with the old credential is 401. |
| Account signs out / revokes a session | Session row revoked; that session's controller sockets closed with 4008. | The PWA shows sign-in; the installation key is kept so signing back in needs no re-pairing. |
| PC owner revokes locally (tray/CLI `revoke`) | Local grant revoked at once; `revoke_controller{controller_id, kid, reason}` to the relay → same as the account path. | If offline, the revocation is journaled in `pending_revocations` and re-sent after every snapshot until the relay acknowledges by omitting the controller. |
| PC owner disables remote control | Every command refused with `PC_REMOTE_DISABLED` (checked at authorization and again right before execution); armed countdowns canceled. | Local flag; no remote frame can change it; survives restarts. |
| Plan downgrade | **Not a revocation.** `pc_enabled: false` / `status: plan_disabled` refuse routing and execution but keep local grants, so revocation and emergency stop keep working for every paired device. | Fresh snapshot on plan change. |

Reconnect rule: an agent processes **no** command until it has applied the connection's first
`grants_snapshot` (`PC_RECONNECTING` otherwise), so revocations made while it was offline always
precede the first command after reconnection. Capability narrowing takes effect before the side
effect (re-check at confirmation time, at execution precheck and on every snapshot) but does not
interrupt a command whose OS call is already under way (`pc-agent/KNOWN_ISSUES.md` #2).

## 5. Security-relevant controls by layer

- **Schemas and parsing.** Every trust-boundary input is validated against
  `shared/protocol/schemas/*.json` with `additionalProperties: false`; strict JSON parsing rejects
  oversize (16 KiB payload, 64 KiB frame, 4 KiB challenge), depth > 8, duplicate keys, NaN/Infinity,
  unsafe integers and prototype-pollution keys; Python and TypeScript share `strict-json-cases.json`
  and the signing fixtures so both accept and reject the same strings (unit-tested in both suites).
  Signatures are verified over the exact transmitted bytes and the payload is parsed once.
- **Security events.** `security_events(account_id, kind, severity, actor, subject_id, detail,
  ip_hash)`. Kinds written by this build: `account_created`, `logout`, `session_revoked`,
  `pc_linked`, `pc_link_denied`, `pc_credential_issued`, `pc_enabled`, `pc_unlinked`,
  `agent_connected`, `agent_disconnected`, `agent_token_refused`, `link_code_lookup_failed`,
  `pairing_started`, `pairing_claimed`, `pairing_declined`, `pairing_failed`,
  `pairing_rate_limited`, `controller_paired`, `controller_limit_reached`, `controller_revoked`,
  `grant_revoked`, `command_rejected`, `confirmation_rejected`, `subscribe_refused`,
  `relay_frame_rejected`, `controller_throttled`, `relay_events_throttled`. `detail` is redacted
  before storage; the list is exposed to the customer on Free at
  `GET /v1/account/security-events`. The agent keeps local security events for identity mismatches,
  unknown snapshot controllers, IPC identity mismatches and changed approved-app binaries.
- **Rate limits (in-memory, per process).** Link start 10/h per IP; failed `user_code` lookups
  10/15 min per account and per IP; pairing claims 5/15 min per account and per IP; login 60/min per
  IP; agent token 30/min per IP; command buckets from `plans.json`; controller frame budget
  600/min burst 120; security-event rows per socket 20/min.
- **Logging.** structlog JSON; redaction of `token`, `secret`, `code`, `device_code`,
  `pc_credential`, `cookie`, `authorization`, `payload`, `sig`, `title`, `challenge`, `email`,
  `code_hash`, `challenge_text` and `*_token/_secret/_code/_credential/_key`; URLs as route
  templates with `user_code` masked; no query strings. Agent and PWA logs follow the same rule
  (codes, ids, counts and durations only). Diagnostics bundles are customer-initiated and redacted.
- **Secrets at rest.** Hashes for session ids, device codes, PC credentials, access tokens and
  pairing codes; DPAPI (current user) for the PC key and credential on Windows; non-extractable
  WebCrypto key on the phone; the entitlement signing key is a PEM provisioned by the operator.
  Managed-database encryption at rest and backups are a hosting requirement, not implemented in code.

## 6. Operator boundaries

There is **no operator interface** in this build (Phase C). The statements below describe what a
person with production access (database, host, secrets) can and cannot do given the code as it is:

- Can: read every table (public keys, hashes, lifecycle rows, redacted security events), change
  `accounts.plan`, revoke sessions, controllers, grants and PCs by writing to the database, restart
  the process, read routed payloads and `state` frames from memory or by modifying the code.
- Cannot, without the customer's devices: sign a command (no private keys exist server-side), enrol
  a controller on a PC (the PC's local approval is required and the snapshot cannot add), satisfy a
  confirmation (the phone must sign it with the paired key), or override "Disable remote control".
- Must not, by policy to be enforced by the future operator interface: execute customer PC commands,
  bypass local approvals, or read unredacted diagnostics. Administrative changes need an audit trail
  (`operator_audit` table exists, nothing writes to it).
- Deployment prerequisites that only the founder can complete: production OIDC application and
  redirect URIs, `DOME_TRUSTED_PROXIES` for the real ingress, Ed25519 key provisioning, Stripe keys
  (Phase C), code-signing identity, store listings.

## 7. The local PC boundary

The agent runs as the logged-in user without elevation. It does not and cannot defend against that
user or against malware running as that user: whoever can write `state.sqlite3` can edit the
approved-app allowlist, grants and settings; whoever can call DPAPI as that user can read the PC
key and credential. The design goals at this boundary are narrower and are met by the code:
remote parties never send paths, arguments or shell text; the agent never runs with more privilege
than the user; the native host accepts only same-user, same-session clients; and the key store is
only ever written by local approval. A Session 0 service is deliberately not used (spec §10).

## 8. What is NOT protected in V1

- **Confidentiality from the relay.** Commands, confirmations, results and `state` frames
  (including YouTube titles, media titles, app window titles) are readable by the relay operator.
  There is no end-to-end payload encryption and the product must not be described as encrypted
  end to end or zero-knowledge.
- **A compromised browser origin or phone OS.** A script running on the API origin, or malware on
  the phone, can sign commands while the page is open even though it cannot extract the key.
- **A compromised PC.** A PC that is already compromised holds the key store, the credential and
  the allowlist; DoMe adds no protection there and relies on the user's own OS security.
- **The identity provider.** Account security is as strong as the configured issuer and its MFA.
  DoMe does not offer its own MFA.
- **Availability.** The relay is a single process; a restart or outage drops every socket and ends
  in-flight commands as `outcome_unknown` (corrected by the PC's re-sent results on reconnect). There is no replica, no broker, and no
  rate limiting of agent frames yet.
- **Proof of possession at link start** (`cloud-api/CONTRACT_ISSUES.md` #10): anyone who knows a
  PC's public JWK can start a link code for it; the server-side mitigation refuses to approve a
  code for a PC that is still linked, so the practical impact is that a victim could be shown a
  stray approval page. Cryptographic fix proposed for the next protocol MINOR.
- **Update integrity, code signing, store review, installer.** Not built.
- **Operator interface, audit log writes, account deletion job, data purge jobs, written retention
  table.** Not built.
- **Linux/macOS secret storage.** Outside Windows the PC key and credential are 0600 files with a
  logged warning (development only).
- **Product analytics / activation events.** Nothing is emitted; when added it must stay separate
  from security records and avoid command content.
- **Anything device-specific.** DPAPI, named-pipe DACLs, lock detection, power APIs, foreground
  rules, real YouTube DOM, iOS key persistence and camera access are implemented against the real
  APIs but have not been exercised on a device.

## 9. Hardening backlog (recorded, not fixed)

| Item | Source | Severity as assessed by review |
| --- | --- | --- |
| Per-socket frame budget for `/ws/agent` | `cloud-api/KNOWN_ISSUES.md` #1 | medium |
| Move rate limiters out of process memory before running >1 replica | `cloud-api/KNOWN_ISSUES.md` #2 | medium (only when scaling) |
| Proof of possession on `agent_link_start_request` | `cloud-api/CONTRACT_ISSUES.md` #10 | minor (mitigated) |
| `EdDSA` JWS alg name deprecated by RFC 9864 → switch to `Ed25519` in a MINOR bump | cloud-api/pc-agent `CONTRACT_ISSUES.md` | low (no security impact) |
| `error` frames for rejected confirmations carry no `command_id` (`ref_command_id` proposed) | `cloud-api/CONTRACT_ISSUES.md` #3 | low |
| Fixture `tab_token` is 24 characters, schema requires 22 | `mobile-app/CONTRACT_ISSUES.md` #1 | low (fixtures still valid as signing vectors) |
| `AbortSystemShutdownW` window is effectively nil with `dwTimeout = 0` | `pc-agent/KNOWN_ISSUES.md` #1 | product decision |
| Confirmation after a controller socket reconnect is unverified end to end | `mobile-app/KNOWN_ISSUES.md` #1 | low (challenge expires on the PC either way) |
| Stale subscriptions after unlink, unscoped-looking `command_id` probe cost, `user_code` in request logs | cloud-api review | fixed in this build (`DECISIONS.md` #24, #25, README) |

## 10. Development hygiene

- `tools/dev-idp` is a separate process used only by local development and tests; the backend has
  no non-OIDC login path and no production bypass.
- Test doubles are isolated: `dome_agent/testing/` is selected only by `DOME_AGENT_PLATFORM=fake`
  (the agent logs a warning and reports `platform: "development"`); cloud-api has none; the PWA and
  extension use fakes only inside their test directories.
- `.env.example` contains placeholders only; real values are never committed; rotating
  `DOME_SESSION_SECRET` signs everyone out; rotating the entitlement key is a redeploy (agents
  re-fetch the JWKS on every connect).
- The fixture key `shared/protocol/fixtures/test-controller-key.pem` is a test key only and is
  never accepted by a real pairing (it would need a PC's local approval like any other key).
