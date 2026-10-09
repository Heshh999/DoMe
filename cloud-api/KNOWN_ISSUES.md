# cloud-api — known issues

Items the review surfaced that are deliberately deferred, with the reason and the intended fix.

1. **Agent sockets have no per-socket frame budget.** The controller socket is frame-limited before any
   database work (DECISIONS #22); `/ws/agent` relies on the PC credential (issued only after an account
   owner approved the link) and on per-frame schema validation. A compromised or buggy agent could still
   write `agent_connected`/`pairing_*` events or `state` broadcasts at wire speed. Intended fix: the same
   `TokenBucketLimiter` keyed by `pc_id` in `agent_ws._loop`, closing with 4000 after `burst` refusals.
2. **Rate limiters are in-memory per process.** Correct for ADR-0001 D1 (one relay process); a second
   process would double every budget. If the deployment ever runs more than one API process, the sliding
   windows and buckets move to PostgreSQL (`security_events`-style rows with a TTL) or the proxy layer.
3. **`ACCOUNT_MISMATCH` for a command to the account's own, just-unlinked PC.** The routing order treats a
   soft-deleted PC exactly like a PC that never belonged to the account (design step 5). Subscribers are
   now told the PC is gone (DECISIONS #24), so a phone only sees this code if it ignores that
   `pc_status`. A dedicated code would be a contract change (`errors.json` is frozen for this component).
4. **Proof of possession at link start** is a contract gap (`CONTRACT_ISSUES.md` #10); the backend-side
   mitigation (no takeover of a linked PC) is in place, the cryptographic fix needs the MINOR bump.
5. **`DOME_TRUSTED_PROXIES` on Fly.io is a deployment fact, not a code default.** The README explains
   how to find the proxy's source range; the process refuses to start in staging/production without an
   explicit value rather than guessing one.
6. **Operators have no support route.** Tickets are written and read by the owning account only; the
   `status` / `answer` columns exist (`in_review`, `answered`, `closed`) but nothing sets them yet. The
   operator interface (spec §15: strong operator authentication, audit, no command execution) is the
   intended home; until then an operator answers out of band. `DOME_SUPPORT_RESPONSE_EXPECTATION` stays unset
   until the founder decides on a realistic expectation.
7. **Per-batch database work on the input path.** `route_input_batch` performs about five queries per
   accepted batch (controller record, account/plan, plan-enabled controllers, PC, grant). The suite forwards
   well above the 40 batches/s budget on one socket, but at many concurrent touchpads this is the first thing
   to optimise: a one-second per-socket authorisation cache invalidated by `grant_update`, revocation and
   `pc_status` changes. Not done yet because the agent re-verifies every batch against its local grant, so a
   stale relay check cannot grant anything.
8. **The relay does not tell the agent that a controller's socket closed.** `rules.input_sessions` lists
   "the relay losing the controller's socket" as a session-ending trigger with reason
   `controller_disconnected`, but `relay_to_agent` has no frame for it (`CONTRACT_ISSUES.md` #12); the agent's
   3 s lease ends the session instead.
9. **`PC_RECONNECTING` for input is not exercised by a test.** The window between an agent's `hello_ack` and
   its first `grants_snapshot` is a single awaited send in the same coroutine, so the harness cannot observe
   a batch landing inside it; the branch is covered by the same check commands use (unit-level reasoning,
   not yet verified by an integration test).
10. **`.env.example` is a root file** and was not extended with `DOME_RATE_SUPPORT_TICKETS_PER_HOUR` /
    `DOME_SUPPORT_RESPONSE_EXPECTATION`; both default sensibly and are documented in this README.

