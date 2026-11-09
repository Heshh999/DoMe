/**
 * The app's build identity (DECISIONS 92): deploy/Dockerfile passes the image's DOME_BUILD_COMMIT to the PWA stage;
 * scripts/build-commit.ts accepts a git commit (7-40 lower-case hex, the pattern /healthz's settings accept), empty,
 * unset or "dev", and stops the build on anything else without printing it; the app shows the first 12 digits
 * ("app: 0.1.0 · build 6fc0951a2b3c", "build dev" without a commit) and the diagnostics bundle carries the commit.
 */
import { describe, expect, it } from "vitest";

import { BUILD_COMMIT_PATTERN, buildCommit } from "../scripts/build-commit.ts";
import { APP_VERSION, appReadoutLine, BUILD_COMMIT, BUILD_COMMIT_SHORT, BUILD_COMMIT_SHORT_LENGTH, buildDiagnostics, buildLabel, normalizeBuildCommit } from "../src/lib/diagnostics.ts";

const SHA = "6fc0951a2b3c4d5e6f708192a3b4c5d6e7f80912";

describe("the build commit at build time (scripts/build-commit.ts)", () => {
  it("a git commit passes through; unset, empty, blank and 'dev' mean a local build", () => {
    expect(buildCommit(SHA)).toBe(SHA);
    expect(buildCommit("6fc0951")).toBe("6fc0951");
    expect(buildCommit(` ${SHA}\n`)).toBe(SHA);
    for (const local of [undefined, "", "  ", "dev"]) expect(buildCommit(local), String(local)).toBe("");
  });

  it("anything else stops the build, and the message never repeats the value", () => {
    for (const bad of ["6FC0951A2B3C", "6fc095", `${SHA}0`, "main", "6fc0951 ; rm", "pasted-by-mistake-value"]) {
      let message = "";
      try {
        buildCommit(bad);
      } catch (e) {
        message = e instanceof Error ? e.message : String(e);
      }
      expect(message, bad).toMatch(/DOME_BUILD_COMMIT must be a git commit/);
      expect(message, bad).not.toContain(bad.trim());
    }
  });

  it("uses the same pattern as cloud-api's settings.py build_commit (^([0-9a-f]{7,40})?$ without the empty case)", () => {
    expect(BUILD_COMMIT_PATTERN.source).toBe("^[0-9a-f]{7,40}$");
  });
});

describe("the build commit in the app (lib/diagnostics.ts)", () => {
  it("keeps only a valid commit; the short form is its first 12 digits", () => {
    expect(normalizeBuildCommit(SHA)).toBe(SHA);
    for (const bad of ["", "dev", "XYZ", 42, null, undefined, "6FC0951"]) expect(normalizeBuildCommit(bad), String(bad)).toBe("");
    expect(BUILD_COMMIT_SHORT_LENGTH).toBe(12);
    expect(BUILD_COMMIT === "" || BUILD_COMMIT_PATTERN.test(BUILD_COMMIT)).toBe(true);
    expect(BUILD_COMMIT_SHORT).toBe(BUILD_COMMIT.slice(0, 12));
  });

  it("formats the label and the readout's first line", () => {
    expect(buildLabel("0.1.0", SHA)).toBe("0.1.0 · build 6fc0951a2b3c");
    expect(buildLabel("0.1.0", "6fc0951")).toBe("0.1.0 · build 6fc0951");
    expect(buildLabel("0.1.0", "")).toBe("0.1.0 · build dev");
    expect(buildLabel("0.1.0", "not-a-commit")).toBe("0.1.0 · build dev");
    expect(appReadoutLine("0.1.0", SHA)).toBe("app: 0.1.0 · build 6fc0951a2b3c");
    expect(appReadoutLine("0.1.0", "")).toBe("app: 0.1.0 · build dev");
    // this bundle's own values by default
    expect(appReadoutLine()).toBe(`app: ${APP_VERSION} · build ${BUILD_COMMIT_SHORT || "dev"}`);
    expect(buildLabel()).toBe(`${APP_VERSION} · build ${BUILD_COMMIT_SHORT || "dev"}`);
  });

  it("the diagnostics bundle carries the full commit (null for a local build) next to the version", () => {
    const report = buildDiagnostics({ relayStatus: "open", controllerBound: true, keyAvailable: true, selectedPcId: null, pcs: [], commands: [], sessionPresent: true, plan: "free" });
    expect(report.app.version).toBe(APP_VERSION);
    expect(report.app.build_commit).toBe(BUILD_COMMIT === "" ? null : BUILD_COMMIT);
    expect(Object.keys(report.app)).toEqual(["version", "build_commit", "protocol_version", "registry_version", "release_channel"]);
  });
});
