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
8. **`input.session_start` availability lacks `windows`** (`actions.json` lists only
   `session_unlocked`). On a non-Windows host without the fake platform a session could start that can
   never inject. The agent's handler refuses `PLATFORM_UNSUPPORTED` itself. Proposal: add `windows` to
   the availability list.
9. **No `restarted` reason in `agent_input_session.reason`.** When the owning controller starts again
   (page reload, PC switch back) the agent ends the old session with `stopped`; `pc_switch` is listed
   but the agent cannot know the phone switched PCs (the PWA sends `input.session_stop` before
   switching). Proposal: either a `restarted` reason or a note that `stopped` covers it.
10. **`input_session{suspended}` and holds.** The rule says a suspension discards pending events and
    requires a fresh start but does not say what happens to held buttons; the frame requires
    `holds_released`. The agent releases them at suspension and reports the count. Proposal: state it.
11. **Batch rejections are `error` frames without a reference.** `error_frame` has only `ref_pc_id`;
    an agent → relay error for a dropped batch cannot name the session or the controller, so the relay
    must route it by the owning controller of the PC's live session (it has `pc_state.input_session`).
    The agent rate-limits these to one per code per second and puts counts in the acks. Proposal: an
    optional `ref_input_session_id` on `error_frame`.
12. **`grant_update.capabilities` has `minItems: 1`**, so a grant cannot be emptied through this frame;
    the agent refuses the local change ("revoke instead"). Fine, but worth a sentence in
    `rules.grant_update`.
13. **Provisional controllers and `grant_update`** (follow-up to #1): a capability change made before
    the first snapshot names the controller cannot be sent (no `controller_id`); the agent journals it
    by kid and sends it after the snapshot. Pre-allocating `controller_id` in `pairing_request` would
    remove this too.
14. **Elevated foreground is not an end reason.** `rules.input_sessions` ends a session on "a secure
    desktop"; an elevated window in front is a different, transient state (the customer can click
    elsewhere). The agent keeps the session, sets `pc_state.input_restricted` and answers
    `INPUT_RESTRICTED` per batch. Proposal: say so explicitly.
15. **Bridge protocol error detail vs the version list.** `test_bridge.py` was updated at the 1.1 bump to
    expect `supported == ["1.1"]`, but the agent announces every MINOR it speaks (`["1.0", "1.1"]`, also
    asserted by the relay hello test) as the compatibility rule intends. The test was corrected; the
    contract could state that `supported` lists all accepted versions.
