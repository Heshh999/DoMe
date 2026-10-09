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
                 throttle, log (redacting), diagnostics, format, outcome, connection, power, pricing
  store/         zustand: session, devices (REST inventory + selection), live (relay state, commands), input
  app/           runtime wiring, AppShell (tabs, banners, confirmation modal), PublicLayout, router, hooks
  components/    ui primitives, Sheet, ConfirmationModal, CommandOutcome, VolumeSlider, PcSwitcher, PcStatus,
                 OfflineScreen, TouchpadSurface, KeyboardPanel, GestureGuide, NowPlaying, FailureLinks,
                 ProExplanation
  pages/app/     Dashboard, Remote, Touchpad, Command, Apps, Health, Routines (Pro preview), Layouts (Pro
                 preview), Devices, Pair, Settings, Billing, Link, More
  pages/public/  Landing, Pricing, FAQ, Download, Support (+ ticket form), ReleaseNotes, Privacy (DRAFT),
                 Terms (DRAFT), NotFound
  content/       release-notes.json (public release history + known issues)
test/            vitest (jsdom, fake-indexeddb, fake sockets/timers); test/components for UI,
                 test/helpers/harness.ts (fake socket + Runtime factory shared by component tests)
scripts/         gen-validators.ts, make-icons.mjs
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
  End Drag releases; cancel/lost capture/rotation/hidden release any hold without a tap. Sensitivity
  0.5–3× with fractional carry; values clamped to ±4096. Buttons: Left, Right, Double (true
  `double_click`), Drag/End Drag, Keyboard, Gestures (guide sheet), Settings; Stop Input always visible.
  `touch-action: none` only on the surface (component-tested).
- **Keyboard** (`src/lib/typing.ts` + `KeyboardPanel`, unit- and component-tested): a real
  `<textarea>`; native `beforeinput`/`input`/`compositionstart`/`compositionend` listeners; each edit
  is diffed against what this phone sent and committed once; IME candidates are never forwarded;
  Enter/Tab/Esc/Backspace/Delete/arrows/Space keys; shortcuts Ctrl+A/C/V/Z, and Ctrl+L (labelled
  address bar) only while `foreground_app.browser` is set; an edit in the middle or an uncertain
  deletion pauses live entry and switches to Compose and Send with the reason; Compose and Send keeps
  a memory-only buffer, clears it only on an acknowledged send, and after an uncertain send shows a
  review state with Discard / Send again — never an automatic resend. The panel is inline so the
  touchpad stays usable while typing. No typed text is logged or stored (`log.ts` drops
  `text`/`composer`/`events`/`key(s)`; unit-tested).
- **Permissions UX**: Devices shows "Touchpad / keyboard: Allowed … / Not allowed — the PC owner grants
  it on the PC" per grant with the broad-scope explanation; INPUT_NOT_PERMITTED recovery steps say how.

### Now Playing, health, support, upgrade

- **Now Playing** (`NowPlaying`, `lib/nowPlaying.ts`, unit- and component-tested): PC name, app or
  browser/profile, title, state. A YouTube control is only ever sent to the resolved YouTube tab; two
  playing tabs or several media sessions ask for a deliberate choice; a closed target shows nothing
  rather than another tab; the Windows media fallback is labelled with its app and an explanation.
  Inline explanations for unavailable Previous/Next, ads and fullscreen.
- **Connection health** (`/app/health`, `lib/health.ts`, unit- and component-tested): seven layers —
  phone connectivity, account session, PC agent relay connection (unknown cause stated), local
  remote-control/lock, manual-input permission (+ restricted screen), extension, media target — each
  with one next action; codes/versions behind a control; Retry bounded to 3 attempts then the support
  path (a retry never re-sends a command); last verified result; the first-use walkthrough that returns
  to the failed step; the V1 requirements stated plainly. Linked from every status pill, the reconnect
  banner and every failure.
- **Support** (`/support`, component-tested): self-help topics; signed-in form → `POST
  /v1/support/tickets` (category preselected from the help link, optional reviewed redacted diagnostics
  ≤ 32 KiB) → reference on 201; on failure "Not sent" + copyable redacted summary, never a receipt
  claim; `GET /v1/support/tickets` lists the account's requests. `/release-notes` renders
  `src/content/release-notes.json` honestly (pre-release, no device verification). The Download page's
  official Windows path reads "not yet published".
- **Upgrade experience** (component-tested): no upgrade copy, modal or banner on Dashboard, Remote,
  Touchpad, Devices, Health or the shell (online or offline); Routines and Custom remotes (deliberate
  selections from More) show one dismissible `ProExplanation` naming the benefit and never a
  connectivity claim.
- **Brand**: `pnpm gen:icons` installs the brand icon (`brand/icon.svg`) renders into `public/icons`
  (192/512 + maskable, apple-touch-icon, favicon SVG/PNG/ICO); manifest and `index.html` updated.
- **PWA / theme / accessibility**: unchanged from 1.0 (`autoUpdate` worker precaching the shell only;
  dark default, light via `prefers-color-scheme`; 44 px targets; safe areas; reduced motion).

## Tests

`pnpm test` — **30 files / 273 tests, all passing** (jsdom, fake-indexeddb, fake sockets/timers; no
network). New in 1.1: `gestures` (tap/double thresholds, finger-count change never clicks, scroll
without tap, drag lock + End Drag, cancel releases, sensitivity/clamp, guide coverage), `typing`
(append once, IME commits once in either event order, autocorrect mapping, middle edit → pause,
uncertain deletion → pause, bare backspace, grapheme-safe splitting), `input` (session start via
signed command, batch building/seq/coalescing/size, keepalive cadence with fake timers, ack handling +
awaitAck outcomes, ended/suspended frames, takeover, stop on hidden / PC switch / sign-out / lost
socket, INPUT_* error routing, pointer-only grant, no text in logs), `health` (every layer state and
action, walkthrough), components `TouchpadPage` (session on entry/stop on leave, tap/move/right click,
live pill from input_ack, Drag/End Drag/pointercancel, buttons, takeover prompt, missing grant
explanation) and `KeyboardPanel` (commit once, Enter as key, IME, middle edit → Compose and Send,
Ctrl+L gating, Compose and Send success/review without resend), `NowPlaying`, `PowerConfirmation`,
`SupportPage` (201 reference, failure copy + summary, network failure, signed-out), `UpgradeAndHealth`
(no upgrade copy on Free flows, dismissible Pro explanation, download not published, release notes,
Health page layers/details/retries/walkthrough). Expectations updated for 1.1: `pairing.test.ts`
(default capability list now includes pointer/keyboard) and `Dashboard.test.tsx` (the PC name also
appears in the Now Playing panel). `pnpm typecheck`, `pnpm lint` and `pnpm build` pass.

## Not verifiable here (manual checklist)

Nothing below has been run on a device. Each row is **not yet verified**.

| Area | Step |
| --- | --- |
| iOS Safari key persistence | Install to Home Screen, pair, kill the app, reopen after 24 h and after a device restart: Devices must still show "This phone" without re-pairing. |
| Camera QR scan on iPhone | Devices → Pair → Scan: camera prompt appears once, QR from the PC's tray window is read within ~2 s; denial falls back to typing. |
| Background/resume | Pause YouTube from the remote, background the PWA for 2 minutes, resume: status shows "Online · refreshing" then "Online"; no queued command replays. |
| Real relay path | With cloud-api + pc-agent running: Next changes the video in a background tab; result shows the transition. |
| Confirmation on device | Sleep… → modal shows the registry line, the fixed loss-of-access / no-remote-wake copy and the PC's countdown detail; Approve → "Windows accepted the request". |
| Lighthouse/PWA audit | Installability, contrast ≥ 4.5:1, touch target sizes on a real iPhone; 6-tab bar legible at 375 px. |

### Manual iPhone checklist — touchpad and keyboard (spec §10A F, scenarios 18–20)

Gesture table (Safari and installed PWA, Windows PC with pointer+keyboard granted):

| Phone | Expected on Windows | Check |
| --- | --- | --- |
| Slide one finger | cursor moves relatively; sensitivity slider changes speed | no jump on lift/reposition |
| Short still tap | one left click | no extra click after a slow or moved touch |
| Two quick taps (same spot) | double click (e.g. opens a desktop icon) | second tap > 300 ms later = two singles |
| Double button | double click | works where the two-tap timing fails |
| Two-finger still tap / Right button | context menu | first finger lifting early still right-clicks |
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

Rotation, backgrounding, network loss:

| Case | Expected |
| --- | --- |
| Rotate during two-finger scroll | gesture cancelled, no click, scrolling resumes with the next touch |
| Background the PWA while dragging | held button released by the PC (lease) and stop sent; pill not Live on return until Start |
| Airplane mode during drag / key hold / Compose and Send | hold released on the PC within ~3 s (lease); phone shows Ended with PC_RECONNECTING; Compose shows review, never resends; after reconnect nothing stale is injected |
| Second phone takes over | first phone shows "Another phone took over", holds released count; Take over from the first re-acquires |
| Windows lock / UAC prompt while live | PC ends the session (PC_SESSION_LOCKED / INPUT_RESTRICTED copy); nothing typed into the secure desktop |
