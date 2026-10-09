"""rules.controller_socket_identity: a controller socket is bound to the paired controller only after the
caller proves possession of the key. Without that, anyone signed in to the account could present a paired
phone's (public) kid, subscribe to its PCs' state and cancel its commands."""

from __future__ import annotations

from tests.conftest import AgentSim, Browser, ControllerSim, Env, close_code


async def test_bare_kid_of_a_paired_phone_stays_unbound(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    impostor = ControllerSim(env, alice, name="Same account, no key")
    ack = await impostor.connect(proof=False, kid=paired.kid)
    assert "controller_id" not in ack
    await impostor.subscribe(online_agent.pc_id)
    err = await impostor.recv()
    assert err["type"] == "error" and err["error"]["code"] == "GRANT_MISSING"
    await impostor.send(
        {"type": "cancel", "pc_id": online_agent.pc_id, "command_id": "00000000-0000-4000-8000-000000000001"}
    )
    err = await impostor.recv()
    assert err["type"] == "error" and err["error"]["code"] == "GRANT_MISSING"
    await impostor.close()


async def test_proof_signed_by_another_key_is_rejected_and_logged(
    env: Env, alice: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    impostor = ControllerSim(env, alice, name="Same account, own key")
    first = await impostor.connect(kid=paired.kid, expect_ack=False)  # proof signed by the impostor's key
    assert first["type"] == "error" and first["error"]["code"] == "UNKNOWN_KEY"
    assert await close_code(impostor.ws) == 4003  # type: ignore[arg-type]
    events = (await alice.get("/v1/account/security-events?limit=20"))["events"]
    assert any(e["kind"] == "controller_hello_proof_rejected" for e in events)
    # the real phone is unaffected
    await paired.subscribe(online_agent.pc_id)
    assert (await paired.recv())["type"] == "pc_status"


async def test_proof_is_single_use_and_bound_to_the_session_account(
    env: Env, alice: Browser, bob: Browser, online_agent: AgentSim, paired: ControllerSim
) -> None:
    twin = ControllerSim(env, alice, key=paired.key)  # the same installation opening a second socket
    proof = twin.make_proof()
    ack = await twin.connect(proof=proof)
    assert ack["controller_id"] == paired.controller_id
    replay = ControllerSim(env, alice, key=paired.key)
    first = await replay.connect(proof=proof, expect_ack=False)
    assert first["type"] == "error" and first["error"]["code"] == "UNKNOWN_KEY"
    assert await close_code(replay.ws) == 4003  # type: ignore[arg-type]
    wrong_account = ControllerSim(env, alice, key=paired.key)
    first = await wrong_account.connect(proof=wrong_account.make_proof(account_id=bob.account_id), expect_ack=False)
    assert first["type"] == "error" and first["error"]["code"] == "UNKNOWN_KEY"
    assert await close_code(wrong_account.ws) == 4003  # type: ignore[arg-type]
    # a fresh proof binds again
    again = ControllerSim(env, alice, key=paired.key)
    assert (await again.connect())["controller_id"] == paired.controller_id
    await twin.close()
    await again.close()
