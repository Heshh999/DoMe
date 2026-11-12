"""Account deletion's REST additions (docs/PROTOCOL.md §19.2, no protocol bump): the body of DELETE /v1/account takes
only the exact phrase, the sign-in problem values the callback redirects with include ``account_deleted``, and the
new error code exists. The same decisions as shared/ts/test/accountContract.test.ts."""

import pytest

from dome_protocol import ProtocolError, load_registry, load_schemas

PHRASE = "delete my account"


def ok(name, value):
    load_schemas().validate_rest(name, value)


def bad(name, value):
    with pytest.raises(ProtocolError) as ei:
        load_schemas().validate_rest(name, value)
    assert ei.value.code == "MALFORMED_MESSAGE"


def test_the_delete_body_takes_only_the_exact_phrase():
    ok("account_delete_request", {"confirm": PHRASE})
    for wrong in ("Delete my account", "DELETE MY ACCOUNT", " delete my account", "delete my account ", "delete", ""):
        bad("account_delete_request", {"confirm": wrong})
    bad("account_delete_request", {"confirm": True})
    bad("account_delete_request", {})
    bad("account_delete_request", {"confirm": PHRASE, "reason": "moving"})
    bad("account_delete_request", [PHRASE])


def test_sign_in_problem_values():
    for value in ("email_unverified", "try_again", "account_deleted"):
        ok("sign_in_problem", value)
    for value in ("deleted", "ACCOUNT_DELETED", "", None):
        bad("sign_in_problem", value)


def test_reauth_required_error_code():
    entry = load_registry().errors["REAUTH_REQUIRED"]
    assert entry["retryable"] is False
    assert entry["user_message"].endswith(".") and "sign in again" in entry["user_message"]
    assert "prompt=login" in entry["description"] and "10 minutes" in entry["description"]
