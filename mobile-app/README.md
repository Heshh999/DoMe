# mobile-app — DoMe iPhone-first PWA

The customer-facing web app: public website (landing, pricing, FAQ, download, support, privacy/terms
drafts) plus the installable remote (`/app/*`). React 19, TypeScript 5.9, Vite 7, Tailwind CSS 4,
React Router 7, zustand, idb, vite-plugin-pwa, Vitest + Testing Library. Depends on the shared
protocol library `@dome/protocol` (`link:../shared/ts`).

Design: `docs/design/mobile-app.md`. Decisions made here: `DECISIONS.md`. Contract observations:
`CONTRACT_ISSUES.md`.

## Develop

```sh
cd mobile-app
pnpm install                 # standalone package with its own lockfile
pnpm dev                     # http://localhost:5173 — proxies /v1 and /ws to http://127.0.0.1:8000
pnpm check                   # typecheck + lint + test + build
pnpm gen:validators          # regenerate src/protocol/generated/ after a protocol change
```

Run cloud-api locally on port 8000 with `DOME_PUBLIC_ORIGIN=http://localhost:5173` (and
`tools/dev-idp` as the OIDC issuer) so sign-in redirects back to the dev server. In production the
built `dist/` is served by cloud-api from the same origin (`DOME_STATIC_DIR`); there is no CORS setup.

Environment (all optional, see `vite-env.d.ts`): `VITE_DOME_API_ORIGIN` (unusual: API on another
origin), `VITE_DOME_RELEASE_CHANNEL`, `VITE_DOME_SUPPORT_URL` (mailto:/https: shown on Support pages;
unset = honest "not configured" copy).

## Layout

```
src/
  protocol/      no-eval facade over @dome/protocol's registry + generated validators (CSP script-src 'self')
  lib/           api (REST, schema-validated), relay (WebSocket), commands (lifecycle), confirmations,
                 controllerKey (IndexedDB CryptoKey), intents (deterministic text), labels, pairing, qr,
                 targets, throttle, log (redacting), diagnostics, format, outcome, connection, pricing
  store/         zustand: session, devices (REST inventory + selection), live (relay state, commands)
  app/           runtime wiring, AppShell (tabs, banners, confirmation modal), PublicLayout, router, hooks
  components/    ui primitives, Sheet, ConfirmationModal, CommandOutcome, VolumeSlider, PcSwitcher, PcStatus, OfflineScreen
  pages/app/     Dashboard, Remote, Command, Apps, Routines (Pro preview), Devices, Pair, Settings, Billing, Link, More
  pages/public/  Landing, Pricing, FAQ, Download, Support, Privacy (DRAFT), Terms (DRAFT), NotFound
test/            vitest (jsdom, fake-indexeddb, fake sockets/timers); test/components for UI
scripts/         gen-validators.ts, make-icons.mjs
```

## Behaviour summary

- **Identity**: one non-extractable ECDSA P-256 key per installation in IndexedDB; `kid` sent in the
  socket `hello`; the relay's `hello_ack.controller_id` binds the socket. No tokens in URLs or
  localStorage. Sign-out keeps the key; "Forget this installation" deletes it.
- **Pairing code stays on the phone**: the `/pair#code=…` QR deep link keeps the code in the URL
  fragment only. When the phone is not signed in yet, `RequireSession` scrubs the fragment from the
  address bar and sends `return_to=/app/devices/pair?scan_again=1` (no code) to cloud-api; the code is
  not kept in any storage, and the pairing page asks the customer to scan again after sign-in
  (`src/app/navigation.ts`, `loginUrl` also strips any fragment). The backend only ever sees SHA-256
  of the code.
- **Nothing is claimed that is not known**: a REST 401 mid-session signs the user out at once; a power
  request noted by the relay after the PC disconnected reads "requested … DoMe cannot tell whether it
  ran" unless this phone saw an `executing` ack / agent success; a confirmation modal over a dead socket
  says so and can be closed locally; state older than 75 s disables controls even without any new frame.
- **Trust boundary**: every REST body is strict-parsed and validated with `schemas.validateRest`,
  every frame with `schemas.validateFrame`, every result with `registry.validateResult`, every
  challenge with `schemas.validateChallengeText` (raw text preserved and hashed verbatim). Media titles
  and window titles are rendered as text only.
- **Truthful states**: Sent → PC received → Running → Done/Failed/Expired/Cancelled/Outcome unknown.
  "No answer yet" is shown without claiming failure. Stale state disables consequential controls.
- **Confirmations**: modal rendered from the registry (never from PC-supplied labels), PC name from the
  device inventory, PC detail as secondary text, Approve only when bound to the command this phone sent.
- **Text commands**: `src/lib/intents.ts` — ordered rule table, numbers in words, clarifications for
  ambiguous/out-of-range input, injection cases rejected. No AI.
- **Pairing**: QR (BarcodeDetector → jsQR fallback) or typed 4×5 code; only `SHA-256` handle leaves the
  phone; 6-digit verification code computed locally; approval happens on the PC; polling until
  approved/declined/expired.
- **PWA**: `autoUpdate` service worker precaching the built shell only; `/v1`, `/ws`, `/link`,
  `/pair`, `/.well-known` never cached or served from the fallback. Offline shows a disconnected screen.
- **Theme/accessibility**: dark default, light via `prefers-color-scheme`, tokens in `styles.css`,
  44 px targets, safe-area insets, reduced motion, labelled icon buttons, focus rings.

## Tests

`pnpm test` — 13 files / 143 tests: protocol facade parity, intents (spec table + rejections +
injection), controllerKey (fake-indexeddb, non-extractable), pairing (shared fixtures), commands
lifecycle (fake socket/timers), confirmations (raw text + digest + binding), relay client (fake
socket: hello/kid, subscribe, invalid frames, backoff, revoked/unauthenticated/incompatible), api
client (schema validation, CSRF, errors), targets, throttle, log redaction, Dashboard and
ConfirmationModal component tests.

## Not verifiable here (manual steps)

| Area | Step |
| --- | --- |
| iOS Safari key persistence | Install to Home Screen, pair, kill the app, reopen after 24 h and after a device restart: Devices must still show "This phone" without re-pairing. |
| Camera QR scan on iPhone | Devices → Pair → Scan: camera prompt appears once, QR from the PC's tray window is read within ~2 s; denial falls back to typing. |
| Background/resume | Pause YouTube from the remote, background the PWA for 2 minutes, resume: status shows "Online · refreshing" then "Online"; no queued command replays. |
| Real relay path | With cloud-api + pc-agent running: Next changes the video in a background tab; result shows the transition. |
| Confirmation on device | Sleep… → modal shows the registry line and the PC's countdown detail; Approve → "Windows accepted the request". |
| Lighthouse/PWA audit | Installability, contrast ≥ 4.5:1, touch target sizes on a real iPhone. |
