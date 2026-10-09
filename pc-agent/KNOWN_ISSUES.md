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
    Windows; a pid file deleted by a crash leaves nothing to report (the `Local\` mutex namespace
    already prevents an actual conflict). Not device-tested.
11. **The recovery file is written synchronously from the dispatch thread** on every hold change
    (button down/up, shortcut begin/end): a tiny atomic replace without fsync; a power loss in that
    window could lose the last hold change.
12. **`input_session{ended}` after a relay disconnect cannot be delivered** (the socket is gone); the
    phone relies on the relay's disconnect handling and on `INPUT_SESSION_EXPIRED` for the old id.
