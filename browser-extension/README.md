# browser-extension — DoMe for YouTube

Chrome/Edge Manifest V3 extension that lets the DoMe agent on the same PC control YouTube playback in
this browser: a background service worker talks to the agent over Native Messaging
(`com.dome.agent` → `dome-native-host` → agent IPC), and a content script drives the player in each
`https://www.youtube.com/*` tab through the `<video>` element and YouTube's own buttons. It has no
network access of its own, no remote code, no page UI, and only `nativeMessaging`, `storage`,
`alarms`, `scripting` (to start its own content script in YouTube tabs that were already open) plus
the single YouTube host permission. Spec: `docs/spec/MASTER_PROMPT.md` §9 and §15;
design: `docs/design/browser-extension.md`; decisions: `DECISIONS.md`; contract notes:
`CONTRACT_ISSUES.md`; residual risks: `KNOWN_ISSUES.md`.

```
public/manifest.json          exactly the designed manifest (+ alarms, + scripting, + explicit CSP)
src/background/               service worker: connection.ts (native port, hello/ack, backoff via alarms),
                              tabs.ts (attachment registry, probing, youtube_tab composition,
                              content-script injection into already-open tabs),
                              requests.ts (bridge_request → op routing, timeouts, error codes),
                              service.ts (composition root, events, popup messages), storage.ts, frames.ts
src/content/                  content script: detect.ts (DOM reads), adapter.ts (ops + success criteria),
                              wait.ts (event-driven waits, no timer chains), emitter.ts (player_state ≤ 2/s)
src/popup/                    status popup (connection state, ids, profile label, retry);
                              status.ts holds the plain-language state texts
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

Last run here (Linux, Node 22.22, 2026-10-10): `tsc` clean for `src` and `test`, `eslint` clean,
**104 tests passed in 8 files**, build produced the six `dist/` files (`background.js` 319 KB
unminified, `content.js` 26 KB classic script). An earlier smoke run of the built
`dist/background.js` under a minimal `chrome` stub with `eval` and `Function` disabled sent a valid
`bridge_hello`, answered a `set_paused` request and rejected a malformed request — the bundle works
under the MV3 CSP.

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
5. Start the agent (tray icon), then click the DoMe toolbar icon. The popup must say **Connected**
   and show the `Browser id` the phone's tab picker will display. The agent may also start after the
   browser: for the first 5 minutes the extension retries every few seconds (at most 8 s apart),
   then every 30 s, and finds it by itself, or at once with **Retry connection**. If the popup says *Not connected - DoMe does not know this
   extension* (`host_forbidden`), the ID in step 4 does not match; *Not connected - set up DoMe on
   this PC* (`host_missing`) means step 4 was not run for this browser; *Not connected - start DoMe
   on this PC* means the native host found no agent IPC endpoint, the agent quit, or it did not
   answer the hello. The line under the title (*Details: …*) carries the technical reason.
6. Open a YouTube watch page. YouTube tabs that were already open get the content script when the
   extension is installed, updated, reloaded or enabled again and when the browser starts
   (`chrome.scripting`), so they need no reload; a tab the browser had discarded (asleep) gets it when
   it loads again.

## How it works (short)

- **Identity.** `browser_instance_id` (22-char random, created once, `chrome.storage.local`)
  identifies this browser profile in every frame. Each content-script attachment generates a
  `tab_token`; every tab op must carry the current token or fails with `TARGET_CHANGED`, so a reused
  Chrome tab id after a restart or reload can never be re-targeted.
- **Connection.** `connectNative("com.dome.agent")` → `bridge_hello` (protocol versions `1.0`,
  `1.1`) → `bridge_hello_ack` (versions negotiated both ways; an ack above `1.1` and
  `PROTOCOL_INCOMPATIBLE` carry both lists). While the port is open, Chrome ≥ 116 keeps the worker
  alive. On disconnect: backoff 1, 2, 4, 8, 8 … s (capped so an agent started after the browser is
  found within ~10 s) with `setTimeout` plus a `chrome.alarms` safety net that survives worker
  termination, for 5 minutes (at most 40 attempts); then every 30 s through the alarm alone, so the
  worker can sleep and the PC starts one native host per 30 s instead of per 8 s (KNOWN_ISSUES K5).
  Browser start, install/reload, **Retry connection** and every ack start a new fast window; its
  end is kept in `chrome.storage.session`, so a worker woken by the alarm stays slow.
  `connect()` is idempotent. A `bridge_error` `AGENT_DISCONNECTED` /
  `AGENT_NOT_RUNNING` from the host closes the port at once and retries (the host process can stay
  alive after the agent quit, and an open port would block reconnecting); `PROTOCOL_INCOMPATIBLE`
  closes it and retries every 5 min. A host that opens the port but never acknowledges hello is
  dropped after 10 s and retried with the same backoff. The popup's **Retry connection** drops
  anything not acknowledged and connects at once.
- **Tabs open before install.** On `runtime.onInstalled` (install, update, reload) and
  `runtime.onStartup` the worker runs `content.js` in every open, non-discarded YouTube tab with
  `chrome.scripting.executeScript`. Every worker start also runs it in each YouTube tab whose probe
  found no content script at all (extension disabled and enabled again, crashed renderer), without
  holding up the connection. The content script starts once per page (a marker on its
  isolated-world global), so a tab that also gets the manifest injection keeps one instance and one
  `tab_token`; an instance orphaned by an extension reload is replaced.
- **Requests.** `list_tabs` probes every YouTube tab for fresh state. Tab ops: `tabs.get` →
  `TARGET_GONE`; not on YouTube → `TARGET_GONE`; discarded or no content script →
  `TAB_NOT_CONTROLLABLE`; otherwise the op goes to the content script with a deadline shorter than
  the agent's `timeout_ms` (budget floored to 1 s so the deadline always precedes the background's
  wait). `next`/`previous` succeed only when a new `video_id` is observed and return
  `previous_video_id`; the result snapshot is taken once the new page settled (≤ 1.5 s), otherwise
  `title`/`duration_seconds`/`position_seconds` are omitted rather than describing the previous
  video; a click without an observed transition is `OUTCOME_UNKNOWN` (never retried by the
  extension). The result is composed from the state the content script just reported, so a tab
  event fired by the navigation itself cannot turn an observed success into `TARGET_GONE`. Ads: only pause/mute/volume; Shorts: no next/previous/theater; live: no
  seeking; fullscreen without a user gesture: `ACTIVATION_REQUIRED`.
- **Events.** `tabs_changed` (full list, debounced 300 ms) on open/close/navigation/attach/detach;
  `player_state` at most 2/s per tab. In-site (SPA) navigations keep the attachment and token:
  `tabs.onUpdated(status:"loading")` only triggers a verification probe, and the attachment is
  dropped only when no content script answers any more (full reload, discard, tab closed).
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
| Worker: storage-backed id survives restart, hello, ack (1.0 and 1.1 accepted, 1.2 refused), hello-ack deadline, request routing, `TARGET_GONE`/`TAB_NOT_CONTROLLABLE`/`TARGET_CHANGED`/`OUTCOME_UNKNOWN`/`INVALID_PARAMETERS`, timeouts and the time-budget invariant down to `timeout_ms: 100`, SPA navigation during `next` (no `TARGET_GONE`, no `script_attached:false` flicker), result composed from the recorded reply, startup race (early ack corrected after a slow probe), malformed frames, backoff + alarms (8 s cap for a 5-minute fast window, then the 30 s alarm alone with no timer left; a worker woken in the slow phase stays slow; browser start, Retry and an ack restart the fast window), host missing/forbidden/not running, `AGENT_DISCONNECTED` after the ack (port dropped, reconnect after the backoff), incompatible agent (either side), event debounce/throttle, sender checks, sanitization, popup messages and Retry, content-script injection on install/startup and, on any worker start, into tabs without a script (not awaited) | **unit-tested** with a chrome API stub (`test/background.test.ts`) |
| Player adapter: every op's success path and failure code on the six DOM fixtures (watch, ad, live, shorts, no-next, no player), token/expected_video_id guards, transition observation plus post-transition settle (new title reported; fields omitted when the page never settles; bounded by the deadline), hidden live badge on VOD is not live / DVR live stays live, timeouts | **unit-tested** in jsdom against a scriptable `<video>` (`test/player-adapter.test.ts`) |
| Content entry: attach message, probe, op routing, sender checks, ≤ 2/s emission, SPA re-acquire, detach, start-once guard (second injection, orphan after reload) | **unit-tested** (`test/content-entry.test.ts`) |
| Popup texts: plain title + next step for every connection state (retrying "every few seconds at first and then every 30 seconds"), technical reason as *Details* | **unit-tested** (`test/popup-status.test.ts`) |
| Built bundle runs with `eval`/`new Function` disabled | **smoke-tested** (Node, built `dist/background.js`) |
| Real Chrome/Edge: Native Messaging handshake with `dome-native-host`, real YouTube DOM selectors, background-tab behaviour, worker termination, fullscreen activation rules, Edge brand detection | **not yet verified** — no browser session, Windows agent or native host here. The selectors come from YouTube's current DOM (`video.html5-main-video`, `.ytp-next-button`, `ytd-watch-flexy[theater]`, …); the fixtures mirror them but are not copies of youtube.com. |

## Manual verification checklist (real PC, real browser)

Record results in `docs/ACCEPTANCE.md` with the tag *Windows-device-tested*.

1. **Handshake.** Agent running → load unpacked → popup shows *Connected*; agent status shows
   `extension: True`; `youtube.list_tabs` from the phone lists this browser's YouTube tabs with the
   popup's `Browser id`. Quit the agent with the browser left open → popup shows *Not connected -
   start DoMe on this PC* within a few seconds; start the agent again → *Connected* within ~10 s
   without touching the browser, and at once after **Retry connection**. Load the extension first
   and start the agent 2 minutes later → *Connected* within ~10 s of the agent starting; start it
   more than 5 minutes later → *Connected* within 30 s, or at once after **Retry connection**. Wrong
   `DOME_AGENT_DEV_EXTENSION_ID` → *Not connected - DoMe does not know this extension*. Suspend the
   agent process (e.g. freeze it in a debugger) while the native host is up → the popup must leave
   *Connecting…* within ~10 s, show *Not connected* with *Details: The DoMe agent did not answer the
   extension's hello*, and reconnect on its own once the agent resumes (hello-ack deadline,
   DECISIONS D20).
2. **Next while the browser is in the background.** Play a video, focus another application
   (Explorer, a game), send Next from the phone: the tab advances, the result shows
   `previous_video_id` and the **new** video's title and duration (never the previous video's; if the
   result has no title at all, the settle events in `src/content/adapter.ts` need re-checking against
   the current site). The tab must stay listed as attached with the same `tab_token` through the
   navigation (no *reload needed* flicker). Audio continues. Repeat with the browser window minimized.
3. **Multiple tabs.** Two watch pages playing: the phone must be asked to choose
   (`TARGET_AMBIGUOUS` from the agent); pick each tab and confirm Pause/Play affect only that tab.
   Close the selected tab → the next command fails with `TARGET_GONE`, nothing else changes.
4. **Ad / live / Shorts.** During a pre-roll ad: Next → *not available for the current page*
   (`UNSUPPORTED_CONTEXT`), Pause and Mute still work, the ad is never skipped. **Ordinary VOD page:
   `is_live` must be `false` and ±10 s seek must work** (YouTube keeps a hidden `.ytp-live-badge` in
   every player; see DECISIONS D24). Live stream: seek → `UNSUPPORTED_CONTEXT`, `is_live` shown,
   pause works; a live stream with DVR (finite duration shown) must still report `is_live:true`.
   Shorts: Next/Previous → `UNSUPPORTED_CONTEXT`, pause/mute/volume work, `context: shorts` shown.
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
13. **Tabs open before install.** With a YouTube video open, reload the extension on the
    extensions page (or remove and load it again) → without reloading the tab, the popup's *YouTube
    tabs ready* counts it again within a few seconds and Pause from the phone works. The phone lists
    the tab once, also after a second extension reload. Then turn the extension off and on again on
    the extensions page → the same, without reloading the tab.

## Store publishing (founder action)

Unpacked loading is a development path only. Publishing requires a Chrome Web Store developer
account, the listing's key pinned in `public/manifest.json` (`"key"`), that ID added to
`PRODUCTION_EXTENSION_IDS` in the agent, and the same for the Edge Add-ons store.
