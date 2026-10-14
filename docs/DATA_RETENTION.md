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
- The optional AI assistant (Phase E step 1, `docs/design/assistant.md` §3.10, §3.12) stores
  counts, costs, outcome codes, the model id, the provider's request id and timestamps. It never
  stores or logs the words, the recent lines, the context, the persona, the reply, the choices or the
  proposed steps, in the database, logs, files, error bodies, security events or diagnostics
  (integration-tested: cloud-api `tests/test_assistant_routes.py::test_words_never_reach_logs_or_tables`).
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
launch (**not yet verified**). Staging's database is Neon's free plan (since 2026-10-14): test data only,
reached over TLS (`sslmode=require`), kept recoverable for 6 hours by Neon's restore window and nothing
else; Neon's encryption at rest is likewise **not yet verified**. Application-level column encryption is not used; secrets are stored
only as hashes.

| Record class (table) | Purpose | Contents | Retention | Deletion trigger | Encryption at rest | Who can access |
| --- | --- | --- | --- | --- | --- | --- |
| `accounts` | Identity anchor | OIDC `issuer` + `subject`, email, display name, `plan`, timestamps, `deleted_at`; AI assistant consent (`assistant_consent_version`, `assistant_consented_at`) | Life of the account; soft-deleted 30 days, then hard-deleted (proposed). Consent columns until withdrawn (cleared at once, implemented) or the account is deleted; the history is in `security_events` | Account deletion (Phase C); `DELETE /v1/assistant/consent` for the consent columns | Managed volume encryption (expected) | The account (`GET /v1/session`, `GET /v1/assistant`); founder DB access |
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
| `usage_periods` (Phase E) | AI allowance accounting per account and calendar month (UTC) | Counters only: interpretations used and reserved, cost used and reserved (micro-dollars), input/output/cache-read/cache-write tokens, failed turns | Life of the account (proposed; billing and reconciliation questions) | Account deletion | Managed | The account (`GET /v1/assistant` allowance) |
| `assistant_turns` (Phase E) | AI turn ledger: reservation, settlement, reconciliation | Phone's turn id, status code, charged flag, period and month, reserved and actual cost, the four token counts, model served, fallback used, provider request id, latency, timestamps; **no text column of any kind** | Proposed: 400 days, then deleted (reconciliation and billing questions); stale `pending` rows are settled as `abandoned` by the sweeper (implemented, integration-tested) | Purge, account deletion (`ON DELETE CASCADE`) | Managed | Founder DB access; the account sees only its allowance |
| `ai_spend_months` (Phase E) | Operator AI spending ceiling | Month start, spent and reserved micro-dollars across all accounts; **no account data** | Life of the service (proposed) | — | Managed | Founder DB access |
| `layouts`, `routines` (Phase C) | Customer configuration | Layout definition / routine steps (action ids and validated params, no secrets), name, `deleted_at` | Life of the account; retained through downgrade (spec §3); soft-deleted 30 days then purged (proposed) | Customer delete, account deletion | Managed | The account only |
| `support_diagnostics` (Phase C) | Customer-initiated support bundle | Redacted bundle JSON, `expires_at` | `expires_at` (proposed 14 days), then deleted | Expiry, customer delete, account deletion | Managed | The account and authorised support staff through the audited operator interface |
| `pending_deletions` (Phase C) | Deletion job state | Provider cancel state, attempts, redacted error | Until `completed_at` + 30 days (proposed) | Completion | Managed | Operators |
| `operator_users`, `operator_audit` (Phase C/D) | Operator identity and audit trail | Operator OIDC subject/email/role; audited action, target account id, redacted detail | Audit: 7 years (proposed); never cascaded from account deletion (`target_account_id` is a plain UUID) | Policy | Managed | Operators with the audit role |

Server logs (structlog JSON on stderr): retained by the host's log pipeline (Fly.io keeps a short
window unless shipped elsewhere); if shipped to Grafana Cloud the free tier keeps 14 days.
Proposed: 30 days. Contents are redacted as above; request lines carry route templates and masked
paths. **Not yet verified** on the production host.

AI provider (Phase E step 1, only for an account with current assistant consent): for each request
the phone's own parser could not handle, cloud-api sends Anthropic's API the owner's words, at most
six recent assistant lines (built on the phone from closed templates, no app, player, video or search
names) and a closed context of enums and numbers (`docs/design/assistant.md` §2.5), and keeps none of
it. What Anthropic retains, and for how long, follows Anthropic's API data terms for the key's
organisation; confirming those terms is part of the Phase E release gate (**not yet verified**).
Never sent: audio, screenshots, the app list or the PC's app names, titles, clipboard, files or
anything typed with the Touchpad keyboard.

Database backups: included with the managed Postgres; retention and encryption follow the vendor's
plan and must be confirmed (**not yet verified**). A restore rehearsal is required before release
(`docs/spec/MASTER_PROMPT.md` §15).

## 2. Windows PC (pc-agent)

Location: `%LOCALAPPDATA%\DoMe` on Windows (`~/.local/state/dome` elsewhere). The directory is the
customer's own. Nothing here leaves the PC except through the agent's relay connection or a
customer-initiated diagnostics export.

**What "uninstall" means in this table** (`pc-agent/DECISIONS.md` 55, a reasoned deviation from spec
§15): the Windows uninstaller always removes the program folder (with `dome-server.json`), the
native-messaging manifest and keys and start at login. It deletes the rest of `%LOCALAPPDATA%\DoMe`
(the key, the credential, `identity.json`, `state.sqlite3`, the logs and diagnostics) **only** when the
user answers **Yes** to its question, or when a silent uninstall is given `/REMOVEDATA`. By default
(No, or any silent uninstall without `/REMOVEDATA`) those records stay on the PC, still DPAPI-protected
where the table says so, until the user deletes them or reinstalls and uninstalls with Yes. In every
case the PC stays listed on the DoMe service until it is unlinked from the phone (Devices): the agent
cannot unlink itself (section 1).

| Record class | Purpose | Contents | Retention | Deletion trigger | Encryption at rest | Who can access |
| --- | --- | --- | --- | --- | --- | --- |
| `secrets/pc_key.bin` | PC identity key | PKCS8 PEM of the ES256 private key | Life of the install | Uninstall (Yes / `/REMOVEDATA` only) / delete state dir | DPAPI current-user scope on Windows (implemented, **not Windows-device-tested**); file mode 0600 with a logged warning elsewhere | The Windows user account that installed the agent |
| `secrets/pc_credential.bin` | Relay credential | Opaque 32-byte credential from link time | Until `revoked`, credential rejection or unlink (implemented: discarded on `revoked` / 401) | Unlink, revocation, uninstall (Yes / `/REMOVEDATA` only) | DPAPI / 0600 | Same |
| `identity.json` | Link record | `pc_id`, `account_id`, PC name, relay/API URLs, `linked_at` | Life of the link | Unlink, uninstall (Yes / `/REMOVEDATA` only) | Plain file (non-secret) | Same |
| `state.sqlite3 › settings` | Local switches | `remote_enabled` (default false), `media_while_locked`, `start_at_login`, PC name, last snapshot id, `snapshot_pc_enabled`, last verified entitlement claims (never the token) | Life of the install | Uninstall (Yes / `/REMOVEDATA` only) | Plain SQLite (WAL) | Same |
| `state.sqlite3 › grants` | Locally approved controllers (the only key source) | Controller id, `kid`, public JWK, capabilities, display name, timestamps, revocation reason, snapshot intersection data | Until revoked; revoked rows are kept so a snapshot cannot un-revoke them and are replaced on re-approval of the same `kid` (implemented, unit-tested) | Local revoke, snapshot revocation, uninstall (Yes / `/REMOVEDATA` only) | Plain SQLite | Same |
| `state.sqlite3 › journal` | Durable command journal (replay protection, crash recovery) | `command_id`, SHA-256 of the signed payload, action, controller id, state, the last emitted result frame (validated result shape, may include the resulting player/tab state such as a video id and title as returned to the phone), error code, timestamps, `sent` | Newest 5,000 rows (implemented, pruned on every insert, unit-tested) | Prune, uninstall (Yes / `/REMOVEDATA` only) | Plain SQLite | Same; a redacted subset appears in diagnostics |
| `state.sqlite3 › challenges` | Confirmation transactions | Exact `challenge_text`, pinned `kid`, digests, `expires_at`, `consumed_at` | Purged when older than 24 h past expiry at every start-up (implemented) | Purge, uninstall (Yes / `/REMOVEDATA` only) | Plain SQLite | Same |
| `state.sqlite3 › approved_apps` | Local app allowlist | `app_id`, display name, absolute `.exe` path, pinned SHA-256 | Until removed locally | `remove-app`, uninstall (Yes / `/REMOVEDATA` only) | Plain SQLite; never editable remotely | Same (boundary: a local user who can edit the file is the same user the agent runs as) |
| `state.sqlite3 › pending_power` | Armed power countdown | Command id, action, `fires_at` | Cleared on completion, cancel and at every start-up (implemented) | Completion/restart | Plain SQLite | Same |
| `state.sqlite3 › pending_revocations` | Offline local revocations | Controller id, `kid`, reason | Until the relay acknowledges the `revoke_controller` write (implemented) | Acknowledgement | Plain SQLite | Same |
| `state.sqlite3 › security_events` | Local security log | Kind + redacted detail | Newest 500 rows (implemented) | Prune, uninstall (Yes / `/REMOVEDATA` only) | Plain SQLite | Same; included (redacted) in diagnostics |
| `logs/agent.log` | Redacted JSON-lines log | Events after the redaction processor; no tokens, codes, challenge texts, payloads or titles | Rotating: 2 MB × 3 backups ≈ 8 MB (implemented) | Rotation, uninstall (Yes / `/REMOVEDATA` only) | Plain files | Same |
| `logs/crash.log` | Last-resort record of an unexpected error in `DoMe.exe` or `dome-native-host.exe` (`pc-agent/DECISIONS.md` 56) | Time, program name, the full command line and the Python traceback, including the exception message. Bearer tokens and long token-like strings are masked as in `agent.log`, but it is **not** a reviewed support bundle: a message can still hold a path, an address or other text | Appended per crash; started afresh once it passes 1 MB (implemented) | Uninstall (Yes / `/REMOVEDATA` only), or the user deletes it | Plain file | Same; never sent anywhere by DoMe. Support may ask the user to read it and copy its last lines (`docs/SUPPORT.md`) |
| `diagnostics/dome-diagnostics-*.json` | Customer-initiated support bundle | Versions, non-secret settings, redacted status, local security events, last 200 redacted log lines; `kid`s truncated | Until the customer deletes it (no automatic purge) | Customer, uninstall (Yes / `/REMOVEDATA` only) | Plain file | The customer; shared only if they choose to |
| `com.dome.agent.json` + `HKCU` native-messaging keys | Browser bridge registration | Path to the native host, allowed extension ids | Life of the install | `uninstall-native-host`, uninstall (always) | — | Same |
| `HKCU\…\Run\DoMe` | Start at login | Command line | Until toggled off | Tray toggle, uninstall (always) | — | Same |
| `%LOCALAPPDATA%\Programs\DoMe\dome-server.json` | The DoMe server address that `DoMe.exe link` falls back to (written by Setup) | Only `{"api_url": "https://…"}`: no account, PC or user data | Life of the install | Uninstall (always) | Plain file (non-secret) | Same |

## 3. Phone (PWA) and browser extension

| Record class | Purpose | Contents | Retention | Deletion trigger | Encryption at rest | Who can access |
| --- | --- | --- | --- | --- | --- | --- |
| IndexedDB `dome › keys › controller` | Controller identity | Non-extractable ECDSA P-256 `CryptoKeyPair`, cached `controller_id` | Life of the installation; survives sign-out by design | "Forget this installation" (awaited deletion, unit-tested), clearing site data (then re-pairing is required) | Browser storage protection of the device (iOS data protection when locked); the private key is never exportable to script | This origin in this browser profile only |
| Session cookie `dome_session` | Sign-in | Opaque session id | 30-day idle / 90-day absolute (server-side) | Logout, revoke, expiry | HttpOnly, Secure (https origins), SameSite=Lax; not readable by script | Browser → cloud-api only |
| Service-worker precache | App shell | Built static assets only; `/v1`, `/ws`, `/link`, `/pair`, `/.well-known` are never cached (implemented, unit-tested) | Until the next deploy (`autoUpdate`) | Update, clearing site data | Browser cache | This origin |
| In-memory stores (zustand) | Live state, command records, pending confirmations | Cleared on reload; sign-out resets stores and closes the socket | Page lifetime | Reload, sign-out | — | — |
| Pairing code | Never stored: read from the URL fragment once, scrubbed from the address bar, kept in component state until the claim is sent, then discarded (unit-tested) | — | seconds | — | — | — |
| Diagnostics download | Customer-initiated | Redacted client log and environment facts | Until the customer deletes the file | Customer | Device storage | The customer |
| `localStorage` `dome.assistant.prefs` (Phase E, being built 2026-10-11) | Assistant preferences on this phone | Flags (show the Assistant tab, use AI, ask before every action, muted, voice notice seen), the butler's name and form of address, the chosen voice and speaking rate; **nothing said or typed** | Life of the installation | Settings, "Forget this installation", clearing site data | Browser storage | This origin in this browser profile only |
| In-memory assistant store (Phase E, being built) | Conversation on screen, the recent lines for the next AI request, the last app, turn timings | At most 30 bubbles; never written to `localStorage`, `sessionStorage` or IndexedDB | Cleared on sign-out, on switching PC, after 10 minutes idle and on reload | Those events | — | — |
| Speech recognition and synthesis (iOS) | Voice in and out | Handled by the iPhone (Apple may process recognition on its servers); DoMe receives only the transcript text, never audio | Not stored by DoMe | — | — | Apple, under its own terms |
| Extension `chrome.storage.local` | Browser identity for tab targeting | Random `browser_instance_id`, user-set `profile_label`, last connection state | Life of the extension install | Uninstall, clearing extension data | Browser profile storage | The extension only (no network access of its own) |
| Extension in-memory tab registry | Tab tokens and player state | Rebuilt after every service-worker restart | Worker lifetime | Termination | — | — |

## 4. Proposed cleanup job (not implemented)

A daily task in cloud-api (`lifecycle.py` already owns the in-flight sweeper) would delete, in
small batches: expired `pc_access_tokens`, `device_link_codes` and `auth_flows` older than 24 h past
expiry, `pairing_sessions` decided/expired more than 7 days ago, `sessions` expired or revoked more
than 30 days ago, `commands` older than 30 days, `security_events` older than 12 months (90 days when
unattributed), `assistant_turns` older than 400 days, `support_diagnostics` past `expires_at`, and
hard-delete `accounts` soft-deleted more than 30 days ago (cascading), keeping `billing_events` and
`operator_audit`. Each run should write one
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
encrypted) and does not store them. If you turn on the optional AI assistant, the words of requests
DoMe cannot work out by itself, with a short note of the recent conversation and a few coarse facts
about your PC, go through DoMe's service to Anthropic; DoMe keeps only counts (requests, tokens,
cost) and stores none of the words or answers. You can turn it off at any time.
