/**
 * The git commit a PWA build is made from (mobile-app DECISIONS 92). deploy/Dockerfile's PWA stage receives the same
 * `DOME_BUILD_COMMIT` build argument as the runtime stage, whose /healthz reports it as "commit"; vite.config.ts turns
 * it into the `__BUILD_COMMIT__` define (lib/diagnostics.ts `BUILD_COMMIT`).
 *
 * Unset, empty or "dev" = "" (a local build: the app says "build dev"). Anything else must be 7-40 lower-case hex
 * digits, the pattern cloud-api's settings.py accepts for the same value, or the build stops. The value is never
 * printed: a wrong value pasted into the variable stays out of the build log.
 */
export const BUILD_COMMIT_PATTERN = /^[0-9a-f]{7,40}$/;

export function buildCommit(raw: string | undefined): string {
  const value = (raw ?? "").trim();
  if (value === "" || value === "dev") return "";
  if (!BUILD_COMMIT_PATTERN.test(value)) throw new Error("DOME_BUILD_COMMIT must be a git commit (7-40 lower-case hex digits), empty or unset.");
  return value;
}
