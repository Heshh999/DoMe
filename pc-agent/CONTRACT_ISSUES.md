# Contract issues found while building pc-agent (contract left unchanged)

1. **`pairing_request` has no `controller_id`.** `grants` rows are keyed by `controller_id` and the
   KeyRecord binding needs it, but the relay only assigns the id after `pairing_decision{approve}`.
   The design's "approve stores the grant so the agent accepts commands even before the next
   snapshot" therefore cannot hold: until the snapshot names the controller, a command from the new kid
   fails `CONTROLLER_MISMATCH`. Workaround: the grant is stored under a provisional id
   (`pending-<kid>`) that `apply_snapshot` rewrites by kid. Proposal: let the relay pre-allocate and
   include `controller_id` in `pairing_request` (the row is created on approval with that id).
2. **`power_result` wording vs `rules.in_flight`.** `power_result` says "the countdown was armed",
   while `rules.in_flight` exempts "armed power countdowns until fires_at + timeout_ms", which only
   makes sense if the command stays in flight during the countdown. The agent follows the rule (ack
   `executing`, result when the OS call is issued, `pending_power_action` in state frames for the
   countdown). Proposal: state explicitly in `actions.json` that the result follows the OS call and
   that cancellation ends the command as `canceled/POWER_CANCELED`.
3. **`agent_result` for a user-initiated `cancel` has no error code.** `COMMAND_SUPERSEDED` and
   `POWER_CANCELED` exist, but a plain queued/awaiting command canceled by the relay `cancel` frame has
   no dedicated code; the agent emits `canceled` without `error`. Proposal: add `COMMAND_CANCELED`.
4. **`cancel` for an executing command is undefined.** The agent ignores it (logged); a code such as
   `CANNOT_CANCEL` or a documented "ignored" rule would make the behaviour explicit.
5. **`EdDSA` algorithm name.** RFC 9864 deprecates the polymorphic `EdDSA` identifier in favour of
   `Ed25519`; `joserfc` warns when `EdDSA` is used. The agent keeps the contract value and silences
   only that warning during verification. Proposal: switch `plans.json → entitlement_assertion.algorithm`
   and the issuer header to `Ed25519` in a protocol MINOR bump once joserfc/cloud-api agree.
6. **`relay_to_agent_cancel.controller_id` vs provisional ids**: a cancel for a command whose grant
   was still provisional cannot match a journal row (the row stores the real controller id only after
   the snapshot). Harmless in practice because such commands are rejected before being journaled.
7. **Verification strategy `observe_video_transition` is executed by the extension**, but the agent
   also re-checks that `tab.video_id != previous_video_id` and fails with `NO_NEXT_VIDEO` /
   `NO_PREVIOUS_VIDEO` when the extension answered `ok:true` without a transition. The contract should
   say which side is authoritative so the extension and agent cannot disagree on a success.
