# browser-extension — contract / shared-library issues

Nothing under `shared/` was modified. Each item states the problem and the workaround used here.

## 1. `@dome/protocol` cannot run in a Manifest V3 service worker (Ajv `new Function`)

**Problem.** `shared/ts/src/registry.ts` compiles every schema with Ajv at module import time, and
`shared/ts/src/index.ts` re-exports it, so importing *anything* from `@dome/protocol` executes
`new Function(...)`. Chrome's MV3 extension CSP never allows `unsafe-eval` for extension pages or the
service worker, so the import throws `EvalError` and the worker dies before registering listeners.
The task text ("validated with `@dome/protocol schemas.validateBridgeFrame` before send and after
receive") is therefore not literally implementable in the extension runtime.

**Workaround.** `scripts/gen-validators.ts` precompiles the needed validators
(`bridge#extension_to_agent`, `bridge#agent_to_extension`, `relay-frames#youtube_tab`,
`results#tabs_result`, `results#youtube_state_result`) from the same frozen schema files with Ajv's
standalone code generator (plain JavaScript, verified to contain no `eval`/`new Function`; the
built bundle is smoke-tested with both disabled). `src/shared/validate.ts` exposes the same API
shape. `test/frames.test.ts` asserts that the generated validators and
`schemas.validateBridgeFrame` accept and reject the same frames, and `test/generated.test.ts` fails
when the committed output drifts from the schemas. Types are still taken from `@dome/protocol`
(`import type`), which has no runtime cost.

**Suggested fix for the shared library (not done here).** Provide an eval-free entry point, e.g.
`@dome/protocol/validators` generated with `ajv/dist/standalone` in `shared/ts/scripts`, or make the
registry compile lazily and offer a precompiled build for CSP-restricted runtimes. The same
limitation will hit the PWA if it ever ships with a `script-src` policy without `unsafe-eval`.

## 2. No error code for "clicked, but no transition observed"

**Problem.** For `next`/`previous`, the contract's verification strategy
(`observe_video_transition`) lists `NO_NEXT_VIDEO / TARGET_CHANGED / UNSUPPORTED_CONTEXT` as the
failure codes. None of them describes the real ambiguous case: the Next button existed and was
clicked, but no video change was observed before the deadline. Reporting `NO_NEXT_VIDEO` there
would be false ("There is no next video available") and could invite a retry that double-skips.

**Workaround.** The extension returns `OUTCOME_UNKNOWN` with a message stating that Next was
clicked and no change was observed within the deadline. Its `user_message` text mentions a lost PC
connection, which is slightly off, but its semantics ("may or may not have happened, check state
before retrying") and the phone's handling (warn on retry) are exactly right. A dedicated code such
as `TRANSITION_NOT_OBSERVED` (non-retryable, "The video did not change; check the PC before trying
again") would be clearer.

## 3. `bridge_response.error` has no `retryable` / `detail`

**Observation, no workaround needed.** Unlike `relay-frames#error`, the bridge error object carries
only `code` and `message`; the agent fills `retryable` from `errors.json` defaults. That is fine for
every code the extension emits today.

## 4. Native-messaging frames cannot be strict-parsed by the extension

**Observation.** Chrome parses native-messaging JSON before handing it to `port.onMessage`, so the
extension cannot apply `loadsStrict` (duplicate keys, depth) to inbound frames; it validates the
already-parsed value against the schema and relies on `dome-native-host`, which does strict-parse
both directions. Outbound frames are size-checked against `limits.max_frame_bytes` (64 KiB) before
posting.
