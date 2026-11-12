Prepared from the code on 2026-11-12 for counsel; not legal advice; not reviewed by counsel.
Checked against commit 297f991 of the repository; every open point is marked "for counsel to confirm".

# DoMe data map

## What this is

This is a list of every kind of information DoMe handles: what it is, why DoMe has it, where it is kept, how
long it stays and who can see it. Each row names the file and line it was checked against (`path:line`, paths
from the top of the repository), so an engineer can show where it comes from. The outside companies that
receive any of it are listed in `docs/legal/subprocessors.md`.

Line numbers refer to commit 297f991, except those in the draft Privacy page
(`mobile-app/src/pages/public/PrivacyPage.tsx`) and Terms (`mobile-app/src/pages/public/TermsPage.tsx`), which
refer to the drafts as rewritten on 2026-11-12, the versions sent to counsel with this file.

Words used here:

- **Phone app**: the DoMe web app the customer opens on their iPhone (`mobile-app`).
- **PC app**: the DoMe Windows program on the customer's own PC (`pc-agent`).
- **Extension**: the DoMe browser add-on that controls YouTube (`browser-extension`).
- **Server**: DoMe's online service (`cloud-api`), hosted at Fly.io. It includes the **relay**, the part that
  passes messages between the phone and the PC.
- **Database**: the server's database. Production would use Fly Managed Postgres (not created yet); staging
  uses Neon.
- **Hash** (or fingerprint): a one-way code computed from a value; the value cannot be read back from it. A
  **keyed hash** is computed with a secret only the server holds. The same value always gives the same keyed
  hash, so it can still link events together. Whether a keyed hash of an address counts as personal
  information is for counsel to confirm.
- **Implemented** means the code enforces a rule today. **Proposed** means the rule is written down but nothing
  enforces it yet.

Environments. Staging is public at https://staging.domeremote.com (`deploy/launch/environments.toml:24-28`).
The repository calls staging "test data only", but it holds real sign-ups: at least the founder's own account and
a test sign-up; whether anyone else has signed up is not recorded (`docs/DATA_RETENTION.md:39`;
`docs/PROGRESS.md:7-10`; `docs/LAUNCH_SETUP.md:748-750`). The
brief for this pack says production is not live yet. The repository is not consistent on this point
(`docs/LAUNCH_SETUP.md:313-315` speaks of domeremote.com "today"), so the founder should confirm it.

## Summary for counsel

1. The server keeps each account's sign-in identity (email and display name), its PCs, phones and permissions,
   a history of command *types* (never their content), security events, support requests, billing state and
   usage counters (section 1).
2. Plain IP addresses are not stored. Keyed hashes of an address are stored on a few kinds of security event
   and on PC linking codes (section 1, section 2).
3. The relay can read what it routes: commands, typed keyboard text, YouTube searches, the PC's state (for
   example YouTube tab titles and the title of the window in front) and the results the PC sends back (for
   example approved apps' names and window titles, and YouTube search results). This traffic is encrypted
   between each device and the server, but it is not end-to-end encrypted. The relay does not store it
   (section 2).
4. Screen view pictures go directly between the PC and the phone, encrypted, and are never recorded. When no
   direct path exists, an outside relay company (Cloudflare) carries the encrypted stream and sees the devices'
   network addresses, times and data amounts (sections 2 and 6).
5. The AI assistant is switched off by default, and no deployment sets its provider key
   (`cloud-api/dome_api/settings.py:150`; `docs/LAUNCH_SETUP.md:702`). When it is on, the words of a request go
   to Anthropic through the server, and DoMe stores none of them (sections 2 and 6).
6. Nothing deletes old records automatically, and there is no way to delete an account except by asking support.
   Most retention periods are proposals (section 7).
7. The repository's own retention document and some engineering notes contain gaps and some statements that do
   not match the code. Section 8 lists them, so counsel is not misled by them. The draft Privacy page rewritten
   on 2026-11-12 was checked against this file; the mismatches found in the earlier draft are fixed in it
   (section 8).

## 1. Kept on DoMe's server

Who can see it. The account holder can see the rows marked "account holder" in the app. Rows marked "account
holder through the service's API only" can be read by the signed-in account from DoMe's service, but no app
screen shows them yet: the app's functions for the command history and security events
(`mobile-app/src/lib/api.ts:220,227`) are never called, nothing in the app calls the sessions list
(search of `mobile-app/src`), and the app has no page for them (`mobile-app/src/app/router.tsx:49-66`). Beyond
that, the only way in is the founder's direct database access: no operator screen exists
(`docs/DATA_RETENTION.md:30-33`; `docs/SUPPORT.md:179-183`). Where the database itself is hosted is in
`docs/legal/subprocessors.md`.

| What | Why | Where kept | How long | Who can see it | Source |
| --- | --- | --- | --- | --- | --- |
| Account identity: the sign-in provider's issuer and subject id, email (up to 254 characters), display name (up to 128), plan, plan-change time, created and deleted times | Sign-in; one account per person | Server database | Life of the account. No deletion feature exists; a 30-day soft delete followed by a hard delete is proposed only | Account holder; founder | `cloud-api/dome_api/db/models.py:115-128`; `docs/DATA_RETENTION.md:46`; `cloud-api/KNOWN_ISSUES.md:110-111` |
| How email and name arrive: from the sign-in provider (it is asked for "openid email profile"). The name falls back to the username, then the first part of the email address, then "DoMe user". Both are refreshed at each sign-in. A sign-in is refused, and nothing is written, unless the provider says the email is verified | Keep contact details current; refuse unconfirmed emails | Server database | As above | As above | `deploy/fly.toml:63`; `cloud-api/dome_api/auth/oidc.py:233-242`; `cloud-api/dome_api/routes/auth.py:121-147` |
| AI assistant consent: version and time | Record of consent for the optional assistant | Server database | Until withdrawn (cleared at once) or the account goes; giving and withdrawing are also security events | Account holder; founder | `cloud-api/dome_api/db/models.py:129-131`; `docs/design/assistant.md:900-907` |
| Web sign-in sessions: keyed hash of the session id, a security (CSRF) token, a hash of the browser's user-agent text, created, last-seen and expiry times, revoked time. There is no IP address column | Keep the person signed in | Server database | Expire after 30 days unused or 90 days in all (implemented). Expired and revoked rows stay; deleting them 30 days later is proposed | Account holder through the service's API only (the session list, `cloud-api/dome_api/routes/account.py:51-73`); no app screen yet; founder | `cloud-api/dome_api/db/models.py:134-148`; `cloud-api/dome_api/auth/sessions.py:36-41,49-58`; `cloud-api/dome_api/settings.py:76-77`; `docs/DATA_RETENTION.md:47` |
| Sign-in attempt in progress: random state, nonce and PKCE verifier, and the page to return to | Finish one sign-in safely | Server database | 10 minutes. Deleted when the sign-in returns, and every new sign-in deletes all expired attempts (implemented) | Nobody | `cloud-api/dome_api/db/models.py:151-160`; `cloud-api/dome_api/auth/oidc.py:43,167-179,205-215` |
| PCs: the name the person chose (default "My PC"), public key and key id, platform, PC app version, protocol versions, on/off flags, created and last-seen times, last power request (action and time only), deleted time | List the person's PCs and route commands to them | Server database | Life of the link. On unlink the row is marked deleted and kept, so a re-link reuses it | Account holder; founder | `cloud-api/dome_api/db/models.py:163-187`; `pc-agent/dome_agent/link.py:96`; `docs/DATA_RETENTION.md:49` |
| PC credentials and one-hour access tokens: hashes only | Let a PC prove who it is | Server database | Until revoked; revoked and expired rows are not purged | Nobody (hash only) | `cloud-api/dome_api/db/models.py:190-209`; `docs/DATA_RETENTION.md:50-51` |
| PC linking codes: hash of the device code; the short code the person types, in plain text (9 characters); the PC's public key, app version, platform and suggested name; a keyed hash of the requesting PC's IP address | Link a PC to an account | Server database | Code valid 10 minutes (implemented). Rows are not purged; deletion 24 hours after expiry is proposed. On account deletion the link to the account (and to the PC) is cleared and the row stays (`cloud-api/dome_api/db/models.py:228-229`) | The signed-in person who types the code; founder | `cloud-api/dome_api/db/models.py:212-235`; `cloud-api/dome_api/routes/agent_link.py:50-60`; `cloud-api/dome_api/security/tokens.py:41-44`; `docs/DATA_RETENTION.md:52` |
| Phones: public key and key id, the phone's display name, last seen, revoked time, plan-selection time | Know which phones may send commands | Server database | Life of the pairing; revoked rows kept | Account holder; founder | `cloud-api/dome_api/db/models.py:238-253`; `docs/DATA_RETENTION.md:53` |
| Permissions: which capabilities each phone has on each PC | Enforce what a phone may do | Server database | Life of the permission; revoked rows kept as security history | Account holder; founder | `cloud-api/dome_api/db/models.py:256-278`; `docs/DATA_RETENTION.md:54` |
| Pairing records: hash of the pairing code (never the code itself), the claiming phone's public key, name and requested capabilities, attempt count, a check value for the PC's key | Pair a phone with a PC | Server database | Valid 5 minutes (implemented). Rows are not purged; 7 days is proposed | Account holder, after approval; founder | `cloud-api/dome_api/db/models.py:281-312`; `docs/DATA_RETENTION.md:55,67` |
| Command history: the action name (for example "pause"), a hash of the signed command, state, error code and times. No parameters, results, targets or titles | Investigate problems; stop duplicates. No app screen shows it yet | Server database | Nothing deletes it; 30 days is proposed | Account holder through the service's API only (last 100, `cloud-api/dome_api/routes/commands.py:17-18`); no app screen yet; founder | `cloud-api/dome_api/db/models.py:4-5,315-339`; `docs/DATA_RETENTION.md:56` |
| Security events (more than 40 kinds, for example sign-in, logout, phone paired or revoked, PC linked or unlinked, assistant consent given or withdrawn, support request sent, Screen view started or ended, plan changed, top-up credited): kind, severity, who acted, subject id, redacted detail. A keyed IP hash is added only for sign-in and account creation, failed pairing claims and failed link-code lookups | Account security history | Server database | Nothing deletes it; 12 months is proposed (90 days for events tied to no account) | Account holder through the service's API only (last 200 at most, `cloud-api/dome_api/routes/account.py:24-25`); no app screen yet; founder | `cloud-api/dome_api/db/models.py:342-358`; `cloud-api/dome_api/routes/auth.py:149-158`; `cloud-api/dome_api/routes/pairing.py:70-81`; `cloud-api/dome_api/routes/agent_link.py:89-103`; `cloud-api/dome_api/routes/account.py:24-33`; `docs/DATA_RETENTION.md:57`; kinds: search of `cloud-api/dome_api` for `kind="` |
| Support requests: category, error code, the customer's own message (up to 2,000 characters, after token-like text, pairing codes and network addresses are masked), optional diagnostics the customer reviewed first (up to 32,768 characters, redacted again by the server), app version, status, answer, reference, times. Sending needs a signed-in account; at most 10 per hour | Answer support requests | Server database | Deleted only together with the account. No time limit, and no rule in `docs/DATA_RETENTION.md` | Account holder; an operator only through direct database access (nothing sets the answer today) | `cloud-api/dome_api/db/models.py:813-838`; `cloud-api/dome_api/routes/support.py:57-115`; `cloud-api/dome_api/logging.py:209-215`; `cloud-api/dome_api/settings.py:122`; `docs/SUPPORT.md:179-183,216-218`; `cloud-api/KNOWN_ISSUES.md:26-30` |
| Subscription: Stripe customer, subscription, price and checkout ids; plan, billing interval, state, current period, grace deadline. No email, card data or amounts | Know which plan is paid for | Server database | Deleted with the account (database cascade), although `docs/DATA_RETENTION.md` proposes account life plus 7 years. This conflict is for counsel to confirm | Account holder (Billing page); founder | `cloud-api/dome_api/db/models.py:378-416`; `docs/DATA_RETENTION.md:59` |
| AI top-up purchases: Stripe checkout and payment ids, number of AI requests, amount in cents, currency, status, times | Credit each top-up exactly once | Server database | Deleted with the account; no rule in `docs/DATA_RETENTION.md` | Founder | `cloud-api/dome_api/db/models.py:486-504` |
| Payment notices from Stripe: Stripe's event id, type, object id, status, short error, a hash of the notice. The notice itself is never stored | Process each notice once | Server database | 7 years proposed. On account deletion the link to the account is cleared and the row stays | Founder | `cloud-api/dome_api/db/models.py:419-442`; `docs/DATA_RETENTION.md:60` |
| AI usage counters: 5-hour and weekly windows (request counts, cost in millionths of a dollar, failed turns); the one-time Free taste and top-up balances; one row per AI turn (status, cost, four token counts, model served, provider request id, timing), with no text column of any kind. An older monthly table is kept but no longer written. A service-wide monthly spending total holds no account data | Apply allowances and control cost | Server database | AI turns: 400 days proposed. Windows and balances: no rule in `docs/DATA_RETENTION.md` | Account holder sees the allowance; founder | `cloud-api/dome_api/db/models.py:445-584`; `cloud-api/KNOWN_ISSUES.md:87-90`; `docs/DATA_RETENTION.md:61-63` |
| Screen view counters: Free allowance seconds per account; one row per session start (ids, seconds, times, end reason; no connection set-up data, address or name); the phone's current relay login name from Cloudflare (never its password); revocation queues; credential seconds per day; relayed bytes per account per day as Cloudflare reports them | Meter the Free allowance and the relay cost | Server database | The three per-day tables: 62 days (implemented). The rest: proposed periods only | Account holder sees the allowance; founder | `cloud-api/dome_api/db/models.py:590-768`; `docs/DATA_RETENTION.md:68-76` |
| Tables that exist but that no code writes: product measurement, layouts, routines, support bundles, operator users, operator audit | Planned features (routines are a preview that saves nothing) | Server database | Empty today | Nobody | `cloud-api/dome_api/db/models.py:361-372,771-811,855-876` (search of `cloud-api/dome_api`: no other code uses them); `mobile-app/src/pages/app/RoutinesPage.tsx:1` |

## 2. Passing through the server without being stored

| What | Why | Where kept | How long | Who can see it | Source |
| --- | --- | --- | --- | --- | --- |
| The PC's latest state: YouTube tab titles, video ids, playback position and length; media app labels, titles and artists; the process name and window title of the app in front; browser profile labels; volume; lock and remote-control flags | Show the phone what the PC is doing | Server memory only, one copy per connected PC | While the PC is connected; never written to the database | The server process | `shared/protocol/schemas/relay-frames.schema.json:851,915,1011,1102-1122,1532-1543`; `cloud-api/dome_api/relay/manager.py:3-4,152`; `cloud-api/dome_api/relay/agent_ws.py:319`; `mobile-app/src/pages/public/PrivacyPage.tsx:64` |
| Commands, typed keyboard text, YouTube search words and Screen view connection set-up | Deliver them between phone and PC | Pass through the server. Encrypted between each device and the server (TLS), not end to end | Not stored. Typed input does not even leave a command row | Readable by the server while it routes them | `docs/adr/0001-foundational-decisions.md:167-178`; `docs/SECURITY.md:112`; `shared/protocol/version.json:70`; `cloud-api/dome_api/screen/service.py:5-6` |
| Results the PC sends back for each command: for example each approved app's display name with the titles of up to 32 of its windows, and YouTube search results (up to 10: video id, title, channel, length) | Show the phone the outcome of a command | Pass through the server to the phone, inside the PC's result message | Not stored: the command row keeps no results | Readable by the server while it routes them | `shared/protocol/schemas/relay-frames.schema.json:769-770,812-815,1294,1308-1309`; `shared/protocol/schemas/results.schema.json:127-200,430-491`; only approved apps are listed (`shared/protocol/actions.json:723-731`); `cloud-api/dome_api/db/models.py:1-5,315-339`; disclosed at `mobile-app/src/pages/public/PrivacyPage.tsx:64` |
| AI requests, only when the assistant is on and the account has consented: the person's words and whether they were spoken, typed or a tapped choice; up to six recent lines from the last 10 minutes; the assistant's chosen name and form of address; PC connection state; YouTube and media playback state as fixed codes; volumes; number of search results; part of day; the phone's capability list | Ask the AI provider to interpret a request DoMe could not work out alone | Pass through the server to Anthropic | Not stored or logged by DoMe | Anthropic, under its own terms | `cloud-api/dome_api/assistant/service.py:124-142`; `docs/DATA_RETENTION.md:84-91`; `mobile-app/src/pages/public/PrivacyPage.tsx:89`; `docs/design/assistant.md:1091-1093` |
| Client network (IP) addresses | Limit abuse of sign-in, PC tokens, linking and pairing | Server memory. On Fly.io the address is read from Fly's own header, and only on connections from Fly's proxy | The plain address is not stored; keyed hashes only, as in section 1 | The server process | `cloud-api/dome_api/security/proxy.py:1-17`; `deploy/fly.toml:67-76`; `cloud-api/dome_api/routes/auth.py:98`; `cloud-api/dome_api/security/tokens.py:41-44` |
| Server logs: for each request the method, route, a masked path, status and duration (no IP, no query string). Named sensitive fields are masked before output, including email, name, address, titles, typed text, the assistant's words, Screen view connection data and credentials. The AI and payment software libraries are held to warnings only | Running the service | Fly.io log output | Fly's own short window; no log shipping is set up; 30 days is proposed; "not yet verified" | Founder; Fly.io | `cloud-api/dome_api/security/headers.py:120-149`; `cloud-api/dome_api/logging.py:20-95,357-358,365-376`; `docs/DATA_RETENTION.md:79-82` |

## 3. On the phone

| What | Why | Where kept | How long | Who can see it | Source |
| --- | --- | --- | --- | --- | --- |
| A signing key that cannot be exported; the phone's controller id; the selected PC; each PC's public key pinned at pairing | Sign commands; check that Screen view answers come from the real PC | Phone browser storage (IndexedDB) | Key and pins: until "Forget this installation" or the site's data is cleared. Controller id and selected PC: cleared at sign-out | DoMe's web app in that browser only | `mobile-app/src/lib/controllerKey.ts:55-73,191-205`; `docs/DATA_RETENTION.md:140,150` |
| Cookie `dome_session`: a random id. Not readable by page scripts (HttpOnly); SameSite=Lax; Secure on https | Signed-in state | Phone browser | The browser keeps it 30 days from sign-in: it is set only at sign-in and never set again (its only caller is `cloud-api/dome_api/routes/auth.py:160`). On the server a session also ends after 30 days unused or 90 days in all (`cloud-api/dome_api/auth/sessions.py:69-76`) | The browser and the server only | `cloud-api/dome_api/auth/sessions.py:25,84-93`; `cloud-api/dome_api/settings.py:76-77`; disclosed at `mobile-app/src/pages/public/PrivacyPage.tsx:124` |
| Cookie `dome_reprompt`: the value "1" and nothing else (no account, email or id). Sent only to the sign-in routes | After a sign-in refused for an unconfirmed email, show the provider's login page again | Phone browser | One day; cleared by the next successful sign-in | The browser and the server's sign-in routes only | `cloud-api/dome_api/routes/auth.py:37-54,71-81,121-126,161-163` |
| No other cookie: the server sets cookies in two places only, and the phone app never writes one | — | — | — | — | `cloud-api/dome_api/auth/sessions.py:85`; `cloud-api/dome_api/routes/auth.py:73` (search of `cloud-api/dome_api` and `mobile-app/src`) |
| Preferences in the browser's local storage: assistant settings (on/off flags, the assistant's name and form of address, voice), touchpad settings, Screen view settings, whether the Screen view readout is hidden, dismissed Pro explanations, colour theme, and two markers that voice input failed or is unavailable | Remember settings on this phone | Phone browser (localStorage keys starting `dome.`) | Until changed or the site's data is cleared | DoMe's web app in that browser only | `mobile-app/src/store/assistant.ts:35`; `mobile-app/src/store/input.ts:16`; `mobile-app/src/store/screen.ts:20`; `mobile-app/src/components/ScreenReadout.tsx:22`; `mobile-app/src/components/ProExplanation.tsx:15`; `mobile-app/src/lib/theme.ts:15`; `mobile-app/src/lib/speech.ts:55-56` |
| A sign-in problem marker: a reason code | Explain why a sign-in did not work | Phone browser tab (sessionStorage) | About 60 seconds | That tab only | `mobile-app/src/lib/signInProblem.ts:75-81` |
| The assistant conversation (at most 30 items) | Show the conversation and give the AI recent context | Phone memory only | Cleared after 10 minutes without a request, at sign-out, on switching PC and on reload | DoMe's web app | `mobile-app/src/store/assistant.ts:147-148`; `mobile-app/src/pages/public/PrivacyPage.tsx:93` |
| Camera picture while scanning the pairing QR code (video only, no sound), decoded on the phone | Pair the phone | Phone memory | While the scanner is open | Nobody else | `mobile-app/src/lib/qr.ts:1-5,47` |
| Voice requests: handled by the iPhone's own speech recognition, which is Apple's and may process the words on Apple's servers. DoMe receives only the resulting text | Voice input for the assistant | Apple; DoMe gets text only | Listening stops after at most 15 seconds. DoMe never records or uploads audio | Apple, under its own terms | `mobile-app/src/lib/speech.ts:5-7,52-53`; `mobile-app/src/pages/public/PrivacyPage.tsx:87` |
| Phone diagnostics report: app and protocol versions, browser user-agent, language, screen size, plan name, each PC's id, state and last-seen time, the last 30 commands (ids, action names, states, timing; no parameters), input counters, the redacted recent log | Support | Built on the phone only when the customer asks; sent only if they attach it to a support request | If sent, as for support requests (section 1) | The customer reviews it before sending | `docs/SUPPORT.md:113-125` |
| The Screen view picture | Live view of the PC | Phone memory only | While the page is open | Nobody else | `docs/DATA_RETENTION.md:151`; `shared/protocol/version.json:76` |

## 4. On the PC

All of this is under `%LOCALAPPDATA%\DoMe`, which belongs to the Windows user who installed DoMe. Uninstalling
removes the program but deletes this data only if the person answers Yes, or a silent uninstall is given
`/REMOVEDATA`. Either way, the PC stays listed on the server until it is unlinked from the phone
(`docs/DATA_RETENTION.md:99-111`).

| What | Why | Where kept | How long | Who can see it | Source |
| --- | --- | --- | --- | --- | --- |
| The PC's private key and its server credential | Prove the PC's identity to the server | PC, protected by Windows (DPAPI) for that Windows user | Life of the install; the credential is discarded on revocation or unlink | That Windows user | `docs/DATA_RETENTION.md:115-116` |
| `identity.json`: PC id, account id, PC name, server addresses, link time | Remember the link | PC, plain file | Life of the link | That Windows user | `docs/DATA_RETENTION.md:117` |
| Local settings, approved phones (id, public key, capabilities, display name), pending power countdown, pending revocations | Run the PC app; the PC is the only source of phone keys | PC (`state.sqlite3`) | Life of the install; revoked phones kept | That Windows user | `docs/DATA_RETENTION.md:118-119,123-124` |
| Command journal: command id, hash, action, phone id, state, the last result sent (which may include a video id and title), error, times | Refuse replays; recover after a crash | PC (`state.sqlite3`) | Newest 5,000 rows (implemented) | That Windows user | `docs/DATA_RETENTION.md:120`; `pc-agent/dome_agent/store.py:652` |
| Confirmation texts for disruptive actions | Confirm power actions and closing apps | PC (`state.sqlite3`) | Deleted at start-up once 24 hours past expiry (implemented) | That Windows user | `docs/DATA_RETENTION.md:121` |
| Approved apps: display name, program path, SHA-256 of the program | Only these apps can be opened remotely | PC (`state.sqlite3`) | Until removed on the PC | That Windows user | `docs/DATA_RETENTION.md:122` |
| Local security events: kind and redacted detail | Local security log | PC (`state.sqlite3`) | Newest 500 rows (implemented) | That Windows user | `docs/DATA_RETENTION.md:125`; `pc-agent/dome_agent/store.py:789` |
| Screen view history: phone id and name, start and end times, seconds, end or refusal reason. No picture, connection data or address | Let the PC owner see who viewed and when | PC (`state.sqlite3`) | Newest 20 (implemented); the owner can clear it | That Windows user | `docs/DATA_RETENTION.md:131`; `pc-agent/dome_agent/store.py:19,820` |
| `screen_session.json`: session id, phone id and name, start time | Recover from a crash during Screen view | PC | Deleted at session end, or at the next start after a crash | That Windows user | `docs/DATA_RETENTION.md:133` |
| `logs/agent.log` (redacted) and `logs/native-host.log` (the browser bridge's log) | Troubleshooting | PC | Agent log rotates at 2 MB with 3 backups (implemented). The bridge log is not described in `docs/DATA_RETENTION.md` | That Windows user | `docs/DATA_RETENTION.md:126`; `pc-agent/dome_agent/logsetup.py:154`; `pc-agent/dome_agent/bridge/host.py:185` |
| `logs/crash.log`: the program's command line and error trace. Tokens are masked, but nobody reviews it, so it may hold a file path or an address | Last-resort crash record | PC | Started afresh after 1 MB. Never sent anywhere by DoMe | That Windows user | `docs/DATA_RETENTION.md:127`; `docs/SUPPORT.md:166-170` |
| Diagnostics files, made only when the customer asks: versions, settings, status (including the process name of the app in front), local security events, last 200 redacted log lines, and a hardware inventory (monitors, graphics adapters, Windows build, remote-session flag, HDR state, graphics preference) | Support | PC | Until the customer deletes them. DoMe never uploads them | The customer, and anyone they choose to send them to | `pc-agent/dome_agent/diagnostics.py:1-14,74-89`; `docs/SUPPORT.md:155-160`; `docs/DATA_RETENTION.md:128` |
| `input_holds.json`: which keys and buttons were held down (never typed text) | Release stuck keys after a crash | PC | Deleted once released. Not described in `docs/DATA_RETENTION.md` | That Windows user | `pc-agent/dome_agent/input_session.py:111,420-438`; `docs/SECURITY.md:112` |
| `update-check.json` (last check time and result, highest version seen) and `downloads/` (an installer the person asked to download) | Update check | PC | Not described in `docs/DATA_RETENTION.md` | That Windows user | `pc-agent/dome_agent/update_check.py:77-78,343-345` |
| `agent.lock`, `agent.pid` and the bridge socket | Run one copy; talk to the extension | PC | While running. Not described in `docs/DATA_RETENTION.md` | That Windows user | `pc-agent/dome_agent/single_instance.py:40-41`; `pc-agent/dome_agent/settings.py:82` |
| The update check: about every 24 hours (first 10 minutes after start), two small files fetched over HTTPS from GitHub, sending only "DoMe-agent/" plus the version as the user agent. GitHub therefore sees the PC's IP address and DoMe version. An installer is downloaded only when the person asks | Tell the person a newer version exists | GitHub (outside service) | GitHub's own terms | GitHub | `pc-agent/dome_agent/update_check.py:16-21,59-65,74-76,292` |

## 5. In the browser extension

| What | Why | Where kept | How long | Who can see it | Source |
| --- | --- | --- | --- | --- | --- |
| A random browser id, a profile label the person sets, the last connection state; and, until the browser restarts, the end of a fast-reconnect window | Tell browser profiles apart; reconnect | Extension storage in that browser profile | Until the extension is removed or its data cleared | The extension only | `browser-extension/src/background/storage.ts:1-15`; `docs/DATA_RETENTION.md:152` |
| YouTube tab state (titles, video ids, playback) | Control YouTube | Passed to the PC app on the same PC, which reports it to the relay as part of the PC's state (section 2) | Memory only | The PC app; the server while routing | `shared/protocol/schemas/relay-frames.schema.json:1011`; `docs/DATA_RETENTION.md:153` |
| Its permissions: native messaging, storage, alarms, scripting. It runs only on https://www.youtube.com pages and makes no network requests of its own | — | — | — | — | `browser-extension/public/manifest.json:7-10`; `docs/DATA_RETENTION.md:152` |
| YouTube search words: placed only in the search address of youtube.com/results, in the person's own browser | Run a search the person asked for | The person's browser sends it to YouTube | DoMe keeps no copy (the server keeps only the command's hash) | YouTube | `browser-extension/src/shared/youtubeUrl.ts:44-53`; `shared/protocol/version.json:70` |

The extension is not published on the Chrome Web Store or Edge Add-ons; it is loaded by hand for development
(`docs/PRODUCT_AND_PLANS.md:165`; `docs/WINDOWS_INSTALL.md:737`).

## 6. What DoMe never collects or keeps

| Never collected or kept | What the code or contract says | Source |
| --- | --- | --- |
| Screen pictures and PC sound | No frame or audio sample is written to disk, logged, put in diagnostics, sent to the server or sent to an AI. The media travels encrypted between the PC and the phone; a relay company sees only encrypted data, addresses and timing | `shared/protocol/version.json:76`; `mobile-app/src/pages/public/PrivacyPage.tsx:100-102` |
| The mouse pointer's picture | Treated as screen content under the same rule | `shared/protocol/version.json:76` |
| Screen view connection data: connection set-up text, network candidates, IP addresses, relay passwords | Never in logs, journals, results, diagnostics, security events or storage. The server forwards set-up messages without reading them and keeps only the relay login name | `shared/protocol/version.json:76`; `cloud-api/dome_api/screen/service.py:5-6`; `cloud-api/dome_api/db/models.py:670-674` |
| Keystrokes and typed text | Passes through the server in memory; never in logs, security events, the PC's journal, the holds file, diagnostics, PC state or the PC database | `docs/SECURITY.md:112` |
| What the person says or types to the assistant, and its answers | Not in the database (the AI ledger has no text column), logs, files, error bodies, security events or diagnostics. The log masker also hides these fields | `cloud-api/dome_api/db/models.py:546-551`; `docs/design/assistant.md:1091-1093`; `cloud-api/dome_api/logging.py:57-72` |
| Voice audio | DoMe never records or uploads audio; it receives text from the iPhone | `mobile-app/src/lib/speech.ts:5-7` |
| Plain IP addresses | No IP column on sessions; keyed hashes only on some events and linking codes; request logs carry no IP; the masker hides addresses in free text | `cloud-api/dome_api/db/models.py:134-148`; `cloud-api/dome_api/security/tokens.py:41-44`; `cloud-api/dome_api/security/headers.py:120-149`; `cloud-api/dome_api/logging.py:209-215` |
| Passwords | Typed on the sign-in provider's pages; the product never sees a password | `docs/adr/0001-foundational-decisions.md:36-39`; `docs/DATA_RETENTION.md:171-172` |
| Card details | Typed only on Stripe's own pages; billing tables hold no card data or email | `docs/LAUNCH_SETUP.md:312-313`; `cloud-api/dome_api/db/models.py:379-381` |
| Pairing codes | Only a hash on the server; on the phone the code is read once from the link, cleared from the address bar and discarded after use | `cloud-api/dome_api/db/models.py:298`; `docs/DATA_RETENTION.md:145` |
| Command parameters, results, media titles | Not stored by the server (lifecycle and hashes only) | `cloud-api/dome_api/db/models.py:1-5,315-339` |
| Raw payment notices from Stripe | Never stored; only a hash | `cloud-api/dome_api/db/models.py:420-421` |
| The Windows computer name and Windows user name | The PC app never reads them; the PC name is what the person types, or "My PC" | Search of `pc-agent/dome_agent` for host-name and user-name calls: none; `pc-agent/dome_agent/link.py:96` |
| Browsing history | The extension runs only on YouTube pages | `browser-extension/public/manifest.json:8,10`; `mobile-app/src/pages/public/PrivacyPage.tsx:82` |
| Location, push notifications | The phone app uses neither | Search of `mobile-app/src`: no geolocation or push code |
| Analytics, advertising, session replay, error-reporting tools | None in the shipped code. The page's security policy lets it load scripts and connect only to its own server; fonts are the device's own | `mobile-app/package.json:18-27`; `cloud-api/dome_api/security/headers.py:19-24`; `mobile-app/src/styles.css:239-241`; `mobile-app/src/pages/public/PrivacyPage.tsx:72` |

## 7. Retention and deletion today

- **No automatic deletion job exists.** Every period marked "proposed" is a statement of intent, not a guarantee
  (`docs/DATA_RETENTION.md:5-9,155-165`; `docs/OPERATIONS.md:408`).
- **What is enforced today:** sign-in attempt clean-up (`cloud-api/dome_api/auth/oidc.py:167-169`), session
  expiry (`cloud-api/dome_api/auth/sessions.py:69-76`), the 62-day Screen view day tables
  (`docs/DATA_RETENTION.md:74-76`), and the PC's own limits: journal 5,000 rows, local events 500, Screen view
  history 20, log rotation (`docs/DATA_RETENTION.md:8-9`; `pc-agent/dome_agent/store.py:652,789,820`).
- **No account deletion feature.** There is no account-deletion route or screen; deletion is a support request. A
  billing-cancel step and its retry record exist for when it is built (`cloud-api/KNOWN_ISSUES.md:110-111`;
  `cloud-api/dome_api/db/models.py:841-852`). The draft Privacy page says so: the app has no delete button and
  accounts are deleted by hand on request (`mobile-app/src/pages/public/PrivacyPage.tsx:168`). It then describes
  what deleting an account would remove as the database is built (`PrivacyPage.tsx:169`).
- **What an account deletion would leave.** Most account rows go with the account (database cascade), but two
  kinds stay without the link to the account: Stripe payment notices (`cloud-api/dome_api/db/models.py:436`)
  and PC linking codes, with the typed code, the PC's name and public key and the keyed IP hash
  (`cloud-api/dome_api/db/models.py:218-235`). The draft Privacy page names both (`PrivacyPage.tsx:169`).
- **Financial records conflict.** If an account row were deleted, its subscription and top-up rows would go
  with it (`cloud-api/dome_api/db/models.py:395-397,494-496`), while `docs/DATA_RETENTION.md:59-60` proposes
  keeping billing records for 7 years. Stripe holds its own records. How long DoMe must keep its own copy is for
  counsel to confirm.
- **Backups and encryption at rest** for both databases, and production backup retention, are "not yet
  verified". No restore has been rehearsed (`docs/DATA_RETENTION.md:37-42,93-95`; `docs/OPERATIONS.md:168-178,403-404`).
- **Operator access** is the founder's direct database access only, to be audited through the hosting provider's
  logs (`docs/DATA_RETENTION.md:30-33`).

## 8. Where the repository's own documents disagree with the code

Counsel may be sent these documents, so the differences are listed here. The draft Privacy page as it stood at
commit 297f991 had four such mismatches (session records holding a "hashed IP", every security event carrying
one, account deletion described as if it existed, and `docs/DATA_RETENTION.md` described as not yet written).
All four are fixed in the draft rewritten on 2026-11-12 (`mobile-app/src/pages/public/PrivacyPage.tsx:31,46,168,227`), so they are no longer
listed here.

| Document and line | What it says | What the code does | Source for the code |
| --- | --- | --- | --- |
| `docs/DATA_RETENTION.md:47` | Sessions hold a "SHA-256 of the session id" | It is a keyed hash | `cloud-api/dome_api/auth/sessions.py:36-37` |
| `docs/DATA_RETENTION.md:48` | Clean-up of expired sign-in attempts is proposed | It is implemented | `cloud-api/dome_api/auth/oidc.py:167-169` |
| `docs/DATA_RETENTION.md:49` | PC row contents | Leaves out protocol versions and the plan-disabled time | `cloud-api/dome_api/db/models.py:176,181-183` |
| `docs/DATA_RETENTION.md:52` | Linking codes hold a "SHA-256 of requester IP" | It is a keyed hash | `cloud-api/dome_api/routes/agent_link.py:60`; `cloud-api/dome_api/security/tokens.py:41-44` |
| `docs/DATA_RETENTION.md:61` | The monthly AI table is the current allowance table | It is no longer written; rolling windows replaced it | `cloud-api/dome_api/db/models.py:507-509` |
| `docs/DATA_RETENTION.md` (whole file) | — | No rows for support requests, AI windows and balances, top-up purchases; the PC files `input_holds.json`, `update-check.json`, `downloads/`, `native-host.log`, lock and socket files; the PC diagnostics' hardware inventory; most phone storage keys | Sections 1, 3 and 4 above |
| `docs/DATA_RETENTION.md:39`; `docs/OPERATIONS.md:172` | Staging holds test data only | Staging holds real sign-ups (at least the founder's own account and a test sign-up); whether anyone else has signed up is not recorded | `docs/PROGRESS.md:7-10`; `docs/LAUNCH_SETUP.md:748-750` |
| `docs/design/assistant.md:1606` (draft consent text) | Counts are kept "to apply your monthly allowance" | The app's own consent sheet says "your allowance", and the code uses rolling 5-hour and weekly windows | `mobile-app/src/components/AssistantConsentSheet.tsx:34`; `cloud-api/KNOWN_ISSUES.md:87-90` |
| `docs/PRODUCT_AND_PLANS.md:31` | Lists "no screen capture" as a principle | Screen view (a live picture of the PC's screen) is built and switched on in staging | `docs/LAUNCH_SETUP.md:470-477,492-494` |

## 9. Questions for counsel about this data

Each of these is for counsel to confirm; none is settled.

1. Does a keyed hash of an IP address, the plain 9-character linking code, or DoMe's account id (which is also
   sent to Cloudflare and Stripe) count as personal information under the laws that apply?
2. What retention period should each record class have, and on what basis? In particular: support requests,
   security events, the command history, and billing records (7 years proposed, yet deleted with the account).
3. Must account deletion be built before launch? The drafts say there is no delete button yet and that
   accounts are deleted by hand on request (`mobile-app/src/pages/public/PrivacyPage.tsx:168`;
   `mobile-app/src/pages/public/TermsPage.tsx:139`).
4. Staging holds real sign-ups (at least the founder's own account and a test sign-up; whether anyone else has
   signed up is not recorded) and runs on a free database plan with a 6-hour restore window
   (`docs/OPERATIONS.md:170-172`). Should staging sign-ups be restricted or covered by the same notice?
5. The server can read commands, typed text, YouTube searches, window titles and the PC's results while routing
   them (section 2). Is the draft Privacy page's disclosure of this enough (`mobile-app/src/pages/public/PrivacyPage.tsx:64,190`)?
6. The PC's `crash.log` may hold paths or addresses and gets no review (section 4). Does any duty attach to it,
   given that DoMe never receives it?
7. Command history, security events and the session list can be read only through the service's API, with no
   app screen (section 1). Does that matter for access requests (`mobile-app/src/pages/public/PrivacyPage.tsx:228`)?

## How this was checked

Every row and bullet above was re-read against the file and line it cites on 2026-11-12, at commit 297f991.
After the Privacy and Terms drafts were rewritten that day, every citation of them was re-pointed to the
rewritten drafts and re-read, and the statements added then (the PC's results in section 2, the cookie's
lifetime, who can see sessions, commands and security events, and what an account deletion would leave) were
checked the same way. A script also confirmed that every cited file exists and every cited line is inside it.
A few PC rows cite `docs/DATA_RETENTION.md` alone: the fact comes from that document, which says the PC's
limits are implemented and unit-tested (`docs/DATA_RETENTION.md:8-9`), and was not traced further into the PC
code unless a second citation is given. "Search" means a repository-wide text search, recorded as the source
where no single line can show an absence. This file contains no outside research.
