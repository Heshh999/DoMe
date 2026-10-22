"""The Screen view worker: a separate send-only aiortc process (design §5, §15 ``screen/worker/*``).

Started by the agent as ``DoMe.exe screen-worker`` (frozen) or ``python -m dome_agent.screen.worker`` with an
anonymous pipe pair (:mod:`pipe`). It holds aiortc, PyAV and the capture code; it imports none of
``dome_agent.api``, ``dome_agent.relay_client`` or the agent itself (tested); it writes no file except a crash log
through ``crashguard`` (masked); its stderr goes to the agent's redacting log pipeline; it pins the aiortc/aioice
loggers to WARNING; it is send-only and no decoder runs on network input.

Modules: :mod:`main` (the pipe loop, liveness, ``--self-test``), :mod:`rtc` (the peer: strict offer use, the
PLI → IDR hook, the data channel, path detection, bitrate steps from ``getStats()``), :mod:`track` (the pre-encoded
track with SPS/PPS before every IDR and the 1 fps floor), :mod:`encode` (H.264 through PyAV codec contexts, level
3.1 sizing, Annex B helpers), :mod:`fake` (``lavfi testsrc`` or synthetic frames: the only sources that run on
Linux), :mod:`capture_dda` and :mod:`capture_wgc` (the Windows sources, written against the design, **not yet
verified on a device**).
"""
