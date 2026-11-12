# DoMe data retention

Status: this table describes every record class the code stores today (cloud-api PostgreSQL models,
the agent's SQLite store and files, the PWA's browser storage, the extension's storage, logs) and the
retention rule for each. **Implemented** means the code enforces the rule now (evidence tag given);
**proposed** means the rule is the design and no job enforces it yet. Since 2026-11-12 cloud-api runs
the hourly retention purge (§4, cloud-api DECISIONS #122): a rule marked **implemented (purge job)** is
enforced by it, at most 500 rows per table per pass, integration-tested against real PostgreSQL
(cloud-api `tests/test_retention_purge.py`). What is still marked `proposed` (the financial records'
7 years, the operator audit, server logs, aggregated activation events) is a policy statement, not a
guarantee. The agent's bounds (journal 5,000 rows, security events 500 rows, challenge purge, log
rotation) are implemented and unit-tested.

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
  from `accounts` removes the rows when the account row is hard-deleted, except the financial records
  (`subscriptions`, `topup_purchases`, `billing_events`) and `activation_events`, whose `account_id` is
  set to NULL instead (`ON DELETE SET NULL`; for the first two since migration 0008).
- Account deletion (`DELETE /v1/account`, cloud-api DECISIONS #121; implemented, integration-tested:
  cloud-api `tests/test_account_deletion.py`): after a confirmed password entry within 10 minutes and the typed
  phrase, the account is marked deleted and its email and display name are blanked at once; the
  subscription is cancelled (or the cancel is retried until the provider confirms it); every session,
  phone, grant, PC credential and PC token is revoked, every PC soft-deleted, the phones' TURN
  credentials revoked, the assistant consent cleared, the support diagnostics deleted and the layouts
  and routines soft-deleted, in one transaction with one `account_deleted` security event (counts
  only). The account row and everything that cascades from it are hard-deleted 30 days later by the
  purge job, never while a cancel is pending, a provider subscription is not cancelled or a Checkout
  page is still pending. The sign-in provider's login is not deleted by DoMe (cloud-api KNOWN_ISSUES
  #40).
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
reached over TLS with channel binding (`sslmode=require&channel_binding=require`), kept recoverable for 6 hours by Neon's restore window and nothing
else; Neon's encryption at rest is likewise **not yet verified**. Application-level column encryption is not used; secrets are stored
only as hashes.

| Record class (table) | Purpose | Contents | Retention | Deletion trigger | Encryption at rest | Who can access |
| --- | --- | --- | --- | --- | --- | --- |
| `accounts` | Identity anchor | OIDC `issuer` + `subject`, email, display name, `plan`, timestamps, `deleted_at`; AI assistant consent (`assistant_consent_version`, `assistant_consented_at`) | Life of the account. On deletion the email and display name are blanked and the consent columns cleared at once; `issuer` and `subject` stay so a sign-in with the same identity lands on the "account deleted" notice; the row is hard-deleted 30 days after `deleted_at` with everything that cascades (implemented, purge job), never while billing is open (a pending cancel, a live provider subscription, a pending Checkout page). Consent columns until withdrawn (cleared at once, implemented) or the account is deleted; the history is in `security_events` | Account deletion (`DELETE /v1/account`, implemented), then the purge job; `DELETE /v1/assistant/consent` for the consent columns | Managed volume encryption (expected) | The account (`GET /v1/session`, `GET /v1/assistant`); founder DB access |
| `sessions` | Web sign-in | SHA-256 of the session id, CSRF token, timestamps, SHA-256 of the user agent, `revoked_at` | 30-day idle / 90-day absolute expiry (implemented, integration-tested); expired and revoked rows deleted 30 days after expiry or revocation (implemented, purge job) | Logout, session revoke, account deletion, purge | Managed | The account (`GET /v1/account/sessions`, metadata only) |
| `auth_flows` | OIDC login in progress | `state`, `nonce`, PKCE verifier, `return_to` path | Deleted on callback, success or failure (implemented); abandoned flows expire in minutes and are deleted at the next login start or 24 h past expiry (implemented, purge job) | Callback or purge | Managed | Nobody (internal) |
| `pcs` | Linked PC inventory | Name, PC public key (JWK) and `kid`, `enabled`, reported remote-enabled flag, platform, agent version, `last_seen_at`, `last_power_request` (action + time only), `deleted_at` | Life of the link; soft-deleted on unlink and kept so a re-link reuses the row (implemented, DECISIONS #2); soft-deleted at account deletion and hard-deleted with the account 30 days later (implemented, purge job) | Unlink + account deletion | Managed | The account (`GET /v1/pcs`) |
| `pc_credentials` | PC long-lived credential | SHA-256 of the credential, timestamps, `revoked_at` | Until revoked; revoked rows kept for audit until the account's hard delete (implemented, purge job) | Unlink, re-link, account deletion | Managed | Nobody (hash only) |
| `pc_access_tokens` | 1-hour relay tokens | SHA-256 of the token, `expires_at`, `revoked_at` | 1 h validity (implemented); expired rows deleted 24 h past expiry (implemented, purge job) | Expiry purge, unlink | Managed | Nobody (hash only) |
| `device_link_codes` | PC linking (device-code flow) | SHA-256 of `device_code`, `user_code`, PC public JWK, agent version, platform, name hint, state, SHA-256 of requester IP | Valid 10 minutes (implemented); rows deleted 24 h after expiry (implemented, purge job) | Purge | Managed | The signed-in account previews a pending code by `user_code` |
| `controllers` | Paired phone installations | Controller public JWK + `kid`, display name, `last_seen_at`, `revoked_at` | Life of the pairing; revoked rows kept (needed so a re-pair of the same key is detectable) until the account's hard delete (implemented, purge job) | Revoke + account deletion | Managed | The account (`GET /v1/controllers`) |
| `grants` | Controller → PC permissions | Capability list, timestamps, `revoked_at` | Life of the grant; revoked rows retained as security history until the account's hard delete (implemented, purge job) | Revoke, unlink, account deletion | Managed | The account (`GET /v1/pcs/{id}/grants`) |
| `pairing_sessions` | Pairing in progress | SHA-256 of the pairing code (never the code), state, claimed controller JWK/name/capabilities, attempt counter | Valid 5 minutes (implemented); decided/expired rows deleted after 7 days (implemented, purge job) | Purge | Managed | The account (`GET /v1/pairing/{id}` after claim) |
| `commands` | Command lifecycle history | Action name, SHA-256 of the signed payload, state, error code, timestamps, duration; **no params, no results, no targets, no titles** | 30 days, then deleted (implemented, purge job); in-flight rows are swept to terminal states by the deadline sweeper (implemented) | Purge, account deletion | Managed | The account (`GET /v1/commands`, last 100 at most) |
| `security_events` | Account security activity | Kind, severity, actor, subject id, redacted `detail` (passed through the log redactor before storage, DECISIONS #13), SHA-256 of client IP keyed by the session secret | 12 months (365 days), then deleted; unattributed rows (`account_id NULL`) 90 days (implemented, purge job); an account's rows also go with its hard delete | Purge, account deletion | Managed | The account (`GET /v1/account/security-events`, last 200 at most) |
| `activation_events` | Product measurement (install → first command funnel) | Kind, subject id, `detail` without command content | Table exists; **no event is emitted yet**. Raw rows 90 days (implemented, purge job); 24 months aggregated (proposed, no aggregation is built); `account_id` set to NULL on account deletion (`ON DELETE SET NULL`, implemented) | Purge | Managed | Founder analytics only; separate from security records by table |
| `subscriptions` (Phase C) | Billing state cache | Stripe customer/subscription/price ids, state, period, grace | Life of the account; survives the account's hard delete with `account_id` NULL (implemented, migration 0008); then 7 years as a financial record (proposed, **for counsel to confirm**; nothing deletes it yet) | Financial retention window | Managed | The account (Billing page); operators via audited interface |
| `billing_events` (Phase C) | Webhook receipt and dedup | Provider event id, type, status, redacted error, payload digest; **no payload** | 7 years (financial records, proposed, **for counsel to confirm**; nothing deletes it yet); `account_id` set NULL at the account's hard delete (implemented) | Financial retention window | Managed | Operators (failure queue) |
| `topup_purchases` (1.3) | AI top-up purchases | Stripe Checkout Session and payment-intent ids, requests bought, amount in cents and currency, status, timestamps; **no card data** | Life of the account; survives the account's hard delete with `account_id` NULL (implemented, migration 0008); then 7 years as a financial record (proposed, **for counsel to confirm**; nothing deletes it yet) | Financial retention window | Managed | The account sees its balance (Billing page); founder DB access |
| `usage_periods` (Phase E) | AI allowance accounting per account and calendar month (UTC) | Counters only: interpretations used and reserved, cost used and reserved (micro-dollars), input/output/cache-read/cache-write tokens, failed turns | Life of the account (billing and reconciliation questions; no shorter period is stated); deleted with the account's hard delete (implemented, purge job). Not written since protocol 1.3 | Account deletion | Managed | The account (`GET /v1/assistant` allowance) |
| `usage_windows` (1.3) | AI allowance per account: the 5-hour and the weekly window | Window start and end; requests used and reserved; cost used and reserved (micro-dollars); failed turns. Counts only | Life of the account (no shorter period is stated; ended windows are kept); deleted with its hard delete (implemented, purge job) | Account hard delete | Managed | The account (`GET /v1/assistant` allowance); founder DB access |
| `ai_balances` (1.3) | AI balances that never roll over: Free's one-time taste and purchased top-up requests | Counters only: taste used and reserved, top-up requests remaining and reserved, `updated_at` | Life of the account; deleted with its hard delete (implemented, purge job) | Account hard delete | Managed | The account (allowance); founder DB access |
| `assistant_turns` (Phase E) | AI turn ledger: reservation, settlement, reconciliation | Phone's turn id, status code, charged flag, period and month, reserved and actual cost, the four token counts, model served, fallback used, provider request id, latency, timestamps; **no text column of any kind** | 400 days, then deleted, settled rows only (implemented, purge job; reconciliation and billing questions); stale `pending` rows are settled as `abandoned` by the sweeper (implemented, integration-tested) | Purge, account deletion (`ON DELETE CASCADE`) | Managed | Founder DB access; the account sees only its allowance |
| `ai_spend_months` (Phase E) | Operator AI spending ceiling | Month start, spent and reserved micro-dollars across all accounts; **no account data** | Life of the service (no purge rule; kept) | — | Managed | Founder DB access |
| `layouts`, `routines` (Phase C) | Customer configuration | Layout definition / routine steps (action ids and validated params, no secrets), name, `deleted_at` | Life of the account; retained through downgrade (spec §3); soft-deleted (customer delete or account deletion) rows deleted 30 days after `deleted_at` (implemented, purge job). No route writes these tables yet | Customer delete, account deletion | Managed | The account only |
| `support_diagnostics` (Phase C) | Customer-initiated support bundle | Redacted bundle JSON, `expires_at` | Deleted at `expires_at` (implemented, purge job; 14 days proposed for `expires_at`), and at once on account deletion (implemented). No route writes this table yet | Expiry, customer delete, account deletion | Managed | The account and authorised support staff through the audited operator interface |
| `support_tickets` (1.1) | Customer-initiated support request | Reference, category, error code, the customer's message, the customer-reviewed diagnostics text after the server's redaction, app version, status, the answer, timestamps; never pairing material, credentials, typed text or media titles | Life of the account (no shorter period is stated); deleted with its hard delete (implemented, purge job) | Account hard delete | Managed | The account (`GET /v1/support/tickets`); founder DB access |
| `pending_deletions` (Phase C) | Deletion job state | Provider cancel state, attempts, redacted error | Until `completed_at` + 30 days (implemented, purge job); an open row is never deleted and keeps the account from its hard delete | Completion | Managed | Operators |
| `pairing_sessions.pc_key_mac` (1.4) | The PC key pin the phone verifies (Screen view, `rules.pc_key_pinning`) | The 43-character HMAC the PC computed over its own key with the pairing code (the code itself is never sent) | With its pairing row (deleted after 7 days, as above; implemented, purge job) | Purge | Managed | The phone that claimed the pairing, once approved (`GET /v1/pairing/{id}`) |
| `screen_accounts` (1.4) | The per-account lock every Screen view reservation and TURN mint takes, and the monthly mint counter | `account_id`, month start, mints this month, `updated_at` | Life of the account; deleted with its hard delete (implemented, purge job) | Account deletion (`ON DELETE CASCADE`) | Managed | Founder DB access |
| `screen_view_windows` (1.4) | The Free Screen view allowance (5-hour rolling windows) | Window start/end; seconds used and reserved; forgiven failed and refused starts; seconds of pause discount given | 30 days after the window ends (implemented, purge job) | Account deletion | Managed | The account sees its allowance (`screen_ice_servers`), founder DB access |
| `screen_reservations` (1.4) | One Screen view start as the relay measured it (the ledger) | Ids (account, PC, phone, command, session), reserved/charged seconds, forward/start/end times, end reason; **no SDP, address or name** | 90 days (billing questions), then deleted, settled rows only (implemented, purge job); stale rows are settled by the sweeper (implemented, integration-tested) | Account deletion | Managed | Founder DB access |
| `turn_credentials` (1.4) | The phone's current TURN credential (one row per phone) | The provider's username (never the credential, which reaches the phone once), mint/expiry times, TTL, the start and session it serves, state, revocation reason | Replaced by the phone's next mint; otherwise deleted 30 days after expiry (implemented, purge job); revoked at the provider on account deletion (implemented) | Next mint, phone or account deletion | Managed | Founder DB access |
| `turn_deferred_revocations` (1.5) | A phone's TURN username still serving a live Screen view session after the phone minted its next credential (a Next screen switch keeps the old session until the new one shows its picture) | The provider's username (never the credential), the screen session id, account and phone ids, expiry | Until that session ends, then revoked at the provider and deleted; or the phone or account is revoked; or it expires | Session end, phone or account deletion | Managed | Founder DB access |
| `turn_pending_revocations` (1.4) | Revocations the TURN provider has not confirmed yet | Username, account and phone ids (no foreign key: a deleted account's credential is still revoked), reason, expiry, attempts, last error code | Until the provider confirms or the credential expires (implemented, integration-tested) | Confirmation or expiry (the sweeper) | Managed | Nobody (internal) |
| `turn_mint_days` (1.4) | The spending guard's count of credential lifetimes not covered by a session | Per account and UTC day: TTL seconds minted and released | 62 days (implemented, with the hourly reconciliation pass) | Purge, account deletion | Managed | Founder DB access |
| `turn_usage_days` (1.4) | Relayed bytes per account per day as the TURN provider reports them | Bytes, reconciliation time | 62 days (implemented) | Purge, account deletion | Managed | Founder DB access (`/healthz` shows only the month's total) |
| `turn_reconcile_passes` (1.4) | When the provider's figures were last pulled (the guard's freshness) | Timestamp, row count; **no account data** | 62 days (implemented) | Purge | Managed | Founder DB access |
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
| `state.sqlite3 › screen_history` (1.4) | The PC's Screen view history (tray › history) | Phone id and name, start and end time, seconds live, end or refusal reason; **no picture, SDP or address** | Newest 20 rows (implemented, unit-tested) | Prune, `DoMe.exe screen-history --clear`, uninstall (Yes / `/REMOVEDATA` only) | Plain SQLite | Same |
| `state.sqlite3 › screen_grant_settings` and the cooldown settings (1.4) | The PC owner's Screen view choices per phone and the post-Stop cooldown | Ask each time, cooldown length; the cooldown's end time and which phone was stopped | Life of the grant; the cooldown until it ends (implemented) | Grant removal, uninstall (Yes / `/REMOVEDATA` only) | Plain SQLite | Same |
| `screen_session.json` (1.4) | Crash recovery of a live Screen view session | Session id, phone id and name, start time; **no SDP** | Deleted at every session end; read and deleted at the next start-up after a crash (implemented, unit-tested) | Session end, start-up | Plain file | Same |
| `%LOCALAPPDATA%\Programs\DoMe\dome-server.json` | The DoMe server address that `DoMe.exe link` falls back to (written by Setup) | Only `{"api_url": "https://…"}`: no account, PC or user data | Life of the install | Uninstall (always) | Plain file (non-secret) | Same |

## 3. Phone (PWA) and browser extension

| Record class | Purpose | Contents | Retention | Deletion trigger | Encryption at rest | Who can access |
| --- | --- | --- | --- | --- | --- | --- |
| IndexedDB `dome › keys › controller` | Controller identity | Non-extractable ECDSA P-256 `CryptoKeyPair`, cached `controller_id` | Life of the installation; survives sign-out by design | "Forget this installation" (awaited deletion, unit-tested), clearing site data (then re-pairing is required) | Browser storage protection of the device (iOS data protection when locked); the private key is never exportable to script | This origin in this browser profile only |
| Session cookie `dome_session` | Sign-in | Opaque session id | 30-day idle / 90-day absolute (server-side) | Logout, revoke, expiry | HttpOnly, Secure (https origins), SameSite=Lax; not readable by script | Browser → cloud-api only |
| Re-prompt marker cookie `dome_reprompt` | A sign-in refused because the email is not confirmed (cloud-api DECISIONS #119) | The value `1`, nothing else: no account, email or id | 1 day after the last refusal | The next successful sign-in in that browser, or expiry | HttpOnly, Secure (https origins), SameSite=Lax, Path `/v1/auth`; not readable by script | Browser → cloud-api auth routes only |
| Service-worker precache | App shell | Built static assets only; `/v1`, `/ws`, `/link`, `/pair`, `/.well-known` are never cached (implemented, unit-tested) | Until the next deploy (`autoUpdate`) | Update, clearing site data | Browser cache | This origin |
| In-memory stores (zustand) | Live state, command records, pending confirmations | Cleared on reload; sign-out resets stores and closes the socket | Page lifetime | Reload, sign-out | — | — |
| Pairing code | Never stored: read from the URL fragment once, scrubbed from the address bar, kept in component state until the claim is sent, then discarded (unit-tested) | — | seconds | — | — | — |
| Diagnostics download | Customer-initiated | Redacted client log and environment facts | Until the customer deletes the file | Customer | Device storage | The customer |
| `localStorage` `dome.assistant.prefs` (Phase E, being built 2026-10-11) | Assistant preferences on this phone | Flags (show the Assistant tab, use AI, ask before every action, muted, voice notice seen), the butler's name and form of address, the chosen voice and speaking rate; **nothing said or typed** | Life of the installation | Settings, "Forget this installation", clearing site data | Browser storage | This origin in this browser profile only |
| In-memory assistant store (Phase E, being built) | Conversation on screen, the recent lines for the next AI request, the last app, turn timings | At most 30 bubbles; never written to `localStorage`, `sessionStorage` or IndexedDB | Cleared on sign-out, on switching PC, after 10 minutes idle and on reload | Those events | — | — |
| Speech recognition and synthesis (iOS) | Voice in and out | Handled by the iPhone (Apple may process recognition on its servers); DoMe receives only the transcript text, never audio | Not stored by DoMe | — | — | Apple, under its own terms |
| IndexedDB `dome › pins` (1.4) | The PC keys pinned at pairing (Screen view verifies every PC-signed answer against them) | Per PC: `pc_id`, the PC key id and public JWK, `pinned_at`; never the pairing code | Until the PC is paired again (replaced) or the installation is forgotten | Sign-out / account switch, "Forget this installation", clearing site data | Browser storage | This origin in this browser profile only |
| Screen view session (1.4) | The live picture and its connection | In memory only (`<video>.srcObject`); the SDP, candidates, ICE servers, TURN credential and answer envelope are never logged, stored or put in diagnostics | Page lifetime | Stop, leaving the page, sign-out | — | — |
| Extension `chrome.storage.local` | Browser identity for tab targeting | Random `browser_instance_id`, user-set `profile_label`, last connection state | Life of the extension install | Uninstall, clearing extension data | Browser profile storage | The extension only (no network access of its own) |
| Extension in-memory tab registry | Tab tokens and player state | Rebuilt after every service-worker restart | Worker lifetime | Termination | — | — |

## 4. Cleanup job (implemented: the retention purge)

`dome_api/purge.py` (cloud-api DECISIONS #122) runs in the API process every hour, the first pass an
hour after start-up; `DOME_PURGE_ENABLED=false` stops it (`.env.example`). Each pass:

1. Finishes account deletions a crash interrupted: an account deleted more than 10 minutes ago without
   its `account_deleted` event gets the rest of the deletion (§1, "Account deletion").
2. Deletes, per table, at most 500 rows past their period, oldest first, skipping rows another
   transaction holds: `sessions` 30 days after expiry or revocation; `auth_flows`, `pc_access_tokens`
   and `device_link_codes` 24 h past expiry; `pairing_sessions` 7 days after they were decided or
   expired; `commands` after 30 days; `security_events` after 12 months (90 days when unattributed);
   `activation_events` after 90 days; `support_diagnostics` at `expires_at`; `turn_credentials` 30 days
   after expiry; `screen_view_windows` 30 days after the window ends; `screen_reservations` after 90
   days and `assistant_turns` after 400 days, settled rows only; `pending_deletions` 30 days after
   `completed_at`; `layouts` and `routines` 30 days after `deleted_at`.
3. Hard-deletes accounts deleted more than 30 days ago, with everything that cascades from them. The
   rows of `subscriptions`, `topup_purchases` and `billing_events` stay, with `account_id` set to
   NULL. An account is kept while its subscription cancel is pending, a provider subscription of it is
   not cancelled or a Checkout page of it is still pending.

Each table runs in its own transaction. One log line per pass (`purge.run`) carries counts per table
only. The per-pass `retention_purge` security event this section once proposed is not written: the
log line carries the counts. Not covered by the job: the financial records' own period (7 years
proposed, for counsel to confirm), `operator_audit`, `ai_spend_months`, server logs and database
backups (the host's retention), and the tables that live as long as the account (cloud-api
KNOWN_ISSUES #42).

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
cost) and stores none of the words or answers. You can turn it off at any time. You can delete your
account yourself after signing in again: your subscription stops, your phones and PCs are signed out
at once, and the account is erased 30 days later. Payment records are kept after that, without your
email or name, for a period still to be confirmed. Your login at the sign-in provider is separate;
DoMe support can have it deleted on request. Old records (sign-in sessions, command history, security
activity) are deleted automatically after the periods in this document.
