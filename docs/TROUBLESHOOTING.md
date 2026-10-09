# DoMe troubleshooting

Status: written on 2026-10-09 from the code in this repository. Every error code below is a stable
code from `shared/protocol/errors.json`; the customer-facing sentence the phone shows for a code is
the `user_message` in that file, and the numbered steps the phone adds underneath come from
`mobile-app/src/lib/labels.ts::recoverySteps`. Where this document says a code "is shown", that is
**unit-tested** (`mobile-app/test/components/Dashboard.test.tsx`, `PairPage.test.tsx`,
`CommandOutcome` through `commands.test.ts`) and **integration-tested** for the codes listed in
`docs/HANDOFF.md` §3 (the real agent process answered them through the real relay on Linux). Nothing
in this document has been **Windows-device-tested** or **iPhone-tested**; the Windows and iPhone steps
describe what the code does, not what anyone has watched happen on a device.

## How DoMe reports problems

| Where | What you see |
| --- | --- |
| Phone, result card | A lifecycle label (*Sent* → *PC received* → *Running on the PC* → *Done* / *Failed* / *Not delivered* / *Expired* / *Cancelled* / *Outcome unknown*), the error's sentence, and the recovery steps. *Not delivered* means the relay refused the command before the PC saw it (`result{origin: relay}`); *Failed* means the PC answered. |
| Phone, status pill | *Online*, *Online · refreshing* (state older than 75 s or just resumed), *Reconnecting*, *Offline · last seen …*, *Not paired with this phone*, *Disabled on your plan*. Consequential controls are disabled unless the pill is *Online*. |
| Phone, offline screen | "You're offline" when the phone itself has no network. Nothing is queued. |
| PC, tray icon | Green = connected, amber = reconnecting, red = remote control disabled or superseded by another agent instance, grey = offline. Hover shows "DoMe — ‹status line›". Notifications: "DoMe cannot connect", "DoMe needs to be re-linked", "DoMe is running elsewhere". |
| PC, console | `DoMe.exe status` (Windows) or `dome-agent status` (development) prints JSON: `identity.linked`, `connection` (`offline` / `connecting` / `connected` / `reconnecting` / `superseded` / `stopped`), `remote_enabled`, `extension_connected`, `entitlement`, `pending_revocations`, `pending_confirmations`, `pending_power`, `notes`, and `configuration_error` when the relay URL is unusable. |
| PC, extension popup | *Connected to the DoMe agent* with the `Browser id`; or *not installed* (`host_missing`), *not registered* (`host_forbidden`), *agent is not running*, *disconnected* (hello not acknowledged within 10 s), *Connecting…*. |
| Account | More → Settings → security activity (`GET /v1/account/security-events`, last 200) lists rejected commands, pairing failures, revocations, sign-ins and throttling with the PC/phone id but never the command content. |

Diagnostics for support are customer-initiated and redacted on both sides: phone More → Settings →
**Download diagnostics** (`mobile-app/src/lib/diagnostics.ts`); PC tray **Diagnostics…** or
`dome-agent diagnostics`, which writes `dome-diagnostics-<timestamp>.json` with the status, the last
500 local security events and the last 200 log lines after a second pass through the redaction
filter (`pc-agent/dome_agent/diagnostics.py`, **unit-tested**:
`tests/test_control_cli_misc.py::test_diagnostics_bundle_is_redacted`). There is no support intake
endpoint yet (`support_diagnostics` table exists, nothing writes to it); send the file to support by
the channel on the Support page, which is "not configured" until `VITE_DOME_SUPPORT_URL` is set.

## Quick index

| Symptom on the phone | Section |
| --- | --- |
| *Offline* / *Reconnecting* pill; `PC_OFFLINE`, `PC_RECONNECTING`, `COMMAND_EXPIRED`, `OUTCOME_UNKNOWN` | [1. The PC agent is offline](#1-the-pc-agent-is-offline) |
| YouTube controls say the extension is not connected; `EXTENSION_DISCONNECTED`, `TAB_NOT_CONTROLLABLE` | [2. Browser extension missing or disconnected](#2-browser-extension-missing-or-disconnected) |
| "No YouTube tab" / `TARGET_GONE` after the browser was closed | [3. Browser closed or no YouTube tab](#3-browser-closed-or-no-youtube-tab) |
| `PAIRING_CODE_INVALID`, `PAIRING_DECLINED`, pairing page stuck | [4. Pairing expired, invalid or declined](#4-pairing-expired-invalid-or-declined) |
| *Not paired with this phone*; `CONTROLLER_REVOKED`, `UNKNOWN_KEY`, `SIGNATURE_INVALID`, `GRANT_MISSING` | [5. This phone was revoked](#5-this-phone-was-revoked) |
| `UNSUPPORTED_CONTEXT`, `ACTIVATION_REQUIRED`, `NO_NEXT_VIDEO`, `ACTION_UNAVAILABLE`, `PLATFORM_UNSUPPORTED`, `APP_NOT_APPROVED`, `PROTOCOL_INCOMPATIBLE` | [6. Unsupported action or context](#6-unsupported-action-or-context) |
| `ENTITLEMENT_PENDING`, `BILLING_PAST_DUE` (future), `ENTITLEMENT_REQUIRED` | [7. Payment pending or past due](#7-payment-pending-or-past-due-phase-c-not-implemented) |
| AI quota | [8. AI quota exhausted](#8-ai-quota-exhausted-phase-e-not-implemented) |
| Microphone | [9. Microphone permission denied](#9-microphone-permission-denied-phase-e-not-implemented) |
| `PC_SESSION_LOCKED`, `PC_SESSION_LOCKED_MEDIA_ONLY` | [10. The PC is locked](#10-the-pc-is-locked) |
| *Disabled on your plan*; `PC_PLAN_DISABLED`, `CONTROLLER_PLAN_DISABLED`, `DEVICE_LIMIT_REACHED` | [11. Plan-disabled device](#11-plan-disabled-device) |
| `CLOCK_SKEW`; commands expire immediately; entitlement "issued in the future" | [12. Clock skew](#12-clock-skew) |
| `CONFIRMATION_INVALID`, `CONFIRMATION_EXPIRED`, `CONFIRMATION_DECLINED`, modal says connection lost | [13. Confirmations](#13-confirmation-problems) |
| "Your session has ended", sign-in loop, `UNAUTHENTICATED` | [14. Sign-in and sessions](#14-sign-in-and-session-problems) |
| Linking the PC fails (`LINK_EXPIRED`, `LINK_DENIED`, `PC_ALREADY_LINKED`, "DoMe needs to be re-linked") | [15. Linking the PC](#15-linking-the-pc-to-the-account) |
| `RATE_LIMITED`, `QUEUE_FULL`, `INTERNAL` | [16. Throttling and service errors](#16-throttling-and-service-errors) |

---

## 1. The PC agent is offline

**What the phone shows.** Status pill *Offline · Last seen … Commands are not stored for later*, or
*Reconnecting*; every consequential control is disabled (**unit-tested**:
`Dashboard.test.tsx` "offline PC … disables every consequential control"). A command sent in the
gap is answered at once by the relay with `PC_OFFLINE` ("The PC is not connected right now. Nothing
was sent.") — it is never queued (**integration-tested**:
`tests/test_e2e_reliability.py::test_offline_pc_never_queues`,
`cloud-api/tests/test_relay_routing.py::test_offline_pc_is_never_queued`).

**Related codes.**

| Code | Meaning | Who answers |
| --- | --- | --- |
| `PC_OFFLINE` | No agent socket for this PC. | relay |
| `PC_RECONNECTING` | The agent is connected but has not applied its `grants_snapshot` yet; try again in a moment. | PC (`authz.py` step 1) |
| `COMMAND_EXPIRED` | The command's 30 s window (90 s for confirmed actions) passed before the PC checked it, or the relay's in-flight deadline fired before any `executing` ack. Nothing ran. | PC or relay sweeper |
| `OUTCOME_UNKNOWN` | The agent's socket dropped (or the agent crashed) after it reported *Running on the PC*. The action may or may not have happened. If the agent later reconnects with a journaled result, the phone replaces the outcome once (`rules.late_results`). | relay, corrected by PC |

**Steps.**

1. On the PC, look at the tray icon. Grey or amber means the agent is not connected; red means
   remote control is switched off (section 6 of `docs/WINDOWS_INSTALL.md`) or the agent was
   superseded.
2. Run `DoMe.exe status` (or `dome-agent status`). Read:
   - `identity.linked: false` → the PC was never linked or was unlinked; run `DoMe.exe link`
     (section 15).
   - `configuration_error` non-empty → the relay or API URL is unusable ("CONFIGURATION ERROR: The
     relay URL is invalid…"). Unset `DOME_AGENT_RELAY_URL` / `DOME_AGENT_API_URL` if you set them; the
     credential is kept and no re-link is needed (**unit-tested**:
     `tests/test_relay_client.py::test_invalid_relay_url_is_configuration_error_not_credential_rejected`).
   - `connection: superseded` → another agent instance connected for the same PC (the relay closed
     this one with code 4001) and this one deliberately stopped reconnecting. Quit the duplicate, then
     tray → **Reconnect** or `dome-agent reconnect` (**unit-tested**:
     `tests/test_agent_e2e.py::test_superseded_4001_requires_manual_reconnect`).
   - `connection: stopped` with the notification "DoMe needs to be re-linked" → the relay rejected
     the PC credential (close 4003 or HTTP 401/403 on `/v1/agent/token`), or `hello_ack` named a
     different `pc_id`. The PC was unlinked from the account or re-linked elsewhere. Run
     `DoMe.exe link` again; pairings are **not** inherited (`cloud-api/DECISIONS.md` #2).
   - `connection: reconnecting` for more than a minute → the agent cannot reach
     `wss://<origin>/ws/agent`. Backoff is 1 s → 60 s with full jitter and never stops by itself.
     Check the PC's internet connection, any corporate proxy that blocks WebSockets, and (operator)
     `GET /healthz` on the service (`docs/OPERATIONS.md`).
3. If the PC is asleep or shut down, there is nothing DoMe can do: there is no Wake-on-LAN in V1
   (`docs/PRODUCT_AND_PLANS.md` §5). The phone's last-seen time is the honest state.
4. Once the pill is *Online*, send the command again. For `OUTCOME_UNKNOWN`, first look at the
   dashboard's current state (the agent sends a fresh `state` frame within 30 s of reconnecting) so
   you do not, for example, skip two videos.

**Operator notes.** A service restart drops every agent and controller socket. The start-up sweep
marks in-flight command rows `outcome_unknown` / `expired` (`cloud-api/tests/test_relay_lifecycle.py::
test_startup_sweep_closes_rows_from_a_previous_process`); agents reconnect within their backoff and
phones re-subscribe. Expect a burst of `agent_connected` security events, not customer action.

## 2. Browser extension missing or disconnected

**What the phone shows.** On the Remote and Command pages the YouTube controls are withheld and the
page explains that the PC reports no browser extension (`state.extension_connected: false`, PWA
`targets.ts` reason `no_extension`). A YouTube command that still reaches the PC is answered
`EXTENSION_DISCONNECTED` ("DoMe's browser extension is not connected on the PC. Open Chrome or Edge
and check the extension.") (**integration-tested**: `tests/test_e2e_youtube.py::
test_target_identity_is_preserved` covers the disconnected bridge; **unit-tested**:
`pc-agent/tests/test_authz.py::test_availability_extension_disconnected`).

`EXTENSION_NOT_INSTALLED` exists in `errors.json` and the phone has copy for it, but **no component
emits it today**: the agent cannot distinguish "never installed" from "not connected right now", so
both appear as `EXTENSION_DISCONNECTED`. Treat the two sections of steps below as one.

**Steps on the PC.**

1. Open Chrome or Edge and click the DoMe toolbar icon. The popup tells you which link is broken:
   - *not installed* (`host_missing`): the native-messaging host is not registered for this browser.
     Run `DoMe.exe install-native-host` (per user, no elevation; section 4.3 of
     `docs/WINDOWS_INSTALL.md`), then click **Retry** in the popup or restart the browser.
   - *not registered* (`host_forbidden`): the extension's id is not in the host manifest's
     `allowed_origins`. In a development build set `DOME_AGENT_DEV_EXTENSION_ID=<32-letter id from
     chrome://extensions>` and run `install-native-host` again. In a release build the id must be in
     `PRODUCTION_EXTENSION_IDS` (empty until the store listing exists —
     `docs/WINDOWS_INSTALL.md` §9).
   - *agent is not running*: the host started but found no agent IPC endpoint. Start DoMe (tray icon
     visible) and wait; the extension retries with backoff 1 → 60 s on its own and a
     `chrome.alarms` safety net survives worker termination (**unit-tested**:
     `browser-extension/test/background.test.ts` "backs off 1→2→4 s…", "AGENT_NOT_RUNNING … retried").
   - *disconnected* after *Connecting…*: the host or agent did not acknowledge `bridge_hello` within
     10 s. The popup cannot tell which; `dome-native-host` logs do. The reconnect loop recovers either
     way (`browser-extension/KNOWN_ISSUES.md` K4).
2. `dome-agent status` → `extension_connected: true` confirms the agent side.
3. Tabs opened before the extension was installed or enabled have no content script. The phone lists
   them as not controllable and a command fails with `TAB_NOT_CONTROLLABLE` ("Reload the YouTube tab
   on the PC so DoMe can control it."). Reload the tab; the new attachment gets a new `tab_token`.
4. If the extension was removed, reinstall it (development: load `browser-extension/dist` unpacked;
   store listing: Phase D) and repeat step 1.

**What is and is not verified.** The handshake with a real `dome-native-host.exe`, Chrome's
behaviour on worker termination and the popup wording in a real browser are **not yet verified**;
see `browser-extension/README.md` manual checklist items 1 and 8.

## 3. Browser closed or no YouTube tab

**What the phone shows.** The Remote shows no tab to control (`targets.ts` reasons `no_tabs` /
`none_attached`); YouTube buttons are withheld rather than failing. If a command targets a tab that
has since been closed or navigated away from YouTube, the PC answers `TARGET_GONE` ("That tab or
window is no longer there.") and does nothing else (**integration-tested**:
`tests/test_e2e_youtube.py::test_target_identity_is_preserved`; **unit-tested**:
`browser-extension/test/background.test.ts` "TARGET_GONE for a closed tab or a tab that left YouTube").

`BROWSER_NOT_RUNNING` is defined in `errors.json` and the phone has copy for it, but **nothing emits
it today**: when the browser is closed the extension's service worker is gone too, so the agent sees
"extension disconnected" (section 2), and when the browser is open without YouTube tabs the phone
sees an empty tab list.

**Steps.**

1. On the PC open Chrome or Edge and a `youtube.com/watch` page. Within a second the agent's next
   `state` frame lists it (tab list changes are debounced 300 ms in the extension, state frames
   500 ms in the agent) and the phone's Remote enables.
2. If you expected a different tab: the Remote's tab picker lists every YouTube tab per browser
   profile ("Chrome", "Edge", plus the profile label). With two or more playing tabs you must pick
   one (`TARGET_AMBIGUOUS`, "More than one video is playing. Choose a tab."); with none playing and
   several open, the phone asks (`TARGET_REQUIRED`) (**integration-tested**:
   `tests/test_e2e_youtube.py::test_pause_seek_player_volume_and_two_tabs`).
3. `TARGET_CHANGED` ("The page changed before the action ran. Nothing was done.") means the tab was
   reloaded or moved to another video between your tap and the action; refresh and tap again.
4. Windows media sessions (Spotify and other players) are controlled through the Media controls and
   do not need the browser; the extension is YouTube only.

## 4. Pairing expired, invalid or declined

The pairing code is generated on the PC, lasts **5 minutes** and works **once**; the service only
ever sees its SHA-256 (`version.json → rules.pairing_secret`). Both devices compute the same 6-digit
verification code; the PC owner approves on the PC (`docs/IPHONE_SETUP.md` §3).

| What the phone shows | Cause | Steps |
| --- | --- | --- |
| `PAIRING_CODE_INVALID` ("That pairing code is not valid or has expired. Get a new code from the PC.") or the pairing page reports an invalid/expired code after a `404` while waiting | Code expired (5 min), already used, mistyped, generated by a PC on **another account**, or the PC started a new pairing (which expires the previous open session) | On the PC choose **Pair a phone…** again (or `dome-agent pair`), then scan or type the new code within 5 minutes. Make sure the phone is signed in to the same account that linked the PC. **integration-tested**: `cloud-api/tests/test_pairing.py::test_wrong_code_expired_code_and_reuse_are_invalid`, `test_other_account_cannot_claim_or_see`, `tests/test_e2e_security.py::test_expired_code_is_rejected`; **unit-tested**: `PairPage.test.tsx` "a 404 while waiting is shown as an invalid/expired code with its recovery steps" |
| `PAIRING_DECLINED` ("Pairing was declined on the PC.") | The PC owner pressed Decline, or the verification codes did not match | Start again from the PC. If the codes differed, do not approve: it means the two devices are not talking about the same key. Report it. |
| The phone keeps "Waiting for approval on the PC" | The PC shows the request only while the agent is running; a claim made while the PC was offline is delivered when the agent next connects (**integration-tested**: `tests/test_e2e_security.py::test_claim_while_pc_offline_is_delivered_on_reconnect`) | Make sure the agent is connected (section 1) and look at the PC's pairing window or console. Approve within the same 5 minutes; afterwards the claim expires with the code. |
| `DEVICE_LIMIT_REACHED` / "Your plan's device limit has been reached" at claim | Free allows 2 paired phones per account | Revoke a phone you no longer use under Devices, or wait for Pro (Phase C). **integration-tested**: `cloud-api/tests/test_pairing.py::test_controller_limit_is_enforced_at_claim` |
| `RATE_LIMITED` (HTTP 429) on the pairing page | More than 5 claims in 15 minutes from this account or IP; each failure is also a `pairing_failed` security event | Wait 15 minutes. If you did not make those attempts, review security activity and consider revoking sessions (section 14). |
| Scanning the QR sends you to sign in, and afterwards the pairing page asks you to scan again | By design: the code is carried only in the URL fragment and is never stored on the phone while the browser leaves for the identity provider (`mobile-app/KNOWN_ISSUES.md` #4) | Scan the same QR again if it is still within 5 minutes; otherwise get a new code. |
| Camera does not open on iPhone | Permission denied or not a secure context | Type the 4×5 code instead; camera behaviour on a real iPhone is **not yet verified** (`docs/IPHONE_SETUP.md` §9). |

An account recovery (new sign-in) never approves a controller: approval only happens on the PC
(**integration-tested**: `tests/test_e2e_security.py::test_pairing_requires_pc_approval_and_codes_are_single_use`).

## 5. This phone was revoked

**What the phone shows.** The socket receives a `revoked` frame and is closed with code 4003; the
status pill turns to *Not paired with this phone* and commands are refused with one of
`CONTROLLER_REVOKED` ("Access for this phone was revoked. Pair again from the PC to restore it."),
`UNKNOWN_KEY`, `SIGNATURE_INVALID` or `GRANT_MISSING` ("This phone does not have permission for that
action on this PC."). The steps shown are the same for all four: pair again from the PC
(**integration-tested**: `tests/test_e2e_security.py::test_account_revocation_terminates_live_control`,
`test_local_emergency_disable_and_local_revoke`; `cloud-api/tests/test_relay_lifecycle.py::
test_rest_revocation_closes_sockets_and_resnapshots`; **unit-tested**: `mobile-app/test/relay.test.ts`
"close code 4003 …").

**Where the revocation came from.**

| Path | Effect |
| --- | --- |
| Devices → phone → **Revoke** on any signed-in device, or `DELETE /v1/controllers/{id}` | Service closes the phone's sockets, pushes a new `grants_snapshot` to the PC; the PC marks the controller revoked locally and cancels its queued, awaiting-confirmation and armed-countdown commands. An executing OS call is not interrupted (`pc-agent/KNOWN_ISSUES.md` #2). |
| Revoking one **grant** (Devices → PC → permissions) | Only that phone→PC pair loses access; other PCs keep working (`test_grant_revocation_is_scoped_to_one_pc`). |
| On the PC: `DoMe.exe revoke <controller_id>` or the pairing window | Immediate locally; the relay is told with `revoke_controller`, re-sent after the next `grants_snapshot` if the PC was offline (`tests/test_agent_e2e.py::test_local_revocation_while_offline_reaches_relay_on_reconnect`). |
| Unlinking the PC (Devices → PC → Unlink) | Every grant for that PC is revoked and subscribers see the PC disappear (`test_unlink_pc_revokes_everything`). |
| Forgetting the installation on the phone (More → Settings → **Forget this installation**) | Deletes the phone's key; the controller row stays until revoked. Revoke it from Devices too. |

**Steps.**

1. If you revoked it yourself: nothing to fix. To restore access, pair again from the PC
   (section 4). The phone keeps its key, so the PC will show the same `kid` prefix; the account will
   list it as a new controller.
2. If you did not: open More → Settings → security activity and look for `controller_revoked`,
   `grant_revoked` or `pc_unlinked`, and for sign-ins you do not recognise. Revoke other sessions
   (section 14) and change the password at your identity provider. Pair again only after that.
3. A revocation always wins over a plan state and is never paywalled (`plans.json → downgrade_policy`).

## 6. Unsupported action or context

These are honest refusals, not faults. Nothing ran unless the table says so.

| Code | Sentence shown | What it means / what to do |
| --- | --- | --- |
| `UNSUPPORTED_CONTEXT` | "This control is not available for the current page (for example an ad, a live stream, or Shorts)." | During an ad only pause, mute and volume work and the ad is never skipped; on Shorts no Next/Previous/Theater; on a live stream no seeking. Wait for the ad or open a normal video. **integration-tested**: `tests/test_e2e_youtube.py::test_end_of_queue_ad_and_fullscreen_are_reported_honestly`; **unit-tested**: `browser-extension/test/player-adapter.test.ts` guards. Live/ad/Shorts detection against the real youtube.com DOM is **not yet verified** (`browser-extension/KNOWN_ISSUES.md` K1). |
| `ACTIVATION_REQUIRED` | "Fullscreen must be started on the PC itself; the browser blocks remote fullscreen." | Browsers require a user gesture on the PC. Use Theater mode from the phone or press F on the PC. |
| `NO_NEXT_VIDEO` / `NO_PREVIOUS_VIDEO` | "There is no next/previous video available." | End of the playlist or a plain watch page. |
| `TARGET_REQUIRED` / `TARGET_AMBIGUOUS` | "Choose which tab or player to control." / "More than one video is playing. Choose a tab." | Pick from the list (section 3). |
| `ACTION_UNAVAILABLE` | "That control is not available right now." | The media session or player does not offer that control (for example a Spotify session without `next`), or an internal availability condition the agent does not recognise. |
| `UNKNOWN_ACTION` / `INVALID_PARAMETERS` | "That action is not supported by this PC." / "Those settings are not valid for this action." | The agent or phone is on a different registry version; update both. Typed commands never send unknown actions (`intents.ts` only produces registry actions). |
| `PLATFORM_UNSUPPORTED` | "This action is only available on Windows." | The agent is running on Linux/macOS (development run, `docs/WINDOWS_INSTALL.md` §8): only YouTube works there. |
| `APP_NOT_APPROVED` | "That app is not approved for remote control. Approve it on the PC first." | Approve the executable on the PC: `DoMe.exe approve-app <id> "<absolute path>.exe"`. Only existing `.exe` files outside temp folders are accepted and the file's SHA-256 is pinned; after an app update you must approve it again (`APP_LAUNCH_FAILED` with a changed-executable detail). **unit-tested**: `pc-agent/tests/test_approved_apps.py`, `test_handlers.py::test_launch_refuses_changed_executable`. |
| `APP_NOT_RUNNING`, `FOCUS_DENIED`, `CLOSE_REFUSED` | see `errors.json` | The app is not open; Windows refused to bring it to the front (the app *is* running); the app did not close, probably asking about unsaved work on the PC — DoMe never force-kills. Windows behaviour **not yet verified** on a device. |
| `MEDIA_SESSION_GONE` | "That media player is no longer available." | The player closed. Refresh the list. |
| `PROTOCOL_INCOMPATIBLE` | "One of your DoMe components needs an update before they can talk to each other." | The frame names both version lists. Update the component that is behind: agent (`DoMe.exe --version`), extension, or reload the web app. The relay must always be at least as new as the clients (`docs/OPERATIONS.md` §11). |
| `ENTITLEMENT_REQUIRED` | "This is a DoMe Pro feature." | A routine-origin command or Pro-only feature on a Free account; see section 7. |

Text commands the parser does not understand are not sent at all: the Command page shows a
clarification ("Which window?", "YouTube or the PC?") or a refusal for out-of-range values and for
anything that looks like a path, argument or shell text (**unit-tested**:
`mobile-app/test/intents.test.ts` "rejections and clarifications", "injection resistance").

## 7. Payment pending or past due (Phase C, not implemented)

**Today there is no billing.** `GET /v1/plans` reports `billing_enabled: false`, the Billing page
shows the current plan and its limits with no checkout, and no component emits `ENTITLEMENT_PENDING`
or `BILLING_PAST_DUE` (they are defined in `errors.json` and the phone already has copy for them so
that the Phase C rollout does not need a client change):

- `ENTITLEMENT_PENDING` — "Your payment is being confirmed. Pro features unlock automatically once it
  is verified." Steps shown: no action needed; check Billing in a few minutes.
- `BILLING_PAST_DUE` — "Your last payment did not go through. Update your payment method to keep DoMe
  Pro." Steps shown: update the payment method from Billing; Free features keep working.

The designed behaviour (7-day past-due grace, downgrade selection window, what is never paywalled)
is in `docs/BILLING.md` §8 and is **not yet verified** in any form. What exists and works now:

- `ENTITLEMENT_REQUIRED` from the PC for routine-origin commands on a Free account, regardless of
  what the client claims (**integration-tested**: `tests/test_e2e_security.py::test_client_state_cannot_unlock_pro`).
- Plan limits on devices (section 11).

If a customer reports a payment problem before Phase C ships, no charge can have been made by DoMe.

## 8. AI quota exhausted (Phase E, not implemented)

DoMe has **no AI feature**: `plans.json` fixes `ai_interpretations_per_period` and
`ai_transcription_minutes_per_period` at 0 for both plans, `DOME_AI_ENABLED` defaults to false, no
provider adapter exists and `errors.json` has no AI quota code yet. Typed commands are parsed
deterministically on the phone (`intents.ts`) and never leave the device as free text.

Planned behaviour when Phase E ships (from `docs/PRODUCT_AND_PLANS.md` §3.2 and spec §13): explicit
consent, a metered allowance per usage period (`usage_periods` table exists, empty), a clear "AI
allowance used up — typed commands keep working" state, and a fallback to the deterministic parser.
Until then there is nothing to recover from; if a customer asks why "AI" did not understand them,
the answer is that no AI is involved and the Command page's clarification is the parser asking for
a more specific instruction.

## 9. Microphone permission denied (Phase E, not implemented)

DoMe does **not** request the microphone. The PWA's `Permissions-Policy` from the service allows only
`camera=(self)` (for QR scanning); there is no `getUserMedia` or Web Speech code in `mobile-app/src`.
Voice input today is the iOS keyboard's own dictation into the Command field, which is a system
feature with its own permission (Support page copy: "DoMe uses your keyboard's dictation; check that
dictation is enabled in iOS Settings → General → Keyboard. Typing a command works exactly the same
way.").

When push-to-talk ships (Phase E) the spec requires a tested fallback for permission denial and for
browsers without a supported speech API; the recovery copy will be added to `labels.ts` then. There
is no code and no evidence for it now.

## 10. The PC is locked

**What the phone shows.** `PC_SESSION_LOCKED` ("The PC is locked. Unlock it on the PC to
continue.") for actions that need the interactive session (apps, YouTube, theater, …), or
`PC_SESSION_LOCKED_MEDIA_ONLY` ("The PC is locked. Only media controls are allowed until it is
unlocked.") when the PC allows media while locked. Steps shown: unlock Windows on the PC; media while
locked can be allowed in the DoMe settings on the PC.

**How it works.** Every action in the registry declares availability conditions; `session_unlocked`
refuses while locked, `session_media_allowed` refuses only when the PC's `media_while_locked` setting
is off (`pc-agent/dome_agent/authz.py::check_availability`, re-checked right before execution;
**unit-tested**: `tests/test_authz.py::test_availability_session_locked`). Lock state is read with
`WTSQuerySessionInformationW(WTSSessionInfoEx)` with an `OpenInputDesktop` fallback
(`platform/windows/session.py`) — **not yet verified on Windows 10/11**; if a device reports the wrong
state, that module is the place to look. `windows.lock` itself is always allowed and is how a phone
locks the PC on purpose.

**Steps.**

1. Unlock the PC (Windows sign-in). The agent's next `state` frame (within 30 s, sooner on change)
   updates the phone.
2. To allow play/pause/volume while locked: the `media_while_locked` setting exists in the agent's
   store but has **no tray or CLI toggle yet** (`docs/WINDOWS_INSTALL.md` §9); it stays off, which is
   the safe default.
3. Power actions (`power.sleep`, `power.restart`, `power.shutdown`) still require the phone's
   confirmation and are not blocked by the lock state; `power.cancel` works while locked.

## 11. Plan-disabled device

**What the phone shows.** Status pill *Disabled on your plan · Enable it under Devices (plan limits
apply)*; a command is refused by the relay with `PC_PLAN_DISABLED` ("This PC is not enabled on your
current plan. Choose it in Devices or upgrade to DoMe Pro.") and, independently, by the PC if it ever
reached it. `CONTROLLER_PLAN_DISABLED` is the same state for a phone. `DEVICE_LIMIT_REACHED` is
reported when a link or pairing would exceed the limit (the PC is still linked, but disabled).
Steps shown: your plan limits how many PCs and phones are enabled; choose which stay enabled under
Devices, or upgrade when available.

**Facts.** Free: 1 enabled PC, 2 paired phones; Pro (not purchasable yet): 5 and 5
(`shared/protocol/plans.json`, design assumptions). A second PC on Free links successfully but
`enabled: false` (**integration-tested**: `tests/test_e2e_security.py::test_second_pc_is_limited_on_free`,
`cloud-api/tests/test_agent_link.py::test_second_pc_exceeds_free_limit_but_is_created_disabled`).
Disabling is not revocation: local grants stay, `Disable remote control` and revocation keep working,
and nothing is deleted (`version.json → rules.plan_state`;
`cloud-api/tests/test_relay_lifecycle.py::test_plan_disabled_pc_is_refused_but_keeps_grants`).

**Steps.**

1. Devices → the PC you want → **Enable** (`PATCH /v1/pcs/{id} {enabled: true}`). The service
   refuses with `DEVICE_LIMIT_REACHED` while another PC is enabled; disable that one first. The
   change is transactional, so two phones racing cannot both succeed.
2. For phones: after a downgrade the service keeps the most recently seen controllers enabled
   automatically (`downgrade_policy.default_selection`); an explicit chooser and the 14-day
   selection window are Phase C. Today the way to change the selection is to revoke a phone you do
   not need (**integration-tested**: `cloud-api/tests/test_entitlement_and_misc.py::test_plan_disabled_controllers_after_downgrade`).
3. The PC's `dome-agent status` shows `pc_enabled` from the last snapshot; the tray stays green
   because the connection itself is healthy.

## 12. Clock skew

**What the phone shows.** `CLOCK_SKEW` ("The PC's clock looks wrong. Check the time settings on the
PC."), or commands that fail immediately with `COMMAND_EXPIRED` although the PC is online, or
confirmations that are always `CONFIRMATION_EXPIRED`.

**How it works.** The PC checks `issued_at`/`expires_at` once when it receives a command, with a
tolerance of **5 seconds** (`version.json → limits.max_clock_skew_seconds`): `issued_at` more than
5 s in the future → `CLOCK_SKEW`; `expires_at` more than 5 s in the past → `COMMAND_EXPIRED`
(`shared/python/dome_protocol/timeutil.py`; **unit-tested**:
`pc-agent/tests/test_authz.py::test_expired_and_future_commands`). The phone stamps commands with its
own clock and a 30 s lifetime (90 s for confirmed actions), so:

| Symptom | Likely cause |
| --- | --- |
| `CLOCK_SKEW` on every command | PC clock is **behind** the phone by more than 5 s |
| `COMMAND_EXPIRED` immediately, PC online | PC clock is **ahead** of the phone by more than ~30 s |
| `CONFIRMATION_EXPIRED` right after approving | Either clock off by more than the 60 s challenge window allows |
| Agent log "entitlement assertion issued in the future", `entitlement.grace_active` | PC clock behind the service |
| Phone signed out unexpectedly, cookies rejected | Phone clock far off (session expiry is server-side; a very wrong phone clock mostly affects TLS) |

**Steps.**

1. On the PC: Settings → Time & Language → **Sync now** (or `w32tm /resync` in an elevated prompt).
   Windows normally keeps time within a second; a drift of 5 s indicates time sync is off or the
   CMOS battery is failing.
2. On the phone: Settings → General → Date & Time → **Set Automatically**.
3. Send the command again. No re-pairing or re-linking is needed; nothing about keys depends on the
   clock.
4. The relay does not check the command window itself (it checks its own in-flight deadline), so a
   correct service clock cannot cause this; the two devices must agree with each other. Service
   clock problems show up instead as rejected OIDC tokens at sign-in (`docs/OPERATIONS.md` §10).

## 13. Confirmation problems

Disruptive actions (power, closing apps, …) require a signed confirmation from the same phone within
**60 seconds** of the PC's challenge (`docs/PROTOCOL.md` §5).

| What the phone shows | Cause | Steps |
| --- | --- | --- |
| `CONFIRMATION_EXPIRED` ("The confirmation timed out. Start again.") | More than 60 s passed before Approve reached the PC | Send the action again and approve within a minute. |
| `CONFIRMATION_INVALID` ("The confirmation did not match. Start again.") | The approval did not match the challenge the PC issued: another phone tried to approve, the challenge text was altered, or a stale modal | Only the phone that sent the command can approve (`confirmations.test.ts` "binding"). Dismiss and start again. |
| `CONFIRMATION_DECLINED` ("You declined this action.") | You pressed Decline | Nothing ran. |
| Modal says the connection was lost and offers only **Close** | The socket dropped while the challenge was pending | Nothing runs unless approved; the challenge expires on the PC within 60 s. Close, wait for *Online*, and send the action again. Approving after a reconnect is **not verified** end to end (`mobile-app/KNOWN_ISSUES.md` #1). |
| Only **Decline** is offered | The challenge does not bind to a command this phone sent (for example another controller's power request appeared in the state) | Expected; you can refuse but not approve someone else's action. |
| `POWER_CANCELED` / `power.cancel` reports `canceled: false` | The countdown was cancelled in time / the OS call had already been issued | The PC reports what Windows actually did; after Windows accepted a shutdown with `dwTimeout = 0` an abort almost always fails (`pc-agent/KNOWN_ISSUES.md` #1). |
| "requested … DoMe cannot tell whether it ran" on the dashboard | The PC disconnected right after a power request and this phone saw no `executing` ack | Honest unknown: lost connectivity is never presented as proof of shutdown (`mobile-app/test/power.test.ts`). Check the PC. |

## 14. Sign-in and session problems

| Symptom | Cause | Steps |
| --- | --- | --- |
| "Your session has ended. Sign in again." (`UNAUTHENTICATED`, REST 401 or socket close 4008) | Session expired (30 days idle, 90 days absolute), was revoked from another device, or the operator rotated `DOME_SESSION_SECRET` | Sign in again. Paired PCs stay paired; the phone's key is kept (`controllerKey.test.ts`). |
| Sign-in page returns HTTP 503 | The identity provider is unreachable (OIDC discovery/token exchange failed) | Service-side; existing sessions keep working. See `docs/OPERATIONS.md` §10. |
| Sign-in loops back to the start | `return_to` was not a same-origin relative path, or cookies are blocked (the session cookie is HttpOnly, SameSite, `Secure` on https) | Allow cookies for the DoMe origin; open the app from its own origin, not from a wrapper. |
| Another device signed in that you do not recognise | — | More → Settings → sessions → revoke it (`DELETE /v1/account/sessions/{id}`); revoking closes that session's sockets at once (`cloud-api/tests/test_relay_lifecycle.py::test_logout_closes_controller_sockets`). Change the password at the identity provider. |
| After **Sign out** the screen says "Signing out" for a long time | The navigation to `/` was blocked | Reload the page (`mobile-app/KNOWN_ISSUES.md` #8). |
| Storage cleared / "Forget this installation" | The phone's key is gone; the account lists the old controller until revoked | Pair again (section 4) and revoke the stale controller under Devices (`docs/IPHONE_SETUP.md` §5). |

## 15. Linking the PC to the account

`DoMe.exe link` prints a user code and opens `https://<origin>/link?…`; the signed-in customer
approves it; the agent polls and receives its credential (`docs/ARCHITECTURE.md` §4.2). Codes last
**10 minutes**.

| Symptom | Cause | Steps |
| --- | --- | --- |
| `LINK_EXPIRED` ("The link code expired or was already used. Start again on the PC.") | 10 minutes passed, or the code was already decided | Run `DoMe.exe link` again. |
| `LINK_DENIED` | You pressed Deny | Run `link` again if that was a mistake. |
| `PC_ALREADY_LINKED` (HTTP 409) at approval | The PC's key is already linked in an account | Unlink the old entry under Devices (or on the PC `DoMe.exe unlink`), then link again. A code naming a linked PC's key is refused so a copied public key cannot take over a PC (`cloud-api/tests/test_agent_link.py::test_copied_public_key_cannot_take_over_a_linked_pc`). |
| HTTP 429 when entering a code | More than 10 failed code look-ups in 15 minutes from this account or IP (each is a `link_code_lookup_failed` security event) | Wait 15 minutes; type the code exactly (it is case-insensitive). |
| "Note: this PC is not enabled on your plan yet" after linking | Device limit (section 11) | The link succeeded; enable it under Devices. |
| Tray: "DoMe needs to be re-linked" | Credential rejected or identity mismatch (section 1) | `DoMe.exe link`; approve; pair phones again. |
| `link` fails with "set DOME_AGENT_API_URL" | Development run without the API URL | Export `DOME_AGENT_API_URL` (`docs/WINDOWS_INSTALL.md` §8). |

## 16. Throttling and service errors

| Code | Where | Limit as implemented | Steps |
| --- | --- | --- | --- |
| `RATE_LIMITED` on commands | relay, per controller | 120 manual commands/min (burst 30); 360/min (burst 60) for slider actions; the phone itself sends at most one slider command per 250 ms plus the release value (`VolumeSlider.test.tsx`) | Wait a moment. Superseded slider values terminate as `COMMAND_SUPERSEDED` ("Replaced by a newer value."), which is normal. |
| `RATE_LIMITED` then socket closed (4000) | relay, per socket | 600 frames/min, burst 120, before any database work; after 120 refusals the socket is closed with one `controller_throttled` event | The app reconnects with backoff. If this happens without a flood, collect diagnostics. |
| `QUEUE_FULL` | relay / PC, per PC | 16 in-flight commands | Earlier commands are still running; wait. |
| HTTP 503 on connect | relay | `DOME_RELAY_MAX_CONNECTIONS` (default 5000) reached | Operator capacity issue (`docs/OPERATIONS.md` §9). |
| `PAYLOAD_TOO_LARGE` / socket closed 1009 | relay / PC | 16 KiB payload, 64 KiB frame | Not reachable from the shipped phone app; indicates a modified client. |
| `MALFORMED_MESSAGE` | any | A frame failed strict validation | Reload the app / update the agent; a persistent case is a bug — collect diagnostics. |
| `INTERNAL` ("Something went wrong on our side. Try again.") | service / PC | Unexpected exception; the request log line has the route and a request id, never the payload | Try again; report with the time and the error from diagnostics. |

## Evidence summary for this document

| Claim group | Tag |
| --- | --- |
| The codes, sentences and recovery steps exist as described and are rendered on the phone | unit-tested (`mobile-app/test`) |
| The agent and relay answer the codes in sections 1–6, 10–13, 15–16 as described | integration-tested on Linux with the fake platform and fake extension (`tests/`, `cloud-api/tests`, `pc-agent/tests`) |
| Tray colours, notifications, native-host registration states, Windows lock detection, `w32tm`, popup wording in a real browser | not yet verified (code read only) |
| iPhone camera, Home Screen, storage-cleared behaviour | not yet verified |
| Sections 7, 8, 9 (billing, AI, microphone) | not implemented; copy exists only where stated |

Note on the working tree: at the time of writing, `shared/protocol/version.json`, `errors.json` and
`relay-frames.schema.json` carry uncommitted changes (controller `hello` proof of possession, a
stricter re-send-on-reconnect rule, and a reworded `OUTCOME_UNKNOWN` sentence) that the components
have not been updated for yet. Quote codes, not sentences, when filing issues.
