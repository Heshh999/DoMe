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
- Key id (`kid`) = base64url(RFC 7638 JWK thumbprint, SHA-256).
- Pairing: the PC agent requests a pairing session; the backend issues a single-use 8-character
  code (Crockford base32, 40 bits, 5-minute expiry, hashed at rest, rate-limited). QR content is
  `https://<app>/pair#code=XXXX-XXXX` (fragment ⇒ never sent in Referer). The phone, signed in
  to the **same account**, submits code + public JWK + requested scopes. The backend forwards
  the request to the PC. Both devices independently compute a 6-digit verification code =
  first 6 decimal digits of `SHA-256("dome-pair-v1" ‖ pairing_id ‖ pc_id ‖ controller_kid)`
  and show it; the customer approves **on the PC**. Only then does the PC store the local grant
  and the backend create the controller + grant rows. An administrator or a password-only
  attacker cannot enroll a controller.
- Revocation is possible from the phone (account) and the PC (local). The agent synchronises
  the revocation list (`grants_snapshot`) on every connect **before** accepting commands.
  Local "Disable remote control" wins over anything remote.

### D6. Command authentication: detached ES256 signature over exact bytes

Envelope: `{"v":1,"alg":"ES256","kid":"…","payload":"<exact JSON text>","sig":"<base64url r‖s>"}`.
The verifier (backend for routing checks, PC for authorization) verifies the signature over
the UTF-8 bytes of `payload` with the controller's paired public key, then parses `payload`
**once** with a strict parser (≤ 16 KiB, depth ≤ 8, duplicate keys rejected, no NaN/Infinity,
unknown top-level fields rejected). No canonicalisation step is needed because the signer's
bytes are transmitted verbatim. Signature encoding is IEEE P1363 (`r‖s`, 64 bytes), which is
what WebCrypto produces natively; Python converts DER↔raw with `cryptography`.

The PC additionally checks: paired key present and not revoked, grant covers the action's
capability, `expires_at` within `[now-5s, now+5min]` skew window, `account_id`/`target_pc_id`
match its own identity, `command_id` unseen (durable SQLite journal; identical digest returns
the previous result, different digest is rejected), parameters validate against the action
schema, target still exists, and — for disruptive actions — a consumed confirmation.

### D7. Confirmation transactions

Disruptive actions (`app.close`, `power.*`) use a two-message transaction: the PC answers the
command with `confirmation_required` carrying a single-use challenge bound to the command
digest, controller, PC, action, exact params and target state, 60 s expiry. The phone shows
exactly that action and PC name; the user approves; the controller signs a `confirmation`
payload referencing `command_id` + `challenge_id` + `challenge_digest`. The PC verifies and
consumes the challenge atomically in SQLite before executing. A caller-supplied `confirm=true`
does not exist in the protocol.

### D8. Entitlements

Central plan definitions live in `shared/protocol/plans.json`. The backend is authoritative
for plan state (Stripe webhooks → subscription state machine → entitlement). For PC-side
gates (routines, >1 PC enabled) the backend issues short-lived (1 h) **EdDSA-signed
entitlement assertions** (JWT via `joserfc`) bound to `account_id` + `pc_id`; the agent
verifies them against the backend's JWKS. Grace: the agent honours the last verified Pro
assertion for at most 72 h after expiry when the backend is unreachable; the Free path is
always available. Entitlement never grants a capability the controller lacks and vice versa.

### D9. Repository and toolchain

- `uv` workspace (Python 3.12; agent pinned to 3.12 for Windows library compatibility,
  backend tested on 3.12 and 3.13): `cloud-api`, `pc-agent`, `shared/python`, `tools/dev-idp`,
  `tests`.
- `pnpm` workspace (Node 22): `mobile-app`, `browser-extension`, `shared/ts`.
- TypeScript pinned to 5.9.x (the 7.x native-port line is not yet validated with the Vite
  plugin set we use). React 19, Vite 7+, Tailwind CSS 4, Vitest.
- JSON Schema files in `shared/protocol/` are the single source of truth for action
  parameters, envelopes, WebSocket frames, error codes and plans. Python uses `jsonschema`
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
