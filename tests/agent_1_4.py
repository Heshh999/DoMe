"""`dome-agent` for test_e2e_versions.py's Screen view cases: the real CLI on the fake platform with the version checks
of the agent release one protocol MINOR behind this tree (1.4: `SUPPORTED_PROTOCOL_VERSIONS = ("1.0", ..., "1.4")`,
registry 1.4), the PC a 1.5 phone meets in the field (the PWA updates when the cloud deploys, PCs later).

It announces only those versions in its relay hello (so the relay records them and `GET /v1/pcs` shows them), and the
shared library verifies every command, confirmation and the PC-signed screen answer against protocol 1.4 exactly as
the 1.4 release did (`protocol_compatible(stamp, ("1.4",))`). Like `older_agent.py` (which stays 1.1) it only changes
those constants; everything else is `screen_agent.py`'s seams (pc-agent's FakeScreenUI as the person at the PC and the
`test_*` control ops), so Screen view works on Linux.

What this proves and what it does not (design 2a §5.2): it runs THIS tree's agent code with the 1.4 constants, so it
witnesses the version, stamp and vocabulary rows (the hello, the relay's stamp gate, a 1.4-stamped start, the answer
stamped 1.4, no 1.5 key or message). The shipped 1.4 agent's own consent order (its consent window before ending the
old session) is documented in docs/PROTOCOL.md section 21, not witnessed here."""

from __future__ import annotations

import sys

import dome_agent
import screen_agent
from dome_agent import frames
from dome_agent.cli import main
from dome_protocol import load_registry

PROTOCOL_VERSIONS = ("1.0", "1.1", "1.2", "1.3", "1.4")


def install() -> None:
    registry = load_registry()  # cached: the agent's every verifier uses this instance
    registry.protocol_version = PROTOCOL_VERSIONS[-1]
    registry.registry_version = PROTOCOL_VERSIONS[-1]
    dome_agent.SUPPORTED_PROTOCOL_VERSIONS = PROTOCOL_VERSIONS
    frames.SUPPORTED_PROTOCOL_VERSIONS = PROTOCOL_VERSIONS  # the relay hello (imported by name)
    screen_agent.install()  # the person at the PC and the test_* control ops


if __name__ == "__main__":
    install()
    sys.exit(main())
