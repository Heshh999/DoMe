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
