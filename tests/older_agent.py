"""`dome-agent` for test_e2e_versions.py: the real CLI on the fake platform, with the version checks of the agent
release one protocol MINOR behind this tree (1.1: `SUPPORTED_PROTOCOL_VERSIONS = ("1.0", "1.1")`, registry 1.1).

It announces only those versions in its relay hello, and the shared library verifies every command and confirmation
against protocol 1.1 exactly as the 1.1 release did (`protocol_compatible(stamp, ("1.1",))`). That is the PC a phone
meets in the field: the PWA updates as soon as the cloud deploys, PCs later (docs/PROTOCOL.md §18.2). Nothing else
differs from `dome-agent run`; the browser bridge is not used by these tests. test_e2e_versions.py starts this file
instead of the `dome-agent` script."""

from __future__ import annotations

import sys

import dome_agent
from dome_agent import frames
from dome_agent.cli import main
from dome_protocol import load_registry

PROTOCOL_VERSIONS = ("1.0", "1.1")


def install() -> None:
    registry = load_registry()  # cached: the agent's every verifier uses this instance
    registry.protocol_version = PROTOCOL_VERSIONS[-1]
    registry.registry_version = PROTOCOL_VERSIONS[-1]
    dome_agent.SUPPORTED_PROTOCOL_VERSIONS = PROTOCOL_VERSIONS
    frames.SUPPORTED_PROTOCOL_VERSIONS = PROTOCOL_VERSIONS  # the relay hello (imported by name)


if __name__ == "__main__":
    install()
    sys.exit(main())
