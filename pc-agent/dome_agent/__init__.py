"""DoMe PC agent (``dome_agent``).

Runs in the logged-in user's interactive session, opens one outbound WebSocket to the managed
relay and executes locally authorised actions. Every trust-boundary input (relay frames, signed
envelopes, bridge frames, REST bodies, entitlement assertions) is validated with ``dome_protocol``
before use. Windows adapters live under ``dome_agent.platform.windows`` and are only imported on
Windows; everywhere else ``dome_agent.platform.unsupported`` answers ``PLATFORM_UNSUPPORTED``.
"""

__version__ = "0.1.0"
COMPONENT_NAME = "agent"
SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = ("1.0", "1.1")
