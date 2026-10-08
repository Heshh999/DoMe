"""Spec §17.17 — a small load smoke through the real relay with simulated agents and controllers.

This prints numbers; it is not a pass/fail performance promise. Evidence tag: load-tested (small,
loopback, simulated endpoints). The real-device latency targets in the spec (median < 500 ms,
p95 < 1.5 s on a healthy network) remain to be measured with real devices.
"""

from __future__ import annotations

import asyncio
import resource
import statistics
import time
from typing import Any

from conftest import AgentSim, Browser, ControllerSim, Env, make_account  # noqa: F401 - fixture re-export

PAIRS = 40
COMMANDS_PER_CONTROLLER = 10


async def _pair(env: Env, browser: Browser) -> tuple[AgentSim, ControllerSim]:
    agent = AgentSim(env)
    await agent.link(browser, name=f"PC {agent.kid[:6]}")
    await agent.connect()
    ctrl = ControllerSim(env, browser, name=f"Phone {agent.kid[:6]}")
    await ctrl.pair(agent, ("status", "media", "volume"))
    await ctrl.connect()
    await ctrl.subscribe(agent.pc_id)
    await ctrl.recv_type("pc_status", pc_id=agent.pc_id)
    return agent, ctrl


async def _serve(agent: AgentSim, count: int) -> None:
    for _ in range(count):
        await agent.serve_one()


async def _drive(ctrl: ControllerSim, pc_id: str, count: int, latencies: list[float]) -> None:
    for _ in range(count):
        t0 = time.perf_counter()
        cid = await ctrl.command(pc_id, "system.ping")
        res = await ctrl.recv_type("result", command_id=cid, timeout=30)
        latencies.append(time.perf_counter() - t0)
        assert res["state"] == "succeeded", res


async def test_relay_load_smoke(env: Env, make_account: Any) -> None:
    # Free allows one enabled PC and two controllers per account, so each pair gets its own account
    # (the plan limits are real product behaviour, not something the load test may bypass).
    accounts = [await make_account() for _ in range(PAIRS)]
    pairs: list[tuple[AgentSim, ControllerSim]] = []
    for i in range(PAIRS):
        pairs.append(await _pair(env, accounts[i]))

    latencies: list[float] = []
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    t0 = time.perf_counter()
    await asyncio.gather(
        *[_serve(agent, COMMANDS_PER_CONTROLLER) for agent, _ in pairs],
        *[_drive(ctrl, agent.pc_id, COMMANDS_PER_CONTROLLER, latencies) for agent, ctrl in pairs],
    )
    elapsed = time.perf_counter() - t0
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    total = PAIRS * COMMANDS_PER_CONTROLLER
    assert len(latencies) == total
    p50 = statistics.median(latencies)
    p95 = sorted(latencies)[int(len(latencies) * 0.95) - 1]
    print(
        f"\nLOAD SMOKE: {PAIRS} agents + {PAIRS} controllers (same process as the relay), {total} commands "
        f"in {elapsed:.2f}s = {total / elapsed:.0f} cmd/s; round-trip p50 {p50 * 1000:.0f} ms, p95 {p95 * 1000:.0f} ms; "
        f"max RSS {rss_before / 1024:.0f} → {rss_after / 1024:.0f} MiB (test process incl. API, sims and PostgreSQL client)"
    )
    for agent, ctrl in pairs:
        await ctrl.close()
        await agent.close()
