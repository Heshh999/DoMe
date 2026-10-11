# tests — cross-component integration

These tests prove the actual execution path on this Linux environment with real processes and real
signatures. Only the Windows OS adapters and the browser are replaced, each by an explicitly isolated
test double that ships inside `pc-agent/dome_agent/testing/` and is selectable only by
`DOME_AGENT_PLATFORM=fake` / a test connecting to the bridge socket.

```
Python-signed controller (ES256, the phone)          Browser(session) — real OIDC login via tools/dev-idp
        │ HTTP + WebSocket (loopback)                        │
        ▼                                                    ▼
cloud-api under uvicorn in-process (real PostgreSQL, fresh database per session, Alembic)
        ▲ WebSocket (loopback)
        │
dome-agent  — the REAL agent process (`dome-agent run --headless`, DOME_AGENT_PLATFORM=fake)
        ▲ unix socket <state dir>/bridge.sock
        │
FakeExtension — answers bridge ops the way the real extension does (tab_token, transitions, contexts)
```

Evidence tag for everything here: **integration-tested (Linux, fake platform, fake extension)** —
not Windows-device-tested, not iPhone-tested. See `docs/ACCEPTANCE.md` for the scenario matrix.

## Run

```sh
# once: PostgreSQL on /tmp:54329 (make db-start db-create from the root), tools/dev-idp venv
cd tests
uv venv --python 3.12 && uv sync
uv run pytest -q                 # ~5.5 minutes; last full run: 30 passed in 319 s
uv run pytest -q -k load         # the small load smoke only (prints numbers; never a pass/fail promise)
```

`DOME_TEST_DATABASE_URL` selects the PostgreSQL server (default `postgresql+psycopg://dome@/dome_test?host=/tmp&port=54329`);
the database name is replaced by a random `dome_test_<hex>` that is dropped afterwards. The agent's
state directory is a short temporary path because Unix socket paths are limited to ~104 bytes.

## Files

| File | Scenarios (numbers refer to spec §17 / `docs/design/integration.md`) |
| --- | --- |
| `conftest.py` | Re-uses cloud-api's harness (database, dev IdP, API, `Browser`, `ControllerSim`) and adds `RealAgent`: link via `dome-agent link --no-browser`, run, pair through the local control channel, bridge a `FakeExtension`. Commands (`ControllerSim`) and confirmations (`confirmation_envelope`) are stamped like the phone's: the action's `since`, and the confirmed command's version |
| `test_e2e_youtube.py` | 1, 2 — Next in a background tab with an observed transition; pause/seek/volume; two tabs need an explicit target; player vs Windows volume. Protocol 1.2: YouTube search in a new tab and in the selected tab, then "play the second video" on the results tab the search returned (new tab_token, targetable before the extension re-announces its tabs); the query reaches the extension and never the phone's state, the relay's database or any agent file/log, even from an extension that reports it as the page title; an extension that negotiated 1.0/1.1 gets `EXTENSION_UPDATE_REQUIRED` with nothing written to it while its older controls keep working |
| `test_e2e_windows.py` | Protocol 1.2 window actions on the fake platform — app.maximize / app.restore / app.set_fullscreen never start an approved app (`APP_NOT_RUNNING`), then act on the window of the launched app and return the state read back; F11 full screen only in an app that uses it (Notepad: `FULLSCREEN_UNSUPPORTED`, nothing pressed), and a window minimised in full screen is not toggled out of it; an explicit window id must be the app's; a 1.2 action stamped 1.1 is refused by the relay (`PROTOCOL_INCOMPATIBLE`, never forwarded) while 1.1 actions keep working with that stamp; a browser window's title (after a search: the query) never leaves the PC — app.list names it "Microsoft Edge", `pc_state.foreground_app` has no title, no file on the PC holds it or another window's title. Runs the agent through `fake_window_agent.py` (the real CLI, where a launched fake app shows one window and comes to the front) |
| `test_e2e_versions.py` | Protocol 1.2 phone, PC one MINOR behind (rules.command_version_stamping, `docs/PROTOCOL.md` §18.2/§18.7) — the real agent with the 1.1 release's version checks (`older_agent.py`: announces 1.0/1.1, verifies against 1.1) runs a confirmed `power.sleep` whose command and confirmation the phone stamped 1.0; the same confirmation stamped 1.2 is refused (`PROTOCOL_INCOMPATIBLE`) and leaves the challenge open; `app.maximize` / `app.set_fullscreen` get `PROTOCOL_INCOMPATIBLE` from the agent while `app.list` and `system.ping` keep working |
| `test_e2e_security.py` | 3, 5, 6, 7, 8, 13 — cross-account isolation, pairing rules and offline claim delivery, revocation (account and local), replay/duplicate/expiry/target-changed/confirmation-digest rejections, client state cannot unlock Pro |
| `test_e2e_reliability.py` | 9, 10, 11, 14-ish — kill the agent mid-execution → `outcome_unknown` and no re-execution; offline PC never queues; volume burst coalesces within budget; confirmed power countdown + cancel on the fake platform |
| `test_e2e_input.py` | 18–20 (manual input, spec §10A) — the PC owner grants touchpad/keyboard locally (`grant_update` reaches the relay and comes back in the snapshot); a signed batch with motion, a held button, literal text and a key is accepted in order; a replayed `seq` and a stale batch are refused and the reason reaches the phone with the session it named; the PC-side lease expires on its own and releases the held button; retired sessions are refused; one owner per PC with explicit takeover; local revocation ends the session; a keyboard-only grant cannot move the pointer; typed text never appears in the relay's command rows or in any file the agent wrote |
| `test_load_smoke.py` | 17 — 40 simulated agents + 40 controllers, 400 commands; prints throughput/latency and RSS as numbers. Last run on the shared 4-CPU build machine: 400 commands in 13.47 s = 30 cmd/s, round trip p50 1314 ms / p95 1629 ms, RSS 215 → 218 MiB (loopback, every endpoint in the test process; numbers, not a promise) |
