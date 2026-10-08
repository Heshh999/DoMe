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
uv run pytest -q                 # ~3–4 minutes
uv run pytest -q -k load         # the small load smoke only (prints numbers; never a pass/fail promise)
```

`DOME_TEST_DATABASE_URL` selects the PostgreSQL server (default `postgresql+psycopg://dome@/dome_test?host=/tmp&port=54329`);
the database name is replaced by a random `dome_test_<hex>` that is dropped afterwards. The agent's
state directory is a short temporary path because Unix socket paths are limited to ~104 bytes.

## Files

| File | Scenarios (numbers refer to spec §17 / `docs/design/integration.md`) |
| --- | --- |
| `conftest.py` | Re-uses cloud-api's harness (database, dev IdP, API, `Browser`, `ControllerSim`) and adds `RealAgent`: link via `dome-agent link --no-browser`, run, pair through the local control channel, bridge a `FakeExtension` |
| `test_e2e_youtube.py` | 1, 2 — Next in a background tab with an observed transition; pause/seek/volume; two tabs need an explicit target; player vs Windows volume |
| `test_e2e_security.py` | 3, 5, 6, 7, 8, 13 — cross-account isolation, pairing rules and offline claim delivery, revocation (account and local), replay/duplicate/expiry/target-changed/confirmation-digest rejections, client state cannot unlock Pro |
| `test_e2e_reliability.py` | 9, 10, 11, 14-ish — kill the agent mid-execution → `outcome_unknown` and no re-execution; offline PC never queues; volume burst coalesces within budget; confirmed power countdown + cancel on the fake platform |
| `test_load_smoke.py` | 17 — 40 simulated agents + 40 controllers, 400 commands; prints throughput/latency and RSS as numbers |
