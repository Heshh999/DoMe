# Design: manual touchpad and keyboard input (spec §10A, protocol 1.1)

Status: design brief for the component builders. The normative contract is `shared/protocol`
(`version.json → rules.input_sessions`, `rules.grant_update`, `rules.ai_eligibility`,
`actions.json` capabilities `pointer`/`keyboard` and actions `input.session_start` /
`input.session_stop`, `relay-frames.schema.json` defs `input_event`, `input_batch_payload`,
`controller_input_batch`, `relay_to_agent_input_batch`, `agent_input_ack`, `agent_input_session`,
`agent_grant_update`, `foreground_app`, `pc_state.input_session`, `results.schema.json`
`input_session_result`). Everything here must agree with those files; where they differ, the contract
wins and the builder records the gap in `CONTRACT_ISSUES.md`.

Evidence tags: nothing in this design is Windows-device-tested or iPhone-tested. The Windows input
adapter and the real phone keyboard can only be verified on devices; builders ship the real adapters
with honest unconfigured/unsupported errors plus explicit test doubles, and say so.

## 1. Why a separate stream

Commands are durable, journaled, 30-second, one-result-each. Cursor motion and typing are a stream:
tens of small events per second that are worthless once stale and dangerous when replayed. The
contract therefore adds a bounded, signed, non-journaled stream next to the command path:

```
phone gesture/keyboard ──► input_batch (signed by the controller key, ≤ 64 events, seq, 5 s window)
      │ WSS                                  relay: verify signature, grant covers event types,
      ▼                                             PC online, per-controller rate budget, forward verbatim
  relay ──► agent: verify signature against the LOCAL grant, live session owned by this controller,
                   seq strictly increasing, age ≤ 1 s, then inject through the Windows input adapter
            agent ──► input_ack (≤ 4/s: Windows accepted N events, holds) ──► relay ──► owning phone
            agent ──► input_session{started|suspended|ended, reason, holds_released}
```

Session lifecycle is a normal command pair (`input.session_start` → `input_session_result`,
`input.session_stop`) so it inherits ack/result/journal semantics; only the events bypass the queue.

## 2. Capabilities and permission

- Two new Free capabilities, `pointer` and `keyboard`, independently grantable per controller.
- Existing grants do not gain them. The PC owner adds them locally (tray menu and CLI
  `dome-agent grant <controller_id> --pointer --keyboard`, and the pairing approval screen offers them
  when the phone requested them). The agent then sends `grant_update{controller_id, kid,
  capabilities}`; the relay replaces the grant's capability list and pushes a fresh
  `grants_snapshot`. Removing them works the same way and ends any live session (`grant_removed`).
- Setup copy must say plainly: manual input reaches every app of the unlocked Windows session, not
  only approved apps; the approved-app list restricts structured app actions, not a real mouse and
  keyboard. The threat model (`docs/SECURITY.md`) gains a row for this.
- `input.session_start` is permitted by either capability (`alternate_capabilities`); the result
  reports which of `pointer`/`keyboard` the session may use. The agent refuses pointer events on a
  keyboard-only grant and vice versa (`INPUT_NOT_PERMITTED`), and so does the relay.
- `ai_eligible: false` on both input actions; no input primitive may appear in an AI tool catalogue,
  a routine or a layout shortcut (the PWA's text-command parser must never emit them either).

## 3. Agent (pc-agent)

### 3.1 Input adapter

`platform/protocol.py` gains:

```python
class InputAdapter(Protocol):
    def move(self, dx: int, dy: int) -> None            # relative, desktop pixels, SendInput MOUSEEVENTF_MOVE
    def button(self, button: str, action: str) -> None  # left|right|middle × down|up|click|double_click
    def scroll(self, dx: int, dy: int) -> None          # notches × WHEEL_DELTA (dy>0 = away from user)
    def text(self, text: str) -> None                   # KEYEVENTF_UNICODE per UTF-16 code unit, surrogate pairs intact
    def key(self, key: str) -> None                     # named key press+release (VK codes)
    def shortcut(self, name: str) -> None               # ctrl_a/c/v/z/l: CTRL down, key, CTRL up — modifier always released
    def release(self, buttons: set[str], keys: set[str]) -> int  # release exactly these; returns count released
    def foreground(self) -> ForegroundApp | None        # GetForegroundWindow → process name, title, browser, elevated?
    def input_restricted(self) -> bool                  # locked session / secure desktop / elevated foreground where detectable
```

- Windows implementation in `platform/windows/input.py` with `ctypes` `SendInput` and correctly sized
  `INPUT`/`MOUSEINPUT`/`KEYBDINPUT` structures (both 32- and 64-bit layouts), `GetForegroundWindow`,
  `GetWindowThreadProcessId`, `GetWindowTextW`, process name via `psutil`; multi-monitor: relative
  motion needs no topology, but the adapter must not clamp to a single display. A `SendInput` return
  smaller than the count is `INPUT_INJECTION_FAILED`; `GetLastError` 5 (access denied) while
  `input_restricted()` is true is `INPUT_RESTRICTED`; never claim UIPI from the return value alone.
- `testing/fake_platform.py` gains `FakeInput` recording every call in order with timestamps, a
  settable foreground app, settable restriction flag, injectable failures, and a held-set the tests
  can read.
- `platform/unsupported.py` raises `PLATFORM_UNSUPPORTED` for every injection call (development run on
  Linux without `DOME_AGENT_PLATFORM=fake`).

### 3.2 Session manager (`dome_agent/input_session.py`)

One `InputSession` per PC at a time: `input_session_id` (22 random base64url chars),
`controller_id`, `kid`, `pointer`/`keyboard` flags from the effective grant, `last_seq`,
`lease_expires_at`, `held_buttons`, `held_keys`, `state in {live, suspended, ended}`.

- `start(controller, takeover)` — refuses while the session is locked or remote control is disabled
  (`PC_SESSION_LOCKED` / `PC_REMOTE_DISABLED`), `INPUT_SESSION_OWNED` when another controller owns a
  live session and `takeover` is false; otherwise ends the old session (release holds first, emit
  `input_session{ended, takeover}`), creates the new one, starts the lease watchdog, emits
  `input_session{started}` and updates `pc_state.input_session`.
- `handle_batch(verified)` — session id must be the live one (`INPUT_SESSION_REQUIRED` when none,
  `INPUT_SESSION_EXPIRED` for a retired id), controller must be the owner, `seq > last_seq`
  (`INPUT_SEQUENCE_INVALID`, drop), `age ≤ input_age_budget_ms` (`INPUT_STALE`, drop), event types
  covered by the session's capabilities (`INPUT_NOT_PERMITTED`, drop whole batch), then renew the
  lease and enqueue for dispatch. Rejections of a batch never end the session except as specified.
- Dispatch runs in order on one worker: sum adjacent `pointer_move`s, never merge across any other
  event; `pointer_button down` adds to `held_buttons`, `up` removes; text/key/shortcut call the adapter;
  if the dispatch backlog's oldest event is older than the age budget → discard the backlog, state
  `suspended`, emit `input_session{suspended, backpressure}` (a fresh `input.session_start` is required).
- Before each `text`/`key`/`shortcut` the manager compares the foreground window identity with the
  one captured when the session started or last pointer click happened; a change stops the remaining
  text/key events of that batch with `INPUT_TARGET_CHANGED` (reported in the next `input_ack` as
  dropped events plus an `error` frame with that code). A user-directed click re-captures the target.
- Watchdog: lease `input_lease_seconds` (3 s), renewed by every valid batch (empty batches are
  keepalives). Expiry → end with `lease_expired`. Independent of relay liveness: the relay socket
  dropping also ends the session (`controller_disconnected` when the relay reports it, otherwise the
  lease expires).
- End reasons and triggers (release holds first, then retire the id, then emit): `stopped`
  (`input.session_stop`), `lease_expired`, `takeover`, `controller_revoked` (snapshot/local
  revocation), `grant_removed` (pointer and keyboard both gone), `pc_switch` (the PWA stops the old
  session before starting on another PC; the agent treats a start from the same controller as a
  restart), `session_locked`, `secure_desktop`, `backpressure` (suspended), `remote_disabled`,
  `agent_restart` (crash recovery: a tiny non-content file `input_holds.json` with the held
  buttons/keys, released at the next start-up, then deleted).
- Acks: at most every 250 ms while live, carrying `last_seq`, cumulative accepted/dropped counts and
  the current holds.
- Content hygiene: `text` payloads never appear in logs, security events, the journal, diagnostics or
  state frames; log only counts and event types.

### 3.3 State and single instance

- `pc_state.foreground_app` (process name, title as untrusted display data, `browser`, `elevated`),
  `pc_state.input_session` (owner, flags, lease expiry), `pc_state.input_restricted`.
- Single instance per Windows user session (spec §10 "One agent per Windows user session"): a named
  mutex `Local\DoMe.Agent.<session id>` on Windows, an `flock`ed lock file in the state directory
  elsewhere. A second launch connects to the existing control channel and asks it to show the
  tray/setup window (`show` op), prints what it did, exits 0. If the lock is held but the control
  socket does not answer, report "another DoMe instance is running but not responding (pid N)" and
  offer `dome-agent repair`, which re-registers the native host and control endpoint while preserving
  identity, credential, grants and approved apps; it never kills processes. Report a stale control
  endpoint, a permission problem on the state directory and an other-session conflict distinctly.
- Tests: ordering and coalescing, lease expiry releases holds, stale and forged batches, old session
  id after restart, ownership and takeover, grant removal/revocation ends the session, lock ends the
  session, target change stops text, acks ≤ 4/s, crash recovery releases holds, single instance
  (second launch → `show`, stale lock, repair preserves grants), text never in logs (assert on the
  captured log output).

## 4. Relay (cloud-api)

- `controller_ws`: new frame `input_batch` → `route_input_batch`: bound controller; envelope kid ==
  socket kid; `verify_and_parse_input_batch` with the controller's key record; `target_pc_id ==
  frame.pc_id`; PC in the account and enabled; live grant whose capabilities cover
  `input_event_capabilities(events)`; agent online and past its first snapshot; per-controller
  `plans.input_rate_limit` token bucket; forward `{type: input_batch, envelope, relay{received_at,
  connection_id}}` verbatim. Rejections are `error` frames with `ref_pc_id` (no command id exists),
  rate-limited to one security event per minute per controller (`input_rejected`), and never close the
  socket except for kid mismatch (`UNKNOWN_KEY`, 4003) as today.
- `agent_ws`: `input_ack` and `input_session` are forwarded to every socket bound to the owning
  controller (`send_to_controller` with no preferred connection); `input_session` is additionally
  broadcast to the PC's subscribers so other phones see ownership change. `grant_update` replaces the
  grant row's capabilities (validated: controller belongs to this PC's account and holds a live
  grant on this PC), writes a `grant_updated` security event, pushes a fresh snapshot.
- No `commands` row, no per-batch log line with content. `pc_state` passes through unchanged.
- REST: `GET /v1/pcs/{id}/grants` already lists capabilities; the device list shows `pointer` /
  `keyboard`. Pairing claim may request them (`requested_capabilities` enum widened).
- Support (spec §11A, smallest practical): `POST /v1/support/tickets {category, message ≤ 2000,
  diagnostics?: redacted bundle ≤ 32 KiB}` → `201 {ticket_id, reference, status: received,
  created_at}`; `GET /v1/support/tickets/{id}`; Alembic `0002_support_tickets`; account-scoped; no
  execution capability; the bundle is passed through the log redactor before storage. REST schemas
  for both bodies are a contract gap to record (`rest.schema.json` has no defs for them yet): add
  them to `shared/protocol/schemas/rest.schema.json` — this is the ONE place builders may touch the
  contract, additively, with `additionalProperties: false`, and must regenerate validators.
- Tests: forged/replayed/stale batches, grant coverage per event type, rate budget, PC offline, ack
  and session frames reach the owner, grant_update widens then narrows and snapshots follow, ticket
  create/read scoped per account.

## 5. Phone (mobile-app)

- Touchpad page (`/app/touchpad`): large surface with pointer-capture gesture state machine
  (move, tap → left click, double tap, two-finger tap → right click, two-finger move → scroll,
  explicit Drag mode with conspicuous active state and End Drag, Right Click and Left Click buttons,
  Stop Input always visible), selected PC name, sensitivity and scroll-direction preferences
  (localStorage is acceptable: they are not secrets), a short gesture guide. `touch-action: none`
  only on the surface. Handle `pointercancel`, lost capture, orientation and visibility changes:
  all of them release Drag and send `pointer_button up` for anything held.
- Keyboard sheet: a real `<textarea>` for the mobile keyboard; `beforeinput`/`input`/composition
  handling that commits text once (IME candidates are never forwarded), Backspace/Delete/Enter/Tab/
  Escape/arrows keys, shortcut palette (Ctrl+A/C/V/Z, Ctrl+L only when `foreground_app.browser` is
  set), Compose and Send fallback (memory-only buffer, cleared after a successful send or when leaving;
  uncertain send → review state, deliberate retry, never automatic). Never select-all or delete
  unknown PC text to "repair" an edit. Show the selected PC and `foreground_app`; stop live typing
  when it changes.
- Session: `input.session_start` on entering the screen (takeover prompt on `INPUT_SESSION_OWNED`),
  keepalive batch every second while open, `input.session_stop` on leaving, visibility hidden,
  sign-out, PC switch and Stop Input; handle `input_session{suspended|ended}` and `input_ack`
  (connected / live indicator shows Windows acceptance, never an app effect). Batches are built with
  `buildInputBatchPayload` + `signInputBatch`, ≤ 64 events, coalesced motion per animation frame,
  `seq` strictly increasing per session.
- Permissions UX: Devices page shows `pointer`/`keyboard` per grant with the broad-scope explanation;
  pairing requests them by default (the PC owner decides); `INPUT_NOT_PERMITTED` explains that the PC
  owner grants them on the PC.
- Dashboard: compact Now Playing panel (PC name, app/browser, title where available, state) and the
  media-target clarity rules of spec §9; power confirmations state the loss of remote access and that
  V1 has no remote wake (ConfirmationModal copy).
- Health screen (`/app/health`): layered states (phone connectivity, account session, PC agent relay
  connection, local remote-control and manual-input permissions, extension availability, media target),
  one next action per state, details behind a control, bounded retries with a retry button and the
  support path, V1 requirements stated. Onboarding walkthrough returns to the failed step.
- Support page: ticket form → `POST /v1/support/tickets` with the existing redacted diagnostics
  bundle; success shows the reference; failure shows a copyable redacted summary and never claims
  receipt. Known issues / release history page from `src/content/release-notes.json`.
- Upgrade experience: no upgrade modals or banners on Free flows; deliberate Pro selections show a
  dismissible explanation (audit RoutinesPage/BillingPage).
- Tests (jsdom): gesture state machine (no accidental click on finger-count change, cancelled
  touches, drag completion), batch building and seq, composition commit once, Compose and Send
  review state, Ctrl+L gating, session stop on visibility hidden/PC switch, no text in logs,
  health-state mapping, support failure copy.

## 6. Brand (`brand/`)

Original wordmark and app icon as editable SVG (light/dark), exports through
`mobile-app/scripts/make-icons.mjs` (PWA icons, favicon) and PNGs for the Windows tray/installer in
`pc-agent/dome_agent/assets/`, plus `brand/BRAND.md` (colour tokens, type, spacing, usage). No
claims, no competitor comparisons.

## 7. Documents (after the builds, from the real code)

`docs/INPUT_CONTROL.md` (gesture mapping, session protocol, permissions, focus behaviour, recovery,
tested compatibility with evidence tags), `docs/SUPPORT.md`, updates to `TROUBLESHOOTING.md`
(health states, gesture/input help, power-state limits, download/setup repair), `SECURITY.md`
(T16 broad input grant, misdirected text/focus, stuck input, stale events, controller takeover,
typed-data exposure to the relay), `PRODUCT_AND_PLANS.md` (Free rows), `COST_MODEL.md` (input
session traffic and verification cost), `ACCEPTANCE.md` (scenarios 18–25), `PROGRESS.md`,
`HANDOFF.md`.
