"""Confirmation transactions through the real agent frame path (relay → agent)."""

from __future__ import annotations

import asyncio
from typing import Any

from dome_protocol import challenge_digest, load_schemas, loads_strict

from .conftest import AgentHarness
from .helpers import Controller, payload_of


async def start_confirmation(h: AgentHarness, controller: Controller, action: str = "power.sleep", params: dict[str, Any] | None = None, target: dict[str, Any] | None = None) -> tuple[str, str]:
    env = controller.command(action, params if params is not None else {"countdown_seconds": 0}, target, lifetime=90)
    cid = payload_of(env)["command_id"]
    await h.send_command(env)
    ack = await h.ack(cid)
    assert ack["state"] == "awaiting_confirmation"
    req = await h.relay.expect("confirmation_required", command_id=cid)
    text = req["challenge_text"]
    challenge = load_schemas().validate_challenge_text(text)  # the relay/phone apply the same strict parse
    assert challenge["command_id"] == cid and challenge["action"] == action and challenge["pc_id"] == controller.pc_id
    assert challenge["display"]["pc_name"] == "Test PC"
    return cid, text


async def test_happy_path_executes_once(harness: AgentHarness, controller: Controller) -> None:
    cid, text = await start_confirmation(harness, controller)
    await harness.send_confirmation(controller.confirmation(cid, text))
    acks = [await harness.ack(cid), await harness.ack(cid)]
    assert [a["state"] for a in acks] == ["accepted", "executing"]
    res = await harness.result(cid)
    assert res["state"] == "succeeded" and res["result"]["accepted"] is True
    assert harness.fake.count("power_sleep") == 1
    # replaying the same confirmation cannot run it twice: the challenge is consumed
    before = len(harness.relay.drain())
    await harness.send_confirmation(controller.confirmation(cid, text))
    err = await harness.relay.expect("error")
    assert err["error"]["code"] == "CONFIRMATION_INVALID"
    assert harness.fake.count("power_sleep") == 1 and before >= 0


async def test_digest_mismatch(harness: AgentHarness, controller: Controller) -> None:
    cid, text = await start_confirmation(harness, controller)
    tampered = text.replace('"countdown_seconds":0', '"countdown_seconds":1')
    await harness.send_confirmation(controller.confirmation(cid, text, digest=challenge_digest(tampered)))
    res = await harness.result(cid)
    assert res["state"] == "failed" and res["error"]["code"] == "CONFIRMATION_INVALID"
    assert harness.fake.count("power_sleep") == 0
    assert harness.agent.store.journal_get(cid).state == "failed"  # type: ignore[union-attr]


async def test_declined(harness: AgentHarness, controller: Controller) -> None:
    cid, text = await start_confirmation(harness, controller)
    await harness.send_confirmation(controller.confirmation(cid, text, decision="decline"))
    res = await harness.result(cid)
    assert res["state"] == "canceled" and res["error"]["code"] == "CONFIRMATION_DECLINED"
    assert harness.fake.count("power_sleep") == 0


async def test_expired_challenge(harness: AgentHarness, controller: Controller) -> None:
    from datetime import datetime, timedelta

    from dome_protocol import now_utc

    cid, text = await start_confirmation(harness, controller)
    # move the agent's confirmation clock forward past the 60 s challenge lifetime
    harness.agent.confirmations._clock = lambda: now_utc() + timedelta(seconds=120)  # type: ignore[assignment]  # noqa: SLF001
    await harness.send_confirmation(controller.confirmation(cid, text))
    res = await harness.result(cid)
    assert res["state"] == "expired" and res["error"]["code"] == "CONFIRMATION_EXPIRED"
    assert harness.fake.count("power_sleep") == 0
    assert isinstance(datetime.now(), datetime)


async def test_sweeper_expires_unanswered_challenge(harness: AgentHarness, controller: Controller) -> None:
    import dome_agent.agent as agent_mod

    cid, _text = await start_confirmation(harness, controller)
    from datetime import timedelta

    from dome_protocol import now_utc

    harness.agent.confirmations._clock = lambda: now_utc() + timedelta(seconds=120)  # type: ignore[assignment]  # noqa: SLF001
    res = await harness.result(cid, timeout=agent_mod.CONFIRMATION_SWEEP_SECONDS + 5)
    assert res["state"] == "expired" and res["error"]["code"] == "CONFIRMATION_EXPIRED"


async def test_wrong_kid(harness: AgentHarness, controller: Controller, fake_api: Any) -> None:
    other = Controller(fake_api.account_id, fake_api.pc_id, display_name="Other phone")
    other.grant_locally(harness.agent.store, snapshot_id=harness.agent.store.current_snapshot_id())
    harness.relay.controllers.append(other.snapshot_entry())
    await harness.relay.send_snapshot()
    await harness.relay.expect("state")
    cid, text = await start_confirmation(harness, controller)
    # a different paired controller signs a confirmation for this command (its own controller_id)
    await harness.send_confirmation(other.confirmation(cid, text))
    res = await harness.result(cid)
    assert res["state"] == "failed" and res["error"]["code"] == "CONFIRMATION_INVALID"
    assert harness.fake.count("power_sleep") == 0


async def test_unverifiable_confirmation_does_not_terminate(harness: AgentHarness, controller: Controller, fake_api: Any) -> None:
    stranger = Controller(fake_api.account_id, fake_api.pc_id)
    cid, text = await start_confirmation(harness, controller)
    env = controller.confirmation(cid, text)
    env["kid"] = stranger.kid  # unknown key
    await harness.send_confirmation(env)
    err = await harness.relay.expect("error")
    assert err["error"]["code"] == "UNKNOWN_KEY"
    assert harness.agent.store.journal_get(cid).state == "awaiting_confirmation"  # type: ignore[union-attr]
    # the real controller can still approve
    await harness.send_confirmation(controller.confirmation(cid, text))
    res = await harness.result(cid)
    assert res["state"] == "succeeded"


async def test_target_changed_before_approval(harness: AgentHarness, controller: Controller, tmp_path: Any) -> None:
    exe = tmp_path / "apps" / "note.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    harness.agent.apps._temp_dirs = ()  # noqa: SLF001
    harness.agent.apps.approve("note", str(exe))
    harness.fake.processes[str(exe.resolve())] = [500]
    harness.fake.add_window(500, "4242", "Untitled - Notepad")
    cid, text = await start_confirmation(harness, controller, "app.close", {}, {"app_id": "note", "window_id": "4242"})
    challenge = loads_strict(text)
    assert challenge["target"] == {"app_id": "note", "window_id": "4242"} and challenge["display"]["detail"] == "Close note"
    # the window title changes (e.g. the document was edited) before the user approves
    harness.fake.add_window(500, "4242", "*Untitled - Notepad")
    await harness.send_confirmation(controller.confirmation(cid, text))
    res = await harness.result(cid)
    assert res["state"] == "failed" and res["error"]["code"] == "TARGET_CHANGED"
    assert harness.fake.count("request_close") == 0


async def test_app_close_happy_and_refused(harness: AgentHarness, controller: Controller, tmp_path: Any) -> None:
    exe = tmp_path / "apps" / "note.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    harness.agent.apps._temp_dirs = ()  # noqa: SLF001
    harness.agent.apps.approve("note", str(exe))
    harness.fake.processes[str(exe.resolve())] = [500]
    harness.fake.add_window(500, "4242", "Doc - Notepad")
    env = controller.command("app.close", {}, {"app_id": "note", "window_id": "4242"}, lifetime=90)
    cid = payload_of(env)["command_id"]
    await harness.send_command(env)
    req = await harness.relay.expect("confirmation_required", command_id=cid)
    await harness.send_confirmation(controller.confirmation(cid, req["challenge_text"]))
    res = await harness.result(cid)
    assert res["state"] == "succeeded" and res["result"] == {"app_id": "note", "window_id": "4242", "closed": True}
    assert "4242" not in harness.fake.windows
    # refused close (unsaved-work dialog): never force-killed
    harness.fake.processes[str(exe.resolve())] = [501]
    harness.fake.add_window(501, "4243", "Doc2 - Notepad")
    harness.fake.refuse_close = True
    from dome_protocol import load_registry

    spec = load_registry().get("app.close")
    object.__setattr__(spec, "timeout_ms", 1500)
    try:
        env = controller.command("app.close", {}, {"app_id": "note", "window_id": "4243"}, lifetime=90)
        cid = payload_of(env)["command_id"]
        await harness.send_command(env)
        req = await harness.relay.expect("confirmation_required", command_id=cid)
        await harness.send_confirmation(controller.confirmation(cid, req["challenge_text"]))
        res = await harness.result(cid)
    finally:
        object.__setattr__(spec, "timeout_ms", 15000)
    assert res["state"] == "failed" and res["error"]["code"] == "CLOSE_REFUSED" and res["result"]["closed"] is False
    assert "4243" in harness.fake.windows
    await asyncio.sleep(0)
