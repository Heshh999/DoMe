"""The agent and the browser extension AS SHIPPED must agree on a protocol version.

The YouTube chain broke on exactly such a mismatch: the agent acked its own newest version, the extension
refused that ack, and each side's own tests passed against hard-coded lists. These tests read the list the
extension really sends (``SUPPORTED_PROTOCOL_VERSIONS`` in browser-extension/src/shared/version.ts) and check
the agent's ack against it with the extension's own acceptance rule, so a version bump on one side only
(the 1.2 contract, for example) fails here instead of on the owner's PC."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from dome_protocol.commands import protocol_compatible

from dome_agent import SUPPORTED_PROTOCOL_VERSIONS
from dome_agent.bridge.server import negotiate_protocol_version
from dome_agent.testing.fake_extension import SHIPPED_PROTOCOL_VERSIONS, FakeExtension

VERSION_TS = Path(__file__).resolve().parents[2] / "browser-extension" / "src" / "shared" / "version.ts"
_ARRAY = re.compile(r"export\s+const\s+SUPPORTED_PROTOCOL_VERSIONS\b[^=]*=\s*\[(?P<items>[^\]]*)\]")
_ITEM = re.compile(r"""(["'])(?P<version>[0-9]+\.[0-9]+)\1""")


def parse_supported_versions(source: str) -> tuple[str, ...]:
    """The string literals of the ``SUPPORTED_PROTOCOL_VERSIONS`` array literal, in order."""
    match = _ARRAY.search(source)
    assert match is not None, "no `export const SUPPORTED_PROTOCOL_VERSIONS ... = [...]` array literal"
    items = [item.strip() for item in match.group("items").split(",") if item.strip()]
    versions: list[str] = []
    for item in items:
        literal = _ITEM.fullmatch(item)
        assert literal is not None, f"unexpected entry {item!r}: expected a quoted MAJOR.MINOR string"
        versions.append(literal.group("version"))
    assert versions, "SUPPORTED_PROTOCOL_VERSIONS is empty"
    return tuple(versions)


def shipped_extension_versions() -> tuple[str, ...]:
    assert VERSION_TS.is_file(), f"{VERSION_TS} is missing: these tests need the whole repository"
    return parse_supported_versions(VERSION_TS.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('export const SUPPORTED_PROTOCOL_VERSIONS: readonly string[] = ["1.0", "1.1"];', ("1.0", "1.1")),
        (
            "export const SUPPORTED_PROTOCOL_VERSIONS = [\n  '1.0',\n  '1.1',\n  '1.2',\n] as const;",
            ("1.0", "1.1", "1.2"),
        ),
        ('/** ["9.9"] */\nexport const SUPPORTED_PROTOCOL_VERSIONS: string[] = ["1.0"];', ("1.0",)),
    ],
)
def test_the_parser_reads_the_array_literal(source: str, expected: tuple[str, ...]) -> None:
    assert parse_supported_versions(source) == expected


def test_the_parser_refuses_what_it_cannot_read() -> None:
    with pytest.raises(AssertionError):
        parse_supported_versions("export const SUPPORTED_PROTOCOL_VERSIONS = [...BASE, '1.2'];")
    with pytest.raises(AssertionError):
        parse_supported_versions("export const OTHER = ['1.0'];")


def test_the_agent_acks_a_version_the_shipped_extension_accepts() -> None:
    extension = shipped_extension_versions()
    acked = negotiate_protocol_version(extension)
    assert acked is not None, f"agent {SUPPORTED_PROTOCOL_VERSIONS} and extension {extension} share no version"
    # connection.ts refuses an ack that protocolCompatible() rejects (same rule as dome_protocol's)
    assert protocol_compatible(acked, extension), f"the extension ({extension}) would refuse the ack {acked!r}"
    assert acked == max(set(extension) & set(SUPPORTED_PROTOCOL_VERSIONS), key=lambda v: tuple(map(int, v.split("."))))


def test_the_fake_extension_lists_what_the_shipped_extension_lists() -> None:
    """Every bridge test (and the integration suite) talks to FakeExtension's default list: it must be the
    real one, or the tests exercise a handshake no browser ever performs."""
    assert shipped_extension_versions() == SHIPPED_PROTOCOL_VERSIONS
    assert FakeExtension(Path("unused")).protocol_versions == SHIPPED_PROTOCOL_VERSIONS
