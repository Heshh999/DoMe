# pc-agent known issues (recorded, not fixed)

1. **`AbortSystemShutdownW` window is effectively nil on real Windows.** `WindowsPower` issues
   `InitiateSystemShutdownExW` with `dwTimeout = 0` (the cancellable countdown is the agent's own,
   and `bForceAppsClosed = FALSE` lets Windows prompt about unsaved work). Windows only honours
   `AbortSystemShutdownW` during its own `dwTimeout` grace period, so after the agent's countdown has
   fired a `power.cancel` almost always gets `canceled: false` — which is the honest answer. Giving
   Windows a non-zero grace period would add a second, OS-drawn countdown after DoMe's; that is a
   product decision for the maintainer, not a bug fix. The abort path is implemented and tested with
   the fake adapter so it works unchanged if `dwTimeout` is ever raised.
2. **A capability narrowing does not interrupt a command that is already executing** (only a
   revocation does). The OS/browser call may be under way; interrupting it would produce
   `outcome_unknown` for an action the account merely narrowed. Queued, awaiting-confirmation and
   armed-countdown commands are canceled (DECISIONS.md #16).
3. **Pending local revocations are re-sent only after a `grants_snapshot`** (i.e. on reconnect), not
   on a timer while connected. The initial send happens immediately; the only way it is dropped is a
   socket that is down, and the next snapshot is the first moment the relay is known to be listening.
4. **`EdDSA` deprecation warning from joserfc** (see CONTRACT_ISSUES.md #5) is silenced during
   verification only; the contract value is kept.
5. **The Windows input adapter is not device-tested.** `SendInput`, `MapVirtualKeyW` scan codes,
   `OpenInputDesktop`/`GetUserObjectInformationW` secure-desktop detection, token integrity levels and
   the CTRL-release-on-failure path have only been exercised through their pure builders on Linux.
   The README checklist lists the manual steps.
6. **Relative motion is subject to Windows pointer acceleration** ("Enhance pointer precision"), like a
   physical mouse. The phone's sensitivity setting cannot compensate exactly; the ratio must be
   measured on a device.
7. **`foreground_app.elevated` is usually absent exactly when it matters**: `OpenProcess` on an elevated
   process typically fails for a medium-integrity agent, which is reported as unknown (None), never as
   a guess. Refusals then surface as `INPUT_RESTRICTED` from the `SendInput` error mapping (access
   denied + non-default input desktop) or as a plain `INPUT_INJECTION_FAILED`.
8. **Lock / secure-desktop detection is polled every second** (`lock_poll_seconds`), so up to one
   second of batches can reach `SendInput` after a lock; Windows refuses them and the session then ends
   with the held input released.
9. **The tray's *Paired phones* submenu is rebuilt on status updates**, so a phone paired a moment ago
   appears after the next `update_status` (every connection/state change), not instantly.
10. **Other-session conflicts are detected only through the pid file's recorded session id** on
    Windows; a pid file deleted by hand (or two sessions starting within the same instant, before either
    wrote it) leaves nothing to detect, and then a second agent of the same account could start in
    another session and supersede the first at the relay (DECISIONS.md #44). A global (`Global\`)
    per-account mutex would close that gap but needs the account SID in the name and a device test; not
    done. Not device-tested.
11. **The recovery file is written synchronously from the dispatch thread** on every hold change
    (button down/up, shortcut begin/end): a tiny atomic replace without fsync; a power loss in that
    window could lose the last hold change.
12. **`input_session{ended}` after a relay disconnect cannot be delivered** (the socket is gone); the
    phone relies on the relay's disconnect handling and on `INPUT_SESSION_EXPIRED` for the old id.
13. **Batch-rejection reasons do not reach the phone through the current relay** (CONTRACT_ISSUES.md
    #11): the agent emits them as `error` frames, which cloud-api logs and drops. The phone sees
    `dropped_events`, `pc_state.input_restricted` / `foreground_app` and `input_session` frames only.
    Needs a contract field (`error_frame.ref_input_session_id`) and relay routing; not fixable inside
    pc-agent.
    **Resolved by the maintainer (protocol 1.1 amendment, 2026-10-09):** rejection reasons now reach the phone (see CONTRACT_ISSUES #11); the phone ignores rejections naming a session it has left.

14. **Clock-offset estimate, residual cases** (DECISIONS.md #39): the first batch of a session is judged
    against the `input.session_start` sample; if that command and the first batch were both delayed by
    the same stall, the first batch can pass (the envelope window still bounds it to ~15 s, and the next
    fresh batch re-establishes the baseline). A phone clock stepped BACKWARDS mid-session makes batches
    look stale for up to 30 s (the estimator window) unless the session is restarted.
15. **A click on the elevated window itself is dropped by Windows without an error** (UIPI). Keyboard
    events are refused honestly while the restriction is known; pointer events are not, so the customer
    can click a normal window. The ack then counts such a click as accepted. Not detectable from
    `SendInput`; documented in the README checklist. The same applies to keyboard events while the
    foreground's integrity level is UNKNOWN (#7: `OpenProcess`/`OpenProcessToken` refused): the agent
    refuses only on a known restriction, never on a guess, so such keystrokes can be dropped by UIPI and
    still counted as accepted. Device testing must show how often elevation is undeterminable.
16. **A `SendInput` call that never returns cannot be interrupted.** The session still ends after the
    bounded 2 s wait and releases what it knows; anything that stuck call presses later is released
    when (if) it returns (DECISIONS.md #42), otherwise at the next start-up from `input_holds.json`.
17. **The target-change grace (2 s) is a heuristic for keyboard-only phones** (DECISIONS.md #40): a
    phone that kept live-typing without pausing for a `foreground_app` change for more than 2 s would
    type into the new window. The PWA pauses live typing on that change; a stricter contract signal
    (e.g. a target generation number echoed by the phone) would remove the heuristic.
18. **Apps off the F11 list need the owner's opt-in** (DECISIONS.md #46, #49). An app not on the built-in
    list is `FULLSCREEN_UNSUPPORTED` until *Full screen with F11* is ticked for it in the tray's Approved apps
    window (or `dome-agent app-option <id> --fullscreen-f11 on`), even when its F11 does mean full screen; the
    phone is told so in those words. A caption-less app (Spotify, Discord) maximised under an auto-hidden
    taskbar reads as full screen: without the opt-in maximise/restore refuse it with `WINDOW_STATE_UNCHANGED`;
    with it they press F11 once to "leave full screen", which in an app that was only maximised may instead
    put it into its own full screen, reported as `WINDOW_STATE_UNCHANGED` (the owner opted in to F11 there).
    The phone's own copy for `FULLSCREEN_UNSUPPORTED` may still say only "Try Maximise instead" where it does
    not show the agent's message (mobile-app).
19. **The PyPI `av` wheel is not the shipping build.** `uv sync --locked --extra dev` installs the stock PyAV wheel
    from PyPI (it bundles GPL x264/x265 and every FFmpeg decoder) so that the Linux unit tests can run aiortc on
    loopback; it never ships. The shipping LGPL FFmpeg + PyAV wheel comes from `.github/workflows/ffmpeg-lgpl.yml`
    (written, never run) and will be pinned under `[tool.uv.sources]` with `marker = "sys_platform == 'win32'"`
    once published (WP0.1). Until then a Windows developer's `uv sync` also gets the GPL wheel (fine for
    development; `packaging/licence_gate.py` refuses it in a release). `DoMe.exe screen-worker --self-test`
    reports `decoders.exactly_wrapped_avframe: false` on it, honestly.
20. **Nothing in Screen view is verified on a device.** Capture (`ddagrab` through PyAV's `lavfi` device, the
    Windows.Graphics.Capture fallback), `h264_mf` encoding and whether a vendor's MFT repeats SPS/PPS, the Tk
    bars with `WS_EX_NOACTIVATE` / `SetWindowDisplayAffinity`, `RegisterHotKey`, the Job object killing the
    worker when DoMe.exe dies, `SetCursorPos` under per-monitor DPI on mixed-DPI, rotated or HDR monitors, mDNS
    `.local` candidates from a standard-user process and the Windows firewall prompt are written against the
    design and the documentation only. The Linux tests use the fake frame sources (`lavfi testsrc`, synthetic
    frames) and the development `libx264`; every failure on a real PC must come back as
    `SCREEN_CAPTURE_UNAVAILABLE{driver|busy|unsupported_display|remote_session}` or
    `SCREEN_ENCODER_UNAVAILABLE` and is reported, never pretended.
21. **The WGC fallback is not yet verified on a device.** The first device run (2026-10-26, `DoMe-Setup-0.1.0+fa8de22`)
    ended every start `capture_failed`: Desktop Duplication failed for a reason the log did not keep, and the fallback
    answered at once "Windows.Graphics.Capture support is not installed" (`windows-capture` was no dependency). Since
    then windows-capture 2.0.1's native module is a Windows dependency of the `screen` extra and is bundled
    (DECISIONS #88, #89), the monitor is asked for by its raw position (DECISIONS #85), and the worker's self-test on the
    frozen build must load the module. What only a PC can show: that Windows.Graphics.Capture starts in DoMe's worker
    (a desktop process without a window), the first frame's size on scaled, rotated and HDR monitors, colours
    (libswscale's BT.601 conversion of the BGRA frame), the CPU cost of a full-resolution copy per changed frame at the
    desktop's refresh rate, and Windows' yellow border. Why Desktop Duplication failed on the founder's PC is still
    unknown: the next diagnostics bundle names FFmpeg's reason (DECISIONS #90).
22. **Bar placement on scaled and multi-monitor PCs is written against the documentation only** (DECISIONS #79): the
    Tk thread is made per-monitor DPI aware (v2) so bar geometry is physical pixels, each bar's real rectangle is read
    with `GetWindowRect` / `MonitorFromWindow` and must lie on its monitor (else the start is refused and a live
    mis-placement pauses capture), and taps are refused on the measured rectangle. On Windows older than 10 1607
    (no `SetThreadDpiAwarenessContext`) the bars may land elsewhere on a scaled display and the placement check then
    refuses Screen view rather than stream without a bar. Making the Tk thread DPI aware also renders DoMe's other
    tkinter windows (pairing, grant, consent) unscaled in pixel terms (fonts scale): **not yet verified on a device**.
    Off Windows (development) the reserved band is still the fixed 56 px.
23. **Display changes are detected by polling, not by `WM_DISPLAYCHANGE`**: the agent enumerates the monitors afresh
    every second while a session is live (and the Tk thread checks every second that each bar window still exists), so
    a monitor plugged in, unplugged or switched to another resolution pauses capture within about a second, the bars
    are recreated for the new set and capture resumes; the session ends `capture_failed` if that takes more than 10 s,
    and at once if the viewed monitor itself was unplugged. When the viewed monitor changed size, rotation or place the
    worker rebuilds its capture at the new size and announces a new geometry generation; taps aimed at the old picture
    are refused `SCREEN_GEOMETRY_CHANGED` until the phone has the new one. A change that leaves every monitor's
    rectangle as it was (a colour-depth or refresh-rate switch) is not noticed by the agent; `ddagrab` rebuilds itself
    then (up to three times).
24. **Bitrate steps reopen the encoder (about 100 ms without frames) and no REMB is wired**: the worker steps
    1.5 → 0.8 → 0.5 Mbps only from `getStats()` receiver reports (`fractionLost` above 10 % for 5 s).
25. **`screen_session_result.allowance.remaining_seconds` is always `null`** from the agent (CONTRACT_ISSUES.md
    #18): the phone shows the window figures it received in `screen_ice_servers` and the data-channel
    `allowance` message instead.
26. **A headless agent (`--headless`, `NullUI`) can never start Screen view**: with no window there is no
    indicator, so every start is `SCREEN_CAPTURE_UNAVAILABLE{detail: indicator}`. This is the fail-closed rule,
    not a bug; `grant --screen` likewise needs the running tray to open its window.
27. **`screen_audio` is granted but nothing is streamed in Phase 1** (DECISIONS.md #67): the audio m-line is
    `inactive`, the bar shows no speaker glyph and `screen_session_result.audio` is `false`.
28. **The strict offer check accepts any `application` section whose protocol names SCTP** and leaves the finer
    SDP parsing to aiortc; an offer aiortc refuses after the strict check answers `SCREEN_OFFER_INVALID` with
    `reason: media` rather than a more specific reason.
29. **The agent's monitor numbering is not checked against `ddagrab`'s `output_idx`.** The agent numbers monitors
    primary first, then by position (`platform/windows/screen.py`), and passes that number to `ddagrab` as
    `output_idx`, which counts the outputs of the DEFAULT graphics adapter only, as DXGI lists them (`ddagrab` has no
    adapter option: DECISIONS #90). On a one-adapter PC the primary is normally output 0, and Phase 1 phones only ask
    for the primary, but on a multi-monitor or hybrid-GPU PC the two numberings may differ, or the monitor may not be
    on the default adapter at all ("Failed to enumerate DXGI output N", then the WGC fallback, which finds monitors by
    rectangle): **not yet verified on a device**. Phase 2 ("Next screen") must map by the monitor's rectangle
    (`IDXGIOutput::GetDesc`) before it offers other monitors.
30. **The Screen view tkinter windows (grant window, consent window, bars) have no automated test that opens a real
    window.** (The bar controller's placement check and heartbeat are unit-tested against a fake tkinter since
    2026-10-22.)
    Their button logic (`GrantWindowModel`, `ConsentWindowModel`), the menu items, the fail-closed paths without a
    display and the agent's use of them through a fake UI are unit-tested; the windows themselves, `WS_EX_NOACTIVATE`,
    the display affinity and the hotkey are **not yet verified on a device** (this environment has no Tk).
31. **A capture source that cannot open is reported only after DTLS** (DECISIONS #82): no frame may exist before the
    phone's certificate is verified, so `ddagrab` / WGC open in the track, after `connected`. A PC whose duplication is
    busy (another capture app) or unsupported therefore answers the start, shows the bars, connects and then ends
    `capture_failed` ("Your PC's screen sharing stopped unexpectedly"), instead of refusing the start with
    `SCREEN_CAPTURE_UNAVAILABLE{busy|unsupported_display}`. The worker's `error` still names the detail and the
    capture's own reason in the agent's log (DECISIONS #90). A pre-flight that checks the duplication without
    capturing a frame is a candidate for the device round.
32. **Desktop Duplication's still-desktop behaviour is not verified on a device** (DECISIONS #83): `DdagrabSource`
    treats `av.error.BlockingIOError` (EAGAIN) as "nothing new" and returns None, but if PyAV's lavfi read blocks
    instead, the 1 fps floor cannot fire while the desktop is still and the phone's stall detector would re-session.
    Device checklist (`README.md`): a still desktop for 10 s keeps ≥ 1 fps with no rebuild; one UAC prompt pauses
    rather than ends. If lavfi blocks, switch to PyAV's `Graph` pull API (ADR-0002 D4).
33. **Current Windows builds do not include Screen view** (DECISIONS #86): they are made without the `screen` extra, so
    they still announce protocol 1.4 (pairing needs `pc_key_mac`) but refuse every start with
    `ACTION_UNAVAILABLE{reason: screen_view_not_included}` before any window or bar, and the tray shows "Screen view:
    not in this version of DoMe". A build with the shipping LGPL wheel (`.github/workflows/ffmpeg-lgpl.yml`, never run)
    is what turns it on.
