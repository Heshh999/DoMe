"""A phone one protocol MINOR ahead of its PC (rules.command_version_stamping, docs/PROTOCOL.md §18.2): the PWA
updates when the cloud deploys, PCs later. A 1.2 phone stamps each command with its action's `since` and each
confirmation with the protocol_version of the command it confirms, so the real agent process with the version
checks of a 1.1 release (older_agent.py) keeps executing everything 1.1 defines, confirmed power actions included,
through the 1.2 relay, and answers an action 1.2 added with PROTOCOL_INCOMPATIBLE."""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from dome_protocol import loads_strict

from conftest import REGISTRY, Browser, Env, RealAgent, confirmation_envelope, run_command

HERE = Path(__file__).resolve().parent
OLDER = "1.1"


@pytest.fixture
async def agent(env: Env, alice: Browser) -> AsyncIterator[RealAgent]:
    """This module's PC: the real agent with the version checks of the 1.1 release (older_agent.py)."""
    a = RealAgent(env, alice, command=[sys.executable, str(HERE / "older_agent.py")])
    await a.link()
    await a.start()
    try:
        yield a
    finally:
        await a.stop()
        a.cleanup()


def stamp(envelope: dict[str, Any]) -> str:
    """The protocol_version a signed envelope's payload carries."""
    return str(loads_strict(envelope["payload"])["protocol_version"])


async def wait_security_event(agent: RealAgent, kind: str, timeout: float = 10) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        store = agent.store()
        try:
            events = [e for e in store.list_security_events() if e["kind"] == kind]
        finally:
            store.close()
        if events:
            return events[0]
        await asyncio.sleep(0.1)
    raise TimeoutError(f"the agent recorded no {kind} security event")


async def test_an_agent_one_minor_behind_runs_a_confirmed_power_action_from_a_newer_phone(
    agent: RealAgent, phone: Any
) -> None:
    assert REGISTRY.protocol_version != OLDER  # the phone and the relay are the newer side
    status = await agent.control("status")
    assert status["protocol_version"] == OLDER

    cid, command = phone.envelope(agent.pc_id, "power.sleep", {"countdown_seconds": 2}, lifetime=90)
    assert stamp(command) == "1.0"  # power.sleep's `since`
    await phone.send_envelope(agent.pc_id, command)
    req = await phone.recv_type("confirmation_required", command_id=cid, timeout=10)

    # Stamped with the phone's own version, the confirmation is refused by the older PC (its MINOR is too
    # high) and the challenge stays open: nothing ran, nothing was consumed.
    too_new = confirmation_envelope(
        phone, agent.pc_id, cid, req["challenge_text"], protocol_version=REGISTRY.protocol_version
    )
    await phone.send_envelope(agent.pc_id, too_new, kind="confirmation")
    refused = await wait_security_event(agent, "confirmation_rejected")
    assert refused["detail"] == {"code": "PROTOCOL_INCOMPATIBLE"}
    assert (await agent.control("status"))["pending_power"] is None

    # What the phone actually sends carries the confirmed command's version, and the PC runs it.
    confirmation = confirmation_envelope(phone, agent.pc_id, cid, req["challenge_text"])
    assert stamp(confirmation) == "1.0"
    await phone.send_envelope(agent.pc_id, confirmation, kind="confirmation")
    res = await phone.recv_type("result", command_id=cid, timeout=15)
    assert res["origin"] == "agent" and res["state"] == "succeeded", res
    REGISTRY.validate_result("power.sleep", res["result"])
    assert res["result"]["accepted"] is True


async def test_an_agent_one_minor_behind_refuses_only_the_actions_it_predates(agent: RealAgent, phone: Any) -> None:
    # The relay (1.2) forwards app.maximize stamped 1.2; the PC refuses it before looking at the app.
    for action, params in (("app.maximize", {}), ("app.set_fullscreen", {"enabled": True})):
        res = await run_command(phone, agent.pc_id, action, params, {"app_id": "notepad"})
        assert res["origin"] == "agent" and res["state"] == "failed", (action, res)
        assert res["error"]["code"] == "PROTOCOL_INCOMPATIBLE"
    # actions the older PC knows keep working from the same phone
    res = await run_command(phone, agent.pc_id, "app.list")
    assert res["state"] == "succeeded" and res["result"]["apps"] == [], res
    res = await run_command(phone, agent.pc_id, "system.ping")
    assert res["state"] == "succeeded" and res["result"]["protocol_version"] == OLDER, res
