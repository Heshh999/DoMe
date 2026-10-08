# browser-extension — DoMe for YouTube

Chrome/Edge Manifest V3 extension that lets the DoMe agent on the same PC control YouTube playback in
this browser: a background service worker talks to the agent over Native Messaging
(`com.dome.agent` → `dome-native-host` → agent IPC), and a content script drives the player in each
`https://www.youtube.com/*` tab through the `<video>` element and YouTube's own buttons. It has no
network access of its own, no remote code, no page UI, and only `nativeMessaging`, `storage`,
`alarms` plus the single YouTube host permission. Spec: `docs/spec/MASTER_PROMPT.md` §9 and §15;
design: `docs/design/browser-extension.md`; decisions: `DECISIONS.md`; contract notes:
`CONTRACT_ISSUES.md`.

```
public/manifest.json          exactly the designed manifest (+ alarms, + explicit CSP)
src/background/               service worker: connection.ts (native port, hello/ack, backoff via alarms),
                              tabs.ts (attachment registry, probing, youtube_tab composition),
                              requests.ts (bridge_request → op routing, timeouts, error codes),
                              service.ts (composition root, events, popup messages), storage.ts, frames.ts
src/content/                  content script: detect.ts (DOM reads), adapter.ts (ops + success criteria),
                              wait.ts (event-driven waits, no timer chains), emitter.ts (player_state ≤ 2/s)
src/popup/                    status popup (connection state, ids, profile label, retry)
src/shared/                   messages, sanitize, validate (precompiled schema validators), throttle, log
src/generated/validators.js   generated from shared/protocol/schemas by scripts/gen-validators.ts (no eval)
fixtures/                     hand-written YouTube-like DOM fixtures + tab fixture (see fixtures/README.md)
test/                         Vitest: chrome API stub, jsdom player tests, worker tests, contract parity
```

## Build

Prerequisites: Node 22, pnpm 10, and `pnpm install` once in `../shared/ts` (the linked `@dome/protocol`
keeps its own `node_modules`).

```sh
cd browser-extension
pnpm install            # standalone package; @dome/protocol is linked from ../shared/ts
pnpm check              # typecheck + lint + tests + build
pnpm build              # → dist/{manifest.json,background.js,content.js,popup.html,popup.js,popup.css}
pnpm gen:validators     # only after shared/protocol/schemas changed (test/generated.test.ts enforces it)
```

Last run here (Linux, Node 22.22): `tsc` clean for `src` and `test`, `eslint` clean, **79 tests passed
in 7 files**, build produced the six `dist/` files (`background.js` 314 KB unminified, `content.js`
23 KB classic script). A smoke run of the built `dist/background.js` under a minimal `chrome` stub
with `eval` and `Function` disabled sent a valid `bridge_hello`, answered a `set_paused` request and
rejected a malformed request — the bundle works under the MV3 CSP.

## Load unpacked (development)

1. `pnpm build`.
2. Chrome: `chrome://extensions` → enable **Developer mode** → **Load unpacked** → select
   `browser-extension/dist`. Edge: `edge://extensions` → **Developer mode** → **Load unpacked**.
3. The card shows the extension **ID** (32 letters `a–p`). Unpacked IDs are derived from the folder
   path, so keep `dist/` where it is (moving the checkout changes the ID). For a stable development
   ID add a `"key"` to `public/manifest.json` (a base64 RSA public key; Chrome derives the ID from it)
   — production will pin the Web Store key the same way; the source manifest deliberately has none.
4. Register the ID with the agent on the PC (per user, no elevation):
   ```
   set DOME_AGENT_DEV_EXTENSION_ID=<the 32-letter id>
   dome-agent install-native-host        (or DoMe.exe install-native-host)
   ```
   This writes the `com.dome.agent` native-messaging manifest with
   `allowed_origins = [chrome-extension://<id>/]` under `HKCU\Software\Google\Chrome\NativeMessagingHosts`
   and the Edge equivalent (`pc-agent/README.md`). Production builds carry the store ID in
   `pc-agent/dome_agent/bridge/manifest.py::PRODUCTION_EXTENSION_IDS`; development IDs are never
   baked in.
5. Start the agent (tray icon), then click the DoMe toolbar icon. The popup must say **Connected to
   the DoMe agent** and show the `Browser id` the phone's tab picker will display. If it says
   *not registered* (`host_forbidden`), the ID in step 4 does not match; *not installed*
   (`host_missing`) means step 4 was not run for this browser; *agent is not running* means the
   native host started but found no agent IPC endpoint.
6. Open a YouTube watch page (or reload existing ones — tabs opened before the extension was
   installed have no content script and are listed as *reload needed*).

## How it works (short)

- **Identity.** `browser_instance_id` (22-char random, created once, `chrome.storage.local`)
  identifies this browser profile in every frame. Each content-script attachment generates a
  `tab_token`; every tab op must carry the current token or fails with `TARGET_CHANGED`, so a reused
  Chrome tab id after a restart or reload can never be re-targeted.
- **Connection.** `connectNative("com.dome.agent")` → `bridge_hello` → `bridge_hello_ack`
  (versions negotiated both ways; `PROTOCOL_INCOMPATIBLE` carries both lists). While the port is
  open, Chrome ≥ 116 keeps the worker alive. On disconnect: backoff 1→60 s with `setTimeout` plus a
  `chrome.alarms` safety net that survives worker termination; `connect()` is idempotent.
- **Requests.** `list_tabs` probes every YouTube tab for fresh state. Tab ops: `tabs.get` →
  `TARGET_GONE`; not on YouTube → `TARGET_GONE`; discarded or no content script →
  `TAB_NOT_CONTROLLABLE`; otherwise the op goes to the content script with a deadline shorter than
  the agent's `timeout_ms`. `next`/`previous` succeed only when a new `video_id` is observed and
  return `previous_video_id`; a click without an observed transition is `OUTCOME_UNKNOWN` (never
  retried by the extension). Ads: only pause/mute/volume; Shorts: no next/previous/theater; live: no
  seeking; fullscreen without a user gesture: `ACTIVATION_REQUIRED`.
- **Events.** `tabs_changed` (full list, debounced 300 ms) on open/close/navigation/attach/detach;
  `player_state` at most 2/s per tab.
- **Validation.** Every inbound frame is validated against `bridge.schema.json#agent_to_extension`
  (bad frames get `bridge_error{MALFORMED_MESSAGE}`), every outbound frame against
  `extension_to_agent` plus the 64 KiB limit, every composed `youtube_tab` against the relay schema.
  Page-derived strings are sanitized (control characters, length, video-id alphabet) first.
  Validators are precompiled from the frozen schemas because Ajv's runtime compiler needs `eval`
  (see `CONTRACT_ISSUES.md` #1).
- **Logging.** Codes, ids, counts, durations only. Never titles, tokens, URLs or frames.

## What is verified here and what is not

Evidence tags follow spec §17.

| Area | Status |
| --- | --- |
| Manifest permissions/CSP exactly as designed | **unit-tested** (`test/manifest.test.ts`) |
| Every emitted frame type validates against the frozen contract; generated validators agree with `@dome/protocol` on good and bad frames; `protocolCompatible` parity | **unit-tested** (`test/frames.test.ts`) |
| Worker: storage-backed id survives restart, hello, ack, request routing, `TARGET_GONE`/`TAB_NOT_CONTROLLABLE`/`TARGET_CHANGED`/`OUTCOME_UNKNOWN`/`INVALID_PARAMETERS`, timeouts, malformed frames, backoff + alarms, host missing/forbidden/not running, incompatible agent, event debounce/throttle, sender checks, sanitization, popup messages | **unit-tested** with a chrome API stub (`test/background.test.ts`) |
| Player adapter: every op's success path and failure code on the six DOM fixtures (watch, ad, live, shorts, no-next, no player), token/expected_video_id guards, transition observation, timeouts | **unit-tested** in jsdom against a scriptable `<video>` (`test/player-adapter.test.ts`) |
| Content entry: attach message, probe, op routing, sender checks, ≤ 2/s emission, SPA re-acquire, detach | **unit-tested** (`test/content-entry.test.ts`) |
| Built bundle runs with `eval`/`new Function` disabled | **smoke-tested** (Node, built `dist/background.js`) |
| Real Chrome/Edge: Native Messaging handshake with `dome-native-host`, real YouTube DOM selectors, background-tab behaviour, worker termination, fullscreen activation rules, Edge brand detection | **not yet verified** — no browser session, Windows agent or native host here. The selectors come from YouTube's current DOM (`video.html5-main-video`, `.ytp-next-button`, `ytd-watch-flexy[theater]`, …); the fixtures mirror them but are not copies of youtube.com. |

## Manual verification checklist (real PC, real browser)

Record results in `docs/ACCEPTANCE.md` with the tag *Windows-device-tested*.

1. **Handshake.** Agent running → load unpacked → popup shows *Connected*; agent status shows
   `extension: True`; `youtube.list_tabs` from the phone lists this browser's YouTube tabs with the
   popup's `Browser id`. Quit the agent → popup turns to *agent is not running* within a minute and
   reconnects by itself (≤ 60 s) after the agent restarts. Wrong `DOME_AGENT_DEV_EXTENSION_ID` →
   *not registered*.
2. **Next while the browser is in the background.** Play a video, focus another application
   (Explorer, a game), send Next from the phone: the tab advances, the result shows
   `previous_video_id` and the new title; audio continues. Repeat with the browser window minimized.
3. **Multiple tabs.** Two watch pages playing: the phone must be asked to choose
   (`TARGET_AMBIGUOUS` from the agent); pick each tab and confirm Pause/Play affect only that tab.
   Close the selected tab → the next command fails with `TARGET_GONE`, nothing else changes.
4. **Ad / live / Shorts.** During a pre-roll ad: Next → *not available for the current page*
   (`UNSUPPORTED_CONTEXT`), Pause and Mute still work, the ad is never skipped. Live stream: seek →
   `UNSUPPORTED_CONTEXT`, `is_live` shown, pause works. Shorts: Next/Previous →
   `UNSUPPORTED_CONTEXT`, pause/mute/volume work, `context: shorts` shown.
5. **Playlist end and Previous.** Last video of a playlist: Next → `NO_NEXT_VIDEO`; Previous
   works and reports the transition. On a plain watch page (no playlist): Previous →
   `NO_PREVIOUS_VIDEO`.
6. **Seek / volume / mute / theater.** ±10 s seek lands within 1.5 s of target; `seek_to` beyond the
   duration clamps to the end; volume 35 shows 35 in the player state; mute via the phone updates
   YouTube's speaker icon (button path) and the slider position after a direct volume change is
   noted (known cosmetic gap, DECISIONS D11); theater on/off toggles the layout and reads back.
7. **Fullscreen.** From the phone → `ACTIVATION_REQUIRED` with the explanation; pressing `f` on the PC
   then `get_state` shows `fullscreen: true`.
8. **Worker termination.** Keep the agent stopped so no port is open; wait > 30 s (or use
   `chrome://serviceworker-internals` → Stop); start the agent → the alarm reconnects without any
   click; `list_tabs` still returns correct `tab_token`s (cache rebuilt by probing).
   `browser_instance_id` in the popup is unchanged after a browser restart.
9. **Tab reload / navigation.** Reload a YouTube tab: `tabs_changed` shows it first with
   *reload needed* then attached with a **new** `tab_token`; a command still holding the old token
   fails with `TARGET_CHANGED`. Navigate the tab to a non-YouTube site → it disappears from the list;
   navigate within YouTube via the site's own links → the same token stays valid and
   `player_state` updates follow.
10. **Expected video guard.** Start Next from the phone while clicking a different video on the PC
    at the same moment → either the observed transition is reported, or `TARGET_CHANGED` — never a
    silent double skip.
11. **Two profiles / Edge.** A second Chrome profile and an Edge profile each show their own
    `Browser id` and `browser` kind; the agent lists two `browser_instances`.
12. **Hygiene.** `chrome://extensions` → *Errors* stays empty; the service-worker console shows no
    titles or tokens; `dist/` contains no remote URLs other than none (the extension makes no
    requests).

## Store publishing (founder action)

Unpacked loading is a development path only. Publishing requires a Chrome Web Store developer
account, the listing's key pinned in `public/manifest.json` (`"key"`), that ID added to
`PRODUCTION_EXTENSION_IDS` in the agent, and the same for the Edge Add-ons store.
