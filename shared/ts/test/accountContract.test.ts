import { describe, expect, it } from "vitest";

import { ERRORS, ProtocolError, schemas } from "../src/index.ts";
import type { rest } from "../src/generated/index.ts";

// Account deletion's REST additions (docs/PROTOCOL.md §19.2, no protocol bump): the same accept/refuse decisions as
// shared/python/tests/test_account_contract.py.

const PHRASE = "delete my account";

function ok(name: string, value: unknown): void {
  schemas.validateRest(name, value);
}

function bad(name: string, value: unknown): void {
  let code = "OK";
  try {
    schemas.validateRest(name, value);
  } catch (e) {
    if (!(e instanceof ProtocolError)) throw e;
    code = e.code;
  }
  expect(code).toBe("MALFORMED_MESSAGE");
}

describe("account deletion REST additions", () => {
  it("takes only the exact phrase in the delete body", () => {
    const body: rest.AccountDeleteRequest = { confirm: "delete my account" };
    ok("account_delete_request", body);
    for (const wrong of ["Delete my account", "DELETE MY ACCOUNT", " delete my account", "delete my account ", "delete", ""]) {
      bad("account_delete_request", { confirm: wrong });
    }
    bad("account_delete_request", { confirm: true });
    bad("account_delete_request", {});
    bad("account_delete_request", { confirm: PHRASE, reason: "moving" });
    bad("account_delete_request", [PHRASE]);
  });

  it("lists the sign-in problem values the callback redirects with", () => {
    const values: rest.SignInProblem[] = ["email_unverified", "try_again", "account_deleted"];
    for (const value of values) ok("sign_in_problem", value);
    for (const value of ["deleted", "ACCOUNT_DELETED", "", null]) bad("sign_in_problem", value);
  });

  it("has the REAUTH_REQUIRED error code", () => {
    const entry = ERRORS.REAUTH_REQUIRED;
    expect(entry?.retryable).toBe(false);
    expect(entry?.user_message.endsWith(".")).toBe(true);
    expect(entry?.user_message).toContain("sign in again");
  });
});
