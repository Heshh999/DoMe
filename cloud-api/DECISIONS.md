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
26. **A controller socket is bound only after a signed hello proof** (cross-component review, 2026-10-09).
    `GET /v1/controllers` reveals every paired phone's `kid` to the whole account, so a bare kid in `hello`
    must not be enough to act as that phone: `controller_ws` verifies `hello.proof` (an ES256 envelope over
    `hello_proof{kid, account_id, issued_at, expires_at, nonce}`, `rules.controller_socket_identity`) with
    the JWK stored at pairing, binds the socket only then, answers a present-but-invalid proof with
    `UNKNOWN_KEY` + close 4003 + a `controller_hello_proof_rejected` event, and keeps verified nonces in
    `ConnectionManager.hello_nonces` until they expire (single use). No proof → unbound socket, as for an
    unpaired installation.
27. **Everything forwarded ends as `outcome_unknown` when the PC connection is lost** (same review).
    `_fail_inflight`, `sweep_deadlines` and `startup_sweep` no longer distinguish "executing ack seen"
    from "not yet acked": once a command was written to the agent's socket the relay cannot know whether
    the PC ran it (a half-open socket keeps executing for up to 55 s), so `failed/PC_OFFLINE` and
    `COMMAND_EXPIRED` are reserved for commands that were never forwarded. The agent's re-sent results
    (`rules.late_results`) correct the unknown outcome exactly once, including the `PC_OFFLINE` verdict
    for queued commands it dropped.
28. **Protocol 1.1 frames flow only between peers that announced 1.1** (`rules.controller_socket_identity`:
    "1.0 peers never receive them"). Each socket records whether its `hello.protocol_versions` covers 1.1.
    A 1.0 controller sending `input_batch` gets `PROTOCOL_INCOMPATIBLE`; a batch for a PC whose agent
    announced only 1.0 is refused with `PROTOCOL_INCOMPATIBLE` ("the PC's DoMe agent needs an update") instead
    of being written to a socket that would reject it as malformed; `input_ack` / `input_session` skip 1.0
    sockets. The test harness announces both versions by default.
29. **Input batches have their own pre-database bucket and never close the socket for overshooting.** The
    command frame bucket (600/min) would starve a 40 batches/s touchpad, so `input_batch` frames are charged
    to a per-socket bucket sized from `version.json → limits.input_batches_per_second` with the plan burst,
    then to the per-controller `plans.input_rate_limit` bucket once verified. Refusals are `RATE_LIMITED`
    error frames at most once per second per socket; unlike command floods they do not count toward the
    4000 close, because a phone that briefly overshoots while dragging must keep its session (spec §10A:
    bounded queues, no delayed backlog — dropped batches are simply not forwarded).
30. **`COMMAND_EXPIRED` from the shared window check is reported as `INPUT_STALE` for batches.** The library's
    `check_command_window` speaks in command terms; for a stream the contract's own code for "delayed too
    long to deliver safely" is `INPUT_STALE`, which is what the phone copy expects. Other codes
    (`SIGNATURE_INVALID`, `CONTROLLER_MISMATCH`, `ACCOUNT_MISMATCH`, `MALFORMED_MESSAGE`, `CLOCK_SKEW`,
    `PROTOCOL_INCOMPATIBLE`) pass through unchanged.
31. **Relay-side replay guard per socket.** `rules.input_sessions` makes the agent's strictly-increasing `seq`
    the authoritative check, but the relay keeps the highest forwarded `seq` per `input_session_id` on each
    controller socket (bounded to 8 sessions) and drops a repeat with `INPUT_SEQUENCE_INVALID`, so a replayed
    batch costs the PC nothing. It is per socket (a second socket of the same phone is not cross-checked) and
    says nothing about ownership — session ids are fresh random 22-character strings.
32. **Input-session ownership is learned only from the agent.** `input_ack` carries no controller id; the
    relay maps `input_session_id → controller_id` from the agent's `input_session` frames (the agent issues
    ids and owns sessions) and forgets it on `ended`. A batch's own `controller_id` is never used to learn
    ownership (a non-owner could otherwise redirect acks to itself by guessing an id). An ack for an unknown
    session is dropped and logged. `input_session` frames naming a controller outside the PC's account are
    refused with a `relay_frame_rejected` event; other frames of the PC are unaffected.
33. **Support diagnostics are redacted structurally and by pattern; the message gets the pattern pass too.**
    The log redactor knows key names, but a customer may paste a token under a harmless key or into the free
    text. `redact_diagnostics` parses JSON, applies the key rules, then masks token-shaped substrings (JWT
    triples, `Bearer …`, Stripe-style keys, base64url/hex runs ≥ 32 chars) in every string; non-JSON text gets
    the pattern pass only. The key rule for `code` (pairing codes) also masks an `error.code` field inside a
    bundle — accepted: the top-level `error_code` field of the ticket carries the error the customer saw.
34. **`grant_update` nudges subscribers with `pc_status`.** The contract has no "your grant changed" frame for
    controllers; every phone already treats `pc_status` as "re-read this PC", which makes it refresh
    `GET /v1/pcs/{id}/grants`. The PC gets the authoritative `grants_snapshot`.
35. **Support ticket budget counts successes.** `svc.support_ticket_limiter` is checked before the body is
    read (so a flood costs no parsing) but only *charged* after the row is durable; malformed submissions do
    not eat the hour's budget. A reference collision (randomly impossible in practice) is retried five times
    and then answered `503 SERVICE_UNAVAILABLE`, never a fabricated reference.
36. **The input `error` frames carry `ref_pc_id` exactly as subscription errors do.** `rules.terminal_result`
    reserves `error` frames for situations without a command id; a batch has none, so no `result` is ever
    emitted for it and the controller correlates by PC (and by the session it is driving).
37. **A sustained input flood closes the socket (amends #29).** #29 kept every input overshoot open; review showed
    that lets one paired phone stream valid-shaped `input_batch` frames at wire speed, each costing a strict parse
    and schema validation on the single relay process. Refusals of the per-socket input bucket now also drain a
    second, leaky per-socket budget (`RateLimiters.input_refusals`: 2x `input_batches_per_second` sustained,
    capacity 5 s of that, i.e. 80/s and 400). Emptying it closes the socket (4000) with one `controller_throttled`
    event, `detail.reason = input_flood` (the command path's event now carries `reason = frame_flood`). A token
    bucket rather than a 5-second window: a touchpad at up to 3x the budget (40 accepted + 80 refused per second)
    never closes, a wire-speed burst of 400 frames never closes, anything well above that closes in proportion to
    how far above it is. Input refusals are counted separately from command refusals (`input_refused`), so an
    overshooting drag can never push a later command flood check over its limit.
38. **RATE_LIMITED from the per-controller input budget is throttled per socket.** A batch that passes the socket
    bucket but not the controller's `input_rate_limit` (two sockets of one phone) cost the DB checks, an error frame
    and a log line every time. `_reject_input` now shares the socket's once-per-second notice
    (`ControllerConn.input_notice_due`) with the socket bucket: further refusals in that second send nothing, log
    nothing and write nothing. The per-batch DB work itself is still bounded by the socket bucket (KNOWN_ISSUES #7).
39. **Input-session frames reach each socket once; owners are learned only for live grants.**
    `send_input_frame_to_controller` returns the connection ids it wrote and the subscriber broadcast skips them.
    Learning an owner (any non-`ended` frame naming a controller the connection does not already map to the
    session) requires the controller to be in the account, not revoked, and to hold a live grant on this PC;
    `ended` only requires account membership so a just-revoked controller's sockets and the subscribers still hear
    that its session ended. `AgentConn.input_sessions` keeps the latest session per controller and at most
    `MAX_AGENT_INPUT_SESSIONS = 4` entries (one live session per PC is the rule; the slack covers a takeover whose
    `ended` is still in flight). Agent-attributed `relay_frame_rejected` rows go through
    `ConnectionManager.agent_rejection_event`, a per-PC sliding window sized like the controller socket cap, with
    one `relay_events_throttled` row on the first suppression (this also covers invalid `confirmation_required`).
40. **Diagnostics have their own stricter redactor (amends #33).** The log key list is the wrong tool for what
    spec §11A forbids in diagnostics. `redact_diagnostics` now masks, on top of the log keys, the keys that carry
    typed text, input events, URLs, search queries, pairing material, clipboard and video ids (plus `_url`, `_href`,
    `_text`, `_query` suffixes); strings get absolute URLs reduced to scheme + host (userinfo dropped), bare
    `host/path` reduced to the host, the token patterns and a pairing-code pass. Pairing codes: 20 Crockford
    symbols contiguous, or 4x5 / 5x4 groups with one consistent separator (`-`, `_`, `.`, space); within a longer
    run of groups each window is checked and a uniform-case window is preferred ("code K7Q2 M9XD 4HPR 8WTV ZC3N"
    keeps "code"); a candidate needs a digit, or upper case with separators, so ordinary lower-case prose never
    matches. Over-redaction is the accepted failure mode (a shouted run of five four-letter words is masked;
    UUIDs were already masked by the 32+ run rule). JSON embedded as a string is parsed and redacted by key too.
    The ticket *message* gets the token and pairing-code pass but keeps URLs: it is the customer's own words and
    the spec's URL rule is about default diagnostics. `text` joins the log `REDACT_EXACT` set
    (rules.input_sessions (5)).
41. **The support ticket budget is reserved, not checked (amends #35).** `exhausted()` before the first await
    and `hit()` after the flush let concurrent posts all pass (12 stored against a limit of 10 in review's probe).
    The handler now calls `allow()` — check and record in one synchronous step on the event loop — before reading
    the body, and `release()`s the reservation on any exception before the commit (malformed bodies, 503, a failed
    commit), so #35's "failures do not eat the budget" still holds. `account_id` is read once up front, and a
    reference collision is retried inside `db.begin_nested()` (a SAVEPOINT with a fresh `SupportTicket`) instead of
    rolling back the whole session, so no expired ORM attribute is touched on the retry path.
42. **1.0 subscribers get state frames without the 1.1 pc_state keys.** The compatibility rule rejects unknown
    fields, so forwarding an updated agent's `state` unchanged would break a stale 1.0 PWA's live view.
    `frames.legacy_state_frame` drops `foreground_app`, `input_session` and `input_restricted`
    (`manager.PC_STATE_1_1_FIELDS`) for sockets that did not announce 1.1, live and for the cached frame sent on
    subscribe. State frames are unsigned routing data, so removing keys does not touch anything the phone verifies;
    1.1 sockets still get the agent's frame unchanged.
