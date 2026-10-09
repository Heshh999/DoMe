# pc-agent known issues (recorded, not fixed)

1. **`AbortSystemShutdownW` window is effectively nil on real Windows.** `WindowsPower` issues
   `InitiateSystemShutdownExW` with `dwTimeout = 0` (the cancellable countdown is the agent's own,
   and `bForceAppsClosed = FALSE` lets Windows prompt about unsaved work). Windows only honours
   `AbortSystemShutdownW` during its own `dwTimeout` grace period, so after the agent's countdown has
   fired a `power.cancel` almost always gets `canceled: false` — which is the honest answer. Giving
   Windows a non-zero grace period would add a second, OS-drawn countdown after DoMe's; that is a
   product decision for the maintainer, not a bug fix. The abort path is implemented and tested with
   the fake adapter so it works unchanged if `dwTimeout` is ever raised.
2. **A capability narrowing does not interrupt a command that is already executing** (only a
   revocation does). The OS/browser call may be under way; interrupting it would produce
   `outcome_unknown` for an action the account merely narrowed. Queued, awaiting-confirmation and
   armed-countdown commands are canceled (DECISIONS.md #16).
3. **Pending local revocations are re-sent only after a `grants_snapshot`** (i.e. on reconnect), not
   on a timer while connected. The initial send happens immediately; the only way it is dropped is a
   socket that is down, and the next snapshot is the first moment the relay is known to be listening.
4. **`EdDSA` deprecation warning from joserfc** (see CONTRACT_ISSUES.md #5) is silenced during
   verification only; the contract value is kept.
5. **The Windows input adapter is not device-tested.** `SendInput`, `MapVirtualKeyW` scan codes,
   `OpenInputDesktop`/`GetUserObjectInformationW` secure-desktop detection, token integrity levels and
   the CTRL-release-on-failure path have only been exercised through their pure builders on Linux.
   The README checklist lists the manual steps.
6. **Relative motion is subject to Windows pointer acceleration** ("Enhance pointer precision"), like a
   physical mouse. The phone's sensitivity setting cannot compensate exactly; the ratio must be
   measured on a device.
7. **`foreground_app.elevated` is usually absent exactly when it matters**: `OpenProcess` on an elevated
   process typically fails for a medium-integrity agent, which is reported as unknown (None), never as
   a guess. Refusals then surface as `INPUT_RESTRICTED` from the `SendInput` error mapping (access
   denied + non-default input desktop) or as a plain `INPUT_INJECTION_FAILED`.
8. **Lock / secure-desktop detection is polled every second** (`lock_poll_seconds`), so up to one
   second of batches can reach `SendInput` after a lock; Windows refuses them and the session then ends
   with the held input released.
9. **The tray's *Paired phones* submenu is rebuilt on status updates**, so a phone paired a moment ago
   appears after the next `update_status` (every connection/state change), not instantly.
10. **Other-session conflicts are detected only through the pid file's recorded session id** on
    Windows; a pid file deleted by hand (or two sessions starting within the same instant, before either
    wrote it) leaves nothing to detect, and then a second agent of the same account could start in
    another session and supersede the first at the relay (DECISIONS.md #44). A global (`Global\`)
    per-account mutex would close that gap but needs the account SID in the name and a device test; not
    done. Not device-tested.
11. **The recovery file is written synchronously from the dispatch thread** on every hold change
    (button down/up, shortcut begin/end): a tiny atomic replace without fsync; a power loss in that
    window could lose the last hold change.
12. **`input_session{ended}` after a relay disconnect cannot be delivered** (the socket is gone); the
    phone relies on the relay's disconnect handling and on `INPUT_SESSION_EXPIRED` for the old id.
13. **Batch-rejection reasons do not reach the phone through the current relay** (CONTRACT_ISSUES.md
    #11): the agent emits them as `error` frames, which cloud-api logs and drops. The phone sees
    `dropped_events`, `pc_state.input_restricted` / `foreground_app` and `input_session` frames only.
    Needs a contract field (`error_frame.ref_input_session_id`) and relay routing; not fixable inside
    pc-agent.
    **Resolved by the maintainer (protocol 1.1 amendment, 2026-10-09):** rejection reasons now reach the phone (see CONTRACT_ISSUES #11); the phone ignores rejections naming a session it has left.

14. **Clock-offset estimate, residual cases** (DECISIONS.md #39): the first batch of a session is judged
    against the `input.session_start` sample; if that command and the first batch were both delayed by
    the same stall, the first batch can pass (the envelope window still bounds it to ~15 s, and the next
    fresh batch re-establishes the baseline). A phone clock stepped BACKWARDS mid-session makes batches
    look stale for up to 30 s (the estimator window) unless the session is restarted.
15. **A click on the elevated window itself is dropped by Windows without an error** (UIPI). Keyboard
    events are refused honestly while the restriction is known; pointer events are not, so the customer
    can click a normal window. The ack then counts such a click as accepted. Not detectable from
    `SendInput`; documented in the README checklist. The same applies to keyboard events while the
    foreground's integrity level is UNKNOWN (#7: `OpenProcess`/`OpenProcessToken` refused): the agent
    refuses only on a known restriction, never on a guess, so such keystrokes can be dropped by UIPI and
    still counted as accepted. Device testing must show how often elevation is undeterminable.
16. **A `SendInput` call that never returns cannot be interrupted.** The session still ends after the
    bounded 2 s wait and releases what it knows; anything that stuck call presses later is released
    when (if) it returns (DECISIONS.md #42), otherwise at the next start-up from `input_holds.json`.
17. **The target-change grace (2 s) is a heuristic for keyboard-only phones** (DECISIONS.md #40): a
    phone that kept live-typing without pausing for a `foreground_app` change for more than 2 s would
    type into the new window. The PWA pauses live typing on that change; a stricter contract signal
    (e.g. a target generation number echoed by the phone) would remove the heuristic.
