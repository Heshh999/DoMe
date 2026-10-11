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
`results#tabs_result`, `results#youtube_state_result`, `results#youtube_search_result`) from the same frozen schema files with Ajv's
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

## 5. `bridge_request.timeout_ms` minimum (100 ms) is below any workable budget

**Problem.** The bridge schema allows `timeout_ms` from 100 to 60000. A tab op needs a content-script
deadline strictly shorter than the background's wait, which in turn must end before the agent's own
timer, and the bridge round trip plus a `tabs.sendMessage` exchange costs real milliseconds. Below
roughly 1 s the margins invert: the background would give up before the content script has run and
a healthy tab would be reported `OUTCOME_UNKNOWN` (mutating ops) or `TAB_NOT_CONTROLLABLE`.

**Workaround.** The extension floors the budget to 1000 ms (`computeBudget` in
`src/background/requests.ts`): background wait = budget − 200 ms, content deadline = budget − 700 ms.
Smaller values are accepted and silently widened rather than rejected with `INVALID_PARAMETERS`, so
an agent that sends e.g. 500 ms gets an honest answer ~800 ms later instead of an instant error. The
agent currently sends 8000/10000 ms (actions.json), so this is latent. A schema minimum of 1000 for
`timeout_ms` would make the invariant explicit.

## 6. `bridge_request.args` does not tie `tab_id` to `tab_token` for `search`

**Observation.** `youtube_browser_target` has `dependentRequired` (`tab_id` ⇔ `tab_token`), but the
bridge `args` object (shared by every op) cannot express it per op, so a `search` request with only
one of the two validates against `bridge.schema.json`.

**Workaround.** The worker refuses it with `INVALID_PARAMETERS` before anything is navigated
(`src/background/search.ts`, test "a stale token, a tab without DoMe, …"). The agent builds both
from the verified target, so this is latent.

## 7. `youtube_search_item.title` has no `minLength`, and there is no code for "results unreadable after the search ran"

**Observation.** A result with an empty title would validate; the extension skips rows without a
readable title instead (D28), so every listed result can be shown. After the browser navigated,
two failures have no dedicated code: the results page never reported back (`OUTCOME_UNKNOWN`, which
is accurate: the search may or may not have loaded) and the results page attached but its rows could
not be read in time (`ACTION_UNAVAILABLE`, whose phone copy suggests nothing happened although the
search is open on the PC; the message says so). A code such as `SEARCH_RESULTS_UNREADABLE`
("The search opened on the PC, but DoMe could not read its results") would let the phone say it
plainly.

## 8. `ACTIVATION_REQUIRED`'s `user_message` speaks only of fullscreen

**Observation.** Since D32 `youtube.set_paused{paused:false}` answers `ACTIVATION_REQUIRED` when the
browser's autoplay policy refuses `play()`. `errors.json` still gives the code the copy "Fullscreen must
be started on the PC itself; the browser blocks remote fullscreen.", and the phone shows that copy as
the headline for every `ACTIVATION_REQUIRED` (its recovery steps already cover playback).

**Workaround.** The extension's own message says what happened and what to do on the PC. A generic
`user_message` (for example "The browser on the PC only allows this after a click on the PC itself.")
or a phone headline for `youtube.set_paused` would fix the copy; no code or field changes.
