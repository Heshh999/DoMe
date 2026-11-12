# Screen view round 2a: design (protocol 1.5)

Status (2026-11-09): **designed and reviewed; the build was lost and must be redone.** The four additions
(Next screen, the pointer drawn by the phone, zoom in Touchpad mode, hardware H.264) were designed by a judge
panel, attacked by four adversarial reviewers (25 findings, all accepted, section 9 below) and then built and
tested on Linux (shared 575, cloud-api 610, pc-agent 1354, mobile-app 1614, e2e 50 of 50 green on 2026-11-05).
The cloud build machine was cleared before that work was committed, so none of the code exists any more.
This document is what survived, reconstructed from the session record:

0. **Part 0** lists what the first builders found in the tree that the design did not say.
1. **Part 1** is the final specification as far as its text survived: the build order (section 8), the review
   log (section 9) and the final text of sections 1.4 (tail), 1.8, 1.9, 2.4, 2.5, 3.3, 3.4, 3.5, 3.9, 3.10,
   4.1, 4.7 to 4.9 and 5.1 to 5.3.
2. **Part 2** is the complete text as it stood before the adversarial review. Where Part 1 has a section,
   Part 1 wins. Every other section applies with the corrections listed in the review log (section 9).
3. **Part 3** is the brief every builder read first (facts about the tree as of 2026-11-02, with file and line
   references that may have moved since).

Lessons for the rebuild (recorded in docs/PROGRESS.md): one finding the end-to-end tests caught during the
build must be kept. A 1.5 phone must open the second data channel `dome-screen-cursor-v1` only in a start that
carries `cursor`, because the shipped 1.4 worker sends on whichever data channel the phone announced last
(pc-agent/dome_agent/screen/worker/rtc.py at commit bdda249, `_on_datachannel`); otherwise a 1.4 PC's state,
allowance and restricted messages are lost. And work in progress is now saved to GitHub after every stage.

---

# Part 0. Notes from the first build (2026-11-03 to 2026-11-05; the code was lost, these facts were not)

What the first builders found in the tree that the design did not say. A rebuild follows these; where one
contradicts Part 1 or Part 2, this part wins.

**Tooling and contract**
- Tooling is frozen. shared/python has no ruff or mypy configuration and shared/ts has no lint script; CI runs
  pytest for shared/python and gen:types-matches, typecheck and test for shared/ts. Do not add any.
- Pillow is not a dependency of shared/python: the fixture PNG (a real 1x1 RGBA image) is built with the
  standard library (struct + zlib stored deflate), and the TypeScript generator builds the same bytes with
  node:zlib level 0. Both carry the same base64 literal and write `screen-dc-cases.json` byte-identically.
- The parsers refuse anything above 12288 bytes, and anything above 1024 bytes sent phone_to_pc, before parsing
  (`MALFORMED_MESSAGE`, "data-channel message too long"). The answer builders also refuse a stamp the library
  does not speak (`PROTOCOL_INCOMPATIBLE {peer, supported}`).
- `relay-frames.schema.json` `$defs/screen_dc_message.description` must mention the cursor_shape size exception
  and the second channel (a description-only change, the generated files regenerated).
- Consent inheritance follows the rule text `rules.screen_sessions (1b)`: a same-controller replacement inherits
  an explicit `granted` only where the person would otherwise get an ask-mode window or a one-window-per-minute
  (`backoff`) refusal; the session code and PROTOCOL.md section 21 must say the same.

**The compatibility bug the end-to-end run caught (keep this fix)**
- The phone opens the second data channel `dome-screen-cursor-v1` ONLY in a start that carries `cursor`. The
  shipped 1.4 worker sends on whichever data channel the phone announced last
  (`pc-agent/dome_agent/screen/worker/rtc.py` `_on_datachannel` at commit bdda249), so a two-channel offer to a
  1.4 PC loses its state, allowance and restricted messages. `ScreenSessionClient.buildOffer(cursorChannel)` takes
  the same flag that puts `cursor: "overlay"` into the params. The e2e Python phone (`tests/screen_phone.py`)
  must follow the app: one channel unless `cursor` is given.

**cloud-api**
- The test harness computes the stamp in `ControllerSim.envelope` (which `ControllerSim.command` calls): stamp by
  content there. `AgentSim.screen_answer` takes `protocol_version` (default "1.4").
- The relay stamp gate sits after the `supports_screen` gate and before the operator switch
  (`SCREEN_VIEW_DISABLED`), the start rate limiter and `reserve_start`. "No rate-limiter token spent" is proven
  behaviourally: after the refused start, six starts still pass and the seventh is `RATE_LIMITED`.
- `tests/test_e2e_versions.py::test_an_agent_one_minor_behind_refuses_only_the_actions_it_predates` expected
  `origin == "agent"`; with the generalised relay gate those refusals come from the relay.

**pc-agent**
- `ScreenSession.protocol_version` carries the start's stamp to the signer; `start_session`/`_start_session` take
  a required `protocol_version` keyword. `tests/helpers.py stamp_for(action, params)` stamps by content.
- The worker's flat `cursor_*` stats are whitelisted and stored as the nested `media.cursor` object with short
  names; `settings.screen_h264_hw` lives in the bundle's top-level `settings` section.
- When the re-validation after the second `live_session` read re-asks consent, `inherited` resets to false. The
  old session's `_end(..., keep_indicator=True)` is inside the try/except that closes kept bars on failure.
- DECISIONS: #105 to #111 (#111: cursor_pos on the unordered cursor channel). The encoder references are #101
  (encoder_stalled) and #103 (VP8 first), not #100. KNOWN_ISSUES: #35 rewritten, #40 to #43 new.
- `tests/test_screen_worker.py::test_loopback_overlay_session_without_a_cursor_channel_uses_the_reliable_one`
  failed once under full-suite load (it waited 10 s for three cursor_pos): make it deterministic.

**mobile-app**
- `test/screenStatus.test.ts` checks every `COPY` string against forbidden words, so the switch texts live in a
  sibling export `SWITCH_COPY` in `lib/screenStatus.ts`. `test/directGestures.test.ts` forces the Pinch row to
  read "Zooms the picture on your phone only; nothing is sent to the PC".
- Component tests live in `test/components/`. `test/helpers/fakePeer.ts` must pass `protocolVersion` to
  `buildScreenAnswerPayload`.
- `ScreenSurface` takes `onZoom(scale)` (wired to `client.noteZoom`) so the readout's `zoom: max` moves. Within
  one pinch the unsnapped zoom is kept and `snapIdentity` applies to what is shown. The zoom-follow frame loop
  runs only while zoomed in Touchpad mode.
- The back `<video>` sits under the front one (front `z-[1]` with a black background), not `display:none`, so
  Safari keeps decoding it. No "Couldn't switch screens" chip for `SCREEN_ALLOWANCE_EXHAUSTED`. The 1x button is
  labelled "Back to the whole screen" and stops pointer propagation. The gesture sheet's lists are headed "With
  Touchpad off", "With Touchpad on" and "Either way".
- The "newer DoMe is ready" notice was shown on the Screen page only (KNOWN_ISSUES #45); the review proposed one on
  the Devices page too. KNOWN_ISSUES #46: with Drag on, Touchpad mode has no pinch zoom (pre-1.5 behaviour).

---

# Part 1. Final specification (surviving text, after the adversarial review)

## Final table of contents (headings of the lost final file, with its line numbers)

```
47:### 1.1 `version.json`
60:### 1.2 `actions.json`
92:### 1.3 `results.schema.json` — `screen_session_result`
106:### 1.4 `relay-frames.schema.json` — `screen_dc_pc_to_phone`
164:### 1.5 `rest.schema.json` — `$defs/pc`
179:### 1.6 Parser rule for the size cap (both libraries)
193:### 1.7 Rule text (`version.json → rules`)
204:### 1.8 Shared libraries
227:### 1.9 Fixtures
234:### 1.10 `docs/PROTOCOL.md` §21 — "Protocol 1.5: Screen view, Phase 2a (Next screen, phone-drawn pointer, Touchpad zoom, hardware encoding)"
238:### 1.11 `shared/protocol/README.md` entry
246:### 2.1 Versions
249:### 2.2 Persisted `protocol_versions`
255:### 2.3 Command stamp gate
269:### 2.4 Tests (`cloud-api/tests`, real PostgreSQL)
276:### 2.5 Docs
283:### 3.1 Versions and answer stamp
287:### 3.2 `actions/screen.py`
291:### 3.3 `screen/session.py`
327:### 3.4 `input_session.py` — confinement
365:### 3.5 Worker: pipe protocol and channels
372:### 3.6 Worker: sources without the pointer
379:### 3.7 Worker: `screen/worker/cursor.py` (pure logic + the poller) and `cursor_win.py` (ctypes)
401:### 3.8 Worker: hardware H.264 (`encode.py`, `track.py`)
432:### 3.9 Testing doubles
436:### 3.10 Diagnostics and redaction
441:### 3.11 Tests (`cd pc-agent && uv run --frozen pytest -q tests`, ruff, mypy both configs)
452:### 3.12 Docs
461:### 4.1 `lib/screen.ts` (`ScreenSessionClient`)
504:### 4.2 `lib/pointer.ts` (new, pure)
528:### 4.3 `lib/cursorShape.ts` (new, pure)
532:### 4.4 `lib/cursorGlyphs.ts` (new) and `components/ScreenPointer.tsx` (new)
538:### 4.5 `lib/twoFinger.ts` (new), `lib/gestures.ts`, `lib/directGestures.ts`
554:### 4.6 `lib/screenMap.ts`
559:### 4.7 `components/ScreenSurface.tsx`
569:### 4.8 `pages/app/ScreenPage.tsx`
590:### 4.9 Stores and runtime
596:### 4.10 Tests (`cd mobile-app && pnpm typecheck && pnpm lint && pnpm test && pnpm build`)
608:### 4.11 Readout (staging builds only; `screenStats.ts readoutLines`)
616:### 4.12 Docs
623:### 5.1 `tests/screen_phone.py`
626:### 5.2 `tests/agent_1_4.py` (new)
629:### 5.3 `tests/screen_agent.py` control ops (added to `_control_ops`)
635:### 5.4 `tests/test_e2e_screen.py` additions
640:### 5.5 `tests/test_e2e_versions.py` additions (fixture `agent14` starts `agent_1_4.py`)
```

## 9. Review log (adversarial findings, 2026-11-03)

Every claim below was re-checked in the tree before the decision; "accepted" means the fix is written into the sections named.

| # | Finding | Decision | Why / where |
| --- | --- | --- | --- |
| 1 | D13 consent inheritance is unbounded (one Allow lasts for ever; `max_duration` restarts per replacement) | **Accepted** | `session.py:1242` counts `max_duration` from `started_at`; a replacement is a new `ScreenSession`. Fix: `chain_started_at`, inheritance only while the chain is younger than `max_seconds`, watchdog on `chain_started_at` (D13, §1.7 (1b), §3.3, §3.11). |
| 2 | D13 inheritance bypasses `ConsentGate.decide()` (ask-mode switched on mid-session; the Stop/cooldown race between the two `live_session` reads) | **Accepted** | `consent.py:184-199` is the only place ask-mode/block/cooldown are evaluated; `agent.py:756` writes `ask_each_time` immediately; `_start_session` awaits between the reads (`session.py:530-540`). Fix: always `decide()`, inherit only an explicit `granted` for `ask_mode`/`backoff`, never for cooldown/blocked, re-validate after the second read (§3.3, tests §3.11). |
| 3 | Confinement cannot keep "the person at the PC is never constrained" (GetCursorPos cannot tell our move from theirs) | **Accepted** | `input_session.py:849-851` + a plain `cursor_position()`. Fix: read before and after; confine only inside→outside; reworded rule (5), WINDOWS_INSTALL copy; tests (D15, §1.7, §3.4, §3.11, §5.4, §6). |
| 4 | `pngDimensions` can throw out of the data-channel handler on a schema-valid 33-character `png` | **Accepted** | `screen.ts:998-1012` guards only the parse; the pattern allowed non-multiple-of-4 lengths. Fix: base64-valid-length pattern, try/catch in `pngDimensions` and around the `switch`, fixture + tests (D11, §1.4, §1.9, §4.1, §4.3, §4.10). |
| 5 | `"default": "drawn"` on `cursor` makes every start 1.5 and leaks `result.cursor` to 1.4 phones (blocking) | **Accepted** | `registry.ts:150-151` and `registry.py:169-173` copy defaults into the signed/forwarded params; `commands.ts:70-92` signs them; `actions/screen.py:40-42` reads the filled dict. Fix: no default; load check "no default on a `param_since` param"; stamp and post-schema check over RAW params; agent reads presence from `ctx.command.payload["params"]`; tests (D3, D8, §1.2, §1.7, §1.8, §2.4, §3.2, §3.11, §4.10). |
| 6 | The PWA's no-eval registry facade is not in the spec; the bundle would stamp `spec.since` and overlay mode would silently degrade | **Accepted** | `vite.config.ts:26-37` redirects `./registry.ts` for dev/test/build; `mobile-app/src/protocol/registry.ts` has no `paramSince`/`minVersion`; `versions.ts:1-6` is documented registry-free. Fix: facade owned by the step-1 builder with `paramSince` + `minVersion`; `commandProtocolVersion` in `commands.ts`; parity tests; the hand-written union extended (§1.4, §1.8, §8). |
| 7 | "A 1.4 agent keeps Next screen" is false in ask mode/cooldown, and a refused switch kills the view on the phone | **Accepted** | `session.py:529` runs `_consent` before `_end` (`:547`); `WINDOW_INTERVAL_SECONDS = 60`. Fix: honest rows in D28/§1.10/§1.11/§5.5 (and D25); phone keeps the old heartbeat and restores the old session on a failed switch (D14, §4.1, §4.10). |
| 8 | Rationale names the wrong error: a 1.4 agent answers `INVALID_PARAMETERS`, not `MALFORMED_MESSAGE`, for an unknown param | **Accepted** | `command.schema.json` leaves `params` open; `registry.py:168`/`registry.ts:148` raise `INVALID_PARAMETERS`. Fix: wording in D3, §1.7, §1.10, §2.3; the surface-gone retry is conditional on "no `cursor` in the start" (D21, §4.1). |
| 9 | REST `pc.protocol_versions` breaks an open stale PWA until reload; the precedent claim is unsubstantiated | **Accepted** | `api.ts:160-166` validates strictly, `$defs/pc` is `additionalProperties:false`; `git log -S` shows the last `pc` additions in 9698041 (2026-10-08, before any deploy); `pwa.ts:14` `autoUpdate` applies on the next load. Fix: honest D28 sentence; devices store turns `MALFORMED_MESSAGE` from `pcs()` into an update notice + `requestAppUpdate()` (§1.5, §2.5, §4.8, §4.9, §4.10, §4.12). |
| 10 | Touchpad prediction scaled with the encode width (geometry 1280×720), not the monitor's physical width | **Accepted** | `worker/main.py:251, 318-333` put `fit_level_31` sizes in `geometry`; `screen.ts:531-532` seeds from the physical surface and is then overwritten. Fix: `surfaceSize()` = `state.surfaces[surfaceIndex]` (physical), explicit in D18/§4.1/§4.2, pointer test 2560×1440 vs 1280×720 (§4.10). |
| 11 | A switch cannot show "Switching…" over the last picture: `onTrack` replaces the stream and the `<video>` goes blank for the whole handshake; `play()` may reject in Low Power Mode | **Accepted (option b)** | `screen.ts:655-669` sets `stream` on the verified track; `ScreenSurface.tsx:115-149` reassigns `srcObject`; lines 152-177 call `play()` per stream. Fix: `incomingStream` on a second hidden `<video>`, roles swap on its first frame, `switching` yields to `playHint` (D29, §4.1, §4.7, §4.8, §4.10); the §7 question reframed to the remaining moment. |
| 12 | The old peer's data-channel handlers stay wired during a switch; a late `onclose` stops the NEW session's heartbeat | **Accepted** | `screen.ts:457-461` bind unguarded handlers; only `closePeer()` (1232-1243) nulls them. Fix: detach at switch time, every handler checks it belongs to the current object, test "old close after new open leaves hb running" (§4.1, §4.10). |
| 13 | `cursor_pos` on a reliable ordered channel: a lost packet releases a stale burst (replayed as a backward hop) and a lost final report arrives ≥ 1 s late | **Accepted (both parts)** | `screen.ts:455` `{ordered: true}` reliable; aiortc `SCTP_RTO_MIN = 1`, fast retransmit at `_misses == 3` (`rtcsctptransport.py:49-50, 1211`); aiortc honours `maxRetransmits` on its sends (`:1341-1345, 1709`). Fix: second channel `dome-screen-cursor-v1` `{ordered: false, maxRetransmits: 0}` with reliable fallback, worker dispatch by label, Python phone, one report per frame last-wins (D12, §1.4, §1.6, §1.7, §3.5, §3.7, §4.1, §4.2, §5.1, §5.4). |
| 14 | The WGC cursor-off error surfaces from `start_free_threaded()`, not the constructor; the class name is always `Exception` | **Accepted** | `windows-capture-python/src/lib.rs:176-209, 327-330`; `graphics_capture_api.rs:33, 138-139`; today's code already wraps both (`capture_wgc.py:216-218`). Fix: wrap construct+start, text match only, fake raises from `start_free_threaded()` (D9, §3.6, §3.11). |
| 15 | DI_MASK into a 32-bpp DIB yields white/black pixels, but `alpha_from_mask` was specified over packed 1-bpp bits | **Accepted (DI_MASK pixel path)** | Win32: `DrawIconEx(DI_MASK)` paints the mask into the selected surface; packed bits come only from `GetDIBits(hbmMask, 1-bpp)`. Fix: `alpha_from_mask(mask_bgra, …)` reads the DI_MASK DIB's pixels; hotspot stays in cursor pixels (§3.7, §3.11). |
| 16 | Founder item 3 delivered only in overlay mode: zoomed Touchpad view cannot follow on a 1.4 agent or a `drawn` session | **Accepted** | `ScreenPointer` was the only feeder and renders only in overlay mode; the touchpad machine has no one-finger pan. Fix: `PointerModel.followSample()`/`seedFollow` in every mode, follow driven by `ScreenSurface`, copy reworded ("On an up-to-date PC the pointer comes along"), tests (D19, §4.2, §4.7, §4.8, §4.10). |
| 17 | A switch refused by the relay (`RATE_LIMITED` 6/min, `SCREEN_ALLOWANCE_EXHAUSTED`, `PC_OFFLINE`) destroys the working session | **Accepted** | `plans.json` `screen_start_rate_limit {6, 6}`, `router.py:389` before `reserve_start`; `fail()` (`screen.ts:1199-1211`) nulls the stream; `sendChannel` uses `this.dc` (984-987). Fix: old heartbeat kept, restore unless the old id's `ended` was seen, `COPY.switchFailed`, tests, DECISIONS #86 (D14, §4.1, §4.8, §4.10, §4.12). |
| 18 | Adding `"png"` to `SENSITIVE_KEY_PARTS` redacts the spec's own `*png*` counters in logs and the bundle | **Accepted (rename)** | `logsetup.py:114-118` substring match; `diagnostics.py:51` uses it. Fix: every counter/log key says `bitmap` (`bitmap_chars`, `cursor_bitmap_max_chars`, `cursor_bitmap_too_large`); `png` is only the message field; tests assert counters present and the exact `png` key redacted (D26, §3.3, §3.7, §3.10, §3.11). |
| 19 | The e2e stamp assertions depend on the cloud-api harness's command builder, which no builder was allowed to change; the spec's pointer (`tests/conftest.py:343`) is the confirmation builder | **Accepted** | `tests/conftest.py:65` `ControllerSim = harness.ControllerSim`; `cloud-api/tests/conftest.py:953` stamps `min_version(action)` without params; `tests/conftest.py:343` is the confirmation stamp. Fix: pointer corrected, `conftest.py:953` assigned to the cloud-api builder with a harness test, listed in the §8 interface contract (§1.8, §2.4, §8). |
| 20 | The new probe re-gates the only encoder verified on a device (software h264_mf) and nothing says so | **Accepted (hardware-only gate)** | `encode.py:487-543` accepts after open + one encode with no packet requirement. Fix: the slice/SPS-PPS gate applies to the hardware candidate; the software candidate keeps today's acceptance with the probe's facts recorded (`gated: false`); KNOWN_ISSUES #35 and §7 item 5 say the NV12 software path is not re-verified (D22, §3.8, §3.11, §3.12, §7). |
| 21 | WGC refusal raised from `start_free_threaded()`, not the constructor (duplicate of 14, with the fake-test detail) | **Accepted (merged into 14)** | Same evidence. The fake raises the exact text from `start_free_threaded()`; a second test keeps other texts as `SCREEN_CAPTURE_UNAVAILABLE` (§3.11). |
| 22 | "Bars kept across the replacement and re-armed" leaves `_bring_up` on already-armed bars unspecified; the e2e "did not drop to zero" assertion detects nothing | **Accepted** | `indicator.py:68-104` `arm()` always calls `show_screen_bars`; `bar_requests` is a cumulative length (`screen_agent.py:51-62`). Fix: reuse when `armed && monitors == tuple(monitors)` and same viewer/audio, else `disarm()` + `arm()`; unit and e2e assertions on unchanged `bar_requests`/`bars_hidden` (D13, §3.3, §3.11, §5.4). |
| 23 | The e2e "click's recorded position is inside monitor 1" cannot be made with `test_input` unchanged | **Accepted** | `fake_platform.py:470-512` records `button` without a position; `test_input` returns only the final cursor. Fix: `InputRecord.cursor`, `test_input` rows `[name, *args, "@", x, y]`, unit test asserts the same (§3.9, §3.11, §5.3, §5.4). |
| 24 | Direct-mode two-finger scroll moves the real pointer (`pointer_move_to` midpoint) but the spec predicted only taps and loupe ends | **Accepted** | `screenInput.ts:72-74`. Fix: `drainDirect` `scroll` → `pointer.predictTo(midpoint)` before send; test (D18, §4.7, §4.10, §7 item 2). |
| 25 | Two helpers used but never defined: `fitRef` and `client.surfaceSize()` | **Accepted in part** | `ScreenSurface.tsx:79` has only `viewRef`; `fit` is recomputed inside `locate()` (191); `screen.ts` has no `surfaceSize`. `fitRef` is defined as proposed (§4.7; null → hidden; tests §4.10). `surfaceSize()` is defined, but **as the physical surface** (finding 10), not the GeometryTracker's encode-size `width/height` the finding proposed, which would reproduce the 1.5–2× prediction error (§4.1). |

Rejected outright: none. Partially accepted: #25 (the `surfaceSize` definition it proposed conflicts with #10 and was replaced by the physical size).

## 8. Build order and what each builder must NOT touch

**Step 1 — Contract (one builder, lands first; everything else waits for it):** §1 in full: `version.json`, `actions.json` (no default on `cursor`), the three schemas, rule text, `errors.json` description, both libraries (`param_since` with the no-default load check, `min_version(params)` over raw params, `command_protocol_version` in `commands.py`/`commands.ts`, the post-schema since check over raw params, the answer stamp argument, the parser cap, `SCREEN_CURSOR_CHANNEL_LABEL`, the hand-written `ScreenDcPcToPhone` union), **the PWA registry facade `mobile-app/src/protocol/registry.ts`** (`paramSince`, load validation, `registry.minVersion`), fixture generators + regenerated `es256-*.json` + `screen-dc-cases.json`, `pnpm gen:types` and `pnpm gen:validators` outputs committed, the `protocol-parity.test.ts` stamping cases, `PROTOCOL.md` §21, `shared/protocol/README.md`. Touches only `shared/**`, `mobile-app/src/protocol/**` (the facade and `generated/**`), `mobile-app/test/protocol-parity.test.ts`, `docs/PROTOCOL.md`. Must NOT touch `cloud-api/`, `pc-agent/`, `mobile-app/src` outside `protocol/`, other `mobile-app/test` files, `tests/`.

**Step 2 — in parallel after step 1:**
- **cloud-api builder** (§2): touches `cloud-api/**` only (model, migration 0006, `agent_ws.py`, `routes/pcs.py`, `router.py`, tests including `tests/conftest.py:953` so the harness stamps by content, DECISIONS/KNOWN_ISSUES). Must NOT touch `shared/`, `pc-agent/`, `mobile-app/`, `tests/`.
- **pc-agent builder** (§3): touches `pc-agent/**` only (including `dome_agent/testing/*` doubles, `__init__.py`, DECISIONS/KNOWN_ISSUES/CONTRACT_ISSUES). Must NOT touch `shared/`, `cloud-api/`, `mobile-app/`, `tests/`.
- **mobile-app builder** (§4): touches `mobile-app/src/**` (except `protocol/**`), `mobile-app/test/**` (except `protocol-parity.test.ts`), mobile-app docs. Must NOT touch `shared/`, `cloud-api/`, `pc-agent/`, `tests/`, `mobile-app/src/protocol/**`.

**Step 3 — after steps 2 land:** **e2e + docs builder** (§5, §6): touches `tests/**` (`screen_phone.py`, `screen_agent.py`, `agent_1_4.py`, `test_e2e_screen.py`, `test_e2e_versions.py`), `docs/design/screen-view.md`, `docs/WINDOWS_INSTALL.md`, `docs/ACCEPTANCE.md`, `docs/PROGRESS.md`, `docs/HANDOFF.md`. Must NOT touch component source; a failing e2e is reported back to the owning component builder with the test name.

Interface contract the parallel builders rely on without talking (all fixed by this document): the `start` pipe op keys `cursor`/`hw_encoding` and the worker events `cursor`/`encoders`/stats counters (pc-agent internal); the control ops `test_set_monitors`/`test_cursor_script`/`test_set_cursor` and the `InputRecord.cursor` field behind `test_input` (pc-agent provides on the fake platform through `tests/screen_agent.py`'s seam: the pc-agent builder exposes `FakeState.monitors` + `on_display_change()`, `InputRecord.cursor` and the `cursor_script` source-spec key; the e2e builder writes the ops); **the cloud-api test harness stamps commands by content** (`ControllerSim.command` passes `params` to `min_version`; the e2e builder relies on it for every `cursor` start); the REST field name `protocol_versions`; `ScreenClient.start(surface, cursor)` in the Python phone; the result key `cursor` present only when the signed params carried it; the answer stamp = the start's stamp; the DC vocabulary of §1.4 and the channel labels `dome-screen-v1`/`dome-screen-cursor-v1` (cursor_pos on the latter when open); the error detail `{peer: "agent", required: "1.5"}`; no counter or log key containing `png`.

Check commands (every builder, green before handing over): `cd shared/python && uv run --frozen pytest -q && uv run ruff check . && uv run mypy`; `cd shared/ts && pnpm test && pnpm typecheck && pnpm lint`; `cd cloud-api && uv run --frozen pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy dome_api tests`; `cd pc-agent && uv run --frozen pytest -q tests && uv run --frozen ruff check . && uv run --frozen ruff format --check . && uv run --frozen mypy` (both configs); `cd mobile-app && pnpm typecheck && pnpm lint && pnpm test && pnpm build`; `cd tests && uv run --frozen pytest -q` (PostgreSQL at `/tmp:54329`). Nobody commits or pushes; the orchestrator does.

---


### Final text of section 1.8

### 1.8 Shared libraries

**shared/python (`dome_protocol`)**
- `registry.py`: `ActionSpec.param_since: dict[str, str]` (default empty); load validation per §1.2 (including "no default on a `param_since` param"); `Registry.min_version(name, params: Mapping[str, Any] | None = None) -> str` = `since` raised to the max `param_since` of keys **present in the mapping given** (callers pass the raw params, never the output of `validate_params`).
- `versions.py`: `command_protocol_version(action: str, params: Mapping[str, Any], *, registry: Registry | None = None) -> str` (delegates to `min_version`). `SCREEN_INPUT_PROTOCOL_VERSION` stays `"1.4"` (no new input events).
- `commands.py`: `verify_and_parse_command` keeps `_check_stamp(payload, registry, since=registry.min_version(action))` before schema (line 191); after `registry.validate_params(spec.name, payload["params"])` (line 203) it calls `_require_since(payload["protocol_version"], registry.min_version(spec.name, payload["params"]))` — the RAW `payload["params"]`, not the default-filled `params` — so a 1.4-stamped start carrying `cursor` is `PROTOCOL_INCOMPATIBLE {peer, required: "1.5"}` and a 1.4-stamped start without it passes. `VerifiedCommand.payload["params"]` (`commands.py:74-80`) stays the raw object: the agent reads param presence from it (§3.2).
- Test harness stamps: the command stamp lives in `cloud-api/tests/conftest.py:945-956` (`ControllerSim.command`: `REGISTRY.min_version(action) if known else REGISTRY.protocol_version`), which `tests/conftest.py:65` reuses for the e2e Python phone (`tests/screen_phone.py:360`); it becomes `REGISTRY.min_version(action, params)` (§2.4). `tests/conftest.py:343` is the confirmation builder and is unchanged.
- `screen.py`: `SCREEN_CURSOR_CHANNEL_LABEL`; `build_screen_answer_payload(..., protocol_version: str)` (required keyword) replaces `registry.protocol_version` at line 105; `parse_screen_dc_message` per §1.6. The verifier at lines 181-184 is unchanged (compatibility rule).
- `PROTOCOL_VERSION` is derived from `version.json` (`registry.py:77`): nothing hard-coded changes.

**shared/ts (`@dome/protocol`)**
- `registry.ts`: `ActionSpec.paramSince: Record<string, string>` parsed from `r.param_since`; load validation (incl. the no-default check); `registry.minVersion(action: string, params?: Record<string, unknown>): string` over the keys of the object given.
- `commands.ts`: the existing `minVersion(action)` at lines 68-70 becomes `minVersion(action, params?)` delegating to `registry.minVersion`; new export `commandProtocolVersion(action, params)` = the same (it lives HERE, where `registry` is already imported and substituted by the PWA facade, **not** in `versions.ts`, which is documented as registry-free, `versions.ts:1-6`). `buildCommandPayload` (line 92) stamps `protocol_version: commandProtocolVersion(spec.name, input.params ?? {})` — the raw input, evaluated before/independently of `validateParams`; `verifyAndParseCommand` adds the post-schema `requireSince` against `minVersion(action, payload.params)` (raw).
- **`mobile-app/src/protocol/registry.ts` (the PWA's no-eval facade, owned by the step-1 contract builder, §8):** `vite.config.ts:26-37` (`domeProtocolNoEval`) resolves every `./registry.ts` import made from inside `shared/ts/src` to this file for dev, test and build alike, so it must mirror: `ActionSpec.paramSince` (lines 29-52, parsed at 86-110 with the same load validation) and `registry.minVersion(action, params?)` (the `registry` object at line 115). Without it the shipped bundle would stamp every start 1.4 and overlay mode would silently degrade through D7.
- `screen.ts`: `SCREEN_CURSOR_CHANNEL_LABEL`; `buildScreenAnswerPayload({ ..., protocolVersion })` (required) replaces `PROTOCOL_VERSION` at line 89; `parseScreenDcMessage` per §1.6; export `CursorKind`; the hand-written union extended (§1.4).
- `PROTOCOL_VERSION`/`REGISTRY_VERSION` derive from JSON (`registry.ts:62-63`).

**Tests whose literals change** (every `required: "1.4"`, `since` 1.4 and "relay shields pre-1.4 agents" assertion stays true):
- `shared/python/tests/test_screen_contract.py:530-531, 616-617, 632-647` and `shared/ts/test/screenContract.test.ts:376-377, 447-448, 473-474`: the newer-MINOR stamp becomes `"1.6"` and the detail `{"peer": "1.6", "supported": ["1.5"]}`.
- `shared/python/tests/test_registry_and_schemas.py:247, 360, 425`, `test_input_batch.py:110`, `shared/ts/test/registry.test.ts:82`, `stamping.test.ts:75, 87`: `"1.5"` as the "too new" literal becomes `"1.6"` (and `"1.5"` becomes a supported version where the test enumerates them).
- New tests (both libraries): `command_protocol_version("screen.session_start", {offer_sdp, surface: 1})` → `"1.4"`; with `cursor: "overlay"` → `"1.5"`; `min_version` ignores defaults (a `validate_params` output without `cursor` still stamps 1.4); a 1.4-stamped start carrying `cursor` is `PROTOCOL_INCOMPATIBLE {peer: "controller"|…, required: "1.5"}` after schema; a 1.4-stamped start WITHOUT `cursor` passes the post-schema check; `build_screen_answer_payload(protocol_version="1.4")` verifies against a `Registry` copy whose `protocol_version = "1.4"`; the §1.6 size cases; `param_since` load validation incl. the default check.
- `mobile-app/test/protocol-parity.test.ts` (lines 279-291 today check `since` only): for every action `minVersion(name, {})` equal between facade and real module; for `screen.session_start`, `minVersion(name, {cursor: "overlay"})` is `"1.5"` in both; `buildCommandPayload` with `cursor` yields `protocol_version "1.5"` and without it `"1.4"` under the facade.

### 1.9 Fixtures

- `shared/python/scripts/make_fixtures.py:164`: `newer_stamp = f"{major}.{minor + 1}"` computed from `registry.protocol_version`; `shared/ts/scripts/make-fixtures.ts:152`: the same from `PROTOCOL_VERSION`. `$comment` → "Protocol 1.4+. The newer-MINOR case is stamped one MINOR above this library's version."
- The good/tampered/by-controller-key cases pass `protocol_version=registry.protocol_version` (so the fixture's accepted answer is a 1.5 answer; the e2e "1.5 agent answers a 1.4 phone in 1.4" proves the other direction).
- Regenerate: `cd shared/python && uv run python scripts/make_fixtures.py` → `shared/protocol/fixtures/es256-python.json`; `cd shared/ts && pnpm fixtures` → `es256-typescript.json`; both signed with `shared/protocol/fixtures/test-pc-key.pem` / `test-controller-key.pem`; commit both. `test_fixtures.py` / `fixtures.test.ts` cross-check.
- New `shared/protocol/fixtures/screen-dc-cases.json`: `{ "accepted": [...], "rejected": [...] }`, each case `{ "name", "direction", "text" }` with `text` the exact message string. Accepted (pc_to_phone): a `cursor_pos`; a named `cursor_shape{kind: "ibeam"}`; a custom `cursor_shape` carrying a real 1×1 RGBA PNG (generated by the fixture scripts with PIL / a byte literal); a `state{sending, encoder: "h264", cursor: "overlay"}`; a 9000-byte custom `cursor_shape` (PNG padded with a valid tEXt chunk). Rejected: custom without `png`; `png` containing `-`; a 33-character `png` (`"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="`, base64-invalid length); `width: 65`; `kind: "custom"` with `visible` key; a 1025-byte `state` (pc_to_phone); a 12289-byte `cursor_shape`; a `cursor_pos` without `visible`; a 1025-byte `hb` (phone_to_pc); a `cursor_pos` sent phone_to_pc. Run by `test_shared_cases.py` and `sharedCases.test.ts`.


### Final text of section 1.4 tail

  "description": "Since 1.5. An application's own pointer as a 32-bit RGBA PNG of exactly width x height (at most 64 x 64; the PC scales larger cursors down, hotspot included), standard base64 (base64-valid length and padding; the PC's base64.b64encode output always matches) of at most 8192 characters, with its hotspot in bitmap pixels. A cursor bitmap is screen content (rules.screen_content): never logged, stored or put in a diagnostics bundle by either side. This is the only data-channel message that may exceed screen_dc_message_max_bytes; it is bounded by screen_dc_cursor_shape_max_bytes instead."
}
```

The `png` pattern admits only lengths ≡ 0 (mod 4) with correct padding, so a decoder's `atob` of any prefix that is a multiple of 4 cannot throw on padding; python `re` and the precompiled Ajv both accept the pattern.

`state` member, after `data_saver`:

```json
"cursor": { "type": "string", "enum": ["drawn", "overlay"], "description": "Since 1.5. The pointer mode in effect; present in every state message of a session whose start carried `cursor`, absent otherwise." }
```

Generated artefacts: `cd shared/ts && pnpm gen:types` (commit `src/generated`); `cd mobile-app && pnpm gen:validators` (commit `src/protocol/generated`; `screen_dc_pc_to_phone` is already in `RELAY_FRAME_DEFS` at `gen-validators.ts:36`, nothing to add there; the `rest` document is regenerated by the same run). TS types that must exist after generation: `ScreenDcCursorPos`, `ScreenDcCursorShapeNamed`, `ScreenDcCursorShapeCustom`, and the union member names of `ScreenDcPcToPhone` (whatever `json-schema-to-typescript` emits; the builder exports `CursorKind` as the named-kind enum type from `shared/ts/src/screen.ts`). The **hand-written** union `ScreenDcPcToPhone` in `shared/ts/src/screen.ts:192-196` (what `parseScreenDcMessage` returns and `onChannelMessage` narrows on) is extended by hand: `state` gains `cursor?: "drawn" | "overlay"`, and the members `{ type: "cursor_pos"; gen: number; x: number; y: number; visible: boolean }`, `{ type: "cursor_shape"; kind: CursorKind }`, `{ type: "cursor_shape"; kind: "custom"; width: number; height: number; hotspot_x: number; hotspot_y: number; png: string }` are added.


### Final text of section 2.4

### 2.4 Tests (`cloud-api/tests`, real PostgreSQL)
- `conftest.py:74 DEFAULT_PROTOCOL_VERSIONS += "1.5"`; `test_auth_and_sessions.py:15` → `"1.5"`; `test_relay_routing.py:136` → `supported == ["1.5"]`; `test_relay_routing.py:668`'s "too new" literal → `"1.6"`.
- **`conftest.py:953` (`ControllerSim.command`):** `protocol_version = REGISTRY.min_version(action, params) if known else REGISTRY.protocol_version` — the harness stamps by content, exactly like the phone. This line is shared with the e2e suite (`tests/conftest.py:65`), so the cloud-api builder owns it and the e2e builder relies on it (§8). Harness test (`test_harness.py` or next to the sim): a `screen.session_start` with `cursor` is stamped `"1.5"`, one without stays `"1.4"`, an explicit `protocol_version=` still overrides.
- `test_screen_routing.py` new: `test_stamp_gate_shields_a_1_4_agent_from_a_1_5_start` — `AgentSim(protocol_versions=("1.0",…,"1.4"))`; a 1.5-stamped `screen.session_start` → `result{origin: relay, failed, PROTOCOL_INCOMPATIBLE, detail {peer: "agent", required: "1.5"}}`, the agent socket received nothing, `screen_reservations` has no new row, the ledger is unchanged, the start rate limiter spent no token; a 1.4-stamped start to the same sim is forwarded; both to a default (1.5) sim are forwarded; a 1.4-stamped start WITHOUT `cursor` to a 1.5 sim is forwarded and the sim's result without `cursor` is relayed unchanged. The existing `test_stamp_gate_shields_a_1_2_agent_from_1_4_batches` (line 833) stays.
- `test_entitlement_and_misc.py`: `GET /v1/pcs` body validates; shows `["1.0",…,"1.5"]` while a sim is online; after the sim disconnects the row still shows the list; a PC that never connected shows `[]`.
- `test_screen_migration.py` (or a new `test_pcs_migration.py`): `0006` upgrade/downgrade round-trip, default `[]`.

### 2.5 Docs
`cloud-api/DECISIONS.md` #115 "`pc.protocol_versions` is a persisted REST field, not a `pc_status` field" (why: `pc_status` is `additionalProperties:false` and `broadcast_to_subscribers` sends one frame to every subscriber; REST answers offline and follows `agent_version`; an open stale PWA sees `MALFORMED_MESSAGE` on `/v1/pcs` until it reloads, which the PWA turns into an update notice); #116 "The relay refuses any command whose stamp the agent did not announce before forwarding" (generalises the 1.4 batch gate; placement before the rate limiter and `reserve_start`). `KNOWN_ISSUES.md`: "`protocol_versions` is as announced at the last hello; a downgraded agent shows its shorter list only after reconnecting".

---


### Final text of section 3.5

### 3.5 Worker: pipe protocol and channels
- `start` gains `cursor: "drawn" | "overlay"` (absent for a 1.4-stamped start) and `hw_encoding: bool`.
- New events: `{"ev": "cursor", "mode": "drawn"|"overlay", "source": "dda"|"wgc"|"fake"}` once per source open (only when `start.cursor` was present); `{"ev": "encoders", "probed": [ProbeResult.as_dict()...]}` after `answer`; `stats` gains the cursor counters and `encoder_hw`.
- `_state_message()` (main.py:364) adds `"cursor": self._cursor_effective` when `self._cursor_requested is not None` (the guess until the source opened; then the effective mode).
- `_shape_for_vocabulary(message)` in `main.py`: when `self._cursor_requested is None` (a 1.4 session) it deletes `cursor` from a `state` message and refuses to send `cursor_pos`/`cursor_shape` (returns None → not sent, counted `dropped_1_4`). `_send_dc` passes every outbound message through it; `rtc.send_dc` still validates against `pc_to_phone`.
- `rtc.py` `_on_datachannel(channel)` (line 342) dispatches by label: `SCREEN_DATA_CHANNEL_LABEL` → today's handling (`self.channel`); `SCREEN_CURSOR_CHANNEL_LABEL` → `self.cursor_channel = channel`, any inbound message on it is dropped and counted (`dc_dropped`); any other label → `channel.close()`, counted `dc_unknown_channels`. New `send_cursor_pos(message) -> bool`: validates like `send_dc`, sends on `cursor_channel` when its `readyState == "open"` (counted `cursor_pos_unreliable`), else falls back to `send_dc`. aiortc applies the phone's partial-reliability parameters to its own sends (`rtcsctptransport.py:1341-1345, 1709`: `_max_retransmits`, abandoned chunks, forward TSN); the loopback test proves the second channel opens and carries `cursor_pos`; the no-retransmit property itself is not asserted on Linux.


### Final text of section 3.9/3.10

### 3.9 Testing doubles
- `testing/fake_screen_worker.py`: accepts `cursor` and `hw_encoding` in `start`; emits `{"ev": "cursor", "mode": script.cursor_mode or requested, "source": "fake"}` after `connected` when the start carried `cursor`; `script.cursor_mode: str | None`.
- `testing/fake_platform.py`: `FakeState.cursor_capture_configurable: bool = True`; `FakeScreen.cursor_capture_configurable()`. `InputRecord` gains `cursor: tuple[int, int]` (the fake's cursor at injection time, captured in `_inject`, `fake_platform.py:479-489`), so a `button` record says where the click landed.

### 3.10 Diagnostics and redaction
- `diagnostics.py`: `status.screen.session.media.cursor {mode, pos_sent, pos_unreliable, shape_sent, custom_sent, bitmap_max_chars, bitmap_too_large, dropped_gen, poll_errors, last_kind}`, `media.encoders` (probe rows incl. `gated`), `media.encoder_hw`, `status.screen.session.surface {index, switched_from}`, `status.screen.session.consent {value, inherited, chain_seconds}`, `status.input.session.confined_moves`, `status.settings.screen_h264_hw`. No key under any of these contains `png`.
- `logsetup.py`: `"png"` added to `SENSITIVE_KEY_PARTS` (substring rule, `logsetup.py:114-118`; the same rule redacts the bundle, `diagnostics.py:49-56`). Mirrored in `crashguard.py` only if it copies the key list (it copies the address patterns only today).
- Licence gate: unchanged inventory (no new DLL; PIL already bundled; `user32`/`gdi32` are system).


### Final text of section 4.1

### 4.1 `lib/screen.ts` (`ScreenSessionClient`)

State additions (`ScreenSessionState`, with `EMPTY_SCREEN_STATE` defaults):

```ts
surfaceIndex: number | null;                                   // result.surface_index, then geometry.surface_index
switching: { to: number; from: number | null; startedAt: number } | null;
switched: { index: number; count: number; at: number } | null; // drives the 1.5 s "Screen 2 of 2" chip
switchedGone: { human: number; at: number } | null;            // drives the 3 s "Screen N isn't connected any more" chip
switchFailed: { code: string; human: number; at: number } | null; // drives the 3 s "Couldn't switch screens" chip
incomingStream: MediaStream | null;                            // the new session's stream during a switch (second, hidden <video>)
cursorMode: "drawn" | "overlay" | null;                        // result.cursor (provisional) then state.cursor (authoritative); null = 1.4 session
pointerOverlayAsked: boolean;                                  // this start carried cursor: "overlay"
```

Deps: `ScreenClientDeps.pcProtocolVersions(pcId: string): readonly string[]` (from the devices store). Instance fields (not state): `pointer: PointerModel` (public, §4.2), `noCursorFor: string | null`, `previous: PreviousSession | null` (below), `lastSurfaceByPc: Map<string, number>`, `cdc: RTCDataChannel | null` (the cursor channel), `latestCursorPos: PointerReport | null` (one slot, drained once per frame).

**Channels** (`createPeer`, line 455): after `dc = pc.createDataChannel(SCREEN_DATA_CHANNEL_LABEL, { ordered: true })` also `cdc = pc.createDataChannel(SCREEN_CURSOR_CHANNEL_LABEL, { ordered: false, maxRetransmits: 0 })` (same offer, no renegotiation); `cdc.onmessage = (ev) => this.onCursorChannelMessage(cdc, ev.data)` accepts only a parsed `cursor_pos` (anything else: `dcDropped++`). Every handler takes its channel/peer and **ignores an object that is not the current one**: `dc.onmessage = (ev) => this.onChannelMessage(dc, ev.data)`, `dc.onclose = () => { if (dc === this.dc) this.stopHeartbeat(); }`, `dc.onopen = () => { if (dc === this.dc) this.onChannelOpen(); }`, `pc.ontrack = (ev) => this.onTrack(pc, ev)` (today's handlers at `screen.ts:457-461` are unguarded, so an old channel's late `close` would stop the new session's heartbeat).

`start(pcId, options: { takeover?: boolean; retries?: number; surface?: number | "primary"; switching?: { from: number | null } })`:
- When `options.switching` is absent: as today (`stop("restart" | "pc_switch")` when not idle/ended/failed). When present: **no `stop()`**; `this.nonce += 1`; clear every timer except the heartbeat; `this.previous = { pc: this.pc, dc: this.dc, cdc: this.cdc, sessionId, surfaceIndex, phase, stream: this.state.stream, hbTimer: this.hbTimer, endedByPc: false }`; **detach the old objects' handlers** (`dc.onopen = dc.onmessage = dc.onclose = null`, same for `cdc`, `pc.ontrack = pc.onconnectionstatechange = pc.oniceconnectionstatechange = pc.onicegatheringstatechange = null`: the first half of `closePeer()`, `screen.ts:1232-1243`) but **keep the old heartbeat running on the old `dc`** (`previous.hbTimer` keeps sending `hb` every `HEARTBEAT_MS` while `previous.dc.readyState === "open"`, so the lease never lapses during a switch: `rules.screen_sessions (3)`, `screen_lease_seconds = 8`); `this.pc = this.dc = this.cdc = null; this.hbTimer = null`; the old id is NOT retired yet (its `ended` frame must still be recognised); state reset keeps `kept = { allowance, otherViewer, surfaces, surfaceIndex, stream, sessionId: null, switching: { to: options.surface, from, startedAt: now } }`.
- `surface = options.surface ?? this.lastSurfaceByPc.get(pcId) ?? "primary"`.
- `speaks15 = protocolCompatible("1.5", this.deps.pcProtocolVersions(pcId)) && this.noCursorFor !== pcId`; params: `{ offer_sdp, surface, audio: false, takeover: options.takeover === true, ...(speaks15 ? { cursor: "overlay" } : {}) }`; the shared `buildCommandPayload` stamps 1.5 iff `cursor` is present in the params given (no default fills it in). `pointerOverlayAsked = speaks15`.
- Result failure handling (in `onResult`/the failed path): `PROTOCOL_INCOMPATIBLE` with `detail.required === "1.5"` while `pointerOverlayAsked` → `this.noCursorFor = pcId`, `log.info("screen.cursor_param_refused", { pc_id })`, restart once without `cursor` (same `surface`, same `retries`, `switching` preserved); `INVALID_PARAMETERS` while `surface !== "primary"` **and the start carried no `cursor`** (or the error message names `surface`) → `lastSurfaceByPc.delete(pcId)`, `switchedGone = { human: human(surface), at: now }`, retry once with `"primary"`; an `INVALID_PARAMETERS` on a start that carried `cursor` is reported, never retried as a surface problem (a pre-1.5 agent answers that code for an unknown param, D3).
- **A failed switching start** (any terminal failure, `noAnswer`, or a local error before the new `ontrack`): if `previous.endedByPc` (the old id's `ended` frame was seen: the agent took the session over and the old one is gone) → close `previous` and fall through to the existing `fail()`; otherwise **restore**: `this.pc = previous.pc; this.dc = previous.dc; this.cdc = previous.cdc`, re-attach the handlers, keep `previous.hbTimer` as `this.hbTimer`, `set({ phase: previous.phase, sessionId: previous.sessionId, surfaceIndex: previous.surfaceIndex, stream: previous.stream, incomingStream: null, switching: null, switchFailed: { code, human: human(previous.surfaceIndex), at: now } })`, `facts.surfaces.switchFailures++`, `log.info("screen.switch_failed", { code, origin })`, `previous = null`. This covers every `origin: relay` result (`RATE_LIMITED` from `limiters.allow_screen_start` at 6 starts/min, `router.py:389`; `SCREEN_ALLOWANCE_EXHAUSTED` from `reserve_start`; `PC_OFFLINE`; the D4 gate) and the 1.4 agent's `SCREEN_CONSENT_DECLINED` (its `_consent` runs before `_end`, so the old session is intact). If an `ended{replaced}` for the restored id arrives later it is handled as today (a `NO_RESUME` end).
- `noCursorFor` is cleared when `pcProtocolVersions(pcId)` later includes 1.5 (checked at each `start`).

`onEnded(frame)`: when `previous !== null && frame.screen_session_id === previous.sessionId` → `previous.endedByPc = true`, stop `previous.hbTimer`, and (if the new session already has `incomingStream` or `stream`) `closePeer(previous)`; no state change (the stage stays). Other `ended` frames as today.

`switchSurface(index: number): Promise<ScreenSessionState>`: no-op unless `phase ∈ {live, paused}`, `switching === null`, `index !== surfaceIndex` and `surfaces.some(s => s.index === index)`; then `start(pcId, { surface: index, retries: this.state.retries, switching: { from: this.state.surfaceIndex } })`. `nextSurfaceIndex(): number | null` = the index after `surfaceIndex` in `surfaces` sorted by `index`, wrapping.

`surfaceSize(): { width: number; height: number } | null` = `state.surfaces.find(s => s.index === state.surfaceIndex)` (physical pixels; `screen_surface` is documented "in physical pixels"), **never** `GeometryTracker.known.width/height` (the encode size after the first `geometry`, §0 D18).

`onResult`: `surfaceIndex = r.surface_index`, `cursorMode = r.cursor ?? null`, seeds the tracker as today; `lastSurfaceByPc.set(pcId, r.surface_index)`; `pointer.reset(); pointer.setMode(cursorMode)`; when switching, `this.retire(previous.sessionId)` happens here (the new session is real; late frames of the old one are now dropped except the `ended` frame, which `onEnded` matches by id before the retired check).

`onTrack(pc, ev)` (line 655): ignore when `pc !== this.pc`. When `switching !== null`: `set({ incomingStream: media })` (the visible `stream` is untouched: D29); else as today (`stream` or `pendingStream`).

`onVideoFrame` (line 874, now also fed by the hidden incoming video): on the first frame of the new session while `switching !== null`: `set({ stream: incomingStream, incomingStream: null, switched: { index: surfaceIndex, count: surfaces.length, at: now }, switching: null })`, stop `previous.hbTimer`, `closePeer(previous)`, `previous = null`. (`ScreenSurface` swaps the two elements' roles, §4.7; it never sets `srcObject` on the visible element during a switch.)

`onChannelMessage(dc, data)` (line 998): ignore when `dc !== this.dc`; the whole `switch` body is inside `try/catch` (a thrown handler counts `dcDropped`, logs the error name only, never the payload). New cases: `state` → `cursorMode = msg.cursor ?? cursorMode`; when `msg.state === "paused"` → `pointer.hide()`; `geometry` → as today plus `surfaceIndex = msg.surface_index`, `pointer.geometryChanged()` (drops the predicted/reported position until the next report); `cursor_pos` (reliable-channel fallback) → `this.acceptCursorPos(msg)`; `cursor_shape` → `const s = parseCursorShape(msg); if (s) pointer.shape(s); else facts.pointer.rejected++`. `onCursorChannelMessage(cdc, data)`: ignore when `cdc !== this.cdc`; parse; `cursor_pos` → `acceptCursorPos`, anything else → `dcDropped++`. `acceptCursorPos(msg)`: `if (msg.gen !== this.geometry.known?.gen) { facts.pointer.droppedGen++; return; } this.latestCursorPos = msg` (one slot: last wins; `PointerModel.sample()` consumes it once per animation frame through `pointer.reported(this.takeLatestCursorPos(), now)`), `facts.pointer.positions++`. No `set()` per cursor message.

`restartFresh(cause)` passes `surface: this.state.surfaceIndex ?? undefined` and is a no-op while `switching !== null`. `stop()` unchanged, plus `pointer.reset()` and closing `previous` if set. `facts` (`ScreenClientFacts`) gain `pointer: { mode, shapes, custom, rejected, positions, unreliable, droppedGen, lastKind, predErrMaxPx }`, `surfaces: { count, showing, switches, switchFailures, gone }`, `zoom: { maxScale, followPans }` (numbers and short words only).


### Final text of section 4.7-4.9

### 4.7 `components/ScreenSurface.tsx`
- Props gain `pointer: PointerModel`, `incomingStream: MediaStream | null`, `surfaceSize: () => { width: number; height: number } | null` (the PHYSICAL size from `client.surfaceSize()`, §4.1), `cursorMode: "drawn" | "overlay" | null`, `pcPaused: boolean`, `switching: boolean`, `onFollowPan?: () => void` (counts follow pans for the readout), `onIncomingFrame?: () => void`.
- **`fitRef = useRef<Fit | null>(null)`**: refreshed with `fitContent(box, video.videoWidth, video.videoHeight)` (`screenMap.ts:60`) in the existing ResizeObserver callback, on the front video's `loadedmetadata`/`resize` events and on every stream/element swap; today `fit` is recomputed per call inside `locate()` (line 191) and a 60 Hz rAF loop has nothing to read without it. Read by `ScreenPointer` and by the follow logic (null → pointer hidden, no follow).
- **Two video elements** (D29): `videoA`/`videoB` refs and a `front: "a" | "b"` state. The front element carries `stream` and is visible; the back element carries `incomingStream` (mounted hidden, `playsinline muted`, same `play()` handling as today at lines 152-177) and drives `cb.current.onFrame()` from ITS `requestVideoFrameCallback` while it has a stream (so the client's first-frame watchdog and `onVideoFrame` observe the new session). When the client moves `incomingStream` into `stream` (first incoming frame), the roles swap by CSS (the back becomes front, `applyView(IDENTITY_VIEW)`, `fitRef` refreshed, the old front's `srcObject` set to null): **`srcObject` is never reassigned on the visible element during a switch**, so the last picture stays under the overlay. Outside a switch the behaviour of lines 115-149 is unchanged on the front element. The loupe, `locate()`, `onProbe` and `context()` read the front element.
- `drainTouchpad`: `pinch` → when `cursorMode !== "overlay"` and the follow point is unknown, `pointer.seedFollow(toSurface(cx, cy, fit, view))` first; then `applyView(snapIdentity(zoomAt(view, scale, cx, cy, box)))`; `move` → `pointer.predictMoveBy(dx, dy, surfaceSize(), now)` **then** `send(touchpadEvent(out))` (the same `dx, dy` that go into the batch: PC pixels, sensitivity applied by `emitMove`, `gestures.ts:288-296`); everything else as today. `drainDirect`: `tap` and `loupe end` → `pointer.predictTo(x, y, now)` when `locate()` was `ok` (normalised point from `screenInput.ts`); **`scroll` → `pointer.predictTo(x, y, now)` with the normalised midpoint before `send(...)`** (the batch is `[pointer_move_to(midpoint), pointer_wheel]`, `screenInput.ts:72-74`, so the PC's pointer jumps there); the ripple sets `rippleAt`. Pointer down/up in touchpad mode → `pointer.fingerActive(true|false, now)`.
- **Follow** (every mode of the PC, D19): `ScreenSurface` schedules one `requestAnimationFrame` after each touchpad `move` output (and while a pointer ease is `busy`) and, when `mode === "touchpad" && viewRef.current.s > 1 && fitRef.current`, reads `p = pointer.followSample()`; if `p` → `applyView(followPointer(view, fromSurface(p.x, p.y, fit, view), box))` (count `followPans` when the view changed). Not in direct mode. `ScreenPointer` is not involved (it is hidden on a 1.4 agent or in `drawn` mode, where the follow must still work).
- `1×` chip: `<button data-testid="screen-zoom-reset" className={STRIP_BUTTON + " pointer-events-auto absolute bottom-3 left-3 z-10"}>1×</button>` rendered while `view.s > 1` in both modes; `onClick → applyView(IDENTITY_VIEW)`.
- Mode switch resets the view to identity (added to the existing mode effect at line 255-267) and `pointer.fingerActive(false)`.
- Renders `<ScreenPointer pointer={pointer} fitRef={fitRef} viewRef={viewRef} enabled={cursorMode === "overlay"} hidden={loupe !== null || pcPaused || switching || rippleRecent} />` after the view layer and before the loupe.

### 4.8 `pages/app/ScreenPage.tsx`
- Header (lines 262-297): line two under the PC name becomes `{COPY.encrypted}` (or the beta label) + `" · "` + `<span data-testid="screen-time-left">{leftText}</span>` (amber class on the span only when `low`); the standalone time-left `<span>` is removed. New button before Touchpad, only when `state.surfaces.length >= 2`:

```tsx
<button type="button" className={STRIP_BUTTON} data-testid="screen-next" disabled={state.switching !== null || !(state.phase === "live" || state.phase === "paused")}
  aria-label={`Next screen. Showing screen ${human(state.surfaceIndex)} of ${state.surfaces.length}. Switch to screen ${human(next)}.`}
  onClick={() => { const n = client.nextSurfaceIndex(); if (n !== null) void client.switchSurface(n); }}>
  <Icon name="layout" size={14} />
  {state.switching ? <Spinner small label="Switching" /> : <span className="tabular-nums">{human(state.surfaceIndex)}/{state.surfaces.length}</span>}
</button>
```

`human(i) = position of surface i in surfaces sorted by index, + 1`.
- Overlay: while `state.switching` the overlay (`screen-overlay`, line 303) shows a spinner and `COPY.switching(human(switching.to))` over the kept picture; the stage stays mounted (stream kept); `status.kind` never becomes idle during a switch. **`switching` yields to `playHint`**: when the back element's `play()` was refused (Low Power Mode, `onPlayBlocked(true)`), the existing "Tap to show the picture" hint is shown instead of the spinner, and the tap plays both elements. If `status.kind === "consent"` while switching (a 1.4 agent in ask mode, or the cooldown edge) the existing consent text shows unchanged.
- Chips (`role="status"`, bottom-3 pill style, lowest priority after `notice`/`viewOnly`): `COPY.switched(n, of)` for 1.5 s when `state.switched` is fresh; `COPY.screenGone(n)` for 3 s when `switchedGone`; `COPY.switchFailed(n)` for 3 s when `switchFailed` (for `SCREEN_ALLOWANCE_EXHAUSTED` the existing exhausted state takes over without an intermediate failed card); `COPY.pointerInPicture` once per session for 3 s when `pointerOverlayAsked && cursorMode === "drawn"` (never on 1.4 PCs: they were never asked).
- `ScreenSurface` receives the new props (`pointer={client.pointer}`, `incomingStream={state.incomingStream}`, `surfaceSize={() => client.surfaceSize()}`, `cursorMode`, `pcPaused={state.pcState === "paused"}`, `switching={state.switching !== null}`).
- Explainer (line 379) becomes: "Tap what you see to click it. Pinch to zoom in; press and hold for a magnifier. Touchpad moves the pointer like a laptop touchpad and lets you drag. If your PC has two screens, the screens button at the top switches between them."
- Gesture sheet (line 385): three lists. `DIRECT_GESTURES` (directGestures.ts:364-373) rewritten: Tap → Clicks right there · Tap twice, quickly → Double-click · Tap with two fingers → Right-click · Press and hold, then slide → A magnifier to aim with; let go to click · Slide two fingers → Scrolls · Pinch → Zooms the picture on your phone only · Slide one finger while zoomed → Moves the zoomed picture. New `TOUCHPAD_SCREEN_GESTURES` in `gestures.ts` (the Touchpad page's `GESTURES` is untouched): Slide one finger → Moves the pointer, like a laptop touchpad · Tap → Clicks where the pointer is · Tap with two fingers → Right-click · Slide two fingers → Scrolls · Pinch → Zooms the picture; the view follows the pointer · Drag, then slide → Holds the mouse button while you move; End Drag lets go. New `SCREEN_COMMON_GESTURES` in `ScreenPage.tsx`: Screens button (top), only with two or more screens → Shows the PC's other screen. On an up-to-date PC the pointer comes along. DoMe calls the main screen 1; Windows may number them differently. · 1× (bottom left, while zoomed) → Back to the whole screen · The pointer you see → Drawn by your phone so it moves at once; the picture catches up. On some PCs it stays in the picture instead.
- `COPY` additions (`lib/screenStatus.ts`, exact text): `switching: (n: number) => \`Switching to screen ${n}…\`` · `switched: (n: number, of: number) => \`Screen ${n} of ${of}\`` · `screenGone: (n: number) => \`Screen ${n} isn't connected any more. Showing screen 1.\`` · `switchFailed: (n: number) => \`Couldn't switch screens. Still showing screen ${n}.\`` · `pointerInPicture: "On this PC the pointer stays in the picture, so it can lag a little behind your finger."` · `updateReady: "A newer DoMe is ready. Close and reopen the app to update."`.
- No new setting; no badge for hardware/software; "Basic picture mode" stays VP8-only.

### 4.9 Stores and runtime
- `store/devices.ts`: `pcProtocolVersions(pcId): string[]` selector (from `rest.Pc.protocol_versions ?? []`); `pcSpeaks(pcId, minor)`. **Stale bundle after the deploy** (D28): when `pcs()` rejects with an `ApiError` whose code is `MALFORMED_MESSAGE`, the store keeps the last list, sets `updateReady = true` (shown as the `COPY.updateReady` notice instead of an error) and calls `requestAppUpdate()`.
- `pwa.ts`: keep the `updateSW` function `registerSW()` returns (line 14) and export `requestAppUpdate(): void` = `updateSW?.(true)` when available, else `navigator.serviceWorker.getRegistration().then(r => r?.update())`; a no-op in dev.
- `app/runtime.ts` `pc_status` case (line 281): `const before = live.pcs[frame.pc_id]?.connection; live.onPcStatus(frame); if (before !== undefined && before !== frame.connection) this.scheduleDevicesRefresh()` (debounced 2 s, `void useDevicesStore.getState().refresh()`), so `protocol_versions`/`agent_version` follow an agent update without a reload.
- `rest.Pc` gains `protocol_versions?: string[]` (generated).


### Final text of section 5.1-5.3

### 5.1 `tests/screen_phone.py`
- `ScreenClient.start(*, takeover=False, sign_offer=None, apply_answer=True, surface: str | int = "primary", cursor: str | None = None)`: `params["surface"] = surface`; `cursor` key present only when not None (the harness stamp follows the content, §2.4). `Viewer` creates both channels (`dome-screen-v1` ordered, `dome-screen-cursor-v1` `ordered=False, maxRetransmits=0`, `screen_phone.py:222`); messages from either channel are parsed with `parse_screen_dc_message(message, "pc_to_phone")` and recorded with their channel label (`Viewer.messages: list[tuple[label, dict]]`), `Viewer.dc_raw_lengths: list[int]`.

### 5.2 `tests/agent_1_4.py` (new)
A copy of `older_agent.py` with `PROTOCOL_VERSIONS = ("1.0", "1.1", "1.2", "1.3", "1.4")` and the same `install()` (registry `protocol_version`/`registry_version` = `"1.4"`, `dome_agent.SUPPORTED_PROTOCOL_VERSIONS`, `frames.SUPPORTED_PROTOCOL_VERSIONS`), run on the fake platform exactly as `screen_agent.py` (it imports `screen_agent`'s seams: fake UI, control ops). `older_agent.py` stays 1.1. It runs the NEW `_start_session` with constants monkeypatched, so it proves the version, stamp and vocabulary rows only; the shipped 1.4 agent's consent order (consent before `_end`) is documented, not witnessed.

### 5.3 `tests/screen_agent.py` control ops (added to `_control_ops`)
- `test_set_monitors(monitors=[{index,left,top,width,height,primary}, …])`: sets `state.monitors`, calls `agent.screen.on_display_change()` (or the existing re-check hook), returns the list.
- `test_cursor_script(samples=[[flags, tag, x, y], …], shapes={tag: "arrow" | {"custom": {"w","h","hx","hy"}}})`: sets the fake cursor script the worker's `FakeCursorReader` reads from the `source` spec on the next start (a custom entry makes the test agent generate a `w×h` RGBA PNG with PIL).
- `test_set_cursor(x, y)`: parks the fake platform's cursor (`FakeState.cursor`) so a test can stage "the person's pointer is on the other monitor" before an injected move.
- `test_input` (lines 80-84): `injected` rows become `[name, *args, "@", x, y]` with the fake's cursor at injection time (`InputRecord.cursor`, §3.9), so a `button` row says where the click landed; `cursor` stays.


### Final text of section 3.3-3.4

### 3.3 `screen/session.py`
- `ScreenSession` gains: `cursor_requested: str | None = None`, `cursor_mode: str | None = None` (the guess, then the worker's effective mode), `switched_from: int | None = None`, `chain_started_at: float` (monotonic; `started_at` for a fresh consent, copied from the replaced session when consent was inherited), `consent_inherited: bool = False`. `confined_moves` is NOT here (input side).
- `start_session(..., cursor: str | None = None)` → `_start_session(..., cursor=cursor)`.
- `_start_session` (from line 502), in order: unchanged checks through `check_offer`; `existing = self.live_session`; owned check; **consent decision** (replaces the unconditional `_consent` at line 529):

```python
decision = self.gate.decide(controller_id)               # ALWAYS: ask_each_time, block, cooldown, backoff live only here
now = time.monotonic()
same = existing is not None and existing.controller_id == controller_id and existing.state != "ended"
inheritable = (
    same
    and existing.consent == "granted"                        # an explicit Allow by the person; never an inherited not_required
    and now - existing.chain_started_at < self.max_seconds   # the chain never outlives screen_session_max_seconds
    and (
        (decision.kind == "ask" and decision.cause == "ask_mode")
        or (decision.kind == "refused" and decision.refusal == "backoff")   # no window is opened, so the backoff's purpose holds
    )
)
if decision.kind == "not_required":
    consent, inherited = "not_required", False
elif inheritable:
    consent, inherited = "granted", True
else:
    consent, inherited = await self._consent(controller_id, display_name, decision=decision), False   # cooldown, blocked, fresh ask
```

`_consent(controller_id, display_name, *, decision: GateDecision | None = None)` (line 736) uses the given decision instead of calling `decide()` again; otherwise unchanged. Then: monitors; `_pick_surface` (an unknown index raises `INVALID_PARAMETERS` before anything ends); pre-flight only when `existing is None` (unchanged); `_recheck_start_conditions`; `existing2 = self.live_session`; owned check again; **re-validation of an inherited consent** (several awaits passed since the first read): `if inherited and (existing2 is None or existing2.screen_session_id != existing.screen_session_id or self.gate.cooldown_active()): consent = await self._consent(controller_id, display_name)` — before anything is ended or created (Stop at the PC in that window started the cooldown; its first window is never skipped); then `if existing2 is not None: await self._end(existing2, reason, keep_indicator=existing2.controller_id == controller_id)` with `reason = "takeover"|"replaced"`; `drain_ends()`; build `ScreenSession(..., consent=consent, consent_inherited=inherited, chain_started_at=existing.chain_started_at if inherited else now, cursor_requested=cursor, cursor_mode=self._cursor_guess(cursor), switched_from=existing.monitor.index if replacement and existing.monitor.index != monitor.index else None)`.
- Watchdog (`session.py:1242`): `if now - session.chain_started_at >= self.max_seconds: await self._end(session, "max_duration")` (a chain of replacements ends like one session).
- `_cursor_guess(cursor) -> str | None`: `None` when `cursor is None`; `"drawn"` when `cursor == "drawn"`; else `"overlay"` unless the source will be WGC on a build below 18362: `wgc = self.source.get("kind") == "wgc" or (preflight says DDA is skipped)`; `"drawn" if wgc and not self._platform.screen.cursor_capture_configurable() else "overlay"`. `ScreenAdapter.cursor_capture_configurable() -> bool` is new on the platform protocol: Windows returns `sys.getwindowsversion().build >= 18362`; the fake returns `FakeState.cursor_capture_configurable` (default True).
- `_end(session, reason, *, keep_indicator: bool = False)` (line 1079): when true `self.indicator.disarm()` is skipped (the bars stay). **Re-arm rule in `_bring_up`** (line 610): `if self.indicator.armed and self.indicator.monitors == tuple(monitors) and <same viewer_name and audio as armed>: reuse self.indicator.reserved (no show_screen_bars request)` else `self.indicator.disarm(); await self.indicator.arm(...)` (`indicator.py:68-104` `arm()` always creates bars and has no adopt path; a second `arm()` on armed bars would double them). The indicator records `viewer_name`/`audio` of the armed request for this comparison. Safety: `_start_session` wraps everything after that `_end` in `try/except`: on any exception before the new session's `_bring_up` confirmed its bars, `self.indicator.disarm()` (never leave bars up without a session), then re-raise.
- `_start_message`: adds `"cursor": session.cursor_requested` when not None; adds `"hw_encoding": os.environ.get("DOME_SCREEN_H264_HW", "1") != "0"`.
- `_on_worker_event`: new kinds `"cursor"` (`{"ev": "cursor", "mode": "drawn"|"overlay"}` → `session.cursor_mode = mode`, `log.info("screen cursor mode", mode=mode, source=ev.get("source"))`), `"encoders"` (`{"ev": "encoders", "probed": [...]}` → `session.media["encoders"] = probed` after `media_from_stats`-style key whitelisting: `encoder, ok, gated, reason, frames_in, open_ms, mft, parameter_sets`); `stats` events carry the cursor counters (`cursor_pos_sent, cursor_pos_unreliable, cursor_shape_sent, cursor_custom_sent, cursor_bitmap_max_chars, cursor_bitmap_too_large, cursor_dropped_gen, cursor_poll_errors, cursor_last_kind`) and `encoder_hw: bool` through `media_from_stats` (`session.py:147` whitelist extended; numbers and the kind word only; no key contains `png`, D26).
- `first_frame` toast: when `session.switched_from is not None`: `self._ui_notify("Screen view", f"{session.display_name} switched to screen {session.monitor.index + 1}. {STOP_HINT}")`, else the existing wording.
- New accessor `streamed_monitor(controller_id: str) -> Monitor | None`: `self._current.monitor` when the session exists, `state != "ended"`, `connected`, and `controller_id` matches (gen-independent; confinement works while `geometry_pending`).
- `summary()`/`status_view`/history: `cursor: {requested, mode}`, `consent_inherited`, `chain_seconds` and the counters; the history row stays content-free.

### 3.4 `input_session.py` — confinement
- `InputSessionManager.__init__(..., streamed_monitor: Callable[[str], Monitor | None] | None = None)`; `agent.py:170` wires `streamed_monitor=lambda controller_id: self.screen.streamed_monitor(controller_id)` next to `screen_target`.
- `_DispatchOutcome` gains `confined: int = 0`, `confine_failures: int = 0`; `InputSession` gains `confined_moves: int = 0`, `confine_failures: int = 0` (into `summary()` and the acks' log fields: `status.input.session.confined_moves`).
- In `_dispatch_sync` the `pointer_move` branch (line 849-851) becomes:

```python
if kind == "pointer_move":
    dx, dy = int(ev["dx"]), int(ev["dy"])
    if dx or dy:
        monitor = self._confine_monitor(session)            # None: no connected screen session of this controller
        before = self._cursor_quietly() if monitor is not None else None
        adapter.move(dx, dy)
        if monitor is not None and before is not None:
            self._confine_after_move(session, out, monitor, before)
```

```python
def _confine_after_move(self, session, out, monitor, before) -> None:
    """rules.screen_cursor (5): only a move WE injected that crossed the streamed monitor's edge is pulled back.
    GetCursorPos cannot tell our SendInput from the person's mouse, so a pointer that was already elsewhere
    before our move is left alone (no clip rectangle, ever)."""
    if not _inside(before, monitor):
        return
    try:
        after = self._platform.input.cursor_position()
        if after is None or _inside(after, monitor):
            return
        cx = min(max(after[0], monitor.left), monitor.left + monitor.width - 1)
        cy = min(max(after[1], monitor.top), monitor.top + monitor.height - 1)
        self._platform.input.move_to(cx, cy)
        out.confined += 1
    except ProtocolError as exc:  # best effort: the batch is never failed by its confinement
        out.confine_failures += 1
        log.debug("pointer confinement failed", code=exc.code)
```

`_inside(pos, m)` = `m.left <= x < m.left + m.width and m.top <= y < m.top + m.height`. Coalescing (`coalesce_events`, line 278) already makes adjacent moves one event, so this is at most 80 `GetCursorPos` per second and a `SetCursorPos` only when our move crossed an edge. Never `ClipCursor`. `pointer_move_to` never triggers it (it already maps onto the streamed monitor). A `dx == dy == 0` move does nothing (already skipped at line 850).

---

# Part 2. Full text before the adversarial review (superseded where Part 1 or section 9 says so)

# Screen view round 2a — THE design specification (protocol 1.5)

Status: decided. This document rules every contradiction between the three candidate designs and the three judgements; a builder follows it without asking. Design only: no repository file was modified while writing it. No input of any kind travels over the WebRTC data channel (founder decision): every touch, click and key stays an `input_batch` on the signed relay path; nothing below adds, hints at or leaves room for a data-channel input path.

Every file path, line number and symbol below was read in the tree at `/home/user/DoMe` on 2026-11-02. Where this document changes an existing rule or behaviour it says so and why. Status language in every doc this round touches: "unit-tested", "integration-tested", "not yet verified on a device".

---

## 0. Summary of decisions

| # | Topic | Decision |
| --- | --- | --- |
| D1 | Version | One additive MINOR, 1.4 → 1.5. `version.json protocol_version` and `actions.json registry_version` become `"1.5"`. Screen actions keep `since: "1.4"`; the relay's `SCREEN_PROTOCOL_VERSION` stays `"1.4"`. |
| D2 | The signed screen answer's stamp | **Changed rule.** The PC's signed answer carries the `protocol_version` of the `screen.session_start` it answers (1.4 or 1.5), not the library's version. Today `shared/python/dome_protocol/screen.py:105` and `shared/ts/src/screen.ts:89` stamp `registry.protocol_version`/`PROTOCOL_VERSION`, and the verifiers (`screen.py:181-184`, `screen.ts:159-160`) refuse any stamp above their own; without this change every 1.4 phone (stale PWA bundle, the Python phone) would reject a 1.5 agent's answer with `PROTOCOL_INCOMPATIBLE`. `build_screen_answer_payload` / `buildScreenAnswerPayload` take the stamp as a required argument. |
| D3 | Command stamping with a new param | **Changed rule.** A command is stamped with the lowest version that defines every param it carries: the action's `since`, raised by `param_since` of any present param (new top-level action key in `actions.json`). `screen.session_start` is stamped 1.4 without `cursor`, 1.5 with it. Reason: `shared/ts/src/commands.ts:92` stamps `spec.since`; a 1.4-stamped start carrying `cursor` passes a 1.4 agent's version checks (`commands.py:188-191`) and dies in its schema (`additionalProperties:false`) as `MALFORMED_MESSAGE`, the outcome the 1.4 rule promises never happens. Content-based stamping is already the rule for input batches. Verifiers also refuse, after schema validation, a command stamped below what its present params need. |
| D4 | Relay shield | `router.py`: right after the `supports_screen` gate (lines 380-385) and **before** `reserve_start`, refuse any command whose stamp the target agent did not announce: `result{origin: relay, failed, PROTOCOL_INCOMPATIBLE, detail{peer: "agent", required: <stamp>}}` (generalises the input-batch gate at `router.py:565`). A refused `cursor` start charges nothing and leaves no history row. |
| D5 | Pointer mode vocabulary | `"drawn" | "overlay"` (the brief's words). Never `video`. |
| D6 | Capability signal | REST `$defs/pc.protocol_versions` (array, optional in the schema, always sent by the server), persisted on the `pcs` row from the agent hello next to `agent_version` (Alembic `0006`). Offline PCs show the last announced list; `[]` before any hello to a 1.5 relay. The phone refreshes `/v1/pcs` (debounced) when a `pc_status` flips a PC's `connection`, because `mobile-app/src/app/runtime.ts:281-289` only reloads grants today. No new `pc_status` field (its schema is `additionalProperties:false` and `manager.py:559-586` broadcasts one frame to sockets of every version). |
| D7 | Phone gating | The phone sends `cursor: "overlay"` only when `protocolCompatible("1.5", pc.protocol_versions)`. On `failed PROTOCOL_INCOMPATIBLE` with `detail.required === "1.5"` it marks the PC "no cursor" for the app run and retries once without `cursor` (not counted as a retry). Next screen needs no gating (`surface: N` is 1.4). |
| D8 | What a 1.4 phone never receives | `result.cursor`, `state.cursor`, `cursor_pos` and `cursor_shape` are sent only in a session whose start carried the `cursor` param (equivalently: was stamped 1.5). `result.cursor` and `state.cursor` are present whenever the param was present (any value); `cursor_pos`/`cursor_shape` only while the effective mode is `overlay`. In `drawn` mode no cursor message is sent at all. A worker helper strips 1.5-only keys for a 1.4-stamped session and a unit test proves a 1.4 session never emits a 1.5 key. |
| D9 | Effective mode truth | `result.cursor` is the PC's start-time best guess (`overlay` unless the source will be Windows.Graphics.Capture on a Windows build below 18362); the first `state{sending, cursor}` after the source opened is authoritative (WGC may refuse `cursor_capture=False` with `CursorConfigUnsupported`; a DDA→WGC fallback re-reports). The phone never draws two pointers. |
| D10 | Data-channel size cap | `screen_dc_message_max_bytes` stays 1024 for every message. New `screen_dc_cursor_shape_max_bytes = 12288` applies only to a `pc_to_phone` message that parses as `cursor_shape`. The rule lives inside `parse_screen_dc_message` / `parseScreenDcMessage` because the worker self-validates outbound messages through the same parser (`rtc.py:365-368`). |
| D11 | `cursor_shape` schema | Two explicit `oneOf` members (named / custom), no `if/then/else`: clean `gen:types` output and the no-eval Ajv in `mobile-app/scripts/gen-validators.ts`. Named kinds: 14 lower-case concatenated names. Custom fields: `width, height, hotspot_x, hotspot_y, png`. No `other` kind: an unrenderable or oversize custom cursor is sent as `arrow` and counted. |
| D12 | `cursor_pos` shape | `{type, gen, x, y, visible}` exactly as the brief; `visible:false` when Windows hides the pointer or it is off the streamed monitor (x, y then hold the last on-surface position). Sent on change only, coalesced to ≤ 60/s; no periodic repeat; no `on_surface`. |
| D13 | Surface switch (Next screen) | A fresh signed `screen.session_start{surface: N}` from the owning controller. The agent ends the old session `replaced`, keeps the bars up across the replacement, and starts a new session at `gen: 1`. **Changed rule:** a same-controller replacement while the old session is live with consent `granted` or `not_required` opens no new consent window (`consent.py:197` would refuse a second window within 60 s as `backoff`, so Next screen would otherwise fail `SCREEN_CONSENT_DECLINED` in ask mode). A takeover still never skips consent. No pre-flight on a replacement (unchanged: the old worker holds the duplication). |
| D14 | Phone-side switch | `switchSurface(index)`: **no `bye`, no `screen.session_stop`** (`session.py:880` would end the old session `stopped`, not `replaced`, and `screen.ts:1118-1131 stop()` resets to `EMPTY_SCREEN_STATE`, which unmounts the stage). The client retires the old id, keeps `stream`/`allowance`/`otherViewer`/`surfaces`, sets `switching`, starts the new session, and closes the old `RTCPeerConnection` when the new `ontrack` fires or the start fails. `switching` clears on the new session's first frame. |
| D15 | Pointer confinement | Agent-side, after **each** injected `pointer_move` in `_dispatch_sync` (not once per batch: a coalesced move-click-move batch is dispatched in order, `input_session.py:849-866`, and must never click on the other monitor): read `cursor_position()`; if outside the streamed monitor owned by that controller, `move_to()` the clamped point. Never `ClipCursor`; only our injected moves; failures counted, never fail the batch. No centring after a switch (an injected move nobody made). |
| D16 | Watched monitor unplugged | Out of scope this round; today's fail-closed `capture_failed` path (`session.py:1305-1309`) stays. Recorded as a Phase 2b candidate. |
| D17 | Pointer overlay rendering | DOM (`<svg>` for named kinds, `<img src="data:image/png;base64,…">` for custom) rendered **outside** the zoom/pan layer at a constant 24 CSS px nominal height (custom bitmaps scaled down to 24 px, never up, hotspot scaled), positioned with the inverse of `toSurface` through `fit` and `view` in a `requestAnimationFrame` loop; no React state per message; no canvas (`test/no-pixel-access.test.ts` stays untouched). Hidden until the first report of the session, when `visible:false`, in `drawn` mode, while paused, while switching, while the loupe is open, and for 150 ms around a direct-tap ripple. z-order above the readout (`z-[5]`), below the loupe (`z-20`). |
| D18 | Pointer prediction | A pure `PointerModel`: touchpad mode adds each `move{dx,dy}` output (already PC pixels via sensitivity) × 65535/(W−1); direct mode snaps to the tap/loupe point; reports blend 35 % per report while a finger moves (`BLEND_WHILE_MOVING = 0.35`), ease fully over 80 ms when idle ≥ 120 ms (`EASE_MS = 80`, `IDLE_MS = 120`), snap only when the error exceeds 10 % of the surface (`SNAP_FRACTION = 0.1`). Constants are named tunables; device test decides their values. |
| D19 | Zoom in Touchpad mode | Extract the direct machine's two-finger classifier (`directGestures.ts:246-285`, ratio 0.1 before 8 px, `twoPending` pair guard) into `lib/twoFinger.ts`, used by both machines; `GestureMachine` emits `pinch`; one-finger drag stays a relative move; `followPointer()` auto-pans to keep the pointer inside an inner margin (20 % of the box, at least 48 px) while zoomed in touchpad mode; a `1×` chip (both modes, while `s > 1`) and a 1.1× snap back to identity. No gate/replay wrapper. |
| D20 | Header layout | Time-left moves to the second line under the PC name (after "Encrypted…", separated by " · ", `data-testid="screen-time-left"` kept). A screens button (`Icon name="layout"` + tabular `1/2`) appears only when `surfaces.length >= 2`, disabled with a spinner while switching; a 1.5 s "Screen 2 of 2" chip after a switch and when a session starts on a non-primary screen. Humans count from 1 (index 0 = screen 1). The strip is at its 390 px limit (`ScreenPage.tsx:262-297`). |
| D21 | Remembered surface | `lastSurfaceByPc` in memory for the app run (not persisted): `restartFresh`, resume after backgrounding and the next Show screen return to the watched screen; `INVALID_PARAMETERS` on a remembered index retries once with `"primary"`; a new launch starts on the primary. No new setting anywhere this round. |
| D22 | Hardware H.264 | `EncoderChoice.pix_fmt`; both `h264_mf` candidates and the dev `libx264` candidate take `nv12`; sources deliver the encoder's `pix_fmt` (DDA native; WGC `reformat(... format=pix_fmt)`; fakes stay yuv420p so the track's conversion path is tested on Linux); `probe_candidate` feeds up to `PROBE_MAX_FRAMES = 16` frames within `PROBE_BUDGET_SECONDS = 3.0` and accepts only when a slice packet came out **and** SPS+PPS exist (in band or extradata). 16, because `pc-agent/DECISIONS.md` #98 records the software MFT's first packet 5.5–9.2 s after the first frame at the 1 fps floor (6–9 held frames); 8 would demote the founder's working software path to VP8. `DOME_SCREEN_H264_HW=0` removes the hardware candidate; `H264Encoder.reopen` retries once after 100 ms. No vendor is forced on the hybrid laptop; the MFT name is logged. |
| D23 | Encoder facts toward the phone | None new. No `encoder_hardware` field; no badge; "Basic picture mode" stays VP8-only. Facts go to the log, the bundle and the staging readout. |
| D24 | Fixtures | `make_fixtures.py:164` and `make-fixtures.ts:152` compute the rejected "newer MINOR" stamp as the next MINOR above the library's version (1.6 now) instead of hard-coding; regenerate both `es256-*.json` with the committed `test-pc-key.pem`; never hand-edit. New `fixtures/screen-dc-cases.json` run by both libraries. |
| D25 | e2e "1.4 agent with a 1.5 phone" | `tests/agent_1_4.py`, a launcher in the `tests/older_agent.py:19-27` pattern (module constants monkeypatched before the real agent starts). No production code or environment hook. |
| D26 | Redaction | `"png"` joins `SENSITIVE_KEY_PARTS` in `pc-agent/dome_agent/logsetup.py` (`_LONG_TOKEN_RE` at line 57 is `[A-Za-z0-9_-]{40,}` and does not cover standard base64's `+` and `/`). The phone logs shape kinds, sizes and counts only. |
| D27 | Numbering | pc-agent DECISIONS #105–#110, KNOWN_ISSUES #35 rewritten + new entries after #39; mobile-app DECISIONS #85–#90; cloud-api DECISIONS #115–#116. |
| D28 | Deploy order | cloud-api + PWA first (same deploy; `api.ts:161` validates REST bodies with `additionalProperties:false`, so the PWA and the server move together as every REST addition has), then agents. A 1.4 agent keeps every 1.4 behaviour including Next screen; a 1.4 phone never receives a 1.5 field or message. |

---

## 1. Contract 1.5 (`shared/protocol`, `shared/python`, `shared/ts`)

### 1.1 `version.json`

- `"protocol_version": "1.5"`.
- `limits` gains exactly one entry (after `screen_dc_message_max_bytes`):

```json
"screen_dc_cursor_shape_max_bytes": 12288
```

No other limit changes. The cursor rate limits (60 positions/s, 4 custom shapes/s), the bitmap bound (64 px, 8192 base64 characters) are stated in the schema and rule text and as constants in the worker, not as limits (they bound a sender, not a verifier).

- Rules amended and added: §1.7.

### 1.2 `actions.json`

- `"registry_version": "1.5"`.
- `screen.session_start` gains a top-level key `param_since` (a sibling of `since`; not a JSON-Schema keyword, so strict Ajv and python-jsonschema never see it) and a `cursor` param. `since` stays `"1.4"`. Everything else in the action is unchanged.

```json
"screen.session_start": {
  "summary": "<unchanged>",
  "since": "1.4",
  "param_since": { "cursor": "1.5" },
  "params": {
    "type": "object",
    "additionalProperties": false,
    "required": ["offer_sdp"],
    "properties": {
      "offer_sdp": { "<unchanged>": null },
      "surface": { "<unchanged: \"primary\" | integer 0..7, default \"primary\">": null },
      "audio": { "<unchanged>": null },
      "takeover": { "<unchanged>": null },
      "cursor": {
        "type": "string",
        "enum": ["drawn", "overlay"],
        "default": "drawn",
        "description": "Since 1.5. Who draws the pointer. `drawn`: the PC paints its pointer into the picture (the 1.4 behaviour). `overlay`: the PC captures WITHOUT the pointer and reports its position and shape on the data channel (cursor_pos, cursor_shape) so the phone draws it itself. The result's `cursor` is the mode the PC will try and the data-channel `state.cursor` the mode in effect (rules.screen_cursor). A command carrying this param is stamped 1.5 (rules.command_version_stamping, param_since); a phone sends it only to a PC whose agent announced 1.5 (rest $defs/pc.protocol_versions)."
      }
    }
  },
  "<rest unchanged: target null, result screen_session_result, capability screen, risk moderate, confirmation none, timeout_ms 45000, availability [windows, session_unlocked], verification response, idempotent false, routine_allowed false, ai_eligible false>": null
}
```

Registry load validation (both libraries): every `param_since` key names a property of `params.properties`; every value parses as MAJOR.MINOR, is not below the action's `since` and not above `protocol_version`; otherwise `ValueError` / `Error("action … has param_since … outside [since, protocol_version]")`. `shared/python/tests/test_registry_and_schemas.py` (the `since` validation test at line 360) and `shared/ts/test/registry.test.ts` gain the mirror cases for `param_since` (unknown param name; version below since; version above protocol).

### 1.3 `results.schema.json` — `screen_session_result`

Add one optional property; nothing else changes (`required` unchanged).

```json
"cursor": {
  "type": "string",
  "enum": ["drawn", "overlay"],
  "description": "Since 1.5; present ONLY when the start carried the `cursor` param (a 1.4 phone validates this result with additionalProperties:false). The pointer mode the PC will try: `overlay` when the start asked for it and this PC can capture without the pointer, else `drawn`. The data-channel `state.cursor` is authoritative once capture is open (rules.screen_cursor)."
}
```

A surface switch returns the same shape with the new `surface_index` and `gen: 1`.

### 1.4 `relay-frames.schema.json` — `screen_dc_pc_to_phone`

The `oneOf` gains three members (`cursor_pos`, named `cursor_shape`, custom `cursor_shape`); the `state` member gains `cursor`. `screen_dc_phone_to_pc` is unchanged. The def's description sentence "Phase 2/3 add cursor_shape, cursor_pos and roi." becomes "1.5 adds cursor_pos and cursor_shape (the pointer drawn by the phone, rules.screen_cursor); Phase 3 adds roi."

```json
{
  "type": "object",
  "additionalProperties": false,
  "required": ["type", "gen", "x", "y", "visible"],
  "properties": {
    "type": { "const": "cursor_pos" },
    "gen": { "type": "integer", "minimum": 1, "maximum": 2147483647 },
    "x": { "type": "integer", "minimum": 0, "maximum": 65535 },
    "y": { "type": "integer", "minimum": 0, "maximum": 65535 },
    "visible": { "type": "boolean" }
  },
  "description": "Since 1.5. The real pointer's hotspot over the streamed surface of geometry generation `gen`, normalised 0..65535 like pointer_move_to. `visible` is false when Windows hides the pointer or it is on another monitor; x and y then hold the last position inside the surface. Sent only in an overlay session, when changed, coalesced to at most 60 per second."
},
{
  "type": "object",
  "additionalProperties": false,
  "required": ["type", "kind"],
  "properties": {
    "type": { "const": "cursor_shape" },
    "kind": {
      "type": "string",
      "enum": ["arrow", "ibeam", "hand", "wait", "appstarting", "cross", "no", "sizeall", "sizens", "sizewe", "sizenesw", "sizenwse", "uparrow", "help"]
    }
  },
  "description": "Since 1.5. The pointer took one of Windows' system shapes (IDC_*); the phone draws it with its own crisp vector art and hotspot."
},
{
  "type": "object",
  "additionalProperties": false,
  "required": ["type", "kind", "width", "height", "hotspot_x", "hotspot_y", "png"],
  "properties": {
    "type": { "const": "cursor_shape" },
    "kind": { "const": "custom" },
    "width": { "type": "integer", "minimum": 1, "maximum": 64 },
    "height": { "type": "integer", "minimum": 1, "maximum": 64 },
    "hotspot_x": { "type": "integer", "minimum": 0, "maximum": 63 },
    "hotspot_y": { "type": "integer", "minimum": 0, "maximum": 63 },
    "png": { "type": "string", "minLength": 32, "maxLength": 8192, "pattern": "^[A-Za-z0-9+/]+={0,2}$" }
  },
  "description": "Since 1.5. An application's own pointer as a 32-bit RGBA PNG of exactly width x height (at most 64 x 64; the PC scales larger cursors down, hotspot included), standard base64 of at most 8192 characters, with its hotspot in bitmap pixels. A cursor bitmap is screen content (rules.screen_content): never logged, stored or put in a diagnostics bundle by either side. This is the only data-channel message that may exceed screen_dc_message_max_bytes; it is bounded by screen_dc_cursor_shape_max_bytes instead."
}
```

`state` member, after `data_saver`:

```json
"cursor": { "type": "string", "enum": ["drawn", "overlay"], "description": "Since 1.5. The pointer mode in effect; present in every state message of a session whose start carried `cursor`, absent otherwise." }
```

Generated artefacts: `cd shared/ts && pnpm gen:types` (commit `src/generated`); `cd mobile-app && pnpm gen:validators` (commit `src/protocol/generated`; `screen_dc_pc_to_phone` is already in `RELAY_FRAME_DEFS` at `gen-validators.ts:36`, nothing to add there; the `rest` document is regenerated by the same run). TS types that must exist after generation: `ScreenDcCursorPos`, `ScreenDcCursorShapeNamed`, `ScreenDcCursorShapeCustom`, and the union member names of `ScreenDcPcToPhone` (whatever `json-schema-to-typescript` emits; the builder exports `CursorKind` as the named-kind enum type from `shared/ts/src/screen.ts`).

### 1.5 `rest.schema.json` — `$defs/pc`

Add to `properties` (NOT to `required`, so fixtures and the dev preview that build `pc` rows keep validating; the server always sends it):

```json
"protocol_versions": {
  "type": "array",
  "maxItems": 8,
  "items": { "type": "string", "pattern": "^[0-9]+\\.[0-9]+$" },
  "description": "The protocol versions the PC's agent announced in its last relay hello (empty before the agent connected to a relay that records them). A phone sends an optional param a later MINOR added (screen.session_start.cursor) only to a PC whose list includes that MINOR. A stale list costs one PROTOCOL_INCOMPATIBLE round-trip, never a wrong result."
}
```

REST bodies are exempt from the bump rule; this rides the same bump anyway.

### 1.6 Parser rule for the size cap (both libraries)

`parse_screen_dc_message(text, direction)` (`shared/python/dome_protocol/screen.py:216`) and `parseScreenDcMessage(text, direction)` (`shared/ts/src/screen.ts:206`):

1. If the UTF-8 byte length ≤ `screen_dc_message_max_bytes`: parse and validate as today.
2. Else, if `direction == "phone_to_pc"`: raise `MALFORMED_MESSAGE` ("data-channel message too long").
3. Else (`pc_to_phone`): `loads_strict(text, max_bytes=screen_dc_cursor_shape_max_bytes, …)`, validate against `screen_dc_pc_to_phone`; if `parsed["type"] != "cursor_shape"` raise `MALFORMED_MESSAGE` ("only cursor_shape may exceed screen_dc_message_max_bytes"). Anything above 12288 bytes is refused by `loads_strict` as today.

Unit tests in both suites (`test_screen_contract.py`, `screenContract.test.ts`): a 1025-byte `hb` (phone_to_pc) → `MALFORMED_MESSAGE`; a 1025-byte `state` (pc_to_phone) → `MALFORMED_MESSAGE`; a 9000-byte custom `cursor_shape` → accepted; a 12289-byte `cursor_shape` → `MALFORMED_MESSAGE`.

### 1.7 Rule text (`version.json → rules`)

- **`command_version_stamping`** — replace the sentence "a command carries its action's `since` (actions.json; absent = 1.0)" with: "a command carries the lowest protocol version that defines every param it carries: the action's `since` (actions.json; absent = 1.0), raised to the `param_since` of any present param a later MINOR added (actions.json `param_since`; the shared libraries' `command_protocol_version` / `commandProtocolVersion`). So `screen.session_start` is stamped 1.4 unless it carries `cursor` (1.5)". Append to the "Verifiers (relay and agent) also refuse" sentence: "and a command stamped below what its present params need (checked after schema validation, since the param must exist to be judged; the compatibility check and the action's `since` stay BEFORE schema validation)". Append to the relay sentence: "and, since 1.5, refuses before forwarding ANY command whose stamp is not compatible with the target agent's announced protocol_versions (result{origin: relay, failed, PROTOCOL_INCOMPATIBLE detail{peer: \"agent\", required}}), so a 1.5-stamped start never reaches a 1.4 agent; a phone therefore sends a newer param only to an agent that announced the MINOR (rest $defs/pc.protocol_versions) and, when that knowledge was stale, gets a clean refusal it retries without the param."
- **`screen_signaling`** (5) — replace the clause that fixes the answer's `protocol_version` at 1.4 with: "`protocol_version` in the signed answer is the stamp of the `screen.session_start` it answers (1.4 for a 1.4-stamped start, 1.5 for one that carried `cursor`), as a confirmation carries the stamp of its command; the phone's verifier accepts it under the compatibility rule."
- **`screen_sessions`** — insert after (1): "(1b) Surface switch. A `screen.session_start{surface: N}` from the controller that owns the live session is a replacement: the agent retires the old id, ends the old session `replaced` (its `ended` frame carries that reason), and starts a fresh session on surface N with a new screen_session_id, a new DTLS certificate and `gen: 1`; there is never a renegotiation (rules.screen_signaling (8)). While the old session is live and its consent was `granted` or `not_required`, a same-controller replacement opens no new consent window (the person already allowed this phone to watch) and the new result carries that consent value; a takeover never skips consent (5), and the post-Stop cooldown's first consent is never skipped (a live session cannot predate the Stop that started the cooldown). The indicator bars stay up across the replacement and are re-armed for the new session. The relay meters the new session like any other (rules.screen_metering (3)); the phone keeps its countdown across the switch and treats the old session's late frames as retired." Add to the end-reason list: "`replaced` is also the reason when the same controller switched surface."
- **`screen_cursor`** (new) — "The pointer is drawn by the phone when the controller asked `screen.session_start.cursor: overlay` and the PC could capture without it. (1) Capture without the pointer is per source: Desktop Duplication `draw_mouse=0`; Windows.Graphics.Capture `cursor_capture=False`, and when Windows refuses the setting (Windows 10 before build 18362) the capture runs with the pointer drawn and the PC says so. (2) `screen_session_result.cursor` is the PC's intent at start; the first `state{sending, cursor}` after the source opened is authoritative, and every later state message of that session repeats it; the phone draws only while the latest known mode is `overlay`. (3) In an overlay session the PC polls the pointer (position, visibility, shape handle) at about 60 Hz and sends `cursor_shape` when the shape changed (a named system kind, or `custom` with a 32-bit RGBA PNG of at most 64 x 64 pixels and 8192 base64 characters plus its hotspot; a shape it cannot render within those bounds is sent as `arrow`), coalesced to at most 4 custom shapes per second, and `cursor_pos{gen, x, y, visible}` when position or visibility changed, coalesced to at most 60 per second; `visible` is false when Windows hides the pointer or it is on another monitor. The first cursor_shape precedes the first cursor_pos; both are re-sent after every `geometry` and after every resume; nothing is sent while paused and nothing at all in a `drawn` session. (4) A cursor bitmap is screen content (rules.screen_content): no component writes it to disk, logs it, journals it, includes it in a diagnostics bundle (kinds, sizes and counts only) or sends it anywhere but the phone over this channel; the phone keeps at most the current shape in memory, never in storage. (5) The pointer stays on the watched screen: while a connected screen session owned by the batch's controller exists, after each injected relative `pointer_move` the agent reads the pointer position and, if it left the streamed monitor's rectangle, places it on the nearest point inside (physical pixels). Only the phone's injected moves are corrected; the person at the PC is never constrained and no clip rectangle is ever set; a 1.4 agent does not confine. (6) `result.cursor`, `state.cursor`, `cursor_pos` and `cursor_shape` are sent only in a session whose start carried `cursor`; a 1.4 phone never receives them."
- **`screen_content`** — replace the vocabulary sentence with: "The screen data channel (screen_dc_message) is a closed vocabulary: hb, keyframe, bye and audio{on} from the phone; geometry, state, allowance, restricted, cursor_pos and cursor_shape (1.5) from the PC; every message is at most screen_dc_message_max_bytes except cursor_shape, which is at most screen_dc_cursor_shape_max_bytes (a longer message of any other type is dropped); unknown or malformed messages are dropped, and no data-channel message can inject input, change a grant or run anything on the PC (input stays on the signed input_batch path). A cursor bitmap (the `png` of cursor_shape) is screen content under this rule (rules.screen_cursor (4))."
- **`screen_metering`** (3) — append: "A surface switch is a new session: its setup counts, and the refund of the unused chunk applies to the replaced session as to any other."

`errors.json`: no new code. `PROTOCOL_INCOMPATIBLE`'s description gains "…and, since 1.5, any command whose stamp the target agent did not announce (the relay refuses it before forwarding)". `INVALID_PARAMETERS` already covers an unknown surface index (`session.py` `_pick_surface`).

### 1.8 Shared libraries

**shared/python (`dome_protocol`)**
- `registry.py`: `ActionSpec.param_since: dict[str, str]` (default empty); load validation per §1.2; `Registry.min_version(name, params: Mapping[str, Any] | None = None) -> str` = `since` raised to the max `param_since` of present keys.
- `versions.py`: `command_protocol_version(action: str, params: Mapping[str, Any], *, registry: Registry | None = None) -> str` (delegates to `min_version`). `SCREEN_INPUT_PROTOCOL_VERSION` stays `"1.4"` (no new input events).
- `commands.py`: `verify_and_parse_command` keeps `_check_stamp(payload, registry, since=registry.min_version(action))` before schema; after `registry.validate_params(...)` it calls `_require_since(payload["protocol_version"], registry.min_version(spec.name, params))` (so a 1.4-stamped start carrying `cursor` is `PROTOCOL_INCOMPATIBLE {peer, required: "1.5"}`, not accepted). Test harness stamps (`tests/conftest.py:343`, `cloud-api/tests/conftest.py:953`) pass `params` to `min_version`.
- `screen.py`: `build_screen_answer_payload(..., protocol_version: str)` (required keyword) replaces `registry.protocol_version` at line 105; `parse_screen_dc_message` per §1.6. The verifier at lines 181-184 is unchanged (compatibility rule).
- `PROTOCOL_VERSION` is derived from `version.json` (`registry.py:77`): nothing hard-coded changes.

**shared/ts (`@dome/protocol`)**
- `registry.ts`: `ActionSpec.paramSince: Record<string, string>`; load validation; `minVersion(action: string, params?: Record<string, unknown>): string`.
- `versions.ts`: `commandProtocolVersion(action, params)`.
- `commands.ts:92`: `protocol_version: commandProtocolVersion(spec.name, params)`; `verifyAndParseCommand` adds the post-schema `requireSince` against `minVersion(action, params)`.
- `screen.ts`: `buildScreenAnswerPayload({ ..., protocolVersion })` (required) replaces `PROTOCOL_VERSION` at line 89; `parseScreenDcMessage` per §1.6; export `CursorKind`.
- `PROTOCOL_VERSION`/`REGISTRY_VERSION` derive from JSON (`registry.ts:62-63`).

**Tests whose literals change** (every `required: "1.4"`, `since` 1.4 and "relay shields pre-1.4 agents" assertion stays true):
- `shared/python/tests/test_screen_contract.py:530-531, 616-617, 632-647` and `shared/ts/test/screenContract.test.ts:376-377, 447-448, 473-474`: the newer-MINOR stamp becomes `"1.6"` and the detail `{"peer": "1.6", "supported": ["1.5"]}`.
- `shared/python/tests/test_registry_and_schemas.py:247, 360, 425`, `test_input_batch.py:110`, `shared/ts/test/registry.test.ts:82`, `stamping.test.ts:75, 87`: `"1.5"` as the "too new" literal becomes `"1.6"` (and `"1.5"` becomes a supported version where the test enumerates them).
- New tests (both libraries): `command_protocol_version("screen.session_start", {offer_sdp, surface: 1})` → `"1.4"`; with `cursor: "overlay"` → `"1.5"`; a 1.4-stamped start carrying `cursor` is `PROTOCOL_INCOMPATIBLE {peer: "controller"|…, required: "1.5"}` after schema; `build_screen_answer_payload(protocol_version="1.4")` verifies against a `Registry` copy whose `protocol_version = "1.4"`; the §1.6 size cases; `param_since` load validation.

### 1.9 Fixtures

- `shared/python/scripts/make_fixtures.py:164`: `newer_stamp = f"{major}.{minor + 1}"` computed from `registry.protocol_version`; `shared/ts/scripts/make-fixtures.ts:152`: the same from `PROTOCOL_VERSION`. `$comment` → "Protocol 1.4+. The newer-MINOR case is stamped one MINOR above this library's version."
- The good/tampered/by-controller-key cases pass `protocol_version=registry.protocol_version` (so the fixture's accepted answer is a 1.5 answer; the e2e "1.5 agent answers a 1.4 phone in 1.4" proves the other direction).
- Regenerate: `cd shared/python && uv run python scripts/make_fixtures.py` → `shared/protocol/fixtures/es256-python.json`; `cd shared/ts && pnpm fixtures` → `es256-typescript.json`; both signed with `shared/protocol/fixtures/test-pc-key.pem` / `test-controller-key.pem`; commit both. `test_fixtures.py` / `fixtures.test.ts` cross-check.
- New `shared/protocol/fixtures/screen-dc-cases.json`: `{ "accepted": [...], "rejected": [...] }`, each case `{ "name", "direction", "text" }` with `text` the exact message string. Accepted (pc_to_phone): a `cursor_pos`; a named `cursor_shape{kind: "ibeam"}`; a custom `cursor_shape` carrying a real 1×1 RGBA PNG (generated by the fixture scripts with PIL / a byte literal); a `state{sending, encoder: "h264", cursor: "overlay"}`; a 9000-byte custom `cursor_shape` (PNG padded with a valid tEXt chunk). Rejected: custom without `png`; `png` containing `-`; `width: 65`; `kind: "custom"` with `visible` key; a 1025-byte `state` (pc_to_phone); a 12289-byte `cursor_shape`; a `cursor_pos` without `visible`; a 1025-byte `hb` (phone_to_pc); a `cursor_pos` sent phone_to_pc. Run by `test_shared_cases.py` and `sharedCases.test.ts`.

### 1.10 `docs/PROTOCOL.md` §21 — "Protocol 1.5: Screen view, Phase 2a (Next screen, phone-drawn pointer, Touchpad zoom, hardware encoding)"

In the §20 shape: 21.1 What 1.5 adds (table: `cursor` param + `param_since`; `result.cursor`; DC `cursor_pos`, `cursor_shape` named/custom, `state.cursor`; limit `screen_dc_cursor_shape_max_bytes`; REST `pc.protocol_versions`; the stamp rule; the answer stamp; library helpers) · 21.2 Limits · 21.3 Surface switch (`rules.screen_sessions` (1b); sequence: start{surface:1} → old `ended{replaced}` → result{surface_index 1, gen 1} + answer → geometry) · 21.4 The pointer (`rules.screen_cursor`; modes; messages; the named-kinds table with the phone's hotspots; custom bitmap bounds; the content rule; confinement) · 21.5 Version stamping amended (`param_since`, the post-schema check, the answer stamp, the relay gate) · 21.6 Capability discovery (`pc.protocol_versions`; what a phone does with `[]` and a stale list) · 21.7 Result shapes · 21.8 Errors (none new; where `PROTOCOL_INCOMPATIBLE` now arises) · 21.9 Compatibility and deploy order (the §2 matrix; cloud + PWA first, then agents) · 21.10 Examples (a 1.5-stamped start with `cursor`; a `cursor_pos`; a named `cursor_shape`; a custom `cursor_shape` shown truncated `"png": "iVBOR…"` and labelled as illustrative; the REST pc row) · 21.11 Evidence (which tests prove each row; rows "not yet verified on a device" named). §20.7's "Phase 2/3 add…" sentence is updated; the §18.7-style matrix gains the row "1.5 phone, 1.4 agent".

### 1.11 `shared/protocol/README.md` entry

"## Protocol 1.5 — Screen view, Phase 2a (2026-11-xx)". Paragraph: additive MINOR; a 1.4 agent keeps every 1.4 behaviour (Next screen is `surface: N`, 1.4); a 1.4 phone never receives a 1.5 field or message because they are sent only in sessions started with `cursor`; the relay refuses a 1.5-stamped command to a 1.4 agent before forwarding; the PC-signed answer carries the start's stamp. Table rows: version/registry 1.5; `cursor` param + `param_since`; `result.cursor`; DC `cursor_pos`/`cursor_shape`/`state.cursor`; limit; REST field; rules (command_version_stamping, screen_signaling 5, screen_sessions 1b, screen_cursor new, screen_content, screen_metering 3); fixtures (newer-MINOR case computed; `screen-dc-cases.json`). Library helpers: Python `command_protocol_version`, `Registry.min_version(name, params)`, `ActionSpec.param_since`, `build_screen_answer_payload(protocol_version=…)`, `parse_screen_dc_message` per-type cap; TS `commandProtocolVersion`, `minVersion(action, params)`, `buildScreenAnswerPayload({protocolVersion})`, `parseScreenDcMessage`, type `CursorKind`. Also a note under the version rule: "stamping is content-based for commands with `param_since` params, as for input batches".

---

## 2. cloud-api

### 2.1 Versions
`ours = (svc.registry.protocol_version,)` already derives (`agent_ws.py`, `controller_ws.py:154`). `router.SCREEN_PROTOCOL_VERSION` stays `"1.4"`.

### 2.2 Persisted `protocol_versions`
- `dome_api/db/models.py` `PC`: `protocol_versions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"), default=list)` next to `agent_version` (line 179).
- Alembic `dome_api/db/alembic/versions/0006_pc_protocol_versions.py` (revision `"0006"`, down `"0005"`): `op.add_column("pcs", sa.Column("protocol_versions", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")))`; downgrade drops it. Docstring in the 0005 style ("ids, versions, …: no content").
- `relay/agent_ws.py` after `pc.agent_version = …` (line 98): `pc.protocol_versions = [v for v in conn.protocol_versions if parse_version(v) is not None][:8]`.
- `routes/pcs.py::pc_body` (line 24): `"protocol_versions": list(pc.protocol_versions or [])`.

### 2.3 Command stamp gate
`relay/router.py`, immediately after the `supports_screen` rejection (line 380-385) and before the `SCREEN_START_ACTION` block:

```python
# 8b. the stamp gate for commands (rules.command_version_stamping, 1.5): a command stamped with a MINOR the
# agent did not announce (a screen.session_start carrying `cursor` to a 1.4 agent) is refused here, as 1.4-stamped
# input batches are below, so the shipped agent never answers MALFORMED_MESSAGE for a param it cannot validate
stamp = str(cmd.payload["protocol_version"])
if not protocol_compatible(stamp, agent.protocol_versions):
    raise Rejection("PROTOCOL_INCOMPATIBLE", "The PC's DoMe agent needs an update for this feature", detail={"peer": "agent", "required": stamp})
```

Placement before `reserve_start` means a refused `cursor` start reserves no allowance chunk and writes no commands row beyond what `Rejection` writes today for a relay-origin failure (a security event is recorded as for other rejections).

### 2.4 Tests (`cloud-api/tests`, real PostgreSQL)
- `conftest.py:74 DEFAULT_PROTOCOL_VERSIONS += "1.5"`; `test_auth_and_sessions.py:15` → `"1.5"`; `test_relay_routing.py:136` → `supported == ["1.5"]`; `test_relay_routing.py:668`'s "too new" literal → `"1.6"`.
- `test_screen_routing.py` new: `test_stamp_gate_shields_a_1_4_agent_from_a_1_5_start` — `AgentSim(protocol_versions=("1.0",…,"1.4"))`; a 1.5-stamped `screen.session_start` → `result{origin: relay, failed, PROTOCOL_INCOMPATIBLE, detail {peer: "agent", required: "1.5"}}`, the agent socket received nothing, `screen_reservations` has no new row, the ledger is unchanged; a 1.4-stamped start to the same sim is forwarded; both to a default (1.5) sim are forwarded. The existing `test_stamp_gate_shields_a_1_2_agent_from_1_4_batches` (line 833) stays.
- `test_entitlement_and_misc.py`: `GET /v1/pcs` body validates; shows `["1.0",…,"1.5"]` while a sim is online; after the sim disconnects the row still shows the list; a PC that never connected shows `[]`.
- `test_screen_migration.py` (or a new `test_pcs_migration.py`): `0006` upgrade/downgrade round-trip, default `[]`.

### 2.5 Docs
`cloud-api/DECISIONS.md` #115 "`pc.protocol_versions` is a persisted REST field, not a `pc_status` field" (why: `pc_status` is `additionalProperties:false` and `broadcast_to_subscribers` sends one frame to every subscriber; REST answers offline and follows `agent_version`); #116 "The relay refuses any command whose stamp the agent did not announce before forwarding" (generalises #… the 1.4 batch gate; placement before `reserve_start`). `KNOWN_ISSUES.md`: "`protocol_versions` is as announced at the last hello; a downgraded agent shows its shorter list only after reconnecting".

---

## 3. pc-agent

### 3.1 Versions and answer stamp
- `dome_agent/__init__.py:15`: `SUPPORTED_PROTOCOL_VERSIONS = ("1.0", "1.1", "1.2", "1.3", "1.4", "1.5")` (`frames.hello_frame` announces it).
- `screen/signing.py::sign_screen_answer(..., protocol_version: str)` passes it to `build_screen_answer_payload`; `actions/screen.py` passes `ctx.command.payload["protocol_version"]`.

### 3.2 `actions/screen.py`
- `cursor = ctx.params.get("cursor")` (None when absent) → `manager.start_session(..., cursor=cursor)`.
- Result: `"cursor": session.cursor_mode` is added **only when `cursor is not None`**.

### 3.3 `screen/session.py`
- `ScreenSession` gains: `cursor_requested: str | None = None`, `cursor_mode: str | None = None` (the guess, then the worker's effective mode), `switched_from: int | None = None`, `confined_moves: int = 0` is NOT here (input side).
- `start_session(..., cursor: str | None = None)` → `_start_session(..., cursor=cursor)`.
- `_start_session` (from line 502), in order: unchanged checks through `check_offer`; `existing = self.live_session`; owned check; **consent**: `if existing is not None and existing.controller_id == controller_id and existing.state != "ended" and existing.consent in ("granted", "not_required"): consent = existing.consent` else `consent = await self._consent(controller_id, display_name)` (rule 1b); monitors; `_pick_surface` (an unknown index raises `INVALID_PARAMETERS` before anything ends); pre-flight only when `existing is None` (unchanged); re-check; owned check again; `if existing is not None: await self._end(existing, reason, keep_indicator=existing.controller_id == controller_id)` with `reason = "takeover"|"replaced"`; `drain_ends()`; build `ScreenSession(..., cursor_requested=cursor, cursor_mode=self._cursor_guess(cursor), switched_from=existing.monitor.index if replacement and existing.monitor.index != monitor.index else None)`.
- `_cursor_guess(cursor) -> str | None`: `None` when `cursor is None`; `"drawn"` when `cursor == "drawn"`; else `"overlay"` unless the source will be WGC on a build below 18362: `wgc = self.source.get("kind") == "wgc" or (preflight says DDA is skipped)`; `"drawn" if wgc and not self._platform.screen.cursor_capture_configurable() else "overlay"`. `ScreenAdapter.cursor_capture_configurable() -> bool` is new on the platform protocol: Windows returns `sys.getwindowsversion().build >= 18362`; the fake returns `FakeState.cursor_capture_configurable` (default True).
- `_end(session, reason, *, keep_indicator: bool = False)`: when true the bars are not released. Safety: `_start_session` wraps everything after that `_end` in `try/except`: on any exception before the new session's `_bring_up` armed the bars, `await self.indicator.release()` (never leave bars up without a session), then re-raise.
- `_start_message`: adds `"cursor": session.cursor_requested` when not None; adds `"hw_encoding": os.environ.get("DOME_SCREEN_H264_HW", "1") != "0"`.
- `_on_worker_event`: new kinds `"cursor"` (`{"ev": "cursor", "mode": "drawn"|"overlay"}` → `session.cursor_mode = mode`, `log.info("screen cursor mode", mode=mode, source=ev.get("source"))`), `"encoders"` (`{"ev": "encoders", "probed": [...]}` → `session.media["encoders"] = probed` after `media_from_stats`-style key whitelisting: `encoder, ok, reason, frames_in, open_ms, mft, parameter_sets`); `stats` events carry the cursor counters (`cursor_pos_sent, cursor_shape_sent, cursor_custom_sent, cursor_png_max_chars, cursor_png_too_large, cursor_dropped_gen, cursor_poll_errors, cursor_last_kind`) and `encoder_hw: bool` through `media_from_stats` (`session.py:147` whitelist extended; numbers and the kind word only).
- `first_frame` toast: when `session.switched_from is not None`: `self._ui_notify("Screen view", f"{session.display_name} switched to screen {session.monitor.index + 1}. {STOP_HINT}")`, else the existing wording.
- New accessor `streamed_monitor(controller_id: str) -> Monitor | None`: `self._current.monitor` when the session exists, `state != "ended"`, `connected`, and `controller_id` matches (gen-independent; confinement works while `geometry_pending`).
- `summary()`/`status_view`/history: `cursor: {requested, mode}` and the counters; the history row stays content-free.

### 3.4 `input_session.py` — confinement
- `InputSessionManager.__init__(..., streamed_monitor: Callable[[str], Monitor | None] | None = None)`; `agent.py:170` wires `streamed_monitor=lambda controller_id: self.screen.streamed_monitor(controller_id)` next to `screen_target`.
- `_DispatchOutcome` gains `confined: int = 0`, `confine_failures: int = 0`; `InputSession` gains `confined_moves: int = 0`, `confine_failures: int = 0` (into `summary()` and the acks' log fields: `status.input.session.confined_moves`).
- In `_dispatch_sync`, directly after `adapter.move(int(ev["dx"]), int(ev["dy"]))`: `self._confine_after_move(session, out)`:

```python
def _confine_after_move(self, session: InputSession, out: _DispatchOutcome) -> None:
    """rules.screen_cursor (5): an injected relative move that left the streamed monitor is pulled back inside."""
    if self._streamed_monitor is None:
        return
    monitor = self._streamed_monitor(session.controller_id)
    if monitor is None:
        return
    try:
        pos = self._platform.input.cursor_position()
        if pos is None:
            return
        cx = min(max(pos[0], monitor.left), monitor.left + monitor.width - 1)
        cy = min(max(pos[1], monitor.top), monitor.top + monitor.height - 1)
        if (cx, cy) != pos:
            self._platform.input.move_to(cx, cy)
            out.confined += 1
    except ProtocolError as exc:  # best effort: the batch is never failed by its confinement
        out.confine_failures += 1
        log.debug("pointer confinement failed", code=exc.code)
```

Coalescing (`coalesce_events`, line 278) already makes adjacent moves one event, so this is at most 40 `GetCursorPos` per second and a `SetCursorPos` only when an edge was crossed. Never `ClipCursor`. `pointer_move_to` never triggers it (it already maps onto the streamed monitor).

### 3.5 Worker: pipe protocol
- `start` gains `cursor: "drawn" | "overlay"` (absent for a 1.4-stamped start) and `hw_encoding: bool`.
- New events: `{"ev": "cursor", "mode": "drawn"|"overlay", "source": "dda"|"wgc"|"fake"}` once per source open (only when `start.cursor` was present); `{"ev": "encoders", "probed": [ProbeResult.as_dict()...]}` after `answer`; `stats` gains the cursor counters and `encoder_hw`.
- `_state_message()` (main.py:364) adds `"cursor": self._cursor_effective` when `self._cursor_requested is not None` (the guess until the source opened; then the effective mode).
- `_shape_for_vocabulary(message)` in `main.py`: when `self._cursor_requested is None` (a 1.4 session) it deletes `cursor` from a `state` message and refuses to send `cursor_pos`/`cursor_shape` (returns None → not sent, counted `dropped_1_4`). `_send_dc` passes every outbound message through it; `rtc.send_dc` still validates against `pc_to_phone`.

### 3.6 Worker: sources without the pointer
- `capture_dda.ddagrab_graph(output_idx, width, height, fps, *, draw_mouse: bool = True)` → `draw_mouse={1 if draw_mouse else 0}`. `DdagrabSource(..., draw_mouse: bool = True)`, attribute `cursor_drawn = draw_mouse`. The licence gate's graph copy equals the default-argument graph (unchanged equality test); new test: `draw_mouse=False` changes only that token (`graph_filters`).
- `capture_wgc.WgcSource(..., cursor_capture: bool | None = None, pix_fmt: str = "yuv420p")`: `open()` calls `NativeWindowsCapture(self._on_frame_arrived, self._on_closed, cursor_capture=False, monitor_index=position)` when `cursor_capture is False`; if that raises and (`"cursor" in str(exc).lower()` or `"Cursor" in exc.__class__.__name__`), it logs once `"Windows.Graphics.Capture cannot leave the pointer out; it stays drawn"` (reason sanitised), retries with the 1.4 call (`monitor_index=position` only) and sets `self.cursor_drawn = True`; any other exception is today's `SCREEN_CAPTURE_UNAVAILABLE`. `read()` (line 299): `image.reformat(width=self.width, height=self.height, format=self.pix_fmt)`.
- `DdaWithFallback(..., draw_mouse, cursor_capture, pix_fmt)` passes them through; exposes `cursor_drawn` of the active source and `kind` (`"dda"|"wgc"`).
- `main._build_source(kind, spec, width, height, fps, *, cursor_drawn: bool, pix_fmt: str)`; `_start` passes `cursor_drawn = message.get("cursor") != "overlay"` and `pix_fmt = choice.pix_fmt`; `_rebuild` reuses them.
- `track.ScreenTrack(..., on_source_open: Callable[[dict[str, Any]], None] | None = None)`: `_open_quietly` and `_swap_source` call it after a successful `source.open()` with `{"source": kind, "cursor_drawn": bool(getattr(source, "cursor_drawn", True))}`. `WorkerApp._on_source_open(info)`: when `self._cursor_requested is not None`: `self._cursor_effective = "drawn" if (self._cursor_requested == "drawn" or info["cursor_drawn"]) else "overlay"`; emit `{"ev": "cursor", "mode", "source"}`; `_send_dc(self._state_message())`; start the poller (overlay) or make sure it is stopped (drawn).

### 3.7 Worker: `screen/worker/cursor.py` (pure logic + the poller) and `cursor_win.py` (ctypes)

Constants (`cursor.py`): `CURSOR_POS_HZ = 60`, `CURSOR_SHAPE_CUSTOM_PER_SECOND = 4`, `CURSOR_PNG_MAX_CHARS = 8192`, `CURSOR_BITMAP_MAX_PX = 64`, `CURSOR_SHAPE_CACHE_SIZE = 8`, `CURSOR_SHAPE_CACHE_TTL_SECONDS = 10.0`, `CURSOR_POLL_ERRORS_MAX = 10`. `NAMED_KINDS: dict[int, str] = {32512: "arrow", 32513: "ibeam", 32514: "wait", 32515: "cross", 32516: "uparrow", 32642: "sizenwse", 32643: "sizenesw", 32644: "sizewe", 32645: "sizens", 32646: "sizeall", 32648: "no", 32649: "hand", 32650: "appstarting", 32651: "help"}` (IDC_* ids).

Pure functions (unit-tested on Linux):
- `normalise_cursor(px, py, rect: tuple[int, int, int, int]) -> tuple[int, int, bool]`: `(left, top, width, height)` physical pixels → `(x, y, on_surface)`, `x = clamp(round((px − left) · 65535 / max(width − 1, 1)), 0, 65535)` (the inverse of `session.map_to_pixels`), `on_surface = left ≤ px < left+width and top ≤ py < top+height`.
- `fit_cursor(w, h, limit=64) -> tuple[int, int]`: scale down keeping aspect, never up, each ≥ 1.
- `alpha_from_mask(and_bits: bytes, w, h, stride) -> bytes`: AND bit 1 → alpha 0, else 255 (mask-only cursors; inverted-screen pixels become opaque white: documented approximation).
- `encode_cursor_png(bgra: bytes, w, h, stride, hx, hy) -> tuple[bytes, int, int, int, int] | None`: `PIL.Image.frombuffer("RGBA", (w, h), bgra, "raw", "BGRA", stride, 1)`; resize with `Image.LANCZOS` to `fit_cursor(w, h)` (hotspot scaled and clamped to ≤ 63); `save(BytesIO, "PNG", optimize=True)`; returns `(png_bytes, w2, h2, hx2, hy2)`.
- `shape_message(kind, *, width=None, height=None, hotspot_x=None, hotspot_y=None, png: bytes | None = None) -> dict | None`: named → `{"type": "cursor_shape", "kind": kind}`; custom → with base64 (`len ≤ CURSOR_PNG_MAX_CHARS`, else `None`; the caller sends `arrow` and counts `cursor_png_too_large`).
- `pos_message(gen, x, y, visible) -> dict`.

`CursorReader` protocol: `read() -> tuple[int, int, int, int] | None` (`flags, hcursor, px, py`; None when the call failed, treated as not visible), `named_kind(hcursor) -> str | None`, `render(hcursor) -> tuple[bytes, int, int, int, int, int] | None` (`bgra, w, h, stride, hotspot_x, hotspot_y`).

`Win32CursorReader` (`cursor_win.py`, constructed on win32 only; `user32`/`gdi32` loaded lazily; structures `CURSORINFO`, `ICONINFO`, `BITMAP`, `BITMAPINFOHEADER` with `ctypes.sizeof` asserted in a Linux test): the poller thread calls `SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4)` once at start (own thread, nothing to restore; pattern `platform/windows/input.py:480,542`) so `ptScreenPos` is in physical pixels like `Monitor`; `named_kind` compares against `{LoadCursorW(None, id): kind}` built once; `render`: `GetIconInfo` → size from `GetObjectW(hbmColor or hbmMask, BITMAP)` (mask-only: height //= 2) → `CreateDIBSection` 32-bpp top-down (`biHeight = −h`) zeroed → `SelectObject` → `DrawIconEx(hdc, 0, 0, hCursor, w, h, 0, None, DI_NORMAL=3)` → read bits; if every alpha is 0: second DIB with `DI_MASK=1` → `alpha_from_mask`; every GDI object released in `finally`. Cost: `GetCursorInfo` microseconds; a render 0.5–3 ms, a few per second at most.

`FakeCursorReader(script)` in `screen/worker/fake.py`: scripted `(flags, tag, px, py)` samples advanced one per `read()`, `shapes: dict[tag, kind | ("custom", rgba_bytes, w, h, hx, hy)]`; selected when `source.kind == "fake"` and `spec["cursor_script"]` is present (the e2e agent sets it through a control op, §5.3); the default fake script is a "walk" (2 px per tick diagonal; `arrow`/`ibeam` toggle every 30 ticks; one 24×24 custom shape at tick 90; off-rectangle samples at ticks 150–159).

`CursorPoller(threading.Thread, daemon=True)`: `__init__(reader, *, on_pos, on_shape, hz=CURSOR_POS_HZ)`; `set_surface(rect, gen)` (tuple swap, called on the loop thread before the `geometry` message is sent, in `_announce_geometry`); `pause()`, `resume()`, `stop()`. Loop paced by `perf_counter`; each tick: `read()`; `visible = bool(flags & CURSOR_SHOWING) and on_surface`; a `(x, y, visible, gen)` differing from the last sent → `on_pos`; `hcursor != last_hcursor` → `named_kind` or `cache.get(hcursor) or render+encode` → `on_shape` (custom shapes rate-limited to 4/s, latest wins); the first shape precedes the first position; `pause()` emits one `visible: false` then waits; `resume()` forgets the last sample so shape and position are re-sent at once; `CURSOR_POLL_ERRORS_MAX` consecutive exceptions → stop polling, emit one `visible: false`, count `cursor_poll_errors`, log `"cursor reporting stopped"` (no content); `stop()` joins within one tick. `CursorShapeCache`: ≤ 8 entries keyed `(hcursor, hotspot_x, hotspot_y, w, h)`, TTL 10 s.

Delivery (`WorkerApp`): `on_pos`/`on_shape` run on the poller thread: store into a one-slot `_cursor_pending_pos` / a small deque `_cursor_pending_shapes`, then `loop.call_soon_threadsafe(self._cursor_flush)`. `_cursor_flush` (loop thread): drop a position whose `gen != self._gen` (`cursor_dropped_gen += 1`), send shapes before the position through `_send_dc`. `pause`/`resume` pipe ops pause/resume the poller; `rebuild`/`reserved` call `set_surface` before `_announce_geometry` sends, and `_announce_geometry` triggers a re-send (resume semantics). Nothing here logs a position, a handle or a bitmap: log lines carry `kind`, `w`, `h`, `png_chars` only.

### 3.8 Worker: hardware H.264 (`encode.py`, `track.py`)
- `EncoderChoice.pix_fmt: str = "yuv420p"`. `_h264_candidates(win32)`: `[h264_mf hw_encoding=1 nv12, h264_mf hw_encoding=0 nv12]` (options otherwise as today); dev `libx264` candidate `pix_fmt="nv12"`; VP8 `yuv420p`. `encoder_candidates(preference, *, dev_h264, platform, hw: bool = True)`: `hw=False` drops the hardware candidate.
- `H264Encoder.open`: `ctx.pix_fmt = self.choice.pix_fmt` (line 351); `open_facts()` reports the real `pix_fmt` and `hw: choice.hardware` (line 398-403). `reopen(bitrate_kbps)`: on exception, `close(strict=False)`, `time.sleep(0.1)`, one more open; a second failure is the existing `open_error` stall path.
- Probe:

```python
PROBE_MAX_FRAMES = 16      # DECISIONS #98: the software MFT held 6–9 frames on the founder's PC (first packet 5.5–9.2 s at 1 fps)
PROBE_BUDGET_SECONDS = 3.0

@dataclass(frozen=True, slots=True)
class ProbeResult:
    encoder: str; ok: bool; reason: str = ""; frames_in: int = 0; open_ms: int = 0
    mft: str | None = None; parameter_sets: str = ""   # "inband" | "extradata" | ""
    def as_dict(self) -> dict[str, Any]: ...

def probe_candidate(choice: EncoderChoice, width: int, height: int) -> ProbeResult:
    # open (EncoderOpenError → "open: <FFmpeg's words>"); then feed black frames of choice.pix_fmt, pts i*3000 (90 kHz),
    # force_idr on the first; a packet whose NAL types include 5 or 1 is a slice: ok iff enc.parameter_sets() is non-empty
    # ("packet without SPS/PPS" otherwise); stop at PROBE_MAX_FRAMES ("no packet within 16 frames") or the budget
    # ("no packet within 3.0 s"); encode exceptions → "encode: <reason>"; always enc.close().
```

`probe_encoders(candidates, width, height, failures, results: list[ProbeResult] | None = None)` keeps its signature, loops `probe_candidate`, appends a `ProbeFailure(label, reason)` per failure and every `ProbeResult` to `results`. `_start` logs one line per candidate: `log.info("screen encoder probed", encoder=r.encoder, ok=r.ok, frames_in=r.frames_in, open_ms=r.open_ms, mft=r.mft, parameter_sets=r.parameter_sets, reason=r.reason)` (the key is `encoder`, never `candidate`; never a parameter set's bytes) and emits `{"ev": "encoders", "probed": [...]}` after `answer`. `list_encoders` and `--self-test` reuse `probe_candidate` (rows gain `frames_in`, `mft`).
- `track.py`: `_encode_quietly` reformats when `frame.format.name != self._encoder_pix_fmt` (`frame.reformat(format=pix_fmt)`; VP8's target is `yuv420p`); `_last_frame` is kept in the source's format; the drain pump, `encoder_stalled → VP8 first` (DECISIONS #100/#101/#103) and the 1 fps floor stay. `screen encoder opened` adds `hw`, `pix_fmt`, `mft`. `media_snapshot` adds `encoder_hw`.
- `fake.py`: `SyntheticSource`/`LavfiTestSource` stay yuv420p by default (the track converts to nv12 on the dev `libx264` path: the conversion is exercised on Linux); `build_fake_source(spec, …, pix_fmt)` honours `spec["pix_fmt"] == "nv12"` for a direct NV12 test (`_blank_frame` builds NV12: Y = 16, UV = 128).

### 3.9 Testing doubles
- `testing/fake_screen_worker.py`: accepts `cursor` and `hw_encoding` in `start`; emits `{"ev": "cursor", "mode": script.cursor_mode or requested, "source": "fake"}` after `connected` when the start carried `cursor`; `script.cursor_mode: str | None`.
- `testing/fake_platform.py`: `FakeState.cursor_capture_configurable: bool = True`; `FakeScreen.cursor_capture_configurable()`.

### 3.10 Diagnostics and redaction
- `diagnostics.py`: `status.screen.session.media.cursor {mode, pos_sent, shape_sent, custom_sent, png_max_chars, png_too_large, dropped_gen, poll_errors, last_kind}`, `media.encoders` (probe rows), `media.encoder_hw`, `status.screen.session.surface {index, switched_from}`, `status.input.session.confined_moves`, `status.settings.screen_h264_hw`.
- `logsetup.py`: `"png"` added to `SENSITIVE_KEY_PARTS`.
- Licence gate: unchanged inventory (no new DLL; PIL already bundled; `user32`/`gdi32` are system).

### 3.11 Tests (`cd pc-agent && uv run --frozen pytest -q tests`, ruff, mypy both configs)
- `tests/test_screen_cursor.py` (new): `normalise_cursor` is the inverse of `map_to_pixels` on negative origins and mixed sizes; `fit_cursor`; `alpha_from_mask`; `encode_cursor_png` round trip (PIL decode equals input; ≤ 64; base64 ≤ 8192; a 70×70 input scaled to 64 with the hotspot scaled); `shape_message` cap → None; `CursorPoller` with `FakeCursorReader`: shape before pos, only on change, ≤ 60/s under a 240 Hz script, custom ≤ 4/s latest-wins, `pause` → one `visible:false`, `resume` → re-send, `set_surface` gen handoff (no old-gen sample leaves after the swap), cache TTL, ten errors → stop + one `visible:false` + counter; struct sizes (`CURSORINFO`, `ICONINFO`, `BITMAP`, `BITMAPINFOHEADER`).
- `tests/test_screen_worker.py` (aiortc loopback): start with `cursor: "overlay"` on the fake source → `cursor` event, `state{cursor: "overlay"}`, `cursor_shape` then `cursor_pos` on the receiving side validated with `parse_screen_dc_message(…, "pc_to_phone")` (raw lengths asserted: custom `> 1024 and ≤ 12288`); `cursor: "drawn"` → `state.cursor == "drawn"` and no cursor messages; no `cursor` in start → **no 1.5 key in any outbound message** (`_shape_for_vocabulary` test); a `rebuild` is followed only by `cursor_pos` of the new gen; a scripted `cursor_drawn=True` source → `cursor{mode: drawn}` and the poller never starts; the 1 KiB/12 KiB rule through `rtc.send_dc`.
- `tests/test_screen_capture_fallback.py`: `ddagrab_graph(draw_mouse=False)` token; WGC fake raising `CursorConfigUnsupported` on `cursor_capture=False` → retried with the 1.4 call, `cursor_drawn=True`; `_build_source` passes `pix_fmt`; `DdaWithFallback.cursor_drawn` follows the active source.
- `tests/test_screen_media_path.py`: `probe_candidate` with a scripted context that holds 7 frames → `ok, frames_in=8`; one that never outputs → `"no packet within 16 frames"`; packets without SPS/PPS and empty extradata → refused; in-band sets accepted with empty extradata; `pix_fmt == "nv12"` on both `h264_mf` candidates and the dev candidate; `hw=False` removes the hardware candidate; `H264Encoder.open` sets the choice's `pix_fmt`; the track converts a yuv420p fake frame to the encoder's `pix_fmt`; a VP8 track converts an nv12 frame to yuv420p; `reopen` retries once; `encoders` event shape; `WgcSource.read` reformats to the asked format (scripted frame).
- `tests/test_screen_session.py`: result has `cursor` only when requested (1.4 and 1.5 starts); same-controller `surface: 1` → old session `ended replaced`, **no new consent window in ask mode**, new result `surface_index 1`, `gen 1`, `consent` inherited; a takeover still asks; `surface: 5` with two monitors → `INVALID_PARAMETERS`, old session untouched; bars kept across the replacement and released when the new start fails after the old `_end`; the answer is stamped with the command's stamp (1.4 and 1.5); `streamed_monitor` contract (None when ended / not connected / other controller; gen-independent); `_cursor_guess` for ddagrab/wgc/fake × configurable/not; the "switched to screen N" toast; `media` carries the counters and `encoders`.
- `tests/test_input_session_screen.py`: two monitors in `FakeState.monitors`, a session on monitor 1 (left 1920), cursor at 3199 → `pointer_move dx=+400` → `move_to(3199, y)` recorded, cursor `(3199, y)`; a move towards monitor 0 clamped at 1920; a `move, click, move` batch clamps before the click; inside → no `move_to`; no screen session / another controller's session → none; a `pointer_move_to` batch never confines; `move_to` raising → batch still acked, `confine_failures == 1`.
- `tests/test_diagnostics*.py`: cursor counters and probe rows present; a planted `"png": "iVBOR…"` key is `[redacted]`; no `png`, `x`, `y` under `media.cursor`; `settings.screen_h264_hw`.
- `tests/test_licence_gate*.py`: unchanged.
- `tests/test_screen_redaction.py` (or the existing redaction test file): the new log lines (`screen cursor mode`, `screen encoder probed`, `cursor reporting stopped`) carry no coordinate pair, handle value or base64 run.

### 3.12 Docs
- `DECISIONS.md` #105 "Hardware H.264: NV12 on every h264_mf candidate, a probe that proves a slice packet and SPS/PPS within 16 frames, `DOME_SCREEN_H264_HW`, one reopen retry, no vendor forced" · #106 "The pointer overlay: a 60 Hz daemon poller in the worker, named kinds by LoadCursorW identity, custom shapes through GetIconInfo/DrawIconEx/PIL, the bitmap is screen content, the effective mode is reported after the source opened" · #107 "Pointer confinement after each injected relative move; never ClipCursor; counted, best effort" · #108 "A same-controller surface switch skips a new consent window and keeps the bars up" · #109 "The signed answer carries the start's stamp" · #110 "1.5 fields and messages only in sessions whose start carried `cursor` (`_shape_for_vocabulary`)".
- `KNOWN_ISSUES.md` #35 rewritten: "**Hardware H.264 is enabled but not yet verified on a device.** Both `h264_mf` candidates open with NV12 input, the sources deliver NV12 (Desktop Duplication natively, Windows.Graphics.Capture by a libswscale conversion), and a candidate is used only after the probe got a slice packet with SPS and PPS out of it within 16 frames at the session's real size (`screen encoder probed` lines; `media.encoders` in the bundle). Unverified: which transform Media Foundation picks on the hybrid laptop (`mft`), its latency (`frames_in`), bitrate reopens, long sessions (a transform that stops producing is reported `encoder_stalled` and the next start offers VP8 first), extradata-less transforms (in-band sets preferred; neither → skipped with that reason), picture quality. `DOME_SCREEN_H264_HW=0` turns the hardware candidate off." #36 stays. New: "Cursor overlay not yet verified on a device: custom cursors, 150 %/200 % DPI, monochrome (mask-only) cursors render white where inverted, Windows pointer schemes are drawn with the phone's glyphs, a reused cursor handle inside the 10 s cache TTL shows one wrong shape"; "WGC on Windows 10 before 1903 keeps the pointer drawn (`state.cursor: drawn`)"; "the watched monitor being unplugged still ends the session `capture_failed` (Phase 2b: rebuild onto the primary)".
- `CONTRACT_ISSUES.md`: "`result.cursor` is sent only when the param was present (1.4 phones validate with additionalProperties:false)"; "the answer stamp follows the command".

---

## 4. mobile-app

### 4.1 `lib/screen.ts` (`ScreenSessionClient`)

State additions (`ScreenSessionState`, with `EMPTY_SCREEN_STATE` defaults):

```ts
surfaceIndex: number | null;                                   // result.surface_index, then geometry.surface_index
switching: { to: number; from: number | null; startedAt: number } | null;
switched: { index: number; count: number; at: number } | null; // drives the 1.5 s "Screen 2 of 2" chip
switchedGone: { human: number; at: number } | null;            // drives the 3 s "Screen N isn't connected any more" chip
cursorMode: "drawn" | "overlay" | null;                        // result.cursor (provisional) then state.cursor (authoritative); null = 1.4 session
pointerOverlayAsked: boolean;                                  // this start carried cursor: "overlay"
```

Deps: `ScreenClientDeps.pcProtocolVersions(pcId: string): readonly string[]` (from the devices store). Instance fields (not state): `pointer: PointerModel` (public, §4.2), `noCursorFor: string | null`, `previousPeer: RTCPeerConnectionLike | null`, `lastSurfaceByPc: Map<string, number>`.

`start(pcId, options: { takeover?: boolean; retries?: number; surface?: number | "primary"; switching?: { from: number | null } })`:
- When `options.switching` is absent: as today (`stop("restart" | "pc_switch")` when not idle/ended/failed). When present: **no `stop()`**: `this.nonce += 1`; `this.clearTimers()`; `this.retire(this.state.sessionId)`; `this.previousPeer = this.peer; this.peer = null` (the old data channel's heartbeat stops; the PC ends the old session `replaced` when the new start lands); state reset keeps `kept = { allowance, otherViewer, surfaces, surfaceIndex, stream, switching: { to: options.surface, from, startedAt: now } }`.
- `surface = options.surface ?? this.lastSurfaceByPc.get(pcId) ?? "primary"`.
- `speaks15 = protocolCompatible("1.5", this.deps.pcProtocolVersions(pcId)) && this.noCursorFor !== pcId`; params: `{ offer_sdp, surface, audio: false, takeover: options.takeover === true, ...(speaks15 ? { cursor: "overlay" } : {}) }`; the shared `buildCommandPayload` stamps 1.5 iff `cursor` is present. `pointerOverlayAsked = speaks15`.
- Result failure handling (in `onResult`/the failed path): `PROTOCOL_INCOMPATIBLE` with `detail.required === "1.5"` while `pointerOverlayAsked` → `this.noCursorFor = pcId`, `log.info("screen.cursor_param_refused", { pc_id })`, restart once without `cursor` (same `surface`, same `retries`, `switching` preserved); `INVALID_PARAMETERS` while `surface !== "primary"` → `lastSurfaceByPc.delete(pcId)`, `switchedGone = { human: human(surface), at: now }`, retry once with `"primary"`.
- `noCursorFor` is cleared when `pcProtocolVersions(pcId)` later includes 1.5 (checked at each `start`).

`switchSurface(index: number): Promise<ScreenSessionState>`: no-op unless `phase ∈ {live, paused}`, `switching === null`, `index !== surfaceIndex` and `surfaces.some(s => s.index === index)`; then `start(pcId, { surface: index, retries: this.state.retries, switching: { from: this.state.surfaceIndex } })`. `nextSurfaceIndex(): number | null` = the index after `surfaceIndex` in `surfaces` sorted by `index`, wrapping.

`onResult`: `surfaceIndex = r.surface_index`, `cursorMode = r.cursor ?? null`, seeds the tracker as today; `lastSurfaceByPc.set(pcId, r.surface_index)`; `pointer.reset(); pointer.setMode(cursorMode)`.

`onTrack` (line 655): when `this.previousPeer` is set, `closePeer(this.previousPeer)` now (the new track exists), `previousPeer = null`. On any `fail()` while `previousPeer` is set: close it too (the old session is gone on the PC anyway).

`onVideoFrame` (line 874): when `switching !== null` on the first frame of the new session: `switched = { index: surfaceIndex, count: surfaces.length, at: now }`, `switching = null`; `deps.onViewReset?.()` is not needed (the surface resets the view on stream change already, `ScreenSurface.tsx:114-119`).

`onChannelMessage` (line 998) new cases: `state` → `cursorMode = msg.cursor ?? cursorMode`; when `msg.state === "paused"` → `pointer.hide()`; `geometry` → as today plus `surfaceIndex = msg.surface_index`, `pointer.geometryChanged()` (drops the predicted/reported position until the next report); `cursor_pos` → `if (msg.gen !== this.geometry.known?.gen) { facts.pointer.droppedGen++; return; } pointer.reported(msg, now)`; `cursor_shape` → `const s = parseCursorShape(msg); if (s) pointer.shape(s); else facts.pointer.rejected++`. No `set()` per cursor message.

`restartFresh(cause)` passes `surface: this.state.surfaceIndex ?? undefined`. `stop()` unchanged, plus `pointer.reset()` and closing `previousPeer` if set. `facts` (`ScreenClientFacts`) gain `pointer: { mode, shapes, custom, rejected, positions, droppedGen, lastKind, predErrMaxPx }`, `surfaces: { count, showing, switches, switchFailures, gone }`, `zoom: { maxScale, followPans }` (numbers and short words only).

### 4.2 `lib/pointer.ts` (new, pure)

```ts
export const EASE_MS = 80; export const IDLE_MS = 120; export const BLEND_WHILE_MOVING = 0.35; export const SNAP_FRACTION = 0.1;
export interface PointerReport { x: number; y: number; visible: boolean; gen: number }
export type CursorShape = { kind: CursorKind } | { kind: "custom"; width: number; height: number; hotspotX: number; hotspotY: number; src: string };
export interface PointerSample { x: number; y: number; visible: boolean; shape: CursorShape | null; busy: boolean }
export class PointerModel {
  setMode(mode: "drawn" | "overlay" | null): void;       // drawn/null → nothing is ever visible; reset()
  reported(r: PointerReport, now: number): void;        // caller already filtered gen
  shape(s: CursorShape): void;                          // keeps the current shape only
  predictMoveBy(dxPc: number, dyPc: number, surface: { width: number; height: number }, now: number): void; // touchpad
  predictTo(x: number, y: number, now: number): void;   // direct: tap / loupe end point (normalised)
  fingerActive(active: boolean, now: number): void;
  geometryChanged(): void;                              // hide until the next report
  hide(): void; reset(): void;
  sample(now: number): PointerSample;                   // per animation frame
}
```

Rules: nothing visible before the first report of the session (never a guess at 0,0); `predictMoveBy` adds `dx · 65535/(W−1)`, `dy · 65535/(H−1)` to the predicted point, clamped, and records `lastLocalMoveAt`; `predictTo` snaps the predicted point; on `reported`: if no prediction → adopt; `err = reported − predicted`; `|err| > SNAP_FRACTION · 65535` → snap; `now − lastLocalMoveAt > IDLE_MS` → ease from the displayed point to the report over `EASE_MS` (cubic ease-out); else `predicted += BLEND_WHILE_MOVING · err`. `busy` is true while an ease runs (the overlay schedules frames only while busy or on new input). Visible iff `mode === "overlay" && lastReport.visible && !hidden`.

### 4.3 `lib/cursorShape.ts` (new, pure)

`pngDimensions(b64: string): { width: number; height: number } | null`: `atob` the first 44 characters (33 bytes), check the 8-byte PNG signature, `IHDR` at byte 12, read width/height big-endian at 16/20; `parseCursorShape(msg: ScreenDcPcToPhone & { type: "cursor_shape" }): CursorShape | null`: named → `{ kind }`; custom → dimensions must equal `width`/`height` (≤ 64), hotspot inside, else `null` (counted; `log.info("screen.cursor_shape_rejected", { reason })` with a reason word only); returns `src = "data:image/png;base64," + msg.png`. Byte inspection only: no canvas, no `createImageBitmap` (`no-pixel-access.test.ts` FORBIDDEN list). CSP already allows `img-src 'self' data:` (`cloud-api/dome_api/security/headers.py:21`).

### 4.4 `lib/cursorGlyphs.ts` (new) and `components/ScreenPointer.tsx` (new)

`CURSOR_GLYPHS: Record<CursorKind, { paths: Array<{ d: string; fill: "white" | "black" }>; hotspot: { x: number; y: number }; animate?: "spin" }>` in a 24-unit box: `arrow` (1,1) · `ibeam` (12,12) · `hand` (8,2) · `wait` ring, `animate: "spin"` 1 s linear (12,12) · `appstarting` arrow + small spinning ring (1,1) · `cross` (12,12) · `no` (12,12) · `sizeall` (12,12) · `sizens`/`sizewe`/`sizenesw`/`sizenwse` (12,12) · `uparrow` (12,1) · `help` arrow + "?" (1,1). White fill, 1 px black stroke, `drop-shadow(0 1px 1px rgba(0,0,0,.6))`.

`ScreenPointer` props: `{ pointer: PointerModel; fitRef; viewRef; enabled: boolean; hidden: boolean }`. One `<div aria-hidden className="pointer-events-none absolute left-0 top-0 z-[15] will-change-transform">` whose `style.transform = translate3d(px − hx·k, py − hy·k, 0)` and `style.opacity` are written on a ref in a `requestAnimationFrame` loop reading `pointer.sample(now)`; `(px, py) = fromSurface(x, y, fit, view)` (§4.6); `k = 1` for named glyphs (24 CSS px); custom: `k = Math.min(1, 24 / shape.height)`, `<img src={shape.src} width={shape.width·k} height={shape.height·k} draggable={false} alt="" />`. It is rendered by `ScreenSurface` as a sibling AFTER the zoom/pan layer (`data-testid="screen-view-layer"`, `ScreenSurface.tsx:387`), so pinch never scales it. `hidden` is true while `loupe !== null`, while `switching`, while `pcState !== "sending"` (paused), and for 150 ms after a direct tap ripple (`rippleAt` ref). CSS `transition: none` on transform (easing lives in the model), `transition: opacity 80ms`.

### 4.5 `lib/twoFinger.ts` (new), `lib/gestures.ts`, `lib/directGestures.ts`

```ts
export class TwoFingerClassifier {
  constructor(opts: { pinchRatio: number; scrollStartPx: number });
  start(a: Pt, b: Pt): void;
  /** One finger's move; decides on completed PAIRS of moves (the twoPending rule from directGestures.ts:93-98, 225-234). */
  move(id: number, a: Pt, b: Pt): "undecided" | "pinch" | "scroll";
  readonly decided: "pinch" | "scroll" | null;
  readonly startDistance: number; readonly startMid: Pt;
  reset(): void;
}
```

`DirectGestureMachine` uses it internally for `two_undecided` (behaviour byte-for-byte; `directGestures.test.ts` stays green; the moved cases get a `twoFinger.test.ts` twin). `GestureMachine` (`gestures.ts`): `GestureOutput` gains `{ type: "pinch"; scale: number; cx: number; cy: number }`; options gain `pinchRatio = 0.1`, `scrollStartPx = 8`; phase `two` is split into `two_undecided` → `two_scroll` | `two_pinch` using the classifier; `two_pinch` emits `pinch` per move (scale = distance / previous distance, midpoint in the coordinates it was fed) and never a click or scroll; `two_release`/`two_settling` semantics unchanged ("a second finger never clicks"; a quick two-finger lift is still a right click). `ScreenSurface` feeds the touchpad machine `point(e)` (box px) instead of `clientX/Y` (`ScreenSurface.tsx:336, 350`): the machine uses deltas and distances only, so moves and scrolls are unchanged and the pinch centre is in box px. `touchpadEvent()` (line 53) never receives `pinch`: `drainTouchpad` handles it first.

### 4.6 `lib/screenMap.ts`
- `fromSurface(x: number, y: number, fit: Fit, view: View): { px: number; py: number }` = `px = view.tx + view.s · (fit.offX + (x / 65535) · fit.cw)`, likewise `py` (the inverse of `toSurface`, line 107).
- `followPointer(view: View, p: { px: number; py: number }, box: Box, marginFrac = 0.2, minMarginPx = 48): View`: with `s > 1`, the inner window is `[m, box.width − m] × [m, box.height − m]`, `m = max(minMarginPx, marginFrac · min(box.width, box.height))`; if `p` lies outside, shift `tx`/`ty` by the overshoot so it sits on the window's edge; return `clampView(...)`; identity at `s === 1`.
- `snapIdentity(view: View): View` — `s < 1.1` → `IDENTITY_VIEW` (applied by both drains after `zoomAt`).

### 4.7 `components/ScreenSurface.tsx`
- Props gain `pointer: PointerModel`, `surfaceSize: () => { width: number; height: number } | null`, `cursorMode: "drawn" | "overlay" | null`, `pcPaused: boolean`, `switching: boolean`, `onFollowPan?: () => void` (counts follow pans for the readout).
- `drainTouchpad`: `pinch` → `applyView(snapIdentity(zoomAt(view, scale, cx, cy, box)))`; `move` → `pointer.predictMoveBy(dx, dy, surfaceSize(), now)` **then** `send(touchpadEvent(out))` (the same `dx, dy` that go into the batch: PC pixels, sensitivity applied by `emitMove`, `gestures.ts:288-296`); everything else as today. `drainDirect`: `tap` and `loupe end` → `pointer.predictTo(x, y, now)` when `locate()` was `ok` (normalised point from `screenInput.ts`); the ripple sets `rippleAt`. Pointer down/up in touchpad mode → `pointer.fingerActive(true|false, now)`.
- Follow: inside `ScreenPointer`'s rAF callback the surface receives `onPointerBox(px, py)` (ref callback); when `mode === "touchpad" && viewRef.current.s > 1` → `applyView(followPointer(view, { px, py }, box))` (count `followPans` when the view changed). Not in direct mode.
- `1×` chip: `<button data-testid="screen-zoom-reset" className={STRIP_BUTTON + " pointer-events-auto absolute bottom-3 left-3 z-10"}>1×</button>` rendered while `view.s > 1` in both modes; `onClick → applyView(IDENTITY_VIEW)`.
- Mode switch resets the view to identity (added to the existing mode effect at line 255-267) and `pointer.fingerActive(false)`.
- Renders `<ScreenPointer pointer={pointer} fitRef viewRef enabled={cursorMode === "overlay"} hidden={loupe !== null || pcPaused || switching || rippleRecent} />` after the view layer and before the loupe.

### 4.8 `pages/app/ScreenPage.tsx`
- Header (lines 262-297): line two under the PC name becomes `{COPY.encrypted}` (or the beta label) + `" · "` + `<span data-testid="screen-time-left">{leftText}</span>` (amber class on the span only when `low`); the standalone time-left `<span>` is removed. New button before Touchpad, only when `state.surfaces.length >= 2`:

```tsx
<button type="button" className={STRIP_BUTTON} data-testid="screen-next" disabled={state.switching !== null || !(state.phase === "live" || state.phase === "paused")}
  aria-label={`Next screen. Showing screen ${human(state.surfaceIndex)} of ${state.surfaces.length}. Switch to screen ${human(next)}.`}
  onClick={() => { const n = client.nextSurfaceIndex(); if (n !== null) void client.switchSurface(n); }}>
  <Icon name="layout" size={14} />
  {state.switching ? <Spinner small label="Switching" /> : <span className="tabular-nums">{human(state.surfaceIndex)}/{state.surfaces.length}</span>}
</button>
```

`human(i) = position of surface i in surfaces sorted by index, + 1`.
- Overlay: while `state.switching` the overlay (`screen-overlay`, line 303) shows a spinner and `COPY.switching(human(switching.to))`; the stage stays mounted (stream kept); `status.kind` never becomes idle during a switch. If `status.kind === "consent"` while switching (a takeover-less switch never asks; this covers the cooldown/first-consent edge) the existing consent text shows unchanged.
- Chips (`role="status"`, bottom-3 pill style, lowest priority after `notice`/`viewOnly`): `COPY.switched(n, of)` for 1.5 s when `state.switched` is fresh; `COPY.screenGone(n)` for 3 s when `switchedGone`; `COPY.pointerInPicture` once per session for 3 s when `pointerOverlayAsked && cursorMode === "drawn"` (never on 1.4 PCs: they were never asked).
- `ScreenSurface` receives the new props (`pointer={client.pointer}`, `surfaceSize={() => client.surfaceSize()}`, `cursorMode`, `pcPaused={state.pcState === "paused"}`, `switching={state.switching !== null}`).
- Explainer (line 379) becomes: "Tap what you see to click it. Pinch to zoom in; press and hold for a magnifier. Touchpad moves the pointer like a laptop touchpad and lets you drag. If your PC has two screens, the screens button at the top switches between them."
- Gesture sheet (line 385): three lists. `DIRECT_GESTURES` (directGestures.ts:364-373) rewritten: Tap → Clicks right there · Tap twice, quickly → Double-click · Tap with two fingers → Right-click · Press and hold, then slide → A magnifier to aim with; let go to click · Slide two fingers → Scrolls · Pinch → Zooms the picture on your phone only · Slide one finger while zoomed → Moves the zoomed picture. New `TOUCHPAD_SCREEN_GESTURES` in `gestures.ts` (the Touchpad page's `GESTURES` is untouched): Slide one finger → Moves the pointer, like a laptop touchpad · Tap → Clicks where the pointer is · Tap with two fingers → Right-click · Slide two fingers → Scrolls · Pinch → Zooms the picture; the view follows the pointer · Drag, then slide → Holds the mouse button while you move; End Drag lets go. New `SCREEN_COMMON_GESTURES` in `ScreenPage.tsx`: Screens button (top), only with two or more screens → Shows the PC's other screen. The pointer comes along. DoMe calls the main screen 1; Windows may number them differently. · 1× (bottom left, while zoomed) → Back to the whole screen · The pointer you see → Drawn by your phone so it moves at once; the picture catches up. On some PCs it stays in the picture instead.
- `COPY` additions (`lib/screenStatus.ts`, exact text): `switching: (n: number) => \`Switching to screen ${n}…\`` · `switched: (n: number, of: number) => \`Screen ${n} of ${of}\`` · `screenGone: (n: number) => \`Screen ${n} isn't connected any more. Showing screen 1.\`` · `pointerInPicture: "On this PC the pointer stays in the picture, so it can lag a little behind your finger."`.
- No new setting; no badge for hardware/software; "Basic picture mode" stays VP8-only.

### 4.9 Stores and runtime
- `store/devices.ts`: `pcProtocolVersions(pcId): string[]` selector (from `rest.Pc.protocol_versions ?? []`); `pcSpeaks(pcId, minor)`.
- `app/runtime.ts` `pc_status` case (line 281): `const before = live.pcs[frame.pc_id]?.connection; live.onPcStatus(frame); if (before !== undefined && before !== frame.connection) this.scheduleDevicesRefresh()` (debounced 2 s, `void useDevicesStore.getState().refresh()`), so `protocol_versions`/`agent_version` follow an agent update without a reload.
- `rest.Pc` gains `protocol_versions?: string[]` (generated).

### 4.10 Tests (`cd mobile-app && pnpm typecheck && pnpm lint && pnpm test && pnpm build`)
- `test/screen.test.ts`: start without 1.5 in `pcProtocolVersions` → params carry no `cursor`, stamp `"1.4"` (line 168 stays); with 1.5 → `cursor: "overlay"`, stamp `"1.5"`; relay `PROTOCOL_INCOMPATIBLE required "1.5"` → one restart without `cursor`, `noCursorFor` set, cleared when the list later includes 1.5; `switchSurface` sends no `bye` and no `screen.session_stop`, retires the old id, keeps `stream`/`allowance`/`surfaces`, sets `switching`, ignores the old `ended{replaced}`, closes the old peer on the new `ontrack`, ends with `surfaceIndex 1`, `switching` cleared and `switched` set on the first frame; `INVALID_PARAMETERS` on a remembered surface → one retry with `"primary"` and `screenGone`; `restartFresh` keeps the surface; `cursorMode` from result then `state`; `state{paused}` hides the pointer; `cursor_pos` with a stale gen dropped and counted; invalid `cursor_shape` dropped and the log mock never saw the payload string; no `set()` per cursor message (onChange call count).
- `test/pointer.test.ts`: hidden before the first report; `predictMoveBy` units (dx=100 on 1920 → +3414), clamp; `predictTo` snap; blend 0.35 while moving; 80 ms ease when idle; snap above 10 %; `setMode("drawn")` → never visible; `geometryChanged` hides until the next report; `reset`.
- `test/cursorShape.test.ts`: a real 1×1 PNG accepted; wrong signature, truncated, mismatched size, 65 px, hotspot outside → null and counted.
- `test/twoFinger.test.ts` (cases moved from `directGestures.test.ts`), `test/gestures.test.ts` (pinch vs scroll in the touchpad machine; a fast two-finger scroll stays a scroll through the pair guard; once decided stays decided; pinch never clicks or scrolls; two-finger tap still right-clicks; drag button survives a pinch), `test/directGestures.test.ts` unchanged and green.
- `test/screenMap.test.ts`: `fromSurface ∘ toSurface` identity; `followPointer` inside/outside/edges/identity at 1×; `snapIdentity`.
- `test/ScreenPointer.test.tsx`: each named kind renders its SVG with its hotspot offset; custom renders `<img>` with a `data:image/png;base64,` src; 24 px at `s = 1` and `s = 4`; hidden when `visible:false`, in `drawn` mode, while the loupe is open.
- `test/ScreenSurface.test.tsx`: the pointer layer is a sibling after `screen-view-layer`; the touchpad machine receives box coordinates; `pinch` in touchpad mode zooms and sends nothing; a `move` feeds `predictMoveBy` with the sent `dx, dy`; follow pans while zoomed; the `1×` chip appears while zoomed in both modes and resets.
- `test/ScreenPage.test.tsx`: the screens button only with ≥ 2 surfaces; disabled with a spinner while switching; "Switching to screen 2…" over the stage and no idle card; "Screen 2 of 2" chip; time-left on line two still found by `screen-time-left`; `pointerInPicture` once after `state{cursor: "drawn"}` in an overlay-asked session and never in a 1.4 session; the three gesture lists and the explainer text.
- `test/protocol-parity.test.ts`: `cursor_pos`, named and custom `cursor_shape`, `state.cursor`, `rest__pc` with `protocol_versions`. `test/no-pixel-access.test.ts` unchanged and green. `test/ScreenReadout.test.tsx`: the three new lines.

### 4.11 Readout (staging builds only; `screenStats.ts readoutLines`)

```
pointer: overlay|in picture|none (PC speaks 1.4) · shapes 3 custom 1 rejected 0 · pos 412 old-gen 2 · pred err max 9 px
screens: 2 · showing 2 · switches 1 failed 0 · gone 0
zoom: max 2.6x · follow pans 83
```

### 4.12 Docs
`mobile-app/DECISIONS.md` #85 "Pointer updates bypass React: a `PointerModel` on the client, a DOM overlay drawn in rAF, constants `EASE_MS`/`IDLE_MS`/`BLEND_WHILE_MOVING`/`SNAP_FRACTION`" · #86 "A switch is never an ended session: no bye, no stop, retired id, old peer closed on the new ontrack, stage kept" · #87 "`cursor` is sent only to a PC whose `protocol_versions` include 1.5; one retry without it after `PROTOCOL_INCOMPATIBLE required 1.5`" · #88 "Last surface per PC in memory; no setting" · #89 "The pointer is drawn outside the zoom layer at 24 CSS px; hidden under the loupe and before the first report" · #90 "Header: time-left on line two; the screens button only with two or more monitors; the 1× chip". `KNOWN_ISSUES.md`: "prediction drifts with Windows 'Enhance pointer precision' until the next report (tune the constants on a device)"; "whether Safari keeps the last frame under 'Switching to screen 2…' is not yet verified on a device"; "a 24 px data: URL <img> redrawn at 60 Hz under translate3d: not yet verified on a device"; "Low Power Mode frame cadence vs the 80 ms ease". `CONTRACT_ISSUES.md`: "`protocol_versions` is `[]` until the agent's first hello to a 1.5 relay".

---

## 5. tests/ (e2e: real relay, real agent, Python phone; `cd tests && uv run --frozen pytest -q`)

### 5.1 `tests/screen_phone.py`
- `ScreenClient.start(*, takeover=False, sign_offer=None, apply_answer=True, surface: str | int = "primary", cursor: str | None = None)`: `params["surface"] = surface`; `cursor` key present only when not None. `Viewer` unchanged (the 1.5 library makes `cursor_pos`/`cursor_shape` parse); add `Viewer.dc_raw_lengths: list[int]`.

### 5.2 `tests/agent_1_4.py` (new)
A copy of `older_agent.py` with `PROTOCOL_VERSIONS = ("1.0", "1.1", "1.2", "1.3", "1.4")` and the same `install()` (registry `protocol_version`/`registry_version` = `"1.4"`, `dome_agent.SUPPORTED_PROTOCOL_VERSIONS`, `frames.SUPPORTED_PROTOCOL_VERSIONS`), run on the fake platform exactly as `screen_agent.py` (it imports `screen_agent`'s seams: fake UI, control ops). `older_agent.py` stays 1.1.

### 5.3 `tests/screen_agent.py` control ops (added to `_control_ops`)
- `test_set_monitors(monitors=[{index,left,top,width,height,primary}, …])`: sets `state.monitors`, calls `agent.screen.on_display_change()` (or the existing re-check hook), returns the list.
- `test_cursor_script(samples=[[flags, tag, x, y], …], shapes={tag: "arrow" | {"custom": {"w","h","hx","hy"}}})`: sets the fake cursor script the worker's `FakeCursorReader` reads from the `source` spec on the next start (a custom entry makes the test agent generate a `w×h` RGBA PNG with PIL).
- `test_input` unchanged (returns `cursor` and `injected`).

### 5.4 `tests/test_e2e_screen.py` additions
- `test_next_screen_replaces_the_session_and_keeps_the_pointer_on_the_watched_monitor`: `test_set_monitors([primary 1920×1080 at 0,0; 1280×1024 at 1920,0])`; `stream(phone)` → A on surface 0; `phone.client.start(surface=1)` (no bye, no stop) → B: `result.surface_index == 1`, `gen == 1`, `consent == A.consent`; `ended_frame(A) reason == "replaced"`; `test_ui` shows no new consent window and the bar request count did not drop to zero between A and B; B's geometry `surface_index == 1`; input: `pointer_move_to{x: 65535, y: 32768, gen: 1}` lands on monitor 1; from x=3199 a `pointer_move{dx: +400}` → `test_input.cursor[0] == 3199` and the last injected is `["move_to", "3199", …]`; a `move, click, move` batch crossing the edge: the click's recorded position is inside monitor 1; `assert_nothing_leaked`.
- `test_cursor_overlay_messages_reach_the_phone_and_validate`: `test_cursor_script(...)` with positions `(960,540) → (1500,200) → off-monitor`, shapes `arrow → ibeam → custom 24×24 hotspot (3,2)`; `start(cursor="overlay")` → `result.cursor == "overlay"`, first `state.cursor == "overlay"`, `cursor_shape` precedes `cursor_pos`, `cursor_pos.gen == 1`, `x == round(960·65535/1919)`, the off-monitor sample has `visible == False` with x, y unchanged; shape sequence `["arrow", "ibeam", "custom"]`, the custom one `width == height == 24`, `hotspot_x == 3`, `hotspot_y == 2`, `len(png) <= 8192`, raw length `> 1024 and <= 12288`; ≤ 60 `cursor_pos` in any 1 s window; `assert_nothing_leaked` extended with the first 24 characters of the PNG base64 as a secret (log, journal, bundle, DB); the bundle's `media.cursor` has counts only.
- `test_a_1_5_agent_answers_a_1_4_phone_in_1_4`: `start()` without `cursor` → `stamp(start) == "1.4"`, `answer.protocol_version == "1.4"`, no `cursor` key in the result, after 20 frames and one geometry no DC message outside `{geometry, state, allowance, restricted}` and no `cursor` key in `state`; the answer also verifies through `verify_screen_answer_payload(registry=<Registry copy with protocol_version "1.4">)`.

### 5.5 `tests/test_e2e_versions.py` additions (fixture `agent14` starts `agent_1_4.py`)
- `test_a_1_4_agent_with_a_1_5_phone_starts_screen_view_without_the_cursor_param`: `GET /v1/pcs` → `protocol_versions == ["1.0",…,"1.4"]`; `start()` → stamp `"1.4"`, succeeded, answer stamped `"1.4"`, no cursor messages; then `start(surface=1)` with two fake monitors → `surface_index == 1`, `gen == 1`, old `ended{replaced}`.
- `test_a_1_5_stamped_start_to_a_1_4_agent_is_refused_before_forwarding`: `start(cursor="overlay")` → stamp `"1.5"`, `result{origin: relay, failed, PROTOCOL_INCOMPATIBLE, detail {peer: "agent", required: "1.5"}}`; `test_ui` shows no new consent window or bar request; the ledger shows no reservation; then `start()` without `cursor` succeeds.
- The existing three tests keep `older_agent.py`.

---

## 6. Docs (beyond §1.10, §1.11, §2.5, §3.12, §4.12)
- `docs/design/screen-view.md`: §8.3 table gains the Touchpad pinch row and follow-the-pointer; §8.2 gains confinement; new §8.5 "The pointer drawn by the phone" (modes, messages, prediction/reconciliation, the content rule); §16 copy table gains `switching`, `switched`, `screenGone`, `pointerInPicture`; §19 Phase 2 rows WP2.2/WP2.3 → "built 2026-11; unit-tested; integration-tested (Linux, fakes); not yet verified on a device", plus a Phase 2b row "rebuild onto the primary when the watched monitor vanishes"; §5.3 hardware paragraph updated.
- `docs/WINDOWS_INSTALL.md` §4.13: paragraph "Round 2a additions (not yet verified on a device)": two screens (the screens button; switching starts a new Screen view on the other monitor; the PC asks again only if it would have asked anyway), the pointer (drawn by the phone; on a Windows 10 that cannot leave it out the picture keeps it: the log says `screen cursor mode mode=drawn`), video from the graphics chip (`screen encoder opened … hw=True`; the bundle lists why any encoder was skipped). "While a phone is watching" adds: "The phone can move the pointer only on the screen it is watching; your own mouse is never limited."
- `docs/ACCEPTANCE.md` Scenario 26: evidence rows for contract 1.5, the relay stamp gate and REST field, PC switch/confinement/cursor poller/encoder probe, phone switch UX/pointer/touchpad zoom, the e2e cases, each "unit-tested" / "integration-tested (Linux, fakes)"; a device-row template "Round 2a, owner-reported (date; build; GPU; iOS): Next screen —; pointer —; Touchpad zoom —; encoder —" left "not yet verified on a device"; the outstanding list gains §7's items.
- `docs/PROGRESS.md` entry; `docs/HANDOFF.md`: where the four additions live, how to read the readout lines and the bundle fields, the §7 checklist.

---

## 7. Device test checklist (what the founder tries, what they report) and Deferred (needs the device)

Prerequisites: staging with Screen view on; a build whose BUILD-INFO says "screen view: included"; the staging readout visible.

1. **Pointer.** Start Screen view: a white pointer with a black edge appears. Touchpad on, slide: it moves with the finger at once, the picture following. Hover a text field: I-beam; a link: hand. Report the readout `pointer:` line and whether the pointer ever sat where the picture did not.
2. **Pointer in direct mode.** Tap a button: ripple, the pointer appears at the tap, the click lands. Press and hold: the magnifier is never covered by the pointer.
3. **Next screen.** With two monitors the button shows `1/2`. Tap: "Switching to screen 2…", then the other picture and "Screen 2 of 2". Slide in Touchpad mode: the pointer moves on screen 2 only (look at the PC). Report `screens:` and the seconds the switch took, and whether the old picture stayed under the overlay or went black.
4. **Zoom in Touchpad mode.** Pinch; slide one finger: the view follows the pointer. Tap `1×`. Report `zoom:`.
5. **Encoder.** After the session, download the PC bundle (tray → Diagnostics). We read `media.encoders` (`screen encoder probed` lines, `mft`, `frames_in`) and `screen encoder opened … hw=`. Say if the picture is worse or later than last round.
6. **Windows 10 fallback** (if such a PC exists): whether the readout says `in picture`.
7. **Report**: the readout text (Copy), the bundle name, iPhone model + iOS version, Windows build. No screenshots of the PC's screen.

**Deferred (needs the device) — what the test must answer:**
- Does Safari keep the last decoded frame under "Switching to screen 2…" while the old peer is alive and after it closes on the new `ontrack`? If black: the dark overlay is the fallback (a captured poster would violate no-pixel-access); nothing else changes.
- Prediction feel with "Enhance pointer precision": lag or overshoot at speed? Tune `BLEND_WHILE_MOVING`, `IDLE_MS`, `EASE_MS`; the readout's `pred err max` says whether a per-PC gain estimate is ever needed.
- Is a constant 24 CSS px pointer right at 4× zoom in Touchpad mode (finger elsewhere)? Candidate change: scale by `1/√s`.
- Which MFT Media Foundation picks on the hybrid laptop (`mft`), its `frames_in`, whether it emits in-band SPS/PPS with `global_header`, or only extradata, or neither (then: a second hardware candidate variant without `global_header`).
- Rotated monitors (90/270): does `cursor_pos` normalised against the desktop rectangle match the picture's orientation? (Same uncertainty exists for `pointer_move_to` today.)
- Does the countdown show a visible wobble when the new result's allowance figure replaces the carried one across a switch?
- Does the "PC comes online, Show screen tapped before `/v1/pcs` refreshes" race happen in practice? If so: `pc_status.protocol_versions` with per-socket stripping (the `legacy_state_frame` pattern) becomes a 1.6 item.
- Windows pointer schemes (large/inverted/custom .cur) are drawn with the phone's glyphs when their handle is a system handle, else as custom bitmaps: acceptable?
- Custom-cursor flicker in text editors (two custom shapes several times a second) under the 4/s limit: if visible, a per-session shape id cache is the 1.6 answer.
- Low Power Mode frame cadence vs the 80 ms ease; a 24 px data: URL `<img>` under `translate3d` at 60 Hz.

---

## 8. Build order and what each builder must NOT touch

**Step 1 — Contract (one builder, lands first; everything else waits for it):** §1 in full: `version.json`, `actions.json`, the three schemas, rule text, `errors.json` description, both libraries (`param_since`, `min_version(params)`, `command_protocol_version`, the post-schema since check, the answer stamp argument, the parser cap), fixture generators + regenerated `es256-*.json` + `screen-dc-cases.json`, `pnpm gen:types` and `pnpm gen:validators` outputs committed, `PROTOCOL.md` §21, `shared/protocol/README.md`. Touches only `shared/**`, `mobile-app/src/protocol/generated/**`, `docs/PROTOCOL.md`. Must NOT touch `cloud-api/`, `pc-agent/`, `mobile-app/src` outside `protocol/generated`, `tests/`.

**Step 2 — in parallel after step 1:**
- **cloud-api builder** (§2): touches `cloud-api/**` only (model, migration 0006, `agent_ws.py`, `routes/pcs.py`, `router.py`, tests, DECISIONS/KNOWN_ISSUES). Must NOT touch `shared/`, `pc-agent/`, `mobile-app/`, `tests/`.
- **pc-agent builder** (§3): touches `pc-agent/**` only (including `dome_agent/testing/*` doubles, `__init__.py`, DECISIONS/KNOWN_ISSUES/CONTRACT_ISSUES). Must NOT touch `shared/`, `cloud-api/`, `mobile-app/`, `tests/`.
- **mobile-app builder** (§4): touches `mobile-app/src/**` (except `protocol/generated`), `mobile-app/test/**`, mobile-app docs. Must NOT touch `shared/`, `cloud-api/`, `pc-agent/`, `tests/`, `mobile-app/src/protocol/generated/**`.

**Step 3 — after steps 2 land:** **e2e + docs builder** (§5, §6): touches `tests/**` (`screen_phone.py`, `screen_agent.py`, `agent_1_4.py`, `test_e2e_screen.py`, `test_e2e_versions.py`), `docs/design/screen-view.md`, `docs/WINDOWS_INSTALL.md`, `docs/ACCEPTANCE.md`, `docs/PROGRESS.md`, `docs/HANDOFF.md`. Must NOT touch component source; a failing e2e is reported back to the owning component builder with the test name.

Interface contract the parallel builders rely on without talking (all fixed by this document): the `start` pipe op keys `cursor`/`hw_encoding` and the worker events `cursor`/`encoders`/stats counters (pc-agent internal); the control ops `test_set_monitors`/`test_cursor_script` (pc-agent provides on the fake platform through `tests/screen_agent.py`'s seam: the pc-agent builder exposes `FakeState.monitors` + `on_display_change()` and the `cursor_script` source-spec key; the e2e builder writes the ops); the REST field name `protocol_versions`; `ScreenClient.start(surface, cursor)` in the Python phone; the result key `cursor` present only with the param; the answer stamp = the start's stamp; the DC vocabulary of §1.4; the error detail `{peer: "agent", required: "1.5"}`.

Check commands (every builder, green before handing over): `cd shared/python && uv run --frozen pytest -q && uv run ruff check . && uv run mypy`; `cd shared/ts && pnpm test && pnpm typecheck && pnpm lint`; `cd cloud-api && uv run --frozen pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy dome_api tests`; `cd pc-agent && uv run --frozen pytest -q tests && uv run --frozen ruff check . && uv run --frozen ruff format --check . && uv run --frozen mypy` (both configs); `cd mobile-app && pnpm typecheck && pnpm lint && pnpm test && pnpm build`; `cd tests && uv run --frozen pytest -q` (PostgreSQL at `/tmp:54329`). Nobody commits or pushes; the orchestrator does.


---

# Part 3. The builders' brief (2026-11-02)

# Screen view round 2a — shared brief for every agent (read fully before touching anything)

Repository: /home/user/DoMe (branch claude/stoic-heisenberg-txq6ja, which is also the default/deploy branch). Monorepo:
shared/protocol (the contract: JSON schemas, actions.json, version.json, fixtures), shared/python (dome_protocol), shared/ts
(@dome/protocol), cloud-api (FastAPI relay + REST), pc-agent (Windows agent + Screen view worker), mobile-app (React/TS PWA),
tests/ (cross-component e2e with a real relay, fake PC agents and a Python "phone"), docs/.

## The founder's four additions (agreed 2026-10-30), in build order
1. "Next screen" button: switch the picture between the PC's monitors; keep the pointer on the monitor being watched.
2. Pointer drawn by the phone (cursor overlay): the pointer moves the instant the finger does; the PC stops drawing the
   cursor into the video and reports the real cursor position + shape over the data channel.
3. Zoom in Touchpad mode (pinch works there too) and the zoomed view follows the pointer.
4. Hardware H.264 encoding on the graphics chip (Media Foundation hardware transform, NV12 input), with the existing
   software/VP8 fallbacks intact.
DROPPED BY FOUNDER DECISION (security): input over the data channel. Every touch/click/key stays on the relay path
(signed input_batch frames). Do not design or build any input path over the data channel.

## Hard rules (project-wide, from CLAUDE.md/DECISIONS; violations are blocking)
- The phone signs every command; AI output is untrusted; no secrets in chat/logs; search text never logged.
- rules.screen_content: no component writes a frame to disk, logs it, includes it in diagnostics or sends it anywhere but
  the phone. A cursor bitmap is screen content: never logged, never in a diagnostics bundle (sizes/kinds/counts only).
- Verifiers reject unknown fields; schemas use additionalProperties:false; every DC message is validated against the
  receiving side's vocabulary (parse_screen_dc_message / parseScreenDcMessage) and dropped otherwise.
- No Jarvis/Alfred names. No model identifiers in code, commits or docs. Commit trailers are added by the orchestrator;
  agents never commit or push.
- Honest status language in docs: "unit-tested", "integration-tested", "not yet verified on a device".
- Wording for the founder (non-technical) in user-facing copy: plain words, no jargon.

## Contract facts (shared/protocol)
- version.json: protocol_version "1.4"; compatibility_rule: peers list MAJOR.MINOR versions; accept if MAJOR equal and
  MINOR <= highest supported; unknown fields rejected. README rule 1: changing ANY schema requires bumping version.json and
  updating docs/PROTOCOL.md (REST-only bodies between PWA and cloud-api are exempt).
- actions.json registry_version "1.4". `screen.session_start` (since 1.4): params {offer_sdp, surface ("primary" | int 0..7,
  default "primary" — ALREADY ALLOWS AN INDEX), audio, takeover}; result `screen_session_result` {screen_session_id,
  surfaces[1..8] of screen_surface{index,width,height,primary}, surface_index, gen, audio, lease_seconds, allowance,
  consent, encoder?}. rules.command_version_stamping: a controller stamps a command with its action's `since`.
- relay-frames.schema.json $defs: screen_dc_phone_to_pc = hb | keyframe | bye | audio{on}; screen_dc_pc_to_phone =
  geometry{gen,surface_index,width,height,rotation,reserved[]} | state{state, reason?, encoder?, data_saver?} |
  allowance{...} | restricted{restricted, reason?}. Its description already says: "Phase 2/3 add cursor_shape, cursor_pos
  and roi." limits.screen_dc_message_max_bytes = 1024 (one limit for both directions; a PNG cursor will not fit — raise it
  or add a dedicated cap; decide and justify).
- pc_status_event (relay→controller): {type, pc_id, connection, last_seen}. REST $defs/pc: {id, name, enabled, connection,
  last_seen, created_at, platform, ...}. Nothing today tells the phone which protocol MINORs a PC's agent speaks. The
  relay knows: agent_ws.py sets conn.protocol_versions, conn.supports_screen = protocol_compatible("1.4", versions).
- Fixtures es256-python.json / es256-typescript.json contain a screen answer "stamped 1.5" as a REJECTED case (a version
  above the current). If protocol becomes 1.5 that case must become 1.6 and be RE-SIGNED with the committed test PC key —
  find the fixture generator (shared/python or shared/ts scripts/tests) and regenerate; never hand-edit a signed payload.
- 32 files mention "1.4" (listed by `grep -rln '"1.4"' --include=*.py --include=*.ts --include=*.json . | grep -v node_modules | grep -v .venv`).
  Most assert 1.4-specific behaviour that stays true (screen actions are `since` 1.4; the relay shields pre-1.4 agents).
  Only the CURRENT version constants/announcements change: shared PROTOCOL_VERSION/REGISTRY_VERSION (derived from the
  JSON?), pc-agent dome_agent/__init__.py SUPPORTED_PROTOCOL_VERSIONS ("1.0".."1.4") += "1.5", cloud-api `ours`,
  tests/older_agent.py stays 1.1, e2e version tests gain a "1.4 agent with a 1.5 phone" case.
- shared/ts generates types: `pnpm gen:types` (shared/ts/scripts/gen-types.ts); mobile-app generates validators:
  `pnpm gen:validators` (mobile-app/scripts/gen-validators.ts, no-eval precompiled Ajv). Both must be re-run after schema
  changes and the generated files committed.
- docs/PROTOCOL.md has one section per MINOR (§17 1.1, §18 1.2, §20 1.4 with sub-sections What it adds / Limits / rules /
  Result shapes / Errors / Compatibility and deploy order / Examples / Evidence). 1.5 gets §21 in the same shape.
  shared/protocol/README.md has a "## Protocol 1.x" entry per MINOR (table of changes + library helpers).

## pc-agent facts
- dome_agent/screen/session.py: ScreenSessionManager.start(... surface: str|int ...) → _start_session: consent, monitors
  = await self._monitors(), monitor = self._pick_surface(monitors, surface) ("primary" → the primary; int → by index else
  INVALID_PARAMETERS "This PC has no monitor with that surface index."), capture pre-flight, an existing session of the
  same controller ends `replaced` (another controller: `takeover` only with takeover=true), ScreenSession(monitor,
  surfaces=tuple(monitors), gens=[(1, monitor)]). The result reports surfaces + surface_index + gen.
- dome_agent/platform/protocol.py Monitor{index,left,top,width,height,primary,rotation} in PHYSICAL pixels; index 0 =
  primary then by position. InputAdapter: move(dx,dy) [relative SendInput], move_to(x,y) [SetCursorPos under per-monitor
  DPI v2, verified with GetCursorPos], cursor_position() → (x,y)|None, plus buttons/keys.
- dome_agent/input_session.py: dispatch loop ~line 849: `pointer_move` → adapter.move(dx,dy); `pointer_move_to` →
  self._screen_target(controller_id, gen) → (monitor, code) then map_to_pixels(monitor, x, y) → adapter.move_to. There is
  no "current streamed monitor for this controller" accessor for relative moves yet.
- dome_agent/screen/worker/main.py: pipe ops tick/start/stop/pause/resume/keyframe/allowance/restricted/reserved/rebuild;
  self._geometry {gen, surface_index, width, height, rotation, reserved}; _send_dc(message) validates against the
  pc_to_phone vocabulary before sending; emits {"ev": ...} to the agent over the pipe.
- dome_agent/screen/worker/rtc.py: data channel `dome-screen-v1`; inbound parse_screen_dc_message(text,"phone_to_pc");
  hb/bye handled; sends through channel.send(text) when readyState == "open".
- dome_agent/screen/worker/capture_dda.py: ddagrab_graph(output_idx, width, height, fps) =
  "ddagrab=output_idx=N:framerate=F:draw_mouse=1:dup_frames=1,scale_d3d11=width=W:height=H:format=nv12,hwdownload,format=nv12"
  → NV12 CPU frames. DdaWithFallback(DDA proven by first frame within 1.5 s, else WGC). classify_capture_failure, pre-flight.
- dome_agent/screen/worker/capture_wgc.py: windows-capture 2.0.1 NativeWindowsCapture(on_frame_arrived, on_closed,
  cursor_capture, draw_border, secondary_window, minimum_update_interval, dirty_region, monitor_index, window_name,
  window_hwnd); DoMe passes None for everything but monitor_index because the package REFUSES an explicit cursor/border
  setting on a Windows 10 whose GraphicsCaptureSession lacks the property (CursorConfigUnsupported). So "cursor off" on WGC
  = pass cursor_capture=False, and if the package raises CursorConfigUnsupported (or similar), retry with None and report
  that the cursor is still drawn. Frames are BGRA; WgcSource.read reformats to yuv420p at the track size (line ~299).
- dome_agent/screen/worker/encode.py: candidates h264_mf hw_encoding=1 (hardware=True), h264_mf hw_encoding=0, VP8;
  H264Encoder.open sets ctx.pix_fmt = "yuv420p" (line ~351), probe_encoders builds a yuv420p VideoFrame (line ~531).
  KNOWN_ISSUES #35: mfenc's input scoring refuses any input type that is not exactly avctx->pix_fmt while GPU transforms
  offer NV12 → the hardware candidate never opens today. DECISIONS #98/#99/#100: low_delay flag, SPS/PPS prepended on
  every IDR from whatever the encoder used, the drain pump (held pictures out in < 0.5 s), encoder_stalled → VP8-first.
  Enabling hardware means: NV12 input for the h264_mf candidates (both: the software MFT also takes NV12), sources that
  deliver NV12 (DDA already does; WGC: reformat bgra→nv12; fakes), a probe at the real size that PROVES a packet comes
  out (feed up to N frames through the drain pump) and that SPS+PPS exist (in-band or extradata), else the candidate is
  "unavailable" with FFmpeg's reason and the chain falls through. Hybrid laptop (founder): Intel UHD owns both outputs,
  RTX 4060 has none; MF enumerates hardware MFTs by merit — either is fine for system-memory NV12 input.
- dome_agent/screen/worker/track.py ScreenTrack: source.open() after DTLS, source.read() frames, encoder, keyframes,
  drain pump; logs "screen encoder opened", "screen media" every 5 s; counters in the diagnostics bundle (content-free).
- Diagnostics: dome_agent/diagnostics.py bundle (status.screen.session.media, hardware section). Redaction masks
  40-char runs; the licence gate (packaging/licence_gate.py) must still pass: no new DLLs; PIL is already bundled
  (pystray/PIL); windows-capture .pyd only.
- Tests: pc-agent/tests (1246 passed as of 2026-10-30): test_screen_session.py, test_screen_worker.py (aiortc loopback),
  test_screen_media_path.py, test_screen_capture_fallback.py, test_input_session*.py, test_diagnostics*.py, test_licence_gate*.py.
  Run: `cd pc-agent && uv run --frozen pytest -q tests`, `uv run --frozen ruff check . && uv run --frozen ruff format --check .`,
  `uv run --frozen mypy` (config in pyproject). Windows-only code paths are tested through fakes (dome_agent/platform/fake.py,
  screen/worker/fake.py); nothing here runs on Windows — the GitHub windows-installer.yml run and the founder's device do.

## mobile-app facts
- src/lib/screen.ts ScreenSessionClient: start(pcId, {takeover?, retries?}) builds the offer and sends
  screen.session_start with params {offer_sdp, surface: "primary", audio: false, takeover}; onSessionResult seeds
  GeometryTracker (gen, surface_index, width, height) and stores surfaces; onChannelMessage switch handles geometry /
  state / allowance / restricted (parseScreenDcMessage(data,"pc_to_phone")); sendChannel({hb|keyframe|bye|audio});
  NO_RESUME end reasons include "replaced" and "takeover"; restartFresh(cause) for stall/transport/no_picture;
  retiredSessionIds drops late frames of sessions this phone left; first-frame watchdog; stats (screenStats.ts) and a
  staging-only readout (ScreenReadout.tsx). Protocol imports from "@dome/protocol".
- src/components/ScreenSurface.tsx: layers box → zoom/pan layer (transform-origin 0 0, View {s,tx,ty}, zoomAt/panBy from
  lib/screenMap.ts) holding <video playsinline muted> → touch layer. mode "direct" feeds DirectGestureMachine
  (lib/directGestures.ts: tap, loupe, two-finger scroll vs pinch by spread ratio > 10 % before midpoint moves 8 px,
  one-finger pan when zoomed); mode "touchpad" feeds the relative GestureMachine (lib/gestures.ts: move/click/scroll/drag,
  sensitivity, two fingers = scroll or right click; NO pinch) and sends relative pointer_move. Pinch zoom exists only in
  direct mode today. onProbe reports video element facts for the readout.
- src/pages/app/ScreenPage.tsx: full-screen stage; header strip: PC name/label, time left, [Touchpad toggle],
  [Keyboard], [Stop]; badges row (relay/data saver/vp8); touchpad Drag toggle; explainer "Pinch to zoom on the phone;
  press and hold for a magnifier; switch on Touchpad for drags." (must change when zoom works in Touchpad mode);
  prefs.touchpadMode persisted.
- src/lib/screenMap.ts: GeometryTracker (seed/apply, surface_index), View math, mapping to 0..65535 with reserved rects
  dropped. src/lib/screenInput.ts: direct events → signed input events via lib/input.ts (coalesceMotion: relative moves
  summed, pointer_move_to last-wins; FLUSH cap 30/40 per second; back-pressure).
- Protocol parity test: test/protocol-parity.test.ts; tests 1504 passed as of 2026-10-30. Run: `cd mobile-app && pnpm
  typecheck && pnpm lint && pnpm test && pnpm build` (build runs check-release + bundle checks).
- Safari/iOS: the video track arrives inside setRemoteDescription (handled); Low Power Mode blocks autoplay (handled);
  requestVideoFrameCallback for frame timing; no WebGL/canvas pixel reads of the video (test/no-pixel-access.test.ts
  forbids reading pixels). The cursor overlay must be DOM (SVG/img), not drawn into a canvas over video frames.

## cloud-api facts
- relay/agent_ws.py, controller_ws.py: hello → protocol_versions; supports_input (1.1), supports_screen (1.4);
  router.py SCREEN_PROTOCOL_VERSION = "1.4" gates screen.* to agents that announced 1.4 (stays 1.4: screen actions are
  since 1.4). `ours` = the relay's supported versions (from the shared registry). The relay never sees DC messages.
- REST /v1/pcs (routes/pcs.py) returns $defs/pc. Any capability signal for the phone ("does this PC's agent speak 1.5?")
  must come from here or from a relay frame; the relay knows agent conn.protocol_versions while the PC is online.
- Tests: cloud-api/tests against a real PostgreSQL (started with: su postgres -c "/usr/lib/postgresql/16/bin/pg_ctl -D
  /var/lib/postgresql/dome-pgdata -o '-k /tmp -p 54329' -l /tmp/pg.log start"; the tests read DOME_TEST_DATABASE_URL or
  conftest defaults — check cloud-api/tests/conftest.py), `uv run --frozen pytest -q`, `uv run ruff check . && uv run
  ruff format --check . && uv run mypy dome_api tests`.

## tests/ (e2e) facts
- tests/screen_agent.py (the real pc-agent with a fake UI and fake sources, Linux), tests/screen_phone.py (a Python
  phone: Viewer parses DC messages with parse_screen_dc_message(message, "pc_to_phone") — RAISES on anything else, so new
  PC→phone messages need the 1.5 vocabulary there), tests/test_e2e_screen.py, tests/test_e2e_versions.py (older_agent.py
  announces 1.0/1.1). Run from tests/: `uv run --frozen pytest -q` (needs PostgreSQL as above; 17 passed last round).

## Design constraints to honour
- No renegotiation, ever (rules.screen_signaling (8)): "Next screen" = a fresh signed screen.session_start with
  surface: N from the same phone; the PC ends the old session `replaced`. The phone must treat its own deliberate switch
  as a switch (keep the stage, show a short "Switching to screen 2…" over the last picture), not as an ended session.
- Keeping the pointer on the watched monitor: in direct mode pointer_move_to already maps onto the streamed monitor. In
  Touchpad mode relative moves can wander onto another monitor: while a screen session owned by the batch's controller is
  connected, after dispatching a batch's moves the agent reads cursor_position() and, if outside the streamed monitor's
  rectangle, move_to() the clamped point (physical pixels). Only our injected moves are clamped; the person at the PC is
  never constrained (no ClipCursor).
- Cursor overlay: the PC captures WITHOUT the cursor (ddagrab draw_mouse=0; WGC cursor_capture=False with the
  CursorConfigUnsupported fallback) ONLY when the phone asked for overlay mode in screen.session_start and the result
  tells the phone which mode is in effect ("drawn" when the PC could not disable it, so the phone never double-draws).
  The worker polls the cursor (GetCursorInfo: flags CURSOR_SHOWING, hCursor, screen position) at ~60 Hz and sends
  cursor_pos{gen, x, y (0..65535 over the streamed surface), visible} when changed (coalesced, ≤ 60/s) and
  cursor_shape when the hCursor changes: named kinds for the system cursors (compare against LoadCursor(IDC_ARROW,
  IDC_IBEAM, IDC_HAND, IDC_WAIT, IDC_APPSTARTING, IDC_CROSS, IDC_NO, IDC_SIZEALL, IDC_SIZENS, IDC_SIZEWE, IDC_SIZENESW,
  IDC_SIZENWSE, IDC_UPARROW, IDC_HELP)) and "custom" with a PNG (GetIconInfo → DrawIconEx into a 32-bpp DIB → PIL PNG,
  capped at 64×64 and ~8 KiB base64) plus hotspot. The phone draws named kinds as its own crisp SVG and custom shapes as
  an <img> from a data: URL after validating size/shape; never logs or stores the bitmap. The phone shows the pointer at
  a locally predicted position while the finger moves (direct mode: the tap/loupe point; touchpad mode: last reported
  position + sent deltas × the PC-pixels-per-phone-pixel ratio, clamped to the surface) and at the PC-reported position
  otherwise (ease over ~80 ms; never snap-jitter).
- Zoom in Touchpad mode: pinch vs two-finger scroll disambiguation (reuse DirectGestureMachine's rule) in the touchpad
  path; one-finger drag when zoomed remains a relative move (touchpad semantics), so panning when zoomed follows the
  pointer automatically: keep the pointer (predicted/reported) inside an inner margin of the viewport by panning the View.
  Two-finger drag = scroll (unchanged). The explainer/gesture copy changes accordingly.
- Hardware encoder: hw first with NV12, then software (NV12), then VP8; the probe must prove a packet + parameter sets
  within a bounded number of frames; encoder facts logged as today (never a parameter set); KNOWN_ISSUES #35 rewritten
  honestly ("enabled, not yet verified on a device").
- Capability gating: the phone sends `cursor: "overlay"` only when it knows the PC speaks 1.5; "Next screen" needs no
  gating (surface: N is 1.4). Decide the signal (REST pc field from the relay's knowledge, or a relay frame); 1.4 phones
  must never receive a frame they would reject.
- Free allowance: a surface switch is a new session; the relay's metering (rules.screen_metering) continues per PC per
  window; the phone's countdown must not reset visibly in a confusing way (carry the allowance state across the switch).

## What "done" means for this round
- Contract 1.5 complete and consistent across shared/python, shared/ts, generated types/validators, fixtures, PROTOCOL.md §21,
  shared/protocol/README.md.
- pc-agent, mobile-app, cloud-api, tests/ build and pass all their checks (commands above). No Windows-only code path is
  left untested through its fake; every new behaviour has unit tests; e2e covers: surface switch (old session ends
  `replaced`, new result has the new surface_index), cursor messages reach the phone and validate, a 1.4 agent with a 1.5
  phone still starts Screen view (no `cursor` param sent) and the phone's Next screen works against it.
- Docs updated: PROTOCOL.md §21, design/screen-view.md (§8 touchpad zoom, §19 Phase 2 delivery status), pc-agent
  DECISIONS/KNOWN_ISSUES/CONTRACT_ISSUES, mobile-app DECISIONS/KNOWN_ISSUES, cloud-api DECISIONS, docs/ACCEPTANCE.md
  Scenario 26 rows ("not yet verified on a device"), docs/PROGRESS.md entry, docs/HANDOFF.md.

## Addendum (orchestrator, after the brief was written)
- Fixture generators: shared/python/scripts/make_fixtures.py (run from shared/python: `uv run python scripts/make_fixtures.py`)
  and shared/ts/scripts/make-fixtures.ts (shared/ts). Both hard-code the "stamped with a MINOR this library does not
  speak" case as protocol_version "1.5" (python line ~164, ts line ~152): change both to "1.6" and regenerate both

