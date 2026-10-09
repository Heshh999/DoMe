"""The DEV_IDP_PASSPHRASE gate used by testkit/ when the stack is reachable from the internet."""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from dome_dev_idp import DevIdpSettings, create_app
from dome_dev_idp.app import PASSPHRASE_MAX_FAILURES

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


def _client(passphrase: str = "") -> TestClient:
    settings = DevIdpSettings(issuer="https://idp.example.test", redirect_uris=(REDIRECT,), passphrase=passphrase)
    return TestClient(create_app(settings), follow_redirects=False)


def _post(client: TestClient, **extra: str):  # type: ignore[no-untyped-def]
    return client.post("/authorize", data={**AUTH_PARAMS, "email": "tester@example.test", **extra})


def _redeem(client: TestClient, location: str) -> dict[str, object]:
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
    return r.json()


def test_without_passphrase_behaviour_is_unchanged() -> None:
    client = _client()
    page = client.get("/authorize", params=AUTH_PARAMS)
    assert page.status_code == 200
    assert 'name="passphrase"' not in page.text
    assert client.get("/authorize", params={**AUTH_PARAMS, "dev_user": "alice@example.test"}).status_code == 303
    r = _post(client)
    assert r.status_code == 303
    assert r.headers["location"].startswith(REDIRECT + "?code=")


def test_page_asks_for_passphrase_and_posts_relative_action() -> None:
    page = _client("k7m2-x9qp-3wfa").get("/authorize", params=AUTH_PARAMS)
    assert page.status_code == 200
    assert 'name="passphrase"' in page.text
    # Relative action: the page also works when the issuer is mounted below a path prefix.
    assert 'action="authorize"' in page.text
    assert "k7m2-x9qp-3wfa" not in page.text


def test_correct_passphrase_signs_in_and_is_case_and_space_insensitive() -> None:
    client = _client("k7m2-x9qp-3wfa")
    r = _post(client, passphrase="  K7M2-X9QP-3WFA ")
    assert r.status_code == 303
    tokens = _redeem(client, r.headers["location"])
    assert tokens["id_token"]


@pytest.mark.parametrize("supplied", ["", "k7m2-x9qp-3wfb", "k7m2"])
def test_wrong_or_missing_passphrase_is_refused(supplied: str) -> None:
    r = _post(_client("k7m2-x9qp-3wfa"), passphrase=supplied)
    assert r.status_code == 403
    assert "location" not in r.headers


def test_dev_user_shortcut_is_refused_when_passphrase_is_set() -> None:
    r = _client("k7m2-x9qp-3wfa").get("/authorize", params={**AUTH_PARAMS, "dev_user": "alice@example.test"})
    assert r.status_code == 403


def test_repeated_failures_lock_sign_in_even_for_the_right_passphrase() -> None:
    client = _client("k7m2-x9qp-3wfa")
    for _ in range(PASSPHRASE_MAX_FAILURES):
        assert _post(client, passphrase="nope").status_code == 403
    assert _post(client, passphrase="k7m2-x9qp-3wfa").status_code == 429


def test_invalid_client_is_rejected_before_the_passphrase_is_checked() -> None:
    client = _client("k7m2-x9qp-3wfa")
    for _ in range(PASSPHRASE_MAX_FAILURES + 2):
        r = client.post(
            "/authorize", data={**AUTH_PARAMS, "client_id": "other", "passphrase": "nope", "email": "a@b.c"}
        )
        assert r.status_code == 400
    # Malformed requests did not count as passphrase failures.
    assert _post(client, passphrase="k7m2-x9qp-3wfa").status_code == 303


def test_account_button_wins_over_an_empty_any_email_box() -> None:
    """A browser submits the clicked button's email and the untouched "any email" input (empty)."""
    client = _client("k7m2-x9qp-3wfa")
    form = [*AUTH_PARAMS.items(), ("passphrase", "k7m2-x9qp-3wfa"), ("email", "alice@example.test"), ("email", "")]
    r = client.post(
        "/authorize", content=urlencode(form), headers={"content-type": "application/x-www-form-urlencoded"}
    )
    assert r.status_code == 303
    tokens = _redeem(client, r.headers["location"])
    assert tokens["id_token"]
    typed = [*AUTH_PARAMS.items(), ("passphrase", "k7m2-x9qp-3wfa"), ("email", "  Tester@Example.test ")]
    r = client.post(
        "/authorize", content=urlencode(typed), headers={"content-type": "application/x-www-form-urlencoded"}
    )
    assert r.status_code == 303
    empty = [*AUTH_PARAMS.items(), ("passphrase", "k7m2-x9qp-3wfa"), ("email", "")]
    r = client.post(
        "/authorize", content=urlencode(empty), headers={"content-type": "application/x-www-form-urlencoded"}
    )
    assert r.status_code == 400


def test_browser_gets_a_readable_refusal_page_and_scripts_keep_json() -> None:
    client = _client("k7m2-x9qp-3wfa")
    form = [*AUTH_PARAMS.items(), ("passphrase", "wrong-one"), ("email", "alice@example.test")]
    body = urlencode(form)
    as_browser = client.post(
        "/authorize",
        content=body,
        headers={"content-type": "application/x-www-form-urlencoded", "accept": "text/html,*/*;q=0.8"},
    )
    assert as_browser.status_code == 403
    assert as_browser.headers["content-type"].startswith("text/html")
    assert "Wrong passphrase." in as_browser.text and "Go back" in as_browser.text
    as_script = client.post("/authorize", content=body, headers={"content-type": "application/x-www-form-urlencoded"})
    assert as_script.status_code == 403 and as_script.json() == {"detail": "wrong passphrase"}


def test_sign_in_page_fits_a_phone_screen() -> None:
    page = _client("k7m2-x9qp-3wfa").get("/authorize", params=AUTH_PARAMS).text
    assert "box-sizing:border-box" in page and "width=device-width" in page
