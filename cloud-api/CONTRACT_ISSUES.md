# cloud-api — contract observations

The contract under `shared/protocol/` is frozen for this component; nothing there was changed. These are
the gaps found while implementing, with the workaround used. Each proposes an additive change for the
next protocol MINOR bump.

1. **No `rest.schema.json` definition for `GET /v1/account/sessions`.** The design requires the endpoint
   (session list/revoke) but the contract has no body for it. Workaround: the endpoint returns
   `{"sessions":[{"id","created_at","last_seen_at","expires_at","current"}]}` and the API does **not**
   self-validate that body (every other body is validated). Proposed def: `sessions_response` with exactly
   that shape (`additionalProperties:false`).
2. **No definition for `GET /healthz`.** Operational endpoint outside `/v1`; returns
   `{"status","version","database","relay":{"agents","controllers","max_connections"}}`. Proposed:
   either exclude health from the "every REST response" rule explicitly or add `health_response`.
3. **No command-scoped non-terminal rejection for `confirmation` frames.** `rules.terminal_result` says
   `error` frames are only for situations without a `command_id`, yet a confirmation that fails relay
   checks (kid mismatch, not pending, bad signature) has one, and answering with a `result` would
   wrongly terminate a command the PC still owns. Workaround: `error{code, ref_pc_id}` without the
   command id. Proposed: add an optional `ref_command_id` to `error_frame`.
4. **REST-only HTTP conditions have no codes in `errors.json`.** `UNAUTHENTICATED`, `FORBIDDEN`,
   `NOT_FOUND`, `METHOD_NOT_ALLOWED`, `LINK_DENIED`, `LINK_EXPIRED`, `PC_ALREADY_LINKED`, `SERVICE_UNAVAILABLE` are used in
   `error_body` responses only (they match the `^[A-Z_]+$` pattern and never appear in relay frames).
   Proposed: add them to `errors.json` so clients can map them centrally.
5. **`agent_link_poll_response.pc_credential` / `agent_token_response.access_token` lengths.** Fixed at
   43 chars (base64url of 32 bytes) — fine today; noting that any future token format change is a
   contract change, not a backend-only one.
6. **`pairing_status_response.granted_capabilities`** is optional with no statement of *when* it is
   present. Implemented: present iff `state == "approved"`; `controller_id`/`grant_id` likewise.
7. **`grants_snapshot.display_name`** has `maxLength: 64` but `pairing_claim_request.display_name` is
   also ≤ 64, so no truncation occurs; the API truncates defensively anyway.
8. **`pc.last_power_request` semantics** ("recently requested power action") are not defined; set at
   forward time of a confirmed `power.*` command (see `DECISIONS.md` #12).
9. **`EdDSA` is flagged "deprecated via RFC 9864" by joserfc 1.7.** The contract
   (`plans.json → entitlement_assertion.algorithm`, `entitlement.schema.json`) mandates the JWS `alg`
   value `EdDSA`; RFC 9864 introduces the fully-specified `Ed25519` algorithm name instead. joserfc still
   signs and verifies `EdDSA` when it is explicitly allowed (`algorithms=["EdDSA"]`, which the API passes)
   but emits a `SecurityWarning`. The key type/curve (Ed25519) and security are unchanged. Proposed: on the
   next MINOR bump change the algorithm identifier to `Ed25519` (agents verify by JWKS `kid`, so the
   switch is coordinated through `plans.json`).
10. **`agent_link_start_request` has no proof of possession of the PC key.** The body is
    `{pc_public_jwk, agent_version, platform, pc_name_hint?}` with `additionalProperties:false`, so the
    backend cannot verify that the caller holds the private key whose public half it registers; anyone
    who learns a PC's public JWK can start a link code for it. Workaround (DECISIONS #2): a code naming
    the key of a PC that is still linked is refused at approval (409 `PC_ALREADY_LINKED`) and silent
    re-link is limited to soft-deleted PCs, where no credential or grant can be inherited. Proposed for
    the next MINOR: an optional `proof` object on the start request — the agent signs
    `"dome-link|" + kid + "|" + <server nonce or RFC 3339 minute>` with its ES256 key (same envelope
    encoding as commands) — which the backend verifies with `dome_protocol.signing` before storing the
    row; once every shipped agent sends it, make it required.
11. **`check_command_window` reports `COMMAND_EXPIRED` for an expired `input_batch`.** The shared
    `verify_and_parse_input_batch` reuses the command window helper, so a batch outside its 5 s window raises
    `COMMAND_EXPIRED` although `errors.json` has `INPUT_STALE` for exactly this case. Workaround: the relay
    translates that one code (DECISIONS #30). Proposed: have the library raise `INPUT_STALE` (or accept an
    `expired_code` argument) for input batches so every component reports the same code.
12. **No relay → agent frame for "the controller's socket is gone".** `rules.input_sessions` names "the relay
    losing the controller's socket" as a trigger that ends the session with reason `controller_disconnected`
    "when the relay reports it", but `relay_to_agent` contains no such frame. Workaround: none on the relay;
    the agent's lease (`input_lease_seconds`) ends the session. Proposed: `relay_to_agent_controller_gone
    {type: "controller_gone", controller_id, connection_id, at}`, sent when the last socket bound to a
    controller that owns a live session closes.
13. **No way for a controller to learn its grant changed except by re-reading REST.** After `grant_update` the
    PC receives `grants_snapshot`, but `relay_to_controller` has no "grant changed" frame; the relay re-sends
    `pc_status` as a nudge (DECISIONS #34). Proposed: an optional `capabilities` array on `pc_status_event`, or
    a dedicated `grant_changed{pc_id, capabilities}` frame.
14. **`rules.input_sessions` says the relay keeps "NO security event per accepted batch" but is silent on
    rejections.** Implemented as at most one `input_rejected` event per minute per controller (bounded by the
    per-socket cap as well). Proposed: state the bound in the rule so phones and the security log agree on what
    a burst of rejections looks like.
    **Resolved by the maintainer (protocol 1.1 amendment, 2026-10-09):** agent `error` frames carrying `ref_controller_id` are now routed (not logged only) after an account check; foreign or owner-contradicting routes are refused with a `relay_frame_rejected` event.

15. **`support_ticket_request.diagnostics` (≤ 32768 chars) exceeds the service's generic 16 KiB REST body
    limit.** Not a contract defect, but worth noting: the support endpoint reads up to 48 KiB. Proposed: a
    `limits.max_rest_body_bytes` (or a per-def hint) so clients and servers size buffers from one place.

16. **The contract does not say what a relay forwards to a 1.0 peer when an updated agent adds 1.1 `pc_state`
    fields.** The compatibility rule ("Unknown fields are rejected, not ignored") means a 1.0 phone rejects the
    whole `state` frame. Workaround (DECISIONS #42): the relay strips `foreground_app`, `input_session` and
    `input_restricted` from state frames sent to sockets that did not announce 1.1 (state frames are unsigned).
    Proposed: state in `rules.controller_socket_identity` (or the compatibility section) that the relay downgrades
    unsigned routing frames to the peer's announced version, and list the fields per MINOR version.

17. **This build changed the contract (docs/design/assistant.md §1, Step A).** The assistant's REST bodies, eight
    error codes, two `plans.json` fields, the `rules.search_queries` clause and `fixtures/assistant-plan-cases.json`
    were added without a version bump under the new `docs/PROTOCOL.md` §1 exception (§19). Observations:
    - `assistant_step.params` is an open object by design; per-action validation is in code on all three sides.
    - A cancelled turn (client gone) has no response body; the server logs status 499, which no client ever sees.
    - `assistant_status_response.turn_deadline_seconds` is bounded 10–60 like `DOME_AI_TURN_DEADLINE_SECONDS`; the
      service reports the setting even when a test enforces a shorter deadline.

18. **Protocol 1.3: `plan_id` admits `pro_plus`, but the PC never sees it.** `agent_entitlement_response.plan`
    references `plan_id`, so the schema would allow `pro_plus` there, yet a 1.2 agent's bundled schema would refuse
    it. cloud-api therefore sends `pro` for every paid plan in that body and in the assertion's `plan` claim
    (DECISIONS #69). Proposed for a later MINOR: give `agent_entitlement_response.plan` its own enum
    `[free, pro]` with a comment, so the contract states what the code guarantees.

## Protocol 1.4 (Screen view, Phase 1; WP1.2, 2026-10-21)

19. **`plans_response` has no `screen_view_enabled`.** The design (§12) and the WP1.2 brief want `GET /v1/plans` to say
    whether the operator switch is on, but `rest.schema.json → plans_response` is closed (`additionalProperties:
    false`) and names only `plans`, `pricing`, `billing_enabled`, `billing_mode`, `ai_enabled`. The API validates every
    body it returns, so the field is **not** sent. Workaround: the switch is visible at `GET /healthz` (`screen_view`:
    `on` / `off`, no contract) and a phone that asks anyway gets `SCREEN_VIEW_DISABLED` on `screen_ice_request`, which
    the §16 copy already covers. Proposed: `screen_view_enabled: boolean` (required) on `plans_response`; the code adds
    it the day the schema does (`routes/plans.py`, one line).
20. **`relay_screen_allowance.reason` has no value for "the relay never reserved this session".** Worked around
    (2026-10-22): a `screen_usage` for a session id the ledger does not know is first bound to the PC's newest
    session-less reservation of the last 105 s (a start whose pending entry was lost with the agent socket or a relay
    restart, KNOWN_ISSUES #29); if there is none it gets **no answer**, so the agent ends at its ticket's deadline with
    `allowance_unconfirmed` instead of reading a reason-less `final` as `allowance_exhausted` (which the phone showed as
    "You've used Free Screen view for now"). A session the relay already settled still gets `final` with no reason.
    Proposed, still: a fourth reason `unknown_session`.
21. **`agent_screen_session` carries no `command_id`.** The relay binds a `started` frame to its ledger reservation
    through the `screen_session_id` the result carried, or, when `started` arrives first, through the controller's one
    pending start on that PC. Both work for Phase 1 (one session per PC, one pending start per controller) but the
    second is a guess the agent could make exact. Proposed: an optional `command_id` on `started` (the start it answers).
22. **`relay_screen_ice_servers` cannot say why TURN is `disabled`.** The relay knows which cap or guard refused
    (daily GB, monthly mints, the hard cap); the phone only learns `turn: disabled` and shows the §16 copy "could only
    try a direct connection right now". Proposed: an optional `turn_reason: cap_daily | cap_monthly_mints | guard_hard`.
23. **`rules.screen_metering` (3) says the pause discount is "at most 120 s per session"; the relay caps it per window**
    (2026-10-22). Only `paused -> resumed` pairs the relay saw during a started session count (the open-pause term that
    `test_pauses_are_discounted_only_as_pairs_the_relay_saw_and_at_most_120s` used to assert is gone: the rule was
    right), and the 120 s are shared by every session of one Free window, which is stricter than "per session" (a
    patched agent that pauses and ends every session would otherwise turn 600 s into hours). A session that spans two
    windows can be discounted in each. Proposed wording: "at most `screen_secure_desktop_pause_max_seconds` per
    allowance window". Likewise the relay forgives a start refused at the PC (`SCREEN_CONSENT_DECLINED` /
    `_TIMEOUT`, no `started`, within the consent timeout + 5 s) on a counter of its own, three per window, which
    `rules.screen_metering` (3) does not mention (it names only `connection_failed`).
