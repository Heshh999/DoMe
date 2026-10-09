/** Support submission failure classification: definite rejection vs. receipt that cannot be confirmed. */
import { describe, expect, it } from "vitest";

import { ProtocolError } from "@dome/protocol";

import { ApiError } from "../src/lib/api.ts";
import { submissionFailure } from "../src/lib/support.ts";

describe("submissionFailure", () => {
  it("never-left-the-phone and definite 4xx rejections are not_sent", () => {
    expect(submissionFailure(new ApiError(0, "UNAUTHENTICATED", "Sign in", false))).toBe("not_sent"); // no CSRF token: thrown before fetch
    expect(submissionFailure(new ApiError(400, "MALFORMED_MESSAGE", "bad", false))).toBe("not_sent");
    expect(submissionFailure(new ApiError(401, "UNAUTHENTICATED", "x", false))).toBe("not_sent");
    expect(submissionFailure(new ApiError(429, "RATE_LIMITED", "x", true))).toBe("not_sent");
    expect(submissionFailure(new ProtocolError("MALFORMED_MESSAGE", "request body failed the contract"))).toBe("not_sent");
  });
  it("network drops, 5xx, unreadable 2xx answers and unknown exceptions are unconfirmed", () => {
    expect(submissionFailure(new ApiError(0, "NETWORK", "offline", true))).toBe("unconfirmed");
    expect(submissionFailure(new ApiError(500, "INTERNAL", "x", true))).toBe("unconfirmed");
    expect(submissionFailure(new ApiError(503, "INTERNAL", "x", true))).toBe("unconfirmed");
    expect(submissionFailure(new ApiError(201, "MALFORMED_MESSAGE", "x", false))).toBe("unconfirmed");
    expect(submissionFailure(new TypeError("body stream already read"))).toBe("unconfirmed");
  });
});
