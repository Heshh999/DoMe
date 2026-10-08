# DoMe data retention

Status: this table describes every record class the code stores today (cloud-api PostgreSQL models,
the agent's SQLite store and files, the PWA's browser storage, the extension's storage, logs) and the
retention rule for each. **Implemented** means the code enforces the rule now (evidence tag given);
**proposed** means the rule is the design and no job enforces it yet. As of this writing cloud-api has
**no automated purge job**: rows marked `proposed` accumulate until Phase C/D adds the scheduled
cleanup described in §4. The agent's bounds (journal 5,000 rows, security events 500 rows, challenge
purge, log rotation) are implemented and unit-tested.

Design rules that hold everywhere (`docs/adr/0001-foundational-decisions.md`, cloud-api
`models.py` docstring, unit- and integration-tested):

- No command parameters, results, media or window titles, pairing codes, confirmation challenge
  texts, plaintext session ids, device codes, credentials or tokens are stored by the cloud. Only
  SHA-256 digests and lifecycle metadata.
- Every account-owned table carries `account_id`, every query is scoped by it, and `ON DELETE CASCADE`
  from `accounts` removes the rows when the account row is hard-deleted.
- Logs are structured and redacted before any sink (keys named `token`, `secret`, `*_code`,
  `pc_credential`, `challenge_text`, `payload`, `sig`, `title`, `email`, …); URLs are logged as paths
  only, `user_code` is masked in request logs, query strings are never logged.
- The relay can see routed payloads in transit (not end-to-end encrypted, ADR-0001 D10) but does not
  persist them.

"Who can access" lists the only parties with an intended read path. Operators do not have one today:
the operator interface is Phase C/D, and until then database access is direct administrative access
to the managed PostgreSQL by the founder, which should be treated as the highest-privilege path and
audited by the hosting provider's own access logs.

## 1. Cloud (cloud-api, PostgreSQL)

Encryption at rest: the managed PostgreSQL on the chosen host (Fly.io Managed Postgres in ADR-0001 D2)
is expected to encrypt volumes and backups at rest; this must be confirmed on the vendor page before
launch (**not yet verified**). Application-level column encryption is not used; secrets are stored
only as hashes.

| Record class (table) | Purpose | Contents | Retention | Deletion trigger | Encryption at rest | Who can access |
| --- | --- | --- | --- | --- | --- | --- |
| `accounts` | Identity anchor | OIDC `issuer` + `subject`, email, display name, `plan`, timestamps, `deleted_at` | Life of the account; soft-deleted 30 days, then hard-deleted (proposed) | Account deletion (Phase C) | Managed volume encryption (expected) | The account (`GET /v1/session`); founder DB access |
| `sessions` | Web sign-in | SHA-256 of the session id, CSRF token, timestamps, SHA-256 of the user agent, `revoked_at` | 30-day idle / 90-day absolute expiry (implemented, integration-tested); expired and revoked rows kept until purge (proposed: delete 30 days after expiry) | Logout, session revoke, account deletion, purge | Managed | The account (`GET /v1/account/sessions`, metadata only) |
| `auth_flows` | OIDC login in progress | `state`, `nonce`, PKCE verifier, `return_to` path | Deleted on callback, success or failure (implemented); abandoned flows expire in minutes (proposed purge of expired rows) | Callback or purge | Managed | Nobody (internal) |
| `pcs` | Linked PC inventory | Name, PC public key (JWK) and `kid`, `enabled`, reported remote-enabled flag, platform, agent version, `last_seen_at`, `last_power_request` (action + time only), `deleted_at` | Life of the link; soft-deleted on unlink and kept so a re-link reuses the row (implemented, DECISIONS #2); hard-deleted with the account | Unlink + account deletion | Managed | The account (`GET /v1/pcs`) |
| `pc_credentials` | PC long-lived credential | SHA-256 of the credential, timestamps, `revoked_at` | Until revoked; revoked rows kept for audit until account purge (proposed) | Unlink, re-link, account deletion | Managed | Nobody (hash only) |
| `pc_access_tokens` | 1-hour relay tokens | SHA-256 of the token, `expires_at`, `revoked_at` | 1 h validity (implemented); expired rows should be purged daily (proposed) | Expiry purge, unlink | Managed | Nobody (hash only) |
| `device_link_codes` | PC linking (device-code flow) | SHA-256 of `device_code`, `user_code`, PC public JWK, agent version, platform, name hint, state, SHA-256 of requester IP | Valid 10 minutes (implemented); rows should be purged 24 h after expiry (proposed) | Purge | Managed | The signed-in account previews a pending code by `user_code` |
| `controllers` | Paired phone installations | Controller public JWK + `kid`, display name, `last_seen_at`, `revoked_at` | Life of the pairing; revoked rows kept (needed so a re-pair of the same key is detectable) until account purge | Revoke + account deletion | Managed | The account (`GET /v1/controllers`) |
| `grants` | Controller → PC permissions | Capability list, timestamps, `revoked_at` | Life of the grant; revoked rows retained as security history until account purge | Revoke, unlink, account deletion | Managed | The account (`GET /v1/pcs/{id}/grants`) |
| `pairing_sessions` | Pairing in progress | SHA-256 of the pairing code (never the code), state, claimed controller JWK/name/capabilities, attempt counter | Valid 5 minutes (implemented); decided/expired rows should be purged after 7 days (proposed) | Purge | Managed | The account (`GET /v1/pairing/{id}` after claim) |
| `commands` | Command lifecycle history | Action name, SHA-256 of the signed payload, state, error code, timestamps, duration; **no params, no results, no targets, no titles** | Proposed: 30 days, then deleted; in-flight rows are swept to terminal states by the deadline sweeper (implemented) | Purge, account deletion | Managed | The account (`GET /v1/commands`, last 100 at most) |
| `security_events` | Account security activity | Kind, severity, actor, subject id, redacted `detail` (passed through the log redactor before storage, DECISIONS #13), SHA-256 of client IP keyed by the session secret | Proposed: 12 months, then deleted; unattributed rows (`account_id NULL`) 90 days | Purge, account deletion | Managed | The account (`GET /v1/account/security-events`, last 200 at most) |
| `activation_events` | Product measurement (install → first command funnel) | Kind, subject id, `detail` without command content | Table exists; **no event is emitted yet**. Proposed: 24 months aggregated, raw rows 90 days; `account_id` set to NULL on account deletion (`ON DELETE SET NULL`, implemented) | Purge | Managed | Founder analytics only; separate from security records by table |
| `subscriptions` (Phase C) | Billing state cache | Stripe customer/subscription/price ids, state, period, grace | Life of the account + 7 years as part of financial records (proposed) | Account hard-delete keeps a minimal reference in `billing_events` | Managed | The account (Billing page); operators via audited interface |
| `billing_events` (Phase C) | Webhook receipt and dedup | Provider event id, type, status, redacted error, payload digest; **no payload** | 7 years (financial records, proposed); `account_id` set NULL on account deletion | Financial retention window | Managed | Operators (failure queue) |
| `usage_periods` (Phase E) | AI allowance accounting | Counters per period | 13 months (proposed) | Purge, account deletion | Managed | The account (Billing page) |
| `layouts`, `routines` (Phase C) | Customer configuration | Layout definition / routine steps (action ids and validated params, no secrets), name, `deleted_at` | Life of the account; retained through downgrade (spec §3); soft-deleted 30 days then purged (proposed) | Customer delete, account deletion | Managed | The account only |
| `support_diagnostics` (Phase C) | Customer-initiated support bundle | Redacted bundle JSON, `expires_at` | `expires_at` (proposed 14 days), then deleted | Expiry, customer delete, account deletion | Managed | The account and authorised support staff through the audited operator interface |
| `pending_deletions` (Phase C) | Deletion job state | Provider cancel state, attempts, redacted error | Until `completed_at` + 30 days (proposed) | Completion | Managed | Operators |
| `operator_users`, `operator_audit` (Phase C/D) | Operator identity and audit trail | Operator OIDC subject/email/role; audited action, target account id, redacted detail | Audit: 7 years (proposed); never cascaded from account deletion (`target_account_id` is a plain UUID) | Policy | Managed | Operators with the audit role |

Server logs (structlog JSON on stderr): retained by the host's log pipeline (Fly.io keeps a short
window unless shipped elsewhere); if shipped to Grafana Cloud the free tier keeps 14 days.
Proposed: 30 days. Contents are redacted as above; request lines carry route templates and masked
paths. **Not yet verified** on the production host.

Database backups: included with the managed Postgres; retention and encryption follow the vendor's
plan and must be confirmed (**not yet verified**). A restore rehearsal is required before release
(`docs/spec/MASTER_PROMPT.md` §15).

## 2. Windows PC (pc-agent)

Location: `%LOCALAPPDATA%\DoMe` on Windows (`~/.local/state/dome` elsewhere). The directory is the
customer's own; uninstall removes it (`pc-agent/README.md`). Nothing here leaves the PC except
through the agent's relay connection or a customer-initiated diagnostics export.

| Record class | Purpose | Contents | Retention | Deletion trigger | Encryption at rest | Who can access |
| --- | --- | --- | --- | --- | --- | --- |
| `secrets/pc_key.bin` | PC identity key | PKCS8 PEM of the ES256 private key | Life of the install | Uninstall / delete state dir | DPAPI current-user scope on Windows (implemented, **not Windows-device-tested**); file mode 0600 with a logged warning elsewhere | The Windows user account that installed the agent |
| `secrets/pc_credential.bin` | Relay credential | Opaque 32-byte credential from link time | Until `revoked`, credential rejection or unlink (implemented: discarded on `revoked` / 401) | Unlink, revocation, uninstall | DPAPI / 0600 | Same |
| `identity.json` | Link record | `pc_id`, `account_id`, PC name, relay/API URLs, `linked_at` | Life of the link | Unlink, uninstall | Plain file (non-secret) | Same |
| `state.sqlite3 › settings` | Local switches | `remote_enabled` (default false), `media_while_locked`, `start_at_login`, PC name, last snapshot id, `snapshot_pc_enabled`, last verified entitlement claims (never the token) | Life of the install | Uninstall | Plain SQLite (WAL) | Same |
| `state.sqlite3 › grants` | Locally approved controllers (the only key source) | Controller id, `kid`, public JWK, capabilities, display name, timestamps, revocation reason, snapshot intersection data | Until revoked; revoked rows are kept so a snapshot cannot un-revoke them and are replaced on re-approval of the same `kid` (implemented, unit-tested) | Local revoke, snapshot revocation, uninstall | Plain SQLite | Same |
| `state.sqlite3 › journal` | Durable command journal (replay protection, crash recovery) | `command_id`, SHA-256 of the signed payload, action, controller id, state, the last emitted result frame (validated result shape, may include the resulting player/tab state such as a video id and title as returned to the phone), error code, timestamps, `sent` | Newest 5,000 rows (implemented, pruned on every insert, unit-tested) | Prune, uninstall | Plain SQLite | Same; a redacted subset appears in diagnostics |
| `state.sqlite3 › challenges` | Confirmation transactions | Exact `challenge_text`, pinned `kid`, digests, `expires_at`, `consumed_at` | Purged when older than 24 h past expiry at every start-up (implemented) | Purge, uninstall | Plain SQLite | Same |
| `state.sqlite3 › approved_apps` | Local app allowlist | `app_id`, display name, absolute `.exe` path, pinned SHA-256 | Until removed locally | `remove-app`, uninstall | Plain SQLite; never editable remotely | Same (boundary: a local user who can edit the file is the same user the agent runs as) |
| `state.sqlite3 › pending_power` | Armed power countdown | Command id, action, `fires_at` | Cleared on completion, cancel and at every start-up (implemented) | Completion/restart | Plain SQLite | Same |
| `state.sqlite3 › pending_revocations` | Offline local revocations | Controller id, `kid`, reason | Until the relay acknowledges the `revoke_controller` write (implemented) | Acknowledgement | Plain SQLite | Same |
| `state.sqlite3 › security_events` | Local security log | Kind + redacted detail | Newest 500 rows (implemented) | Prune, uninstall | Plain SQLite | Same; included (redacted) in diagnostics |
| `logs/agent.log` | Redacted JSON-lines log | Events after the redaction processor; no tokens, codes, challenge texts, payloads or titles | Rotating: 2 MB × 3 backups ≈ 8 MB (implemented) | Rotation, uninstall | Plain files | Same |
| `diagnostics/dome-diagnostics-*.json` | Customer-initiated support bundle | Versions, non-secret settings, redacted status, local security events, last 200 redacted log lines; `kid`s truncated | Until the customer deletes it (no automatic purge) | Customer | Plain file | The customer; shared only if they choose to |
| `com.dome.agent.json` + `HKCU` native-messaging keys | Browser bridge registration | Path to the native host, allowed extension ids | Life of the install | `uninstall-native-host` | — | Same |
| `HKCU\…\Run\DoMe` | Start at login | Command line | Until toggled off | Tray toggle, uninstall | — | Same |

## 3. Phone (PWA) and browser extension

| Record class | Purpose | Contents | Retention | Deletion trigger | Encryption at rest | Who can access |
| --- | --- | --- | --- | --- | --- | --- |
| IndexedDB `dome › keys › controller` | Controller identity | Non-extractable ECDSA P-256 `CryptoKeyPair`, cached `controller_id` | Life of the installation; survives sign-out by design | "Forget this installation" (awaited deletion, unit-tested), clearing site data (then re-pairing is required) | Browser storage protection of the device (iOS data protection when locked); the private key is never exportable to script | This origin in this browser profile only |
| Session cookie `dome_session` | Sign-in | Opaque session id | 30-day idle / 90-day absolute (server-side) | Logout, revoke, expiry | HttpOnly, Secure (https origins), SameSite=Lax; not readable by script | Browser → cloud-api only |
| Service-worker precache | App shell | Built static assets only; `/v1`, `/ws`, `/link`, `/pair`, `/.well-known` are never cached (implemented, unit-tested) | Until the next deploy (`autoUpdate`) | Update, clearing site data | Browser cache | This origin |
| In-memory stores (zustand) | Live state, command records, pending confirmations | Cleared on reload; sign-out resets stores and closes the socket | Page lifetime | Reload, sign-out | — | — |
| Pairing code | Never stored: read from the URL fragment once, scrubbed from the address bar, kept in component state until the claim is sent, then discarded (unit-tested) | — | seconds | — | — | — |
| Diagnostics download | Customer-initiated | Redacted client log and environment facts | Until the customer deletes the file | Customer | Device storage | The customer |
| Extension `chrome.storage.local` | Browser identity for tab targeting | Random `browser_instance_id`, user-set `profile_label`, last connection state | Life of the extension install | Uninstall, clearing extension data | Browser profile storage | The extension only (no network access of its own) |
| Extension in-memory tab registry | Tab tokens and player state | Rebuilt after every service-worker restart | Worker lifetime | Termination | — | — |

## 4. Proposed cleanup job (not implemented)

A daily task in cloud-api (`lifecycle.py` already owns the in-flight sweeper) would delete, in
small batches: expired `pc_access_tokens`, `device_link_codes` and `auth_flows` older than 24 h past
expiry, `pairing_sessions` decided/expired more than 7 days ago, `sessions` expired or revoked more
than 30 days ago, `commands` older than 30 days, `security_events` older than 12 months (90 days when
unattributed), `support_diagnostics` past `expires_at`, and hard-delete `accounts` soft-deleted more
than 30 days ago (cascading), keeping `billing_events` and `operator_audit`. Each run should write one
`security_events` row of kind `retention_purge` (actor `system`, counts only). Until it exists, the
retention column marked *proposed* is a policy statement, not a guarantee.

## 5. Customer-facing summary (for the privacy notice draft)

What DoMe's service keeps about you: your account identity from the sign-in provider, the names and
public keys of your PCs and phones, which phone may do what on which PC, a history of command
*types* and outcomes (never what was playing or typed), and security activity. What it never keeps:
your password (the sign-in provider holds it), pairing codes, confirmation texts, media titles,
command parameters or results. What stays only on your PC: the PC's private key, its credential,
the list of apps you approved and the command journal. What stays only on your phone: the phone's
signing key. The relay can see commands and results while routing them (it is not end-to-end
encrypted) and does not store them.
