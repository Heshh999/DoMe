# DoMe manual touchpad and keyboard input

Status: written on 2026-10-09 from the code in this repository (protocol **1.1**, spec §10A). Normative
contract: `shared/protocol/version.json → rules.input_sessions`, `rules.grant_update`,
`rules.ai_eligibility`, the `pointer`/`keyboard` capabilities and the `input.session_start` /
`input.session_stop` actions in `actions.json`, and the `input_*` / `agent_grant_update` /
`foreground_app` / `error_frame` definitions in `schemas/relay-frames.schema.json`. Design brief:
`docs/design/input-control.md`. Where this document and the contract disagree, the contract wins.

Evidence tags follow spec §17: **unit-tested**, **integration-tested**, **Windows-device-tested**,
**iPhone-tested**, **load-tested**, **not yet verified**. **Nothing in this document is
Windows-device-tested or iPhone-tested.** The Windows input adapter
(`pc-agent/dome_agent/platform/windows/input.py`, `SendInput`) has never injected an event on a real
Windows desktop, and the phone keyboard handling has never run in a real iPhone Safari or installed
PWA. Mocks cannot establish either (spec §10A F); every statement about what Windows or iOS does is the
design intent, checked only through test doubles. Tested compatibility on devices: **none** (§11).

Related: `docs/TROUBLESHOOTING.md` §17–§18 (input help), `docs/WINDOWS_INSTALL.md` §4.9 (granting),
`docs/IPHONE_SETUP.md` §6A (the touchpad and keyboard screens), `docs/SECURITY.md` (threat model).

## 1. What it is

Manual input is a **Free**, human-directed touchpad and keyboard for the PC: the phone moves the
Windows cursor, clicks, scrolls, drags and types literal text into whatever the PC has focused. It is
not a command, not a macro and not AI:

- Pointer and keyboard events travel in a bounded, signed **input stream** (`input_batch` frames), not
  in the 30-second command queue. Only starting and stopping a session are registry commands.
- Both input actions carry `ai_eligible: false` and `routine_allowed: false` (`actions.json`); no input
  primitive may appear in an AI tool catalogue, a saved routine or a layout shortcut
  (`rules.ai_eligibility`). The phone's text-command parser (`mobile-app/src/lib/intents.ts`) never
  produces them, and the PC agent has no AI path.
- Typed text is literal UI input. It is never parsed by the deterministic text-command parser or a
  model, and it never invokes shell execution in the agent: the agent hands it to `SendInput` as
  Unicode key events and nothing else (`InputSessionManager._dispatch_sync`).
- Free and Pro have the same input responsiveness: `plans.json → input_rate_limit` is 40 batches/s,
  burst 80, for both plans (a design assumption, like every limit in `plans.json`).

## 2. Permissions and their broad scope

### 2.1 Two independent capabilities

| Capability | Allows | Default |
| --- | --- | --- |
| `pointer` | `pointer_move`, `pointer_button`, `pointer_scroll` events | **off** for every grant, including every pairing made before protocol 1.1 |
| `keyboard` | `text`, `key`, `shortcut` events | **off** for every grant, including every pairing made before protocol 1.1 |

`input.session_start` is permitted by **either** capability (`alternate_capabilities`); the session
then carries `pointer` / `keyboard` flags that mirror the grant's effective capabilities, and the agent
drops a batch with any event type the session may not send (`INPUT_NOT_PERMITTED`; the relay checks the
same coverage first). A keyboard-only phone can type but not move the cursor, and vice versa
(**integration-tested**: `tests/test_e2e_input.py::test_keyboard_only_grant_cannot_move_the_pointer`,
`cloud-api/tests/test_input_routing.py::test_grant_coverage_per_event_type`,
`::test_pointer_only_grant_refuses_text`).

### 2.2 Only the PC owner grants them, on the PC

The phone may *request* `pointer` and `keyboard` during pairing (the PWA requests them by default:
`mobile-app/src/lib/pairing.ts::ALL_CAPABILITIES`), but only a local action on the PC grants them:

| Where | How |
| --- | --- |
| Pairing approval window (tray → **Pair a phone…**) | Two separate, **unticked** checkboxes, "Allow touchpad / mouse (pointer)" and "Allow keyboard", with the scope explanation below (`dome_agent/ui.py`). |
| Tray menu | **Paired phones ▸ ‹phone› ▸ Allow touchpad / Allow keyboard** (checkable) and **What this allows…** (`dome_agent/tray.py`). Ticking a capability also shows the explanation as a notification. |
| Console | `DoMe.exe grant <controller_id> --pointer --keyboard`; withdraw with `--remove-pointer` / `--remove-keyboard`. The command prints the explanation before granting. `pair` / `pair-approve` grant input only when `--pointer` / `--keyboard` (or `--capabilities`) are named; `--yes` never implies them (**unit-tested**). |

The agent stores the change locally and sends `grant_update{controller_id, kid, capabilities}`; the
relay replaces the grant row's capability list with exactly that list (widen or narrow), writes a
`grant_updated` security event, pushes a fresh `grants_snapshot` to the PC and a `pc_status` nudge to
the account's phones. The agent's effective capabilities remain *local grant ∩ snapshot*, so the relay
can never add a capability the PC did not store. A change made while the agent is offline (or with the
agent not running: the CLI writes the local store and says "the DoMe service has NOT been told yet") is
kept in `pending_grant_updates` and sent after the next snapshot (**integration-tested**:
`pc-agent/tests/test_input_session.py::test_grant_update_is_sent_and_resent_offline`,
`cloud-api/tests/test_input_routing.py::test_grant_update_widens_then_narrows_with_snapshots_and_rest`).
`grant_update` cannot empty a grant (`minItems: 1`); to remove every capability, revoke the phone.

Removing both input capabilities ends a live session with reason `grant_removed`; removing only
`pointer` keeps the session but releases a held drag (`InputSessionManager.apply_capabilities`,
**integration-tested**: `test_grant_narrowing_and_removal`).

### 2.3 What a grant really reaches

The copy shown on the PC (`pc-agent/dome_agent/pairing.py::INPUT_SCOPE_EXPLANATION`) and, in the same
meaning, on the phone's pairing and Devices pages (`mobile-app/src/lib/labels.ts::INPUT_SCOPE_EXPLANATION`):

> Touchpad (pointer) and Keyboard let this phone move the mouse, click, scroll and type into whatever
> is in front on this PC, in every app of the unlocked Windows session, not only the approved apps. The
> approved-app list restricts structured app actions; it is not a sandbox around a real mouse and
> keyboard. Only the phone you approve can use them, only while you keep remote control on, and you can
> switch them off here at any time.

What this means in practice:

- A phone with `pointer`/`keyboard` can do anything a person at the PC could do with a mouse and
  keyboard in the unlocked session, including in apps that are not on the approved-app list. No event
  schema can prevent a consequential action that is reachable through ordinary UI (spec §10A D).
- Every batch still requires authenticated pairing, a valid controller signature, a live grant on the
  relay **and** on the PC, and local remote control switched on. The local emergency stop (tray
  **Disable remote control** or `DoMe.exe disable`) ends the session (`remote_disabled`) and no remote
  frame can undo it. Tray **Stop manual input** / `DoMe.exe stop-input` ends only the current input
  session and releases its held input.
- Windows itself refuses remote input on the lock screen, the sign-in screen, UAC prompts and
  elevated (administrator) windows; DoMe does not try to bypass this and does not run elevated (§7).
- Exactly one phone owns a PC's input session at a time (§5.6).

## 3. Touchpad: gesture mapping as implemented

The Touchpad tab (`/app/touchpad`, `mobile-app/src/pages/app/TouchpadPage.tsx`) has a large surface,
the selected PC's name, the window in front of the PC, a live pill, the buttons **Left**, **Right**,
**Double** and **Drag** / **End Drag**, **Keyboard**, **Gestures** (the guide), **Settings**, and an
always-visible **Stop Input**. The gesture state machine is `mobile-app/src/lib/gestures.ts`
(`GestureMachine`, pure, DOM-free); thresholds are `DEFAULT_GESTURE_OPTIONS`.

| Phone action | Rule in `GestureMachine` | Primitive sent | Windows call (`platform/windows/input.py`) |
| --- | --- | --- | --- |
| Slide one finger | becomes a move once the finger travelled > 10 px or was down > 250 ms | `pointer_move{dx, dy}`: touch delta × sensitivity (0.5–3×, default 1.4×) with fractional carry, clamped to ±4096 | `MOUSEEVENTF_MOVE`, relative; Windows pointer acceleration applies as for a physical mouse |
| Lift and put the finger down elsewhere | nothing is sent on lift or touch-down | — | cursor stays where it is; the next move continues from there |
| Short, still tap | one finger, ≤ 250 ms, ≤ 10 px | `pointer_button{left, click}` | `LEFTDOWN` + `LEFTUP` |
| Two quick taps | second tap within 300 ms and 24 px of the first | a second `pointer_button{left, click}` (the first click is never delayed) | two clicks; Windows combines them into a double click only within its own double-click time |
| **Double** button | — | `pointer_button{left, double_click}` | two full clicks in **one** `SendInput` call |
| Two-finger still tap, or **Right** button | both fingers lift within 250 ms with no movement (the first finger may lift early) | `pointer_button{right, click}` | `RIGHTDOWN` + `RIGHTUP` |
| Two fingers moving | 36 touch px per wheel notch; *Natural* (content follows the fingers, default) or *Standard* | `pointer_scroll{dx, dy}` in notches; positive `dy` = wheel away from the user | `MOUSEEVENTF_WHEEL` / `MOUSEEVENTF_HWHEEL`, `mouseData = notches × 120`; horizontal only where the app supports it |
| **Drag**, then move a finger | finger down presses and holds the left button; lifting keeps the hold (drag lock) | `pointer_button{left, down}`, then moves | `LEFTDOWN`; the ack's `held_buttons` shows `["left"]` |
| **End Drag** (or turning Drag off) | releases the hold; Drag mode stays on for the next drag | `pointer_button{left, up}` | `LEFTUP` |
| **Left** button | — | `pointer_button{left, click}` | `LEFTDOWN` + `LEFTUP` |
| **Keyboard** button | opens the keyboard panel inline (the touchpad stays usable) | — | — |

Rules that keep gestures from producing extra clicks (all **unit-tested** in
`mobile-app/test/gestures.test.ts` and **component-tested** in
`mobile-app/test/components/TouchpadPage.test.tsx`):

- A second finger arriving cancels the first finger's pending tap: going from one finger to two
  **never** clicks. Once a gesture was two-fingered, the remaining finger neither clicks nor moves the
  cursor until every finger lifted.
- Scrolling, cancelled touches and ending a drag never add taps.
- `pointercancel`, loss of pointer capture by a finger that is still down, an orientation change and
  the page becoming hidden call `GestureMachine.cancel()`: the gesture is discarded without a click and
  a held left button is released (`pointer_button{left, up}`). The routine `lostpointercapture` that
  browsers fire right after a normal `pointerup` is ignored, so a two-finger tap still right-clicks
  (`mobile-app/DECISIONS.md` 38).
- Drag mode and the "held" indicator reset whenever the session leaves `live` or the selected PC
  changes (DECISIONS 40).
- `touch-action: none` applies only to the touchpad surface; the rest of the page scrolls normally.

Sensitivity and scroll direction are stored in `localStorage` on this phone only (they are not
secrets; `mobile-app/src/store/input.ts`, default `{sensitivity: 1.4, scrollDirection: "natural"}`).
Motion is relative, so neither depends on the PC's screen size or monitor layout: the phone never maps
its own dimensions onto PC pixels, and the agent does not clamp motion to one display. Whether the feel
is right across multiple monitors and mixed DPI is **not yet verified** (`mobile-app/KNOWN_ISSUES.md`
#12, `pc-agent/KNOWN_ISSUES.md` #6).

The **Gestures** sheet (`components/GestureGuide.tsx`) renders the table in
`gestures.ts::GESTURES` with the note "Adding a second finger never clicks. … Windows accepting input is
shown as the live indicator; whether an app reacted is only visible on the PC."

## 4. Keyboard

The keyboard panel (`mobile-app/src/components/KeyboardPanel.tsx`, mapping logic in
`mobile-app/src/lib/typing.ts`) is a separate surface from the **Type** tab's text commands: what you
type here is literal input for the PC's focused field, never a command. Its first line says: "Click the
field on the PC first. DoMe cannot see which field has focus inside a window."

### 4.1 Live typing ("Type live")

- A real `<textarea>` invokes the phone's keyboard (dictation, accents, emoji, IME all come from the
  system keyboard). The panel listens to `beforeinput`, `input`, `compositionstart` and
  `compositionend` rather than to `keydown`.
- Each edit is diffed against `known`, which is **only what this phone sent in the current live run** —
  never assumed to mirror the PC field. An edit is sent only when it happens at the end of `known` and
  every character it removes is a plain BMP code unit that one Windows Backspace removes with certainty
  (`typing.ts::certainDeletion`). Appended text becomes `text` events; removals become `backspace` keys.
- **IME composition**: candidate text is never forwarded; the diff is taken once at `compositionend`.
  Because every commit is a diff against `known`, a late `input` event after `compositionend` (iOS
  event ordering varies) finds nothing new, so the final characters are not duplicated. A cancelled
  composition sends nothing.
- **Return on the phone keyboard** (`insertLineBreak` / `insertParagraph`) is sent as the explicit
  `enter` key, never inserted as text. Enter may submit a search or insert a newline, depending on the
  PC application; DoMe never presses Enter on its own after text.
- Text is split into `text` events of at most 256 characters (`input_text_max_chars`) without cutting
  a surrogate pair or a grapheme cluster (`typing.ts::splitText`, `Intl.Segmenter` where available).
- **Live typing pauses** (nothing is sent; the panel switches to Compose and Send with the reason) when:
  an edit touches text in the middle of what was sent (autocorrect of an earlier word, caret moved);
  a deletion removes an emoji, a combining mark, a ZWJ sequence, an Indic, Thai or Hangul cluster; or
  certainty about what reached the PC is lost — an edit that could not be queued, a rejected batch
  (`INPUT_STALE`, `RATE_LIMITED`, `INPUT_TARGET_CHANGED`, …), more `dropped_events` in an ack, or the
  session ending, being suspended or restarted. After such a pause the live model and the live field
  start empty, so a later Backspace can never delete PC text this phone did not type
  (`typing.ts::PAUSE_EXPLANATION`, `CERTAINTY_LOST_EXPLANATION`; DECISIONS 25, 36). DoMe never "repairs"
  an uncertain edit by selecting all or deleting text it cannot see.
- The live field shows "N characters sent in this run" — what this phone sent, not the PC's field.

### 4.2 Compose and Send

The fallback for keyboards or input methods where live typing is unreliable:

1. Write in the composer (a separate DOM node from the live field, so a draft never leaks into live
   typing: DECISIONS 37). Pasting into the composer is ordinary text entry.
2. **Send** queues the buffer as `text` events, flushes them in one batch and waits up to 3 s
   (`ACK_WAIT_MS`) for an `input_ack` whose `last_seq` covers that batch (`InputSessionClient.awaitAck`).
3. Acknowledged → the buffer is cleared and the panel says Windows accepted N characters.
   Not acknowledged in time, more events reported dropped, or the session ended first → a **review
   state**: "DoMe cannot tell whether this text reached the PC", with **Discard** and **Send again**.
   Nothing is ever resent automatically.

A line break or tab inside the composed text is typed by the agent as the Enter or Tab key
(`platform/windows/input.py::text_segments`), so a multi-line draft can submit a form in some
applications. Enter is otherwise never added.

The composer is memory-only: it is never written to `localStorage`, logs, analytics, diagnostics or a
journal, and it is cleared after an acknowledged send or when the panel is left.

### 4.3 Keys and shortcuts

| Control | Event | Windows |
| --- | --- | --- |
| Enter, Tab, Esc, ⌫ (Backspace), Del, ← ↑ ↓ →, Space | `key{enter|tab|escape|backspace|delete|arrow_*|space}` | one press and release of the virtual key; arrows and Delete carry `KEYEVENTF_EXTENDEDKEY` (the contract also allows `home`, `end`, `page_up`, `page_down`, which the panel does not show) |
| Select all / Copy / Paste / Undo | `shortcut{ctrl_a|ctrl_c|ctrl_v|ctrl_z}` | `VK_CONTROL` down, key down/up, `VK_CONTROL` up in one `SendInput` call |
| **Address bar** (Ctrl+L) | `shortcut{ctrl_l}` | as above; the button is shown **only** while `foreground_app.browser` is set (Chrome or Edge in front) and is labelled as the browser address-bar shortcut (**component-tested**) |

The modifier is always released: if the shortcut call inserted only part of its events, the adapter
sends key-ups for the letter and CTRL; if that recovery fails too it raises `InputHoldError` and the
session keeps both keys tracked for the end-of-session release and crash recovery (**unit-tested** with a
scripted `_send` double: `pc-agent/tests/test_windows_input_layout.py::test_failed_shortcut_releases_the_letter_and_ctrl`,
`test_input_session.py::test_shortcut_whose_recovery_release_failed_keeps_the_keys_tracked`; **not
Windows-device-tested**).

Ctrl+C / Ctrl+V act on the **PC's own clipboard**. DoMe does not synchronise clipboards between devices
and has no background clipboard reader on either side.

### 4.4 How text reaches Windows

`text` events are injected with `KEYEVENTF_UNICODE`, one key-down/key-up pair per UTF-16 code unit, in
chunks that never split a surrogate pair (`utf16_chunks`, **unit-tested**:
`test_utf16_chunks_keep_surrogate_pairs_together`). This types into the focused control regardless of
the PC's keyboard layout. Whether a given application accepts `KEYEVENTF_UNICODE` input the same way as
physical typing is **not yet verified** on any device.

## 5. The input-session protocol

### 5.1 Overview

```
phone                               relay (cloud-api)                         PC agent
  │ command input.session_start ──────► (ordinary signed command) ─────────────► session manager:
  │ ◄── result input_session_result{input_session_id, lease_seconds, …} ◄────── issues id + lease
  │                                                                              input_session{started}
  │ input_batch{pc_id, envelope} ─────► verify signature, grant coverage,  ───► verify again against the
  │   (signed, seq n, ≤ 64 events)      rate budget, PC online; forward          LOCAL grant; live session,
  │                                     envelope verbatim + relay{received_at}   seq > last, age ≤ 1 s;
  │                                                                              dispatch in order via SendInput
  │ ◄── input_ack (≤ 4/s: Windows accepted N, holds) ◄──────────────────────────
  │ ◄── error{INPUT_*, ref_pc_id, ref_input_session_id} ◄── routed to the batch's controller only
  │ ◄── input_session{suspended|ended, reason, holds_released} ◄─── owner + PC subscribers
  │ command input.session_stop ───────────────────────────────────────────────► release holds, end
```

Commands are durable, journaled and one-result-each; input events are a stream that is worthless when
stale and dangerous when replayed, so they bypass the queue and the journal. The relay keeps **no**
`commands` row, **no** result, **no** security event and **no** log line per accepted batch
(**integration-tested**: `cloud-api/tests/test_input_routing.py::test_input_batch_forwarded_verbatim_without_command_row_or_per_batch_event`).

### 5.2 Limits (`version.json → limits`, `plans.json`)

| Limit | Value | Enforced by |
| --- | --- | --- |
| `input_batch_max_events` | 64 events per batch | schema (both sides); the phone uses the session's `max_batch_events` |
| `input_batch_lifetime_seconds` | `expires_at − issued_at` ≤ 5 s | relay and agent (`verify_and_parse_input_batch`; outside the window → `INPUT_STALE`, an over-long window → `MALFORMED_MESSAGE`) |
| `input_age_budget_ms` | 1000 ms at dispatch | agent (§5.5) |
| `input_lease_seconds` | 3 s, renewed by every valid batch | agent watchdog (§5.7) |
| `input_text_max_chars` | 256 characters per `text` event | schema; the phone splits longer text |
| `input_motion_max` | ±4096 per `pointer_move` / `pointer_scroll` axis | schema; the phone clamps |
| `input_batches_per_second` | 40 per socket, before any database work | relay |
| `plans.json → input_rate_limit` | 40 batches/s, burst 80, per controller, identical for Free and Pro | relay (`RATE_LIMITED`) |
| `max_payload_bytes` / `max_frame_bytes` | 16 KiB signed payload / 64 KiB frame | both |

The relay answers refusals with `RATE_LIMITED` at most once per second per socket. A sustained flood —
refusals above twice the input rate for about five seconds — closes the socket with code 4000 and one
`controller_throttled` security event; ordinary overshoot while dragging keeps the socket
(**integration-tested**: `cloud-api/tests/test_input_hardening.py::test_sustained_input_flood_closes_socket_with_one_security_event`,
`::test_moderate_input_overshoot_keeps_the_socket`).

### 5.3 Starting and stopping

`input.session_start` (params `{takeover?: boolean}`, default `false`; risk `moderate`, no
confirmation, 5 s timeout, availability `session_unlocked`) is an ordinary signed command. A grant
with neither capability is refused by the relay and the agent's authorization (`GRANT_MISSING`). The
agent's handler refuses it with `PLATFORM_UNSUPPORTED` off Windows (unless the explicit fake platform is selected),
`PC_REMOTE_DISABLED`, `PC_SESSION_LOCKED`, `CONTROLLER_REVOKED`, `INPUT_NOT_PERMITTED` (neither
capability effective) or `INPUT_SESSION_OWNED` (another phone owns the session and `takeover` is
false). Otherwise it issues a fresh session and answers:

```json
{
  "input_session_id": "Qm9vdHN0cmFwLXNlc3Npb24",
  "lease_seconds": 3,
  "input_age_budget_ms": 1000,
  "max_batch_events": 64,
  "pointer": true,
  "keyboard": true,
  "foreground_app": { "process_name": "chrome.exe", "browser": "chrome" }
}
```

`input_session_id` is 22 base64url characters (16 random bytes, `secrets.token_urlsafe(16)`), issued
by the agent; the phone cannot choose it. The result deliberately omits `foreground_app.window_title`
because command results are journaled durably; the title reaches the phone only in memory-only
`pc_state` frames. A start from the phone that already owns the session replaces its own session (the
old one ends with reason `stopped`).

`input.session_stop` (params `{input_session_id}`, idempotent) ends the caller's own live or suspended
session and answers `{"stopped": true, "released_holds": 1}`; a stop for any other id answers
`{"stopped": false, "released_holds": 0}`.

The phone (`mobile-app/src/lib/input.ts::InputSessionClient`) starts a session on entering the Touchpad
tab (on `INPUT_SESSION_OWNED` it offers **Take over**, which resends with `takeover: true`) and stops it
on leaving the tab, **Stop Input**, the page becoming hidden, a PC switch and sign-out; a dropped socket
ends it locally. Nothing queued is ever sent late or replayed.

### 5.4 Batches

Controller → relay (`controller_input_batch`):

```json
{ "type": "input_batch", "pc_id": "8f0c…", "envelope": { "v": 1, "alg": "ES256", "kid": "…43 chars…", "payload": "<the JSON below, as a string>", "sig": "…86 chars…" } }
```

Signed payload (`input_batch_payload`):

```json
{
  "type": "input_batch",
  "protocol_version": "1.1",
  "account_id": "…uuid…",
  "controller_id": "…uuid…",
  "target_pc_id": "8f0c…",
  "input_session_id": "Qm9vdHN0cmFwLXNlc3Npb24",
  "seq": 42,
  "issued_at": "2026-10-09T12:00:00.000Z",
  "expires_at": "2026-10-09T12:00:05.000Z",
  "events": [
    { "type": "pointer_move", "dx": 12, "dy": -3 },
    { "type": "pointer_button", "button": "left", "action": "click" },
    { "type": "text", "text": "héllo" },
    { "type": "key", "key": "enter" }
  ]
}
```

Event types (`input_event`): `pointer_move{dx, dy}` (relative desktop pixels), `pointer_button{button:
left|right|middle, action: down|up|click|double_click}`, `pointer_scroll{dx, dy}` (notches),
`text{text}` (1–256 characters, literal), `key{key}` (the 14 named keys in §4.3), `shortcut{name:
ctrl_a|ctrl_c|ctrl_v|ctrl_z|ctrl_l}`. An empty `events` array is a keepalive.

The phone builds batches with `buildInputBatchPayload` + `signInputBatch` (`@dome/protocol`), with `seq`
strictly increasing per session, adjacent motion summed before signing (never across a click, scroll,
text, key or shortcut), one flush per animation frame, and an empty keepalive about every second
(`KEEPALIVE_MS = 1000`) while nothing else was sent (**unit-tested**: `mobile-app/test/input.test.ts`).

Relay → agent (`relay_to_agent_input_batch`): `{type: "input_batch", envelope, relay: {received_at,
connection_id}}` with the envelope **verbatim**; the relay cannot mint or alter a batch, because it does
not hold the controller key.

### 5.5 Checks, in order

**Relay** (`cloud-api/dome_api/relay/router.py::route_input_batch`; **integration-tested**, see the
cloud-api README):

1. envelope `kid` equals the socket's kid — otherwise `UNKNOWN_KEY` and close 4003 (the only input
   rejection that closes the socket);
2. the socket announced protocol 1.1 (`PROTOCOL_INCOMPATIBLE`) and is bound to a paired, non-revoked
   controller (`GRANT_MISSING` / `CONTROLLER_REVOKED`);
3. signature, schema, account/controller binding and the 5 s window (`verify_and_parse_input_batch`;
   `INPUT_STALE` / `MALFORMED_MESSAGE`);
4. `target_pc_id == frame.pc_id` (`TARGET_PC_MISMATCH`); a `seq` this socket already forwarded for the
   same session → `INPUT_SEQUENCE_INVALID` (a relay-side replay guard; the agent's check is
   authoritative);
5. controller and PC enabled under the plan, PC on the account (`CONTROLLER_PLAN_DISABLED`,
   `PC_PLAN_DISABLED`, `ACCOUNT_MISMATCH`);
6. a live grant covering every event type in the batch (`INPUT_NOT_PERMITTED`, the whole batch);
7. the per-controller rate budget (`RATE_LIMITED`);
8. agent online (`PC_OFFLINE`), past its first snapshot (`PC_RECONNECTING`) and speaking 1.1
   (`PROTOCOL_INCOMPATIBLE`).

**Agent** (`Agent._on_input_batch`, then `InputSessionManager.handle_batch`; **integration-tested** in
`pc-agent/tests/test_input_session.py`):

1. signature against the **local** grant's key record (same resolver as commands), identity and
   `target_pc_id`; snapshot received (`PC_RECONNECTING`); local remote control on, plan state, live
   non-revoked grant — a failure here also ends that controller's session (`remote_disabled` /
   `grant_removed`);
2. `input_session_id` is the live session owned by this controller — `INPUT_SESSION_EXPIRED` for a
   retired id (the last 64 are remembered), otherwise `INPUT_SESSION_REQUIRED`;
3. a suspended session drops the batch (`INPUT_SUSPENDED`);
4. `seq` strictly greater than the last accepted one — otherwise dropped (`INPUT_SEQUENCE_INVALID`),
   the session continues;
5. age ≤ `input_age_budget_ms` — otherwise dropped (`INPUT_STALE`). The age is measured against a
   per-session estimate of the phone's clock offset (`AgeEstimator`: the windowed minimum of
   *receipt − issued_at* over 30 s, seeded by the `input.session_start` command), and the relay's
   `received_at` bounds the relay → agent leg the same way, so a phone clock a few seconds off neither
   blocks input nor hides a stall (`pc-agent/DECISIONS.md` #39; **integration-tested** with a phone
   clock 2 s behind and 2 s ahead: `test_phone_clock_skew_neither_kills_input_nor_hides_a_stall`);
6. the session's `pointer`/`keyboard` flags cover every event type — otherwise the whole batch is
   dropped (`INPUT_NOT_PERMITTED`);
7. accepted: the lease is renewed and the events are queued for dispatch (an empty batch only renews).

Rejected batches are counted in the next ack's `dropped_events` and reported as
`error{error, ref_input_session_id, ref_controller_id}`, at most one per code per second. The relay
checks that `ref_controller_id` belongs to the PC's account (and, when it knows the session, is its
owner), strips it, adds `ref_pc_id`, and delivers the frame only to that controller's protocol-1.1
sockets (**integration-tested**:
`cloud-api/tests/test_input_routing.py::test_agent_input_rejections_reach_only_the_batch_controller`).
A rejection never ends a session by itself, except the agent-side grant failures in step 1.

### 5.6 Ownership and takeover

Exactly one controller owns a PC's session. A second phone gets `INPUT_SESSION_OWNED`; with
`takeover: true` the agent ends the old session first (reason `takeover`, its held input released),
then creates the new one. The relay forwards `input_ack` to the owner's sockets only and
`input_session` to the owner **and** the PC's subscribers, so other phones see ownership change; the
owner must hold a live grant on that PC (**integration-tested**:
`tests/test_e2e_input.py::test_one_owner_per_pc_takeover_and_revocation_end_the_session`,
`cloud-api/tests/test_input_hardening.py::test_input_session_owner_requires_live_grant_on_this_pc`).
`pc_state.input_session` carries `{controller_id, pointer, keyboard, lease_expires_at}` of the live
session; the Health screen compares that controller id with this phone's own before saying "another
phone" (DECISIONS 39).

Changing the selected PC on the phone stops the old session (`input.session_stop`) before a session
for the new PC starts. The agent cannot know the phone switched PCs, so such an end reads `stopped`;
the contract's `pc_switch` reason is defined but not emitted (`pc-agent/CONTRACT_ISSUES.md` #9).

### 5.7 Dispatch, ordering, backpressure and acks

- One worker per agent dispatches accepted batches **in order** (`InputSessionManager._run`).
  `coalesce_events` sums **adjacent** `pointer_move`s only; nothing is merged across a button, scroll,
  text, key or shortcut event (**unit-tested**: `test_coalescing_sums_only_adjacent_motion`,
  `test_ordering_and_coalescing_through_the_adapter`).
- If the oldest queued batch has waited longer than the age budget, the backlog is discarded, the held
  input is released and the session is **suspended**: `input_session{event: suspended, reason:
  backpressure, holds_released}`; later batches are dropped with `INPUT_SUSPENDED` until a fresh
  `input.session_start` (**integration-tested**: `test_backpressure_suspends_and_discards`).
- One failed event drops the rest of its batch, so nothing runs out of order
  (`INPUT_INJECTION_FAILED` / `INPUT_RESTRICTED`, §7).
- `input_ack{pc_id, input_session_id, last_seq, accepted_events, dropped_events, held_buttons,
  held_keys, at}` at most every 250 ms (≤ 4/s) while live (**integration-tested**:
  `test_acks_at_most_four_per_second`). It reports **Windows acceptance** — `SendInput` returned the full
  count — never an observed application effect. The phone's pill reads "Live · Windows accepted N".
- Nothing is replayed after a reconnect or restart: the session id changes, old ids are refused, and
  input never enters the durable command journal.

### 5.8 Lease and watchdog

The agent's watchdog task (`InputSessionManager._watch`, every 100 ms) is independent of the relay and
of any phone unload event. It ends the session when the 3 s lease passes without a valid batch
(`lease_expired`), when local remote control is switched off (`remote_disabled`), and — polled every
second — when Windows is locked (`session_locked`) or a secure desktop is active (`secure_desktop`). The
phone's keepalive every second keeps the lease alive while the Touchpad tab is open
(**integration-tested**: `test_lease_expiry_releases_held_button_and_ends`,
`test_keepalives_renew_the_lease`, `tests/test_e2e_input.py::test_input_needs_a_local_grant_then_streams_in_order_and_releases_on_lease_expiry`).
The 3 s starting lease is an implementation value the spec asks to validate on devices; it has not been.

## 6. Focus and the foreground window

What the agent observes (`WindowsInput.foreground`): the foreground window handle and its process
(`GetForegroundWindow`, `GetWindowThreadProcessId`, process name via `psutil`), the window title
(`GetWindowTextW`, untrusted display text, ≤ 200 characters), `browser: chrome|edge|other` from the
process name, and `elevated` when the process's token integrity level could be read. It is published
as `pc_state.foreground_app`, refreshed at most every 2 s while a session is live and on request
otherwise, and a change triggers a state frame.

What is **not observable** and never claimed:

- **Which field has focus inside a window.** DoMe types into whatever the PC has focused; the customer
  selects the field on the PC display (a click with the touchpad, or at the PC). The phone shows the
  window, never a field.
- **Whether the application reacted.** `input_ack` is Windows acceptance only. There is no desktop
  capture, keylogging, document scraping or synthetic success indicator.
- **Whether an elevated window received input.** Windows (UIPI) drops input aimed at a higher-integrity
  window **without an error**; see §7.
- `elevated` is usually **absent** exactly when it matters, because a normal-integrity agent typically
  cannot open an elevated process; absent means unknown, never "not elevated"
  (`pc-agent/KNOWN_ISSUES.md` #7).

**Target-change rule** (`InputSessionManager._dispatch_sync`): the agent captures the foreground
identity (window handle + process id) when the session starts and at every user-directed click or
button press from the phone. Before each `text`, `key` or `shortcut` it compares the current foreground
with that target. On a change it **blocks keyboard input** for the session: the event and every later
keyboard event are dropped and counted, `INPUT_TARGET_CHANGED` is reported once, and a fresh
`foreground_app` state frame goes to the phone, which pauses live typing ("The window in front of the
PC changed, so live typing paused rather than typing into the wrong window. Click where you want to
type on the PC, then continue."). Keyboard batches the phone had queued before it could know are dropped
too. The block clears on a click from the phone (re-capturing the target), on a fresh
`input.session_start`, or for a batch issued at least 2 s after the change — by then the phone has the
new `foreground_app` and paused, so new typing is a deliberate continuation (**integration-tested**:
`test_target_change_stops_typing_until_the_customer_continues`,
`test_keyboard_batches_queued_across_a_target_change_are_not_typed`,
`test_typing_issued_well_after_a_target_change_continues`). The 2 s grace is a heuristic
(`pc-agent/KNOWN_ISSUES.md` #17). Pointer events keep working throughout.

DoMe never steals focus or switches to a remembered browser tab to make typing succeed.

## 7. Protected screens and elevated windows

| Situation on the PC | What happens | What the phone shows |
| --- | --- | --- |
| Windows locked (`session_locked`) | `input.session_start` is refused (`PC_SESSION_LOCKED`); a live session ends within about 1 s (lock polling) with its held input released; up to ~1 s of batches can reach `SendInput` first and are refused by Windows (`pc-agent/KNOWN_ISSUES.md` #8) | session ended, "The PC is locked. Unlock it on the PC to continue." |
| Secure desktop: lock/sign-in screen, UAC consent prompt (`OpenInputDesktop` fails or the input desktop is not `Default`) | the session ends with reason `secure_desktop`, held input released | session ended with the `INPUT_RESTRICTED` sentence |
| Elevated or unknown-integrity window in front on the normal desktop | the session **stays live**; `pc_state.input_restricted` becomes true; keyboard events are refused with `INPUT_RESTRICTED` and never acked as accepted; pointer events still run so you can click a normal window | Health: input layer "Granted, but Windows is showing a protected screen…"; Keyboard: the `INPUT_RESTRICTED` notice |
| `SendInput` inserted fewer events than requested | `INPUT_INJECTION_FAILED` ("Windows accepted N of M input events…"), or `INPUT_RESTRICTED` when the error is access denied **and** a protected screen is detected; UIPI is never claimed from the return value alone | the code's sentence and steps |

Known gap: a **click** on an elevated window is dropped by Windows without an error and is counted as
accepted, and so are keystrokes while the foreground's integrity level is unknown
(`pc-agent/KNOWN_ISSUES.md` #15). The agent refuses only on a known restriction, never on a guess. All
of §7 is **integration-tested with the fake adapter**
(`test_secure_desktop_ends_session_but_elevated_window_only_restricts`,
`test_elevated_window_with_stale_or_unknown_foreground_does_not_end_the_session`, `test_lock_ends_session`)
and **not Windows-device-tested**.

DoMe never bypasses UAC, the lock screen or Windows authentication, never runs the agent as
administrator and never asks for security protections to be disabled.

## 8. Recovery of held input

The agent tracks only the buttons and keys **its current session** injected (`held_buttons`,
`held_keys`; a shortcut's CTRL and letter count while the shortcut runs) and releases exactly those —
never keys held on the physical keyboard. It cannot claim perfect isolation: Windows input state is
global, and overlapping physical and remote use is ambiguous.

Every end follows the same order (`InputSessionManager._end`):

1. **retire the id** (state `ended`, id added to the retired list, queue cleared), so a delayed batch
   can never press anything again;
2. wait up to 2 s for the in-flight `SendInput` call; if it is still running, whatever it presses is
   released as soon as it returns (**integration-tested**:
   `test_dispatch_outliving_the_bounded_wait_is_released_when_it_returns`);
3. **release** this session's held buttons/keys and update or delete the recovery file;
4. emit `input_session{event: ended, reason, holds_released}`.

| End reason (`agent_input_session.reason`) | Trigger | Detected by | Held input |
| --- | --- | --- | --- |
| `stopped` | `input.session_stop` (Stop Input, leaving the Touchpad tab, page hidden, PC switch, sign-out); also a restart by the same phone | phone command | released first; count in the stop result |
| `lease_expired` | no valid batch for 3 s (network loss, PWA backgrounded, phone locked) | agent watchdog | released |
| `takeover` | another phone started with `takeover: true` | agent | released before the new session exists |
| `controller_revoked` | revocation from the account (snapshot) or on the PC | agent | released |
| `grant_removed` | both `pointer` and `keyboard` removed, or the phone/PC disabled under the plan | agent | released (removing only `pointer` releases a drag and keeps the session) |
| `controller_disconnected` | the agent's relay socket dropped or was superseded (4001) | agent | released; the frame cannot be delivered over the dead socket (`pc-agent/KNOWN_ISSUES.md` #12) |
| `session_locked` | Windows locked | watchdog (1 s poll) | released as soon as Windows permits |
| `secure_desktop` | lock/sign-in/UAC secure desktop | watchdog (1 s poll) | released as soon as Windows permits |
| `remote_disabled` | local **Disable remote control** | watchdog, and on the next batch | released |
| `agent_restart` | the agent stops (Quit, update restart) — or crashed and started again | agent shutdown, or start-up recovery | released at shutdown; after a crash, at the next start-up from `input_holds.json` |
| `backpressure` (event `suspended`) | dispatch fell behind the 1 s age budget | agent worker | released at suspension; a fresh start is required |
| `pc_switch` | defined by the contract | — | not emitted today (§5.6) |

Not a session end: when only the **phone's** socket drops, the relay has no frame to tell the agent
(`cloud-api/CONTRACT_ISSUES.md` #12), so the 3 s lease ends the session (`lease_expired`). The phone
ends its side immediately and shows *Ended*.

**Crash recovery.** While anything is held, the agent mirrors the session id, controller id and the
held button/key **names** to `<state dir>/input_holds.json` (atomic replace, no content, no fsync:
`pc-agent/KNOWN_ISSUES.md` #11). At the next start-up `recover_after_restart` releases them, deletes the
file and, after the first snapshot, sends `input_session{ended, agent_restart}` for the old id. Old
session ids are rejected and nothing is replayed from disk (**integration-tested**:
`test_crash_recovery_releases_holds_and_rejects_old_session`). A `SendInput` call that never returns
cannot be interrupted; what it presses later is released when it returns or at the next start-up
(`pc-agent/KNOWN_ISSUES.md` #16).

**On the phone**, cancellation never leaves a hold of its own: `pointercancel`, lost capture,
rotation, hiding the page and leaving the tab send `pointer_button{left, up}` for a drag and then stop
the session; the PC-side lease and release above apply if those frames never arrive.

## 9. Content and privacy

- `text` events are literal customer input. They exist only in the phone's memory, inside the signed
  payload in transit, and in the agent's adapter call. No component logs, journals, persists, puts in
  diagnostics or state frames, or forwards them to an AI provider (`rules.input_sessions` (5)).
  **Unit-tested** on the agent by capturing the log file, stdout, security events, status, the
  diagnostics bundle, state frames, the SQLite file and the recovery file for a sentinel
  (`test_text_content_never_appears_in_logs_or_persisted_state`), and **integration-tested** end to end
  (`tests/test_e2e_input.py`: the typed marker is absent from the relay's command rows and from every
  file in the agent's state directory). The phone's logger drops `text`, `composer`, `events`, `key`
  and `keys` fields (`mobile-app/src/lib/log.ts`, **unit-tested**).
- The relay sees typed content **in transit** inside TLS; DoMe does not claim end-to-end encryption for
  this release. Being on Pro never sends typed content to an AI provider.
- `foreground_app.window_title` can contain document names or URLs. It is display-only, memory-only on
  the phone, and excluded from the durably journaled `input.session_start` result.

## 10. Acceptance evidence (spec §10A F)

| Scenario | Required observed result | What exists | Tag |
| --- | --- | --- | --- |
| Real iPhone Safari and installed PWA controlling Windows | movement, lift/reposition, click, double click, right click, scrolling, explicit drag/release work on the PC | gesture machine (`mobile-app/test/gestures.test.ts`), Touchpad page (`test/components/TouchpadPage.test.tsx`), ordered dispatch and held-button tracking through the real relay and the real agent process with the fake adapter (`tests/test_e2e_input.py`), `SendInput` structure layouts and mouse builders (`pc-agent/tests/test_windows_input_layout.py`) | unit-tested, integration-tested (fake adapter); **not yet verified** on iPhone or Windows |
| Chrome/Edge YouTube and Google search | click the search field, type, press Enter deliberately, observe the search | Enter is an explicit key and never sent after text (component-tested); Unicode injection builders (unit-tested) | **not yet verified** |
| Browser address bar and an ordinary editor such as Notepad | focus, text entry, correction, arrows, tested shortcuts | Ctrl+L gating on `foreground_app.browser` (component-tested); shortcut sequence with CTRL always released (unit-tested with a scripted `_send`); key table covers the contract (unit-tested) | **not yet verified** |
| Phone keyboard varieties | committed text, accents, emoji, at least one IME, composition cancel, autocorrection, Compose and Send never duplicate or erase unrelated text | `mobile-app/test/typing.test.ts` (append once, IME commits once in either event order, autocorrect mapping, middle edit → pause, uncertain deletion → pause, grapheme-safe splitting); KeyboardPanel tests in `TouchpadPage.test.tsx` (Compose and Send success/review without resend, pause after rejection) | unit-tested, component-tested (jsdom); **not yet verified** on any real keyboard |
| Keyboard open, rotation, gesture cancellation, PWA backgrounding | controls usable; cancelled gestures do not click; held remote input releases | `pointercancel`/lost capture/hidden release without a click (component-tested); stop on page hidden (unit-tested); PC-side lease release (integration-tested) | component/integration-tested; **not yet verified** on iPhone |
| Network interruption during drag / key hold / text send | no stuck input beyond the recovery bound, no stale replay, honest uncertain send | lease expiry releases a held button within the 3 s lease plus the 100 ms watchdog tick; stale and replayed batches dropped; retired id refused (`tests/test_e2e_input.py`, `pc-agent/tests/test_input_session.py`); Compose review state with no automatic resend (component-tested) | integration-tested (simulated loss); **not yet verified** on a real network |
| Revocation, two controllers, PC switching, Windows lock/UAC, elevated target | correct permissions and ownership; safe stop; accurate limitation reporting | takeover and revocation end the session and release holds (`tests/test_e2e_input.py`); keyboard-only grant; lock, secure desktop and elevated-window behaviour with the fake adapter; stop on PC switch (`mobile-app/test/input.test.ts`) | integration-tested / unit-tested; real lock, UAC and elevation **not yet verified** |
| Multiple monitors and mixed DPI | pointer and drag follow the real desktop topology | relative motion only, no clamping to one display (code); nothing measured | **not yet verified** |
| Loaded relay and a burst of pointer events | bounded queues, measured latency, no delayed backlog; Free = Pro responsiveness | relay per-socket and per-controller budgets and flood close (`cloud-api/tests/test_input_hardening.py`, `test_input_routing.py::test_input_rate_budget_per_controller`); agent backpressure suspension; identical `input_rate_limit` in `plans.json` | integration-tested; latency on the real relay path **not measured**; **not load-tested** |

Test runs behind this table: `pc-agent` `tests/test_input_session.py`, `tests/test_single_instance.py`
and `tests/test_windows_input_layout.py` — 56 passed, 1 skipped on Linux on 2026-10-09 (run for this
document); `mobile-app` gesture, typing, input, health, support, Touchpad page and Upgrade/Health
component tests — 74 passed in 8 files (run for this document). The cloud-api and cross-component
suites need PostgreSQL and were not re-run for this document; their counts are in `cloud-api/README.md`
and `docs/ACCEPTANCE.md`.

The manual device checklists are `pc-agent/README.md` → "Windows verification checklist" (rows
*SendInput on a real desktop* through *Crash recovery*) and `mobile-app/README.md` → "Manual iPhone
checklist — touchpad and keyboard". Record results in `docs/ACCEPTANCE.md` with the tags
**Windows-device-tested** / **iPhone-tested**. Advertise manual input as a released Free feature only
after those checks pass (spec §10A F).

## 11. Tested compatibility

| Platform | Status |
| --- | --- |
| Windows 10 / 11 desktop, `SendInput` | **none tested** — implemented against the Windows API; layouts and builders unit-tested on Linux |
| Multiple monitors, mixed DPI, pointer acceleration | **none tested** |
| Chrome, Edge, Notepad, Google/YouTube search fields | **none tested** |
| iPhone Safari, installed PWA | **none tested** |
| Phone keyboards: Apple keyboard, IME (Japanese/Chinese), dictation, third-party keyboards | **none tested** |
| Android / desktop browsers as the controller | not targeted; the same web app may work, nothing tested |

Until a device pass is recorded, DoMe makes no compatibility claim for manual input.

## 12. Known limitations (summary)

- Two quick taps rely on Windows' double-click time; over a slow link they can arrive as two single
  clicks. Use the **Double** button (`mobile-app/KNOWN_ISSUES.md` #9).
- Live typing pauses on legitimate edits it cannot mirror safely (autocorrect of an earlier word,
  deleting an emoji). Compose and Send covers the rest (`mobile-app/KNOWN_ISSUES.md` #10).
- Any rejected batch pauses live typing even when it held only pointer events, because an error frame
  does not name the batch's `seq` (`mobile-app/KNOWN_ISSUES.md` #18).
- Windows pointer acceleration applies; the sensitivity slider cannot compensate exactly.
- Clicks on an elevated window, and keystrokes while the foreground's integrity is unknown, can be
  dropped by Windows and still counted as accepted (§7).
- The target-change grace of 2 s is a heuristic (§6).
- `controller_disconnected` and `pc_switch` are not delivered or emitted as the contract describes
  (§5.6, §8); the lease covers the gap.
