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

42. **Commands are stamped with their action's `since`; confirmations with the command's stamp
    (protocol 1.2, rules.command_version_stamping).** The PWA is served by cloud-api, so phones update
    before PCs. Stamping every payload with the newest version would make a 1.1 agent refuse every
    command. The no-eval registry facade parses `since` exactly like `@dome/protocol` (absent = 1.0,
    checked against the protocol version at load; parity-tested per action), the shared
    `buildCommandPayload` stamps it, `CommandRecord.protocolVersion` keeps it, and
    `buildConfirmationEnvelope` requires it (`commandProtocolVersion`). Input batches are stamped 1.1
    by the shared builder. The relay `hello` still offers only this build's version: relay and PWA
    deploy together.

43. **Window controls never start an app and never substitute one state for another.** Maximise,
    Restore and Full screen are disabled for an app that is not running (the PC would answer
    APP_NOT_RUNNING; the step says "Open it first from Apps"). Full screen is an absolute
    `enabled` value: pc_state and app.list do not report it, so the toggle sends the opposite of the
    last `app_window_state_result` the PC read back for that app or window (success, or the
    best-known state carried by a failure), defaulting to "not full screen"; a stale guess costs one
    tap that the PC answers without pressing anything. After FULLSCREEN_UNSUPPORTED the outcome card
    offers "Maximise instead" — a second, visible command (spec: no silent fallback); on the Type page
    it is a chip that fills "maximise <app>". For that code the headline is the agent's own message
    (`errorMessage` with `origin: "agent"`; the relay's never), because only the PC knows why it pressed
    nothing in that app and how to allow full screen for it there; it is shown as plain text with
    control/format characters as spaces, and the shared copy stays the fallback. "Try Maximise
    instead." stays the first recovery step.

44. **YouTube search never navigates the watched video away implicitly.** `resolveYoutubeBrowserTarget`
    reuses a tab only when the customer chose it (the tab picker, the card's "Use the YouTube tab"
    switch) or it is the last results tab still showing results; otherwise it opens a new tab in the
    one connected browser, or asks which browser. A successful search does **not** select its results
    tab (review fix: a list has no player, so Pause/volume/seek then failed with "ad, live stream or
    Shorts" copy while the video kept playing); `resolveYoutubeTarget` never picks a context-"other"
    tab implicitly. A successful `youtube.open_result` selects the tab the video opened in (also
    covering a token change after a full page load). Results are recorded in memory
    (`lastSearchByPc`: commandId, createdAt, the PC's `stateSeq` at the answer, tab, results — never the
    query); a slower older search never replaces a newer one. The new tab is missing from `pc_state`
    until the PC's next (debounced) report, so "closed" is only concluded from a later report. Opening
    a result while another tab plays pauses that tab first (a visible step on the Type page, stated in
    the Remote's hint), so two videos never play at once. The query lives only in the in-memory command record (for the
    outcome line, quoted ≤ 60 characters); `log.ts` redacts `query` and any `*search*` key, and
    diagnostics never include params.

45. **The approved-app list refreshes itself on entry and on return to the foreground.** pc_state
    carries no app list (adding one is versioned protocol work), so the phone asks: `useApprovedApps`
    sends `app.list` when the Apps or Type page mounts and after every `visibilitychange`/window
    `focus`, waiting for fresh PC state (never into a reconnecting socket). `refreshApprovedApps`
    skips while an `app.list` for that PC is being signed or awaiting its answer, so the two events a
    resume fires send one request. `app.list` results are applied in the live store whichever page
    asked (an older answer never replaces a newer one), replacing the Apps page's own effect.

46. **Typed commands keep the customer's words where they are data, and say what they will do.**
    (Review fixes.) `normalizeParts` tracks where every character came from, so a search query is
    sliced from what was typed (control characters as spaces, quotes straightened, nothing else
    rewritten) and "now"/"for me"/"thanks" stay at the end of a search unless a comma sets them apart.
    INJECTION applies per part and, in a search, only to the words around the query: the query only
    becomes the `search_query` of a results URL on the PC and no model reads it; a refused part says
    why. Splitting: a bare "and"/comma right after an open-ended query ("search YouTube for …") stays
    in the query unless a result pick follows; a segmentation whose query swallowed "then" ranks below
    one that splits there, even when that one asks a question. Reserved words (spotify, music, …) lose
    to an approved app of exactly that name or alias (`strongMatch`). App names allow any letters
    (NFKC); the Type page's examples use `appPhrase`. The pause step DoMe adds before a result
    (`added: true`, target "playing_elsewhere", resolved at send time and skipped when nothing else
    plays any more) does not count toward the three steps. `runPlan` retries a window step after its
    app's launch on TARGET_GONE (the PC refuses that before any side effect) and races every wait
    against Stop and the relay status.

47. **The Assistant is a smarter parser plus a spoken reply, on the Type page's own code.** (docs/design/
    assistant.md §4.) Every turn goes through `parseCommand` first; plans, handoffs and every
    clarification stay on the phone — clarifications about apps need the PC's app list, which the AI
    never sees, and the safety ones are product rules — and only `unknown` words reach the AI, when it
    is usable. Its steps return in the grammar's representation and `planFromAssistant` builds the same
    `PlanStep`s with the grammar's own `step()`, `resolveAppSlot()`, `appSteps()` and `openResult()`,
    then the same `assemblePlan` (extracted from `parseCommand`, no behaviour change). `commandText`
    gives each AI step words that parse back to it (three actions the grammar cannot express —
    `media.get_sessions`, `youtube.get_state`, `youtube.list_tabs` — are never offered as chips). The
    run glue was extracted from `CommandPage.tsx` into `planRun.ts` (`runPlanOnPc`, `STATUS_LABEL`,
    the app-list refresh helpers) and both pages use it. Clarifications gained a `topic`
    (`app|player|volume_scope|volume_unknown|safety|other`, absent = other) so `recent` and the "Ask the
    assistant" chip never depend on a message's text; three existing toEqual expectations name it.

48. **Only low-risk plans run without a tap.** The rule reads `registry.get(action).risk`, never a
    hand-kept list: `windows.lock`, `app.launch`, `app.focus` and `app.set_fullscreen` are `moderate`
    and wait for Run (a misheard or injected lock would shut the owner out of remote control until
    someone unlocks the PC at the desk). A lone step with `confirmation: challenge` is sent at once so
    the PC answers with its signed challenge and the global modal shows it; only a tap there approves.
    A plan also waits when the page was hidden during the turn, when it was ready more than 15 s after
    the words, when it came from a multi-step deterministic chip, when the transcript ended on a joiner
    and when **Ask before every action** is on. A spoken "yes" with a card waiting answers "Please tap
    Run on the screen." and runs nothing (the microphone can hear the PC's own speakers); while a plan
    is running new words are ignored. "Cancel"/"never mind" reach `power.cancel` only while a countdown
    is pending — and then always, whatever card waits: the countdown is checked before any card. A new
    request supersedes every card still waiting for Run (shown "Not run."), so a yes, a no or a Run tap
    only ever concerns the card of the turn just before; a stale card can never be run later by
    accident.

49. **`recent` is built from closed templates, never from what was shown or spoken.** Owner lines of
    turns DoMe handled itself are notes of what it did ("(DoMe handled this itself: open the app)");
    butler lines are result templates with every PC-sourced part replaced ("The app is open.", "I
    listed 3 apps on the screen.", the failed code's `errors.json` message, never the agent's text),
    clarification topics, or the model's own cleaned reply. "It" for apps is resolved on the phone
    (`lastAppId`), so `recent` can do without app names. The multi-turn sentinel test proves no display
    name, label, title, query or agent message reaches a turn request. `recent`, the conversation and
    `lastAppId` live in memory only and are cleared on sign-out, PC switch, 10 minutes without a turn
    and reload.

50. **Tap to talk, not hold to talk; Dictate always works.** Recognition starts in the talk button's
    `click` (iOS counts a touch as activation at `touchend`), stops on the second tap, 15 s after
    `audiostart` or when the page hides. Where web speech recognition is missing or refused
    (`service-not-allowed`, typical in a Home Screen app; remembered per display mode), the button
    becomes Dictate and focuses a text field inside the same tap so the keyboard and its microphone key
    open; dictated text is sent as `speech` so the model expects misrecognition. Speech is unlocked by
    a silent utterance on taps that are not the talk button (nor the voice notice's Continue, which
    starts the microphone), tried again on each such `pointerup`/`touchend`/`click` until an utterance
    has really started (`onstart`), since WebKit may ignore a try; a line that does not start within
    1.5 s offers "Tap to hear", and that tap (like Test voice) speaks at once instead of waiting for the
    voice list. Nothing is spoken while listening, muted or hidden.

51. **The phone validates both directions of every assistant call.** Requests are checked against
    `assistant_turn_request` before they leave (so `context` can only hold the closed fields) and
    responses against `assistant_turn_response`; a reply carrying a format character the contract
    forbids is refused as a whole and the phone answers by itself. Model text is cleaned again
    (`\p{Cf}` removed, separators as spaces) and rendered as React text only. The phone never retries a
    turn, except after its own Cancel when the server answers AI_BUSY: the same words are sent again
    with a new `turn_id` every 2 s until the cancelled turn's deadline (AI_BUSY is answered before any
    reservation). The client aborts `turn_deadline_seconds + 5` s after sending.

52. **A turn belongs to the PC and the conversation it started in.** (Review round on the Assistant.)
    The turn records the selected PC and a conversation counter (raised by every clear: PC switch,
    sign-out, idle). After each wait — the AI's answer, the app list, the PC's state — a turn whose PC
    or conversation changed is dropped: no plan is built or run, nothing goes into `recent`, and with a
    PC selected one line says why ("You switched PCs, so I've left that request."). Switching PC also
    cancels the AI request in flight (`voice.ts` subscribes to the selection; the runtime keeps clearing
    the conversation), and `runItem` refuses a card whose PC is not the selected one. The turn is busy
    from its first word to its last line: the app-list wait shows "Checking the PC's apps…" (phase
    `checking`) and neither a second request nor a Run starts meanwhile. The touch at the turn's start
    keeps the idle clear from ever emptying the conversation of a turn still running.

53. **Clarifications are answered by a tap, never by a spoken yes.** A clarification with choices from
    the turn before is a pending question: "yes" answers "Please tap your choice on the screen." and
    "no" answers "Very well.", both on the phone, so a "yes" never becomes a charged AI request about
    nothing (the model never sees the app names behind "Did you mean Notepad?"). Their spoken versions
    are statements ("I think you meant Notepad. Tap it on the screen to go ahead."). The Type page's
    messages keep their wording on screen; `butlerSpeech.ts` speaks fixed lines for those that ask a
    question or quote the owner's words (`ONLY_SEARCH_PLAYS`, `DID_YOU_MEAN`, `SAME_THING`,
    `CONFIRM_ALONE`, `TOO_MANY_STEPS`, `UNDERSTOOD_PART`, `HELP_MESSAGE` exported from `intents.ts`), and
    the Assistant shows its own help line ("things you can say"). An AI error is the whole answer:
    one line per code naming the real cause (the network, a rate limit, a limit with its renewal date,
    the request still finishing), followed only by a part the parser did understand; a named limit is
    not followed by the contract's message naming it again. "Open Spotify" with no Spotify approved is
    "not an approved app" on the phone (a reserved word that is also an app's name), never an AI turn.

54. **The card's Cancel shut-down goes through the Assistant.** It sends `power.cancel` with
    `runPlanOnPc` and its own stopper (so Stop on another plan never stops it and it never waits for
    one): signed, tracked on the card (`cancel.commandId`), shown ("Cancelling…", then the compact
    outcome, or the send error) and said ("Cancelled.", "Nothing was counting down.", "I'm afraid I
    couldn't cancel it: …"). The button goes once the cancel succeeded, once a PC state that named this
    countdown no longer does, or once the countdown's time passed; after a failure it stays to try
    again.

55. **Design system "Midnight Studio" (website and app, light and dark).** One set of colour roles in
    `src/styles.css` (`--dome-*`, mapped to Tailwind names by `@theme inline`): light is the base (airy
    #f6f8fc page, white cards with a soft Navy-tinted shadow, Navy #001441 type); dark is the brand's Navy
    with translucent "glass" cards and a soft DoMe Blue glow at the top of every screen (`.dome-backdrop`).
    `accent` is the text/selection colour (DoMe Blue on light, #86abff on Navy) and `primary` the action
    fill (DoMe Blue with a white label in both themes, 6.6:1; `.dome-primary` adds a top light and a blue
    glow): no single colour can be both a fill under white text and a link on Navy at AA. Coral is only the
    cursor's tiny highlights (the active tab's dot, the recording ring, a status dot). Contrast was checked
    on the composited values: body ≥ 13:1, secondary ≥ 7:1, tertiary ≥ 4.6:1 in both themes.
    Type: the system stack (SF Pro on Apple devices) with display tracking on headings; no font is bundled
    (it would add weight for no gain on the iPhone, and BRAND.md §3 needs none). Motion is a short ease-out
    rise/press, all of it switched off by `prefers-reduced-motion`.

56. **Theme choice without a flash and without inline script.** Nothing stored = follow the device. The
    Light / Auto / Dark switch stores `dome.theme` and sets `<html data-theme>`; CSS repeats the dark
    palette for `@media (prefers-color-scheme: dark) :root:not([data-theme=light])` and
    `:root[data-theme=dark]` (a test keeps the two blocks identical). The CSP (`script-src 'self'`) forbids
    an inline head script, so `public/theme-init.js` is a tiny external classic script loaded from `<head>`
    before the stylesheet and the app: it is render-blocking, so the first paint already has the chosen
    palette (checked in a browser on the production build: device light + saved dark paints Navy). It
    also sets `<meta name="theme-color">`. `.dome-dark` gives one region the dark palette in either theme
    (the Navy panels and the phone picture on the website).

57. **Five tabs.** Home, Remote, Touchpad, Type (or Ask when the Assistant tab is on), More. Apps moved
    from the tab bar to More and to a row on Home; nothing else changed place. Home is the PC (large status
    card), what is playing (with play/pause), the Windows volume and four quick actions; every control
    keeps its accessible name, its disabled rules and its honest-result copy.

58. **The logo is drawn inline from the brand's own paths.** `src/components/brandPaths.ts` holds the
    path data of brand/wordmark.svg, mark.svg and icon.svg (the deploy image builds without brand/);
    `test/brand.test.ts` fails when they drift. Letters and ring take the theme's logo ink (Navy / White,
    exactly the two lockup files), the tip stays Sky and the cursor Coral.

59. **Design preview (development only).** `/preview…` on the dev server runs the real app against a fake
    account and PC (`src/dev/preview.ts`): REST through `configureApi({ fetchImpl })`, the relay through
    `Runtime({ relay: { socketFactory } })`, a quiet speaker and a pretend microphone, and a conversation
    typed through the real Assistant controller, so screenshots and reviews are honest renders of the real
    components. `main.tsx` imports it only under `import.meta.env.DEV` (dropped by `vite build`); its
    marker (`dome-design-preview`) makes `scripts/check-bundle.ts` fail the build if it ever ships, and a
    test forbids any static import of `src/dev/`.

60. **Design A "Midnight Studio" over Design B "Clean Precision" (the owner's choice, 2026-10-12).** Both
    directions were built in full (website and app, light and dark) in separate worktrees and reviewed
    by a design critic from phone and desktop screenshots in both themes. Neither won on the basics: both
    passed WCAG AA in both themes, kept 44 px targets, handled reduced motion and had no sideways scroll at
    375–430 px. The difference was the brand, which is what the owner asked for ("really nice and
    expensive looking", light and dark). A's dark theme is the logo's own Navy and its light theme keeps
    Navy ink; the orbit line, Coral kept to the cursor's tiny dots and the full DoMe REMOTE lockup carry
    the logo through the site. B was calmer and slightly quicker to scan, but its dark theme was a
    generic near-black and the brand nearly disappeared inside the app; making it look like DoMe meant
    re-basing its whole dark palette, a larger change than A's list of local defects (a clipped hero
    shadow, a toast over the product picture, a 250 px desktop gap, see-through tab bar and Ask dock, a
    lopsided dock, too much saturated blue, an unlabelled device switcher). The rollout fixed all fourteen
    of the critic's A items and took five of B's ideas: the pricing layout ("Available now" chip on Free,
    left-aligned comparison table, "Good to know" as quiet prose), the labelled "Devices" pill on Home,
    the balanced Ask dock with a round "Type" button, tinted icon wells with solid blue saved for the one
    primary control per screen, and `role="img"` with a one-sentence label on the hero picture. B was
    never merged; its branch (`worktree-wf_8fd43751-de4-3`) is a reference only.

61. **The demo film is a vector web animation; video files are exported from it, never shipped.** The
    owner wanted "a video example" of Screen view and the AI butler. A video file on the site would weigh
    megabytes per view, go stale whenever a screen, a price or a status label changes, need burned-in or
    separate captions and offer no reduced-motion alternative; the CSP (`'self'` only) also rules out a
    hosted player. So the film is React SVG + CSS drawn with the design tokens and
    the same plan content as the pricing page: a price or a "Coming" label changes in one place and the
    film follows. Captions are real text in a live region, the controls are keyboard operable and reduced
    motion gets a storyboard of stills. Every frame is a pure function of one clock, which makes the
    export deterministic: `scripts/export-film.mjs` seeks the clock frame by frame in Playwright's
    headless shell and pipes the screenshots into ffmpeg (1920×1080 and 1080×1920, MP4 and WebM, 30 fps,
    plus a poster) for sharing and social posts. Playwright's real-time `recordVideo` screencast is
    available as `--record` but is a fallback only: its frame timing and sharpness are the screencast's. Exports go to a temp
    folder or `--out` and are not committed (about 9 MB per MP4, regenerable from any commit). Costs: the
    film's code is in the main bundle (not split yet), and exporting needs a Chromium headless shell and
    ffmpeg on the machine that runs it.

62. **Plan content is static copy until the metering task lands.** The owner decided Free / Pro / Pro Plus
    (prices, devices, Screen view minutes, 5-hour and weekly AI windows, a one-time Free taste, top-ups)
    before any of the machinery existed, and the website had to explain the plans now. `src/content/plans.ts`
    states them as content, with "Coming at paid launch" on both paid plans, and is the one source for
    every surface that names a price or an allowance: the pricing page and comparison table, the FAQ, the
    home teaser, the film and `/demo`, and the in-app Billing page and `ProExplanation` (which until then
    showed the contract's $5.99 experiment price). Device counts and routine limits are read from
    `plans.json`, which the server enforces, so those cannot drift; the contract has no Pro Plus plan yet,
    so Pro Plus shows Pro's counts. The module meters, sells and unlocks nothing. Until the metering task
    (#51) replaces it with contract values: `plans.json` keeps `pricing_defaults` 599 / 4999 cents and a
    monthly AI allowance; `docs/BILLING.md`, `docs/COST_MODEL.md` and `docs/PRODUCT_AND_PLANS.md` keep the
    old experiment price; Settings words Free's allowance as "N of 10 AI requests to try used" (no renewal
    promised) and paid plans keep the monthly wording (`allowanceLine`).
    *Resolved 2026-10-20 (#51, protocol 1.3):* `plans.json` now carries Pro Plus, `pricing_defaults` for both
    paid plans and the top-up, and the `ai_taste_requests` / `ai_window` / `ai_week` / `ai_topup_allowed`
    fields; `plans.ts` reads its prices, device counts and AI figures from there (its literals stay as
    fallbacks and a drift test pins the equality), `allowanceLine` is gone and every allowance surface uses
    `lib/billing.ts` (decision 65). `COMING_AT_PAID_LAUNCH` remains the static default: the pricing page and
    the FAQ flip to "Available now" only when `GET /v1/plans` reports `billing_enabled`.

63. **Honesty labels are shared constants, shown wherever a feature appears.** The product's promise is
    honest results, so no surface presents a planned feature as available. Today: Free "Available now"
    (a chip); Pro and Pro Plus "Coming at paid launch" as a status line with a clock icon, never a
    button (a disabled outline button looked tappable); Screen view "Coming" on every card, comparison
    row and film chip; point-and-talk / click by name "Planned"; the AI butler "Available now", since it
    exists today behind consent and is off until turned on. The strings are exported from
    `content/plans.ts` (`AVAILABLE_NOW`, `COMING_AT_PAID_LAUNCH`, `SCREEN_VIEW_STATUS`,
    `POINT_AND_TALK_STATUS`) and the film's chips are built from them. The fixed claims travel with them:
    the AI never sees the screen (no screenshots, no pictures), nothing is recorded, search words and
    speech are not stored, and "DoMe is in development. The Windows app is not published yet." The film
    adds one line under it: "Concept preview. Screen view and point-and-talk are in development." Tests
    (plans, PricingPage, BillingPage, UpgradeAndHealth, film timeline, Film, DemoPage, LandingPage) assert
    the labels and the absence of recording, screenshot or AI-vision claims. When Screen view or
    point-and-talk ships, change the constant, the copy that says "in development" and those tests
    together.

64. **The Billing page shows only what the server says, and a return from Stripe unlocks nothing.** Every
    state of the page (off, test mode, buyable, pending, paid, past due, canceling, expired, downgrade
    selection) is derived from `GET /v1/account/billing` and `GET /v1/session`; the plan is never read from
    the Checkout return URL. `?checkout=return` and `?topup=return` only tell the page to say "pending" and
    to poll the server every 3 s for at most two minutes, stopping as soon as the server reports the change
    or the bound is reached ("Still confirming…"); the query is dropped from the URL at once. Purchase
    buttons exist only when the server reports `billing_enabled` and `checkout_available` / `portal_available`
    / `topup.allowed`, so a server without Stripe keys keeps today's "Upgrading is not available yet" page
    with no button at all. The browser only ever navigates to an `https://` URL the server returned (the
    contract pins the pattern; the page checks again), through `browserNavigation.assign` so tests can
    observe it. Errors render the contract's `user_message` for the code and never a success. Stripe's test
    mode is labelled on every surface that could start a payment. Plan changes, the monthly/yearly switch,
    card updates and cancellation happen in Stripe's Customer Portal, not in the app: one path, fewer ways
    to be wrong about money.

65. **One refresh wording, pinned by the shared fixture.** `lib/billing.ts` `refreshText` writes when an AI
    allowance comes back in the viewer's time zone: the same calendar day → "Refreshes today at 2 pm",
    the next day → "tomorrow at 9 am", within six days → the weekday, otherwise "27 October at 2 pm";
    12-hour clock, minutes only when non-zero, lowercase am/pm; Free's taste → "Does not renew". The
    cases live in `shared/protocol/fixtures/ai-window-cases.json` (formatted in America/New_York in the
    test) so cloud-api and the phone agree on the arithmetic and the words. The same module gives the
    Settings line (`allowanceSummary`: "2 of 30 used in this 5-hour window · 7 of 120 this week ·
    refreshes Saturday at 2 pm", "starts with your next request" while no window is open, the top-up
    balance at the end), the warning level (`bucketLevel`, 80 % from `warn_at_percent`), the Ask page's
    banner (`warningText`) and chip (`requestsLeft`: the smaller room of the two windows plus a spendable
    top-up), and `limitExplanation` for the five reasons the server can name (taste, window, week,
    cost_cap, failures), preferring the error's own `resets_at`. The butler's voice reuses the same phrase
    ("They refresh today at 2 pm"), so nothing on the phone says "month" or invents a date.


66. **Screen view is one client owned by the runtime; the page only renders it.** (Protocol 1.4, WP1.7;
    docs/design/screen-view.md §16.) `lib/screen.ts` `ScreenSessionClient` takes an injected
    `RTCPeerConnection` factory the way `RelayClient` takes `socketFactory`, receives `screen_*` frames
    and the start command's records from the runtime, and is stopped by the runtime on hidden, PC switch
    and sign-out. `ScreenPage` turns its state into exactly one §16 row through the pure
    `lib/screenStatus.ts` (all copy lives there), so every row is a unit test and a component test.
    Tests use `test/helpers/fakePeer.ts` (`FakePeerConnection`, `FakeDataChannel`, `FakePcKey` signing
    answers like the agent) and `test/helpers/fakeTimers.ts`, the repo's pattern for test-only doubles.

67. **The pairing code is held, not derived, until the PC key is pinned.** §4.1 allows the normalised
    code or a non-extractable HMAC key; the shared library's `verifyPcKeyMac` takes the code, and one
    MAC implementation in one place beats a second copy of the message format. The code lives in a
    `HeldPairingCode` (`lib/pcKeyPins.ts`) carried by the `waiting` step and cleared on approved (after
    `verifyAndPin`), declined, expired, 404, Cancel and unmount; `heldPairingCodes()` counts holders so
    the page test proves "absent after". A MAC that does not verify pins nothing and removes an earlier
    pin for that PC (the relay just served a key the PC did not vouch for); the pairing itself still
    completes, because pairing grants and Screen view trust are separate decisions. Pins live in the
    controller key's IndexedDB (`pins` store, DB version 2; the upgrade only adds a store).

68. **The operator switch is read from `GET /healthz`, and "unknown" is never "on".** The brief named
    `GET /v1/plans`, but `plans_response` has no such field (closed schema, CONTRACT_ISSUES 12) while
    cloud-api already reports `screen_view: "on" | "off"` in `/healthz`. `screenViewSwitch` reads that one
    string tolerantly (no schema exists for the health body), once per app load, credentials omitted.
    Anything else keeps the "Coming" card (DECISIONS 63): the tile, the Home card and the page's controls
    exist only while the server says "on".

69. **Taps are dropped, never clamped, and carry the generation the phone saw.** `lib/screenInput.ts`
    glues the gesture outputs to signed events: a tap is `[pointer_move_to, click]` and is flushed at once
    (no tap delay); scroll and touchpad motion ride the per-frame flush so a slide never becomes 60
    batches a second. Letterbox bars, DoMe's reserved bars, a geometry held back after a `<video>` resize
    and a missing geometry are drops with a reason (a short hint for the last three). The loupe's aim moves
    at 1/2.5 of the finger so the 2.5× view buys real precision; the loupe is a second `<video>` on the
    same stream with a CSS transform — no canvas, no second decoder in our code. Pinch vs scroll is decided
    on pairs of moves (`DirectGestureMachine.twoPending`): browsers report two fingers one at a time, and
    judging after one finger's half of a fast two-finger slide would read it as a spread change, i.e. a
    pinch; a finger that moves twice while the other stays still (an anchored thumb) is judged then.

70. **The input session follows the picture.** It starts once the picture is live and only when the
    grant holds `pointer` or `keyboard`, and stops when the screen session ends or the page is left, so an
    input session never outlives the picture it was for. A view-only grant never starts one.

71. **Nothing starts on arrival; Phase 1 stops on leaving.** "Show screen" is the user gesture iOS wants
    for `play()`. Leaving the page stops the session (the mini view is Phase 2). The background path is
    the runtime's existing "hidden" pattern; on return the page starts one fresh session only when the
    client says `resume: "auto"` (Resume button after an ask-mode start; nothing after a PC stop, a
    refusal, a used-up allowance or a removed grant). Frame timing comes from `requestVideoFrameCallback`
    with `timeupdate` as the fallback; the stall thresholds are inclusive (2 s → keyframe, 5 s → fresh
    session, at most twice).

72. **The no-pixel-access allow-list has one entry, not zero.** §17.2 asks for an empty allow-list, but
    the pairing page's QR scanner (`lib/qr.ts`) must read camera pixels for jsQR. It stays the single,
    reasoned entry; the test also requires that every allow-list entry still needs it, and the Screen view
    files get a stricter check (no canvas at all, the stream only ever assigned to `srcObject`, no fetch
    or socket).

73. **Manifest `orientation: "any"`.** Needed for a landscape picture (§16); every other page is a
    phone-width layout that reflows, so nothing else changes. iOS reportedly ignores the field.

74. **The way in names one state and never claims "Ready" early.** `ScreenViewTile` (Remote tile and
    Home card) shows "Checking…" until the grant and the pin are read, then `entryLine`; the last
    allowance and the last start problem per PC come from the screen store so the tile can say "7 min
    left" or "Update DoMe on your PC" before a session starts.

75. **Review round (2026-10-22): the Screen view copy says only what is true in every configuration.**
    A pause names its cause: a missing bar is "A screen on the PC changed. The picture comes back when
    DoMe's bar is on every screen." with no claim about the clock (the relay discounts only Windows
    prompts); a Windows prompt on Free says "Up to two minutes of Windows permission prompts in each
    5-hour window don't count toward your Free minutes" (what the relay does), never "the clock stops".
    "Stopped on the PC" promises no cooldown length (it is 10 min, 1 min or off per grant). The switch
    off is "Screen view isn't switched on yet." (it is operator-wide, not per account) and "unknown" is
    a checking state. PC sound is labelled "(coming in a later update)" on the phone and in the PC's
    grant window. On Free the consent wait says it counts toward the minutes if the PC allows it.
76. **The phone keeps the PC's pause causes as a set** (`ScreenSessionClient.pauseCauses`): the PC sends
    one `paused` / `resumed` frame per cause (pc-agent CONTRACT_ISSUES #25), so a `resumed` for one cause
    while another remains keeps the picture paused and shows the remaining cause; the data channel's
    `sending` (sent only when no cause is left) clears it.
77. **`ACTION_UNAVAILABLE{reason: screen_view_not_included}` has its own row** ("not_included", no retry):
    the PC's build lacks the media stack; the generic "That control is not available" stays for the rest.

78. **A payment provider error says whose problem it is.** (Staging, 2026-10-23: "Continue to payment" said
    "Our payment provider didn't answer" while Stripe was refusing DoMe's own setup.) `BILLING_PROVIDER_ERROR`
    carries `detail.reason` (cloud-api billing/provider.py; CONTRACT_ISSUES 19). Only `timeout` and
    `provider_error` mean Stripe did not answer; `auth`, `bad_request` and `not_found` mean Stripe refused
    DoMe's key or the request (a price or customer it does not know is `bad_request`, Stripe's 400), or does
    not know a payment page or subscription DoMe saved earlier (`not_found`, Stripe's 404), which trying
    again in a moment cannot fix. For those three the phone says "Payments aren't working on DoMe's side
    right now. Nothing was charged. Try again later."
    (`errorMessage` and `recoverySteps` with `ctx.reason`, `billingErrorText`): the one exception to decision
    64's "the contract's `user_message` for the code". `timeout`, `provider_error`, a missing reason and any
    reason the phone does not know keep the contract's text. While `GET /v1/account/billing` says
    `billing_mode: "test"`, the error notice adds one line for whoever runs the service with what Stripe did
    and the reason code (`providerTestModeLine`: "Test mode: Stripe refused the request, for example a price
    or customer it does not know (bad_request). Run Deploy again: its summary checks the Stripe setup."; the
    Deploy pointer only for `auth` and `bad_request`, as the Deploy check cannot see ids DoMe saved earlier).
    In live mode and with billing off it never appears, so a customer never sees a reason code; only the five
    known reasons are ever shown, never text from the server.

79. **The touchpad stream is paced to the contract, and slows itself when the PC falls behind.** (Device
    round, 2026-10-26: 31–52 % of touchpad batches dropped INPUT_STALE against staging, one session ended by
    its own lateness; the latency root-cause report.) The client flushed one signed batch per animation
    frame — 60/s on an iPhone, 120/s on ProMotion — against the relay's `limits.input_batches_per_second`
    = 40 sustained (`plans.json input_rate_limit`, burst 80): after four seconds of motion one frame in three
    came back RATE_LIMITED, and every frame fed a relay whose service time was already longer than a frame.
    Now (`lib/input.ts`):
    - Flushes still ride `requestAnimationFrame`, so the events of one frame always travel together, but a
      frame that comes sooner than `FLUSH_INTERVAL_MS` = 1000 / `input_batches_per_second` after the last
      flush re-arms instead of flushing when the queue holds only motion (`pointer_move`, `pointer_move_to`,
      `pointer_scroll`, `pointer_wheel`), which keeps coalescing by the rules that already applied within a
      frame: adjacent moves sum (clamped), consecutive `move_to` keep the last, nothing else is merged, order
      and count are kept, 64 per batch. Wheel and scroll events are not summed: neither the contract nor the
      PC's `coalesce_events` does, and Screen view interleaves every wheel with a `move_to` anyway. The
      effective rate is the largest frame multiple under the budget: 40/s on a 120 Hz display, 30/s on 60 Hz
      (the report's queue model puts the relay's safe service time at ~33 ms per batch for 30/s, twice that
      for 60/s). A 2 ms slack absorbs frame-timestamp jitter so that 3 × 8.33 ms counts as 25; the slack alone
      would let a cadence just over the interval (23.5 ms frames) send 42/s and drain the relay's burst in half a
      minute, so the gate has a second guard (review): motion also waits while `input_batches_per_second` flushes
      went out within the last second, whatever the cadence.
    - A button, key, text or shortcut in the queue flushes on the very next frame, taking any held motion
      along in order, so a tap never waits for the gate; `flush()` (Screen view taps, Compose and Send, Stop
      Input) still sends at once and restarts the interval.
    - Back-pressure from the acks the PC already sends (≤ 4/s, `last_seq`): the client remembers when each
      batch left and judges the oldest unacknowledged one against the session's fastest batch-to-ack round
      trip — the PC's own stale rule is "`input_age_budget_ms` slower than the fastest recent batch". More
      than half the budget late → `congested`: the interval doubles (20/s or 15/s) until the excess is back
      under a quarter of the budget. A constant network delay sits in the floor and never counts; the ack
      cadence (a quarter second) stays under the threshold. The relay's own drops leave no trace in the acks
      (cloud-api DECISIONS #112: it drops a batch that waited its age budget behind a newer one and forwards
      only the newest, whose ack settles the dropped ones as if acknowledged), so an `INPUT_STALE` notice —
      from the relay (`ref_pc_id` only) or from the PC — holds `congested` for 1.5 s after the last one
      (review); notices come at most once a second while drops continue. The flag is in the session state and
      the diagnostics bundle (`input.congested`); the pill does not show it yet (KNOWN_ISSUES 39).
    - The signer no longer costs an IndexedDB read and a kid derivation per batch: `lib/controllerKey.ts`
      holds the identity (key pair, JWK, kid) in memory after its first use — dropped on sign-out, "Forget
      this installation", a corrupt record and another tab's schema upgrade, and a failed load is never kept
      — and `lib/signing.ts::signInputBatchWithKid` signs with the derived kid: the shared library's envelope
      and schema check, one `sign` per batch (the shared `signPayload` has no way to pass a kid,
      CONTRACT_ISSUES 21). The key stays non-extractable; `test/signing.test.ts` has the shared verifier
      accept the result. Commands and the hello proof keep the shared signer and share the cached pair.
