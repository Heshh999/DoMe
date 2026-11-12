"""``prompt=login`` (OpenID Connect Core 3.1.2.1), which DoMe sends on sign-ins after a refused one (cloud-api
DECISIONS #119), is accepted on both sign-in paths: this provider keeps no sign-in session, so there is nothing to
skip, and integration tests that sign in again after a refusal keep working. The provider already ignored parameters
it does not know, so nothing in it changed for this; these tests pin that."""

from __future__ import annotations

import base64
import hashlib
import time

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
    "prompt": "login",
    "code_challenge": CHALLENGE,
    "code_challenge_method": "S256",
}
PASSPHRASE = "k7m2-x9qp-3wfa"


def _client(passphrase: str = "") -> TestClient:
    settings = DevIdpSettings(issuer="https://idp.example.test", redirect_uris=(REDIRECT,), passphrase=passphrase)
    return TestClient(create_app(settings), follow_redirects=False)


def _signed_in_email(client: TestClient, location: str) -> object:
    """Redeem the code; the email the issued access token belongs to."""
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
    info = client.get("/userinfo", headers={"authorization": f"Bearer {r.json()['access_token']}"}).json()
    return info["email"]


def test_dev_user_path_accepts_prompt_login() -> None:
    client = _client()
    r = client.get("/authorize", params={**AUTH_PARAMS, "dev_user": "tester@example.test"})
    assert r.status_code == 303
    assert r.headers["location"].startswith(REDIRECT + "?code=") and r.headers["location"].endswith("&state=s1")
    assert _signed_in_email(client, r.headers["location"]) == "tester@example.test"


def test_sign_in_page_is_shown_and_carries_prompt_login_through_the_form() -> None:
    client = _client(PASSPHRASE)
    page = client.get("/authorize", params=AUTH_PARAMS)
    assert page.status_code == 200 and "Development identity provider" in page.text
    assert '<input type="hidden" name="prompt" value="login">' in page.text
    r = client.post("/authorize", data={**AUTH_PARAMS, "passphrase": PASSPHRASE, "email": "tester@example.test"})
    assert r.status_code == 303
    assert _signed_in_email(client, r.headers["location"]) == "tester@example.test"


def test_every_id_token_says_when_the_person_signed_in() -> None:
    """``auth_time`` (OpenID Connect Core 2), required whenever ``max_age`` is asked for: DoMe sends ``max_age=0``
    before deleting an account and keeps the sign-in as a re-authentication only when ``auth_time`` falls within that
    attempt (cloud-api DECISIONS #121). This provider keeps no sign-in session, so it is the moment of the sign-in."""
    client = _client()
    before = int(time.time())
    r = client.get("/authorize", params={**AUTH_PARAMS, "max_age": "0", "dev_user": "tester@example.test"})
    assert r.status_code == 303
    code = r.headers["location"].split("code=", 1)[1].split("&", 1)[0]
    tokens = client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "code_verifier": VERIFIER,
            "client_id": "dome-dev",
            "client_secret": "dome-dev-secret",
        },
    ).json()
    keys = KeySet.import_key_set(client.get("/jwks").json())
    claims = jwt.decode(tokens["id_token"], keys, algorithms=["RS256"]).claims
    auth_time = claims["auth_time"]
    assert isinstance(auth_time, int) and not isinstance(auth_time, bool)
    assert before <= auth_time <= int(time.time())
    assert "auth_time" in client.get("/.well-known/openid-configuration").json()["claims_supported"]
