"""The ``email_verified`` claim a test can choose: DoMe refuses a sign-in unless it is exactly true, so the
integration tests need a provider that can also say false or leave the claim out."""

from __future__ import annotations

import base64
import hashlib

import pytest
from fastapi.testclient import TestClient
from joserfc import jwt
from joserfc.jwk import KeySet

from dome_dev_idp import DevIdpSettings, create_app

REDIRECT = "https://dome.example.test/v1/auth/callback"
VERIFIER = "v" * 64
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
AUTH_PARAMS = {
    "client_id": "dome-dev",
    "response_type": "code",
    "redirect_uri": REDIRECT,
    "scope": "openid email",
    "state": "s1",
    "nonce": "n1",
    "code_challenge": CHALLENGE,
    "code_challenge_method": "S256",
}
PASSPHRASE = "k7m2-x9qp-3wfa"


def _client(passphrase: str = "") -> TestClient:
    settings = DevIdpSettings(issuer="https://idp.example.test", redirect_uris=(REDIRECT,), passphrase=passphrase)
    return TestClient(create_app(settings), follow_redirects=False)


def _redeem(client: TestClient, location: str) -> tuple[dict[str, object], dict[str, object]]:
    """Exchange the code; return the verified ID token claims and the userinfo answer."""
    code = location.split("code=", 1)[1].split("&", 1)[0]
    r = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "code_verifier": VERIFIER,
            "client_id": "dome-dev",
            "client_secret": "dome-dev-secret",
        },
    )
    assert r.status_code == 200, r.text
    tokens = r.json()
    keys = KeySet.import_key_set(client.get("/jwks").json())
    claims = dict(jwt.decode(tokens["id_token"], keys, algorithms=["RS256"]).claims)
    info = client.get("/userinfo", headers={"authorization": f"Bearer {tokens['access_token']}"}).json()
    return claims, info


def _dev_user(client: TestClient, **extra: str):  # type: ignore[no-untyped-def]
    return client.get("/authorize", params={**AUTH_PARAMS, "dev_user": "tester@example.test", **extra})


def test_dev_user_is_verified_by_default() -> None:
    client = _client()
    r = _dev_user(client)
    assert r.status_code == 303
    claims, info = _redeem(client, r.headers["location"])
    assert claims["email_verified"] is True and info["email_verified"] is True
    assert claims["email"] == "tester@example.test"


@pytest.mark.parametrize(("choice", "expected"), [("true", True), ("false", False), ("FALSE", False)])
def test_dev_user_can_choose_the_claim(choice: str, expected: bool) -> None:
    client = _client()
    r = _dev_user(client, dev_email_verified=choice)
    assert r.status_code == 303
    claims, info = _redeem(client, r.headers["location"])
    assert claims["email_verified"] is expected and info["email_verified"] is expected


def test_dev_user_can_leave_the_claim_out() -> None:
    client = _client()
    r = _dev_user(client, dev_email_verified="absent")
    assert r.status_code == 303
    claims, info = _redeem(client, r.headers["location"])
    assert "email_verified" not in claims and "email_verified" not in info
    assert claims["email"] == "tester@example.test"


def test_unknown_choice_is_refused_without_a_code() -> None:
    r = _dev_user(_client(), dev_email_verified="maybe")
    assert r.status_code == 400
    assert "location" not in r.headers


def test_choice_is_never_echoed_into_the_sign_in_page() -> None:
    page = _client().get("/authorize", params={**AUTH_PARAMS, "dev_email_verified": "false"}).text
    assert "dev_email_verified" not in page


def test_dev_user_path_stays_refused_with_a_passphrase_whatever_the_choice() -> None:
    r = _dev_user(_client(PASSPHRASE), dev_email_verified="false")
    assert r.status_code == 403


def test_sign_in_form_checkbox_signs_in_unverified_and_is_off_by_default() -> None:
    """The passphrase-gated form (testkit/) offers the same test on a phone: the checkbox makes the claim false."""
    client = _client(PASSPHRASE)
    page = client.get("/authorize", params=AUTH_PARAMS).text
    assert 'type="checkbox" name="email_unverified" value="1"' in page
    assert 'name="email_unverified" value="1" checked' not in page
    base = {**AUTH_PARAMS, "passphrase": PASSPHRASE, "email": "tester@example.test"}
    r = client.post("/authorize", data={**base, "email_unverified": "1"})
    assert r.status_code == 303
    claims, _ = _redeem(client, r.headers["location"])
    assert claims["email_verified"] is False
    r = client.post("/authorize", data=base)
    assert r.status_code == 303
    claims, _ = _redeem(client, r.headers["location"])
    assert claims["email_verified"] is True
    # the checkbox still needs the passphrase
    r = client.post("/authorize", data={**base, "passphrase": "wrong", "email_unverified": "1"})
    assert r.status_code == 403
