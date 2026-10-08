"""TEST DOUBLES ONLY.

Nothing in this package is imported by a production code path. ``fake_platform`` is selected solely
by ``DOME_AGENT_PLATFORM=fake`` (the installer never sets it) and makes the agent report
``platform: "development"``; ``fake_relay`` and ``fake_extension`` are used by the test suite.
"""
