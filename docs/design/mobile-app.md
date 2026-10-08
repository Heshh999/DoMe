# mobile-app design

React 19 + TypeScript 5.9 + Vite + Tailwind CSS 4 + React Router, `zustand` for state, `idb` for
IndexedDB, `vite-plugin-pwa` for the installable shell, Vitest + Testing Library. Depends on
`@dome/protocol` (`link:../shared/ts`). Served from the API origin in production; in development
Vite proxies `/v1` and `/ws` to `http://127.0.0.1:8000`.

## Controller identity

`src/lib/controllerKey.ts`: `getOrCreateKeyPair()` stores the non-extractable `CryptoKeyPair`
from `generateControllerKeyPair()` in IndexedDB (`dome` → `keys` → `controller`). The public JWK
and `kid` are derived on demand. If IndexedDB is unavailable or the key is gone (storage cleared,
private mode), the UI explains that this installation must pair again — there is no recovery
shortcut. `controller_id` for the account (returned by the first successful pairing) is stored in
IndexedDB next to the key and re-fetched from `GET /v1/controllers` by `kid` when missing.

## Routing

Public (no session): `/` landing, `/pricing`, `/faq`, `/download`, `/support`, `/privacy`, `/terms`.
App (session required, otherwise redirect to `/v1/auth/login?return_to=…`):
`/app` dashboard, `/app/remote`, `/app/command`, `/app/apps`, `/app/routines`, `/app/devices`,
`/app/devices/pair` (also `/pair#code=…` deep link), `/app/settings`, `/app/billing`,
`/link?user_code=…` (approve a PC linking request).

## Data and sockets

- `api.ts`: fetch wrapper; adds `X-DoMe-CSRF` from `/v1/session`; `credentials:"include"`; maps
  error bodies to `ProtocolError`; never caches.
- `relay.ts`: one WebSocket to `/ws/controller`; `hello{kid}` → `hello_ack{controller_id}` →
  `subscribe` to the selected PC(s) (the relay answers with `pc_status` + the cached `state`);
  reconnect with backoff; on `visibilitychange` to visible or `online`, reconnect
  and treat all state as stale until a fresh `pc_status`/`state` arrives. Frames are validated
  with `schemas.validateFrame("relay_to_controller", …)` before use. Media titles are rendered as
  text only.
- `commands.ts`: `send(action, params, target)` → `buildCommandPayload` → `signCommand` →
  `{type:"command", pc_id, envelope}`; tracks lifecycle by `command_id` (`created` → `accepted`
  → `executing` → terminal). "Sent" is shown as *sent*, `accepted` as *PC received*, terminal
  states with their real result. 30 s default lifetime; the UI also times out locally and shows
  "No answer from the PC" without claiming failure.
- `confirmations.ts`: `confirmation_required{challenge_text}` → `schemas.validateChallengeText`
  on the string → modal whose primary line is rendered from `action`/`params`/`target` with the
  app's own registry labels, the PC name from the device inventory matched by `pc_id`, and
  `display.detail` as plain secondary text; countdown to `expires_at`; Approve →
  `buildConfirmationPayload({challengeText})` (the string, verbatim) → `signConfirmation` →
  `{type:"confirmation", pc_id, envelope}`.
- Results are validated with `registry.validateResult(action, result)` before rendering.
- Volume sliders: emit at most one `set_volume` per 250 ms plus the final value on release
  (within `plans.coalescable_command_rate_limit`).

## Deterministic text commands (`src/lib/intents.ts`)

Normalise (lowercase, trim, collapse spaces, strip trailing punctuation), then match an ordered
rule table. Each rule yields `{action, params, needsTarget}` or `{clarify: "…"}`; anything else
→ `{unknown: true}`. Required coverage (with tests): the seven examples in the spec table, "play"
/ "resume", "mute"/"unmute" (ask: YouTube or PC?), "volume up/down" (±10 absolute from last known
value; refuse when unknown), "skip ahead/forward 30 seconds", "next/previous track", "open
<approved app>", "focus/minimise <app>", "close that" → clarify which window, "shut down"/
"restart" → confirmation-required actions (never pre-confirmed), numbers in words 0–100.
Injection cases: text containing "ignore", "instructions", URLs, or anything not matching a rule
is `unknown`; a rule never matches inside a longer sentence that also contains another verb.

## Surfaces (each with explicit empty/offline/error states)

- **Dashboard**: PC switcher (name, truthful connection: online / reconnecting / offline since
  `last_seen`), remote-enabled flag from the PC, active YouTube tab or media session with
  play/pause, system volume slider, quick actions, last command result.
- **Remote**: large play/pause (absolute from known state), previous/next, −10/+10, position
  scrubber when duration known, tab/session picker (shows browser + profile label + title),
  **two** clearly labelled volume controls (YouTube player vs Windows), theater toggle,
  fullscreen button that explains `ACTIVATION_REQUIRED`.
- **Command**: input with dictation hint, parsed intent preview ("Will send: Pause YouTube on
  *Office PC*"), Send, outcome. No AI mention beyond "typed commands work offline from any AI".
- **Apps**: approved apps from `app.list` with launch/focus/minimise/close (close opens the
  confirmation flow).
- **Routines**: Pro preview with example routines rendered as disabled cards and an honest
  "available with DoMe Pro at paid launch" note (no checkout until Phase C).
- **Devices**: PCs (rename, enable/disable, unlink), controllers (this phone highlighted,
  rename, revoke), grants per PC, pairing flow: camera QR scan (`BarcodeDetector` when available, else a `getUserMedia` + jsQR
  fallback) or manual entry of the 4×5-symbol code (normalised with `normalizePairingCode`);
  the app sends `pairingCodeHandle(code)` + its public JWK, computes
  `pairingVerificationCode(code, pairing_id, pc_id, kid)` locally and shows it, then polls
  `GET /v1/pairing/{id}` (state `claimed` while the PC is offline or the user has not approved).
- **Settings/Help**: connection check (`system.ping` round trip), privacy controls, install
  instructions (Add to Home Screen steps for iOS Safari), redacted diagnostics download,
  support link, sign out (clears IndexedDB **except** the key, closes socket).
- **Billing**: current plan and limits from `/v1/session`; Phase C adds checkout/portal. Shows
  "pending activation" semantics only when the backend reports it.
- **Public pages**: product explanation, Free/Pro comparison from `plans.json`, FAQ, download
  (links disabled with "coming soon" until a signed installer exists), privacy and terms drafts.

Rules: every consequential control shows the PC it targets; on stale state controls that could
be consequential are disabled until refreshed; recovery text for each error code comes from
`errors.json` plus page-specific steps.

## PWA shell

`vite-plugin-pwa` with `registerType: "autoUpdate"`, precache of the built assets only,
`navigateFallbackDenylist: [/^\/v1\//, /^\/ws\//, /^\/link/, /^\/pair/]`, runtime caching
disabled for `/v1`. Manifest: name "DoMe", dark theme colour, standalone display, icons.
Offline → a full-screen "You're offline" state, never a connected-looking UI.

## Theme and accessibility

Dark default, light via `prefers-color-scheme`; CSS tokens on `:root`; 44×44 px minimum touch
targets; `env(safe-area-inset-*)` paddings; `prefers-reduced-motion` respected; all icon buttons
labelled; focus-visible rings; contrast ≥ 4.5:1 for text.

## Tests

`intents.test.ts` (full table + rejections + injection), `controllerKey.test.ts` (fake-indexeddb,
non-extractable), `pairing.test.ts` (code normalisation, handle, verification code against the
shared fixtures), `commands.test.ts` (lifecycle state machine with a fake socket),
`confirmations.test.ts` (raw challenge text preserved; digest matches), component tests for
Dashboard offline/online/stale and the confirmation modal; `pnpm build` and `pnpm typecheck` pass.
