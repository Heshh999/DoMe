# cloud-api — implementation decisions

Choices made where `docs/design/cloud-api.md` or the spec were silent. Each is the simplest option
consistent with ADR-0001 and the frozen contract; all are reversible.

1. **PC credential is generated at poll time, not approval time.** The design says only the hash of
   `pc_credential` is stored and that `POST /v1/agent-link/poll` returns it exactly once. Generating it
   when the agent polls (state `approved` → `consumed` in the same transaction) avoids keeping the
   plaintext anywhere between approval and delivery.
2. **Re-linking a known PC key reuses the PC row.** `pcs.kid` is unique. If an agent with an existing
   key links again on the same account (reinstall, lost credential) the row is kept (grants and history
   survive), its credentials are revoked (`revoked{credential_rotated}` to a live socket) and a fresh
   credential is issued. A key already linked to *another* account is refused with 409 `FORBIDDEN`.
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
