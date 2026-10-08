"""ECMA-262-like regular-expression semantics for JSON Schema ``pattern`` keywords.

Python's ``re`` differs from the regex dialect JSON Schema specifies in two ways that matter for
a cross-language protocol: ``$`` also matches before a trailing newline, and ``\\d``/``\\w`` match
Unicode digits/letters. ``shared/ts`` validates with Ajv (ECMA-262); without this shim the Python
relay would accept ``"discord\\n"`` or fullwidth digits that the TypeScript side rejects.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from jsonschema import Draft202012Validator, validators
from jsonschema.exceptions import ValidationError


@lru_cache(maxsize=512)
def compile_pattern(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.ASCII)


def pattern_matches(pattern: str, instance: str) -> bool:
    """``True`` iff ``instance`` matches ``pattern`` with ECMA-262 anchoring semantics."""
    regex = compile_pattern(pattern)
    m = regex.search(instance)
    if m is None:
        return False
    # Python lets `$` match before a final "\n"; ECMA-262 does not. If the pattern is
    # end-anchored and the match stops before the end of the string, reject.
    if _end_anchored(pattern) and m.end() != len(instance):
        return False
    return True


def _end_anchored(pattern: str) -> bool:
    if not pattern.endswith("$"):
        return False
    # count preceding backslashes: an odd number escapes the `$`
    backslashes = len(pattern) - len(pattern[:-1].rstrip("\\")) - 1
    return backslashes % 2 == 0


def _strict_pattern(validator: Any, pattern: str, instance: Any, schema: Any):  # type: ignore[no-untyped-def]
    if not isinstance(instance, str):
        return
    if not pattern_matches(pattern, instance):
        yield ValidationError(f"{instance!r} does not match {pattern!r}")


StrictValidator = validators.extend(Draft202012Validator, validators={"pattern": _strict_pattern})

__all__ = ["StrictValidator", "pattern_matches", "compile_pattern"]
