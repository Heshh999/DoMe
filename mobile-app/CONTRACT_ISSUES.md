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
   Suggested fix: regenerate the fixture with a 22-character token.

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
