# mobile-app — DoMe iPhone-first PWA

The customer-facing web app: public website (landing with the demo film, `/demo`, pricing, FAQ,
download, support, release notes/known issues, privacy/terms drafts) plus the installable remote
(`/app/*`), both in the "Midnight Studio" design (light and dark). React 19, TypeScript 5.9, Vite 7,
Tailwind CSS 4, React Router 7, zustand, idb, vite-plugin-pwa, Vitest + Testing Library. Depends on
the shared protocol library `@dome/protocol` (`link:../shared/ts`), protocol **1.2** (1.1: manual
touchpad/keyboard, spec §10A; 1.2: window controls and YouTube search). Production origin:
`https://domeremote.com` (website, PWA, API and WebSockets on one host; `deploy/README.md` "Domain").

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

To look at every app screen without a backend, use the design preview (`/preview`, below). To export
the demo film as video files, see "The demo film" below.

Run cloud-api locally on port 8000 with `DOME_PUBLIC_ORIGIN=http://localhost:5173` (and
`tools/dev-idp` as the OIDC issuer) so sign-in redirects back to the dev server. In production the
built `dist/` is served by cloud-api from the same origin (`DOME_STATIC_DIR`); there is no CORS setup.
`index.html` carries Open Graph / Twitter tags with the absolute preview image
`https://domeremote.com/og-image.png` (1200×630, kept out of the PWA precache); `public/robots.txt`
keeps `/app`, the device links and the API out of search engines and `public/sitemap.xml` lists exactly
the public routes (`test/public/domain.test.ts` keeps it in step with the router).

Environment (all optional, see `vite-env.d.ts`): `VITE_DOME_API_ORIGIN` (unusual: API on another
origin), `VITE_DOME_RELEASE_CHANNEL`, `VITE_DOME_SUPPORT_URL` (an additional direct contact shown on
Support pages; the in-app ticket form works without it).

## Design system and themes ("Midnight Studio", DECISIONS 55–60)

- **Tokens** live in `src/styles.css` only: colour roles `--dome-color-*` (bg, bg-elevated, bg-raised,
  bg-sunken, surface, border, border-strong, control-edge, text, text-muted, text-faint, logo-ink, accent,
  primary, on-primary, coral, success, warning, danger, info, focus), `--dome-card-*`, `--dome-shadow-*`,
  `--dome-bar-fill` (tab bar and Ask dock: 98 % opaque, with an opaque fallback where `backdrop-filter` is
  missing), `--dome-radius-*`, `--dome-section`, `--dome-ease` and `--dome-dur*`. `@theme inline` maps them
  to Tailwind names (`bg-bg`, `text-text-muted`, `rounded-card` …); the type scale is `text-display`,
  `text-h1`, `text-h2`, `text-title`, `text-caption`.
- **Light** is the base: #f6f8fc page, white cards with a soft Navy-tinted shadow, Navy #001441 text.
  **Dark** is the brand's Navy as the page, translucent "glass" cards and a soft DoMe Blue glow at the top
  (`.dome-backdrop`). `.dome-dark` gives one region the dark palette in either theme (the website's Navy
  bands, the phone picture, the film stage). Where these values differ from the surface tokens that
  `brand/BRAND.md` recommends, BRAND.md §2 lists what shipped.
- **Rules**: one solid DoMe Blue control per screen, the primary action (`.dome-primary`); icon wells are
  tinted (`IconWell`), not gradients; Coral only as tiny accents (the active tab's dot, a status dot, the
  logo's cursor); 44 px targets; motion is a short ease-out, all of it off under `prefers-reduced-motion`;
  WCAG AA contrast in both themes on the composited values (DECISIONS 55).
- **Building blocks**: `components/ui.tsx` (Button, IconButton, Card, Pill, Chip, PageHeader, HeaderPill,
  BackLink, IconWell, Segmented, ListCard, ListRow, KeyValue, CheckRow, SectionLabel, EmptyState, Field,
  Toggle, Notice, Steps, Spinner), `components/icons.tsx` (one 24-unit line set; append, never rename),
  `components/brand.tsx` (Wordmark, BrandMark, AppIcon from `brandPaths.ts`, which a test compares with
  `brand/*.svg`). Website CSS is `src/styles/public.css` (every rule under `.dome-public`, imported by
  `PublicLayout`); the film's is `src/styles/film.css`.
- **CSP** (cloud-api `security/headers.py`: `default-src 'self'`, `style-src 'self'`, `img-src 'self'
  data:` …): no inline `<script>` or `<style>` elements and no external fonts, images or scripts; React
  `style={{…}}` props are fine. Type is the system stack (SF Pro on Apple devices); no font is bundled.
- **Theme switch**: Light / Auto / Dark (website header, menu and footer; More in the app). Nothing stored
  means follow the device; a choice is stored as `dome.theme` and applied before the first paint by
  `public/theme-init.js` (external and render-blocking, because the CSP forbids an inline head script), which
  also sets `<meta name="theme-color">` (#f6f8fc light, #001441 dark). The dark palette is written twice
  in `styles.css` (`prefers-color-scheme: dark` without a saved light choice, and `[data-theme=dark]`); a
  test keeps the two blocks identical.

## Design preview (development only, DECISIONS 59)

With `pnpm dev` running, `/preview…` runs the real app (pages, stores, runtime, relay client, command
signing and the butler) against a fake account and a fake PC in `src/dev/preview.ts`. No backend is
needed and nothing talks to a network. The fixture: "Living Room PC" online, YouTube playing in Edge,
volume 40, approved apps ZStream, Edge and Spotify, a second paired phone, Free's AI taste (4 of 10 used)
and a short butler conversation that types itself in.

| Route | Shows |
| --- | --- |
| `/preview` | Home |
| `/preview/remote` | Remote (YouTube tab with a live scrubber, Spotify paused, search answers) |
| `/preview/assistant` | Ask (the butler conversation) |
| `/preview/apps` | Apps (ZStream and Edge running, Spotify closed; every button answers) |
| `/preview/devices`, `/preview/devices/pair` | Devices; Pair (type `ABCDE-FGHJK-MNPQR-STVWX` → details → waiting for approval) |
| `/preview/settings` | Settings & help (AI on; the consent sheet opens) |
| `/preview/health` | Connection health (Retry and the walkthrough work) |
| `/preview/billing` | Billing (the Free plan) |
| `/preview/command`, `/preview/touchpad` | Type a command (plans run on the fake PC); Touchpad (a live input session, keyboard panel) |
| `/preview/routines`, `/preview/layouts`, `/preview/more` | Routines and Custom remotes (Pro previews); More |
| `/preview/link?user_code=ABCD-EFGH` | Link a PC (preview answers; approving is not in the preview) |

Flags on any route: `?offline=1` (the offline screen), `?confirm=1` (the PC asks this phone to confirm
closing Edge: the global confirmation modal), `?pc=offline` (the PC went offline 3 hours ago: Home and
Remote say what to do, and a command fails with PC_OFFLINE as the relay would answer). `/preview/off`
leaves preview mode, which lasts for this tab only. `PREVIEW_ROUTES` and `PREVIEW_FLAGS` in `preview.ts`
are the list; `test/previewRoutes.test.ts` checks that each route exists, that every `/app` page has one
and that the offline PC answers a command with PC_OFFLINE. Extend the fixtures there when a page needs more state. `main.tsx` imports the module only under
`import.meta.env.DEV`, a test forbids any static import of `src/dev/`, and `scripts/check-bundle.ts` fails
`pnpm build` if its marker (`dome-design-preview`) reaches `dist/`.

## The demo film (`src/components/film/`, `/demo`, DECISIONS 61)

A 66-second web animation (SVG + CSS + the design tokens; no video file, no external asset), muted and
captioned. It plays on the home page ("See it in action", right after the hero) and full-width at
`/demo`. Six scenes (`timeline.ts`): 1 Across the room (0–8 s), 2 Just ask (8–22 s), 3 Screen view
(22–40 s), 4 Point-and-talk (40–50 s), 5 Pick your plan (50–60 s), 6 the DoMe Remote end card (60–66 s).
Each scene's chip says what exists (DECISIONS 63): remote control and the AI butler "Available now",
Screen view "Coming", point-and-talk "Planned", Pro and Pro Plus "arrive at paid launch". Under the film:
"Concept preview. Screen view and point-and-talk are in development." Scene 5's plan facts come from
`content/plans.ts`.

- One clock (`useFilmClock`) drives the stage; every frame is a pure function of the time (`script.ts`
  holds every beat), so stills, scene jumps and exports are exact.
- Controls: play/pause, scene chips, a thin progress bar, replay; the frame takes Space / Enter / K,
  ← / →, Home and 1–6. Captions are a polite live region that stays silent until the visitor starts the
  film or picks a scene.
- Autoplay only while at least half of the frame is visible (85 % in the vertical layout); paused while
  the page is hidden; scene 1's still is the poster. Under `prefers-reduced-motion` the film is a
  storyboard of stills with Previous / Next.
- Layouts (`layout.ts`): `wide` 16:9, and `vertical` 9:16 below 600 px (`VERTICAL_BELOW_PX`) or when
  forced, with a tall caption band under a larger phone.
- `/demo` query flags: `?scene=N&paused=1` (scene N's still), `?t=<ms>&paused=1` (any moment),
  `?layout=vertical|wide`, `?export=1` (no page chrome, controls hidden, deterministic from t = 0, and
  `window.__domeFilm` with seek / play / pause for the export script).

**Export to video.** The files are for sharing and social posts; they are never committed and never
served by the site.

```sh
pnpm build                                      # the script serves dist/ with `vite preview` unless --base is given
node scripts/export-film.mjs --out ~/dome-film  # without --out: <system temp>/dome-film-export
# options: --base <url>   --fps 30   --only wide|vertical   --quick (10 fps)   --record
```

Output: `dome-demo-1080p.mp4` + `.webm` (1920×1080), `dome-demo-vertical.mp4` + `.webm` (1080×1920) and
`poster.png` (scene 1's still). The default mode seeks the film clock frame by frame, screenshots each
frame with Playwright and pipes the PNGs into ffmpeg: constant 30 fps, every frame sharp, the same result
on every run (about 9 MB per MP4, 66.0 s). `--record` instead uses Playwright's real-time `recordVideo`
screencast and trims the lead-in where the recording shows the film starting; its timing and quality are
the screencast's, so it is a fallback only. Tools: `playwright-core` (this package or `/opt/node-tools`),
a Chromium headless shell (`DOME_CHROMIUM`) and ffmpeg (`FFMPEG`, `FFPROBE`; default `/usr/bin/ffmpeg`).
Check an export by pulling frames:
`ffmpeg -ss 17.6 -i dome-demo-1080p.mp4 -frames:v 1 frame.png` (each scene's `still` time is in
`timeline.ts`).

## Plan content (`src/content/plans.ts`, `src/lib/pricing.ts`; DECISIONS 62–65)

Free / Pro / Pro Plus: the owner's decisions of 2026-10-12, now read from the contract (protocol 1.3,
`plans.json` `pricing_defaults` and the plans' `ai_*` fields) with the same figures as literal fallbacks,
and a drift test (`test/public/plans.test.ts`) that fails if content and contract ever disagree. Whether
the paid plans can be bought is not content: the pricing page and the FAQ ask `GET /v1/plans` and the
Billing page asks `GET /v1/account/billing` (see "Billing" below).

| | Free | Pro | Pro Plus |
| --- | --- | --- | --- |
| Status | "Available now" (chip) | "Coming at paid launch" (status line, not a button) until the server reports `billing_enabled`; then "Available now" + "Sign in to subscribe" → `/app/billing` (+ "Test mode" in Stripe test mode) | same as Pro |
| Price (USD, before tax) | $0 | $9.99 / month or $99.99 / year | $24.99 / month or $249.99 / year |
| PCs / phones | 1 / 2 | 5 / 5 | 5 / 5 |
| Screen view ("Coming") | 10 minutes per 5-hour window | unlimited | unlimited |
| AI butler | one-time taste of 10 requests | 30 per 5-hour window, up to 120 per week | 90 per 5-hour window, up to 300 per week |
| Top-up | — | 100 AI requests for $2.99, only when bought | same |

- The module holds the figures, the plain-words allowance rules (`AI_RULES`: what one request is,
  everyday commands never count, the windows, the taste, top-ups, privacy, billing) and the status
  constants `AVAILABLE_NOW`, `COMING_AT_PAID_LAUNCH`, `SCREEN_VIEW_STATUS` ("Coming") and
  `POINT_AND_TALK_STATUS` ("Planned").
- Readers: the pricing page and its comparison table (`lib/pricing.ts → pricingGroups()`), the FAQ, the
  home page's plan teaser, the film's scene 5 and `/demo`, the in-app Billing page and `ProExplanation`.

## Billing (`src/pages/app/BillingPage.tsx`, `src/lib/billing.ts`, `src/lib/api.ts`; docs/BILLING.md; DECISIONS 64–65)

Protocol 1.3 (#51). Everything about the customer's own plan comes from the server, never from a URL:
`GET /v1/account/billing` (`billing_enabled`, `billing_mode`, plan, entitlement state, the subscription row,
what can be bought, the top-up, an open downgrade selection) and `GET /v1/session` (plan and limits).
States of the page, each component-tested (`test/components/BillingPage.test.tsx`):

- **Billing off** (`billing_enabled: false`, production today, or an older server without the endpoint):
  the plan card, the limits, "Upgrading is not available yet" with the website's prices, a link to
  `/pricing`, no buttons.
- **Test mode** (`billing_mode: "test"`): a "Test mode: no real charges" pill on the plan card and above
  every purchase button.
- **Free with checkout**: Pro and Pro Plus cards with the server's prices, a monthly/yearly switch, the AI
  line (+ "Not switched on yet" while `GET /v1/assistant` says `not_configured`), one "Continue to payment"
  each → `POST /v1/billing/checkout {plan, interval}` → the browser leaves for the server's `checkout_url`
  (https only; `browserNavigation.assign`, replaceable in tests). Errors show the contract's message for the
  code (`BILLING_NOT_AVAILABLE`, `ALREADY_SUBSCRIBED` re-reads the status, `RATE_LIMITED`,
  `BILLING_PROVIDER_ERROR`) and never claim a purchase. `BILLING_PROVIDER_ERROR` follows its `detail.reason`:
  `auth`, `bad_request` and `not_found` say payments aren't working on DoMe's side (try again later) instead of
  "didn't answer", and in test mode one more line names Stripe's reason code (decision 78).
- **Returned from Stripe** (`?checkout=return|cancel`, `?topup=return|cancel`, informational only; dropped
  from the URL at once): "Pending activation" / "Top-up pending" while the page polls the session and the
  billing status (or the allowance) every 3 s for at most 2 minutes, stops when the server says
  `active`/`past_due` (or the top-up balance grew), then says "Still confirming…" without unlocking anything.
- **Paid**: the subscription state's copy (`billingStateCopy`: active, cancel scheduled with the end date,
  past due with the grace date, pending, unpaid, canceled), "Renews / Ends {date} · billed monthly/yearly",
  "Manage subscription" → `POST /v1/billing/portal` → `portal_url` (plan changes, interval, card and
  cancellation live in Stripe's portal; `BILLING_NO_CUSTOMER` renders its message); the **AI requests** card
  with `allowanceSummary`, progress bars for the 5-hour window and the week (amber from 80 %, red when
  full) and the refresh time; "Add 100 AI requests for $2.99" only when the server's `topup.allowed`
  (`POST /v1/billing/topup`), with "You choose when; nothing is added or charged automatically."; a kept
  balance on Free is explained as usable again on a paid plan.
- **Downgrade selection** (`selection.open`): checkbox lists of PCs and phones from
  `GET /v1/account/plan-selection`, capped at the plan's limits, "Save" → `PUT`; `PLAN_SELECTION_CLOSED`
  links to Devices, `PLAN_SELECTION_INVALID` shows its message; after the window one line says the default
  (most recently used) applies.

`lib/billing.ts` is pure and unit-tested against the shared fixture `fixtures/ai-window-cases.json`
(`test/billing.test.ts`): `refreshText` ("Refreshes today at 2 pm" / "tomorrow at 9 am" / "Saturday at
2 pm" / "27 October at 2 pm" / "Does not renew", in the viewer's zone), `bucketLevel` (warn at ≥ 80 %),
`allowanceSummary` (Settings and Billing line), `limitExplanation` (taste / window / week / cost cap /
failures, with the error's own `resets_at`), `warningText` (the Ask page's 80 % banner), `requestsLeft`
(the Ask chip), `billingStateCopy`, `billingErrorText`. The Ask page tags a turn paid from a top-up
("used a top-up", `charged_from`), and the butler's voice says when a limit refreshes the way the screen
does. No wording says "month" any more. Evidence: unit-tested and component-tested in jsdom against the
contract's validators; not yet exercised against a Stripe-backed server (the founder's test-mode run,
docs/LAUNCH_SETUP.md "Stripe").
  One module means they cannot disagree.
- Device counts and routine limits are read from the contract (`PLANS` from `@dome/protocol`, i.e.
  `shared/protocol/plans.json`), which the server enforces. The contract has no Pro Plus plan yet, so Pro
  Plus shows Pro's counts.
- Nothing here meters, sells or unlocks anything. Until the metering task (#51) lands, the server applies
  `plans.json` (a monthly AI allowance, 0 in the committed file; `pricing_defaults` still the old $5.99 /
  $49.99 experiment), and Settings words Free's allowance as "N of 10 AI requests to try used" (no
  renewal promised) while paid plans keep the monthly wording (`allowanceLine` in `lib/assistant.ts`).
- `test/public/plans.test.ts` pins the figures; the PricingPage, FaqPage, BillingPage, UpgradeAndHealth and
  film tests check that every surface shows them with their labels.

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
                 AssistantConsentSheet, VoiceNoticeSheet; film/ (the 66 s demo film: Film, Stage, Pc, Phone,
                 timeline, script, layout, useFilmClock, filmIcons — every frame a pure function of the clock)
  dev/           preview.ts — the development-only design preview (never in a production bundle)
  styles.css     the design system: colour roles for light and dark, surfaces, shadows, radii, type, motion
  styles/        public.css (website pages, every rule scoped to .dome-public), film.css (the demo film)
  pages/app/     Dashboard, Remote, Touchpad, Command, Assistant (Ask), Apps, Health, Routines (Pro preview),
                 Layouts (Pro preview), Devices, Pair, Settings, Billing, Link, More
  pages/public/  Landing, Demo (the film with scene/time/layout/export flags), Pricing, FAQ, Download, Support
                 (+ ticket form), ReleaseNotes, Privacy (DRAFT), Terms (DRAFT), NotFound, publicIcons
  content/       release-notes.json (public release history + known issues), plans.ts (Free / Pro / Pro Plus
                 content: prices, allowances, status labels — read by the pricing page, the FAQ, the in-app
                 Billing page and ProExplanation so they never disagree), faq.tsx, nav.ts
test/            vitest (jsdom, fake-indexeddb, fake sockets/timers); test/components for app UI, test/public
                 for the website, test/film for the demo film,
                 test/helpers/harness.ts (fake socket + Runtime factory shared by component tests)
scripts/         gen-validators.ts, make-icons.mjs, check-bundle.ts (no key material, no design preview in dist/),
                 export-film.mjs (the demo film as 1080p + vertical MP4/WebM and a poster, Playwright + ffmpeg;
                 output goes to a temp folder or --out, never committed)
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

### Screen view (beta), protocol 1.4 — `/app/screen` (docs/design/screen-view.md §2.2, §4, §8, §16; DECISIONS 66–74)

Built and **unit-/component-tested in jsdom only, with a fake `RTCPeerConnection`; not yet verified on a
device** (KNOWN_ISSUES 28). Behind the operator switch: the app reads `screen_view` from the public
`GET /healthz` once per load (`lib/api.ts::screenViewSwitch`; CONTRACT_ISSUES 12). Until the server says
`"on"`, the Remote page keeps the "Screen view: Coming" card and nothing else exists; with it on, the
Remote page's tile and a Home card (`ScreenViewTile`) name the one next state ("Allow Screen view on your
PC first", "Pair again so your phone can check it is your PC", "Update DoMe on your PC", "Ready · 7 min
left in this window", "Free Screen view refreshes today at 4:12 pm") and open the page.

- **PC key pinning** (`PairPage`, `lib/pcKeyPins.ts`; DECISIONS 67): the pairing code is held in a
  `HeldPairingCode` from the claim until the status poll returns `approved` with `pc_key`; then
  `verifyPcKeyMac` (WebCrypto HMAC verify, constant time) checks the PC's MAC over its identity key and
  `{pc_id, pc_kid, pc_public_jwk, pinned_at}` is written to IndexedDB next to the controller key; then the
  code is dropped. It is also dropped on declined, expired, 404, Cancel and unmount. A MAC that does not
  verify pins nothing (and removes an earlier pin for that PC) and says so; an older PC that sends no key
  pins nothing. "Forget this installation" deletes the pins with the key; sign-out keeps both.
- **The session** (`lib/screen.ts` `ScreenSessionClient`, owned by the runtime): no pin → nothing is sent
  (SCREEN_PC_UNVERIFIED, "pair again"); `screen_ice_request` on the bound socket → ICE servers, the
  allowance and whether a relay server is available; a receive-only offer (video + audio transceivers,
  the `dome-screen-v1` data channel, H.264/VP8/Opus preferences), vanilla ICE (gathering complete or
  3 s), the strict SDP check; the offer inside the signed `screen.session_start` (60 s lifetime,
  stamped 1.4). The PC's `screen_session_result` and its `screen_answer` frame arrive in either order;
  the answer is applied — exactly the signed SDP — only after `verifyScreenAnswer` passed against the
  pinned key for this account, controller, PC, command and offer. A different key is the hard stop
  "This PC's identity changed. Pair again."; every other failure is "Couldn't confirm this is your PC.
  Nothing was shown." There is no "continue anyway".
- **Live**: a full-viewport stage with a thin strip (PC name · "Encrypted between your PC and this phone",
  shown only after the answer verified · Free time left · Touchpad · Keyboard · Stop) and badges for
  "Via relay server", "Data saver" and "Basic picture mode". `hb` on the data channel every 2 s; the
  PC's `geometry` / `state` / `allowance` / `restricted` messages are parsed with the closed vocabulary
  and anything else is dropped. No frame for 2 s while the PC says `sending` asks for a keyframe; 5 s →
  one fresh signed session, at most twice, then "Couldn't connect". A screen wake lock is held while a
  session runs. The picture is a `<video playsinline muted>` fed by `srcObject` only; a refused `play()`
  shows "Tap to show the picture." (`components/ScreenSurface.tsx`).
- **Taps** (`lib/screenMap.ts`, `lib/directGestures.ts`, `lib/screenInput.ts`): tap → `pointer_move_to`
  (0..65535 over the streamed surface, at the geometry generation the phone saw) + click, sent at once;
  a second tap within 500 ms / 12 px reuses the first position; two-finger tap → right click; press and
  hold 350 ms → a 2.5× loupe (`ScreenLoupe`, a second `<video>` on the same stream) whose aim moves at
  1/2.5 of the finger and clicks on lift; two fingers → pinch zoom (local only, 1–4×) or scroll
  (`pointer_wheel`, 120 = one notch, the touchpad's direction preference); one finger pans while zoomed.
  Taps in the letterbox bars and on DoMe's own "Screen view on" bars are dropped, never clamped; after a
  `<video>` resize taps wait (≤ 300 ms) for the matching geometry. **Touchpad** swaps in the relative
  touchpad over the picture (drags, with a Drag toggle). Batches with the new events are stamped 1.4
  by the shared library; touchpad batches stay 1.1. The input session starts once the picture is live
  (only with `pointer` or `keyboard` granted) and ends with it; a view-only grant shows "View only. Allow
  the touchpad on your PC to tap." and sends nothing.
- **Stopping**: Stop sends `bye` and the signed `screen.session_stop` and shows "Stopped · 3 min 12 s";
  leaving the page (Phase 1 has no mini view), a PC switch and sign-out stop it the same way. The app
  going to the background sends `bye` and stops (no minutes count while away); back in front with the
  page open, one fresh session starts by itself — or a **Resume** button when the PC asked someone to
  allow the last one — never after the PC stopped it, a refusal, a used-up allowance or a removed grant.
- **Every §16 state** is one row of `lib/screenStatus.ts` (copy, one next action, the error code for
  the help links), from "Allow Screen view on your PC" to "Windows is asking for permission on the PC";
  a pause names its cause (a Windows prompt, or "A screen on the PC changed" while a bar is missing) and the
  phone keeps the PC's pause causes as a set, so the picture comes back only when none is left; a PC whose
  DoMe build has no Screen view says "This version of DoMe on your PC doesn't include Screen view yet";
  the Free allowance bar and "Refreshes today at 4:12 pm" reuse `lib/billing.ts`. Nothing about a
  session is stored; the SDP, candidates, ICE servers, credentials and the answer envelope never reach
  the log (`log.ts` drops those keys) or diagnostics (phase, path, codes and seconds only).

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
  connectivity claim (dismissal is remembered). It and the Billing page take the paid plans' prices and
  "coming at paid launch" from `content/plans.ts`; nothing can be bought or charged from the app.
- **Brand**: `pnpm gen:icons` installs the brand icon (`brand/icon.svg`) renders into `public/icons`
  (192/512 + maskable, apple-touch-icon, favicon SVG/PNG/ICO); manifest and `index.html` updated.
- **PWA / theme / accessibility**: `autoUpdate` worker precaching the shell only; 44 px targets; safe
  areas; reduced motion. Theme ("Midnight Studio", DECISIONS 55): follows the device by default; the
  Light / Auto / Dark switch (website header, menu and footer; More in the app) is remembered in
  `localStorage` (`dome.theme`) and applied before the first paint by `public/theme-init.js` (see
  "Design system and themes" above).

## Tests

`pnpm check` — **84 files / 1431 tests passing, 1 skipped** (2026-10-22, Screen view beta after the review
round; typecheck, lint, build and the bundle check clean).

Screen view beta (protocol 1.4, WP1.7): `screen` (the client over
the real runtime with `test/helpers/fakePeer.ts` and `fakeTimers.ts`: no pin → nothing sent; the ICE request,
the receive-only offer, the signed 60 s start stamped 1.4; the answer verified against the pinned key and
applied verbatim, every §4.3 refusal shows nothing; heartbeats, the closed data-channel vocabulary, pauses,
stall → keyframe → at most two fresh sessions, the connect timeout and relay hint, `ended` reasons, hidden →
`bye` + stop + resume, Stop, PC switch and sign-out, the lost socket, no SDP or credential in logs or
diagnostics), `screenStatus` (the §16 table as copy), `screenMap` (letterbox, zoom/pan inverse, reserved
bars, generations), `directGestures` (tap, double tap position, two-finger tap, loupe, pinch vs scroll,
pan), `screenInput` (events per gesture, drops never clamped, the slowed loupe aim, the 1.4 stamp),
`pcKeyPins` (valid MAC pins; wrong MAC, wrong kid, missing key or lost code leave it unpinned; the
cross-language vector; forget-installation), `wakeLock`, `no-pixel-access` (no pixel or media API under
`src/` but the camera QR scanner; none at all in the Screen view files), `protocol-parity` (the new
validators and the two facade methods), and components `PairPage` (the code is held through `waiting`
and gone after done, declined, expired, Cancel and unmount; valid MAC pins, invalid MAC or an old PC
pins nothing), `ScreenPage` (nothing on arrival; the whole signed path to a live picture; tap → 1.4
batch, pinch → nothing, two-finger slide → `pointer_wheel`, the loupe, Touchpad mode; Stop, leaving,
background and return; view-only; `play()` refused; wake lock; every §16 state with its copy) and
`ScreenViewTile` (the switch, "Coming" kept while off, every entry line).

`pnpm test` — **68 files / 1218 tests, all passing** (2026-10-13, at commit 6d76945; `pnpm typecheck`,
`pnpm lint` and `pnpm build` with its bundle check also clean).

Design A rollout, film and plan content (2026-10-12/13): `test/public/` — `plans` (every figure, the
contract-derived device counts, the status constants, the rules' wording), `PricingPage` (three cards,
"Available now" chip, paid plans as status lines and never buttons, the grouped comparison, "Coming" on
Screen view), `FaqPage`, `PublicLayout` (nav, skip link, menu, theme switch, page titles), `pages` (the
other public pages and their caveats), `domain` (canonical origin, robots, sitemap = the router's public
routes, preview image not precached) and `heroLayout.browser` (the hero's toast never covers a call to
action or the caveat at 1024–1920 px, measured in headless Chromium; skipped where Playwright is not
installed). `test/film/` — `timeline` (scene boundaries, stills, chips and their labels, plan facts from
`plans.ts`; no recording, screenshot or AI-vision claim in the film's words), `Film` (controls, keyboard,
captions silent until played, the end card's real links, the storyboard under reduced motion, export
mode, the vertical layout), `DemoPage` (query flags, export mode) and `LandingPage` (the film right after
the hero, Screen view listed as Coming, the plan teaser). Autoplay-in-view is not covered: jsdom has no
IntersectionObserver, so it was only looked at in a browser. Also `components/BillingPage` (every state of the
Billing page: off, test mode, the four purchase paths and the redirect, the return from Stripe that polls and
never unlocks by itself, the two-minute bound, paid copy and the portal, the AI card and the top-up, the
downgrade selection and its errors), `billing` (the shared fixture's refresh wording in New York, the 80 %
level, the lines, the state copy, the error text), `previewRoutes` (every preview route exists, every
`/app` page is covered, the offline PC answers PC_OFFLINE) and `styles-bars` (bar fills ≥ 0.9 in both
themes, the no-`backdrop-filter` fallback, tinted icon wells).

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

At protocol 1.2 and the Assistant (2026-10-11) the suite was 51 files / 1116 tests (jsdom,
fake-indexeddb, fake sockets/timers; no network). New in 1.2: `commands`/`confirmations` (each action stamped with its `since`, verified by the
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
| Redesign and film on iPhone | Safari and the Home Screen app, Light, Auto and Dark: no flash of the wrong theme on launch; Home, Remote, Ask, Touchpad fit above the tab bar and dock with nothing showing through them; website home, `/demo` and `/pricing` have no sideways scroll at 375–430 px; the film starts only once scrolled into view, pauses when the tab is hidden and restacks to the vertical layout; with Reduce Motion on, the storyboard of stills. |

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

### Manual iPhone checklist — Screen view beta (docs/design/screen-view.md §18 items 2, 3, 5, 9–11)

Not run on a device: everything in Screen view is **not yet verified on a device** (KNOWN_ISSUES 28). Safari
tab **and** Home Screen app, iOS 18.x and 26.x, a Windows PC with `screen` (and `pointer`) allowed in the DoMe
window on the PC; record the iOS version and mode for each row:

| Area | Step |
| --- | --- |
| Pinning | Re-pair the phone once after the 1.4 update (Done shows no warning); Screen view opens. Re-link the PC with `--new-key`: "This PC's identity changed. Pair again." and no picture. A pairing made in a Safari tab does not pin the Home Screen app (separate storage): re-pair from the app. |
| First picture | Show screen → first frame within 4 s (Phase 0 gate); "Encrypted between your PC and this phone" appears with it; Low Power Mode → "Tap to show the picture." then the picture. |
| Paths | Same Wi-Fi (direct), cellular (relayed: "Via relay server"), hotel-style Wi-Fi; the copy while connecting switches to "Trying the relay server…" after 5 s. |
| Taps | At 1×, 2× and 4× and with the loupe on a 1080p and a 1440p desktop: the click lands where the crosshair or finger was; taps in the black bars and on the "Screen view on" bar do nothing; a double tap opens a desktop icon. |
| Scroll / zoom | Two-finger scroll in a browser page (vertical and horizontal — check the horizontal direction feels right, KNOWN_ISSUES 35); pinch never scrolls; one finger pans while zoomed. |
| Touchpad mode | Drag a window with Touchpad → Drag; the batches are the touchpad's own. |
| Lifecycle | Background 5 s and 30 s, lock the phone, an incoming call: the PC ends within 8 s and the Free clock stops; back in front a fresh session starts by itself (or Resume after an ask-each-time start). Stop shows "Stopped · …". Leaving the page stops it. |
| Frozen picture | Switch apps and come back: a black or frozen picture asks for a keyframe and recovers, or restarts once. |
| Landscape | Rotate: the picture fills the screen in landscape (manifest `orientation: "any"`); safe areas respected. |
| Wake lock | The phone does not dim while watching (Home Screen app from iOS 18.4). |
| States on the PC | UAC prompt → "Windows is asking for permission on the PC…"; Win+L → "Your PC is locked…"; Stop on the PC's bar → "Screen view was stopped on the PC…" and no automatic restart; an elevated window → "That window runs as administrator…". |
| Free minutes | The bar counts down while live; at 0 the PC ends it: "You've used Free Screen view for now. Refreshes … ." |
| Battery and heat | 30 minutes of viewing: note battery used and whether the phone gets hot. |
