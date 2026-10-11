# mobile-app — DoMe iPhone-first PWA

The customer-facing web app: public website (landing, pricing, FAQ, download, support, release
notes/known issues, privacy/terms drafts) plus the installable remote (`/app/*`). React 19,
TypeScript 5.9, Vite 7, Tailwind CSS 4, React Router 7, zustand, idb, vite-plugin-pwa, Vitest +
Testing Library. Depends on the shared protocol library `@dome/protocol` (`link:../shared/ts`),
protocol **1.1** (manual touchpad/keyboard, spec §10A).

Design: `docs/design/mobile-app.md` and `docs/design/input-control.md` §5. Decisions made here:
`DECISIONS.md`. Deferred items: `KNOWN_ISSUES.md`. Contract observations: `CONTRACT_ISSUES.md`.

Evidence tags used below: **unit-tested** / **component-tested** (Vitest, jsdom, fake sockets and
timers — the shipped code path), **not yet verified** (needs a real iPhone and/or a real Windows PC).
Nothing in this component is iPhone-tested or Windows-device-tested.

## Develop

```sh
cd mobile-app
pnpm install                 # standalone package with its own lockfile
pnpm dev                     # http://localhost:5173 — proxies /v1 and /ws to http://127.0.0.1:8000
pnpm check                   # typecheck + lint + test + build
pnpm gen:validators          # regenerate src/protocol/generated/ after a protocol change
pnpm gen:icons               # install the brand icon renders (brand/icon.svg via brand/exports) into public/icons
```

**Design preview (development only).** With `pnpm dev` running, open `http://localhost:5173/preview`
(or `/preview/remote`, `/preview/assistant`, …): this tab then runs the real app — pages, stores,
runtime, relay client, signing and the butler — against a fake account and a fake PC from
`src/dev/preview.ts` ("Living Room PC" online, YouTube playing in Edge, volume 40, approved apps ZStream,
Edge and Spotify, a short butler conversation). No backend is needed; buttons get believable answers.
`/preview/off` leaves it. `main.tsx` loads that module only under `import.meta.env.DEV`, and
`pnpm build`'s bundle check fails if it ever reaches `dist/`.

Run cloud-api locally on port 8000 with `DOME_PUBLIC_ORIGIN=http://localhost:5173` (and
`tools/dev-idp` as the OIDC issuer) so sign-in redirects back to the dev server. In production the
built `dist/` is served by cloud-api from the same origin (`DOME_STATIC_DIR`); there is no CORS setup.

Environment (all optional, see `vite-env.d.ts`): `VITE_DOME_API_ORIGIN` (unusual: API on another
origin), `VITE_DOME_RELEASE_CHANNEL`, `VITE_DOME_SUPPORT_URL` (an additional direct contact shown on
Support pages; the in-app ticket form works without it).

## Layout

```
src/
  protocol/      no-eval facade over @dome/protocol's registry + generated validators (CSP script-src 'self')
  lib/           api (REST, schema-validated, support tickets), relay (WebSocket + sendInputBatch), commands
                 (lifecycle + onceSettled), input (manual-input session client), gestures (touchpad state
                 machine), typing (live typing / IME mapper), health (layered connection health), nowPlaying,
                 inputStatus, support, confirmations, controllerKey, intents, labels, pairing, qr, targets,
                 throttle, log (redacting), diagnostics, format, outcome, connection, power, pricing,
                 planRun (shared plan runner), assistant (turn pipeline), assistantContext, butlerSpeech,
                 speech (recognition + synthesis)
  store/         zustand: session, devices (REST inventory + selection), live (relay state, commands), input,
                 assistant (phone prefs persisted; conversation, recent, last app in memory)
  app/           runtime wiring, AppShell (tabs, banners, confirmation modal, speech unlock), PublicLayout,
                 router, hooks, voice (the one recognizer/speaker/Assistant controller)
  components/    ui primitives, icons (one 24-unit line set), brand (Wordmark, BrandMark, AppIcon drawn from
                 brandPaths.ts = the brand/*.svg paths), ThemeSwitch, Sheet, ConfirmationModal, CommandOutcome,
                 VolumeSlider, PcSwitcher, PcStatus, OfflineScreen, TouchpadSurface, KeyboardPanel, GestureGuide,
                 NowPlaying, FailureLinks, ProExplanation, TalkButton, Conversation, AssistantSettingsCard,
                 AssistantConsentSheet, VoiceNoticeSheet
  dev/           preview.ts — the development-only design preview (never in a production bundle)
  styles.css     the design system: colour roles for light and dark, surfaces, shadows, radii, type, motion
  pages/app/     Dashboard, Remote, Touchpad, Command, Assistant (Ask), Apps, Health, Routines (Pro preview),
                 Layouts (Pro preview), Devices, Pair, Settings, Billing, Link, More
  pages/public/  Landing, Pricing, FAQ, Download, Support (+ ticket form), ReleaseNotes, Privacy (DRAFT),
                 Terms (DRAFT), NotFound
  content/       release-notes.json (public release history + known issues)
test/            vitest (jsdom, fake-indexeddb, fake sockets/timers); test/components for UI,
                 test/helpers/harness.ts (fake socket + Runtime factory shared by component tests)
scripts/         gen-validators.ts, make-icons.mjs, check-bundle.ts (no key material, no design preview in dist/)
```

## Behaviour summary

- **Identity**: one non-extractable ECDSA P-256 key per installation in IndexedDB; `kid` + signed
  `hello_proof` in the socket `hello`; the relay's `hello_ack.controller_id` binds the socket. No
  tokens in URLs or localStorage. Sign-out keeps the key; "Forget this installation" deletes it.
- **Pairing code stays on the phone**: the `/pair#code=…` deep link keeps the code in the URL fragment
  only; the backend only ever sees SHA-256 of it. Pairing requests `status, media, volume, apps,
  lock, power, pointer, keyboard` by default (all unticked-able except status); the PC owner decides on
  the PC what is granted, and the page states that manual input reaches every app of the unlocked
  session (component-tested for the body; copy unit-tested).
- **Nothing is claimed that is not known**: a REST 401 mid-session signs the user out at once; a power
  request noted by the relay after the PC disconnected reads "requested … DoMe cannot tell whether it
  ran" unless this phone saw an `executing` ack / agent success; state older than 75 s disables
  controls even without any new frame.
- **Trust boundary**: every REST body, frame, result, challenge and signed payload is validated against
  the frozen schemas before use; media/window titles and the PC's foreground-window title are rendered
  as text only.
- **Truthful states**: Sent → PC received → Running → Done/Failed/Expired/Cancelled/Outcome unknown.
  Every failure carries two links: Connection health and Get help (support form with the error's
  category and code preselected).
- **Confirmations**: modal rendered from the registry (never from PC-supplied labels); Approve only when
  bound to the command this phone sent. **Every Sleep/Restart/Shutdown confirmation adds the fixed copy
  that the action can interrupt or end remote access and that remote wake is not supported in this
  version; the PC's `display.detail` is shown verbatim as text** (component-tested).

### Manual touchpad and keyboard (protocol 1.1, Free) — `/app/touchpad`

- **Session** (`src/lib/input.ts`, unit-tested over the real Runtime and a fake socket):
  `input.session_start` as a signed command on entering the screen (INPUT_SESSION_OWNED → Take over
  prompt → `params.takeover=true`); the result's session id, lease, `max_batch_events`,
  pointer/keyboard flags and foreground app drive the UI. Events are signed with
  `buildInputBatchPayload` + `signInputBatch` into `controller_input_batch` frames
  (`RelayClient.sendInputBatch`, validated like every frame): `seq` strictly increasing per session,
  ≤ 64 events, adjacent motion summed, never across a click/scroll/text/key/shortcut, one flush per
  animation frame, an empty keepalive about every second. `input.session_stop` on leaving the screen,
  Stop Input, page hidden, PC switch, sign-out; a dropped socket ends the session locally. Nothing
  queued is ever sent late or replayed. `input_ack` = Windows acceptance (live pill "Live · Windows
  accepted N", held buttons/keys shown), never an app effect; `input_session` suspended/ended carry the
  agent's reason with copy from `errors.json`/`labels.ts`. INPUT_* error frames are routed to the
  session, never mistaken for a refused subscription.
- **Gestures** (`src/lib/gestures.ts`, unit-tested): tap ≤ 250 ms / ≤ 10 px → left click; second tap
  within 300 ms / 24 px → second click marked double (Windows combines them; DECISIONS 22); one → two
  fingers never clicks; two-finger still tap → right click; two-finger movement → scroll notches
  (36 px/notch, natural or standard direction); Drag mode = drag lock with a conspicuous active state,
  End Drag releases; cancel/lost capture of a still-down finger/rotation/hidden release any hold
  without a tap, while the routine `lostpointercapture` browsers fire after each `pointerup` is
  ignored so a two-finger tap still right-clicks (component-tested in jsdom; DECISIONS 38). Drag mode
  and the held indicator reset whenever the session leaves `live` (including idle after a PC switch
  or hidden page) or the PC changes (component-tested; DECISIONS 40). Sensitivity
  0.5–3× with fractional carry; values clamped to ±4096. Buttons: Left, Right, Double (true
  `double_click`), Drag/End Drag, Keyboard, Gestures (guide sheet), Settings; Stop Input always visible.
  `touch-action: none` only on the surface (component-tested).
- **Keyboard** (`src/lib/typing.ts` + `KeyboardPanel`, unit- and component-tested): a real
  `<textarea>`; native `beforeinput`/`input`/`compositionstart`/`compositionend` listeners; each edit
  is diffed against what this phone sent and committed once; IME candidates are never forwarded;
  Enter/Tab/Esc/Backspace/Delete/arrows/Space keys; shortcuts Ctrl+A/C/V/Z, and Ctrl+L (labelled
  address bar) only while `foreground_app.browser` is set; an edit in the middle or an uncertain
  deletion pauses live entry and switches to Compose and Send with the reason; so does any loss of
  certainty about what reached the PC — an edit that could not be queued, a rejected batch
  (INPUT_STALE, RATE_LIMITED, INPUT_TARGET_CHANGED, …), more dropped events in an ack, or the session
  ending, being suspended or restarted — after which the live model and field start empty, so a later
  Backspace never deletes PC text this phone did not type (component-tested; DECISIONS 36). The live
  and compose fields are separate DOM nodes, so a compose draft never leaks into live typing
  (component-tested; DECISIONS 37). Compose and Send keeps
  a memory-only buffer, clears it only on an acknowledged send, and after an uncertain send shows a
  review state with Discard / Send again — never an automatic resend. The panel is inline so the
  touchpad stays usable while typing. No typed text is logged or stored (`log.ts` drops
  `text`/`composer`/`events`/`key(s)`; unit-tested).
- **Permissions UX**: Devices shows "Touchpad / keyboard: Allowed … / Not allowed — the PC owner grants
  it on the PC" per grant with the broad-scope explanation; INPUT_NOT_PERMITTED recovery steps say how.

### Window controls and YouTube search (protocol 1.2)

- **Version stamping** (`src/protocol/registry.ts` mirrors `since`; DECISIONS 42): every command is
  stamped with its action's `since` by the shared builder (1.0 actions `1.0`, input `1.1`, the new
  actions `1.2`), so a 1.1 PC keeps accepting everything it knows and answers a new action with
  PROTOCOL_INCOMPATIBLE ("update the PC agent"). `CommandRecord.protocolVersion` keeps the stamp and
  a confirmation carries the confirmed command's stamp.
- **Apps** (`AppsPage`, component-tested): Maximise, Restore and a Full screen toggle per app and per
  window, disabled for an app that is not running (they never start it), for one running without a
  window ("Running in the background": Edge's Startup boost, a player closed to the tray; Open shows
  it, and TARGET_GONE on an app says "no open window", not "refresh the tab") and while the PC is locked.
  Opening or closing an app re-reads the list at once, after 1.5 s and after 5 s, so the window buttons
  follow the app's first window without Refresh.
  The toggle follows the last `app_window_state_result` the PC read back (unknown = not full screen).
  FULLSCREEN_UNSUPPORTED shows the PC's own message (why nothing was pressed in that app and how to
  allow full screen for it on the PC; only an agent-reported message, as plain text) and offers
  "Maximise instead" as a separate, visible command — never a silent maximise. The list is re-read from the PC on entry and whenever the app returns to the foreground
  (`useApprovedApps`, also for the Type page), so apps added with the tray's **Add an app…** appear by
  themselves; the empty state and APP_NOT_APPROVED point there (DECISIONS 45).
- **Search YouTube** (Remote → YouTube, `YoutubeSearch`, component-tested; DECISIONS 44): ≤ 100
  characters, trimmed; searches in the tab the customer chose, else again in the last results tab
  while it still shows results, otherwise opens a new tab in the one connected browser or the one
  picked. The results tab is **not** selected (a list has no player: Pause, volume and seek keep
  reaching the video being watched, and a results page is never the implicit target); results are
  plain text and a tap first pauses a video playing in another tab, then sends `youtube.open_result`
  `{index, video_id}` to the results tab, and the opened video's tab becomes the selected one. Once a
  video was opened from the list (or its tab closed, judged only from a state report newer than the
  search's answer), the list is off with "Search again". The query is never logged (`log.ts` drops
  `query`/`*search*`), never in diagnostics, and the box clears once sent; results live in memory only
  (`lastSearchByPc`, dropped on PC switch and sign-out). Captions and playback speed are not in 1.2.
- **Typed commands** (Type page, `intents.ts` + `commandPlan.ts`, unit- and component-tested;
  DECISIONS 46): a search query is taken exactly as typed (no "%"/"'s" rewrites, "now" and "thanks"
  kept) and is free text (the injection guard applies to the words around it); a bare "and" or comma
  after an open-ended query stays in it unless a result pick follows, while "then" always starts a
  step; an approved app named like a media word (Spotify) wins by exact name; after an app step a bare
  "full screen"/"maximise" is that app; a player step right after a search asks to pick a result first.
  Opening a result while another tab plays adds a listed "Pause YouTube" step first (not counted in the
  three). A window step right after its app was started is retried twice, 2 s apart, on TARGET_GONE
  ("Waiting for … to open its window…"); Stop and a dropped connection end the plan at once and say
  the step on its way may still run. The examples and the "Which app?" help name the customer's own
  approved apps in words that parse back ("Full screen ZStream", "Exit full screen ZStream"; Notepad
  only while none is approved). A step that ends in FULLSCREEN_UNSUPPORTED shows the PC's message and
  a "Maximise instead" chip that fills "maximise <app>" (sent only with Send).

### The voice assistant, step 1 — `/app/assistant` (docs/design/assistant.md §4)

- **Deterministic first** (`lib/assistant.ts`, controller-tested): the words (speech, typed, or dictated
  with the keyboard's microphone) go through the Type page's own parser and context first. Plans,
  handoffs and clarifications are handled on the phone — instant, free, with AI off or down; chips DoMe
  built are `local` turns that never reach the AI. Only words the parser does not know go to
  `POST /v1/assistant/turn`, and only when the account consented (current consent version), this
  phone's **Use AI on this phone** is on and no limit stopped it. A clarification about something else
  (`topic: other`) offers "Ask the assistant", which sends the owner's own words on a tap.
- **The AI's steps are the grammar's steps** (`planFromAssistant` in `intents.ts`): same `PlanStep`s,
  app names resolved against the PC's approved apps on the phone ("it" = the app of the step before,
  else the conversation's last app), every step re-checked (registry, `ai_eligible`, params, fields the
  AI may not set: `video_id`, `countdown_seconds`, `app_id`), then the same plan rules (`assemblePlan`:
  ≤ 3 steps, a confirmation step alone, no duplicates). Anything else is a clarification; nothing
  partial runs. In mode `act` the model's reply is ignored.
- **Running** (`runPlanOnPc`, shared with the Type page, `source: "assistant"`): the plan card is always
  shown first; a plan runs at once only when every step's registry risk is `low` (or it is a single
  step the PC confirms), **Ask before every action** is off, the page stayed visible and the plan was
  ready ≤ 15 s after the words, it did not come from a multi-step chip and the words did not end on
  "and"/"then"/a comma. Otherwise Run/Cancel; a spoken "yes" never runs anything. Lock, open, focus
  and full screen are `moderate` and always wait for Run. A new request supersedes a card still
  waiting for Run ("Not run."), so yes/no only ever answer the newest card; a yes or no after a
  clarification with choices is answered on the phone ("Please tap your choice on the screen.", "Very
  well."), never sent to the AI. "Cancel"/"never mind" while a countdown runs always cancel the
  countdown. Shut-down, restart, sleep and close go to the PC's own signed challenge in the global
  confirmation modal; after a countdown started, the card offers a large "Cancel shut-down", which sends
  `power.cancel` through the same runner and shows ("Cancelling…", then the outcome) and says the
  result. A turn records its PC: switching PC cancels the AI request in flight and drops anything the
  turn would still do ("You switched PCs, so I've left that request."), and a card only runs on the PC
  it was shown for. Waiting for the PC's apps shows "Checking the PC's apps…" and is busy like
  thinking.
- **What is said** (`butlerSpeech.ts`): deterministic sentences from the real results ("Very good.
  YouTube is paused."), the configured form of address (none by default), never a search query, title,
  URL or key name aloud (one exception: "what is playing" asked on its own). The Type page's messages
  have spoken statements of their own (a "Did you mean …?" or "Play “…” on YouTube?" question is said
  as a statement, a message quoting the owner's words is said without them, help is "things you can
  ask me"), and each AI error code has one line naming the real cause. `recent` for a later AI
  turn holds closed template lines only ("(DoMe handled this itself: open the app)", "The app is
  open."), never a PC display name, player label, title, query or agent message.
- **Speech** (`speech.ts`): Web Speech recognition started only from the talk tap (tap to talk, tap to
  stop, 15 s from `audiostart`, aborted when hidden), with every iOS error mapped (`service-not-allowed`
  → Dictate, remembered per display mode; two transient failures → Dictate for the session; "Try voice
  again"); speechSynthesis with an en-GB ranking (Daniel/Arthur first, novelty voices never by
  themselves), unlocked by non-talk taps (tried again on each until speech has really started once),
  "Tap to hear" when speech did not start in 1.5 s (that tap and Test voice speak at once, without
  waiting for the voice list), mute, rate 0.8–1.2, nothing spoken while listening or hidden.
- **Screen readers and layout**: bubbles carry "You said:" / "{name}:" for screen readers; the polite
  live region reads each new butler line once and is never refilled with an older one after listening;
  passing notices are a `role="status"`; footer links are 44 px targets. The space under the
  conversation follows the dock's measured height (ResizeObserver), so the newest card's buttons can
  scroll above the dock in every state.
- **Consent and Settings** (`AssistantSettingsCard`): status and the reason when unavailable, the
  allowance ("5 of 300 used this month · renews …"), consent (DRAFT text, version pinned by the
  contract) and withdrawal for the account, and the phone preferences (`dome.assistant.prefs`: Ask tab,
  Use AI, name, form of address, Ask before every action, Read replies aloud, voice, rate). The
  conversation itself is never stored.

### Now Playing, health, support, upgrade

- **Now Playing** (`NowPlaying`, `lib/nowPlaying.ts`, unit- and component-tested): PC name, app or
  browser/profile, title, state. A YouTube control is only ever sent to the resolved YouTube tab; two
  playing tabs or several media sessions ask for a deliberate choice; a closed target shows nothing
  rather than another tab; the Windows media fallback is labelled with its app and an explanation.
  Inline explanations for unavailable Previous/Next, ads and fullscreen.
- **Connection health** (`/app/health`, `lib/health.ts`, unit- and component-tested): seven layers —
  phone connectivity, account session, PC agent relay connection (unknown cause stated), local
  remote-control/lock, manual-input permission (+ restricted screen; the PC's input-session owner is
  compared with this phone's controller id, so "another phone" is only said when observed —
  unit-tested, DECISIONS 39), extension, media target — each
  with one next action; codes/versions behind a control; Retry bounded to 3 attempts then the support
  path (a retry never re-sends a command); last verified result; the first-use walkthrough that returns
  to the failed step; the V1 requirements stated plainly. Linked from every status pill, the reconnect
  banner and every failure.
- **Support** (`/support`, component-tested): self-help topics; signed-in form → `POST
  /v1/support/tickets` (category preselected from the help link, optional reviewed redacted diagnostics
  ≤ 32 KiB) → reference on 201; a definite rejection (never left the phone, 4xx) → "Not sent"; a
  network drop, 5xx or unreadable 2xx → "Not confirmed: DoMe could not confirm whether support
  received this request" with "Refresh your requests" before any deliberate resend (component- and
  unit-tested; DECISIONS 41); both with a copyable redacted summary, never a receipt claim; `GET /v1/support/tickets` lists the account's requests. `/release-notes` renders
  `src/content/release-notes.json` honestly (pre-release, no device verification). The Download page's
  official Windows path reads "not yet published".
- **Upgrade experience** (component-tested): no upgrade copy, modal or banner on Dashboard, Remote,
  Touchpad, Devices, Health or the shell (online or offline); Routines and Custom remotes (deliberate
  selections from More) show one dismissible `ProExplanation` naming the benefit and never a
  connectivity claim.
- **Brand**: `pnpm gen:icons` installs the brand icon (`brand/icon.svg`) renders into `public/icons`
  (192/512 + maskable, apple-touch-icon, favicon SVG/PNG/ICO); manifest and `index.html` updated.
- **PWA / theme / accessibility**: `autoUpdate` worker precaching the shell only; 44 px targets; safe
  areas; reduced motion. Theme ("Midnight Studio", DECISIONS 55): follows the device by default; the
  Light / Auto / Dark switch (website header, menu and footer; More in the app) is remembered in
  `localStorage` (`dome.theme`) and applied before the first paint by `public/theme-init.js`.

## Tests

Voice assistant (step 1): `assistantPlan` (equivalence with typed words, app resolution, refusals, plan
rules, `commandText` round trip for every AI-eligible action, the shared fixture
`shared/protocol/fixtures/assistant-plan-cases.json`), `assistant` (the controller with fake API/PC/
speaker/visibility/clock: deterministic first, AI usability, act/ask/answer/decline, every error's
fallback and its copy per code, Cancel and the AI_BUSY resend, the risk rule, yes/no/cancel with
countdowns, superseded cards and clarifications, switching PC mid-turn, the busy app-list wait,
readiness, failures, `recent` bounds, the T2 multi-turn sentinel test, timings), `butlerSpeech`,
`speech` (fake recognizer and synthesiser; the unlock latch), `assistantContext`, `assistantStore`,
`api` (the four endpoints), `checkBundle` (the build's key-material check) and components
`AssistantPage` (over the real runtime: talk states, live line and live region, auto-run vs Run, the
AI's shutdown through the confirmation modal, Cancel shut-down's three outcomes, a PC switch while
thinking, tags, allowance chip, banners, XSS, dictation, the dock spacer) and `AssistantSettings`
(consent POST/DELETE, prefs, clearing the form of address, voice picker, tab swap, speech unlock, More
entries).
`parseCommand`'s clarifications gained a `topic`; three expectations in `intents.grammar` name it.

`pnpm test` — **51 files / 1116 tests, all passing** (jsdom, fake-indexeddb, fake sockets/timers; no
network). New in 1.2: `commands`/`confirmations` (each action stamped with its `since`, verified by the
real verifier; confirmations carry the command's stamp), `protocol-parity` (`since` per action, 1.2
params/targets/results, a below-`since` stamp refused), `targets` (search target: chosen tab, new tab,
browser choice; results-tab target), `labels` (1.2 labels, steps, help categories, result summaries),
`liveStore` (app.list and search results applied centrally, the results tab never selected and the
opened video's tab selected once, cleared on PC switch/sign-out), `log` (query/search redaction), components `AppsPage` (Maximise/Restore/Full screen
targets and stamps, disabled states, toggle from read-back state, "Maximise instead", Add an app…
empty state, re-read on foreground), `ApprovedApps` (hook waits for fresh state, no duplicate request)
and `YoutubeSearch` (new tab by default, trimmed query, box cleared, query absent from log and
diagnostics, playing video kept as the target, pause-then-open, list off after an open, no false
"closed" before the next state report, browser choice, closed results tab). Review fixes added
`intents.grammar` "review findings", `intents.plan` (pause step, window retry, Stop/disconnect),
`CommandPage` (pause step, Stop, disconnect, window wait, examples) and `AppsPage` (windowless apps,
re-read after Open) cases.
`relay.test.ts` now expects `hello` to offer this build's `PROTOCOL_VERSION`/`REGISTRY_VERSION` instead
of pinned strings. New in 1.1: `gestures` (tap/double thresholds, finger-count change never clicks, scroll
without tap, drag lock + End Drag, cancel releases, sensitivity/clamp, guide coverage), `typing`
(append once, IME commits once in either event order, autocorrect mapping, middle edit → pause,
uncertain deletion → pause, bare backspace, grapheme-safe splitting), `input` (session start via
signed command, batch building/seq/coalescing/size, keepalive cadence with fake timers, ack handling +
awaitAck outcomes, ended/suspended frames, takeover, stop on hidden / PC switch / sign-out / lost
socket, INPUT_* error routing, pointer-only grant, no text in logs), `health` (every layer state and
action, walkthrough), components `TouchpadPage` (session on entry/stop on leave, tap/move/right click,
live pill from input_ack, Drag/End Drag/pointercancel, buttons, takeover prompt, missing grant
explanation, implicit lostpointercapture between the lifts of a two-finger tap → exactly one right
click, capture lost by a still-down finger → no click and a held drag released, drag/held reset after
an idle stop) and `KeyboardPanel` (commit once, Enter as key, IME, middle edit → Compose and Send,
Ctrl+L gating, Compose and Send success/review without resend, INPUT_STALE / dropped-events ack /
suspended-then-restart → pause with no Backspace for earlier text, silent reset with nothing typed,
compose draft never leaks into the live field), `health` (PC input session owned by this phone vs.
another controller), `support` (submission failure classification), `NowPlaying`, `PowerConfirmation`,
`SupportPage` (201 reference, 4xx "Not sent", 5xx / unreadable 201 / network "Not confirmed" with a
list refresh and no resend, signed-out), `UpgradeAndHealth`
(no upgrade copy on Free flows, dismissible Pro explanation, download not published, release notes,
Health page layers/details/retries/walkthrough). Expectations updated for 1.1: `pairing.test.ts`
(default capability list now includes pointer/keyboard), `Dashboard.test.tsx` (the PC name also
appears in the Now Playing panel), and the `hello_ack`/agent `protocol_version` fixtures in
`test/helpers/harness.ts`, `relay.test.ts`, `runtime.test.ts`, `protocol-parity.test.ts` and `api.test.ts` now say
`1.1` — the phone offers only `["1.1"]` in `hello`, so a relay can only answer `1.1` (the client
does not re-check the negotiated value; the fixtures were merely stale). `pnpm typecheck`, `pnpm lint` and `pnpm build` pass.

## Not verifiable here (manual checklist)

Nothing below has been run on a device. Each row is **not yet verified**.

| Area | Step |
| --- | --- |
| Add an app (1.2) | Add an app with the tray's **Add an app…**, switch back to the PWA (already on Apps or Type): the app appears without tapping Refresh. |
| Window controls (1.2) | Apps → Notepad: Maximise, Restore, Full screen; Edge: Full screen on/off (F11); Notepad Full screen → the PC's message (how to allow full screen for that app on the PC) and "Maximise instead" offered, nothing maximised until tapped; a closed app's window buttons are disabled. Type "full screen zstream": the same message and a "Maximise instead" chip, unless full screen is allowed for ZStream on the PC. |
| YouTube search (1.2) | Remote → Search YouTube "lofi": a new Edge tab opens with results, the list appears on the phone, tapping result 2 plays it; a second search reuses that tab; the query is absent from Settings → Diagnostics. |
| iOS Safari key persistence | Install to Home Screen, pair, kill the app, reopen after 24 h and after a device restart: Devices must still show "This phone" without re-pairing. |
| Camera QR scan on iPhone | Devices → Pair → Scan: camera prompt appears once, QR from the PC's tray window is read within ~2 s; denial falls back to typing. |
| Background/resume | Pause YouTube from the remote, background the PWA for 2 minutes, resume: status shows "Online · refreshing" then "Online"; no queued command replays. |
| Real relay path | With cloud-api + pc-agent running: Next changes the video in a background tab; result shows the transition. |
| Confirmation on device | Sleep… → modal shows the registry line, the fixed loss-of-access / no-remote-wake copy and the PC's countdown detail; Approve → "Windows accepted the request". |
| Lighthouse/PWA audit | Installability, contrast ≥ 4.5:1, touch target sizes on a real iPhone; 5-tab bar legible at 375 px; both themes. |

### Manual iPhone checklist — voice assistant (docs/design/assistant.md §8.6)

Not run on a device yet. Safari tab **and** Home Screen app, iOS version recorded:

| Area | Step |
| --- | --- |
| Recognition | Tap to talk: "Getting ready…" then "Listening…"; or `service-not-allowed` and the switch to Dictate (expected in the Home Screen app); "Try voice again"; the dock stays above the keyboard while dictating. |
| Dock | In each dock state (idle, text field open, Thinking with Cancel, Working with Stop, Dictate with its hint, keyboard open), a plan card that waits for Run: its Run and Cancel buttons scroll fully above the dock. |
| Permissions | Deny the microphone: Dictate and "How to allow it" — check the Settings path shown against iOS 17 and 18, Safari and the Home Screen app, and correct the copy. Siri & Dictation off: the Dictate copy. |
| Interruptions | An incoming call or Siri while listening and while speaking: recognition aborts, speech stops, the talk button returns to idle and works again. |
| Network | Airplane mode while Thinking → the phone's own answer, nothing sent to the PC; while Working → the honest connection message. |
| Background | Lock or background the phone while listening, while Thinking (→ the plan comes back with Run, not run) and while speaking. |
| Misheard / pauses | Say "look my computer": the model asks or the Run card shows, nothing runs by itself. "Open Spotify … and then play some jazz" with a pause: a Run card, not a half-run. |
| Speech out | Replies with the Silent switch on and off, right after the microphone was used, after returning from the background and after a relaunch; whether `navigator.audioSession` routing helps (`ROUTE_AUDIO_SESSION` in `speech.ts`); "Tap to hear" when speech stays locked. |
| Voices | Which en-GB voices the phone lists; the ranking and deny list in `speech.ts` confirmed; mute / Read replies aloud, form of address, Ask before every action. |
| VoiceOver | With Read replies aloud off: no double reading; nothing captured into the transcript. |
| Latency | Settings → Diagnostics `assistant.timing` entries for the AI checks: words ended → response, → first result, → speech; the first request after a restart noted separately. |

### Manual iPhone checklist — touchpad and keyboard (spec §10A F, scenarios 18–20)

Gesture table (Safari and installed PWA, Windows PC with pointer+keyboard granted):

| Phone | Expected on Windows | Check |
| --- | --- | --- |
| Slide one finger | cursor moves relatively; sensitivity slider changes speed | no jump on lift/reposition |
| Short still tap | one left click | no extra click after a slow or moved touch |
| Two quick taps (same spot) | double click (e.g. opens a desktop icon) | second tap > 300 ms later = two singles |
| Double button | double click | works where the two-tap timing fails |
| Two-finger still tap / Right button | context menu | first finger lifting early still right-clicks (Safari fires lostpointercapture between the lifts) |
| Two fingers moving | scroll (vertical; horizontal where supported); Natural vs Standard | no click, no cursor jump when one finger lifts first |
| Drag → move → End Drag | window/icon drags, release at End Drag | lifting mid-drag keeps the hold (drag lock) |
| Rotation / incoming call / app switch during drag | hold released ("Held on the PC" clears), no click | pill returns to Live after resume only via Start |
| Stop Input | `input.session_stop` sent; PC releases holds | buttons disabled, Start offered |

Keyboard varieties (click a real field on the PC first — Google search, address bar, Notepad):

| Case | Expected | Check |
| --- | --- | --- |
| Plain typing, spaces, punctuation | appears live, once | echo counter matches |
| Enter | submits search / newline in Notepad | nothing submitted on its own after text |
| Autocorrect of the last word | backspaces + corrected word; no duplication | "teh" → "the " |
| Autocorrect of an earlier word, caret moved | "Live typing paused", Compose and Send shown, PC text unchanged | nothing deleted on the PC |
| Japanese/Chinese IME | committed text once; candidates never appear on the PC | composition cancel sends nothing |
| Dictation | text appears once | long dictation splits into ≤ 256-char events, no cut emoji |
| Emoji, accents (é, ñ), non-Latin | inserted correctly | deleting an emoji on the phone pauses live entry |
| Ctrl+L in Chrome/Edge | address bar focused; button absent when Notepad is in front | modifier released afterwards |
| Ctrl+A/C/V/Z | act on the PC's clipboard/field | pasting into the composer is plain text |
| Compose and Send | text appears once; "Windows accepted N characters" | airplane mode before Send → review state, no automatic resend |
| Live typing on weak cellular (INPUT_STALE) or after Stop/Start | "Live typing paused"; erasing on the phone deletes nothing on the PC | PC text you typed yourself stays intact |

Rotation, backgrounding, network loss:

| Case | Expected |
| --- | --- |
| Rotate during two-finger scroll | gesture cancelled, no click, scrolling resumes with the next touch |
| Background the PWA while dragging | held button released by the PC (lease) and stop sent; pill not Live on return until Start |
| Airplane mode during drag / key hold / Compose and Send | hold released on the PC within ~3 s (lease); phone shows Ended with PC_RECONNECTING; Compose shows review, never resends; after reconnect nothing stale is injected |
| Second phone takes over | first phone shows "Another phone took over", holds released count; Take over from the first re-acquires |
| Windows lock / UAC prompt while live | PC ends the session (PC_SESSION_LOCKED / INPUT_RESTRICTED copy); nothing typed into the secure desktop |
