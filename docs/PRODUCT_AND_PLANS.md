# DoMe product and plans

Status: pre-release engineering build. Nothing is publicly available, no live billing exists, and
no Windows PC or iPhone has yet run this code. Every feature below carries an evidence tag:

- `unit-tested` — component tests on Linux (pytest / Vitest) with test doubles.
- `integration-tested` — the real cloud-api, the real agent process and signed controllers on Linux
  with a fake Windows platform and a fake extension (`tests/`).
- `sandbox-provider-tested` — exercised against a vendor's test environment (none yet).
- `Windows-device-tested` / `iPhone-tested` — exercised on real hardware (none yet).
- `load-tested` — only the small loopback smoke in `tests/test_load_smoke.py` (numbers, not a promise).
- `not yet verified` — implemented against real APIs but unproven, or not implemented.

## 1. Principles

From `docs/spec/MASTER_PROMPT.md` §2, in the order the code enforces them:

1. Free is useful indefinitely and needs no credit card. Everything that makes the product safe
   (authentication, TLS, revocation, emergency stop, security activity, permissions, security fixes)
   is on every plan and is never a sales lever.
2. Charge for convenience, customisation, more computers and costly optional services, never for
   responsiveness, and never with advertisements, artificial delays or daily paywalls.
3. A button works without an LLM. Typed commands are parsed deterministically on the phone; no AI
   service is in the execution path.
4. Display actual results from the PC. "Sent" is not "received", "received" is not "done"; when the
   PC vanishes mid-command the phone says *outcome unknown*, not failed and not succeeded.
5. The PC never listens on the internet. It opens one outbound connection; the phone never sends a
   path, argument, script or shell text; the agent rejects anything outside the action registry.
6. No hidden remote access, no login bypass, no screen capture, no autonomous agent. Disruptive
   actions need a signed confirmation of the exact action; the local "Disable remote control" switch
   wins over everything remote.
7. Minimise setup: sign in, name the PC, scan a code. No port forwarding, VPN, Python or tokens.
8. Build in stages; a feature is finished only when its execution path works with evidence.

## 2. Free and Pro

The plan contract lives in one file, `shared/protocol/plans.json`, read by cloud-api (`plans.py`),
the PWA pricing table (`mobile-app/src/lib/pricing.ts`) and the entitlement assertion. These are
**design assumptions, not validated prices**; nothing is purchasable today.

| Capability | DoMe Free | DoMe Pro | Status |
| --- | --- | --- | --- |
| Linked PCs that can be controlled | 1 | up to 5 | limits enforced transactionally — integration-tested |
| Paired phones (controller installations) per account | up to 2 | up to 5 | integration-tested |
| Ordinary manual controls | included; published abuse limits 120 commands/min (burst 30), sliders 360/min (burst 60) | same limits, same responsiveness | integration-tested |
| Secure away-from-home access through the managed relay | included | included | integration-tested on loopback; cellular/home-internet path not yet verified |
| YouTube controls, Windows media sessions, system volume | included | included | see §3 |
| Approved app launch / focus / minimise / close | included | included | see §3 |
| Lock, and confirmed sleep / restart / shutdown with a cancellable countdown | included | included | see §3 |
| Typed commands and keyboard dictation (deterministic) | included | included | unit-tested |
| Basic remote layout | included | included | unit-tested (components) |
| Custom layouts, shortcuts, named profiles | — | at paid launch | not implemented (tables exist) |
| Saved routines (up to 10 steps, 60 s) using approved, non-disruptive actions | — | at paid launch | gate implemented and integration-tested (`ENTITLEMENT_REQUIRED`); routine CRUD/UI not implemented |
| Permissions, revocation, session management, security activity | included | included | integration-tested |
| Optional cloud AI interpretation and push-to-talk transcription | not offered | not offered until the Phase E gate (allowance 0 in `plans.json`) | not implemented |
| Price | free | US$5.99/month or US$49.99/year (pricing experiment inputs) | no checkout exists |

Account means one owner, not a shared household login. A controller installation is one browser or
home-screen PWA installation; clearing its site data deletes its key and requires pairing again
(there is no recovery shortcut by design).

When Pro ends, settings are kept, nothing is deleted, 1 PC and 2 phones remain enabled (the most
recently seen by default; an explicit selection within 14 days is planned), and revocation and
emergency stop keep working for every paired device. Details: `docs/BILLING.md` §8.

## 3. Released versus planned features

"Released" below means *implemented in this repository and tested as tagged*; it does not mean
publicly available.

### 3.1 Implemented

| Feature | Where | Evidence |
| --- | --- | --- |
| Sign-in through a standard OpenID Connect issuer (Authorization Code + PKCE); server-side sessions, HttpOnly cookie, CSRF header + exact Origin | cloud-api | integration-tested with `tools/dev-idp`; Auth0/Keycloak configuration not yet verified |
| PC linking without the password on the PC (device-code flow in the system browser), transactional device limit, PC named and remote control explicitly enabled by the customer | cloud-api, pc-agent, PWA `/link` | integration-tested (real `dome-agent link`) |
| Pairing a phone: PC-generated 20-symbol code / QR, backend sees only a hash, 6-digit verification code computed on both devices, approval on the PC, 5-minute expiry, single use, rate-limited; works when the PC is offline (delivered on reconnect) | all components | integration-tested; camera scanning on a real iPhone not yet verified |
| Signed command envelopes (ES256 over exact bytes), strict parsing, per-controller grants, replay/duplicate rejection, expiry windows, target binding (`tab_token`, expected video id) | shared, all components | unit-tested in Python and TypeScript with cross-language fixtures; integration-tested end to end |
| Truthful lifecycle: created → PC received → running → succeeded / failed / expired / cancelled / outcome unknown; relay-side rejections are results, never silent drops; agent crash mid-command yields *outcome unknown* and never re-executes | all components | integration-tested (kill the agent between ack and result) |
| YouTube: play/pause, next (verified by observing the video transition), previous, ±10 s and seek-to, mute and player volume separate from Windows volume, theater, fullscreen (reports `ACTIVATION_REQUIRED` when the browser refuses), tab list with browser/profile identity, background-tab control, ads/live/Shorts/playlist detection with honest `UNSUPPORTED_CONTEXT` | browser-extension, pc-agent, PWA | unit-tested against hand-written DOM fixtures; integration-tested through a fake extension; **selectors not verified against live youtube.com** (KNOWN_ISSUES K1); no real browser session yet |
| Windows media sessions (play/pause/next/previous only when the session advertises them), system volume get/set/mute with read-back, lock | pc-agent | unit-tested with a fake platform; **not Windows-device-tested** |
| Approved apps: locally approved absolute `.exe` paths with pinned hash; launch/focus/minimise; graceful close with a confirmation and `CLOSE_REFUSED` when the app keeps a dialog open; never force-killed; `FOCUS_DENIED` reported honestly | pc-agent | unit-tested; not Windows-device-tested |
| Confirmed power actions: sleep/restart/shutdown behind a signed confirmation of the exact action, cancellable countdown, no forced close of unsaved work, result reports what Windows accepted | pc-agent, PWA | integration-tested on the fake platform (nothing powers off); the deliberate hardware test is pending |
| Deterministic typed commands ("Skip this video", "Set my PC volume to 35 percent", "Lock my computer", numbers in words, clarification for ambiguity, injection text rejected) | PWA | unit-tested |
| Devices and permissions: PC inventory with online/reconnecting/offline and last seen, rename, enable/disable, unlink; phones with revoke; grants per PC; session list and revoke; security activity | cloud-api, PWA | integration-tested |
| Revocation terminates live control; local emergency "Disable remote control"; a reconnecting PC applies the revocation snapshot before accepting commands; a snapshot can never add a controller or widen a grant | all components | integration-tested |
| Plan gates in the service and the agent: device limits, plan-disabled devices, Pro entitlement assertions (EdDSA, 1 h, 72 h offline grace), "editing client state cannot unlock Pro" | cloud-api, pc-agent | integration-tested |
| Abuse limits: per-controller command rate limits, per-PC queue depth 16, frame-size and connection caps, login/link/pairing/token limits, bounded security-event writes | cloud-api | integration-tested |
| PWA: installable shell (iPhone-first dark theme, light mode, 44 px targets, safe areas, reduced motion), offline screen, stale-state disabling, confirmation modal rendered from the registry, two clearly labelled volume controls, Pro preview without checkout, public pages (landing, pricing from `plans.json`, FAQ, download page with disabled links, privacy and terms **drafts**) | mobile-app | unit-tested (205 tests); not iPhone-tested; Lighthouse/PWA audit pending |
| Redacted structured logs on the service and the agent; customer-initiated redacted diagnostics bundles | cloud-api, pc-agent, PWA | unit-tested |
| Tray agent with status, enable/disable, pair, approved apps, start-at-login, diagnostics; native-messaging host registration per user without elevation; PyInstaller specs | pc-agent | code exists; **PyInstaller build and every tray/Windows path not yet verified on Windows** |
| Development identity provider, Makefile, lockfiles, migrations, `.env.example` | repo | runs in CI-like Linux use |

### 3.2 Planned, with the stage it belongs to

| Feature | Stage | What exists today |
| --- | --- | --- |
| Pro checkout and Customer Portal (Stripe test mode first), subscription state machine, webhook reconciliation, cancellation, downgrade selection window, account deletion | Phase C (paid launch) | Database tables, env settings, `GET /v1/plans` with `billing_enabled: false`, automatic default selection, design in `docs/BILLING.md` |
| Custom layouts and named profiles | Phase C | `layouts` table |
| One-tap routines (≤ 10 steps, ≤ 60 s, approved non-disruptive actions, stop on error, per-step results) | Phase C | `routines` table, agent-side `ENTITLEMENT_REQUIRED` gate, PWA preview cards |
| Operator interface (health, billing reconciliation, plan configuration, redacted support records) with audited actions; operators can suspend but never execute PC commands | Phase C/D | `operator_users`, `operator_audit`, `support_diagnostics` tables |
| Signed Windows installer, update manifest with downgrade protection, staged rollout, uninstall cleanup | Phase D | PyInstaller specs and manual uninstall steps |
| Chrome Web Store / Edge Add-ons listings (production extension id pinned in the agent) | Phase D | Unpacked developer loading only; `PRODUCTION_EXTENSION_IDS` empty |
| Production identity provider (Auth0 or Keycloak), production hosting, backups/restore rehearsal, monitoring, incident runbooks | Phase D | ADR-0001 decisions, `cloud-api/README.md` settings; `deploy/` is a placeholder |
| Real-device evidence: Windows 10/11 and iPhone Safari checklists | Phase B/D acceptance | Checklists in `pc-agent/README.md`, `mobile-app/README.md`, `browser-extension/README.md` |
| Activation-funnel instrumentation (install, connected, paired, extension connected, first verified command, repeat use, upgrade, cancellation) | Phase D | `activation_events` table; no events emitted |
| Optional cloud AI interpretation and push-to-talk transcription with explicit consent, metering, quotas, spending ceiling, prompt-injection tests | Phase E (separate release gate) | `usage_periods` table, allowances fixed at 0, env settings declared |
| Narrow keyboard/trackpad control, optional clipboard, consented screen previews, schedules, guest access with separate identities, supported Wake-on-LAN | Later expansion | Nothing; the action registry and grant model leave room |
| Horizontal relay scaling (Redis pub/sub) | When a second relay replica is demonstrably needed | Single-process design; seam documented in ADR-0001 D1 |
| End-to-end payload encryption between phone and PC | Later, separately reviewed, with a maintained standard protocol | Not started; privacy copy states the relay can read routed payloads |

## 4. Roadmap stages (spec §4 and §18)

| Stage | Goal | State |
| --- | --- | --- |
| A — foundation | Secure relay/agent/extension path, one real phone-to-PC YouTube action | Code complete; Next/Pause proven end to end only with a fake extension and fake platform on Linux |
| B — Free beta | Pairing/revocation, native extension integration, all core controls, deterministic text, onboarding, truthful states, permission/replay tests, development installer, real-device evidence | Code complete except packaging; real-device evidence outstanding |
| C — paid launch | Multi-PC limits (done), layouts, routines, billing lifecycle, deletion, cost model (done: `docs/COST_MODEL.md`) | Design only for billing/layouts/routines |
| D — public release preparation | Signed distribution and updates, privacy/help/pricing pages (drafts exist), monitoring, store submissions, backup/restore, support flows, load checks | Not started beyond drafts and this documentation |
| E — optional AI/voice | Provider adapter, consent, metering, quotas, real iPhone microphone behaviour | Not started |

## 5. What is deliberately not promised

- **Not end-to-end encrypted, not zero-knowledge.** Commands are signed, which authenticates them;
  the relay can read them in transit. A compromised browser origin, browser profile or PC can
  compromise control even though controller keys are non-extractable.
- **No offline or LAN mode.** The relay needs internet on both sides; sharing Wi-Fi does not make
  local control work.
- **No remote power-on.** Wake-on-LAN needs hardware, configuration and an awake device on the LAN;
  it is a future feature. Losing connectivity after a shutdown request is reported as "accepted by
  Windows; PC disconnected", never as "shutdown verified".
- **No screen viewing, trackpad, keyboard, clipboard, scheduling, guest access, native iOS/Android
  apps, plugin marketplace or autonomous desktop agent** in V1.
- **No AI in the product today**, and when it comes it will be optional, consented, metered, with no
  automatic overage; typed commands and buttons never depend on it.
- **No arbitrary executables, arguments, shell, PowerShell, registry edits, downloaded code or
  remote-updated extension logic.** Apps are approved locally by path and hash; the extension has one
  host permission (`https://www.youtube.com/*`) and no network access.
- **No ad or paywall bypass, no browsing history collection, no sale of activity, no session-replay
  or advertising scripts on authenticated screens.**
- **No support or administrator backdoor.** Knowing the password or being an operator cannot enrol
  a controller; pairing is approved on the PC.
- **No claimed verification we do not have.** Nothing has been run on a Windows PC or an iPhone; no
  Stripe sandbox evidence exists; latency targets (median < 500 ms, p95 < 1.5 s) are targets to
  measure, not results; the load smoke prints numbers for 40 + 40 loopback sockets only.
- **No proven pricing.** US$5.99 / US$49.99 and the plan limits are inputs to an experiment; the
  cost model (`docs/COST_MODEL.md`) shows the service runs at a loss below roughly 1,000 active
  accounts at a 3 % conversion under its assumptions.
- **No fabricated testimonials, customer counts, screenshots, certifications or legal compliance
  claims.** The privacy and terms pages are drafts awaiting founder and legal review.

## 6. Support boundaries at launch (planned copy, not live)

Supported YouTube contexts at launch: ordinary watch pages (video on demand) in Chrome or Edge on
Windows with the DoMe extension installed, including playlists; ads, live streams and Shorts are
detected and refuse unsupported controls honestly; YouTube Music is out of scope (no host
permission). Supported phones: iPhone Safari as an installed PWA first; other modern mobile
browsers should work but are untested. Windows 10/11 with a signed-in interactive user session;
locked sessions refuse app and lock actions and allow media only when the customer enabled "media
while locked" on the PC.
