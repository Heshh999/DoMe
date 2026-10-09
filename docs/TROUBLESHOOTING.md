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
| PC, console | `DoMe.exe status` (Windows) or `dome-agent status` (development) prints JSON: `identity.linked`, `connection` (`offline` / `connecting` / `connected` / `reconnecting` / `superseded` / `stopped`), `remote_enabled`, `extension_connected`, `entitlement`, `pending_revocations`, `pending_confirmations`, `pending_power`, `notes`, and `configuration_error` when the relay URL is unusable. Protocol 1.1 adds `input` (the manual-input session, its counters and held input), `pending_grant_updates` and an `INSTANCE:` line for single-instance problems (section 20). |
| PC, extension popup | *Connected to the DoMe agent* with the `Browser id`; or *not installed* (`host_missing`), *not registered* (`host_forbidden`), *agent is not running*, *disconnected* (hello not acknowledged within 10 s), *Connecting…*. |
| Phone, Connection health | More → **Connection health** (`/app/health`): seven layers checked separately, one next action each, bounded retries (section 17). |
| Phone, Touchpad pill | *Connecting…*, *Connected*, *Live · waiting for the PC*, *Live · Windows accepted N* (Windows acceptance, never an app effect), *Paused*, *Ended*, *Not connected*; "Held on the PC: …" while the PC reports held input (section 18). |
| PC, tray menu | *Stop manual input* ends the live touchpad/keyboard session and releases its held input; *Paired phones ▸ ‹phone› ▸ Allow touchpad / Allow keyboard*. |
| Account | More → Settings → security activity (`GET /v1/account/security-events`, last 200) lists rejected commands, pairing failures, revocations, sign-ins and throttling with the PC/phone id but never the command content. |

Diagnostics for support are customer-initiated and redacted on both sides: phone More → Settings →
**Download diagnostics** (`mobile-app/src/lib/diagnostics.ts`), or **Attach redacted diagnostics…** in
the Support form, which shows exactly what would be sent first; PC tray **Diagnostics…** or
`dome-agent diagnostics`, which writes `dome-diagnostics-<timestamp>.json` with the status, the last
500 local security events and the last 200 log lines after a second pass through the redaction
filter (`pc-agent/dome_agent/diagnostics.py`, **unit-tested**:
`tests/test_control_cli_misc.py::test_diagnostics_bundle_is_redacted`). Support requests go through
the in-app form (`POST /v1/support/tickets`), which returns a reference after receipt; the PC bundle is
a local file that DoMe does not upload. See section 22 and `docs/SUPPORT.md`.

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
| Connection health screen: what each row means and what to do | [17. Connection health screen](#17-connection-health-screen-states-and-the-one-next-action) |
| Stuck drag, cursor does not move, typing goes nowhere, *Live typing paused*; `INPUT_*` codes | [18. Touchpad and keyboard](#18-touchpad-and-keyboard) |
| PC asleep or shut down after a power request; "can DoMe wake it?" | [19. Power-state limits](#19-power-state-limits-no-remote-wake) |
| "already running", "not responding", "already runs for this Windows account", `dome-agent repair` | [20. Single instance and repair](#20-single-instance-and-repair-messages-pc) |
| No download link, setup stopped part-way, SmartScreen warning | [21. Download and setup repair](#21-download-and-setup-repair) |
| Sending a support request, reference ids, "Not sent" / "Not confirmed" | [22. Getting support](#22-getting-support) |

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
   (`docs/PRODUCT_AND_PLANS.md` §5, section 19). The phone's last-seen time is the honest state.
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

## 17. Connection health screen: states and the one next action

More → **Connection health** (`/app/health`), also linked from every status pill, the reconnect banner
and every failure (**Check connection health**). The screen checks seven layers separately
(`mobile-app/src/lib/health.ts::assessHealth`) and says: "Each row below is checked separately. A green
row never implies the others work." It diagnoses only what the phone can observe; an unreachable PC has
an unknown cause. Codes, versions and timings sit behind **Show technical details (codes, versions)**.
**Unit- and component-tested** (`mobile-app/test/health.test.ts`,
`test/components/UpgradeAndHealth.test.tsx`); **not iPhone-tested**.

| Layer | State shown (summary) | The one next action |
| --- | --- | --- |
| This phone | "Your phone reports no internet connection." | **Reconnect this phone** (turn on Wi-Fi or cellular) |
| This phone | "Connecting to the DoMe service…" / "Not connected to the DoMe service." | **Reconnect this phone** |
| This phone | "This phone's pairing was revoked. Pair it again from the PC." | **Pair this phone with the PC** (§5) |
| This phone | "This app and the DoMe service speak different protocol versions…" (`PROTOCOL_INCOMPATIBLE`) | **Reconnect this phone** after reloading the app; update the PC agent |
| Your account | "Your sign-in has ended or could not be verified. Paired PCs stay paired." | **Sign in again** (§14) |
| Your PC | "No PC is linked to your account." | **Link a PC to your account** (opens Download, §21) |
| Your PC | "This phone is not paired with this PC." / "…not paired with any PC yet." | **Pair this phone with the PC** (§4) |
| Your PC | "This PC is disabled on your plan." | **Enable it under Devices** (§11) |
| Your PC | "The PC's connection to DoMe dropped and has not come back yet. DoMe cannot tell whether the PC is asleep, off or without internet." | **Open DoMe on the PC** (§1) |
| Your PC | "The PC is not connected to DoMe. The cause is unknown from here: it may be asleep, shut down, offline, or the DoMe agent may not be running." | **Open DoMe on the PC** (§1, §19) |
| Your PC | "The PC is connected; waiting for its current state." | none — wait a moment |
| Remote control on the PC | "Switched off on the PC. Remote commands can never switch it back on." | **Enable remote control on the PC** (tray, `docs/WINDOWS_INSTALL.md` §4.8) |
| Remote control on the PC | "Windows is locked. … Touchpad and keyboard never work on the lock screen." | **Unlock Windows on the PC** (§10) |
| Touchpad and keyboard permission | "Not granted for this phone. The PC owner allows it on the PC; existing pairings do not get it automatically." | **Allow touchpad/keyboard on the PC** (§18, `docs/WINDOWS_INSTALL.md` §4.9) |
| Touchpad and keyboard permission | "Granted, but Windows is showing a protected screen (lock, sign-in or an administrator prompt) where remote input is refused by design." | **Unlock Windows on the PC** / finish the prompt at the PC |
| Touchpad and keyboard permission | "… allowed. Another phone currently holds the PC's input session." | none — use **Take over** on the Touchpad tab if intended |
| Browser extension | "The DoMe extension is not connected on the PC. Needed only for YouTube controls; Windows media, volume, apps and the touchpad work without it." | **Install or update the extension on the PC** (§2) |
| Media target | "More than one player is active; DoMe does not guess which one you mean." | **Choose a media target** (Remote) |
| Media target | "A YouTube tab is open but the extension is not attached to it; reload that tab on the PC." | **Open a YouTube tab on the PC** (reload it, §2 step 3) |
| Media target | "No YouTube tab or media player is active on the PC." / "No media player is active; YouTube needs the extension." | **Open a YouTube tab on the PC** / **Install or update the extension on the PC** (§3) |

Rows whose state is not known yet say "Not known until the PC reports its state." rather than guessing.

Also on the screen:

- **Retry** re-checks this phone's connection and asks the PC for its current state. "It never re-sends
  a command: an action whose outcome is unknown stays unknown until the PC reports." Retries are bounded
  to **3 per visit** (`mobile-app/src/pages/app/HealthPage.tsx::MAX_RETRIES`); after that the screen says "Retrying further will
  not change anything by itself. Check the PC directly, then contact support with your redacted
  diagnostics." (§22).
- **Last verified result**: the last command on this PC for which the phone saw the PC's terminal result.
- **First-use walkthrough** (sign in → link the PC → pair → PC online with remote control on → choose
  what to control → one action with its result): "Continue at step N" returns you to the first undone
  step instead of restarting.
- **What DoMe needs (V1)**: internet on both devices; the Windows app running in a signed-in session;
  remote control on locally; touchpad/keyboard permission for manual input; Chrome or Edge with the
  extension for YouTube; Windows unlocked. "DoMe cannot wake or power on a PC remotely in this version."

## 18. Touchpad and keyboard

How it works and what is verified: `docs/INPUT_CONTROL.md`. **Nothing in this section is
Windows-device-tested or iPhone-tested**; the codes and recovery steps are unit-/component-tested on
the phone and integration-tested on the agent and relay with a fake input adapter.

Agent-side rejections now reach the phone: the agent sends `error{…, ref_input_session_id,
ref_controller_id}` and the relay delivers it to that controller only (`rules.input_sessions`). Each
rejection is also counted in the next `input_ack.dropped_events`. When the touchpad shows a notice, its
sentence is the code's `user_message` and the steps come from `labels.ts::recoverySteps`.

### 18.1 Symptoms

| Symptom | Likely cause | Steps |
| --- | --- | --- |
| **Stuck drag** — the PC still seems to hold the left button | A drag lock is on (Drag mode keeps the hold after you lift the finger by design), or the phone lost its connection mid-drag | 1. Press **End Drag** (or **Stop Input**). 2. If the phone is disconnected, wait: the PC's 3 s lease ends the session and releases exactly what it pressed. 3. On the PC, tray → **Stop manual input** or `DoMe.exe stop-input` releases it immediately ("Manual input stopped; released N held button(s)/key(s)."). 4. If the agent crashed, starting DoMe again releases the recorded holds. The phone's "Held on the PC: left button" line shows what the PC reported holding. |
| **The cursor does not move** | Session not live (pill *Not connected*, *Ended*, *Paused*); no `pointer` permission (keyboard-only grant); the PC is locked or on a secure desktop; the touchpad is disabled because the PC is offline | Read the pill and the notice. **Start again** for an ended session. For permission, see `INPUT_NOT_PERMITTED` below. Check Health (§17). If the pill says "Live · Windows accepted N" and N grows but nothing moves on screen, Windows accepted the events: check the right PC is selected and look for an elevated window in front (§18.2, `INPUT_RESTRICTED`). |
| **Cursor moves too fast or too slowly** | Sensitivity, plus Windows pointer acceleration ("Enhance pointer precision") | Touchpad → **Settings** → Sensitivity (0.5–3×, saved on this phone only). Acceleration on the PC is Windows' own setting; DoMe cannot compensate exactly (`pc-agent/KNOWN_ISSUES.md` #6). |
| **Scrolling goes the wrong way** | Scroll direction preference | Touchpad → **Settings** → *Natural (content follows fingers)* or *Standard (wheel-like)*. |
| **Two taps give two single clicks instead of a double click** | The clicks reached the PC further apart than Windows' double-click time (slow or jittery connection) | Use the **Double** button, which sends a true double click in one call (`mobile-app/KNOWN_ISSUES.md` #9). |
| **Typing goes nowhere** | No field has keyboard focus on the PC, the wrong window is in front, the session is keyboard-less (pointer-only grant), or an elevated window is in front | 1. Click the field on the PC with the touchpad first — DoMe cannot see which field has focus inside a window. 2. Check the window shown at the top of the keyboard panel. 3. If the pill shows "Windows accepted N" but nothing appears, the application did not take the input: an elevated (administrator) window drops it silently (§18.2). 4. Try **Compose and Send** — it reports whether Windows accepted the text. |
| **"Live typing paused"** | An edit could not be mirrored safely (a correction in the middle, deleting an emoji or accented cluster), or DoMe lost certainty about what reached the PC (rejected batch, dropped events, the session ended or restarted) | Expected and safe: nothing was sent and nothing was deleted on the PC. Fix the text on the PC, or use **Compose and Send** for the rest, then tap **Type live** to continue (`mobile-app/KNOWN_ISSUES.md` #10, #18). |
| **Compose and Send says "DoMe cannot tell whether this text reached the PC"** | No acknowledgement within 3 s, the PC reported dropped input, or the session ended first | Look at the PC. If the text is there, **Discard**; if not, **Send again**. DoMe never resends on its own. |
| **A line break in Compose and Send submitted a form** | Line breaks and tabs in composed text are typed as the Enter and Tab keys | Remove line breaks before sending, or send the lines separately. |
| **The Address bar (Ctrl+L) button is missing** | Shown only while Chrome or Edge is the window in front (`foreground_app.browser`) | Bring the browser to the front on the PC (click it with the touchpad). |
| **"Another phone is using the touchpad on this PC"** | One phone owns a PC's input session at a time | **Take over** ends the other phone's session (its held input is released first), or **Wait, try again**. |
| **The touchpad stops after the phone was locked or the app was in the background** | iOS suspends the page; the session is stopped on hide and the PC's lease ends it | Expected. Return to the Touchpad tab and press **Start touchpad on ‹PC›** (or **Start again**). Nothing you did while away is sent later. |

### 18.2 `INPUT_*` codes

| Code | Sentence (`errors.json`) | Who reports it | What to do |
| --- | --- | --- | --- |
| `INPUT_NOT_PERMITTED` | "This phone does not have touchpad or keyboard permission on this PC. The PC owner grants it on the PC." | relay (grant does not cover the event types) or PC (session flags); also the end reason `grant_removed` | On the PC: tray → **Paired phones ▸ ‹phone› ▸ Allow touchpad / Allow keyboard**, or `DoMe.exe grant <controller_id> --pointer --keyboard`. Existing pairings never get these automatically. Then open the touchpad again. |
| `INPUT_SESSION_OWNED` | "Another phone is using the touchpad on this PC. Take over to continue." | PC, at session start | **Take over** or wait. |
| `INPUT_SESSION_REQUIRED` | "Start the touchpad or keyboard on this PC first." | PC | **Start again**. Nothing done while disconnected is sent later. |
| `INPUT_SESSION_EXPIRED` | "The touchpad connection timed out. Tap the touchpad again to reconnect it." | PC (a retired session id, usually after the 3 s lease ran out) | **Start again**. If it happens while you are actively using the touchpad, check the connection under Health. |
| `INPUT_SEQUENCE_INVALID` | "Some input arrived out of order and was dropped. Continue; nothing was replayed." | relay (same `seq` twice on one socket) or PC | Keep going; if it repeats, reload the app. |
| `INPUT_STALE` | "Input was delayed too long to deliver safely and was discarded. Try again." | relay (outside the batch's 5 s window) or PC (older than the 1 s age budget after clock-offset correction) | Repeat the gesture or keystroke. Frequent `INPUT_STALE` means a slow or congested connection — check Health; on cellular, move to better coverage. A phone clock a few seconds off is compensated (`docs/INPUT_CONTROL.md` §5.5); a phone clock stepped backwards mid-session can cause it for up to 30 s — **Stop Input** and start again (`pc-agent/KNOWN_ISSUES.md` #14). |
| `INPUT_SUSPENDED` | "Input paused because the connection fell behind. Start the touchpad again." | PC (dispatch fell behind; pending input discarded, holds released) | **Start again**. Old movement or text is never played back. |
| `INPUT_RESTRICTED` | "Windows is showing a protected screen (lock screen, sign-in or an administrator prompt). The phone cannot type or click there." | PC | Finish that screen at the PC. With an elevated window in front, keyboard input is refused while the session stays live; click a normal window with the touchpad to continue. DoMe never bypasses UAC or the lock screen. |
| `INPUT_INJECTION_FAILED` | "Windows did not accept that input." | PC (`SendInput` inserted fewer events than requested) | Click a normal window and try again; an elevated window in front is the usual cause. The rest of that batch was dropped so nothing ran out of order. If it persists, send diagnostics (§22). |
| `INPUT_TARGET_CHANGED` | "The window in front of the PC changed, so typing stopped. Check the PC and continue." | PC (foreground window differs from the one captured at start or at your last click) | Look at the PC, click where you want to type, continue. Typing issued at least 2 s after the change continues without a click. |
| `RATE_LIMITED` (on input) | "Too many requests. Wait a moment and try again." | relay (40 batches/s, burst 80 per phone; the same for Free and Pro) | Not reachable by normal use; a sustained flood closes the socket (4000) and the app reconnects. |
| `NO_ANSWER` (phone only) | "The PC did not answer in time." | phone (the touchpad start got no answer) | Check the PC shows Online under Health, then **Start again**. |

A session that ends shows its reason with the matching sentence: `lease_expired` → `INPUT_SESSION_EXPIRED`;
`takeover` → "Another phone took over this PC's touchpad."; `controller_revoked` → `CONTROLLER_REVOKED`;
`grant_removed` → `INPUT_NOT_PERMITTED`; `session_locked` → `PC_SESSION_LOCKED`; `secure_desktop` →
`INPUT_RESTRICTED`; `remote_disabled` → `PC_REMOTE_DISABLED`; `controller_disconnected` / `agent_restart` →
`PC_RECONNECTING`; `stopped` → `INPUT_SESSION_REQUIRED`. When the PC released holds, the notice adds
"The PC released N held button(s)/key(s)." (`mobile-app/src/lib/input.ts::endCode`).

## 19. Power-state limits: no remote wake

Sleep, restart and shutdown are always confirmed on the phone. Every confirmation shows the fixed copy
"This can interrupt or end remote access to the PC. DoMe cannot wake or power it on again remotely in
this version, so you will need to be at the PC to restore access." (`mobile-app/src/lib/power.ts`) and
the PC's own detail "‹N› s countdown, cancellable from the phone. ‹Sleeping|Restarting|Shutting down›
can interrupt or end remote access to this PC. DoMe cannot wake it or turn it on again remotely (no
remote wake in V1)." (`pc-agent/dome_agent/authz.py::power_confirmation_detail`) (**component-tested** /
**unit-tested**; not on a device).

| Situation | What DoMe can and cannot do |
| --- | --- |
| The PC went to sleep or shut down after a confirmed request | Nothing remotely. There is no Wake-on-LAN or remote power-on in V1; it needs compatible hardware, configuration and usually an awake device on the PC's network, and is a future feature. Someone at the PC must wake or start it, sign in, and (unless Start at login is on) start DoMe. |
| The PC is unreachable | The phone shows *Offline · last seen …* and, when one exists, the recently requested power action ("requested … DoMe cannot tell whether it ran" unless this phone saw the PC start it). Connectivity cannot prove whether the PC is asleep, off or disconnected; DoMe never presents lost connectivity as proof of shutdown (§13). |
| A restart finished | The agent reconnects only if it starts at login (tray **Start at login**) and Windows signs the user in; DoMe does not bypass the sign-in screen. |
| A manual-input session was live | It ends when the agent disconnects or the PC locks; held input is released by the PC where Windows permits, or at the agent's next start-up (`docs/INPUT_CONTROL.md` §8). |
| Cancel | `power.cancel` works during the agent's countdown; after Windows accepted the request, cancellation is reported honestly and usually fails (§13, `pc-agent/KNOWN_ISSUES.md` #1). |

## 20. Single instance and repair messages (PC)

DoMe runs one agent per Windows user session and, because the identity is per Windows account, at most
one per account. Starting it again never starts a competitor and never terminates another process
(`pc-agent/dome_agent/single_instance.py`; **unit-/integration-tested** on Linux with `flock`; the
Windows named mutex `Local\DoMe.Agent.<session id>` and `ProcessIdToSessionId` are **not yet
verified**).

| Message (`DoMe.exe` / `dome-agent run`, `status` → `INSTANCE:`) | Meaning | Steps |
| --- | --- | --- |
| "DoMe is already running in this session (pid N); its window was brought up. Nothing else started." | A second launch reached the running agent, which showed its window (tray notification "DoMe is already running"). Exit code 0. | Nothing to fix. |
| "Another DoMe instance is running but not responding (pid N). It may still be starting; if this persists, quit it from the tray (or sign out and back in) and run `dome-agent repair`." | The instance lock is held but the local control channel did not answer. Exit code 1. | Wait a few seconds and try again. If it persists, quit DoMe from the tray (or end it yourself in Task Manager — DoMe will not), then `DoMe.exe repair`. |
| "DoMe already runs for this Windows account in session N (pid M). Both sessions share this account's DoMe identity, so only one agent can run: use DoMe from that session, or quit it there (tray → Quit) and start it here." | Another Windows session of the same account (Remote Desktop, a second sign-in) runs the agent. Exit code 1. | Use that session, or quit DoMe there first. Detection relies on the pid file (`pc-agent/KNOWN_ISSUES.md` #10). |
| "Permission problem: cannot write to the state directory … Run `dome-agent repair` or fix the folder permissions." | `%LOCALAPPDATA%\DoMe` is not writable for this user | Restore your own account's write access to that folder, then `DoMe.exe repair`. |
| "Stale control endpoint: a previous DoMe did not shut down cleanly. `dome-agent repair` removes it." | Left over from a crash | `DoMe.exe repair`. |
| "Stale pid file from a previous DoMe (no such process). Harmless; `dome-agent repair` cleans it up." | Left over from a crash | Optional `DoMe.exe repair`. |
| "No DoMe agent is running in this session." | — | Start DoMe. |

`DoMe.exe repair [--host-path <dome-native-host.exe>]` prints one line per step and ends with "Repair
finished. Pairing, grants and approved apps were preserved; nothing was terminated." It checks the state
directory is writable ("state directory writable: ok" or "NOT FIXED: …" and stops), leaves a running
agent alone ("running agent found (pid N); left running"), refuses to touch an unresponsive one ("…
NOT killed — quit it from the tray or sign out/in, then run repair again"), removes stale local
endpoints and a stale pid file, reports `identity.json`, `state.sqlite3` and the secrets as "present,
untouched", rewrites and re-registers the native-messaging manifest, and says when it could not ("native
host executable not found at …; manifest not re-registered (pass --host-path)", "native host manifest
NOT written: …"). Repair never re-pairs or re-approves anything: a new phone still needs the local
approval on the PC (**integration-tested**: `pc-agent/tests/test_single_instance.py::test_repair_preserves_identity_credential_grants_and_apps`).

## 21. Download and setup repair

There is **no official Windows download yet**. The Download page says "Release status: not yet
published" and offers no link; there is no unsigned build or beta to download. Development installs follow `docs/WINDOWS_INSTALL.md` §4.

| Problem | Steps |
| --- | --- |
| Windows SmartScreen or antivirus warns about a development build | Development builds are unsigned (`docs/WINDOWS_INSTALL.md` §9). Do not disable antivirus, SmartScreen or any other protection; release builds will be signed. Use a development build only on a machine you control. |
| Setup stopped at a step | The phone's first-use walkthrough (Health, §17) returns you to the first undone step; nothing is restarted. On the PC each step is a separate command: `DoMe.exe link` (§15), `install-native-host` (§2), `DoMe.exe` to run, tray **Pair a phone…** (§4). |
| The browser extension cannot reach the agent after setup | `DoMe.exe repair` re-registers the native-messaging host (§20); then reload the extension or restart the browser (§2). |
| The agent will not start or says another instance runs | §20. |
| Linking fails | §15. |
| You want to start over | `docs/WINDOWS_INSTALL.md` §5 (uninstall) removes everything DoMe created; then install again. Pairings are not inherited after a re-link. |
| The mouse on the PC is broken | Pairing and approval need one usable local input on the PC. The pairing approval window is a standard window; whether every step is fully keyboard-navigable has **not yet been verified** on Windows. The console path works by keyboard alone: `DoMe.exe pair` shows the code and prompts for approval in the console, including explicit touchpad/keyboard prompts. |

## 22. Getting support

Every failure has **Get help**, which opens the Support page with the category and code preselected. A
signed-in request returns a reference (`DM-` and 8 characters) after it is stored; if sending fails,
the page says "Not sent" or "Not confirmed", never "received", and offers a copyable redacted summary.
Diagnostics are attached only after you review them and never contain pairing codes, credentials,
typed text, command text, media titles or URLs. Full behaviour, redaction rules and what support staff
can and cannot do: `docs/SUPPORT.md`.

## Evidence summary for this document

| Claim group | Tag |
| --- | --- |
| The codes, sentences and recovery steps exist as described and are rendered on the phone | unit-tested (`mobile-app/test`) |
| The agent and relay answer the codes in sections 1–6, 10–13, 15–16 as described | integration-tested on Linux with the fake platform and fake extension (`tests/`, `cloud-api/tests`, `pc-agent/tests`) |
| Tray colours, notifications, native-host registration states, Windows lock detection, `w32tm`, popup wording in a real browser | not yet verified (code read only) |
| iPhone camera, Home Screen, storage-cleared behaviour | not yet verified |
| Sections 7, 8, 9 (billing, AI, microphone) | not implemented; copy exists only where stated |
| Section 17: health layers, next actions, retry bound, walkthrough | unit-tested / component-tested (`mobile-app/test/health.test.ts`, `test/components/UpgradeAndHealth.test.tsx`); not iPhone-tested |
| Section 18: input codes, end reasons, recovery steps, live-typing pause, Compose and Send review | unit-/component-tested on the phone; agent and relay behaviour integration-tested with a fake input adapter (`pc-agent/tests/test_input_session.py`, `cloud-api/tests/test_input_routing.py`, `tests/test_e2e_input.py`); real Windows injection and real iPhone keyboards not yet verified |
| Section 19: power confirmation copy | component-tested (`PowerConfirmation.test.tsx`) / unit-tested (agent detail text); no device test, no real sleep/shutdown run |
| Section 20: single-instance messages and repair | unit-/integration-tested on Linux (`pc-agent/tests/test_single_instance.py`); Windows mutex and session detection not yet verified |
| Sections 21–22: download status, support submission | component-tested (`UpgradeAndHealth.test.tsx`, `SupportPage.test.tsx`), integration-tested on the service (`cloud-api/tests/test_support_tickets.py`); not yet verified on a device or a deployed service |

Note on versions: this document describes protocol 1.1 (`shared/protocol/version.json`). Error
sentences can be reworded between versions; quote codes, not sentences, when filing issues.
