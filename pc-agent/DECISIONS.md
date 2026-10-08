# pc-agent decisions (where the design document was silent)

1. **Power results are emitted when the OS call is issued, not when the countdown is armed.** The
   confirmed command is acked `executing`, the countdown is visible as `pending_power_action` in state
   frames, and `result` carries what Windows actually accepted (`accepted`, `countdown_seconds`,
   `fires_at`) or `POWER_DENIED`/`OS_ERROR`. `power.cancel`, a relay `cancel` frame, local *Disable
   remote control* and agent shutdown end the command as `canceled`/`POWER_CANCELED`. The executor
   supports this through a `DEFERRED` handler return + `Executor.complete()` so the single worker is
   never blocked for the countdown. `SetSuspendState` blocks until resume, so sleep is reported as
   accepted when it has not failed within 2 s. This matches `rules.in_flight` ("armed power
   countdowns are exempt until fires_at + timeout_ms").
2. **Rejections after the journal row exists are journaled as `failed`** (availability, target,
   entitlement, QUEUE_FULL), so an identical re-send gets the identical answer; rejections before
   step 5 (signature, identity, plan state, grant) are not journaled, exactly as the design orders.
   `COMMAND_ID_REUSED` never touches the original row.
3. **Routine-step check (design step 9) runs before the confirmation step** so a refused routine step
   never issues a challenge. Disruptive actions are never `routine_allowed`, so the observable
   behaviour is unchanged.
4. **Availability condition `windows` fails with `PLATFORM_UNSUPPORTED`** (the design's test list
   asks for this on Linux); `session_unlocked` fails with `PC_SESSION_LOCKED_MEDIA_ONLY` when the
   local "media while locked" setting is on, else `PC_SESSION_LOCKED`.
5. **Executable identity**: `approve-app` pins the SHA-256 of the `.exe`; `app.launch` refuses
   (`APP_NOT_APPROVED`, security event `approved_app_hash_changed`) when the file changed, so an
   updated application must be re-approved once. Window operations (focus/minimize/close) only need
   the approval row. Boundary: a local user who can edit `state.sqlite3` can edit the allowlist — the
   agent runs as that user and does not claim to defend against them.
6. **Provisional controller ids**: `pairing_request` carries no `controller_id`, so a locally
   approved grant is stored under `pending-<kid>` until the first `grants_snapshot` names the real id
   (`Store.apply_snapshot` matches provisional rows by kid). Until then a command from that kid fails
   `CONTROLLER_MISMATCH` (the record cannot bind). See `CONTRACT_ISSUES.md`.
7. **Local control channel** (`control.py`): the CLI talks to the running agent over a second IPC
   endpoint with the same identity rule as the bridge (named pipe `\\.\pipe\DoMe.Control.<hash>` /
   `control.sock` 0600). The pairing code is returned only to that local caller; it is never written
   to disk or logs. `pair` therefore requires the running agent; `status/enable/disable/approve-app/
   revoke/diagnostics` fall back to the SQLite store when the agent is not running.
8. **Unverifiable confirmations never terminate a command**: when the confirmation envelope does not
   verify (unknown kid, bad signature) the agent sends an `error` frame and keeps the pending
   challenge, so an attacker (or a relay bug) cannot cancel a pending confirmation with a forged
   frame. Verified-but-mismatching confirmations (wrong kid, digest, declined, expired, target
   changed) consume the challenge and end the command.
9. **Late results**: queued-not-started commands are journaled `failed/PC_OFFLINE` with `sent=1` on
   disconnect (the relay has already answered the controller); results of commands that were
   executing are kept `sent=0` and re-sent once after the next snapshot — the only case the relay may
   have closed as `outcome_unknown`.
10. **Mismatch storm** closes the socket with 4000 and lets the normal backoff run (no reset to
    attempt 0); the storm is recorded as `mismatch_storm_reconnect`.
11. **`revoked` and a rejected credential discard the credential; an identity mismatch (relay names
    another PC) stops reconnecting but keeps the credential** — the relay misbehaved, not the account.
12. **Entitlement claims (never the token) are persisted** so the 72 h grace can be honoured across a
    restart during an outage; a `200 {assertion:null}` clears them immediately.
13. **Thread model on Windows**: pystray owns the main thread, the asyncio agent runs in a worker
    thread, every tkinter object lives on a dedicated Tk thread fed through a queue. Headless mode
    (`DOME_AGENT_HEADLESS=1` or `--headless`) runs asyncio on the main thread with no UI imports, which
    is what CI and the integration tests use.
14. **Agent waits for a link**: `run` without a link does not exit; it polls `identity.json` every 3 s
    so `dome-agent link` can be run from another console (or the installer) and the agent connects as
    soon as the link exists.
15. **Store calls stay synchronous on the event loop**: every call is an indexed point operation on a
    local WAL database (sub-millisecond); only OS adapters run in threads.
