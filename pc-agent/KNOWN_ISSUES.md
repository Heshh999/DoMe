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
