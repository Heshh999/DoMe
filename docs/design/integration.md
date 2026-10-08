# Integration test design (`tests/`)

Purpose: prove the actual execution path end to end on this Linux environment with real
processes and real signatures — the only parts replaced are the Windows OS adapters and the
browser, each by an explicitly isolated test double.

```
PWA-equivalent test client (Python or Node, signs with ES256)
        │ HTTPS/WSS (loopback)
        ▼
cloud-api (uvicorn, real PostgreSQL on :54329, real OIDC login via tools/dev-idp)
        ▲ WSS (loopback)
        │
pc-agent (dome-agent run, DOME_AGENT_PLATFORM=fake, headless) ── bridge/server ──► fake extension
```

## Harness (`tests/conftest.py`)

- Session fixtures start, on free ports: `dome-dev-idp`; `dome-api` with a fresh database
  (`dome_it_<random>`, Alembic upgrade) and `DOME_PUBLIC_ORIGIN=http://127.0.0.1:<pwa-port>`;
  the agent (`dome-agent run`) with `DOME_AGENT_PLATFORM=fake`, `DOME_AGENT_HEADLESS=1`, a temp
  state dir, `DOME_AGENT_API_URL`/`DOME_AGENT_RELAY_URL` pointing at the API.
- A `Controller` helper: logs in through the dev IdP (`dev_user=`), reads `/v1/session`,
  generates an ES256 key with `dome_protocol`, pairs, opens `/ws/controller` with the session
  cookie + `Origin`, sends `hello{kid}`, subscribes, signs commands/confirmations and collects
  frames with timeouts. Two helpers = two accounts.
- A `FakeExtension` helper connects to the agent's bridge IPC socket (Unix socket in the temp
  state dir) speaking the bridge schema, advertises one browser instance with configurable tabs
  (video id, paused, tab_token, script_attached) and answers ops like the real extension would
  (next → new video id after a short delay; no next → NO_NEXT_VIDEO; ad showing →
  UNSUPPORTED_CONTEXT).
- Linking: the test drives `dome-agent link` non-interactively (`--no-browser` prints the
  `user_code`), approves it through the REST path as the signed-in user, then starts the agent.
- Pairing: the test drives `dome-agent pair --print-code` to get the code (stdout, test only),
  then claims from the controller with `pairing_code_handle(code)`, computes the verification
  code on both sides (agent prints it in headless mode), approves via `dome-agent pair-approve
  <pairing_id>` (headless approval path), and asserts the grant appears in REST and in a fresh
  `grants_snapshot` (visible through agent `status`).

## Scenarios (map to spec §17)

1. `youtube.next` in a background tab: controller → relay → agent → fake extension observes
   the transition → result `succeeded` with `previous_video_id` and the new tab state (§17.1–2).
2. `youtube.set_paused`, `seek_relative`, `windows.set_volume` (fake platform read-back),
   `youtube.list_tabs` with two tabs → `TARGET_REQUIRED`/explicit target works (§17.2).
3. Cross-account: account B cannot list, subscribe to (`GRANT_MISSING` with `ref_pc_id`), pair
   with (`PAIRING_CODE_INVALID`), or command (relay-origin `failed`) account A's PC (§17.5).
4. Pairing: expired code rejected; used code rejected; claim while agent offline is delivered
   on reconnect; approval on the PC is required (no grant before `pairing_decision`) (§17.6).
5. Revocation: revoke the controller via REST mid-session → socket receives `revoked` and
   closes; agent's next command from that kid is rejected; local disable (`dome-agent disable`)
   → commands fail `PC_REMOTE_DISABLED` until `enable` (§17.7).
6. Replay/duplicates: identical re-send returns the same result; same id with different bytes →
   `COMMAND_ID_REUSED`; expired command → `COMMAND_EXPIRED`; confirmation with a mismatched
   digest → `CONFIRMATION_INVALID`; changed target → `TARGET_CHANGED` (§17.8).
7. Disconnect after side effect: kill the agent between `executing` ack and result → relay
   emits `outcome_unknown`; restart → journal shows `outcome_unknown`, no re-execution; the
   fake platform records exactly one effect (§17.9).
8. Offline PC: command while agent down → `PC_OFFLINE` immediately, nothing queued; agent
   restart executes nothing (§17.10).
9. Volume burst: 40 `windows.set_volume` commands in 2 s → last value applied, superseded ones
   terminate `COMMAND_SUPERSEDED`, no `RATE_LIMITED` within the coalescable budget (§17.11).
10. Confirmed power (fake platform): `power.sleep` → `confirmation_required` → approve → result
    `accepted` with `fires_at`; `power.cancel` cancels; decline → `CONFIRMATION_DECLINED`; the fake
    platform records the call but performs nothing (§17 note on destructive tests).
11. Protocol negotiation: an agent announcing `protocol_versions: ["2.0"]` gets
    `PROTOCOL_INCOMPATIBLE`.
12. Load smoke: 50 agent sockets + 50 controller sockets, 500 commands, bounded memory check
    (reported as numbers, not as a pass/fail promise) (§17.17).

Every scenario asserts frames against `schemas.validate_frame` and results against
`registry.validate_result`. Evidence tag for all of the above: **integration-tested (Linux,
fake platform, fake extension)** — not Windows-device-tested, not iPhone-tested.
