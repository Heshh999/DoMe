# mobile-app — known issues

Items from the review that were recorded rather than fully closed, plus residual limitations.

1. **Confirmation after a reconnect.** When the relay socket drops while a confirmation is pending and
   comes back before `expires_at`, the modal clears its "connection lost" state and offers
   Approve/Decline again. Whether the relay accepts a `confirmation` frame for a command that was
   sent on a previous socket (same controller, new connection) is not verified from this component;
   if it refuses, the customer sees the error and can Close. The challenge expires on the PC either
   way (nothing runs unless approved).

2. **Freshness clock granularity.** The 75 s rule is re-evaluated every 5 s (`CLOCK_INTERVAL_MS`), so
   controls can stay enabled for up to 5 s past the threshold. Chosen over a 1 s ticker to avoid
   re-rendering every page every second.

3. **Power-request evidence is per session.** `CommandRecord`s live in memory; after a page reload the
   dashboard can only show the neutral "requested … cannot tell whether it ran" wording even if this
   phone did see the `executing` ack before the reload. `GET /v1/commands` (history) is not consulted
   for this notice.

4. **Sign-in round trip on the pairing deep link requires a re-scan.** By design (DECISIONS.md 8): the
   code is not kept anywhere on the phone while the browser leaves for the identity provider. The PC's
   code is valid for 5 minutes, which is normally enough for sign-in + re-scan; otherwise the customer
   chooses "Pair a phone" on the PC again.

5. **Not verifiable here**: iOS Safari behaviour on resume (suspended WebSocket still reporting
   `readyState === 1`) is handled by the 5 s nudge deadline but was tested only against a fake socket.
   Manual check: open the PWA, lock the phone for > 2 min, unlock — the status pill must go
   "Online · refreshing" → "Online" within ~6 s, not after 75–100 s.

6. **Review follow-ups applied (this round).** Sign-out/Forget race with the sign-in redirect (major)
   fixed with the `signing_out` session state and a reordered `Runtime.signOut`; the dead multi-verb
   guard in `intents.ts`; connection-check gating; confirmation header PC name; voluntary close code
   1000; README counts and two copy claims; component tests for `VolumeSlider`, `PairPage`,
   `AppsPage` → confirmation modal and `GlobalConfirmation`. Nothing from the review remains open.

7. **Pairing polling cadence is fixed at 2 s** (`POLL_MS` in `PairPage.tsx`) and its component tests
   therefore run with real timers (~5 s each). Chosen over a test-only prop so the shipped component
   has no knob a test could leave in a different state.

8. **`signing_out` has no exit other than navigation.** After a manual sign-out the store stays in
   `signing_out` until the page navigates to `/` (which it does right after `Runtime.signOut`
   resolves). If a browser blocked that navigation the customer would see the "Signing out" screen
   until reload; no path through the app sets `signing_out` without the navigation that follows.

9. **Double tap over a slow link.** Two quick taps are two clicks combined by Windows' double-click
   timer (DECISIONS 22). When relay latency jitter spreads them beyond the PC's double-click time, the
   PC sees two single clicks. Workaround: the Double button. Not device-tested.

10. **Live typing pauses on legitimate edits.** Any edit that is not at the end of what this phone sent
    in the current run, or that removes an emoji/combining cluster, switches to Compose and Send with an
    explanation (DECISIONS 25). This is deliberate (never guess at the PC field) but means autocorrect
    that rewrites an earlier word, or moving the caret in the textarea, pauses live mode.

11. **Nothing here is iPhone-tested or Windows-device-tested.** The gesture machine, typing mapper and
    session client are unit/component-tested in jsdom with fake sockets and timers. Real Safari
    pointer-event semantics (pointercancel on system gestures, lost capture on rotation, keyboard
    `beforeinput` ordering with IME/dictation/autocorrect), real relay latency and real Windows
    acceptance are not verified; see README "Manual iPhone checklist".

12. **Touchpad sensitivity is relative only.** Cursor motion is `dx·sensitivity` in desktop pixels;
    there is no acceleration curve and no per-monitor awareness (the agent injects relative motion,
    which Windows applies across the real topology). Mixed-DPI feel is unverified.

13. **Foreground-app context is window-level.** The keyboard panel shows `pc_state.foreground_app`
    (process/title/browser) from the agent's 30 s state frames and from the session start result; a
    window change between frames is detected by the PC (INPUT_TARGET_CHANGED) before the phone shows
    it. Field-level focus is never shown because it is not observable.

14. **Support tickets list is fetched on page load only** (and after a successful submission); there
    is no polling for status changes. Operators' answers appear on the next visit.

15. **Health "Retry" budget is per page visit.** The 3-attempt bound resets when the page is
    re-opened; it exists to stop a spinner loop, not to rate-limit the relay (which has its own limits).

16. **Brand exports are copied, not regenerated, by `pnpm gen:icons`.** If `brand/icon.svg` changes,
    run `node brand/scripts/export.mjs` from the repository root first (it writes `brand/exports/`),
    then `pnpm gen:icons` here; the script refuses exports whose manifest source is not `icon.svg`.

17. **Test helpers wait for the first fake socket.** `connected()` in `runtime.test.ts` and
    `makeRuntime().connect()` in `test/helpers/harness.ts` assumed the socket existed after 10 macrotasks;
    with 30 files in parallel the key generation + hello-proof signing occasionally took longer and two
    unrelated tests failed intermittently. Both helpers now poll (bounded, 50 rounds). No shipped code
    changed for this.

18. **Live-typing pause is conservative.** Any batch rejection or increase in dropped events pauses
    live typing when text was typed in that run, even if the dropped batch held only pointer events,
    because an `error` frame does not name the batch's seq (CONTRACT_ISSUES 8). The customer
    resumes with one tap on "Type live". Text that was already queued but not yet flushed when the
    pause happened is still sent (it is text the customer typed, in order); only the phone's model of
    it is forgotten.

19. **The capture-loss and live-typing certainty fixes are jsdom-tested only.** The implicit
    `lostpointercapture` after `pointerup` and the INPUT_STALE / dropped-ack / suspended-restart
    sequences are reproduced by dispatching events in jsdom; real iPhone Safari ordering and real
    agent rejections are **not yet verified** (README manual checklist rows added).

20. **"Not confirmed" support submissions are not reconciled automatically.** After "Refresh your
    requests" the customer compares the list by eye; the page does not match a new ticket to the
    unconfirmed draft (there is no request id to match on, CONTRACT_ISSUES 11).
