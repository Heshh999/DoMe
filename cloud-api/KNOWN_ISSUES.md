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
9. ~~**`PC_RECONNECTING` for input is not exercised by a test.**~~ Resolved. The earlier note claimed the window
   between `register_agent` and `snapshot_sent = True` was a single awaited send; it actually spans a database
   transaction (`db.get(PC)`, `build_snapshot` with its queries) plus the send, so a batch can land in it.
   `tests/test_input_hardening.py::test_input_batch_before_first_snapshot_is_pc_reconnecting` holds the snapshot
   build on an event, asserts `PC_RECONNECTING`, releases it and asserts the next batch is forwarded.
10. **`.env.example` is a root file** and was not extended with `DOME_RATE_SUPPORT_TICKETS_PER_HOUR` /
    `DOME_SUPPORT_RESPONSE_EXPECTATION`; both default sensibly and are documented in this README.
11. **An input flood still costs a parse and a schema validation per frame until the socket closes.** The
    pre-database input bucket runs after `loads_strict` + `validate_frame` (the frame type is only known then).
    DECISIONS #37 bounds the cost to about the refusal budget (400 frames plus 80/s) per socket before the 4000
    close; a reconnect needs a fresh hello proof (signature check + database). Peeking at the frame type before the
    strict parse would save work but duplicates the parser's trust boundary; not done.
12. **Pairing-code redaction is heuristic.** A code typed with mixed separators (e.g. `K7Q2-M9XD 4HPR`) or split
    across lines is not recognised; a 20-symbol code with no digit in lower case is not masked (probability
    ~0.06 % for a random code). Diagnostics bundles come from the PWA, which never includes pairing material
    (mobile-app), so this is the server's second line, not the first.
13. **Support budget release is approximate under concurrency.** `SlidingWindowLimiter.release` drops the most
    recent timestamp for the key, which may belong to a concurrent request of the same account; the count stays
    exact, the window edge moves by the gap between the two requests (milliseconds).
14. **Timing-based relay tests can flake on a loaded machine.** The input flood tests and the 1.0-build
    `test_queue_depth_and_rate_limit` compare token-bucket outcomes with wall-clock send rates. While these fixes
    were made, one full run out of about ten failed once without a captured cause (an earlier failure of
    `test_queue_depth_and_rate_limit` followed a crashed flood test that left the server draining a backlog; that
    test bug is fixed). If it recurs, swap the limiters for smaller ones in the test, as
    `test_controller_frame_flood_is_throttled_without_growing_security_events` does.
