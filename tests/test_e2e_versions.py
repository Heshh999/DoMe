"""A phone ahead of its PC (rules.command_version_stamping, docs/PROTOCOL.md §18.2, §21): the PWA updates when the
cloud deploys, PCs later. The phone stamps each command with the lowest version that defines everything it carries
(its action's `since`, raised by any param a later MINOR added) and each confirmation with the protocol_version of
the command it confirms, so the real agent process with the version checks of a 1.1 release (older_agent.py) keeps
executing everything 1.1 defines, confirmed power actions included. Since 1.4 the relay refuses, before forwarding,
an input batch stamped with a MINOR the agent did not announce (a Screen view tap), and since 1.5 any command so
stamped (an action 1.2 added, sent to that 1.1 PC, comes back PROTOCOL_INCOMPATIBLE from the relay), which an older
agent would otherwise answer MALFORMED_MESSAGE or INVALID_PARAMETERS.

Protocol 1.5 with a 1.4 PC (agent_1_4.py: this tree's agent with the 1.4 release's version checks, on the fake
platform with screen_agent.py's seams): the relay shows the PC's announced versions in `GET /v1/pcs`, a start without
`cursor` (stamped 1.4) gets a 1.4 session and Next screen works, and a start with `cursor` (stamped 1.5) is refused by
the relay before the PC sees it. What agent_1_4.py does not witness is the shipped 1.4 agent's own consent order
(agent_1_4.py's docstring)."""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import test_e2e_screen as screen_e2e
from dome_protocol import loads_strict

from conftest import REGISTRY, Browser, Env, RealAgent, confirmation_envelope, run_command, sql

HERE = Path(__file__).resolve().parent
OLDER = "1.1"
VERSIONS_1_4 = ["1.0", "1.1", "1.2", "1.3", "1.4"]
# Screen view fixtures from test_e2e_screen.py (pytest finds fixtures by module attribute): the operator switch on
# with the loopback STUN server, and the phones a test pairs (closed afterwards)
stun_server = screen_e2e.stun_server
screen_on = screen_e2e.screen_on
phones = screen_e2e.phones


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


@pytest.fixture
async def agent14(env: Env, alice: Browser) -> AsyncIterator[RealAgent]:
    """A PC one MINOR behind a 1.5 phone: the real agent with the version checks of the 1.4 release and Screen view on
    the fake platform (agent_1_4.py)."""
    a = RealAgent(env, alice, command=[sys.executable, str(HERE / "agent_1_4.py")])
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
    # app.maximize is stamped 1.2, which the 1.1 PC never announced: since 1.5 the relay refuses it before forwarding
    # (rules.command_version_stamping), as it refuses a 1.4 tap; the PC never looks at the app.
    for action, params in (("app.maximize", {}), ("app.set_fullscreen", {"enabled": True})):
        res = await run_command(phone, agent.pc_id, action, params, {"app_id": "notepad"})
        assert res["origin"] == "relay" and res["state"] == "failed", (action, res)
        assert res["error"]["code"] == "PROTOCOL_INCOMPATIBLE"
        assert res["error"]["detail"] == {"peer": "agent", "required": "1.2"}, res
    # actions the older PC knows keep working from the same phone
    res = await run_command(phone, agent.pc_id, "app.list")
    assert res["state"] == "succeeded" and res["result"]["apps"] == [], res
    res = await run_command(phone, agent.pc_id, "system.ping")
    assert res["state"] == "succeeded" and res["result"]["protocol_version"] == OLDER, res


async def test_the_relay_refuses_a_screen_view_tap_to_an_agent_that_predates_it(agent: RealAgent, phone: Any) -> None:
    """Protocol 1.4 (rules.command_version_stamping, PROTOCOL.md §20): a Screen view tap is an input batch with
    `pointer_move_to`, stamped 1.4. An agent from before 1.4 would answer it MALFORMED_MESSAGE, so the relay's stamp
    gate refuses it with PROTOCOL_INCOMPATIBLE {peer: agent, required: 1.4} and never forwards it (an agent-origin
    PROTOCOL_INCOMPATIBLE is never routed to a phone); the same phone's plain touchpad batches, stamped 1.1, keep
    reaching the PC in the same session."""
    res = await agent.control("grant", controller_id=phone.controller_id, add=["pointer"])
    assert "pointer" in res["capabilities"]
    deadline = time.monotonic() + 15
    while True:
        grants = await phone.browser.get(f"/v1/pcs/{agent.pc_id}/grants")
        if any(g["controller_id"] == phone.controller_id and "pointer" in g["capabilities"] for g in grants["grants"]):
            break
        assert time.monotonic() < deadline, "the pointer grant never reached the relay"
        await asyncio.sleep(0.2)
    res = await run_command(phone, agent.pc_id, "input.session_start", {})
    assert res["state"] == "succeeded" and res["result"]["pointer"] is True, res
    sid = res["result"]["input_session_id"]

    tap = [
        {"type": "pointer_move_to", "x": 32768, "y": 32768, "gen": 1},
        {"type": "pointer_button", "button": "left", "action": "click"},
    ]
    sent = await phone.input_batch(agent.pc_id, sid, 1, tap)
    assert stamp(sent) == "1.4"
    err = await phone.recv_type("error", timeout=10)
    assert err["error"]["code"] == "PROTOCOL_INCOMPATIBLE", err
    assert err["error"]["detail"] == {"peer": "agent", "required": "1.4"} and err["ref_pc_id"] == agent.pc_id, err

    sent = await phone.input_batch(agent.pc_id, sid, 2, [{"type": "pointer_move", "dx": 3, "dy": 4}])
    assert stamp(sent) == "1.1"
    while True:
        ack = await phone.recv_type("input_ack", timeout=10, input_session_id=sid)
        if ack["last_seq"] >= 2:
            break
    assert ack["accepted_events"] == 1 and ack["dropped_events"] == 0, ack  # the tap's events never reached the PC


# ----- protocol 1.5 phone, 1.4 PC (design 2a §5.5) --------------------------------------------------------------------
async def test_a_1_4_agent_with_a_1_5_phone_starts_screen_view_without_the_cursor_param(
    env: Env, alice: Browser, agent14: RealAgent, screen_on: Any, phones: list[Any]
) -> None:
    """The relay records the versions the PC announced (`GET /v1/pcs` `protocol_versions`), so a 1.5 phone knows this
    PC predates the pointer drawn by the phone and starts Screen view without `cursor`: stamped 1.4, answered with an
    answer stamped 1.4 and a 1.4 session (no 1.5 key or message). Next screen is `surface: N`, which 1.4 defines: the
    phone's start for screen 2 replaces the session (`replaced`, `gen: 1`). (Here nobody is asked at the PC: in ask
    mode or the post-Stop cooldown the shipped 1.4 agent asks before it ends the old session, which this agent with
    1.4 constants does not witness; docs/PROTOCOL.md section 21.)"""
    assert REGISTRY.protocol_version == "1.5"  # the phone and the relay
    assert (await agent14.control("status"))["protocol_version"] == "1.4"
    phone = await screen_e2e.pair_phone(env, alice, agent14, phones, "Alice's iPhone")
    await screen_e2e.allow_screen_on_the_pc(agent14, phone)
    pcs = (await alice.get("/v1/pcs", schema="pcs_response"))["pcs"]
    row = next(p for p in pcs if p["id"] == agent14.pc_id)
    assert row["protocol_versions"] == VERSIONS_1_4, row
    assert "1.5" not in row["protocol_versions"]  # so the phone leaves `cursor` out
    await agent14.control("test_set_monitors", monitors=screen_e2e.TWO_MONITORS)

    first = await screen_e2e.stream(phone)
    assert first.stamp == "1.4" and first.result["origin"] == "agent", first.result
    assert first.answer is not None and first.answer.payload["protocol_version"] == "1.4"
    session = first.result["result"]
    assert "cursor" not in session and len(session["surfaces"]) == 2, session
    assert (session["surface_index"], session["gen"]) == (0, 1), session
    viewer = first.viewer
    await viewer.wait(lambda: bool(viewer.dc_of("geometry")) and bool(viewer.dc_of("state")), 10, "geometry + state")
    await asyncio.sleep(1.0)
    assert {m["type"] for m in viewer.dc_messages} <= {"geometry", "state", "allowance", "restricted"}
    assert all("cursor" not in m for m in viewer.dc_of("state")), viewer.dc_of("state")

    second = await screen_e2e.stream(phone, surface=1)
    assert second.stamp == "1.4" and second.answer is not None
    assert second.answer.payload["protocol_version"] == "1.4"
    assert (second.result["result"]["surface_index"], second.result["result"]["gen"]) == (1, 1), second.result
    ended = await screen_e2e.ended_frame(phone, first.session_id)
    assert ended["reason"] == "replaced", ended
    viewer = second.viewer
    await viewer.wait(lambda: bool(viewer.dc_of("geometry")), 10, "geometry")
    assert viewer.dc_of("geometry")[0]["surface_index"] == 1
    assert {m["type"] for m in viewer.dc_messages} <= {"geometry", "state", "allowance", "restricted"}
    res = await phone.log.command(agent14.pc_id, "screen.session_stop", {"screen_session_id": second.session_id})
    assert res["state"] == "succeeded", res


async def test_a_1_5_stamped_start_to_a_1_4_agent_is_refused_before_forwarding(
    env: Env, alice: Browser, agent14: RealAgent, screen_on: Any, phones: list[Any]
) -> None:
    """A phone whose knowledge of the PC was stale sends `cursor: overlay`, so its start is stamped 1.5. The relay's
    stamp gate refuses it before forwarding (result origin relay, PROTOCOL_INCOMPATIBLE {peer: agent, required:
    1.5}): the 1.4 PC never sees it (no consent window, no bar, no session), the ledger reserves nothing. The same
    phone's start without `cursor` then works, which is what the app retries with."""
    phone = await screen_e2e.pair_phone(env, alice, agent14, phones, "Alice's iPhone")
    await screen_e2e.allow_screen_on_the_pc(agent14, phone)
    ui_before = await agent14.control("test_ui")
    started_before = (await agent14.control("status"))["screen"]["sessions_started"]

    refused = await phone.client.start(cursor="overlay")
    assert refused.stamp == "1.5"
    result = refused.result
    assert (result["origin"], result["state"]) == ("relay", "failed"), result
    assert result["error"]["code"] == "PROTOCOL_INCOMPATIBLE", result
    assert result["error"]["detail"] == {"peer": "agent", "required": "1.5"}, result
    rows = sql(
        env.database_url,
        "SELECT count(*) FROM screen_reservations WHERE command_id = %s",
        (uuid.UUID(refused.command_id),),
    )
    assert rows[0][0] == 0, rows  # nothing reserved for it
    await asyncio.sleep(0.5)
    ui = await agent14.control("test_ui")
    assert ui["consent_windows"] == ui_before["consent_windows"] and ui["bar_requests"] == ui_before["bar_requests"]
    status = (await agent14.control("status"))["screen"]
    assert status["session"] is None and status["sessions_started"] == started_before, status
    assert not phone.log.unread("screen_answer")

    again = await screen_e2e.stream(phone)
    assert again.stamp == "1.4" and again.answer is not None
    assert again.answer.payload["protocol_version"] == "1.4" and "cursor" not in again.result["result"]
    res = await phone.log.command(agent14.pc_id, "screen.session_stop", {"screen_session_id": again.session_id})
    assert res["state"] == "succeeded", res
