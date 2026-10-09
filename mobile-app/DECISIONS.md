# mobile-app — decisions

Choices made where `docs/design/mobile-app.md` and the spec are silent. ADR-0001 is not re-opened.

1. **No `eval` protocol layer.** cloud-api serves the PWA with `script-src 'self'`, and
   `@dome/protocol`'s registry compiles Ajv validators with `new Function` at import time. The PWA
   precompiles every validator it needs from the frozen schemas (`pnpm gen:validators` → Ajv standalone
   code in `src/protocol/generated/`) and a Vite plugin resolves the library's internal `./registry.ts`
   import to `src/protocol/registry.ts`, an API-identical facade. `test/protocol-parity.test.ts` fails
   if the generated code is stale or disagrees with the real module. Everything else in the shared
   library (strict JSON, keys, signing, command building) is used unchanged.

2. **Subscribe to every PC of the account.** The relay refuses PCs on which this controller holds no
   grant with `error{GRANT_MISSING, ref_pc_id}`; the PWA records that per PC and shows "Not paired with
   this phone" instead of hiding the PC. One socket, at most 16 subscriptions (schema limit).

3. **Staleness.** State is stale after (re)connect, visibility→visible, `online`, when the relay says
   the PC is not online, when a subscription is refused, or when the last `state` frame is older than
   75 s (the agent sends every 30 s). Stale ⇒ every consequential control is disabled and the status
   pill reads "Online · refreshing" rather than "Online". The 75 s rule is re-evaluated without any
   frame: `Runtime.start()` advances `useLiveStore.now` every 5 s and `useSelectedPc`/`useLivePcs`
   apply `withTimeStaleness` against that clock (a state frame received after the last tick stays
   fresh). On a resume nudge with an apparently open socket the client re-sends `subscribe` (the relay
   answers with `pc_status` + cached `state` at once), pings with `t`, and reconnects immediately when
   nothing arrives within 5 s (`NUDGE_DEADLINE_MS`) — a suspended iOS PWA's socket often reports open
   although it is dead.

4. **Target choice** follows spec §9 verbatim (`src/lib/targets.ts`): explicit selection while that
   attachment still exists, else the single controllable tab, else the single playing tab, else ask.
   Targets always include `tab_token` and `expected_video_id` when known.

5. **Confirmation modal binding.** Approve is offered only when the challenge matches the command this
   phone sent (command, PC, controller, action, params, target). Otherwise only Decline is shown with
   the reason. Primary line from the registry labels; `display.pc_name` and `display.action_label` are
   never rendered; `display.detail` is plain secondary text labelled "Reported by the PC".
   The modal never traps the app: once the challenge expired, or while the relay socket is not open
   (`Runtime` flags every pending confirmation `connectionLost` when the status leaves `open` and
   clears it on `open`), a Close button sets `decision: "dismissed"` locally
   (`CommandService.dismissConfirmation`) — no frame is sent, the PC discards the challenge at
   `expires_at`, and a later `result` frame still lands on the record. Over a dead socket the countdown
   is replaced by "Connection to DoMe lost". `CommandService` checks `connected()` before identity so a
   dropped socket reads as PC_RECONNECTING, never as "this phone is not paired".

6. **"No answer" is not "failed".** A local timer (expires_at + action timeout [+ 60 s for challenge
   actions] + 10 s) flags a command as *no answer yet*; the relay's own deadline rule produces the
   terminal frame. Post-terminal frames are dropped except an agent correction of a relay
   `outcome_unknown` (version.json `late_results`).

7. **Sign-out keeps the key.** `clearAccountState()` wipes controller id and selected PC; the key stays
   so signing back in needs no re-pairing. "Forget this installation" (Settings) deletes the key.
   Both are customer-initiated and must never race the shell's automatic sign-in redirect: the
   session store distinguishes `signed_out` (the server's verdict — REST 401 or relay close 4008 —
   which `RequireSession` answers with the `loginUrl` redirect) from `signing_out` (set first thing in
   `Runtime.signOut()`, rendered by `RequireSession` as a neutral "Signing out" screen, never a
   redirect; a 401 from the logout call or a 4008 close during the flow leaves it alone). The order
   inside `Runtime.signOut({ forgetInstallation })` is: `signing_out` → close the socket (no reconnect
   can mint a new key) → delete the key and await the IndexedDB transaction → server logout → reset
   stores → clear account state. The Settings page navigates exactly once, to `/`, after that
   resolves, through the `navigation()` seam so tests can observe the single navigation.

8. **Pairing code handling.** The deep-link fragment is read once and scrubbed with
   `history.replaceState` before React renders anything else; the code lives in component state only
   until the claim is sent and the verification code computed, then it is discarded. Polling every 2 s
   (4 s after a transient error) with `AbortController` on unmount. When the phone is signed out, the
   fragment is NOT forwarded through sign-in: `RequireSession` builds `return_to` from path + query
   only, scrubs the fragment from the address bar first, and adds the non-secret `scan_again=1`
   marker so the pairing page asks for the code again (`src/app/navigation.ts`; `loginUrl` strips any
   fragment as a second line of defence). The code is deliberately not stashed in sessionStorage
   either — re-scanning is cheaper than a stored secret.

9. **Camera**: `BarcodeDetector` (`qr_code` format) when present; otherwise `getUserMedia` frames
   decoded by jsQR, which is loaded lazily only in that case. Camera errors fall back to typing.

10. **Routing**: `/pair#code=…` redirects to `/app/devices/pair` keeping the fragment (client-side
    only; see 8 for the signed-out case); `/link` is a standalone session-guarded page without the app
    tab bar (it is opened from the PC's browser flow).

11. **Pro preview without checkout** (Phase C not built): Routines shows example routines as disabled
    cards; Billing shows plan, limits and the planned price from `plans.json` with "not available yet".

12. **Chunking**: the protocol layer is its own chunk; jsQR is a lazy chunk. The app is served from the
    API origin, so no CORS configuration exists; `VITE_DOME_API_ORIGIN` is accepted only for unusual
    setups and switches `fetch` to `mode: "cors"`.

13. **Test doubles** live only in tests (`FakeSocket`, fake timers, `fetchImpl` injection,
    fake-indexeddb, `setNavigationForTests` for the sign-in redirect). No runtime flag selects a fake
    transport or a fake navigation.

14. **Power request after a disconnect.** `pc_status.last_power_request` is written by the relay at
    forward time (command still `created`), so on its own it only proves a request. The dashboard says
    "A shutdown was requested …; DoMe cannot tell whether it ran" and upgrades to "Windows accepted …"
    only when this phone's own record for that PC/action (within 10 min) has an `executing` ack, an
    agent `succeeded`, or a relay `outcome_unknown` (which the relay emits only after an executing ack)
    — `src/lib/power.ts`.

15. **Log redaction keys.** The redaction regex names pairing material explicitly (`pairing_code`,
    `code_hash`, `user_code`, `verification`) instead of the bare word `code`, so stable protocol error
    codes (`api.error`, `command.result`, `relay.invalid_frame_dropped`) survive into diagnostics. No
    call site logs the pairing code under any key.

16. **REST 401 mid-session.** `Runtime` wires `configureApi({ onUnauthenticated })` to clear the
    session store and close the socket, so `RequireSession` redirects to sign-in on the next render
    instead of leaving a signed-in-looking UI whose requests all fail.

17. **Connection check gating.** `system.ping` is a `status`-capability, risk-`low`, non-consequential
    action, so Settings gates it only on the socket being open and the relay reporting the PC
    `online` — not on the 75 s freshness rule or `remote_enabled`. It is precisely the diagnostic a
    customer needs while the pill reads "Online · refreshing" or remote control is off on the PC; the
    PC answers `PC_RECONNECTING` / `PC_REMOTE_DISABLED` honestly and `recoverySteps` explains both.
    Every consequential control keeps the stricter `canControl` rule.

18. **Confirmation header PC.** `GlobalConfirmation` resolves the PC name from the device inventory by
    the *challenge's* `pc_id` (falling back to "your PC"), not by the record's `pcId`, so when the
    binding check reports "names a different PC" the header agrees with the warning.

19. **Voluntary socket closes use 1000.** The contract reserves 4000 for protocol errors; the client
    closes with 1000 for the resume nudge, the post-pairing rebind, hello/idle timeouts and sign-out.
    Browsers only allow 1000 or 3000–4999 from script, so 1001 was not an option. Invalid inbound
    frames are dropped without closing, so the client sends no 4000 at all.

20. **Text-command multi-verb guard** (`intents.ts`): the verb regex is deliberately *not* global.
    A global regex used with `.test()` keeps `lastIndex` across calls, so the second segment of
    "pause youtube and shut down" was scanned from the end of the first match and the guard never
    fired (the injection tests passed only because every rule is `^…$`-anchored). Tests now assert the
    guard itself and its statelessness across repeated calls.
21. **The relay client signs a hello proof on every connect** (cross-component review, 2026-10-09).
    `RelayClient` takes a `helloProof(kid)` callback; the runtime answers with an ES256 envelope over
    `hello_proof{kid, account_id, issued_at, expires_at, nonce}` built by `@dome/protocol`
    (`buildHelloProofPayload`/`signHelloProof`, validated by the no-eval facade) when a session is
    known, else `null` so the socket opens unbound. The relay binds the socket to the paired controller
    only after verifying the proof (`rules.controller_socket_identity`); a bare kid, which every
    account member can read from the device list, is no longer enough to observe this phone's PCs.

22. **Double tap = two immediate clicks (protocol 1.1 touchpad).** The gesture machine never delays a
    tap's click to find out whether a second tap follows (that would add the whole double-tap window,
    300 ms, to every single click), and it never sends the `double_click` primitive after a click it
    already sent (click + double_click = three presses, which selects a paragraph in many editors).
    Two quick taps therefore become two `pointer_button click` events and Windows' own double-click
    time (500 ms default) combines them; the machine marks the second one `double` so tests and the UI
    can see the threshold (300 ms, 24 px). A real `double_click` primitive is offered as the
    **Double** button under the surface. Not device-tested: on a slow link the second click can arrive
    late and count as a single click (KNOWN_ISSUES 9).

23. **Drag mode is a drag lock.** Entering Drag mode presses the left button on the next touch and the
    hold survives lifting the finger, so the customer can reposition across a large distance; only
    End Drag, Stop Input, leaving the page, pointercancel/lost capture/rotation/hidden, or the PC
    ending the session release it. The surface shows a conspicuous "DRAG MODE — holding the left
    button" state the whole time. The release path never produces a click.

24. **One finger → two fingers never clicks; after a two-finger gesture nothing clicks or moves until
    every finger is up.** The machine discards the first finger's pending tap when a second arrives
    (`two`), completes a right click only when both fingers were down ≤ 250 ms and nearly still
    (`two_release`), and otherwise settles silently (`two_settling`), so the finger left behind after a
    scroll cannot jump the cursor or tap.

25. **Live typing maps only edits at the end of what this phone sent, with certain deletions.**
    `LiveTyping.known` is the text committed to the PC in this run — never a mirror of the PC field.
    An edit is forwarded only when it touches the end of `known` and every removed code unit is a
    plain BMP character (no surrogates, combining marks, variation selectors, ZWJ, Indic/Thai/Hangul
    jamo clusters). Everything else pauses live entry and switches to Compose and Send with the
    reason; the phone never sends Ctrl+A/Backspace to "repair" text it cannot see (spec §10A B). IME
    compositions commit once at compositionend by diffing against `known`, so a trailing or leading
    `input` event (iOS ordering varies) sends nothing twice.

26. **Enter is a key, never a newline.** `beforeinput` with `insertLineBreak`/`insertParagraph` is
    prevented and sent as `key enter`; a Backspace in an empty textarea is sent as `key backspace`
    only when nothing of ours could have been deleted instead. Nothing is submitted after text on its
    own.

27. **Compose and Send success = the PC's `input_ack` covering that batch's seq.** The client's
    `awaitAck(seq)` resolves `accepted` when `last_seq ≥ seq` without new drops, `dropped` when the
    cumulative drop counter rose meanwhile (the batch may or may not have been among them), `timeout`
    after 3 s, `ended` when the session stopped first. Only `accepted` clears the buffer; every other
    outcome keeps the text in memory in a review state with Discard / Send again, and nothing is ever
    resent automatically. Acceptance means Windows accepted the events, which the copy says; whether
    the field changed is only visible on the PC.

28. **Keepalive and flush cadence.** Pointer motion is coalesced into the pending queue and flushed
    once per animation frame (`requestAnimationFrame`, injectable for tests); batches are capped at
    the PC's `max_batch_events` (≤ 64) and split in order; signing is serialised so `seq` is strictly
    increasing even though WebCrypto is async. An empty batch goes out every ~1 s while nothing else
    was sent (lease 3 s). A pending frame is dropped, never sent late, when the session ends, is
    suspended, the page hides, the PC switches or the socket drops.

29. **`input.session_start`/`stop` reuse the command path; stop during sign-out keeps an identity.**
    `Runtime.signOut` enters `signing_out` first (DECISIONS 7) which empties the session store, yet the
    `input.session_stop` that releases the PC's held input must still be signed for the account it
    belongs to, so `Runtime.identity()` keeps the last account id for exactly the `signing_out`
    window. Stops are sent before the socket closes and never awaited for a result.

30. **INPUT_* and RATE_LIMITED error frames with `ref_pc_id` go to the input client, not to the
    subscription state.** The contract reuses the error frame (no command id exists for a batch); the
    runtime asks the input client first and only then treats a `ref_pc_id` error as a refused
    subscription (GRANT_MISSING). Fatal codes (INPUT_SESSION_EXPIRED/REQUIRED, PC_OFFLINE/RECONNECTING
    while live) end the session; INPUT_SUSPENDED suspends it; the rest are shown as a dismissible
    notice while the session continues.

31. **Health screen diagnoses observable state only.** `assessHealth` is pure over the stores: phone
    connectivity, account session, PC relay connection (an unreachable PC is "cause unknown"), local
    remote-control/lock state, manual-input grant (+ `input_restricted`), extension, media target —
    each with exactly one next action (PC-side actions are instructions, not buttons that pretend to
    act). Retry is bounded to 3 attempts and only nudges the socket and refreshes inventories; it
    never resends a command. The walkthrough returns to the first undone step.

32. **Support form only when signed in; failure never claims receipt.** The public Support page keeps
    self-help for everyone and shows the form after `GET /v1/session` succeeds (the POST needs the CSRF
    token). The category comes from the help link (`?category=…&code=…`, validated against the enum
    and `^[A-Z_]+$`). Diagnostics are attached only after the customer opened the preview; the text is
    bounded to 32 768 characters by dropping the oldest log entries first. A failed POST shows "Not
    sent", the redacted summary in a copyable textarea and a Try again — no reference, no "received".

33. **Upgrade copy lives in one component.** `ProExplanation` is rendered only by the Routines and
    Custom remotes pages (both reached deliberately from More), names the selected benefit, is
    dismissible and never mentions connectivity. `UpgradeAndHealth.test.tsx` renders Dashboard,
    Remote, Touchpad, Devices, Health and the shell (online and offline) and asserts no upgrade copy.
    `recoverySteps` for plan-limit codes still mentions Pro (a plan state, not a repair for a fault).

34. **Brand icons are copied from `brand/exports`, not re-rendered here.** `scripts/make-icons.mjs`
    now points at `brand/icon.svg` as the favicon source and installs the brand's committed PNG/ICO
    renders of that same SVG (checked against `brand/exports/manifest.json`), because re-running the
    brand export script would write outside `mobile-app/`. The manifest gains the 192 px maskable icon;
    `index.html` gains PNG/ICO favicon links.

35. **Log redaction gains `text`/`composer`/`events`/`key(s)`.** No call site logs typed content; the
    regex (`^text$|_text$|composer|^events$|^key$|^keys$`) guarantees it would not survive if one did.
    `context` is deliberately not matched (`^text$` anchors) so YouTube tab contexts stay loggable.

36. **Live typing treats any loss of certainty as a pause (review fix, blocker).** `LiveTyping` only
    knows what this phone *queued*. `KeyboardPanel` now watches the session and, when the phone can
    no longer vouch that what it queued reached the PC, resets the model and empties the live
    textarea; if anything had been typed in that live run it pauses (Compose and Send + an
    explanation from `CERTAINTY_LOST_EXPLANATION`). Triggers: `enqueue()` returned false; a new
    `session.problem` object while the session continues (every such code — INPUT_STALE,
    RATE_LIMITED, INPUT_TARGET_CHANGED, INPUT_INJECTION_FAILED, INPUT_SEQUENCE_INVALID,
    INPUT_RESTRICTED, INPUT_NOT_PERMITTED — means a batch was dropped); `lastAck.droppedEvents`
    increased; `sessionId` changed or the phase entered/left `live` (ended, suspended with the agent
    discarding pending events, a fresh Start). Detection is by object identity of `problem` (the
    client creates a new object for every rejection and the store keeps identity), so two identical
    consecutive codes both trigger. A reset with nothing typed is silent. Conservative by design: a
    rejection of a pointer-only batch also pauses typing, because the phone cannot tell which batch
    the error refers to (CONTRACT_ISSUES 8). We deliberately do not try to "re-send" the lost text.

37. **The live and compose textareas are keyed separately.** Found while testing decision 36: both
    branches render a `<div><label/><textarea/></div>` at the same tree position, so React reused one
    DOM node. The live field is uncontrolled, so after Compose → Type live it still held the composer
    draft, and the next keystroke diffed the whole draft as new text and sent it. `key="live-mode"` /
    `key="compose-mode"` force a fresh node; component-tested.

38. **Lost pointer capture cancels only for a finger the machine still tracks (review fix, major).**
    Browsers release capture implicitly right after each `pointerup` and fire `lostpointercapture`
    for that (already lifted) pointer, i.e. between the two lifts of a two-finger tap. The surface now
    asks `GestureMachine.hasFinger(id)` and, only for a still-down finger, calls `cancel()` — never
    `pointerUp()`, because a capture lost mid-touch must not become a tap (the old code turned it into
    a left click for a single finger). Component-tested by dispatching the implicit event in jsdom.

39. **Health compares the PC's input session with this phone's controller id (review fix, major).**
    `state.input_session.controller_id` is a controller UUID; it used to be compared with the
    agent-issued `input_session_id`, so Health claimed "another phone" for this phone's own lingering
    session. `HealthInput` gains `controllerId` (from the live store). Foreign id → "Another phone
    currently holds…"; own id while not live → "The PC still lists this phone's last input session;
    its lease ends it within a few seconds."; no bound controller id → nothing claimed.

40. **Touchpad drag/held state resets on every move into a non-live phase and on any PC change
    (review fix, minor).** Previously only ended/failed/suspended reset it; a runtime stop for a PC
    switch or a hidden page goes to `idle` and left `DRAG MODE` showing and the machine's drag lock
    set, so the next touch moved without a held button and End Drag sent a stray `up`. The page now
    compares phase, the session's PC and the selected PC with the previous render, resets the
    indicators during render and the machine in an effect (its release output is discarded — the PC
    already released that session's holds).

41. **Support failure copy distinguishes "Not sent" from "Not confirmed" (review fix, minor).**
    `submissionFailure()` in `lib/support.ts`: never left the phone (no CSRF token, request failed the
    contract) or a definite 4xx → "Not sent … Support has not received this request." A network
    failure (the POST may have left first), a 5xx (possibly after commit), a 2xx that fails
    `support_ticket_response` validation, or an unknown exception → "Not confirmed: DoMe could not
    confirm whether support received this request", with a "Refresh your requests" button that
    re-reads `GET /v1/support/tickets` before the customer decides to send again. Nothing is resent
    automatically. A client request id would make retries safe (CONTRACT_ISSUES 11).
