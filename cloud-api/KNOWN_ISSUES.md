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
