# ADR-0001 — Foundational decisions for DoMe

Status: accepted (2026-10-08). Reversible decisions are marked **[reversible]**.

## Context

DoMe is a public freemium product: a phone (iPhone-first PWA) controls the customer's own
Windows PC through a managed relay. Many independent customers, isolated accounts, no customer
port-forwarding or VPN setup. See the master prompt for full requirements.

## Decisions

### D1. Topology

`Phone PWA → HTTPS/WSS cloud backend ("relay") ← outbound WSS Windows user agent → Windows
actions / Native Messaging → browser extension → YouTube tab`.

- The PC never listens on the internet. It opens one authenticated outbound WebSocket.
- One modular FastAPI process owns REST, both WebSocket endpoints, and the in-process
  connection manager. PostgreSQL is the only datastore. No broker until horizontal scaling
  is demonstrated. **[reversible: introduce Redis pub/sub when >1 relay replica is needed]**

### D2. Hosting target: Fly.io (single region to start) **[reversible]**

Chosen because it runs long-lived WebSocket connections on ordinary VMs and provides managed
Postgres. Documented constraints (see `docs/DEPLOYMENT.md`): per-VM connection limits are set
by our own `DOME_RELAY_MAX_CONNECTIONS`; Fly proxy idle timeout requires application-level
pings (agent pings every 25 s); deploys use `kill_timeout` draining so agents reconnect with
backoff. Render/Railway are equivalent alternatives; nothing in the code is Fly-specific.

### D3. Authentication: OpenID Connect Authorization Code + PKCE via Authlib

- The backend is an OIDC relying party (`authlib`), configurable by issuer URL, client ID and
  client secret. First-class documented providers: **Auth0** and **Keycloak** (both standard
  OIDC; any compliant issuer works). The product never sees a password.
- Web sessions: server-side session rows in Postgres, `HttpOnly; Secure; SameSite=Lax`
  cookie, 30-day idle expiry, sliding. No bearer tokens in URLs or `localStorage`.
- CSRF: every state-changing request must carry `X-DoMe-CSRF` equal to the session's CSRF
  token (readable by the PWA from `GET /v1/session`) **and** an exact `Origin` match against
  the configured allowed origins. WebSocket upgrades are checked for exact `Origin` too.
- The PWA is served from the same origin as the API (`/` static, `/v1` REST, `/ws` sockets).
  Vite dev server proxies `/v1` and `/ws`.
- Development/test identity: `tools/dev-idp` is a tiny standalone OIDC issuer process used
  by local development and the integration tests. It is **not** part of the backend service
  and the backend contains no non-OIDC login path. Production configures a real issuer.

### D4. PC ↔ account linking: device-authorization-shaped flow (RFC 8628 shape)

The agent never handles the account password. It generates an ES256 identity key (private key
protected by Windows DPAPI), asks the backend for a `device_code` (secret, kept on the PC) +
`user_code` (shown to the user), opens the system browser to
`https://<app>/link?user_code=…`, and polls. The signed-in customer approves "Link this PC",
names it, and explicitly enables remote control. The agent receives an opaque PC credential
(stored hashed server-side; DPAPI-protected on the PC) and connects to `/ws/agent` with it.

### D5. Controller identity and pairing

- A *controller installation* = one browser/PWA installation with a non-extractable WebCrypto
  **ECDSA P-256** key pair stored as a `CryptoKey` in IndexedDB. Chosen over Ed25519 because
  P-256 is available non-extractable in every current iOS Safari/Chrome/Firefox build;
  Ed25519 in WebCrypto is newer and was not universally shipped at decision time.
  **[reversible: add `EdDSA` as a second supported `alg` once verified on target devices]**
- Key id (`kid`) = base64url(RFC 7638 JWK thumbprint, SHA-256). A controller socket announces
  its `kid` in `hello`; the relay binds the socket to that controller and every envelope on the
  socket must carry the same `kid`.
- Pairing (the pairing code is a **secret the backend never sees**): the PC generates a
  20-symbol Crockford base32 code (100 bits) and registers only
  `code_hash = SHA-256("dome-pair-handle-v1|" + code)` with the backend
  (`POST /v1/pairing/start`). QR content is `https://<app>/pair#code=XXXXX-XXXXX-XXXXX-XXXXX`
  (fragment ⇒ never sent in Referer); manual entry accepts the four groups, case-insensitively,
  with I/L→1 and O→0. The phone, signed in to the **same account**, submits `code_hash` + public
  JWK + display name + requested capabilities. The backend forwards a `pairing_request` to the PC
  (immediately, or on its next connect if offline). Both devices independently compute the
  6-digit verification code `HMAC-SHA256(key = code, msg = "dome-pair-verify-v1|" + pairing_id +
  "|" + pc_id + "|" + controller_kid)`, taking the first 8 bytes as a big-endian integer mod 10⁶,
  zero-padded (normative test vectors: `shared/protocol/fixtures/es256-*.json`). Because the
  backend does not hold the key, it cannot grind a substitute controller key whose code matches
  the phone's display. The customer approves **on the PC**; only then does the PC store the local
  grant and the backend create the controller + grant rows. An administrator or a password-only
  attacker cannot enroll a controller, and a relay that substitutes a key is detected.
- Revocation is possible from the phone (account) and the PC (local, `revoke_controller`
  frame). The PC's local store is the **only** source of controller public keys; the backend's
  `grants_snapshot` is a revocation/intersection list applied on every connect **before**
  accepting commands (a kid is usable only if locally approved *and* listed; effective
  capabilities = local ∩ snapshot). A snapshot can never add a controller or widen a grant.
  Plan-disabled devices (`pc_enabled=false`, `status=plan_disabled`) keep their local grants and
  are merely refused, so revocation and emergency stop keep working after a downgrade.
  Local "Disable remote control" wins over anything remote.

### D6. Command authentication: detached ES256 signature over exact bytes

Envelope: `{"v":1,"alg":"ES256","kid":"…","payload":"<exact JSON text>","sig":"<base64url r‖s>"}`.
The verifier (backend for routing checks, PC for authorization) verifies the signature over
the UTF-8 bytes of `payload` with the controller's paired public key, then parses `payload`
**once** with a strict parser (≤ 16 KiB, depth ≤ 8, duplicate keys rejected, no NaN/Infinity,
unknown top-level fields rejected). No canonicalisation step is needed because the signer's
bytes are transmitted verbatim. Signature encoding is IEEE P1363 (`r‖s`, 64 bytes), which is
what WebCrypto produces natively; Python converts DER↔raw with `cryptography`.

Verifiers resolve `kid` to a *key record* `{controller_id, account_id, jwk, capabilities}` and
reject a payload whose `controller_id`/`account_id` differ from the record
(`CONTROLLER_MISMATCH`/`ACCOUNT_MISMATCH`), so a controller can never sign its way into another
controller's grant. The PC additionally checks: paired key present locally and not revoked, grant
covers the action's capability, `expires_at` within the skew window, `target_pc_id` matches its
own identity, `command_id` unseen (durable SQLite journal keyed by `command_id` with
`command_digest = SHA-256(exact signed payload bytes)`; identical digest re-emits the previous
result, different digest is rejected with `COMMAND_ID_REUSED`), parameters/target validate against
the registry, target still exists (`tab_token` binds a YouTube target to one content-script
attachment), and — for disruptive actions — a consumed confirmation. Every action also declares a
`result` schema (`schemas/results.schema.json`) validated by the PC before emitting and by the
phone before rendering.

### D7. Confirmation transactions

Disruptive actions (`app.close`, `power.*`) use a two-message transaction: the PC answers the
command with `confirmation_required{challenge_text}` — the challenge serialised **once** into an
opaque string, exactly like the envelope payload, so no re-serialisation anywhere can change its
bytes. The challenge binds command, controller, PC, action, exact params, target and a digest of
the current target state, with a 60 s expiry. The relay validates a strictly parsed copy and
forwards the original string verbatim. The phone strictly parses it once for display (rendering
the primary line from `action`/`params`/`target` with its own registry labels and treating
`display.detail` as untrusted secondary text), the user approves, and the controller signs a
`confirmation` payload with `challenge_digest = SHA-256(challenge_text)` plus its own
`issued_at`/`expires_at`. The PC verifies the confirmation with the **same kid** as the command,
re-checks the target state digest, and consumes the challenge atomically in SQLite before
executing. Once `confirmation_required` was emitted the challenge's expiry governs (the command's
own `expires_at` is checked once at receipt; controllers default disruptive commands to a 90 s
lifetime). A caller-supplied `confirm=true` does not exist in the protocol.

### D8. Entitlements

Central plan definitions live in `shared/protocol/plans.json`. The backend is authoritative
for plan state (Stripe webhooks → subscription state machine → entitlement). For PC-side
gates (routine steps, marked by the optional `origin` member of the command payload) the backend
issues short-lived (1 h) **EdDSA-signed entitlement assertions** (claims in
`schemas/entitlement.schema.json`, signed with `joserfc`) bound to `account_id` + `pc_id`; the
agent verifies them against `/.well-known/dome-jwks.json`, refreshes on every connect and at 80 %
of the lifetime, and honours the last verified Pro assertion for at most 72 h after expiry
**only** when the refresh fails with a network error or 5xx. The Free path is always available.
Plan limits on devices are expressed as `pc_enabled` / `status: plan_disabled` in
`grants_snapshot` and refused by both relay and agent; they are never revocations. Entitlement
never grants a capability the controller lacks and vice versa.

### D9. Repository and toolchain

- `uv` workspace (Python 3.12; agent pinned to 3.12 for Windows library compatibility,
  backend tested on 3.12 and 3.13): `cloud-api`, `pc-agent`, `shared/python`, `tools/dev-idp`,
  `tests`.
- `pnpm` workspace (Node 22): `mobile-app`, `browser-extension`, `shared/ts`.
- TypeScript pinned to 5.9.x (the 7.x native-port line is not yet validated with the Vite
  plugin set we use). React 19, Vite 7+, Tailwind CSS 4, Vitest.
- JSON Schema files in `shared/protocol/` are the single source of truth for action
  parameters and results, envelopes, WebSocket frames, bridge frames, REST bodies, entitlement
  claims, error codes and plans; `version.json → rules` holds the normative behavioural rules
  (terminal result, duplicates, snapshot semantics, pairing, identity binding, state cache,
  in-flight deadlines, coalescing). Python validates with ECMA-262-equivalent `pattern`
  semantics so both languages accept and reject the same strings. Python uses `jsonschema`
  at boundaries plus hand-written Pydantic models checked against the schemas in tests;
  TypeScript types are generated by `json-schema-to-typescript` and validated at runtime with
  `ajv`. Cross-language signing fixtures in `shared/protocol/fixtures/` are verified by both
  implementations in CI.

### D10. Explicitly not in V1 (documented, not simulated)

End-to-end payload encryption from the relay (the relay can read routed payloads; stated in
privacy docs), LAN/offline mode, Wake-on-LAN, native apps, AI interpretation/voice
(Phase E), screen viewing, trackpad/keyboard. The architecture leaves seams (transport
abstraction, action registry, provider adapter interface) without exposing unfinished UI.

## Consequences

- A single Postgres + single process keeps operating cost and failure modes small; the
  connection manager is bounded and documented.
- OIDC delegation means a real identity provider must be configured before public launch
  (founder action: choose Auth0/Keycloak, register the application, set redirect URIs).
- P-256 over Ed25519 costs nothing functionally; the `alg` field allows adding `EdDSA` later.
- Everything Windows- or iPhone-specific is implemented against real OS APIs but can only be
  **verified** on real devices; `docs/ACCEPTANCE.md` tracks evidence tags honestly.
