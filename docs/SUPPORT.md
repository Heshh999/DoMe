# DoMe support: requests, status, redaction, release history

Status: written on 2026-10-09 from the code in this repository (spec §11A "Downloads, help, and
support", §15). Evidence tags follow spec §17. The support flow is **integration-tested** on the
service (`cloud-api/tests/test_support_tickets.py`, real PostgreSQL) and **component-tested** on the
phone (`mobile-app/test/components/SupportPage.test.tsx`, `test/support.test.ts`, jsdom with a fake
API). Nothing here is **iPhone-tested**, and no real customer request has been handled: there is no
operator interface yet (§7) and no published response expectation (§5).

Related: `docs/TROUBLESHOOTING.md` (what customers try first), `docs/DATA_RETENTION.md`,
`docs/OPERATIONS.md`, `docs/SECURITY.md` §6 (operator boundaries).

## 1. How a customer reaches help

| Entry point | What it does |
| --- | --- |
| Every failure on the phone (`components/FailureLinks.tsx`) | Two links: **Check connection health** (`/app/health`) and **Get help** (`/support?category=…&code=…`). The category is derived from the error code (`labels.ts::supportCategoryFor`: `INPUT_*` → Touchpad / keyboard, `PAIRING_*` → Pairing, `PC_*` → Connection, …) and the code is carried along. Never a payment step. |
| Connection health (`/app/health`) | One next action per layer; after three retries it points to support with redacted diagnostics (`docs/TROUBLESHOOTING.md` §17). |
| Support page (`/support`, public) | Self-help topics (PC offline, extension, touchpad/keyboard not permitted, typing paused or went to the wrong place, pairing code rejected, phone revoked, outcome unknown, dictation), then the request form for signed-in customers. |
| More → Settings & help | Diagnostics download, a link to the Support page and to Connection health, and — only when `VITE_DOME_SUPPORT_URL` is set at build time — "Contact support directly". |
| Release notes and known issues (`/release-notes`) | Public release history and known issues with workarounds (§8). |

The support path is reachable on Free with no upgrade prompt (**component-tested**:
`test/components/UpgradeAndHealth.test.tsx`).

## 2. Submitting a request

The form on `/support` (`mobile-app/src/pages/public/SupportPage.tsx`) requires sign-in, so a request is
tied to the account; DoMe never asks for a password or a pairing code. Fields:

| Field | Rule |
| --- | --- |
| Category | one of `connection`, `pairing`, `media`, `input`, `apps`, `power`, `install`, `billing`, `account`, `other` (`rest.schema.json → support_ticket_request`), preselected from the help link |
| Error code | the code the customer was looking at, when the help link carried one (`^[A-Z_]+$`, ≤ 64) |
| Message | 1–2000 characters, the customer's own words. The hint says "Do not include passwords or pairing codes." |
| Diagnostics (optional) | **Attach redacted diagnostics…** builds the phone's report (§6.1); the customer can expand **Review exactly what would be sent**, then tick or untick **Include the diagnostics below**. Bounded to 32 768 characters (oldest log lines dropped first). Nothing is sent until **Send**. |
| App version | added automatically |

`POST /v1/support/tickets` (`cloud-api/dome_api/routes/support.py`) requires the session cookie, the
CSRF token and the exact Origin, validates the body strictly against `support_ticket_request`
(body up to 48 KiB because of the diagnostics), reserves the account's hourly budget **before** reading
the body (default 10 per hour, `DOME_RATE_SUPPORT_TICKETS_PER_HOUR`; over budget → `429 RATE_LIMITED`
"Too many support requests this hour. Try again later."), redacts the message and diagnostics (§6.2),
stores one `support_tickets` row (migration `0002_support_tickets`) and writes a
`support_ticket_created` security event with the category, reference and whether diagnostics were
attached — never the message. **Integration-tested**:
`test_create_list_get_scoped_per_account_with_redaction`, `test_create_requires_session_csrf_and_valid_body`,
`test_ticket_rate_limit_per_account`, `test_ticket_budget_holds_under_concurrent_submissions` (40
concurrent posts store exactly 10).

## 3. The reference id and status

On success the service answers `201` with `support_ticket_response`:

```json
{ "ticket_id": "…uuid…", "reference": "DM-7K3QX9MZ", "status": "received", "category": "input",
  "error_code": "INPUT_TARGET_CHANGED", "created_at": "…", "updated_at": "…" }
```

- `reference` is `DM-` plus 8 random Crockford base32 characters (no I, L, O, U;
  `^DM-[0-9A-HJKMNP-TV-Z]{8}$`). A collision with an existing reference is retried inside a savepoint
  (up to five times); if no row could be stored the answer is `503 SERVICE_UNAVAILABLE` "Could not record
  the support request. Nothing was received." — a reference is only ever shown for a stored row
  (**integration-tested**: `test_reference_collision_retries_in_a_savepoint`).
- The phone shows **"Received — reference DM-…"**, "Keep this reference. Status: received.", and either
  the configured response expectation (§5) or "A response time has not been published for this
  deployment yet."
- `status` is one of `received`, `in_review`, `answered`, `closed`; `answer` (≤ 4000 characters) appears
  when an operator answered. **Today nothing sets anything but `received`**: there is no operator route
  (`cloud-api/KNOWN_ISSUES.md` #6).
- **Your requests** on the Support page lists the account's own tickets, newest first, at most 50
  (`GET /v1/support/tickets`); `GET /v1/support/tickets/{id}` returns one. Another account's id answers
  `404` exactly like a missing one. The list is fetched when the page loads and after a successful
  submission; there is no polling (`mobile-app/KNOWN_ISSUES.md` #14).

## 4. When submission fails

DoMe never claims that support received a request without a stored reference. The phone classifies a
failure (`mobile-app/src/lib/support.ts::submissionFailure`):

| Outcome | When | What the customer sees |
| --- | --- | --- |
| **Not sent** | the request never left the phone (no CSRF token, the body failed the contract) or the service answered a definite 4xx (validation, `RATE_LIMITED`, signed out) | "‹error sentence› Support has not received this request." plus a read-only **redacted summary** with **Copy summary** and **Try again**; "send it another way" (once a support contact is configured) |
| **Not confirmed** | the connection dropped or timed out, the service answered 5xx, or a 2xx body failed schema validation | "DoMe could not confirm whether support received this request: it may have arrived before the problem." The customer is asked to press **Refresh your requests** first and to send again only if the request is not listed, so it does not arrive twice. The redacted summary is offered too. |

The summary contains the category, error code, app version, the message and — if included — the
redacted diagnostics text; nothing else. Nothing is resent automatically. The request body has no
idempotency key, so the phone cannot match an unconfirmed draft to a stored ticket by itself
(`mobile-app/CONTRACT_ISSUES.md` #11, `KNOWN_ISSUES.md` #20). **Component-tested**:
`SupportPage.test.tsx` (201 reference, 4xx "Not sent", 5xx / unreadable 201 / network "Not confirmed"
with a list refresh and no resend, signed-out state).

## 5. Response expectation (founder-configured)

DoMe publishes **no** support response time until the founder configures one:

- `DOME_SUPPORT_RESPONSE_EXPECTATION` (cloud-api, ≤ 200 characters, default empty). When set, every
  ticket body carries it as `response_expectation` and the phone shows it after receipt. When empty the
  field is absent and the phone says "A response time has not been published for this deployment yet."
- `VITE_DOME_SUPPORT_URL` (mobile-app build, optional) adds a direct contact link to the Support and
  Settings pages. The in-app form works without it.

Before setting either: decide who answers, during which hours, through which channel (there is no
operator route yet, §7), and pick a promise that holds on the worst normal week. Do not publish a time
that only holds when nothing goes wrong. Leave both unset rather than guess; record the decision in
`docs/HANDOFF.md`.

## 6. Redaction rules

Diagnostics are **customer-initiated, reviewable and redacted** on the phone, redacted again on the
service, and never collected in the background.

### 6.1 What the phone's report contains (`mobile-app/src/lib/diagnostics.ts`)

App version, protocol and registry version, release channel; the browser's user-agent string, whether
the app runs standalone, online state, language, viewport size, QR/IndexedDB/WebCrypto support;
whether a session exists and the plan name; relay status and whether a controller is bound; per PC its
id, connection state, enabled flag, last-seen time, freshness, and the reported remote-control,
extension and lock flags; the last 30 commands as id, PC id, **action name**, lifecycle state, origin,
error code, duration, time and a no-answer flag (no parameters, no results); the manual-input session
summary (phase, PC id, pointer/keyboard flags, sequence counters, accepted/dropped counts, problem code,
end reason — never events); and the recent in-memory log, whose logger redacts any field named like a
token, code, pairing value, challenge, payload, signature, title, artist, detail, e-mail, CSRF value,
JWK, cookie, password, display name, nonce, URL, `text`, `composer`, `events`, `key` or `keys`
(`mobile-app/src/lib/log.ts::REDACT_KEY`, **unit-tested**).

### 6.2 What the service does before storing (`cloud-api/dome_api/logging.py`)

- `redact_diagnostics` on the diagnostics text: the log key rules plus the keys that carry what spec
  §11A forbids — `text`, `events`, `composer`, `typed`, `keys`, `url`/`href`/`link`, `query`/`search`,
  `pairing`/`pairing_code`, `clipboard`, `video_id` and anything ending in `_url`, `_href`, `_text`,
  `_query`; then every remaining string: absolute URLs reduced to scheme and host, bare `host/path` to
  the host, token-shaped substrings (JWT-like, `Bearer …`, Stripe-style keys, base64url/hex runs of 32+
  characters) and pairing codes masked. JSON embedded as a string gets the key rules too.
- `redact_text` on the message: the token and pairing-code pass only (it is the customer's own words,
  so URLs are left as typed).
- **Integration-tested** with a typed-text input event, a YouTube watch URL and pairing codes that never
  reach `support_tickets.diagnostics_redacted`. Over-redaction is accepted; pairing-code detection is
  heuristic (`cloud-api/KNOWN_ISSUES.md` #12).

### 6.3 Never included by default

Default diagnostics and support records never contain:

- pairing material: pairing codes, verification codes, code hashes;
- credentials: passwords, session cookies, CSRF tokens, PC credentials, access tokens, private keys,
  signatures, entitlement assertions;
- raw command text typed on the **Type** tab, command parameters or results;
- typed keyboard content, composer text or input events from the touchpad/keyboard;
- confirmation challenge text;
- audio (DoMe records none);
- full media URLs, video ids, media or window titles;
- unrelated browsing data (other tabs, history).

The PC's own bundle (tray **Diagnostics…** / `DoMe.exe diagnostics`, `pc-agent/dome_agent/diagnostics.py`)
follows the same rule: status (including the input summary's counters, held key/button names and the
foreground **process name**, never a window title or typed text), the last 500 local security events
and the last 200 log lines after a second redaction pass. It is written to
`%LOCALAPPDATA%\DoMe\diagnostics\` and is **not uploaded by DoMe**; the form accepts only the phone's
report. A customer who wants to share it uses the contact configured in §5.

What is included and could still be identifying: the browser user-agent string, PC and command ids,
action names, timestamps and the customer's own message. That is the necessary minimum for diagnosis;
the customer sees it before sending.

## 7. Support staff boundaries

- No support route can execute, queue or forward anything to a PC (`routes/support.py`), and support
  staff have no way to run customer commands, approve a phone or bypass a local approval.
- There is **no operator interface yet**. Tickets are readable by the owning account only through the
  API. Until the protected operator interface exists (spec §15: strong operator authentication, audited
  changes, redacted records only), an operator who must read a ticket does so through an audited,
  read-only database session and answers out of band; record each such access. Nothing sets `status` or
  `answer` today.
- Operator suspension of an account (spec §15) is not implemented either. When it exists it will still
  not be a substitute for the customer revoking a phone or disabling remote control on the PC.

## 8. Known issues and release history upkeep

The public page `/release-notes` renders `mobile-app/src/content/release-notes.json`
(`pages/public/ReleaseNotesPage.tsx`): a `status` block (channel, headline, what verification means),
`releases[]` (`version`, `date`, `state`, `changes`, `verified`, `not_verified`) and `known_issues[]`
(`title`, `detail`, `workaround`). Rules for keeping it honest:

1. **Every release gets an entry before it is published**, with `state` saying exactly what it is
   (today: "in development — not published"). List only changes that exist in that build.
2. **`verified` lists only evidence that exists**, with its kind (unit, integration, Windows device,
   iPhone, load). Anything without device evidence goes to `not_verified`. Never move an item to
   `verified` because tests on a simulator passed.
3. **Known issues are customer-facing**: what happens, why, and a supported workaround. Never "disable
   antivirus/SmartScreen" or any OS protection. Sources: the components' `KNOWN_ISSUES.md` files,
   `docs/INPUT_CONTROL.md` §12 and support tickets with a recurring category/error code. Remove an entry
   only when the fix shipped in a published release, and say so in that release's `changes`.
4. **Keep the download status consistent** with `pages/public/DownloadPage.tsx` ("Release status: not yet
   published") and `docs/WINDOWS_INSTALL.md` §9.
5. The page is part of the PWA build: change the JSON, run `pnpm check` in `mobile-app/`, deploy.
6. Review the list at each release and whenever a ticket category spikes.

## 9. Operational notes

- **Configuration**: `DOME_RATE_SUPPORT_TICKETS_PER_HOUR` (default 10 per account per hour, in-memory per
  process: correct for the single relay process of ADR-0001 D1, `cloud-api/KNOWN_ISSUES.md` #2);
  `DOME_SUPPORT_RESPONSE_EXPECTATION` (§5). Neither is in the root `.env.example` yet
  (`cloud-api/KNOWN_ISSUES.md` #10).
- **Monitoring**: count `support_ticket_created` security events per day and per category; a spike in
  `input` or `connection` usually tracks a release or an incident (`docs/OPERATIONS.md` §8).
- **Retention**: `support_tickets` rows are deleted with the account (foreign key `ON DELETE CASCADE`).
  No time-based purge exists yet and `docs/DATA_RETENTION.md` has no row for this table; adding both is
  open work. The older `support_diagnostics` table remains unused.
- **Incident handling**: if a stored ticket is found to contain something §6.3 forbids, delete the
  field's content, record the incident, and fix the redactor with a regression test before re-enabling.
- **Budget release**: the hourly budget is given back when nothing is stored; under concurrency the
  window edge can move by milliseconds (`cloud-api/KNOWN_ISSUES.md` #13).
- **Acceptance (spec §17 scenario 25)**: failed submission and successful receipt are
  component-/integration-tested as above; the redaction check is integration-tested; none of it has
  been exercised on a real iPhone or against a deployed service (**not yet verified**).
