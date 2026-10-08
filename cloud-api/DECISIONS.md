# cloud-api — implementation decisions

Choices made where `docs/design/cloud-api.md` or the spec were silent. Each is the simplest option
consistent with ADR-0001 and the frozen contract; all are reversible.

1. **PC credential is generated at poll time, not approval time.** The design says only the hash of
   `pc_credential` is stored and that `POST /v1/agent-link/poll` returns it exactly once. Generating it
   when the agent polls (state `approved` → `consumed` in the same transaction) avoids keeping the
   plaintext anywhere between approval and delivery.
2. **Re-linking a known PC key is allowed only for an unlinked (soft-deleted) PC.** `pcs.kid` is unique.
   `POST /v1/agent-link/start` carries no proof that the caller holds the private key
   (`CONTRACT_ISSUES.md` #10), so approving a code whose key belongs to a PC that is *still linked* must
   not hand that PC's identity, credentials and grants to whoever submitted the code: it is refused with
   409 `PC_ALREADY_LINKED` and the pending code stays pending (the owner can unlink under Devices and
   approve again within the code's lifetime). A key that belongs to a soft-deleted PC on the same account
   (the agent kept its identity key and lost its credential after `revoked{pc_unlinked}`) reuses the row so
   history survives, with every credential and grant revoked — phones pair again. A key linked to
   *another* account is refused with 409 `FORBIDDEN`. (Changed after review; previously a live PC was
   silently re-linked and kept its grants.)
3. **Disabling a PC never revokes it.** `PATCH /v1/pcs/{id} {enabled:false}` pushes
   `grants_snapshot{pc_enabled:false}` and a `pc_status{enabled:false}`; the socket stays open and local
   grants stay (ADR-0001 D5/D8: plan/account state, not revocation). Only `DELETE /v1/pcs/{id}` sends
   `revoked{pc_unlinked}` and closes the socket.
4. **Plan-disabled controller selection** follows `plans.json → downgrade_policy.default_selection`
   (`most_recently_seen`): the `max_controllers` most recently seen live controllers of the account are
   `active`, the rest `plan_disabled`. The explicit customer selection UI is Phase C.
5. **Revocation and in-flight commands.** A revoked controller's in-flight commands are not terminated
   by the relay with a fabricated `canceled`; the relay sends a best-effort `cancel` to the PC and the
   PC's own result (or the deadline sweeper) terminates them truthfully. Sockets of the controller get
   `revoked` and close with 4003; affected PCs receive a fresh snapshot.
6. **Duplicate check happens before rate limiting and the online check.** An identical re-submission is
   answered from the lifecycle row even when the PC is offline and never consumes rate-limit budget;
   `COMMAND_ID_REUSED` also covers a reused id from a different controller or PC.
7. **Rejections before the signature is verified** (`UNKNOWN_KEY`, `GRANT_MISSING` on an unbound socket,
   `SIGNATURE_INVALID`, …) still answer with a `result` when a `command_id` can be read from the
   unverified payload with the strict parser; that id is used for nothing but addressing the frame.
   Without a readable id an `error` frame with `ref_pc_id` is sent.
8. **Confirmation rejections** (kid mismatch, revoked, bad signature, no pending challenge) are `error`
   frames with `ref_pc_id`, not `result`s: the command is still owned by the PC and must end with exactly
   one terminal result. See `CONTRACT_ISSUES.md` #3.
9. **WebSocket upgrade refusals** use uvicorn's `websocket.http.response` extension so the design's
   HTTP statuses are real (401 no session/token, 403 bad or present Origin, 503 relay full); on a server
   without the extension the handshake is closed instead.
10. **REST bodies are self-validated outside production** (`Settings.validate_rest_responses`): every
    response built from a `rest.schema.json` definition is validated before it is sent, so a contract
    drift becomes a loud 500 in development/tests. Production skips the check for latency.
11. **Agent `hello` version negotiation** accepts the peer when any announced protocol version is
    compatible with ours (same MAJOR, MINOR ≤ ours) *and* its `registry_version` is compatible;
    otherwise `error{PROTOCOL_INCOMPATIBLE, detail{peer, supported}}` and close 4000.
12. **`last_power_request`** on the PC row is set when a confirmed power command is forwarded (not when it
    succeeds): the spec wants "recently requested power action" shown even when the PC then vanishes.
13. **Security-event `detail`** passes through the log redaction rules before storage, so a coding
    mistake cannot put a token, code, challenge or media title into the table.
14. **Session cookie** is `Secure` iff the public origin is https (plain-http development/test origins
    could not set it otherwise); `HttpOnly; SameSite=Lax; Path=/` always.
15. **Agent endpoints** (`/v1/agent-link/*`, `/v1/agent/*`, `/ws/agent`) reject any request carrying an
    `Origin` header: they are for the native agent process, never for a page.
16. **Login/token abuse limits** not named in the design: `/v1/auth/login` 60/min per IP,
    `/v1/agent/token` 30/min per IP (sliding windows, in-memory like the others).
17. **`GET /v1/pairing/{id}`** answers 404 for sessions that are still `open` (not yet claimed) — the phone
    only learns a `pairing_id` by claiming, and an open session must not be enumerable.
18. **Request transactions commit before the response is sent.** FastAPI ≥ 0.118 runs the exit code of
    `yield` dependencies *after* the response by default, which let a client act on a 2xx before the row
    was durable (seen as a 401 on `/ws/agent` right after `/v1/agent/token`). The `DB` dependency uses
    `Depends(db_session, scope="function")` so commit/rollback happens before the response, and handlers
    that must act on live sockets only after durability (`pairing/claim`, revocations) call
    `await db.commit()` explicitly first.
19. **Security-event detail keys avoid `code`/`*_code`.** Those names are redacted by the logging rules
    (pairing codes); a rejection's error code is stored under `reason`.
20. **Test doubles**: none exist in `dome_api`. The agent and controller in `tests/` are simulators built
    on the shared library and only live under `tests/`.
21. **Client address resolution lives in the application, not in uvicorn's flags.** `X-Forwarded-For` is
    honoured only from `DOME_TRUSTED_PROXIES` (explicit addresses/CIDRs; `*` refused; required in
    staging/production, `none` for a directly reached process) and resolves to the right-most hop that is
    not a trusted proxy. `dome-api` starts uvicorn with `proxy_headers=False` so `FORWARDED_ALLOW_IPS` in
    the environment cannot widen the trust. The middleware wraps uvicorn's well-tested
    `ProxyHeadersMiddleware` walk rather than re-implementing it.
22. **Per-socket frame budget and bounded security events (relay).** Every inbound controller frame is
    charged to a token bucket per connection *before* any database work (default 600/min, burst 120 —
    above the plan limits in `plans.json` so legitimate sliders hit the per-command plan limiter, which
    gives a precise `RATE_LIMITED` result, first). A refused `command` still gets a relay `result`
    (DECISIONS #7, when its id is readable), other refused frames an `error`; after `burst` refusals the
    socket closes with 4000 and one `controller_throttled` event. Security-event rows per socket are
    capped per minute; the first suppressed row becomes one `relay_events_throttled` row so the flood is
    still visible in the account's security log. Sockets that were accepted but have not sent `hello`
    hold a slot toward `DOME_RELAY_MAX_CONNECTIONS`. Agent sockets are authenticated by a PC credential
    and are not frame-limited (see `KNOWN_ISSUES.md`).
23. **`user_code` entry is rate limited by failures, not by lookups.** Successful previews of a pending
    code (the approval page) are unlimited; unknown/expired/decided codes count against a 15-minute
    budget per account and per client IP (defaults 10) after which every lookup, approval and denial from
    that key is `429` without touching the table. Each counted failure is a `link_code_lookup_failed`
    security event (bounded by the budget).
24. **Unlinking a PC is announced to its subscribers.** After `DELETE /v1/pcs/{id}` every subscribed
    controller receives one `pc_status{connection:"offline", enabled:false}` and its subscription to that
    PC is dropped; later commands to it are `ACCOUNT_MISMATCH` ("No such PC on this account") as for
    any PC that is not on the account.
25. **Command ids are looked up within the account.** The duplicate check filters `commands` by
    `account_id`; an id that exists in another tenant is invisible and surfaces only as a primary-key
    conflict at insert time, answered with the same `COMMAND_ID_REUSED` — no cross-tenant oracle, and
    the probe costs the caller a fully verified command.
