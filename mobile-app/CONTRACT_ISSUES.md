# mobile-app — contract issues

Observations about `shared/protocol/` made while building the PWA. Nothing under `shared/` was
modified; each item records the workaround used here.

1. **Fixture `tab_token` length vs `youtube_target` schema.** `shared/protocol/fixtures/es256-*.json`
   command payloads carry `target.tab_token = "dGFiLXRva2VuLTAwMDAwMDAy"` (24 characters) while
   `actions.json → target_schemas.youtube_target.tab_token` requires exactly 22 characters
   (`^[A-Za-z0-9_-]{22}$`). The signing fixtures still serve their purpose (signature over exact bytes),
   but `verifyAndParseCommand` would reject that payload with `INVALID_PARAMETERS`, so the fixture
   cannot double as an end-to-end "valid command" sample. **Workaround:** the PWA's own tests use
   22-character tokens; the fixture is used only for the challenge/pairing digests.
   Suggested fix: regenerate the fixture with a 22-character token. **Resolved after the build:** both
   fixture generators now use 22-character tokens and the fixtures were regenerated; the fixture
   command payloads validate as commands.

2. **`hello_ack` without `controller_id` is a legal, long-lived state** (unpaired installation). The
   contract says such a socket may only be used for REST pairing status. The PWA keeps the socket open
   (so revocation/`hello_ack` state is live) but shows "This phone is not paired yet" and never sends
   `subscribe`/`command`. After a successful pairing the PWA reopens the socket so `hello` rebinds; the
   contract has no "rebind" frame. Suggested addition (minor): allow the relay to push a fresh
   `hello_ack{controller_id}` when a controller row is created for a connected unbound socket.

3. **`pairing_status_response.pc_online` is optional.** When absent the UI can only say "waiting for
   approval" without saying whether the PC will ask immediately or on its next connect. Not a blocker;
   cloud-api sets it today.

4. **Error codes the PWA needs that `errors.json` does not define**: `UNAUTHENTICATED` (401 bodies),
   `NOT_FOUND`, `FORBIDDEN`, `LINK_EXPIRED`, `LINK_DENIED`, and a client-only `NETWORK`. `errors.json`
   is documented as the table of *protocol* error codes, and the REST `error_body.code` pattern
   (`^[A-Z_]+$`) admits these, so the PWA maps them in `src/lib/labels.ts::errorMessage` with its own
   copy. Suggested: list the REST-only codes in `errors.json` (or a sibling table) so copy is central.

5. **`challenge_text` size vs strict parser error code.** An over-long `challenge_text` fails
   `loadsStrict` with `PAYLOAD_TOO_LARGE` rather than `MALFORMED_MESSAGE`; the relay-frame schema
   bounds the string at 4096 so this only matters for a misbehaving relay. The PWA treats both codes as
   "invalid challenge: nothing to show".

6. **`input.session_start` params default.** `actions.json` declares `takeover` with `default: false`,
   and `buildCommandPayload` applies schema defaults, so every start carries `params.takeover=false`
   even when the phone sends `{}`. Harmless (the agent treats absent and false alike) but worth knowing
   when reading relay logs; the PWA test asserts the applied default.

7. **`support_ticket_response` is a bare `$ref`.** The generated TypeScript has no
   `SupportTicketResponse` type (the alias collapses to `SupportTicket`); `api.ts` types the 201/200
   bodies as `rest.SupportTicket`. Validation still uses the `support_ticket_response` definition name.

8. **Input errors reuse the `error` frame with `ref_pc_id`.** The relay-frames schema documents
   `ref_pc_id` as "required for subscription errors"; input-batch rejections (INPUT_*, RATE_LIMITED,
   PC_OFFLINE/PC_RECONNECTING) arrive in the same shape, so a controller cannot tell a refused
   subscription from a rejected batch by shape alone. The PWA routes by code (DECISIONS 30).
   Suggested (minor): a dedicated `input_error{input_session_id, seq?, error}` frame.

9. **`agent_input_ack.held_keys` items are free strings (maxLength 32)** while `input_event.key` is
   an enum. The PWA renders held keys as text only. Suggested: reuse the key enum plus modifier names.

10. **No `NO_ANSWER` code exists** for a command that got neither ack nor result within the local
    deadline; the PWA uses a client-only `NO_ANSWER` in `labels.ts` (like `NETWORK`, item 4) for the
    touchpad start that times out.

11. **`support_ticket_request` has no idempotency key.** After a network drop, timeout, 5xx or an
    unreadable 2xx the phone cannot know whether the ticket was stored, and a retry may create a
    duplicate. Workaround: the PWA says "DoMe could not confirm whether support received this
    request" and offers "Refresh your requests" before a deliberate resend (DECISIONS 41). Suggested
    (minor, next revision): optional `client_request_id` (UUID) in `support_ticket_request`, with the
    server returning the existing ticket for a repeated id.

12. **No Screen view switch in `plans_response`.** The 1.4 work brief gates the Screen tile on `GET /v1/plans`
    `screen_view_enabled`, but `plans_response` is a closed schema without that field. cloud-api already
    reports the operator switch as `screen_view: "on" | "off"` in the public `GET /healthz`, whose body has
    no schema in `rest.schema.json`; `lib/api.ts::screenViewSwitch` reads that one string tolerantly and
    treats anything else as "unknown" (the "Coming" card stays, DECISIONS 68). Suggested (next MINOR):
    `plans_response.screen_view_enabled: boolean`, or a `health_response` definition.

13. **No step-down carrier.** §16's "Weak connection: lowering quality" row has no message in
    `screen_dc_pc_to_phone` (`state` carries `encoder` and `data_saver` only), so the phone cannot show it
    (KNOWN_ISSUES 30). Suggested: optional `state.quality: "full" | "reduced"`.

14. **`SCREEN_UNSUPPORTED_BROWSER` is client-only**, like `NETWORK` and `NO_ANSWER` (items 4 and 10): a
    browser without `RTCPeerConnection` fails every start honestly before anything is sent. `labels.ts`
    holds its copy.

15. **A consent back-off refusal is indistinguishable from a Deny.** Both are `SCREEN_CONSENT_DECLINED`
    without detail; §16 wants "Not allowed at the PC. Try again in a few minutes." after a refusal. The
    phone infers the back-off from timing (KNOWN_ISSUES 36). Suggested: `error.detail.reason:
    "denied" | "blocked" | "backoff"` (the agent's history already records these reasons).

16. **`consent: pending` is the phone's own state.** The contract's result is terminal (`consent:
    not_required | granted`), so "Waiting for someone at the PC to allow this… (30)" is shown when the
    PC acknowledged the start (`executing`) and 2 s pass without a result — a slow start without a
    window shows the line briefly too. The countdown runs from the acknowledgement, not from the window's
    own clock.

17. **`ACTION_UNAVAILABLE` with `detail.reason: "screen_view_not_included"`** (pc-agent CONTRACT_ISSUES
    #24): a PC build without the media stack refuses a start with it; the phone reads the reason (an
    additive detail `errors.json` does not list) to show the honest copy. Proposed: name the detail, or a
    `screen_view` capability flag in the agent's `hello`, at the next contract round.

18. **`screen_session{paused|resumed}` arrive per pause cause** (pc-agent CONTRACT_ISSUES #25): the phone
    tracks the causes as a set; `rules.screen_sessions` does not say whether pauses nest.

19. **`BILLING_PROVIDER_ERROR` with `detail.reason`** (cloud-api billing/provider.py `ProviderErrorReason`:
    `auth`, `bad_request`, `not_found`, `timeout`, `provider_error`): `errors.json` gives the code one message
    ("didn't answer… Try again in a moment"), which is wrong for the three reasons that mean DoMe's own payment
    setup is wrong. The phone reads the reason (an additive detail `errors.json` does not list) for its own
    copy (decision 78); an unknown or missing reason keeps the contract's message. Proposed: name the detail
    and its five values at the next contract round.
