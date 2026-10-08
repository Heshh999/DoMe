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
