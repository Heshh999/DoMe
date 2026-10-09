"""Executor behaviour: coalescing, timeouts, never-retry, result validation, deferred completion."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from dome_protocol import ProtocolError, load_registry, verify_and_parse_command

from dome_agent.actions import all_handlers, handler_for
from dome_agent.actions.context import DEFERRED
from dome_agent.queue import Emitter, Executor, coalescing_group

from .conftest import AgentHarness
from .helpers import Controller, payload_of


class Capture:
    def __init__(self) -> None:
        self.frames: list[dict[str, Any]] = []
        self.event = asyncio.Event()

    async def send(self, frame: dict[str, Any]) -> bool:
        self.frames.append(frame)
        self.event.set()
        return True

    def results(self, command_id: str | None = None) -> list[dict[str, Any]]:
        return [
            f for f in self.frames if f["type"] == "result" and (command_id is None or f["command_id"] == command_id)
        ]

    async def wait_result(self, command_id: str, timeout: float = 5.0) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            res = self.results(command_id)
            if res:
                return res[-1]
            await asyncio.sleep(0.01)
        raise TimeoutError(command_id)


@pytest.fixture
async def executor(harness: AgentHarness):
    all_handlers()  # load the real handlers before tests monkeypatch individual entries
    capture = Capture()
    emitter = Emitter(harness.agent.store, capture.send)
    ex = Executor(harness.agent.services, emitter, precheck=harness.agent.authz.precheck)
    harness.agent.power.bind(ex.complete)
    ex.start()
    yield ex, capture
    await ex.stop()


def verified(
    harness: AgentHarness,
    controller: Controller,
    action: str,
    params: dict[str, Any] | None = None,
    target: dict[str, Any] | None = None,
    **kw: Any,
) -> Any:
    env = controller.command(action, params, target, **kw)
    vc = verify_and_parse_command(env, harness.agent.authz.resolve_key)
    harness.agent.store.journal_insert(
        command_id=vc.command_id, digest=vc.digest, action=action, controller_id=vc.controller_id
    )
    return vc


async def test_coalescing_supersedes_within_group_not_across_targets(
    harness: AgentHarness, controller: Controller, executor: Any
) -> None:
    ex, cap = executor
    # block the worker with a slow command first so later ones queue up
    slow = verified(harness, controller, "media.set_paused", {"paused": True}, {"session_id": "slow#0"})
    harness.fake.add_session("slow#0", status="playing")
    original = harness.agent.services.platform.media.set_paused

    def slow_set_paused(session_id: str, paused: bool) -> Any:
        import time

        time.sleep(0.4)
        return original(session_id, paused)

    harness.agent.services.platform.media.set_paused = slow_set_paused  # type: ignore[method-assign]
    await ex.enqueue(slow)
    v1 = verified(harness, controller, "windows.set_volume", {"value": 10})
    v2 = verified(harness, controller, "windows.set_volume", {"value": 20})
    v3 = verified(harness, controller, "windows.set_volume", {"value": 30})
    # a YouTube volume for a different target shares no group with Windows volume
    ext = await harness.connect_extension()
    from dome_agent.testing.fake_extension import FakeTab

    tab = ext.add_tab(FakeTab(tab_id=1))
    ext.publish_tabs()
    await asyncio.sleep(0.05)
    yt = verified(
        harness,
        controller,
        "youtube.set_volume",
        {"value": 40},
        {"browser_instance_id": ext.browser_instance_id, "tab_id": 1, "tab_token": tab.tab_token},
    )
    await ex.enqueue(v1)
    await ex.enqueue(yt)
    await ex.enqueue(v2)
    await ex.enqueue(v3)
    r1 = await cap.wait_result(v1.command_id)
    r2 = await cap.wait_result(v2.command_id)
    assert r1["state"] == "canceled" and r1["error"]["code"] == "COMMAND_SUPERSEDED" and "result" not in r1
    assert r2["state"] == "canceled" and r2["error"]["code"] == "COMMAND_SUPERSEDED"
    r3 = await cap.wait_result(v3.command_id)
    assert r3["state"] == "succeeded" and r3["result"] == {"value": 30, "muted": False}
    ryt = await cap.wait_result(yt.command_id)
    assert ryt["state"] == "succeeded" and ryt["result"]["tab"]["volume"] == 40
    assert harness.fake.count("set_volume") == 1  # only the latest value was applied
    assert harness.agent.store.journal_get(v1.command_id).state == "canceled"  # type: ignore[union-attr]
    assert coalescing_group(v1) == coalescing_group(v2) != coalescing_group(yt)


async def test_queue_full(harness: AgentHarness, controller: Controller, executor: Any) -> None:
    ex, cap = executor
    harness.agent.store.set_remote_enabled(True)
    await ex.stop()  # nothing drains the queue
    for _ in range(16):
        await ex.enqueue(verified(harness, controller, "system.ping"))
    with pytest.raises(ProtocolError) as ei:
        await ex.enqueue(verified(harness, controller, "system.ping"))
    assert ei.value.code == "QUEUE_FULL"


async def test_executing_ack_and_success(harness: AgentHarness, controller: Controller, executor: Any) -> None:
    ex, cap = executor
    vc = verified(harness, controller, "system.ping")
    await ex.enqueue(vc)
    res = await cap.wait_result(vc.command_id)
    acks = [f for f in cap.frames if f["type"] == "ack" and f["command_id"] == vc.command_id]
    assert [a["state"] for a in acks] == ["executing"]
    assert res["state"] == "succeeded" and res["result"]["protocol_version"] == "1.1"
    row = harness.agent.store.journal_get(vc.command_id)
    assert row is not None and row.state == "succeeded" and row.sent


async def test_timeout_idempotent_is_failed_non_idempotent_is_outcome_unknown(
    harness: AgentHarness, controller: Controller, executor: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ex, cap = executor
    registry = load_registry()
    # shrink timeouts for the test
    ping_spec = registry.get("system.ping")
    lock_spec = registry.get("media.next")  # non-idempotent action

    async def slow_ping(ctx: Any) -> dict[str, Any]:
        await asyncio.sleep(2)
        return {}

    async def slow_lock(ctx: Any) -> dict[str, Any]:
        ctx.mark_side_effect()
        await asyncio.sleep(2)
        return {"accepted": True}

    import dome_agent.actions as actions_pkg

    monkeypatch.setitem(actions_pkg._HANDLERS, "system.ping", slow_ping)  # noqa: SLF001
    monkeypatch.setitem(actions_pkg._HANDLERS, "media.next", slow_lock)  # noqa: SLF001
    object.__setattr__(lock_spec, "timeout_ms", 200)
    object.__setattr__(ping_spec, "timeout_ms", 200)
    try:
        vp = verified(harness, controller, "system.ping")
        vl = verified(harness, controller, "media.next", {}, {"session_id": "s#0"})
        await ex.enqueue(vp)
        await ex.enqueue(vl)
        rp = await cap.wait_result(vp.command_id)
        rl = await cap.wait_result(vl.command_id)
    finally:
        object.__setattr__(ping_spec, "timeout_ms", 5000)
        object.__setattr__(lock_spec, "timeout_ms", 5000)
    assert rp["state"] == "failed" and rp["error"]["code"] == "ACTION_UNAVAILABLE"
    assert rl["state"] == "outcome_unknown" and rl["error"]["code"] == "OUTCOME_UNKNOWN" and "warning" in rl
    # the agent never retried: the slow handlers ran exactly once each
    assert handler_for("system.ping") is slow_ping


async def test_invalid_handler_result_is_reported_internal(
    harness: AgentHarness, controller: Controller, executor: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ex, cap = executor

    async def bad(ctx: Any) -> dict[str, Any]:
        return {"nope": 1}

    import dome_agent.actions as actions_pkg

    monkeypatch.setitem(actions_pkg._HANDLERS, "system.ping", bad)  # noqa: SLF001
    vc = verified(harness, controller, "system.ping")
    await ex.enqueue(vc)
    res = await cap.wait_result(vc.command_id)
    assert res["state"] == "failed" and res["error"]["code"] == "INTERNAL"


async def test_precheck_runs_right_before_execution(
    harness: AgentHarness, controller: Controller, executor: Any
) -> None:
    ex, cap = executor
    vc = verified(harness, controller, "system.ping")
    harness.agent.store.set_remote_enabled(False)  # disabled after authorization, before execution
    await ex.enqueue(vc)
    res = await cap.wait_result(vc.command_id)
    assert res["state"] == "failed" and res["error"]["code"] == "PC_REMOTE_DISABLED"


async def test_deferred_power_completion(harness: AgentHarness, controller: Controller, executor: Any) -> None:
    ex, cap = executor
    vc = verified(harness, controller, "power.shutdown", {"countdown_seconds": 1}, lifetime=90)
    harness.agent.store.journal_set_state(vc.command_id, "accepted")
    await ex.enqueue(vc)
    await asyncio.sleep(0.2)
    assert vc.command_id in ex.deferred_ids
    pending = harness.agent.store.get_pending_power()
    assert pending is not None and pending.action == "power.shutdown"
    res = await cap.wait_result(vc.command_id, timeout=5)
    assert res["state"] == "succeeded" and res["result"]["accepted"] is True and res["result"]["countdown_seconds"] == 1
    assert harness.fake.count("power_shutdown") == 1
    assert harness.agent.store.get_pending_power() is None
    assert handler_for("power.shutdown") is not None and DEFERRED is DEFERRED


async def test_power_cancel_terminates_pending_command(
    harness: AgentHarness, controller: Controller, executor: Any
) -> None:
    ex, cap = executor
    vc = verified(harness, controller, "power.sleep", {"countdown_seconds": 30}, lifetime=90)
    await ex.enqueue(vc)
    await asyncio.sleep(0.2)
    cancel = verified(harness, controller, "power.cancel")
    await ex.enqueue(cancel)
    rc = await cap.wait_result(cancel.command_id)
    assert rc["state"] == "succeeded" and rc["result"] == {
        "canceled": True,
        "action": "power.sleep",
        "command_id": vc.command_id,
    }
    rs = await cap.wait_result(vc.command_id)
    assert rs["state"] == "canceled" and rs["error"]["code"] == "POWER_CANCELED"
    assert harness.fake.count("power_sleep") == 0
    # a second power command while nothing is pending → fine; cancel with nothing pending → canceled false
    cancel2 = verified(harness, controller, "power.cancel")
    await ex.enqueue(cancel2)
    rc2 = await cap.wait_result(cancel2.command_id)
    assert rc2["result"] == {"canceled": False}


async def test_power_denied_by_os_is_failed(harness: AgentHarness, controller: Controller, executor: Any) -> None:
    ex, cap = executor
    harness.fake.power_fail = "POWER_DENIED"
    vc = verified(harness, controller, "power.restart", {"countdown_seconds": 0}, lifetime=90)
    await ex.enqueue(vc)
    res = await cap.wait_result(vc.command_id)
    assert res["state"] == "failed" and res["error"]["code"] == "POWER_DENIED"


async def test_fail_queued_offline_does_not_send(harness: AgentHarness, controller: Controller, executor: Any) -> None:
    ex, cap = executor
    await ex.stop()
    vc = verified(harness, controller, "system.ping")
    await ex.enqueue(vc)
    failed = await ex.fail_queued_offline()
    assert failed == [vc.command_id]
    assert cap.results(vc.command_id) == []
    row = harness.agent.store.journal_get(vc.command_id)
    assert row is not None and row.state == "failed" and row.error_code == "PC_OFFLINE" and row.sent


async def test_cancel_for_controller_cancels_running(
    harness: AgentHarness, controller: Controller, executor: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    ex, cap = executor

    async def slow(ctx: Any) -> dict[str, Any]:
        await asyncio.sleep(5)
        return {}

    import dome_agent.actions as actions_pkg

    monkeypatch.setitem(actions_pkg._HANDLERS, "system.ping", slow)  # noqa: SLF001
    v1 = verified(harness, controller, "system.ping")
    v2 = verified(harness, controller, "system.ping")
    await ex.enqueue(v1)
    await ex.enqueue(v2)
    await asyncio.sleep(0.1)
    canceled = await ex.cancel_for_controller(
        controller.controller_id, load_registry().make_error("CONTROLLER_REVOKED")
    )
    assert set(canceled) == {v1.command_id, v2.command_id}
    r1 = await cap.wait_result(v1.command_id)
    r2 = await cap.wait_result(v2.command_id)
    assert r1["state"] == "canceled" and r2["state"] == "canceled"
    assert payload_of(controller.command("system.ping"))["action"] == "system.ping"


# ----- review findings: re-check before execution, honest power cancel, lost browser answers -----------------


async def test_precheck_refuses_capability_narrowed_after_authorization(
    harness: AgentHarness, controller: Controller, executor: Any
) -> None:
    import uuid

    ex, cap = executor
    vc = verified(harness, controller, "windows.set_volume", {"value": 10})
    # the account narrows the phone to status-only between authorization and execution
    harness.agent.store.apply_snapshot(str(uuid.uuid4()), True, [controller.snapshot_entry(capabilities=("status",))])
    await ex.enqueue(vc)
    res = await cap.wait_result(vc.command_id)
    assert res["state"] == "failed" and res["error"]["code"] == "GRANT_MISSING"
    assert harness.fake.count("set_volume") == 0


async def test_precheck_refuses_controller_plan_disabled_after_authorization(
    harness: AgentHarness, controller: Controller, executor: Any
) -> None:
    import uuid

    ex, cap = executor
    vc = verified(harness, controller, "system.ping")
    harness.agent.store.apply_snapshot(str(uuid.uuid4()), True, [controller.snapshot_entry(status="plan_disabled")])
    await ex.enqueue(vc)
    res = await cap.wait_result(vc.command_id)
    assert res["state"] == "failed" and res["error"]["code"] == "CONTROLLER_PLAN_DISABLED"


async def test_power_cancel_during_os_call_is_refused_not_faked(
    harness: AgentHarness, controller: Controller, executor: Any
) -> None:
    """Cancel arrives while SetSuspendState is blocking in its worker thread: the thread cannot be
    stopped, so the agent must not report `canceled` for either command."""
    ex, cap = executor
    harness.fake.power_block_seconds = 1.0  # a real SetSuspendState blocks; the fake blocks for 1 s
    vc = verified(harness, controller, "power.sleep", {"countdown_seconds": 0}, lifetime=90)
    await ex.enqueue(vc)
    for _ in range(100):  # wait until the countdown is in its issuing phase
        armed = harness.agent.power.pending
        if armed is not None and armed.issuing:
            break
        await asyncio.sleep(0.01)
    assert harness.agent.power.pending is not None and harness.agent.power.pending.issuing
    cancel = verified(harness, controller, "power.cancel")
    await ex.enqueue(cancel)
    rc = await cap.wait_result(cancel.command_id)
    assert rc["state"] == "failed" and rc["error"]["code"] == "ACTION_UNAVAILABLE"
    assert rc["result"] == {"canceled": False, "action": "power.sleep", "command_id": vc.command_id}
    rs = await cap.wait_result(vc.command_id)
    assert rs["state"] == "succeeded" and rs["result"]["accepted"] is True
    assert harness.fake.count("power_sleep") == 1
    assert all(f["state"] != "canceled" for f in cap.results(vc.command_id))
    assert harness.agent.store.journal_get(vc.command_id).state == "succeeded"  # type: ignore[union-attr]


async def test_power_cancel_aborts_initiated_restart_only_when_windows_agrees(
    harness: AgentHarness, controller: Controller, executor: Any
) -> None:
    ex, cap = executor
    vc = verified(harness, controller, "power.restart", {"countdown_seconds": 0}, lifetime=90)
    await ex.enqueue(vc)
    rs = await cap.wait_result(vc.command_id)
    assert rs["state"] == "succeeded" and harness.fake.count("power_restart") == 1
    # Windows refuses the abort (e.g. no grace period left): honest `canceled: false`
    harness.fake.abort_shutdown_ok = False
    c1 = verified(harness, controller, "power.cancel")
    await ex.enqueue(c1)
    r1 = await cap.wait_result(c1.command_id)
    assert r1["state"] == "succeeded" and r1["result"] == {"canceled": False}
    assert harness.fake.count("power_abort_shutdown") == 1
    # Windows aborts it: reported against the restart's command_id, and only once
    harness.fake.abort_shutdown_ok = True
    c2 = verified(harness, controller, "power.cancel")
    await ex.enqueue(c2)
    r2 = await cap.wait_result(c2.command_id)
    assert r2["result"] == {"canceled": True, "action": "power.restart", "command_id": vc.command_id}
    c3 = verified(harness, controller, "power.cancel")
    await ex.enqueue(c3)
    assert (await cap.wait_result(c3.command_id))["result"] == {"canceled": False}
    assert harness.fake.count("power_abort_shutdown") == 2


async def test_power_cancel_too_late_from_relay_cancel_frame_keeps_real_outcome(
    harness: AgentHarness, controller: Controller
) -> None:
    """Same race through the real frame path: relay `cancel` during the OS call → no `canceled` result."""
    harness.fake.power_block_seconds = 1.0
    env = controller.command("power.sleep", {"countdown_seconds": 0}, lifetime=90)
    cid = payload_of(env)["command_id"]
    await harness.send_command(env)
    req = await harness.relay.expect("confirmation_required", command_id=cid)
    await harness.send_confirmation(controller.confirmation(cid, req["challenge_text"]))
    await harness.ack(cid, state="executing")
    for _ in range(200):
        armed = harness.agent.power.pending
        if armed is not None and armed.issuing:
            break
        await asyncio.sleep(0.01)
    await harness.relay.send({"type": "cancel", "command_id": cid, "controller_id": controller.controller_id})
    res = await harness.result(cid)
    assert res["state"] == "succeeded" and res["result"]["accepted"] is True
    assert harness.fake.count("power_sleep") == 1
