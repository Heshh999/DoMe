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
