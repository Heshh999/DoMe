from __future__ import annotations

import time

import pytest

from dome_agent.entitlement import EntitlementError, EntitlementManager, verify_assertion
from dome_agent.store import Store
from dome_agent.testing.fake_relay import FakeApi


@pytest.fixture
def api() -> FakeApi:
    return FakeApi()


def test_verify_good_assertion(api: FakeApi) -> None:
    v = verify_assertion(api.make_assertion(), api.jwks(), account_id=api.account_id, pc_id=api.pc_id)
    assert v.plan == "pro" and v.routines and v.exp > time.time()


def test_verify_rejects_wrong_binding_and_key(api: FakeApi) -> None:
    other = FakeApi()
    with pytest.raises(EntitlementError):
        verify_assertion(api.make_assertion(pc_id=other.pc_id), api.jwks(), account_id=api.account_id, pc_id=api.pc_id)
    with pytest.raises(EntitlementError):
        verify_assertion(api.make_assertion(account_id=other.account_id), api.jwks(), account_id=api.account_id, pc_id=api.pc_id)
    with pytest.raises(EntitlementError):
        verify_assertion(api.make_assertion(), other.jwks(), account_id=api.account_id, pc_id=api.pc_id)
    with pytest.raises(EntitlementError):
        verify_assertion(api.make_assertion(iat=int(time.time()) - 7200, lifetime=3600), api.jwks(), account_id=api.account_id, pc_id=api.pc_id)
    with pytest.raises(EntitlementError):
        verify_assertion("not.a.jws", api.jwks(), account_id=api.account_id, pc_id=api.pc_id)
    token = api.make_assertion()
    header, payload, sig = token.split(".")
    with pytest.raises(EntitlementError):
        verify_assertion(f"{header}.{payload}.{sig[:-4]}AAAA", api.jwks(), account_id=api.account_id, pc_id=api.pc_id)


def test_wrong_typ_or_alg_rejected(api: FakeApi) -> None:
    from joserfc import jwt

    key = api.signing_key()
    claims = {"iss": api.url, "sub": api.account_id, "pc": api.pc_id, "plan": "pro", "limits": {"max_enabled_pcs": 5, "max_controllers": 5, "routines": True, "routine_max_steps": 10, "routine_max_seconds": 60, "custom_layouts": True}, "iat": int(time.time()), "exp": int(time.time()) + 100, "jti": "11111111-1111-4111-8111-111111111111"}
    bad_typ = jwt.encode({"alg": "EdDSA", "typ": "JWT", "kid": key.thumbprint()}, claims, key, algorithms=["EdDSA"])
    with pytest.raises(EntitlementError):
        verify_assertion(bad_typ, api.jwks(), account_id=api.account_id, pc_id=api.pc_id)


def test_grace_only_on_soft_failure(store: Store, api: FakeApi) -> None:
    mgr = EntitlementManager(store, account_id=api.account_id, pc_id=api.pc_id)
    assert mgr.effective_plan() == "free" and not mgr.routines_allowed()
    mgr.apply_assertion(api.make_assertion(lifetime=60), api.jwks())
    assert mgr.effective_plan() == "pro" and mgr.routines_allowed()
    later = time.time() + 3600  # assertion expired an hour ago
    assert mgr.effective_plan(later) == "free"
    mgr.note_refresh_failure(soft=True)
    assert mgr.effective_plan(later) == "pro"  # within 72 h grace after a network/5xx failure
    assert mgr.effective_plan(later + 72 * 3600 + 120) == "free"  # grace over
    mgr.note_refresh_failure(soft=False)  # a definitive 4xx → Free immediately
    assert mgr.effective_plan(later) == "free"
    # persisted claims survive a restart (token itself is never stored)
    mgr.apply_assertion(api.make_assertion(lifetime=60))
    again = EntitlementManager(store, account_id=api.account_id, pc_id=api.pc_id)
    assert again.current is not None and again.current.plan == "pro"
    assert "assertion" not in (store.get_setting("entitlement_last_verified") or "")
    mgr.set_free()
    assert EntitlementManager(store, account_id=api.account_id, pc_id=api.pc_id).current is None


async def test_refresh_against_fake_api(store: Store, fake_api: FakeApi) -> None:
    from dome_agent.api import ApiClient
    from dome_agent.relay_client import TokenManager

    cred = fake_api.issue_credential()
    api = ApiClient(fake_api.url)
    tokens = TokenManager(api, lambda: cred)
    mgr = EntitlementManager(store, account_id=fake_api.account_id, pc_id=fake_api.pc_id)
    mgr.bind(api, tokens.get)
    await mgr.refresh()
    assert mgr.effective_plan() == "free"
    fake_api.plan = "pro"
    fake_api.entitlement_assertion = fake_api.make_assertion()
    await mgr.refresh()
    assert mgr.effective_plan() == "pro"
    fake_api.entitlement_status = 503
    await mgr.refresh()
    assert mgr.effective_plan() == "pro"  # outage: last verified assertion still honoured
    fake_api.entitlement_status = 200
    fake_api.entitlement_assertion = None
    fake_api.plan = "free"
    await mgr.refresh()
    assert mgr.effective_plan() == "free"  # assertion null → Free immediately
    await api.close()
