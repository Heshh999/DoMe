# pc-agent decisions (where the design document was silent)

1. **Power results are emitted when the OS call is issued, not when the countdown is armed.** The
   confirmed command is acked `executing`, the countdown is visible as `pending_power_action` in state
   frames, and `result` carries what Windows actually accepted (`accepted`, `countdown_seconds`,
   `fires_at`) or `POWER_DENIED`/`OS_ERROR`. `power.cancel`, a relay `cancel` frame, local *Disable
   remote control* and agent shutdown end the command as `canceled`/`POWER_CANCELED`. The executor
   supports this through a `DEFERRED` handler return + `Executor.complete()` so the single worker is
   never blocked for the countdown. `SetSuspendState` blocks until resume, so sleep is reported as
   accepted when it has not failed within 2 s. This matches `rules.in_flight` ("armed power
   countdowns are exempt until fires_at + timeout_ms").
2. **Rejections after the journal row exists are journaled as `failed`** (availability, target,
   entitlement, QUEUE_FULL), so an identical re-send gets the identical answer; rejections before
   step 5 (signature, identity, plan state, grant) are not journaled, exactly as the design orders.
   `COMMAND_ID_REUSED` never touches the original row.
3. **Routine-step check (design step 9) runs before the confirmation step** so a refused routine step
   never issues a challenge. Disruptive actions are never `routine_allowed`, so the observable
   behaviour is unchanged.
4. **Availability condition `windows` fails with `PLATFORM_UNSUPPORTED`** (the design's test list
   asks for this on Linux); `session_unlocked` fails with `PC_SESSION_LOCKED_MEDIA_ONLY` when the
   local "media while locked" setting is on, else `PC_SESSION_LOCKED`.
5. **Executable identity**: `approve-app` pins the SHA-256 of the `.exe`; `app.launch` refuses
   (`APP_NOT_APPROVED`, security event `approved_app_hash_changed`) when the file changed, so an
   updated application must be re-approved once. Window operations (focus/minimize/close) only need
   the approval row. Boundary: a local user who can edit `state.sqlite3` can edit the allowlist — the
   agent runs as that user and does not claim to defend against them.
6. **Provisional controller ids**: `pairing_request` carries no `controller_id`, so a locally
   approved grant is stored under `pending-<kid>` until the first `grants_snapshot` names the real id
   (`Store.apply_snapshot` matches provisional rows by kid). Until then a command from that kid fails
   `CONTROLLER_MISMATCH` (the record cannot bind). See `CONTRACT_ISSUES.md`.
7. **Local control channel** (`control.py`): the CLI talks to the running agent over a second IPC
   endpoint with the same identity rule as the bridge (named pipe `\\.\pipe\DoMe.Control.<hash>` /
   `control.sock` 0600). The pairing code is returned only to that local caller; it is never written
   to disk or logs. `pair` therefore requires the running agent; `status/enable/disable/approve-app/
   revoke/diagnostics` fall back to the SQLite store when the agent is not running.
8. **Unverifiable confirmations never terminate a command**: when the confirmation envelope does not
   verify (unknown kid, bad signature) the agent sends an `error` frame and keeps the pending
   challenge, so an attacker (or a relay bug) cannot cancel a pending confirmation with a forged
   frame. Verified-but-mismatching confirmations (wrong kid, digest, declined, expired, target
   changed) consume the challenge and end the command.
9. **Late results**: queued-not-started commands are journaled `failed/PC_OFFLINE` with `sent=1` on
   disconnect (the relay has already answered the controller); results of commands that were
   executing are kept `sent=0` and re-sent once after the next snapshot — the only case the relay may
   have closed as `outcome_unknown`.
10. **Mismatch storm** closes the socket with 4000 and lets the normal backoff run (no reset to
    attempt 0); the storm is recorded as `mismatch_storm_reconnect`.
11. **`revoked` and a rejected credential discard the credential; an identity mismatch (relay names
    another PC) stops reconnecting but keeps the credential** — the relay misbehaved, not the account.
12. **Entitlement claims (never the token) are persisted** so the 72 h grace can be honoured across a
    restart during an outage; a `200 {assertion:null}` clears them immediately.
13. **Thread model on Windows**: pystray owns the main thread, the asyncio agent runs in a worker
    thread, every tkinter object lives on a dedicated Tk thread fed through a queue. Headless mode
    (`DOME_AGENT_HEADLESS=1` or `--headless`) runs asyncio on the main thread with no UI imports, which
    is what CI and the integration tests use.
14. **Agent waits for a link**: `run` without a link does not exit; it polls `identity.json` every 3 s
    so `dome-agent link` can be run from another console (or the installer) and the agent connects as
    soon as the link exists.
15. **Store calls stay synchronous on the event loop**: every call is an indexed point operation on a
    local WAL database (sub-millisecond); only OS adapters run in threads.
16. **Authorization steps 3-4 are re-applied at every later transition** (`Authorizer.recheck_grant`):
    when a confirmation arrives (the command may have waited 60 s), as the executor precheck right
    before the handler runs, and for every pending confirmation / queued command / armed power
    countdown whenever a new `grants_snapshot` lands. A narrowed capability, `pc_enabled=false` or a
    `plan_disabled` controller therefore takes effect before the side effect, not only before
    acceptance (`rules.grants_snapshot`, `rules.plan_state`). Snapshot-triggered terminations are
    `canceled` with the specific code (`GRANT_MISSING` / `PC_PLAN_DISABLED` /
    `CONTROLLER_PLAN_DISABLED`); a confirmation that fails the re-check ends `failed` with that code.
    An *executing* command is never interrupted by a narrowing (only by revocation), because the OS
    call may already be under way.
17. **Lost browser answers for non-idempotent ops are `outcome_unknown`.** `BridgeServer.request`
    takes `non_idempotent`; once the `bridge_request` frame has been written, a timeout or a native-host
    disconnect raises `OUTCOME_UNKNOWN` (non-retryable) instead of `EXTENSION_DISCONNECTED`
    (retryable), and the executor turns that into `result{outcome_unknown}` + warning. Failures before
    the frame was written stay `EXTENSION_DISCONNECTED`, because the op cannot have run. The bridge, not
    the executor, decides, because only the bridge knows whether the frame left the process.
18. **Power countdown has an `issuing` phase that cancel cannot interrupt.** Once the countdown sets
    `issuing` and calls the adapter in a worker thread, `PowerManager.cancel()` returns
    `{canceled:false, action, command_id}` (the `power.cancel` command fails `ACTION_UNAVAILABLE` with
    that result) and the countdown's own completion reports what Windows did. Cancelling the awaiting
    coroutine would not stop the thread, so reporting `canceled` there would be a lie. For a
    restart/shutdown Windows already accepted, `power.cancel` calls `AbortSystemShutdownW` and reports
    `canceled:true` only when it returned TRUE. The adapter keeps `dwTimeout = 0` (the cancellable
    countdown is the agent's; `bForceAppsClosed=FALSE` lets Windows prompt about unsaved work), so on
    real Windows the abort window is effectively nil and the honest answer is `canceled:false` — see
    KNOWN_ISSUES.md.
19. **Local revocations are journaled until the relay has them.** `Store.revoke_grant_locally` revokes
    and inserts a `pending_revocations` row; `revoke_controller` is sent at once and, if the write
    failed (offline), re-sent after every `grants_snapshot` until it succeeds. `apply_snapshot` also
    reports locally revoked controllers the relay still lists (`still_listed_revoked`) and those are
    re-sent too (idempotent on the relay). The CLI's offline fallback says plainly that the service has
    not been told yet.
20. **An invalid relay URL is `configuration_error`, not a rejected credential.** Only `POST
    /v1/agent/token` → 401/403 is `credential_rejected` (credential discarded, re-link). A malformed
    `DOME_AGENT_RELAY_URL` / `relay_url` stops connecting, keeps the credential and surfaces
    "relay URL is invalid" in the tray notification and `dome-agent status`.
21. **A socket write is not delivery** (cross-component review, 2026-10-09). `RelayClient` records the
    wall-clock instant of the last inbound frame per connection (`last_inbound_at`, rolled into
    `previous_last_inbound_at` when a new connection is established). After the first snapshot of a
    connection, `_resend_late_results` re-sends every terminal result that was never written to a
    socket **and** every one finished after the previous connection's last inbound frame
    (`Store.journal_terminal_finished_after`), whether or not `sent` was set: a frame written into a
    half-open socket never reached the relay, which by then has told the phone `outcome_unknown` and
    accepts one correction per command. This also delivers the `failed/PC_OFFLINE` verdict for queued
    commands dropped at disconnect, so the phone learns that nothing ran.
22. **Input sessions retire the id before anything is released** (`InputSessionManager._end`): state →
    `ended`, id into the retired ring (64 ids), queue cleared, the in-flight dispatch (a worker thread)
    awaited for up to 2 s, THEN `release(held_buttons, held_keys)`, THEN `input_session{ended}`. A
    delayed batch for a retired id gets `INPUT_SESSION_EXPIRED` and can never press anything again;
    the dispatch thread also re-checks `session.live` before every event.
23. **Acks count the phone's events, not the coalesced injections.** `coalesce_events` returns
    `(event, n)` pairs so `accepted_events` / `dropped_events` match what the controller sent (its own
    bookkeeping) while adjacent motion is still one `SendInput` call.
24. **Event-type coverage (`INPUT_NOT_PERMITTED`) is the manager's check**, using the session's
    pointer/keyboard flags that always mirror the grant's effective capabilities
    (`apply_capabilities` on every snapshot and every local change). The agent applies steps 3-4
    (remote switch, plan state, live grant) first. This way the drop is counted in the acks instead of
    vanishing in an error frame.
25. **(Superseded by #40.) Target change stops typing once, then the customer continues.** The spec forbids silently
    stealing focus and asks to stop pending text when the known target changes; it does not ask the
    agent to keep refusing forever. After one `INPUT_TARGET_CHANGED` the new foreground identity becomes
    the target (the phone shows it via `pc_state.foreground_app`); a user-directed click re-captures it
    immediately. Pointer events in the same batch still run (ordering preserved).
26. **`\n`, `\r\n` and `\t` inside a text event are rendered as the Enter / Tab keys**, everything else
    as `KEYEVENTF_UNICODE` code units. `WM_CHAR` U+000A/U+0009 are not accepted by ordinary edit
    controls; rendering them as the key the customer would press keeps the text literal. The phone
    sends an explicit Enter key event anyway.
27. **(Decision kept, mechanism replaced by #41.) Secure desktop ends the session; an elevated foreground
    window only restricts.** The watchdog
    ends with `secure_desktop` when `input_restricted()` is true and the foreground is unknown or not
    elevated (lock screen, UAC/consent desktop); an elevated window in front keeps the session alive
    (the customer can click elsewhere), reports `pc_state.input_restricted` and makes injections fail
    `INPUT_RESTRICTED`.
28. **A suspended session releases its holds immediately** (a stuck drag must not wait for the lease),
    keeps its id answering `INPUT_SUSPENDED`, and is ended by the lease watchdog (`lease_expired`) or
    replaced by the next `input.session_start`. Backpressure is measured as dispatch lag (monotonic time
    since the batch was accepted), not as the controller's `issued_at`: the receipt check already
    bounded the latter, and the rule speaks of dispatch falling behind.
29. **A start from the controller that already owns the session is a restart**: the old session ends
    with reason `stopped` (there is no `restarted` reason; `pc_switch` is what the PWA sends as a stop
    before leaving, see CONTRACT_ISSUES.md #9) and its holds are released before the new id exists.
30. **Error frames for dropped batches are rate-limited to one per code per second per session**; the
    counts travel in the acks (≤ 4/s). Forty stale batches per second would otherwise produce forty
    error frames per second on the controller socket.
31. **One failed injection drops the rest of its batch.** Continuing after a failed event would run the
    remainder out of order relative to what failed; the next batch starts clean. Failed `down` events
    are not recorded as holds.
32. **The instance lock lives in `cmd_run`, not in `Agent.start()`**, so tests and embedded agents can
    construct an `Agent` without taking it; the control op `ping` reports the pid so `status`/`repair`
    can name a running instance even when the pid file is missing; the inspection probe acquires the
    lock with `probe=True` (no pid file written or removed).
33. **Local grant changes are journaled by kid** (`pending_grant_updates`): a provisional controller
    (`pending-<kid>`) cannot be named to the relay until the first snapshot resolves its id, after
    which the pending `grant_update` is sent with the real id. `grant_update.capabilities` has
    `minItems: 1`, so withdrawing the last capability is refused: revoke the phone instead.
34. **Power confirmation detail** (`authz.power_confirmation_detail`): "N s countdown, cancellable from
    the phone. Sleeping/Restarting/Shutting down can interrupt or end remote access to this PC. DoMe
    cannot wake it or turn it on again remotely (no remote wake in V1)." — ≤ 200 characters, shown
    verbatim by the phone.
35. **Relay disconnect ends the session with `controller_disconnected`; agent stop with
    `agent_restart`.** The frame for the disconnect case cannot be delivered (the socket is gone) and
    is sent best-effort; the phone learns it from the relay's own disconnect handling and the id is
    retired locally so no reconnect can replay it.
36. **`input.session_start` refuses `PLATFORM_UNSUPPORTED` in the handler** because the registry's
    availability list for it lacks `windows` (CONTRACT_ISSUES.md #8): a session that can never inject
    must not be started.
37. **The crash-recovery file carries the session and controller ids** (no content) so the restarted
    agent can send `input_session{ended, agent_restart, holds_released}` after its first snapshot; if
    the release itself fails the file is kept so the next start-up retries.
38. **`SUPPORTED_PROTOCOL_VERSIONS = ("1.0", "1.1", "1.2")` is announced everywhere** (relay hello, bridge
    error detail): the MINOR rule makes every listed version acceptable and the e2e test asserts the hello
    list. The bridge test that expected `["1.1"]` alone contradicted that and was corrected; it now expects
    all three. Commands are stamped with their action's `since` (1.2 only for the window and search
    actions), so a 1.2 agent still serves 1.0/1.1 phones.
39. **Input age is judged against the phone's own clock** (review finding). `issued_at` is the phone's
    stamp and `rules.input_sessions` says `now - issued_at <= input_age_budget_ms`, but the envelope
    window admits ±5 s of skew while the budget is 1 s: read literally, a phone 1 s behind kills the
    touchpad and a phone ahead hides stalls. `AgeEstimator` keeps, per session, a windowed (30 s)
    minimum of `delta = receipt − issued_at` (= latency − clock offset), seeded by the
    `input.session_start` command's own `issued_at`; a batch's age is `delta − minimum`, i.e. its delay
    over the fastest recent batch, independent of a constant offset. The baseline is clamped to what
    the envelope can admit (`[-max_clock_skew, lifetime + max_clock_skew]`); samples expire after 30 s
    (longer than any batch the envelope can admit) so a phone clock correction is followed. The relay's
    `received_at` feeds a second estimator per relay connection id and the larger age wins, bounding
    the relay → agent leg. The dispatch-lag (backpressure) check is unchanged. Residual: the very first
    batch after a stall that ALSO delayed the start command is judged against that late seed; the
    envelope window still bounds it. CONTRACT_ISSUES.md #16 proposes rule wording.
40. **A target change blocks keyboard input until a deliberate re-capture** (review finding; replaces
    #25). Dropping only the rest of the current batch let batches the phone had already streamed type
    into the new window. Now `keyboard_blocked` is set on the change (one `INPUT_TARGET_CHANGED`); every
    later text/key/shortcut is dropped and counted until (a) a `pointer_button` down/click/double_click
    of this session (user-directed click: target re-captured), (b) a fresh `input.session_start`, or
    (c) a batch whose estimated issue time (receipt − age, #39) is ≥ `target_change_grace_seconds` (2 s)
    after the block. (c) exists for keyboard-only grants, which cannot click: 2 s is far longer than a
    state frame takes to reach the phone, and the PWA pauses live typing when `foreground_app` changes,
    so keyboard input issued that late is a deliberate continuation. Pointer events are never blocked.
41. **Secure desktop vs elevated window is asked of the adapter, live** (review finding). The watchdog
    used the cached foreground (2 s refresh, None during a switch, `elevated` None when unknown) to
    decide, so clicking Task Manager could end a session as `secure_desktop`. `InputAdapter` gained
    `secure_desktop_active()` (Windows: the input desktop cannot be opened or is not `Default`); only
    that, or a locked session, ends the session. `input_restricted()` (secure desktop OR known-elevated
    foreground) only sets `pc_state.input_restricted` (a state frame is requested when it flips).
    Keyboard events are checked live once per batch: when restricted they fail `INPUT_RESTRICTED`
    instead of reaching `SendInput`, because UIPI drops input to a higher-integrity window WITHOUT an
    error and the agent would otherwise ack it as accepted. Pointer events still run so the customer
    can click elsewhere; a click on the elevated window itself is dropped by Windows unseen (documented,
    not detectable).
42. **Holds after a bounded in-flight wait; click on a held button.** `_end`/`_suspend` still wait at
    most 2 s for the running adapter call (a session end must not hang on a stuck `SendInput`); if the
    call outlives the wait, a done-callback releases whatever it pressed meanwhile as soon as it
    returns. `_release` removes only what it released (not a blanket `clear()`, which could lose a
    concurrent press) and rewrites the recovery file for the CURRENT session, so a late release of an
    old session cannot delete a newer session's file. `click`/`double_click` on a held button removes
    it from `held_buttons` (Windows sent down+up, so the button is up).
43. **Shortcut recovery releases the letter and CTRL; a failed recovery keeps them tracked.** During a
    shortcut the session tracks `ctrl` and the shortcut name (its letter key, released by the Windows
    adapter via `SHORTCUT_KEY`) so crash recovery covers both. On a partial insertion the adapter sends
    key-up for the letter and CTRL; if that fails too it raises `InputHoldError(stuck_keys)` and the
    manager keeps those keys in `held_keys` for the end-of-session release and `input_holds.json`.
44. **One agent per Windows account's state directory.** The `Local\` mutex is per logon session but
    `%LOCALAPPDATA%\DoMe` (identity, credential, grants, `state.sqlite3`, `agent.pid`) is per account.
    A second agent of the same account in another session would share the PC identity and supersede
    the first at the relay (4001). `dome-agent run` refuses before taking the lock when `agent.pid`
    names a live process recorded in another session (pid reuse is excluded by comparing the process's
    actual session with the recorded one) and says "DoMe already runs for this Windows account in
    session N"; `status` reports the same conflict. No flag overrides it: two agents with one identity
    cannot both work. Different Windows accounts have different state directories and are unaffected.
45. **No window title in the `input.session_start` result; `--yes` never grants input.** Results are
    journaled durably (SQLite) for duplicate re-emission, and spec §15 avoids storing window titles, so
    the result's `foreground_app` omits `window_title`; the phone gets the title from the next
    `pc_state` frame (memory only; a state frame is requested at session start). `pair --yes` /
    `pair-approve --yes` approve the pairing and its non-input capabilities only; `pointer`/`keyboard`
    need `--pointer`/`--keyboard` (or an explicit `--capabilities` list), keeping the PC owner's
    per-capability decision explicit (spec §10A-D).
46. **F11 only where it means full screen; never decided on a minimised read; one window sequence at a
    time.** `app.set_fullscreen` (and maximise/restore leaving full screen first) presses F11 only into an
    app on `platform.protocol.F11_FULLSCREEN_PROCESSES` (browsers, File Explorer, VS Code, Windows
    Terminal, Notepad++, Sublime Text) or one the PC owner opted in (#49). Elsewhere F11 does something
    else (Excel: a chart sheet from the selection; Visual Studio: Step Into, which starts debugging; Word:
    next field), so those apps are `FULLSCREEN_UNSUPPORTED` (or `WINDOW_STATE_UNCHANGED` when already in
    their own full screen) with nothing pressed and the window untouched. A minimised window reads as not
    full screen while Windows shows it again in its previous placement, full screen for one minimised in
    F11 full screen; a minimised window without a caption is therefore shown again (`SW_RESTORE`) and read
    before anything is decided, and `_press_f11` reads the state again after the window came to the front
    (no press when it already matches). The handlers accept maximised/normal only when not in full screen.
    `asyncio` cannot cancel the worker thread of a timed-out command, so `WindowControl` (and the fake)
    runs one sequence at a time under a re-entrant lock (the next waits, bounded by its deadline, then
    reads what the previous one left), and the handler passes a deadline 1 s before the command's timeout
    after which F11 is never pressed, plus whether keys are held from the phone (maximise/restore of a
    full-screen window press F11 too). `mark_side_effect` is not added to maximise/restore: they are
    idempotent, so the executor's timeout outcome does not depend on it (queue.py `_timeout`). The
    `AttachThreadInput` step is skipped when the foreground window is hung: a shared input queue makes the
    focus calls synchronous and they would wait on the frozen app. Leaving full screen without a
    `window_id` targets the app's full-screen window (several Edge windows); entering keeps the front-most
    window.
47. **A browser window's title never leaves the PC; no window title is journaled.** A browser window's
    title is its active tab's page title, and on a YouTube results page that is the search query
    (`rules.search_queries`). `app.list` names a browser's windows (`Microsoft Edge`, `Google Chrome`, ...)
    from the approved executable's name, `ForegroundApp.as_result` omits `window_title` whenever `browser`
    is set and `WindowsInput.foreground` does not even read a browser's title, so `pc_state` names the
    browser only (the phone shows "Edge"; it can no longer tell two tabs apart by title). Other windows'
    titles still reach the phone in `app.list`, but its journaled copy has every title blanked
    (`actions.journal_view`; a duplicate or late re-send shows "Untitled window"), like the
    `input.session_start` result (#45).
48. **Every YouTube answer is scrubbed and bound to its browser.** `scrub_tab` (no title on a page
    without a video) is applied to the `list_tabs` merge and to each op's result tab before it is checked,
    cached, journaled or returned, so an older extension that reports a results page's title (the query)
    cannot leak it through results either. `open_result` must report the targeted tab (same
    `browser_instance_id` and `tab_id`), else `INTERNAL`; `BridgeServer.note_tab` takes the instance the
    request went to and ignores a tab naming another one, as `_event` does, so one connection's answer can
    never rewrite another browser's tab records (their `tab_token` feeds target resolution).
49. **Full screen with F11 is the PC owner's per-app opt-in** (round 3: the owner's "full screen zstream"
    could never work). `approved_apps.fullscreen_f11` (default off) lets F11 be pressed in an app off the
    built-in list; an app on the list gets F11 whatever the flag says (the tray shows its box ticked and
    disabled, and the Add dialog pre-ticks it only for those). The column is added to an older database at
    start-up (`Store._ADDED_COLUMNS`: `ALTER TABLE … ADD COLUMN … NOT NULL DEFAULT 0`, idempotent;
    existing approvals read as off, nothing is rewritten, and an older agent still reads and writes the
    table because it names its columns). Re-approving (after an update, *Find it again…*) keeps the
    choice; only an explicit value changes it. It is set only on the PC — the tray's Approved apps window,
    `dome-agent app-option <app_id> --fullscreen-f11 on|off` (control op `app_option`; falls back to the
    store when the agent is not running), `approve-app --fullscreen-f11` — never over the wire: `app.list`
    and the protocol are unchanged. The handler reads it from the approval for each command and passes it
    to the adapter (`fullscreen_f11=` on `maximize`/`restore`/`set_fullscreen`, mirrored by the fake).
    Every other F11 safeguard still applies. When F11 was needed but the app is not opted in, the adapter
    raises `F11NotEnabledError` (`FULLSCREEN_UNSUPPORTED` entering, `WINDOW_STATE_UNCHANGED` when the app
    is in its own full screen) with nothing pressed, and the handler words the message with the app's
    approved display name only (never a window title or search text): "Full screen is not turned on for
    ZStream. On the PC: DoMe tray > Approved apps > ZStream > Full screen with F11. Or use Maximise." A
    change calls `state.request_update()` on the agent loop (the tray goes through
    `Agent.set_app_fullscreen_f11_local`; it is one indexed UPDATE, so it runs inline like every store
    call).
50. **A Tk that cannot open a window is reported at once.** `TkThread` treats an exception from `tk.Tk()` (no
    display, no desktop session) like a missing tkinter: it logs, marks itself ready with no root and ends,
    instead of dying in its thread and leaving `start()` to wait out its 5 s. The tray then offers the CLI,
    and the Tk window test skips with that reason. Tests that run a real Tk on a worker thread quit the root
    and never destroy it: destroying it and then freeing it from another thread aborts the process
    ("Tcl_AsyncDelete: async handler deleted by the wrong thread"), reproduced under Xvfb.
51. **One PyInstaller folder for both programs; the contract without its test fixtures.**
    `packaging/dome-agent.spec` builds `DoMe.exe` and `dome-native-host.exe` into one COLLECT
    (`dist\DoMe\`, sharing `_internal\`); `dome-native-host.spec` is gone. While DoMe is stopped the
    browser starts the host every 1–8 s and then every 30 s (browser-extension K5): a one-file host
    would unpack ~10 MB to `%TEMP%\_MEI*` on every start, trigger antivirus rescans, leak folders when
    killed and start slower than the 10 s hello deadline. `install-native-host` already looks for the host
    next to `DoMe.exe`. The bundled contract is `actions/errors/plans/version.json` + `schemas/` only:
    the old spec shipped `shared/protocol/fixtures`, including a test private key, and
    `build_installer.py` now fails if any private key is in the folder. Both programs carry
    `brand/exports/windows/dome.ico`; the spec stops if it is missing.
52. **`quit` and `start-at-login` exist for Setup; Setup never kills DoMe.** `dome-agent quit` is the
    tray's Quit DoMe (control op `quit`), then waits until the agent's pid has exited and the instance
    lock is free (`single_instance.wait_for_exit`); when the lock is held but the control channel does not
    answer it retries for up to 10 s (DoMe may be starting) and then exits 1 without touching it. Setup
    runs `uninstall-native-host` and `quit` before it replaces files and stops with "DoMe is still running
    and did not close" (exit code 7, nothing changed, the host registered again) when DoMe still holds its
    `Local\DoMe.Agent.<session>` mutex; the uninstaller asks the user to quit DoMe and stops on Cancel.
    Restart Manager stays a backstop for other files only: pystray's window ignores `WM_CLOSE`. Every
    DoMe.exe Setup runs gets 60 s (CreateProcess + a message-pumping wait in `[Code]`); only that command
    is terminated after it. `start-at-login on|off|status` uses the tray's adapter and `startup_command()`,
    so the Run value is exactly what the tray reads; it does not write the store's unused copy.
    `start-at-login`, `install-native-host` and `uninstall-native-host` build only the adapter they need
    (`platform.build_startup` / `build_native_host_registrar`, not the whole platform) and, like `quit`,
    create no state folder, so an uninstall for someone who never started DoMe leaves nothing behind.
53. **`install-native-host` and `repair` keep the extension already registered here** when no production
    id and no `DOME_AGENT_DEV_EXTENSION_ID` exist (`bridge.manifest.resolve_origins`): an upgrade
    unregisters the host while it replaces files and must register it again, and the tray's Repair failed
    without the environment variable. Only a well-formed `com.dome.agent` manifest whose every origin is a
    `chrome-extension://<32 a-p>/` origin is trusted; it is the user's own file, so this adds no trust. A
    build with production ids never reads it.
54. **The server address comes from `dome-server.json`, not an environment variable.** Setup writes
    `{"api_url": "https://domeremote.com"}` (or a development build's `/ApiUrl=`, remembered for upgrades)
    next to `DoMe.exe`; only `link` reads it (`settings.installed_api_url`, frozen builds only, https
    origins only), after `--api-url` and `DOME_AGENT_API_URL`. A persistent `DOME_AGENT_API_URL` would
    override the addresses the service returned at link time for every later run (`agent.py`), so a PC
    linked through the test kit would send its test credential to production, get a 401 and discard it.
    Setup also removes every `DOME_AGENT_*` variable from the environment of the programs it runs.
55. **Uninstall keeps `%LOCALAPPDATA%\DoMe` by default — a reasoned deviation from spec §15.** The
    uninstaller always removes the program, the native-host keys and manifest, start at login and
    `StartupApproved`, but deletes the link, the DPAPI key and credential, the paired phones and their
    touchpad/keyboard grants, the approved apps and the logs only when the user answers Yes (default No)
    or a silent uninstall passes `/REMOVEDATA`. Deleting them is irreversible (the PC must be linked and
    every phone paired again) and the same folder holds the test kit's link; a reinstall should just
    work. The final message tells the user to remove the PC in the phone app (the agent cannot unlink
    itself on the service) and the browser add-on. Kept data is still protected by DPAPI (current user).
    Empty `logs`/`secrets`/`diagnostics` folders and an empty `%LOCALAPPDATA%\DoMe` are removed.
56. **A crash in a frozen program never shows the bootloader's modal box.** Both entry scripts run through
    `crashguard.run_guarded`: an exception ends in `<state dir>/logs/crash.log` (and stderr when there is
    one) and exit code 70; `SystemExit` (argparse) passes through. Setup waits for its DoMe.exe commands
    and the browser starts the host in the background: a box nobody sees would block them. Only the tray
    (`run` without `--headless`) shows a short plain-English message with the file's path. A frozen
    `DOME_AGENT_PLATFORM=fake` (excluded `dome_agent.testing`) now ends this way too, and Setup clears
    that variable anyway. `cli._flush_stdout` makes `link` and `pair` work without a stdout (a windowed
    `DoMe.exe` started from the Start menu has none).
